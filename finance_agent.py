from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timezone

import google.generativeai as genai

logger = logging.getLogger(__name__)

# From plan_gemini.md
CATEGORY_MAP = {
    "food", "groceries", "transport", "utilities", "rent", "entertainment",
    "shopping", "gifts", "health", "travel", "subscriptions", "other"
}

class FinanceAgent:
    def __init__(self) -> None:
        # Per plan, Finance can use a more powerful model if needed
        self.model_name = os.getenv("GEMINI_FINANCE_MODEL", "gemini-1.5-pro-latest")

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
            """Logs a new expense. Category must be one of the allowed values."""
            if category.lower() not in CATEGORY_MAP:
                return f"Invalid category '{category}'. Must be one of: {', '.join(CATEGORY_MAP)}"

            # Store amount in minor units (cents/paise)
            amount_minor = int(amount * 100)
            final_currency = (currency or default_currency).upper()

            expense_id = db.log_expense(
                user_id=user_id,
                amount_minor=amount_minor,
                currency=final_currency,
                category=category.lower(),
                merchant=merchant,
                method=method,
            )
            return f"Logged expense #{expense_id}: {amount} {final_currency} for {category}."

        def delete_last_expense() -> str:
            """Deletes the most recently logged expense."""
            last_expense = db.get_last_expense(user_id)
            if not last_expense:
                return "No expenses found to delete."
            
            ok = db.delete_expense(user_id, last_expense["id"])
            if ok:
                amount = last_expense['amount_minor'] / 100
                return f"Deleted last expense: {amount} {last_expense['currency']} for {last_expense['category']}."
            return "Failed to delete the last expense."

        def monthly_summary() -> str:
            """Provides a summary of expenses for the current month."""
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

        tools_list = [log_expense, delete_last_expense, monthly_summary]
        
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if api_key:
            genai.configure(api_key=api_key)
            
        model = genai.GenerativeModel(
            model_name=self.model_name,
            tools=tools_list,
            system_instruction=(
                "You are the Finance Specialist. You handle expense logging, budget checks, and financial summaries.\n"
                f"Available expense categories are: {', '.join(CATEGORY_MAP)}.\n"
                "Use the provided tools to fulfill the user's request. If a date is not specified, assume today.\n"
                "If an amount is given without a currency, assume the user's default currency.\n"
                "Always reply with a single, terse sentence starting with a relevant emoji."
            )
        )
        
        chat = model.start_chat(enable_automatic_function_calling=True)
        user_msg = bundle.get("subrequest") or bundle.get("original_message")
        
        try:
            response = chat.send_message(f"Request: {user_msg}")
            text = ""
            try:
                text = response.text.strip()
            except ValueError:
                text = "✅ Finance task executed."
            if not text:
                text = "✅ Finance task completed."
            return {"kind": "reply", "text": text}
        except Exception as e:
            logger.error("FinanceAgent error: %s", e)
            return {"kind": "reply", "text": "Sorry, I couldn't process your finance request right now."}