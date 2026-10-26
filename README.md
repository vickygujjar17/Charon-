# Charon

**A semi-automated MQL5 copilot for the part of trading that is not opinion.**

Charon does not predict direction. It does not read indicators to find entries, watch
news, or scan market structure. A human opens the trade; Charon takes over the
arithmetic and the discipline: a strict stop that is always there, a trailing engine
that only ever tightens, explicit reward:risk targets, position sizing that respects a
hard risk budget, and an optional pyramiding ladder with a basket-level ceiling.

Current release: **v1.6.2**

---

## Scope, stated plainly

| In scope | Out of scope (deliberately absent) |
|---|---|
| Strict stop loss enforcement | Entry signals |
| Trailing stop engine (4 modes) | Indicator-based direction calls |
| RR / points targets | News and session filters |
| Risk measurement and position sizing | Market-structure scanners |
| Pyramiding with basket risk ceiling | Anything that decides *when* to open the first trade |
| RR target ladder (display) | |

If you want an EA that finds trades, Charon is the wrong tool. If you want a copilot that
makes sure the trade you already took cannot hurt you more than you agreed to, it is the
right one.

---

## Design invariants

These are not features, they are guarantees the code is built around.

1. **No unprotected managed position.** Every tick, every adopted ticket has its stop
   verified against the broker. Missing or wrong-sided stops are re-armed immediately.
   If a stop cannot be placed, the position is flattened (configurable).
2. **Stops only tighten.** Trailing and ladder updates pass through a single improvement
   gate (`ChStopImproves`). A candidate that is not strictly better is discarded, so no
   code path can widen a stop.
3. **Risk never exceeds the budget.** Position size is rounded **down** to the broker's
   volume step, then re-verified against the exact applied money risk. If the result falls
   below the minimum lot, the trade is reported as unsizable rather than overshooting.
4. **Every exposure is sized against its own stop.** Pyramid legs are opened with a strict
   stop attached in the same `OrderSend` call, and basket risk is re-checked with the leg
   included.
5. **Nothing is decided silently.** Refusals are logged with the reason and the gate that
   rejected them.

---

## Files

```
Charon/
  Charon.mq5              EA entry point: OnInit / OnTick / OnTimer / OnTradeTransaction / OnChartEvent
  CharonDefines.mqh       enums, structs, every input, hard limits, CHARON_VERSION
  CharonMath.mqh          pure arithmetic: money, lots, stops, R, improvement gate
  CharonRisk.mqh          symbol spec, ATR, risk budget, position sizing
  CharonTrade.mqh         the ONLY module that calls OrderSend()
  CharonStops.mqh         strict stop arming/enforcement, RR targets, R ladder
  CharonTrail.mqh         trailing engine, 4 modes, high-water mark
  CharonPyramid.mqh       tier ladder, gates, scale-in execution
  CharonState.mqh         per-ticket table, persistence, basket rows
  CharonBridge.mqh        optional file bridge to the Python dashboard (no order verbs)
  CharonEngine.mqh        orchestration, basket view, dashboard
  CharonSelfTest.mq5      unit tests for the pure math (never trades)
  bridge/
    main.py               FastAPI app: snapshot in, dashboard + 3 buttons out
    charon_bridge/
      version.py          BRIDGE_VERSION, rewritten by bump_version.py
      paths.py            locates Common\Files\Charon, sanitises symbol names
      schema.py           the wire contract; the verb whitelist lives here
      state.py            reads the snapshot, decides live / stale / offline
      commands.py         atomic command writes, FIFO names, audit trail
      logs.py             ring buffer behind the SSE log tail
      mt5_adapter.py      optional read-only terminal diagnostic + deal/bars reads
      history.py          closed-trade pairing and statistics; no MT5 import
      journal.py          notes beside the ticket, never sent to the EA
    static/
      index.html          the dashboard
      app.js              renders it; never recomputes a price
      favicon.svg         inline mark as a file: obol + ferryman's pole
      tailwind.css        PREBUILT — no CDN, works offline
      fonts/              vendored Inter + JetBrains Mono woff2 (Latin subset)
    tests/                pytest tests
      fake_ea.py          stand-in EA: drains the inbox like CCharonBridge
      client_harness.js   executes app.js in node and checks it polls
      test_integration.py real uvicorn + fake EA across the file bridge
    src/input.css         Tailwind source (prebuilt into static/tailwind.css)
    tailwind.config.js
    package.json          Tailwind build only; version kept in lockstep
    requirements.txt
    requirements-dev.txt
    .env.example
  _tools/
    build_check.ps1       staged MetaEditor compile gate (0 errors / 0 warnings)
    bridge_check.ps1      Python gate: ruff, pytest, invariant assertions
    integration_check.ps1 two-process gate: real uvicorn + fake EA over the file bridge
    bump_version.py       keeps every version string in lockstep
  README.md
  CHANGELOG.md
  VERSION
  .gitignore
  LICENSE
```

Dependency direction is one-way and acyclic:

```
CharonMath -> CharonRisk -> CharonTrade -> CharonStops -> CharonTrail
                                              \-> CharonPyramid
                                     CharonState (leaf, broker reads only)
                                    CharonBridge (leaf, file I/O only)
                                                        \-> CharonEngine -> Charon.mq5
```

`CharonBridge` is a leaf: it depends on nothing but `CharonDefines` and `CharonMath`, so
it can never reach an order function even by accident. `CharonEngine` feeds it a
snapshot and hands it validated commands to apply.

---

## Installation

Charon installs into **two** folders, because its entry points reach the modules by
angle include and angle includes resolve against `MQL5\Include`:

1. Copy the two `.mq5` files into:
   `<terminal data folder>\MQL5\Experts\Charon\`
2. Copy the ten `.mqh` files into:
   `<terminal data folder>\MQL5\Include\Charon\`
3. Open `Charon.mq5` in MetaEditor and press **F7** (Compile).
4. Attach `Charon` to a chart. One instance manages one symbol.

Charon only touches positions whose symbol matches the chart symbol. Run it on as many
charts as you like: since v1.3.0 each instance has its own command inbox
(`commands\<SYMBOL>\`), so one chart can no longer consume another's commands. The
dashboard lists every chart it can see and lets you pick which one to drive.

A position on a symbol with **no** Charon chart is invisible to the EA by construction.
`GET /api/unmanaged` exists for exactly that case: it lists open positions no live chart
accounts for, and says plainly when it cannot check rather than reporting a false
all-clear.

> **The split is not optional.** `Charon.mq5` and `CharonSelfTest.mq5` include their
> modules with angle brackets (`#include <Charon\CharonDefines.mqh>`), and MQL5
> resolves angle includes against `MQL5\Include` — never against the folder holding
> the `.mq5`. Copy the `.mq5` files without the headers and compilation fails with
> `error 106: file 'Include\Charon\CharonDefines.mqh' not found`. The header folder
> must be named exactly `Charon`, because the name is part of every include path.
>
> The headers overwrite nothing in the standard library: no stock MQL5 header lives
> under `Include\Charon`. Upgrading is a plain copy over the top of both folders.
>
> **Do not compile `Charon.mq5` from a clone of this repository.** The repository
> keeps the `.mq5` and `.mqh` files side by side for review, which is not the
> installed layout. A build run in the repo would resolve `<Charon\...>` against
> whatever happens to be installed in your terminal, silently testing the wrong
> code. Use `_tools\build_check.ps1`, which stages a throwaway tree in the real
> layout; an F7 belongs in the terminal folder.
>
> `build_check.ps1` compiles exactly this layout, so `*** GATE PASSED ***` means the
> files you have would compile as an installed EA — not merely that a hand-built
> staging tree would.

### Migrating from v1.1.1 or earlier

Before v1.1.2 all twelve files lived together in `Experts\Charon\` and were joined by
sibling-relative quotes. If you are upgrading in place, **move** the ten `.mqh` files
out of `Experts\Charon\` into `Include\Charon\` — leaving them where they are is
harmless but pointless, since nothing reads that copy any more. An install left
un-migrated does not half-work: the new `.mq5` cannot see the old headers and the
build stops immediately.

---

## The workflow it is built for

1. You analyse the market and open a position manually, **with or without a stop**.
2. Charon adopts it within one tick (or immediately, via `OnTradeTransaction`).
3. If you set a stop, it is respected — that distance becomes **1R**.
4. If you did not, Charon arms one from your configured SL unit.
5. From then on: breakeven at 1R, optional half-risk at 0.5R, trailing after your
   activation threshold, and optional scale-ins at your R triggers.

Charon measures risk and sizes scale-ins; it does not open your first position.

---

## Input reference

### [0] General
| Input | Default | Meaning |
|---|---|---|
| `InpMagicFilter` | `0` | `0` = manage every position on the symbol (manual trades). Otherwise only this magic. **With `0`, an unrelated position on the chart's symbol is adopted as a Charon leg** — so on a busy account prefer a specific magic, or treat the unmanaged-position panel as the authoritative list. |

| `InpMagic` | `20261024` | Magic stamped on Charon's own scale-in legs. |
| `InpVerbose` | `true` | Log every stop/trail/tier decision. |
| `InpShowDashboard` | `true` | Chart-corner status panel. |
| `InpPersistState` | `true` | Persist per-ticket state in terminal global variables. |

### [1] Risk model
| Input | Default | Meaning |
|---|---|---|
| `InpLotMode` | `RISK_PCT` | `RISK_PCT`, `FIXED_MONEY`, or `FIXED_LOTS`. |
| `InpRiskPct` | `0.50` | Risk per trade as % of account. |
| `InpRiskMoney` | `50.0` | Fixed money risk per trade. |
| `InpFixedLots` | `0.10` | Volume for `FIXED_LOTS` mode. |
| `InpUseEquity` | `true` | Size off equity; `false` uses balance. |
| `InpMaxRiskPct` | `2.00` | Hard ceiling. Exceeding setups are flagged/refused. |
| `InpAllowMinLot` | `false` | Allow the broker minimum when risk rounds to zero. |
| `InpCommissionPerLot` | `0.0` | Round-turn commission per lot, folded into sizing. |

### [2] Strict stop loss
| Input | Default | Meaning |
|---|---|---|
| `InpSlUnit` | `ATR` | `POINTS`, `ATR`, or `PRICE_PCT`. |
| `InpSlPoints` | `300` | Stop distance in points. |
| `InpSlAtrMult` | `1.50` | Stop distance as an ATR multiple. |
| `InpSlPricePct` | `0.50` | Stop distance as % of entry price. |
| `InpAtrPeriod` | `14` | ATR period. |
| `InpStrictStopBufferPts` | `2.0` | Extra points beyond the broker's minimum stop level. |
| `InpMissingSlAction` | `ATTACH` | `ATTACH`, `CLOSE`, or `LOG_ONLY`. |
| `InpHalfRiskMode` | `OFF` | `OFF`, `HALVE` (0.5R), or `LOCK_EN` (breakeven early). |
| `InpHalfRiskRR` | `0.50` | Trigger for the half-risk stage. |
| `InpBreakevenRR` | `1.00` | Trigger for breakeven. |

### [3] RR targets
| Input | Default | Meaning |
|---|---|---|
| `InpTpUnit` | `NONE` | `NONE`, `RR`, or `POINTS`. |
| `InpTpRR` | `2.00` | Target as an R multiple of the strict stop. |
| `InpTpPoints` | `600` | Fixed-point target. |
| `InpApplyTpOnAdopt` | `true` | Write the target when a trade is adopted. |
| `InpRrLadderA` | `1.50` | Ladder rung A in R. `0` hides that rung. Display only — never writes a TP. |
| `InpRrLadderB` | `2.00` | Ladder rung B in R. |
| `InpRrLadderC` | `3.00` | Ladder rung C in R. |

### [4] Trailing stop
| Input | Default | Meaning |
|---|---|---|
| `InpTrailMode` | `OFF` | `OFF`, `POINTS`, `ATR`, `HWM_PERCENT`, `RR_LOCK`. |
| `InpTrailStartRR` | `1.00` | Activation in R. |
| `InpTrailStartPoints` | `0.0` | Alternative activation in points (`0` = off). |
| `InpTrailPoints` | `150` | Distance for `POINTS` mode. |
| `InpTrailAtrMult` | `1.50` | Distance for `ATR` mode. |
| `InpTrailGivebackPct` | `25.0` | `HWM_PERCENT`: max % of the gain given back. |
| `InpTrailGivebackRR` | `0.50` | `RR_LOCK`: R given back from the peak. |
| `InpTrailStepPoints` | `5.0` | Minimum improvement before a modify is sent. |

### [5] Pyramiding
| Input | Default | Meaning |
|---|---|---|
| `InpPyramidEnable` | `false` | Master switch. Off by default. |
| `InpPyramidMaxLegs` | `3` | Total legs including the primary (2–4). |
| `InpPyramidT2RR` / `T3` / `T4` | `1.0 / 2.0 / 3.0` | R trigger for each tier. |
| `InpPyramidT2Mult` / `T3` / `T4` | `0.50` | Lot multiplier vs the primary size. |
| `InpPyramidMaxRiskPct` | `1.50` | Refuse a tier that pushes basket risk above this. |
| `InpPyramidCollectiveBE` | `true` | Move all legs to breakeven before adding. |
| `InpPyramidCooldownSec` | `30` | Minimum seconds between scale-ins. |
| `InpPyramidTrigger` | `AUTO` | `AUTO` fires at the R trigger; `MANUAL` also requires an arming press. |
| `InpPyramidArmKey` | `65` | Virtual-key code that arms a MANUAL tier (`65` = `A`, `0` = arming disabled). |
| `InpPyramidArmTimeoutSec` | `60` | How long an arm stays live (`0` = never expires). |

### [6] Execution
| Input | Default | Meaning |
|---|---|---|
| `InpSlippagePoints` | `20` | Max deviation. |
| `InpOrderRetries` | `3` | `OrderSend` attempts. |
| `InpRetryDelayMs` | `300` | Delay between attempts. |
| `InpMaxSpreadPoints` | `0.0` | Block new legs above this spread (`0` = off). |

---

## Trailing modes

All four are driven by a per-position high-water mark (HWM) and pass the same
improvement gate.

| Mode | Stop candidate |
|---|---|
| `POINTS` | `HWM ∓ InpTrailPoints` |
| `ATR` | `HWM ∓ ATR × InpTrailAtrMult` |
| `HWM_PERCENT` | Keep `(1 − giveback%)` of the gain: `entry ± gain × (1 − pct/100)` |
| `RR_LOCK` | Lock `(progressR − InpTrailGivebackRR)` as a price |

Activation is `InpTrailStartRR` (primary) or `InpTrailStartPoints`; either arm it.

---

## RR ladder

The panel projects up to three R rungs — `InpRrLadderA/B/C`, default `1.5R / 2.0R / 3.0R` —
as **prices**, shown twice:

- once for the basket, projected from its volume-weighted **average entry** and the
  primary leg's R
- once per ticket, projected from that leg's own entry and its own R

```text
RR ladder   : 1.50R 1.10450 | 2.00R 1.10600 | 3.00R 1.10900
  #123456 BUY  T1 0.10 lots | R 300 pts | 0.42R | SL 1.09700
       -> 1.50R 1.10450 | 2.00R 1.10600 | 3.00R 1.10900
```

The ladder is **display only**. It shows where each rung sits relative to where the trade
actually is; it never writes a take profit. Setting a rung to `0` hides it, and rungs are
listed in the order you configured them rather than sorted, so a non-monotonic ladder stays
readable as you set it.

This is deliberately separate from `InpTpUnit`, which is the single target Charon actually
attaches to the position. A ladder of several resting targets would need partial closes —
an execution decision, and out of scope.

---

## Pyramiding

A tier fires only when **all** gates pass, evaluated in this order:

1. enabled, and `legs + 1 ≤ InpPyramidMaxLegs`
2. (MANUAL trigger only) the trader has armed the ladder
3. the tier's R trigger is reached
4. the basket has an unambiguous direction
5. the cooldown has elapsed
6. spread is inside the ceiling
7. projected basket risk stays under `InpPyramidMaxRiskPct`

Tier volume is `primarySize × tierMultiplier`, sized against that leg's own strict stop.
The leg is sent with its stop already attached, so the basket is never unprotected — not
even between the fill and the next tick.

### Trigger: AUTO or MANUAL

`InpPyramidTrigger = AUTO` (default) is the v1.0.1 behaviour: the R trigger fires the tier
on its own.

`InpPyramidTrigger = MANUAL` makes the R trigger a *necessary but not sufficient*
condition. The trader must first arm the ladder by pressing `InpPyramidArmKey` on the
chart (default `A`). One press authorises exactly one tier and is consumed when that leg
opens; `InpPyramidArmTimeoutSec` expires an arm that goes unused (default 60 s, `0` = never).

Arming only satisfies gate 2. Gates 3–7 still apply, so an arm can never force an exposure
add that the risk model would otherwise refuse. The dashboard shows the live state
(`ARMED 42s left`, `disarmed`, `expired`).

> **Chart focus:** MQL5 delivers `CHARTEVENT_KEYDOWN` only while the chart window has
> focus. Click the chart before pressing the key. The dashboard row is the confirmation
> that the press registered — if it still reads `disarmed`, the chart did not have focus.

### Lot decay preset

Tier volume scales linearly on the shared `refRiskPrice`, so no extra input is needed for
a decaying ladder. To reproduce `0.5 → 0.3 → 0.15` lots:

```text
InpPyramidT2Mult = 0.60
InpPyramidT3Mult = 0.30
InpPyramidT4Mult = 0.15
```

---

## Safety defaults

Charon ships in its least dangerous configuration:

- trailing **off**
- pyramiding **disabled**
- targets **off** (`InpTpUnit = NONE`)
- half-risk ladder **off**
- missing stop → **attach**, never silently ignore
- `InpMaxRiskPct = 2.00` hard ceiling

Nothing moves a stop or opens a trade until you enable it.

---

## The dashboard (optional)

Charon ships a local web panel. The EA is fully functional without it, and since v1.2.0
the bridge publishes by default: `InpBridgeEnable` is `true` out of the box, so a freshly
attached chart starts writing snapshots with no configuration. Set it to `false` to opt out.

MetaTrader stores input values per chart, so a chart that was attached before v1.2.0 keeps
whatever it saved — open its properties once and set `InpBridgeEnable` to `true`, or copy
the saved `.set` file to other charts.

```powershell
cd bridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8787
# open http://127.0.0.1:8787
```

### Why files instead of an HTTP server in the EA

MQL5 has no `SocketListen` or `SocketAccept`, and an EA cannot be called into from
outside the terminal. A shared directory is the only channel both sides can reach
without shipping a bridge DLL. `FILE_COMMON` (`%APPDATA%\MetaQuotes\Terminal\Common\
Files\Charon\`) is shared by every terminal on the machine and needs no `WebRequest`
URL allow-listing, so there is no security setting to weaken.

```
state_<SYMBOL>.json          EA writes once per timer tick, Python reads
commands\<SYMBOL>\*.json     Python writes, that chart's EA consumes
commands\<SYMBOL>\processed\ EA renames each consumed file here (ok_* / rejected_*)
commands\*.json              legacy flat inbox, still read by pre-v1.3.0 EAs
```

Both directions write a temp file and rename it into place, so neither side can ever
read a half-written document. Each command is retired **before** it is applied, so a
crash cannot replay it; a command stamped more than 60s away from the EA's clock is
refused outright.

### What the dashboard can and cannot do

| Can | Cannot |
|---|---|
| Show basket, legs, live stop, R progress | Choose a volume, a stop distance or an R multiple |
| Render the EA's RR ladder verbatim | Compute a rung price itself |
| Submit an order ticket (side, block, risk %, rung) | Bypass `CCharonRisk` — the EA sizes and prices it |
| Re-derive a stop from a new block (`order_modify`) | Loosen a stop, or move one without a block |
| Cancel a pending ticket or flatten a position | Act on a ticket the EA has not adjudicated |
| Arm / disarm the MANUAL pyramid trigger | Override any risk gate |
| Select the trailing mode | Move a stop directly |
| Pick which chart to drive, and search symbols | Send a display label as a symbol name |
| List positions no live chart accounts for (`/api/unmanaged`) | Close, size or modify any of them |
| Read closed deals and the statistics they imply (`/api/history`) | Change what was booked, or reopen anything |
| Attribute results to a session | Decide which session a trade "should" have been in |
| Write a journal note against a ticket (`POST /api/journal`) | Send that note to the chart, or turn it into an order |
| Tail the bridge log | Substitute for the Experts log |

### Closed trades, and what "not available" means

`GET /api/history` is the only source of realized results. The snapshot describes **open**
risk and nothing else: the moment a position closes it leaves the snapshot entirely, so win
rate, profit factor and the equity curve cannot be derived from it.

The endpoint reports `available: false` with a `reason` in three distinct cases, and they are
deliberately not merged:

* the MetaTrader5 package is not installed;
* the package is installed but the terminal could not be read;
* the symbol is not one the bridge can address.

None of those is the same as "no closed trades", which is `available: true` with
`metrics.total: 0`. A zeroed summary would be read as "you have lost nothing", so it is never
returned.

**Undefined ratios are `null`, not `0`.** With no losing trade, `profit_factor` is `null` rather
than a large number or infinity; with no closed trades, `win_rate` is `null` rather than `0.0`.
The panel prints `—` and names the reason. A profit factor of `999` reads as perfection and a
0% win rate reads as total failure; both are claims the data does not support.

**Sessions are cut on broker-server time.** MT5 deal times carry no offset, so the bridge
either infers it from the EA's `server_time` against its own clock or accepts
`tz_offset_minutes` explicitly. Either way `tz_offset_minutes` and `tz_source`
(`snapshot` / `request` / `assumed`) come back in the response, and the panel prints the clock
it used. A session bucket computed against an unstated clock is a label, not a fact.

### The journal is a sidecar, not a command

`POST /api/journal` writes
`<Common>\Files\Charon\journal_<SYMBOL>.json`. It cannot reach the EA, cannot open a position
and cannot close one. That is why it needs no live chart: a trade that closed an hour ago still
deserves a note, and refusing the write because the chart was removed would lose exactly the
reflection a journal exists for.

A ticket of `0` is refused here. Ticket `0` means "queue this note with the next order", which
is `POST /api/order`'s job; filing it as a completed entry would hide it from the row the
operator was looking at.

### Design notes

The dashboard is rebuilt to the Apex Institutional Terminal design, re-implemented into the
vendored stylesheet. The reference used Tailwind via CDN, Google Fonts and Material Symbols;
`_tools/bridge_check.ps1` forbids all three, and a panel that needs a network to render is not
a panel. Fonts are real `.woff2` files in `static/fonts/` with `@font-face` in
`src/input.css`; icons are inline SVG.

The v1.x colour tokens (`ink-*`, `shade-*`, `live`, `offline`, `stale`, `accent`, `violet`) are
**kept as aliases** onto the Apex values. `app.js` builds those class names at runtime as
complete literals and the test suite asserts on them, so renaming rather than aliasing would
strip the colour from every status pill with no error anywhere.

### Symbol names and the picker

A broker rarely names EURUSD the way a trader does: it may be `EURUSD.a`,
`EURUSDm` or `mEURUSD`. The dashboard shows a short label and sends the exact broker
name — the two are never interchangeable, and no code path can invent a name that the
EA is not publishing. `CHARON_SYMBOL_PREFIX` / `CHARON_SYMBOL_SUFFIX` (see
`bridge\.env.example`) tell the picker which part of a name is decoration; they affect
**display only**, so a wrong value makes labels noisy, never wrong-targeted.

`POST /api/order` pre-flight refuses (`422`) a ticket for a symbol with no live snapshot.
Sending one would queue a file that no chart could ever drain — the operator would see
"queued" and believe something was about to happen.

**The ticket's symbol field is a searchable combobox (v1.6.1).** The collapsed control shows
the display label; the value it posts lives in a hidden `#orderSymbol` and is always the exact
broker name. Typing filters on the broker name, the label and the asset class, so `eur`, the
suffix `eurusd.a` and the heading `forex` all find the same instrument. Rows are grouped under
their class with a short badge (`FX`, `MET`, `IDX`, …), and the list is capped at 50 painted
rows with an honest footer — a full MT5 install lists thousands, and a list nobody can scan is
not more helpful than a filtered one.

The picker does **not** restrict the choice. When nothing in the catalogue matches, whatever was
typed is offered verbatim as its own row and sent exactly as typed: a broker can list an
instrument the bridge cannot enumerate, and refusing a correct name would be worse than
accepting it. The only thing the client will not do is invent a name.

**The spread is labelled in points, never pips.** Points are defined by the instrument's own
`digits`, so `ask - bid` scaled by `10^digits` is true on a 5-digit FX pair, a 3-digit JPY pair
and a cash index alike. A "pip" is 10 points on the first, 1 point on the second and undefined
on the third, so it is the wrong unit on two of the three.

The quote beside the ticket is the one the bridge actually published, and it belongs to the
symbol the bridge is reading. Naming a different instrument says there is no quote for it rather
than showing a neighbouring price, and an EA that never published a tick (schema 2/3) says so
instead of printing a zero.

### Upgrading to schema 4 (v1.5.0)

The wire revision moved `3` -> `4`. The same asymmetry applies, but the gate is now **per
field** rather than per version, which is a change from v1.4.0:

| Direction | Behaviour |
|---|---|
| Snapshot, EA schema 2/3 -> v1.5.0 bridge | **Rendered.** `watch` defaults to empty and `bid`/`ask` to null, so an older chart shows no watches and no distance column. |
| Command, v1.5.0 bridge -> EA schema 3 | **Per field.** A ticket using only schema-3 semantics passes; anything schema-3 cannot honour is refused with a message naming it. |

v1.4.0 refused on `snapshot.schema < SCHEMA_VERSION`, which worked while there was one
frontier. With two it would have broken every schema-3 chart the moment the bridge learned
schema 4 — a schema-3 EA understands a journal note perfectly well. So each field carries
the revision that first honours it, and only fields above the chart's own revision are
refused. Two things are gated at 4:

* **`watch_add`** — a schema-3 EA does not know the verb at all and would reject the file.
* **`trail_start_price`** — a schema-3 EA would ignore the key and trail from its own
  default, so the operator would be told a level was armed when it was not.

`POST /api/order`, `/api/order/modify` and `/api/orders/watch/cancel` pre-flight it, so the
refusal is synchronous and readable instead of a silent no-op visible only in the Experts
log.

**Upgrade in this order:** recompile *every* chart with the v1.5.0 EA first, then restart
the bridge. Restarting the bridge first leaves schema-3 charts refusing watches and
absolute-price activation — loudly, with a message naming the field.

The command vocabulary is nine verbs: four state-only (`arm`, `disarm`, `trail_mode`,
`ping`), three for attended execution (`order_open`, `order_modify`, `order_cancel`) and two
for the watch queue (`watch_add`, `watch_cancel`). Both sides whitelist it independently, so
neither can widen the set alone.

The order verbs carry **intent, never size**. A ticket states a side, the block the stop
is derived from, a risk figure (a percentage **or** an amount of money, never both), the
rung it is stated against, and -- since v1.4.0 -- up to three pyramid stages, an optional
trail activation threshold, an optional basket-wide risk ceiling and a journal note. Since
v1.5.0 the trail threshold may also be stated as an absolute price. A ticket does not
contain a lot, a stop distance, a take-profit or an R multiple, because those are the EA's
answers: `CCharonRisk::LotForBudget` solves the volume from the block height and the
ticket's budget, clamps that budget to `InpMaxRiskPct`, and re-verifies the applied risk
before anything is sent. A pyramid stage is a **multiplier** against that solved volume, so
it cannot express a size either. `CharonTrade.mqh` is still the only module that calls
`OrderSend`.

The raw verbs an attacker would reach for — `open`, `close`, `modify`, `buy`, `sell`,
`size`, `cancel` — remain forbidden on both sides, joined in v1.5.0 by `watch` and
`watch_fire`: whether a level was reached is the EA's conclusion from its own tick, never
something the bridge can declare. `POST /api/trade` exists purely to answer **`405`** with
the reason, and points at `POST /api/order` instead.

Executing from the browser is attended, not autonomous: the EA applies its own gates
(`InpMaxRiskPct`, the broker's stop level, one basket per symbol, no opposite direction,
one working pending ticket) and reports a verdict — `accepted`, `pending`, `watching` or
`rejected` with the reason — in the snapshot's `order` block, which the panel renders in the
EA's own numbers.

### Watched entries (v1.5.0)

A `MARKET` ticket that names an `entry` price no longer fills at market. It arms a **watch**,
and the EA submits the ticket itself once its own tick reaches that price. Before this,
`ApplyOrderCommand` replaced the entry with the live ask or bid for any MARKET ticket, so an
operator who typed `1.1050` next to MARKET got whatever the market was — and the dashboard
said "queued".

| Ticket | Behaviour |
|---|---|
| `MARKET`, no entry | Fills now. The pre-v1.5.0 default, unchanged. |
| `MARKET`, with entry | `watch_add`. Nothing is submitted until the price is reached. |
| `LIMIT`, with entry | `order_open`. The broker holds it; no second mechanism. |

A watch is **not** a pending order and deliberately does not share its table or its cancel
path: nothing exists at the broker, so it has no ticket, and it is cancelled by the id the
EA minted via `POST /api/orders/watch/cancel`. The panel shows the two queues separately for
that reason.

The trigger test is *touched or crossed*, using the ask for a BUY and the bid for a SELL —
the price the fill would actually use. Equality is never tested: a level the market gaps
over must still fire, and an exact-tick comparison would silently never trigger.

At most `CHARON_BRIDGE_MAX_WATCHES` (8) watches are held at once. The queue is persisted to
`MQL5\Files\Charon\watches.txt` and restored on init, so a recompile does not lose an entry
the operator set. It is deliberately **not** saved when `InpPersistState` is false.

The one thing a watch cannot survive is being fired from `OnInit`: the EA restores the queue
there but evaluates it on the first `OnTickWork`, because a chart reload can carry a stale
cached tick and firing a trade against it would be worse than waiting one tick.

### Honesty about staleness

The panel polls once a second. Past **5 seconds** of snapshot age the header turns amber
and a sticky banner reads *STALE DATA — do not act on these numbers*; past **30 seconds**
it turns red and reports the EA offline. A missing ladder field renders as *not reported*
rather than a plausible-looking price. Python never recomputes a risk figure: it has no
tick data, and a number that looks authoritative but disagrees with the chart is worse
than no number.

The stylesheet is precompiled and vendored (`static/tailwind.css`), so the panel renders
correctly with no network access. The server binds `127.0.0.1` only; set `CHARON_TOKEN`
to require `X-Charon-Token` on the account-bearing `/api/*` routes if other local processes
are a concern. `/api/health` and `/api/logs` stay open on purpose — a supervisor needs the
first, and an `EventSource` cannot send custom headers for the second. Neither exposes
account data.

---

## Building and testing

Charon is validated by a staged compile gate. It never compiles against your live
terminal headers and never deploys anything — the `.ex5` lands in `%TEMP%`.

```powershell
# 0 errors / 0 warnings required, and every header must come from the staging tree
powershell -NoProfile -ExecutionPolicy Bypass -File _tools\build_check.ps1

# version strings must agree everywhere (EA, #property, VERSION, bridge, README)
python _tools\bump_version.py --check

# Python bridge: ruff + pytest + the no-order-path invariants
powershell -NoProfile -ExecutionPolicy Bypass -File _tools\bridge_check.ps1

# two-process gate: a real uvicorn serves a POST that the stand-in EA drains
powershell -NoProfile -ExecutionPolicy Bypass -File _tools\integration_check.ps1
```

The gate compiles **both** `Charon.mq5` and `CharonSelfTest.mq5`, staged in the documented
install layout — entry points in `MQL5\Experts\Charon\`, modules in
`MQL5\Include\Charon\` — and fails if any `#include` resolved outside the staging tree or
outside `Include\Charon`, so a green result means *this commit* compiled as an installed EA,
not some stale deployed copy and not a tree of convenience that the docs never mention. It
also aborts if either staged half does not match its source set, so a partial copy cannot be
compiled as if it were whole.

`integration_check.ps1` is the one gate that exercises both halves at once. It starts an
actual uvicorn process against a throwaway `Common\Files` tree and drives the full loop —
`POST /api/pyramid/arm` → `commands\<SYMBOL>\*.json` → the stand-in EA drains it →
`commands\<SYMBOL>\processed\ok_*.json` → `GET /api/commands` reports `accepted`. The
bridge, the HTTP surface and every file crossing the filesystem are real; only the EA's
side of the handshake is simulated, because CI has no MetaTrader terminal. Nothing there
touches the network beyond `127.0.0.1` or writes to your real terminal folder.

`bridge_check.ps1` is deliberately offline: it never installs a package or downloads a
wheel. Besides linting and running the tests, it asserts three things that are easy to
break by accident — that no bridge module calls an MT5 order function, that
`POST /api/trade` still refuses with `405`, and that the dashboard stylesheet is
vendored rather than pulled from a CDN.

### Unit tests

`CharonSelfTest.mq5` asserts the pure arithmetic: money conversion, lot rounding (always
down), stop geometry for all three SL units, R projection and progress, the improvement
gate, HWM tracking, clamps, the pyramid tier model, the RR ladder projection, manual arm
expiry, and the bridge's command vocabulary and JSON helpers. It performs no trades and
needs no market data. Run it on any chart and read the log:

```
SELFTEST RESULT: PASS (147/147)
```

The bridge's 168 Python tests run offline:

```powershell
cd bridge
.\.venv\Scripts\python.exe -m pytest tests -q
```

`test_ladder.py` pins the invariant that a missing ladder surfaces as `null` rather than
a recomputed price. `test_live_server.py` starts a real uvicorn process and speaks HTTP
to it, because static mounting and the vendored stylesheet are exactly the things
in-process tests cannot see.

Two of these suites exist because of a regression that shipped in v1.0.3.

`test_client_script.py` checks the *shape* of `static/app.js`, not its syntax. A
truncating edit had left one function unclosed, so the remaining functions and the
`boot()` call were nested inside it: the file parsed, the page loaded, and no request
was ever made to `/api/status`. `node --check` passed the whole time, because
parseability is not execution. The suite now asserts that braces balance to zero, that
every `function` is declared at top level, that nothing follows the final `boot()`, and
that every `$("id")` exists in `index.html`.

Shape alone is still not proof, so `tests/client_harness.js` goes further: it executes
`app.js` in a real node VM against a DOM stub and requires that the client actually
requests `/api/status`, writes the equity, colours the pill, builds the leg row and
renders both ladder rungs. Run against the v1.0.3 file it reports every one of those as
missing, which is exactly what the operator saw. A second test points the same harness
at a deliberately inert page and requires it to fail, so the check cannot become a
rubber stamp.

`test_integration.py` closes the loop the unit tests cannot: a real uvicorn process, a
real `Common\Files` tree, `POST /api/pyramid/arm` → a file in `commands/` → drained,
retired as `ok_*` → `GET /api/commands` reporting the verdict. It also drives the
staleness ladder live → stale → offline and proves SSE `Last-Event-ID` resumes without
replaying lines. There is no MetaTrader terminal in CI, so `tests/fake_ea.py`
impersonates `CCharonBridge` — FIFO ordering, the verb whitelist, the ±60s anti-replay
window, retire-before-apply. The bridge, the HTTP surface and every byte crossing the
filesystem are real; only the EA is a stand-in.

---

## Versioning

`CHARON_VERSION` in `CharonDefines.mqh` is the single source of truth for the release
string. `#property version` must use MetaEditor's numeric `xxx.yyy` form, which mirrors it
(`1.0.0 → 1.00`, `1.2.3 → 1.02`). `BRIDGE_VERSION` in `bridge/charon_bridge/version.py`
carries the same release string, so the EA and the panel can never claim different
versions — `bump_version.py --check` fails if the six disagree. Each literal is checked in
place: `CHARON_VERSION`, every `#property version`, `VERSION`, `BRIDGE_VERSION`,
`bridge/package.json` and the README's *Current release* line.

Every change bumps the version, and every commit subject starts with it:

```
v1.0.0: initial Charon core — strict stops, trailing engine, RR/risk model, pyramiding
```

Use `_tools/bump_version.py` rather than editing version literals by hand.

---

## Limitations

- **One symbol per chart.** One instance manages one symbol; the basket model assumes a
  single instrument.
- **Hedging account expected.** Charon tracks positions per ticket. On a netting account,
  separate legs of the same symbol merge into one position and tiers collapse.
- **ATR needs history.** With `InpSlUnit = ATR`, the first ticks after attaching may have
  no ATR value; Charon then falls back to the broker's minimum stop and re-arms once ATR
  is ready.
- **Broker stop levels win.** If the broker's minimum distance is larger than your
  configured stop, the stop is widened to the broker minimum. This is logged.
- **No partial closes.** The scope is stops, trails, targets and sizing — Charon does not
  scale out.
- **Not a strategy-tester EA.** Because entries are human, there is nothing for the tester
  to drive. Validate on a demo account.
- **The EA runs standalone.** The Python dashboard is optional. With
  `InpBridgeEnable = false` nothing writes to `Common\Files` and every trading feature
  above works identically. It defaults to `true` since v1.2.0, so check that input
  before wondering why a snapshot directory appeared.
- **One bridge per terminal.** Snapshot names are per symbol, so several charts can run
  at once, but two bridge processes pointed at the same terminal would race on the
  command inbox.
- **The dashboard log is not the trading record.** It is a bridge-side transcript; the
  terminal's Experts log remains authoritative for what the EA decided and why.

---

## Licence

MIT — see `LICENSE`.

