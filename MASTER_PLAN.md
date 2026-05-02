# Home OS — Master Architecture & Roadmap

**Status:** v3 (Groq-First, Multi-Tenant) Live.
**Goal:** A multi-tenant, privacy-first, proactive personal and household assistant running on WhatsApp.

---

## 1. Strategic Context & Architecture

**The "Groq-First" AI Pipeline**
*   **Ears (Audio):** `whisper-large-v3-turbo` (Groq) — Native OGG/OPUS transcription.
*   **Brain (Supervisor):** `llama-3.3-70b-versatile` (Groq) — Strict JSON output for routing. Acts as a "Privacy Bouncer".
*   **Hands (Specialists):** `llama-3.3-70b-versatile` (Groq) — Domain-specific tool execution.
*   **Eyes (Images/Docs):** `gemini-2.5-flash` (Google) — Retained strictly for visual tasks (receipt OCR, document parsing).

**The Concurrency & Execution Model**
*   **Flask / Webhook:** Threaded processing (no asyncio overhead).
*   **Parallel Fan-Out:** `ThreadPoolExecutor` for multi-step plans (e.g., logging an expense and setting a reminder simultaneously).
*   **Stateless Agents:** Specialists hold no in-memory state across turns. They read context from SQLite/Chroma per call.
*   **Async Jobs (Slow Path):** Slow specialist work (statement parsing, big web research) returns `{"kind": "ack", "text": "Working on it..."}`. A background worker thread picks up the job, processes it, and pushes the result later.

---

## 2. Multi-Tenant Data Model & Auth

**Household Workspaces**
*   `user_id` vs `household_id` scoping in SQLite (`PRAGMA journal_mode=WAL` and `synchronous=NORMAL` enabled for concurrency).
*   **Implicit Personal Scope:** If `household_id IS NULL`, data is strictly personal and cryptographically invisible to everyone else.
*   **Context Cues:** Even when routing silently, the bot explicitly echoes the scope to catch hallucinations (e.g., *"💸 Logged ₹8000 to Couple budget."*).

**Auth & Onboarding (Viral Loop)**
*   **The Lobby (Bouncer):** Unauthenticated strangers are bounced by a fast-path regex (no LLM tokens wasted).
*   **Admin Bootstrap:** `/claim <secret>` provisions the first owner and burns the master key.
*   **Self-Serve Invites:** `/invite` generates a 6-digit code (24h TTL); `/join <code>` adds helpers or spouses seamlessly.
*   **Onboarding State Machine:** Users are gracefully stepped through `await_currency`, `await_timezone`, and `await_family_size` on first join.

**Financial Data Hygiene**
*   **Minor Units:** Money is stored in minor units (cents/paise) as integers (`amount_minor`) to prevent float drift on summing.
*   **Category Normalization:** A static `CATEGORY_MAP` maps free-form text into fixed buckets (food, transport, utilities, rent, entertainment, etc.).

---

## 3. The "Privacy Bouncer" Routing Engine

The Supervisor JSON schema expands to four states: `kind = "reply" | "route" | "plan" | "clarify"`.
It routes silently (zero friction) for 95% of queries based on per-resource defaults, but returns `clarify` if it trips any of the **3 Rules of Friction**:

1.  **Privacy Guard:** Resource is High-Stakes (Finance/Journal) AND the inferred target household contains non-family members.
2.  **Ambiguity Guard:** Two or more candidate households are equally plausible and confidence is < 85%.
3.  **Intent Override Guard:** User explicitly references a scope that conflicts with their default (e.g., "Log $50 for parents" when default is Couple).

*(Clarification state is stored in a lightweight, 5-minute TTL `pending_clarifications` table).*

---

## 2. Specialist Roster

| Agent | Owns | Key Tools |
|---|---|---|
| **Planner** | Tasks, ad-hoc reminders | Uses deterministic `parse_when(text, user_tz)` so LLMs don't guess timestamps. Tools: `create_task`, `set_reminder`. |
| **Finance** | Expenses, budgets, statements | New `expenses`, `budgets`, `cards` tables. Normalizes to `CATEGORY_MAP`. |
| **Shopping** | Groceries, inventory, prices | ~25 tools. Tracks consumption velocity. Tools: `suggest_rebuy`, `grocery_budget_summary`. |
| **Events** | Birthdays, anniversaries | `add_event`. Background cron auto-expands recurring events into actionable reminders. |
| **Knowledge** | Web search, Wikipedia, math, "how do I…" | `web_search`, `wikipedia_summary`, `calculate` |
| **Journal** | Free-form notes, semantic recall | `save_note`, `search_notes` (fallback to Chroma DB). Explicitly isolated to `user_id`. |

**Memory Strategy:**
*   **Short-Term (`conversations` SQLite):** Rolling transcript of the last N turns for Supervisor context.
*   **Long-Term (ChromaDB):** Semantic recall with `metadata={"agent": "..."}` filtering so Journal recall doesn't accidentally surface grocery lists.

---

## 5. WhatsApp Platform Constraints & Defenses

*   **24-Hour Window Compliance:** Meta blocks free-form business-initiated messages >24h after the user's last inbound message. Proactive scheduled threads (`grocery_digest`, `reminders`) detect this and automatically fallback to pre-approved **Meta Template Messages**.
*   **Quiet Hours:** Defer non-urgent pushes between `quiet_hours_start` and `quiet_hours_end`.
*   **Media Downloads:** Webhook parses incoming `media_id` payloads and calls `download_media()` to capture raw OGG/PDF bytes before the 5-minute Meta URL expiration.

---

## 6. Implementation Phases (Roadmap)

### Phase 1: What is Built & Live (v1.0)

- [x] Meta WhatsApp Cloud API Integration (Webhooks, 24h compliance, Media downloads).
- [x] Self-serve Onboarding (`YES`/`STOP`), `/deleteme`, and Quota Limits.
- [x] Groq LLM migration and OpenAI-compatible tool calling.
- [x] Household multi-tenant database (`/switch`, `/leave`, `/createhouse`).
- [x] Planner, Shopping, Events, and Finance core tools.
- [x] Proactive Grocery Digest (consumption velocity tracking).
- [x] Vercel Landing Page with Privacy & Terms.

### Phase 2: Knowledge & Journal Deepening (Current)
*   **Knowledge:** Wire `web_search`, `wikipedia_summary`, and `calculate` into the KnowledgeAgent via Groq tool calling.
*   **Journal:** Implement JournalAgent with metadata-filtered Chroma recall. Ensure explicit `household_id IS NULL` scoping for strict privacy.

### Phase 3: Asynchronous Jobs & Receipt Parsing
*   **Job Queue:** Introduce an `ack` pattern for slow operations (`{"kind": "ack", "text": "Parsing your receipt, give me 30s"}`).
*   **Jobs Table:** Track `status='pending'`, `lease_until` (for crashed worker recovery), and `started_at`.
*   **Blob Storage:** Save downloaded WhatsApp media (images) to `data/jobs/{job_id}/` with a 7-day TTL.
*   **Receipt OCR:** Pass images to Gemini 2.5 Flash to extract line items.
*   **Smart Basket Optimization:** Use OCR data to populate the `store_prices` table. Build `suggest_grocery_run` to recommend the cheapest store for a pending basket based on historical prices.

### Phase 4: Financial Document Ingestion
*   **Statement Parser:** Allow users to upload bank/credit card PDF statements.
*   **Categorization Engine:** Map raw statement rows to the static `CATEGORY_MAP` (`food`, `transport`, `utilities`, etc.).
*   **Idempotency:** Prevent duplicate imports via a `(user_id, statement_hash)` check before insert.

### Phase 5: Voice Note Replies (TTS)
*   **Mirroring UX:** If a user sends a text, reply with text. If a user sends a Voice Note, reply with a generated Voice Note.
*   **Generation:** Send final composed text to OpenAI TTS or ElevenLabs.
*   **Transcoding:** Run output through `ffmpeg` (via Python `subprocess`) to convert to native OGG/OPUS mono so it renders as a playable waveform UI in WhatsApp.
*   **Media Upload:** POST to Meta's `/media` endpoint, then send via `whatsapp_client.py`.

---

## 7. Operations & Scaling

*   **Monitoring:** Use `SELECT * FROM routing_log` to track Supervisor fallback rates.
*   **Legacy Agent:** Maintain the legacy flat `agent.py` as a fallback only. Delete it once the fallback rate drops below 0.5% over a week.
*   **Deployment:** Keep on GCP `e2-micro` with SQLite WAL mode. Scale to Cloud SQL/Postgres only if concurrent database locks become a bottleneck (>100 active households).