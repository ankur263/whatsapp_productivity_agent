import os
from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()
test_user = "+18884443333"

print("📄 TESTING FINANCE CSV STATEMENT INGESTION...\n")

# 1. Setup Admin
router.route(test_user, "/claim admin2026")
print("✅ Setup Complete: User authenticated.\n")

# 2. Simulate sending CSV text
csv_text = """Please parse this CSV bank statement and log the expenses:
Date,Merchant,Amount,Category
2023-10-01,Uber,15.50,transport
2023-10-02,Starbucks,5.00,food
2023-10-03,Netflix,12.99,subscriptions
2023-10-04,Shell Gas,45.00,transport
"""

print("User: [Uploading CSV statement text...]")
reply1 = router.route(test_user, csv_text)
print(f"Bot: {reply1}\n")
assert "4" in reply1 or "Successfully logged" in reply1, "Bot failed to log the multiple expenses!"

# 3. Verify via Monthly Summary
print("User: What is my expense summary for this month?")
reply2 = router.route(test_user, "What is my expense summary for this month?")
print(f"Bot: {reply2}\n")
assert "transport" in reply2.lower() and "food" in reply2.lower() and "subscriptions" in reply2.lower(), "Summary is missing parsed categories!"

print("🎉 FINANCE CSV INGESTION TEST PASSED!")