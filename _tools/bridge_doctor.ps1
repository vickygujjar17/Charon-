# bridge_doctor.ps1 -- read-only deployment diagnosis for the Charon bridge
#
# The "no Charon snapshot found" symptom has several unrelated causes, and
# they are indistinguishable from inside the dashboard:
#
#   * the EA is not enabled            (InpBridgeEnable = false)
#   * MT5 is not running               (no terminal64 process)
#   * the bridge resolves a different  (CHARON_COMMON_DIR unset on a VPS,
#     directory than the terminal's      so it falls back to ~/MetaQuotes)
#   * the terminal is on another       (a file transport cannot cross it)
#     machine entirely
#   * the tunnel is pointed at a       (ngrok -> :8000 while the app binds
#     port nothing is listening on       :8787)
#
# This script reports on all of them and changes nothing. It reads files,
# asks the OS for processes, and makes at most one loopback HTTP request.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File _tools\bridge_doctor.ps1
#   ... -BridgeUrl http://127.0.0.1:8787

param(
    [string]$Bridge = "",
    [string]$BridgeUrl = "http://127.0.0.1:8787"
)

$ErrorActionPreference = "Continue"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Bridge)) { $Bridge = Join-Path $repoRoot "bridge" }

function Section([string]$title) {
    Write-Host ""
    Write-Host "--- $title "
    Write-Host ("-" * 68)
}

Write-Host "=================================================================="
Write-Host "CHARON BRIDGE DOCTOR  (read-only)"
Write-Host "=================================================================="

# --- 1) which directory does the bridge resolve? -------------------------
# Mirrors charon_bridge/paths.py: override, then %APPDATA%, then home.
Section "resolution (mirrors charon_bridge/paths.py)"
$override = $env:CHARON_COMMON_DIR
if ($override) {
    $common = $override
    Write-Host "  source        : CHARON_COMMON_DIR (explicit override)"
} elseif ($env:APPDATA) {
    $common = Join-Path $env:APPDATA "MetaQuotes\Terminal\Common\Files"
    Write-Host "  source        : %APPDATA%"
} else {
    $common = Join-Path $HOME "MetaQuotes\Terminal\Common\Files"
    Write-Host "  source        : home fallback -- NO %APPDATA%, no Wine prefix" -ForegroundColor Yellow
    Write-Host "                  This is the split-deployment signature: a Linux VPS or" -ForegroundColor Yellow
    Write-Host "                  bare container resolves a directory no terminal writes to." -ForegroundColor Yellow
}
$root = Join-Path $common "Charon"

Write-Host "  common files  : $common  [$(if (Test-Path $common) { 'exists' } else { 'MISSING' })]"
Write-Host "  bridge root   : $root  [$(if (Test-Path $root) { 'exists' } else { 'MISSING' })]"
Write-Host "  commands      : $(Join-Path $root 'commands')  [$(if (Test-Path (Join-Path $root 'commands')) { 'exists' } else { 'MISSING' })]"
Write-Host "  state_path    : $(Join-Path $root 'state_<SYMBOL>.json')"

# --- 1b) is the bridge itself even installed here? ---------------------
Section "bridge install  $Bridge"
$venvPython = Join-Path $Bridge ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Host "  venv          : present"
} else {
    Write-Host "  venv          : MISSING -- nothing can be running from this checkout" -ForegroundColor Yellow
    Write-Host "  -> create it: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Yellow
}
$versionFile = Join-Path $Bridge "charon_bridge\version.py"
if (Test-Path $versionFile) {
    $versionLine = Select-String -Path $versionFile -Pattern '^BRIDGE_VERSION\s*=' | Select-Object -First 1
    if ($versionLine) { Write-Host ("  version.py    : {0}" -f $versionLine.Line.Trim()) }
}


# --- 2) is anything publishing? ----------------------------------------
Section "snapshots"
if (Test-Path $root) {
    $states = @(Get-ChildItem $root -Filter "state_*.json" -File -ErrorAction SilentlyContinue)
    if ($states.Count -eq 0) {
        Write-Host "  none. The EA has never published to this directory." -ForegroundColor Yellow
    } else {
        $now = Get-Date
        foreach ($f in $states) {
            $age = [int]($now - $f.LastWriteTime).TotalSeconds
            $verdict = if ($age -le 5) { "live" } elseif ($age -le 30) { "stale" } else { "offline" }
            $colour = if ($verdict -eq "live") { "Green" } elseif ($verdict -eq "stale") { "Yellow" } else { "Red" }
            Write-Host ("  {0,-28} {1,6}s  {2}" -f $f.Name, $age, $verdict) -ForegroundColor $colour
        }
    }
} else {
    Write-Host "  bridge root does not exist, so there is nothing to scan." -ForegroundColor Yellow
    Write-Host "  If the bridge has ever started here, ensure_dirs() would have made it." -ForegroundColor Yellow
    Write-Host "  -> the bridge has not run on this machine." -ForegroundColor Yellow
}

# --- 3) is the terminal even up? ---------------------------------------
Section "MetaTrader5 terminal"
$term = @(Get-Process -Name "terminal64", "terminal" -ErrorAction SilentlyContinue)
if ($term.Count -eq 0) {
    Write-Host "  no terminal64 process is running" -ForegroundColor Yellow
} else {
    $term | ForEach-Object { Write-Host ("  running: {0} (pid {1})" -f $_.ProcessName, $_.Id) }
}
$origin = Join-Path $env:APPDATA "MetaQuotes\Terminal"
if (Test-Path $origin) {
    Get-ChildItem $origin -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "Common" } |
        ForEach-Object {
            $log = Join-Path $_.FullName "logs\metaeditor.log"
            $when = if (Test-Path $log) { (Get-Item $log).LastWriteTime } else { $null }
            $ageText = if ($when) { "{0:N0}d ago" -f ((Get-Date) - $when).TotalDays } else { "no log" }
            Write-Host ("  terminal {0}  last activity {1}" -f $_.Name.Substring(0, [Math]::Min(12, $_.Name.Length)), $ageText)
        }
}

# --- 4) is the bridge listening, and on which port? --------------------
Section "bridge listener (default CHARON_PORT=8787)"
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 8000 -and $_.LocalPort -le 9100 } |
    Sort-Object LocalPort -Unique)
if ($listeners.Count -eq 0) {
    Write-Host "  nothing is listening on 8000-9100" -ForegroundColor Yellow
    Write-Host "  -> a tunnel pointed at this host would answer 502" -ForegroundColor Yellow
} else {
    $listeners | ForEach-Object {
        $proc = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Write-Host ("  {0}:{1}  pid {2}  {3}" -f $_.LocalAddress, $_.LocalPort, $_.OwningProcess, $proc)
    }
}

# --- 5) does it answer, and what does it say? --------------------------
Section "loopback probe  $BridgeUrl/api/health"
try {
    $health = Invoke-RestMethod -Uri "$BridgeUrl/api/health" -TimeoutSec 4 -ErrorAction Stop
    Write-Host ("  bridge_version: {0}   schema: {1}" -f $health.bridge_version, $health.schema)
    Write-Host ("  common_files  : {0} [{1}]" -f $health.locations.common_files, $health.locations.common_exists)
    Write-Host ("  bridge_root   : {0} [{1}]" -f $health.locations.bridge_root, $health.locations.root_exists)
    $syms = @($health.symbols)
    if ($syms.Count -eq 0) {
        Write-Host "  symbols       : none -- nothing is publishing" -ForegroundColor Yellow
    } else {
        Write-Host ("  symbols       : {0}" -f ($syms -join ", "))
    }
} catch {
    Write-Host "  no answer: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  -> the bridge is not running here, or not on this port" -ForegroundColor Yellow
}

# --- 6) the EA gate ----------------------------------------------------
Section "EA gate (InpBridgeEnable)"
$defines = Join-Path $repoRoot "MQL5\Include\Charon\CharonDefines.mqh"
if (-not (Test-Path $defines)) { $defines = Join-Path $repoRoot "CharonDefines.mqh" }
if (Test-Path $defines) {
    $hit = Select-String -Path $defines -Pattern 'InpBridgeEnable\s*=\s*(true|false)' |
        Select-Object -First 1
    if ($hit) {
        $default = $hit.Matches[0].Groups[1].Value
        $colour = if ($default -eq "false") { "Yellow" } else { "Green" }
        Write-Host ("  {0}:{1}  default = {2}" -f (Split-Path $defines -Leaf), $hit.LineNumber, $default) -ForegroundColor $colour
        if ($default -eq "false") {
            Write-Host "  -> non-stock: the source default is false, so a chart must set it true" -ForegroundColor Yellow
            Write-Host "     or the EA publishes NOTHING" -ForegroundColor Yellow
        } else {
            Write-Host "  -> stock default since v1.2.0. A chart attached earlier keeps its" -ForegroundColor DarkGray
            Write-Host "     saved value, so check the chart's inputs too" -ForegroundColor DarkGray
        }
    }
} else {
    Write-Host "  could not locate CharonDefines.mqh" -ForegroundColor Yellow
}

# --- verdict -----------------------------------------------------------
Write-Host ""
Write-Host "=================================================================="
Write-Host "The transport is a shared folder, not a network protocol."
Write-Host "The bridge and the EA must see the SAME directory on the SAME host."
Write-Host "If MT5 runs on one machine and the bridge on another, no code fix helps."
Write-Host "=================================================================="

