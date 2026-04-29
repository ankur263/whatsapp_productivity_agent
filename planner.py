import json
import logging
import os
import time
from datetime import datetime, timezone
from openai import OpenAI

logger = logging.getLogger(__name__)

class PlannerAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GROQ_SPECIALIST_MODEL", "llama-3.3-70b-versatile")
        
    def handle(self, bundle: dict) -> dict:
        user_id = bundle["user_id"]
        
        def create_task(title: str) -> str:
            from tools import create_task as ct
            return ct(title, user_id)
            
        def list_tasks(status: str = "") -> str:
            from tools import list_tasks as lt
            return lt(status, user_id)
            
        def complete_task(task_id: str) -> str:
            from tools import complete_task as cmt
            return cmt(str(task_id), user_id)
            
        def delete_task(task_id: str) -> str:
            from tools import delete_task as dt
            return dt(str(task_id), user_id)
            
        def set_reminder(message: str, time_string: str) -> str:
            from tools import set_reminder as sr
            return sr(f"{message}|{time_string}", user_id)
            
        local_tools = {
            "create_task": create_task,
            "list_tasks": list_tasks,
            "complete_task": complete_task,
            "delete_task": delete_task,
            "set_reminder": set_reminder
        }

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "create_task",
                    "description": "Creates a new TODO task.",
                    "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "list_tasks",
                    "description": "Lists tasks. status can be 'pending', 'done', or empty.",
                    "parameters": {"type": "object", "properties": {"status": {"type": "string"}}, "required": []}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "complete_task",
                    "description": "Marks a task as done.",
                    "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_task",
                    "description": "Deletes a task.",
                    "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "set_reminder",
                    "description": "Sets a reminder. time_string must be relative or absolute like 'tomorrow at 5pm' or 'in 2 hours'.",
                    "parameters": {"type": "object", "properties": {"message": {"type": "string"}, "time_string": {"type": "string"}}, "required": ["message", "time_string"]}
                }
            }
        ]

        system_instruction = (
            "You are the Planner Specialist. You handle tasks and one-shot reminders.\n"
            "Use the provided tools to fulfill the user's request.\n"
            "If you encounter an error from a tool, explain it to the user.\n"
            "Always reply with a single, terse sentence starting with a relevant emoji.\n"
            "Visible Context Cues: Always explicitly echo the scope/household you used in your final reply to catch misroutes.\n"
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
                    text = msg.content.strip() if msg.content else "✅ Planner task executed."
                    return {"kind": "reply", "text": text}
            except Exception as e:
                if attempt == 0 and "429" in str(e):
                    import re
                    m = re.search(r"retry in (\d+)", str(e))
                    wait_sec = int(m.group(1)) + 2 if m else 10
                    time.sleep(wait_sec)
                else:
                    logger.error("PlannerAgent error: %s", e)
                    return {"kind": "reply", "text": "Sorry, I couldn't process your planner request right now."}
        return {"kind": "reply", "text": "✅ Planner task completed."}

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