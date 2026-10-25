"""Reconciliation: telling "unmanaged" apart from "we cannot tell".

The properties here are all about *not* saying things. A dashboard that
reports a managed position as unmanaged is a nuisance; one that reports an
unmanaged position as managed is dangerous; and one that shouts during a
terminal restart teaches the operator to ignore the alert entirely.
"""

from __future__ import annotations

from typing import Any

import pytest

from charon_bridge import reconcile
from charon_bridge.schema import Snapshot
from tests.conftest import make_snapshot


def _position(ticket: int, symbol: str = "GBPUSD", **overrides: Any) -> dict[str, Any]:
    row = {
        "ticket": ticket,
        "symbol": symbol,
        "side": "BUY",
        "volume": 0.10,
        "price_open": 1.26000,
        "profit": 12.5,
        "magic": 20240001,
        "comment": "",
    }
    row.update(overrides)
    return row


def _snapshot(**overrides: Any) -> Snapshot:
    """A parsed snapshot, exactly as ``state.read_snapshot`` would build it."""
    return Snapshot.from_json(make_snapshot(**overrides))


def _live(*tickets: int, symbol: str = "GBPUSD", **overrides: Any) -> dict[str, Snapshot]:
    """One live chart on ``symbol`` whose legs are ``tickets``."""
    legs = [dict(make_snapshot()["legs"][0], ticket=ticket) for ticket in tickets]
    return {symbol: _snapshot(legs=legs, **overrides)}


#+------------------------------------------------------------------+
#| The clean cases                                                   |
#+------------------------------------------------------------------+
def test_a_position_in_the_snapshot_is_managed() -> None:
    result = reconcile.classify(
        [_position(111)], _live(111), reconcile.Tracker(), now=1_760_000_000.0
    )
    assert result["positions"] == []
    assert result["summary"]["state"] == reconcile.STATE_OK
    assert result["summary"]["clean"] is True


def test_a_symbol_with_no_chart_is_reported_immediately() -> None:
    """Waiting cannot help: nothing will ever publish this symbol."""
    result = reconcile.classify(
        [_position(500, "USDJPY")], _live(111), reconcile.Tracker(), now=1_760_000_000.0
    )
    assert [item["ticket"] for item in result["positions"]] == [500]
    assert result["positions"][0]["verdict"] == reconcile.NO_CHARON
    assert "USDJPY" in result["positions"][0]["reason"]


#+------------------------------------------------------------------+
#| The N-of-N debounce                                               |
#+------------------------------------------------------------------+
def test_an_unseen_ticket_needs_three_observations() -> None:
    """A fill can appear a tick after the EA publishes. Do not cry wolf."""
    tracker = reconcile.Tracker()
    snapshots = _live(111)

    first = reconcile.classify([_position(222)], snapshots, tracker, now=1.0)
    assert first["positions"] == []
    assert [item["ticket"] for item in first["pending"]] == [222]
    assert first["summary"]["clean"] is True, "a pending finding is not an alert"

    second = reconcile.classify([_position(222)], snapshots, tracker, now=2.0)
    assert second["positions"] == []
    assert second["pending"][0]["seen"] == 2

    third = reconcile.classify([_position(222)], snapshots, tracker, now=3.0)
    assert [item["ticket"] for item in third["positions"]] == [222]
    assert third["positions"][0]["seen"] == reconcile.CONFIRMATIONS
    assert third["positions"][0]["verdict"] == reconcile.NOT_TRACKED
    assert third["summary"]["clean"] is False
    assert third["summary"]["confirmed"] == 1


def test_a_sighting_resets_the_count() -> None:
    """A transient gap must not accumulate towards a later false alarm."""
    tracker = reconcile.Tracker()
    confirmations = reconcile.CONFIRMATIONS

    for step in range(confirmations - 1):
        reconcile.classify([_position(222)], _live(111), tracker, now=float(step))
    assert tracker.count(222) == confirmations - 1

    # --- the EA catches up and publishes the leg
    reconciled = reconcile.classify([_position(222)], _live(111, 222), tracker, now=9.0)
    assert reconciled["positions"] == []
    assert tracker.count(222) == 0

    # --- so the next absence starts from zero again
    again = reconcile.classify([_position(222)], _live(111), tracker, now=10.0)
    assert again["positions"] == []
    assert again["pending"][0]["seen"] == 1


def test_a_closed_position_is_forgotten() -> None:
    """Counters must not outlive the position or leak onto a reused ticket."""
    tracker = reconcile.Tracker()
    reconcile.classify([_position(222)], _live(111), tracker, now=1.0)
    assert tracker.count(222) == 1

    reconcile.classify([], _live(111), tracker, now=2.0)
    assert tracker.count(222) == 0


def test_unmanaged_for_is_measured_from_the_first_sighting() -> None:
    tracker = reconcile.Tracker()
    reconcile.classify([_position(222)], _live(111), tracker, now=100.0)
    later = reconcile.classify([_position(222)], _live(111), tracker, now=104.5)
    assert later["pending"][0]["unmanaged_for"] == pytest.approx(4.5)


#+------------------------------------------------------------------+
#| Uncertainty must never read as "all clear"                        |
#+------------------------------------------------------------------+
def test_a_starting_ea_is_unknown_not_empty() -> None:
    """An EA that has not finished starting publishes no legs.

    Read literally that says every position on the symbol is unmanaged, so
    a terminal restart would fire alerts about positions Charon itself
    opened. The chart's state must be ``unknown`` and nothing may be
    confirmed on its evidence.
    """
    tracker = reconcile.Tracker()
    starting = {"GBPUSD": _snapshot(ready=False, legs=[])}

    for step in range(reconcile.CONFIRMATIONS + 2):
        result = reconcile.classify([_position(111)], starting, tracker, now=float(step))

    assert result["positions"] == [], "a starting EA reported a real position as unmanaged"
    assert result["symbols"][0]["state"] == reconcile.STATE_UNKNOWN
    assert result["summary"]["clean"] is False
    assert result["summary"]["state"] == reconcile.STATE_UNKNOWN


def test_a_missing_snapshot_for_a_known_symbol_is_unknown() -> None:
    """The caller passes ``None`` when it has nothing trustworthy to offer."""
    result = reconcile.classify(
        [_position(111)], {"GBPUSD": None}, reconcile.Tracker(), now=1.0
    )
    assert result["positions"] == []
    assert result["symbols"][0]["state"] == reconcile.STATE_UNKNOWN
    assert result["summary"]["clean"] is False


def test_no_charts_available_is_unavailable_not_clean() -> None:
    """Nothing checked must never be rendered as nothing found."""
    result = reconcile.classify([], {}, reconcile.Tracker(), now=1.0)
    assert result["summary"]["state"] == reconcile.STATE_UNAVAILABLE
    assert result["summary"]["clean"] is False


def test_clean_requires_every_chart_to_be_healthy() -> None:
    snapshots = {"GBPUSD": _snapshot(legs=[]), "EURUSD": _snapshot(ready=False, legs=[])}
    result = reconcile.classify([], snapshots, reconcile.Tracker(), now=1.0)
    assert {item["symbol"]: item["state"] for item in result["symbols"]} == {
        "GBPUSD": reconcile.STATE_OK,
        "EURUSD": reconcile.STATE_UNKNOWN,
    }
    assert result["summary"]["clean"] is False
