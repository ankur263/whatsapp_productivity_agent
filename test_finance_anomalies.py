import os
from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()
test_user = "+18889990000"

print("💸 TESTING FINANCE ANOMALIES & BUDGETS...\n")

# 1. Setup Admin
router.route(test_user, "/claim admin2026")
print("✅ Setup Complete: User authenticated.\n")

# 2. Set Budget (Directly via DB for testing)
router.db.set_budget(test_user, "food", 15000, "USD") # $150 budget
print("✅ Set monthly 'food' budget to $150.00.\n")

# 3. Log normal expenses to establish baseline
print("Logging normal expenses to establish baseline ($15 each)...")
for _ in range(3):
    router.route(test_user, "log $15 for food")
print("✅ Baseline established.\n")

# 4. Trigger Anomaly and 80% Budget Warning
print("User: log $80 for food (Should trigger anomaly AND budget warning)")
reply1 = router.route(test_user, "log $80 for food")
print(f"Bot: {reply1}\n")
assert "Anomaly Alert" in reply1, "Anomaly detection failed!"
assert "Budget Warning" in reply1, "Budget warning failed!"

# 5. Trigger Budget Exceeded
print("User: log $40 for food (Should trigger budget exceeded alert)")
reply2 = router.route(test_user, "log $40 for food")
print(f"Bot: {reply2}\n")
assert "Budget Alert" in reply2, "Budget exceeded alert failed!"

print("🎉 FINANCE ANOMALY & BUDGET TESTS PASSED!")