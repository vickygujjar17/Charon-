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

    python main.py

``CHARON_HOST`` / ``CHARON_PORT`` configure the bind address that way;
``uvicorn main:app`` still works and uses uvicorn's own flags instead.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from charon_bridge import commands, journal, mt5_adapter, paths, reconcile, state, symbols
from charon_bridge.logs import LOG
from charon_bridge.schema import (
    ALLOWED_VERBS,
    FORBIDDEN_VERBS,
    MAX_JOURNAL_LEN,
    MAX_STAGES,
    TRAIL_MODE_NAMES,
    SchemaError,
    Stage,
    build_order_intent,
    journal_tag,
)
from charon_bridge.version import BRIDGE_VERSION, SCHEMA_VERSION

STATIC_DIR = Path(__file__).resolve().parent / "static"

# --- How long a reconciliation answer may be reused. The EA republishes
# --- about once a second and the terminal read costs a real initialize()
# --- round trip, so this is deliberately short: long enough to keep a
# --- polling dashboard from hammering MT5, short enough that an alert is
# --- never more than a moment out of date.
UNMANAGED_TTL_SECONDS = 2.0

# --- Confirmation counters live for the life of the process. See
# --- ``reconcile.Tracker``: the count is a debounce, not a record.
_TRACKER = reconcile.Tracker()
_reconcile_cache: dict[str, Any] = {"at": 0.0, "payload": None}


# --- optional shared secret. Unset means "loopback trust": acceptable for
# --- a single-user desktop, and the README says exactly that.
TOKEN_ENV = "CHARON_TOKEN"

# --- stdlib logger for the console. ``LOG`` is the dashboard's in-memory
# --- ring buffer: it is only reachable over HTTP and it is wiped on every
# --- restart, which is exactly useless for the question "which directory is
# --- this process actually reading?". That answer has to reach stderr, where
# --- whoever started uvicorn is already looking.
logger = logging.getLogger("charon.bridge")


def _announce(level: str, message: str) -> None:
    """Record a startup fact in the ring buffer *and* on the console.

    Both destinations matter and neither substitutes for the other: the ring
    buffer is what the dashboard tail shows while the bridge is up, the
    stdlib logger is what survives in the terminal transcript afterwards.
    """
    getattr(LOG, level)(message)
    getattr(logger, "warning" if level in ("warn", "error") else "info")(message)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Announce where we are looking, so a misconfigured path is obvious.

    The modern replacement for ``@app.on_event("startup")``, which is
    deprecated and emits a ``DeprecationWarning`` on every boot.
    """
    where = state.locate()
    _announce("info", f"Charon bridge {BRIDGE_VERSION} (schema {SCHEMA_VERSION})")
    _announce("info", f"common files: {where['common_files']} [{where['common_exists']}]")
    _announce("info", f"bridge root : {where['bridge_root']} [{where['root_exists']}]")
    if not mt5_adapter.available():
        _announce("info", "MetaTrader5 package absent - dashboard reads the EA snapshot only")

    # --- Checked *before* ensure_dirs(), so the answer describes what the EA
    # --- left behind rather than what this process just created. The
    # --- directory is a poor liveness signal anyway -- the bridge makes it by
    # --- existing -- so the durable test is whether any snapshot is in it.
    if not state.discover_symbols():
        _announce(
            "warn",
            f"no snapshot in {where['bridge_root']} - nothing is publishing here. "
            "Set InpBridgeEnable=true and attach Charon to a chart, or point "
            f"{paths.ENV_OVERRIDE} at the terminal's Common\\Files folder. If the "
            "terminal runs on another machine, this process cannot see it: the "
            "EA publishes to a local folder, not over the network.",
        )
    try:
        paths.ensure_dirs()
    except OSError as exc:
        _announce("error", f"could not create bridge directories: {exc}")
    yield


app = FastAPI(
    title="Charon bridge",
    version=BRIDGE_VERSION,
    description=(
        "Read-mostly window onto a running Charon EA. The EA remains the "
        "only component that talks to the broker."
    ),
    lifespan=_lifespan,
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
        # --- Name the directory that was searched. "no snapshot found" sends
        # --- an operator looking at the chart; "no snapshot in <path>" sends
        # --- them at the one thing that is usually wrong -- the path, or the
        # --- fact that the terminal is on a different machine entirely.
        where = state.locate()
        raise HTTPException(
            status_code=503,
            detail=(
                f"no Charon snapshot found in {where['bridge_root']} "
                f"(common files {where['common_exists']}). "
                "Enable InpBridgeEnable on the chart, "
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


def journal_for(symbol: str, snapshot: Any) -> list[dict[str, Any]]:
    """The notes for ``symbol``, with any unfiled note moved onto its ticket.

    A note is written when the ticket is queued, before a position exists, so
    it is stored under ticket 0. Once the EA reports an accepted verdict the
    real ticket is known and the note is re-filed against it -- this is the
    only moment that fact becomes available, so it happens on read rather than
    on a timer.
    """
    order = snapshot.order
    if order.ticket and order.status in ("accepted", "pending"):
        journal.rekey(symbol, 0, order.ticket)
    return journal.for_symbol(symbol)


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
        # --- the revision this bridge speaks, next to the revision the EA
        # --- published below. They differ on a half-upgraded install, and the
        # --- UI needs both to explain why a ticket was refused.
        "bridge_schema": SCHEMA_VERSION,
        "journal": journal_for(resolved, snap),
        "snapshot": {
            "symbol": snap.symbol,
            "seq": snap.seq,
            # --- the EA's own revision, not ours
            "schema": snap.schema,
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
            # --- the EA's verdict on the last ticket. ``journal_tag`` is the
            # --- short ASCII tag the ticket carried; the note itself is in the
            # --- sidecar above, keyed by ticket.
            "order": {
                "status": snap.order.status,
                "reason": snap.order.reason,
                "side": snap.order.side,
                "lots": snap.order.lots,
                "entry": snap.order.entry,
                "stop": snap.order.stop,
                "risk_money": snap.order.risk_money,
                "risk_pct": snap.order.risk_pct,
                "rung": snap.order.rung,
                "ticket": snap.order.ticket,
                "journal_tag": snap.order.journal_tag,
            },
            # --- orders that are still working. A LIMIT ticket is invisible in
            # --- ``legs`` until it fills, so this is the only way it can be
            # --- shown and cancelled.
            "pending": [
                {
                    "ticket": order.ticket,
                    "side": order.side,
                    "order_type": order.order_type,
                    "price": order.price,
                    "sl": order.stop,
                    "lot": order.lot,
                    "placed_at": order.placed_at,
                }
                for order in snap.pending
            ],
            # --- entries the EA is holding until the market reaches a price.
            # --- Separate from ``pending`` on purpose: these have no broker
            # --- ticket and are cancelled by watch id, not ticket number.
            "watch": [
                {
                    "watch_id": item.watch_id,
                    "side": item.side,
                    "price": item.price,
                    "order_type": item.order_type,
                    "created_at": item.created_at,
                    "status": item.status,
                }
                for item in snap.watch
            ],
            # --- the EA's last tick. Null on a schema-2/3 EA, which never
            # --- published it; the dashboard then simply omits distance.
            "bid": snap.bid,
            "ask": snap.ask,
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
        # --- working orders travel with the positions: a caller that renders
        # --- one without the other can only show half the book.
        "pending": snap["pending"],
        "journal": payload["journal"],
    }


@app.get("/api/terminal", dependencies=[Depends(require_token)])
def terminal() -> dict[str, Any]:
    """Optional MT5 diagnostic; reports ``available: false`` when absent."""
    return mt5_adapter.describe().as_dict()


#+------------------------------------------------------------------+
#| Symbols                                                          |
#+------------------------------------------------------------------+
@app.get("/api/symbols", dependencies=[Depends(require_token)])
def list_symbols() -> dict[str, Any]:
    """Instruments the dashboard can address, with their display labels.

    Two sources, merged: the symbols the EA is publishing snapshots for
    (live and actionable) and the instruments the terminal lists (a superset,
    but only meaningful when the MetaTrader5 package is installed).

    ``symbol`` is the exact broker name -- that is what a command must
    carry. ``label`` is the short form for display and search. The two are
    never interchangeable, and ``sources`` says which one can actually be
    driven right now: a broker-only entry has no chart to receive a
    command, so the UI must not pretend otherwise.
    """
    chart = state.discover_symbols()
    broker = mt5_adapter.symbols()
    entries = symbols.catalogue(chart=chart, broker=broker)
    return {
        "symbols": [entry.as_dict() for entry in entries],
        "charts": chart,
        # --- the picker's grouping order. The bridge states it so the client
        # --- never has to keep a second copy in step with this module.
        "asset_classes": list(symbols.ASSET_CLASSES),
        "broker_available": mt5_adapter.available(),
        "broker_count": len(broker),
    }


#+------------------------------------------------------------------+
#| Unmanaged positions                                              |
#+------------------------------------------------------------------+
def _snapshot_for(symbol: str) -> Any:
    """A parsed snapshot for ``symbol``, or ``None`` if it is not usable.

    A stale or unreadable snapshot is not evidence of anything, so it
    becomes ``None`` here rather than being passed on as an empty one.
    """
    try:
        reading = state.read_snapshot(symbol)
    except state.StateUnavailable:
        return None
    # --- ``Reading.stale`` is measured from the file's mtime, not from the
    # --- EA's clock, so a skewed terminal clock cannot make an old snapshot
    # --- look current. Beyond that it cannot be trusted at all.
    if reading.stale:
        return None
    return reading.snapshot


@app.get("/api/unmanaged", dependencies=[Depends(require_token)])
def unmanaged() -> dict[str, Any]:
    """Open positions no live Charon chart accounts for.

    This is a **read-only** report. It cannot close, modify or size
    anything -- it exists so an operator can see a position Charon is not
    managing, which is otherwise invisible because the dashboard only ever
    shows what the EA published.

    The three-way distinction in ``summary.state`` is the point:

    * ``ok``          -- every chart was checked and the answer is complete;
    * ``unknown``     -- some chart is stale or starting, so an absence
                         proves nothing;
    * ``unavailable`` -- no chart is publishing at all, or the MT5 package
                         is not installed.

    Only ``ok`` may ever be rendered as "all clear". The result is cached
    for :data:`UNMANAGED_TTL_SECONDS` because each call costs a terminal
    round trip.
    """
    now = time.monotonic()
    cached = _reconcile_cache["payload"]
    if cached is not None and now - float(_reconcile_cache["at"]) < UNMANAGED_TTL_SECONDS:
        return cached

    charts = state.discover_symbols()
    snapshots = {symbol: _snapshot_for(symbol) for symbol in charts}
    account = mt5_adapter.positions()

    if not mt5_adapter.available():
        payload: dict[str, Any] = {
            "symbols": [],
            "positions": [],
            "pending": [],
            "summary": {
                "state": reconcile.STATE_UNAVAILABLE,
                "confirmed": 0,
                "pending": 0,
                "clean": False,
                "confirmations": reconcile.CONFIRMATIONS,
            },
            "reason": (
                "the MetaTrader5 package is not installed, so the account "
                "cannot be listed. Install MetaTrader5 or read positions "
                "from the chart itself."
            ),
        }
    else:
        payload = reconcile.classify(
            account, snapshots, _TRACKER, now=time.time()
        )

    _reconcile_cache["at"] = now
    _reconcile_cache["payload"] = payload
    return payload


def _require_live_chart(symbol: str) -> Any:
    """Refuse a trade-affecting command for a symbol with no live chart.

    Without this the ticket is written, queued, and silently never
    collected -- a v1.2.0 EA only drains the flat inbox, and a chart that is
    not running cannot drain anything at all. The operator sees "queued"
    and believes something is about to happen.

    ``unknown`` is treated as a refusal, not a pass: an order is exactly the
    action that must not proceed on a guess. The message names the symbol
    and where the bridge looked, so the usual cause -- a typo, or the wrong
    terminal -- is obvious.

    Returns the parsed snapshot so a caller that needs to inspect the EA's
    revision does not have to read the file twice.
    """
    snapshot = _snapshot_for(symbol)
    if snapshot is None:
        where = state.locate()
        raise HTTPException(
            status_code=422,
            detail=(
                f"no live Charon snapshot for {symbol!r} in "
                f"{where['bridge_root']}. A command can only be honoured by "
                "an EA publishing that exact symbol; check the name against "
                "GET /api/symbols, and that InpBridgeEnable is true on that "
                "chart."
            ),
        )
    return snapshot


# --- fields an older EA would accept and then ignore, each with the schema
# --- revision that first honours it. Listing them explicitly (rather than
# --- testing "is this a v1.5 ticket") keeps the refusal tied to the field that
# --- is actually unhonourable, so the message can name it -- and, just as
# --- importantly, keeps a schema-3 EA usable for everything schema 3 does
# --- understand. A single "needs SCHEMA_VERSION" test would refuse a v1.4.0
# --- operator their own journal note the moment the bridge moved to schema 4.
_SEMANTIC_FIELDS: tuple[tuple[str, int, str], ...] = (
    ("risk_money", 3, "a money-denominated risk"),
    ("basket_risk_ceiling_pct", 3, "a basket-wide risk ceiling"),
    ("trail_start_rr", 3, "a per-ticket trail activation"),
    ("trail_start_points", 3, "a per-ticket trail activation"),
    ("stages", 3, "pyramid stages"),
    ("journal", 3, "a journal note"),
    ("trail_start_price", 4, "an absolute-price trail activation"),
    # --- a watch is gated on the *verb*, not on a field: a schema-3 EA does
    # --- not know ``watch_add`` exists, so it would reject the file outright.
    # --- ``is_watch`` is a property, and the loop below only asks whether it
    # --- is truthy, which is exactly the question here.
    ("is_watch", 4, "a watched entry"),
)


def _require_semantic_capable(symbol: str, intent: Any, where: str) -> None:
    """Refuse a ticket aimed at an EA that cannot honour it.

    ``CCharonBridge::ReadCommandFile`` ignores keys it does not know, and the
    EA refuses a schema mismatch by design. So a schema-4 ticket against a
    schema-2 EA is already safe from *trading wrongly* -- it is refused on
    the chart. But that refusal is only visible in the Experts log, while the
    dashboard has just said "queued". This turns it into a synchronous,
    readable answer naming both the field and the fix.

    The check is **per field**, not per version: each entry carries the
    revision that first honours it, and only fields above the EA's own
    revision are refused. That is what lets a schema-3 chart keep placing
    v1.4.0 tickets after the bridge has learned schema 4.

    A ticket that carries none of the newer semantics is deliberately
    allowed through: an operator with no upgraded charts can still place a
    plain block-and-risk ticket, and the EA will accept it exactly as before.
    """
    snapshot = _require_live_chart(symbol)

    offending = sorted({
        label
        for field, needs, label in _SEMANTIC_FIELDS
        if getattr(intent, field, None) and snapshot.schema < needs
    })
    if not offending:
        return

    raise HTTPException(
        status_code=422,
        detail=(
            f"{where} needs a newer EA, but the chart publishing "
            f"{symbol!r} speaks schema {snapshot.schema}. It would read this "
            f"ticket, ignore {', '.join(offending)}, and trade "
            "anyway. Either drop that part of the ticket, or install the "
            f"v{SCHEMA_VERSION} EA (version {snapshot.ea_version or 'unknown'} "
            "is running) and recompile that chart."
        ),
    )



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


class OrderTicket(BaseModel):
    """The operator's ticket, as the dashboard submits it.

    Deliberately *not* a trade request: there is no ``lots`` field, because
    volume is solved by the EA from the block height and the risk figure.
    Accepting a caller-chosen volume would move a risk decision out of
    ``CharonRisk.mqh`` and into the browser, which is exactly what the
    architecture forbids.

    ``entry`` may be omitted for a MARKET ticket; ``LIMIT`` requires it.

    Risk is stated **once** -- ``risk_pct`` or ``risk_money`` -- and the
    exclusivity is enforced in ``build_order_intent`` so both the API and any
    future caller share one rule.
    """

    symbol: str | None = Field(
        default=None,
        description="Defaults to the symbol the EA last published.",
    )
    side: str = Field(description="BUY or SELL")
    order_type: str = Field(default="MARKET", description="MARKET or LIMIT")
    entry: float | None = Field(default=None, description="Required for LIMIT")
    block_top: float = Field(description="Upper structural boundary")
    block_bottom: float = Field(description="Lower structural boundary")
    risk_pct: float | None = Field(
        default=None, description="Risk as a percent of account (or use risk_money)"
    )
    risk_money: float | None = Field(
        default=None, description="Risk as an amount of account currency"
    )
    basket_risk_ceiling_pct: float | None = Field(
        default=None,
        ge=0,
        description="Hard whole-basket risk ceiling enforced before any stage fires",
    )
    trail_trigger: int = Field(default=0, ge=0, le=4, description="TrailMode rung")
    trail_start_rr: float | None = Field(
        default=None, description="Per-ticket trail activation in R"
    )
    trail_start_points: float | None = Field(
        default=None, description="Per-ticket trail activation in points"
    )
    trail_start_price: float | None = Field(
        default=None,
        description=(
            "Per-ticket trail activation as an absolute price the market must "
            "reach. Exclusive with trail_start_rr / trail_start_points."
        ),
    )
    rung: int = Field(default=0, ge=0, le=3, description="RR rung to report against")
    stages: list[Stage] | None = Field(
        default=None,
        max_length=MAX_STAGES,
        description="Pyramid stages: trigger kind, trigger value, volume multiplier",
    )
    journal: str | None = Field(
        default=None,
        max_length=MAX_JOURNAL_LEN,
        description="Free-text trade note; the bridge keeps it, the EA gets a short tag",
    )


def _intent_from(ticket: "OrderTicket") -> Any:
    """Validate a submitted ticket into an :class:`OrderIntent`.

    Both order endpoints funnel through here, so a rule added to
    ``build_order_intent`` cannot apply to one path and not the other.
    """
    return build_order_intent(
        side=ticket.side,
        order_type=ticket.order_type,
        entry=ticket.entry,
        block_top=ticket.block_top,
        block_bottom=ticket.block_bottom,
        risk_pct=ticket.risk_pct,
        risk_money=ticket.risk_money,
        basket_risk_ceiling_pct=ticket.basket_risk_ceiling_pct,
        trail_trigger=ticket.trail_trigger,
        trail_start_rr=ticket.trail_start_rr,
        trail_start_points=ticket.trail_start_points,
        trail_start_price=ticket.trail_start_price,
        rung=ticket.rung,
        stages=[stage.to_doc() for stage in ticket.stages] if ticket.stages else None,
        journal=ticket.journal,
    )



@app.post("/api/order", dependencies=[Depends(require_token)])
def place_order(ticket: "OrderTicket") -> dict[str, Any]:
    """Queue an order ticket for the EA to adjudicate.

    This endpoint does **not** place a trade. It writes one command file
    describing what the operator asked for, and the EA decides -- under
    ``CCharonRisk`` and the block geometry -- whether a position is opened,
    how large it is and where the stop goes. The response says only that a
    ticket was queued; the verdict arrives in the next snapshot's ``order``
    block, which is where the dashboard reads its numbers from.

    A ``MARKET`` ticket that names an ``entry`` is **not** filled at market:
    it is queued as a watch, and the EA submits it itself once its own tick
    reaches that price. Before v1.5.0 that combination silently filled at
    market, which made "wait for 1.1050" indistinguishable from "buy now" --
    the operator's price was read, stored in the ticket, and ignored.

    Refusals here are all *shape* refusals (bad side, inverted block, a
    LIMIT entry outside its block, a risk figure that is not a positive
    number) plus one liveness check: a symbol with no live snapshot has no
    chart that could act on this ticket, so it is refused up front rather
    than queued into silence. Anything about money or market state is the
    EA's answer.
    """
    resolved = _symbol_or_404(ticket.symbol)
    try:
        intent = _intent_from(ticket)
        # --- shape first, liveness second: a malformed ticket is wrong
        # --- regardless of which charts are up, and telling the operator
        # --- that is more useful than "no chart". Both refuse before
        # --- anything is written.
        _require_semantic_capable(resolved, intent, "this ticket")
        verb = "watch_add" if intent.is_watch else "order_open"
        sent = commands.submit(verb, resolved, order=intent)
    except (SchemaError, commands.CommandError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if intent.is_watch:
        LOG.command(
            f"watch_add queued for {resolved}: {intent.side} MARKET once "
            f"{intent.entry} is touched, risk="
            f"{intent.risk_pct if intent.risk_pct is not None else str(intent.risk_money) + ' ccy'}"
            f" block={intent.block_bottom}-{intent.block_top} ({sent['file']})"
        )
    else:
        LOG.command(
            f"order_open queued for {resolved}: {intent.side} {intent.order_type} "
            f"risk={intent.risk_pct if intent.risk_pct is not None else str(intent.risk_money) + ' ccy'}"
            f" block={intent.block_bottom}-{intent.block_top} ({sent['file']})"
        )
    # --- the note is filed against the ticket the EA will report. Until the
    # --- verdict arrives there is no position ticket, so it is keyed by 0 and
    # --- re-keyed by ``_sync_journal`` as soon as the snapshot carries one.
    # --- Written *after* the command, so a failed submit leaves no orphan note.
    if intent.journal:
        journal.note(resolved, 0, intent.journal, tag=journal_tag(intent.journal))
    response: dict[str, Any] = {
        "queued": True,
        "verb": verb,
        "command": sent,
        "note": (
            f"Watching {intent.entry}. The EA submits this ticket itself once "
            "the price is touched, then decides sizing and the stop."
            if intent.is_watch else
            "Queued for the EA. The EA decides sizing and the stop."
        ),
    }
    if intent.is_watch:
        # --- the UI shows the target rather than making the operator re-read
        # --- the ticket they just sent. ``watching`` is the flag it keys on.
        response["watching"] = True
        response["entry"] = intent.entry
    return response


@app.post("/api/order/modify", dependencies=[Depends(require_token)])
def modify_order(ticket: "OrderTicket", position: int = Query(gt=0)) -> dict[str, Any]:
    """Re-submit block geometry for an existing position.

    The EA re-derives the stop from the new block and re-checks it against
    ``MinStopDistance``/``StopsLevel`` before touching the position.
    """
    resolved = _symbol_or_404(ticket.symbol)
    try:
        intent = _intent_from(ticket)
        _require_semantic_capable(resolved, intent, "modifying a stop")
        sent = commands.submit("order_modify", resolved, order=intent, ticket=position)
    except (SchemaError, commands.CommandError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    LOG.command(f"order_modify queued for {resolved} ticket={position} ({sent['file']})")
    return {"queued": True, "verb": "order_modify", "command": sent}


@app.post("/api/order/cancel", dependencies=[Depends(require_token)])
def cancel_order(position: int = Query(gt=0),
                 symbol: str | None = Query(default=None)) -> dict[str, Any]:
    """Ask the EA to cancel a pending order or flatten a position."""
    resolved = _symbol_or_404(symbol)
    try:
        sent = commands.submit("order_cancel", resolved, ticket=position)
    except (SchemaError, commands.CommandError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    LOG.command(f"order_cancel queued for {resolved} ticket={position} ({sent['file']})")
    return {"queued": True, "verb": "order_cancel", "command": sent}


@app.post("/api/orders/watch/cancel", dependencies=[Depends(require_token)])
def cancel_watch(watch_id: int = Query(gt=0),
                 symbol: str | None = Query(default=None)) -> dict[str, Any]:
    """Ask the EA to drop an armed watch before it fires.

    Deliberately *not* ``/api/order/cancel``: a watch has no broker ticket, so
    routing it through the order path would send a ticket number the EA would
    look for among its pending orders and not find. The two queues are
    cancelled by different names because they are different objects.
    """
    resolved = _symbol_or_404(symbol)
    try:
        sent = commands.submit("watch_cancel", resolved, watch_id=watch_id)
    except (SchemaError, commands.CommandError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    LOG.command(f"watch_cancel queued for {resolved} watch={watch_id} ({sent['file']})")
    return {"queued": True, "verb": "watch_cancel", "command": sent}


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
async def logs(
    stream: bool = Query(default=False),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> Any:
    """Bridge-side transcript.

    ``?stream=true`` returns server-sent events; the plain form returns
    the buffer so the page can render something before the stream opens.
    Deliberately unauthenticated: it carries only bridge messages, and an
    ``EventSource`` cannot send custom headers.

    Every frame carries an ``id:``, and ``Last-Event-ID`` is honoured on
    reconnect. Without both, ``EventSource`` replays the whole tail after
    every hiccup and the operator watches the same fifty lines pile up.
    """
    if not stream:
        return {
            "entries": [entry.as_dict() for entry in LOG.tail(200)],
            "latest_seq": LOG.latest_seq,
        }

    def frame(entry: Any) -> str:
        return f"id: {entry.seq}\ndata: {json.dumps(entry.as_dict())}\n\n"

    # --- 3s is long enough to ride out a reload and short enough that a
    # --- dropped stream recovers while the operator is still looking at it
    reconnect_ms = 3000

    def resume_point() -> int:
        """Where to start when the browser says how far it got."""
        if not last_event_id:
            return 0
        try:
            return max(0, int(last_event_id.strip()))
        except (AttributeError, ValueError):
            # --- a malformed id is treated as "no id": replay the tail
            # --- rather than guessing and silently dropping history
            return 0

    async def events():
        yield f"retry: {reconnect_ms}\n\n"

        cursor = resume_point()
        if cursor:
            # --- resuming: send only what the client has not seen
            for entry in LOG.since(cursor):
                cursor = max(cursor, entry.seq)
                yield frame(entry)
        else:
            # --- first connect: replay what already happened so a late
            # --- subscriber is not blind
            for entry in LOG.tail(50):
                cursor = max(cursor, entry.seq)
                yield frame(entry)

        while True:
            await asyncio.sleep(0.5)
            for entry in LOG.since(cursor):
                cursor = max(cursor, entry.seq)
                yield frame(entry)
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


def _bind() -> tuple[str, int]:
    """Resolve the listen address from the environment.

    ``.env.example`` documents ``CHARON_HOST`` and ``CHARON_PORT``, so they
    have to actually do something -- otherwise a trader who moves the panel
    to another port follows the docs and gets no effect. Loopback stays the
    default: a dashboard that can place no orders still shows your account.
    """
    host = os.environ.get("CHARON_HOST", "").strip() or "127.0.0.1"
    raw_port = os.environ.get("CHARON_PORT", "").strip()
    if not raw_port:
        return host, 8787
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"CHARON_PORT must be a number, got {raw_port!r}") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"CHARON_PORT out of range: {port}")
    return host, port


if __name__ == "__main__":
    import uvicorn

    _host, _port = _bind()
    uvicorn.run(app, host=_host, port=_port)

