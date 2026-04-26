# Multi-Agent WhatsApp Productivity System — Plan

Generic day-to-day life assistant over WhatsApp: reminders, anniversaries, shopping, expenses, credit-card analysis, monthly budget, notes, recall, and ad-hoc questions. Built as an evolution of the existing repo (Gemini agent loop, ~50 tools in `tools.py`, SQLite at `database.py`, Chroma memory, per-user router) — not a rewrite.

---

## 1. Target architecture: supervisor + specialists

```
WhatsApp -> whatsapp_server -> Router (per user)
                                |
                                v
                      +-------------------+
                      |  Supervisor Agent |  (Gemini, function-calling)
                      |  classifies intent|
                      |  + delegates      |
                      +---------+---------+
           +----------+---------+----------+----------+----------+
           v          v         v          v          v          v
       Planner   Finance   Shopping   Events    Knowledge    Journal
        Agent    Agent     Agent      Agent     Agent        Agent
        (tasks,  (expenses,(groceries,(birthdays,(web, wiki, (notes,
        reminders)budgets, inventory) anniversaries,calc)    daily
                 CC stmts)            recurring)            summary,
                                                            recall)
                                |
                                v
                    Shared: SQLite + Chroma + WhatsApp send
```

One supervisor, many specialists. Each specialist is a Gemini call with a narrow prompt and a narrow tool set. The supervisor never touches domain tools — it just picks an agent, optionally splits a multi-part request, and composes the final reply.

### Why supervisor (not a flat tool-calling agent)

- Prompts stay small -> better accuracy and latency on Gemini 2.5 Flash.
- Each specialist owns its domain quirks (finance parses currency, events parse dates, shopping knows units/categories).
- Models can be swapped per specialist later (e.g., Pro for finance analysis, Flash for chit-chat).
- Tradeoff: one extra LLM hop per turn. Mitigate by letting the supervisor answer trivial things itself (greetings, time) and by caching the route for follow-ups in the same short window.

---

## 2. Specialist roster

| Agent | Owns | New tools / data |
|---|---|---|
| **Planner** | Tasks, reminders ("remind me X at Y") | Reuses `create_task`, `set_reminder`. Add scheduler thread that actually sends WhatsApp at `remind_at`. |
| **Finance** | Expense log, monthly budget, credit-card statement analysis | New `expenses` + `budgets` tables. Tools: `log_expense`, `monthly_summary`, `budget_check`, `ingest_statement(file)` (PDF/CSV parser). |
| **Shopping** | Groceries, inventory, store prices | Mostly already built in `tools.py` — move the ~25 grocery tools behind this agent. |
| **Events** | Birthdays, anniversaries, recurring life events | New `events` table (title, date, recurrence). Auto-creates reminders N days before. |
| **Knowledge** | Web search, Wikipedia, calculator, "how do I..." | Already have `web_search`, `wikipedia_summary`, `calculate`. |
| **Journal** | Free-form notes, daily summary, "what did I do last week", semantic recall | Reuses Chroma + `notes` table; adds `recall(query)` over Chroma. |

---

## 3. Data model additions (`database.py`)

```sql
events(id, user_id, title, event_date, recurrence, notes, created_at)
expenses(id, user_id, amount, currency, merchant, category, method, occurred_at, source, created_at)
budgets(user_id, category, monthly_cap, currency)
conversations(id, user_id, role, content, agent, created_at)   -- for recall
jobs(id, user_id, type, status, payload, result, created_at, finished_at)
```

Keep existing `tasks`, `reminders`, `notes`, `grocery_items`, `inventory`.

---

## 4. Routing mechanism

Supervisor prompt returns strict JSON:

```json
{"agent": "finance", "subrequest": "log $42 dinner at Swensens yesterday"}
```

Multi-part ("remind me about Priya's anniversary and log $30 flowers"):

```json
{"plan": [
  {"agent": "events",  "subrequest": "anniversary Priya, recurring yearly"},
  {"agent": "finance", "subrequest": "log $30 flowers category:gifts"}
]}
```

Validate with a small pydantic model; if parsing fails, fall back to the current flat agent so we never regress UX.

---

## 5. Execution loop

1. `whatsapp_server` -> `router.route(user_id, text)` (unchanged).
2. Router calls `Supervisor.handle(user_id, text)`.
3. Supervisor reads last N conversation turns from `conversations` table for context.
4. Supervisor returns plan -> router dispatches each step to the named specialist (parallel where independent, sequential when step N's output feeds step N+1).
5. Final composed reply goes back to WhatsApp; all turns appended to `conversations` + relevant facts written to Chroma.

### Reminder delivery (currently missing)

`set_reminder` writes to DB but nothing sends. Add a single background scheduler thread in `whatsapp_server.py` that polls `reminders` + `events` every 30s and pushes due items via `whatsapp_client.send_text`. Required for "remind me" and anniversary to work at all.

---

## 6. Concurrency model (non-blocking by design)

### What's already non-blocking

- **Flask webhook is threaded.** Each inbound message lands on its own thread. Message A (grocery) and message B ("remind me X") run in parallel.
- **No session lock on agents.** The supervisor re-routes every message fresh — no "user is in grocery mode" state. Mid-grocery you can switch topics and the next message goes straight to Planner / Finance / whatever.
- **Specialists are stateless instances.** They read context from DB / Chroma / `conversations`, not in-memory between turns. Two messages can hit the same specialist class simultaneously without stepping on each other.

So "I'm adding groceries, now remind me to call mom, now back to groceries" works without blocking. Each message is ~2–5s of Gemini latency on its own thread.

### What needs to be added

**(a) Job queue + ack pattern** for long-running specialist work (statement parse, big web research, monthly analysis over 1000 rows).

```
Specialist.handle(req) -> returns either:
  (1) {"reply": "..."}                    # fast path, <5s, inline
  (2) {"ack": "working on it...",         # slow path
       "job": {type, payload}}
```

Slow path enqueues to a background worker thread (or `asyncio` task), replies with the ack immediately, then pushes the real answer later via `whatsapp_client.send_text(user_id, result)`. New `jobs` table tracks state.

**(b) Parallel sub-tasks within one multi-part request.** Independent steps run with `asyncio.gather` (or thread pool) and compose one reply. Sequential only when step N's output feeds step N+1.

**(c) Per-user write safety on SQLite.** Flask threads + SQLite = need WAL mode and one connection per thread (or a pool). Set `PRAGMA journal_mode=WAL;` at DB init. Small change, prevents lock errors.

**(d) Back-pressure for a chatty user.** If someone fires 10 messages in 3 seconds, don't race 10 Gemini calls. Per-user `asyncio.Lock` or tiny FIFO queue so messages from one user process in arrival order, but different users stay parallel. Prevents "reply arrives before the question it's answering" bugs.

### Updated mental model

```
msg from user A ->|
msg from user A ->|-> per-user FIFO queue -> Supervisor -> Specialist(s) in parallel
msg from user A ->|                                       \
                                              slow ones -> jobs table -> worker -> push reply later
msg from user B ->|-> per-user FIFO queue -> Supervisor -> ...
```

Per-user ordering, cross-user parallelism, fast replies never wait for slow ones, multi-part requests fan out.

---

## 7. Phased build

**Phase 1 — Supervisor shell + Planner + Events (1–2 days).**
Drop in supervisor class, wire up the two simplest specialists, add `events` table, add reminder scheduler thread. Turn on SQLite WAL + per-user lock from day one. Flat agent stays as fallback. Milestone: "remind me to call mom at 7pm" and "Priya's anniversary on June 3" both work end-to-end.

**Phase 2 — Finance (2–3 days).**
`expenses` + `budgets` tables, log/query tools, monthly summary prompt. Introduce `jobs` table + worker (statement parsing is the first real slow path). Defer PDF parsing; start with manual "log $42 dinner" messages. Milestone: "what did I spend on food this month" returns a real number.

**Phase 3 — Shopping migration (0.5 day).**
Move existing grocery/inventory tools behind ShoppingAgent. Pure refactor, no new features.

**Phase 4 — Knowledge + Journal (1 day).**
Wire existing web/wiki/calc tools into KnowledgeAgent. Add `recall()` over Chroma for JournalAgent. Add parallel fan-out in supervisor for multi-part plans. Milestone: "what did I tell you about the Bali trip last week".

**Phase 5 — Statement ingest (2 days, optional).**
WhatsApp media URL -> download PDF/CSV -> parse rows -> insert into `expenses` with `source='statement'`. Gemini does the parsing if rows are messy.

---

## 8. Open decisions before coding

1. **Currency / locale** — SGD? INR? Both? Determines parsing defaults.
2. **Credit-card analysis depth** — category breakdown is easy; anomaly detection / merchant clustering is a bigger lift. What do you actually want to see each month?
3. **Reminder delivery window** — 30s polling is simplest; if minute-accuracy isn't enough, use APScheduler.
4. **Keep flat agent as fallback?** — Recommend yes for one release while supervisor stabilizes, then remove.
