from __future__ import annotations

import json
import logging
import os
import re
import time

import google.generativeai as genai
import requests
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

def _clean_json_text(text: str) -> str:
    """Strips Markdown formatting that LLMs sometimes hallucinate around JSON output."""
    text = text.strip()
    if text.startswith("```json"): text = text[7:]
    elif text.startswith("```"): text = text[3:]
    if text.endswith("```"): text = text[:-3]
    return text.strip()

class PlanStep(BaseModel):
    agent: str = Field(description="Specialist agent: 'planner', 'finance', 'shopping', 'events', 'knowledge', 'journal'")
    subrequest: str = Field(description="The specific request for this specialist")

class SupervisorOutput(BaseModel):
    kind: str = Field(description="One of: 'reply', 'route', 'plan', 'clarify'")
    text: str | None = Field(default=None, description="Direct text response, if kind='reply'")
    agent: str | None = Field(default=None, description="Specialist agent to route to, if kind='route'")
    subrequest: str | None = Field(default=None, description="The rewritten request, if kind='route'")
    steps: list[PlanStep] | None = Field(default=None, description="List of steps, if kind='plan'")

SUPERVISOR_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["reply", "route", "plan", "clarify"]},
        "text": {"type": "string", "nullable": True},
        "agent": {"type": "string", "nullable": True},
        "subrequest": {"type": "string", "nullable": True},
        "steps": {
            "type": "array",
            "nullable": True,
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string"},
                    "subrequest": {"type": "string"},
                },
                "required": ["agent", "subrequest"],
            },
        },
    },
    "required": ["kind"],
}

SUPERVISOR_PROMPT = """You are the Supervisor Agent for a personal WhatsApp assistant.
Your job is to analyze the user's message and decide how to handle it.

You have access to the following specialists:
- planner: Tasks and ad-hoc one-shot reminders ("remind me to call mom at 7pm", "create a task").
- finance: Logging expenses, checking budgets, anything with a price/amount ("paid 200 for milk").
- shopping: Groceries, home inventory, and store prices ("buy milk", "we are out of toilet paper").
- events: Birthdays, anniversaries, and recurring life events.
- knowledge: Web search, Wikipedia, calculator, and general "how do I..." questions.
- journal: Free-form notes, daily summaries, and semantic recall ("what did I tell you about the trip?").

Ownership Rules:
1. Dated life events (birthdays, anniversaries, recurring) -> events.
2. Ad-hoc one-shots ("remind me to call mom") -> planner.
3. Anything with a price/amount -> finance (even if it sounds like shopping, e.g. "paid 200 for milk" is finance, "buy milk" is shopping).

Output format instructions:
- If the message is a simple greeting or trivial question (like asking the time), return kind="reply" with the "text".
- If the message requires exactly one specialist, return kind="route" with the "agent" name and a clear "subrequest".
- If the message contains multiple independent tasks, return kind="plan" with a list of "steps".
- If the message is off-task chat, abuse, or random text, return kind="reply" with a short helpful guidance message that suggests supported commands.
- Never repeat the user's exact input text as your reply. Do not parrot.

Be strictly compliant with the JSON schema.

Language: When you produce a kind="reply" text, always write it in the same language the user wrote in (English, Hindi, Hinglish, etc.). Mirror their language exactly.
"""

class SupervisorAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GEMINI_SUPERVISOR_MODEL", "gemini-2.5-flash")
        self._model = None
        
    def _get_model(self):
        if self._model is not None:
            return self._model
            
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if not api_key:
            logger.warning("GOOGLE_API_KEY not set for SupervisorAgent.")
        else:
            genai.configure(api_key=api_key)
            
        self._model = genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=SUPERVISOR_PROMPT
        )
        return self._model
        
    def decide(
        self,
        user_id: str,
        message: str,
        recent_turns: list[dict],
        user_settings: dict | None,
        media_bytes: bytes | None = None,
        mime_type: str | None = None
    ) -> SupervisorOutput | None:
        context_parts = []
        if user_settings:
            context_parts.append(f"User Settings:\n{json.dumps(user_settings)}\n")
        if recent_turns:
            turn_lines = [f"{t.get('role', 'unknown').upper()}: {t.get('content', '')}" for t in recent_turns[-5:]]
            context_parts.append(f"Recent Conversation:\n" + "\n".join(turn_lines) + "\n")
        
        context_parts.append(f"New Message:\n{message}")
        prompt = "\n".join(context_parts)
        
        return self._call_with_retries(prompt, media_bytes, mime_type)
        
    def _call_with_retries(self, prompt: str, media_bytes: bytes | None = None, mime_type: str | None = None) -> SupervisorOutput | None:
        groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
        # Use Groq for text for speed and quota savings. Keep Gemini for image analysis!
        if not media_bytes and groq_api_key:
            return self._call_groq(prompt, groq_api_key)
            
        model = self._get_model()
        config = genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=SUPERVISOR_SCHEMA,
            temperature=0.0
        )
        
        contents = [prompt]
        if media_bytes and mime_type:
            contents.append({"mime_type": mime_type, "data": media_bytes})

        current_prompt = prompt
        for attempt in range(2):  # 1 retry
            try:
                contents[0] = current_prompt
                response = model.generate_content(
                    contents,
                    generation_config=config
                )
                text = response.text or ""
                clean_text = _clean_json_text(text)
                return SupervisorOutput.model_validate_json(clean_text)
            except ValidationError as e:
                logger.warning("Supervisor validation failed on attempt %d: %s", attempt + 1, e)
                current_prompt += f"\n\nSystem Error on previous attempt:\n{e}\nPlease correct the JSON output."
            except Exception as e:
                logger.error("Supervisor API call failed on attempt %d: %s", attempt + 1, e)
                if "429" in str(e) or "ResourceExhausted" in str(e):
                    m = re.search(r"retry in (\d+)", str(e))
                    wait_sec = int(m.group(1)) + 2 if m else 40
                    logger.warning("Supervisor rate limit hit. Waiting %d seconds...", wait_sec)
                    time.sleep(wait_sec)
                else:
                    time.sleep(2)
        return None

    def _call_groq(self, prompt: str, api_key: str) -> SupervisorOutput | None:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        schema_text = json.dumps(SUPERVISOR_SCHEMA)
        system_msg = f"{SUPERVISOR_PROMPT}\n\nYou must reply with valid JSON matching this schema:\n{schema_text}"
        
        payload = {
            "model": os.getenv("GROQ_SUPERVISOR_MODEL", "llama-3.3-70b-versatile"),
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0
        }

        current_prompt = prompt
        for attempt in range(2):
            try:
                payload["messages"][1]["content"] = current_prompt
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
                if resp.status_code != 200:
                    raise Exception(f"HTTP {resp.status_code}: {resp.text}")
                data = resp.json()
                text = data["choices"][0]["message"]["content"] or ""
                return SupervisorOutput.model_validate_json(_clean_json_text(text))
            except ValidationError as e:
                logger.warning("Supervisor validation failed on attempt %d: %s", attempt + 1, e)
                current_prompt += f"\n\nSystem Error on previous attempt:\n{e}\nPlease correct the JSON output."
            except Exception as e:
                logger.error("Groq API call failed on attempt %d: %s", attempt + 1, e)
                time.sleep(2)
        return None
