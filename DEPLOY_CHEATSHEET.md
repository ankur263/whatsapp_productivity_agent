# WhatsApp Agent — Deploy & Ops Cheat Sheet

## Live setup

| Component | Value |
|---|---|
| GCP project | `whatsapp-agent-494702` |
| VM name | `whatsapp-agent` |
| VM zone | `us-central1-a` |
| Machine type | `e2-micro` (always-free tier) |
| Public IP | Ephemeral (changes on VM stop/start) |
| Domain | `homeos-bot.duckdns.org` |
| Webhook URL | `https://homeos-bot.duckdns.org/webhook` |
| Health URL | `https://homeos-bot.duckdns.org/health` |
| Reverse proxy | Caddy (auto Let's Encrypt cert) |
| Container name | `whatsapp` |
| Container port | `8000` (bound to `127.0.0.1` only — Caddy proxies) |
| Persistent data | `~/app/data` on VM → `/app/data` in container (SQLite lives here) |

## Connect to the VM

```bash
gcloud compute ssh whatsapp-agent --zone=us-central1-a
```

## Daily ops

```bash
# Tail live logs (Ctrl+C to exit)
docker logs -f whatsapp

# Recent logs only
docker logs whatsapp --tail 50

# Restart container (keeps data)
docker restart whatsapp

# Check container is running
docker ps

# Check Caddy is serving HTTPS
sudo systemctl status caddy --no-pager | head -10

# Test the endpoint from anywhere
curl https://homeos-bot.duckdns.org/health
```

## Push a code change

**On your Mac:**
```bash
cd ~/Desktop/whatsapp_productivity_agent
tar --exclude='venv' --exclude='venv311' --exclude='__pycache__' \
    --exclude='.git' --exclude='data' --exclude='workspaces' --exclude='.local' \
    -czf /tmp/app.tgz .
gcloud compute scp /tmp/app.tgz whatsapp-agent:~/app.tgz --zone=us-central1-a
```

**Then SSH in and run:** gcloud compute ssh whatsapp-agent --zone=us-central1-a 
```bash
cd ~/app && tar -xzf ~/app.tgz
docker build -t whatsapp-agent .
docker stop whatsapp && docker rm whatsapp
docker run -d \
  --name whatsapp \
  --restart unless-stopped \
  --env-file ~/app/.env \
  -v ~/app/data:/app/data \
  -p 127.0.0.1:8000:8000 \
  whatsapp-agent
docker logs whatsapp --tail 20
```

## Update an env var / secret

On the VM:
```bash
nano ~/app/.env
# edit, Ctrl+O, Enter, Ctrl+X
docker stop whatsapp && docker rm whatsapp
docker run -d \
  --name whatsapp \
  --restart unless-stopped \
  --env-file ~/app/.env \
  -v ~/app/data:/app/data \
  -p 127.0.0.1:8000:8000 \
  whatsapp-agent
```

## Generate a fresh WhatsApp permanent token

If the token gets revoked or compromised:

1. Open https://business.facebook.com/settings/system-users
2. Click on `whatsapp-bot` system user
3. Click **Generate new token**
4. App: pick your WhatsApp app
5. Token expiration: **Never**
6. Permissions: check `whatsapp_business_messaging` AND `whatsapp_business_management`
7. Click **Generate token** → copy immediately (shown once)
8. Update `WHATSAPP_ACCESS_TOKEN` in `~/app/.env` on the VM (and locally)
9. Restart the container (see above)

## DNS — if the VM's external IP changes

GCP's external IP is *ephemeral* — it can change if the VM is stopped and restarted.

```bash
# Get current IP
gcloud compute instances describe whatsapp-agent \
  --zone=us-central1-a \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

Then update it at https://www.duckdns.org (paste IP into your domain row, click "update ip").

### Auto-heal DuckDNS every 5 minutes (recommended)

Run on VM after pulling this repo into `~/app`:

```bash
cd ~/app
chmod +x scripts/duckdns_update.sh scripts/install_duckdns_cron.sh
./scripts/install_duckdns_cron.sh homeos-bot <YOUR_DUCKDNS_TOKEN>
```

Quick verify:

```bash
~/app/scripts/duckdns_update.sh
tail -n 5 ~/.duckdns/duck.log
crontab -l | grep duckdns_update.sh
```

To make the IP permanent (~$3/mo if VM is ever stopped, free while running):
```bash
gcloud compute addresses create whatsapp-agent-ip --region=us-central1
gcloud compute instances delete-access-config whatsapp-agent --zone=us-central1-a
gcloud compute instances add-access-config whatsapp-agent \
  --zone=us-central1-a --address=<RESERVED_IP>
```

## Stop / start the VM

```bash
# Stop (no compute charges; disk still costs ~$1/mo from credits)
gcloud compute instances stop whatsapp-agent --zone=us-central1-a

# Start (gets a NEW external IP — must update DuckDNS unless using reserved IP)
gcloud compute instances start whatsapp-agent --zone=us-central1-a
```

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| `401 OAuthException code 190` in logs | WhatsApp access token expired | Regenerate System User token (see above) |
| `connection refused` on HTTPS test | Caddy down or container down | `sudo systemctl restart caddy && docker restart whatsapp` |
| `502 Bad Gateway` from Caddy | Container crashed | `docker logs whatsapp --tail 50` to see Python error |
| Webhook verification fails on Meta side | Verify token mismatch | Check `WHATSAPP_VERIFY_TOKEN` in `.env` matches what's typed in Meta dashboard |
| `Killed` during `docker build` | Out of memory on e2-micro | Add swap: `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile` |
| Gemini quota errors | Free tier = 20 req/day on `gemini-2.5-flash-lite` | Wait 24h, or enable billing in GCP for higher quota |
| Apple `tar: Ignoring xattr...` warnings on extract | Harmless macOS metadata | Ignore |

## Cost summary

| Item | Cost |
|---|---|
| `e2-micro` in `us-central1` | $0 (always-free tier, 1 instance) |
| 30 GB pd-standard disk | $0 (always-free tier covers up to 30 GB) |
| Public IP (while VM running) | $0 |
| Egress (1 GB/month free) | $0 for normal webhook traffic |
| Caddy + Let's Encrypt | $0 |
| DuckDNS subdomain | $0 |
| **Total** | **$0 / month** as long as the VM stays in the free tier |

## Files inventory

| Path | Purpose |
|---|---|
| `~/app/Dockerfile` | Container build recipe |
| `~/app/.env` | Secrets + config (NOT in image; loaded at runtime) |
| `~/app/data/tasks.db` | SQLite — all user data, tasks, expenses, groceries |
| `~/app/data/memory_fallback.db` | SQLite memory store (replaces Chroma) |
| `/etc/caddy/Caddyfile` | Caddy config (HTTPS reverse proxy) |

## Manage user records (in `tasks.db`)

The container has Python + sqlite3 baked in, so you can read/write the live DB via `docker exec` without needing sqlite CLI on the host. **Run these on the VM** (after `gcloud compute ssh whatsapp-agent --zone=us-central1-a`).

### List all users

```bash
docker exec whatsapp python -c "
import sqlite3
c = sqlite3.connect('/app/data/tasks.db')
for r in c.execute('SELECT user_id, default_currency, timezone, locale, onboarding_state FROM user_settings'):
    print(r)
"
```

### Find user(s) with a specific bad value (dry-run before deleting)

Replace the LIKE pattern with whatever you're hunting for.

```bash
docker exec whatsapp python -c "
import sqlite3
c = sqlite3.connect('/app/data/tasks.db')
rows = c.execute(\"SELECT user_id, timezone FROM user_settings WHERE timezone LIKE '%singapore%'\").fetchall()
print(rows)
"
```

### Fix one column for one user (e.g. correct a typo'd timezone)

```bash
docker exec whatsapp python -c "
import sqlite3
c = sqlite3.connect('/app/data/tasks.db')
c.execute(\"UPDATE user_settings SET timezone=? WHERE timezone=?\", ('Asia/Singapore', 'Aisa/singapore'))
c.commit()
print('updated', c.total_changes, 'row(s)')
"
```

### Wipe one user's account completely (so they can re-onboard)

Always run the dry-run SELECT above first to confirm you're targeting the right `user_id`. This deletes their settings, the household they created, their household memberships, and their conversation history.

```bash
docker exec whatsapp python -c "
import sqlite3
c = sqlite3.connect('/app/data/tasks.db')
rows = c.execute(\"SELECT user_id FROM user_settings WHERE timezone='Aisa/singapore'\").fetchall()
print('will delete:', rows)
for (uid,) in rows:
    c.execute('DELETE FROM user_settings WHERE user_id=?', (uid,))
    c.execute('DELETE FROM households WHERE created_by=?', (uid,))
    c.execute('DELETE FROM household_members WHERE user_id=?', (uid,))
    c.execute('DELETE FROM conversations WHERE user_id=?', (uid,))
c.commit()
print('done')
"
```

After wiping, re-onboard from WhatsApp:
1. Send `hello` (or anything) → bot welcomes you
2. Send `/claim admin2026` → bot grants admin
3. Reply with currency code: `SGD` / `INR` / `USD`
4. Reply with timezone: `Asia/Singapore` (must contain a `/`)
5. Reply with household size: a number

## SSH session tips

```text
Ctrl+Z          # accidentally suspended a process — sends it to background, paused
                # You'll see this when you 'exit' next:
                #   logout
                #   There are stopped jobs.

# How to recover:
jobs            # list the suspended jobs
fg              # bring the most recent one back to the foreground (then Ctrl+C to kill it)
kill %1         # or just kill job #1
exit            # second time around, ignores the warning and logs out

Ctrl+D          # cleaner than 'exit' — sends EOF, exits the shell directly
```

## Hard-reset (nuclear option)

If everything's broken and you want to start over:
```bash
docker stop whatsapp; docker rm whatsapp; docker rmi whatsapp-agent
cd ~/app && rm -rf .
# then re-upload from Mac and rebuild (see "Push a code change" section)
```

To preserve user data (`tasks.db`) across a rebuild, the volume mount `-v ~/app/data:/app/data` already handles that — `~/app/data` survives container deletion.
