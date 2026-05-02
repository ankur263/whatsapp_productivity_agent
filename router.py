from __future__ import annotations

import logging
import os
import re
import time
import json
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
            profile_reply = self._maybe_handle_household_profile_update(user_id, text, settings)
            if profile_reply:
                return profile_reply

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
                if target["role"] == "owner" and self.db.get_household_owner_count(target["id"]) <= 1:
                    return "❌ You cannot leave this household because you are the last owner."
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

        if settings and settings.get("onboarding_state") == "complete":
            profile_reply = self._maybe_handle_household_profile_update(user_id, text, settings)
            if profile_reply:
                return profile_reply

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
                    "household_profile": self._effective_household_profile(settings),
                    "household_profile_summary": self._format_household_profile(self._effective_household_profile(settings)),
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

    def _profile_total(self, profile: dict | None) -> int:
        if not profile:
            return 0
        return sum(max(0, int(profile.get(k) or 0)) for k in ["adults", "children", "babies", "people"])

    def _normalize_household_profile(self, profile: dict | None) -> dict:
        base = {"adults": 0, "children": 0, "babies": 0, "people": 0}
        if profile:
            for key in base:
                try:
                    base[key] = max(0, int(profile.get(key) or 0))
                except (TypeError, ValueError):
                    base[key] = 0
        return base

    def _load_profile_json(self, raw: str | None) -> dict | None:
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        profile = self._normalize_household_profile(parsed)
        return profile if self._profile_total(profile) else None

    def _profile_from_family_size(self, raw: str | int | None) -> dict | None:
        try:
            size = int(raw) if raw is not None and str(raw).strip() else 0
        except (TypeError, ValueError):
            size = 0
        if size < 1:
            return None
        return {"adults": 0, "children": 0, "babies": 0, "people": size}

    def _effective_household_profile(self, settings: dict | None) -> dict:
        settings = settings or {}
        hh_id = settings.get("active_household_id")
        if hh_id:
            hh_profile = self._load_profile_json(self.db.get_household_profile(hh_id))
            if hh_profile:
                return hh_profile
            hh_size = self.db.get_household_family_size(hh_id)
            from_size = self._profile_from_family_size(hh_size)
            if from_size:
                return from_size
        user_profile = self._load_profile_json(settings.get("household_profile"))
        if user_profile:
            return user_profile
        return self._profile_from_family_size(settings.get("family_size")) or {"adults": 0, "children": 0, "babies": 0, "people": 1}

    def _effective_family_size(self, settings: dict | None) -> int:
        return self._profile_total(self._effective_household_profile(settings)) or 1

    def _format_household_profile(self, profile: dict | None) -> str:
        profile = self._normalize_household_profile(profile)
        parts = []
        labels = [
            ("adults", "adult", "adults"),
            ("children", "child", "children"),
            ("babies", "baby", "babies"),
            ("people", "person", "people"),
        ]
        for key, singular, plural in labels:
            count = profile.get(key, 0)
            if count:
                parts.append(f"{count} {singular if count == 1 else plural}")
        return " + ".join(parts) if parts else "1 person"

    def _save_household_profile(self, user_id: str, settings: dict | None, profile: dict) -> dict:
        profile = self._normalize_household_profile(profile)
        total = self._profile_total(profile)
        if total < 1:
            raise ValueError("Household profile must include at least one person.")
        profile_json = json.dumps(profile, sort_keys=True, separators=(",", ":"))
        self.db.upsert_user_setting(user_id, "family_size", str(total))
        self.db.upsert_user_setting(user_id, "household_profile", profile_json)
        hh_id = (settings or {}).get("active_household_id")
        if hh_id:
            self.db.set_household_profile(hh_id, profile_json, total)
        if settings is not None:
            settings["family_size"] = str(total)
            settings["household_profile"] = profile_json
        return profile

    def _parse_household_profile_text(self, text: str, current_profile: dict | None = None, default_operation: str = "replace") -> dict:
        original = (text or "").strip()
        lowered = original.lower()
        if not lowered:
            return {"ok": False, "reason": "empty"}

        word_map = {
            "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
            "ek": "1", "do": "2", "teen": "3", "char": "4", "chaar": "4",
            "paanch": "5", "panch": "5", "che": "6", "chah": "6", "saat": "7",
            "aath": "8", "nau": "9", "das": "10", "एक": "1", "दो": "2", "तीन": "3", "चार": "4"
        }
        normalized = lowered.replace("&", " and ")
        normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
        normalized = re.sub(r"\b(a|an)\b", "1", normalized)
        for word, digit in word_map.items():
            normalized = re.sub(rf"\b{re.escape(word)}\b", digit, normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()

        replace_markers = [
            "actually", "correction", "instead", "make it", "change to", "set to",
            "we are", "we re", "total", "overall", "only"
        ]
        add_markers = [
            "add", "also", "plus", "include", "including", "another", "more", "extra", "along with"
        ]
        starts_additive = re.match(r"^(and|plus|also|add)\b", normalized) is not None
        operation = default_operation
        if any(marker in normalized for marker in replace_markers):
            operation = "replace"
        elif starts_additive or any(marker in normalized for marker in add_markers):
            operation = "add"

        profile = {"adults": 0, "children": 0, "babies": 0, "people": 0}
        category_patterns = [
            ("babies", r"babies|baby|infants?|newborns?"),
            ("children", r"children|child|kids?|toddlers?"),
            ("adults", r"adults?|grownups?|grown ups?"),
            ("people", r"people|persons?|members?|family members?"),
        ]

        for key, pattern in category_patterns:
            combined = rf"(?:\b(?P<count1>\d{{1,2}})\s+(?P<cat1>{pattern})\b|\b(?P<cat2>{pattern})\s+(?P<count2>\d{{1,2}})\b)"
            for match in re.finditer(combined, normalized):
                count = match.group("count1") or match.group("count2")
                if count is not None:
                    value = int(count)
                    if value <= 50:
                        profile[key] += value

        relation_terms = [
            "wife", "husband", "spouse", "partner", "mom", "mother", "mum", "dad", "father",
            "parent", "grandma", "grandmother", "grandpa", "grandfather"
        ]
        relation_count = sum(1 for term in relation_terms if re.search(rf"\b{term}\b", normalized))
        relation_context = (
            default_operation == "replace"
            or any(marker in normalized for marker in ["household", "family", "also", "plus", "include", "including", "along with"])
            or starts_additive
        )
        if relation_count and relation_context:
            profile["adults"] += relation_count
            if re.search(r"\b(me|myself)\b", normalized):
                profile["adults"] += 1
            elif not re.search(r"\b(i am|i m|we are|we re)\b", normalized):
                profile["adults"] += 1
            if not profile["babies"] and re.search(r"\b(baby|infant|newborn)\b", normalized):
                profile["babies"] += 1
            if not profile["children"] and re.search(r"\b(child|kid|toddler)\b", normalized):
                profile["children"] += 1

        mentioned_category_without_count = False
        if not self._profile_total(profile):
            for key, pattern in category_patterns:
                if re.search(rf"\b(?:{pattern})\b", normalized):
                    mentioned_category_without_count = True
                    bare_addition = re.fullmatch(
                        rf"(?:and\s+|plus\s+|also\s+|add\s+)?(?:{pattern})(?:\s+also)?",
                        normalized,
                    )
                    if operation == "add" and key != "people" and bare_addition:
                        profile[key] += 1
                    break

        if not self._profile_total(profile):
            number_tokens = [int(n) for n in re.findall(r"\b\d{1,2}\b", normalized) if int(n) <= 50]
            has_household_context = any(
                marker in normalized
                for marker in ["household", "family", "people", "person", "member", "we are", "we re", "make it", "total"]
            )
            if len(number_tokens) == 1 and (default_operation == "replace" or has_household_context):
                profile["people"] = number_tokens[0]

        if self._profile_total(profile) < 1:
            return {"ok": False, "reason": "ambiguous" if mentioned_category_without_count else "no_profile"}

        if operation == "add":
            current = self._normalize_household_profile(current_profile)
            addition_has_specific_people = any(profile[k] for k in ["adults", "children", "babies"])
            if current.get("people") and addition_has_specific_people:
                current["adults"] += current["people"]
                current["people"] = 0
            for key, value in profile.items():
                current[key] += value
            profile = current

        return {"ok": True, "operation": operation, "profile": self._normalize_household_profile(profile)}

    def _maybe_handle_household_profile_update(self, user_id: str, text: str, settings: dict | None) -> str | None:
        if (text or "").lstrip().startswith("/"):
            return None

        parsed = self._parse_household_profile_text(
            text,
            current_profile=self._effective_household_profile(settings),
            default_operation="add",
        )
        if not parsed.get("ok"):
            return None

        canonical = self._canonical_text(text)
        profile_words = {
            "adult", "adults", "child", "children", "kid", "kids", "baby", "babies",
            "infant", "newborn", "people", "person", "members", "family", "household",
            "wife", "husband", "spouse", "partner", "mom", "mother", "dad", "father",
            "actually", "also", "plus", "include", "including", "make it", "we are"
        }
        if not any(word in canonical for word in profile_words):
            return None

        profile = self._save_household_profile(user_id, settings, parsed["profile"])
        return f"✅ Household updated: *{self._format_household_profile(profile)}*. I'll use this for grocery estimates."

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
        if owner.get("household_profile"):
            self.db.upsert_user_setting(user_id, "household_profile", owner["household_profile"])
        if owner.get("locale"):
            self.db.upsert_user_setting(user_id, "locale", owner["locale"])
        return {"default_currency": currency, "timezone": tz}

    def _handle_settings_command(self, user_id: str, text: str) -> str:
        parts = text.split(maxsplit=2)
        settings = self.db.get_user_settings(user_id) or {}
        currency = settings.get("default_currency") or "—"
        tz = settings.get("timezone") or "—"
        household = self._format_household_profile(self._effective_household_profile(settings))

        if len(parts) == 1:
            return (
                "⚙️ *Your settings*\n"
                f"• Currency: *{currency}*\n"
                f"• Timezone: *{tz}*\n"
                f"• Household: *{household}*\n\n"
                "To change:\n"
                "• `/settings currency USD`\n"
                "• `/settings timezone Asia/Kolkata`\n"
                "• `/settings household 3 adults 1 baby`"
            )

        if len(parts) < 3:
            return "❌ Usage: `/settings currency USD`, `/settings timezone Asia/Kolkata`, or `/settings household 3 adults 1 baby`"

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
        if key in {"household", "family", "members"}:
            parsed = self._parse_household_profile_text(
                value,
                current_profile=self._effective_household_profile(settings),
                default_operation="replace",
            )
            if not parsed.get("ok"):
                return "❌ Please describe your household like `3 adults 1 baby`, `2 adults 1 child`, or `4 people`."
            profile = self._save_household_profile(user_id, settings, parsed["profile"])
            return f"✅ Household updated to *{self._format_household_profile(profile)}*."
        return "❌ Unknown setting. Use `currency`, `timezone`, or `household`."

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
                reply = "Got it! Lastly, who is in your household for grocery planning? You can say `3 adults`, `2 adults 1 child`, or `3 adults 1 baby`."
            except Exception:
                reply = "That doesn't look like a valid timezone. Please use a format like 'Asia/Singapore', 'Asia/Kolkata', or 'America/New_York'."

        elif state == "await_family_size":
            parsed = self._parse_household_profile_text(text, default_operation="replace")
            if not parsed.get("ok"):
                reply = "Please describe your household like `3 adults`, `2 adults 1 child`, or `3 adults 1 baby`. You can also say `4 people`."
            else:
                profile = self._save_household_profile(user_id, settings, parsed["profile"])
                family_size = self._profile_total(profile)
                household_summary = self._format_household_profile(profile)

                # Check if they already joined a household via an invite code
                if settings and settings.get("active_household_id"):
                    hh_id = settings["active_household_id"]
                    if self.db.get_household_family_size(hh_id) is None:
                        self.db.set_household_profile(
                            hh_id,
                            json.dumps(profile, sort_keys=True, separators=(",", ":")),
                            family_size,
                        )
                    self.db.upsert_user_setting(user_id, "onboarding_state", "complete")
                    reply = (
                        f"Done! ✅ Household: *{household_summary}*. You’re all set.\n\n"
                        "Try something simple:\n"
                        "• “Remind me to call mom tomorrow at 10”\n"
                        "• “Add milk to my shopping list”\n"
                        "• “Log $20 for lunch”"
                    )
                else:
                    self.db.upsert_user_setting(user_id, "onboarding_state", "await_first_action")
                    reply = (
                        f"Done! ✅ Household: *{household_summary}*. You’re all set.\n\n"
                        "Try something simple:\n"
                        "• “Remind me to call mom tomorrow at 10”\n"
                        "• “Add milk to my shopping list”\n"
                        "• “Log $20 for lunch”\n\n"
                        "You can refine the household now, like “and 1 baby also”, or update it later with `/settings household 3 adults 1 baby`.\n\n"
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
