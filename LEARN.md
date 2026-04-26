# LEARN.md

## ReAct in 30 seconds

ReAct means the model does this loop:
1. `Thought` (reason)
2. `Action` (pick one tool call)
3. `Observation` (tool output)
4. repeat until `Final Answer`

The model only outputs text. Your Python code performs the action.

## Why user isolation matters

This bot is multi-user. Every data path is scoped by user:
- SQLite queries include `user_id`
- Memory uses per-user collection names
- File tools write/read from `workspaces/<user>/`

Without this, one user can see another user's tasks and notes.

## Why webhook signature verification matters

Meta includes `X-Hub-Signature-256`.
You recompute HMAC over the raw body using app secret:
- match -> trusted
- mismatch -> reject (403)

This prevents spoofed requests.

## Why temporary token rotation is needed

WhatsApp test tokens expire every ~24 hours.
When expired:
- outbound send returns 401
- refresh token in `.env`

For stable deployment, migrate to system user token.

## Data stores in this project

- `data/tasks.db` (SQLite): tasks, reminders, notes
- `data/memory_db` (Chroma): semantic memory
- fallback memory: `data/memory_fallback.db` if Chroma unavailable
- `workspaces/<user>/`: user-safe file area

## Debug sequence

1. `python main.py --smoke` with `LLM_BACKEND=mock`
2. `python whatsapp_server.py`
3. hit `/health`
4. verify webhook handshake in Meta
5. send text to WhatsApp bot
6. check `data/agent.log` for route, tool, and send events
