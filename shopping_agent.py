from __future__ import annotations

import json
import logging
import os
import time
from openai import OpenAI

logger = logging.getLogger(__name__)

class ShoppingAgent:
    def __init__(self) -> None:
        self.model_name = os.getenv("GROQ_SPECIALIST_MODEL", "llama-3.3-70b-versatile")

    def handle(self, bundle: dict) -> dict:
        from tools import TOOLS, TOOL_DESCRIPTIONS
        user_id = bundle["user_id"]
        user_settings = bundle.get("user_settings", {})
        family_size = user_settings.get("family_size", 1)

        def mark_multiple_bought(item_numbers: str) -> str:
            """Marks multiple grocery items as bought. Args: comma-separated item numbers (e.g. '1, 3, 5')."""
            from tools import mark_grocery_bought
            results = []
            for num in item_numbers.split(','):
                if num.strip():
                    results.append(mark_grocery_bought(num.strip(), user_id))
            return "\n".join(results)

        allowed = [
            "add_grocery_item", "list_grocery_items", "mark_grocery_bought",
            "remove_grocery_item", "clear_bought_groceries", "clear_all_groceries",
            "replace_grocery_item", "set_grocery_category", "set_grocery_price",
            "grocery_budget_summary", "repeat_last_groceries", "suggest_rebuy",
            "plan_meals_to_grocery", "record_store_price", "compare_store_price",
            "stock_item", "use_item", "set_stock_item", "set_inventory_threshold",
            "list_inventory", "report_item_low", "record_multiple_store_prices",
            "suggest_grocery_run", "mark_multiple_bought"
        ]

        local_tools = {}
        local_desc = dict(TOOL_DESCRIPTIONS)
        local_desc["mark_multiple_bought"] = "Marks multiple grocery items as bought. Args: comma-separated item numbers (e.g. '1, 3, 5')."

        for name in allowed:
            if name == "mark_multiple_bought":
                local_tools[name] = lambda arg="", uid=user_id: mark_multiple_bought(arg)
            else:
                func = TOOLS.get(name)
                if func:
                    local_tools[name] = (lambda f, uid=user_id: lambda arg="": f(arg, uid))(func)

        openai_tools = []
        for name in local_tools.keys():
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": local_desc.get(name, "Tool function"),
                    "parameters": {
                        "type": "object",
                        "properties": {"arg": {"type": "string", "description": "The argument string for the tool."}},
                        "required": ["arg"] if name not in ["suggest_rebuy"] else []
                    }
                }
            })

        system_instruction = (
            "You are the Shopping Specialist. You handle groceries, home inventory, and store prices.\n"
            f"Context: The user's household has {family_size} members. Use this to estimate how long items will last or suggest extra quantities if guests are coming.\n"
            "Use the provided tools to fulfill the user's request.\n"
            "If the user asks what they need or what to buy, use `suggest_rebuy` to predict their needs based on household consumption velocity.\n"
            "If the user asks to optimize their grocery run or find the cheapest store, use `suggest_grocery_run`.\n"
            "If the user uploads a grocery receipt (bill):\n"
            "1. Identify the store name, purchased items, and their individual prices from the image.\n"
            "2. Use `mark_multiple_bought` to mark the items you found on the receipt as bought.\n"
            "4. Use `record_multiple_store_prices` passing a JSON list (e.g. `[{\"store\": \"Fairprice\", \"item\": \"Milk\", \"price\": 3.50}]`) to log the prices.\n"
            "5. Use `stock_item` to update home inventory if applicable.\n" 
            "Visible Context Cues: Always explicitly echo the scope/household you used in your final reply to catch misroutes (e.g., '✅ Added milk to **Home** groceries').\n"
            "Always reply in the same language the user wrote in (English, Hindi, Hinglish, etc.). Mirror their language exactly. Item names from the database may be in any language; preserve them as stored."
        )

        user_msg = bundle.get("subrequest") or bundle.get("original_message")
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Request: {user_msg}"}
        ]

        if bundle.get("media_bytes") and bundle.get("mime_type"):
            return self._fallback_gemini(bundle, allowed, local_tools, local_desc, system_instruction)

        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            logger.error("GROQ_API_KEY not set.")
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
                            arg_val = args.get("arg", "")
                        except Exception:
                            arg_val = ""
                        
                        try:
                            obs = local_tools[func_name](arg_val)
                        except Exception as e:
                            obs = f"Error: {e}"
                            
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": str(obs)
                        })
                else:
                    from tool_fallback import absorb_leaked_calls
                    # Shopping passes a single positional `arg` string to each tool;
                    # adapt the leak handler with a thin wrapper.
                    def _shop_tool(name):
                        def _call(**kw):
                            arg = kw.get("arg") or kw.get("query") or ""
                            if not arg and kw:
                                # Fall back to first JSON-string value
                                for v in kw.values():
                                    if isinstance(v, str):
                                        arg = v
                                        break
                            return local_tools[name](arg)
                        return _call
                    wrapped = {n: _shop_tool(n) for n in local_tools.keys()}
                    if absorb_leaked_calls(messages, msg.content or "", wrapped):
                        continue
                    text = msg.content.strip() if msg.content else "✅ Shopping task executed."
                    return {"kind": "reply", "text": text}
            except Exception as e:
                if attempt == 0 and ("429" in str(e) or "ResourceExhausted" in str(e)):
                    import re, time
                    m = re.search(r"retry in (\d+)", str(e))
                    wait_sec = int(m.group(1)) + 2 if m else 10
                    logger.warning("ShoppingAgent rate limit hit. Waiting %d seconds...", wait_sec)
                    time.sleep(wait_sec)
                else:
                    logger.error("ShoppingAgent error: %s", e)
                    return {"kind": "reply", "text": "Sorry, I couldn't process your shopping request right now."}
        return {"kind": "reply", "text": "✅ Shopping task completed."}

    def _fallback_gemini(self, bundle, allowed, local_tools, local_desc, system_instruction):
        import google.generativeai as genai
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        genai.configure(api_key=api_key)
        
        gemini_tools = []
        for name in allowed:
            if name in local_tools:
                def make_wrapper(n):
                    def wrapper(arg: str = "") -> str:
                        return local_tools[n](arg)
                    wrapper.__name__ = n
                    wrapper.__doc__ = local_desc.get(n, "Executes tool")
                    return wrapper
                gemini_tools.append(make_wrapper(name))
                
        model = genai.GenerativeModel(
            model_name=os.getenv("GEMINI_SPECIALIST_MODEL", "gemini-2.5-flash"),
            tools=gemini_tools,
            system_instruction=system_instruction
        )
        chat = model.start_chat(enable_automatic_function_calling=True)
        prompt = bundle.get("subrequest") or bundle.get("original_message")
        contents = [prompt]
        if bundle.get("media_bytes") and bundle.get("mime_type"):
            contents.append({"mime_type": bundle.get("mime_type"), "data": bundle.get("media_bytes")})
        
        try:
            response = chat.send_message(contents)
            text = response.text.strip() if response.text else "✅ Image processed."
            return {"kind": "reply", "text": text}
        except Exception as e:
            logger.error("Gemini fallback error: %s", e)
            return {"kind": "reply", "text": "Sorry, I couldn't process your image right now."}