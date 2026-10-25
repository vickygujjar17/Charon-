"""Symbol resolution: the boundary between a label and a command argument.

The property under test is that this module can only ever return a name it
was told about. A picker that invents a symbol produces a command the EA
refuses at best -- and at worst, one that a *different* chart accepts.
"""

from __future__ import annotations

import pytest

from charon_bridge import symbols


@pytest.fixture(autouse=True)
def _clear_affixes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no configured broker affixes."""
    monkeypatch.delenv(symbols.PREFIX_ENV, raising=False)
    monkeypatch.delenv(symbols.SUFFIX_ENV, raising=False)


#+------------------------------------------------------------------+
#| canonical() -- display only                                      |
#+------------------------------------------------------------------+
def test_canonical_is_upper_case_and_identity_without_affixes() -> None:
    assert symbols.canonical("eurusd") == "EURUSD"
    assert symbols.canonical("  EURUSD  ") == "EURUSD"
    assert symbols.canonical("US30.cash") == "US30.CASH"


def test_canonical_strips_a_configured_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".a")
    assert symbols.canonical("EURUSD.a") == "EURUSD"
    # --- case-insensitive, because brokers do not promise a case either
    assert symbols.canonical("EURUSD.A") == "EURUSD"


def test_canonical_strips_a_configured_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(symbols.PREFIX_ENV, "m")
    assert symbols.canonical("mEURUSD") == "EURUSD"


def test_canonical_never_reduces_a_name_to_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An instrument literally called ``.a`` must not vanish from the picker."""
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".a")
    assert symbols.canonical(".a") == ".A"
    assert symbols.canonical("") == ""


def test_canonical_does_not_guess_unconfigured_affixes() -> None:
    """No configuration means no stripping -- guessing mangles real names.

    A heuristic that stripped everything after ``.`` would turn
    ``US30.cash`` into ``US30``, a symbol this broker does not have.
    """
    assert symbols.canonical("US30.cash") == "US30.CASH"
    assert symbols.canonical("#EURUSD") == "#EURUSD"


#+------------------------------------------------------------------+
#| resolve() -- never invents a name                                 |
#+------------------------------------------------------------------+
def test_resolve_returns_the_broker_name_not_the_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: ``EURUSD`` in, ``EURUSD.a`` out."""
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".a")
    known = ["EURUSD.a", "GBPUSD.a"]
    assert symbols.resolve("EURUSD", known) == "EURUSD.a"
    assert symbols.resolve("eurusd", known) == "EURUSD.a"
    assert symbols.resolve("EURUSD.a", known) == "EURUSD.a"


def test_resolve_returns_none_for_an_unknown_symbol() -> None:
    assert symbols.resolve("USDJPY", ["EURUSD", "GBPUSD"]) is None
    assert symbols.resolve("", ["EURUSD"]) is None


def test_resolve_refuses_an_ambiguous_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two broker names collapsing to one label must not be guessed between.

    This needs both affixes configured, and it is not hypothetical: with
    ``m``/``.a`` a terminal can offer ``mEURUSD.a`` and ``EURUSD.a`` at the
    same time. Silently picking the first would send a live order to
    whichever chart happened to sort earlier.
    """
    monkeypatch.setenv(symbols.PREFIX_ENV, "m")
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".a")

    known = ["mEURUSD.a", "EURUSD.a"]
    assert symbols.canonical("mEURUSD.a") == symbols.canonical("EURUSD.a") == "EURUSD"
    assert symbols.resolve("EURUSD", known) is None
    assert symbols.matches("EURUSD", known) == ["mEURUSD.a", "EURUSD.a"]


def test_matches_prefers_an_exact_hit_over_a_canonical_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exact name is never ambiguous, even when others share its label."""
    monkeypatch.setenv(symbols.PREFIX_ENV, "m")
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".a")
    known = ["mEURUSD.a", "EURUSD.a"]
    # --- the label is ambiguous, but the exact broker name is not
    assert symbols.matches("EURUSD.a", known) == ["EURUSD.a"]
    assert symbols.matches("eurusd.a", known) == ["EURUSD.a"]
    assert symbols.resolve("EURUSD.a", known) == "EURUSD.a"


def test_matches_is_ordered_most_specific_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".a")
    assert symbols.matches("EURUSD", ["EURUSD.a"]) == ["EURUSD.a"]
    assert symbols.matches("EURUSD", []) == []


def test_resolve_ignores_case_only_duplicates() -> None:
    """A broker listing both cases is one instrument, not an ambiguity."""
    assert symbols.resolve("EURUSD", ["EURUSD", "eurusd"]) == "EURUSD"


#+------------------------------------------------------------------+
#| catalogue() -- merging the two sources                            |
#+------------------------------------------------------------------+
def test_catalogue_puts_live_charts_first() -> None:
    entries = symbols.catalogue(chart=["GBPUSD"], broker=["AUDUSD", "EURUSD"])
    assert [entry.name for entry in entries] == ["GBPUSD", "AUDUSD", "EURUSD"]
    assert entries[0].sources == (symbols.SOURCE_CHART,)


def test_catalogue_merges_a_symbol_found_in_both_sources() -> None:
    """A live, listed symbol appears once and says it is both."""
    entries = symbols.catalogue(chart=["EURUSD"], broker=["EURUSD", "GBPUSD"])
    by_name = {entry.name: entry for entry in entries}
    assert len(entries) == 2
    assert by_name["EURUSD"].sources == (symbols.SOURCE_CHART, symbols.SOURCE_BROKER)
    assert by_name["GBPUSD"].sources == (symbols.SOURCE_BROKER,)


def test_catalogue_carries_both_the_exact_name_and_the_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``symbol`` is what gets sent; ``label`` is only ever displayed."""
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".a")
    entry = symbols.catalogue(broker=["EURUSD.a"])[0]
    assert entry.name == "EURUSD.a"
    assert entry.canonical == "EURUSD"
    assert entry.as_dict() == {
        "symbol": "EURUSD.a",
        "label": "EURUSD",
        "sources": [symbols.SOURCE_BROKER],
    }


def test_catalogue_drops_blank_and_duplicate_names() -> None:
    entries = symbols.catalogue(
        chart=["EURUSD", "", "  ", "eurusd"],
        broker=["EURUSD"],
    )
    assert [entry.name for entry in entries] == ["EURUSD"]


def test_catalogue_of_nothing_is_empty() -> None:
    assert symbols.catalogue() == []
