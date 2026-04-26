from __future__ import annotations

import ast
import json
import math
import operator
import os
import re
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests

from database import TaskDatabase
from memory import MemoryStore


TASK_DB = TaskDatabase()


@lru_cache(maxsize=1)
def _memory_store() -> MemoryStore:
    # Lazy init prevents import-time env freeze for MEMORY_BACKEND.
    return MemoryStore(backend=os.getenv("MEMORY_BACKEND", "chroma"))


def sanitize_user_id(user_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_+.-]+", "_", user_id).strip("_")
    return cleaned or "default_user"


def user_workspace(user_id: str) -> Path:
    root = Path("workspaces") / sanitize_user_id(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_workspace_path(user_id: str, filename: str) -> Path:
    root = user_workspace(user_id).resolve()
    target = (root / filename).resolve()
    if root not in target.parents and target != root:
        raise ValueError("Invalid path: outside user workspace")
    return target


def _parse_int(value: str) -> int:
    return int(value.strip())


def _parse_float(value: str) -> float:
    return float(value.strip())


def _strip_quotes(value: str) -> str:
    s = value.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s[1:-1]
    return s


GROCERY_UNITS = {
    "unit",
    "units",
    "kg",
    "g",
    "gram",
    "grams",
    "l",
    "ml",
    "pack",
    "packs",
    "pkt",
    "pc",
    "pcs",
    "piece",
    "pieces",
    "bottle",
    "bottles",
    "can",
    "cans",
    "box",
    "boxes",
    "dozen",
}

GROCERY_SYNONYMS = {
    "toilet paper": "washroom tissue",
    "tissue paper": "washroom tissue",
    "bathroom tissue": "washroom tissue",
    "tp": "washroom tissue",
    "curd": "yogurt",
}

CATEGORY_KEYWORDS = {
    "dairy": {"milk", "cheese", "yogurt", "butter", "curd"},
    "produce": {"apple", "banana", "tomato", "onion", "potato", "spinach", "fruit", "vegetable"},
    "bakery": {"bread", "bun", "cake", "croissant"},
    "protein": {"egg", "eggs", "chicken", "fish", "paneer", "tofu"},
    "pantry": {"rice", "flour", "salt", "sugar", "oil", "pasta", "lentil", "dal"},
    "household": {"washroom tissue", "detergent", "soap", "cleaner", "trash bag", "sponge"},
    "beverages": {"coffee", "tea", "juice", "water"},
    "snacks": {"chips", "biscuit", "cookies", "chocolate"},
    "frozen": {"frozen", "ice cream"},
}


STORE_CATEGORY_ORDER = [
    "produce",
    "dairy",
    "protein",
    "bakery",
    "pantry",
    "beverages",
    "snacks",
    "frozen",
    "household",
    "other",
]

CATEGORY_ALIASES = {
    "veg": "produce",
    "vegetable": "produce",
    "vegetables": "produce",
    "fruits": "produce",
    "drink": "beverages",
    "drinks": "beverages",
    "home": "household",
    "home care": "household",
    "cleaning": "household",
    "essentials": "household",
    "misc": "other",
    "miscellaneous": "other",
}

STORE_ALIASES = {
    "fairprice": "fairprice",
    "ntuc": "fairprice",
    "ntuc fairprice": "fairprice",
    "giant": "giant",
    "shengsiong": "sheng siong",
    "sheng siong": "sheng siong",
    "ss": "sheng siong",
    "mustafa": "mustafa",
    "mustafa centre": "mustafa",
}

MEAL_INGREDIENTS: dict[str, list[tuple[str, float, str, str]]] = {
    "pasta": [
        ("pasta", 1, "pack", "pantry"),
        ("tomato sauce", 1, "bottle", "pantry"),
        ("cheese", 1, "pack", "dairy"),
    ],
    "omelette": [
        ("eggs", 6, "unit", "protein"),
        ("onion", 2, "unit", "produce"),
    ],
    "sandwich": [
        ("bread", 1, "pack", "bakery"),
        ("cheese", 1, "pack", "dairy"),
        ("tomato", 2, "unit", "produce"),
    ],
    "fried rice": [
        ("rice", 1, "kg", "pantry"),
        ("egg", 4, "unit", "protein"),
        ("onion", 2, "unit", "produce"),
    ],
    "paneer curry": [
        ("paneer", 1, "pack", "protein"),
        ("tomato", 4, "unit", "produce"),
        ("onion", 3, "unit", "produce"),
    ],
    "salad": [
        ("lettuce", 1, "unit", "produce"),
        ("tomato", 2, "unit", "produce"),
        ("cucumber", 2, "unit", "produce"),
    ],
}


def _normalize_category_name(category: str) -> str:
    cleaned = " ".join(category.strip().lower().split())
    if not cleaned:
        return "other"
    return CATEGORY_ALIASES.get(cleaned, cleaned)


def _format_qty(qty: float) -> str:
    return str(int(qty)) if float(qty).is_integer() else str(qty)


def _format_money(amount: float) -> str:
    return f"${amount:.2f}"


def _store_category_sort_key(category: str) -> tuple[int, str]:
    normalized = _normalize_category_name(category)
    try:
        return STORE_CATEGORY_ORDER.index(normalized), normalized
    except ValueError:
        return len(STORE_CATEGORY_ORDER), normalized


def _normalize_store_name(store: str) -> str:
    cleaned = " ".join(store.strip().lower().split())
    if not cleaned:
        raise ValueError("Store name cannot be empty.")
    return STORE_ALIASES.get(cleaned, cleaned)


def _normalize_grocery_name(name: str) -> str:
    normalized = " ".join(name.strip().lower().split())
    return GROCERY_SYNONYMS.get(normalized, normalized)


def _infer_grocery_category(item_name: str) -> str:
    lowered = _normalize_grocery_name(item_name)
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                return _normalize_category_name(category)
    return "other"


def _parse_grocery_add_payload(raw: str) -> tuple[str, float, str, str]:
    text = _strip_quotes(raw).strip()
    if not text:
        raise ValueError("Please provide an item name.")

    # Explicit schema: item|qty|unit|category
    if "|" in text:
        parts = [p.strip() for p in text.split("|")]
        item_name = parts[0]
        if not item_name:
            raise ValueError("Item name cannot be empty.")
        qty = 1.0
        unit = "unit"
        category = "other"
        if len(parts) > 1 and parts[1]:
            qty = _parse_float(parts[1])
        if len(parts) > 2 and parts[2]:
            unit = parts[2].lower()
        if len(parts) > 3 and parts[3]:
            category = _normalize_category_name(parts[3])
        item_name = _normalize_grocery_name(item_name)
        if category == "other":
            category = _infer_grocery_category(item_name)
        return item_name, qty, unit, category

    # Natural schema: "2 milk", "1.5 kg rice", "bread"
    tokens = text.split()
    qty = 1.0
    unit = "unit"

    first = tokens[0].lower()
    rest_tokens = tokens[:]
    compact_qty_unit = re.match(r"^(\d+(?:\.\d+)?)([a-zA-Z]+)$", first)
    if compact_qty_unit and compact_qty_unit.group(2).lower() in GROCERY_UNITS:
        qty = _parse_float(compact_qty_unit.group(1))
        unit = compact_qty_unit.group(2).lower()
        rest_tokens = tokens[1:]
    elif re.match(r"^\d+(?:\.\d+)?x?$", first):
        qty = _parse_float(first.rstrip("x"))
        rest_tokens = tokens[1:]
        if rest_tokens and rest_tokens[0].lower() in GROCERY_UNITS:
            unit = rest_tokens[0].lower()
            rest_tokens = rest_tokens[1:]
    elif first in GROCERY_UNITS:
        unit = first
        rest_tokens = tokens[1:]

    item_name = _normalize_grocery_name(" ".join(rest_tokens).strip())
    if not item_name:
        raise ValueError("Item name cannot be empty.")
    if qty <= 0:
        raise ValueError("Quantity must be greater than zero.")
    category = _infer_grocery_category(item_name)
    return item_name, qty, unit, category


def _parse_item_qty_unit(raw: str, default_qty: float = 1.0, default_unit: str = "unit") -> tuple[str, float, str]:
    text = _strip_quotes(raw).strip()
    if not text:
        raise ValueError("Please provide item details.")

    if "|" in text:
        parts = [p.strip() for p in text.split("|")]
        item_name = parts[0]
        qty = default_qty
        unit = default_unit
        if len(parts) > 1 and parts[1]:
            qty = _parse_float(parts[1])
        if len(parts) > 2 and parts[2]:
            unit = parts[2].lower()
        if not item_name:
            raise ValueError("Item name cannot be empty.")
        if qty <= 0:
            raise ValueError("Quantity must be greater than zero.")
        return _normalize_grocery_name(item_name), qty, unit

    patterns = [
        r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s+(.+)$",  # 2 kg rice
        r"^(\d+(?:\.\d+)?)\s+(.+)$",  # 2 rice
        r"^(.+?)\s+(\d+(?:\.\d+)?)\s*([a-zA-Z]+)$",  # rice 2 kg
        r"^(.+?)\s+(\d+(?:\.\d+)?)$",  # rice 2
    ]

    for idx, pattern in enumerate(patterns):
        m = re.match(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        if idx == 0:
            qty = _parse_float(m.group(1))
            unit = m.group(2).lower()
            item_name = m.group(3).strip()
        elif idx == 1:
            qty = _parse_float(m.group(1))
            unit = default_unit
            item_name = m.group(2).strip()
        elif idx == 2:
            item_name = m.group(1).strip()
            qty = _parse_float(m.group(2))
            unit = m.group(3).lower()
        else:
            item_name = m.group(1).strip()
            qty = _parse_float(m.group(2))
            unit = default_unit
        if not item_name:
            raise ValueError("Item name cannot be empty.")
        if qty <= 0:
            raise ValueError("Quantity must be greater than zero.")
        return _normalize_grocery_name(item_name), qty, unit

    return _normalize_grocery_name(text), default_qty, default_unit


def web_search(query: str) -> str:
    url = "https://duckduckgo.com/html/"
    try:
        resp = requests.post(url, data={"q": query}, timeout=12)
        resp.raise_for_status()
        html = resp.text
        # Very lightweight extraction from DDG HTML fallback page.
        matches = re.findall(
            r'<a rel="nofollow" class="result__a" href="(.*?)">(.*?)</a>', html
        )
        if not matches:
            return "No search results found."
        lines = []
        for href, title_html in matches[:3]:
            title = re.sub("<.*?>", "", title_html)
            lines.append(f"- {title}\n  {href}")
        return "Top web results:\n" + "\n".join(lines)
    except Exception as exc:
        return f"Web search failed: {exc}"


_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


def _eval_expr(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_eval_expr(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_eval_expr(node.left), _eval_expr(node.right))
    raise ValueError("Unsupported expression")


def calculate(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_expr(tree.body)
        return str(result)
    except Exception as exc:
        return f"Calculation failed: {exc}"


def wikipedia_summary(topic: str) -> str:
    try:
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(topic)}"
        resp = requests.get(url, timeout=12)
        if resp.status_code == 404:
            return f"No Wikipedia page found for '{topic}'."
        resp.raise_for_status()
        data = resp.json()
        extract = data.get("extract", "").strip()
        if not extract:
            return f"No summary found for '{topic}'."
        return extract[:1200]
    except Exception as exc:
        return f"Wikipedia lookup failed: {exc}"


def get_current_time(_: str = "") -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def create_task(arg: str, user_id: str) -> str:
    title = _strip_quotes(arg)
    if not title:
        return "Task title cannot be empty."
    task_id = TASK_DB.create_task(user_id, title)
    return f"Task #{task_id} created: {title}"


def list_tasks(arg: str, user_id: str) -> str:
    status = _strip_quotes(arg).lower() or None
    if status not in (None, "pending", "done"):
        status = None
    tasks = TASK_DB.list_tasks(user_id, status)
    if not tasks:
        return "No tasks found."
    lines = [f"{t['id']}. [{t['status']}] {t['title']}" for t in tasks]
    return "Your tasks:\n" + "\n".join(lines)


def complete_task(arg: str, user_id: str) -> str:
    try:
        task_id = _parse_int(_strip_quotes(arg))
    except Exception:
        return "Please provide a numeric task ID."
    ok = TASK_DB.complete_task(user_id, task_id)
    return "Task marked done." if ok else f"Task #{task_id} not found."


def delete_task(arg: str, user_id: str) -> str:
    try:
        task_id = _parse_int(_strip_quotes(arg))
    except Exception:
        return "Please provide a numeric task ID."
    ok = TASK_DB.delete_task(user_id, task_id)
    return "Task deleted." if ok else f"Task #{task_id} not found."


def add_grocery_item(arg: str, user_id: str) -> str:
    try:
        item_name, qty, unit, category = _parse_grocery_add_payload(arg)
    except ValueError as exc:
        return str(exc)
    except Exception:
        return "Could not parse item. Try: 'buy 2 milk' or 'rice|1|kg|produce'."

    result = TASK_DB.upsert_pending_grocery_item(
        user_id=user_id,
        item_name=item_name,
        qty=qty,
        unit=unit,
        category=category,
    )
    qty_text = _format_qty(float(result["qty"]))
    if result["merged"]:
        return f"Updated grocery item: {item_name} now {qty_text} {unit} ({category})."
    return f"Added grocery item: {item_name} ({qty_text} {unit}, {category})."


def list_grocery_items(arg: str, user_id: str) -> str:
    raw = _strip_quotes(arg).strip().lower()
    status: str | None = None
    view = "grouped"
    for token in raw.split():
        if token in {"pending", "bought", "all"}:
            status = token
        elif token in {"flat", "ungrouped"}:
            view = "flat"
        elif token in {"group", "grouped", "category", "categories", "store", "aisle"}:
            view = "grouped"
    rows = TASK_DB.list_grocery_items(user_id=user_id, status=status)
    if not rows:
        return "No grocery items found."
    if view == "flat":
        lines = []
        for idx, row in enumerate(rows, start=1):
            qty = float(row["qty"])
            qty_text = _format_qty(qty)
            unit_price = row.get("unit_price")
            if unit_price is not None:
                estimate = qty * float(unit_price)
                price_text = f", unit {_format_money(float(unit_price))}, est {_format_money(estimate)}"
            else:
                price_text = ""
            lines.append(
                f"{idx}. [{row['status']}] {row['item_name']} - {qty_text} {row['unit']} ({row['category']}{price_text})"
            )
        return "Grocery items:\n" + "\n".join(lines)

    by_category: dict[str, list[dict]] = {}
    for row in rows:
        category = _normalize_category_name(str(row.get("category") or "other"))
        by_category.setdefault(category, []).append(row)

    lines = ["Grocery items:"]
    counter = 1
    for category in sorted(by_category, key=_store_category_sort_key):
        lines.append(f"{category.title()}:")
        for row in by_category[category]:
            qty = float(row["qty"])
            qty_text = _format_qty(qty)
            unit_price = row.get("unit_price")
            if unit_price is not None:
                est = qty * float(unit_price)
                suffix = f" [{_format_money(float(unit_price))}/unit, est {_format_money(est)}]"
            else:
                suffix = ""
            lines.append(
                f"  {counter}. [{row['status']}] {row['item_name']} - {qty_text} {row['unit']}{suffix}"
            )
            counter += 1
    return "\n".join(lines)


def mark_grocery_bought(arg: str, user_id: str) -> str:
    ref = _resolve_grocery_ref(user_id, arg, preferred_status="pending")
    if not ref:
        return "Please provide a valid grocery item number from your list."
    ok = TASK_DB.mark_grocery_bought(user_id, int(ref["id"]))
    return (
        f"Grocery item marked bought: {ref['item_name']}."
        if ok
        else "Could not mark grocery item as bought."
    )


def remove_grocery_item(arg: str, user_id: str) -> str:
    ref = _resolve_grocery_ref(user_id, arg, preferred_status="pending")
    if not ref:
        return "Please provide a valid grocery item number from your list."
    ok = TASK_DB.remove_grocery_item(user_id, int(ref["id"]))
    return (
        f"Grocery item removed: {ref['item_name']}."
        if ok
        else "Could not remove grocery item."
    )


def clear_bought_groceries(arg: str, user_id: str) -> str:
    _ = arg
    deleted = TASK_DB.clear_grocery_items(user_id=user_id, status="bought")
    if deleted == 0:
        return "No bought groceries to clear."
    return f"Cleared {deleted} bought grocery item(s)."


def clear_all_groceries(arg: str, user_id: str) -> str:
    _ = arg
    deleted = TASK_DB.clear_grocery_items(user_id=user_id, status="all")
    if deleted == 0:
        return "No groceries to clear."
    return f"Cleared all groceries ({deleted} item(s))."


def _has_explicit_qty_or_unit(text: str) -> bool:
    t = _strip_quotes(text).strip().lower()
    if not t:
        return False
    if re.search(r"\d", t):
        return True
    tokens = t.split()
    return bool(tokens and tokens[0] in GROCERY_UNITS)


def replace_grocery_item(arg: str, user_id: str) -> str:
    raw = _strip_quotes(arg).strip()
    if not raw or "|" not in raw:
        return "Format: new_item|old_item (example: suji|rice)."

    new_text, old_text = [p.strip() for p in raw.split("|", 1)]
    if not new_text or not old_text:
        return "Both new and old item names are required."

    old_norm = _normalize_grocery_name(old_text)
    pending = TASK_DB.list_grocery_items(user_id=user_id, status="pending")
    target = None
    for row in reversed(pending):
        row_norm = _normalize_grocery_name(str(row.get("item_name", "")))
        if row_norm == old_norm:
            target = row
            break
    if target is None:
        for row in reversed(pending):
            row_norm = _normalize_grocery_name(str(row.get("item_name", "")))
            if old_norm in row_norm or row_norm in old_norm:
                target = row
                break

    try:
        new_name, parsed_qty, parsed_unit, parsed_category = _parse_grocery_add_payload(new_text)
    except Exception:
        return "Could not parse replacement item. Try: 'suji|rice' or '2 kg suji|rice'."

    explicit_new = _has_explicit_qty_or_unit(new_text)
    if target is not None:
        TASK_DB.remove_grocery_item(user_id=user_id, item_id=int(target["id"]))
        qty = float(parsed_qty) if explicit_new else float(target.get("qty") or 1.0)
        unit = parsed_unit if explicit_new else str(target.get("unit") or "unit")
        inferred_cat = _infer_grocery_category(new_name)
        if explicit_new:
            category = parsed_category
        else:
            category = inferred_cat if inferred_cat != "other" else str(target.get("category") or "other")
    else:
        qty = float(parsed_qty)
        unit = parsed_unit
        category = parsed_category

    result = TASK_DB.upsert_pending_grocery_item(
        user_id=user_id,
        item_name=new_name,
        qty=qty,
        unit=unit,
        category=category,
    )
    qty_text = _format_qty(float(result["qty"]))
    if target is None:
        return (
            f"Couldn't find pending '{old_text}'. Added '{new_name}' instead "
            f"({qty_text} {unit}, {category})."
        )
    if result["merged"]:
        return (
            f"Updated correction: replaced '{target['item_name']}' with '{new_name}'. "
            f"Now '{new_name}' is {qty_text} {unit} ({category})."
        )
    return (
        f"Updated correction: replaced '{target['item_name']}' with '{new_name}' "
        f"({qty_text} {unit}, {category})."
    )


def _parse_id_with_value(raw: str) -> tuple[int, str]:
    text = _strip_quotes(raw).strip()
    if "|" in text:
        left, right = text.split("|", 1)
        return _parse_int(left), right.strip()
    m = re.match(r"^(\d+)\s+(.+)$", text)
    if not m:
        raise ValueError("Format must be '<item_number> <value>' or '<item_number>|<value>'.")
    return _parse_int(m.group(1)), m.group(2).strip()


def _ordered_grocery_rows_for_display(user_id: str, status: str | None) -> list[dict]:
    rows = TASK_DB.list_grocery_items(user_id=user_id, status=status)
    by_category: dict[str, list[dict]] = {}
    for row in rows:
        category = _normalize_category_name(str(row.get("category") or "other"))
        by_category.setdefault(category, []).append(row)

    ordered: list[dict] = []
    for category in sorted(by_category, key=_store_category_sort_key):
        ordered.extend(by_category[category])
    return ordered


def _resolve_grocery_ref(user_id: str, number_text: str, preferred_status: str = "pending") -> dict | None:
    try:
        ref_num = _parse_int(_strip_quotes(number_text))
    except Exception:
        return None

    preferred_rows = _ordered_grocery_rows_for_display(user_id, preferred_status)
    if 1 <= ref_num <= len(preferred_rows):
        row = dict(preferred_rows[ref_num - 1])
        row["_ref_mode"] = "display"
        row["_display_index"] = ref_num
        return row

    all_rows = _ordered_grocery_rows_for_display(user_id, "all")
    if 1 <= ref_num <= len(all_rows):
        row = dict(all_rows[ref_num - 1])
        row["_ref_mode"] = "display_all"
        row["_display_index"] = ref_num
        return row

    row = TASK_DB.get_grocery_item(user_id, ref_num)
    if row:
        row = dict(row)
        row["_ref_mode"] = "db_id"
        row["_display_index"] = None
        return row
    return None


def set_grocery_category(arg: str, user_id: str) -> str:
    try:
        item_ref, category = _parse_id_with_value(arg)
        category = _normalize_category_name(category)
        if not category:
            return "Category cannot be empty."
    except Exception:
        return "Format: <item_number> <category> (example: '1 produce')."

    ref = _resolve_grocery_ref(user_id, str(item_ref), preferred_status="pending")
    if not ref:
        return "Please provide a valid grocery item number from your list."

    ok = TASK_DB.set_grocery_category(user_id, int(ref["id"]), category)
    return (
        f"Grocery item updated: {ref['item_name']} category set to '{category}'."
        if ok
        else "Could not update grocery category."
    )


def set_grocery_price(arg: str, user_id: str) -> str:
    try:
        item_ref, price_raw = _parse_id_with_value(arg)
        price = _parse_float(price_raw.replace("$", "").strip())
        if price <= 0:
            return "Price must be greater than zero."
    except Exception:
        return "Format: <item_number> <unit_price> (example: '1 3.50')."

    ref = _resolve_grocery_ref(user_id, str(item_ref), preferred_status="pending")
    if not ref:
        return "Please provide a valid grocery item number from your list."

    ok = TASK_DB.set_grocery_unit_price(user_id, int(ref["id"]), price)
    return (
        f"Grocery item updated: {ref['item_name']} unit price set to {_format_money(price)}."
        if ok
        else "Could not update grocery price."
    )


def grocery_budget_summary(arg: str, user_id: str) -> str:
    status = _strip_quotes(arg).strip().lower() or "pending"
    if status not in {"pending", "bought", "all"}:
        return "Status must be one of: pending, bought, all."

    stats = TASK_DB.grocery_budget_summary(user_id, status=status)
    if stats["item_count"] == 0:
        return "No grocery items found for budget summary."

    lines = [
        f"Grocery budget summary ({status}):",
        f"- Items: {stats['item_count']}",
        f"- Priced: {stats['priced_count']}",
        f"- Missing price: {stats['missing_price_count']}",
        f"- Estimated total: {_format_money(float(stats['estimated_total']))}",
    ]
    categories = sorted(
        stats["categories"],
        key=lambda c: _store_category_sort_key(str(c["category"])),
    )
    lines.append("- By category:")
    for cat in categories:
        lines.append(
            "  "
            + f"{str(cat['category']).title()}: {cat['item_count']} items, "
            + f"priced {cat['priced_count']}, missing {cat['missing_price_count']}, "
            + f"est {_format_money(float(cat['estimated_total']))}"
        )
    return "\n".join(lines)


def _parse_repeat_days(arg: str) -> int:
    text = _strip_quotes(arg).strip().lower()
    if not text:
        return 7
    if text.isdigit():
        return max(1, int(text))
    m = re.search(r"(\d+)\s*(d|day|days)\b", text)
    if m:
        return max(1, int(m.group(1)))
    m = re.search(r"(\d+)\s*(w|week|weeks)\b", text)
    if m:
        return max(1, int(m.group(1)) * 7)
    m = re.search(r"(\d+)\s*(m|month|months)\b", text)
    if m:
        return max(1, int(m.group(1)) * 30)
    if "week" in text:
        return 7
    if "month" in text:
        return 30
    return 7


def repeat_last_groceries(arg: str, user_id: str) -> str:
    days = _parse_repeat_days(arg)
    result = TASK_DB.repeat_recent_bought_to_pending(user_id=user_id, days=days, limit=40)
    if result["total_candidates"] == 0:
        return f"No bought groceries found in the last {days} day(s)."
    preview = ", ".join(result["items"][:8])
    more = ""
    if len(result["items"]) > 8:
        more = f" (+{len(result['items']) - 8} more)"
    return (
        f"Repeated groceries from last {days} day(s): "
        f"{result['created']} created, {result['merged']} merged. "
        f"Items: {preview}{more}"
    )


def suggest_rebuy(arg: str, user_id: str) -> str:
    _ = arg
    rows = TASK_DB.suggest_rebuy_candidates(user_id=user_id, limit=8)
    if not rows:
        return "No rebuy suggestions yet. Mark items as bought a few times first."
    lines = ["Rebuy suggestions:"]
    for row in rows:
        qty = _format_qty(float(row["avg_qty"]))
        lines.append(
            f"- {row['item_name']} ({qty} {row['unit']}, {row['category']}) "
            f"[bought {row['times_bought']} times]"
        )
    return "\n".join(lines)


def _parse_meal_list(arg: str) -> list[str]:
    text = _strip_quotes(arg).strip().lower()
    if not text:
        return []
    text = re.sub(r"\b(and|&)\b", ",", text)
    raw_parts = [p.strip() for p in text.split(",") if p.strip()]
    return raw_parts


def plan_meals_to_grocery(arg: str, user_id: str) -> str:
    meals = _parse_meal_list(arg)
    if not meals:
        return "Please provide meals, e.g. 'pasta, omelette, salad'."

    added = 0
    merged = 0
    unknown: list[str] = []
    for meal in meals:
        meal_key = None
        if meal in MEAL_INGREDIENTS:
            meal_key = meal
        else:
            for known in MEAL_INGREDIENTS:
                if known in meal:
                    meal_key = known
                    break
        if meal_key is None:
            unknown.append(meal)
            continue

        for item_name, qty, unit, category in MEAL_INGREDIENTS[meal_key]:
            result = TASK_DB.upsert_pending_grocery_item(
                user_id=user_id,
                item_name=item_name,
                qty=qty,
                unit=unit,
                category=category,
            )
            if result["merged"]:
                merged += 1
            else:
                added += 1

    lines = [f"Meal planner added groceries: {added} created, {merged} merged."]
    if unknown:
        lines.append(f"Unknown meals skipped: {', '.join(unknown)}")
    return "\n".join(lines)


def record_store_price(arg: str, user_id: str) -> str:
    text = _strip_quotes(arg).strip()
    if not text:
        return "Format: item|store|price (example: milk|fairprice|3.25)."

    if "|" in text:
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 3:
            return "Format: item|store|price (example: milk|fairprice|3.25)."
        item_name = parts[0]
        store = parts[1]
        price_text = parts[2]
    else:
        m = re.match(
            r"^(.+?)\s+at\s+([a-zA-Z ]+)\s+\$?([0-9]+(?:\.[0-9]+)?)$",
            text,
            flags=re.IGNORECASE,
        )
        if not m:
            return "Format: item|store|price (example: milk|fairprice|3.25)."
        item_name = m.group(1).strip()
        store = m.group(2).strip()
        price_text = m.group(3).strip()

    try:
        unit_price = _parse_float(price_text.replace("$", ""))
        normalized_store = _normalize_store_name(store)
        rec_id = TASK_DB.record_store_price(
            user_id=user_id,
            item_name=_normalize_grocery_name(item_name),
            store=normalized_store,
            unit_price=unit_price,
            currency="SGD",
            source="manual",
        )
    except Exception as exc:
        return f"Could not save store price: {exc}"
    return f"Saved price #{rec_id}: {_normalize_grocery_name(item_name)} at {normalized_store} = {_format_money(unit_price)}."


def compare_store_price(arg: str, user_id: str) -> str:
    item_name = _normalize_grocery_name(_strip_quotes(arg))
    if not item_name:
        return "Please provide item name, e.g. 'compare price milk'."
    rows = TASK_DB.compare_store_prices(user_id=user_id, item_name=item_name)
    if not rows:
        return f"No store price data found for '{item_name}'. Add with: price {item_name}|fairprice|3.25"
    cheapest = rows[0]
    priciest = rows[-1]
    savings = max(float(priciest["unit_price"]) - float(cheapest["unit_price"]), 0.0)
    lines = [
        f"Store price comparison for '{item_name}':",
        f"- Best: {cheapest['store']} at {_format_money(float(cheapest['unit_price']))}",
    ]
    if len(rows) > 1:
        lines.append(f"- Max possible saving: {_format_money(savings)}")
    lines.append("- Prices:")
    for row in rows:
        lines.append(
            f"  - {row['store']}: {_format_money(float(row['unit_price']))} "
            f"({row['currency']}, seen {row['observed_at'][:10]})"
        )
    return "\n".join(lines)


def _maybe_auto_replenish(user_id: str, item_name: str, stock_state: dict) -> str:
    if not stock_state.get("is_low"):
        return ""
    if int(stock_state.get("auto_replenish", 1)) != 1:
        return " Stock is low, but auto-replenish is disabled."
    needed_qty = float(stock_state.get("needed_qty") or 0.0)
    if needed_qty <= 0:
        return " Stock is low."
    result = TASK_DB.upsert_pending_grocery_item(
        user_id=user_id,
        item_name=item_name,
        qty=needed_qty,
        unit=str(stock_state.get("unit") or "unit"),
        category=_infer_grocery_category(item_name),
    )
    qty_text = _format_qty(float(needed_qty))
    if result["merged"]:
        return f" Auto-added low-stock refill: {qty_text} {stock_state.get('unit')} {item_name} (merged)."
    return f" Auto-added low-stock refill: {qty_text} {stock_state.get('unit')} {item_name}."


def stock_item(arg: str, user_id: str) -> str:
    try:
        item_name, qty, unit = _parse_item_qty_unit(arg, default_qty=1.0, default_unit="unit")
        stock = TASK_DB.adjust_inventory_stock(
            user_id=user_id,
            item_name=item_name,
            qty_delta=qty,
            unit=unit,
            event_type="restock",
            note="stock added",
        )
    except Exception as exc:
        return f"Could not update stock: {exc}"
    qty_text = _format_qty(float(stock['qty_on_hand']))
    return f"Stock updated: {item_name} now {qty_text} {unit} on hand."


def use_item(arg: str, user_id: str) -> str:
    try:
        item_name, qty, unit = _parse_item_qty_unit(arg, default_qty=1.0, default_unit="unit")
        stock = TASK_DB.adjust_inventory_stock(
            user_id=user_id,
            item_name=item_name,
            qty_delta=-qty,
            unit=unit,
            event_type="consume",
            note="used item",
        )
    except Exception as exc:
        return f"Could not update usage: {exc}"
    qty_text = _format_qty(float(stock["qty_on_hand"]))
    low_suffix = _maybe_auto_replenish(user_id, item_name, stock)
    return f"Usage recorded: {item_name} now {qty_text} {unit} on hand.{low_suffix}"


def set_stock_item(arg: str, user_id: str) -> str:
    try:
        item_name, qty, unit = _parse_item_qty_unit(arg, default_qty=0.0, default_unit="unit")
        stock = TASK_DB.set_inventory_stock(
            user_id=user_id,
            item_name=item_name,
            qty_on_hand=qty,
            unit=unit,
        )
    except Exception as exc:
        return f"Could not set stock: {exc}"
    qty_text = _format_qty(float(stock["qty_on_hand"]))
    if stock.get("is_low"):
        return (
            f"Stock set: {item_name} now {qty_text} {unit} on hand. "
            "Item is below threshold."
        )
    return f"Stock set: {item_name} now {qty_text} {unit} on hand."


def set_inventory_threshold(arg: str, user_id: str) -> str:
    try:
        item_name, threshold, unit = _parse_item_qty_unit(arg, default_qty=1.0, default_unit="unit")
        stock = TASK_DB.set_inventory_threshold(
            user_id=user_id,
            item_name=item_name,
            threshold_qty=threshold,
            unit=unit,
        )
    except Exception as exc:
        return f"Could not set threshold: {exc}"
    threshold_text = _format_qty(float(stock["threshold_qty"]))
    if stock.get("is_low"):
        return (
            f"Threshold set: {item_name} low-stock threshold is {threshold_text} {unit}. "
            "Current stock is below threshold."
        )
    return f"Threshold set: {item_name} low-stock threshold is {threshold_text} {unit}."


def list_inventory(arg: str, user_id: str) -> str:
    raw = _strip_quotes(arg).strip().lower()
    low_only = "low" in raw
    rows = TASK_DB.list_inventory_items(user_id=user_id, low_only=low_only)
    if not rows:
        return "No inventory items found."
    lines = ["Inventory:"]
    for row in rows:
        qty = float(row["qty_on_hand"])
        threshold = float(row["threshold_qty"])
        is_low = threshold > 0 and qty < threshold
        status = "LOW" if is_low else "OK"
        lines.append(
            f"- {row['item_name']}: {_format_qty(qty)} {row['unit']} "
            f"(threshold {_format_qty(threshold)} {row['unit']}) [{status}]"
        )
    return "\n".join(lines)


def report_item_low(arg: str, user_id: str) -> str:
    item_name = _normalize_grocery_name(_strip_quotes(arg))
    if not item_name:
        return "Please provide item name, e.g. 'toilet tissue low'."
    result = TASK_DB.upsert_pending_grocery_item(
        user_id=user_id,
        item_name=item_name,
        qty=1.0,
        unit="unit",
        category=_infer_grocery_category(item_name),
    )
    if result["merged"]:
        return f"Marked low and updated grocery refill for {item_name}."
    return f"Marked low and added {item_name} to grocery list."


def read_file(arg: str, user_id: str) -> str:
    filename = _strip_quotes(arg)
    if not filename:
        return "Please provide a filename."
    try:
        path = _safe_workspace_path(user_id, filename)
        if not path.exists():
            return f"File does not exist: {filename}"
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Read failed: {exc}"


def write_file(arg: str, user_id: str) -> str:
    # Expected format: filename|content
    raw = _strip_quotes(arg)
    if "|" not in raw:
        return "Format: filename|content"
    filename, content = raw.split("|", 1)
    filename = filename.strip()
    if not filename:
        return "Filename cannot be empty."
    try:
        path = _safe_workspace_path(user_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote file: {filename}"
    except Exception as exc:
        return f"Write failed: {exc}"


def parse_when(text: str, user_tz: str) -> datetime | None:
    text = text.strip().lower()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(user_tz)
    except Exception:
        tz = timezone.utc
        
    now = datetime.now(tz)
    
    m = re.match(r"^in\s+(\d+)\s*(m|min|mins|minute|minutes)$", text)
    if m: return now + timedelta(minutes=int(m.group(1)))
    
    h = re.match(r"^in\s+(\d+)\s*(h|hr|hrs|hour|hours)$", text)
    if h: return now + timedelta(hours=int(h.group(1)))
    
    d = re.match(r"^in\s+(\d+)\s*(d|day|days)$", text)
    if d: return now + timedelta(days=int(d.group(1)))
    
    tmrw = re.match(r"^tomorrow(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", text)
    if tmrw:
        hr = int(tmrw.group(1))
        mn = int(tmrw.group(2) or 0)
        ampm = tmrw.group(3)
        if ampm == "pm" and hr < 12: hr += 12
        if ampm == "am" and hr == 12: hr = 0
        return (now + timedelta(days=1)).replace(hour=hr, minute=mn, second=0, microsecond=0)
        
    tdy = re.match(r"^(?:today\s+at|at)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", text)
    if tdy:
        hr = int(tdy.group(1))
        mn = int(tdy.group(2) or 0)
        ampm = tdy.group(3)
        if ampm == "pm" and hr < 12: hr += 12
        if ampm == "am" and hr == 12: hr = 0
        dt = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if dt < now: dt += timedelta(days=1)
        return dt
        
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})$", text)
    if iso:
        try:
            return now.replace(
                year=int(iso.group(1)), month=int(iso.group(2)), day=int(iso.group(3)),
                hour=int(iso.group(4)), minute=int(iso.group(5)), second=0, microsecond=0
            )
        except ValueError:
            pass
            
    return None


def set_reminder(arg: str, user_id: str) -> str:
    # Format: message|time_string
    raw = _strip_quotes(arg)
    if "|" not in raw:
        return "Format: message|time_string"
    msg, when = [s.strip() for s in raw.split("|", 1)]
    if not msg:
        return "Reminder message cannot be empty."
        
    settings = TASK_DB.get_user_settings(user_id) or {}
    user_tz = settings.get("timezone") or "UTC"
    
    dt = parse_when(when, user_tz)
    if dt is None:
        return "Unsupported reminder time format. Use e.g. 'in 10 mins', 'tomorrow at 3pm', or 'at 7pm'."
        
    remind_at = dt.astimezone(timezone.utc).isoformat()
    reminder_id = TASK_DB.add_reminder(user_id, msg, remind_at)
    return f"Reminder #{reminder_id} set for {dt.strftime('%Y-%m-%d %H:%M %Z')}."


def save_note(arg: str, user_id: str) -> str:
    content = _strip_quotes(arg)
    if not content:
        return "Note content cannot be empty."
    note_id = TASK_DB.save_note(user_id, content)
    return f"Saved note #{note_id}."


def daily_summary(arg: str, user_id: str) -> str:
    _ = arg
    stats = TASK_DB.daily_summary(user_id)
    mem_hits = _memory_store().recall(user_id, "important tasks reminders notes", k=3)
    lines = [
        "Daily summary:",
        f"- Total tasks: {stats['task_total']}",
        f"- Pending tasks: {stats['task_pending']}",
        f"- Done tasks: {stats['task_done']}",
    ]
    if stats["recent_notes"]:
        lines.append("- Recent notes:")
        for n in stats["recent_notes"][:3]:
            lines.append(f"  - {n['content'][:120]}")
    if mem_hits:
        lines.append("- Memory highlights:")
        for m in mem_hits:
            lines.append(f"  - {m[:120]}")
    return "\n".join(lines)


TOOLS: dict[str, Callable[[str, str], str]] = {
    "web_search": lambda arg, _uid: web_search(_strip_quotes(arg)),
    "calculate": lambda arg, _uid: calculate(_strip_quotes(arg)),
    "wikipedia_summary": lambda arg, _uid: wikipedia_summary(_strip_quotes(arg)),
    "get_current_time": lambda arg, _uid: get_current_time(_strip_quotes(arg)),
    "create_task": create_task,
    "list_tasks": list_tasks,
    "complete_task": complete_task,
    "delete_task": delete_task,
    "add_grocery_item": add_grocery_item,
    "list_grocery_items": list_grocery_items,
    "mark_grocery_bought": mark_grocery_bought,
    "remove_grocery_item": remove_grocery_item,
    "clear_bought_groceries": clear_bought_groceries,
    "clear_all_groceries": clear_all_groceries,
    "replace_grocery_item": replace_grocery_item,
    "set_grocery_category": set_grocery_category,
    "set_grocery_price": set_grocery_price,
    "grocery_budget_summary": grocery_budget_summary,
    "repeat_last_groceries": repeat_last_groceries,
    "suggest_rebuy": suggest_rebuy,
    "plan_meals_to_grocery": plan_meals_to_grocery,
    "record_store_price": record_store_price,
    "compare_store_price": compare_store_price,
    "stock_item": stock_item,
    "use_item": use_item,
    "set_stock_item": set_stock_item,
    "set_inventory_threshold": set_inventory_threshold,
    "list_inventory": list_inventory,
    "report_item_low": report_item_low,
    "read_file": read_file,
    "write_file": write_file,
    "set_reminder": set_reminder,
    "save_note": save_note,
    "daily_summary": daily_summary,
}


TOOL_DESCRIPTIONS: dict[str, str] = {
    "web_search": "Search the web. Args: query string.",
    "calculate": "Evaluate arithmetic expression safely. Args: expression string.",
    "wikipedia_summary": "Get Wikipedia summary. Args: topic string.",
    "get_current_time": "Get current local date/time. Args: empty string.",
    "create_task": "Create TODO task. Args: task title.",
    "list_tasks": "List tasks for current user. Args: optional status ('pending'|'done' or empty).",
    "complete_task": "Mark task as done. Args: numeric task id.",
    "delete_task": "Delete task by id. Args: numeric task id.",
    "add_grocery_item": "Add/merge grocery entry. Args: natural text or item|qty|unit|category.",
    "list_grocery_items": "List grocery entries. Args: optional status pending|bought|all and view grouped|flat.",
    "mark_grocery_bought": "Mark grocery item bought. Args: grocery item number from list (1-based).",
    "remove_grocery_item": "Remove grocery item. Args: grocery item number from list (1-based).",
    "clear_bought_groceries": "Delete all bought groceries for current user. Args: empty string.",
    "clear_all_groceries": "Delete all groceries for current user. Args: empty string.",
    "replace_grocery_item": "Replace a pending grocery item. Args: new_item|old_item.",
    "set_grocery_category": "Set grocery category. Args: '<item_number> <category>' or '<item_number>|<category>'.",
    "set_grocery_price": "Set grocery unit price. Args: '<item_number> <price>' or '<item_number>|<price>'.",
    "grocery_budget_summary": "Show grocery estimated total using unit prices. Args: pending|bought|all.",
    "repeat_last_groceries": "Repeat recently bought groceries into pending list. Args: optional period like 7d/2w/month.",
    "suggest_rebuy": "Suggest recurring items that are not currently pending. Args: empty string.",
    "plan_meals_to_grocery": "Convert meals into grocery items. Args: comma-separated meals (e.g. pasta, omelette).",
    "record_store_price": "Record manual store price. Args: item|store|price.",
    "compare_store_price": "Compare latest prices across stores. Args: item name.",
    "stock_item": "Increase home inventory stock. Args: item|qty|unit or natural text.",
    "use_item": "Decrease home inventory stock. Args: item|qty|unit or natural text.",
    "set_stock_item": "Set absolute home stock level. Args: item|qty|unit or natural text.",
    "set_inventory_threshold": "Set low-stock threshold. Args: item|qty|unit or natural text.",
    "list_inventory": "List inventory items. Args: optional 'low'.",
    "report_item_low": "Mark item as low and add refill to grocery list. Args: item name.",
    "read_file": "Read file in current user workspace. Args: filename.",
    "write_file": "Write file in current user workspace. Args: filename|content.",
    "set_reminder": "Set reminder. Args: message|in 10m (or in 2h).",
    "save_note": "Save short note. Args: note text.",
    "daily_summary": "Summarize tasks, notes, and memory highlights. Args: empty string.",
}


def tools_description_text() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in TOOL_DESCRIPTIONS.items())


def run_tool(name: str, arg: str, user_id: str) -> str:
    fn = TOOLS.get(name)
    if fn is None:
        return f"Unknown tool: {name}. Available: {', '.join(sorted(TOOLS))}"
    try:
        out = fn(arg, user_id)
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
    except Exception as exc:
        return f"Tool '{name}' failed: {exc}"
