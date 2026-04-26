from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for Python < 3.9
    from backports.zoneinfo import ZoneInfo

from agent import Agent
from database import TaskDatabase
from supervisor import SupervisorAgent
from planner import PlannerAgent
from events_agent import EventsAgent
from finance_agent import FinanceAgent
from shopping_agent import ShoppingAgent


logger = logging.getLogger(__name__)


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"+{digits}" if digits else ""


def _load_allowlist() -> set[str]:
    raw = os.getenv("ALLOWED_USERS", "")
    vals = [_normalize_phone(v.strip()) for v in raw.split(",") if v.strip()]
    return {v for v in vals if v}


class AgentRouter:
    def __init__(self) -> None:
        self.agents: Dict[str, Agent] = {}
        self.db = TaskDatabase()
        self.supervisor = SupervisorAgent()
        self.planner = PlannerAgent()
        self.events = EventsAgent()
        self.finance = FinanceAgent()
        self.shopping = ShoppingAgent()

    def _get_agent(self, user_id: str) -> Agent:
        if user_id not in self.agents:
            self.agents[user_id] = Agent()
        return self.agents[user_id]

    def route(self, from_phone: str, text: str) -> str:
        allowlist = _load_allowlist()
        user_id = _normalize_phone(from_phone.strip())
        if not user_id:
            return "Invalid sender phone number."
        if not allowlist:
            logger.error("ALLOWED_USERS is empty. Denying all incoming requests by default.")
            return "Bot is locked: no allowlisted users configured."
        if user_id not in allowlist:
            return "Sorry, this bot is private. Your number is not allowlisted."

        # 1. Onboarding State Machine
        settings = self.db.get_user_settings(user_id)
        if not settings or settings.get("onboarding_state") != "complete":
            return self._handle_onboarding(user_id, text, settings)

        user_tz_str = (settings or {}).get("timezone", "UTC")
        try:
            user_tz = ZoneInfo(user_tz_str)
        except Exception:
            logger.warning("Invalid timezone '%s' for user %s, falling back to UTC.", user_tz_str, user_id)
            user_tz = timezone.utc
        now_local = datetime.now(user_tz)

        # 2. Pre-Router Fast Path (Bypass LLM)
        agent = self._get_agent(user_id)
        fast_reply = agent._maybe_run_direct_tool(user_id, text)
        if fast_reply:
            self.db.append_conversation(user_id, "user", text)
            self.db.append_conversation(user_id, "assistant", fast_reply, agent_name="fast_path")
            return fast_reply

        # 3. Supervisor Routing
        start_time = time.time()
        recent_turns = self.db.get_recent_conversations(user_id, limit=10)
        decision = self.supervisor.decide(user_id, text, recent_turns, settings)
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

        # 4. Dispatch to Specialists (Using legacy fallback as a bridge until specialists are built)
        target_agent = decision.agent or "multiple"
        
        if decision.kind == "route" and target_agent == "planner":
            bundle = {
                "user_id": user_id,
                "subrequest": decision.subrequest or text,
                "original_message": text,
                "user_settings": settings or {},
                "recent_turns": recent_turns
            }
            self.db.insert_routing_log(user_id, "planner", decision.kind, 0, latency_ms, 2)
            result = self.planner.handle(bundle)
            reply = result.get("text", "Done.")
            self.db.append_conversation(user_id, "assistant", reply, agent_name="planner")
            return reply
            
        if decision.kind == "route" and target_agent == "events":
            bundle = {
                "user_id": user_id,
                "subrequest": decision.subrequest or text,
                "original_message": text,
                "user_settings": settings or {},
                "recent_turns": recent_turns,
                "now_local_iso": now_local.isoformat()
            }
            self.db.insert_routing_log(user_id, "events", decision.kind, 0, latency_ms, 2)
            result = self.events.handle(bundle)
            reply = result.get("text", "Done.")
            self.db.append_conversation(user_id, "assistant", reply, agent_name="events")
            return reply

        if decision.kind == "route" and target_agent == "finance":
            bundle = {
                "user_id": user_id,
                "subrequest": decision.subrequest or text,
                "original_message": text,
                "user_settings": settings or {},
                "recent_turns": recent_turns,
                "now_local_iso": now_local.isoformat()
            }
            self.db.insert_routing_log(user_id, "finance", decision.kind, 0, latency_ms, 2)
            result = self.finance.handle(bundle)
            reply = result.get("text", "Done.")
            self.db.append_conversation(user_id, "assistant", reply, agent_name="finance")
            return reply
            
        if decision.kind == "route" and target_agent == "shopping":
            bundle = {
                "user_id": user_id,
                "subrequest": decision.subrequest or text,
                "original_message": text,
                "user_settings": settings or {},
                "recent_turns": recent_turns,
                "now_local_iso": now_local.isoformat()
            }
            self.db.insert_routing_log(user_id, "shopping", decision.kind, 0, latency_ms, 2)
            result = self.shopping.handle(bundle)
            reply = result.get("text", "Done.")
            self.db.append_conversation(user_id, "assistant", reply, agent_name="shopping")
            return reply
            
        self.db.insert_routing_log(user_id, target_agent, decision.kind, 1, latency_ms, 1, "Specialists not implemented, delegated to fallback")
        reply = agent.run(user_id=user_id, question=text)
        self.db.append_conversation(user_id, "assistant", reply, agent_name="legacy_fallback")
        return reply

    def _handle_onboarding(self, user_id: str, text: str, settings: dict | None) -> str:
        state = settings.get("onboarding_state") if settings else None

        if not state:
            self.db.upsert_user_setting(user_id, "onboarding_state", "await_currency")
            return "Welcome! To help me manage your finances, please tell me your default 3-letter currency code (e.g., INR, SGD, USD)."

        if state == "await_currency":
            currency = text.strip().upper()
            if not re.match(r"^[A-Z]{3}$", currency):
                return "Please enter a valid 3-letter currency code (e.g., INR, SGD, USD)."
            self.db.upsert_user_setting(user_id, "default_currency", currency)
            self.db.upsert_user_setting(user_id, "onboarding_state", "await_timezone")
            return f"Got it, {currency}. Next, what is your timezone? (e.g., Asia/Kolkata, Asia/Singapore, America/New_York)"

        if state == "await_timezone":
            tz = text.strip()
            if "/" not in tz:  # Extremely basic IANA validation
                return "Please enter a valid timezone format like 'Asia/Kolkata' or 'America/New_York'."
            self.db.upsert_user_setting(user_id, "timezone", tz)
            self.db.upsert_user_setting(user_id, "onboarding_state", "complete")
            return "Setup complete! How can I help you today?"

        return "Setup is in an unknown state. Please contact the administrator."
