import logging
import os

import google.generativeai as genai

logger = logging.getLogger(__name__)

class EventsAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GEMINI_SPECIALIST_MODEL", "gemini-2.5-flash")
        
    def handle(self, bundle: dict) -> dict:
        user_id = bundle["user_id"]
        
        def add_event(title: str, event_date: str, recurrence: str = "", remind_lead_days: int = 1) -> str:
            """Adds a life event (e.g. birthday, anniversary). event_date must be strictly YYYY-MM-DD. recurrence can be 'yearly', 'monthly', or empty."""
            from database import TaskDatabase
            db = TaskDatabase()
            eid = db.add_event(user_id, title, event_date, recurrence or None, remind_lead_days)
            return f"Event '{title}' added with ID {eid}."
            
        def list_events() -> str:
            """Lists all life events for the user."""
            from database import TaskDatabase
            db = TaskDatabase()
            events = db.list_events(user_id)
            if not events:
                return "No events found."
            lines = []
            for e in events:
                lines.append(f"{e['id']}: {e['title']} on {e['event_date']} (recur: {e['recurrence']})")
            return "Events:\n" + "\n".join(lines)

        def delete_event(event_id: int) -> str:
            """Deletes a life event by ID."""
            from database import TaskDatabase
            db = TaskDatabase()
            ok = db.delete_event(user_id, event_id)
            return "Event deleted." if ok else "Event not found."
            
        tools_list = [add_event, list_events, delete_event]
        
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if api_key:
            genai.configure(api_key=api_key)
            
        model = genai.GenerativeModel(
            model_name=self.model_name,
            tools=tools_list,
            system_instruction=(
                "You are the Events Specialist. You manage birthdays, anniversaries, and recurring life events.\n"
                "Use tools to add, list, or delete events. Event dates MUST be YYYY-MM-DD.\n"
                f"Today's date is {bundle.get('now_local_iso', 'unknown')}.\n"
                "Always reply with a single, terse sentence starting with a relevant emoji."
            )
        )
        
        chat = model.start_chat(enable_automatic_function_calling=True)
        user_msg = bundle.get("subrequest") or bundle.get("original_message")
        
        try:
            response = chat.send_message(f"Request: {user_msg}")
            return {"kind": "reply", "text": response.text.strip()}
        except Exception as e:
            logger.error("EventsAgent error: %s", e)
            return {"kind": "reply", "text": "Sorry, I couldn't process your events request right now."}