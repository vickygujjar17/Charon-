# Changelog

All notable changes to Charon are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every commit subject starts with the version it ships, for example
`v1.0.1: fix ladder re-arm after a rejected modify`.

## [Unreleased]

## [1.7.1] - 2026-10-26

The release where the dashboard stops lying about *why* it cannot see the EA.

v1.7.0 shipped a bridge that could fail silently in two independent ways, and from the browser
they looked identical. If `InpBridgeEnable` was false, `ServiceBridge()` returned early and no
snapshot was ever written — the dashboard reported "EA offline" forever, while the EA was
running perfectly and had no idea the bridge existed. If a state write failed, `FileWriteString`
returned short and was ignored, so a half-written `state_<SYM>.json` was reported as a
successful publish. In both cases `/api/status` answered `503` and neither component said
anything: no log line in the EA, no line in the dashboard's own log tail.

This release makes both failures audible, and adds the cross-origin path that a hosted UI needs.

### Added

- **`CCharonBridge::LastOk()`** exposes the publish result that `m_lastOk` already tracked and
  nothing read. `CCharonEngine::PublishBridge()` now logs on *transition* — `FAILED + Note()`
  when publishing starts failing, `recovered` when it comes back — and stays quiet in the steady
  state, so a bridge that has been down for an hour produces one line, not 720.
- **The rates-refresh gate announces itself.** When `m_risk.Refresh()` fails, `OnTimerWork()`
  returns before `PublishBridge()`, so the snapshot silently stops advancing. That path now logs
  once on entry and once on recovery. A suspended EA used to be indistinguishable from a hung
  one — and, worse, from a healthy one whose dashboard had simply gone stale.
- **`Bridge :` row on the dashboard**, reading `off (InpBridgeEnable=false)` or
  `ok|FAILED | seq N | <note>` from the EA's own state rather than from a snapshot field.
- **`CHARON_ALLOWED_ORIGINS`** (bridge). Off by default and exact-match only: origins are
  trusted literally, so no wildcard and no regex, with `CHARON_TOKEN` recommended alongside it.
  `DELETE` is included in the allowed methods because the journal's row delete would otherwise
  fail cross-origin in the browser while working in `curl`.
- **The bridge says at startup which origins it trusts**, and warns when
  `CHARON_ALLOWED_ORIGINS` is set without `CHARON_TOKEN`. A same-origin request carries no CORS
  header, so an operator who configured the allowlist and one who forgot it saw the same working
  console; the allowlist decides which *pages* may call the bridge, not who may use them, and
  widening that surface on a live trading account should not be silent.
- **`charon_api` cookie** (client). One static bundle now serves both layouts: empty means
  same-origin, and a value such as `https://bridge.example.com` re-points all twelve `fetch()`
  calls and the `/api/logs` `EventSource` at that bridge. Also `charon_token`, which is sent as
  `X-Charon-Token` on every request except the SSE stream (which cannot carry a header).

### Fixed

- **A short state write is now a failure.** `WriteStateFile()` compares `FileWriteString`'s
  return against `StringLen(payload)`, deletes the truncated `.tmp`, records
  `state write short (...)` in `Note()`, and reports `LastOk() == false`. Previously a partial
  file was published as a good snapshot.
- **"Cannot reach the bridge" now names the bridge.** The client's log line includes the
  resolved base, because the common cause is not a dead bridge but a page asking the wrong host:
  a hosted page resolving `/api/status` against its own origin, or against its *own*
  `127.0.0.1` — the browser's loopback, not the operator's PC. That distinction was previously
  invisible.

### Changed

- The dashboard's unreachable/timeout log line is now asserted by `tests/client_harness.js`,
  which runs in two modes: relative (the default) and re-targeted (absolute base + token). The
  re-targeted run fails if any request ignores the configured base, so the resolver cannot rot
  back to hardcoded paths.
- CORS installation is a function (`_install_cors`) returning whether it ran, so the *absence*
  of the header on a loopback install is asserted rather than assumed.

## [1.7.0] - 2026-10-26

The release where a ticket finally drives the pyramid ladder it described.

v1.4.0 taught the *wire* to carry pyramid stages: `ReadStages()` parsed them, validated each
one, refused a ladder that did not advance in the trade's direction, and stored the result on
`SCharonBridgeCommand`. And then nothing read it. `CCharonEngine` never touched
`cmd.stageCount`, and `CCharonPyramid::Evaluate()` went on resolving every tier from
`InpPyramidT2RR`/`T3`/`T4`, because `SCharonTierSpec` only knew `activationRR`. A stage could
say `{"kind": "price", "trigger": 1.29}` and `Evaluate()` would still ask whether 2R had been
reached. The feature was documented, unit-tested and inert.

v1.7.0 connects it, and closes the gap that made the connection impossible: a tier used to be
*only* an R multiple, so a PRICE stage had nowhere to live. `SCharonTierSpec` now carries
`triggerKind` alongside `triggerRR` and `triggerPrice`, `SCharonStagePlan` holds up to three
stages, and a plan rides on the primary row (`tier == 1`) so it survives a reload.

Also: the two defaults that made the previous behaviour the *only* behaviour are flipped.
Trailing starts armed (`InpTrailMode = ATR`, `InpTrailStartRR = 0.00`) and pyramiding is on
(`InpPyramidEnable = true`).

### Added

- **Per-ticket pyramid stages actually fire.** `ApplyTicketPlan()` parses a ticket's `stages`
  onto the primary row and hands the plan to `CCharonPyramid`; `RestoreTicketPlan()` re-attaches
  it after a reload. Stage *n* drives tier *n+2* — tier 1 is the primary leg — so a two-stage
  plan covers tiers 2 and 3. A tier the plan does not name still resolves from the chart input,
  but only if the ceiling reaches it: `ChPlanLegCeiling` counts `stages + 1`, so the default
  `InpPyramidMaxLegs = 3` stops a two-stage ticket at tier 3.
- **PRICE triggers.** A stage may be `{"kind": "price", "trigger": 1.29000}` instead of an R
  multiple. `CCharonPyramid::Evaluate()` takes the quote the next leg would trade at and judges
  it against the level, so a price stage fires on the same number the fill sees. A zero price
  never fires, in either direction: a condition Charon cannot evaluate holds rather than arms.
- **`ChStageTriggerReached`** — one predicate for both kinds, comparing an RR stage with
  `CHARON_EPS` so `1.99R` holds at a `2.00R` trigger while `2.00R` fires. An R stage with no
  usable trigger is never reached.
- **`ChPlanLegCeiling` / `ChPyramidAllowed`** — the leg ceiling is
  `max(InpPyramidMaxLegs, stages + 1)`, resolved only when a plan exists, so a plan-less chart
  is byte-identical to 1.6.3. A plan may **raise** the ceiling and may never **lower** it:
  `InpPyramidMaxLegs` is the operator's standing decision about exposure.
- **`ChFormatStagePlan` / `ChParseStagePlan`** — `"kind,trigger,mult;..."`, triggered at 8
  decimals and multiplied at 4. Empty is "no plan" and not an error; anything malformed refuses
  the whole plan, because three stages silently becoming one is a lie about the exposure the
  operator authorised.
- **Plans persist.** `CharonState` writes `stN` plus three numeric keys per stage
  (`st0k/st0t/st0m`…). `stN` is always written — `0` when the row has no plan — so an install
  that never used stages gains no stage keys; a plan that *shrank* also deletes the tail it no
  longer owns, so a removed stage cannot be resurrected on the next load. `Erase()` sweeps every
  possible stage key. A stage is re-validated on load — a GlobalVariable is a number on disk and
  can be edited by hand — and one bad stage drops the whole plan.
- **Watches carry their ladder.** `SaveWatches()` gains a 17th field. `LoadWatches()` treats it
  as optional, so a queue written by any older build restores as "no plan".
- **Filled pending tickets keep their plan.** A LIMIT ticket has no position row until it fills,
  so the ladder is parked against the order ticket exactly like the trail thresholds and claimed
  by `AdoptTicket()`.

### Changed

- **Trailing is armed by default.** `InpTrailMode = CHARON_TRAIL_ATR` and
  `InpTrailStartRR = 0.00`, so a fresh chart trails from its first tick instead of waiting for
  1R.
- **Pyramiding is enabled by default.** `InpPyramidEnable = true`. The exposure ceiling is
  unchanged — three legs, `InpPyramidMaxRiskPct` on the basket — so this widens *when* a scale-in
  may fire, not *how much* the basket may risk.
- **Existing charts keep their saved values.** MetaTrader persists input values per chart and in
  `.set`/`.tpl` templates, so neither flip reaches a chart that was already attached: it goes on
  publishing `InpTrailMode = OFF` / `InpPyramidEnable = false` until those inputs are set once by
  hand. Only charts attached after the upgrade inherit the new defaults.
- `CCharonPyramid::Evaluate()` gained a `price` parameter and now resolves tiers through
  `ChResolveTierSpec`, so the projection in `MaybePyramid()` and the trigger in `Evaluate()`
  use one multiplier rather than two.
- The direction check in `Evaluate()` moved ahead of the trigger check, because a PRICE stage
  needs to know which side "beyond" means.

### Fixed

- **The self-test asserted a stale schema.** `TestBridgeTicketHelpers` pinned
  `CHARON_BRIDGE_SCHEMA == 3` against a header that says `4`, so the suite was failing on a
  clean checkout. It now asserts `4`, and `SCHEMA_VERSION` is unchanged by this release.
- `CharonState.mqh` includes `CharonPyramid.mqh` explicitly: rows persist a plan, so the header
  depends on `ChAddStage`/`ChStageIsValid` rather than on the entry point's include order.

### Tests

- Four new MQL5 suites — `TestStagePlan`, `TestStagePlanText`, `TestStageAllowance`,
  `TestStageTrigger` — covering format/parse round-trip, malformed refusal, empty ⇒ no plan,
  tier mapping (including `ChResolveTierSpec` falling back to the chart ladder for a tier the
  plan does not name, which the leg ceiling may still refuse), per-kind field placement, the
  `ChPyramidAllowed` truth table, `1.99R` versus `2.00R`, PRICE in both directions, zero price,
  and a plan that raises but never lowers the ceiling. The suite now runs 275 checks.
- `fake_ea.py` retains `self.stages` and mirrors `ReadStages` + `StagesAdvance`, so the Python
  suite can prove a ladder arrives in order; `test_order_loop.py` round-trips a mixed `rr` +
  `price` ticket and asserts the EA's own refusal of a retreating one. The bridge suite now runs
  461 tests.

### Upgrade

Recompile **every** chart with the v1.7.0 EA, then restart the bridge. Charts on an older build
refuse a stage-plan watch and an absolute-price activation loudly, naming the field, but a
half-upgraded install is still a broken one.

## [1.6.3] - 2026-10-26

A bridge and dashboard patch. **No EA change, no schema change:** `SCHEMA_VERSION` stays `4`,
no `.mqh` logic changed (only the version string), and the command file format is untouched.

The release exists because a feature that was written and tested had never been connected to
anything. `charon_bridge/symbols.py` has shipped `resolve()` and `matches()` since v1.4.0 —
four cases, one of them pinning the `CL`/`CLP` hazard — and **no production code called
either one**. `POST /api/order` went through `_symbol_or_404`, which called
`paths.sanitize_symbol` and stopped there. So the documented promise that broker naming
variants "resolve cleanly" was, in the running system, false.

### Fixed

- **`/api/order` now resolves a broker name variant to the chart that is actually live.**
  An operator whose terminal publishes `EURUSD.a` got `422 no live Charon snapshot for
  'EURUSD'` for a chart that was publishing, with nothing in the message to suggest the
  suffix was the problem. A symbol with no snapshot of its own is now matched against
  `discover_symbols()` — the names the EA is *actually* publishing — and a unique hit is
  used. The response gains `resolved_from` / `resolved_to` (absent when nothing was
  rewritten, so the common case is unchanged) and the rename is logged.

  Four limits, each of which is a test:

  - **the exact name always wins when it exists**, including the *stale* case — a chart that
    stopped publishing is a dead chart, not a typo, and retargeting it to `EURUSD.a` would
    send an order to an instrument the operator never chose;
  - candidates are **live charts, never the broker's listing**: a broker-only symbol has no
    chart to drain the command, so resolving to it would turn one clear refusal into a
    vaguer one;
  - an **ambiguous** hint (`mEURUSD.a` and `EURUSD.a` both collapsing to `EURUSD`) is
    refused with both names, rather than picking whichever sorted first;
  - the answer is always a member of the published set, so this cannot invent a symbol.

- **The resolution needs no configuration.** `canonical()` deliberately strips nothing
  unless `CHARON_SYMBOL_SUFFIX` says what to strip — a blanket "everything after a dot" rule
  turns `US30.cash` into `US30` and then fails to match the real chart. That is right for a
  label and useless for a lookup, so the rule is inverted: each *known* name is asked whether
  it is the hint plus a short, punctuation-anchored tag (`.a`, `.pro`, `.raw`, `_m`, `#`).
  Nothing is stripped from the hint, so `CL` still does not resolve to `CLPUSD` — the
  remainder `PUSD` carries no separator and is therefore not a tag. A `resolve()` that only
  worked for operators who had already set the env var would have been a feature for the
  people who did not need it.

### Added

- **The symbol classifier recognises the tickers a broker lists instead of a word.** Copper
  (`HG`, and `COPPER`), RBOB gasoline (`RB`), the grains (`ZC ZW ZS ZM ZL`), the softs
  (`SB KC CT CC OJ`) and livestock (`LE GF HE`) all landed under **Other** beside genuine
  unknowns like `AAPL`, because only the spelled-out forms (`CORN`, `CATTLE`) were matched.
  The two-letter tickers are matched **whole and never as a prefix**, reusing the
  `_ENERGY_EXACT` pattern that exists for exactly this: `LE` would otherwise claim `LEU…`,
  `HE` would claim `HEL…`, and `HG` would claim `HGD…`. Filing an unrelated instrument under
  Metals is worse than filing it under Other, which is at least honest about not knowing.

- **A trader's nickname finds the instrument, in the picker and at the order boundary.** The
  bridge publishes a small `alias` per instrument — `Fiber`, `Cable`, `Gopher`, `Aussie`,
  `Loonie`, `Swissie`, `Kiwi`, `Gold`, `Silver`, `Platinum`, `Palladium`, `WTI`, `Brent`,
  and the softs by their full names — so typing `gold` finds `XAUUSD` without the client
  keeping a second copy of the table. A row reads `XAUUSD · Gold`; the hint under the field
  repeats the nickname once the row is chosen.

  The alias is **never a command argument**, and the tests say so three ways: the catalogue's
  `symbol` is still the broker name, `matches()` reads the table backwards and can only
  return a member of the list it was given, and `client_harness.js` asserts that choosing by
  nickname posts `XAUUSD`. An exact symbol beats a nickname (`GOLD` stays `GOLD`), and two
  charts sharing one nickname stay **ambiguous** rather than one being chosen —
  `USOIL`/`XTIUSD` are both "WTI", which is exactly the case `resolve()` refuses to break.

## [1.6.2] - 2026-10-26

A dashboard-only follow-up to 1.6.1. **No EA change, no schema change:** `SCHEMA_VERSION` stays
`4` and `/api/order` is untouched.

### Fixed

- **A background catalogue refresh no longer moves the keyboard cursor in the symbol picker.**
  `/api/symbols` is polled every 10 s and the open list is repainted from it, and every repaint
  snapped the highlight back to the top row. An operator arrowing through the list therefore had
  the cursor yanked out from under them, and the next `Enter` sent an instrument they had never
  looked at. The cursor is now held across a repaint **by symbol, not by index**, so rows shifting
  (a chart attaching above them) cannot silently change what `Enter` will send. A changed query or
  a fresh open still anchors on the top match.
  `client_harness.js` asserts this by shifting the catalogue between polls and then pressing
  `Enter`, so a future edit that re-derives the cursor from the top of the list fails.

## [1.6.1] - 2026-10-26

A dashboard-only patch. **No EA change, no schema change:** `SCHEMA_VERSION` stays `4`, the
`.mqh` headers carry only the version string, and the `/api/order` payload is untouched.

The ticket's symbol field was a `<input list=...>` bound to a datalist. That control had three
defects that a wider catalogue makes visible rather than causes: it could not show an asset class,
it could not be driven by a keyboard, and — the one that matters — the browser's own suggestion
list offers `option.value`, so a name picked from it and a name typed into it arrived by
different paths with nothing pinning either to the exact broker name.

### Changed

- **The symbol field is a searchable ARIA combobox.** Grouped by asset class with a badge per
  row (`FX`, `MET`, `IDX`, `ENR`, `CMD`, `CRY`, `STK`, `OTH`), filtering on the broker name, the
  display label and the class heading. `ArrowUp` / `ArrowDown` / `Home` / `End` / `Enter` /
  `Escape` / `Tab` all behave as a combobox should, `aria-activedescendant` follows the highlight,
  and the highlight is the same affordance for pointer and keyboard so Enter never takes a row
  the operator cannot see.
- **Only one element claims `role="combobox"`, and it is the search input.** The collapsed
  control is a button carrying `aria-haspopup` / `aria-expanded` / `aria-controls`; the textbox
  owns `aria-activedescendant`. A button claiming `role=combobox` with no textbox would leave the
  keyboard with nowhere to type, and this page is driven from the keyboard.
- **The value that is posted is the exact broker name, in a hidden `#orderSymbol`.** Clicks read
  it from the row's `data-symbol` and Enter writes the same string, so a label (`EURUSD` for
  `EURUSD.a`) can never reach `/api/order`. `test_symbol_combobox_sends_the_broker_name_not_the_label`
  fails if any future edit writes the field from anything else.
- **Free text is still accepted.** When nothing in the catalogue matches, what was typed is
  offered as its own row and sent verbatim. A broker can list an instrument the bridge cannot
  enumerate; refusing a correct name would be worse than accepting it.
- **The list is capped at 50 painted rows** with a footer that says which of four states it is in
  — no catalogue, no match, matches withheld by the cap, or narrowed by the query. A filtered
  list never looks like the whole one.

### Added

- **An honest bid/ask beside the ticket**, from the `bid` / `ask` the EA already publishes on
  `/api/status`, with the spread in **points** (`ask - bid` scaled by `10^digits`). Points, not
  pips: a pip is 10 points on a 5-digit pair, 1 point on a 3-digit JPY pair and undefined on
  `US30.cash`. The quote belongs to the symbol the bridge is reading, so naming another
  instrument says there is no quote for it rather than borrowing the neighbour's price, and an
  EA that never published a tick (schema 2/3) says that instead of printing a zero.
- **Price boxes step to the instrument's digits.** `digits` from the snapshot drives `step` on
  the entry, block-top, block-bottom and activation-price inputs, so a 3-digit JPY pair no longer
  refuses every price the operator types. No price is ever written into them.

### Fixed

- `tests/client_harness.js` gained `focus()` / `document.activeElement`, real `className` ↔
  `classList` synchronisation, parent links with `contains()` / `closest()`, `removeAttribute()`
  and a bubbling `fire()` dispatcher. Without those the popover could not be exercised at all —
  and a stub whose `className` and `classList` disagree would have let a popover that opens but
  cannot close pass unnoticed. The harness also had no `orderTrailPrice` id, so it had never
  actually wired that field.

## [1.6.0] - 2026-10-25

Four modules that had no data source now have one, and the dashboard is rebuilt to the Apex
Institutional Terminal design. **No EA change:** the snapshot still describes open risk only, so
realized results are read from MT5's deal history by the bridge instead.

`SCHEMA_VERSION` stays `4` and the `.mqh` headers are untouched apart from the version string.
The new terminal calls are reads, and the allow-list test that guards them was extended rather
than relaxed.

### Added

- **Closed-trade history (`GET /api/history`).** `history_deals_get` over a 1-365 day window,
  folded into one trade per closed position. `IN` and `OUT` legs are paired by `position_id`,
  partial closes are summed, and realized is `profit + swap + commission` across **both** legs
  — reading only the closing deal understates every loss by the entry commission.
- **The statistics it implies.** Win rate, profit factor, expectancy, average R:R, best/worst,
  longest win and loss streaks, average hold, and a per-session matrix.
- **`charon_bridge/history.py`, with no MetaTrader5 import.** The arithmetic is separated from
  the terminal so it can be unit-tested on a machine with no MT5 installed — which is the only
  way it gets exercised before it meets a real account.
- **Session attribution on broker-server time.** London / New York / Asian, with the offset
  either inferred from the EA's `server_time` against the bridge clock or passed explicitly.
  `tz_offset_minutes` and `tz_source` are echoed back, so the UI states the clock it used
  instead of implying UTC.
- **Equity curve** in `GET /api/history`, cumulative from zero — the strategy's result, not the
  account balance, which would drag in deposits and other EAs.
- **Journal write-back.** `POST /api/journal` and `DELETE /api/journal` create or remove a note
  against a real ticket, with optional `psych` and `tags`. This is a local sidecar write: it
  never reaches the EA and cannot move money, so it needs no live chart.
- **`GET /api/candles`** for OHLC bars, behind the same availability contract.
- **Vendored Inter and JetBrains Mono** (`static/fonts/*.woff2`, ~160 KB, Latin subset) with
  `@font-face` in the stylesheet. `font-display: swap` means a number is readable immediately
  rather than invisible while the face loads.
- **Apex design system** — 56px header, 32px ticker strip, 64px icon rail, 5-cell KPI grid,
  hairline panel heads, 24px table rows, 2px status chips, meters and tags.

### Fixed

- **An undefined ratio rendered as a confident zero.** Win rate, profit factor and average R:R
  now return `null` when their denominator is zero, never `0` and never infinity. "0% win rate"
  reads as "you have lost every trade" and a profit factor of `999` reads as perfection; both
  are lies about someone's money. The panel prints `—` and says why.
- **A deposit could have been counted as trading profit.** Balance operations carry no symbol,
  and `history_deals_get` returns them; they are skipped.

### Changed

- **The palette tokens were aliased, not renamed.** `ink-*`, `shade-*`, `live`, `offline`,
  `stale`, `accent` and `violet` still resolve — now to the Apex values — because `app.js`
  builds those class names at runtime as complete literals and the test suite asserts on them.
  Renaming would strip the colour from every status pill with no error anywhere. The Apex names
  (`surface`, `borderline`, `bull`, `bear`, `warn`, `brand`, `alt`) are what new markup uses.
- **`shadow-panel` and `shadow-glow-*` are gone.** Apex is flat: depth is a border and a
  background step. Old markup referencing them would reintroduce a look the design rejects.
- **Typography is no longer system-only.** The vendored faces replace the `Bahnschrift` /
  system-ui stack. The fallbacks stay in the stack for a browser that refuses the woff2.

### Notes

- **The live path is untested here.** `history_deals_get` and `copy_rates_from_pos` require a
  running terminal, which this build machine does not have. The degradation path — package
  absent, empty result, HTTP error — is covered by tests; the happy path is not, and should be
  watched on first run against a real account.
- **`_tools/bridge_check.ps1` still forbids CDN references.** The design reference used Tailwind
  via CDN, Google Fonts and Material Symbols; all three were re-implemented into the vendored
  build with inline SVG icons.

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
