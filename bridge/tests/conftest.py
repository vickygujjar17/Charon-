"""Shared fixtures: a synthetic EA snapshot in the EA's real wire format.

The dicts here are hand-written to match ``CCharonBridge::Publish()``
field for field. If the MQL5 side ever changes a key, these tests are
where the mismatch should surface.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest


def make_snapshot(**overrides: Any) -> dict[str, Any]:
    """A live, healthy GBPUSD snapshot with two legs."""
    snap: dict[str, Any] = {
        "schema": 2,
        "seq": 42,
        "ea_version": "1.0.3",
        "symbol": "GBPUSD",
        "server_time": 1_760_000_000,
        "ready": True,
        "dashboard": True,
        "persist": True,
        "digits": 5,
        "currency": "USD",
        "balance": 10_000.0,
        "equity": 10_120.5,
        "basket": {
            "valid": True,
            "mixed": False,
            "dir": 0,
            "legs": 2,
            "lot": 0.50,
            "avg_entry": 1.26500,
            "open_pl": 120.50,
            "risk_money": 45.00,
            "risk_pct": 0.4447,
            "best": 1.26900,
            "ladder": "1.50R 1.26950 | 2.00R 1.27200 | 3.00R 1.27700",
        },
        "model": {
            "sl": "300 points",
            "tp": "2.00R",
            "sizing": "0.50% of equity",
            "max_risk_pct": 1.0,
        },
        "trail": {"mode": 2, "mode_name": "ATR", "mutable": True},
        "pyramid": {
            "enabled": True,
            "max_legs": 3,
            "max_risk_pct": 1.5,
            "trigger": "MANUAL",
            "arm_key": 65,
            "armed": False,
            "secs_left": -1,
            "next_tier": 2,
            "progress_rr": 1.42,
        },
        "legs": [
            {
                "ticket": 111,
                "type": 0,
                "side": "BUY",
                "tier": 1,
                "entry": 1.26000,
                "lot": 0.30,
                "sl": 1.25700,
                "risk_price": 0.00300,
                "hwm": 1.26900,
                "progress_rr": 3.0,
                "trail_active": True,
                "be_done": True,
                "half_risk_done": True,
                "ladder": "1.50R 1.26450 | 2.00R 1.26600 | 3.00R 1.26900",
            },
            {
                "ticket": 222,
                "type": 0,
                "side": "BUY",
                "tier": 2,
                "entry": 1.27000,
                "lot": 0.20,
                "sl": 1.26700,
                "risk_price": 0.00300,
                "hwm": 1.27100,
                "progress_rr": 0.33,
                "trail_active": False,
                "be_done": False,
                "half_risk_done": False,
                "ladder": "1.50R 1.27450 | 2.00R 1.27600 | 3.00R 1.27900",
            },
        ],
    }
    snap.update(overrides)
    return snap


@pytest.fixture()
def common_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the bridge at a throwaway Common\\Files tree.

    Every test that touches the filesystem uses this, so nothing ever
    reads or writes the real terminal folder.
    """
    files = tmp_path / "Common" / "Files"
    files.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHARON_COMMON_DIR", str(files))
    return files


@pytest.fixture()
def write_snapshot(common_dir: Path):
    """Write a snapshot where the EA would, with a chosen age."""

    def _write(payload: dict[str, Any] | None = None, *, age_seconds: float = 0.0,
               symbol: str | None = None) -> Path:
        data = payload if payload is not None else make_snapshot()
        sym = symbol or data["symbol"]
        folder = common_dir / "Charon"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"state_{sym}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        if age_seconds:
            stamp = time.time() - age_seconds
            os.utime(path, (stamp, stamp))
        return path

    return _write
