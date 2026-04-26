import logging
import os
from datetime import datetime, timezone

import google.generativeai as genai

logger = logging.getLogger(__name__)

class PlannerAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GEMINI_SPECIALIST_MODEL", "gemini-1.5-flash-latest")
        
    def handle(self, bundle: dict) -> dict:
        user_id = bundle["user_id"]
        
        # Define wrappers to pass implicitly required properties (like user_id)
        def create_task(title: str) -> str:
            """Creates a new TODO task."""
            from tools import create_task as ct
            return ct(title, user_id)
            
        def list_tasks(status: str = "") -> str:
            """Lists tasks. status can be 'pending', 'done', or empty."""
            from tools import list_tasks as lt
            return lt(status, user_id)
            
        def complete_task(task_id: int) -> str:
            """Marks a task as done."""
            from tools import complete_task as cmt
            return cmt(str(task_id), user_id)
            
        def delete_task(task_id: int) -> str:
            """Deletes a task."""
            from tools import delete_task as dt
            return dt(str(task_id), user_id)
            
        def set_reminder(message: str, time_string: str) -> str:
            """Sets a reminder. time_string must be relative or absolute like 'tomorrow at 5pm' or 'in 2 hours'."""
            from tools import set_reminder as sr
            return sr(f"{message}|{time_string}", user_id)
            
        tools_list = [create_task, list_tasks, complete_task, delete_task, set_reminder]
        
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if api_key:
            genai.configure(api_key=api_key)
            
        model = genai.GenerativeModel(
            model_name=self.model_name,
            tools=tools_list,
            system_instruction=(
                "You are the Planner Specialist. You handle tasks and one-shot reminders.\n"
                "Use the provided tools to fulfill the user's request.\n"
                "If you encounter an error from a tool, explain it to the user.\n"
                "Always reply with a single, terse sentence starting with a relevant emoji."
            )
        )
        
        chat = model.start_chat(enable_automatic_function_calling=True)
        user_msg = bundle.get("subrequest") or bundle.get("original_message")
        prompt = f"Request: {user_msg}"
        
        try:
            response = chat.send_message(prompt)
            text = ""
            try:
                text = response.text.strip()
            except ValueError:
                text = "✅ Planner task executed."
            return {"kind": "reply", "text": text}
        except Exception as e:
            logger.error("PlannerAgent error: %s", e)
            return {"kind": "reply", "text": "Sorry, I couldn't process your planner request right now."}