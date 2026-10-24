# integration_check.ps1 -- Charon loopback integration gate
#
# The bridge is only half of a two-process conversation. bridge_check.ps1
# proves the Python is sound and that no order path exists; this gate proves
# the two halves still talk to each other:
#
#   POST /api/pyramid/arm  ->  commands/*.json  ->  EA drains it
#                          ->  commands/processed/ok_*.json
#                          ->  GET /api/commands reports "accepted"
#
# It runs the real suite in bridge/tests/test_integration.py, which starts an
# actual uvicorn process against a throwaway Common\Files tree. Nothing here
# touches the network beyond 127.0.0.1, installs a package, or writes to the
# real terminal folder.
#
# The EA half is played by bridge/tests/fake_ea.py, because CI has no
# MetaTrader terminal. That substitution is honest and documented: the bridge,
# the HTTP surface and every file crossing the filesystem are real; only the
# EA's side of the handshake is simulated.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File _tools\integration_check.ps1

param(
    [string]$Bridge = "",
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Bridge)) { $Bridge = Join-Path $repoRoot "bridge" }

$venvPython = Join-Path $Bridge ".venv\Scripts\python.exe"
$suite = Join-Path $Bridge "tests\test_integration.py"

Write-Host "=================================================================="
Write-Host "CHARON INTEGRATION GATE"
Write-Host "=================================================================="
Write-Host "bridge   : $Bridge"
Write-Host "python   : $(if (Test-Path $venvPython) { $venvPython } else { 'MISSING' })"
Write-Host "suite    : $suite"
Write-Host ""

$failures = New-Object System.Collections.Generic.List[string]

if (-not (Test-Path $venvPython)) {
    Write-Host "FATAL: no virtualenv at $venvPython"
    Write-Host "  create it with:"
    Write-Host "    cd bridge"
    Write-Host "    python -m venv .venv"
    Write-Host "    .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt"
    exit 2
}

if (-not (Test-Path $suite)) {
    Write-Host "FATAL: $suite is missing"
    exit 2
}

# --- 0) prerequisite: the client must be structurally sound --------------
# The integration suite drives HTTP, but a dead app.js would make the panel
# useless even if every endpoint answered. Cheap to check, so check it first.
Write-Host "--- prerequisite: client script structure ---------------------"
Push-Location $Bridge
try {
    & $venvPython -m pytest tests/test_client_script.py -q 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { $failures.Add("static/app.js is structurally unsound") }
} finally {
    Pop-Location
}

# --- 1) the loopback suite ----------------------------------------------
Write-Host ""
Write-Host "--- loopback integration --------------------------------------"
Push-Location $Bridge
try {
    & $venvPython -m pytest tests/test_integration.py -q 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { $failures.Add("integration suite reported failures") }
} finally {
    Pop-Location
}

# --- 2) invariant: the suite really starts a server ---------------------
# A skipped suite is a green suite that proves nothing. If uvicorn never
# starts, the tests would silently pass by being collected and skipped, so
# assert the harness is present and actually subprocess-launched.
Write-Host ""
Write-Host "--- invariant: the suite launches a real server ---------------"
$harness = Get-Content $suite -Raw
if ($harness -notmatch 'uvicorn' -or $harness -notmatch 'subprocess\.Popen') {
    Write-Host "  FAIL: test_integration.py no longer starts a real uvicorn process"
    $failures.Add("integration suite does not launch a server")
} else {
    Write-Host "  ok: the suite subprocess-launches uvicorn, not an in-process app"
}

# --- 3) invariant: the fake EA mirrors the real contract ----------------
Write-Host ""
Write-Host "--- invariant: fake EA mirrors CharonBridge.mqh ---------------"
$fakePath = Join-Path $Bridge "tests\fake_ea.py"
$bridgePath = Join-Path $repoRoot "CharonBridge.mqh"
if (-not (Test-Path $fakePath)) {
    Write-Host "  FAIL: tests\fake_ea.py is missing"
    $failures.Add("fake EA harness missing")
} elseif (Test-Path $bridgePath) {
    $fake = Get-Content $fakePath -Raw
    $mqh = Get-Content $bridgePath -Raw
    $missing = @()
    # --- the anti-replay window and the verb list must not drift apart
    if ($mqh -match 'CHARON_BRIDGE_CMD_MAX_AGE\s+(\d+)') {
        $window = $Matches[1]
        if ($fake -notmatch "CMD_MAX_AGE\s*=\s*$window") {
            $missing += "CMD_MAX_AGE ($window) does not match CharonBridge.mqh"
        }
    }
    foreach ($verb in @("arm", "disarm", "trail_mode", "ping")) {
        if ($mqh -match "CHARON_BRIDGE_V_.*\s+`"$verb`"" -and $fake -notmatch "`"$verb`"") {
            $missing += "verb '$verb' missing from the fake EA"
        }
    }
    if ($missing.Count -gt 0) {
        Write-Host "  FAIL: the fake EA has drifted from the EA:"
        $missing | ForEach-Object { Write-Host "    - $_" }
        $failures.Add("fake EA drifted from CharonBridge.mqh")
    } else {
        Write-Host "  ok: verb list and anti-replay window agree with CharonBridge.mqh"
    }
}

# --- verdict ------------------------------------------------------------
Write-Host ""
Write-Host "=================================================================="
Write-Host "VERDICT"
Write-Host "=================================================================="
if ($failures.Count -eq 0) {
    Write-Host "*** INTEGRATION GATE PASSED ***"
    exit 0
}
Write-Host "*** INTEGRATION GATE FAILED ***"
$failures | ForEach-Object { Write-Host "  - $_" }
exit 1
