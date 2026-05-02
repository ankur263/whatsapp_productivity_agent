# WhatsApp Productivity Agent

Production-style AI productivity agent built from scratch on this machine.

- Framework: Flask webhook server
- LLM: Ollama local-first (`llama3.2`), OpenAI optional fallback
- Persistence: SQLite (`data/tasks.db`) + Chroma (`data/memory_db`, fallback included)
- Transport: Meta WhatsApp Cloud API
- Tunnel (local dev): ngrok

## 1) Project Layout :-

```text
whatsapp_productivity_agent/
├── agent.py
├── tools.py
├── prompts.py
├── memory.py
├── database.py
├── router.py
├── whatsapp_client.py
├── whatsapp_server.py
├── main.py
├── ROADMAP.md
├── LEARN.md
├── .env.example
├── requirements.txt
└── data/ (runtime)
```

## 2) Setup

1. Create venv (Python 3.11 preferred):
   - `python3.11 -m venv venv`
   - fallback: `python3 -m venv venv`
2. Activate:
   - `source venv/bin/activate`
3. Install deps:
   - `pip install -r requirements.txt`
4. Env file:
   - `cp .env.example .env`
   - fill WhatsApp credentials + tokens.

## 3) Ollama (Local-First)

1. Install Ollama from official installer.
2. Start service:
   - `ollama serve`
3. Pull model:
   - `ollama pull llama3.2`
4. Quick verify:
   - `curl http://localhost:11434/api/tags`

If Ollama is unavailable, set `LLM_BACKEND=mock` to run local smoke flows.

## 4) Meta WhatsApp Cloud API

In Meta developer dashboard:
1. Create app (`Business` type), add WhatsApp product.
2. Capture and place in `.env`:
   - `WHATSAPP_PHONE_NUMBER_ID`
   - `WHATSAPP_ACCESS_TOKEN` (temporary 24-hour token initially)
   - `WHATSAPP_APP_SECRET`
   - `WHATSAPP_VERIFY_TOKEN` (your own random string)
3. Add your phone as test recipient.

### Token lifecycle
- **Development:** temporary 24-hour token from API Setup page.
- **Production:** generate permanent **System User token** (Business settings), then replace `WHATSAPP_ACCESS_TOKEN`.

## 5) Run

CLI mode:
- `python main.py`

CLI smoke tests:
- `LLM_BACKEND=mock python main.py --smoke`

Webhook server:
- `python whatsapp_server.py`

ngrok:
- `ngrok http 8000`

Meta webhook config:
- Callback URL: `https://<your-ngrok-id>.ngrok-free.app/webhook`
- Verify token: value from `WHATSAPP_VERIFY_TOKEN`

## 6) Security + Reliability Notes

- Signature verification enforced with `X-Hub-Signature-256` (HMAC SHA-256).
- Message idempotency implemented in-memory via processed message IDs.
- Per-user rate limiting in webhook layer.
- User allowlist enforced by `ALLOWED_USERS` (if empty, server acts permissive in dev).

## 7) Acceptance Checklist

1. CLI tools: calculator, time, wiki, web search.
2. Task tools: create/list/complete/delete.
3. Grocery tools: add/list/mark-bought/remove/set-category/set-price/budget.
4. File tools: read/write in `workspaces/<user>/`.
5. Memory and task persistence survives restart.
6. WhatsApp E2E works for allowlisted users.
7. Non-allowlisted users get rejection.
8. Invalid signature rejected with 403.

## 8) Grocery Commands (G1-G7)

Supported quick commands:
- `buy milk`
- `buy 2 milk`
- `buy 1.5 kg rice`
- `add grocery eggs`
- `get toilet paper`
- `show grocery list`
- `show bought groceries`
- `show all groceries`
- `mark grocery 2 bought`
- `remove grocery 2`
- `clear bought groceries`
- `clear all groceries`
- `set grocery 2 category produce`
- `set grocery 2 price 3.50`
- `show grocery budget`
- `show grocery list by category`
- `I mean suji not rice` (quick correction)
- `repeat last week groceries`
- `suggest rebuy`
- `plan meals pasta, omelette`
- `set price milk|fairprice|3.20`
- `compare price milk`
- `set threshold rice 2 kg`
- `stock rice 3 kg`
- `use rice 1 kg`
- `show inventory`
- `show inventory low`
- `toilet tissue low`

Tool argument contracts:
- `add_grocery_item`: natural text or `item|qty|unit|category` (upsert on pending item)
- `list_grocery_items`: optional status `pending|bought|all` and view `grouped|flat`
- `mark_grocery_bought`: grocery item number from displayed list
- `remove_grocery_item`: grocery item number from displayed list
- `clear_bought_groceries`: clear all bought items for current user
- `clear_all_groceries`: clear all items for current user
- `set_grocery_category`: `<item_number> <category>` or `<item_number>|<category>`
- `set_grocery_price`: `<item_number> <unit_price>` or `<item_number>|<unit_price>`
- `grocery_budget_summary`: `pending|bought|all`
- `replace_grocery_item`: `new_item|old_item` (for quick correction)
- `repeat_last_groceries`: optional period like `7d`, `2w`, `month`
- `suggest_rebuy`: empty
- `plan_meals_to_grocery`: comma-separated meals (`pasta, omelette`)
- `record_store_price`: `item|store|price`
- `compare_store_price`: item name
- `stock_item`: item + qty + unit (`rice|3|kg` or `stock rice 3 kg`)
- `use_item`: item + qty + unit (`rice|1|kg` or `use rice 1 kg`)
- `set_stock_item`: absolute stock (`rice|2|kg` or `set stock rice 2 kg`)
- `set_inventory_threshold`: threshold (`rice|2|kg` or `set threshold rice 2 kg`)
- `list_inventory`: empty or `low`
- `report_item_low`: item name (`toilet tissue`)
