# GCP Ephemeral IP Auto-Updater Plan (DuckDNS)

## The Problem
Google Cloud Platform (GCP) free-tier VMs (`e2-micro`) use ephemeral public IP addresses. Whenever the VM is stopped and restarted, or if Google performs underlying network maintenance, your server's public IP address changes.

When the IP changes, your DuckDNS domain (`homeos-bot.duckdns.org`) still points to the old, dead IP, which causes Meta's webhooks to fail and your WhatsApp bot to stop responding.

## The Solution
We will create a lightweight background job (a `cron` task) on your Linux VM.
Every 5 minutes, this job will quietly wake up, ping the DuckDNS servers, and say: "I am still here, update my domain to my current IP."

DuckDNS automatically detects the source IP of the request if the `ip=` parameter is left blank, so the script doesn't even need to look up its own IP!

## Step-by-Step Implementation Guide

### 1. SSH into your VM
First, connect to your Google Cloud VM from your Mac terminal:
```bash
gcloud compute ssh whatsapp-agent --zone=us-central1-a
```

### 2. Create the Updater Script
Run this entire block of code in your VM terminal. 
**IMPORTANT:** Before hitting enter, replace `YOUR_DUCKDNS_TOKEN` with your actual token from the DuckDNS dashboard!

```bash
# Create a dedicated folder for the script
mkdir -p ~/duckdns

# Create the script file (duck.sh)
cat > ~/duckdns/duck.sh <<'EOF'
#!/bin/sh
echo url="https://www.duckdns.org/update?domains=homeos-bot&token=YOUR_DUCKDNS_TOKEN&ip=" | curl -s -K - >> ~/duckdns/duck.log
EOF

# Make the script executable
chmod +x ~/duckdns/duck.sh
```

### 3. Add the Cron Job (Background Worker)
Now, register the script with Linux's built-in background task scheduler (`cron`) to run every 5 minutes:

```bash
(crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1") | crontab -
```

Once you run that final command, the IP fix is fully implemented! You will never have to manually update your DuckDNS dashboard again. If your server ever reboots and gets a new IP, this script will automatically heal the connection within 5 minutes.