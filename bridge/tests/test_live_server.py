"""End-to-end smoke test: a real uvicorn process against a real file tree.

The unit tests exercise the ASGI app in-process. This one starts an
actual server and talks HTTP to it, because the things most likely to
break in a fresh checkout are the ones in-process tests cannot see:
static file mounting, the lifespan hook, and the vendored stylesheet
actually being served.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

BRIDGE_DIR = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(url: str, timeout: float = 30.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


@pytest.fixture()
def live_server(common_dir: Path, tmp_path: Path):
    """Run the bridge as a real process with a snapshot already on disk."""
    from tests.conftest import make_snapshot

    folder = common_dir / "Charon"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state_GBPUSD.json").write_text(
        json.dumps(make_snapshot()), encoding="utf-8"
    )

    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(BRIDGE_DIR),
        env={**__import__("os").environ,
             "CHARON_COMMON_DIR": str(common_dir),
             "PYTHONPATH": str(BRIDGE_DIR)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        if not _wait_for(f"{base}/api/health"):
            output = b""
            if process.stdout:
                process.stdout.close()
            pytest.fail(f"bridge did not start:\n{output.decode(errors='replace')}")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_live_server_serves_status(live_server: str) -> None:
    import urllib.request

    with urllib.request.urlopen(f"{live_server}/api/status", timeout=10) as response:
        body = json.loads(response.read())
    assert body["status"] == "live"
    assert body["snapshot"]["symbol"] == "GBPUSD"
    # --- the ladder must arrive exactly as the EA wrote it
    assert body["snapshot"]["basket"]["ladder"].startswith("1.50R 1.26950")


def test_live_server_serves_health(live_server: str) -> None:
    import urllib.request

    with urllib.request.urlopen(f"{live_server}/api/health", timeout=10) as response:
        body = json.loads(response.read())
    assert body["ok"] is True
    assert body["mt5_available"] in (True, False)


def test_live_server_serves_the_dashboard_page(live_server: str) -> None:
    import urllib.request

    with urllib.request.urlopen(f"{live_server}/", timeout=10) as response:
        html = response.read().decode()
    assert response.status == 200
    assert "Charon" in html
    assert "/static/tailwind.css" in html


def test_live_server_serves_the_vendored_stylesheet(live_server: str) -> None:
    """No CDN at runtime: the CSS must be served from our own static dir."""
    import urllib.request

    with urllib.request.urlopen(f"{live_server}/static/tailwind.css", timeout=10) as response:
        css = response.read().decode()
    assert response.status == 200
    assert len(css) > 2000
    # --- proof it is our build, not a framework default. v1.6.0 moved the
    # --- palette to the Apex token names, so the marker has to be a token our
    # --- own config defines. `--tw-` prefixed variables are framework output
    # --- and would pass on a default build; `bg-surface` cannot.
    assert "bg-surface" in css
    # --- and the vendored faces must be reachable offline, since the whole
    # --- point of shipping woff2 into static/fonts is that no request leaves
    # --- the machine
    assert "@font-face" in css
    assert "/static/fonts/" in css


def test_live_server_serves_the_vendored_fonts(live_server: str) -> None:
    """A woff2 in the repo is not a font the browser can use.

    The stylesheet points at /static/fonts/*.woff2, so those files must be
    reachable through the same static mount. A 404 here renders the panel in
    a fallback face with no error anywhere -- the typography simply looks
    wrong, which is the hardest kind of defect to notice.
    """
    import urllib.request

    for name in ("inter-400.woff2", "inter-600.woff2", "jetbrains-mono-400.woff2"):
        with urllib.request.urlopen(
            f"{live_server}/static/fonts/{name}", timeout=10
        ) as response:
            payload = response.read()
        assert response.status == 200
        # --- wOF2 is the woff2 magic number; a text/html error page would not
        # --- start with it
        assert payload[:4] == b"wOF2", f"{name} is not a woff2 payload"


def test_live_server_serves_the_client_script(live_server: str) -> None:
    import urllib.request

    with urllib.request.urlopen(f"{live_server}/static/app.js", timeout=10) as response:
        js = response.read().decode()
    assert response.status == 200
    assert "renderLadder" in js


def test_live_server_refuses_trade(live_server: str) -> None:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(f"{live_server}/api/trade", method="POST")
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)
    assert caught.value.code == 405
    body = json.loads(caught.value.read())
    assert "CharonTrade.mqh" in body["detail"]
