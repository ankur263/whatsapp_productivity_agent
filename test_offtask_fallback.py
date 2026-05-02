import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ["ADMIN_MASTER_KEY"] = "admin2026"
os.environ["LLM_BACKEND"] = "mock"
os.environ["GROQ_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""

DB = Path("data/tasks.db")
if DB.exists():
    DB.unlink()

from router import AgentRouter, OFF_TASK_GUIDANCE_REPLY
from supervisor import SupervisorOutput


def run() -> None:
    router = AgentRouter()
    owner = "+15550000999"
    member = "+15550001000"

    # Bootstrap owner to skip onboarding.
    claim = router.route(owner, "/claim admin2026")
    assert "Admin access granted" in claim

    # 1) Smalltalk/random chat gets guidance instead of echo.
    r1 = router.route(owner, "hi")
    assert r1 == OFF_TASK_GUIDANCE_REPLY
    assert r1.strip().lower() != "hi"

    # 2) Abusive/off-task input also gets safe guidance.
    r2 = router.route(owner, "chutiye")
    assert r2 == OFF_TASK_GUIDANCE_REPLY
    assert r2.strip().lower() != "chutiye"

    # 3) Supervisor echo is blocked by anti-echo sanitizer.
    original_decide = router.supervisor.decide
    try:
        router.supervisor.decide = lambda *args, **kwargs: SupervisorOutput(kind="reply", text="i am bored")
        r3 = router.route(owner, "i am bored")
        assert r3 == OFF_TASK_GUIDANCE_REPLY
    finally:
        router.supervisor.decide = original_decide

    # 4) Grocery flow: add + list + mark bought + clear bought
    r4 = router.route(owner, "buy milk")
    assert "milk" in r4.lower()
    assert r4 != OFF_TASK_GUIDANCE_REPLY

    r5 = router.route(owner, "show my grocery list")
    assert "grocery items" in r5.lower() and "milk" in r5.lower()

    r6 = router.route(owner, "mark grocery 1 bought")
    assert "bought" in r6.lower() or "marked" in r6.lower()

    r7 = router.route(owner, "clear bought groceries")
    assert "cleared" in r7.lower()

    # 5) Task flow: create + list + complete
    r8 = router.route(owner, "create task call mom")
    assert "task" in r8.lower() and "call mom" in r8.lower()

    r9 = router.route(owner, "show my tasks")
    assert "call mom" in r9.lower()

    r10 = router.route(owner, "complete task 1")
    assert "done" in r10.lower() or "completed" in r10.lower()

    # 6) Reminder flow
    r11 = router.route(owner, "remind me in 5 min to drink water")
    assert "reminder" in r11.lower() and "set" in r11.lower()

    # 7) Settings validation + update messaging
    r12 = router.route(owner, "/settings timezone Asia/Nope")
    assert "valid timezone" in r12.lower() or "doesn't look like" in r12.lower()

    r13 = router.route(owner, "/settings timezone Asia/Singapore")
    assert "timezone updated" in r13.lower()

    # 8) Invite + member onboarding flow with message checks
    invite = router.route(owner, "/invite")
    m = re.search(r"Invite Code:\s*(\d{6})", invite)
    assert m, f"Expected invite code in reply. Got: {invite}"
    code = m.group(1)

    j1 = router.route(member, f"/join {code}")
    assert "success" in j1.lower() and "currency code" in j1.lower()

    j2 = router.route(member, "USD")
    assert "timezone" in j2.lower()

    j3 = router.route(member, "Asia/Singapore")
    assert "how many people" in j3.lower() or "household" in j3.lower()

    j4 = router.route(member, "3")
    assert "all set" in j4.lower() or "done" in j4.lower()

    # 9) Shared household visibility + switching behavior
    add_member = router.route(member, "buy eggs")
    assert "eggs" in add_member.lower()

    owner_list_shared = router.route(owner, "show my grocery list")
    assert "eggs" in owner_list_shared.lower()

    sw0 = router.route(owner, "/switch 0")
    assert "personal space" in sw0.lower()

    owner_list_personal = router.route(owner, "show my grocery list")
    assert "no grocery items" in owner_list_personal.lower()

    sw1 = router.route(owner, "/switch 1")
    assert "my home" in sw1.lower() or "switched" in sw1.lower()

    # 10) Invalid command path should stay user-friendly
    bad_leave = router.route(owner, "/leave 999")
    assert "invalid" in bad_leave.lower()

    # 11) Bare /join should still return a non-empty safe response (no crash)
    bare_join = router.route(owner, "/join")
    assert isinstance(bare_join, str) and len(bare_join.strip()) > 0

    print("✅ regression checks passed (off-task, edge cases, and real-user message flows)")


if __name__ == "__main__":
    run()
