import json
import logging
import os
import time
from openai import OpenAI

logger = logging.getLogger(__name__)

class KnowledgeAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GROQ_SPECIALIST_MODEL", "llama-3.3-70b-versatile")
        
    def handle(self, bundle: dict) -> dict:
        from tools import web_search, calculate, wikipedia_summary, get_current_time
        
        local_tools = {
            "web_search": lambda query: web_search(query),
            "calculate": lambda expression: calculate(expression),
            "wikipedia_summary": lambda topic: wikipedia_summary(topic),
            "get_current_time": lambda: get_current_time()
        }

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Searches the web for current information.",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "calculate",
                    "description": "Evaluates a mathematical expression safely.",
                    "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "wikipedia_summary",
                    "description": "Gets a summary of a topic from Wikipedia.",
                    "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_current_time",
                    "description": "Gets the current local date and time.",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            }
        ]

        system_instruction = (
            "You are the Knowledge Specialist. You handle factual questions, math, web searches, and general inquiries.\n"
            "Use the provided tools to fulfill the user's request. If they ask about recent events or facts you don't know, use web_search or wikipedia_summary.\n"
            "Always reply with a single, terse, helpful sentence starting with a relevant emoji.\n"
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
                    text = msg.content.strip() if msg.content else "✅ Knowledge task executed."
                    return {"kind": "reply", "text": text}
            except Exception as e:
                if attempt == 0 and "429" in str(e):
                    import re
                    m = re.search(r"retry in (\d+)", str(e))
                    wait_sec = int(m.group(1)) + 2 if m else 10
                    time.sleep(wait_sec)
                else:
                    logger.error("KnowledgeAgent error: %s", e)
                    return {"kind": "reply", "text": "Sorry, I couldn't process your question right now."}
        return {"kind": "reply", "text": "✅ Knowledge task completed."}

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