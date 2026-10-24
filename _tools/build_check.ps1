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
# Charon's entry uses SIBLING-RELATIVE includes ("CharonDefines.mqh"), which
# MQL5 resolves against the including file's own directory. The headers must
# therefore sit in the same folder as the .mq5 -- wherever that folder is.
#
# v1.0.6: the gate used to stage everything FLAT in MQL5\Experts, a layout the
# documentation never told anyone to use. That made the gate pass while a real
# install failed with "file 'Experts\CharonDefines.mqh' not found": a user who
# copied Charon.mq5 without the headers beside it still got a green build here.
# We now stage exactly the documented install layout -- MQL5\Experts\Charon\ --
# so the gate validates the path users actually take.
if (Test-Path $buildRoot) { Remove-Item $buildRoot -Recurse -Force }
$deploy = Join-Path $experts "Charon"
New-Item -ItemType Directory -Force -Path $deploy, $stageInc | Out-Null

Copy-Item (Join-Path $Source "*.mqh") $deploy -Force
Copy-Item (Join-Path $Source "*.mq5") $deploy -Force

$charonModules = Get-ChildItem (Join-Path $Source "*.mqh") |
    ForEach-Object { $_.BaseName } | Sort-Object

if ($charonModules.Count -eq 0) {
    Write-Host "FATAL: no Charon headers found in $Source"
    exit 2
}

# --- Deployment completeness --------------------------------------------
# Every header plus both entry points must land in ONE folder. A partial copy
# is the failure mode this gate exists to make impossible, so verify the staged
# set against the source set rather than trusting Copy-Item.
$srcMql = @(Get-ChildItem (Join-Path $Source "*.mq*") -File | ForEach-Object { $_.Name } | Sort-Object)
$stgMql = @(Get-ChildItem (Join-Path $deploy "*.mq*") -File | ForEach-Object { $_.Name } | Sort-Object)
$missingFiles = @($srcMql | Where-Object { $stgMql -notcontains $_ })
if ($missingFiles.Count -gt 0) {
    Write-Host "FATAL: staging is incomplete, missing: $($missingFiles -join ', ')"
    exit 2
}
$charonSelfTest = Join-Path $Source "CharonSelfTest.mq5"
if ((Test-Path $charonSelfTest) -and ($stgMql -notcontains "CharonSelfTest.mq5")) {
    Write-Host "FATAL: CharonSelfTest.mq5 was not staged beside the headers"
    exit 2
}
Write-Host ("deploy    : {0} ({1} MQL5 files, headers beside the entry point)" -f $deploy, $stgMql.Count)
Write-Host ""

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
    $entryPath = Join-Path $deploy $ep
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

$ex5 = @(Get-ChildItem $deploy -Filter "*.ex5" -File -ErrorAction SilentlyContinue)

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

