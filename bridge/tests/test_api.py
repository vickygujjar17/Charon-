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
from tests.conftest import make_snapshot


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


def test_mt5_adapter_reads_are_an_explicit_allow_list() -> None:
    """The read-only guarantee is a closed list, not an absence of bad names.

    ``FORBIDDEN_CALLS`` catches the obvious mistake, but it would not catch
    a novel modifying call added under a name nobody thought to forbid --
    ``history_add`` or ``positions_modify_by`` would sail through. Pinning
    the exact set of ``_mt5.*`` attributes this module may touch turns any
    new terminal interaction into a deliberate, reviewable line in a diff.

    v1.3.0 adds ``symbols_get`` and ``positions_get``, both reads. Neither
    can change an account, which is the only thing this module is allowed
    to be part of.

    v1.6.0 adds ``history_deals_get`` and ``copy_rates_from_pos``, also
    reads. History and bars are what let the dashboard report realized
    results instead of only floating ones; neither call can place, close or
    modify anything. The allow-list is extended here rather than relaxed,
    so the next addition still has to be argued for in a diff.
    """
    import ast

    allowed = {
        "initialize", "shutdown", "last_error",
        "terminal_info", "account_info", "symbol_info",
        "symbols_get", "positions_get",
        "history_deals_get", "copy_rates_from_pos",
    }

    source = Path(mt5_adapter.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    reached = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "_mt5"
    }

    unexpected = reached - allowed
    assert not unexpected, (
        f"mt5_adapter reaches for {sorted(unexpected)}, which is not on the "
        "read-only allow-list. Adding a call here is a security decision."
    )
    # --- and the reads the module claims to make must actually be there,
    # --- so this test cannot pass by the code being deleted
    assert {
        "symbols_get",
        "positions_get",
        "history_deals_get",
        "copy_rates_from_pos",
    } <= reached


def test_mt5_adapter_degrades_to_empty_lists_without_the_package() -> None:
    """No package means no data, never an exception and never an invention."""
    assert mt5_adapter.symbols() == []
    assert mt5_adapter.positions() == []
    assert mt5_adapter.deals() == []
    assert mt5_adapter.rates("EURUSD") == []


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


def test_order_endpoint_queues_a_ticket(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    write_snapshot()          # --- the gate demands a live chart for this symbol
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
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    write_snapshot()
    response = client.post("/api/order", json=_ticket(order_type="MARKET", entry=None))

    assert response.status_code == 200
    inbox = common_dir / "Charon" / "commands"
    sent = json.loads(next(inbox.glob("*.json")).read_text(encoding="utf-8"))
    assert sent["order_type"] == "MARKET"
    assert "entry" not in sent


def test_market_with_an_entry_is_queued_as_a_watch_not_an_order(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    """The v1.5.0 routing rule, at the API boundary.

    Before this, MARKET+entry was written as ``order_open`` and the EA filled
    it at market -- the price was carried, read, and ignored. Now the verb
    itself changes, so an un-upgraded EA refuses the file instead of trading
    on a ticket it only half understands.
    """
    write_snapshot(make_snapshot(schema=SCHEMA_VERSION))
    response = client.post("/api/order", json=_ticket(
        order_type="MARKET", entry=1.25600))

    assert response.status_code == 200
    body = response.json()
    assert body["verb"] == "watch_add"
    assert body["watching"] is True
    assert body["entry"] == 1.25600

    inbox = common_dir / "Charon" / "commands"
    sent = json.loads(next(inbox.glob("*.json")).read_text(encoding="utf-8"))
    assert sent["verb"] == "watch_add"
    assert sent["entry"] == 1.25600
    assert sent["order_type"] == "MARKET"
    # --- the whole ticket still rides along: a watch defers *when*, not *what*
    assert sent["risk_pct"] == 0.5
    assert sent["block_top"] == 1.27000
    assert sent["block_bottom"] == 1.25000


def test_a_watch_needs_a_schema_4_chart(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    """A schema-3 EA does not know ``watch_add`` at all.

    Refusing synchronously names the problem; writing the file would leave the
    operator reading an Experts-log rejection after the dashboard said
    "queued".
    """
    write_snapshot(make_snapshot(schema=3))
    response = client.post("/api/order", json=_ticket(
        order_type="MARKET", entry=1.25600))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "schema" in detail
    # --- nothing was written, so no chart can act on it
    inbox = common_dir / "Charon" / "commands"
    assert list(inbox.glob("*.json")) == []


def test_an_absolute_trail_price_needs_a_schema_4_chart(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    """Gap C has the same half-upgrade trap as Gap B: a schema-3 EA would
    ignore the key and trail from its own default, so the operator would be
    told a level was armed when it was not."""
    write_snapshot(make_snapshot(schema=3))
    response = client.post("/api/order", json=_ticket(
        order_type="MARKET", entry=None, trail_start_price=1.27500))

    assert response.status_code == 422
    assert "absolute-price" in response.json()["detail"]


def test_a_schema_3_ea_still_accepts_a_v14_ticket(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    """The per-field gate must not over-refuse.

    A schema-3 chart understands every schema-3 field, so a ticket carrying
    one is still accepted after the bridge learned schema 4. Refusing on the
    bridge's own revision alone would have broken every un-upgraded chart on
    the day of the upgrade.
    """
    write_snapshot(make_snapshot(schema=3))
    response = client.post("/api/order", json=_ticket(
        order_type="MARKET", entry=None, journal="a note a schema-3 EA can hold"))

    assert response.status_code == 200
    assert response.json()["verb"] == "order_open"


def test_watch_cancel_queues_a_watch_cancel(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    write_snapshot(make_snapshot(schema=SCHEMA_VERSION))
    response = client.post("/api/orders/watch/cancel", params={"watch_id": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["verb"] == "watch_cancel"

    inbox = common_dir / "Charon" / "commands"
    sent = json.loads(next(inbox.glob("*.json")).read_text(encoding="utf-8"))
    assert sent["verb"] == "watch_cancel"
    assert sent["watch_id"] == 7
    # --- it names a watch, never a ticket: nothing reached the broker
    assert "ticket" not in sent


def test_watch_cancel_refuses_a_non_positive_id(client: TestClient) -> None:
    response = client.post("/api/orders/watch/cancel", params={"watch_id": 0})
    assert response.status_code == 422


def test_status_publishes_the_watch_queue_and_the_tick(
        client: TestClient, write_snapshot) -> None:
    write_snapshot(make_snapshot(
        schema=SCHEMA_VERSION,
        bid=1.25980,
        ask=1.26020,
        watch=[{"id": 3, "side": "BUY", "price": 1.25600,
                "order_type": "MARKET", "created_at": 1, "status": "armed"}],
    ))
    snap = client.get("/api/status", params={"symbol": "GBPUSD"}).json()["snapshot"]

    assert len(snap["watch"]) == 1
    assert snap["watch"][0]["watch_id"] == 3
    assert snap["watch"][0]["price"] == 1.25600
    assert snap["watch"][0]["status"] == "armed"
    assert snap["bid"] == 1.25980
    assert snap["ask"] == 1.26020


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


def test_order_modify_needs_a_position_ticket(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    write_snapshot()
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


#+------------------------------------------------------------------+
#| The liveness gate on trade-affecting verbs                       |
#+------------------------------------------------------------------+
def test_order_is_refused_for_a_symbol_with_no_live_chart(
        client: TestClient, common_dir: Path) -> None:
    """A ticket for a symbol nothing publishes can only be queued into silence."""
    response = client.post("/api/order", json=_ticket(symbol="EURUSD"))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "EURUSD" in detail
    assert "snapshot" in detail
    # --- and nothing was written: a refusal must not leave a file behind
    inbox = common_dir / "Charon" / "commands"
    assert list(inbox.glob("*.json")) == [] if inbox.is_dir() else True


def test_order_modify_is_refused_for_a_symbol_with_no_live_chart(
        client: TestClient, common_dir: Path) -> None:
    response = client.post(
        "/api/order/modify", params={"position": 42}, json=_ticket(symbol="EURUSD")
    )
    assert response.status_code == 422
    assert "EURUSD" in response.json()["detail"]


def test_a_stale_snapshot_does_not_satisfy_the_gate(
        client: TestClient, write_snapshot) -> None:
    """Queued work for a chart that stopped publishing is lost work."""
    write_snapshot(age_seconds=60.0)
    response = client.post("/api/order", json=_ticket())
    assert response.status_code == 422
    assert "no live Charon snapshot" in response.json()["detail"]


#+------------------------------------------------------------------+
#| v1.4.0: the revision pre-flight                                  |
#+------------------------------------------------------------------+
def _semantic_ticket(**overrides: object) -> dict[str, object]:
    """A ticket carrying something only a v1.4.0 EA can honour."""
    body = _ticket()
    # --- risk is exclusive, so a money ticket has to drop the percentage.
    # --- Sending both is itself a refusal, which would hide what is being
    # --- tested here.
    if "risk_money" in overrides:
        body.pop("risk_pct", None)
    body.update(overrides)
    return body


def test_a_v14_ticket_is_refused_against_a_schema_2_ea(
        client: TestClient, write_snapshot, common_dir: Path) -> None:
    """The EA would ignore the new fields and trade anyway -- refuse first."""
    write_snapshot(make_snapshot(schema=2))
    response = client.post("/api/order", json=_semantic_ticket(risk_money=250.0))
    assert response.status_code == 422
    detail = response.json()["detail"]
    # --- the message names the field and the fix, not just "wrong schema"
    assert "money-denominated risk" in detail
    assert "schema 2" in detail
    # --- and nothing was queued: the refusal is not advisory
    inbox = common_dir / "Charon" / "commands"
    assert list(inbox.glob("*.json")) == [] if inbox.is_dir() else True


def test_a_plain_ticket_still_works_against_a_schema_2_ea(
        client: TestClient, write_snapshot) -> None:
    """An un-upgraded chart must stay usable for the ticket it does understand."""
    write_snapshot(make_snapshot(schema=2))
    response = client.post("/api/order", json=_ticket())
    assert response.status_code == 200
    assert response.json()["queued"] is True


def test_a_v14_ticket_is_accepted_against_a_schema_3_ea(
        client: TestClient, write_snapshot, common_dir: Path) -> None:
    write_snapshot(make_snapshot(schema=SCHEMA_VERSION))
    response = client.post("/api/order", json=_semantic_ticket(risk_money=250.0))
    assert response.status_code == 200
    inbox = common_dir / "Charon" / "commands"
    sent = json.loads(next(inbox.glob("*.json")).read_text(encoding="utf-8"))
    assert sent["risk_money"] == 250.0
    assert "risk_pct" not in sent


def test_status_reports_both_revisions(client: TestClient, write_snapshot) -> None:
    """The UI needs both numbers to explain a refusal."""
    write_snapshot(make_snapshot(schema=2))
    body = client.get("/api/status", params={"symbol": "GBPUSD"}).json()
    assert body["bridge_schema"] == SCHEMA_VERSION
    assert body["snapshot"]["schema"] == 2


#+------------------------------------------------------------------+
#| v1.4.0: journal and pending orders over HTTP                      |
#+------------------------------------------------------------------+
def test_a_journal_note_is_kept_and_rekeyed_onto_the_ticket(
        client: TestClient, write_snapshot) -> None:
    """The note is written at queue time, then claimed when a ticket exists."""
    # --- a journal is a v1.4.0 field, so this needs a v1.4.0 EA publishing.
    write_snapshot(make_snapshot(schema=SCHEMA_VERSION))
    posted = client.post("/api/order", json=_semantic_ticket(
        journal="London break - retest of VWAP"))
    assert posted.status_code == 200

    # --- not yet attributable: no verdict has named a position ticket
    body = client.get("/api/status", params={"symbol": "GBPUSD"}).json()
    assert body["journal"] == []

    # --- the EA accepts the ticket and reports #555
    write_snapshot(make_snapshot(schema=SCHEMA_VERSION, order={
        "status": "accepted", "reason": "filled", "side": "BUY", "lots": 0.2,
        "entry": 1.26, "stop": 1.25, "risk_money": 20.0, "risk_pct": 0.2,
        "rung": 1, "ticket": 555, "journal_tag": "LONDON-BREAK-RETEST-OF-VWAP",
    }))
    body = client.get("/api/status", params={"symbol": "GBPUSD"}).json()
    assert [row["ticket"] for row in body["journal"]] == [555]
    assert body["journal"][0]["note"] == "London break - retest of VWAP"
    # --- the EA echoes the short tag, never the note
    assert body["snapshot"]["order"]["journal_tag"] == "LONDON-BREAK-RETEST-OF-VWAP"


def test_pending_orders_are_published_with_the_positions(
        client: TestClient, write_snapshot) -> None:
    write_snapshot(make_snapshot(pending=[
        {"ticket": 900, "side": "BUY", "order_type": "LIMIT", "price": 1.25,
         "sl": 1.24, "lot": 0.20, "placed_at": 1_760_000_000},
    ]))
    body = client.get("/api/positions", params={"symbol": "GBPUSD"}).json()
    assert [row["ticket"] for row in body["pending"]] == [900]
    assert body["pending"][0]["order_type"] == "LIMIT"


def test_symbols_offer_an_asset_class_for_each_instrument(
        client: TestClient, write_snapshot) -> None:
    write_snapshot()
    body = client.get("/api/symbols").json()
    assert body["symbols"], "no instruments offered at all"
    for entry in body["symbols"]:
        assert entry["asset_class"]
    # --- the grouping order is the bridge's, so the client cannot drift
    assert body["asset_classes"][0] == "forex"


def test_shape_refusals_come_before_the_liveness_check(client: TestClient) -> None:
    """A malformed ticket is wrong whatever is running, and must say so.

    Both refuse, but the order matters for the operator: "risk_pct must be
    positive" is actionable, while "no chart" would send them off checking
    the terminal for a problem that is actually in the request.
    """
    body = client.post("/api/order", json=_ticket(risk_pct=0, symbol="EURUSD")).json()
    assert "risk_pct" in body["detail"]
    assert "snapshot" not in body["detail"]


#+------------------------------------------------------------------+
#| GET /api/symbols                                                 |
#+------------------------------------------------------------------+
def test_symbols_lists_live_charts_with_both_name_and_label(
        client: TestClient, write_snapshot) -> None:
    write_snapshot()
    body = client.get("/api/symbols").json()

    assert body["charts"] == ["GBPUSD"]
    entry = body["symbols"][0]
    assert entry["symbol"] == "GBPUSD", "the command argument must be exact"
    assert entry["label"] == "GBPUSD"
    assert entry["sources"] == ["chart"]
    # --- the package is absent in CI; the endpoint must still answer
    assert body["broker_available"] is False
    assert body["broker_count"] == 0


def test_symbols_merges_the_broker_list(client: TestClient, write_snapshot,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """A listed symbol with no chart is offered, but only as broker-sourced."""
    write_snapshot()
    monkeypatch.setattr(main.mt5_adapter, "symbols", lambda: ["EURUSD", "GBPUSD"])

    entries = {item["symbol"]: item for item in client.get("/api/symbols").json()["symbols"]}
    assert entries["GBPUSD"]["sources"] == ["chart", "broker"]
    assert entries["EURUSD"]["sources"] == ["broker"]


def test_symbols_is_empty_and_honest_with_no_charts(client: TestClient) -> None:
    body = client.get("/api/symbols").json()
    assert body["symbols"] == []
    assert body["charts"] == []


#+------------------------------------------------------------------+
#| GET /api/unmanaged                                               |
#+------------------------------------------------------------------+
@pytest.fixture()
def _fresh_cache(monkeypatch: pytest.MonkeyPatch):
    """Each test starts with an empty reconcile cache and a zeroed tracker."""
    monkeypatch.setattr(main, "_TRACKER", main.reconcile.Tracker())
    monkeypatch.setattr(main, "_reconcile_cache", {"at": 0.0, "payload": None})
    return None


def test_unmanaged_reports_unavailable_without_the_mt5_package(
        client: TestClient, common_dir: Path, write_snapshot, _fresh_cache) -> None:
    """No package means no account listing, which is not "all clear"."""
    write_snapshot()
    body = client.get("/api/unmanaged").json()

    assert body["summary"]["state"] == main.reconcile.STATE_UNAVAILABLE
    assert body["summary"]["clean"] is False
    assert "MetaTrader5" in body["reason"]


def test_unmanaged_reports_unknown_when_a_snapshot_is_stale(
        client: TestClient, write_snapshot, monkeypatch: pytest.MonkeyPatch,
        _fresh_cache) -> None:
    write_snapshot(age_seconds=60.0)
    monkeypatch.setattr(main.mt5_adapter, "available", lambda: True)
    monkeypatch.setattr(main.mt5_adapter, "positions", lambda: [])

    body = client.get("/api/unmanaged").json()
    assert body["summary"]["state"] == main.reconcile.STATE_UNKNOWN
    assert body["summary"]["clean"] is False
    assert body["symbols"][0]["symbol"] == "GBPUSD"


def test_unmanaged_reports_a_confirmed_orphan(
        client: TestClient, write_snapshot, monkeypatch: pytest.MonkeyPatch,
        _fresh_cache) -> None:
    """A position on a symbol with no chart is reported, not hidden."""
    write_snapshot()
    monkeypatch.setattr(main.mt5_adapter, "available", lambda: True)
    monkeypatch.setattr(main.mt5_adapter, "positions", lambda: [
        main.mt5_adapter.Position(
            ticket=900, symbol="USDJPY", side="SELL", volume=0.2,
            price_open=150.0, profit=-3.5, magic=0, comment="manual",
        )
    ])

    body = client.get("/api/unmanaged").json()
    assert body["summary"]["state"] == main.reconcile.STATE_OK
    assert [item["ticket"] for item in body["positions"]] == [900]
    assert body["positions"][0]["verdict"] == main.reconcile.NO_CHARON
    assert body["summary"]["clean"] is False


def test_unmanaged_is_cached_for_the_ttl(
        client: TestClient, write_snapshot, monkeypatch: pytest.MonkeyPatch,
        _fresh_cache) -> None:
    """The terminal round trip is expensive; a polling UI must not repeat it."""
    write_snapshot()
    calls: list[int] = []

    def counting_positions():
        calls.append(1)
        return []

    monkeypatch.setattr(main.mt5_adapter, "available", lambda: True)
    monkeypatch.setattr(main.mt5_adapter, "positions", counting_positions)

    first = client.get("/api/unmanaged").json()
    second = client.get("/api/unmanaged").json()

    assert len(calls) == 1, "the second call should have been served from cache"
    assert first == second

    # --- once the window passes, the account is read again
    monkeypatch.setattr(main.time, "monotonic", lambda: 1e9)
    client.get("/api/unmanaged")
    assert len(calls) == 2


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


#+------------------------------------------------------------------+
#| GET /api/history                                                 |
#+------------------------------------------------------------------+
def _deal_row(**overrides) -> dict:
    """One synthetic MT5 deal, in the adapter's field names."""
    row = {
        "ticket": 1, "position_id": 1, "symbol": "GBPUSD", "type": 0,
        "entry": 0, "volume": 1.0, "price": 1.2650, "profit": 0.0,
        "swap": 0.0, "commission": 0.0, "time": 1_760_000_000,
        "magic": 7, "comment": "",
    }
    row.update(overrides)
    return row


def _closed_pair(position_id: int = 1, *, profit: float = 100.0,
                 opened: int = 1_760_000_000, closed: int = 1_760_003_600) -> list[dict]:
    """The two deals MT5 writes for one closed long position."""
    return [
        _deal_row(ticket=position_id * 10, position_id=position_id, time=opened),
        _deal_row(
            ticket=position_id * 10 + 1, position_id=position_id, type=1,
            entry=1, price=1.2660, profit=profit, time=closed,
        ),
    ]


def test_history_is_unavailable_without_the_mt5_package(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    """No package is not "no losing trades"; the payload must say so."""
    write_snapshot()
    body = client.get("/api/history").json()

    assert body["available"] is False
    assert "MetaTrader5" in body["reason"]
    # --- a zeroed summary would read as a clean account, so ratios stay null
    assert body["metrics"]["win_rate"] is None
    assert body["metrics"]["profit_factor"] is None
    assert body["trades"] == []


def test_history_unavailable_payload_keeps_every_key(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    """The degraded shape matches the healthy one, so the client needs no guard."""
    write_snapshot()
    body = client.get("/api/history").json()
    assert set(body) == {
        "available", "reason", "symbol", "days", "trades", "metrics",
        "sessions", "equity", "tz_offset_minutes", "tz_source",
        "session_windows",
    }
    assert [row["session"] for row in body["sessions"]] == [
        "ASIAN", "LONDON", "NEW_YORK",
    ]


def test_history_reports_closed_trades_and_metrics(
        client: TestClient, write_snapshot, monkeypatch: pytest.MonkeyPatch) -> None:
    write_snapshot()
    monkeypatch.setattr(main.mt5_adapter, "available", lambda: True)
    monkeypatch.setattr(
        main.mt5_adapter, "deals",
        lambda days=30, symbol="": _closed_pair(1, profit=100.0)
        + _closed_pair(2, profit=-40.0),
    )

    body = client.get("/api/history?symbol=GBPUSD").json()

    assert body["available"] is True
    assert body["symbol"] == "GBPUSD"
    assert body["metrics"]["total"] == 2
    assert body["metrics"]["wins"] == 1
    assert body["metrics"]["net_realized"] == 60.0
    # --- newest close first, so the table reads like a statement
    assert [row["position_id"] for row in body["trades"]] == [2, 1]


def test_history_accepts_an_explicit_clock(
        client: TestClient, write_snapshot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The session label is only meaningful with the clock it was cut on."""
    write_snapshot()
    monkeypatch.setattr(main.mt5_adapter, "available", lambda: True)
    monkeypatch.setattr(
        main.mt5_adapter, "deals", lambda days=30, symbol="": _closed_pair()
    )

    body = client.get("/api/history?symbol=GBPUSD&tz_offset_minutes=-240").json()
    assert body["tz_source"] == "request"
    assert body["tz_offset_minutes"] == -240


def test_history_infers_the_broker_clock_from_the_snapshot(
        client: TestClient, write_snapshot, monkeypatch: pytest.MonkeyPatch) -> None:
    """server_time minus the bridge clock recovers a UTC+3 broker."""
    monkeypatch.setattr(main.time, "time", lambda: 1_760_000_000)
    write_snapshot(make_snapshot(server_time=1_760_000_000 + 3 * 3600))
    monkeypatch.setattr(main.mt5_adapter, "available", lambda: True)
    monkeypatch.setattr(
        main.mt5_adapter, "deals", lambda days=30, symbol="": _closed_pair()
    )

    body = client.get("/api/history?symbol=GBPUSD").json()
    assert body["tz_source"] == "snapshot"
    assert body["tz_offset_minutes"] == 180


def test_history_rejects_an_impossible_clock(
        client: TestClient) -> None:
    assert client.get("/api/history?tz_offset_minutes=5000").status_code == 422


def test_history_rejects_an_out_of_range_window(client: TestClient) -> None:
    assert client.get("/api/history?days=0").status_code == 422
    assert client.get("/api/history?days=400").status_code == 422


def test_history_limit_caps_rows_not_the_summary(
        client: TestClient, write_snapshot, monkeypatch: pytest.MonkeyPatch) -> None:
    write_snapshot()
    monkeypatch.setattr(main.mt5_adapter, "available", lambda: True)
    monkeypatch.setattr(
        main.mt5_adapter, "deals",
        lambda days=30, symbol="": _closed_pair(1) + _closed_pair(2) + _closed_pair(3),
    )

    body = client.get("/api/history?symbol=GBPUSD&limit=1").json()
    assert len(body["trades"]) == 1
    assert body["metrics"]["total"] == 3


#+------------------------------------------------------------------+
#| GET /api/candles                                                 |
#+------------------------------------------------------------------+
def test_candles_is_unavailable_without_the_mt5_package(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    write_snapshot()
    body = client.get("/api/candles?symbol=GBPUSD").json()

    assert body["available"] is False
    assert body["bars"] == []
    assert "MetaTrader5" in body["reason"]


def test_candles_rejects_an_unknown_timeframe(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    """A silent fallback to M15 would draw the wrong chart without saying so."""
    write_snapshot()
    response = client.get("/api/candles?symbol=GBPUSD&timeframe=M7")
    assert response.status_code == 422
    assert "unknown timeframe" in response.json()["detail"]


def test_candles_returns_bars_oldest_first(
        client: TestClient, write_snapshot, monkeypatch: pytest.MonkeyPatch) -> None:
    write_snapshot()
    monkeypatch.setattr(main.mt5_adapter, "available", lambda: True)
    monkeypatch.setattr(
        main.mt5_adapter, "rates",
        lambda symbol, timeframe="M15", count=120: [
            main.mt5_adapter.Rate(
                time=1_760_000_000 + i * 900, open=1.26, high=1.2602,
                low=1.2598, close=1.2601, tick_volume=100 + i,
            )
            for i in range(3)
        ],
    )

    body = client.get("/api/candles?symbol=GBPUSD&timeframe=m15&count=3").json()
    assert body["available"] is True
    assert body["timeframe"] == "M15"
    assert [bar["time"] for bar in body["bars"]] == [
        1_760_000_000, 1_760_000_900, 1_760_001_800,
    ]
    assert body["reason"] is None


def test_candles_explains_an_empty_result_rather_than_drawing_flat(
        client: TestClient, write_snapshot, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty list must not render as 'price did not move'."""
    write_snapshot()
    monkeypatch.setattr(main.mt5_adapter, "available", lambda: True)
    monkeypatch.setattr(
        main.mt5_adapter, "rates", lambda symbol, timeframe="M15", count=120: []
    )

    body = client.get("/api/candles?symbol=GBPUSD").json()
    assert body["available"] is True
    assert body["bars"] == []
    assert "no M15 bars" in body["reason"]


#+------------------------------------------------------------------+
#| POST/DELETE /api/journal                                         |
#+------------------------------------------------------------------+
def test_journal_write_persists_the_note_and_tags(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    write_snapshot()
    response = client.post(
        "/api/journal?symbol=GBPUSD",
        json={"ticket": 555, "note": "held through the retest",
              "psych": "patient", "tags": [" playbook-a ", ""]},
    )

    assert response.status_code == 200
    entry = response.json()["entry"]
    assert entry["ticket"] == 555
    assert entry["psych"] == "patient"
    # --- blank tags are dropped rather than rendered as empty chips
    assert entry["tags"] == ["playbook-a"]
    assert entry["tag"], "a broker-comment tag is still derived"

    # --- and it is readable back through the status endpoint
    listed = client.get("/api/status?symbol=GBPUSD").json()["journal"]
    assert [row["ticket"] for row in listed] == [555]
    assert listed[0]["note"] == "held through the retest"
    assert listed[0]["tags"] == ["playbook-a"]


def test_journal_write_replaces_an_existing_note(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    write_snapshot()
    client.post("/api/journal?symbol=GBPUSD", json={"ticket": 700, "note": "first"})
    client.post("/api/journal?symbol=GBPUSD", json={"ticket": 700, "note": "second"})

    listed = client.get("/api/status?symbol=GBPUSD").json()["journal"]
    assert [row["note"] for row in listed] == ["second"]


def test_journal_write_needs_a_real_ticket(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    """Ticket 0 queues an order; it is not a place to file a reflection."""
    write_snapshot()
    assert client.post(
        "/api/journal?symbol=GBPUSD", json={"ticket": 0, "note": "x"}
    ).status_code == 422


def test_journal_write_rejects_an_overlong_note(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    write_snapshot()
    assert client.post(
        "/api/journal?symbol=GBPUSD", json={"ticket": 1, "note": "x" * 501}
    ).status_code == 422


def test_journal_delete_removes_the_entry(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    write_snapshot()
    client.post("/api/journal?symbol=GBPUSD", json={"ticket": 808, "note": "gone soon"})

    body = client.delete("/api/journal?symbol=GBPUSD&ticket=808").json()
    assert body["deleted"] is True
    assert client.get("/api/status?symbol=GBPUSD").json()["journal"] == []


def test_journal_delete_is_404_when_nothing_was_written(
        client: TestClient, common_dir: Path, write_snapshot) -> None:
    """A cheerful 200 would make a mistyped ticket look successful."""
    write_snapshot()
    response = client.delete("/api/journal?symbol=GBPUSD&ticket=999")
    assert response.status_code == 404
    assert "999" in response.json()["detail"]


def test_journal_write_does_not_need_a_live_chart(
        client: TestClient, common_dir: Path) -> None:
    """A trade that already closed still deserves its note."""
    response = client.post(
        "/api/journal?symbol=EURUSD", json={"ticket": 42, "note": "after hours"}
    )
    assert response.status_code == 200
    assert response.json()["symbol"] == "EURUSD"

