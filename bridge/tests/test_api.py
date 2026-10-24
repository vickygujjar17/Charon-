"""HTTP surface tests, with the order boundary as the headline assertion."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from charon_bridge import mt5_adapter


@pytest.fixture()
def client(common_dir: Path) -> TestClient:
    return TestClient(main.app)


def test_health_needs_no_token(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["schema"] == 1
    assert "locations" in body


def test_status_reports_live_basket(client: TestClient, write_snapshot) -> None:
    write_snapshot()
    body = client.get("/api/status", params={"symbol": "GBPUSD"}).json()
    assert body["status"] == "live"
    assert body["snapshot"]["symbol"] == "GBPUSD"
    assert body["snapshot"]["basket"]["direction"] == "BUY"
    assert body["snapshot"]["basket"]["legs"] == 2
    assert len(body["snapshot"]["legs"]) == 2


def test_status_503_when_no_snapshot(client: TestClient) -> None:
    response = client.get("/api/status")
    assert response.status_code == 503
    assert "InpBridgeEnable" in response.json()["detail"]


def test_status_picks_the_only_symbol_without_a_query(client: TestClient, write_snapshot) -> None:
    write_snapshot()
    assert client.get("/api/status").json()["snapshot"]["symbol"] == "GBPUSD"


def test_trade_endpoint_refuses_loudly(client: TestClient) -> None:
    """405 with a reason, so a caller cannot mistake it for a wrong URL."""
    response = client.post("/api/trade")
    assert response.status_code == 405
    body = response.json()
    assert body["error"] == "method_not_allowed"
    assert "CharonTrade.mqh" in body["detail"]
    assert "open" in body["forbidden_verbs"]
    assert "arm" in body["allowed_verbs"]


def test_trade_refuses_even_with_a_body(client: TestClient) -> None:
    response = client.post("/api/trade", json={"side": "buy", "lot": 1.0})
    assert response.status_code == 405


def test_arm_queues_a_command(client: TestClient, write_snapshot, common_dir: Path) -> None:
    write_snapshot()
    body = client.post("/api/pyramid/arm", params={"symbol": "GBPUSD"}).json()
    assert body["queued"] is True
    inbox = common_dir / "Charon" / "commands"
    assert len(list(inbox.glob("*.json"))) == 1


def test_disarm_queues_a_command(client: TestClient, write_snapshot) -> None:
    write_snapshot()
    body = client.post("/api/pyramid/disarm", params={"symbol": "GBPUSD"}).json()
    assert body["command"]["verb"] == "disarm"


def test_trail_endpoint_accepts_every_valid_mode(client: TestClient, write_snapshot) -> None:
    write_snapshot()
    for mode in range(5):
        response = client.post("/api/trail", params={"mode": mode, "symbol": "GBPUSD"})
        assert response.status_code == 200
        assert response.json()["command"]["sent"]["mode"] == mode


@pytest.mark.parametrize("mode", [-1, 5, 99])
def test_trail_endpoint_rejects_out_of_range_modes(client: TestClient, write_snapshot,
                                                   mode: int) -> None:
    write_snapshot()
    response = client.post("/api/trail", params={"mode": mode, "symbol": "GBPUSD"})
    assert response.status_code == 422


def test_positions_mirrors_the_basket(client: TestClient, write_snapshot) -> None:
    write_snapshot()
    body = client.get("/api/positions", params={"symbol": "GBPUSD"}).json()
    assert body["basket"]["avg_entry"] == pytest.approx(1.26500)
    assert [leg["ticket"] for leg in body["legs"]] == [111, 222]


def test_logs_plain_form_returns_a_buffer(client: TestClient) -> None:
    body = client.get("/api/logs").json()
    assert isinstance(body["entries"], list)
    assert "latest_seq" in body


def test_terminal_reports_unavailable_without_the_package(client: TestClient) -> None:
    body = client.get("/api/terminal").json()
    assert "available" in body
    assert isinstance(body["available"], bool)


def test_token_is_enforced_when_configured(client: TestClient, write_snapshot,
                                           monkeypatch) -> None:
    write_snapshot()
    monkeypatch.setenv("CHARON_TOKEN", "s3cret")

    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status",
                      headers={"X-Charon-Token": "wrong"}).status_code == 401
    ok = client.get("/api/status", headers={"X-Charon-Token": "s3cret"})
    assert ok.status_code == 200


def test_health_stays_open_when_a_token_is_set(client: TestClient, monkeypatch) -> None:
    """Liveness must be checkable by a supervisor that has no token."""
    monkeypatch.setenv("CHARON_TOKEN", "s3cret")
    assert client.get("/api/health").status_code == 200


def test_dashboard_page_is_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Charon" in response.text


def test_mt5_adapter_never_references_an_order_call() -> None:
    """A source-level guard: this module must stay read-only.

    Prose may *discuss* the prohibited functions (the module docstring
    says which ones it refuses to call), so this inspects the parsed code
    rather than the raw text: names are resolved from the AST, which by
    construction excludes comments and docstrings. A future edit that
    actually reaches for ``order_send`` fails here.
    """
    import ast

    source = Path(mt5_adapter.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                referenced.add(alias.name)

    for forbidden in mt5_adapter.FORBIDDEN_CALLS:
        assert forbidden not in referenced, (
            f"{forbidden} is referenced in code, not just documentation"
        )


def test_mt5_adapter_is_importable_without_the_package() -> None:
    """The bridge must work on a machine that has no MetaTrader5 package."""
    assert hasattr(mt5_adapter, "available")
    assert isinstance(mt5_adapter.available(), bool)
    view = mt5_adapter.describe()
    assert isinstance(view.available, bool)
    assert isinstance(view.as_dict(), dict)
