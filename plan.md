# WhatsApp Productivity Agent — Plan

**Status:** Ready for implementation. Plan finalized 2026-04-19. Adapted for this machine on 2026-04-19.

**Context for resumption:** This plan was originally designed on another machine (paths `/Users/ankur263/agents/...`). It builds directly on top of the existing Level 2 agent. The Level 2 code is already complete and working (CLI only). This plan adds a WhatsApp interface and multi-user support.

> **⚠️ Prerequisite check (this machine):** The Level 2 `productivity_agent/` folder does **not** currently exist on this machine. Before starting Phase 1, clone/sync it to `/Users/vanshikakhare/Desktop/productivity_agent/` (or adjust the paths below to wherever you put it). See Section 3.

---

## 1. Context

Today the only way to talk to the productivity agent at `productivity_agent/` is to open Terminal and run `python main.py`. That's fine for learning but nobody will actually use it day-to-day.

We want to wrap the **same agent brain** in a WhatsApp interface so Ankur, his friends, and his family can text the bot from their phones to:

- Add / list / complete personal tasks
- Search the web, do math, look things up on Wikipedia
- Read/write files in a personal sandbox
- Have multi-turn conversations the bot remembers (even across days)

The agent's brain (`agent.py`, `tools.py`, `memory.py`, `database.py`, `prompts.py`) already exists and does all of this. **We are not rewriting it.** We are adding:

1. A web server that receives WhatsApp messages from Meta's Cloud API
2. A small adapter that figures out *who* is texting and routes to a per-user agent
3. Schema + memory changes so multiple users can share the same bot without seeing each other's data
4. A whitelist so only approved phone numbers can use the bot

---

## 2. Decisions (locked from the user)

| Decision | Choice | Why |
|---|---|---|
| WhatsApp transport | Meta WhatsApp Cloud API (official, free tier, webhook-based) | Production-grade, transferable skill, free for low volume |
| Multi-tenant? | Yes — multi-user, with per-user task lists and per-user memory | Ankur wants to share with friends and family |
| Front-end scope for v1 | Text in / text out only. No voice notes, no images, no scheduled reminders | Keep MVP small; add features later as `LEARN.md` exercises |
| Public URL for webhook | `ngrok` during development; documented but not provisioned in v1 | Simplest local dev story |
| Code reuse strategy | New folder `whatsapp_productivity_agent/` that imports and reuses code from `productivity_agent/`. The existing Level 2 code stays untouched as a learning artifact. | Preserves the learning roadmap; no risk of breaking what works |

---

## 3. Resuming on Another Machine

If you're picking this up on a different laptop (this machine: `/Users/vanshikakhare/Desktop/`), do these steps in order:

1. **Clone / sync the repo** so `productivity_agent/` exists. On this machine, put it at `/Users/vanshikakhare/Desktop/productivity_agent/`. It should already contain all 6 files: `agent.py`, `tools.py`, `prompts.py`, `memory.py`, `database.py`, `main.py`.

2. **Verify the Level 2 agent works** before touching anything new:
   - `cd /Users/vanshikakhare/Desktop/productivity_agent && source venv/bin/activate && python main.py`
   - Type "Create a task: test", then "Show my tasks". You should see the task. If this fails, fix Level 2 first — **don't proceed.**

3. **Read these two files** to refresh context:
   - `/Users/vanshikakhare/Desktop/ROADMAP.md` (the 5-level overview)
   - `/Users/vanshikakhare/Desktop/productivity_agent/LEARN.md` (deep dive on Level 2)

4. **Read this plan** — section 4 onwards is the actual work.

5. **Start at Phase 1** below. Each phase is self-contained and ends with a verifiable test.

---

## 4. Architecture

```
Phone (you           WhatsApp message      Meta WhatsApp Cloud API
 or family)    ─────────────────────────►
              ◄─────────────────────────
                    reply text                        │
                                                      │ HTTP POST
                                                      │ to webhook URL
                                                      ▼
                                          ┌──────────────────────────┐
                                          │ FastAPI server           │
                                          │ (webhook.py)             │
                                          │ + ngrok tunnel           │
                                          └──────────────┬───────────┘
                                                         │
                                                         ▼
                                          ┌──────────────────────────┐
                                          │ AgentRouter (router.py)  │
                                          │  - whitelist check       │
                                          │  - get/create per-user   │
                                          │    Agent                 │
                                          │  - call agent.run()      │
                                          └──────────────┬───────────┘
                                                         │
                                                         ▼
                                          ┌──────────────────────────┐
                                          │ WhatsAppAgent            │
                                          │ (multi_user_agent.py)    │
                                          │ ReAct loop reused        │
                                          │ from Level 2             │
                                          └──────┬───────┬───────────┘
                                                 │       │           │
                      ┌──────────────────────────┘       │           └──────────────────┐
                      ▼                                  ▼                              ▼
          ┌──────────────────────┐         ┌────────────────────────┐      ┌──────────────────────────┐
          │ multi_user_tools     │         │ multi_user_memory.py   │      │ multi_user_database.py   │
          │ (per-user workspace) │         │ (one ChromaDB          │      │ (tasks WHERE             │
          │                      │         │  collection per user)  │      │  user_phone = ?)         │
          └──────────────────────┘         └────────────────────────┘      └──────────────────────────┘
```

---

## 5. File Structure (new project)

```
/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/
├── README.md               ← Operations guide: how to set up Meta API, ngrok, run the server
├── LEARN.md                ← Beginner education guide (mirrors productivity_agent/LEARN.md style)
├── requirements.txt        ← fastapi, uvicorn, python-dotenv, httpx + same deps as productivity_agent
├── .env.example            ← Template: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, VERIFY_TOKEN, ALLOWED_USERS
├── .env                    ← (git-ignored) actual secrets
├── .gitignore              ← venv/, .env, data/, workspaces/, __pycache__/
├── webhook.py              ← FastAPI app: GET /webhook (Meta verification), POST /webhook (incoming msgs)
├── whatsapp_client.py      ← Thin wrapper around Meta Graph API: send_text_message(to, body)
├── router.py               ← AgentRouter: whitelist + per-user Agent registry + dispatch
├── multi_user_agent.py     ← WhatsAppAgent class — reuses Level 2 ReAct loop, scopes everything by user_phone
├── multi_user_memory.py    ← Wraps MemoryStore so each user gets their own ChromaDB collection
├── multi_user_database.py  ← Wraps TaskDatabase so all queries include WHERE user_phone = ?
├── data/                   ← (auto-created at runtime, git-ignored)
│   ├── tasks.db            ← SQLite — NEW schema with user_phone column
│   └── memory_db/          ← ChromaDB — one collection per user
└── workspaces/             ← (auto-created, git-ignored) one sub-folder per user phone
```

The existing `productivity_agent/` folder is **not modified.**

---

## 6. Implementation Phases

Work top to bottom. Each phase has a clear "done when" test.

### Phase 1 — Meta Cloud API setup (no code yet, ~30 min)

Done outside the codebase, documented in `README.md`:

1. Sign in at https://developers.facebook.com → "My Apps" → "Create App"
2. Choose **Business** as the app type, give it any name
3. From the App Dashboard, click "Add Product" → choose WhatsApp → "Set up"
4. On the WhatsApp → API Setup page, copy these into a notes file:
   - Temporary access token (only valid 24h — fine for now)
   - Phone Number ID (Meta gives you a free test number)
   - WhatsApp Business Account ID
5. Under "To" recipients, add your personal WhatsApp number and verify the OTP
6. Install ngrok: `brew install ngrok` then `ngrok config add-authtoken <token>` (sign up at ngrok.com to get the token)
7. Pick a `VERIFY_TOKEN` — any random string, e.g. `my-secret-verify-2026`

**Done when:** You can send a templated "hello_world" message from Meta's API Setup page to your phone and it arrives.

---

### Phase 2 — Webhook scaffolding (~2 hours)

Build `webhook.py` and `whatsapp_client.py` so that when you send "hello" from your phone, the bot echoes back "echo: hello". No real agent yet.

- `webhook.py` — FastAPI app with two routes:
  - `GET /webhook` — Meta's one-time verification handshake. Echoes `hub.challenge` if `hub.verify_token == VERIFY_TOKEN`.
  - `POST /webhook` — receives every inbound message. Parse the nested JSON (`entry[0].changes[0].value.messages[0]`), extract `from` (phone), `text.body`, and `id`. Return 200 OK fast, then process.
- `whatsapp_client.py` — exposes `send_text_message(to: str, body: str)`. One `httpx.post` to `https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages` with the bearer token from `.env`.

Run with `uvicorn webhook:app --reload --port 8000`. In a second terminal: `ngrok http 8000`. Paste the public ngrok URL into Meta's webhook config (Webhooks → Configure → Callback URL = `<ngrok>/webhook`, Verify token = your `VERIFY_TOKEN`).

**Done when:** Texting "hello" to the test number triggers the server logs to print the message and the bot replies "echo: hello".

---

### Phase 3 — Multi-user database (~1 hour)

Build `multi_user_database.py`, modeled directly on `productivity_agent/database.py`.

**Changes from the original:**

- New table schema: `id, user_phone TEXT NOT NULL, title, status, created_at`
- Every method takes `user_phone` as the first arg and adds `WHERE user_phone = ?` to all queries
- Methods: `create_task(user_phone, title)`, `list_tasks(user_phone, status_filter)`, `complete_task(user_phone, task_id)`, `delete_task(user_phone, task_id)`
- Reuse the connection pattern (`_get_connection`, `with` context manager) from the existing `database.py`

**Done when:** From a Python REPL:

```python
from multi_user_database import MultiUserTaskDatabase
db = MultiUserTaskDatabase()
db.create_task("+6591111111", "buy milk")
db.create_task("+6592222222", "feed dog")
print(db.list_tasks("+6591111111"))  # only "buy milk"
print(db.list_tasks("+6592222222"))  # only "feed dog"
```

---

### Phase 4 — Multi-user memory (~1 hour)

Build `multi_user_memory.py`, modeled directly on `productivity_agent/memory.py`.

**Changes from the original:**

- One ChromaDB collection per user: collection name = `f"user_{sanitized_phone}"` (strip `+` and non-digits — collection names have character restrictions)
- A factory `get_memory_for(user_phone) -> MemoryStore` that returns a memory object scoped to that user
- All other behavior (`store_memory`, `search_memory`, `get_memory_count`) is identical to the original

**Done when:** Storing a memory under user A and searching under user B returns nothing; searching under user A returns the stored memory.

---

### Phase 5 — Multi-user agent wrapper (~3 hours)

Build `multi_user_agent.py`. **This is the meatiest phase.**

- Defines `WhatsAppAgent` that **composes** rather than inherits — it holds:
  - `user_phone` (string, e.g. `"+6591234567"`)
  - `memory` = per-user `MemoryStore` (from `multi_user_memory.get_memory_for(user_phone)`)
  - `task_db` = the shared `MultiUserTaskDatabase` (queries get scoped by passing `user_phone` per call)
  - `conversation_history` (per-instance list, exactly like the original)
- The ReAct loop logic is copied minimally from `productivity_agent/agent.py` with two changes:
  - Tool calls that touch tasks pass `user_phone` through (e.g. `create_task(title)` becomes a closure that calls `task_db.create_task(self.user_phone, title)`)
  - File tool sandbox path is `workspaces/{sanitized_phone}/` instead of one shared `workspace/`
- **Reuses as-is** from the existing code:
  - `build_system_prompt` from `productivity_agent/prompts.py`
  - `TOOL_DESCRIPTIONS` from `productivity_agent/tools.py`
  - `web_search`, `calculate`, `wikipedia_summary`, `get_current_time` from `productivity_agent/tools.py` (these are global, no user scoping needed)
- Tool functions that need user scoping (`create_task`, `list_tasks`, `complete_task`, `read_file`, `write_file`) are re-bound to per-user closures in this file's local `TOOLS` dict
- LLM call (`ollama.chat`) and regex parsing (`_extract_action`, `_extract_final_answer`) are copied verbatim

**Done when:** From a REPL, two `WhatsAppAgent` instances with different phones each handle "add task X" and "list my tasks" and each only sees its own.

---

### Phase 6 — Router (~30 min)

Build `router.py`.

- Loads `ALLOWED_USERS` (comma-separated phone numbers, e.g. `+6591234567,+6598765432`) from `.env`
- Holds `agents: dict[str, WhatsAppAgent] = {}` — one Agent per phone, created on first message, kept in memory
- `route(from_phone: str, text: str) -> str`:
  1. If `from_phone` not in `ALLOWED_USERS`, return a polite "Sorry, this bot is private."
  2. If no agent exists for this phone in `self.agents`, create one (which auto-creates their ChromaDB collection and workspace folder)
  3. Call `agent.run(text)` and return the answer

**Done when:** Calling `router.route("+6500000000", "hi")` with that number NOT in the allowlist returns the rejection message; calling with an allowed number returns a real agent answer.

---

### Phase 7 — Wire it all together in `webhook.py` (~1 hour)

Update the `POST /webhook` handler from Phase 2:

1. Parse the Meta payload (nested JSON: `entry[0].changes[0].value.messages[0]`)
2. Skip non-text messages with a friendly "I only understand text right now" reply
3. Idempotency: Meta retries webhook deliveries. Skip messages whose `id` we've already processed (in-memory `set` for v1, SQLite later)
4. Call `router.route(from_phone, text)`
5. Call `whatsapp_client.send_text_message(from_phone, answer)`

**Done when:** All the end-to-end tests in section 8 below pass.

---

### Phase 8 — Beginner-friendly docs (~2 hours)

- `README.md` — the **operations guide**: env vars, ngrok setup, how to run the server, how to add an allowlisted user
- `LEARN.md` — the **education guide** in the same teaching style as `productivity_agent/LEARN.md`. Covers:
  - What a webhook is (Meta calls *us*, not the other way around)
  - What a Phone Number ID vs Business Account ID is
  - Why we need a verify token (security: makes sure the request is really from Meta)
  - Why memory has to be per-user (privacy)
  - How `ngrok` punches a hole through your home router so Meta's servers can reach your laptop
  - The data isolation pattern (every query has `WHERE user_phone = ?`) and why it matters

---

## 7. Critical Files (paths)

**New files to be created** (on this machine):

- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/webhook.py`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/whatsapp_client.py`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/router.py`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/multi_user_agent.py`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/multi_user_memory.py`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/multi_user_database.py`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/requirements.txt`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/.env.example`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/.gitignore`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/README.md`
- `/Users/vanshikakhare/Desktop/whatsapp_productivity_agent/LEARN.md`

**Existing files reused (NOT modified)** — assumes Level 2 synced to `/Users/vanshikakhare/Desktop/productivity_agent/`:

- `productivity_agent/prompts.py` — `build_system_prompt()` reused as-is
- `productivity_agent/tools.py` — `TOOL_DESCRIPTIONS` reused as-is; `web_search`, `calculate`, `wikipedia_summary`, `get_current_time` reused as-is; task & file tool functions are re-implemented as per-user closures in `multi_user_agent.py`
- `productivity_agent/agent.py` — ReAct loop pattern is the reference for `WhatsAppAgent`
- `productivity_agent/database.py` — schema and connection pattern is the reference for `multi_user_database.py`
- `productivity_agent/memory.py` — Chroma usage pattern is the reference for `multi_user_memory.py`

---

## 8. Verification (end-to-end)

Each phase has its own test (above). After Phase 7, run this full smoke test:

1. **Send first message:** Text "Add a task: pick up dry cleaning" from your phone → reply confirms task #1
2. **List:** Text "show my tasks" → reply lists "pick up dry cleaning"
3. **Multi-turn memory:** Text "I finished it" → agent should figure out from memory that "it" = the dry cleaning task and call `complete_task`
4. **Web search:** Text "what's the weather in Singapore?" → reply contains real weather data
5. **Multi-user isolation:** Have a friend (whitelisted) text "show my tasks" → empty list (proves isolation)
6. **Persistence:** Quit the server (Ctrl+C), restart it, text "what tasks do I have?" → still shows the dry cleaning task
7. **Whitelist:** Text from a non-allowed number → friendly rejection message
8. **Long-term memory:** Wait a day (or restart and clear `conversation_history`), text "what did I ask you to remember yesterday?" → bot recalls the dry cleaning task via ChromaDB
9. **Manual sanity:** Open `data/tasks.db` with `sqlite3 data/tasks.db "SELECT * FROM tasks;"` and confirm rows have correct `user_phone` values

---

## 9. Out of scope for v1 (future LEARN.md exercises)

- Voice note transcription (Whisper)
- Image / document handling
- Proactive reminders (APScheduler + a `due_at` column on tasks)
- Replacing the temporary 24-hour Meta token with a permanent system-user token
- Persisting `processed_message_ids` to SQLite (so dedup survives restarts)
- Streaming long agent answers as multiple WhatsApp messages
- Hosting on a VPS / Railway / Fly.io instead of laptop+ngrok
- Streamlit admin dashboard to view all users, tasks, memories
- Rate limiting per user (no more than N messages per minute)

---

## 10. Estimated Time

| Phase | Time | Cumulative |
|---|---|---|
| 1 — Meta API setup | 30 min | 0:30 |
| 2 — Webhook scaffolding | 2 hours | 2:30 |
| 3 — Multi-user DB | 1 hour | 3:30 |
| 4 — Multi-user memory | 1 hour | 4:30 |
| 5 — Agent wrapper | 3 hours | 7:30 |
| 6 — Router | 30 min | 8:00 |
| 7 — Wire-up | 1 hour | 9:00 |
| 8 — Docs | 2 hours | 11:00 |

**Total:** ~11 hours of focused work = a long weekend or two evenings + a Saturday.
