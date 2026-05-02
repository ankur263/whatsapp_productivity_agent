# WhatsApp Bot — Debug Session Summary

## Current state
- ✅ New DuckDNS subdomain created: `homeos-bot.duckdns.org`
- ⏳ Caddy reverse proxy not yet updated to serve the new domain
- ⏳ Meta webhook URL not yet updated to point to new domain
- ⏳ Bot is currently unreachable from the internet → cannot receive messages

---

## Original symptoms reported by user

1. **Bot contact appears as phone number `+1 (934) 239-0745`** instead of a name in WhatsApp.
2. **Repeated logs every ~12 hours** showing:
   ```
   "Re-engagement message", "error_data": {"details": "Message failed to send because more than 24 hours have passed since the customer last replied to this number."}
   ```
3. User concerned that "every time user leaves chat for >24h, bot stops working" — perceived as bug.
4. **Bot did not reply to `/join`** when sent in WhatsApp.
5. WhatsApp Business Manager only had `hello_world` template — no `reminder_v1` or `grocery_digest_v1`.

---

## Root causes identified (in order of severity)

### 1. CRITICAL: Webhook endpoint unreachable from internet (root cause of bot being dead)

GCP gave the VM a new ephemeral IP. DuckDNS pointed to the old one.

| | IP |
|---|---|
| VM is actually at | `34.69.205.160` |
| Old DuckDNS (`whatsapp-productivity-agent.duckdns.org`) pointed to | `34.45.145.204` (stale) |
| New DuckDNS (`homeos-bot.duckdns.org`) | needs to be set to `34.69.205.160` after Caddy reconfig |

Curl test from VM to its own public domain timed out after 10s. Meta could not deliver any inbound webhook events → bot couldn't see `/join` or any other message.

### 2. Bot was using Meta's sandbox/test phone number ID, not production

| Phone Number ID | Number | Name | Use |
|---|---|---|---|
| `1086634547864362` (was in env) | +1 555-634-2321 | "Test Number" | Meta sandbox — limited to 5 pre-registered test recipients, can't be shared with friends |
| `1100969749771363` (now in env) | +1 934-239-0745 | "Vanshika Assistant" → being changed to "Home OS" | Real production number |

This was discovered when the user pasted env vars showing `WHATSAPP_PHONE_NUMBER_ID=1086634547864362` while the API confirmed `1100969749771363` was the live number.

### 3. Templates `reminder_v1` and `grocery_digest_v1` not approved (originally absent)

The bot's reminder loop in [whatsapp_server.py:163-168](whatsapp_server.py#L163-L168) correctly falls back to `send_template_message("reminder_v1", ...)` when the user has been silent >24h. But the template didn't exist in Meta Business Manager. Each fallback call returned error 131047 → log spam every ~12 hours.

### 4. Display name change didn't stick

POST to `verified_name=Home OS` returned `{"success":true}` but later GET still showed `Vanshika Assistant`. Likely because the name change was applied against an ID owned by an account with `name_status: AVAILABLE_WITHOUT_REVIEW` but Meta still has eventual consistency, or the change was applied to a stale phone number context. Will be re-applied after DNS fix.

### 5. `/join` (no code) handler missing

[router.py:62](router.py#L62) requires a space + code (`/join 640284`). Bare `/join` falls through to the LLM and produces a confused/silent response. Cosmetic but affects friends-testing UX.

### 6. WhatsApp 24-hour customer service window — NOT a bug

This is a Meta platform rule, not a bot bug. Confirmed user understood:
- Bot can reply freely within 24h of user's last message
- Outside 24h, only approved templates can be sent
- User reply resets the timer immediately
- "Users leaving chat" is fine — they get instant replies whenever they message; only proactive bot pushes after 24h+ silence need templates

---

## What we've completed ✅

### Display name
- Changed from default to `Vanshika Assistant` via Meta UI (worked)
- Attempted change to `Home OS` via API:
  ```bash
  curl -X POST "https://graph.facebook.com/v21.0/1100969749771363" \
    -H "Authorization: Bearer $TOKEN" \
    --data-urlencode "verified_name=Home OS"
  ```
  Returned `{"success":true}` — but later GET still showed `Vanshika Assistant`. Will re-apply after DNS fix.
- Confirmed via API: `name_status: AVAILABLE_WITHOUT_REVIEW` and `quality_rating: GREEN` — name changes are instant with no review queue.

### Templates
- **`reminder_v1`** — submitted to Meta with reworked body that doesn't put `{{1}}` at start/end (initial submission rejected for "too many variables for length"). Body example: `Hi! This is your reminder: {{1}}. Reply here if you want to update or snooze it.`
- **`grocery_digest_v1`** — abandoned after multiple rejections (Meta kept classifying as Marketing). Code in [whatsapp_server.py:256-260](whatsapp_server.py#L256-L260) was repurposed to reuse `reminder_v1` with a formatted digest string instead. User said "done" — assumed code change applied.

### Container env / restart
- Updated `WHATSAPP_PHONE_NUMBER_ID` from `1086634547864362` → `1100969749771363` in `/home/vanshikakhare/app/.env` on the VM.
- Backed up `.env` first (`.env.backup.before_phoneid_fix`).
- Stopped, removed, and re-launched container with same docker run command from `DEPLOY_CHEATSHEET.md`.
- Verified via `docker exec whatsapp printenv WHATSAPP_PHONE_NUMBER_ID` → `1100969749771363`.
- Verified container started cleanly: `Reminder scheduler started.`, `Proactive grocery digest scheduler started.`, `Starting Flask server on port=8000`.

### DNS diagnosis
- Confirmed VM IP is `34.69.205.160` via `curl -s ifconfig.me`.
- Confirmed `getent hosts whatsapp-productivity-agent.duckdns.org` returned `34.45.145.204` (mismatched).
- Confirmed `curl -i -m 10 https://whatsapp-productivity-agent.duckdns.org/webhook?...` timed out — endpoint unreachable.
- User unable to access original DuckDNS account that owned `whatsapp-productivity-agent`.
- Created **new DuckDNS subdomain `homeos-bot.duckdns.org`** under accessible account.

---

## What's pending ⏳

### Critical path to get bot working again

1. **Set IP for new DuckDNS subdomain** to `34.69.205.160`:
   ```bash
   curl -s "https://www.duckdns.org/update?domains=homeos-bot&token=NEW_DUCKDNS_TOKEN&ip=34.69.205.160"
   ```
   Expect `OK`. Token is on the DuckDNS dashboard (top of page).

2. **Update Caddy config on VM** to serve `homeos-bot.duckdns.org`:
   - Caddy config file is likely `/etc/caddy/Caddyfile` (or `/home/vanshikakhare/Caddyfile`)
   - Replace old domain block with new domain
   - Caddy will auto-issue a fresh Let's Encrypt cert
   - Reload: `sudo systemctl reload caddy`
   - Verify: `sudo systemctl status caddy`

3. **Verify reachability** from VM:
   ```bash
   VTOKEN=$(docker exec whatsapp printenv WHATSAPP_VERIFY_TOKEN)
   curl -i -m 10 "https://homeos-bot.duckdns.org/webhook?hub.mode=subscribe&hub.challenge=test123&hub.verify_token=$VTOKEN"
   ```
   Expect `200 OK` + body `test123`.

4. **Update Meta webhook URL** to new domain:
   - https://developers.facebook.com/apps → click WhatsApp app
   - Left sidebar → **WhatsApp** → **Configuration**
   - **Webhook** section → Edit:
     - Callback URL: `https://homeos-bot.duckdns.org/webhook`
     - Verify token: same as `docker exec whatsapp printenv WHATSAPP_VERIFY_TOKEN`
   - Click **Verify and save** — should return green "Verified" badge
   - **Webhook fields** → ensure `messages` is subscribed

5. **Subscribe WABA to app** (often missed step):
   ```bash
   TOKEN=$(docker exec whatsapp printenv WHATSAPP_ACCESS_TOKEN)
   WABA_ID=<find_in_meta_business_manager>
   curl -X POST "https://graph.facebook.com/v21.0/$WABA_ID/subscribed_apps" \
     -H "Authorization: Bearer $TOKEN"
   ```

6. **End-to-end inbound test** — send "hi" from phone to +1 (934) 239-0745, watch:
   ```bash
   docker logs -f whatsapp --tail 0
   ```
   Payload should appear within 1-2 seconds, bot should reply.

### After DNS + webhook are restored

7. **Re-apply Home OS display name** against the now-correct phone number ID:
   ```bash
   TOKEN=$(docker exec whatsapp printenv WHATSAPP_ACCESS_TOKEN)
   curl -X POST "https://graph.facebook.com/v21.0/1100969749771363" \
     -H "Authorization: Bearer $TOKEN" \
     --data-urlencode "verified_name=Home OS"
   ```
   Verify with GET fields=verified_name.

8. **Confirm `reminder_v1` template approval status**:
   ```bash
   TOKEN=$(docker exec whatsapp printenv WHATSAPP_ACCESS_TOKEN)
   WABA_ID=<...>
   curl -s "https://graph.facebook.com/v21.0/$WABA_ID/message_templates?access_token=$TOKEN" | jq '.data[] | {name, status}'
   ```
   Look for `reminder_v1` with `status: APPROVED`.

9. **Set up DuckDNS auto-update cron** so the IP→DNS mapping survives future GCP IP rotations:
   ```bash
   mkdir -p ~/duckdns
   cat > ~/duckdns/duck.sh <<'EOF'
   #!/bin/sh
   echo url="https://www.duckdns.org/update?domains=homeos-bot&token=YOUR_DUCKDNS_TOKEN&ip=" | curl -s -K - >> ~/duckdns/duck.log
   EOF
   chmod +x ~/duckdns/duck.sh
   (crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1") | crontab -
   ```
   Or alternatively reserve a static IP in GCP (~$3/mo).

### Code-level cleanups (low priority, before sharing with friends)

10. **Add bare `/join` handler** in [router.py:62](router.py#L62) so users who type `/join` without a code get a hint about correct usage rather than a confused LLM reply.

11. **Update `DEPLOY_CHEATSHEET.md`** to reference the new domain `homeos-bot.duckdns.org`.

---

## Important context / things confirmed

- **Bot does NOT need templates approved for friends to test.** Templates are only needed for proactive bot-initiated messages outside 24h. Normal user-initiated chat works without any templates.
- **The 24h window resets every time the user replies.** Friends won't have to wait 24h between any messages — only the bot's proactive pushes are gated.
- **`name_status: AVAILABLE_WITHOUT_REVIEW`** means display name changes apply instantly with no Meta review wait.
- **`quality_rating: GREEN`** means messaging health is excellent.
- **Business is NOT verified** by Meta (confirmed by user). 30-day cooldown rule for display name changes does not apply because of `AVAILABLE_WITHOUT_REVIEW` status.
- **Friends won't see the bot as "Home OS" automatically** without Meta Green Tick verification (hard to get for personal projects). Workaround: tell friends to save the contact, or send a vCard.
- **Once webhook is fixed, all the cosmetic issues become solvable in minutes.** The DNS issue was masking everything.

---

## Files referenced

| File | Purpose |
|---|---|
| [whatsapp_server.py:163-168](whatsapp_server.py#L163-L168) | 24h window handling — sends template if outside window |
| [whatsapp_server.py:256-260](whatsapp_server.py#L256-L260) | Grocery digest send (now reuses `reminder_v1`) |
| [whatsapp_server.py:418](whatsapp_server.py#L418) | Webhook handler — logs every payload |
| [whatsapp_client.py:64-102](whatsapp_client.py#L64-L102) | `send_template_message` |
| [router.py:62](router.py#L62) | `/join CODE` handler (missing bare-/join fallback) |
| `/home/vanshikakhare/app/.env` (VM) | Env vars including phone number ID |
| `/etc/caddy/Caddyfile` (VM, expected) | Reverse-proxy config — needs update for new domain |
| `DEPLOY_CHEATSHEET.md` | VM ops reference |

---

## Quick reference — IDs and values

| | |
|---|---|
| GCP VM | `whatsapp-agent` (zone `us-central1-a`, project `whatsapp-agent-494702`) |
| VM external IP | `34.69.205.160` (ephemeral — changes on stop/restart) |
| Old domain (broken) | `whatsapp-productivity-agent.duckdns.org` (points to stale `34.45.145.204`) |
| New domain (in progress) | `homeos-bot.duckdns.org` |
| Production phone | +1 (934) 239-0745 |
| Production phone ID | `1100969749771363` |
| Test/sandbox phone (deprecated) | +1 (555) 634-2321 (ID `1086634547864362`) |
| App secret env | `WHATSAPP_APP_SECRET=48768b542acc02342b64a38e82eecb2d` |
| API version | `v25.0` |
| Approved template | `hello_world` only (so far) |
| Submitted template | `reminder_v1` (pending — status not reconfirmed) |



I am at this step currently -- 
Path B: Create a new subdomain (15 minutes, recoverable)
If you genuinely can't recover the original account, we abandon whatsapp-productivity-agent.duckdns.org and create a fresh one. Steps:

On DuckDNS (in any account you can access), create a new subdomain — e.g., homeos-bot → URL becomes homeos-bot.duckdns.org
Set its IP to 34.69.205.160
Update Caddy on the VM to serve the new domain (auto-renewal of TLS cert via Let's Encrypt)
Update Meta webhook URL to https://homeos-bot.duckdns.org/webhook
Re-verify in Meta
The original .duckdns.org will still exist but unused — no harm, no cost.



success: domain homeos-bot.duckdns.org added to your account
no what next ??