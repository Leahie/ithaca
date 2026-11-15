# Windows version of scripts/dev.sh: runs the API (hub, agents, twin) and the web app, both
# reachable from phones on the same Wi-Fi. Ctrl-C stops both.
#
#   pnpm dev:win                                  # or: powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
#   $env:LAN_IP = "192.168.1.20"; pnpm dev:win    # if the address it picks is the wrong one
#   $env:API_PORT = 8001; $env:WEB_PORT = 3001; pnpm dev:win
#   pnpm dev:tunnel                               # phones on any network, over HTTPS (needs cloudflared)
#
# The API reads keys from .env at the repo root (add ANTHROPIC_API_KEY for real agents; without
# it they are scripted). The web app reads apps/web/.env.local; this script sets the address the
# family QR code points at, so it follows your current Wi-Fi address.
#
# -Tunnel opens a Cloudflare quick tunnel to the web app (as scripts/phone-tunnel.sh does) and
# points the QR code at it. A phone needs HTTPS for the microphone, camera and motion sensors, and
# venue Wi-Fi often keeps it from reaching the laptop. The web app proxies /api and /ws to the API,
# so one address is enough. The address is public while this runs: anyone who has it can reach
# the app and its API.
param([switch]$Tunnel)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ApiPort = if ($env:API_PORT) { [int]$env:API_PORT } else { 8000 }
$WebPort = if ($env:WEB_PORT) { [int]$env:WEB_PORT } else { 3000 }

# This computer's address on the network: the one the default route goes out of.
function Get-LanIp {
  $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
  if (-not $route) { return $null }
  (Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress
}
$LanIp = if ($env:LAN_IP) { $env:LAN_IP } else { Get-LanIp }
if (-not $LanIp) { $LanIp = 'localhost' }

function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Write-Error "dev.ps1: $cmd is not installed. $hint" }
}
Need uv 'Install it from https://docs.astral.sh/uv/'
Need pnpm 'Install it with: corepack enable pnpm (or, without admin rights: corepack enable pnpm --install-directory "$env:USERPROFILE\.local\bin")'
if ($Tunnel) { Need cloudflared 'Install it with: winget install Cloudflare.cloudflared (or put cloudflared-windows-amd64.exe from its GitHub releases on PATH as cloudflared.exe)' }

foreach ($port in $ApiPort, $WebPort) {
  if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
    Write-Error "dev.ps1: port $port is already in use. Stop whatever runs there (an earlier dev server?) or pick another port, e.g. `$env:WEB_PORT = 3001."
  }
}

if (-not (Test-Path apps/web/node_modules)) {
  Write-Host 'Installing web dependencies...'
  pnpm install
  if ($LASTEXITCODE) { exit $LASTEXITCODE }
}

# The variables set for the web app are restored on exit: a script run with .\ shares the session.
$WebEnv = @{
  NEXT_PUBLIC_USE_FIXTURES  = '0'  # this script runs the real API, whatever apps/web/.env.local says
  NEXT_PUBLIC_PUBLIC_ORIGIN = "http://${LanIp}:$WebPort"
  DEV_ALLOWED_ORIGINS       = $LanIp
  API_PROXY_TARGET          = "http://localhost:$ApiPort"
  # The browser opens the live connection on the page's host at port 8000 by itself; another API
  # port has to be named.
  NEXT_PUBLIC_WS_BASE       = if ($ApiPort -ne 8000) { "ws://${LanIp}:$ApiPort" } else { '' }
}
$SavedEnv = @{}
foreach ($k in @($WebEnv.Keys) + 'PYTHONUNBUFFERED') { $SavedEnv[$k] = [Environment]::GetEnvironmentVariable($k) }

# Each server runs in its own hidden console, its output going to log files that are echoed here
# with a prefix. On Windows, uvicorn's reloader restarts its worker with a Ctrl-C to its whole
# console; in a shared console that would also stop the web app and this script.
$LogDir = Join-Path ([IO.Path]::GetTempPath()) "ithaca-dev-$PID"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Tails = New-Object System.Collections.Generic.List[object]

function Start-Server($name, $file, $arguments, [switch]$Quiet) {
  $out = Join-Path $LogDir "$name.out.log"
  $err = Join-Path $LogDir "$name.err.log"
  $p = Start-Process $file -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
  if (-not $Quiet) { foreach ($path in $out, $err) { $Tails.Add([pscustomobject]@{ Prefix = "[$name] "; Path = $path; Offset = 0L; Partial = '' }) } }
  $p
}

function Write-Logs {
  foreach ($t in $Tails) {
    if (-not (Test-Path $t.Path)) { continue }
    $fs = [IO.File]::Open($t.Path, 'Open', 'Read', 'ReadWrite')
    try {
      $n = $fs.Length - $t.Offset
      if ($n -le 0) { continue }
      $buf = New-Object byte[] $n
      [void]$fs.Seek($t.Offset, 'Begin')
      $n = $fs.Read($buf, 0, $n)
      $t.Offset += $n
      $lines = ($t.Partial + [Text.Encoding]::UTF8.GetString($buf, 0, $n)) -split "`r?`n"
      $t.Partial = $lines[-1]
      if ($lines.Count -gt 1) { foreach ($line in $lines[0..($lines.Count - 2)]) { Write-Host "$($t.Prefix)$line" } }
    }
    finally { $fs.Dispose() }
  }
}

function Wait-WithLogs($ms) { Write-Logs; Start-Sleep -Milliseconds $ms }

$Api = $null
$Web = $null
$Cloudflared = $null
$PhoneOrigin = "http://${LanIp}:$WebPort"
try {
  # --- tunnel -------------------------------------------------------------------------------------
  if ($Tunnel) {
    Write-Host 'Opening a Cloudflare tunnel to the web app...'
    # 127.0.0.1: the web app listens on IPv4 only.
    $Cloudflared = Start-Server tunnel cloudflared @('tunnel', '--url', "http://127.0.0.1:$WebPort") -Quiet
    $tunnelLog = Join-Path $LogDir 'tunnel.err.log'
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
      if ($Cloudflared.HasExited) { break }
      $m = Select-String -Path $tunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($m) { $url = $m.Matches[0].Value } else { Start-Sleep -Milliseconds 500 }
    }
    if (-not $url) { Get-Content $tunnelLog -ErrorAction SilentlyContinue | Write-Host; Write-Error 'dev.ps1: the tunnel did not start.' }
    $PhoneOrigin = $url
    # The browser keeps NEXT_PUBLIC_PUBLIC_ORIGIN in the bundle, so it is set before the web app starts.
    $WebEnv.NEXT_PUBLIC_PUBLIC_ORIGIN = $url
  }

  # --- API ----------------------------------------------------------------------------------------
  Write-Host "Starting the API on port $ApiPort (the first run installs Python packages, about a minute)..."
  # Python 3.12, as in infra/api.Dockerfile.
  $apiArgs = @('run', '--quiet', '--no-project', '--python', '3.12',
    '--with-requirements', 'services/api/requirements-b.txt',
    '--with-requirements', 'services/api/twin/requirements.txt')
  # OBJECT_SEGMENTER=local (in the environment or .env) needs torch and friends; nothing else does.
  if ($env:OBJECT_SEGMENTER -eq 'local' -or (Select-String -Path .env -Pattern '^OBJECT_SEGMENTER="?local' -Quiet -ErrorAction SilentlyContinue)) {
    $apiArgs += @('--with-requirements', 'services/api/objects/requirements-segment.txt')
  }
  $apiArgs += @('uvicorn', 'services.api.main:app', '--host', '0.0.0.0', '--port', "$ApiPort",
    '--reload', '--reload-dir', 'services', '--reload-dir', 'packages')
  $env:PYTHONUNBUFFERED = '1'
  $Api = Start-Server api uv $apiArgs

  # 127.0.0.1, not localhost: localhost tries ::1 first, and Windows takes about 2 s to refuse it
  # (the servers listen on IPv4 only).
  $health = $null
  for ($i = 0; $i -lt 180 -and -not $health; $i++) {
    if ($Api.HasExited) { Write-Logs; Write-Error 'dev.ps1: the API stopped while starting; see the [api] lines above.' }
    try { $health = Invoke-RestMethod "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 2 } catch { Wait-WithLogs 1000 }
  }
  if (-not $health) { Write-Error 'dev.ps1: the API did not answer within 3 minutes.' }

  # --- web ----------------------------------------------------------------------------------------
  Write-Host "Starting the web app on port $WebPort..."
  foreach ($k in $WebEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $WebEnv[$k]) }
  # pnpm.cmd (npm, corepack) or pnpm.exe (standalone); Start-Process cannot run the .ps1 shim.
  $pnpm = (Get-Command pnpm -CommandType Application | Select-Object -First 1).Source
  $Web = Start-Server web $pnpm @('--dir', 'apps/web', 'exec', 'next', 'dev', '-H', '0.0.0.0', '-p', "$WebPort")

  $up = $false
  for ($i = 0; $i -lt 60 -and -not $up; $i++) {
    if ($Web.HasExited) { Write-Logs; Write-Error 'dev.ps1: the web app stopped while starting; see the [web] lines above.' }
    Write-Logs
    try { Invoke-WebRequest "http://127.0.0.1:$WebPort" -UseBasicParsing -TimeoutSec 30 | Out-Null; $up = $true } catch { Wait-WithLogs 1000 }
  }
  Write-Logs

  $agent = $health.agent_provider
  $agentHint = if ($agent -eq 'scripted') { '  (add ANTHROPIC_API_KEY to .env for real agents)' } else { '' }
  $phoneHelp = if ($Tunnel) {
    "  Phones: any network. Open the house, press Family or Connect a phone, scan the code.`n  The tunnel address is public while this runs."
  } else {
    "  Phones: join the same Wi-Fi, open the house, press Family, scan the code.`n  If a phone can't load the page: allow ports $WebPort and $ApiPort through Windows Firewall, or, on a`n  network that keeps devices apart (eduroam, hotel Wi-Fi), use pnpm dev:tunnel."
  }
  Write-Host @"

  Ithaca is running.
    Big screen:   http://localhost:$WebPort   (the front page; "Step inside" opens the house)
    Phones:       $PhoneOrigin   (the QR codes point here)
    API:          http://${LanIp}:$ApiPort/api/health
    Agents:       $agent$agentHint

$phoneHelp
  Ctrl-C stops everything.

"@

  # When any one stops, stop the others.
  while (-not $Api.HasExited -and -not $Web.HasExited -and -not ($Cloudflared -and $Cloudflared.HasExited)) { Wait-WithLogs 300 }
  foreach ($s in @(@('API', $Api), @('web app', $Web), @('tunnel', $Cloudflared))) {
    if ($s[1] -and $s[1].HasExited) { Write-Host "dev.ps1: the $($s[0]) stopped." }
  }
}
finally {
  Write-Logs
  Write-Host "`nStopping..."
  # /T also stops their children (uvicorn's reloader and worker, Next's workers).
  foreach ($p in $Web, $Api, $Cloudflared) {
    if ($p -and -not $p.HasExited) { taskkill /T /F /PID $p.Id 2>&1 | Out-Null }
  }
  foreach ($k in $SavedEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $SavedEnv[$k]) }
  Remove-Item -Recurse -Force $LogDir -ErrorAction SilentlyContinue
}
