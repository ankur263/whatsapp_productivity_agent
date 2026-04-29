import os
import re
from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()

admin = "+10000000001"
spouse = "+10000000002"
helper = "+10000000003"

def get_invite_code(reply: str) -> str:
    match = re.search(r"Invite Code: (\d{6})", reply)
    return match.group(1) if match else ""

print("🚀 Starting rigorous Household flow tests...\n")

# 1. Admin Bootstrap
reply = router.route(admin, "/claim admin2026")
assert "Admin access granted" in reply
print("✅ Admin claimed master key and created 'My Home'.")

# 2. Admin creates a second household
reply = router.route(admin, "/createhouse Vacation Home")
assert "Created new household" in reply
print("✅ Admin created 'Vacation Home'.")

# 3. Admin checks workspaces (Should see both, active in Vacation Home)
reply = router.route(admin, "/switch")
assert "My Home" in reply and "Vacation Home" in reply

# 4. Admin switches back to My Home
reply = router.route(admin, "/switch 1")
assert "Switched active household to *My Home*" in reply
print("✅ Admin switched workspace back to 'My Home'.")

# 5. Admin invites Spouse to My Home
invite_my_home = get_invite_code(router.route(admin, "/invite"))
assert invite_my_home
print(f"✅ Admin generated My Home invite: {invite_my_home}")

# 6. Spouse joins My Home
reply = router.route(spouse, f"/join {invite_my_home}")
assert "Success" in reply and "My Home" in reply
print("✅ Spouse successfully joined 'My Home'.")

# 7. Spouse adds groceries (Should scope to My Home)
# Using the fast-path regex to simulate real user text
router.route(spouse, "add 2 cartons of milk to groceries")
print("✅ Spouse added milk.")

# 8. Admin checks groceries (Should see Spouse's milk)
reply = router.route(admin, "show my groceries")
assert "milk" in reply.lower()
print("✅ Admin can see the milk Spouse added (Shared Scope Works!).")

# 9. Admin switches to Vacation Home and adds Sunscreen
router.route(admin, "/switch 2")
router.route(admin, "buy sunscreen")
print("✅ Admin switched to Vacation Home and added sunscreen.")

# 10. Admin checks groceries (Should see sunscreen, NO milk)
reply = router.route(admin, "show my groceries")
assert "sunscreen" in reply.lower()
assert "milk" not in reply.lower()
print("✅ Admin sees ONLY sunscreen in Vacation Home (Isolation Works!).")

# 11. Spouse checks groceries (Should see milk, NO sunscreen)
reply = router.route(spouse, "show my groceries")
assert "milk" in reply.lower()
assert "sunscreen" not in reply.lower()
print("✅ Spouse sees ONLY milk in My Home (Isolation Works!).")

# 12. Helper texts without an invite (Should hit Lobby)
reply = router.route(helper, "hi")
assert "Welcome to Home OS" in reply and "invite code" in reply
print("✅ Unauthenticated Helper safely blocked by Lobby Bouncer.")

# 13. Admin generates Vacation Home invite for Helper
invite_vacation = get_invite_code(router.route(admin, "/invite"))
reply = router.route(helper, f"/join {invite_vacation}")
assert "Success" in reply and "Vacation Home" in reply
print("✅ Helper successfully joined 'Vacation Home'.")

# 14. Helper leaves Vacation Home
reply = router.route(helper, "/leave 1")
assert "You have left" in reply
reply = router.route(helper, "show my groceries")
assert "No grocery items found" in reply
print("✅ Helper left household and reverted to personal scope.")

print("\n🎉 ALL TESTS PASSED! The architecture is production-ready.")