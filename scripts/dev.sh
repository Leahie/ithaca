#!/usr/bin/env bash
# Runs the whole app for local use: the API (hub, agents, twin) and the web app, both reachable
# from phones on the same Wi-Fi. Ctrl-C stops both.
#
#   scripts/dev.sh            # or: pnpm dev:all
#   LAN_IP=192.168.1.20 scripts/dev.sh     # if the address it picks is the wrong one
#   API_PORT=8001 WEB_PORT=3001 scripts/dev.sh
#   scripts/dev.sh --tunnel   # or: pnpm dev:all:tunnel. Phones on any network, over HTTPS (needs cloudflared)
#
# --tunnel opens a Cloudflare quick tunnel to the web app and points the QR code at it. Use it
# when a phone cannot reach this computer (a firewall here, or Wi-Fi that keeps devices apart:
# eduroam, hotel and venue networks), and for the phone's microphone, which only works on HTTPS.
# The web app proxies /api and /ws to the API, so one address is enough. The address is public
# while this runs: anyone who has it can reach the app and its API.
#
# The API reads keys from .env at the repo root (add ANTHROPIC_API_KEY for real agents; without
# it they are scripted). The web app reads apps/web/.env.local; this script sets the address the
# family QR code points at, so it follows your current Wi-Fi address.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TUNNEL="${TUNNEL:-}"
for arg in "$@"; do
  case "$arg" in
    --tunnel) TUNNEL=1 ;;
    *) echo "dev.sh: unknown option $arg (the only one is --tunnel)" >&2; exit 1 ;;
  esac
done

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
CLOUDFLARED=""
if [ -n "$TUNNEL" ]; then
  CLOUDFLARED="$(command -v cloudflared || true)"
  [ -z "$CLOUDFLARED" ] && [ -x "$HOME/.local/bin/cloudflared" ] && CLOUDFLARED="$HOME/.local/bin/cloudflared"
  [ -n "$CLOUDFLARED" ] || { echo "dev.sh: --tunnel needs cloudflared. Install it (pacman -S cloudflared, apt install cloudflared, brew install cloudflared), or put the binary from https://github.com/cloudflare/cloudflared/releases in ~/.local/bin." >&2; exit 1; }
fi

# Why a phone on the same Wi-Fi may still not get through: this computer's own firewall. Returns
# 0 (and prints the zone) when firewalld is running and turns away the web port on the Wi-Fi.
firewall_blocks() {
  command -v firewall-cmd >/dev/null 2>&1 || return 1
  [ "$(firewall-cmd --state 2>/dev/null)" = running ] || return 1
  local iface zone info
  iface="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -n1)"
  zone="$(firewall-cmd --get-zone-of-interface="$iface" 2>/dev/null || firewall-cmd --get-default-zone 2>/dev/null)"
  [ -n "$zone" ] || return 1
  info="$(firewall-cmd --zone="$zone" --list-all 2>/dev/null)" || return 1
  echo "$info" | grep -qE 'target: *ACCEPT' && return 1
  echo "$info" | grep -E '^ *ports:' | grep -qE "(^|[ :])$WEB_PORT/tcp" && return 1
  echo "$zone"
}

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
TUNNEL_PID=""
TUNNEL_LOG=""
cleanup() {
  trap - EXIT INT TERM
  echo
  echo "Stopping…"
  # Each runs in its own process group (setsid), so this also stops their children
  # (uvicorn's reloader, Next's workers).
  for pid in "$TUNNEL_PID" "$WEB_PID" "$API_PID"; do
    [ -n "$pid" ] && kill -TERM -- "-$pid" 2>/dev/null || true
  done
  [ -n "$TUNNEL_LOG" ] && rm -f "$TUNNEL_LOG"
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# --- API ------------------------------------------------------------------------------------------
echo "Starting the API on port $API_PORT (the first run installs Python packages, about a minute)…"
# OBJECT_SEGMENTER=local (in the environment or .env) needs torch and friends; nothing else does.
SEGMENT_REQS=()
if [ "${OBJECT_SEGMENTER:-}" = local ] || grep -qsE '^OBJECT_SEGMENTER="?local' .env; then
  SEGMENT_REQS=(--with-requirements services/api/objects/requirements-segment.txt)
fi
setsid uv run --quiet --no-project \
  --with-requirements services/api/requirements-b.txt \
  --with-requirements services/api/twin/requirements.txt \
  "${SEGMENT_REQS[@]}" \
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

# --- tunnel ---------------------------------------------------------------------------------------
# Before the web app: the address goes into its bundle (NEXT_PUBLIC_PUBLIC_ORIGIN), for the QR code.
PHONE_ORIGIN="http://$LAN_IP:$WEB_PORT"
if [ -n "$TUNNEL" ]; then
  echo "Opening a Cloudflare tunnel to the web app…"
  TUNNEL_LOG="$(mktemp)"
  # 127.0.0.1: the web app listens on IPv4 only.
  setsid "$CLOUDFLARED" tunnel --url "http://127.0.0.1:$WEB_PORT" >"$TUNNEL_LOG" 2>&1 &
  TUNNEL_PID=$!
  url=""
  for _ in $(seq 1 60); do
    url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n1 || true)"
    [ -n "$url" ] && break
    kill -0 "$TUNNEL_PID" 2>/dev/null || break
    sleep 0.5
  done
  [ -n "$url" ] || { echo "dev.sh: the tunnel did not start:" >&2; cat "$TUNNEL_LOG" >&2; exit 1; }
  PHONE_ORIGIN="$url"
fi

# --- web ------------------------------------------------------------------------------------------
echo "Starting the web app on port $WEB_PORT…"
# The browser opens the live connection on the page's host at port 8000 by itself; another API
# port has to be named.
WS_BASE=""
[ "$API_PORT" != 8000 ] && WS_BASE="ws://$LAN_IP:$API_PORT"
# This script runs the real API, so the web app must use it whatever apps/web/.env.local says.
NEXT_PUBLIC_USE_FIXTURES=0 \
NEXT_PUBLIC_PUBLIC_ORIGIN="$PHONE_ORIGIN" \
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

if [ -n "$TUNNEL" ]; then
  PHONE_HELP="  Phones: any network. On the big screen open the house, press Family, scan the code.
  The tunnel address is public while this runs: anyone who has it can reach the app and its API."
else
  PHONE_HELP="  Phones: join the same Wi-Fi. On the big screen open the house, press Family, scan the code.
  If a phone can't load the page, run this with --tunnel (any network, and HTTPS for the phone's
  microphone). Or stay on the Wi-Fi: allow ports $WEB_PORT and $API_PORT through the firewall, and on a
  network that keeps devices apart (eduroam, hotel Wi-Fi) use Tailscale: LAN_IP=<tailscale IP> scripts/dev.sh"
  if ZONE="$(firewall_blocks)"; then
    PHONE_HELP="$PHONE_HELP

  ! This computer's firewall (firewalld, zone \"$ZONE\") turns away port $WEB_PORT: no phone can reach
    the address above. Use --tunnel, or open the ports:
      sudo firewall-cmd --zone=$ZONE --add-port=$WEB_PORT/tcp --add-port=$API_PORT/tcp"
  fi
fi

cat <<INFO

  Ithaca is running.
    Big screen:   http://localhost:$WEB_PORT   (the front page; "Step inside" opens the house)
    Phones:       $PHONE_ORIGIN   (the family QR code points here)
    API:          http://$LAN_IP:$API_PORT/api/health
    Agents:       ${AGENT:-unknown}$([ "${AGENT:-}" = scripted ] && echo "  (add ANTHROPIC_API_KEY to .env for real agents)")

$PHONE_HELP
  Ctrl-C stops everything.

INFO

# When either one stops, stop the other.
wait -n "$API_PID" "$WEB_PID" ${TUNNEL_PID:+"$TUNNEL_PID"} || true
