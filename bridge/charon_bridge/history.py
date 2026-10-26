"""Closed-trade history and the statistics the dashboard draws from it.

Why this module has no MetaTrader5 import
----------------------------------------
:mod:`charon_bridge.mt5_adapter` is a deliberately thin, allow-listed,
read-only shim: it may only touch a fixed set of terminal calls, and a test
asserts that set. Statistics are a different concern and a much larger one,
so they live here instead, taking plain deal dictionaries as input.

That split is what makes the arithmetic testable. Every number below can be
checked against synthetic deals on a machine with no terminal installed --
which is the only way this logic ever gets exercised before it meets a real
account. A wrong profit factor is a wrong decision; it should not require a
live broker to discover.

What "realized" means here
--------------------------
A closed position produces two deals in MT5's history: an ``IN`` deal when
the position opened and an ``OUT`` deal when it closed. Profit is booked on
the ``OUT`` leg, but commission is usually split across both (and swap is
settled on the out leg). Summing all three across the pair is the only
figure that matches the broker statement, so that is what
:func:`pair_deals` reports.

Never invent a number
---------------------
Ratios with an undefined denominator return ``None``, never ``0`` and never
``inf``:

* no trades at all             -> ``win_rate`` is ``None`` (not "0%")
* no losing trades             -> ``profit_factor`` is ``None`` (not infinity)
* no trades to average         -> ``avg_rr`` is ``None``

The dashboard renders ``None`` as "not enough closed trades", which is a
true statement. Rendering ``0.0`` would be a false one: "0% win rate" reads
as "you have lost every trade", and a profit factor of ``999`` reads as
perfection. Both are lies about someone's money.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Iterable

# --- MT5 deal ``entry`` values. 0 is a position opening, 1 a position
# --- closing (2/3 are reversals and are folded into the same pairing).
_ENTRY_OUT = 1

# --- Deal ``type`` values.
_DEAL_BUY = 0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- Session buckets, in *broker-server* hours. Forex liquidity moves with
# --- the three desks; these are the conventional boundaries and they are
# --- named constants so the UI can print them rather than imply them.
SESSIONS: tuple[tuple[str, int, int], ...] = (
    ("ASIAN", 0, 8),
    ("LONDON", 8, 13),
    ("NEW_YORK", 13, 22),
)


def session_of(epoch_seconds: int, offset_minutes: int = 0) -> str:
    """Which desk was open when ``epoch_seconds`` fell.

    MT5 deal times are in the **broker server's** timezone, not UTC, and the
    offset is not discoverable from the deal itself. The caller states it,
    and the API echoes it back so the UI can label the clock it used instead
    of silently implying UTC.

    Hours outside every desk (the 22:00-00:00 gap) fall into ``ASIAN``,
    because that is the session that follows. A ``CLOSED`` bucket would be
    claiming the market was shut, which is a much stronger statement than
    this function can support.
    """
    stamp = _dt.datetime.fromtimestamp(
        int(epoch_seconds) + int(offset_minutes) * 60, tz=_dt.timezone.utc
    )
    hour = stamp.hour
    for name, start, end in SESSIONS:
        if start <= hour < end:
            return name
    return "ASIAN"


@dataclass(frozen=True)
class Trade:
    """One closed position, both legs folded together."""

    position_id: int
    symbol: str
    side: str
    volume: float
    entry_price: float
    exit_price: float
    opened_at: int
    closed_at: int
    profit: float
    swap: float
    commission: float
    comment: str = ""

    @property
    def realized(self) -> float:
        """Net result: gross profit plus swap and commission on both legs."""
        return round(self.profit + self.swap + self.commission, 2)

    @property
    def duration_seconds(self) -> int:
        return max(0, int(self.closed_at) - int(self.opened_at))

    @property
    def outcome(self) -> str:
        """``win`` / ``loss`` / ``flat`` -- the vocabulary the UI colours by."""
        if self.realized > 0:
            return "win"
        if self.realized < 0:
            return "loss"
        return "flat"

    def as_dict(self, *, offset_minutes: int = 0) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "profit": round(self.profit, 2),
            "swap": round(self.swap, 2),
            "commission": round(self.commission, 2),
            "realized": self.realized,
            "outcome": self.outcome,
            "duration_seconds": self.duration_seconds,
            "session": session_of(self.closed_at, offset_minutes),
            "comment": self.comment,
        }


def _field_of(deal: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a dict or an object, so callers need not convert."""
    if isinstance(deal, dict):
        return deal.get(name, default)
    return getattr(deal, name, default)


def pair_deals(deals: Iterable[Any], symbol: str = "") -> list[Trade]:
    """Fold raw deals into one :class:`Trade` per closed position.

    ``deals`` may hold dictionaries or objects carrying the documented MT5
    deal attributes; both are read through :func:`_field_of`.

    Only positions with a closing leg become trades. An ``IN`` deal whose
    position never closed is an **open** position: it belongs to the live
    snapshot, not to realized history, and counting it here would report the
    same money twice -- once as floating and once as booked.

    ``symbol`` filters when given, so a history panel can be per-chart
    without the caller fetching the whole account.
    """
    opens: dict[int, Any] = {}
    outs: dict[int, list[Any]] = {}

    for deal in deals or []:
        position_id = _as_int(_field_of(deal, "position_id"), 0)
        if position_id <= 0:
            continue
        if _as_int(_field_of(deal, "entry"), 0) == _ENTRY_OUT:
            # --- a position can close in several partial deals, so these are
            # --- collected rather than overwritten
            outs.setdefault(position_id, []).append(deal)
        else:
            opens.setdefault(position_id, deal)

    trades: list[Trade] = []
    for position_id, out_deals in outs.items():
        open_deal = opens.get(position_id)
        out_deal = out_deals[0]
        final_out = max(out_deals, key=lambda deal: _as_int(_field_of(deal, "time")))

        # --- money is summed across *every* leg. Commission is typically
        # --- charged twice (once per side), swap settles on the out leg, and
        # --- a partial close adds another leg entirely, so reading only the
        # --- closing deal understates the result.
        profit = swap = commission = 0.0
        for deal in ([open_deal] if open_deal is not None else []) + out_deals:
            profit += _as_float(_field_of(deal, "profit"))
            swap += _as_float(_field_of(deal, "swap"))
            commission += _as_float(_field_of(deal, "commission"))

        # --- entry context comes from the IN leg when there is one; without
        # --- it, the out leg is all we have and the fields stay 0 rather
        # --- than being guessed
        source = open_deal if open_deal is not None else out_deal
        opened_at = _as_int(_field_of(source, "time"), 0)
        entry_price = _as_float(_field_of(source, "price"))
        volume = _as_float(_field_of(source, "volume"))
        deal_symbol = str(_field_of(out_deal, "symbol", "") or "")
        if not deal_symbol:
            deal_symbol = str(_field_of(source, "symbol", "") or "")

        if symbol and deal_symbol and deal_symbol != symbol:
            continue

        # --- an OUT deal *sells* to close a long, so the trade's own side is
        # --- the opposite of the closing deal's type
        out_type = _as_int(_field_of(out_deal, "type"), _DEAL_BUY)
        side = "SELL" if out_type == _DEAL_BUY else "BUY"

        comment = str(_field_of(out_deal, "comment", "") or "")
        if not comment:
            comment = str(_field_of(source, "comment", "") or "")

        trades.append(
            Trade(
                position_id=position_id,
                symbol=deal_symbol,
                side=side,
                volume=round(volume, 4),
                entry_price=entry_price,
                exit_price=_as_float(_field_of(final_out, "price")),
                opened_at=opened_at,
                closed_at=_as_int(_field_of(final_out, "time"), 0),
                profit=profit,
                swap=swap,
                commission=commission,
                comment=comment,
            )
        )

    trades.sort(key=lambda trade: (trade.closed_at, trade.position_id))
    return trades


def _ratio(numerator: float, denominator: float) -> float | None:
    """``numerator / denominator``, or ``None`` when that is undefined.

    Returning ``None`` rather than ``0.0`` is the whole point: an undefined
    ratio is *unknown*, and the dashboard says so. A zero would be a claim.
    """
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _streaks(trades: list[Trade]) -> tuple[int, int]:
    """Longest run of wins and of losses, in chronological order."""
    best_win = best_loss = 0
    run = 0
    for trade in trades:
        if trade.realized > 0:
            run = run + 1 if run > 0 else 1
            best_win = max(best_win, run)
        elif trade.realized < 0:
            run = run - 1 if run < 0 else -1
            best_loss = max(best_loss, -run)
        # --- a flat trade breaks the streak without extending either side
        else:
            run = 0
    return best_win, best_loss


def metrics(trades: Iterable[Trade]) -> dict[str, Any]:
    """Aggregate statistics over closed ``trades``.

    Every ratio is ``None`` when its denominator is zero -- see the module
    docstring. ``trades`` is walked once into a list so the caller may pass a
    generator.
    """
    rows = list(trades)
    wins = [t for t in rows if t.realized > 0]
    losses = [t for t in rows if t.realized < 0]
    flats = [t for t in rows if t.realized == 0]

    gross_profit = round(sum(t.realized for t in wins), 2)
    gross_loss = round(abs(sum(t.realized for t in losses)), 2)
    net = round(sum(t.realized for t in rows), 2)

    avg_win = gross_profit / len(wins) if wins else None
    avg_loss = gross_loss / len(losses) if losses else None
    best_win, best_loss = _streaks(rows)

    # --- expectancy is the mean result per trade: the single number that
    # --- answers "does this system make money". Undefined with no trades.
    expectancy = round(net / len(rows), 2) if rows else None

    # --- avg_rr is average win divided by average loss. It is NOT the
    # --- reward:risk on the ticket, which lives in the snapshot; this is
    # --- what the trades actually returned.
    avg_rr = None
    if avg_win is not None and avg_loss:
        avg_rr = round(avg_win / avg_loss, 4)

    holds = [t.duration_seconds for t in rows if t.duration_seconds > 0]

    return {
        "total": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(flats),
        "net_realized": net,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "win_rate": _ratio(len(wins), len(rows)),
        "profit_factor": _ratio(gross_profit, gross_loss),
        "avg_win": round(avg_win, 2) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
        "avg_rr": avg_rr,
        "expectancy": expectancy,
        "max_win_streak": best_win,
        "max_loss_streak": best_loss,
        "best": round(max((t.realized for t in rows), default=0.0), 2) if rows else None,
        "worst": round(min((t.realized for t in rows), default=0.0), 2) if rows else None,
        "avg_hold_seconds": int(sum(holds) / len(holds)) if holds else None,
    }


def by_session(trades: Iterable[Trade], offset_minutes: int = 0) -> list[dict[str, Any]]:
    """Per-session statistics, in the fixed desk order.

    Every session appears even with no trades, so the UI renders a complete
    matrix instead of a shifting set of rows. A session with no closed trades
    reports ``total: 0`` and ``None`` ratios, which is a fact; omitting the
    row would let an empty New York desk look like it had never been
    checked.
    """
    rows = list(trades)
    buckets: dict[str, list[Trade]] = {name: [] for name, _, _ in SESSIONS}
    for trade in rows:
        buckets.setdefault(session_of(trade.closed_at, offset_minutes), []).append(trade)

    out: list[dict[str, Any]] = []
    for name, start, end in SESSIONS:
        out.append(
            {
                "session": name,
                "window": f"{start:02d}:00-{end:02d}:00",
                **metrics(buckets.get(name, [])),
            }
        )
    return out


def equity_curve(
    trades: Iterable[Trade], *, offset_minutes: int = 0
) -> list[dict[str, Any]]:
    """Cumulative realized P&L after each closing trade, oldest first.

    Starts from zero rather than from account balance: this is the shape of
    the *strategy's* result, and a balance would drag in deposits and other
    EAs. The first point is therefore always ``0.0``, which makes the curve
    honest about where measurement began.
    """
    rows = sorted(trades, key=lambda t: (t.closed_at, t.position_id))
    points: list[dict[str, Any]] = [
        {
            "closed_at": rows[0].opened_at if rows else 0,
            "cumulative": 0.0,
            "position_id": 0,
        }
    ]
    total = 0.0
    for trade in rows:
        total += trade.realized
        points.append(
            {
                "closed_at": trade.closed_at,
                "cumulative": round(total, 2),
                "position_id": trade.position_id,
                "realized": trade.realized,
                "session": session_of(trade.closed_at, offset_minutes),
            }
        )
    return points


def report(
    deals: Iterable[Any],
    *,
    symbol: str = "",
    offset_minutes: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    """The whole ``/api/history`` payload, minus the availability wrapper.

    ``limit`` caps the returned trade rows only. Aggregates are always
    computed over the full window -- a summary that changed when you paged
    the table would be worse than useless.
    """
    trades = pair_deals(deals, symbol)
    newest_first = list(reversed(trades))
    return {
        "trades": [
            trade.as_dict(offset_minutes=offset_minutes)
            for trade in newest_first[: max(0, limit)]
        ],
        "metrics": metrics(trades),
        "sessions": by_session(trades, offset_minutes),
        "equity": equity_curve(trades, offset_minutes=offset_minutes),
        "tz_offset_minutes": offset_minutes,
        "session_windows": [
            {"session": name, "from": start, "to": end} for name, start, end in SESSIONS
        ],
    }


__all__ = [
    "SESSIONS",
    "Trade",
    "by_session",
    "equity_curve",
    "metrics",
    "pair_deals",
    "report",
    "session_of",
]
