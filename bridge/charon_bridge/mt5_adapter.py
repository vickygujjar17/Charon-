"""Optional read-only view through the MetaTrader5 Python package.

The EA publishes everything the dashboard *needs*. This module exists
only to answer questions the file snapshot cannot: "is a terminal even
running on this machine, and which build?". That helps a confused user
far more than a generic "no snapshot" error.

**Read-only by construction.** Only ``initialize``, ``terminal_info``,
``account_info``, ``symbol_info``, ``symbols_get``, ``positions_get``,
``history_deals_get`` and ``copy_rates_from_pos`` are ever called. There is no
call to ``order_send``, ``order_check`` or any position-modifying function, and
the test-suite asserts that this module's source contains no such name.
Charon's single ``OrderSend`` site stays ``CharonTrade.mqh``.

``symbols_get`` and ``positions_get`` are here for a read-only question the
snapshot cannot answer: *which instruments exist, and which positions are
open that no Charon chart is managing?* ``history_deals_get`` and
``copy_rates_from_pos`` answer the other two the snapshot cannot: *what has
actually been closed, and what did price do around it?* All four are list
calls, not actions -- none can change an account, and the forbidden-name
list below is what keeps a reviewer from mistaking their purpose.

If the package is not installed -- or not on a platform that has it --
everything degrades to ``available = False`` and the dashboard simply
does not show the extra panel. Nothing depends on this module.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


# --- imported lazily: the package is Windows-only and optional, so a
# --- missing install must not stop the bridge from serving the snapshot.
try:  # pragma: no cover - exercised only where MT5 is installed
    import MetaTrader5 as _mt5
except Exception:  # noqa: BLE001 - any import failure means "unavailable"
    _mt5 = None


# --- The MetaTrader5 package is documented as not thread-safe: concurrent
# --- initialize()/shutdown() calls from more than one thread can wedge the
# --- terminal connection. FastAPI serves requests on a thread pool, so every
# --- call below takes this one lock rather than each growing its own. None
# --- of them calls another while holding it, so the lock is never re-entered.
_TERMINAL_LOCK = threading.Lock()


@dataclass(frozen=True)
class TerminalView:
    """A tiny, JSON-safe summary of the local terminal."""

    available: bool
    reason: str = ""
    path: str | None = None
    build: int | None = None
    connected: bool = False
    company: str | None = None
    account_login: int | None = None
    account_server: str | None = None
    account_currency: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "path": self.path,
            "build": self.build,
            "connected": self.connected,
            "company": self.company,
            "account_login": self.account_login,
            "account_server": self.account_server,
            "account_currency": self.account_currency,
        }


def available() -> bool:
    return _mt5 is not None


def describe() -> TerminalView:
    """Best-effort terminal summary; never raises.

    A diagnostic helper that can throw would defeat its own purpose, so
    every failure is folded into a human-readable ``reason``.
    """
    if _mt5 is None:
        return TerminalView(
            available=False,
            reason="MetaTrader5 package not installed (optional; the bridge "
                   "works from the EA's snapshot alone)",
        )

    try:  # pragma: no cover - requires a live terminal
        with _TERMINAL_LOCK:
            return _describe_locked()
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        return TerminalView(available=False, reason=f"MetaTrader5 probe failed: {exc}")


def _describe_locked() -> TerminalView:
    """The body of :func:`describe`, called with ``_TERMINAL_LOCK`` held."""
    if not _mt5.initialize():  # type: ignore[union-attr]
        return TerminalView(
            available=False,
            reason=f"MetaTrader5 present but initialize() failed: {_mt5.last_error()}",  # type: ignore[union-attr]
        )
    try:
        term = _mt5.terminal_info()  # type: ignore[union-attr]
        acct = _mt5.account_info()  # type: ignore[union-attr]
        return TerminalView(
            available=True,
            path=getattr(term, "path", None),
            build=getattr(term, "build", None),
            connected=bool(getattr(term, "connected", False)),
            company=getattr(term, "company", None),
            account_login=getattr(acct, "login", None) if acct else None,
            account_server=getattr(acct, "server", None) if acct else None,
            account_currency=getattr(acct, "currency", None) if acct else None,
        )
    finally:
        _mt5.shutdown()  # type: ignore[union-attr]


# --- Names that must never appear in this module. The test-suite reads
# --- this file and asserts the absence, so a future edit that reaches for
# --- an order function fails CI instead of quietly creating a second path.
FORBIDDEN_CALLS: tuple[str, ...] = (
    "order_send",
    "order_check",
    "positions_modify",
    "positions_close",
)


def _rows(value: Any) -> list[Any]:
    """Normalise a ``*_get`` return into a list.

    The package returns ``None`` on failure and a tuple of named tuples on
    success. ``None`` must become ``[]`` rather than raising, because "the
    terminal is not up" is an ordinary state here, not an error.
    """
    if not value:
        return []
    try:
        return list(value)
    except TypeError:
        return []


def symbols() -> list[str]:
    """Every instrument the terminal lists, best effort.

    Returns ``[]`` when the package is missing or the terminal is not
    reachable. An empty list is never presented as "you have no
    instruments" -- :mod:`charon_bridge.symbols` merges this with the
    charts the EA is publishing, and the UI shows the sources separately so
    an empty broker list reads as "unavailable", not as "empty account".
    """
    if _mt5 is None:
        return []

    try:  # pragma: no cover - requires a live terminal
        with _TERMINAL_LOCK:
            if not _mt5.initialize():
                return []
            try:
                found = []
                for entry in _rows(_mt5.symbols_get()):
                    name = getattr(entry, "name", None)
                    if isinstance(name, str) and name.strip():
                        found.append(name)
                return found
            finally:
                _mt5.shutdown()
    except Exception:  # noqa: BLE001 - optional panel; never break the page
        return []


@dataclass(frozen=True)
class Position:
    """One open position, flattened to the fields reconciliation needs."""

    ticket: int
    symbol: str
    side: str
    volume: float
    price_open: float
    profit: float
    magic: int = 0
    comment: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "side": self.side,
            "volume": self.volume,
            "price_open": self.price_open,
            "profit": self.profit,
            "magic": self.magic,
            "comment": self.comment,
        }


# --- MT5 reports direction as an integer type, not a string. Mapping it
# --- here keeps the integer from leaking into the API, where a consumer
# --- would have to know that 0 means buy.
_POSITION_TYPES = {0: "BUY", 1: "SELL"}


def positions() -> list[Position]:
    """Open positions across the whole account, best effort.

    ``[]`` on any failure, including "package missing". The caller must be
    able to distinguish that from "no positions" before saying anything to
    a trader, so :func:`available` is the companion check.
    """
    if _mt5 is None:
        return []

    try:  # pragma: no cover - requires a live terminal
        with _TERMINAL_LOCK:
            if not _mt5.initialize():
                return []
            try:
                found: list[Position] = []
                for entry in _rows(_mt5.positions_get()):
                    found.append(
                        Position(
                            ticket=int(getattr(entry, "ticket", 0) or 0),
                            symbol=str(getattr(entry, "symbol", "") or ""),
                            side=_POSITION_TYPES.get(
                                int(getattr(entry, "type", -1) or -1), "?"
                            ),
                            volume=float(getattr(entry, "volume", 0.0) or 0.0),
                            price_open=float(getattr(entry, "price_open", 0.0) or 0.0),
                            profit=float(getattr(entry, "profit", 0.0) or 0.0),
                            magic=int(getattr(entry, "magic", 0) or 0),
                            comment=str(getattr(entry, "comment", "") or ""),
                        )
                    )
                return found
            finally:
                _mt5.shutdown()
    except Exception:  # noqa: BLE001 - optional panel; never break the page
        return []


@dataclass(frozen=True)
class Deal:
    """One historical deal, flattened to the fields analytics needs.

    MT5 reports a closed position as two deals -- an ``IN`` leg and an ``OUT``
    leg -- so this is deliberately a *deal*, not a trade. Folding pairs
    together is arithmetic, and it lives in :mod:`charon_bridge.history`
    where it can be tested without a terminal.
    """

    ticket: int
    position_id: int
    symbol: str
    side: str
    entry: int
    volume: float
    price: float
    profit: float
    swap: float
    commission: float
    time: int
    magic: int = 0
    comment: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticket": self.ticket,
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side,
            "entry": self.entry,
            "volume": self.volume,
            "price": self.price,
            "profit": self.profit,
            "swap": self.swap,
            "commission": self.commission,
            "time": self.time,
            "magic": self.magic,
            "comment": self.comment,
        }


@dataclass(frozen=True)
class Rate:
    """One OHLC bar, flattened for JSON."""

    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "tick_volume": self.tick_volume,
        }


# --- MT5 reports deal direction as an integer, same as positions.
_DEAL_TYPES = {0: "BUY", 1: "SELL"}


def deals(days: int = 30, symbol: str = "") -> list[Deal]:
    """Closed deals from the last ``days`` of account history, best effort.

    ``history_deals_get`` is a **read**: it cannot open, close or modify
    anything, which is why it belongs in this module's allow-list. An empty
    list means "no history, or the terminal could not be read" and is never
    presented as "you have made no trades" -- :func:`available` is the
    companion check, exactly as for :func:`positions`.

    ``date_from``/``date_to`` bound the query so a long-running account does
    not make every dashboard poll walk years of deals.
    """
    if _mt5 is None:
        return []

    try:  # pragma: no cover - requires a live terminal
        import datetime as _dt

        until = _dt.datetime.now() + _dt.timedelta(minutes=1)
        since = until - _dt.timedelta(days=max(1, int(days)))
        with _TERMINAL_LOCK:
            if not _mt5.initialize():
                return []
            try:
                found: list[Deal] = []
                for entry in _rows(_mt5.history_deals_get(since, until)):
                    row_symbol = str(getattr(entry, "symbol", "") or "")
                    # --- balance operations (deposits, credit, bonuses)
                    # --- carry no symbol and are not trades; including them
                    # --- would make a deposit look like trading profit
                    if not row_symbol:
                        continue
                    if symbol and row_symbol != symbol:
                        continue
                    found.append(
                        Deal(
                            ticket=int(getattr(entry, "ticket", 0) or 0),
                            position_id=int(getattr(entry, "position_id", 0) or 0),
                            symbol=row_symbol,
                            side=_DEAL_TYPES.get(
                                int(getattr(entry, "type", -1) or -1), "?"
                            ),
                            entry=int(getattr(entry, "entry", -1) or -1),
                            volume=float(getattr(entry, "volume", 0.0) or 0.0),
                            price=float(getattr(entry, "price", 0.0) or 0.0),
                            profit=float(getattr(entry, "profit", 0.0) or 0.0),
                            swap=float(getattr(entry, "swap", 0.0) or 0.0),
                            commission=float(
                                getattr(entry, "commission", 0.0) or 0.0
                            ),
                            time=int(getattr(entry, "time", 0) or 0),
                            magic=int(getattr(entry, "magic", 0) or 0),
                            comment=str(getattr(entry, "comment", "") or ""),
                        )
                    )
                return found
            finally:
                _mt5.shutdown()
    except Exception:  # noqa: BLE001 - optional panel; never break the page
        return []


# --- Timeframe names the API accepts, mapped to MT5's integer constants.
# --- Kept as literal values so this module needs no import-time lookup.
TIMEFRAMES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
}


def rates(symbol: str, timeframe: str = "M15", count: int = 120) -> list[Rate]:
    """The most recent ``count`` bars for ``symbol``, oldest first.

    ``copy_rates_from_pos`` is a read. Returns ``[]`` on any failure, so a
    sparkline that cannot be drawn renders as "no bars available" rather
    than as a flat line pretending the price did not move.
    """
    if _mt5 is None or not symbol:
        return []

    constant = TIMEFRAMES.get((timeframe or "").upper())
    if constant is None:
        return []

    try:  # pragma: no cover - requires a live terminal
        with _TERMINAL_LOCK:
            if not _mt5.initialize():
                return []
            try:
                bars = _mt5.copy_rates_from_pos(
                    symbol, constant, 0, max(1, int(count))
                )
                return [
                    Rate(
                        time=int(getattr(bar, "time", 0) or 0),
                        open=float(getattr(bar, "open", 0.0) or 0.0),
                        high=float(getattr(bar, "high", 0.0) or 0.0),
                        low=float(getattr(bar, "low", 0.0) or 0.0),
                        close=float(getattr(bar, "close", 0.0) or 0.0),
                        tick_volume=int(getattr(bar, "tick_volume", 0) or 0),
                    )
                    for bar in _rows(bars)
                ]
            finally:
                _mt5.shutdown()
    except Exception:  # noqa: BLE001 - optional panel; never break the page
        return []


__all__ = [
    "FORBIDDEN_CALLS",
    "Deal",
    "Position",
    "Rate",
    "TerminalView",
    "available",
    "deals",
    "describe",
    "positions",
    "rates",
    "symbols",
]

