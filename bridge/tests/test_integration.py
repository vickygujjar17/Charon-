"""Loopback integration tests: HTTP in, files out, EA verdict back in.

The unit tests exercise the app in-process and the live-server tests check that
static assets are served. Neither closes the loop. This suite does:

    POST /api/pyramid/arm  ->  commands/*.json  ->  FakeEA.poll()
                           ->  commands/processed/ok_*.json
                           ->  GET /api/commands reports "accepted"

and the same loop for ``trail_mode``, including the cases that must *not*
apply: a backdated command, a bad verb, a mismatched symbol.

It also pins the staleness ladder end to end (live -> stale -> offline) and the
SSE ``id:`` / ``Last-Event-ID`` contract, which the client relies on to avoid
duplicating log lines on every reconnect.

The EA here is ``tests/fake_ea.py`` -- a faithful impersonation of
``CCharonBridge`` because no MetaTrader terminal exists in CI. That substitution
is disclosed in the CHANGELOG; what is *real* is the bridge process, the HTTP
surface, and every byte that crosses the filesystem.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.fake_ea import SCHEMA, FakeEA

BRIDGE_DIR = Path(__file__).resolve().parent.parent


#+------------------------------------------------------------------+
#| Server harness                                                   |
#+------------------------------------------------------------------+
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_health(base: str, process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            output = b""
            if process.stdout:
                output = process.stdout.read()
            pytest.fail(f"bridge exited early:\n{output.decode(errors='replace')}")
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    pytest.fail("bridge did not become healthy in time")


class Client:
    """The smallest possible HTTP client: no test-only shortcut paths."""

    def __init__(self, base: str) -> None:
        self.base = base

    def get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = json.loads(error.read() or b"{}")
            return error.code, body

    def post(self, path: str) -> tuple[int, dict]:
        request = urllib.request.Request(self.base + path, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            body = json.loads(error.read() or b"{}")
            return error.code, body


@pytest.fixture()
def ea(common_dir: Path) -> FakeEA:
    """A fake EA publishing into the same tree the bridge reads."""
    fake = FakeEA(common_dir)
    fake.publish()
    return fake


@pytest.fixture()
def bridge(common_dir: Path, ea: FakeEA):
    """A real uvicorn process, pointed at the throwaway Common\\\\Files tree."""
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(BRIDGE_DIR),
        env={**os.environ,
             "CHARON_COMMON_DIR": str(common_dir),
             "PYTHONPATH": str(BRIDGE_DIR)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base, process)
        yield Client(base)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


#+------------------------------------------------------------------+
#| The loop: HTTP -> inbox -> EA -> processed/ -> HTTP              |
#+------------------------------------------------------------------+
def test_arm_command_travels_the_whole_loop(bridge: Client, ea: FakeEA) -> None:
    """Arm via HTTP, let the EA drain it, then read the verdict back."""
    status, body = bridge.post("/api/pyramid/arm")
    assert status == 200, body
    assert body["queued"] is True

    pending_file = body["command"]["file"]
    assert (ea.inbox / pending_file).is_file(), "the command did not reach the inbox"

    # --- the EA drains on its own timer; one tick is enough
    verdicts = ea.drain(limit=1)
    assert len(verdicts) == 1
    assert verdicts[0]["accepted"] is True, verdicts[0]
    assert verdicts[0]["file"].startswith("ok_")
    assert ea.armed is True

    # --- the audit trail the dashboard reads is the same file
    status, commands = bridge.get("/api/commands")
    assert status == 200
    assert commands["pending"] == [], "the inbox should be empty after a drain"
    accepted = [entry for entry in commands["processed"] if entry["verdict"] == "accepted"]
    assert accepted, commands
    assert accepted[0]["file"] == verdicts[0]["file"]


def test_disarm_travels_the_loop_and_cancels_the_arm(bridge: Client, ea: FakeEA) -> None:
    assert bridge.post("/api/pyramid/arm")[0] == 200
    ea.drain()
    assert ea.armed is True

    assert bridge.post("/api/pyramid/disarm")[0] == 200
    verdicts = ea.drain(limit=1)
    assert [v["accepted"] for v in verdicts] == [True]
    assert ea.armed is False


def test_trail_mode_command_changes_the_ea_mode(bridge: Client, ea: FakeEA) -> None:
    """The one command that mutates EA state, end to end."""
    assert ea.trail_mode == 2

    status, body = bridge.post("/api/trail?mode=4")
    assert status == 200, body
    assert body["command"]["sent"]["mode"] == 4

    verdicts = ea.drain(limit=1)
    assert verdicts[0]["accepted"] is True, verdicts[0]
    assert ea.trail_mode == 4

    # --- republish, and the dashboard must report what the EA now believes
    ea.publish()
    status, payload = bridge.get("/api/status")
    assert status == 200
    assert payload["snapshot"]["trail"]["mode"] == 4
    assert payload["snapshot"]["trail"]["mode_name"] == "RR_LOCK"


def test_commands_are_consumed_fifo(bridge: Client, ea: FakeEA) -> None:
    """An arm issued before a disarm must never be applied the other way round."""
    bridge.post("/api/pyramid/arm")
    bridge.post("/api/pyramid/disarm")

    verdicts = ea.drain(limit=5)
    assert [v["verb"] for v in verdicts] == ["arm", "disarm"]
    assert ea.armed is False, "FIFO ordering was violated"



#+------------------------------------------------------------------+
#| Commands the EA must refuse                                      |
#+------------------------------------------------------------------+
def test_backdated_command_is_rejected_and_logged(bridge: Client, ea: FakeEA) -> None:
    """The +/-60s window is the anti-replay guard; prove it still bites.

    A file left in the inbox and replayed later must be a no-op, and the
    refusal must be visible in the audit trail rather than silent.
    """
    inbox_file = ea.inbox / "00000000000000000001-000001-stale.json"
    inbox_file.write_text(json.dumps({
        "schema": SCHEMA,
        "id": "stale-command",
        "verb": "arm",
        "symbol": ea.symbol,
        "issued": int(time.time()) - 600,      # ten minutes old
    }), encoding="utf-8")

    verdicts = ea.drain(limit=1)
    assert len(verdicts) == 1
    assert verdicts[0]["accepted"] is False
    assert verdicts[0]["file"].startswith("rejected_")
    assert "stale command" in verdicts[0]["reason"]
    assert ea.armed is False, "a replay was applied"

    status, commands = bridge.get("/api/commands")
    assert status == 200
    rejected = [e for e in commands["processed"] if e["verdict"] == "rejected"]
    assert rejected, "the refusal is not visible to the dashboard"


def test_future_dated_command_is_rejected(ea: FakeEA) -> None:
    (ea.inbox / "00000000000000000002-000002-future.json").write_text(json.dumps({
        "schema": SCHEMA, "id": "future", "verb": "arm", "symbol": ea.symbol,
        "issued": int(time.time()) + 600,
    }), encoding="utf-8")

    verdict = ea.poll()
    assert verdict is not None
    assert verdict["accepted"] is False
    assert "future-dated" in verdict["reason"]
    assert ea.armed is False


def test_wrong_symbol_command_is_rejected(ea: FakeEA) -> None:
    """The EA targets exactly one symbol: its own."""
    (ea.inbox / "00000000000000000003-000003-eurusd.json").write_text(json.dumps({
        "schema": SCHEMA, "id": "wrong-symbol", "verb": "arm", "symbol": "EURUSD",
        "issued": int(time.time()),
    }), encoding="utf-8")

    verdict = ea.poll()
    assert verdict is not None
    assert verdict["accepted"] is False
    assert "is not" in verdict["reason"]


def test_poison_command_does_not_block_the_inbox(ea: FakeEA) -> None:
    """An invalid file must be retired, not left head-of-line blocking."""
    (ea.inbox / "00000000000000000000-000000-broken.json").write_text(
        "{ this is not json", encoding="utf-8")
    (ea.inbox / "00000000000000000009-000009-arm.json").write_text(json.dumps({
        "schema": SCHEMA, "id": "good", "verb": "arm", "symbol": ea.symbol,
        "issued": int(time.time()),
    }), encoding="utf-8")

    first = ea.poll()
    assert first is not None and first["accepted"] is False

    second = ea.poll()
    assert second is not None and second["accepted"] is True
    assert ea.armed is True


def test_bridge_never_emits_a_forbidden_verb(bridge: Client, ea: FakeEA) -> None:
    """Whatever the bridge writes must be inside the EA's whitelist."""
    bridge.post("/api/pyramid/arm")
    bridge.post("/api/trail?mode=1")

    for path in ea.inbox.glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["verb"] in ("arm", "disarm", "trail_mode", "ping")

    assert bridge.post("/api/trade")[0] == 405



#+------------------------------------------------------------------+
#| The staleness ladder, live                                      
#|                                                                  
#| 5s and 30s are thresholds in state.py; this proves the whole      
#| pipeline reports them, not just the dataclass property.           
#+------------------------------------------------------------------+
def _age_snapshot(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_status_moves_from_live_to_stale_to_offline(bridge: Client, ea: FakeEA) -> None:
    status, payload = bridge.get("/api/status")
    assert status == 200
    assert payload["status"] == "live"
    assert payload["age_seconds"] < 5

    # --- past the 5s stale threshold but inside the 30s offline one
    _age_snapshot(ea.state_file, 8)
    status, payload = bridge.get("/api/status")
    assert status == 200
    assert payload["status"] == "stale"
    assert payload["stale_after"] == 5.0
    assert payload["offline_after"] == 30.0

    # --- past the offline threshold
    _age_snapshot(ea.state_file, 45)
    status, payload = bridge.get("/api/status")
    assert status == 200
    assert payload["status"] == "offline"

    # --- a fresh publish recovers without a restart
    ea.publish()
    status, payload = bridge.get("/api/status")
    assert payload["status"] == "live"


def test_missing_snapshot_reports_a_clear_503(bridge: Client, ea: FakeEA) -> None:
    """No terminal, no snapshot: the message must name the fix."""
    ea.state_file.unlink()
    status, body = bridge.get("/api/status")
    assert status == 503
    assert "InpBridgeEnable" in body["detail"] or "no Charon snapshot" in body["detail"]


def test_stale_snapshot_still_serves_its_numbers(bridge: Client, ea: FakeEA) -> None:
    """Stale is not withheld -- it is labelled. The operator decides."""
    _age_snapshot(ea.state_file, 12)
    status, payload = bridge.get("/api/status")
    assert status == 200
    assert payload["status"] == "stale"
    assert payload["snapshot"]["equity"] == 10_120.5
    assert payload["snapshot"]["basket"]["ladder"].startswith("1.50R")



#+------------------------------------------------------------------+
#| SSE: ids, Last-Event-ID, and no duplicated replays               |
#+------------------------------------------------------------------+
def _read_sse(base: str, path: str, headers: dict[str, str] | None = None,
              limit: int = 8, timeout: float = 12.0) -> list[str]:
    """Collect every raw SSE line until ``limit`` data frames have arrived.

    ``id:`` and ``retry:`` lines matter as much as ``data:`` here, so the
    collector keeps everything and uses the data count only to decide when it
    has seen enough.
    """
    request = urllib.request.Request(base + path, headers=headers or {})
    lines: list[str] = []
    data_frames = 0
    deadline = time.time() + timeout
    with urllib.request.urlopen(request, timeout=timeout) as response:
        assert response.headers.get("content-type", "").startswith("text/event-stream")
        for raw in response:
            line = raw.decode().rstrip("\n")
            lines.append(line)
            if line.startswith("data:"):
                data_frames += 1
                if data_frames >= limit:
                    break
            if time.time() > deadline:
                break
    return lines


def test_sse_frames_carry_an_id_field(bridge: Client, ea: FakeEA) -> None:
    """Without ``id:``, EventSource cannot resume and replays the tail."""
    bridge.post("/api/pyramid/arm")
    lines = _read_sse(bridge.base, "/api/logs?stream=true", limit=3)

    ids = [line for line in lines if line.startswith("id:")] or []
    assert ids, "SSE frames carry no id:, so Last-Event-ID cannot work"


def test_sse_honours_last_event_id(bridge: Client, ea: FakeEA) -> None:
    """A reconnecting client must not be handed entries it already has."""
    bridge.post("/api/pyramid/arm")
    time.sleep(0.6)
    bridge.post("/api/pyramid/disarm")
    time.sleep(0.6)

    status, history = bridge.get("/api/logs")
    assert status == 200
    entries = history["entries"]
    assert len(entries) >= 2
    latest = entries[-1]["seq"]

    # --- ask to resume after the newest entry: nothing may be replayed
    lines = _read_sse(
        bridge.base,
        "/api/logs?stream=true",
        headers={"Last-Event-ID": str(latest)},
        limit=3,
        timeout=6,
    )
    data_lines = [line for line in lines if line.startswith("data:")]
    payloads = [json.loads(line[len("data:"):].strip()) for line in data_lines]
    replayed = [entry for entry in payloads if entry["seq"] <= latest]
    assert not replayed, f"replayed entries the client already had: {replayed}"


def test_sse_keep_alive_comments_are_comments(bridge: Client) -> None:
    """Keep-alives must be comments, never data frames the client parses."""
    request = urllib.request.Request(bridge.base + "/api/logs?stream=true")
    with urllib.request.urlopen(request, timeout=10) as response:
        seen = []
        for raw in response:
            line = raw.decode().rstrip("\n")
            seen.append(line)
            if len(seen) > 12:
                break
    for line in seen:
        if line.startswith(":"):
            assert "keep-alive" in line, f"unexpected comment frame: {line!r}"

