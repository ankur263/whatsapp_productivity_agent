import os
import re
from dotenv import load_dotenv

load_dotenv()
# Temporarily override the master key just for this test
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()

admin_phone = "+19999999999"
helper_phone = "+18888888888"

print("=== 1. ADMIN CLAIMS MASTER KEY ===")
print("Reply:", router.route(admin_phone, "/claim admin2026"))

print("\n=== 2. ADMIN CREATES A SECOND HOUSEHOLD ===")
print("Reply:", router.route(admin_phone, "/createhouse Beach House"))

print("\n=== 3. ADMIN CHECKS THEIR WORKSPACES ===")
print("Reply:\n" + router.route(admin_phone, "/switch"))

print("\n=== 4. ADMIN GENERATES AN INVITE CODE ===")
invite_reply = router.route(admin_phone, "/invite")
print("Reply:\n" + invite_reply)

print("\n=== 5. HELPER JOINS THE HOUSEHOLD ===")
# Extract the 6-digit code from the Admin's reply using regex
match = re.search(r"Invite Code: (\d{6})", invite_reply)
if match:
    code = match.group(1)
    print(f"Helper sending: /join {code}")
    print("Reply:", router.route(helper_phone, f"/join {code}"))
else:
    print("Failed to extract invite code!")