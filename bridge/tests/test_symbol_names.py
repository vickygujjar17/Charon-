"""The EA and the bridge must agree on file-name sanitising.

``CCharonBridge::SafeName`` and ``paths.sanitize_symbol`` both turn a symbol
into a folder name. They are written in different languages and cannot share
code, so the risk is not that one of them crashes -- it is that they quietly
disagree and split the command queue across two folder names, one of which
nothing drains. That failure is invisible: no error, no log line, just
commands that never get collected.

The cases are *not* re-typed here. This test reads them out of
``CharonSelfTest.mq5`` itself, so the two suites cannot drift: adding a case
to the MQL5 self-test automatically holds Python to it, and a Python-only
change that breaks agreement fails immediately.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from charon_bridge import paths

REPO_ROOT = Path(__file__).resolve().parents[2]
SELF_TEST = REPO_ROOT / "CharonSelfTest.mq5"

# --- ``Check(CCharonBridge::SafeName("x") == "y", ...)`` from the self-test.
# --- The input is a C string literal; the expected value is what follows ==.
_CASE = re.compile(
    r'CCharonBridge::SafeName\(\s*"((?:[^"\\]|\\.)*)"\s*\)\s*==\s*"((?:[^"\\]|\\.)*)"'
)


def _unescape_c(text: str) -> str:
    """Minimal C-string unescaping for the literals used in the fixture."""
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            nxt = text[index + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _fixture() -> list[tuple[str, str]]:
    """Every ``(input, expected)`` pair declared in the MQL5 self-test."""
    source = SELF_TEST.read_text(encoding="utf-8")
    found = [
        (_unescape_c(raw), _unescape_c(want))
        for raw, want in _CASE.findall(source)
    ]
    return found


def test_the_self_test_actually_declares_cases() -> None:
    """Guard the guard: an unparsed file must not silently mean "all good"."""
    assert SELF_TEST.is_file(), f"the EA self-test moved: {SELF_TEST}"
    cases = _fixture()
    assert len(cases) >= 10, (
        f"only {len(cases)} SafeName cases parsed out of {SELF_TEST.name}; "
        "the regex or the self-test has changed"
    )


@pytest.mark.parametrize("raw,expected", _fixture())
def test_sanitize_symbol_matches_the_ea(raw: str, expected: str) -> None:
    assert paths.sanitize_symbol(raw) == expected, (
        f"the EA would use folder {expected!r} for {raw!r} but the bridge "
        f"would use {paths.sanitize_symbol(raw)!r}"
    )
