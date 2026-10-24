"""The RR ladder must be passed through verbatim, and a missing ladder
must stay missing.

Invariant: Python never recomputes a rung. A rung price on the dashboard
has to be the same number the EA would write on the chart, or the panel
is worse than useless -- it would look authoritative while disagreeing
with the thing that actually manages the trade.

The second half of this file is the important one: when the EA does not
publish a ladder, every layer must return ``None`` rather than fabricate
a plausible price out of ``entry`` and ``risk_price``.
"""

from __future__ import annotations

import pytest

from charon_bridge.schema import Snapshot
from tests.conftest import make_snapshot

VERBATIM = "1.50R 1.26950 | 2.00R 1.27200 | 3.00R 1.27700"


def test_basket_ladder_is_copied_not_recomputed() -> None:
    snap = Snapshot.from_json(make_snapshot())
    assert snap.basket.ladder == VERBATIM


def test_leg_ladders_are_independent() -> None:
    """Each leg carries its own geometry; tier 2 must not inherit tier 1."""
    snap = Snapshot.from_json(make_snapshot())
    primary, tier2 = snap.legs
    assert primary.ladder != tier2.ladder
    assert primary.ladder == "1.50R 1.26450 | 2.00R 1.26600 | 3.00R 1.26900"
    assert tier2.ladder == "1.50R 1.27450 | 2.00R 1.27600 | 3.00R 1.27900"


def test_ladder_text_is_untouched_including_whitespace() -> None:
    """Any reformatting would be a silent claim that we understand it."""
    odd = "1.5R 1.2695|2R 1.2720"
    payload = make_snapshot()
    payload["basket"]["ladder"] = odd
    assert Snapshot.from_json(payload).basket.ladder == odd


def test_missing_basket_ladder_is_null_not_a_guess() -> None:
    """Absent key -> None. Never a price invented from entry/R."""
    payload = make_snapshot()
    del payload["basket"]["ladder"]
    snap = Snapshot.from_json(payload)
    assert snap.basket.ladder is None


def test_null_basket_ladder_stays_null() -> None:
    payload = make_snapshot()
    payload["basket"]["ladder"] = None
    assert Snapshot.from_json(payload).basket.ladder is None


def test_empty_basket_ladder_is_preserved_as_empty() -> None:
    """The EA sends "" when no rung is usable -- that is a fact, not a gap."""
    payload = make_snapshot()
    payload["basket"]["ladder"] = ""
    assert Snapshot.from_json(payload).basket.ladder == ""


def test_missing_leg_ladder_is_null() -> None:
    payload = make_snapshot()
    del payload["legs"][0]["ladder"]
    del payload["legs"][1]["ladder"]
    snap = Snapshot.from_json(payload)
    assert [leg.ladder for leg in snap.legs] == [None, None]


def test_non_string_ladder_is_rejected_as_null() -> None:
    """A number where a string is expected is a contract break, not a rung."""
    payload = make_snapshot()
    payload["basket"]["ladder"] = 1.26950
    assert Snapshot.from_json(payload).basket.ladder is None


def test_ladder_survives_a_mixed_basket() -> None:
    """A mixed basket has no single ladder; the EA sends "" and we keep it."""
    payload = make_snapshot()
    payload["basket"]["mixed"] = True
    payload["basket"]["dir"] = 0
    payload["basket"]["ladder"] = ""
    snap = Snapshot.from_json(payload)
    assert snap.basket.direction == "MIXED"
    assert snap.basket.ladder == ""


def test_status_endpoint_returns_ladder_null_when_absent(write_snapshot) -> None:
    """The HTTP layer must not fill the gap either."""
    from fastapi.testclient import TestClient

    import main

    payload = make_snapshot()
    del payload["basket"]["ladder"]
    for leg in payload["legs"]:
        del leg["ladder"]
    write_snapshot(payload)

    client = TestClient(main.app)
    body = client.get("/api/status", params={"symbol": "GBPUSD"}).json()
    assert body["snapshot"]["basket"]["ladder"] is None
    assert all(leg["ladder"] is None for leg in body["snapshot"]["legs"])


def test_risk_figures_are_passed_through_unchanged() -> None:
    """Python must not recompute risk either -- it has no tick data."""
    snap = Snapshot.from_json(make_snapshot())
    assert snap.basket.risk_money == pytest.approx(45.00)
    assert snap.basket.risk_pct == pytest.approx(0.4447)
    assert snap.basket.avg_entry == pytest.approx(1.26500)
