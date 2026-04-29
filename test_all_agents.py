import os
import re
from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()
test_user = "+14445556666"

print("🚀 STARTING RIGOROUS MULTI-AGENT TEST SUITE...\n")

# 0. Setup
print("--- 0. SETUP ---")
router.route(test_user, "/claim admin2026")
print("✅ Admin claimed and workspace initialized.\n")

# 1. Planner Agent
print("--- 1. PLANNER AGENT ---")
reply = router.route(test_user, "Create a task to buy new shoes")
print(f"Bot: {reply}")
assert "task" in reply.lower() or "shoe" in reply.lower(), "Planner failed to create task."

reply = router.route(test_user, "Show my pending tasks")
print(f"Bot: {reply}")
assert "shoe" in reply.lower(), "Planner failed to list tasks."
print("✅ Planner tests passed.\n")

# 2. Events Agent
print("--- 2. EVENTS AGENT ---")
reply = router.route(test_user, "Add my friend John's birthday on 2024-05-15 recurring yearly")
print(f"Bot: {reply}")
assert "event" in reply.lower() or "john" in reply.lower(), "Events failed to add event."

reply = router.route(test_user, "List my events")
print(f"Bot: {reply}")
assert "john" in reply.lower(), "Events failed to list event."
print("✅ Events tests passed.\n")

# 3. Finance Agent
print("--- 3. FINANCE AGENT ---")
reply = router.route(test_user, "Log $25 for transport")
print(f"Bot: {reply}")
assert "25" in reply and "transport" in reply.lower(), "Finance failed to log expense."

reply = router.route(test_user, "What is my monthly summary?")
print(f"Bot: {reply}")
assert "transport" in reply.lower() and "25" in reply, "Finance failed to summarize expenses."
print("✅ Finance tests passed.\n")

# 4. Shopping Agent
print("--- 4. SHOPPING AGENT ---")
reply = router.route(test_user, "Add 3 apples to my grocery list")
print(f"Bot: {reply}")
assert "apple" in reply.lower(), "Shopping failed to add grocery."

reply = router.route(test_user, "Show my groceries")
print(f"Bot: {reply}")
assert "apple" in reply.lower(), "Shopping failed to list groceries."
print("✅ Shopping tests passed.\n")

# 5. Knowledge Agent
print("--- 5. KNOWLEDGE AGENT ---")
reply = router.route(test_user, "Calculate 25 * 4 + 10")
print(f"Bot: {reply}")
assert "110" in reply, "Knowledge failed to calculate math."
print("✅ Knowledge tests passed.\n")

# 6. Journal Agent
print("--- 6. JOURNAL AGENT ---")
reply = router.route(test_user, "Save a private note that my locker code is 4321")
print(f"Bot: {reply}")

reply = router.route(test_user, "What is my locker code?")
print(f"Bot: {reply}")
assert "4321" in reply, "Journal failed to recall memory."
print("✅ Journal tests passed.\n")

# 7. Multi-Agent Plan (Supervisor Integration)
print("--- 7. SUPERVISOR MULTI-PLAN ---")
complex_req = "Remind me to call the plumber tomorrow at 9am, and also log $150 for utilities."
print(f"User: {complex_req}")
reply = router.route(test_user, complex_req)
print(f"Bot: {reply}")
assert "utilities" in reply.lower() or "150" in reply.lower(), "Supervisor failed multi-plan routing."
print("✅ Multi-Agent Plan passed.\n")

# 8. Extreme Edge Cases & Error Handling
print("--- 8. EXTREME EDGE CASES ---")

reply = router.route(test_user, "Calculate 50 / 0")
print(f"Bot [Div by Zero]: {reply}")
assert "fail" in reply.lower() or "error" in reply.lower() or "zero" in reply.lower(), "Knowledge failed to catch division by zero."

reply = router.route(test_user, "What is the password to my secret volcano lair?")
print(f"Bot [Fake Recall]: {reply}")
assert "found" in reply.lower() or "no" in reply.lower() or "don't know" in reply.lower(), "Journal hallucinated a fake memory!"

reply = router.route(test_user, "Log a new expense for grabbing a coffee")
print(f"Bot [Missing Amount]: {reply}")
assert "amount" in reply.lower() or "how much" in reply.lower(), "Finance improperly logged an expense without an amount!"

reply = router.route(test_user, "Add 0.5 liters of vanilla extract to my groceries")
print(f"Bot [Decimal/Fractions]: {reply}")
assert "vanilla" in reply.lower() or "extract" in reply.lower(), "Shopping failed to parse decimal units."

print("✅ All Edge Cases Handled Gracefully.\n")

print("🎉 ALL RIGOROUS TESTS COMPLETED SUCCESSFULLY!")