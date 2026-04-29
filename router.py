from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
import concurrent.futures
from typing import Dict

from agent import Agent
from database import TaskDatabase
from supervisor import SupervisorAgent
from planner import PlannerAgent
from events_agent import EventsAgent
from shopping_agent import ShoppingAgent
from finance_agent import FinanceAgent
from knowledge_agent import KnowledgeAgent
from journal_agent import JournalAgent


logger = logging.getLogger(__name__)


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"+{digits}" if digits else ""

class AgentRouter:
    def __init__(self) -> None:
        self.agents: Dict[str, Agent] = {}
        self.db = TaskDatabase()
        self.supervisor = SupervisorAgent()
        self.planner = PlannerAgent()
        self.events = EventsAgent()
        self.shopping = ShoppingAgent()
        self.finance = FinanceAgent()
        self.knowledge = KnowledgeAgent()
        self.journal = JournalAgent()

    def _get_agent(self, user_id: str) -> Agent:
        if user_id not in self.agents:
            self.agents[user_id] = Agent()
        return self.agents[user_id]

    def route(self, from_phone: str, text: str, media_bytes: bytes | None = None, mime_type: str | None = None) -> str:
        user_id = _normalize_phone(from_phone.strip())
        if not user_id:
            return "Invalid sender phone number."

        # 1. Slash Commands (Administrative & Workspaces)
        if text.startswith("/join "):
            code = text.split(" ", 1)[1].strip()
            res = self.db.consume_invite(user_id, code)
            if res:
                self.db.upsert_user_setting(user_id, "is_allowed", "1")
                settings = self.db.get_user_settings(user_id) or {}
                if settings.get("onboarding_state") != "complete":
                    self.db.upsert_user_setting(user_id, "onboarding_state", "await_currency")
                    return f"🎉 Success! You have joined *{res['name']}*.\n\nBefore we start, I need to quickly set up your personal profile. What is your default 3-letter currency code (e.g., INR, SGD, USD)?"
                return f"🎉 Success! You have joined *{res['name']}*. You can now start adding groceries or tasks."
            return "❌ Invalid or expired invite code."

        settings = self.db.get_user_settings(user_id)
        is_allowed = int(settings.get("is_allowed", 0)) if settings else 0

        if not is_allowed:
            text_upper = text.strip().upper()
            if text_upper == "YES":
                self.db.upsert_user_setting(user_id, "is_allowed", "1")
                self.db.create_household(user_id, "My Home")
                self.db.upsert_user_setting(user_id, "onboarding_state", "await_currency")
                return "🎉 Welcome to Home OS! I've created your personal household.\n\nBefore we start, I need to quickly set up your personal profile. What is your default 3-letter currency code (e.g., INR, SGD, USD)?"
            elif text_upper == "STOP":
                return "Got it. You won't receive any more messages from me. If you ever change your mind, just reply YES."
            else:
                return (
                    "Hi! I'm Home OS, a private WhatsApp assistant for:\n"
                    "• Reminders & tasks\n"
                    "• Personal finance\n"
                    "• Groceries & shopping\n"
                    "• Journal & notes\n\n"
                    "Works solo or shared with your family.\n"
                    "Reply *YES* to set up your free account (takes 30 seconds).\n"
                    "Reply *STOP* anytime to opt out.\n\n"
                    "_(By continuing, you agree to our Privacy Policy)_"
                )

        if text.startswith("/invite"):
            hh_id = self.db.get_active_household(user_id)
            if not hh_id:
                return "❌ You are not currently active in any household."
            code = self.db.create_invite(hh_id, user_id)
            return f"🎟️ *Invite Code: {code}*\n\nSend this code to your family or helper. They just need to text me:\n`/join {code}`\n\n_(Code expires in 24 hours)_"

        if text.startswith("/switch"):
            parts = text.split(" ", 1)
            hhs = self.db.get_user_households(user_id)
            if len(parts) == 1:
                lines = ["🏠 *Your Households:*"]
                for idx, h in enumerate(hhs, 1):
                    lines.append(f"{idx}. {h['name']} ({h['role']})")
                lines.append("\nReply `/switch <number>` to change.")
                return "\n".join(lines)
            try:
                idx = int(parts[1].strip()) - 1
                if idx < 0: raise ValueError()
                target = hhs[idx]
                self.db.upsert_user_setting(user_id, "active_household_id", target["id"])
                return f"✅ Switched active household to *{target['name']}*."
            except Exception:
                return "❌ Invalid household number."

        if text.startswith("/leave"):
            parts = text.split(" ", 1)
            hhs = self.db.get_user_households(user_id)
            if len(parts) == 1:
                lines = ["🏃 *Leave a Household:*"]
                for idx, h in enumerate(hhs, 1):
                    lines.append(f"{idx}. {h['name']}")
                lines.append("\nReply `/leave <number>` to leave.")
                return "\n".join(lines)
            try:
                idx = int(parts[1].strip()) - 1
                if idx < 0: raise ValueError()
                target = hhs[idx]
                self.db.leave_household(user_id, target["id"])
                return f"✅ You have left *{target['name']}*."
            except Exception:
                return "❌ Invalid household number."

        if text.startswith("/createhouse "):
            name = text.split(" ", 1)[1].strip()
            if not name:
                return "❌ Please provide a name. Usage: `/createhouse Beach House`"
            self.db.create_household(user_id, name)
            return f"✅ Created new household '*{name}*'. You are now active in it. Use /invite to add members."

        if text.startswith("/deleteme"):
            self.db.delete_user(user_id)
            return "🗑️ Your account and all your personal data have been completely deleted. You won't receive any more messages from me.\n\nIf you ever want to return, just say 'hi' to start over."

        # Check monthly quota
        free_limit = int(os.getenv("FREE_MESSAGE_LIMIT", "200"))
        if self.db.get_monthly_message_count(user_id) >= free_limit:
            return f"⚠️ *Quota Exceeded*\n\nYou've reached your free limit of {free_limit} messages for this month. Please wait until the 1st of next month."

        # 2. Onboarding State Machine
        if not settings or settings.get("onboarding_state") != "complete":
            return self._handle_onboarding(user_id, text, settings)

        pending_msg = self.db.get_and_clear_pending_clarification(user_id)
        is_clarification = bool(pending_msg)

        # 3. Pre-Router Fast Path (Bypass LLM) - Skipped if clarifying
        agent = self._get_agent(user_id)
        if not media_bytes and not is_clarification:
            fast_reply = agent._maybe_run_direct_tool(user_id, text)
            if fast_reply:
                self.db.append_conversation(user_id, "user", text)
                self.db.append_conversation(user_id, "assistant", fast_reply, agent_name="fast_path")
                return fast_reply

        supervisor_text = f"Original Request: {pending_msg}\nUser's Clarification: {text}" if is_clarification else text

        # 3. Supervisor Routing
        start_time = time.time()
        recent_turns = self.db.get_recent_conversations(user_id, limit=10)
        decision = self.supervisor.decide(user_id, supervisor_text, recent_turns, settings, media_bytes, mime_type)
        latency_ms = int((time.time() - start_time) * 1000)

        # Record the user's inbound message now
        self.db.append_conversation(user_id, "user", text)

        # Handle validation failure
        if not decision:
            logger.warning("Supervisor failed to produce valid output, falling back to legacy agent.")
            self.db.insert_routing_log(user_id, "supervisor", "fallback", 1, latency_ms, 2, "Validation failed")
            reply = agent.run(user_id=user_id, question=text)
            self.db.append_conversation(user_id, "assistant", reply, agent_name="legacy_fallback")
            return reply

        # Handle Supervisor inline reply (greetings, simple questions)
        if decision.kind == "reply" and decision.text:
            self.db.insert_routing_log(user_id, "supervisor", "reply", 0, latency_ms, 1)
            self.db.append_conversation(user_id, "assistant", decision.text, agent_name="supervisor")
            return decision.text

        # Handle Supervisor Clarification
        if decision.kind == "clarify" and decision.text:
            save_text = pending_msg if pending_msg else text
            self.db.set_pending_clarification(user_id, save_text)
            self.db.insert_routing_log(user_id, "supervisor", "clarify", 0, latency_ms, 1)
            self.db.append_conversation(user_id, "assistant", decision.text, agent_name="supervisor")
            return decision.text

        # 4. Dispatch to Specialists
        def run_specialist(agent_name: str, subrequest: str) -> str:
            bundle = {
                "user_id": user_id,
                "subrequest": subrequest,
                "original_message": text,
                "user_settings": settings or {},
                "recent_turns": recent_turns,
                "now_local_iso": datetime.now(timezone.utc).isoformat(),
                "media_bytes": media_bytes,
                "mime_type": mime_type
            }
            if agent_name == "planner":
                return self.planner.handle(bundle).get("text", "Done.")
            elif agent_name == "events":
                return self.events.handle(bundle).get("text", "Done.")
            elif agent_name == "shopping":
                return self.shopping.handle(bundle).get("text", "Done.")
            elif agent_name == "finance":
                return self.finance.handle(bundle).get("text", "Done.")
            elif agent_name == "knowledge":
                return self.knowledge.handle(bundle).get("text", "Done.")
            elif agent_name == "journal":
                return self.journal.handle(bundle).get("text", "Done.")
            return f"[{agent_name}] Not yet implemented."

        if decision.kind == "plan" and decision.steps:
            self.db.insert_routing_log(user_id, "supervisor", "plan", 0, latency_ms, len(decision.steps) + 1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(decision.steps))) as executor:
                futures = [executor.submit(run_specialist, step.agent, step.subrequest) for step in decision.steps]
                results = [f.result() for f in futures]
            reply = "\n".join(results)
            self.db.append_conversation(user_id, "assistant", reply, agent_name="supervisor_plan")
            return reply

        target_agent = decision.agent or "multiple"
        if decision.kind == "route" and target_agent in ["planner", "events", "shopping", "finance", "knowledge", "journal"]:
            self.db.insert_routing_log(user_id, target_agent, decision.kind, 0, latency_ms, 2)
            reply = run_specialist(target_agent, decision.subrequest or text)
            self.db.append_conversation(user_id, "assistant", reply, agent_name=target_agent)
            return reply

        self.db.insert_routing_log(user_id, target_agent, decision.kind, 1, latency_ms, 1, "Specialists not implemented, delegated to fallback")
        reply = agent.run(user_id=user_id, question=text)
        self.db.append_conversation(user_id, "assistant", reply, agent_name="legacy_fallback")
        return reply

    def _handle_onboarding(self, user_id: str, text: str, settings: dict | None) -> str:
        state = settings.get("onboarding_state") if settings else None
        reply = "Setup is in an unknown state. Please contact the administrator."

        if not state:
            self.db.upsert_user_setting(user_id, "onboarding_state", "await_currency")
            reply = "Welcome! To help me manage your finances, please tell me your default 3-letter currency code (e.g., INR, SGD, USD)."

        elif state == "await_currency":
            currency = text.strip().upper()
            if not re.match(r"^[A-Z]{3}$", currency):
                reply = "Please enter a valid 3-letter currency code (e.g., INR, SGD, USD)."
            else:
                self.db.upsert_user_setting(user_id, "default_currency", currency)
                self.db.upsert_user_setting(user_id, "onboarding_state", "await_timezone")
                reply = f"Got it, {currency}. Next, what is your timezone? (e.g., Asia/Kolkata, Asia/Singapore, America/New_York)"

        elif state == "await_timezone":
            tz = text.strip()
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz)  # Will raise an exception if the timezone is not valid
                self.db.upsert_user_setting(user_id, "timezone", tz)
                self.db.upsert_user_setting(user_id, "onboarding_state", "await_family_size")
                reply = "Got it! Lastly, how many people are in your household? (This helps me estimate your grocery needs!)"
            except Exception:
                reply = "That doesn't look like a valid timezone. Please use a format like 'Asia/Singapore', 'Asia/Kolkata', or 'America/New_York'."

        elif state == "await_family_size":
            digits = "".join(filter(str.isdigit, text))
            if not digits or int(digits) < 1:
                reply = "Please enter a valid number (e.g., 2, 4)."
            else:
                self.db.upsert_user_setting(user_id, "family_size", digits)
                self.db.upsert_user_setting(user_id, "onboarding_state", "complete")
                reply = "Setup complete! How can I help you today?"

        return self._translate_onboarding(reply, text)

    def _translate_onboarding(self, system_reply: str, user_text: str) -> str:
        if not user_text.strip():
            return system_reply
            
        import requests
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            return system_reply
            
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        system_msg = (
            "You are an empathetic translation assistant. "
            "Translate the system's response into the exact language, tone, and script "
            "(e.g., Hindi, Hinglish, Spanish) used by the user in their input. "
            "If the user is speaking English, just return the system response exactly as is without translation. "
            "Only output the translated system response, nothing else. Do not use quotes."
        )
        
        prompt = f"User's input: '{user_text}'\n\nSystem's response to translate: '{system_reply}'"
        
        payload = {
            "model": os.getenv("GROQ_SUPERVISOR_MODEL", "llama-3.3-70b-versatile"),
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0
        }
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                translated = data["choices"][0]["message"]["content"].strip(' "\'')
                return translated
        except Exception as e:
            logger.warning("Onboarding translation failed: %s", e)
            
        return system_reply
