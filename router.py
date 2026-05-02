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

OFF_TASK_GUIDANCE_REPLY = (
    "I can help with tasks, groceries, reminders, and expenses.\n"
    "Try: \"add milk\", \"show my grocery list\", \"remind me to call mom at 7pm\", or \"spent 200 on ghee\"."
)


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

        # 1. Developer Backdoor (Hidden command to bypass onboarding for automated tests)
        if text.startswith("/claim "):
            key = text.split(" ", 1)[1].strip()
            if key == os.getenv("ADMIN_MASTER_KEY", "admin2026"):
                self.db.upsert_user_setting(user_id, "is_allowed", "1")
                self.db.create_household(user_id, "My Home")
                self.db.upsert_user_setting(user_id, "onboarding_state", "complete")
                return "✅ Admin access granted. Created household 'My Home'. Setup complete."
            return "❌ Invalid master key."

        # 1. Slash Commands (Administrative & Workspaces)
        if text.startswith("/join "):
            code = text.split(" ", 1)[1].strip()
            res = self.db.consume_invite(user_id, code)
            if res:
                self.db.upsert_user_setting(user_id, "is_allowed", "1")
                settings = self.db.get_user_settings(user_id) or {}
                if settings.get("onboarding_state") == "complete":
                    return f"🎉 Success! You have joined *{res['name']}*. You can now start adding groceries or tasks."

                inherited = self._inherit_household_profile(user_id, res["household_id"])
                if inherited:
                    self.db.upsert_user_setting(user_id, "onboarding_state", "complete")
                    return (
                        f"🎉 Success! You have joined *{res['name']}*.\n\n"
                        f"I've set you up with *{inherited['default_currency']}* and *{inherited['timezone']}* (matching the household).\n"
                        f"Reply `/settings` to change them."
                    )
                self.db.upsert_user_setting(user_id, "onboarding_state", "await_currency")
            return (
                f"🎉 Success! You have joined *{res['name']}*.\n\n"
                "Before we start, I need to quickly set up your personal profile.\n\n"
                "What is your default 3-letter currency code (e.g., INR, SGD, USD)?"
            )
            return "❌ Invalid or expired invite code."

        settings = self.db.get_user_settings(user_id)
        is_allowed = int(settings.get("is_allowed", 0)) if settings else 0

        if not is_allowed:
            text_upper = text.strip().upper()
            if text_upper == "YES":
                self.db.upsert_user_setting(user_id, "is_allowed", "1")
                self.db.upsert_user_setting(user_id, "onboarding_state", "await_currency")
                return (
                    "🎉 Welcome to Home OS! Let's get your profile set up.\n\n"
                    "First, what is your default 3-letter currency code (e.g., INR, SGD, USD)?"
                )
            elif text_upper == "STOP":
                return "Got it. You won't receive any more messages from me. If you ever change your mind, just reply YES."
            else:
                return (
                    "Hi! I'm Home OS, a private WhatsApp assistant for:\n"
                    "• Reminders & tasks\n"
                    "• Personal finance\n"
                    "• Groceries & shopping\n"
                    "• Journal & notes\n\n"
                    "Use it solo, or invite others to collaborate.\n"
                    "Reply *YES* to set up your free account (takes 30 seconds).\n"
                    "Reply *STOP* anytime to opt out.\n\n"
                    "_(By continuing, you agree to our Privacy Policy)_"
                )

        # Intercept the first real action to set up a workspace
        if settings and settings.get("onboarding_state") == "await_first_action":
            clean_text_for_greeting = re.sub(r"[^\w\s]", "", text.strip().lower())
            is_greeting = clean_text_for_greeting in ["hi", "hello", "hey", "help", "test", "ping"]
            
            if not text.startswith("/") and not media_bytes and not is_greeting:
                self.db.set_pending_clarification(user_id, text)
                self.db.upsert_user_setting(user_id, "onboarding_state", "await_workspace_type")
                reply = (
                    "I can set that up for you! Before I do, what kind of space would you like?\n\n"
                    "👤 **Personal** (Just for you)\n"
                    "👥 **Shared** (To collaborate with others)\n\n"
                    "*Reply with \"Personal\" or \"Shared\".*"
                )
                recent_turns = self.db.get_recent_conversations(user_id, limit=3)
                return self._translate_onboarding(reply, text, recent_turns)


        if text.startswith("/invite"):
            hh_id = self.db.get_active_household(user_id)
            if not hh_id:
                return "❌ You are not currently active in any household."
            code = self.db.create_invite(hh_id, user_id)
            return f"🎟️ *Invite Code: {code}*\n\nSend this code to your family or helper. They just need to text me:\n`/join {code}`\n\n_(Code expires in 24 hours)_"

        if text.startswith("/settings"):
            return self._handle_settings_command(user_id, text)

        if text.startswith("/switch"):
            parts = text.split(" ", 1)
            hhs = self.db.get_user_households(user_id)
            if len(parts) == 1:
                lines = ["🏠 *Your Households:*"]
                lines.append("0. Personal Space")
                for idx, h in enumerate(hhs, 1):
                    lines.append(f"{idx}. {h['name']} ({h['role']})")
                lines.append("\nReply `/switch <number>` to change.")
                return "\n".join(lines)
            try:
                idx = int(parts[1].strip())
                if idx == 0:
                    self.db.upsert_user_setting(user_id, "active_household_id", "")
                    return "✅ Switched active household to *Personal Space*."
                if idx < 0: raise ValueError()
                target = hhs[idx - 1]
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
            # Emergency Override: If they upload a file anytime during workspace setup, save the file by defaulting to Personal
            setup_states = ["await_first_action", "await_workspace_type", "await_workspace_name"]
            if settings and settings.get("onboarding_state") in setup_states and media_bytes:
                self.db.upsert_user_setting(user_id, "onboarding_state", "complete")
                self.db.upsert_user_setting(user_id, "active_household_id", "")
                self.db.append_conversation(user_id, "assistant", "Auto-created your Personal Space to process this file.", agent_name="system")
                settings["onboarding_state"] = "complete" # Allow it to fall through to the Supervisor
            else:
                recent_turns = self.db.get_recent_conversations(user_id, limit=3)
                return self._handle_onboarding(user_id, text, settings, from_phone, recent_turns)

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
            if self._is_non_task_chat(text):
                reply = OFF_TASK_GUIDANCE_REPLY
                self.db.append_conversation(user_id, "user", text)
                self.db.append_conversation(user_id, "assistant", reply, agent_name="offtask_guard")
                self.db.insert_routing_log(
                    user_id, "supervisor", "off_task", 0, 0, 0, "Deterministic off-task guard"
                )
                return reply

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
            safe_reply = self._sanitize_supervisor_reply(text, decision.text)
            self.db.insert_routing_log(user_id, "supervisor", "reply", 0, latency_ms, 1)
            self.db.append_conversation(user_id, "assistant", safe_reply, agent_name="supervisor")
            return safe_reply

        # Handle Supervisor Clarification
        if decision.kind == "clarify" and decision.text:
            save_text = pending_msg if pending_msg else text
            self.db.set_pending_clarification(user_id, save_text)
            self.db.insert_routing_log(user_id, "supervisor", "clarify", 0, latency_ms, 1)
            self.db.append_conversation(user_id, "assistant", decision.text, agent_name="supervisor")
            return decision.text

        # 4. Dispatch to Specialists
        def run_specialist(agent_name: str, subrequest: str) -> str:
            try:
                bundle = {
                    "user_id": user_id,
                    "subrequest": subrequest,
                    "original_message": text,
                    "user_settings": settings or {},
                    "family_size": self._effective_family_size(settings),
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
            except Exception as e:
                logger.exception("Specialist %s failed", agent_name)
                return f"⚠️ {agent_name.title()} Specialist encountered an error and could not complete."

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

    def _sanitize_supervisor_reply(self, user_text: str, supervisor_text: str) -> str:
        clean = (supervisor_text or "").strip()
        if not clean:
            return OFF_TASK_GUIDANCE_REPLY
        if self._looks_like_echo(user_text, clean):
            logger.info("echo_blocked user_text=%r supervisor_reply=%r", user_text[:100], clean[:100])
            return OFF_TASK_GUIDANCE_REPLY
        return clean

    def _is_non_task_chat(self, text: str) -> bool:
        canonical = self._canonical_text(text)
        if not canonical:
            return True

        if re.fullmatch(r"[\W_]+", text, flags=re.UNICODE):
            return True

        smalltalk = {
            "hi", "hii", "hiii", "hello", "hey", "yo", "sup", "whats up", "whatsup",
            "good morning", "good night", "help", "hmm", "ok", "okay", "thanks",
            "thank you", "lol", "haha", "hehe", "test", "ping",
        }
        profanity_or_noise = {
            "chutiye", "chutiya", "madarchod", "bkl", "bc", "mc", "gaandu", "gandu", "fck", "wtf",
        }
        if canonical in smalltalk or canonical in profanity_or_noise:
            return True

        return False

    def _looks_like_echo(self, user_text: str, reply_text: str) -> bool:
        user_canon = self._canonical_text(user_text)
        reply_canon = self._canonical_text(reply_text)
        if not user_canon or not reply_canon:
            return False
        if user_canon == reply_canon:
            return True
        if abs(len(user_canon) - len(reply_canon)) <= 2 and (
            user_canon.startswith(reply_canon) or reply_canon.startswith(user_canon)
        ):
            return True
        return False

    def _canonical_text(self, text: str) -> str:
        cleaned = re.sub(r"[^\w\s]", "", (text or "").strip().lower(), flags=re.UNICODE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _effective_family_size(self, settings: dict | None) -> int:
        hh_id = (settings or {}).get("active_household_id")
        if hh_id:
            size = self.db.get_household_family_size(hh_id)
            if size:
                return size
        raw = (settings or {}).get("family_size")
        try:
            return int(raw) if raw else 1
        except (TypeError, ValueError):
            return 1

    def _inherit_household_profile(self, user_id: str, household_id: str) -> dict | None:
        owner_id = self.db.get_household_owner(household_id)
        if not owner_id or owner_id == user_id:
            return None
        owner = self.db.get_user_settings(owner_id) or {}
        currency = owner.get("default_currency")
        tz = owner.get("timezone")
        if not currency or not tz:
            return None
        self.db.upsert_user_setting(user_id, "default_currency", currency)
        self.db.upsert_user_setting(user_id, "timezone", tz)
        if owner.get("family_size"):
            self.db.upsert_user_setting(user_id, "family_size", owner["family_size"])
        if owner.get("locale"):
            self.db.upsert_user_setting(user_id, "locale", owner["locale"])
        return {"default_currency": currency, "timezone": tz}

    def _handle_settings_command(self, user_id: str, text: str) -> str:
        parts = text.split(maxsplit=2)
        settings = self.db.get_user_settings(user_id) or {}
        currency = settings.get("default_currency") or "—"
        tz = settings.get("timezone") or "—"

        if len(parts) == 1:
            return (
                "⚙️ *Your settings*\n"
                f"• Currency: *{currency}*\n"
                f"• Timezone: *{tz}*\n\n"
                "To change:\n"
                "• `/settings currency USD`\n"
                "• `/settings timezone Asia/Kolkata`"
            )

        if len(parts) < 3:
            return "❌ Usage: `/settings currency USD` or `/settings timezone Asia/Kolkata`"

        key, value = parts[1].lower(), parts[2].strip()
        if key == "currency":
            code = value.upper()
            if not re.match(r"^[A-Z]{3}$", code):
                return "❌ Please provide a valid 3-letter currency code (e.g., INR, SGD, USD)."
            self.db.upsert_user_setting(user_id, "default_currency", code)
            return f"✅ Currency updated to *{code}*."
        if key == "timezone":
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(value)
            except Exception:
                return "❌ That doesn't look like a valid timezone. Use a format like `Asia/Singapore` or `America/New_York`."
            self.db.upsert_user_setting(user_id, "timezone", value)
            return f"✅ Timezone updated to *{value}*."
        return "❌ Unknown setting. Use `currency` or `timezone`."

    def _handle_onboarding(self, user_id: str, text: str, settings: dict | None, from_phone: str, recent_turns: list[dict]) -> str:
        state = settings.get("onboarding_state") if settings else None
        reply = "Setup is in an unknown state. Please contact the administrator."

        if not state:
            self.db.upsert_user_setting(user_id, "onboarding_state", "await_currency")
            reply = (
                "Welcome! To help me manage your finances, I need to set up your profile.\n\n"
                "Please tell me your default 3-letter currency code (e.g., INR, SGD, USD)."
            )

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
            word_map = {
                "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
                "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4",
                "paanch": "5", "panch": "5", "che": "6", "chah": "6", "saat": "7",
                "aath": "8", "nau": "9", "das": "10", "एक": "1", "दो": "2", "तीन": "3", "चार": "4"
            }
            processed_text = text.strip().lower()
            for word, digit in word_map.items():
                processed_text = re.sub(rf"\b{word}\b", digit, processed_text)
            
            digits = "".join(filter(str.isdigit, processed_text))
            if not digits or int(digits) < 1:
                reply = "Please enter a valid number (e.g., 2, 4)."
            else:
                self.db.upsert_user_setting(user_id, "family_size", digits)

                # Check if they already joined a household via an invite code
                if settings and settings.get("active_household_id"):
                    hh_id = settings["active_household_id"]
                    if self.db.get_household_family_size(hh_id) is None:
                        self.db.set_household_family_size(hh_id, int(digits))
                    self.db.upsert_user_setting(user_id, "onboarding_state", "complete")
                    reply = (
                        "Done! ✅ You’re all set.\n\n"
                        "Try something simple:\n"
                        "• “Remind me to call mom tomorrow at 10”\n"
                        "• “Add milk to my shopping list”\n"
                        "• “Log $20 for lunch”"
                    )
                else:
                    self.db.upsert_user_setting(user_id, "onboarding_state", "await_first_action")
                    reply = (
                        "Done! ✅ You’re all set.\n\n"
                        "Try something simple:\n"
                        "• “Remind me to call mom tomorrow at 10”\n"
                        "• “Add milk to my shopping list”\n"
                        "• “Log $20 for lunch”\n\n"
                        "Note: You can use this solo, or with friends, family, and teammates. I can also create a shared space for you in seconds whenever you're ready."
                    )

        elif state == "await_first_action":
            reply = "Please send a normal task (like 'add milk' or 'remind me to call mom') to complete your workspace setup!"

        elif state == "await_workspace_type":
            clean_text = text.strip().lower()
            original_command = self.db.get_and_clear_pending_clarification(user_id)

            if "personal" in clean_text or clean_text in ["p", "1"]:
                self.db.upsert_user_setting(user_id, "onboarding_state", "complete")
                self.db.upsert_user_setting(user_id, "active_household_id", "")
                reply = "Great! 👍 Your personal space is ready."
                if original_command:
                    command_reply = self.route(from_phone, original_command)
                    reply += f"\n\n{command_reply}"
            elif "shared" in clean_text or clean_text in ["s", "2"]:
                self.db.upsert_user_setting(user_id, "onboarding_state", "await_workspace_name")
                reply = (
                    "Got it! 👥 Let's name your shared group.\n\n"
                    "Suggested name: **\"Home\"**\n\n"
                    "Reply **YES** to use this, or just type a different name (like \"Beach Trip\" or \"The Smiths\")."
                )
                if original_command:
                    self.db.set_pending_clarification(user_id, original_command)
            elif clean_text in ["cancel", "exit", "stop", "hi", "hello"]:
                # Allow the user to escape the menu trap
                self.db.upsert_user_setting(user_id, "onboarding_state", "await_first_action")
                reply = "Workspace setup paused. I'm ready whenever you want to add a task or list!"
            else:
                reply = "Please reply with 'Personal' or 'Shared'."
                if original_command:
                    self.db.set_pending_clarification(user_id, original_command)

        elif state == "await_workspace_name":
            name = text.strip()
            if not name:
                reply = "Please provide a name for your group."
            else:
                if name.upper() == 'YES':
                    name = 'Home'
                self.db.create_household(user_id, name)
                self.db.upsert_user_setting(user_id, "onboarding_state", "complete")
                original_command = self.db.get_and_clear_pending_clarification(user_id)
                reply = f"Group **\"{name}\"** created! ✅"
                if original_command:
                    command_reply = self.route(from_phone, original_command)
                    reply += f"\n\n{command_reply}"
                reply += "\n\n💡 Want to invite members right now? Just text me `/invite` to get your secret join code!"

        return self._translate_onboarding(reply, text, recent_turns)

    def _translate_onboarding(self, system_reply: str, user_text: str, recent_turns: list[dict]) -> str:
        clean_text = user_text.strip()
        if not clean_text:
            return system_reply
            
        # Bypass translation for numbers, timezones, and short codes to prevent LLM language hallucination
        bypass_words = {"yes", "no", "stop", "personal", "shared", "p", "s"}
        if clean_text.isdigit() or "/" in clean_text or clean_text.lower() in bypass_words:
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
        
        turn_lines = [f"{t.get('role', 'unknown').upper()}: {t.get('content', '')}" for t in recent_turns]
        history_context = "\n".join(turn_lines)
        prompt = f"Recent Conversation:\n{history_context}\n\nUser's latest input: '{user_text}'\n\nSystem's response to translate: '{system_reply}'"
        
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
