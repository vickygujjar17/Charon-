"""The command vocabulary is the security boundary, so it gets its own tests.

The property under test is negative and that is deliberate: we are
asserting that certain things are *impossible*, not that some feature
works. A future change that adds an ``open`` verb has to delete a test
that says it must not exist.
"""

from __future__ import annotations

import json
import os
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
from charon_bridge.version import SCHEMA_VERSION
from tests.fake_ea import local_epoch as fake_local_epoch


def test_allowed_verbs_are_exactly_seven() -> None:
    """The vocabulary grew in v1.1.0, and the growth is deliberate.

    The three order verbs submit *intent* to the EA's risk model; none of
    them accepts a caller-chosen volume, stop distance or R multiple. The
    raw verbs they might have been named after remain forbidden, so this
    list is the whole of what the bridge can express.
    """
    assert set(ALLOWED_VERBS) == {
        "arm", "disarm", "trail_mode", "ping",
        "order_open", "order_modify", "order_cancel",
    }


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
        "schema": SCHEMA_VERSION,
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
    assert loaded["schema"] == SCHEMA_VERSION
    assert isinstance(loaded["issued"], int)


def test_submit_leaves_no_temp_files(common_dir: Path) -> None:
    commands.submit("arm", "GBPUSD")
    inbox = common_dir / "Charon" / "commands"
    assert not list(inbox.glob("*.tmp"))


def test_submit_refuses_a_forbidden_verb(common_dir: Path) -> None:
    with pytest.raises(SchemaError):
        commands.submit("close", "GBPUSD")


def test_submitted_issued_is_accepted_by_the_eas_clock(common_dir: Path) -> None:
    """The regression that shipped broken: ``issued`` was a UTC epoch second.

    ``CharonBridge.mqh`` compares ``issued`` against ``(long)TimeLocal()``.
    When the bridge stamped ``time.time_ns() // 1e9`` the two clocks differed
    by the host's UTC offset, so on a UTC+5:30 terminal every command was
    refused as ``future-dated (19800s ahead)`` and the dashboard was inert.

    This test re-derives the EA's verdict from the file itself using the same
    comparison ``fake_ea.py`` mirrors, so it fails on any host whose offset
    exceeds the window -- which is most of them, and is exactly the point.
    """
    sent = commands.submit("arm", "GBPUSD")
    inbox = common_dir / "Charon" / "commands"
    issued = json.loads((inbox / sent["file"]).read_text(encoding="utf-8"))["issued"]

    # --- mirrors ``long age = (long)TimeLocal() - cmd.issued;`` in the EA
    age = fake_local_epoch() - int(issued)
    assert abs(age) <= 60, (
        f"the EA would refuse this command: age={age}s against its +/-60s window"
    )


def test_issued_is_the_local_epoch_not_the_utc_one() -> None:
    """Pin the distinction directly, so the fix cannot be 'simplified' away."""
    now_ns = commands.time.time_ns()
    local = commands.local_epoch(now_ns)
    utc = now_ns // 1_000_000_000

    # --- ``local`` must equal ``time.localtime`` re-read as a UTC struct
    fields = commands.time.localtime(now_ns / 1_000_000_000)
    assert local == commands.calendar.timegm(fields)

    # --- and it must differ from the naive UTC stamp by exactly the host's
    # --- offset. Checked against the interpreter's own tz globals, which are
    # --- maintained by the C library rather than by the round-trip above, so
    # --- this is not just re-asserting the implementation.
    offset = local - utc
    if offset == 0:
        pytest.skip("host clock is UTC; the UTC/local distinction is unobservable")
    expected = -(commands.time.altzone if fields.tm_isdst else commands.time.timezone)
    assert offset == expected, (
        f"issued is {offset}s from UTC but the host offset is {expected}s"
    )


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


def _retire(folder: Path, name: str, *, at: float) -> Path:
    """Drop a retired command in ``processed/`` with a forced timestamp."""
    path = folder / name
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (at, at))
    return path


def test_recent_orders_by_verdict_time_not_by_verdict_prefix(common_dir: Path) -> None:
    """Regression: ``ok_`` used to outrank ``rejected_`` regardless of age.

    The EA retires files as ``ok_<fifo>.json`` / ``rejected_<fifo>.json``,
    so sorting on the whole filename sorts on the *verdict* first. A
    rejection that happened before an acceptance would then be reported as
    the more recent event, which misrepresents the audit trail.
    """
    processed = common_dir / "Charon" / "commands" / "processed"
    processed.mkdir(parents=True, exist_ok=True)

    # --- the ".json" suffix is mandatory: the EA only ever retires .json
    older_rejection = _retire(
        processed, "rejected_00000001760000000000-000001-aaaaaaaa.json", at=1_760_000_000
    )
    newer_ok = _retire(
        processed, "ok_00000001760000090000-000002-bbbbbbbb.json", at=1_760_000_900
    )
    assert older_rejection.stat().st_mtime < newer_ok.stat().st_mtime

    trail = commands.recent()
    assert [entry["file"] for entry in trail] == [newer_ok.name, older_rejection.name]
    assert [entry["verdict"] for entry in trail] == ["accepted", "rejected"]


def test_recent_strips_the_verdict_prefix_in_its_tiebreak(common_dir: Path) -> None:
    """Same verdict time -> order by the FIFO name, not the prefixed name."""
    processed = common_dir / "Charon" / "commands" / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    stamp = 1_760_000_000

    early = _retire(processed, "rejected_00000001760000000001-000001-a.json", at=stamp)
    late = _retire(processed, "ok_00000001760000000002-000002-b.json", at=stamp)

    trail = commands.recent()
    assert [entry["file"] for entry in trail] == [late.name, early.name]


def test_recent_respects_the_limit_after_ordering(common_dir: Path) -> None:
    processed = common_dir / "Charon" / "commands" / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    newest = _retire(processed, "rejected_00000001760000000300-000003-c.json", at=1_760_000_300)
    for index, stamp in enumerate((1_760_000_100, 1_760_000_200)):
        _retire(processed, f"ok_00000001760000000{index}-00000{index}-d.json", at=stamp)

    trail = commands.recent(limit=1)
    assert [entry["file"] for entry in trail] == [newest.name]


def test_recent_tolerates_a_file_retired_mid_listing(
    common_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The EA retires concurrently; a vanished file must not 500 the endpoint."""
    processed = common_dir / "Charon" / "commands" / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    keeper = _retire(processed, "ok_00000001760000000000-000001-e.json", at=1_760_000_000)
    doomed = _retire(processed, "rejected_00000001760000000001-000002-f.json", at=1_760_000_001)

    real_stat = Path.stat

    def flaky_stat(self: Path, *args, **kwargs):
        if self.name == doomed.name:
            raise FileNotFoundError(self.name)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    trail = commands.recent()
    assert [entry["file"] for entry in trail] == [keeper.name]


def test_recent_returns_empty_for_a_nonpositive_limit(common_dir: Path) -> None:
    processed = common_dir / "Charon" / "commands" / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    _retire(processed, "ok_00000001760000000000-000001-g.json", at=1_760_000_000)
    assert commands.recent(limit=0) == []


#+------------------------------------------------------------------+
#| Per-symbol routing, decided by the file system                   |
#|                                                                  |
#| The bridge cannot know which EA build is running, so it asks the  |
#| file system: a v1.3.0 EA creates commands\<SYMBOL> in Init().     |
#| These tests pin both directions, because getting it wrong strands  |
#| every command in a folder nothing drains -- silently.             |
#+------------------------------------------------------------------+
def test_submit_targets_the_legacy_inbox_until_the_ea_makes_a_subfolder(
    common_dir: Path,
) -> None:
    """A pre-v1.3.0 EA must keep working after only Python is upgraded."""
    sent = commands.submit("arm", "GBPUSD")

    legacy = common_dir / "Charon" / "commands"
    assert (legacy / sent["file"]).is_file(), "a flat EA would never see this"
    # --- and the bridge must not have created the subfolder itself: doing so
    # --- would make its own probe succeed on the next call and divert every
    # --- later command into a folder this EA never reads.
    assert not (legacy / "GBPUSD").exists()


def test_submit_targets_the_symbol_inbox_once_the_ea_creates_it(
    common_dir: Path,
) -> None:
    scoped = common_dir / "Charon" / "commands" / "GBPUSD"
    (scoped / "processed").mkdir(parents=True, exist_ok=True)

    sent = commands.submit("arm", "GBPUSD")

    assert (scoped / sent["file"]).is_file()
    assert not (common_dir / "Charon" / "commands" / sent["file"]).exists()


def test_a_subfolder_for_one_symbol_does_not_divert_another(
    common_dir: Path,
) -> None:
    """Routing is per symbol, so a half-upgraded terminal degrades per chart."""
    (common_dir / "Charon" / "commands" / "GBPUSD" / "processed").mkdir(parents=True)

    scoped = commands.submit("arm", "GBPUSD")
    flat = commands.submit("arm", "EURUSD")

    root = common_dir / "Charon" / "commands"
    assert (root / "GBPUSD" / scoped["file"]).is_file()
    assert (root / flat["file"]).is_file(), "EURUSD has no chart-scoped inbox yet"


def test_pending_sees_both_layouts_at_once(common_dir: Path) -> None:
    """The queue is a union, so an upgrade never hides already-queued work."""
    (common_dir / "Charon" / "commands" / "GBPUSD" / "processed").mkdir(parents=True)

    flat = commands.submit("arm", "EURUSD")
    scoped = commands.submit("arm", "GBPUSD")

    waiting = commands.pending()
    assert flat["file"] in waiting
    assert scoped["file"] in waiting
    # --- narrowing to one symbol must exclude the other chart's command
    assert commands.pending("GBPUSD") == [scoped["file"]]


def test_recent_reads_every_archive(common_dir: Path) -> None:
    """Old verdicts in the flat archive survive the move to subfolders."""
    root = common_dir / "Charon" / "commands"
    (root / "processed").mkdir(parents=True, exist_ok=True)
    (root / "GBPUSD" / "processed").mkdir(parents=True, exist_ok=True)
    _retire(root / "processed",
            "ok_00000001760000000000-000001-flat.json", at=1_760_000_000)
    _retire(root / "GBPUSD" / "processed",
            "rejected_00000001760000000001-000002-scoped.json", at=1_760_000_001)

    verdicts = {entry["verdict"] for entry in commands.recent()}
    assert verdicts == {"accepted", "rejected"}

    # --- scoping the trail to one symbol must not include the legacy archive,
    # --- because those files carry no reliable symbol attribution
    scoped = commands.recent(symbol="GBPUSD")
    assert [entry["verdict"] for entry in scoped] == ["rejected"]
