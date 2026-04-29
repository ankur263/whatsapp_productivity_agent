import os
from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()
test_user = "+18885552222"

print("📊 TESTING FINANCE BUDGET & SUMMARY...\n")

# 1. Setup Admin
router.route(test_user, "/claim admin2026")
print("✅ Setup Complete: User authenticated.\n")

# 2. Set Budget via Chat
print("User: Set my monthly food budget to $300")
reply1 = router.route(test_user, "Set my monthly food budget to $300")
print(f"Bot: {reply1}\n")
assert "300" in reply1, "Bot failed to set the budget!"

# 3. Log some expenses
print("User: Log $45 for food")
reply2 = router.route(test_user, "Log $45 for food")
print(f"Bot: {reply2}\n")

print("User: Log $120 for transport")
reply3 = router.route(test_user, "Log $120 for transport")
print(f"Bot: {reply3}\n")

# 4. Get Monthly Summary
print("User: Give me my expense summary for this month")
reply4 = router.route(test_user, "Give me my expense summary for this month")
print(f"Bot: {reply4}\n")
assert "food" in reply4.lower() and "transport" in reply4.lower(), "Summary is missing categories!"

print("🎉 FINANCE BUDGET & SUMMARY TESTS PASSED!")