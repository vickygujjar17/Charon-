# bridge_check.ps1 -- Charon Python bridge gate
#
# Mirrors the discipline of build_check.ps1 for the Python side: a clean,
# repeatable, offline check that fails loudly. Verifies the venv exists,
# lints with ruff, runs pytest, then proves the two invariants that matter
# most are still enforced:
#
#   * no Python module references an MT5 order function
#   * POST /api/trade still refuses with 405
#
# Nothing here touches the network: no package is installed, no wheel is
# downloaded. If the venv is missing this script says so and stops.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File _tools\bridge_check.ps1

param(
    [string]$Bridge = "",
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($Bridge)) { $Bridge = Join-Path $repoRoot "bridge" }

$venvPython = Join-Path $Bridge ".venv\Scripts\python.exe"

Write-Host "=================================================================="
Write-Host "CHARON BRIDGE GATE"
Write-Host "=================================================================="
Write-Host "bridge   : $Bridge"
Write-Host "python   : $(if (Test-Path $venvPython) { $venvPython } else { 'MISSING' })"
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

# --- 1) lint ------------------------------------------------------------
Write-Host "--- ruff -------------------------------------------------------"
& $venvPython -m ruff check "$Bridge\charon_bridge" "$Bridge\main.py" "$Bridge\tests" 2>&1 |
    ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { $failures.Add("ruff reported problems") }

# --- 2) tests -----------------------------------------------------------
Write-Host ""
Write-Host "--- pytest ----------------------------------------------------"
Push-Location $Bridge
try {
    & $venvPython -m pytest tests -q 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { $failures.Add("pytest reported failures") }
} finally {
    Pop-Location
}

# --- 3) invariant: no order path in Python ------------------------------
# mt5_adapter.py is excluded from THIS text scan on purpose: it documents the
# prohibited function names in a FORBIDDEN_CALLS tuple, which a grep cannot
# distinguish from a real call. The pytest suite asserts that invariant
# properly, by parsing the module with ast and checking every referenced
# name -- so the documentation is skipped here and the AST test is the real
# guard. Every other module must be clean by simple inspection.
Write-Host ""
Write-Host "--- invariant: Python originates no orders --------------------"
$orderPattern = "order_send|order_check|positions_modify|positions_close"
$orderHits = @(Get-ChildItem "$Bridge\charon_bridge" -Filter *.py -File |
    Where-Object { $_.Name -ne "mt5_adapter.py" } |
    Select-String -Pattern $orderPattern |
    Where-Object { $_.Line -notmatch '^\s*#' })

if ($orderHits.Count -gt 0) {
    Write-Host "  FAIL: an MT5 order call appears in the bridge:"
    $orderHits | ForEach-Object { Write-Host ("    {0}:{1} {2}" -f $_.Filename, $_.LineNumber, $_.Line.Trim()) }
    $failures.Add("order call found in charon_bridge")
} else {
    Write-Host "  ok: no order call in any bridge module (mt5_adapter checked by AST test)"
}

# --- 4) invariant: /api/trade refuses ----------------------------------
Write-Host ""
Write-Host "--- invariant: POST /api/trade returns 405 --------------------"
$tradeGuard = @(Select-String -Path (Join-Path $Bridge "main.py") -Pattern '"/api/trade"' -SimpleMatch)
$status405 = @(Select-String -Path (Join-Path $Bridge "main.py") -Pattern "status_code=405")
if ($tradeGuard.Count -eq 0 -or $status405.Count -eq 0) {
    Write-Host "  FAIL: /api/trade no longer refuses with 405"
    $failures.Add("/api/trade 405 guard missing")
} else {
    Write-Host "  ok: /api/trade keeps its 405 refusal"
}

# --- 5) invariant: the dashboard is vendored, not a CDN link -----------
Write-Host ""
Write-Host "--- invariant: dashboard CSS is vendored ----------------------"
$indexPath = Join-Path $Bridge "static\index.html"
$cssPath   = Join-Path $Bridge "static\tailwind.css"
if (-not (Test-Path $cssPath)) {
    Write-Host "  FAIL: static\tailwind.css is missing"
    $failures.Add("vendored stylesheet missing")
} else {
    $cdn = @(Select-String -Path $indexPath -Pattern "cdn\.|unpkg\.|jsdelivr" -ErrorAction SilentlyContinue)
    if ($cdn.Count -gt 0) {
        Write-Host "  FAIL: dashboard references a CDN"
        $cdn | ForEach-Object { Write-Host ("    line {0}: {1}" -f $_.LineNumber, $_.Line.Trim()) }
        $failures.Add("dashboard references a CDN")
    } else {
        Write-Host ("  ok: vendored stylesheet ({0} bytes), no CDN references" -f (Get-Item $cssPath).Length)
    }
}

# --- verdict ------------------------------------------------------------
Write-Host ""
Write-Host "=================================================================="
Write-Host "VERDICT"
Write-Host "=================================================================="
if ($failures.Count -eq 0) {
    Write-Host "*** BRIDGE GATE PASSED ***"
    exit 0
}
Write-Host "*** BRIDGE GATE FAILED ***"
$failures | ForEach-Object { Write-Host "  - $_" }
exit 1
