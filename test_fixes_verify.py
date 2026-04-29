"""Deterministic verification of the four bug fixes — no LLM calls.

Confirms:
  1. database._grocery_scope exists and grocery write+read round-trips.
  2. tool_fallback.parse_leaked_tool_calls + absorb_leaked_calls correctly
     extract Llama-style <function=NAME>JSON</function> leaks.
  3. database.log_expense / get_expenses_for_period / get_last_expense /
     delete_expense exist and round-trip.
  4. database.search_notes finds saved notes by substring AND keyword.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).parent
DB = ROOT / "data" / "tasks.db"
if DB.exists():
    DB.unlink()

from database import TaskDatabase
from tool_fallback import parse_leaked_tool_calls, absorb_leaked_calls

PASS = 0
FAIL = 0
def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {label}")
    else:
        FAIL += 1
        print(f"  ❌ {label}  ({detail})")

db = TaskDatabase()
USER = "+10000000001"
USER2 = "+10000000002"

# -------- Fix 1: _grocery_scope --------
print("\n[1] _grocery_scope exists + grocery I/O works")
check("_grocery_scope is a method", hasattr(db, "_grocery_scope"))
res = db.upsert_pending_grocery_item(user_id=USER, item_name="apple", qty=3.0, unit="ea", category="produce")
check("upsert_pending_grocery_item returns dict with id",
      isinstance(res, dict) and "id" in res, repr(res))
rows = db.list_grocery_items(user_id=USER)
check("list_grocery_items returns the apple",
      any("apple" in r["item_name"].lower() for r in rows), repr(rows))

# Add a second item, then merge same name+unit (should bump qty)
db.upsert_pending_grocery_item(user_id=USER, item_name="apple", qty=2.0, unit="ea", category="produce")
rows = db.list_grocery_items(user_id=USER)
apples = [r for r in rows if r["item_name"].lower() == "apple"]
check("repeat add merges same item", len(apples) == 1 and float(apples[0]["qty"]) == 5.0,
      f"apples={apples}")

# Household scoping: create household for USER, switch active, add scoped item
hh_id = db.create_household(USER, "TestHouse")
db.upsert_user_setting(USER, "active_household_id", hh_id)
db.upsert_pending_grocery_item(user_id=USER, item_name="milk", qty=1.0, unit="L", category="dairy")
rows_hh = db.list_grocery_items(user_id=USER)
check("scoped item visible in active household",
      any("milk" in r["item_name"].lower() for r in rows_hh), repr(rows_hh))

# -------- Fix 2: tool_fallback parses leaked tool calls --------
print("\n[2] tool_fallback parses Llama-style tool leakage")
samples = {
    'clean': '<function=list_tasks>{"status": "pending"}</function>',
    'no body': '<function=list_events></function>',
    'body inside opening tag': '<function=list_tasks {"status": "pending"} </function>',
    'with prefix text': "📝 <function=calculate>{\"expression\": \"25 * 4 + 10\"}</function>",
    'multiple': "<function=foo>{\"a\":1}</function> and <function=bar>{\"b\":2}</function>",
}
for label, txt in samples.items():
    leaks = parse_leaked_tool_calls(txt)
    check(f"parse '{label}'", len(leaks) >= 1, f"got {leaks}")

# absorb test: pretend tool returned something and verify messages mutate
calls_made = []
def fake_tool(**kw):
    calls_made.append(kw)
    return f"OK {kw}"
local = {"calculate": fake_tool}
msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "Q"}]
handled = absorb_leaked_calls(msgs, '<function=calculate>{"expression": "1+1"}</function>', local)
check("absorb_leaked_calls returned True", handled is True)
check("absorb appended assistant + user msgs", len(msgs) == 4, f"len={len(msgs)}")
check("absorb executed local tool", calls_made and calls_made[0].get("expression") == "1+1",
      f"calls_made={calls_made}")

handled = absorb_leaked_calls(msgs, "no tags here at all", local)
check("absorb returns False on plain text", handled is False)

# -------- Fix 3: finance DB methods --------
print("\n[3] finance DB methods exist + round-trip")
for m in ("log_expense", "get_expenses_for_period", "get_last_expense", "delete_expense"):
    check(f"db.{m} exists", hasattr(db, m))

eid = db.log_expense(user_id=USER2, amount_minor=2500, currency="USD",
                    category="transport", method="manual")
check("log_expense returned id", isinstance(eid, int) and eid > 0, f"eid={eid}")
last = db.get_last_expense(USER2)
check("get_last_expense matches just-logged",
      last and last["amount_minor"] == 2500 and last["category"] == "transport",
      repr(last))

# Period query (today)
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc)
start = (now - timedelta(days=1)).isoformat()
end = (now + timedelta(days=1)).isoformat()
period = db.get_expenses_for_period(USER2, start, end)
check("get_expenses_for_period returns the expense",
      any(e["id"] == eid for e in period), f"period={period}")

ok = db.delete_expense(USER2, eid)
check("delete_expense succeeds", ok is True)

# -------- Fix 4: notes search merges with recall --------
print("\n[4] search_notes finds saved notes")
db.save_note(USER, "my locker code is 4321")
db.save_note(USER, "buy detergent next week")
hits = db.search_notes(USER, "locker", limit=5)
check("substring search finds 'locker' note",
      any("locker" in h["content"].lower() for h in hits), repr(hits))

hits2 = db.search_notes(USER, "locker code please", limit=5)
check("keyword fallback finds note",
      any("locker" in h["content"].lower() for h in hits2), repr(hits2))

hits3 = db.search_notes(USER, "completely unrelated nonsense", limit=5)
check("no match returns []", hits3 == [], repr(hits3))

# -------- Summary --------
print(f"\n{'='*50}\n  {PASS} passed / {FAIL} failed\n{'='*50}")
sys.exit(0 if FAIL == 0 else 1)
