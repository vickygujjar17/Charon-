"""Watched entries: a MARKET ticket that names a price waits for it.

The gap these tests close is specific and was live until v1.5.0:
``order_type`` was ``MARKET`` whenever no limit price was given, and the
``entry`` field was carried through to the EA, which then *ignored it* for a
MARKET ticket -- ``ApplyOrderCommand`` replaces the entry with the live ask or
bid. So an operator who typed 1.1050 next to MARKET got a fill at whatever the
market was, and the dashboard said "queued".

The routing rule is now explicit: MARKET with no entry fills immediately;
MARKET with an entry arms a watch. Everything below asserts one half of that
sentence, plus the properties that make a watch safe:

* it carries the identical validated ticket,
* it is a different object from a pending order and is cancelled by id,
* restating an activation is refused rather than arbitrated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from charon_bridge import commands
from charon_bridge.schema import (
    FORBIDDEN_VERBS,
    MAX_WATCHES,
    WATCH_VERBS,
    SchemaError,
    Snapshot,
    Watch,
    build_command,
    build_order_intent,
)
from tests.fake_ea import SCHEMA, FakeEA, local_epoch


def _ticket(**overrides):
    """A valid BUY ticket that differs from the defaults only where asked."""
    fields = dict(
        side="BUY", order_type="MARKET", entry=1.25000,
        block_top=1.27000, block_bottom=1.24000,
        risk_pct=0.5, trail_trigger=0, rung=1,
    )
    fields.update(overrides)
    return build_order_intent(**fields)


#+------------------------------------------------------------------+
#| What counts as a watch                                            |
#+------------------------------------------------------------------+
def test_market_without_an_entry_is_an_immediate_fill() -> None:
    """The pre-v1.5.0 behaviour, still available and still the default."""
    assert _ticket(entry=None).is_watch is False


def test_market_with_an_entry_is_a_watch() -> None:
    """The trap this release closes.

    A MARKET ticket naming 1.25000 must NOT be filled at market. It is armed.
    """
    intent = _ticket(entry=1.25000)
    assert intent.is_watch is True
    assert intent.entry == 1.25000


def test_a_limit_ticket_is_never_a_watch() -> None:
    """LIMIT already defers, and it does so at the broker. Two mechanisms for
    one intent would be two sources of truth, so a LIMIT stays an order."""
    assert _ticket(order_type="LIMIT", entry=1.25500).is_watch is False


#+------------------------------------------------------------------+
#| The command the bridge writes                                     |
#+------------------------------------------------------------------+
def test_watch_add_carries_the_whole_validated_ticket() -> None:
    """A watch defers *when* a ticket is submitted, never *what* it says.

    Every field the EA adjudicates must ride along, or the deferred ticket
    would be a weaker one than the immediate ticket it stands in for.
    """
    intent = _ticket(entry=1.25000, trail_trigger=2, rung=2,
                     basket_risk_ceiling_pct=1.5, journal="watching the retest",
                     trail_start_price=1.28000)
    doc = build_command("watch_add", 0, symbol="EURUSD", command_id="w1", order=intent)

    assert doc["verb"] == "watch_add"
    assert doc["schema"] == SCHEMA
    assert doc["entry"] == 1.25000
    assert doc["order_type"] == "MARKET"
    assert doc["block_top"] == 1.27000
    assert doc["block_bottom"] == 1.24000
    assert doc["risk_pct"] == 0.5
    assert doc["trail_trigger"] == 2
    assert doc["rung"] == 2
    assert doc["basket_risk_ceiling_pct"] == 1.5
    # --- the tag is the upper-cased ASCII form; the full note stays local
    assert doc["journal_tag"] == "WATCHING-THE-RETEST"
    assert doc["trail_start_price"] == 1.28000


def test_watch_add_without_a_price_is_refused() -> None:
    """A watch with nothing to watch for is not a watch.

    The constructor refuses it because the alternatives -- the EA firing at
    once, or waiting forever -- are both wrong answers to a malformed ticket.
    """
    with pytest.raises(SchemaError, match="entry price to watch for"):
        build_command("watch_add", 0, symbol="EURUSD", command_id="w2",
                      order=_ticket(entry=None))


def test_watch_add_refuses_a_limit_ticket() -> None:
    with pytest.raises(SchemaError, match="only defers a MARKET"):
        build_command("watch_add", 0, symbol="EURUSD", command_id="w3",
                      order=_ticket(order_type="LIMIT", entry=1.25500))


def test_watch_cancel_needs_a_watch_id_not_a_ticket() -> None:
    """The distinction is load-bearing: nothing reached the broker, so there
    is no ticket to name and an order_cancel would find nothing."""
    doc = build_command("watch_cancel", 0, symbol="EURUSD", command_id="w4",
                        watch_id=7)
    assert doc["watch_id"] == 7
    assert "ticket" not in doc

    with pytest.raises(SchemaError, match="positive watch_id"):
        build_command("watch_cancel", 0, symbol="EURUSD", command_id="w5")


def test_the_raw_watch_verbs_stay_forbidden() -> None:
    """Widening the vocabulary must not widen it to ``watch`` or
    ``watch_fire``: firing is the EA's conclusion from its own tick."""
    assert "watch" in FORBIDDEN_VERBS
    assert "watch_fire" in FORBIDDEN_VERBS
    for verb in FORBIDDEN_VERBS:
        with pytest.raises(SchemaError):
            build_command(verb, 0, symbol="EURUSD", command_id="x")


def test_watch_verbs_are_a_subset_of_allowed_verbs() -> None:
    assert set(WATCH_VERBS) == {"watch_add", "watch_cancel"}
    build_command("watch_add", 0, symbol="EURUSD", command_id="x",
                  order=_ticket(entry=1.25))
    build_command("watch_cancel", 0, symbol="EURUSD", command_id="x", watch_id=1)


def _bare_snapshot(**extra):
    """The minimum a snapshot needs, plus whatever the test is exercising.

    ``basket``, ``model``, ``trail`` and ``pyramid`` are mandatory in
    :class:`Snapshot`: a snapshot missing one is a snapshot nobody could
    reason about, so the module refuses it rather than rendering zeroes.
    """
    raw = {
        "schema": SCHEMA, "symbol": "EURUSD", "server_time": 1, "digits": 5,
        "basket": {"valid": False},
        "model": {},
        "trail": {"mode": 0},
        "pyramid": {"enabled": False},
    }
    raw.update(extra)
    return raw


#+------------------------------------------------------------------+
#| The snapshot the EA publishes                                     |
#+------------------------------------------------------------------+
def test_watches_parse_and_survive_a_bad_row() -> None:
    """One malformed row must not hide the rest -- same rule as ``pending``.

    A watch the operator cannot see is one they cannot cancel.
    """
    raw = _bare_snapshot(
        watch=[
            {"id": 1, "side": "BUY", "price": 1.25000, "status": "armed"},
            {"id": 0, "side": "BUY", "price": 1.25000},      # no usable id
            {"side": "BUY", "price": 1.25000},               # ditto
            {"id": 2, "side": "LONG", "price": 1.26000},     # no usable side
            {"id": 3, "side": "SELL", "target": 1.27000},    # ``target`` alias
            "not-an-object",
        ],
    )
    snap = Snapshot.from_json(raw)

    assert [w.watch_id for w in snap.watch] == [1, 3]
    assert snap.watch[0].status == "armed"
    # --- ``target`` is accepted where the EA said ``price``; the two must
    # --- never disagree, and price wins when both are present.
    assert snap.watch[1].price == 1.27000


def test_a_watch_without_a_price_reports_none_not_zero() -> None:
    """A zero would render as a real target nobody chose."""
    snap = Snapshot.from_json(_bare_snapshot(watch=[{"id": 9, "side": "BUY"}]))
    assert snap.watch[0].price is None


def test_a_schema_3_snapshot_has_no_watches_and_no_quote() -> None:
    """v1.5.0 still renders an older chart; it simply has nothing to show."""
    raw = _bare_snapshot()
    raw["schema"] = 3
    snap = Snapshot.from_json(raw)
    assert snap.watch == ()
    assert snap.bid is None and snap.ask is None


def test_watch_id_accepts_either_key() -> None:
    assert Watch.from_json({"id": 4, "side": "BUY"}).watch_id == 4
    assert Watch.from_json({"watch_id": 5, "side": "BUY"}).watch_id == 5


def test_the_watch_cap_matches_the_ea() -> None:
    """Must equal CHARON_BRIDGE_MAX_WATCHES in CharonBridge.mqh; a mismatch
    would let the bridge arm a queue the EA truncates."""
    assert MAX_WATCHES == 8


#+------------------------------------------------------------------+
#| Round trip through the fake EA                                    |
#+------------------------------------------------------------------+
def test_a_watch_is_armed_not_filled(common_dir: Path) -> None:
    """The whole point, end to end: the EA validates the ticket, parks it,
    and reports it in ``watch`` -- with no position and no verdict claiming
    a fill."""
    ea = FakeEA(common_dir)
    commands.submit("watch_add", ea.symbol,
                    order=_ticket(entry=1.25000, risk_pct=0.5))

    verdict = ea.poll()
    assert verdict is not None
    assert verdict["accepted"] is True, verdict["reason"]
    assert verdict["verb"] == "watch_add"

    # --- parked, not submitted
    assert len(ea.watches) == 1
    assert ea.watches[0]["side"] == "BUY"
    assert ea.watches[0]["price"] == 1.25000
    assert ea.watches[0]["status"] == "armed"
    assert ea.order["status"] == "watching"
    assert ea.order["lots"] == 0.0        # nothing was sized
    assert ea.order["ticket"] == 0        # nothing reached the broker

    ea.publish()
    snap = Snapshot.from_json(json.loads(ea.state_file.read_text(encoding="utf-8")))
    assert len(snap.watch) == 1
    assert snap.watch[0].side == "BUY"
    assert snap.watch[0].price == 1.25000
    assert snap.order.status == "watching"
    # --- the quote rides along so the dashboard can show the distance
    assert snap.bid is not None and snap.ask is not None


def test_a_watch_can_be_cancelled_by_its_id(common_dir: Path) -> None:
    ea = FakeEA(common_dir)
    commands.submit("watch_add", ea.symbol, order=_ticket(entry=1.25000))
    ea.poll()
    assert len(ea.watches) == 1
    watch_id = ea.watches[0]["id"]

    commands.submit("watch_cancel", ea.symbol, watch_id=watch_id)
    verdict = ea.poll()
    assert verdict is not None and verdict["accepted"] is True
    assert ea.watches == []
    assert ea.order["status"] == "accepted"


def test_cancelling_an_unknown_watch_is_reported_not_ignored(common_dir: Path) -> None:
    """A silent no-op would leave the operator believing they cancelled."""
    ea = FakeEA(common_dir)
    commands.submit("watch_cancel", ea.symbol, watch_id=99)
    verdict = ea.poll()
    assert verdict is not None and verdict["accepted"] is True
    assert ea.watches == []
    assert ea.order["status"] == "rejected"
    assert "no watch" in ea.order["reason"]


def test_the_ea_refuses_a_watch_with_no_price(common_dir: Path) -> None:
    """Defence in depth: a hand-edited file bypasses the Python validator, so
    the EA has to refuse it on its own terms."""
    ea = FakeEA(common_dir)
    (ea.inbox / "00000000000000000009-000009-bad.json").write_text(json.dumps({
        "schema": SCHEMA, "id": "no-price", "verb": "watch_add",
        "symbol": ea.symbol, "issued": local_epoch(),
        "side": "BUY", "order_type": "MARKET",
        "block_top": 1.27, "block_bottom": 1.24, "risk_pct": 0.5,
    }), encoding="utf-8")

    verdict = ea.poll()
    assert verdict is not None
    assert verdict["accepted"] is False
    assert "entry price" in verdict["reason"]
    assert ea.watches == []


def test_the_ea_refuses_watch_cancel_without_an_id(common_dir: Path) -> None:
    ea = FakeEA(common_dir)
    (ea.inbox / "00000000000000000010-000010-bad.json").write_text(json.dumps({
        "schema": SCHEMA, "id": "no-id", "verb": "watch_cancel",
        "symbol": ea.symbol, "issued": local_epoch(),
    }), encoding="utf-8")

    verdict = ea.poll()
    assert verdict is not None and verdict["accepted"] is False
    assert "watch_id" in verdict["reason"]


def test_watch_and_pending_are_separate_queues(common_dir: Path) -> None:
    """A watch must never appear in ``pending``: it has no broker ticket, so a
    dashboard that merged them would offer to cancel a ticket that is not the
    watch's and would fail to cancel the watch."""
    ea = FakeEA(common_dir)
    commands.submit("watch_add", ea.symbol, order=_ticket(entry=1.25000))
    ea.poll()

    ea.pending.append({
        "ticket": 4242, "side": "SELL", "order_type": "LIMIT",
        "price": 1.26500, "sl": 1.27000, "lot": 0.10, "placed_at": 1,
    })
    ea.publish()

    snap = Snapshot.from_json(json.loads(ea.state_file.read_text(encoding="utf-8")))
    assert len(snap.watch) == 1
    assert len(snap.pending) == 1
    assert snap.pending[0].ticket == 4242
    # --- the two id spaces must not be conflated
    assert snap.watch[0].watch_id != snap.pending[0].ticket
