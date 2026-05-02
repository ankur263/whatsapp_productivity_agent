#!/usr/bin/env sh
set -eu

# DuckDNS updater for ephemeral IP environments (e.g., GCP free-tier VM).
# Reads config from environment variables or ~/.duckdns/duck.env.
#
# Required:
#   DUCKDNS_DOMAIN=homeos-bot
#   DUCKDNS_TOKEN=<duckdns token>
#
# Optional:
#   DUCKDNS_LOGFILE=~/.duckdns/duck.log
#   DUCKDNS_TIMEOUT=10

DUCKDNS_DIR="${HOME}/.duckdns"
ENV_FILE="${DUCKDNS_DIR}/duck.env"
LOGFILE="${DUCKDNS_LOGFILE:-${DUCKDNS_DIR}/duck.log}"
TIMEOUT="${DUCKDNS_TIMEOUT:-10}"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

DOMAIN="${DUCKDNS_DOMAIN:-}"
TOKEN="${DUCKDNS_TOKEN:-}"

if [ -z "$DOMAIN" ] || [ -z "$TOKEN" ]; then
  echo "Missing DUCKDNS_DOMAIN or DUCKDNS_TOKEN. Set env vars or ${ENV_FILE}." >&2
  exit 1
fi

mkdir -p "$DUCKDNS_DIR"
touch "$LOGFILE"

URL="https://www.duckdns.org/update?domains=${DOMAIN}&token=${TOKEN}&ip="

# DuckDNS auto-detects source public IP when ip= is blank.
RESP="$(curl -fsS --max-time "$TIMEOUT" "$URL" || true)"
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

if [ "$RESP" = "OK" ]; then
  printf "%s OK\n" "$TS" >> "$LOGFILE"
  exit 0
fi

printf "%s FAIL response=%s\n" "$TS" "${RESP:-<empty>}" >> "$LOGFILE"
exit 1
