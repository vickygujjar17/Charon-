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
        # --- v1.4.0: a grouping heading for the picker, derived from the
        # --- canonical name. Advisory only -- see symbols.asset_class.
        "asset_class": symbols.ASSET_FOREX,
        # --- v1.6.3: the trader's word for the instrument, for search only.
        # --- Never a command argument -- see symbols.alias_for.
        "alias": "Fiber",
    }


def test_catalogue_drops_blank_and_duplicate_names() -> None:
    entries = symbols.catalogue(
        chart=["EURUSD", "", "  ", "eurusd"],
        broker=["EURUSD"],
    )
    assert [entry.name for entry in entries] == ["EURUSD"]


def test_catalogue_of_nothing_is_empty() -> None:
    assert symbols.catalogue() == []


#+------------------------------------------------------------------+
#| asset_class() -- a grouping label, never a filter                 |
#+------------------------------------------------------------------+
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("EURUSD", symbols.ASSET_FOREX),
        ("GBPJPY", symbols.ASSET_FOREX),
        ("USDCAD", symbols.ASSET_FOREX),
        ("XAUUSD", symbols.ASSET_METALS),
        ("XAGUSD", symbols.ASSET_METALS),
        ("US30.cash", symbols.ASSET_INDEX),
        ("NAS100", symbols.ASSET_INDEX),
        ("DE40", symbols.ASSET_INDEX),
        ("SPX500", symbols.ASSET_INDEX),
        ("USOIL", symbols.ASSET_ENERGY),
        ("WTIUSD", symbols.ASSET_ENERGY),
        # --- v1.5.0: crude named by its ticker rather than the word "oil"
        ("XTIUSD", symbols.ASSET_ENERGY),
        ("XBRUSD", symbols.ASSET_ENERGY),
        ("CL", symbols.ASSET_ENERGY),
        # --- softs and grains, which had no home before v1.5.0 and landed
        # --- under Other beside genuine unknowns like AAPL
        ("WHEAT", symbols.ASSET_COMMODITIES),
        ("CORN", symbols.ASSET_COMMODITIES),
        ("SOYBEAN", symbols.ASSET_COMMODITIES),
        ("COFFEE", symbols.ASSET_COMMODITIES),
        ("COCOA", symbols.ASSET_COMMODITIES),
        ("SUGAR", symbols.ASSET_COMMODITIES),
        ("COTTON", symbols.ASSET_COMMODITIES),
        ("ORANGE", symbols.ASSET_COMMODITIES),
        ("BTCUSD", symbols.ASSET_CRYPTO),
        ("ETH/USD", symbols.ASSET_CRYPTO),
        # --- v1.6.3: the ticker a broker actually lists, when there is no
        # --- word in the name at all. Everything below used to land under
        # --- Other beside genuine unknowns like AAPL.
        ("HG", symbols.ASSET_METALS),
        ("COPPER", symbols.ASSET_METALS),
        ("RB", symbols.ASSET_ENERGY),
        ("ZC", symbols.ASSET_COMMODITIES),
        ("ZW", symbols.ASSET_COMMODITIES),
        ("ZS", symbols.ASSET_COMMODITIES),
        ("ZM", symbols.ASSET_COMMODITIES),
        ("ZL", symbols.ASSET_COMMODITIES),
        ("SB", symbols.ASSET_COMMODITIES),
        ("KC", symbols.ASSET_COMMODITIES),
        ("CT", symbols.ASSET_COMMODITIES),
        ("CC", symbols.ASSET_COMMODITIES),
        ("OJ", symbols.ASSET_COMMODITIES),
        ("LE", symbols.ASSET_COMMODITIES),
        ("GF", symbols.ASSET_COMMODITIES),
        ("HE", symbols.ASSET_COMMODITIES),
    ],
)
def test_asset_class_recognises_the_common_names(name: str, expected: str) -> None:
    assert symbols.asset_class(name) == expected


def test_asset_class_strips_the_broker_suffix_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment-configured suffix must not change what an instrument *is*."""
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".a")
    assert symbols.asset_class("XAUUSD.a") == symbols.ASSET_METALS


def test_asset_class_prefers_energy_over_forex() -> None:
    """``USOIL`` contains no currency pair but ``USD`` alone; check the order."""
    assert symbols.asset_class("USOIL") == symbols.ASSET_ENERGY
    assert symbols.asset_class("USDJPY") == symbols.ASSET_FOREX


def test_a_crude_ticker_never_reaches_the_pair_test() -> None:
    """``XTIUSD`` is six letters containing a currency at the end.

    Energy is matched before the structural pair rule for exactly this reason:
    ``XTI`` + ``USD`` would otherwise look like a currency pair and the symbol
    would be filed under Forex beside EURUSD.
    """
    assert symbols.asset_class("XTIUSD") == symbols.ASSET_ENERGY
    assert symbols.asset_class("XBRUSD") == symbols.ASSET_ENERGY


def test_short_tickers_are_matched_whole_not_as_a_prefix() -> None:
    """Every two-letter ticker added in v1.6.3 must stay exact-match only.

    The hazard is not hypothetical and it is not confined to ``CL``/``CLP``.
    ``LE`` (live cattle) would claim ``LEU...``; ``HE`` would claim
    ``HEL...``; ``HG`` would claim ``HGD...``. A picker that files an
    unrelated instrument under Metals or Commodities is worse than one that
    files it under Other, because Other is *honest* about not knowing. So the
    assertion is one-directional on purpose: ``LEUUSD`` is allowed to be
    Other or Forex, but it must never be a commodity.
    """
    # --- metals: HG is copper's ticker, HGD is not copper
    assert symbols.asset_class("HG") == symbols.ASSET_METALS
    assert symbols.asset_class("HGDUSD") != symbols.ASSET_METALS
    assert symbols.asset_class("HGX") != symbols.ASSET_METALS
    # --- energy: RB is RBOB gasoline, RBXYZ is not gasoline
    assert symbols.asset_class("RB") == symbols.ASSET_ENERGY
    assert symbols.asset_class("RBXYZ") != symbols.ASSET_ENERGY
    # --- commodities: the 2-letter grain/soft/livestock tickers
    for ticker in ("LE", "HE", "ZC", "ZW", "ZS", "ZM", "ZL", "SB", "KC", "CT",
                   "CC", "OJ", "GF"):
        assert symbols.asset_class(ticker) == symbols.ASSET_COMMODITIES, ticker
    for name in ("LEUUSD", "HELUSD", "ZCX", "ZWD", "SBUX", "KCASH", "CTRY",
                 "OIL", "GFX"):
        assert symbols.asset_class(name) != symbols.ASSET_COMMODITIES, name


def test_a_two_letter_metal_ticker_does_not_swallow_a_currency_pair() -> None:
    """``HG`` is copper, but the classifier must not read ``HG`` as a prefix.

    ``ZAR`` and ``MXN`` are in ``_CURRENCIES``; a prefix rule on ``HG`` would
    turn any pair starting with it into a metal. Asserting the pair survives
    keeps the exact-match rule pinned from the other direction.
    """
    assert symbols.asset_class("USDZAR") == symbols.ASSET_FOREX
    assert symbols.asset_class("USDMXN") == symbols.ASSET_FOREX


def test_a_commodity_token_is_matched_whole_not_as_a_prefix() -> None:
    """``CL`` is crude's ticker, but ``CLP`` is the Chilean peso.

    Prefix matching on a short token is how a picker ends up filing a currency
    under Energy, so the short tokens are matched exactly. ``USDCLP`` is not
    asserted to be Forex: ``CLP`` is not in ``_CURRENCIES``, so it is Other,
    which is the pre-existing answer and beside the point here. What matters
    is that it is *not* Energy.
    """
    assert symbols.asset_class("CL") == symbols.ASSET_ENERGY
    assert symbols.asset_class("CLPUSD") != symbols.ASSET_ENERGY
    assert symbols.asset_class("USDCLP") != symbols.ASSET_ENERGY


def test_commodities_do_not_swallow_forex() -> None:
    """Every commodity token must be distinct from every currency token, or a
    pair would be classified by whichever rule ran first."""
    for name in ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD"):
        assert symbols.asset_class(name) == symbols.ASSET_FOREX


def test_commodities_are_in_the_declared_class_list() -> None:
    """A class missing from ASSET_CLASSES would be invisible to the grouping
    code that iterates the tuple rather than the data."""
    assert symbols.ASSET_COMMODITIES in symbols.ASSET_CLASSES
    assert len(set(symbols.ASSET_CLASSES)) == len(symbols.ASSET_CLASSES)


@pytest.mark.parametrize("name", ["FOOBAR", "AAPL", "", "   ", "???", "12345"])
def test_asset_class_never_rejects_an_unknown_name(name: str) -> None:
    """The whole point: an unrecognised symbol is still offered, under Other."""
    assert symbols.asset_class(name) == symbols.ASSET_OTHER


def test_asset_class_is_purely_derived_from_the_name() -> None:
    """Two calls with the same input must agree -- it is a label, not state."""
    assert symbols.asset_class("EURUSD") == symbols.asset_class("EURUSD")


#+------------------------------------------------------------------+
#| alias_for() -- the trader's word, for search only                 |
#+------------------------------------------------------------------+
@pytest.mark.parametrize(
    ("name", "alias"),
    [
        ("EURUSD", "Fiber"),
        ("GBPUSD", "Cable"),
        ("USDJPY", "Gopher"),
        ("AUDUSD", "Aussie"),
        ("USDCAD", "Loonie"),
        ("USDCHF", "Swissie"),
        ("NZDUSD", "Kiwi"),
        ("XAUUSD", "Gold"),
        ("XAGUSD", "Silver"),
        ("XPTUSD", "Platinum"),
        ("XPDUSD", "Palladium"),
        ("USOIL", "WTI"),
    ],
)
def test_alias_names_the_instrument_the_way_a_trader_does(name: str, alias: str) -> None:
    assert symbols.alias_for(name) == alias


def test_alias_is_empty_for_something_it_does_not_know() -> None:
    """No alias is the correct answer -- inventing one would be a lie."""
    assert symbols.alias_for("FOOBAR") == ""
    assert symbols.alias_for("") == ""
    assert symbols.alias_for("AAPL") == ""


def test_alias_survives_a_configured_broker_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """``XAUUSD.m`` is still gold: the alias is keyed on the canonical name."""
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".m")
    assert symbols.alias_for("XAUUSD.m") == "Gold"
    entry = symbols.catalogue(broker=["XAUUSD.m"])[0]
    assert entry.name == "XAUUSD.m", "the alias must never become the symbol"
    assert entry.as_dict()["alias"] == "Gold"


def test_alias_is_never_a_command_argument() -> None:
    """The catalogue's ``symbol`` is the broker name even when an alias exists.

    This is the invariant the whole module exists for, restated for the new
    field: a row that displays "Gold" must still send ``XAUUSD``.
    """
    entry = symbols.catalogue(chart=["XAUUSD"])[0]
    body = entry.as_dict()
    assert body["symbol"] == "XAUUSD"
    assert body["alias"] == "Gold"
    assert body["symbol"] != body["alias"]


def test_every_reference_alias_key_is_a_canonical_name() -> None:
    """Alias keys are matched against ``canonical``, so they must be upper case.

    A lower-case key would silently never match, which is the kind of typo
    that reads as "that instrument has no nickname".
    """
    for key in symbols.REFERENCE_ALIASES:
        assert key == key.upper(), key
        assert key == symbols.canonical(key), key
    assert len(set(symbols.REFERENCE_ALIASES.values())) <= len(symbols.REFERENCE_ALIASES)


#+------------------------------------------------------------------+
#| A broker tag with no configured suffix                            |
#+------------------------------------------------------------------+
def test_an_unconfigured_deployment_still_resolves_a_tagged_name() -> None:
    """``EURUSD`` -> ``EURUSD.a`` with no ``CHARON_SYMBOL_SUFFIX`` set.

    ``canonical`` refuses to strip a guessed affix, and rightly so -- but that
    left the common case unresolvable: an operator who never set the env var
    could not reach a chart the terminal insists on calling ``EURUSD.a``. The
    tag is recognised on the *known* name, which is a structural test rather
    than a guess about one broker's convention.
    """
    assert symbols.resolve("EURUSD", ["EURUSD.a", "GBPUSD.a"]) == "EURUSD.a"
    assert symbols.resolve("EURUSD", ["EURUSD.pro"]) == "EURUSD.pro"
    assert symbols.resolve("EURUSD", ["EURUSD.raw"]) == "EURUSD.raw"
    assert symbols.resolve("EURUSD", ["EURUSD_m"]) == "EURUSD_m"
    assert symbols.resolve("US30", ["US30.cash"]) == "US30.cash"
    # --- a trailing separator with no tag after it is still a listing style
    assert symbols.resolve("EURUSD", ["EURUSD#"]) == "EURUSD#"


def test_a_tagged_lookup_stays_ambiguous_with_two_candidates() -> None:
    """``EURUSD.a`` and ``EURUSD.pro`` are two charts, not one."""
    known = ["EURUSD.a", "EURUSD.pro"]
    assert symbols.resolve("EURUSD", known) is None
    assert symbols.matches("EURUSD", known) == ["EURUSD.a", "EURUSD.pro"]


def test_a_prefix_must_not_be_mistaken_for_a_tag() -> None:
    """The whole safety argument: no separator means no tag.

    ``CLPUSD`` starts with ``CL`` and ``BTCUSD`` starts with ``BTC``. Reading
    the remainder as a broker tag would resolve crude's ticker to the Chilean
    peso -- a rename that lands on a real chart publishing a different
    instrument, which is the worst outcome this module can produce.
    """
    assert symbols.matches("CL", ["CLPUSD"]) == []
    assert symbols.matches("BTC", ["BTCUSD"]) == []
    assert symbols.matches("USD", ["USDJPY"]) == []
    assert symbols.matches("EUR", ["EURUSD"]) == []


def test_a_long_remainder_is_not_a_tag() -> None:
    """A suffix is a short deployment marker, not the rest of a name."""
    assert symbols.matches("EUR", ["EURUSDT"]) == []
    assert symbols.resolve("XAU", ["XAUUSDT"]) is None


def test_a_tag_never_outranks_an_exact_name() -> None:
    """A terminal listing both ``EURUSD`` and ``EURUSD.a`` keeps the exact one."""
    assert symbols.resolve("EURUSD", ["EURUSD", "EURUSD.a"]) == "EURUSD"


def test_the_tag_pass_does_not_fire_when_a_suffix_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuration is more specific than the structural rule, and still wins."""
    monkeypatch.setenv(symbols.SUFFIX_ENV, ".a")
    known = ["EURUSD.a", "GBPUSD.a"]
    assert symbols.resolve("eurusd", known) == "EURUSD.a"
    assert symbols.matches("EURUSD", known) == ["EURUSD.a"]


#+------------------------------------------------------------------+
#| The alias pass in matches() -- a trader's word, not a symbol      |
#+------------------------------------------------------------------+
def test_a_nickname_resolves_to_a_known_name() -> None:
    """``gold`` reaching ``XAUUSD`` is what makes the alias list worth having.

    The value returned is always a member of ``known``: a nickname is an
    input, never an output, or the picker would start sending "Gold".
    """
    assert symbols.resolve("gold", ["XAUUSD", "EURUSD"]) == "XAUUSD"
    assert symbols.resolve("cable", ["GBPUSD"]) == "GBPUSD"
    assert symbols.resolve("GOLD", ["XAUUSD"]) == "XAUUSD"


def test_an_exact_name_still_beats_a_nickname() -> None:
    """A broker with a literal ``GOLD`` symbol keeps it over ``XAUUSD``'s alias.

    Passes run cheapest-and-most-specific first, so adding the alias pass
    must not have demoted the exact one.
    """
    assert symbols.resolve("GOLD", ["XAUUSD", "GOLD"]) == "GOLD"
    assert symbols.resolve("gold", ["XAUUSD"]) == "XAUUSD"


def test_a_nickname_for_an_unknown_instrument_matches_nothing() -> None:
    """The alias table is a hint list, not a catalogue."""
    assert symbols.resolve("gold", ["EURUSD"]) is None
    assert symbols.matches("platinum", []) == []


def test_an_ambiguous_nickname_is_still_ambiguous() -> None:
    """Two charts for one trader word must not be chosen between."""
    # --- USOIL and XTIUSD are both "WTI" as far as a trader is concerned
    known = ["USOIL", "XTIUSD"]
    assert symbols.resolve("WTI", known) is None
    assert symbols.matches("WTI", known) == ["USOIL", "XTIUSD"]
