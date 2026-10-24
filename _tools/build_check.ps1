# build_check.ps1 -- Charon strict compilation gate
#
# Stages a temporary MQL5 tree that mirrors the terminal layout, compiles via
# the MetaEditor CLI, verifies every Charon header was linked from the staging
# tree (never from a live terminal Include folder), and fails unless the result
# is exactly 0 errors AND 0 warnings.
#
# Nothing is ever deployed: the .ex5 lands in %TEMP%, never in MQL5\Experts.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File _tools\build_check.ps1

param(
    [string]$Source = "",
    [string]$MetaEditor = "C:\Program Files\Five Percent Online MetaTrader 5\MetaEditor64.exe",
    [string]$Entry = "Charon.mq5"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ([string]::IsNullOrWhiteSpace($Source)) {
    $Source = $repoRoot
}

$buildRoot = Join-Path $env:TEMP "charon_build"
$mql5Root  = Join-Path $buildRoot "MQL5"
$experts   = Join-Path $mql5Root "Experts"
$stageInc  = Join-Path $mql5Root "Include"
$includes  = Join-Path $stageInc "Charon"
$logPath   = Join-Path $buildRoot "compile.log"

Write-Host "=================================================================="
Write-Host "CHARON COMPILATION GATE"
Write-Host "=================================================================="
Write-Host "source    : $Source"
Write-Host "metaeditor: $MetaEditor"
Write-Host "entry     : $Entry"
Write-Host "buildRoot : $buildRoot"
Write-Host ""

if (-not (Test-Path $MetaEditor)) {
    Write-Host "FATAL: MetaEditor not found at $MetaEditor"
    exit 2
}
if (-not (Test-Path (Join-Path $Source $Entry))) {
    Write-Host "FATAL: entry point not found: $(Join-Path $Source $Entry)"
    exit 2
}

# --- Stage a clean tree -------------------------------------------------
# Layout must mirror a real terminal so the compiler resolves includes, and
# /inc must point at the staging MQL5 folder so MetaEditor never reads the
# live terminal's headers.
#
# Charon's entry uses RELATIVE includes ("CharonDefines.mqh"), so the headers
# are staged next to the .mq5 inside Experts. The provenance check below then
# proves every header really came from this staging tree.
if (Test-Path $buildRoot) { Remove-Item $buildRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $experts, $stageInc | Out-Null

Copy-Item (Join-Path $Source "*.mqh") $experts -Force
Copy-Item (Join-Path $Source "*.mq5") $experts -Force

$charonModules = Get-ChildItem (Join-Path $Source "*.mqh") |
    ForEach-Object { $_.BaseName } | Sort-Object

if ($charonModules.Count -eq 0) {
    Write-Host "FATAL: no Charon headers found in $Source"
    exit 2
}

# --- Stock framework includes so <Trade/...> resolves -------------------
# Only the official include tree is copied, never a Charon folder.
$candidates = New-Object System.Collections.Generic.List[string]
$terminalRoot = Join-Path $env:APPDATA "MetaQuotes\Terminal"
if (Test-Path $terminalRoot) {
    Get-ChildItem $terminalRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $inc = Join-Path $_.FullName "MQL5\Include"
        if (Test-Path (Join-Path $inc "Trade\Trade.mqh")) { $candidates.Add($inc) }
    }
}
$candidates.Add((Join-Path $env:APPDATA "MetaQuotes\Terminal\Common\MQL5\Include"))
$candidates.Add("C:\Program Files\Five Percent Online MetaTrader 5\MQL5\Include")

$found = $false
foreach ($cand in $candidates) {
    if (Test-Path (Join-Path $cand "Trade\Trade.mqh")) {
        Get-ChildItem $cand -Force | Where-Object { $_.Name -ne "Charon" } |
            ForEach-Object { Copy-Item $_.FullName $stageInc -Recurse -Force -ErrorAction SilentlyContinue }
        Write-Host "framework : $cand"
        $found = $true
        break
    }
}
if (-not $found) {
    Write-Host "FATAL: stock MQL5 Include tree (Trade\Trade.mqh) not found"
    exit 2
}

# --- Compile: EA, then the self-test script ------------------------------
# Both entry points are held to the same 0 errors / 0 warnings standard. The
# script is staged next to the headers so its relative includes resolve.
$entryPoints = @($Entry)
$scriptSrc = Join-Path $Source "CharonSelfTest.mq5"
if (Test-Path $scriptSrc) { $entryPoints += "CharonSelfTest.mq5" }

$allLines = New-Object System.Collections.Generic.List[string]
foreach ($ep in $entryPoints) {
    $entryPath = Join-Path $experts $ep
    if (-not (Test-Path $entryPath)) {
        $allLines.Add("FATAL: staged entry missing: $entryPath")
        continue
    }
    $logFor = Join-Path $buildRoot ("compile_" + [System.IO.Path]::GetFileNameWithoutExtension($ep) + ".log")
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & $MetaEditor /compile:"$entryPath" /inc:"$mql5Root" /log:"$logFor" | Out-Null
    $sw.Stop()
    Write-Host ("compile {0,-22} {1,6:N0} ms" -f $ep, $sw.Elapsed.TotalMilliseconds)

    if (-not (Test-Path $logFor)) { $allLines.Add("FATAL: no log for $ep"); continue }
    $bytes = [System.IO.File]::ReadAllBytes($logFor)
    if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
        $txt = [System.Text.Encoding]::Unicode.GetString($bytes, 2, $bytes.Length - 2)
    } else {
        $txt = [System.Text.Encoding]::UTF8.GetString($bytes)
    }
    foreach ($l in ($txt -split "`r?`n")) { $allLines.Add($l.TrimEnd("`r")) }
}
Write-Host ""

$logPath = Join-Path $buildRoot "compile.log"
$lines = $allLines.ToArray()

# --- Link provenance ----------------------------------------------------
# MetaEditor logs "including <path>" for every header it pulls in. Each Charon
# header must appear AND must resolve inside the staging tree, otherwise the
# gate would be validating headers from somewhere other than this commit.
$linked = @{}
foreach ($l in $lines) {
    if ($l -match "including\s+(.+\.mqh)\s*$") {
        $p = $Matches[1].Trim()
        $linked[(Split-Path $p -Leaf)] = $p
    }
}

Write-Host "=================================================================="
Write-Host "LINK PROVENANCE (every Charon header must come from the staging tree)"
Write-Host "=================================================================="
$provenanceBad = @()
foreach ($m in $charonModules) {
    $leaf = "$m.mqh"
    if (-not $linked.ContainsKey($leaf)) {
        Write-Host ("  {0,-24} [NOT LINKED]" -f $leaf)
        $provenanceBad += "$leaf : not reported by the compiler"
        continue
    }
    $p = $linked[$leaf]
    $fromStage = $p.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)
    Write-Host ("  {0,-24} {1} {2}" -f $leaf, $(if ($fromStage) { "[STAGED]  " } else { "[EXTERNAL]" }), $p)
    if (-not $fromStage) { $provenanceBad += "$leaf : linked from OUTSIDE staging -> $p" }
}
Write-Host ""

# --- Diagnostics --------------------------------------------------------
$errors = @()
$warnings = @()
$summary = ""
foreach ($line in $lines) {
    $t = $line.Trim()
    if ($t -match "(^|:\s)error\s+\d+:")   { $errors   += $t; continue }
    if ($t -match "(^|:\s)warning\s+\d+:") { $warnings += $t; continue }
    if ($t -match "^\d+\s+error\(s\)")     { $summary += $t + " " }
    if ($t -match "^\d+\s+warning\(s\)")   { $summary += $t + " " }
    if ($t -match "^Result:")              { $summary += $t }
}

Write-Host "=================================================================="
Write-Host "COMPILER LOG (full)"
Write-Host "=================================================================="
foreach ($line in $lines) {
    if ($line.Trim() -ne "") { Write-Host $line }
}
Write-Host ""

$ex5 = @(Get-ChildItem $experts -Filter "*.ex5" -File -ErrorAction SilentlyContinue)

Write-Host "=================================================================="
Write-Host "VERDICT"
Write-Host "=================================================================="
Write-Host ("modules   : {0}" -f $charonModules.Count)
Write-Host ("errors    : {0}" -f $errors.Count)
Write-Host ("warnings  : {0}" -f $warnings.Count)
if ($ex5.Count -gt 0) {
    foreach ($b in $ex5) { Write-Host ("binary    : {0} ({1} bytes)" -f $b.Name, $b.Length) }
} else { Write-Host "binary    : NOT PRODUCED" }
if ($summary) { Write-Host ("summary   : {0}" -f $summary.Trim()) }
Write-Host ""

if ($errors.Count -gt 0) {
    Write-Host "ERRORS:"
    $errors | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
}
if ($warnings.Count -gt 0) {
    Write-Host "WARNINGS:"
    $warnings | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
}
if ($provenanceBad.Count -gt 0) {
    Write-Host "PROVENANCE FAILURES:"
    $provenanceBad | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
}

if ($errors.Count -eq 0 -and $warnings.Count -eq 0 -and $ex5.Count -gt 0 -and $provenanceBad.Count -eq 0) {
    Write-Host "*** GATE PASSED: 0 errors, 0 warnings, all headers staged ***"
    exit 0
}
Write-Host "*** GATE FAILED ***"
exit 1

