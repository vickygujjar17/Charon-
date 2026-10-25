# Changelog

All notable changes to Charon are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every commit subject starts with the version it ships, for example
`v1.0.1: fix ladder re-arm after a rejected modify`.

## [Unreleased]

## [1.5.0] - 2026-10-25

Four gaps closed, one of them a live trap. A `MARKET` ticket that names an `entry` price no
longer fills at market — it **arms a watch**, and the EA submits it itself when its own tick
reaches that price. Trail activation may be stated as an **absolute price**. Commodities get
their own asset class, and the dashboard shows both queues side by side.

`SCHEMA_VERSION` goes `3` → `4`. Snapshots stay **tolerant** (a schema-2 or schema-3 EA is
still rendered); commands are now gated **per field** rather than per version — see Notes.

### Fixed

- **`MARKET` + an entry price silently filled at market.** `ApplyOrderCommand` replaced the
  entry with the live ask or bid for any MARKET ticket, so "wait for 1.1050" and "buy now"
  produced the same trade — the price was validated, carried across the wire, read, and then
  ignored. The routing rule is now explicit: MARKET with no entry fills immediately, MARKET
  with an entry arms a watch. This was the most dangerous of the four gaps, because the
  dashboard reported "queued" for both.
- **A schema-3 chart would have been refused every v1.4.0 field the moment the bridge learned
  schema 4.** `_require_semantic_capable` compared against `SCHEMA_VERSION`, which is only
  correct while there is one frontier. Each field now carries the revision that first honours
  it, so a schema-3 EA still accepts a schema-3 ticket.
- **`XTI`/`XBR` crude was filed under Forex.** Both are six letters ending in a currency, so
  the structural pair test claimed them before energy could. Energy is matched first.
- **`CL`, `CO1` and the softs/grains landed under Other** beside genuine unknowns like `AAPL`,
  because no token list mentioned them.

### Added

- **The watch queue (`watch_add`, `watch_cancel`).** A watch parks an already-validated
  ticket and submits it through the identical `ApplyOrderCommand` path when the level is hit,
  so a deferred ticket is never a weaker one than an immediate ticket. The queue is capped at
  `CHARON_BRIDGE_MAX_WATCHES` (8), persisted to `MQL5\Files\Charon\watches.txt`, and restored
  on init so a recompile does not lose an operator's entry.
- **`watch[]` in the snapshot,** beside `pending[]` rather than merged into it. A watch has no
  broker ticket — nothing reached the broker — so it is cancelled by the id the EA minted,
  through `POST /api/orders/watch/cancel`. The panel gives it its own table for that reason.
- **`watching` as an order verdict,** so the last submitted ticket can be reported as armed
  rather than filled. `accepted`/`pending`/`rejected` keep their existing meanings.
- **Absolute-price trail activation (`trail_start_price`).** Validated against the *fill* in
  `ApplyTicketTrail`, not in the parser: only the EA has a fill to compare against, and a
  threshold on the wrong side of the entry would arm the trail in the wrong direction.
  Mutually exclusive with `trail_start_rr`/`trail_start_points` — a ticket stating two is a
  ticket whose author had not decided, so it is refused rather than resolved.
- **`ASSET_COMMODITIES`** (`WHEAT`, `CORN`, `SOYBEAN`, `COFFEE`, `COCOA`, `SUGAR`, `COTTON`,
  `ORANGE`, plus `RICE`/`OATS`/`LUMBER`/`CATTLE`/`HOG`) and `XTI`/`XBR`/`CO1` under energy.
  `CL` is matched exactly, never as a prefix, because `CLP` is the Chilean peso. Classification
  remains **advisory only**: an unrecognised symbol is still offered, under Other.
- **`bid`/`ask` in the snapshot,** so a watch row can show its remaining distance instead of
  making the operator subtract two prices by hand. Advisory: no decision depends on it.
- **`ChPriceAtOrBeyond()`** in `CharonMath.mqh`, shared by the watch trigger and the trail
  activation so the two can never drift. Both test *touched or crossed*, never equality — a
  level the market gaps over must still fire, and an exact-tick comparison would silently
  never trigger, which is the worst possible failure for a condition whose job is to wait.

### Notes

- **Upgrade in this order:** recompile every chart with the v1.5.0 EA first, then restart the
  bridge. Restarting first leaves schema-3 charts refusing watches and absolute-price
  activation — loudly, naming the field.
- **Two things require schema 4:** the `watch_add` verb (a schema-3 EA does not know it) and
  `trail_start_price` (a schema-3 EA would ignore the key and trail from its own default).
  Everything else still passes against a schema-3 chart.
- **Watches are restored, not fired, at `OnInit`.** The queue is loaded there but evaluated on
  the first `OnTickWork`: a chart reload can carry a stale cached tick, and firing a trade
  against it would be worse than waiting one tick.
- **The cross-to-fill path is not covered by the Python suite.** It needs a live terminal and a
  tick stream. The arithmetic is pinned by `CharonSelfTest.mq5` (`TestTrailStartPrice`), the
  command and snapshot shapes by `tests/test_watch.py`, and the EA's own behaviour by
  `tests/fake_ea.py` — but the moment a real tick fires a real watch has only been reasoned
  about, not observed.
- **A watch the operator armed with `InpPersistState` false does not survive a reload.** The
  queue file is skipped in that mode, deliberately, because "do not persist" should mean
  exactly that. The EA logs the restore count on init so a silent loss is visible.

## [1.4.0] - 2026-10-25

The institutional ticket. Order placement grows the decisions a desk actually makes
before sending: risk in **percent or money**, a hard **basket-wide ceiling**, a **trail
activation** threshold per ticket, up to **three pyramid stages** with their own trigger
kind, and a **journal** note. Positions and working orders gain **per-row** Modify and
Cancel, and pending orders are published for the first time.

`SCHEMA_VERSION` goes `2` â†’ `3`. Commands are **strict** (a schema-3 ticket is refused by a
schema-2 EA, which is the point) but snapshots are **tolerant**: this bridge still renders
a schema-2 EA. See Notes for the upgrade order.

### Added

- **Risk in money or percent, never both.** `build_order_intent` enforces exactly one
  figure and `ReadCommandFile` re-checks it, so the EA never has to arbitrate between two
  contradictory budgets. A money figure is refused if it exceeds
  `MaxAllowedRiskMoney()` rather than silently shrunk â€” the verdict says what happened.
- **Basket risk ceiling** (`basket_risk_ceiling_pct`). It only ever *tightens* the chart's
  `InpPyramidMaxRiskPct`; a ticket cannot use it to make a dashboard click more permissive
  than the chart it drives. A ceiling below the primary leg's own risk is refused as a
  contradiction.
- **Per-ticket trail activation.** `trail_start_rr` / `trail_start_points` are stored on the
  position row (`SCharonPosState.trailStartRR/Points`, persisted as `trR`/`trP`) and win
  over `InpTrailStartRR`. Zero means "not stated", so an older ticket changes nothing. The
  trail *mode* remains chart-wide via `trail_trigger` â†’ `SetMode`.
- **Three pyramid stages per ticket** (`stages[]`), each a `kind` (`rr` or `price`), a
  `trigger` and a `lots_mult`. The multiplier applies to the primary leg's *solved* volume,
  so a stage can never express an absolute lot. Stages must advance in the trade's
  direction â€” a stage that could never fire is refused rather than dropped.
- **Journal notes.** The full text is kept bridge-side in
  `Charon\journal_<SYMBOL>.json`, keyed by ticket and re-keyed onto the position ticket when
  the EA reports one. The EA receives only a short printable-ASCII `journal_tag` for the
  broker comment and echoes it back; the note is never reconstructed from the tag.
- **`pending[]` in the snapshot** (`CollectPending`). A pending order is not a position, so
  a `LIMIT` ticket was previously invisible until it filled â€” there was no way to cancel
  one from the dashboard.
- **Per-row Modify / Close** on the legs table and **Cancel** on the pending table. v1.3.0's
  buttons only ever addressed whatever the last verdict happened to name.
- **Asset-class grouping** in the symbol picker (`symbols.asset_class`), advisory only: an
  unrecognised name is offered under *Other* and never rejected.

### Fixed

- **`ReadJsonString` did not decode `\uXXXX`.** Every escape outside the short list
  (`\n \t \r \" \\`) was copied verbatim, so a note containing `Ã©` arrived as the literal
  `u00e9`. The parser now decodes four hex digits and refuses the string if they do not
  parse, rather than inventing characters.

### Changed

- **`SCHEMA_VERSION` 2 â†’ 3** and `CHARON_BRIDGE_SCHEMA` likewise. `Snapshot.from_json`
  accepts `(2, 3)` and now records the revision it *actually read*, so the pre-flight can
  compare it. `main.py` refuses a ticket that carries v1.4.0 semantics against an older EA
  with a message naming the field and the fix; a plain ticket still passes, so an
  un-upgraded chart stays usable for what it understands.
- `GET /api/status` gains `bridge_schema`, `journal`, `snapshot.schema` and
  `snapshot.pending`; `GET /api/positions` gains `pending` and `journal`;
  `GET /api/symbols` gains `asset_classes` and a per-entry `asset_class`.

## [1.3.0] - 2026-10-25

A correctness release with two features on top. The headline is a bug that made the
dashboard silently inert on any host whose clock is not UTC: **every** command was
refused by the EA, and nothing in the panel said so.

No wire-format change — `SCHEMA_VERSION` stays `2` — but the file *layout* changes, in a
way that keeps a v1.2.0 EA working. See Notes.

### Fixed

- **`issued` was a UTC epoch second while the EA compares against `TimeLocal()`.** The
  two differ by the host's UTC offset, so on a UTC+5:30 machine every `arm`, `trail_mode`,
  `order_*` and `order_cancel` was stamped 19800 s in the past relative to the EA's clock
  — 330× the 60 s tolerance. The EA retired each one as
  `rejected_… future-dated command (19800s ahead)`, so the panel reported "queued", the
  inbox emptied, and nothing happened. `commands.local_epoch()` now derives the stamp
  through `time.localtime()` → `calendar.timegm()`, which follows the host's offset
  including DST crossings and needs no `tm_gmtoff`.
- **The EA stand-in in the test-suite compared against UTC too**, which is why the bug
  shipped green. `tests/fake_ea.py` now mirrors `TimeLocal()`, and
  `test_submitted_issued_is_accepted_by_the_eas_clock` re-derives the EA's verdict from
  the written file. On a UTC host the bug is unobservable; on this one the test fails by
  exactly 19800 s.

### Added

- **Per-symbol command inboxes.** `commands\<SYMBOL>\` and its `processed\`. Before this,
  every chart on a terminal drained one shared folder: a chart that picked up a command
  meant for another chart did not apply it (the `symbol` field stopped that) but had
  already retired it into `processed\`, so the rightful chart never saw it. The MQL5 side
  creates the folder in `Init()`; `SafeName()` mirrors `paths.sanitize_symbol()` exactly,
  and `tests/test_symbol_names.py` parses the EA self-test's own fixtures so the two
  cannot drift.
- **Searchable symbol picker** (`GET /api/symbols`, `charon_bridge/symbols.py`). Merges the
  charts the EA is publishing with the instruments the terminal lists, and shows a short
  label while sending the exact broker name. `resolve()` returns `None` on ambiguity
  rather than guessing between two broker names that collapse to one label.
  `CHARON_SYMBOL_PREFIX` / `CHARON_SYMBOL_SUFFIX` configure the decoration; they are
  display-only by construction.
- **Unmanaged-position detection** (`GET /api/unmanaged`, `charon_bridge/reconcile.py`).
  Lists open positions no live Charon chart accounts for, with two verdicts:
  `not_tracked_by_ea` (a live chart on that symbol is publishing, and the ticket is absent
  from its legs — needs 3 consecutive confirmations) and `no_charon_on_symbol` (nothing is
  publishing that symbol, so it can never be managed — reported immediately).
- **`422` pre-flight on `POST /api/order` and `/api/order/modify`.** A ticket for a symbol
  with no live snapshot is refused before it is written, instead of queueing a file no
  chart can drain. Shape errors are still reported first, because "risk_pct must be
  positive" is more actionable than "no chart".
- **Read-only account listing in `mt5_adapter`.** `symbols()` and `positions()` back the two
  new endpoints, both behind one module-level lock because the MetaTrader5 package is not
  thread-safe — and `describe()` now takes the same lock, closing a race that already
  existed. The permitted `_mt5.*` calls are a closed allow-list a test enforces, rather
  than a blocklist of four forbidden names that a novel call would slip past.


### Changed

- **`/api/commands` reads the union of both layouts.** Without that, every command queued
  by a v1.2.0 EA would vanish from the panel the moment the bridge was upgraded, and stay
  invisible until something drained it.
- **The UI's unmanaged panel is deliberately independent of the transport banner.** The
  banner reports whether the bridge is talking; the panel reports whether the account
  holds something Charon is not managing. Sharing one would let a healthy transport cover
  for an unmanaged position.

### Notes

- **Upgrading is safe in either order.** The bridge writes to the flat inbox until that
  chart's subfolder exists, so a v1.2.0 EA keeps working with a v1.3.0 bridge. The
  subfolder is created by the EA and never by the bridge: if the bridge created it, its
  own probe would always succeed and every command for an un-upgraded EA would be routed
  into a folder nothing reads.
- **`summary.state` is not a boolean.** `ok` means every chart was checked; `unknown`
  means a chart is stale or still starting, so an absence proves nothing; `unavailable`
  means nothing could be checked at all. Only `ok` with no rows is an all-clear, and a
  starting EA's empty legs array is treated as *absent* rather than as "no positions" —
  otherwise a terminal restart would fire alerts about Charon's own trades.
- **`InpMagicFilter = 0` still adopts every position on the chart's symbol.** The
  unmanaged panel cannot see those, because the EA does treat them as its own. Use a
  specific magic on a busy account.
- Existing charts keep their saved `InpBridgeEnable`; a chart attached before v1.2.0 still
  needs its inputs touched once.

## [1.2.0] - 2026-10-25

A single-default release. No calculation, no wire format and no `SCHEMA_VERSION` (`2`) moves:
a v1.1.2 EA and a v1.2.0 panel remain fully interchangeable. What changes is that the bridge
publishes without being asked to.

### Changed

- **`InpBridgeEnable` now ships as `true`.** The EA writes its snapshot under
  `Common\Files\Charon` and drains the command queue on attach, with no chart configuration.
  This is the setting that made the v1.1.x bridge look broken on a fresh install: the panel
  was up and `/api/status` returned `no Charon snapshot found` because a default-off EA had
  never created the directory. Publishing state is now the stock behaviour and opting out is
  the explicit act.

### Notes

- **Existing charts keep their saved value.** MetaTrader persists input values per chart and in
  `.set`/`.tpl` templates, so the new default reaches only charts attached after the upgrade.
  A chart that was already running will keep publishing with `false` until its inputs are set
  to `true` once. The README documents this rather than implying the flip retrofits anything.

### Added

- **`_tools/bridge_doctor.ps1`** — a read-only diagnostic for the filesystem transport. It
  reports the resolved bridge root, per-symbol snapshot ages (live / stale / offline), MT5
  process and log recency, the bridge install and its version, listeners on 8000-9100, a
  loopback `/api/health` probe, and the `InpBridgeEnable` default, then prints a verdict.
  Written because "no Charon snapshot found" named neither the directory that was searched nor
  the two-machine mistake that most often causes it.
- **Startup now warns when no snapshot exists.** `_lifespan` emits a `WARNING` naming the
  resolved bridge root, before `ensure_dirs()` runs, so it describes what the EA actually left
  behind instead of a directory the bridge just created itself. The 503 body carries the same
  directory and whether `Common` exists, and the dashboard footer renders the path the bridge
  is reading so a cross-machine mismatch is visible without reading logs.

## [1.1.2] - 2026-10-25

A packaging release with one breaking change and no change to trading behaviour: the wire
format, `SCHEMA_VERSION` (`2`), every input default and every calculation are untouched. What
moves is the install layout.

### Changed

- **The MQL5 sources are split into `Experts` and `Include`.** The two entry points,
  `Charon.mq5` and `CharonSelfTest.mq5`, now reach their modules with angle includes —
  `#include <Charon\CharonDefines.mqh>` — while the ten `.mqh` files live in
  `MQL5\Include\Charon\`. Previously all twelve files sat in one folder and were joined by
  sibling-relative quotes. Angle includes resolve against `MQL5\Include` and quotes resolve
  against the including file's own directory; those are different roots, so the directive
  form and the folder layout have to move together. The reason to prefer the split is that
  the headers are now one versioned copy under `Include`, which is where MQL5 looks for a
  library, and the entry point no longer has to sit beside them.
- **The headers still use sibling-relative quotes for each other.** Only the two entry points
  were converted. Inside `Include\Charon` a module's `#include "CharonDefines.mqh"` resolves
  against that same folder, so the modules stay portable as a unit and could be relocated
  again without touching nine files.
- **`build_check.ps1` stages the split layout.** Entry points go to `MQL5\Experts\Charon`,
  modules to `MQL5\Include\Charon`, and the two staged sets are each reconciled against their
  source sets, so an incomplete copy on either side fails the gate instead of compiling. The
  link-provenance check gained a second assertion: a Charon header must not only come from
  the staging tree but must resolve inside `Include\Charon`. That closes the hole the new
  layout would otherwise open, where a stale installed copy in the developer's own terminal
  could satisfy `<Charon\...>` and make a broken tree look green.

### Breaking

- **Existing installs must move their headers.** An install upgraded in place without moving
  the ten `.mqh` files from `Experts\Charon\` into `Include\Charon\` will not compile: the new
  entry point looks under `Include` and finds nothing. The failure is immediate and total
  rather than subtle — `error 106: file 'Include\Charon\CharonDefines.mqh' not found` — and
  it cannot be mistaken for a partial build. `README.md` documents the migration.

### Fixed

- **`build_check.ps1` no longer declares an unused `$includes` path.** It had computed the
  `MQL5\Include\Charon` staging path since v1.0.6 and never used it, which is the shape of a
  migration that was planned and left half-done. The variable is now what the gate stages to.
- **`README.md` no longer claims a flat `Experts\` install works.** That was true while the
  includes were quoted and became false the moment they were not. The installation section
  now states the two-folder requirement, names the exact error a missing header folder
  produces, and warns against compiling from a repository clone — where `<Charon\...>` would
  silently resolve against whatever is installed in the terminal instead of the working tree.

## [1.1.1] - 2026-10-25

A presentation release. No trading behaviour changed, no wire format changed, and
`SCHEMA_VERSION` stays at `2` — a v1.1.0 EA and a v1.1.1 panel remain interchangeable. Every
change here is in the dashboard's markup, stylesheet and renderer.

### Changed

- **The dashboard was rebuilt as an execution console.** It previously read as a generic admin
  page: four equal cards, sentence-case headings, and no visual hierarchy between a number the
  EA published and decoration around it. It is now an obsidian (`#030712` / `#0a0f1d`) terminal
  with a command bar, a hairline-divided account readout strip, and dense panelled sections.
- **Colour now carries meaning instead of dressing the page.** Emerald (`#10b981`) is
  live/long/profit, crimson (`#f43f5e`) is offline/short/loss/refused, cyan (`#38bdf8`) is
  technical (latency, queued commands), violet (`#818cf8`) is rung and tier metadata. Amber
  remains reserved for staleness, because a stale feed is warnable, not fatal.
- **Readouts use tabular figures.** A ticking equity or latency value can no longer shift the
  layout, which previously made a live panel look restless.
- **The status pill carries a live dot** and breathes only while the snapshot is actually
  `live`. Motion on a stale or offline pill would read as "working" at a glance, so every other
  state holds still.
- **The bridge log is now a tall gutter-marked feed** inside a native `<details>` element, so it
  can be collapsed without JavaScript. Its heading is renamed *Execution log* — it remains the
  bridge-side transcript, and the EA's own Experts log is still the trading record.

### Added

- **An inline SVG mark.** An obol (the ferryman's fare) crossed by Charon's pole, drawn with
  `currentColor` so it inherits the accent tone and costs no extra request.
- **`static/favicon.svg`**, the same mark at 16px, served by the existing `/static` mount.

### Fixed

- **Documentation counts had drifted.** `README.md` claimed 102 Python tests against an actual
  168, and 135/135 self-test checks against an actual 147/147. Both are corrected. (The
  Versioning section's "the six disagree" was already accurate — `bump_version.py` guards six
  literal *kinds*, of which `#property version` covers all twelve MQL5 files.)
- **`CHANGELOG.md` had no `[1.1.0]` entry** despite `v1.1.0` being shipped and tagged. It is
  backfilled below.

## [1.1.0] - 2026-10-24

An execution release, backfilled into this changelog after the fact. It added the first
order-originating control and the schema that carries it.

### Added

- **An interactive New Order panel.** The dashboard can now submit order *intent* — side,
  order type, entry, risk percentage, block top/bottom, trailing mode and the rung the entry
  is stated at — to the EA. It sends no volume, no stop distance and no R multiple: those are
  the EA's answers, and the EA publishes them back in `snap.order` for the panel to render
  verbatim. The client never multiplies, subtracts or otherwise derives a price, which
  `test_client_script.py` and `tests/client_harness.js` both pin.
- **`POST /api/order`, `POST /api/order/modify` and `POST /api/order/cancel`**, plus the
  `order` block in the schema carrying the EA's verdict (accepted / pending / rejected, with
  size, entry, stop, risk and ticket).
- **Dashboard controls for arming and trailing** were unlocked, alongside the New Order
  panel.

### Changed

- **`SCHEMA_VERSION` raised to `2`** for the `order` block and the new command verbs. Pairing
  is checked on the wire, so a schema-1 bridge refuses a schema-2 client instead of silently
  misreading it.
- **`CharonBridge.mqh`, `CharonEngine.mqh`, `CharonRisk.mqh` and `CharonTrade.mqh`** grew the
  command-intake and adjudication paths that back the panel.

## [1.0.6] - 2026-10-24

A deployment-correctness release. No trading behaviour changed. The compilation gate was
validating a directory layout the documentation never described, so it stayed green while
a real installation failed to compile.

### Fixed

- **The compile gate proved the wrong thing.** `build_check.ps1` staged every `.mqh` and
  `.mq5` file *flat* into `MQL5\Experts\`, but `README.md` instructs users to install into
  `MQL5\Experts\Charon\`. Because Charon's includes are sibling-relative
  (`#include "CharonDefines.mqh"`), the compiler resolves them against the including file's
  own directory — so the gate passed on a tree that no user ever has, while a real install
  could fail with `error 106: file 'Experts\CharonDefines.mqh' not found`. The gate now
  stages `MQL5\Experts\Charon\`, the documented layout, and so fails when an install would
  fail. Reproduced the user-facing error first (a lone `Charon.mq5` gives exactly
  `error 106` at line 24, column 10), then confirmed the documented layout compiles to
  `0 errors, 0 warnings` from the nested folder.
- **A partial copy could pass the gate.** Staging used `Copy-Item` and never checked the
  result, so a short copy would have been compiled as if complete. The gate now compares the
  staged `.mq*` set against the source set and aborts on any missing file, naming it.

### Documentation

- `README.md`: the Installation section now states the hard requirement instead of implying
  it — all twelve MQL5 files must remain in one folder, the folder *name* is free, and
  copying `Charon.mq5` alone produces `error 106: file 'Experts\CharonDefines.mqh' not
  found`, with the reason (sibling-relative includes). Also states what a green gate now
  means: the files compile as an installed EA, not just as a staging tree.

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
