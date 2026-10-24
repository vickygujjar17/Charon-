# Changelog

All notable changes to Charon are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every commit subject starts with the version it ships, for example
`v1.0.1: fix ladder re-arm after a rejected modify`.

## [1.0.5] - 2026-10-24

A polish release. No trading behaviour changed and no MQL5 logic was touched — every
change here is in the bridge, its tests, or the documentation. The headline fix is an
audit trail that reported events out of order.

### Fixed

- **`GET /api/commands` reported the audit trail in the wrong order.** The EA retires a
  consumed command as `ok_<fifo>.json` or `rejected_<fifo>.json`, and `recent()` sorted
  on the whole filename. Because every name now begins with the verdict, the sort key was
  the *verdict* rather than the time: an `ok_` from 02:35 sorted ahead of a `rejected_`
  from 02:40, and vice versa, so the trail could claim a later rejection happened before
  an earlier acceptance. Ordering is now by verdict time, with the verdict prefix stripped
  before it is used as the tiebreak. Covered by
  `test_recent_orders_by_verdict_time_not_by_verdict_prefix`.
- **`recent()` could return `500` mid-retire.** It called `stat()` on every file it had
  just globbed, but the EA moves files out of `processed/` concurrently, so a file could
  vanish between listing and stat and raise an unhandled `OSError`. A vanished file is no
  longer an audit entry — it is skipped. Covered by
  `test_recent_tolerates_a_file_retired_mid_listing`.
- **A republish could briefly report a healthy EA as offline.** `read_snapshot()` tested
  for the snapshot exactly once. The EA republishes by renaming a temp file into place; if
  it unlinks before it moves, a reader sampling that instant saw no file at all and the
  dashboard flashed `offline` for a live terminal. The existence check now retries a few
  times over ~50 ms. The retry still ends in `StateUnavailable`, so a genuinely stopped
  EA is reported as stopped rather than waited on.
- **`_read_json()` slept after its final failed attempt.** The 50 ms backoff now sits only
  *between* attempts, removing dead latency from the common "terminal not started" path.
- **`CHARON_HOST` and `CHARON_PORT` did nothing.** Both were documented in
  `.env.example` and the configuration table, but `main.py` never read them: `uvicorn
  main:app` ignores those names, so a trader who moved the panel to another port followed
  the docs and saw no effect. `python main.py` now honours both, defaulting to
  `127.0.0.1:8787`, and rejects a malformed or out-of-range port with a clear message
  instead of a traceback.
- **The startup hook used a deprecated API.** `@app.on_event("startup")` is deprecated and
  emitted a `DeprecationWarning` on every boot; it is now a `lifespan` handler. Verified
  against a live uvicorn process: the process starts, serves `/api/health`, and logs no
  deprecation.

### Changed

- **Documented why there is no file lock**, in `bridge/README.md`. The channel is safe by
  construction — temp file plus `fsync` plus atomic `os.replace`, `uuid4` in every name, a
  temp suffix that is not `*.json` so the EA's glob cannot see it — and a lock file would
  add a worse failure mode (a crashed process leaving a stale lock that silently blocks
  every later command). Stated explicitly so the absence is a decision, not an oversight.

### Documentation

- `README.md`: added `integration_check.ps1` to the gate block and the `_tools/` tree, and
  described what the two-process gate actually proves. Corrected the claim that
  `CHARON_TOKEN` covers all of `/api/*` — `/api/health` and `/api/logs` stay open by
  design, and the text now says why. Corrected `bump_version.py` coverage from four
  literals to the six it has checked since v1.0.4, and named them. Added the missing
  `bridge/` files (`src/input.css`, `tailwind.config.js`, `package.json`,
  `test_integration.py`) to the tree.
- `bridge/README.md`: test count corrected (72 → 112), the Layout block completed, and the
  quick start now shows the entry point that honours `CHARON_HOST`/`CHARON_PORT`.

## [1.0.4] - 2026-10-24

A repair release. The dashboard shipped in v1.0.3 was structurally dead: it loaded,
it parsed, and it never spoke to the bridge. Nothing about the EA's trading logic
changed, and no MQL5 behaviour was touched.

### Fixed

- **The dashboard never polled `/api/status`.** A truncating edit in v1.0.3 left
  `renderLadder()` unclosed, so every subsequent function — including the `boot()`
  call — was nested inside it. The file was valid JavaScript, so `node --check`
  passed and the page appeared to load, but no request was ever made: the panel sat
  at *connecting* with empty cells. `static/app.js` has been rewritten, and the
  functions are now siblings at top level as they were always meant to be.
- **The v1.0.3 gate could not see this.** `node --check` proves a file *parses*, not
  that it *runs*, and the live-server test only grepped for the string
  `"renderLadder"`. Both passed on a dead page. `test_client_script.py` now checks
  structure: braces balance to zero, every `function` is declared at top level,
  nothing follows the final `boot()`, and every `$("id")` exists in `index.html`.
  Two meta-tests feed a deliberately broken sample through the same analyzer, so the
  gate cannot be refactored into a no-op.
- **`bump_version.py --check` missed two release literals.** `bridge/package.json`
  and the README's `Current release:` line were never verified. v1.0.3 shipped with
  the README still claiming v1.0.2; the check now catches that, and both are bumped
  automatically.
- **The log tail repeated itself on every reconnect.** `/api/logs` sent no SSE
  `id:`, so `EventSource` had nothing to resume from and replayed the last 50 lines
  each time the connection hiccuped. Frames now carry `id:`, `Last-Event-ID` is
  honoured, a `retry: 3000` hint bounds the backoff, and the client de-duplicates on
  `seq` as well.

### Added

- **`tests/test_integration.py` — a real loopback integration suite.** A real uvicorn
  process against a real `Common\Files` tree: `POST /api/pyramid/arm` → a file in
  `commands/` → drained and retired as `ok_*` → `GET /api/commands` reporting the
  verdict. It also covers FIFO ordering, `trail_mode` actually changing the EA's
  mode, backdated/future-dated/wrong-symbol rejection, a poison command failing to
  block the inbox, the staleness ladder live → stale → offline → live, and SSE
  resume. 15 tests.
- **`tests/fake_ea.py` — a stand-in for the EA.** There is no MetaTrader terminal in
  CI, so this impersonates `CCharonBridge` faithfully: case-insensitive FIFO
  `NextPending()`, the verb whitelist, `CHARON_BRIDGE_SCHEMA` matching, the ±60s
  anti-replay window, invalid files retired rather than left blocking, and
  retire-before-apply. **This is a substitution, and it is disclosed as one**: the
  bridge process, the HTTP surface and every byte on the filesystem are real, but the
  EA half of the conversation is simulated. A terminal-absent machine cannot prove
  otherwise.
- **Latency indicator.** The header shows the round trip for `/api/status`, amber
  past 750 ms. The poll now has a 4 s deadline, so a hung socket reports an error
  instead of leaving the last payload looking current, and overlapping polls are
  suppressed.
- **`lastGood` is now real.** It was previously assigned and never read. Past the
  stale threshold the panels dim, the banner carries the time of the last valid
  snapshot, and the header shows it persistently — so a frozen screen cannot pass for
  a live one.
- **Leg rows are keyed by ticket.** The table updates in place instead of being
  rebuilt every second, which stops text selection being destroyed and numbers
  flickering while a position is being watched.
- **`test_client_script.py`** — 15 tests covering the client's structure and its
  actual execution (13 structural checks plus 2 that prove the analyzer can fail).

### Changed

- Python tests: 72 → 102.
- `README.md` — test counts, the new suites, and why they exist.
- The MQL5 `#property version` values are unchanged at `"1.00"`, which is the correct
  MetaEditor form for 1.0.4 (`X.Y.Z` → `X.YY`). Self-tests remain **135/135**; no
  MQL5 source was modified apart from the version literals.



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
