import json
import logging
import os
import time
from openai import OpenAI

logger = logging.getLogger(__name__)

class JournalAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GROQ_SPECIALIST_MODEL", "llama-3.3-70b-versatile")
        
    def handle(self, bundle: dict) -> dict:
        from database import TaskDatabase
        from tools import _memory_store
        db = TaskDatabase()
        user_id = bundle["user_id"]
        
        def save_note(content: str) -> str:
            note_id = db.save_note(user_id, content)
            return f"Saved note #{note_id}."
            
        def list_notes(limit: int = 5) -> str:
            notes = db.list_notes(user_id, limit=limit)
            if not notes:
                return "No notes found."
            lines = [f"Note {n['id']} ({n['created_at'][:10]}): {n['content']}" for n in notes]
            return "\n".join(lines)
            
        def remember_fact(fact: str) -> str:
            _memory_store().remember(user_id, fact, metadata={"agent": "journal", "type": "user_fact"})
            return "Fact remembered."
            
        def recall_past(query: str) -> str:
            # Search both stores: semantic memory (remember_fact) AND saved notes
            # (save_note). Earlier versions only checked semantic memory, which
            # missed plain-text private notes.
            sem = []
            try:
                sem = _memory_store().recall(user_id, query, k=5) or []
            except Exception:
                sem = []
            note_rows = db.search_notes(user_id, query, limit=5) or []
            note_lines = [n["content"] for n in note_rows]

            # De-dup while preserving order (notes first since they're exact matches)
            seen = set()
            merged: list[str] = []
            for item in note_lines + list(sem):
                key = item.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    merged.append(item)

            if not merged:
                return "No relevant memories found."
            return "Recalled memories:\n" + "\n".join(f"- {r}" for r in merged)
            
        local_tools = {
            "save_note": save_note,
            "list_notes": list_notes,
            "remember_fact": remember_fact,
            "recall_past": recall_past
        }

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "save_note",
                    "description": "Saves a private, free-form text note for the user.",
                    "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_notes",
                    "description": "Lists recently saved private notes.",
                    "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "remember_fact",
                    "description": "Stores a fact permanently in the user's semantic memory (e.g., 'My dog is named Max').",
                    "parameters": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "recall_past",
                    "description": "Searches the user's semantic memory for past facts or context.",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
                }
            }
        ]

        system_instruction = (
            "You are the Journal Specialist. You handle private notes, daily summaries, and memory recall.\n"
            "All information you store or retrieve is 100% private to this specific user. It is never shared with their household.\n"
            "Use the provided tools to save notes or recall semantic facts.\n"
            "Always reply with a single, terse sentence starting with a relevant emoji.\n"
            "Visible Context Cues: Explicitly state that the action was private (e.g., '📝 Saved to your private notes').\n"
            "Always reply in the same language the user wrote in (English, Hindi, Hinglish, etc.). Mirror their language exactly."
        )
        
        user_msg = bundle.get("subrequest") or bundle.get("original_message")
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Request: {user_msg}"}
        ]

        if bundle.get("media_bytes"):
            return self._fallback_gemini(bundle, local_tools, system_instruction)

        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            return {"kind": "reply", "text": "Missing Groq API key."}
            
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        
        for attempt in range(5):
            try:
                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    tools=openai_tools,
                    tool_choice="auto"
                )
                
                msg = response.choices[0].message
                if msg.tool_calls:
                    messages.append(msg.model_dump(exclude_unset=True))
                    for tc in msg.tool_calls:
                        func_name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments)
                        except Exception:
                            args = {}
                        try:
                            obs = local_tools[func_name](**args)
                        except Exception as e:
                            obs = f"Error: {e}"
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(obs)})
                else:
                    from tool_fallback import absorb_leaked_calls
                    if absorb_leaked_calls(messages, msg.content or "", local_tools):
                        continue
                    text = msg.content.strip() if msg.content else "✅ Journal task executed."
                    return {"kind": "reply", "text": text}
            except Exception as e:
                if attempt == 0 and "429" in str(e):
                    import re
                    m = re.search(r"retry in (\d+)", str(e))
                    wait_sec = int(m.group(1)) + 2 if m else 10
                    time.sleep(wait_sec)
                else:
                    logger.error("JournalAgent error: %s", e)
                    return {"kind": "reply", "text": "Sorry, I couldn't access your journal right now."}
        return {"kind": "reply", "text": "✅ Journal task completed."}

    def _fallback_gemini(self, bundle, local_tools, system_instruction):
        import google.generativeai as genai
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=os.getenv("GEMINI_SPECIALIST_MODEL", "gemini-2.5-flash"),
            tools=list(local_tools.values()),
            system_instruction=system_instruction
        )
        chat = model.start_chat(enable_automatic_function_calling=True)
        prompt = bundle.get("subrequest") or bundle.get("original_message")
        contents = [prompt]
        if bundle.get("media_bytes") and bundle.get("mime_type"):
            contents.append({"mime_type": bundle.get("mime_type"), "data": bundle.get("media_bytes")})
        try:
            response = chat.send_message(contents)
            text = response.text.strip() if response.text else "✅ Processed."
            return {"kind": "reply", "text": text}
        except Exception as e:
            logger.error("Gemini fallback error: %s", e)
            return {"kind": "reply", "text": "Sorry, I couldn't process your request right now."}