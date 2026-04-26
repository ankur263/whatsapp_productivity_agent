# WhatsApp Productivity Agent Roadmap

## Level 0: Bootstrap
- Set up Python 3.11 virtual environment
- Install dependencies
- Configure `.env`
- Validate Ollama + ngrok availability

## Level 1: ReAct CLI Core
- Build ReAct loop with strict `Thought/Action/Observation/Final Answer` format
- Add core tools: web search, calculator, wikipedia summary, current time
- Validate with CLI smoke tests

## Level 2: Productivity Tools (Single User)
- Add SQLite task database
- Add workspace file tools with path safety checks
- Validate task and file workflows from CLI

## Level 3: Persistence + Multi-User Isolation
- Add persistent memory (`chroma` with fallback)
- Scope data by user ID for tasks, files, and memory
- Validate cross-user isolation and restart persistence

## Level 4: WhatsApp Integration (Flask)
- Verify webhook handshake (`GET /webhook`)
- Process inbound messages (`POST /webhook`)
- Enforce signature verification (`X-Hub-Signature-256`)
- Add allowlist and per-user routing

## Level 5: Hardening + Ops
- Add structured logging to file + console
- Add reminder scheduler and WhatsApp-friendly tools
- Add idempotency and error handling
- Document token lifecycle and operational runbook

## Level 6A: Grocery Agent (G1)
- Add `grocery_items` schema in SQLite (user scoped)
- Add core grocery CRUD tools:
  - `add_grocery_item`
  - `list_grocery_items`
  - `mark_grocery_bought`
  - `remove_grocery_item`
- Add deterministic routing for grocery commands in agent fast-path
- Validate from CLI and WhatsApp:
  - `buy milk`
  - `show grocery list`
  - `mark grocery 1 bought`
  - `remove grocery 1`

## Level 6B: Grocery Natural Parsing + Upsert (G2)
- Parse natural add phrases:
  - `buy 2 milk`
  - `buy 1.5 kg rice`
  - `get toilet paper`
- Normalize grocery synonyms (example: `toilet paper` -> `washroom tissue`)
- Infer category automatically from item keywords
- Upsert pending groceries by normalized name (merge quantity instead of duplicate rows)
- Support listing variants:
  - `show grocery list` (pending)
  - `show bought groceries`
  - `show all groceries`

## Level 6C: Grocery Organization + Budget (G3)
- Add per-item editable category and unit price
- Group grocery list by store-friendly category ordering
- Add budget summary tool using per-item estimates
- Add direct routing:
  - `set grocery 2 category produce`
  - `set grocery 2 price 3.50`
  - `show grocery budget`
  - `show grocery list by category`
  - `clear bought groceries`
  - `clear all groceries`

## Level 6D: Repeat + Rebuy (G4)
- Add repeat from recent bought history:
  - `repeat last week groceries`
  - `repeat groceries 14d`
- Add recurring-item suggestions:
  - `suggest rebuy`

## Level 6E: Meal to Grocery Planner (G5)
- Add meal templates to grocery conversion:
  - `plan meals pasta, omelette`
  - `meal plan fried rice, salad`
- Upsert ingredients into pending grocery list

## Level 6F: Store Price Intelligence (G6)
- Add user-scoped `store_prices` tracking in SQLite
- Record manual store prices:
  - `set price milk|fairprice|3.20`
- Compare latest prices across stores:
  - `compare price milk`
  - `best price milk`

## Level 6G: Home Inventory + Auto Replenish (G7)
- Add user-scoped inventory tables and event log
- Track stock and thresholds:
  - `stock rice 3 kg`
  - `use rice 1 kg`
  - `set threshold rice 2 kg`
  - `show inventory`
  - `show inventory low`
- Auto-replenish behavior:
  - when usage drops stock below threshold, add refill quantity to pending grocery list
  - low signal phrase shortcut: `toilet tissue low`
