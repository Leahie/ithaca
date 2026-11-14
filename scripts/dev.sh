#!/usr/bin/env bash
# Runs the whole app for local use: the API (hub, agents, twin) and the web app, both reachable
# from phones on the same Wi-Fi. Ctrl-C stops both.
#
#   scripts/dev.sh            # or: pnpm dev:all
#   LAN_IP=192.168.1.20 scripts/dev.sh     # if the address it picks is the wrong one
#   API_PORT=8001 WEB_PORT=3001 scripts/dev.sh
#
# The API reads keys from .env at the repo root (add ANTHROPIC_API_KEY for real agents; without
# it they are scripted). The web app reads apps/web/.env.local; this script sets the address the
# family QR code points at, so it follows your current Wi-Fi address.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"

# This computer's address on the network: the one the default route goes out of.
lan_ip() {
  ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1 && return
  hostname -I 2>/dev/null | awk '{print $1}'
}
LAN_IP="${LAN_IP:-$(lan_ip)}"
LAN_IP="${LAN_IP:-localhost}"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "dev.sh: $1 is not installed. $2" >&2; exit 1; }
}
need uv "Install it from https://docs.astral.sh/uv/"
need pnpm "Install it with: corepack enable"

for port in "$API_PORT" "$WEB_PORT"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "dev.sh: port $port is already in use. Stop whatever runs there (an earlier dev server?) or pick another port, e.g. WEB_PORT=3001." >&2
    exit 1
  fi
done

if [ ! -d apps/web/node_modules ]; then
  echo "Installing web dependencies…"
  pnpm install
fi

API_PID=""
WEB_PID=""
cleanup() {
  trap - EXIT INT TERM
  echo
  echo "Stopping…"
  # Each runs in its own process group (setsid), so this also stops their children
  # (uvicorn's reloader, Next's workers).
  for pid in "$WEB_PID" "$API_PID"; do
    [ -n "$pid" ] && kill -TERM -- "-$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- API ------------------------------------------------------------------------------------------
echo "Starting the API on port $API_PORT (the first run installs Python packages, about a minute)…"
setsid uv run --quiet --no-project \
  --with-requirements services/api/requirements-b.txt \
  --with-requirements services/api/twin/requirements.txt \
  uvicorn services.api.main:app --host 0.0.0.0 --port "$API_PORT" \
  --reload --reload-dir services --reload-dir packages \
  > >(sed -u 's/^/[api] /') 2>&1 &
API_PID=$!

for _ in $(seq 1 180); do
  if curl -fsS "http://localhost:$API_PORT/api/health" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$API_PID" 2>/dev/null; then echo "dev.sh: the API stopped while starting; see the [api] lines above." >&2; exit 1; fi
  sleep 1
done
curl -fsS "http://localhost:$API_PORT/api/health" >/dev/null 2>&1 || { echo "dev.sh: the API did not answer within 3 minutes." >&2; exit 1; }
AGENT="$(curl -fsS "http://localhost:$API_PORT/api/health" | sed -n 's/.*"agent_provider":"\([a-z]*\)".*/\1/p')"

# --- web ------------------------------------------------------------------------------------------
echo "Starting the web app on port $WEB_PORT…"
# The browser opens the live connection on the page's host at port 8000 by itself; another API
# port has to be named.
WS_BASE=""
[ "$API_PORT" != 8000 ] && WS_BASE="ws://$LAN_IP:$API_PORT"
NEXT_PUBLIC_PUBLIC_ORIGIN="http://$LAN_IP:$WEB_PORT" \
DEV_ALLOWED_ORIGINS="$LAN_IP" \
API_PROXY_TARGET="http://localhost:$API_PORT" \
NEXT_PUBLIC_WS_BASE="$WS_BASE" \
  setsid pnpm --dir apps/web exec next dev -H 0.0.0.0 -p "$WEB_PORT" \
  > >(sed -u 's/^/[web] /') 2>&1 &
WEB_PID=$!

for _ in $(seq 1 60); do
  curl -fsS -o /dev/null "http://localhost:$WEB_PORT" 2>/dev/null && break
  kill -0 "$WEB_PID" 2>/dev/null || { echo "dev.sh: the web app stopped while starting; see the [web] lines above." >&2; exit 1; }
  sleep 1
done

cat <<INFO

  Ithaca is running.
    Big screen:   http://localhost:$WEB_PORT   (on this computer: the Matterport key only allows localhost)
    Phones:       http://$LAN_IP:$WEB_PORT   (the family QR code points here)
    API:          http://$LAN_IP:$API_PORT/api/health
    Agents:       ${AGENT:-unknown}$([ "${AGENT:-}" = scripted ] && echo "  (add ANTHROPIC_API_KEY to .env for real agents)")

  Phones: join the same Wi-Fi, open the house, press Family, scan the code.
  If a phone can't load the page: allow ports $WEB_PORT and $API_PORT through the firewall, or, on a
  network that keeps devices apart (eduroam, hotel Wi-Fi), use Tailscale: LAN_IP=<tailscale IP> scripts/dev.sh
  Ctrl-C stops everything.

INFO

# When either one stops, stop the other.
wait -n "$API_PID" "$WEB_PID" || true
