# Charon bridge

A local, read-mostly web panel for a running Charon EA.

**The EA owns trading.** This service can show you what the EA knows and flip three
switches the trader could already reach from the keyboard. It cannot place, close, size
or modify an order. That is enforced, not merely intended: there is no verb that could
express one, and `POST /api/trade` returns `405` on purpose.

## Quick start

```powershell
cd bridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Then open <http://127.0.0.1:8787>. `main.py` reads `CHARON_HOST` and `CHARON_PORT`, so
`$env:CHARON_PORT = 9001` moves it without editing a file. `uvicorn main:app --host
127.0.0.1 --port 8787` is the equivalent explicit form.

On the chart, set:

| Input | Value | Why |
|---|---|---|
| `InpBridgeEnable` | `true` | publishes the snapshot and drains commands |
| `InpPyramidTrigger` | `MANUAL` | otherwise arming has no effect |
| `InpPyramidEnable` | `true` | otherwise there is no tier to arm |

Verified on Python 3.14 with `fastapi 0.162.0` and `uvicorn 0.54.0`.

## The channel

```
%APPDATA%\MetaQuotes\Terminal\Common\Files\Charon\
  state_<SYMBOL>.json          EA -> Python, once per timer tick
  commands\*.json              Python -> EA, drained one per tick
  commands\processed\*.json    audit trail: ok_* / rejected_*
```

* Both directions write a temp file then rename, so a reader never sees a fragment.
* A command is renamed into `processed\` **before** it is applied. A crash between read
  and apply cannot replay it, and nothing is ever deleted.
* Every command carries `issued` (epoch seconds). The EA refuses anything more than
  **60s** away from its own clock, so a file left on disk and reused later is inert.
* `schema` must match `CHARON_BRIDGE_SCHEMA` (currently `1`) on both sides.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | the dashboard |
| GET | `/api/health` | liveness + where the bridge is looking (no auth) |
| GET | `/api/status` | snapshot, age, and a `live`/`stale`/`offline` verdict |
| GET | `/api/positions` | legs and basket totals only |
| GET | `/api/terminal` | optional MT5 diagnostic, `available: false` when absent |
| GET | `/api/logs` | bridge transcript; `?stream=true` for SSE |
| GET | `/api/commands` | inbox depth and the EA's verdict trail |
| POST | `/api/pyramid/arm` | same call the arm hotkey makes |
| POST | `/api/pyramid/disarm` | cancel a pending arm |
| POST | `/api/trail?mode=0..4` | select the trailing mode |
| POST | `/api/trade` | **always `405`** — documents the boundary |

## Configuration

Copy `.env.example` to `.env` and pass `--env-file .env` to uvicorn, or set the variables
in your shell before `python main.py`.

| Variable | Default | Meaning |
|---|---|---|
| `CHARON_HOST` | `127.0.0.1` | keep it loopback; this panel shows your account |
| `CHARON_PORT` | `8787` | listen port, used when you start with `python main.py` |
| `CHARON_TOKEN` | _unset_ | when set, `/api/*` requires `X-Charon-Token` |
| `CHARON_COMMON_DIR` | auto | override the terminal's `Common\Files` path |

`/api/health` and `/api/logs` stay unauthenticated: a supervisor needs the first, and an
`EventSource` cannot send custom headers for the second. Neither exposes account data.

## Layout

```
main.py                  the FastAPI app
charon_bridge/
  version.py             BRIDGE_VERSION (kept in lockstep by bump_version.py)
  paths.py               finds Common\Files, sanitises symbol names
  schema.py              the wire contract + the verb whitelist
  state.py               reads the snapshot, decides live / stale / offline
  commands.py            atomic writes, FIFO names, audit trail
  logs.py                ring buffer behind the SSE tail
  mt5_adapter.py         optional read-only terminal diagnostic
src/
  input.css              Tailwind source, compiled into static/tailwind.css
static/
  index.html             the dashboard
  app.js                 rendering; never computes a price
  tailwind.css           prebuilt - no CDN, works offline
tests/                   112 pytest tests
  conftest.py            synthetic EA snapshot in the real wire format
  fake_ea.py             stand-in EA: drains the inbox like CCharonBridge
  test_integration.py    real uvicorn + fake EA across the file bridge
  client_harness.js      runs app.js in node and checks it polls
package.json             Tailwind build only
tailwind.config.js
```

## Why there is no file lock

The channel is shared with a running terminal, so it is fair to ask what stops two
writers from corrupting a file. Nothing locks, and nothing needs to:

* every command is written to a temp file, `fsync`ed, then `os.replace`d into place, so a
  reader sees the whole document or the previous one — never a fragment;
* command names carry a `uuid4` suffix, so two bridge processes cannot collide on a name;
* the temp suffix is not `*.json`, so the EA's `*.json` glob can never pick one up;
* the EA retires a command by moving it out of the inbox, and `recent()` skips a file that
  vanishes between listing and stat rather than failing the request.

A lock file would introduce a different failure mode — a crashed process leaving a stale
lock that silently stops every future command — to solve a race that atomic rename already
removes.

## Rebuilding the stylesheet

`static/tailwind.css` is committed so the panel needs no network. It only needs
rebuilding when the markup changes:

```powershell
npm install                       # once
npx tailwindcss -c tailwind.config.js -i src/input.css -o static/tailwind.css --minify
```

`tailwind.config.js` scans `static/app.js` as well as `index.html`, because the status
colours and the `.rung` component are assembled as strings at runtime — without that,
the build silently omits them and the banner renders colourless.

## Testing

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check charon_bridge main.py tests
```

Or run the full gate from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File _tools\bridge_check.ps1
```

The gate is offline by design and additionally asserts that no module calls an MT5 order
function, that `/api/trade` still refuses, and that the CSS is vendored.

## Design notes

**Files, not sockets.** MQL5 has no `SocketListen`/`SocketAccept` and an EA cannot be
called into, so a shared directory is the only channel both sides reach without a bridge
DLL. `FILE_COMMON` needs no `WebRequest` allow-listing.

**The ladder is opaque.** The EA renders RR rungs with `RrLadderText()`; Python and the
browser pass that string through untouched. A rung on the panel therefore cannot disagree
with the rung on the chart. When the field is absent the UI says *not reported* instead of
inventing a price.

**Staleness is a safety property.** Past 5s the panel turns amber and says not to act on
the numbers; past 30s it reports the EA offline. Age is measured from the file's mtime, not
the EA's clock, so a skewed terminal cannot disguise a dead EA as live.

**Nothing recomputes risk.** Python has no tick data. Every risk figure, RR multiple and
tier trigger is copied from the snapshot.

## Licence

MIT — see the repository `LICENSE`.
