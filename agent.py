from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from collections import defaultdict, deque
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from memory import MemoryStore
from prompts import build_system_prompt
from tools import run_tool, tools_description_text


logger = logging.getLogger(__name__)

ACTION_LINE_RE = re.compile(r"^\s*Action:\s*([A-Za-z_]\w*)(?:\s*\((.*)\))?\s*$")
FINAL_RE = re.compile(r"Final Answer:\s*(.*)$", re.DOTALL)


@dataclass
class AgentConfig:
    llm_backend: str = field(
        default_factory=lambda: os.getenv("LLM_BACKEND", "ollama").strip().lower()
    )
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.2").strip()
    )
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
    )
    openai_model: str = field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    )
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-1.5-flash-latest").strip()
    )
    max_steps: int = field(
        default_factory=lambda: int(os.getenv("MAX_AGENT_STEPS", "6"))
    )
    max_history: int = 20


class Agent:
    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        self.system_prompt = build_system_prompt(tools_description_text())
        self.memory = MemoryStore(backend=os.getenv("MEMORY_BACKEND", "chroma"))
        self.history: dict[str, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=self.config.max_history)
        )

    def run(self, user_id: str, question: str) -> str:
        user_id = user_id.strip() or "default_user"
        question = question.strip()
        if not question:
            return "Please send a non-empty question."

        # Deterministic fast-path for high-confidence productivity commands.
        direct = self._maybe_run_direct_tool(user_id, question)
        if direct is not None:
            self.history[user_id].append({"role": "user", "content": question})
            self.history[user_id].append({"role": "assistant", "content": direct})
            self.memory.remember(user_id, f"User asked: {question}", metadata={"type": "user_query"})
            self.memory.remember(user_id, f"Assistant replied: {direct}", metadata={"type": "assistant_reply"})
            return direct

        recalled = self.memory.recall(user_id, question, k=3)
        memory_context = "\n".join(f"- {m}" for m in recalled) if recalled else "(none)"

        scratchpad = ""
        final_answer = ""
        for step in range(1, self.config.max_steps + 1):
            prompt = self._build_turn_prompt(user_id, question, memory_context, scratchpad)
            llm_output = self._chat(prompt)
            logger.info("agent_step=%s backend=%s output=%s", step, self.config.llm_backend, llm_output[:500])

            final = self._extract_final_answer(llm_output)
            if final is not None:
                final_answer = final.strip()
                break

            parsed = self._extract_action(llm_output)
            if parsed is None:
                coerced = self._coerce_plain_final_answer(llm_output)
                if coerced:
                    final_answer = coerced
                else:
                    final_answer = (
                        "I couldn't parse a valid tool action. "
                        "Please rephrase your request."
                    )
                break

            tool_name, tool_arg = parsed
            observation = run_tool(tool_name, tool_arg, user_id)
            scratchpad += (
                f"{llm_output.strip()}\n"
                f"Observation: {observation}\n"
            )

        if not final_answer:
            last_observation = self._latest_observation_from_scratchpad(scratchpad)
            if last_observation:
                final_answer = last_observation
            else:
                final_answer = "I couldn't reach a final answer within step limit."

        self.history[user_id].append({"role": "user", "content": question})
        self.history[user_id].append({"role": "assistant", "content": final_answer})

        # Persist useful memory snippets for future turns.
        self.memory.remember(user_id, f"User asked: {question}", metadata={"type": "user_query"})
        self.memory.remember(user_id, f"Assistant replied: {final_answer}", metadata={"type": "assistant_reply"})
        return final_answer

    def _build_turn_prompt(
        self,
        user_id: str,
        question: str,
        memory_context: str,
        scratchpad: str,
    ) -> str:
        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in self.history[user_id]
        )
        return (
            f"{self.system_prompt}\n\n"
            f"Known memory for this user:\n{memory_context}\n\n"
            f"Recent conversation:\n{history_text or '(none)'}\n\n"
            f"Current user question:\n{question}\n\n"
            "SCRATCHPAD_START\n"
            f"{scratchpad}"
            "SCRATCHPAD_END\n"
        )

    def _extract_action(self, text: str) -> tuple[str, str] | None:
        # Prefer the last explicit Action line in the model output.
        for line in reversed(text.splitlines()):
            m = ACTION_LINE_RE.match(line.strip())
            if not m:
                continue
            tool = m.group(1).strip()
            if tool.lower() == "none":
                return None
            raw_arg = (m.group(2) or "").strip()
            if len(raw_arg) >= 2 and (
                (raw_arg[0] == '"' and raw_arg[-1] == '"')
                or (raw_arg[0] == "'" and raw_arg[-1] == "'")
            ):
                raw_arg = raw_arg[1:-1]
            return tool, raw_arg
        return None

    def _coerce_plain_final_answer(self, text: str) -> str | None:
        """
        If the model returns plain assistant text (without ReAct tags),
        treat it as a direct final answer so greetings like "hello" work.
        """
        cleaned = text.strip()
        if not cleaned:
            return None
        if "Action:" in cleaned:
            return None
        if cleaned.lower().startswith("thought:"):
            return None
        return cleaned

    def _latest_observation_from_scratchpad(self, scratchpad: str) -> str | None:
        matches = re.findall(r"Observation:\s*(.+?)(?=\nThought:|\nAction:|\Z)", scratchpad, re.DOTALL)
        if not matches:
            return None
        last = matches[-1].strip()
        return last or None

    def _maybe_run_direct_tool(self, user_id: str, question: str) -> str | None:
        q = self._normalize_user_text(question)
        ql = q.lower()

        m_correction = re.search(
            r"\b(?:i\s*mean|meant)\s+(.+?)\s+(?:not|instead\s+of)\s+(.+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_correction:
            new_item = (m_correction.group(1) or "").strip(" .,!?:;\"'")
            old_item = (m_correction.group(2) or "").strip(" .,!?:;\"'")
            if new_item and old_item:
                logger.info(
                    "direct_tool user=%s tool=replace_grocery_item arg=%s|%s",
                    user_id,
                    new_item[:80],
                    old_item[:80],
                )
                return run_tool("replace_grocery_item", f"{new_item}|{old_item}", user_id)

        m_plan_meals = re.search(
            r"^(?:plan\s+meals?|meal\s+plan|add\s+meals?|meals?\s+to\s+grocery)\s*[:\-]?\s*(.+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_plan_meals:
            meals = (m_plan_meals.group(1) or "").strip()
            if meals:
                logger.info("direct_tool user=%s tool=plan_meals_to_grocery arg=%s", user_id, meals[:200])
                return run_tool("plan_meals_to_grocery", meals, user_id)
            return "Please provide meals, e.g. 'plan meals pasta, omelette'."

        if re.search(r"\b(suggest|show)\b.*\b(rebuy|repeat)\b", ql):
            logger.info("direct_tool user=%s tool=suggest_rebuy", user_id)
            return run_tool("suggest_rebuy", "", user_id)

        m_repeat = re.search(
            r"\b(repeat|rebuy)\b.*\b(grocery|groceries|shopping)\b",
            ql,
        )
        if m_repeat:
            logger.info("direct_tool user=%s tool=repeat_last_groceries text=%s", user_id, question[:200])
            return run_tool("repeat_last_groceries", q, user_id)

        m_record_price = re.search(
            r"^(?:set|add|record)\s+(?:store\s+)?price\s+(.+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_record_price:
            payload = (m_record_price.group(1) or "").strip()
            logger.info("direct_tool user=%s tool=record_store_price arg=%s", user_id, payload[:200])
            return run_tool("record_store_price", payload, user_id)

        m_compare_price = re.search(
            r"^(?:compare|best|cheapest)\s+(?:store\s+)?price(?:s)?(?:\s+for)?\s+(.+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_compare_price:
            item_name = (m_compare_price.group(1) or "").strip()
            logger.info("direct_tool user=%s tool=compare_store_price arg=%s", user_id, item_name[:200])
            return run_tool("compare_store_price", item_name, user_id)

        if re.search(r"\b(show|list)\b.*\b(inventory|stock)\b.*\b(low)\b", ql):
            logger.info("direct_tool user=%s tool=list_inventory low", user_id)
            return run_tool("list_inventory", "low", user_id)

        if re.search(r"\b(show|list)\b.*\b(inventory|stock)\b", ql):
            logger.info("direct_tool user=%s tool=list_inventory", user_id)
            return run_tool("list_inventory", "", user_id)

        if re.search(r"\b(what|which)\b.*\b(low|running low)\b", ql):
            logger.info("direct_tool user=%s tool=list_inventory low query", user_id)
            return run_tool("list_inventory", "low", user_id)

        m_set_threshold = re.search(
            r"^(?:set|update)\s+(?:low\s+)?threshold(?:\s+for)?\s+(.+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_set_threshold:
            payload = (m_set_threshold.group(1) or "").strip()
            logger.info("direct_tool user=%s tool=set_inventory_threshold arg=%s", user_id, payload[:200])
            return run_tool("set_inventory_threshold", payload, user_id)

        m_set_stock = re.search(
            r"^(?:set|update)\s+stock\s+(.+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_set_stock:
            payload = (m_set_stock.group(1) or "").strip()
            logger.info("direct_tool user=%s tool=set_stock_item arg=%s", user_id, payload[:200])
            return run_tool("set_stock_item", payload, user_id)

        m_stock = re.search(
            r"^(?:stock|restock|add\s+stock)\s+(.+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_stock:
            payload = (m_stock.group(1) or "").strip()
            logger.info("direct_tool user=%s tool=stock_item arg=%s", user_id, payload[:200])
            return run_tool("stock_item", payload, user_id)

        m_use = re.search(
            r"^(?:use|used|consume)\s+(.+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_use:
            payload = (m_use.group(1) or "").strip()
            logger.info("direct_tool user=%s tool=use_item arg=%s", user_id, payload[:200])
            return run_tool("use_item", payload, user_id)

        m_item_low = re.search(
            r"^([a-zA-Z][a-zA-Z0-9\s]+?)\s+low$",
            q,
            flags=re.IGNORECASE,
        )
        if m_item_low and not re.search(r"\b(what|which|show|list)\b", ql):
            item_name = (m_item_low.group(1) or "").strip()
            logger.info("direct_tool user=%s tool=report_item_low arg=%s", user_id, item_name[:200])
            return run_tool("report_item_low", item_name, user_id)

        if re.search(r"\b(show|list)\b.*\b(bought|completed)\b.*\b(grocery|groceries|shopping)\b", ql):
            logger.info("direct_tool user=%s tool=list_grocery_items status=bought", user_id)
            return run_tool("list_grocery_items", "bought", user_id)

        if re.search(r"\b(show|list)\b.*\b(all)\b.*\b(grocery|groceries|shopping)\b", ql):
            logger.info("direct_tool user=%s tool=list_grocery_items status=all", user_id)
            return run_tool("list_grocery_items", "all", user_id)

        if re.search(r"\b(grocery|groceries|shopping)\b.*\b(budget|estimate|total cost|cost)\b", ql):
            logger.info("direct_tool user=%s tool=grocery_budget_summary", user_id)
            return run_tool("grocery_budget_summary", "pending", user_id)

        if re.search(r"\b(show|list)\b.*\b(grocery|groceries|shopping)\b.*\b(category|categories|aisle|store)\b", ql):
            logger.info("direct_tool user=%s tool=list_grocery_items grouped", user_id)
            return run_tool("list_grocery_items", "pending grouped", user_id)

        if re.search(r"\b(show|list)\s+(my\s+)?(grocery|groceries|shopping)(\s+list)?\b", ql) or ql in {
            "grocery list",
            "shopping list",
            "groceries",
        }:
            logger.info("direct_tool user=%s tool=list_grocery_items text=%s", user_id, question[:200])
            return run_tool("list_grocery_items", "pending", user_id)

        m_set_category = re.search(
            r"\b(?:set|update|change|move)\s+(?:grocery|groceries|shopping)\s*(\d+)\s+(?:category\s+)?(?:to\s+)?([a-zA-Z ]+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_set_category:
            item_id = m_set_category.group(1)
            category = m_set_category.group(2).strip()
            if category.lower() in {"bought", "done", "complete", "completed"}:
                logger.info("direct_tool user=%s tool=mark_grocery_bought arg=%s", user_id, item_id)
                return run_tool("mark_grocery_bought", item_id, user_id)
            logger.info(
                "direct_tool user=%s tool=set_grocery_category arg=%s|%s",
                user_id,
                item_id,
                category,
            )
            return run_tool("set_grocery_category", f"{item_id}|{category}", user_id)

        m_set_price = re.search(
            r"\b(?:set|update|change)\s+(?:grocery|groceries|shopping)\s*(\d+)\s+(?:price|cost|unit price)\s*(?:to|as)?\s*\$?([0-9]+(?:\.[0-9]+)?)\b",
            q,
            flags=re.IGNORECASE,
        )
        if m_set_price:
            item_id = m_set_price.group(1)
            price = m_set_price.group(2)
            logger.info(
                "direct_tool user=%s tool=set_grocery_price arg=%s|%s",
                user_id,
                item_id,
                price,
            )
            return run_tool("set_grocery_price", f"{item_id}|{price}", user_id)

        m_add_grocery = re.search(
            r"^(?:add\s+(?:to\s+)?(?:grocery|groceries|shopping(?:\s+list)?)\s*[:\-]?\s*|buy\s+|get\s+|grab\s+|pick\s+up\s+|need\s+)(.+)$",
            q,
            flags=re.IGNORECASE,
        )
        if m_add_grocery and "task" not in ql and not ql.startswith("remind "):
            item = (m_add_grocery.group(1) or "").strip()
            if item:
                logger.info("direct_tool user=%s tool=add_grocery_item arg=%s", user_id, item[:200])
                return run_tool("add_grocery_item", item, user_id)
            return "Please provide an item, e.g. 'buy milk'."

        if re.search(r"\b(mark|set)\b.*\b(grocery|groceries|shopping)\b.*\bbought\b", ql):
            m = re.search(r"\b(\d+)\b", q)
            if m:
                logger.info("direct_tool user=%s tool=mark_grocery_bought arg=%s", user_id, m.group(1))
                return run_tool("mark_grocery_bought", m.group(1), user_id)
            return "Please provide grocery item number, e.g. 'mark grocery 2 bought'."

        if re.search(r"\b(clear|remove|delete)\b.*\b(bought|completed)\b.*\b(grocery|groceries|shopping)\b", ql):
            logger.info("direct_tool user=%s tool=clear_bought_groceries", user_id)
            return run_tool("clear_bought_groceries", "", user_id)

        if re.search(r"\b(clear|remove|delete|reset)\b.*\b(all)\b.*\b(grocery|groceries|shopping)\b", ql):
            logger.info("direct_tool user=%s tool=clear_all_groceries", user_id)
            return run_tool("clear_all_groceries", "", user_id)

        if re.search(r"\b(remove|delete)\b.*\b(grocery|groceries|shopping)\b", ql):
            m = re.search(r"\b(\d+)\b", q)
            if m:
                logger.info("direct_tool user=%s tool=remove_grocery_item arg=%s", user_id, m.group(1))
                return run_tool("remove_grocery_item", m.group(1), user_id)
            return "Please provide grocery item number, e.g. 'remove grocery 2'."

        if re.search(r"\b(show|list)\s+(my\s+)?tasks?\b", ql) or ql in {"my tasks", "tasks"}:
            logger.info("direct_tool user=%s tool=list_tasks text=%s", user_id, question[:200])
            return run_tool("list_tasks", "", user_id)

        m_create = re.search(r"\b(create|add|new)\s+task\b\s*[:\-]?\s*(.*)$", q, flags=re.IGNORECASE)
        if m_create:
            title = (m_create.group(2) or "").strip()
            if title:
                logger.info("direct_tool user=%s tool=create_task arg=%s", user_id, title[:200])
                return run_tool("create_task", title, user_id)
            return "Please provide a task title, e.g. 'create task buy groceries'."

        if re.search(r"\b(complete|done|finish)\s+task\b", ql):
            m = re.search(r"\b(\d+)\b", q)
            if m:
                logger.info("direct_tool user=%s tool=complete_task arg=%s", user_id, m.group(1))
                return run_tool("complete_task", m.group(1), user_id)
            return "Please provide a task number, e.g. 'complete task 2'."

        if re.search(r"\b(delete|remove)\s+task\b", ql):
            m = re.search(r"\b(\d+)\b", q)
            if m:
                logger.info("direct_tool user=%s tool=delete_task arg=%s", user_id, m.group(1))
                return run_tool("delete_task", m.group(1), user_id)
            return "Please provide a task number, e.g. 'delete task 2'."

        if "daily summary" in ql:
            logger.info("direct_tool user=%s tool=daily_summary", user_id)
            return run_tool("daily_summary", "", user_id)

        # "remind me in 5 min to drink water" -> "drink water|in 5m"
        m = re.search(
            r"remind me\s+in\s+(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\s+to\s+(.+)$",
            q,
            re.IGNORECASE,
        )
        if m:
            count = m.group(1)
            unit = m.group(2).lower()
            msg = m.group(3).strip()
            short_unit = "h" if unit.startswith("h") else "m"
            logger.info("direct_tool user=%s tool=set_reminder minutes_or_hours=%s%s", user_id, count, short_unit)
            return run_tool("set_reminder", f"{msg}|in {count}{short_unit}", user_id)

        return None

    def _normalize_user_text(self, text: str) -> str:
        # Remove hidden direction/format characters sometimes present in chat apps.
        cleaned = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _extract_final_answer(self, text: str) -> str | None:
        m = FINAL_RE.search(text)
        if not m:
            return None
        return m.group(1).strip()

    def _chat(self, prompt: str) -> str:
        backend = self.config.llm_backend
        if backend == "ollama":
            return self._chat_ollama(prompt)
        if backend == "openai":
            return self._chat_openai(prompt)
        if backend == "gemini":
            return self._chat_gemini(prompt)
        if backend == "mock":
            return self._chat_mock(prompt)
        raise ValueError(f"Unknown LLM backend: {backend}")

    def _chat_ollama(self, prompt: str) -> str:
        base_url = self.config.ollama_base_url.rstrip("/")
        url = f"{base_url}/api/chat"
        payload = {
            "model": self.config.ollama_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, json=payload, timeout=90)
                if resp.status_code == 404:
                    logger.info("Ollama /api/chat not available; using /api/generate compatibility mode.")
                    return self._chat_ollama_generate(prompt, base_url)
                if 400 <= resp.status_code < 500:
                    return "Sorry, I'm having trouble with that (4xx error). Please check your Ollama setup."
                resp.raise_for_status()
                data = resp.json()
                msg = data.get("message", {}).get("content")
                if not msg:
                    raise RuntimeError(f"Unexpected Ollama response: {json.dumps(data)[:400]}")
                return msg.strip()
            except Exception as exc:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error("Ollama call failed after %d retries: %s", max_retries, exc)
                    return "Sorry, I'm having trouble with that — try again in a minute."
        return "Sorry, I'm having trouble with that — try again in a minute."

    def _chat_ollama_generate(self, prompt: str, base_url: str) -> str:
        url = f"{base_url}/api/generate"
        payload = {
            "model": self.config.ollama_model,
            "prompt": prompt,
            "stream": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=90)
            resp.raise_for_status()
            data = resp.json()
            text = (data.get("response") or "").strip()
            if not text:
                raise RuntimeError(f"Unexpected Ollama generate response: {json.dumps(data)[:400]}")
            return text
        except Exception as exc:
            logger.error("Ollama /api/generate call failed: %s", exc)
            return "Sorry, I'm having trouble with that — try again in a minute."

    def _chat_openai(self, prompt: str) -> str:
        from openai import OpenAI, APIError, APIConnectionError, RateLimitError, BadRequestError  # type: ignore
        
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        client = OpenAI(api_key=api_key)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=self.config.openai_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                )
                msg = response.choices[0].message.content or ""
                return msg.strip()
            except BadRequestError as exc:
                logger.error("OpenAI 4xx error: %s", exc)
                return "Sorry, I'm having trouble with that (4xx error). Please check API keys or quota."
            except (APIError, APIConnectionError, RateLimitError, Exception) as exc:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error("OpenAI call failed after %d retries: %s", max_retries, exc)
                    return "Sorry, I'm having trouble with that — try again in a minute."
        return "Sorry, I'm having trouble with that — try again in a minute."

    def _chat_gemini(self, prompt: str) -> str:
        import google.generativeai as genai  # type: ignore
        from google.api_core.exceptions import InvalidArgument, ResourceExhausted, ServiceUnavailable  # type: ignore

        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(self.config.gemini_model)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt)
                msg = response.text or ""
                return msg.strip()
            except InvalidArgument as exc:
                logger.error("Gemini 4xx error: %s", exc)
                return "Sorry, I'm having trouble with that (4xx error). Please check API keys."
            except Exception as exc:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    logger.error("Gemini call failed after %d retries: %s", max_retries, exc)
                    return "Sorry, I'm having trouble with that — try again in a minute."
        return "Sorry, I'm having trouble with that — try again in a minute."

    def _chat_mock(self, prompt: str) -> str:
        # Lightweight deterministic fallback for local/testing environments.
        scratch = ""
        if "SCRATCHPAD_START" in prompt and "SCRATCHPAD_END" in prompt:
            scratch = prompt.split("SCRATCHPAD_START", 1)[1].split("SCRATCHPAD_END", 1)[0]
        if "Observation:" in scratch:
            clean = scratch.rsplit("Observation:", 1)[-1].strip()
            if clean:
                return f"Final Answer: {clean}"
        q_match = re.search(
            r"Current user question:\n(.*?)\n\nSCRATCHPAD_START",
            prompt,
            re.DOTALL,
        )
        q = (q_match.group(1).strip() if q_match else prompt).lower()
        if "weather" in q or "latest news" in q or "search" in q:
            return 'Thought: I should search the web.\nAction: web_search("latest query")'
        if "calculate" in q or re.search(r"\d+\s*[\+\-\*\/]\s*\d+", q):
            expr = re.search(r"(\d+\s*[\+\-\*\/]\s*\d+)", q)
            value = expr.group(1) if expr else "2+2"
            return f'Thought: I should calculate.\nAction: calculate("{value}")'
        if "time" in q:
            return 'Thought: I should get current time.\nAction: get_current_time("")'
        if "wikipedia" in q:
            return 'Thought: I should read wikipedia.\nAction: wikipedia_summary("Python (programming language)")'
        if "create a task" in q or "add a task" in q:
            title = q.split("task", 1)[-1].strip(": -") or "new task"
            return f'Thought: I should create a task.\nAction: create_task("{title}")'
        if "show my tasks" in q or "list tasks" in q:
            return 'Thought: I should list tasks.\nAction: list_tasks("")'
        if "complete task" in q:
            m = re.search(r"(\d+)", q)
            tid = m.group(1) if m else "1"
            return f'Thought: I should complete it.\nAction: complete_task("{tid}")'
        if "remind me" in q:
            return 'Thought: I should set reminder.\nAction: set_reminder("drink water|in 10m")'
        return "Final Answer: I am running in mock mode. Ask me to create/list tasks or search."
