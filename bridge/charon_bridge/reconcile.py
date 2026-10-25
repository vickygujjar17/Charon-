"""Which open positions is no Charon chart managing?

The question sounds simple and is not. There are three genuinely different
situations behind "the dashboard does not show this position", and only one
of them is a problem:

* the position **is** in a live snapshot's legs -- normal, Charon owns it;
* a Charon chart is live on that symbol but the ticket is absent -- the
  trader opened it by hand, or the EA has not published it yet;
* no Charon chart is publishing that symbol at all -- nothing here can ever
  know about it, which is a fact about the *setup*, not about the position.

Collapsing those into one alert produces noise that trains the operator to
ignore it, which is worse than no alert.

Two asymmetries decide the design
---------------------------------
**The dashboard can be wrong in only one direction.** Showing a managed
position as unmanaged is a nuisance. Showing an unmanaged position as
managed is a lie, and this module is not allowed to tell it. So every
uncertainty resolves to "we do not know", never to "all clear".

**Absence of evidence is not evidence of absence.** A stale snapshot, or
one from an EA that has not finished starting, publishes an empty legs
array. Read literally, that says every position on the symbol is unmanaged
-- and a terminal that just restarted would scream about its own positions.
Such a snapshot is treated as *absent*: the symbol's state becomes
``unknown`` and the confirmation counter does not advance.

The N-of-N rule
---------------
The "-- but the EA has not published it yet" case is real: a chart can open
a position and publish its legs a tick later. A single observation is
therefore not enough to call a position unmanaged; it has to survive
:data:`CONFIRMATIONS` consecutive observations first. A ticket sighting
resets the count the moment it appears in a snapshot, so a transient gap
cannot accumulate towards a false alarm.

``no_charon_on_symbol`` needs no such patience: no snapshot exists for that
symbol either way, so waiting changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# --- Consecutive observations before an absent ticket is called unmanaged.
# --- The EA republishes about once a second, so this is a few seconds of
# --- grace: long enough for a fill to appear in the next snapshot, short
# --- enough that a genuinely manual position is reported promptly.
CONFIRMATIONS = 3

# --- Per-symbol state.
STATE_OK = "ok"
STATE_UNAVAILABLE = "unavailable"
STATE_UNKNOWN = "unknown"

# --- Why a position is not accounted for by a live snapshot.
NOT_TRACKED = "not_tracked_by_ea"
NO_CHARON = "no_charon_on_symbol"


@dataclass(frozen=True)
class SymbolState:
    """What is known about one symbol's chart."""

    symbol: str
    state: str
    reason: str
    legs: int = 0
    age_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "state": self.state,
            "reason": self.reason,
            "legs": self.legs,
            "age_seconds": self.age_seconds,
        }


@dataclass(frozen=True)
class Finding:
    """A position no live snapshot accounts for."""

    ticket: int
    symbol: str
    side: str
    volume: float
    profit: float
    magic: int
    verdict: str
    seen: int
    confirmed: bool
    unmanaged_for: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "side": self.side,
            "volume": self.volume,
            "profit": self.profit,
            "magic": self.magic,
            "verdict": self.verdict,
            "seen": self.seen,
            "confirmed": self.confirmed,
            "unmanaged_for": self.unmanaged_for,
            "reason": self.reason,
        }


@dataclass
class Tracker:
    """Counts how many consecutive observations a ticket has been missing.

    One instance lives for the life of the process. It is deliberately
    mutable and deliberately not persisted: the counter is a debounce, not
    a record. Restarting the bridge and taking three more seconds to
    re-confirm is the correct behaviour -- a stale count restored from disk
    could confirm an alert that is no longer true.
    """

    _counts: dict[int, int] = field(default_factory=dict)
    _first_seen: dict[int, float] = field(default_factory=dict)

    def note_absent(self, ticket: int, now: float) -> int:
        """Count one observation of ``ticket`` as missing; return the total."""
        if ticket not in self._first_seen:
            self._first_seen[ticket] = now
        count = self._counts.get(ticket, 0) + 1
        self._counts[ticket] = count
        return count

    def count(self, ticket: int) -> int:
        return self._counts.get(ticket, 0)

    def forget(self, ticket: int) -> None:
        """Clear the tally -- the ticket appeared, or the position closed."""
        self._counts.pop(ticket, None)
        self._first_seen.pop(ticket, None)

    def unmanaged_for(self, ticket: int, now: float) -> float:
        """Seconds since this ticket was first seen missing, or ``0.0``."""
        started = self._first_seen.get(ticket)
        return 0.0 if started is None else max(0.0, now - started)

    def reset(self) -> None:
        self._counts.clear()
        self._first_seen.clear()

    def retain(self, tickets: Iterable[int]) -> None:
        """Forget every tally whose position is no longer open.

        Without this a ticket closed while it was still ``pending`` would
        keep its counter forever, and a *different* position that happened
        to reuse the number would inherit it.
        """
        live = set(tickets)
        for ticket in [t for t in self._counts if t not in live]:
            self.forget(ticket)
        for ticket in [t for t in self._first_seen if t not in live]:
            self.forget(ticket)


def _legs(snapshot: Any) -> set[int] | None:
    """Tickets in ``snapshot``'s legs, or ``None`` when it cannot be trusted.

    ``None`` is the important return. A snapshot whose EA is not ``ready``,
    or one the caller already judged stale, is not evidence that a symbol
    has no legs -- it is evidence that nobody is currently maintaining the
    answer. Distinguishing that from an empty set is what stops a
    restarting terminal from being reported as full of unmanaged positions.
    """
    if snapshot is None:
        return None
    if getattr(snapshot, "ready", None) is False:
        return None
    legs = getattr(snapshot, "legs", None)
    if legs is None:
        return None
    found: set[int] = set()
    for leg in legs:
        ticket = getattr(leg, "ticket", None)
        if isinstance(ticket, int):
            found.add(ticket)
    return found


def classify(
    positions: Iterable[Mapping[str, Any]] | Iterable[Any],
    snapshots: Mapping[str, Any],
    tracker: Tracker,
    now: float,
    *,
    confirmations: int = CONFIRMATIONS,
) -> dict[str, Any]:
    """Reconcile account positions against the live snapshots.

    ``positions`` is whatever :func:`charon_bridge.mt5_adapter.positions`
    returned, as objects or plain dicts. ``snapshots`` maps a symbol to
    whatever the caller wants used for it: a parsed snapshot, a reading, or
    ``None`` when nothing trustworthy is available.

    Returns ``{"symbols": [...], "positions": [...], "pending": [...],
    "summary": {...}}``. ``pending`` holds findings that have not yet
    reached the confirmation count, so the UI can stay quiet without the
    count being thrown away.
    """
    by_symbol = {str(name): snap for name, snap in snapshots.items()}
    chart_legs = {symbol: _legs(snap) for symbol, snap in by_symbol.items()}

    # --- normalise once, so the ticket set can prune closed positions below
    rows: list[dict[str, Any]] = []
    for raw in positions:
        row = raw.as_dict() if hasattr(raw, "as_dict") else dict(raw)
        if int(row.get("ticket") or 0) > 0 and str(row.get("symbol") or ""):
            rows.append(row)
    tracker.retain(int(row["ticket"]) for row in rows)

    findings: list[Finding] = []

    for row in rows:
        ticket = int(row["ticket"])
        symbol = str(row["symbol"])

        if symbol not in by_symbol:
            # --- nothing is publishing this symbol: waiting would change
            # --- nothing, so this needs no confirmation count.
            tracker.forget(ticket)
            findings.append(
                Finding(
                    ticket=ticket,
                    symbol=symbol,
                    side=str(row.get("side") or "?"),
                    volume=float(row.get("volume") or 0.0),
                    profit=float(row.get("profit") or 0.0),
                    magic=int(row.get("magic") or 0),
                    verdict=NO_CHARON,
                    seen=1,
                    confirmed=True,
                    unmanaged_for=0.0,
                    reason=(
                        f"no Charon chart is publishing {symbol}, so nothing "
                        "here can manage this position"
                    ),
                )
            )
            continue

        legs = chart_legs[symbol]
        if legs is None:
            # --- the chart cannot be trusted right now. Do not advance the
            # --- counter: three stale looks are not three sightings.
            continue

        if ticket in legs:
            tracker.forget(ticket)
            continue

        count = tracker.note_absent(ticket, now)
        findings.append(
            Finding(
                ticket=ticket,
                symbol=symbol,
                side=str(row.get("side") or "?"),
                volume=float(row.get("volume") or 0.0),
                profit=float(row.get("profit") or 0.0),
                magic=int(row.get("magic") or 0),
                verdict=NOT_TRACKED,
                seen=count,
                confirmed=count >= confirmations,
                unmanaged_for=round(tracker.unmanaged_for(ticket, now), 1),
                reason=(
                    f"the Charon chart on {symbol} is live and does not "
                    f"include ticket {ticket}"
                ),
            )
        )

    symbols_out: list[SymbolState] = []
    for symbol in by_symbol:
        legs = chart_legs[symbol]
        if legs is None:
            symbols_out.append(
                SymbolState(
                    symbol=symbol,
                    state=STATE_UNKNOWN,
                    reason="the snapshot is stale or the EA is still starting",
                )
            )
        else:
            symbols_out.append(
                SymbolState(symbol=symbol, state=STATE_OK, reason="", legs=len(legs))
            )

    if not by_symbol:
        # --- no charts at all is a setup problem, not a clean bill of health
        state = STATE_UNAVAILABLE
    elif any(item.state != STATE_OK for item in symbols_out):
        state = STATE_UNKNOWN
    else:
        state = STATE_OK

    confirmed = [item for item in findings if item.confirmed]
    return {
        "symbols": [item.as_dict() for item in symbols_out],
        "positions": [item.as_dict() for item in confirmed],
        "pending": [item.as_dict() for item in findings if not item.confirmed],
        "summary": {
            "state": state,
            "confirmed": len(confirmed),
            "pending": len(findings) - len(confirmed),
            # --- only a fully checked, fully clean account earns this word
            "clean": state == STATE_OK and not confirmed,
            "confirmations": confirmations,
        },
    }


__all__ = [
    "CONFIRMATIONS",
    "Finding",
    "NO_CHARON",
    "NOT_TRACKED",
    "STATE_OK",
    "STATE_UNAVAILABLE",
    "STATE_UNKNOWN",
    "SymbolState",
    "Tracker",
    "classify",
]
