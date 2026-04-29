from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from openai import OpenAI

logger = logging.getLogger(__name__)

# From plan_gemini.md
CATEGORY_MAP = {
    "food", "groceries", "transport", "utilities", "rent", "entertainment",
    "shopping", "gifts", "health", "travel", "subscriptions", "other"
}

class FinanceAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GROQ_SPECIALIST_MODEL", "llama-3.3-70b-versatile")

    def handle(self, bundle: dict) -> dict:
        from database import TaskDatabase
        db = TaskDatabase()
        user_id = bundle["user_id"]
        now_local_iso = bundle.get("now_local_iso")
        if now_local_iso:
            now = datetime.fromisoformat(now_local_iso)
        else:
            # Fallback if not provided, though router should always provide it
            now = datetime.now(timezone.utc)

        user_settings = bundle.get("user_settings", {})
        default_currency = user_settings.get("default_currency", "USD")

        def log_expense(
            amount: float,
            category: str,
            merchant: str | None = None,
            currency: str | None = None,
            method: str | None = None
        ) -> str:
            if category.lower() not in CATEGORY_MAP:
                return f"Invalid category '{category}'. Must be one of: {', '.join(CATEGORY_MAP)}"

            # Store amount in minor units (cents/paise)
            amount_minor = int(amount * 100)
            final_currency = (currency or default_currency).upper()

            # Anomaly Detection (Calculate before logging so new expense isn't included in avg)
            stats = db.get_expense_category_stats(user_id, category.lower(), final_currency)
            anomaly_warning = ""
            if stats and stats["count"] >= 3:
                avg_minor = stats["avg_amount_minor"]
                # Trigger if expense is > 2.5x the average and more than 20 units (e.g. $20)
                if avg_minor > 0 and amount_minor >= (avg_minor * 2.5) and amount_minor > 2000:
                    anomaly_warning = f"\n⚠️ Anomaly Alert: This is {amount_minor/avg_minor:.1f}x higher than your usual average of {avg_minor/100.0:.2f} {final_currency} for {category}."

            # Budget Checking
            budget = db.get_budget(user_id, category.lower())
            budget_warning = ""
            if budget and budget["currency"] == final_currency:
                cap_minor = budget["monthly_cap_minor"]
                current_total_minor = db.get_current_month_category_total(user_id, category.lower(), final_currency)
                new_total_minor = current_total_minor + amount_minor
                
                if new_total_minor >= cap_minor:
                    budget_warning = f"\n🚨 Budget Alert: This puts you OVER your monthly {category} budget of {cap_minor/100.0:.2f} {final_currency} (Total: {new_total_minor/100.0:.2f})!"
                elif new_total_minor >= cap_minor * 0.8:
                    budget_warning = f"\n⚠️ Budget Warning: You are at {(new_total_minor/cap_minor)*100:.1f}% of your monthly {category} budget ({new_total_minor/100.0:.2f} / {cap_minor/100.0:.2f} {final_currency})."

            expense_id = db.log_expense(
                user_id=user_id,
                amount_minor=amount_minor,
                currency=final_currency,
                category=category.lower(),
                merchant=merchant,
                method=method,
            )
            return f"Logged expense #{expense_id}: {amount} {final_currency} for {category}.{anomaly_warning}{budget_warning}"

        def delete_last_expense() -> str:
            last_expense = db.get_last_expense(user_id)
            if not last_expense:
                return "No expenses found to delete."
            
            ok = db.delete_expense(user_id, last_expense["id"])
            if ok:
                amount = last_expense['amount_minor'] / 100
                return f"Deleted last expense: {amount} {last_expense['currency']} for {last_expense['category']}."
            return "Failed to delete the last expense."

        def set_budget(category: str, amount: float, currency: str | None = None) -> str:
            if category.lower() not in CATEGORY_MAP:
                return f"Invalid category '{category}'. Must be one of: {', '.join(CATEGORY_MAP)}"
            
            amount_minor = int(amount * 100)
            final_currency = (currency or default_currency).upper()
            
            db.set_budget(user_id, category.lower(), amount_minor, final_currency)
            return f"Successfully set a monthly budget of {amount:.2f} {final_currency} for the '{category}' category."

        def log_multiple_expenses(expenses_json: str) -> str:
            try:
                data = json.loads(expenses_json)
                if not isinstance(data, list):
                    return "Error: Expected a JSON list of expense objects."
                
                count = 0
                for item in data:
                    amount = item.get("amount")
                    category = item.get("category", "other").lower()
                    merchant = item.get("merchant")
                    currency = item.get("currency", default_currency)
                    
                    if category not in CATEGORY_MAP:
                        category = "other"
                        
                    if amount is not None:
                        db.log_expense(
                            user_id=user_id,
                            amount_minor=int(float(amount) * 100),
                            currency=str(currency).upper(),
                            category=category,
                            merchant=str(merchant) if merchant else None,
                            method="statement",
                        )
                        count += 1
                return f"Successfully logged {count} expenses from the statement."
            except Exception as e:
                return f"Failed to log multiple expenses: {e}"

        def monthly_summary() -> str:
            start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start_of_month.month == 12:
                end_of_month = start_of_month.replace(year=start_of_month.year + 1, month=1)
            else:
                end_of_month = start_of_month.replace(month=start_of_month.month + 1)

            start_utc = start_of_month.astimezone(timezone.utc).isoformat()
            end_utc = end_of_month.astimezone(timezone.utc).isoformat()

            expenses = db.get_expenses_for_period(user_id, start_utc, end_utc)
            if not expenses:
                return f"No expenses logged in {now.strftime('%B %Y')}."

            by_category = defaultdict(lambda: defaultdict(int))
            totals = defaultdict(int)
            for ex in expenses:
                cat = ex["category"]
                curr = ex["currency"]
                amount = ex["amount_minor"]
                by_category[cat][curr] += amount
                totals[curr] += amount

            lines = [f"Expense summary for {now.strftime('%B %Y')}:"]
            for cat, amounts in sorted(by_category.items()):
                amounts_str = ", ".join([f"{amt/100.0:.2f} {curr}" for curr, amt in amounts.items()])
                lines.append(f"- {cat.title()}: {amounts_str}")

            totals_str = ", ".join([f"{total/100.0:.2f} {curr}" for curr, total in totals.items()])
            lines.append(f"\nTotal: {totals_str}")
            return "\n".join(lines)

        local_tools = {
            "log_expense": log_expense,
            "delete_last_expense": delete_last_expense,
            "monthly_summary": monthly_summary,
            "set_budget": set_budget,
            "log_multiple_expenses": log_multiple_expenses
        }

        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "log_expense",
                    "description": "Logs a new expense. Category must be one of the allowed values.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "number"},
                            "category": {"type": "string"},
                            "merchant": {"type": "string"},
                            "currency": {"type": "string"},
                            "method": {"type": "string"}
                        },
                        "required": ["amount", "category"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_last_expense",
                    "description": "Deletes the most recently logged expense.",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "monthly_summary",
                    "description": "Provides a summary of expenses for the current month.",
                    "parameters": {"type": "object", "properties": {}, "required": []}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "set_budget",
                    "description": "Sets a monthly budget cap for a specific expense category.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "amount": {"type": "number"},
                            "currency": {"type": "string"}
                        },
                        "required": ["category", "amount"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "log_multiple_expenses",
                    "description": "Logs multiple expenses at once from a parsed statement.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expenses_json": {"type": "string", "description": "JSON string list of objects: [{'amount': 10.5, 'category': 'food', 'merchant': 'Uber', 'currency': 'USD'}]"}
                        },
                        "required": ["expenses_json"]
                    }
                }
            }
        ]
        
        system_instruction = (
            "You are the Finance Specialist. You handle expense logging, budget checks, and financial summaries.\n"
            f"Available expense categories are: {', '.join(CATEGORY_MAP)}.\n"
            "Use the provided tools to fulfill the user's request. If a date is not specified, assume today.\n"
            "If an amount is given without a currency, assume the user's default currency.\n"
            "If the user uploads a bank statement or receipt document, extract the transactions, map them to the available categories, and use `log_multiple_expenses` passing a JSON list to log them.\n"
            "Always reply with a single, terse sentence starting with a relevant emoji.\n"
            "Visible Context Cues: Always explicitly echo the scope/household you used in your final reply to catch misroutes (e.g., '💸 Logged ₹8000 to **Couple** budget').\n"
            "If an anomaly warning is returned by the tool, explicitly include the warning in your reply!\n"
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
                    text = msg.content.strip() if msg.content else "✅ Finance task executed."
                    return {"kind": "reply", "text": text}
            except Exception as e:
                if attempt == 0 and "429" in str(e):
                    import re
                    m = re.search(r"retry in (\d+)", str(e))
                    wait_sec = int(m.group(1)) + 2 if m else 10
                    time.sleep(wait_sec)
                else:
                    logger.error("FinanceAgent error: %s", e)
                    return {"kind": "reply", "text": "Sorry, I couldn't process your finance request right now."}
        return {"kind": "reply", "text": "✅ Finance task completed."}

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