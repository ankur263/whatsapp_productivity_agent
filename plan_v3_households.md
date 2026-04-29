# Home OS — Architecture & Scaling Plan (v3)

**Status:** Phases 1–4 Implemented & Tested. Moving to Proactive Features and Knowledge/Journal Agents.
**Goal:** Evolve the bot from a single-player Gemini prototype into a multi-tenant, privacy-first, Groq-powered collaborative Home OS running on WhatsApp.

---

## 1. The "Groq-First" AI Architecture

**The Problem:** Google Gemini's Free Tier (20 requests per minute / 1,500 daily, or 20 daily for unbilled projects) collapses under multi-user chat loads.
**The Solution:** Offload 95% of LLM calls to Groq's massive free tier (14,400 RPD) via OpenAI-compatible tool calling.

*   **Ears (Audio):** `whisper-large-v3-turbo` (Groq) — Near-instant Hinglish/English transcription.
*   **Brain (Supervisor):** `llama-3.1-8b-instant` or `llama-3.3-70b-versatile` (Groq) — Strict JSON output for routing.
*   **Hands (Specialists):** `llama-3.3-70b-versatile` (Groq) — Complex tool execution.
*   **Eyes (Images):** `gemini-2.5-flash` (Google) — Retained strictly as a fallback for visual tasks (e.g., receipt parsing) to protect quota.

---

## 2. Multi-Tenant Data Model (Households)

We abandon the `user_id` silo and introduce a "Workspaces" model to allow helpers, spouses, and extended family to share specific domains without leaking private data.

### Core Schema Additions
```sql
CREATE TABLE households (
  id TEXT PRIMARY KEY,
  name TEXT,
  created_at TIMESTAMP,
  created_by TEXT
);

CREATE TABLE household_members (
  household_id TEXT,
  user_id TEXT,
  role TEXT,             -- 'owner', 'member' (v1 skips complex RBAC)
  joined_at TIMESTAMP,
  PRIMARY KEY (household_id, user_id)
);
```

### Privacy & Scoping Mechanics
1.  **The Implicit Personal Scope:** If a record (task, expense, note) has `household_id IS NULL`, it belongs strictly to the `user_id`. It is cryptographically invisible to everyone else.
2.  **Attribution:** Collaborative tables (groceries, events) gain `added_by_user_id` and `bought_by_user_id` so the bot can summarize actions ("Helper bought 5 items").
3.  **Per-Resource Defaults:** Added to `user_settings`. Users do not need to constantly type `/switch`. 
    *   `default_grocery_household` (e.g., Khare Home)
    *   `default_finance_household` (e.g., Couples Private)
    *   `default_task_household` (e.g., NULL / Personal)

---

## 3. The "Privacy Bouncer" Routing Engine

Because the cost of a misrouted grocery item is trivial but a misrouted expense is catastrophic, the Supervisor transitions from a blind router to a "Privacy Bouncer".

### The 4-State Output Schema
The Supervisor JSON schema expands: `kind = "reply" | "route" | "plan" | "clarify"`

### The Rules of Friction
The Supervisor routes silently (zero friction) for 95% of queries based on per-resource defaults. It returns `kind="clarify"` **only if**:
1.  **Privacy Guard:** Resource is High-Stakes (Finance/Journal) AND the inferred target household contains non-family members.
2.  **Ambiguity Guard:** Two or more candidate households are equally plausible and confidence is < 85%.
3.  **Intent Override Guard:** User explicitly references a scope that conflicts with their default (e.g., "Log $50 for parents" when default is Couple).

### The WhatsApp UX
*   **Silent Routes (Context Cues):** Even when silent, the bot explicitly echoes the scope to catch hallucinations. 
    *   *Bot:* "💸 Logged ₹8000 to **Couple** budget."
*   **Clarification Flow:** 
    *   *Bot:* "Should I log ₹8000 to: 1. Couple, 2. Home. Reply 1 or 2."
    *   State is stored in a lightweight, 5-minute TTL "pending clarification" table.

---

## 4. Invite-Led Authentication (Zero-Touch Admin)

Moving away from `.env ALLOWED_USERS` to a fully self-serve onboarding engine.

1.  **The Lobby (Unauthenticated State):** 
    *   Strangers who text the bot hit the Lobby. LLMs are bypassed.
    *   *Bot:* "Welcome! Reply with your 6-digit invite code."
2.  **Admin Bootstrap (The Master Key):** 
    *   The Admin texts `/claim [SECRET_KEY]`.
    *   Bot creates their profile, provisions their first Household, makes them `owner`, and burns the key.
3.  **The Viral Loop (Self-Serve Invites):**
    *   Admin texts `/invite`. Bot generates `482915` (24h TTL).
    *   Helper texts `/join 482915`.
    *   Bot validates code, creates Helper profile, joins them to the Admin's household, and starts Onboarding (Timezone/Currency).
4.  **Seed Invites (B2B Scaling):**
    *   To give a friend their own isolated Home OS, Admin texts `/generate_seed`.
    *   Friend texts `/start SEED-1234`, creating a brand new isolated master household for them to rule.

---

## 5. Go-To-Market & Scaling Defenses

When scaling beyond 5 friends, the system must defend its Groq/Gemini quotas and Meta API standing.

1.  **Spam Filtering:** The Lobby acts as a "Light Captcha". Regex catches `/join` and `/start` commands; everything else is politely rejected. Zero LLM tokens wasted on spam.
2.  **24-Hour Window Compliance:** WhatsApp only allows free-form replies within 24 hours of user input. Reminders/Cron jobs outside this window MUST fallback to pre-approved Meta Template Messages (`send_template_message`).
3.  **Cloud Deployment:** Migrate off laptop/ngrok to Railway.app or AWS EC2 for 24/7 uptime.
4.  **Launch Sequence:**
    *   *Week 1:* 5 friends (Dev mode, stress testing invites).
    *   *Week 2:* Submit Meta Business Verification. 
    *   *Week 3:* Launch free Vercel landing page with `wa.me/number?text=household` pre-filled intent.
    *   *Week 4:* Public rollout, monitoring Groq Token-Per-Minute (TPM) limits.

---

## 6. Implementation Phasing

**Phase 1: The Groq Migration ✅ Completed**
*   [x] Update `agent.py` and `tools.py` to support OpenAI-compatible tool calling.
*   [x] Migrate Planner, Shopping, Events, and Finance Specialists to `llama-3.3-70b-versatile`.
*   [x] Verify quotas stabilize (Eliminated Gemini Token/Minute bottlenecks).

**Phase 2: The Multi-Tenant DB ✅ Completed**
*   [x] Write SQL migrations for `households`, `household_members`, and `invites`.
*   [x] Update `grocery_items`, `inventory_items` to scope using `household_id` and track `added_by_user_id`.
*   [x] Implement `/switch`, `/leave`, and `/createhouse` for seamless workspace management.

**Phase 3: The Privacy Bouncer & Context Cues ✅ Completed**
*   [x] Update `SUPERVISOR_SCHEMA` to include `kind="clarify"`.
*   [x] Implement the 3 Prompt Rules for friction (Privacy, Ambiguity, Intent Override).
*   [x] Build the `pending_clarifications` transient state router (5-minute TTL).
*   [x] Update Specialist prompts to enforce visible context cues in their output strings (e.g. "Logged to Couples Private").

**Phase 4: Auth & Onboarding ✅ Completed**
*   [x] Remove `.env ALLOWED_USERS` and migrate to database `is_allowed` flag.
*   [x] Build the Lobby fast-path router to bounce unauthenticated strangers.
*   [x] Implement `/claim` (Admin Bootstrap), `/invite` (Generate 6-digit code), and `/join` (Self-serve onboarding).

**Phase 5: Knowledge & Journal Agents (Next)**
*   Wire `web_search`, `wikipedia_summary`, and `calculate` into the KnowledgeAgent via Groq tool calling.
*   Implement JournalAgent with metadata-filtered Chroma recall.
*   Apply explicit `household_id IS NULL` scoping so Journal notes remain 100% private to the `user_id`.

**Phase 6: Proactive Grocery Digest ✅ Completed**
*   [x] Implement **Consumption Velocity Tracking**: Flag items as `is_due` based on historical average days between purchases.
*   [x] Implement **Shopping Pattern Detection**: Generate a day-of-week histogram to predict the household's actual shopping day.
*   [x] Add a background cron checker to fire a Weekly Digest Meta Template 24 hours before the predicted shopping day.

**Phase 7: Smart Basket Optimization ✅ Completed**
*   [x] Use uploaded receipt OCR data (via Gemini) to populate historical `store_prices` table.
*   [x] Implement time-decay confidence (ignore prices > 60 days old).
*   [x] Build `suggest_grocery_run` algorithm factoring in price deltas vs. travel friction.