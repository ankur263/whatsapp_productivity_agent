import os
from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()

test_user = "+17770000001"

print("🛡️  TESTING PRIVACY BOUNCER...\n")

# 1. Setup: Claim Admin and create a second household
router.route(test_user, "/claim admin2026")
router.route(test_user, "/createhouse Couples Private")

print("✅ Setup Complete: User is in 'My Home' and 'Couples Private'.\n")

# 2. Send an ambiguous, high-stakes financial request
print("User: log $50 for dinner")
reply_1 = router.route(test_user, "log $50 for dinner")
print(f"Bot: {reply_1}\n")

# Assert that the bot asked for clarification instead of executing it
assert "1" in reply_1 and "2" in reply_1, "Bot failed to ask for clarification!"

# 3. Reply to the clarification
print("User: 2")
reply_2 = router.route(test_user, "2")
print(f"Bot: {reply_2}\n")

# Assert that the bot executed the task and showed the Visible Context Cue
assert "Couples Private" in reply_2 or "My Home" in reply_2, "Bot failed to show the Visible Context Cue!"
assert "Logged expense" in reply_2 or "task executed" in reply_2 or "✅" in reply_2, "Bot failed to execute the delayed task!"

print("🎉 PRIVACY BOUNCER TEST PASSED!")