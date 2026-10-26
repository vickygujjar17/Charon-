"""The attended-execution loop: ticket in, EA verdict out, rendered as-is.

Where ``test_order.py`` pins the shape of a ticket, these tests pin the
round trip. The property under test is that the dashboard never has to
compute a volume, a stop or an R multiple: the EA publishes them and the
bridge copies them through untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

from charon_bridge import commands
from charon_bridge.schema import Snapshot, build_order_intent
from tests.fake_ea import SCHEMA, FakeEA, local_epoch



def test_order_ticket_travels_the_whole_loop(common_dir: Path) -> None:
    """API -> inbox -> EA adjudication -> snapshot -> dashboard.

    This is the assertion that matters for the release: the ticket the
    dashboard submits is understood by the EA-side contract, the EA answers
    with its own numbers, and the bridge surfaces that verdict without
    inventing anything. In particular the *volume* in the snapshot is the
    EA's, not the caller's -- the caller never sent one.
    """
    ea = FakeEA(common_dir)
    ea.publish()

    sent = commands.submit(
        "order_open", ea.symbol,
        order=build_order_intent(
            side="BUY", order_type="MARKET", entry=None,
            block_top=1.27000, block_bottom=1.25000,
            risk_pct=0.5, trail_trigger=4, rung=2,
        ),
    )
    assert sent["sent"]["verb"] == "order_open"

    verdict = ea.poll()
    assert verdict is not None
    assert verdict["accepted"] is True, verdict["reason"]
    assert verdict["verb"] == "order_open"

    # --- the EA solved a volume; the command never contained one
    assert "lots" not in sent["sent"]
    assert ea.order["lots"] > 0
    assert ea.order["status"] == "accepted"

    ea.publish()
    snap = Snapshot.from_json(json.loads(ea.state_file.read_text(encoding="utf-8")))
    assert snap.order.status == "accepted"
    assert snap.order.side == "BUY"
    assert snap.order.lots == ea.order["lots"]
    assert snap.order.rung == 2
    assert snap.order.ticket == ea.order["ticket"]


def test_limit_ticket_is_reported_as_pending_not_filled(common_dir: Path) -> None:
    ea = FakeEA(common_dir)
    commands.submit(
        "order_open", ea.symbol,
        order=build_order_intent(
            side="SELL", order_type="LIMIT", entry=1.26000,
            block_top=1.25000, block_bottom=1.27000,
            risk_pct=0.25, trail_trigger=0, rung=1,
        ),
    )
    verdict = ea.poll()
    assert verdict is not None and verdict["accepted"] is True
    assert ea.order["status"] == "pending"


def test_mixed_rr_and_price_stages_survive_the_loop(common_dir: Path) -> None:
    """A ladder that mixes both trigger kinds must arrive unchanged.

    v1.7.0 lets one ticket state an R trigger for one scale-in and an absolute
    price for the next. The two kinds are ordered against their own peers
    only, so a mixed ladder is legal -- and nothing in the pipe is allowed to
    reorder, drop or re-coerce a stage, because the EA's whole plan depends on
    stage n meaning tier n+2.
    """
    ea = FakeEA(common_dir)
    ea.publish()

    ticket = build_order_intent(
        side="BUY", order_type="MARKET", entry=None,
        block_top=1.27000, block_bottom=1.25000,
        risk_pct=0.5, trail_trigger=4, rung=0,
        stages=[
            {"kind": "rr", "trigger": 1.5, "lots_mult": 0.5},
            {"kind": "price", "trigger": 1.29000, "lots_mult": 0.75},
        ],
    )
    sent = commands.submit("order_open", ea.symbol, order=ticket)
    assert sent["sent"]["stages"] == [
        {"kind": "rr", "trigger": 1.5, "lots_mult": 0.5},
        {"kind": "price", "trigger": 1.29, "lots_mult": 0.75},
    ]

    verdict = ea.poll()
    assert verdict is not None
    assert verdict["accepted"] is True, verdict["reason"]

    # --- order preserved and values intact: an R multiple stays an R
    # --- multiple, a price stays a price, neither is rescaled in flight
    assert [row["kind"] for row in ea.stages] == ["rr", "price"]
    assert ea.stages[0]["trigger"] == 1.5
    assert ea.stages[0]["lots_mult"] == 0.5
    assert ea.stages[1]["trigger"] == 1.29
    assert ea.stages[1]["lots_mult"] == 0.75


def test_ea_refuses_a_retreating_stage_ladder(common_dir: Path) -> None:
    """The second line of defence for the ladder.

    ``build_order_intent`` already refuses this, so the file is written by
    hand: a plan whose second R trigger sits below the first could never fire,
    which would quietly turn a two-stage ticket into a one-stage ticket.
    """
    ea = FakeEA(common_dir)
    (ea.inbox / "00000000000000000003-000003-bad.json").write_text(json.dumps({
        "schema": SCHEMA,
        "id": "retreating-ladder",
        "verb": "order_open",
        "symbol": ea.symbol,
        "issued": local_epoch(),
        "side": "BUY",
        "order_type": "MARKET",
        "block_top": 1.27000,
        "block_bottom": 1.25000,
        "risk_pct": 0.5,
        "stages": [
            {"kind": "rr", "trigger": 3.0, "lots_mult": 0.5},
            {"kind": "rr", "trigger": 2.0, "lots_mult": 0.5},
        ],
    }), encoding="utf-8")

    verdict = ea.poll()
    assert verdict is not None
    assert verdict["accepted"] is False
    assert "advance" in verdict["reason"]


def test_ea_refuses_a_ticket_the_bridge_forgot_to_validate(common_dir: Path) -> None:
    """Defence in depth: the EA is not allowed to trust the writer.

    A hand-edited file bypasses ``build_order_intent`` entirely, so the EA
    must reject it on its own terms. This is the test that would fail if the
    MQL5 parser and the Python validator ever drifted apart.
    """
    ea = FakeEA(common_dir)
    inbox = ea.inbox
    (inbox / "00000000000000000001-000001-bad.json").write_text(json.dumps({
        "schema": SCHEMA,
        "id": "hand-edited",
        "verb": "order_open",
        "symbol": ea.symbol,
        "issued": local_epoch(),
        "side": "BUY",
        "order_type": "MARKET",
        "block_top": 1.25,          # inverted for a BUY
        "block_bottom": 1.27,
        "risk_pct": 0.5,
    }), encoding="utf-8")

    verdict = ea.poll()
    assert verdict is not None
    assert verdict["accepted"] is False
    assert "BUY block" in verdict["reason"]


def test_ea_refuses_an_order_verb_with_an_unknown_side(common_dir: Path) -> None:
    ea = FakeEA(common_dir)
    (ea.inbox / "00000000000000000002-000002-bad.json").write_text(json.dumps({
        "schema": SCHEMA, "id": "no-side", "verb": "order_cancel",
        "symbol": ea.symbol, "issued": local_epoch(), "ticket": 5,
        "side": "LONG",
    }), encoding="utf-8")
    verdict = ea.poll()
    assert verdict is not None and verdict["accepted"] is False
    assert "side" in verdict["reason"]
