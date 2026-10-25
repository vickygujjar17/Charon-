"""The attended-execution contract: intent in, EA adjudication out.

These tests exist to pin one property above all others: the bridge decides
*what was asked for*, and the EA decides *what happens*. A command file may
carry a side, a block, a risk percentage and a rung -- and it may never carry
a volume, a stop distance or an R multiple, because those are the EA's
answers, not the caller's inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from charon_bridge import commands
from charon_bridge.schema import (
    MAX_RISK_PCT,
    MAX_RUNG,
    ORDER_VERBS,
    SchemaError,
    build_command,
    build_order_intent,
)
from charon_bridge.version import SCHEMA_VERSION


def _buy(**overrides: object):
    fields: dict[str, object] = {
        "side": "BUY",
        "order_type": "LIMIT",
        "entry": 1.26000,
        "block_top": 1.27000,
        "block_bottom": 1.25000,
        "risk_pct": 0.5,
        "trail_trigger": 4,
        "rung": 2,
    }
    fields.update(overrides)
    return build_order_intent(**fields)


#+------------------------------------------------------------------+
#| Intent validation                                                |
#+------------------------------------------------------------------+
def test_order_verbs_are_the_three_order_verbs() -> None:
    assert set(ORDER_VERBS) == {"order_open", "order_modify", "order_cancel"}


@pytest.mark.parametrize("side", ["buy", "BUY ", "", "LONG", "SELLS"])
def test_side_must_be_the_exact_uppercase_literal(side: str) -> None:
    with pytest.raises(SchemaError):
        _buy(side=side)


@pytest.mark.parametrize("kind", ["STOP", "market", "LIMIT ", "PENDING"])
def test_order_type_is_an_exact_literal(kind: str) -> None:
    with pytest.raises(SchemaError):
        _buy(order_type=kind)


def test_block_must_have_height() -> None:
    with pytest.raises(SchemaError):
        _buy(block_top=1.26, block_bottom=1.26)


def test_buy_block_must_be_the_right_way_up() -> None:
    with pytest.raises(SchemaError):
        _buy(block_top=1.25, block_bottom=1.27)


def test_sell_block_must_be_the_right_way_up() -> None:
    # --- a SELL block runs downward: block_bottom sits ABOVE block_top
    intent = _buy(side="SELL", order_type="MARKET", entry=None,
                  block_top=1.25000, block_bottom=1.27000, rung=0)
    assert intent.side == "SELL"
    # --- so the BUY orientation must be refused for a SELL ticket
    with pytest.raises(SchemaError):
        _buy(side="SELL", block_top=1.27000, block_bottom=1.25000)


def test_limit_entry_must_sit_inside_the_block() -> None:
    with pytest.raises(SchemaError):
        _buy(entry=1.24500)          # below the block
    with pytest.raises(SchemaError):
        _buy(entry=1.27500)          # above the block


def test_market_ticket_does_not_need_an_entry() -> None:
    intent = _buy(order_type="MARKET", entry=None)
    assert intent.entry is None
    assert "entry" not in intent.to_doc()


@pytest.mark.parametrize("value", [0.0, -1.0, MAX_RISK_PCT + 0.01, float("nan"),
                                   float("inf"), "0.5", None, True])
def test_risk_pct_is_bounded_and_numeric(value: object) -> None:
    with pytest.raises(SchemaError):
        _buy(risk_pct=value)


@pytest.mark.parametrize("value", [-1, MAX_RUNG + 1, 1.5, "2", True])
def test_rung_is_a_small_non_negative_integer(value: object) -> None:
    with pytest.raises(SchemaError):
        _buy(rung=value)


def test_trail_trigger_must_be_a_known_mode() -> None:
    with pytest.raises(SchemaError):
        _buy(trail_trigger=99)


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf"), "1.26"])
def test_prices_must_be_finite_and_positive(value: object) -> None:
    with pytest.raises(SchemaError):
        _buy(block_top=value)


#+------------------------------------------------------------------+
#| Command construction                                             |
#+------------------------------------------------------------------+
def test_order_open_refuses_to_build_without_an_intent() -> None:
    with pytest.raises(SchemaError):
        build_command("order_open", 1, symbol="GBPUSD", command_id="x")


def test_order_cancel_refuses_to_build_without_a_ticket() -> None:
    with pytest.raises(SchemaError):
        build_command("order_cancel", 1, symbol="GBPUSD", command_id="x")
    with pytest.raises(SchemaError):
        build_command("order_cancel", 1, symbol="GBPUSD", command_id="x", ticket=0)


def test_order_command_carries_intent_and_no_volume() -> None:
    doc = build_command("order_open", 1_760_000_000, symbol="GBPUSD",
                        command_id="abc", order=_buy())
    assert doc["verb"] == "order_open"
    assert doc["side"] == "BUY"
    assert doc["block_top"] == 1.27
    assert doc["block_bottom"] == 1.25
    assert doc["risk_pct"] == 0.5
    assert doc["rung"] == 2
    # --- the EA solves volume and stop; the caller never sends either
    for forbidden in ("lots", "volume", "lot", "sl", "stop", "tp", "volume_size"):
        assert forbidden not in doc


def test_submit_writes_an_order_ticket_that_the_ea_accepts(common_dir: Path) -> None:
    sent = commands.submit("order_open", "GBPUSD", order=_buy())
    inbox = common_dir / "Charon" / "commands"
    loaded = json.loads((inbox / sent["file"]).read_text(encoding="utf-8"))
    assert loaded["verb"] == "order_open"
    assert loaded["block_top"] == 1.27
    # --- the wire revision, not a literal: a schema bump must not need a test
    # --- edit, but the *fact* that a ticket stamps the current schema must
    # --- stay pinned.
    assert loaded["schema"] == SCHEMA_VERSION


#+------------------------------------------------------------------+
#| v1.4.0: risk in money, basket ceiling, stages, journal            |
#+------------------------------------------------------------------+
def test_risk_may_be_stated_in_money_instead_of_percent() -> None:
    """Option A: the figure sizes the *primary* leg, and only one unit is sent."""
    intent = _buy(risk_pct=None, risk_money=250.0)
    doc = intent.to_doc()
    assert doc["risk_money"] == 250.0
    assert "risk_pct" not in doc
    assert intent.risk_mode == "money"


def test_a_ticket_may_not_carry_two_risk_figures() -> None:
    """Two answers to one question is a refusal, not a silent preference."""
    with pytest.raises(SchemaError, match="not both"):
        _buy(risk_pct=0.5, risk_money=250.0)


def test_a_ticket_must_carry_a_risk_figure() -> None:
    with pytest.raises(SchemaError, match="either risk_pct or risk_money"):
        _buy(risk_pct=None)


@pytest.mark.parametrize("value", [0.0, -5.0, float("nan"), float("inf"), "250", True, None])
def test_risk_money_must_be_a_positive_number(value: object) -> None:
    with pytest.raises(SchemaError):
        _buy(risk_pct=None, risk_money=value)


def test_basket_ceiling_is_carried_and_can_be_omitted() -> None:
    with_ceiling = _buy(basket_risk_ceiling_pct=1.5).to_doc()
    assert with_ceiling["basket_risk_ceiling_pct"] == 1.5
    # --- absent is the pre-1.4.0 shape: the EA keeps InpPyramidMaxRiskPct
    assert "basket_risk_ceiling_pct" not in _buy().to_doc()


def test_basket_ceiling_below_the_primary_risk_is_a_contradiction() -> None:
    with pytest.raises(SchemaError, match="exceeds the basket ceiling"):
        _buy(risk_pct=2.0, basket_risk_ceiling_pct=1.0)


def test_basket_ceiling_is_bounded() -> None:
    with pytest.raises(SchemaError):
        _buy(basket_risk_ceiling_pct=101.0)


def test_trail_activation_is_per_ticket_and_optional() -> None:
    doc = _buy(trail_start_rr=1.5).to_doc()
    assert doc["trail_start_rr"] == 1.5
    assert "trail_start_rr" not in _buy().to_doc()


def test_stages_are_orders_of_advance_not_volumes() -> None:
    """A stage carries a multiplier, never a lot count."""
    doc = _buy(stages=[{"kind": "rr", "trigger": 2.0, "lots_mult": 0.5}]).to_doc()
    stage = doc["stages"][0]
    assert stage == {"kind": "rr", "trigger": 2.0, "lots_mult": 0.5}
    for forbidden in ("lots", "volume", "lot"):
        assert forbidden not in stage


def test_too_many_stages_is_refused() -> None:
    from charon_bridge.schema import MAX_STAGES

    too_many = [{"kind": "rr", "trigger": float(index + 2)} for index in range(MAX_STAGES + 1)]
    with pytest.raises(SchemaError, match="at most"):
        _buy(stages=too_many)


def test_stage_trigger_must_advance_for_a_buy() -> None:
    """A stage below the trigger already reached could never fire."""
    with pytest.raises(SchemaError, match="advance"):
        _buy(stages=[{"trigger": 3.0}, {"trigger": 2.0}])


def test_stage_price_must_sit_beyond_the_entry() -> None:
    with pytest.raises(SchemaError, match="not beyond the entry"):
        _buy(stages=[{"kind": "price", "trigger": 1.255}])


def test_stage_kind_must_be_known() -> None:
    with pytest.raises(SchemaError, match="kind"):
        _buy(stages=[{"kind": "atr", "trigger": 2.0}])


def test_journal_is_kept_and_a_tag_is_derived() -> None:
    doc = _buy(journal="London break \u2014 retest of VWAP").to_doc()
    assert doc["journal"] == "London break \u2014 retest of VWAP"
    tag = doc["journal_tag"]
    # --- the tag rides in a broker comment, so it must be printable ASCII
    assert tag.isascii()
    assert all(32 <= ord(char) <= 126 for char in tag)
    # --- and the note is never recovered from the tag
    assert tag != doc["journal"]


def test_a_blank_journal_is_no_journal() -> None:
    assert "journal" not in _buy(journal="   ").to_doc()


def test_journal_is_bounded() -> None:
    from charon_bridge.schema import MAX_JOURNAL_LEN

    with pytest.raises(SchemaError, match="limit"):
        _buy(journal="x" * (MAX_JOURNAL_LEN + 1))


#+------------------------------------------------------------------+
#| v1.4.0: the revision gate                                        |
#+------------------------------------------------------------------+
def test_a_schema_2_snapshot_is_still_renderable() -> None:
    """Tolerant on the way in: every v1.4.0 addition is additive."""
    from charon_bridge.schema import Snapshot
    from tests.conftest import make_snapshot

    parsed = Snapshot.from_json(make_snapshot(schema=2))
    assert parsed.schema == 2
    assert parsed.pending == ()


def test_an_unknown_snapshot_schema_is_refused() -> None:
    from charon_bridge.schema import Snapshot
    from tests.conftest import make_snapshot

    with pytest.raises(SchemaError):
        Snapshot.from_json(make_snapshot(schema=99))


def test_pending_orders_parse_and_survive_a_bad_row() -> None:
    """One unusable row must not hide the rest -- it could not be cancelled."""
    from charon_bridge.schema import Snapshot
    from tests.conftest import make_snapshot

    raw = make_snapshot(pending=[
        {"ticket": 900, "side": "BUY", "order_type": "LIMIT", "price": 1.25,
         "sl": 1.24, "lot": 0.2, "placed_at": 1_760_000_000},
        {"ticket": 0, "side": "BUY"},          # unusable ticket
        {"ticket": 901, "side": "SIDEWAYS"},   # unusable side
        {"ticket": 902, "side": "SELL", "order_type": "LIMIT", "price": 1.30},
    ])
    parsed = Snapshot.from_json(raw)
    assert [row.ticket for row in parsed.pending] == [900, 902]
    assert parsed.pending[0].stop == 1.24
