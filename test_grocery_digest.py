import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"

from router import AgentRouter

router = AgentRouter()
db = router.db
test_user = "+19998887777"

print("📊 TESTING PROACTIVE GROCERY DIGEST...\n")

# 1. Setup Admin & House
router.route(test_user, "/claim admin2026")
hh_id = db.get_active_household(test_user)
print("✅ Setup Complete: User is in a Household.")

# 2. Simulate buying "Time-Travel Milk" 3 times (Every 4 days)
now = datetime.now(timezone.utc)
dates = [
    now - timedelta(days=12),
    now - timedelta(days=8),
    now - timedelta(days=4)
]

for dt in dates:
    # 1. Add to pending
    res = db.upsert_pending_grocery_item(test_user, "Time-Travel Milk", 1, "carton", category="dairy")
    # 2. Mark bought
    db.mark_grocery_bought(test_user, res["id"])
    # 3. Backdate the database timestamps manually to simulate time passing
    with db._conn() as c:
        c.execute("UPDATE grocery_items SET updated_at = ?, created_at = ? WHERE id = ?", 
                  (dt.isoformat(), dt.isoformat(), res["id"]))

print("✅ Simulated buying 'Time-Travel Milk' every 4 days over the last two weeks.")

# 3. Check AI Consumption Velocity Predictions
suggestions = db.suggest_rebuy_candidates(test_user, limit=5)
milk_sug = next((s for s in suggestions if "Time-Travel" in s["item_name"]), None)

if milk_sug:
    print(f"\n🧠 AI VELOCITY PREDICTION:")
    print(f"Item: {milk_sug['item_name']}")
    print(f"Velocity: Usually bought every {milk_sug['velocity_days']} days")
    print(f"Days Since Last: {milk_sug['days_since_last']} days ago")
    print(f"Is Due? {'YES ⚠️' if milk_sug['is_due'] else 'NO'}")
    assert milk_sug["is_due"] == True, "Math failed! It should be flagged as due."
else:
    print("❌ Failed to find simulated item in suggestions.")

# 4. Ask the agent directly to see the natural language output!
print("\n💬 ASKING THE SHOPPING AGENT:")
reply = router.route(test_user, "What groceries are we running low on based on our patterns?")
print(f"Bot:\n{reply}\n")

# 5. WhatsApp Background Cron check
events = db.get_recent_bought_events(hh_id, days=60)
assert len(events) >= 3, "Background thread won't have enough data!"
print("✅ Background Cron Thread has enough data to build the Shopping Histogram!")

print("\n🎉 DIGEST MATH TESTS PASSED!")