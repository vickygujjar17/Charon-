"""FastAPI surface for the Charon dashboard.

Design constraints, in priority order:

1. **The EA owns trading.** There is no endpoint that can open, close,
   size or modify a position. ``POST /api/trade`` exists only to return
   ``405`` with the reason, so an operator who assumes otherwise gets a
   clear answer instead of a 404 they might read as "wrong URL".
2. **Loopback only by default.** Binds ``127.0.0.1`` unless explicitly
   told otherwise; a trading dashboard should not be reachable from the
   LAN because someone forgot a flag.
3. **Fail honest.** A stale snapshot is reported as stale, not smoothed
   over. Missing fields surface as ``null``, never as an invented number.

Run with::

    uvicorn main:app --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from charon_bridge import commands, mt5_adapter, paths, state
from charon_bridge.logs import LOG
from charon_bridge.schema import (
    ALLOWED_VERBS,
    FORBIDDEN_VERBS,
    TRAIL_MODE_NAMES,
    SchemaError,
)
from charon_bridge.version import BRIDGE_VERSION, SCHEMA_VERSION

STATIC_DIR = Path(__file__).resolve().parent / "static"

# --- optional shared secret. Unset means "loopback trust": acceptable for
# --- a single-user desktop, and the README says exactly that.
TOKEN_ENV = "CHARON_TOKEN"

app = FastAPI(
    title="Charon bridge",
    version=BRIDGE_VERSION,
    description=(
        "Read-mostly window onto a running Charon EA. The EA remains the "
        "only component that talks to the broker."
    ),
)


#+------------------------------------------------------------------+
#| Auth                                                             |
#+------------------------------------------------------------------+
def require_token(x_charon_token: str | None = Header(default=None)) -> None:
    """Enforce ``X-Charon-Token`` when one is configured.

    When ``CHARON_TOKEN`` is unset the check is a no-op: the server is
    bound to loopback and the threat model is "another local process",
    which a token would not meaningfully stop.
    """
    expected = os.environ.get(TOKEN_ENV)
    if not expected:
        return
    if not x_charon_token or not hmac.compare_digest(x_charon_token, expected):
        raise HTTPException(status_code=401, detail="missing or invalid X-Charon-Token")


def _symbol_or_404(symbol: str | None) -> str:
    """Resolve an explicit symbol, or the only one available."""
    if symbol:
        return paths.sanitize_symbol(symbol)
    found = state.discover_symbols()
    if not found:
        raise HTTPException(
            status_code=503,
            detail=(
                "no Charon snapshot found. Enable InpBridgeEnable on the chart, "
                f"or set {paths.ENV_OVERRIDE} to the terminal's Common\\Files folder."
            ),
        )
    return found[0]


#+------------------------------------------------------------------+
#| Status                                                           |
#+------------------------------------------------------------------+
@app.get("/api/health")
def health() -> dict[str, Any]:
    """Liveness plus where the bridge is looking. No auth: it leaks nothing."""
    return {
        "ok": True,
        "bridge_version": BRIDGE_VERSION,
        "schema": SCHEMA_VERSION,
        "mt5_available": mt5_adapter.available(),
        "locations": state.locate(),
        "symbols": state.discover_symbols(),
    }


@app.get("/api/status", dependencies=[Depends(require_token)])
def status(symbol: str | None = Query(default=None)) -> dict[str, Any]:
    """The snapshot, plus its age and a verdict the UI can colour."""
    resolved = _symbol_or_404(symbol)
    try:
        reading = state.read_snapshot(resolved)
    except state.StateUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    snap = reading.snapshot
    return {
        "status": reading.status,
        "age_seconds": round(reading.age_seconds, 2),
        "stale_after": state.STALE_AFTER_SECONDS,
        "offline_after": state.OFFLINE_AFTER_SECONDS,
        "snapshot": {
            "symbol": snap.symbol,
            "seq": snap.seq,
            "ea_version": snap.ea_version,
            "server_time": snap.server_time,
            "ready": snap.ready,
            "digits": snap.digits,
            "currency": snap.currency,
            "balance": snap.balance,
            "equity": snap.equity,
            "basket": {
                "valid": snap.basket.valid,
                "mixed": snap.basket.mixed,
                "direction": snap.basket.direction,
                "legs": snap.basket.legs,
                "lot": snap.basket.lot,
                "avg_entry": snap.basket.avg_entry,
                "open_pl": snap.basket.open_pl,
                "risk_money": snap.basket.risk_money,
                "risk_pct": snap.basket.risk_pct,
                "best": snap.basket.best,
                # --- verbatim from the EA; null means "not reported"
                "ladder": snap.basket.ladder,
            },
            "model": {
                "sl": snap.model.sl,
                "tp": snap.model.tp,
                "sizing": snap.model.sizing,
                "max_risk_pct": snap.model.max_risk_pct,
            },
            "trail": {
                "mode": snap.trail.mode,
                "mode_name": snap.trail.mode_name,
                "mutable": snap.trail.mutable,
                "options": TRAIL_MODE_NAMES,
            },
            "pyramid": {
                "enabled": snap.pyramid.enabled,
                "max_legs": snap.pyramid.max_legs,
                "max_risk_pct": snap.pyramid.max_risk_pct,
                "trigger": snap.pyramid.trigger,
                "arm_key": snap.pyramid.arm_key,
                "armed": snap.pyramid.armed,
                "secs_left": snap.pyramid.secs_left,
                "next_tier": snap.pyramid.next_tier,
                "progress_rr": snap.pyramid.progress_rr,
            },
            "legs": [
                {
                    "ticket": leg.ticket,
                    "side": leg.side,
                    "tier": leg.tier,
                    "entry": leg.entry,
                    "lot": leg.lot,
                    "sl": leg.stop,
                    "risk_price": leg.risk_price,
                    "hwm": leg.hwm,
                    "progress_rr": leg.progress_rr,
                    "trail_active": leg.trail_active,
                    "be_done": leg.be_done,
                    "half_risk_done": leg.half_risk_done,
                    "ladder": leg.ladder,
                }
                for leg in snap.legs
            ],
        },
    }


@app.get("/api/positions", dependencies=[Depends(require_token)])
def positions(symbol: str | None = Query(default=None)) -> dict[str, Any]:
    """Just the legs and the basket totals."""
    payload = status(symbol)
    snap = payload["snapshot"]
    return {
        "status": payload["status"],
        "age_seconds": payload["age_seconds"],
        "symbol": snap["symbol"],
        "basket": snap["basket"],
        "legs": snap["legs"],
    }


@app.get("/api/terminal", dependencies=[Depends(require_token)])
def terminal() -> dict[str, Any]:
    """Optional MT5 diagnostic; reports ``available: false`` when absent."""
    return mt5_adapter.describe().as_dict()


#+------------------------------------------------------------------+
#| Commands (never orders)                                          |
#+------------------------------------------------------------------+
@app.post("/api/pyramid/arm", dependencies=[Depends(require_token)])
def arm(symbol: str | None = Query(default=None)) -> dict[str, Any]:
    """Arm the MANUAL pyramid trigger -- exactly what the arm hotkey does."""
    resolved = _symbol_or_404(symbol)
    try:
        sent = commands.submit("arm", resolved)
    except (SchemaError, commands.CommandError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    LOG.command(f"arm queued for {resolved} ({sent['file']})")
    return {"queued": True, "command": sent}


@app.post("/api/pyramid/disarm", dependencies=[Depends(require_token)])
def disarm(symbol: str | None = Query(default=None)) -> dict[str, Any]:
    """Cancel a pending arm before it is consumed."""
    resolved = _symbol_or_404(symbol)
    try:
        sent = commands.submit("disarm", resolved)
    except (SchemaError, commands.CommandError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    LOG.command(f"disarm queued for {resolved} ({sent['file']})")
    return {"queued": True, "command": sent}


@app.post("/api/trail", dependencies=[Depends(require_token)])
def set_trail(mode: int = Query(ge=0, le=4),
              symbol: str | None = Query(default=None)) -> dict[str, Any]:
    """Select the trailing mode.

    This changes *how* existing stops are trailed; it cannot create a
    position or move a stop on its own. The EA still enforces every gate
    in ``CCharonTrail::NextStop`` before a stop is touched.
    """
    resolved = _symbol_or_404(symbol)
    try:
        sent = commands.submit("trail_mode", resolved, mode=mode)
    except (SchemaError, commands.CommandError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    LOG.command(f"trail_mode={mode} ({TRAIL_MODE_NAMES[mode]}) queued for {resolved}")
    return {"queued": True, "command": sent}


@app.post("/api/trade")
def trade() -> JSONResponse:
    """Always refuses. Documented so the boundary is explicit.

    Python never originates an order: ``CharonTrade.mqh`` is the only
    module that calls ``OrderSend``, and keeping it that way is what makes
    Charon's risk model auditable.
    """
    return JSONResponse(
        status_code=405,
        content={
            "error": "method_not_allowed",
            "detail": (
                "The Charon bridge cannot place trades. Orders are originated "
                "only by CharonTrade.mqh inside the EA, under the EA's risk model."
            ),
            "allowed_verbs": list(ALLOWED_VERBS),
            "forbidden_verbs": list(FORBIDDEN_VERBS),
        },
    )


@app.get("/api/commands", dependencies=[Depends(require_token)])
def list_commands(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """Inbox depth plus the EA's verdict trail."""
    return {
        "pending": commands.pending(),
        "processed": commands.recent(limit),
        "allowed_verbs": list(ALLOWED_VERBS),
    }


#+------------------------------------------------------------------+
#| Logs (SSE)                                                       |
#+------------------------------------------------------------------+
@app.get("/api/logs")
async def logs(stream: bool = Query(default=False)) -> Any:
    """Bridge-side transcript.

    ``?stream=true`` returns server-sent events; the plain form returns
    the buffer so the page can render something before the stream opens.
    Deliberately unauthenticated: it carries only bridge messages, and an
    ``EventSource`` cannot send custom headers.
    """
    if not stream:
        return {
            "entries": [entry.as_dict() for entry in LOG.tail(200)],
            "latest_seq": LOG.latest_seq,
        }

    async def events():
        cursor = 0
        # --- replay what already happened so a late subscriber is not blind
        for entry in LOG.tail(50):
            cursor = max(cursor, entry.seq)
            yield f"data: {json.dumps(entry.as_dict())}\n\n"
        while True:
            await asyncio.sleep(0.5)
            for entry in LOG.since(cursor):
                cursor = max(cursor, entry.seq)
                yield f"data: {json.dumps(entry.as_dict())}\n\n"
            yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


#+------------------------------------------------------------------+
#| Static dashboard                                                 |
#+------------------------------------------------------------------+
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> Any:
    """Serve the dashboard shell.

    The CSS is vendored locally, so the page renders correctly with no
    network access -- important when it is open next to a live position.
    """
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        return JSONResponse(
            status_code=503,
            content={"error": "dashboard_missing", "detail": f"{page} does not exist"},
        )
    return FileResponse(page)


@app.on_event("startup")
def _startup() -> None:
    """Log where we are looking, so a misconfigured path is obvious."""
    where = state.locate()
    LOG.info(f"Charon bridge {BRIDGE_VERSION} (schema {SCHEMA_VERSION})")
    LOG.info(f"common files: {where['common_files']} [{where['common_exists']}]")
    LOG.info(f"bridge root : {where['bridge_root']} [{where['root_exists']}]")
    if not mt5_adapter.available():
        LOG.info("MetaTrader5 package absent - dashboard reads the EA snapshot only")
    try:
        paths.ensure_dirs()
    except OSError as exc:
        LOG.error(f"could not create bridge directories: {exc}")

