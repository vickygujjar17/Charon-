"""Staleness is a safety property, not a cosmetic one.

A dashboard showing a ten-minute-old basket, an armed ladder and a stop
that has since moved is worse than no dashboard: it invites a decision
based on numbers nothing is maintaining. These tests pin the thresholds
and the words used to describe them.
"""

from __future__ import annotations

import json

import pytest

from charon_bridge import paths, state
from tests.conftest import make_snapshot


def test_missing_snapshot_raises_with_an_actionable_message(common_dir) -> None:
    with pytest.raises(state.StateUnavailable) as caught:
        state.read_snapshot("GBPUSD")
    assert "InpBridgeEnable" in str(caught.value)


def test_fresh_snapshot_is_live(write_snapshot) -> None:
    write_snapshot()
    reading = state.read_snapshot("GBPUSD")
    assert reading.status == "live"
    assert reading.stale is False
    assert reading.offline is False
    assert reading.age_seconds < state.STALE_AFTER_SECONDS


def test_snapshot_just_past_the_stale_threshold_is_stale(write_snapshot) -> None:
    write_snapshot(age_seconds=state.STALE_AFTER_SECONDS + 1)
    reading = state.read_snapshot("GBPUSD")
    assert reading.stale is True
    assert reading.offline is False
    assert reading.status == "stale"


def test_snapshot_past_offline_is_offline(write_snapshot) -> None:
    write_snapshot(age_seconds=state.OFFLINE_AFTER_SECONDS + 1)
    reading = state.read_snapshot("GBPUSD")
    assert reading.offline is True
    assert reading.status == "offline"


def test_ea_not_ready_reports_starting(write_snapshot) -> None:
    write_snapshot(make_snapshot(ready=False))
    assert state.read_snapshot("GBPUSD").status == "starting"


def test_offline_wins_over_not_ready(write_snapshot) -> None:
    """A dead EA that was mid-start is offline, not 'starting' forever."""
    write_snapshot(make_snapshot(ready=False), age_seconds=state.OFFLINE_AFTER_SECONDS + 1)
    assert state.read_snapshot("GBPUSD").status == "offline"


def test_wrong_schema_is_refused(write_snapshot) -> None:
    write_snapshot(make_snapshot(schema=99))
    with pytest.raises(state.StateUnavailable):
        state.read_snapshot("GBPUSD")


def test_corrupt_json_is_refused(write_snapshot, common_dir) -> None:
    folder = common_dir / "Charon"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state_GBPUSD.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(state.StateUnavailable):
        state.read_snapshot("GBPUSD")


def test_symbol_sanitising_blocks_traversal() -> None:
    assert paths.sanitize_symbol("GBPUSD") == "GBPUSD"
    assert paths.sanitize_symbol("EURUSD.a") == "EURUSD.a"
    assert paths.sanitize_symbol("../../etc") == "etc"
    assert paths.sanitize_symbol("XAU/USD") == "XAU_USD"
    assert paths.sanitize_symbol("") == "UNKNOWN"
    assert paths.sanitize_symbol("..") == "UNKNOWN"


def test_state_path_stays_inside_the_bridge_root(common_dir) -> None:
    path = paths.state_path("../../evil")
    assert path.parent == paths.bridge_root()


def test_discover_symbols_finds_every_snapshot(common_dir) -> None:
    folder = common_dir / "Charon"
    folder.mkdir(parents=True, exist_ok=True)
    for symbol in ("GBPUSD", "EURUSD"):
        (folder / f"state_{symbol}.json").write_text(json.dumps(make_snapshot()), encoding="utf-8")
    assert state.discover_symbols() == ["EURUSD", "GBPUSD"]


def test_env_override_wins(monkeypatch, tmp_path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    monkeypatch.setenv(paths.ENV_OVERRIDE, str(target))
    assert paths.common_files_dir() == target
