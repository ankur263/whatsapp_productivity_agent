# Gemini Multi-Agent WhatsApp System — Architecture Plan

**Status:** Design v2. Revised after architecture review.

This document outlines the architecture for evolving the existing WhatsApp productivity agent into a personal life assistant powered by a "Supervisor + Specialists" model on Gemini. v2 incorporates fixes for: WhatsApp's 24-hour messaging window, multi-currency handling, the supervisor↔specialist contract, async/threading choice, error-surfacing instead of mock fallback, and several already-built capabilities the previous plan double-counted.

---

## 1. Strategic Context: Market the Wedge, Deliver the Platform

Goal: a true personal assistant that becomes indispensable, not another generic chatbot.

- **Challenge.** "Personal AI" is crowded (Meta AI, Apple Intelligence, etc.) and built-in assistants are free. New entrants face a cold-start problem.
- **Strategy.** Lead with a sharp, focused wedge that gives instant value; quietly build a broad personalization engine underneath.
  - **Acquire** with a wedge (e.g. "the best WhatsApp assistant for personal finance in India" — assuming we go INR-first; see §3 *Locale*).
  - **Retain** by exposing other capabilities (planning, shopping, journaling) once users are hooked. The agent learns over time and becomes hard to switch away from.
- **Architectural alignment.** Specialists *are* the wedges; the Supervisor is the platform.

---

## 2. Target Architecture: Supervisor + Specialists

```text
WhatsApp -> whatsapp_server -> per-user FIFO worker pool
                                |
                                v
                      +-------------------+
                      |  Supervisor Agent |  (Gemini, structured output)
                      |  routes / plans   |
                      |  composes reply   |
                      +---------+---------+
           +----------+---------+----------+----------+----------+
           v          v         v          v          v          v
       Planner   Finance   Shopping   Events    Knowledge    Journal
        Agent    Agent     Agent      Agent     Agent        Agent
                                |
                                v
                Shared: SQLite + Chroma + WhatsApp client
                                |
                                v
                       Job worker (slow tasks)
```

**Why supervisor.** Smaller per-specialist prompts → better accuracy and latency. Each domain owns its quirks (currency, dates, units). Models can be swapped per specialist later (e.g. Pro for finance analysis, Flash for chit-chat). Trade-off: one extra LLM hop per turn — mitigated by (a) keeping the existing deterministic regex fast paths in front of the Supervisor and (b) letting the Supervisor itself answer trivial things (greetings, time).

**Statelessness.** Specialists hold no in-memory state across turns. They read context from SQLite/Chroma per call. This is what makes per-user FIFO and parallel fan-out safe.

---

## 3. Specialist Roster

| Agent | Owns | New tools / data |
|---|---|---|
| **Planner** | Tasks, ad-hoc reminders ("remind me X at Y") | Reuses `create_task`, `set_reminder`. Adds deterministic `parse_when(text, user_tz)` so the LLM never emits timestamps. |
| **Finance** | Expense logging, budgets, monthly summary, credit-card analysis | New `expenses`, `budgets`, `cards` tables (see §5). Tools: `log_expense`, `update_expense`, `delete_expense`, `monthly_summary`, `budget_check`, `ingest_statement(file)`. |
| **Shopping** | Groceries, home inventory, store prices | Moves the ~25 grocery/inventory tools behind this specialist. No new schema. |
| **Events** | Birthdays, anniversaries, recurring life events | New `events` table. Auto-creates reminders N days before; N is a per-event setting with a per-user default. |
| **Knowledge** | Web search, Wikipedia, calculator, "how do I…" | Reuses existing `web_search`, `wikipedia_summary`, `calculate`. |
| **Journal** | Free-form notes, daily summary, semantic recall | Reuses `notes` table and Chroma. Wraps `recall(query)` (already implemented in `memory.py`) and adds metadata-filtered recall. |

**Ownership precedence (for overlapping intents).**
- Dated life events (birthdays, anniversaries, recurring) → **Events**.
- Ad-hoc one-shots ("remind me to call mom") → **Planner**.
- Anything with a price/amount → **Finance**, even if it sounds like shopping ("paid 200 for milk" is an expense; "buy milk" is shopping).

**Locale.** v2 leaves the wedge open: INR for India-first vs. SGD for current defaults. This must be picked **before Phase 2** because (a) `store_prices.currency` defaults to `'SGD'` in the existing schema and (b) it sets the `default_currency` value for new users. Whichever we pick, write a one-time migration for existing rows.

---

## 4. The Supervisor↔Specialist Contract

The Supervisor returns one of three structured outputs (validated with pydantic, generated via Gemini `response_schema`):

```python
# Direct reply (greetings, trivial answers): supervisor handles inline.
{"kind": "reply", "text": "..."}

# Single specialist:
{"kind": "route", "agent": "finance", "subrequest": "log $42 dinner at Swensens yesterday"}

# Multi-step plan (independent steps run in parallel):
{"kind": "plan", "steps": [
    {"agent": "events",  "subrequest": "anniversary Priya, recurring yearly"},
    {"agent": "finance", "subrequest": "log $30 flowers category:gifts"}
]}
```

If parsing fails, retry once with the validation error appended to the prompt. If it fails again, fall back to the legacy flat agent (see §11 *Fallback rules*).

**Specialist input bundle.** Every specialist call receives:

```python
{
    "user_id": str,
    "subrequest": str,            # supervisor's rewrite OR original message
    "original_message": str,
    "user_settings": {            # always populated; defaults applied
        "default_currency": "INR",
        "timezone": "Asia/Kolkata",
        "locale": "en-IN",
        "quiet_hours": {"start": "22:00", "end": "08:00"},
    },
    "now_local_iso": "2026-04-25T14:32:00+05:30",
    "recent_turns": [...],        # last N from conversations table
}
```

**Specialist return types.**

```python
{"kind": "reply", "text": "..."}                                    # done in <5s
{"kind": "ack", "text": "Parsing your statement, ~30s",             # slow path
 "job": {"type": "ingest_statement", "payload": {...}}}
{"kind": "needs_clarification", "question": "USD or INR?",
 "context": {...}}                                                   # ask back
```

`needs_clarification` writes the pending question to `conversations`; the Supervisor uses it on the next inbound turn to re-route the answer to the same specialist.

**Composition.** For multi-step plans, the Supervisor receives all specialist replies and runs **one** template-based merge (no extra LLM call). If any step returns `needs_clarification`, the composition must merge the successes *with* the clarification (e.g., `"✅ Reminder set.\n\nRegarding the dinner, was that INR or USD?"`). To prevent robotic/wordy concatenation, Specialist system prompts strictly enforce replying in a single, terse sentence starting with a relevant emoji.

---

## 5. Data Model Additions

Already in the schema today (do **not** re-create): `tasks`, `reminders`, `notes`, `grocery_items`, `inventory_items`, `inventory_events`, `store_prices`, `user_settings(user_id, default_currency)`. The reminder scheduler thread is also already wired in [whatsapp_server.py:136-155](whatsapp_server.py#L136-L155) — extend it, don't rebuild it.

**New columns on `user_settings`:**

```sql
ALTER TABLE user_settings ADD COLUMN timezone TEXT;
ALTER TABLE user_settings ADD COLUMN locale TEXT;
ALTER TABLE user_settings ADD COLUMN quiet_hours_start TEXT;   -- 'HH:MM'
ALTER TABLE user_settings ADD COLUMN quiet_hours_end TEXT;
ALTER TABLE user_settings ADD COLUMN onboarding_state TEXT;    -- nullable; e.g. 'await_currency'
```

`upsert_user_setting` in `database.py` currently only accepts `default_currency` — extend its key allowlist accordingly.

**New tables:**

```sql
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,
  event_date TEXT NOT NULL,            -- 'YYYY-MM-DD'
  recurrence TEXT,                     -- 'yearly' | 'monthly' | NULL
  remind_lead_days INTEGER DEFAULT 1,
  notes TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_events_user_date ON events(user_id, event_date);

CREATE TABLE expenses (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,
  amount_minor INTEGER NOT NULL,       -- store as integer minor units (paise/cents)
  currency TEXT NOT NULL,              -- ISO 4217
  amount_home_minor INTEGER,           -- normalized to user's default_currency, nullable until FX known
  fx_rate REAL,                        -- rate used; NULL if same currency
  merchant TEXT,
  category TEXT NOT NULL,              -- normalized via CATEGORY_MAP (see below)
  method TEXT,                         -- 'cash' | 'upi' | 'card'
  card_id INTEGER REFERENCES cards(id),
  occurred_at TEXT NOT NULL,
  source TEXT NOT NULL,                -- 'manual' | 'statement'
  created_at TEXT NOT NULL
);
CREATE INDEX idx_expenses_user_date ON expenses(user_id, occurred_at);
CREATE INDEX idx_expenses_user_cat ON expenses(user_id, category);

CREATE TABLE budgets (
  user_id TEXT NOT NULL,
  category TEXT NOT NULL,              -- must match CATEGORY_MAP keys
  monthly_cap_minor INTEGER NOT NULL,
  currency TEXT NOT NULL,
  PRIMARY KEY (user_id, category)
);

CREATE TABLE cards (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,
  card_name TEXT NOT NULL,             -- 'Axis Magnus'
  last4 TEXT,
  network TEXT,                        -- 'visa' | 'mastercard' | 'amex' | 'rupay'
  created_at TEXT NOT NULL,
  UNIQUE(user_id, card_name)
);

CREATE TABLE conversations (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL,                  -- 'user' | 'assistant' | 'supervisor'
  content TEXT NOT NULL,
  agent TEXT,                          -- specialist name, or 'supervisor', or NULL for user
  created_at TEXT NOT NULL
);
CREATE INDEX idx_conversations_user_created ON conversations(user_id, created_at);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,
  type TEXT NOT NULL,                  -- registry key: 'ingest_statement' | ...
  status TEXT NOT NULL DEFAULT 'pending', -- pending | running | done | failed | dead
  payload_path TEXT,                   -- file path for blobs (PDF/CSV); JSON inline for small payloads
  payload_inline TEXT,                 -- small JSON, < 4 KB
  result TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  lease_until TEXT,                    -- worker leases the row for N seconds; expires on crash
  last_error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX idx_jobs_pickup ON jobs(status, lease_until, created_at);
CREATE INDEX idx_jobs_user ON jobs(user_id);

-- Existing tables requiring ALTER
-- ALTER TABLE reminders ADD COLUMN provider_message_id TEXT;
-- ALTER TABLE events ADD COLUMN provider_message_id TEXT;
```

**Money in minor units.** Storing INR as `amount_minor INTEGER` (paise) avoids float drift on summing.

**FX strategy.** At log time, if `currency != default_currency` and a daily FX rate is available, populate `amount_home_minor` and `fx_rate`. If not, leave NULL and let `monthly_summary` either skip or annotate "in mixed currencies." Daily rates can be cached in a tiny `fx_rates(date, base, quote, rate)` table, but defer that to Phase 2 hardening.

**Category normalization.** A static `CATEGORY_MAP` in code maps free-form text → one of a fixed set: `food`, `groceries`, `transport`, `utilities`, `rent`, `entertainment`, `shopping`, `gifts`, `health`, `travel`, `subscriptions`, `other`. Both `expenses.category` and `budgets.category` reference these keys. The Finance specialist's prompt lists them.

**Blob storage for jobs.** Bank statements live on disk under `data/jobs/{job_id}/`, not in SQLite. Worker deletes the file after success or after a 7-day TTL.

---

## 6. Routing & Execution Loop

1. **Onboarding state machine.** When a user first messages, the router checks `user_settings`. Missing fields drive a state machine:
   - No row → set `onboarding_state='await_currency'`, ask for default currency.
   - `await_currency` → parse reply (validate ISO 4217 or short list); if invalid, re-prompt; on success advance to `await_timezone`.
   - `await_timezone` → parse IANA TZ or offer a short list (e.g. *Asia/Kolkata*, *Asia/Singapore*, *America/New_York*); set, clear `onboarding_state`.
   - Onboarding bypasses the Supervisor entirely.
2. **Pre-Router Fast Path.** The router checks for regex shortcuts (e.g., "tasks"). If matched, it bypasses LLMs, executes the tool, replies instantly, AND explicitly writes both the user's prompt and the system's reply to the `conversations` table so the Supervisor isn't blind to it on the next turn.
3. **Supervisor routing.** Reads last N turns from `conversations`, builds a structured-output prompt, returns one of `reply` / `route` / `plan` (see §4).
4. **Dispatch.** Independent plan steps run in parallel via a `ThreadPoolExecutor` (we stay on threads — see §7). Sequential dependencies are not supported in v2; if encountered, the Supervisor returns a `route` and lets the next user turn drive step 2.
5. **Persistence.** Every turn (including LLM-bypassing fast-path commands) appends to `conversations`. Salient facts are written to Chroma with `agent` metadata so Journal recall can filter.
6. **Composition.** Template merge of specialist string outputs. Successes are combined with any `needs_clarification` prompts.

---

## 7. Concurrency Model

**Thread-based, not asyncio.** Flask is already threaded; mixing `asyncio.run()` per request adds overhead with no win at our scale. v2 stays on threads + `ThreadPoolExecutor` for fan-out. Reconsider only if we hit hundreds of concurrent users.

**Per-user FIFO with cross-user parallelism.** Replace today's single global incoming worker ([whatsapp_server.py:196-209](whatsapp_server.py#L196-L209)) with a small worker pool keyed by `user_id`:
- N=4 worker threads pull from `incoming_queue`.
- Per-user `threading.Lock` ensures one user's messages serialize even if two workers grab them simultaneously.
- Different users always run in parallel.

**Async jobs (the "ack" pattern).** Slow specialist work (statement parse, big web research, monthly analytics over 1k+ rows) returns `{"kind": "ack", ...}`. The router writes the ack to the user, enqueues the job, and a separate `jobs-worker` thread:
- Picks rows where `status='pending'` AND (`lease_until IS NULL` OR `lease_until < now`).
- Sets `status='running'`, `started_at=now`, `lease_until=now+5min`, `attempts=attempts+1`.
- On success: `status='done'`, push result via WhatsApp. **CRITICAL:** The worker must check the user's 24-hour `last_inbound_at` window. If >24h, the worker must use an approved template (e.g., `job_done_v1`: "Your requested task is complete: {{1}}") to prevent WhatsApp API errors.
- On exception: `status='pending'` if `attempts<max_attempts` (with backoff), else `status='dead'`.
- Crashed workers leave `lease_until` in the past → another worker picks up. Idempotency for `ingest_statement` comes from a `(user_id, statement_hash)` uniqueness check before insert.

**SQLite concurrency.** At init, run:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
```

Both are needed — WAL alone with `synchronous=FULL` leaves most of the perf win on the table. Keep the per-call `sqlite3.connect` for now; revisit if lock contention shows up in logs.

---

## 8. WhatsApp Platform Constraints (critical)

**The 24-hour customer-service window.** Meta only allows free-form business-initiated messages within 24 hours of the user's last inbound message. *Reminders and event notifications scheduled outside that window will fail* unless we use **approved message templates**.

Required work:
- Register templates with Meta Business using structured text to avoid approval rejections (e.g., `reminder_v1`: "Here is your requested reminder: {{1}}", `event_v1`: "Heads up — {{1}} is on {{2}}"). If a template send fails (e.g., `WhatsAppClientError`), log it as a dead-letter.
- Add `send_template_message(to, template_name, params)` to `whatsapp_client.py`.
- The reminder/event scheduler picks `send_text_message` if the user's last inbound is <24h ago, else `send_template_message`.
- Track `last_inbound_at` per user (cheap: column on `user_settings` or query `conversations`).

**Quiet hours.** Skip non-urgent pushes during `quiet_hours_start`/`quiet_hours_end`; defer to next allowed minute. Urgent (today's event-day) bypasses.

**Media downloads.** WhatsApp media URLs require an authenticated GET with the access token. Add `download_media(media_id) -> path` to `whatsapp_client.py`. Files land under `data/jobs/{job_id}/`.

---

## 9. Memory: Chroma vs. SQL `conversations`

Two stores, distinct roles, *no overlap*:

| Store | Role | Lifetime |
|---|---|---|
| `conversations` (SQLite) | Rolling transcript for Supervisor short-term context (last N=20 turns) | Pruned to last 200 rows per user via a daily job. Older rows summarized into Chroma. |
| Chroma | Long-term semantic recall (facts, preferences, summaries) | Per-(user, agent) collection. Specialist writes use `metadata={"agent": "finance"}`. JournalAgent recall can filter. |

`memory.py` already implements per-user collections; v2 adds the `agent` metadata filter and one collection-per-domain split when collections grow large.

---

## 10. Error Handling and Production Failure Modes

**Stop using `_chat_mock` as a production fallback.** Today, [agent.py:542-556](agent.py#L542-L556) silently falls back to `_chat_mock` on any Gemini failure — including emitting fake tool calls like `set_reminder("drink water|in 10m")` that *write to the DB*. With Supervisor + 6 specialists per turn, quota and transient errors get more likely. Required changes:
- LLM call wrapper: 3 retries with exponential backoff for 5xx/timeout; surface 4xx (auth, quota) as a user-visible error message.
- Mock backend stays available only via explicit `LLM_BACKEND=mock` for local dev.
- A failed specialist call returns `{"kind": "reply", "text": "Sorry, I'm having trouble with that — try again in a minute."}` to the user. Never write fake state.

**Reminder delivery semantics: at-least-once.** Current code does send-then-mark-sent; a crash between can re-send. v2 adds an intermediate `sent='sending'` state and uses WhatsApp's returned message ID as an idempotency key recorded on the reminder row. On retry, if a message ID already exists, skip the send and just mark sent.

---

## 11. Fallback Rules (when the legacy flat agent fires)

The flat agent stays as a safety net for one release. It fires when:
- Supervisor structured-output validation fails twice in a row, OR
- The selected specialist raises an unhandled exception.

It does **not** fire on `needs_clarification` (that's expected behavior). Every fallback is logged with `agent_route_failed` so we can track the rate and retire the fallback once it's <0.5% of turns.

---

## 12. Observability

A new `routing_log` table (or structured logs — pick one):

```sql
CREATE TABLE routing_log (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL,
  agent TEXT,                          -- 'supervisor' or specialist name
  route_decision TEXT,                 -- 'reply' | 'route' | 'plan' | 'fallback'
  fallback_used INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL,
  llm_calls INTEGER NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL
);
```

Without these metrics, "the supervisor has stabilized" is guesswork.

---

## 13. State Hygiene

- Retire the in-memory `Agent.history` deque ([agent.py:53-55](agent.py#L53-L55)). One source of truth: `conversations` table.
- `AgentRouter.agents` ([router.py:25-31](router.py#L25-L31)) currently leaks one `Agent` per user forever. Under the new model, the Supervisor and specialists are stateless instances created once at process start and shared across all users. No per-user agent cache.
- Keep the deterministic regex fast paths from [agent.py:180-444](agent.py#L180-L444) **in front** of the Supervisor — they bypass *both* LLM hops for the most common commands and are a major UX win we shouldn't surrender.

---

## 14. Pinned Models

One config block, used everywhere:

```python
GEMINI_SUPERVISOR_MODEL = "gemini-2.5-flash"        # routing/composition
GEMINI_SPECIALIST_MODEL = "gemini-2.5-flash"        # default
GEMINI_FINANCE_MODEL    = "gemini-2.5-pro"          # heavier analysis
```

Override via env. No `gemini-1.5-*` references left in code or prose.

---

## 15. Phased Implementation

**Phase 1 — Supervisor shell, onboarding, Planner, Events (3–4 days).**
- Extend `user_settings` (timezone, locale, quiet_hours, onboarding_state); extend `upsert_user_setting` allowlist.
- Onboarding state machine in router.
- Supervisor with structured-output JSON, pydantic validation, retry-once-with-error.
- Planner specialist + `parse_when` deterministic time tool.
- Events specialist + `events` table + recurrence expansion in the existing reminder scheduler thread.
- WhatsApp templates registered (`reminder_v1`, `event_v1`); `send_template_message` + 24-h window logic.
- WAL + synchronous=NORMAL.
- Per-user FIFO worker pool.
- LLM call wrapper with retry/backoff; remove silent mock fallback.
- Routing log.
- **Milestone:** "remind me to call mom tomorrow at 7pm" works end-to-end across the 24-hour boundary. Onboarding flow handles a brand-new user.

**Phase 2 — Finance specialist + job queue (3–4 days).**
- `expenses`, `budgets`, `cards` tables with category normalization.
- Money in minor units; FX hook (defer real FX rates).
- Tools: `log_expense`, `update_expense`, `delete_expense`, `monthly_summary`, `budget_check`.
- Jobs table, jobs worker with leases and idempotency, blob storage on disk.
- Decide locale (INR vs SGD) and run the migration.
- **Milestone:** "what did I spend on food this month?" returns a number; "delete the last expense" works.

**Phase 3 — Shopping migration (1 day).**
- Move ~25 grocery/inventory tools behind ShoppingAgent. Keep the regex fast paths.

**Phase 4 — Knowledge, Journal, parallelism (2 days).**
- Wire web/wiki/calc into KnowledgeAgent.
- JournalAgent with metadata-filtered Chroma recall.
- Daily prune-and-summarize job for `conversations` → Chroma.
- ThreadPoolExecutor fan-out for multi-step plans.
- **Milestone:** "what did I tell you about the Bali trip last week?" returns a relevant memory and *doesn't* surface finance/grocery facts.

**Phase 5 — Statement ingestion (2–3 days, optional).**
- Media download from WhatsApp.
- `ingest_statement` job: parse PDF/CSV → expenses with `source='statement'` and `(user_id, statement_hash)` uniqueness.
- 7-day blob TTL.

**Phase 6 — Retire flat agent.**
- Once routing_log shows fallback rate <0.5% over a week, delete the flat agent.

---

## 16. Resolved Open Decisions

1. **Currency / locale.** Onboarding asks for currency *and* timezone. Default for the wedge: TBD (INR if India-first; SGD to match current code). Pick before Phase 2.
2. **Fallback agent.** Yes, keep the legacy flat agent for one release; retire in Phase 6.
3. **Concurrency.** Threads + ThreadPoolExecutor, not asyncio.
4. **Reminder polling.** 30s loop is enough; existing 20s loop in `whatsapp_server.py` already meets this. APScheduler not needed.
5. **Composition.** Template merge, not LLM merge.
6. **Categories.** Fixed `CATEGORY_MAP` enum, not free-form.
7. **Money.** Integer minor units, not floats.
