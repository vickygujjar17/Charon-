# Changelog

All notable changes to Charon are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every commit subject starts with the version it ships, for example
`v1.0.1: fix ladder re-arm after a rejected modify`.

## [1.0.3] - 2026-10-24

An optional dashboard, built on a file bridge rather than an HTTP one. The EA is
unchanged in what it decides: it still owns every order, stop and size. Everything
new here is a window and a very small number of switches.

### Added

- **`CharonBridge.mqh`** — optional, off by default (`InpBridgeEnable = false`).
  Writes a JSON snapshot to `Common\Files\Charon\state_<SYMBOL>.json` once per timer
  tick and drains `commands\*.json`, one command per tick. Both directions rename a
  temp file into place, so neither side can read a half-written document.
- **Consume-once command handling.** A command is renamed into
  `commands\processed\` as `ok_*` or `rejected_*` *before* it is applied, so a crash
  between read and apply cannot replay it. Nothing is deleted — the directory is an
  audit trail of what the EA accepted and refused, and why.
- **Anti-replay window.** A command stamped more than 60s in the past or future is
  refused. `CHARON_BRIDGE_SCHEMA` must match on both sides.
- **Mutable trailing mode.** `CCharonTrail` gained `m_mode` / `SetMode()` / `Mode()`,
  seeded from `InpTrailMode` at `Init()`. The eight previous direct reads of the input
  now read the live mode, which is what lets the dashboard change it. Default
  behaviour is byte-for-byte unchanged.
- **`bridge/` — a FastAPI service** (`charon_bridge` package): snapshot reader with
  staleness verdicts, command writer, ring-buffer log, and an optional read-only
  MetaTrader5 diagnostic adapter.
- **Endpoints.** `GET /api/health`, `/api/status`, `/api/positions`, `/api/terminal`,
  `/api/logs` (JSON or SSE), `/api/commands`; `POST /api/pyramid/arm`,
  `/api/pyramid/disarm`, `/api/trail`.
- **`POST /api/trade` always returns `405`** with the reason. It exists so that
  someone assuming the bridge can trade gets a straight answer instead of a 404.
- **Tailwind dashboard** at `/`, with the stylesheet **precompiled and vendored**
  (`static/tailwind.css`, 12 KB) — no CDN, so the panel renders identically offline.
  A sticky banner turns amber past 5s of snapshot age and red past 30s.
- **`_tools/bridge_check.ps1`** — offline Python gate: ruff, pytest, and explicit
  assertions that no bridge module references an MT5 order function, that
  `/api/trade` still refuses, and that the CSS is vendored rather than linked.
- **72 Python tests**, including `test_ladder.py` (a missing ladder must surface as
  `null`, never as a recomputed price) and a live-server suite that starts uvicorn
  and speaks HTTP to it.

### Changed

- `_tools/bump_version.py` now also rewrites `BRIDGE_VERSION` in
  `bridge/charon_bridge/version.py` and fails `--check` when the three version
  strings disagree.
- Self-test assertions: 98 → 135 (18 command-vocabulary, 19 JSON helper incl. non-ASCII
  escaping). The bridge self-tests assert what is **refused**: `open`, `close`, `modify`,
  `buy`, `sell`, `size` and `ARM` are all rejected.
- `build_check.ps1` compiles 10 headers (was 9) at 0 errors / 0 warnings.

### Design notes

- **Files, not sockets.** MQL5 has no `SocketListen`/`SocketAccept`, and an EA cannot
  be called into. A shared directory is the only channel both sides can use without
  a bridge DLL, and `FILE_COMMON` needs no `WebRequest` allow-listing.
- **The verb list is the security boundary.** `arm`, `disarm`, `trail_mode`, `ping`.
  Both sides whitelist independently, so neither can widen the set alone. There is no
  verb that can express an order.
- **The ladder is never recomputed.** Python and the browser render the EA's
  `RrLadderText()` string verbatim, so a rung on the dashboard cannot disagree with
  the rung on the chart.
- **Loopback by default**, optional `X-Charon-Token` when `CHARON_TOKEN` is set.
  `/api/health` and `/api/logs` stay open so a supervisor or an `EventSource` can
  reach them.

### Limitations

- The bridge is **optional**: the EA runs standalone, and every trading feature above
  works with `InpBridgeEnable = false`.
- The dashboard's log is a bridge-side transcript. The terminal's Experts log remains
  the authoritative trading record.
- One bridge process per terminal is assumed; per-symbol snapshot names prevent two
  charts clobbering each other, but two bridges would race on the command inbox.



Two additions on top of 1.0.1: a manual trigger for the pyramid ladder, and an RR ladder
readout on the panel. Everything that 1.0.1 already did — risk sizing, strict stops, RR
targets, trailing, automatic tiering — is unchanged.

### Added

- **Manual pyramid trigger.** New `InpPyramidTrigger` (`AUTO` / `MANUAL`). In `MANUAL`
  mode the R trigger is necessary but not sufficient: the trader must arm the ladder first
  by pressing `InpPyramidArmKey` (default `A`). One arm authorises exactly one tier and is
  consumed when that leg opens. `InpPyramidArmTimeoutSec` (default 60, `0` = never) expires
  an unused arm. Arming satisfies only the trigger gate — max-legs, cooldown, spread and
  basket-risk ceilings still apply, so an arm can never force an exposure add that the risk
  model would refuse.
- **RR ladder readout.** New `InpRrLadderA/B/C` (default `1.5R / 2.0R / 3.0R`) project up
  to three R rungs as prices, shown for the basket (from its average entry and the primary
  leg's R) and per ticket (from that leg's own entry and R). Display only — it never writes
  a take profit. A rung set to `0` is hidden.
- Dashboard rows: `Pyramid trig` (mode plus live arm state) and `RR ladder`.
- `_tools` unaffected; `build_check.ps1` still gates 0 errors / 0 warnings on both entry
  points.

### Changed

- `CCharonStops::RPrice()`, previously unused, now backs the ladder readout. Prices are
  normalised to symbol digits so the panel matches broker-acceptable values.
- The primary-leg R lookup in `MaybePyramid()` was extracted into
  `CCharonEngine::BasketRefRisk()` and shared with the ladder, so the trigger and the
  display can never disagree about what 1R means.
- Self-test assertions: 73 → 98 (15 ladder, 10 arm-expiry).

### Notes

- `#property version` stays `"1.00"` because MetaEditor's numeric form is `major.minor`
  only; `1.0.2 → 1.00`. `CHARON_VERSION` carries the full `1.0.2`.
- A tiered lot decay such as `0.5 → 0.3 → 0.15` needs no new input: set
  `InpPyramidT2Mult = 0.60`, `T3 = 0.30`, `T4 = 0.15`.

## [1.0.1] - 2026-10-24

Documentation and tooling accuracy pass. No change to trading behaviour; the
compiled EA is functionally identical to 1.0.0.

### Fixed

- `_tools/bump_version.py` ignored the `VERSION` file, so it could silently drift
  out of step with `CHARON_VERSION`. It is now read, verified in `--check` mode and
  rewritten on a bump.
- Corrected the self-test assertion count in `README.md` and `CHANGELOG.md` to the
  actual figure, verified by counting every `Check(` call site.

## [1.0.0] - 2026-10-24

First release. The scope is deliberately narrow: Charon manages stops, risk and
escalation for positions a human opened. It contains no entry logic of any kind.

### Added

**Strict stop loss**
- Adoption of manually opened positions, with or without an existing stop.
- An existing correct stop is respected and its distance becomes 1R.
- Missing, zero, or wrong-sided stops are re-armed within one tick.
- Three stop units: points, ATR multiple, percent of entry price.
- Broker minimum stop distance is always respected, with a configurable extra buffer.
- `InpMissingSlAction` decides behaviour when no valid stop can be placed:
  attach, flatten, or log only.
- Every tick re-verifies that a valid stop still exists on the broker.

**Trailing stop engine**
- Four modes: fixed points, ATR multiple, high-water-mark percentage giveback, and
  R-lock with a fixed R giveback.
- Activation by R multiple, by points gained, or immediately.
- Per-position high-water mark so trails survive pullbacks without loosening.
- Configurable minimum improvement step before a modify is sent.
- A single improvement gate guarantees stops only ever tighten.

**RR / targets**
- Reward:risk targets computed from 1R, or fixed-point targets.
- Optional target written at adoption time.
- Target is never placed inside the broker's stop level.

**Risk model**
- Three sizing modes: risk percent of account, fixed money risk, fixed lots.
- Volume is rounded down to the broker step and re-verified against the exact
  applied money risk, so the budget is never overshot.
- Round-turn commission per lot is folded into the sizing calculation.
- Hard ceiling (`InpMaxRiskPct`): oversized adopted positions are flagged.
- Unsizable setups are reported rather than silently rounded up.

**Pyramiding**
- Up to three scale-in legs (four tiers total) with per-tier R triggers and
  lot multipliers.
- Six ordered gates: enabled, max legs, trigger, direction clarity, cooldown, spread.
- Basket risk ceiling (`InpPyramidMaxRiskPct`) re-checked with the new leg included.
- Every leg is opened with its strict stop already attached in the same request,
  so the basket is never unprotected.
- Optional collective breakeven across all legs before adding exposure.

**Protection ladder**
- Breakeven at a configurable R trigger (default 1R).
- Optional half-risk stage at 0.5R: halve the risk, or move to breakeven early.
- All ladder moves pass the same no-loosening gate as trailing.

**State and observability**
- Per-ticket state table (ticket, entry, 1R, high-water mark, tier, latches).
- Optional persistence in terminal global variables, keyed by ticket, so restarts
  and timeframe changes keep the R reference and the trail.
- Chart dashboard: configuration summary, basket aggregate, per-ticket R progress
  and live stop.
- Verbose logging with the reason for every refusal.
- `OnTradeTransaction` adoption so a new manual position is picked up immediately.

**Tooling**
- `_tools/build_check.ps1`: staged MetaEditor compile gate requiring 0 errors and
  0 warnings, compiling both the EA and the self-test, with link-provenance
  verification that every header came from the staging tree.
- `_tools/bump_version.py`: keeps `CHARON_VERSION` and every `#property version`
  in lockstep; `--check` mode fails CI on drift.
- `CharonSelfTest.mq5`: 73 assertions over the pure arithmetic, with no trades and
  no market data required.

### Safety defaults

Trailing off, pyramiding disabled, targets off, half-risk ladder off, missing stop
attaches. Nothing moves a stop or opens a trade until explicitly enabled.
