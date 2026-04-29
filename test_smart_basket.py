import os
from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()
test_user = "+19997771111"

print("🛒 TESTING SMART BASKET OPTIMIZATION...\n")

# 1. Setup Admin
router.route(test_user, "/claim admin2026")
print("✅ Setup Complete: User authenticated.")

# 2. Add some pending groceries
print("\nUser: add 2 milk and 1 bread to my grocery list")
reply1 = router.route(test_user, "add 2 milk and 1 bread to my grocery list")
print(f"Bot: {reply1}")

# 3. Simulate Receipt Parsing (via text)
prompt_prices = """Please log these store prices using your tools:
- NTUC: milk $3.50, bread $2.20
- Sheng Siong: milk $3.00, bread $2.00
"""
print("\nUser: [Simulating uploading receipt prices for NTUC and Sheng Siong...]")
reply2 = router.route(test_user, prompt_prices)
print(f"Bot: {reply2}")

# 4. Ask for optimization
print("\nUser: Where should I shop for my current groceries to save money?")
reply3 = router.route(test_user, "Where should I shop for my current groceries to save money?")
print(f"Bot: {reply3}")

assert "Sheng Siong" in reply3, "The bot failed to recommend the cheaper store!"
print("\n🎉 SMART BASKET OPTIMIZATION TEST COMPLETE!")