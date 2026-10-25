"""HTTP surface tests, with the order boundary as the headline assertion."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from charon_bridge import mt5_adapter
from charon_bridge.version import SCHEMA_VERSION


@pytest.fixture()
def client(common_dir: Path) -> TestClient:
    return TestClient(main.app)


def test_health_needs_no_token(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["schema"] == SCHEMA_VERSION
    assert "locations" in body


def test_bind_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented default must be the safe one: no flags, no LAN exposure."""
    monkeypatch.delenv("CHARON_HOST", raising=False)
    monkeypatch.delenv("CHARON_PORT", raising=False)
    assert main._bind() == ("127.0.0.1", 8787)


def test_bind_honours_the_documented_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """CHARON_HOST/CHARON_PORT are documented in .env.example, so they must bind."""
    monkeypatch.setenv("CHARON_HOST", "127.0.0.1")
    monkeypatch.setenv("CHARON_PORT", "9001")
    assert main._bind() == ("127.0.0.1", 9001)


def test_bind_rejects_a_nonsense_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARON_PORT", "not-a-port")
    with pytest.raises(SystemExit):
        main._bind()

    monkeypatch.setenv("CHARON_PORT", "70000")
    with pytest.raises(SystemExit):
        main._bind()


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


def test_status_503_names_the_directory_it_searched(client: TestClient, common_dir: Path) -> None:
    """A bare "no snapshot found" hides the only fact worth having.

    The operator needs the absolute path this process resolved, because the
    usual fault is that it is not the path the terminal is writing to.
    """
    detail = client.get("/api/status").json()["detail"]
    assert str(common_dir / "Charon") in detail
    assert "InpBridgeEnable" in detail


def test_health_locations_survive_a_missing_root(client: TestClient, common_dir: Path) -> None:
    """``/api/health`` must answer before the EA has ever published."""
    locations = client.get("/api/health").json()["locations"]
    assert locations["bridge_root"] == str(common_dir / "Charon")
    assert locations["common_exists"] == "yes"


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


#+------------------------------------------------------------------+
#| POST /api/order                                                  |
#+------------------------------------------------------------------+
def _ticket(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "symbol": "GBPUSD",
        "side": "BUY",
        "order_type": "LIMIT",
        "entry": 1.26000,
        "block_top": 1.27000,
        "block_bottom": 1.25000,
        "risk_pct": 0.5,
        "trail_trigger": 4,
        "rung": 2,
    }
    body.update(overrides)
    return body


def test_order_endpoint_queues_a_ticket(client: TestClient, common_dir: Path) -> None:
    response = client.post("/api/order", json=_ticket())
    assert response.status_code == 200
    body = response.json()
    assert body["queued"] is True
    assert body["verb"] == "order_open"
    # --- the response promises a verdict, it does not claim a trade
    assert "EA" in body["note"]

    inbox = common_dir / "Charon" / "commands"
    files = sorted(p.name for p in inbox.glob("*.json"))
    assert len(files) == 1
    sent = json.loads((inbox / files[0]).read_text(encoding="utf-8"))
    assert sent["verb"] == "order_open"
    assert sent["side"] == "BUY"
    assert sent["risk_pct"] == 0.5
    assert "lots" not in sent and "sl" not in sent


def test_order_endpoint_defaults_to_market_without_an_entry(
        client: TestClient, common_dir: Path) -> None:
    response = client.post("/api/order", json=_ticket(order_type="MARKET", entry=None))
    assert response.status_code == 200
    inbox = common_dir / "Charon" / "commands"
    sent = json.loads(next(inbox.glob("*.json")).read_text(encoding="utf-8"))
    assert sent["order_type"] == "MARKET"
    assert "entry" not in sent


@pytest.mark.parametrize("body,reason", [
    ({"side": "buy"}, "side"),
    ({"order_type": "STOP"}, "order_type"),
    ({"block_top": 1.25, "block_bottom": 1.27}, "BUY block"),
    ({"block_top": 1.26, "block_bottom": 1.26}, "height"),
    ({"entry": 1.24000}, "inside the block"),
    ({"risk_pct": 0}, "risk_pct"),
    ({"risk_pct": 101}, "risk_pct"),
    ({"rung": 9}, "rung"),
])
def test_order_endpoint_refuses_malformed_intent(
        client: TestClient, body: dict[str, object], reason: str) -> None:
    response = client.post("/api/order", json=_ticket(**body))
    assert response.status_code == 422
    detail = response.json()["detail"]
    # --- two layers refuse here: FastAPI's field constraints (a list of
    # --- errors) and the order-intent validator (a plain sentence). Both
    # --- are 422s, and both must name the offending field.
    text = detail if isinstance(detail, str) else json.dumps(detail)
    assert reason in text


def test_order_endpoint_writes_nothing_when_it_refuses(
        client: TestClient, common_dir: Path) -> None:
    client.post("/api/order", json=_ticket(risk_pct=-1))
    inbox = common_dir / "Charon" / "commands"
    assert list(inbox.glob("*.json")) == []


def test_order_modify_needs_a_position_ticket(client: TestClient) -> None:
    response = client.post("/api/order/modify", params={"position": 0}, json=_ticket())
    assert response.status_code == 422

    queued = client.post("/api/order/modify", params={"position": 42}, json=_ticket())
    assert queued.status_code == 200
    assert queued.json()["verb"] == "order_modify"
    assert queued.json()["command"]["sent"]["ticket"] == 42


def test_order_cancel_queues_a_ticket(client: TestClient, common_dir: Path) -> None:
    response = client.post("/api/order/cancel",
                           params={"position": 77, "symbol": "GBPUSD"})
    assert response.status_code == 200
    assert response.json()["verb"] == "order_cancel"
    inbox = common_dir / "Charon" / "commands"
    sent = json.loads(next(inbox.glob("*.json")).read_text(encoding="utf-8"))
    assert sent["ticket"] == 77


def test_trade_endpoint_points_at_the_order_endpoint(client: TestClient) -> None:
    """The tombstone stays, but it must not be the only door in town."""
    body = client.post("/api/trade").json()
    assert "order_open" in body["allowed_verbs"]
    assert "open" not in body["allowed_verbs"]


def test_startup_warns_on_the_console_when_nothing_is_publishing(
    common_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The startup diagnosis must reach stderr, not just the HTTP ring buffer.

    This is the gap that hid the split-deployment fault: ``charon_bridge.logs``
    is only readable over ``/api/logs``, and only while the process is up. An
    operator watching uvicorn saw a clean boot and no reason to suspect the
    path. ``caplog`` captures exactly what the console handler would receive.
    """
    from charon_bridge.logs import LOG

    before = LOG.latest_seq
    with caplog.at_level(logging.WARNING, logger="charon.bridge"):
        with TestClient(main.app):
            pass

    assert any(str(common_dir / "Charon") in rec.getMessage() for rec in caplog.records), (
        "the resolved bridge root must appear in the startup warning"
    )
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)
    assert any(str(common_dir / "Charon") in e.message for e in LOG.since(before))


def test_startup_stays_quiet_when_a_snapshot_is_present(
    write_snapshot, caplog: pytest.LogCaptureFixture
) -> None:
    """A healthy deployment must not be nagged at every boot."""
    write_snapshot()
    with caplog.at_level(logging.WARNING, logger="charon.bridge"):
        with TestClient(main.app):
            pass
    assert not [rec for rec in caplog.records if rec.levelno >= logging.WARNING]

