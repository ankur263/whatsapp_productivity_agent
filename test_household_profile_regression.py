import json
import tempfile
from pathlib import Path

from database import TaskDatabase
from router import AgentRouter


def check(label: str, cond, detail: str = ""):
    if not cond:
        raise AssertionError(f"{label}: {detail}")
    print(f"✅ {label}")


def fresh_router() -> AgentRouter:
    tmpdir = tempfile.TemporaryDirectory()
    router = AgentRouter()
    router._household_profile_tmpdir = tmpdir
    router.db = TaskDatabase(Path(tmpdir.name) / "tasks.db")
    return router


def settings_for(router: AgentRouter, phone: str) -> dict:
    user_id = "+" + "".join(ch for ch in phone if ch.isdigit())
    return router.db.get_user_settings(user_id) or {}


def profile_for(router: AgentRouter, phone: str) -> dict:
    raw = settings_for(router, phone).get("household_profile")
    return json.loads(raw) if raw else {}


router = fresh_router()
phone = "+15551230001"

reply = router.route(phone, "YES")
check("onboarding starts", "profile set up" in reply.lower(), reply)

router.route(phone, "SGD")
reply = router.route(phone, "Asia/Singapore")
check("asks for richer household profile", "3 adults" in reply and "1 baby" in reply, reply)

reply = router.route(phone, "3")
settings = settings_for(router, phone)
check("bare number keeps onboarding moving", settings.get("onboarding_state") == "await_first_action", settings)
check("bare number stores compatible family size", settings.get("family_size") == "3", settings)
check("bare number stores unspecified people", profile_for(router, phone).get("people") == 3, profile_for(router, phone))

reply = router.route(phone, "and 1 baby also")
settings = settings_for(router, phone)
profile = profile_for(router, phone)
check("baby follow-up is treated as profile correction", "3 adults + 1 baby" in reply, reply)
check("baby follow-up does not open workspace type menu", settings.get("onboarding_state") == "await_first_action", settings)
check("baby follow-up updates total", settings.get("family_size") == "4", settings)
check("baby follow-up converts previous count to adults", profile == {"adults": 3, "children": 0, "babies": 1, "people": 0}, profile)

reply = router.route(phone, "actually 2 adults and 1 kid")
profile = profile_for(router, phone)
check("actually replaces household profile", "2 adults + 1 child" in reply, reply)
check("replace profile persisted", profile == {"adults": 2, "children": 1, "babies": 0, "people": 0}, profile)

reply = router.route(phone, "make it 4 people")
profile = profile_for(router, phone)
check("make it replaces with total people", "4 people" in reply, reply)
check("total people persisted", profile == {"adults": 0, "children": 0, "babies": 0, "people": 4}, profile)

reply = router.route(phone, "we are 3 adults")
profile = profile_for(router, phone)
check("we are replaces with adults", "3 adults" in reply, reply)
check("adults persisted", profile == {"adults": 3, "children": 0, "babies": 0, "people": 0}, profile)

reply = router.route(phone, "add one child")
profile = profile_for(router, phone)
check("add child increments profile", "3 adults + 1 child" in reply, reply)
check("add child persisted", profile == {"adults": 3, "children": 1, "babies": 0, "people": 0}, profile)

parsed = router._parse_household_profile_text("my wife and baby", default_operation="replace")
check("natural family phrase infers self, spouse, and baby", parsed.get("profile") == {"adults": 2, "children": 0, "babies": 1, "people": 0}, parsed)

reply = router.route(phone, "/settings")
check("/settings shows household profile", "Household" in reply and "3 adults + 1 child" in reply, reply)
check("/settings documents household edit command", "/settings household 3 adults 1 baby" in reply, reply)

reply = router.route(phone, "/settings household 2 adults 1 baby")
profile = profile_for(router, phone)
check("/settings household replaces profile", "2 adults + 1 baby" in reply, reply)
check("/settings household persisted", profile == {"adults": 2, "children": 0, "babies": 1, "people": 0}, profile)

before = profile_for(router, phone)
parsed = router._parse_household_profile_text("buy baby food", current_profile=before, default_operation="add")
check("negative: baby food is not household profile", not parsed.get("ok"), parsed)

parsed = router._parse_household_profile_text("add 1", current_profile=before, default_operation="add")
check("negative: add 1 needs a person type", not parsed.get("ok"), parsed)

parsed = router._parse_household_profile_text("hello thanks", current_profile=before, default_operation="add")
check("negative: chatter is ignored", not parsed.get("ok"), parsed)

parsed = router._parse_household_profile_text("create task call mom", current_profile=before, default_operation="add")
check("negative: family words in normal tasks are ignored", not parsed.get("ok"), parsed)

print("\nHousehold profile regression tests passed.")
