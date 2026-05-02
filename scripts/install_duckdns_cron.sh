#!/usr/bin/env sh
set -eu

# Installs a 5-minute cron job that updates DuckDNS with the VM's current public IP.
#
# Usage:
#   ./scripts/install_duckdns_cron.sh homeos-bot <duckdns-token>
#
# This creates:
#   ~/.duckdns/duck.env
#   ~/.duckdns/duck.log
# and installs this cron entry:
#   */5 * * * * $HOME/app/scripts/duckdns_update.sh >/dev/null 2>&1

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <duckdns-domain> <duckdns-token>" >&2
  exit 1
fi

DOMAIN="$1"
TOKEN="$2"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UPDATER="${ROOT_DIR}/scripts/duckdns_update.sh"

if [ ! -f "$UPDATER" ]; then
  echo "Updater script not found at: $UPDATER" >&2
  exit 1
fi

chmod +x "$UPDATER"

mkdir -p "${HOME}/.duckdns"
ENV_FILE="${HOME}/.duckdns/duck.env"
LOG_FILE="${HOME}/.duckdns/duck.log"

cat > "$ENV_FILE" <<EOF
DUCKDNS_DOMAIN=${DOMAIN}
DUCKDNS_TOKEN=${TOKEN}
DUCKDNS_LOGFILE=${LOG_FILE}
DUCKDNS_TIMEOUT=10
EOF
chmod 600 "$ENV_FILE"
touch "$LOG_FILE"

CRON_LINE="*/5 * * * * ${UPDATER} >/dev/null 2>&1"
TMP_CRON="$(mktemp)"
crontab -l 2>/dev/null | grep -v "duckdns_update.sh" > "$TMP_CRON" || true
echo "$CRON_LINE" >> "$TMP_CRON"
crontab "$TMP_CRON"
rm -f "$TMP_CRON"

echo "DuckDNS cron installed."
echo "Domain: ${DOMAIN}"
echo "Updater: ${UPDATER}"
echo "Env file: ${ENV_FILE}"
echo "Log file: ${LOG_FILE}"
echo "To verify now: ${UPDATER} && tail -n 5 ${LOG_FILE}"
