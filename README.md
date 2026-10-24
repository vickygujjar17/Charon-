# Charon

**A semi-automated MQL5 copilot for the part of trading that is not opinion.**

Charon does not predict direction. It does not read indicators to find entries, watch
news, or scan market structure. A human opens the trade; Charon takes over the
arithmetic and the discipline: a strict stop that is always there, a trailing engine
that only ever tightens, explicit reward:risk targets, position sizing that respects a
hard risk budget, and an optional pyramiding ladder with a basket-level ceiling.

Current release: **v1.0.6**

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
      mt5_adapter.py      optional read-only terminal diagnostic
    static/
      index.html          the dashboard
      app.js              renders it; never recomputes a price
      tailwind.css        PREBUILT — no CDN, works offline
    tests/                112 pytest tests
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

1. Copy the whole `Charon` folder into your terminal:
   `<terminal data folder>\MQL5\Experts\Charon\`
2. In MetaEditor, open `Charon.mq5` and press **F7** (Compile).
3. Attach `Charon` to a chart. One instance manages one symbol.

Charon only touches positions whose symbol matches the chart symbol.

> **All 12 MQL5 files must stay in the same folder.** `Charon.mq5` includes its
> headers with sibling-relative paths (`#include "CharonDefines.mqh"`), which MQL5
> resolves against the *including file's own directory*. Copy `Charon.mq5` on its own
> and compilation fails with
> `error 106: file 'Experts\CharonDefines.mqh' not found` — the exact error you get
> when only the `.mq5` was copied. Copy the folder, not the file.
>
> The destination folder name is yours to choose: `Experts\Charon\` (documented
> default) and a flat `Experts\` both work, because the includes are relative to the
> files rather than to `Experts`. What is not optional is that the ten headers and
> the two `.mq5` files sit together.
>
> `build_check.ps1` compiles exactly this layout, so `*** GATE PASSED ***` means the
> files you have would compile as an installed EA — not merely that a hand-built
> staging tree would.

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
| `InpMagicFilter` | `0` | `0` = manage every position on the symbol (manual trades). Otherwise only this magic. |
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

Charon ships a local web panel. It is **off by default** and the EA is fully functional
without it — enable `InpBridgeEnable` on the chart when you want it.

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
commands\*.json              Python writes, EA consumes
commands\processed\*.json    EA renames each consumed file here (ok_* / rejected_*)
```

Both directions write a temp file and rename it into place, so neither side can ever
read a half-written document. Each command is retired **before** it is applied, so a
crash cannot replay it; a command stamped more than 60s away from the EA's clock is
refused outright.

### What the dashboard can and cannot do

| Can | Cannot |
|---|---|
| Show basket, legs, live stop, R progress | Place, close, size or modify an order |
| Render the EA's RR ladder verbatim | Compute a rung price itself |
| Arm / disarm the MANUAL pyramid trigger | Override any risk gate |
| Select the trailing mode | Move a stop directly |
| Tail the bridge log | Substitute for the Experts log |

The command vocabulary is four verbs — `arm`, `disarm`, `trail_mode`, `ping` — and both
sides whitelist it independently, so neither can widen the set alone. There is no verb
that can express an order. `POST /api/trade` exists purely to answer **`405`** with the
reason, so someone who assumes otherwise gets a straight answer.

Arming from the browser is the same call the `A` hotkey makes, and every other gate —
max legs, cooldown, spread, basket risk — still applies. An arm can never force an
exposure add the risk model would reject.

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
install layout (`MQL5\Experts\Charon\`), and fails if any `#include` resolved outside the
staging tree — so a green result means *this commit* compiled as an installed EA, not some
stale deployed copy and not a tree of convenience that the docs never mention. It also
aborts if the staged file set does not match the source set, so a partial copy cannot be
compiled as if it were whole.

`integration_check.ps1` is the one gate that exercises both halves at once. It starts an
actual uvicorn process against a throwaway `Common\Files` tree and drives the full loop —
`POST /api/pyramid/arm` → `commands\*.json` → the stand-in EA drains it →
`commands\processed\ok_*.json` → `GET /api/commands` reports `accepted`. The bridge, the
HTTP surface and every file crossing the filesystem are real; only the EA's side of the
handshake is simulated, because CI has no MetaTrader terminal. Nothing there touches the
network beyond `127.0.0.1` or writes to your real terminal folder.

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
SELFTEST RESULT: PASS (135/135)
```

The bridge's 102 Python tests run offline:

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
  above works identically.
- **One bridge per terminal.** Snapshot names are per symbol, so several charts can run
  at once, but two bridge processes pointed at the same terminal would race on the
  command inbox.
- **The dashboard log is not the trading record.** It is a bridge-side transcript; the
  terminal's Experts log remains authoritative for what the EA decided and why.

---

## Licence

MIT — see `LICENSE`.

