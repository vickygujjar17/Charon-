"""The command vocabulary is the security boundary, so it gets its own tests.

The property under test is negative and that is deliberate: we are
asserting that certain things are *impossible*, not that some feature
works. A future change that adds an ``open`` verb has to delete a test
that says it must not exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from charon_bridge import commands
from charon_bridge.schema import (
    ALLOWED_VERBS,
    FORBIDDEN_VERBS,
    TRAIL_MODE_NAMES,
    SchemaError,
    build_command,
)


def test_allowed_verbs_are_exactly_four() -> None:
    assert set(ALLOWED_VERBS) == {"arm", "disarm", "trail_mode", "ping"}


@pytest.mark.parametrize("verb", FORBIDDEN_VERBS)
def test_no_order_verb_can_be_built(verb: str) -> None:
    """Every order-ish verb is refused by the constructor itself."""
    with pytest.raises(SchemaError):
        build_command(verb, 1_760_000_000, symbol="GBPUSD", command_id="x")


def test_unknown_verb_cannot_be_built() -> None:
    with pytest.raises(SchemaError):
        build_command("rm -rf", 1, symbol="GBPUSD", command_id="x")


def test_arm_command_shape() -> None:
    doc = build_command("arm", 1_760_000_000, symbol="GBPUSD", command_id="abc")
    assert doc == {
        "schema": 1,
        "id": "abc",
        "verb": "arm",
        "symbol": "GBPUSD",
        "issued": 1_760_000_000,
    }


def test_trail_mode_requires_a_valid_mode() -> None:
    with pytest.raises(SchemaError):
        build_command("trail_mode", 1, symbol="GBPUSD", command_id="x")
    with pytest.raises(SchemaError):
        build_command("trail_mode", 1, symbol="GBPUSD", command_id="x", mode=99)


@pytest.mark.parametrize("mode", sorted(TRAIL_MODE_NAMES))
def test_every_trail_mode_is_accepted(mode: int) -> None:
    doc = build_command("trail_mode", 1, symbol="GBPUSD", command_id="x", mode=mode)
    assert doc["mode"] == mode


def test_submit_writes_one_fifo_named_file(common_dir: Path) -> None:
    first = commands.submit("arm", "GBPUSD")
    second = commands.submit("disarm", "GBPUSD")

    inbox = common_dir / "Charon" / "commands"
    files = sorted(p.name for p in inbox.glob("*.json"))
    assert len(files) == 2
    # --- lexicographic order must match submission order, because that is
    # --- how the EA decides which command to apply first
    assert files == sorted([first["file"], second["file"]])
    assert files[0] == first["file"]


def test_submit_payload_round_trips(common_dir: Path) -> None:
    sent = commands.submit("trail_mode", "GBPUSD", mode=4)
    inbox = common_dir / "Charon" / "commands"
    loaded = json.loads((inbox / sent["file"]).read_text(encoding="utf-8"))
    assert loaded["verb"] == "trail_mode"
    assert loaded["mode"] == 4
    assert loaded["symbol"] == "GBPUSD"
    assert loaded["schema"] == 1
    assert isinstance(loaded["issued"], int)


def test_submit_leaves_no_temp_files(common_dir: Path) -> None:
    commands.submit("arm", "GBPUSD")
    inbox = common_dir / "Charon" / "commands"
    assert not list(inbox.glob("*.tmp"))


def test_submit_refuses_a_forbidden_verb(common_dir: Path) -> None:
    with pytest.raises(SchemaError):
        commands.submit("close", "GBPUSD")


def test_pending_lists_only_queued_commands(common_dir: Path) -> None:
    commands.submit("arm", "GBPUSD")
    assert len(commands.pending()) == 1
    processed = common_dir / "Charon" / "commands" / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    assert len(commands.pending()) == 1  # processed must not leak in


def test_recent_reports_the_ea_verdict(common_dir: Path) -> None:
    processed = common_dir / "Charon" / "commands" / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    (processed / "ok_0001.json").write_text("{}", encoding="utf-8")
    (processed / "rejected_0002.json").write_text("{}", encoding="utf-8")

    verdicts = {entry["verdict"] for entry in commands.recent()}
    assert verdicts == {"accepted", "rejected"}
