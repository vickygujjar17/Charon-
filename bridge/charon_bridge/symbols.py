"""Symbol naming: what the broker calls it, and what a human calls it.

A broker rarely names a pair the way a trader does. The same EURUSD may be
``EURUSD``, ``EURUSD.a``, ``EURUSD.pro``, ``EURUSDm``, ``mEURUSD`` or
``#EURUSD`` depending on the account. The dashboard has to display one
short, stable label -- and it must never *send* that label.

The rule this module exists to enforce:

    **Canonical form is for humans. Every command carries the exact
    broker name.**

Resolving ``"EURUSD"`` to the broker's ``"EURUSD.a"`` is useful for a
picker and a search box. Sending ``"EURUSD"`` to ``/api/order`` would
address a symbol the terminal may not have -- the EA compares the command's
``symbol`` against its own ``_Symbol`` and refuses a mismatch, so the
failure is at least loud. But the failure that matters is the quieter one:
a wrong-but-plausible name that resolves to a *different* live chart.

So :func:`resolve` only ever returns a name taken from ``known``, which is
built from what the EA is actually publishing plus what the terminal
actually lists. There is no code path here that invents a symbol.

Configuration
-------------
Broker affixes are deployment-specific, so they are configured rather than
guessed:

* ``CHARON_SYMBOL_PREFIX`` -- e.g. ``m`` for ``mEURUSD``
* ``CHARON_SYMBOL_SUFFIX`` -- e.g. ``.a`` or ``.pro`` for ``EURUSD.a``

Both are optional. With neither set, canonical form is just upper case.
Guessing them would be worse than useless: a ``.`` suffix rule would turn
``US30.cash`` into ``US30`` and then fail to match the real chart, and a
heuristic on ``#`` would silently mangle symbols that legitimately contain
it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Sequence

PREFIX_ENV = "CHARON_SYMBOL_PREFIX"
SUFFIX_ENV = "CHARON_SYMBOL_SUFFIX"

# --- where a candidate came from, so the UI can prefer a live chart over a
# --- merely-listed instrument without inventing a second lookup path.
SOURCE_CHART = "chart"
SOURCE_BROKER = "broker"


def _affixes() -> tuple[str, str]:
    """``(prefix, suffix)`` from the environment, read fresh each call.

    Read per call rather than cached at import: a cached value would make
    the setting untestable without reloading modules.
    """
    return (
        os.environ.get(PREFIX_ENV, "").strip(),
        os.environ.get(SUFFIX_ENV, "").strip(),
    )


def canonical(symbol: str) -> str:
    """The short, human-facing form of ``symbol``.

    Display only -- see the module docstring. Stripping is bounded so a
    name can never be reduced to nothing: a symbol that *is* the suffix
    (an instrument literally called ``.a``) keeps its own text.
    """
    text = symbol.strip()
    if not text:
        return ""

    prefix, suffix = _affixes()
    if prefix and len(text) > len(prefix) and text.casefold().startswith(prefix.casefold()):
        text = text[len(prefix):]
    if suffix and len(text) > len(suffix) and text.casefold().endswith(suffix.casefold()):
        text = text[: len(text) - len(suffix)]
    return text.upper()


# --- how a picker groups instruments. Advisory only: this is a display label,
# --- never a filter. A symbol whose class cannot be determined is ASSET_OTHER
# --- and is still offered -- a broker calls things the textbook does not.
ASSET_FOREX = "forex"
ASSET_METALS = "metals"
ASSET_INDEX = "indices"
ASSET_ENERGY = "energy"
ASSET_COMMODITIES = "commodities"
ASSET_CRYPTO = "crypto"
ASSET_STOCK = "stocks"
ASSET_OTHER = "other"

ASSET_CLASSES: tuple[str, ...] = (
    ASSET_FOREX,
    ASSET_METALS,
    ASSET_INDEX,
    ASSET_ENERGY,
    ASSET_COMMODITIES,
    ASSET_CRYPTO,
    ASSET_STOCK,
    ASSET_OTHER,
)

# --- token lists, matched against the canonical (suffix-stripped) name.
# --- Deliberately short and unambiguous: the goal is a useful heading on a
# --- picker, and a wrong guess here must never cost an operator a symbol.
_METAL_TOKENS = frozenset({
    "XAU", "XAG", "XPT", "XPD", "GOLD", "SILVER", "PLATINUM", "PALLADIUM",
    # --- v1.6.3: copper is a metal a retail broker lists, usually spelled out
    "COPPER",
})
# --- metals named by a two-letter ticker. Exact only, for the same reason as
# --- ``_ENERGY_EXACT``: a prefix test on ``HG`` would claim every ``HGD...``
# --- name, and a short ticker is exactly where a prefix rule goes wrong.
_METAL_EXACT = frozenset({"HG"})  # --- COMEX copper
_INDEX_TOKENS = frozenset({
    "US30", "US500", "US100", "NAS100", "SPX500", "GER30", "GER40", "UK100",
    "JP225", "AUS200", "FRA40", "HK50", "DE30", "DE40", "EU50", "US2000",
    "DJ30", "NDX", "SPX", "DAX", "FTSE", "NIKKEI",
})
_ENERGY_TOKENS = frozenset({
    "WTI", "BRENT", "USOIL", "UKOIL", "NGAS", "NATGAS", "GAS", "OIL",
    # --- brokers that name crude by its ticker rather than the word "oil"
    "XTI", "XBR", "CO1",
})
# --- tokens that are only ever the whole instrument name. ``CL`` is crude's
# --- ticker, but as a *prefix* it would read every ``CLP...`` symbol as energy,
# --- so it is matched exactly and never prefixed. ``RB`` is RBOB gasoline --
# --- the CME contract -- and is the same shape of problem.
_ENERGY_EXACT = frozenset({"CL", "RB"})
# --- softs and grains. Exact whole-token matches for the same reason: a prefix
# --- test would claim unrelated names that merely start with the same letters.
# --- v1.6.3 adds the exchange tickers, because a broker that lists ``ZC`` does
# --- not also give it the word "CORN" to match on. Every two-letter entry here
# --- is load-bearing as an *exact* match: ``LE`` would otherwise claim
# --- ``LEU...`` and ``HE`` would claim ``HEL...``.
_COMMODITY_TOKENS = frozenset({
    # --- spelled out
    "WHEAT", "CORN", "SOYBEAN", "SOY", "COFFEE", "COCOA", "SUGAR", "COTTON",
    "ORANGE", "RICE", "OATS", "LUMBER", "CATTLE", "HOG",
    # --- grains (CBOT)
    "ZC", "ZW", "ZS", "ZM", "ZL",
    # --- softs (ICE)
    "SB", "KC", "CT", "CC", "OJ",
    # --- livestock (CME)
    "LE", "GF", "HE",
})
_CRYPTO_TOKENS = frozenset({
    "BTC", "ETH", "XRP", "LTC", "BCH", "ADA", "SOL", "DOGE", "DOT", "AVAX",
    "LINK", "XLM", "TRX", "BNB", "MATIC",
})
# --- currencies that appear in a six-letter pair. Kept as a set of the ones a
# --- retail broker actually lists, plus the majors' crosses.
_CURRENCIES = frozenset({
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK",
    "DKK", "SGD", "HKD", "ZAR", "MXN", "TRY", "PLN", "CZK", "HUF", "CNH",
    "RMB", "INR", "RUB", "BRL", "THB", "KRW",
})

# --- what a trader calls the thing, keyed by the *canonical* name. This is the
# --- one piece of naming knowledge in this module that is not derived from the
# --- symbol itself, and it is deliberately written as data rather than as a
# --- heuristic: "cable" has no relationship to the letters G-B-P that a rule
# --- could discover, and guessing would put a label on the wrong instrument.
# ---
# --- What this table is for: search. An operator types what they say out loud,
# --- and "gold" having to become "XAUUSD" before the picker will find it is a
# --- lookup the client should not be doing itself. What it is **not** for: a
# --- command argument. Nothing here is ever sent -- see alias_for().
# ---
# --- Two keys may share a value on purpose. USOIL and XTIUSD are both "WTI" to
# --- a trader, which is exactly the ambiguity :func:`resolve` refuses to break
# --- by itself.
REFERENCE_ALIASES: dict[str, str] = {
    # --- the majors, by their desk nicknames
    "EURUSD": "Fiber",
    "GBPUSD": "Cable",
    "USDJPY": "Gopher",
    "AUDUSD": "Aussie",
    "USDCAD": "Loonie",
    "USDCHF": "Swissie",
    "NZDUSD": "Kiwi",
    # --- metals
    "XAUUSD": "Gold",
    "XAGUSD": "Silver",
    "XPTUSD": "Platinum",
    "XPDUSD": "Palladium",
    "HG": "Copper",
    # --- energy
    "CL": "Crude",
    "USOIL": "WTI",
    "XTIUSD": "WTI",
    "UKOIL": "Brent",
    "XBRUSD": "Brent",
    "NGAS": "Natural gas",
    "NATGAS": "Natural gas",
    # --- grains, softs and livestock, whose tickers say nothing to a newcomer
    "ZC": "Corn",
    "ZW": "Wheat",
    "ZS": "Soybean",
    "ZM": "Soybean meal",
    "ZL": "Soybean oil",
    "SB": "Sugar",
    "KC": "Coffee",
    "CT": "Cotton",
    "CC": "Cocoa",
    "OJ": "Orange juice",
    "LE": "Live cattle",
    "GF": "Feeder cattle",
    "HE": "Lean hogs",
}


def _alias_index() -> dict[str, tuple[str, ...]]:
    """:data:`REFERENCE_ALIASES` read backwards, casefolded for lookup.

    Built per call rather than cached at import for the same reason the affixes
    are: a module-level cache would be one more thing a test has to reset, and
    the table is small enough that rebuilding it costs nothing.
    """
    index: dict[str, list[str]] = {}
    for key, alias in REFERENCE_ALIASES.items():
        index.setdefault(alias.casefold(), []).append(key)
    return {alias: tuple(keys) for alias, keys in index.items()}


def alias_for(name: str) -> str:
    """The trader's word for ``name``, or ``""`` when there is none.

    Display and search only. **Never** a command argument: the module's rule is
    that a command carries the broker's exact name, and "Gold" is not a symbol
    any EA can compare against its own ``_Symbol``.

    Keyed on :func:`canonical`, so a deployment's ``XAUUSD.m`` still answers
    "Gold" without the table having to know about every suffix.
    """
    return REFERENCE_ALIASES.get(canonical(name), "")


# --- how long a broker tag may be before it stops looking like a tag. A
# --- suffix is a short deployment marker -- ``.a``, ``.pro``, ``.raw``,
# --- ``.cash``, ``#`` -- and the cap is what keeps this rule from reading a
# --- whole different instrument's name as one.
_TAG_MAX = 8
# --- the characters brokers actually introduce a tag with. Requiring a
# --- separator is the entire safety argument for the pass below: it is a
# --- *structural* claim, not a guess about a particular broker's convention.
_TAG_LEADS = "._-#"


def _broker_tag(text: str) -> str:
    """The trailing tag on ``text``, or ``""`` if it does not carry one.

    ``".a"``, ``".cash"`` and a bare ``"#"`` are tags. ``"PUSD"`` -- what
    follows ``CL`` in ``CLPUSD`` -- is **not**, because it does not start with
    a separator, and that single distinction is what stops this from filing
    the Chilean peso under crude.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in _TAG_LEADS:
        return ""
    if len(stripped) > _TAG_MAX:
        return ""
    # --- a bare separator is still a tag (``EURUSD#`` is a real listing); a
    # --- separator plus *anything that is not a short word* is not.
    rest = stripped[1:]
    if rest and not rest.isalnum():
        return ""
    return stripped


def _suffixed_matches(hint: str, names: Sequence[str]) -> list[str]:
    """Known names that are ``hint`` followed by a punctuation-anchored tag.

    The gap this closes: :func:`canonical` deliberately strips **nothing**
    unless ``CHARON_SYMBOL_SUFFIX`` says what to strip, because a blanket
    "everything after a dot" rule turns ``US30.cash`` into ``US30`` and then
    fails to match the real chart. That is right for a display label, and
    useless for a lookup -- an unconfigured deployment can then never resolve
    "EURUSD" to "EURUSD.a" at all.

    So the rule is inverted here. Instead of stripping a *guessed* affix off
    the hint, each known name is asked whether it is the hint plus something
    that is structurally a broker tag. Nothing is stripped from anything, the
    answer is still a member of ``names``, and two candidates stay ambiguous.
    """
    if len(hint) < 2:
        return []
    lowered = hint.casefold()
    found: list[str] = []
    for name in names:
        folded = name.casefold()
        if folded == lowered or not folded.startswith(lowered):
            continue
        if _broker_tag(folded[len(lowered):]):
            found.append(name)
    return found


def asset_class(name: str) -> str:
    """A best-effort instrument class for ``name``, for grouping only.

    The rule that must not be relaxed: **this never rejects or rewrites a
    symbol.** Everything it cannot classify comes back as
    :data:`ASSET_OTHER`, which the picker still shows under its own heading.
    A broker suffix (``.a``, ``m``, ``#``) is already stripped by
    :func:`canonical`, so ``XAUUSD.a`` classifies like ``XAUUSD``.

    Detection order matters. Metals, indices, energy, crypto and commodities
    are matched by token because those names are distinctive; forex is matched
    structurally (two known currencies in a row) and only afterwards, so
    ``USDJPY`` is forex while ``USOIL`` is not.
    """
    text = canonical(name)
    if not text:
        return ASSET_OTHER

    # --- split on the usual separators so BTCUSD, BTC/USD and BTC-USD all
    # --- reduce to the same tokens.
    tokens = [token for token in text.replace("-", "/").replace("_", "/").replace(".", "/").split("/") if token]
    if len(tokens) > 1:
        for token in tokens:
            found = _classify_token(token)
            if found != ASSET_OTHER:
                return found

    found = _classify_token(text)
    return found


def _classify_token(token: str) -> str:
    """Classify one already-uppercased token.

    Prefix matching is used where the instrument's name *starts* with the
    marker (``BTCUSD``, ``XAUUSD``, ``WTIUSD``); exact matching is used for
    index and commodity names, where a prefix test would collide with
    unrelated symbols. Structural forex comes last so ``USOIL`` is never read
    as a currency pair.
    """
    if not token:
        return ASSET_OTHER
    if token in _METAL_EXACT:
        return ASSET_METALS
    for prefix in _METAL_TOKENS:
        if token.startswith(prefix):
            return ASSET_METALS
    for prefix in _ENERGY_TOKENS:
        if token.startswith(prefix):
            return ASSET_ENERGY
    if token in _ENERGY_EXACT:
        return ASSET_ENERGY
    for prefix in _CRYPTO_TOKENS:
        if token.startswith(prefix):
            return ASSET_CRYPTO
    if token in _INDEX_TOKENS:
        return ASSET_INDEX
    # --- softs and grains, before the structural pair test. ``CORN`` is not a
    # --- pair, but ordering it here keeps every commodity in one place rather
    # --- than scattered either side of the forex rule.
    if token in _COMMODITY_TOKENS:
        return ASSET_COMMODITIES
    if len(token) == 6 and token.isalpha():
        if token[:3] in _CURRENCIES and token[3:] in _CURRENCIES:
            return ASSET_FOREX
    return ASSET_OTHER


@dataclass(frozen=True)
class Entry:
    """One instrument, as offered to the picker."""

    name: str
    """The exact name to put in a command. Never canonicalised."""

    canonical: str
    """The label to show. Never sent."""

    sources: tuple[str, ...]
    """``SOURCE_CHART`` and/or ``SOURCE_BROKER``, chart first."""

    alias: str = ""
    """The trader's word for it, or ``""``. Display and search only.

    Defaulted rather than required so every existing construction -- including
    the ones in tests -- keeps meaning what it said, and an instrument with no
    nickname is stated as an empty string rather than a guess.
    """

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.name,
            "label": self.canonical,
            "sources": list(self.sources),
            # --- a heading for the picker, never a filter. See asset_class().
            "asset_class": asset_class(self.name),
            # --- v1.6.3: what a trader calls it, so "gold" finds XAUUSD. The
            # --- client adds this to its search text and shows it beside the
            # --- label; it is never posted. See alias_for().
            "alias": self.alias,
        }


def _unique(names: Iterable[str]) -> list[str]:
    """Drop blanks and case-insensitive duplicates, keeping first spelling.

    The broker's list is authoritative, so two spellings that differ only by
    case are the same instrument; listing both would make every picker entry
    ambiguous for :func:`resolve`.
    """
    seen: set[str] = set()
    found: list[str] = []
    for name in names:
        text = name.strip() if isinstance(name, str) else ""
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(text)
    return found


def catalogue(
    chart: Iterable[str] = (),
    broker: Iterable[str] = (),
) -> list[Entry]:
    """Merge the two sources into one list, chart names first.

    Order is deliberate: charts the EA is already publishing are the ones a
    trader can act on *right now*, so they lead. A broker-listed symbol with
    no chart behind it is still offered -- that is how you discover what to
    attach Charon to next -- but it sorts after the live ones.

    Merging is by exact name and ``sources`` accumulates, so a symbol that is
    both live and listed reports both rather than appearing twice.
    """
    order: list[str] = []
    sources: dict[str, list[str]] = {}
    for source, names in ((SOURCE_CHART, chart), (SOURCE_BROKER, broker)):
        for name in _unique(names):
            if name not in sources:
                sources[name] = []
                order.append(name)
            if source not in sources[name]:
                sources[name].append(source)

    return [
        Entry(
            name=name,
            canonical=canonical(name),
            sources=tuple(sources[name]),
            alias=alias_for(name),
        )
        for name in order
    ]


def matches(hint: str, known: Sequence[str]) -> list[str]:
    """Every known name ``hint`` could mean, most specific first.

    Four passes, stopping at the first that finds anything:

    1. exact match -- the operator pasted the broker's own name;
    2. case-insensitive exact -- ``eurusd.a`` for ``EURUSD.a``;
    3. canonical match -- ``EURUSD`` for ``EURUSD.a``;
    4. a punctuation-anchored broker tag -- ``EURUSD`` for ``EURUSD.a`` even
       when ``CHARON_SYMBOL_SUFFIX`` is unset (v1.6.3);
    5. nickname match -- ``gold`` for ``XAUUSD`` (v1.6.3).

    Each pass is scored against ``known`` only, so an unknown hint yields an
    empty list and an ambiguous one yields several names. That is precisely
    the information the caller needs in order to ask a better question
    instead of picking one at random.

    The order is the rule that keeps this safe. A broker that really lists a
    symbol called ``GOLD`` answers with it, and the nickname on ``XAUUSD`` is
    only consulted when no such symbol exists -- so learning a nickname can
    never displace a real instrument.

    The nickname pass reads ``known`` **through** :data:`REFERENCE_ALIASES`
    rather than the other way round: the table says which names are called
    what, and the answer is still always a member of ``known``. There is no
    path here that returns a nickname.
    """
    wanted = hint.strip() if isinstance(hint, str) else ""
    if not wanted:
        return []

    names = _unique(known)
    exact = [name for name in names if name == wanted]
    if exact:
        return exact

    folded = [name for name in names if name.casefold() == wanted.casefold()]
    if folded:
        return folded

    target = canonical(wanted)
    if not target:
        return []
    canonical_hits = [name for name in names if canonical(name) == target]
    if canonical_hits:
        return canonical_hits

    # --- an unconfigured deployment still gets ``EURUSD`` -> ``EURUSD.a``,
    # --- because the tag is recognised *on the known name* rather than
    # --- guessed off the hint. See _suffixed_matches.
    suffixed = _suffixed_matches(wanted, names)
    if suffixed:
        return suffixed

    # --- the nickname pass. ``known`` first, so two charts nicknamed the same
    # --- thing both come back and the caller can see the ambiguity.
    return [
        name
        for name in names
        if alias_for(name).casefold() == target.casefold()
    ]


def resolve(hint: str, known: Sequence[str]) -> str | None:
    """The one known name ``hint`` unambiguously refers to, or ``None``.

    ``None`` covers both "nothing matched" and "more than one thing
    matched", which are genuinely different answers. Callers that must tell
    them apart should use :func:`matches` and inspect its length; this
    convenience form exists for the common case where either outcome means
    the same thing -- do not proceed.
    """
    found = matches(hint, known)
    return found[0] if len(found) == 1 else None


__all__ = [
    "ASSET_CLASSES",
    "ASSET_COMMODITIES",
    "ASSET_CRYPTO",
    "ASSET_ENERGY",
    "ASSET_FOREX",
    "ASSET_INDEX",
    "ASSET_METALS",
    "ASSET_OTHER",
    "ASSET_STOCK",
    "PREFIX_ENV",
    "REFERENCE_ALIASES",
    "SOURCE_BROKER",
    "SOURCE_CHART",
    "SUFFIX_ENV",
    "Entry",
    "alias_for",
    "asset_class",
    "canonical",
    "catalogue",
    "matches",
    "resolve",
]

