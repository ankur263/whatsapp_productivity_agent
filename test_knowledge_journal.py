import os
from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()

test_user = "+16660000001"

print("🧠 TESTING KNOWLEDGE & JOURNAL AGENTS...\n")

# 1. Setup: Claim Admin to bypass onboarding
router.route(test_user, "/claim admin2026")
print("✅ Setup Complete: User is authenticated.\n")

# 2. Test Knowledge: Calculation
print("User: What is 150 divided by 3 plus 42?")
reply_1 = router.route(test_user, "What is 150 divided by 3 plus 42?")
print(f"Bot: {reply_1}\n")

# 3. Test Knowledge: Wikipedia/Web
print("User: Give me a short summary of the Apollo 11 mission.")
reply_2 = router.route(test_user, "Give me a short summary of the Apollo 11 mission.")
print(f"Bot: {reply_2}\n")

# 4. Test Journal: Save private note / fact
print("User: Save a private note that my favorite color is cerulean blue.")
reply_3 = router.route(test_user, "Save a private note that my favorite color is cerulean blue.")
print(f"Bot: {reply_3}\n")

# 5. Test Journal: Semantic Recall
print("User: Do you remember what my favorite color is?")
reply_4 = router.route(test_user, "Do you remember what my favorite color is?")
print(f"Bot: {reply_4}\n")

assert "cerulean" in reply_4.lower(), "Bot failed to recall the fact from the Journal!"

print("🎉 KNOWLEDGE & JOURNAL TESTS PASSED!")