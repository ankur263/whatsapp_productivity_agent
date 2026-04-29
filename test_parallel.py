import os
from dotenv import load_dotenv

load_dotenv()

from router import AgentRouter

router = AgentRouter()

# 1. Bypass onboarding for our dummy test user
router.db.upsert_user_setting("+1234567890", "is_allowed", "1")
router.db.upsert_user_setting("+1234567890", "onboarding_state", "complete")

# 2. Send a multi-intent request (Planner + Shopping)
print("Sending multi-part request to Supervisor...\n")
reply = router.route("+1234567890", "Remind me to water the plants in 10 minutes, and also add 2 cartons of milk to the grocery list.")

print("\n=== FINAL MERGED REPLY ===")
print(reply)