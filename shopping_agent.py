from __future__ import annotations

import logging
import os

import google.generativeai as genai

logger = logging.getLogger(__name__)

class ShoppingAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GEMINI_SPECIALIST_MODEL", "gemini-1.5-flash-latest")

    def handle(self, bundle: dict) -> dict:
        from tools import (
            add_grocery_item, list_grocery_items, mark_grocery_bought,
            remove_grocery_item, clear_bought_groceries, clear_all_groceries,
            replace_grocery_item, set_grocery_category, set_grocery_price,
            grocery_budget_summary, repeat_last_groceries, suggest_rebuy,
            plan_meals_to_grocery, record_store_price, compare_store_price,
            stock_item, use_item, set_stock_item, set_inventory_threshold,
            list_inventory, report_item_low
        )
        user_id = bundle["user_id"]

        # Tool wrappers to inject user_id
        def add_item(arg: str) -> str:
            """Add/merge grocery entry. Args: natural text or item|qty|unit|category."""
            return add_grocery_item(arg, user_id)

        def list_items(arg: str = "pending grouped") -> str:
            """List grocery entries. Args: optional status pending|bought|all and view grouped|flat."""
            return list_grocery_items(arg, user_id)

        def mark_bought(arg: str) -> str:
            """Mark grocery item bought. Args: grocery item number from list (1-based)."""
            return mark_grocery_bought(arg, user_id)

        def remove_item(arg: str) -> str:
            """Remove grocery item. Args: grocery item number from list (1-based)."""
            return remove_grocery_item(arg, user_id)

        def clear_bought(arg: str = "") -> str:
            """Delete all bought groceries for current user. Args: empty string."""
            return clear_bought_groceries(arg, user_id)

        def clear_all(arg: str = "") -> str:
            """Delete all groceries for current user. Args: empty string."""
            return clear_all_groceries(arg, user_id)

        def replace_item(arg: str) -> str:
            """Replace a pending grocery item. Args: new_item|old_item."""
            return replace_grocery_item(arg, user_id)

        def set_category(arg: str) -> str:
            """Set grocery category. Args: '<item_number> <category>' or '<item_number>|<category>'."""
            return set_grocery_category(arg, user_id)

        def set_price(arg: str) -> str:
            """Set grocery unit price. Args: '<item_number> <price>' or '<item_number>|<price>'."""
            return set_grocery_price(arg, user_id)

        def budget_summary(arg: str = "pending") -> str:
            """Show grocery estimated total using unit prices. Args: pending|bought|all."""
            return grocery_budget_summary(arg, user_id)

        def repeat_groceries(arg: str = "7d") -> str:
            """Repeat recently bought groceries into pending list. Args: optional period like 7d/2w/month."""
            return repeat_last_groceries(arg, user_id)

        def suggest_items(arg: str = "") -> str:
            """Suggest recurring items that are not currently pending. Args: empty string."""
            return suggest_rebuy(arg, user_id)

        def plan_meals(arg: str) -> str:
            """Convert meals into grocery items. Args: comma-separated meals (e.g. pasta, omelette)."""
            return plan_meals_to_grocery(arg, user_id)

        def record_price(arg: str) -> str:
            """Record manual store price. Args: item|store|price."""
            return record_store_price(arg, user_id)

        def compare_price(arg: str) -> str:
            """Compare latest prices across stores. Args: item name."""
            return compare_store_price(arg, user_id)

        # Inventory tools
        def add_to_stock(arg: str) -> str:
            """Increase home inventory stock. Args: item|qty|unit or natural text."""
            return stock_item(arg, user_id)

        def use_from_stock(arg: str) -> str:
            """Decrease home inventory stock. Args: item|qty|unit or natural text."""
            return use_item(arg, user_id)

        def set_stock_level(arg: str) -> str:
            """Set absolute home stock level. Args: item|qty|unit or natural text."""
            return set_stock_item(arg, user_id)

        def set_low_stock_threshold(arg: str) -> str:
            """Set low-stock threshold. Args: item|qty|unit or natural text."""
            return set_inventory_threshold(arg, user_id)

        def list_stock(arg: str = "") -> str:
            """List inventory items. Args: optional 'low'."""
            return list_inventory(arg, user_id)

        def report_low(arg: str) -> str:
            """Mark item as low and add refill to grocery list. Args: item name."""
            return report_item_low(arg, user_id)

        tools_list = [
            add_item, list_items, mark_bought, remove_item, clear_bought, clear_all,
            replace_item, set_category, set_price, budget_summary, repeat_groceries,
            suggest_items, plan_meals, record_price, compare_price, add_to_stock,
            use_from_stock, set_stock_level, set_low_stock_threshold, list_stock, report_low
        ]

        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if api_key:
            genai.configure(api_key=api_key)

        model = genai.GenerativeModel(
            model_name=self.model_name,
            tools=tools_list,
            system_instruction=(
                "You are the Shopping Specialist. You handle groceries, home inventory, and store prices.\n"
                "Use the provided tools to fulfill the user's request.\n"
                "Always reply with a single, terse sentence starting with a relevant emoji."
            )
        )

        chat = model.start_chat(enable_automatic_function_calling=True)
        user_msg = bundle.get("subrequest") or bundle.get("original_message")
        prompt = f"Request: {user_msg}"

        try:
            response = chat.send_message(prompt)
            text = ""
            try:
                text = response.text.strip()
            except ValueError:
                text = "✅ Shopping task executed."
            if not text:
                text = "✅ Shopping task completed."
            return {"kind": "reply", "text": text}
        except Exception as e:
            logger.error("ShoppingAgent error: %s", e)
            return {"kind": "reply", "text": "Sorry, I couldn't process your shopping request right now."}