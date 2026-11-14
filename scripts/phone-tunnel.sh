#!/usr/bin/env bash
# Give a phone an HTTPS address for the web app, from any network.
#
#   scripts/phone-tunnel.sh          (needs: brew install cloudflared; the web app on port 3000)
#
# Why: a phone only allows the microphone, camera and motion sensors on HTTPS pages, and venue
# wifi often stops a phone reaching a laptop directly. This opens a Cloudflare quick tunnel to the
# web app (which proxies /api and /ws to the API, so one address is enough) and points the pairing
# QR code at it by setting NEXT_PUBLIC_PUBLIC_ORIGIN in apps/web/.env.local. Ctrl-C closes the
# tunnel and puts the old value back.
#
# The address is public while this runs: anyone who has it can reach the app and its API.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=apps/web/.env.local
PORT="${1:-3000}"
LOG="$(mktemp)"
old="$(grep -E '^NEXT_PUBLIC_PUBLIC_ORIGIN=' "$ENV_FILE" || true)"

set_origin() {
  if grep -qE '^NEXT_PUBLIC_PUBLIC_ORIGIN=' "$ENV_FILE"; then
    sed -i.bak -E "s|^NEXT_PUBLIC_PUBLIC_ORIGIN=.*|$1|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  else
    echo "$1" >> "$ENV_FILE"
  fi
}
cleanup() {
  kill "$pid" 2>/dev/null || true
  set_origin "${old:-NEXT_PUBLIC_PUBLIC_ORIGIN=}"
  rm -f "$LOG"
  echo; echo "Tunnel closed. Pairing address restored."
}

cloudflared tunnel --url "http://localhost:$PORT" >"$LOG" 2>&1 &
pid=$!
trap cleanup EXIT
trap 'exit 130' INT TERM
url=""
for _ in $(seq 1 40); do
  url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
  [ -n "$url" ] && break
  sleep 0.5
done
if [ -z "$url" ]; then echo "The tunnel did not start:"; cat "$LOG"; exit 1; fi
set_origin "NEXT_PUBLIC_PUBLIC_ORIGIN=$url"
echo "Phone address: $url"
echo "Reload the house page on the laptop, then open the pairing code and scan it."
wait "$pid"
