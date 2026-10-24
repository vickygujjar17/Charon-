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
    assert loaded["schema"] == 2
