"""Structural tests for ``static/app.js``.

These exist because of a specific, shipped failure. In v1.0.3 ``renderLadder``
was left unclosed by a truncating edit. Everything after it -- every remaining
function and the ``boot()`` call -- ended up nested inside it, so the page
parsed cleanly, loaded cleanly, and never issued a single request to
``/api/status``. The dashboard was blank and the existing gate was blind to it:

* ``node --check`` proves a file is *parseable*, not that it runs.
* ``test_live_server.py`` only greps for the string ``"renderLadder"``.

So the checks below are about shape, not syntax. A file can be valid
JavaScript and still be a structurally dead page; that is the case these
tests are built to catch.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
APP_JS = STATIC_DIR / "app.js"
INDEX_HTML = STATIC_DIR / "index.html"


#+------------------------------------------------------------------+
#| A small JavaScript scanner                                       |
#|                                                                  |
#| Counting braces with str.count() is useless here: every brace in  |
#| a comment, a string or a regex is not code. This strips those     |
#| regions first, keeping offsets stable by replacing them with      |
#| spaces, then reports the code-only text.                          |
#+------------------------------------------------------------------+
class ScanError(AssertionError):
    """The scanner found something it refuses to interpret."""


def _blank(chunk: str) -> str:
    """Space-for-character, newlines preserved, so offsets stay valid."""
    return "".join("\n" if ch == "\n" else " " for ch in chunk)


def _scan_string(source: str, start: int) -> int:
    """End offset of the quoted string beginning at ``start``."""
    quote = source[start]
    cursor = start + 1
    length = len(source)
    while cursor < length:
        here = source[cursor]
        if here == "\\":
            cursor += 2
            continue
        if here == quote:
            return cursor + 1
        if here == "\n":
            raise ScanError(f"unterminated {quote} string")
        cursor += 1
    raise ScanError(f"unterminated {quote} string")


def _scan_regex(source: str, start: int) -> int:
    """End offset of the regex literal beginning at ``start``."""
    cursor = start + 1
    length = len(source)
    in_class = False
    while cursor < length:
        here = source[cursor]
        if here == "\\":
            cursor += 2
            continue
        if here == "[":
            in_class = True
        elif here == "]":
            in_class = False
        elif here == "/" and not in_class:
            cursor += 1
            while cursor < length and source[cursor].isalpha():
                cursor += 1      # flags
            return cursor
        elif here == "\n":
            raise ScanError("unterminated regex literal")
        cursor += 1
    raise ScanError("unterminated regex literal")



def _strip_literals(source: str) -> str:
    """Return ``source`` with comments, strings and regexes blanked out."""
    out: list[str] = []
    index = 0
    length = len(source)
    prev_significant = ""       # tells a regex apart from a division
    regex_leaders = set("(,=:[!&|?{};+-*%~^<>")

    while index < length:
        ch = source[index]
        nxt = source[index + 1] if index + 1 < length else ""

        # --- line comment
        if ch == "/" and nxt == "/":
            end = source.find("\n", index)
            end = length if end == -1 else end
            out.append(_blank(source[index:end]))
            index = end
            continue

        # --- block comment
        if ch == "/" and nxt == "*":
            end = source.find("*/", index + 2)
            if end == -1:
                raise ScanError("unterminated block comment")
            end += 2
            out.append(_blank(source[index:end]))
            index = end
            continue

        # --- quoted string
        if ch in ("'", '"'):
            end = _scan_string(source, index)
            out.append(_blank(source[index:end]))
            index = end
            prev_significant = ch
            continue

        # --- template literal: blank the text, keep ${...} as code
        if ch == "`":
            out.append(" ")
            index += 1
            while index < length:
                if source[index] == "\\":
                    out.append(_blank(source[index:index + 2]))
                    index += 2
                    continue
                if source[index] == "`":
                    out.append(" ")
                    index += 1
                    break
                if source[index] == "$" and source[index + 1:index + 2] == "{":
                    out.append("  ")
                    index += 2
                    depth = 1
                    while index < length and depth > 0:
                        if source[index] == "{":
                            depth += 1
                        elif source[index] == "}":
                            depth -= 1
                        out.append(source[index] if depth > 0 else " ")
                        index += 1
                    continue
                out.append(_blank(source[index]))
                index += 1
            else:
                raise ScanError("unterminated template literal")
            prev_significant = "`"
            continue

        # --- regex literal, recognised by what precedes it
        if ch == "/" and (prev_significant == "" or prev_significant in regex_leaders):
            end = _scan_regex(source, index)
            out.append(_blank(source[index:end]))
            index = end
            prev_significant = "/"
            continue

        out.append(ch)
        if not ch.isspace():
            prev_significant = ch
        index += 1

    return "".join(out)


@pytest.fixture(scope="module")
def source() -> str:
    if not APP_JS.is_file():
        pytest.fail(f"{APP_JS} does not exist")
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(source: str) -> str:
    """The file with every literal and comment blanked out."""
    return _strip_literals(source)


def _line_of(code: str, offset: int) -> int:
    return code.count("\n", 0, offset) + 1


def _function_declaration_depths(code: str) -> list[tuple[str, int, int]]:
    """Every ``function name(`` with the brace depth it is declared at."""
    depth = 0
    found: list[tuple[str, int, int]] = []
    pattern = re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(")
    index = 0
    while index < len(code):
        match = pattern.match(code, index)
        if match:
            found.append((match.group(1), depth, _line_of(code, index)))
        ch = code[index]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        index += 1
    return found


#+------------------------------------------------------------------+
#| The checks                                                       |
#+------------------------------------------------------------------+
def test_braces_balance_to_zero(code: str) -> None:
    """An unclosed function is the v1.0.3 bug; this is the tripwire."""
    depth = 0
    lowest = 0
    for ch in code:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            lowest = min(lowest, depth)
    assert depth == 0, (
        f"app.js ends at brace depth {depth}, so something is unclosed. "
        "Everything after that point is nested inside it and will never run."
    )
    assert lowest >= 0, "app.js closes more braces than it opens"


def test_every_function_is_declared_at_top_level(code: str) -> None:
    """Functions must be siblings of ``boot``, not nested inside a renderer.

    A depth > 0 declaration means the file has drifted back into the shape
    that silently disabled the dashboard.
    """
    declarations = _function_declaration_depths(code)
    assert declarations, "no function declarations found at all"

    nested = [(name, depth, line) for name, depth, line in declarations if depth != 0]
    assert not nested, (
        "these functions are declared inside another block, so they are not "
        "reachable at load time:\n"
        + "\n".join(f"  line {line}: {name} (depth {depth})" for name, depth, line in nested)
    )


def test_function_names_are_unique(code: str) -> None:
    declarations = _function_declaration_depths(code)
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for name, _depth, line in declarations:
        if name in seen:
            duplicates.append(f"{name} declared on lines {seen[name]} and {line}")
        else:
            seen[name] = line
    assert not duplicates, "duplicate function declarations:\n" + "\n".join(duplicates)



def test_boot_is_called_exactly_once_and_last(code: str) -> None:
    """The call site has to survive to the end of the file.

    In v1.0.3 the last code in the file was a stray ``}`` belonging to the
    truncated function -- ``boot()`` was present but buried inside it, and
    the statement that actually started the app was unreachable.
    """
    calls = list(re.finditer(r"^\s*boot\(\)\s*;", code, re.MULTILINE))
    assert calls, "app.js never calls boot(); the page would stay inert"
    assert len(calls) == 1, f"boot() is called {len(calls)} times"

    trailing = code[calls[-1].end():].strip()
    assert trailing == "", (
        "code follows the boot() call at the end of the file: "
        f"{trailing[:120]!r}"
    )


def test_boot_only_calls_helpers_declared_at_top_level(code: str) -> None:
    """Everything boot() reaches for must already exist when it runs."""
    boot_body = re.search(r"async function boot\(\)\s*\{(.*?)\n\}", code, re.DOTALL)
    assert boot_body, "boot() is missing"

    declared = {name for name, _depth, _line in _function_declaration_depths(code)}
    # --- the lookbehind keeps method calls (`response.json(`) out of the set:
    # --- only a bare identifier is a helper this file must declare.
    called = set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", boot_body.group(1)))
    keywords = {"if", "for", "while", "switch", "catch", "setInterval", "fetch", "await"}
    missing = sorted(name for name in called - declared - keywords)
    assert not missing, f"boot() calls helpers that are not declared: {missing}"


def test_every_element_id_exists_in_the_page(source: str) -> None:
    """A typo in a selector is a silent null, not an error.

    ``$("typo")`` returns null and the next property access throws inside a
    promise, where nothing surfaces it.
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    declared = set(re.findall(r'\bid="([^"]+)"', html))
    assert declared, "index.html declares no ids at all"

    used = set(re.findall(r'\$\(\s*"([^"]+)"\s*\)', source))
    assert used, "app.js never looks up an element by id"

    missing = sorted(used - declared)
    assert not missing, (
        "app.js references ids that index.html does not define: " + ", ".join(missing)
    )
    unused = sorted(declared - used)
    # --- documented, not enforced: index.html may carry ids for future use
    if unused:
        print(f"note: ids in the page that the client does not touch: {unused}")


def test_script_is_loaded_by_the_page() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "/static/app.js" in html, "index.html does not load the client script"


def test_node_accepts_the_script() -> None:
    """Parseability, kept as a second opinion rather than the only check."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    result = subprocess.run(
        [node, "--check", str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


def test_no_dynamic_tailwind_class_concatenation(source: str) -> None:
    """Tailwind scans this file as text, so a built-up class name emits nothing.

    ``"text-" + kind`` produces no CSS at all -- the banner would render
    colourless. Every class must appear as a complete literal.
    """
    offenders = [
        line.strip()
        for line in source.splitlines()
        if not line.lstrip().startswith("//")
        and not line.lstrip().startswith("*")
        and not line.lstrip().startswith("/*")
        and (
            re.search(r'"[a-z-]*text-"[ ]*[+]', line)
            or re.search(r'"bg-"[ ]*[+]', line)
            or re.search(r'"border-"[ ]*[+]', line)
        )
    ]
    assert not offenders, (
        "dynamically built Tailwind class names will not be compiled:\n"
        + "\n".join(offenders)
    )


def test_ladder_is_rendered_from_server_text(source: str) -> None:
    """The client must not invent rung prices; it renders what it was given."""
    assert "function ladderRungs" in source
    assert "not reported" in source

    ladder_block = re.search(r"function ladderRungs\(text\)\s*\{(.*?)\n\}", source, re.DOTALL)
    assert ladder_block, "ladderRungs is missing"
    assert "*" not in ladder_block.group(1), (
        "ladderRungs performs arithmetic on rung values; the ladder must be "
        "passed through from the EA unmodified"
    )


def test_client_never_calls_the_inbox_endpoints_with_a_verb(source: str) -> None:
    """No order-capable verb may appear in the client at all."""
    for forbidden in ("/api/trade", "order_send", "positions_close"):
        assert forbidden not in source, f"the client references {forbidden}"

#+------------------------------------------------------------------+
#| The ticket's symbol combobox (v1.6.1)                             |
#|                                                                  |
#| The picker replaced a datalist, and a datalist could not go wrong  |
#| in the way this one can. Two failures are worth a guard each:      |
#|                                                                   |
#|  1. Sending the display label instead of the broker name. EURUSD.a |
#|     is shown as "EURUSD"; posting the label names an instrument    |
#|     the broker does not list, and the operator would see a refusal |
#|     for a symbol they can plainly read on screen.                  |
#|  2. Losing the ARIA wiring that makes it a combobox rather than a  |
#|     div with a list in it. That failure is invisible: the list     |
#|     still opens and still works with a mouse.                      |
#+------------------------------------------------------------------+
def _element_tag(html: str, element_id: str) -> str:
    """The opening tag of ``id="element_id"``, across line breaks."""
    match = re.search(rf'<[^>]*\bid="{element_id}"[^>]*>', html, re.DOTALL)
    assert match, f'index.html has no element with id="{element_id}"'
    return match.group(0)


def test_symbol_combobox_is_wired_as_a_combobox() -> None:
    """The markup must declare the roles a screen reader needs.

    Only one element may be the combobox for a given listbox, so the role sits
    on the search input and the collapsed trigger keeps aria-haspopup. Getting
    this backwards is not a cosmetic defect: a button claiming role=combobox
    with aria-expanded but no textbox leaves the keyboard nowhere to type.
    """
    html = INDEX_HTML.read_text(encoding="utf-8")

    trigger = _element_tag(html, "symbolTrigger")
    assert 'aria-haspopup="listbox"' in trigger
    assert 'aria-expanded="false"' in trigger, "the trigger must start collapsed"
    assert "aria-controls=" in trigger
    assert 'role="combobox"' not in trigger, (
        "the collapsed button must not claim role=combobox: only the search "
        "input can be the combobox for #symbolList"
    )

    search = _element_tag(html, "symbolSearch")
    assert 'role="combobox"' in search, "the search input must own role=combobox"
    assert 'aria-autocomplete="list"' in search
    assert "aria-controls=" in search

    assert 'role="listbox"' in _element_tag(html, "symbolList")

    # --- and the value that is posted lives in a hidden input, not in the
    # --- trigger's text
    hidden = _element_tag(html, "orderSymbol")
    assert 'type="hidden"' in hidden, "#orderSymbol must not be a visible field"
def test_symbol_combobox_sends_the_broker_name_not_the_label(source: str) -> None:
    """Every write to #orderSymbol must be a name, never a display label.

    The harness proves the behaviour for one payload; this proves the shape
    cannot drift. A future edit that wrote ``entry.label`` into the field would
    leave the tab looking right and post an instrument the broker does not list.
    """
    writes = re.findall(r'\$\("orderSymbol"\)\.value\s*=\s*([^;]+);', source)
    assert writes, "nothing ever writes the order symbol field"
    for write in writes:
        assert write.strip().startswith("exact"), (
            "the order symbol field is written from something other than the "
            f"chosen name: {write.strip()!r}. Only chooseSymbol() may write it, "
            "and only from the exact broker name in the payload."
        )

    chosen = re.search(r"function chooseSymbol\(name, refocusTrigger\)\s*\{(.*?)\n\}", source,
                       re.DOTALL)
    assert chosen, "chooseSymbol is missing"
    assert "entry.label" not in chosen.group(1), (
        "chooseSymbol() references the display label, which must never be sent"
    )
    assert "option.dataset.symbol" in source, (
        "row clicks must read the exact name from the row's data-symbol"
    )


def _without_comments(source: str) -> str:
    """``source`` with ``//`` and ``/* */`` comments blanked, strings kept.

    ``_strip_literals`` above is the opposite tool: it keeps the code and blanks
    the strings. For "what text does this function actually write?", the strings
    are the evidence and the comments are the noise -- a comment explaining why
    a unit is wrong must not itself trip the check.
    """
    out: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        ch = source[index]
        nxt = source[index + 1] if index + 1 < length else ""
        if ch == "/" and nxt == "/":
            end = source.find("\n", index)
            end = length if end == -1 else end
            out.append(_blank(source[index:end]))
            index = end
            continue
        if ch == "/" and nxt == "*":
            end = source.find("*/", index + 2)
            if end == -1:
                return "".join(out) + source[index:]
            end += 2
            out.append(_blank(source[index:end]))
            index = end
            continue
        if ch in ("'", '"'):
            end = _scan_string(source, index)
            out.append(source[index:end])
            index = end
            continue
        out.append(ch)
        index += 1
    return "".join(out)


    """The spread must be labelled in points.

    "Pip" is ambiguous across the instruments the bridge lists: 10 points on a
    5-digit FX pair, 1 on a 3-digit JPY pair, and meaningless on US30.cash. The
    client therefore states points and scales by the instrument's own digits.

    The check is on what is actually written to the page, not on the function
    body: the comment above the arithmetic explains why "pip" is wrong, and a
    test that banned the word outright would forbid explaining the decision.
    """
    block = re.search(r"function paintOrderQuote\(\)\s*\{(.*?)\n\}", source, re.DOTALL)
    assert block, "paintOrderQuote is missing"
    body = _without_comments(block.group(1))

    assert "Math.pow(10, digits)" in body, (
        "the spread is scaled by a hard-coded factor instead of the instrument's digits"
    )
    assert '" points"' in body, "the spread is not rendered with the unit \"points\""
    emitted = re.findall(r'"([^"\\]*)"', body)
    assert not any("pip" in text.lower() for text in emitted), (
        f"the spread's rendered text mentions pips: {emitted}"
    )


def test_symbol_combobox_never_borrows_another_symbols_quote(source: str) -> None:
    """A quote belongs to one symbol. Naming another must not inherit it."""
    block = re.search(r"function paintOrderQuote\(\)\s*\{(.*?)\n\}", source, re.DOTALL)
    assert block, "paintOrderQuote is missing"
    body = block.group(1)
    assert "quote.symbol" in body, "the quote never checks which symbol it belongs to"
    assert "no quote for" in body, (
        "a mismatched symbol must say there is no quote rather than showing one"
    )


def test_datalist_symbol_picker_is_gone() -> None:
    """The replaced control must not linger half-removed.

    A leftover #orderSymbolFilter listener would keep repainting a node that no
    longer exists, and the ticket's own datalist left in the markup would give
    the browser a second, unlabelled suggestion list beside the real one.

    The page keeps one other <datalist> on purpose -- #psychOptions feeds the
    journal's free-text psych field -- so this asserts on the symbol picker's
    ids and its own datalist, not on the element name.
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="symbolOptions"' not in html, "the old symbol datalist is still in the page"
    assert 'id="orderSymbolFilter"' not in html, "the old filter box is still in the page"
    assert 'list="symbolOptions"' not in html, (
        "an input still points at the removed symbol datalist"
    )
    assert "orderSymbolFilter" not in APP_JS.read_text(encoding="utf-8"), (
        "app.js still listens to the removed filter box"
    )
    assert "paintSymbolOptions" not in APP_JS.read_text(encoding="utf-8"), (
        "app.js still repaints the removed datalist"
    )








#+------------------------------------------------------------------+
#| Self-check: the gate must fail on the bug it was written for     |
#|                                                                  |
#| A test that passes on any input is not a test. These two feed a   |
#| deliberately broken source through the same analyzer the real     |
#| assertions use, so a future refactor cannot quietly turn the      |
#| structural checks into no-ops.                                    |
#+------------------------------------------------------------------+
BROKEN_SAMPLE = """\
function outer() {
  const box = $("ladder");
  box.replaceChildren();

function inner() {
  return 1;
}

async function boot() {
  await refresh();
}

boot();

  const select = $("trailSelect");
  if (select) {
    select.value = "1";
  }
}
"""


def test_scanner_detects_a_function_nested_by_a_missing_brace() -> None:
    code = _strip_literals(BROKEN_SAMPLE)
    nested = [
        name for name, depth, _line in _function_declaration_depths(code) if depth != 0
    ]
    assert "inner" in nested, "the analyzer no longer notices nested declarations"
    assert "boot" in nested, "the analyzer no longer notices a buried boot()"


def test_scanner_detects_code_after_boot() -> None:
    code = _strip_literals(BROKEN_SAMPLE)
    calls = list(re.finditer(r"^\s*boot\(\)\s*;", code, re.MULTILINE))
    assert calls, "boot() call was not found in the sample"
    assert code[calls[-1].end():].strip() != "", (
        "the analyzer no longer notices statements trailing the boot() call"
    )



#+------------------------------------------------------------------+
#| Execution: does the page actually run?                           |
#|                                                                  |
#| The structural checks above prove the file is *shaped* correctly  |
#| and node --check proves it *parses*. Neither proves it runs.      |
#| tests/client_harness.js executes app.js against a DOM stub and    |
#| asserts that it really requests /api/status and writes the        |
#| payload into the page. That is the difference between v1.0.3      |
#| (parsed fine, did nothing) and a working dashboard.               |
#+------------------------------------------------------------------+
HARNESS = Path(__file__).resolve().parent / "client_harness.js"


def test_client_actually_runs_and_polls_the_bridge() -> None:
    """Execute app.js in node and require that it polls and renders."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    if not HARNESS.is_file():
        pytest.fail(f"{HARNESS} is missing")

    result = subprocess.run(
        [node, str(HARNESS)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        "app.js loaded but did not behave like a dashboard:\n" + output
    )
    assert "EXECUTION CHECK PASSED" in output, output


#+------------------------------------------------------------------+
#| Re-targeting a page served from somewhere else (v1.7.1)          |
#|                                                                  |
#| A page served from a Vercel deployment resolves "/api/status" to  |
#| Vercel, which has no bridge -- so the request never reaches the   |
#| operator's PC and the dashboard reports the bridge as down while  |
#| it is running perfectly. The fix is an explicit base, configured  |
#| through a cookie so one static bundle serves both layouts.        |
#|                                                                  |
#| These two cases pin the contract from both sides: an empty cookie |
#| must leave every request relative (or loopback installs break),   |
#| and a set cookie must rewrite every one of them (or the hosted    |
#| page silently talks to the wrong origin).                         |
#+------------------------------------------------------------------+
def _run_harness_with_cookie(cookie: str, base: str = "", token: str = ""):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    env = dict(os.environ)
    env["CHARON_HARNESS_COOKIE"] = cookie
    env["CHARON_HARNESS_COOKIE_BASE"] = base
    env["CHARON_HARNESS_COOKIE_TOKEN"] = token
    return subprocess.run(
        [node, str(HARNESS)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=env,
    )


def test_client_stays_relative_without_a_base_cookie() -> None:
    """No cookie means same origin -- the ordinary loopback install."""
    result = _run_harness_with_cookie("")
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        "the client broke when no charon_api cookie was present:\n" + output
    )
    assert "EXECUTION CHECK PASSED" in output, output


def test_client_retargets_every_request_to_the_configured_base() -> None:
    """A charon_api cookie must move every request, and carry the token."""
    base = "https://bridge.example.com"
    result = _run_harness_with_cookie(
        "charon_api=https%3A%2F%2Fbridge.example.com; charon_token=s3cret",
        base=base,
        token="s3cret",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        "the client did not honour the configured bridge base:\n" + output
    )
    assert "EXECUTION CHECK PASSED" in output, output
    # --- proof the run was not vacuous: the URLs really were rewritten
    assert base + "/api/status" in output, output


def test_harness_can_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prove the execution check is not a rubber stamp.

    The harness is pointed at a stub client that does nothing, which is
    precisely the v1.0.3 failure mode, and it must reject it.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")

    fake_bridge = tmp_path / "bridge"
    (fake_bridge / "static").mkdir(parents=True)
    (fake_bridge / "tests").mkdir(parents=True)

    # --- a page that loads, defines things, and never polls
    (fake_bridge / "static" / "app.js").write_text(
        "function renderLadder(c, t) { c.replaceChildren(); }\n"
        "function refresh() { return null; }\n"
        "async function boot() {}\n"
        "boot();\n",
        encoding="utf-8",
    )
    shutil.copy(HARNESS, fake_bridge / "tests" / "client_harness.js")

    result = subprocess.run(
        [node, str(fake_bridge / "tests" / "client_harness.js")],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode != 0, (
        "the harness accepted a client that never polls, so it proves nothing"
    )
    assert "never requested /api/status" in (result.stdout + result.stderr)

