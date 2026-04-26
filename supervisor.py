from __future__ import annotations

import json
import logging
import os
import time
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

class PlanStep(BaseModel):
    agent: str = Field(description="Specialist agent: 'planner', 'finance', 'shopping', 'events', 'knowledge', 'journal'")
    subrequest: str = Field(description="The specific request for this specialist")

class SupervisorOutput(BaseModel):
    kind: str = Field(description="One of: 'reply', 'route', 'plan'")
    text: str | None = Field(default=None, description="Direct text response, if kind='reply'")
    agent: str | None = Field(default=None, description="Specialist agent to route to, if kind='route'")
    subrequest: str | None = Field(default=None, description="The rewritten request, if kind='route'")
    steps: list[PlanStep] | None = Field(default=None, description="List of steps, if kind='plan'")

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

Be strictly compliant with the JSON schema.
"""

class SupervisorAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GEMINI_SUPERVISOR_MODEL", "gemini-2.5-flash")
        self._model = None
        
    def _get_model(self):
        if self._model is not None:
            return self._model
            
        import google.generativeai as genai  # type: ignore
        
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
        user_settings: dict | None
    ) -> SupervisorOutput | None:
        context_parts = []
        if user_settings:
            context_parts.append(f"User Settings:\n{json.dumps(user_settings)}\n")
        if recent_turns:
            turn_lines = [f"{t.get('role', 'unknown').upper()}: {t.get('content', '')}" for t in recent_turns[-5:]]
            context_parts.append(f"Recent Conversation:\n" + "\n".join(turn_lines) + "\n")
        
        context_parts.append(f"New Message:\n{message}")
        prompt = "\n".join(context_parts)
        
        return self._call_with_retries(prompt)
        
    def _call_with_retries(self, prompt: str) -> SupervisorOutput | None:
        import google.generativeai as genai
        
        model = self._get_model()
        config = genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=SupervisorOutput,
            temperature=0.0
        )
        
        current_prompt = prompt
        for attempt in range(2):  # 1 retry
            try:
                response = model.generate_content(
                    current_prompt,
                    generation_config=config
                )
                text = response.text or ""
                return SupervisorOutput.model_validate_json(text)
            except ValidationError as e:
                logger.warning("Supervisor validation failed on attempt %d: %s", attempt + 1, e)
                current_prompt += f"\n\nSystem Error on previous attempt:\n{e}\nPlease correct the JSON output."
            except Exception as e:
                logger.error("Supervisor API call failed on attempt %d: %s", attempt + 1, e)
                time.sleep(2)
        return None