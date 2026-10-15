"""Turning an instrument_id into something a data provider recognises.

BRKB is BRK-B at Yahoo. Fetching "BRKB" returns either nothing or some
unrelated listing, and a wrong sector is worse than a missing one because
nothing downstream can tell it apart from a right one. So resolution is
explicit, and anything unresolvable is named rather than guessed at.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src import market


def _instruments(rows):
    cols = ["instrument_id", "name", "instrument_type", "price_ticker", "av_ticker"]
    return pd.DataFrame(rows, columns=cols)


def test_price_ticker_wins_when_present():
    df = _instruments([("BRKB", "Berkshire B", "stock", "BRK-B", "BRK.B")])
    assert market.provider_ticker("BRKB", df) == "BRK-B"


def test_av_ticker_is_the_fallback():
    df = _instruments([("X", "X", "stock", None, "X.TO")])
    assert market.provider_ticker("X", df) == "X.TO"


def test_the_id_itself_is_the_last_resort():
    df = _instruments([("ASML", "ASML", "stock", None, None)])
    assert market.provider_ticker("ASML", df) == "ASML"


def test_a_foreign_listing_id_is_already_a_provider_ticker():
    """005930.KS carries its own exchange suffix and needs no mapping."""
    df = _instruments([("005930.KS", "Samsung", "stock", None, None)])
    assert market.provider_ticker("005930.KS", df) == "005930.KS"


@pytest.mark.parametrize("kind", ["cash", "fx"])
def test_cash_and_fx_resolve_to_nothing(kind):
    """There is no market data for a currency balance. Not a hole — a non-question."""
    df = _instruments([("USD_CASH", "US Dollar", kind, None, None)])
    assert market.provider_ticker("USD_CASH", df) is None


def test_an_unknown_instrument_resolves_to_nothing():
    assert market.provider_ticker("NOPE", _instruments([])) is None


def test_unresolvable_names_only_the_ones_worth_fixing():
    """Cash is excluded; a real holding with an unusable id is reported."""
    df = _instruments([
        ("USD_CASH", "US Dollar", "cash", None, None),
        ("BRKB", "Berkshire B", "stock", None, None),
        ("ASML", "ASML", "stock", None, None),
    ])
    out = market.unresolvable(df)
    assert list(out["instrument_id"]) == ["BRKB"]
    assert "BRK-B" in out.iloc[0]["why"] or "dot or dash" in out.iloc[0]["why"]


def _holdings(rows):
    cols = ["ticker", "holding", "holding_name", "weight", "coverage",
            "source", "as_of", "as_of_kind"]
    return pd.DataFrame(rows, columns=cols)


def test_a_valid_holdings_frame_validates():
    df = _holdings([("QQQ", "NVDA", "NVIDIA Corp", 0.085, 0.49, "Yahoo", "2026-10-03", "read")])
    assert len(market.holdings_schema().validate(df)) == 1


def test_a_weight_over_one_is_refused():
    df = _holdings([("QQQ", "NVDA", "NVIDIA Corp", 1.4, 0.49, "Yahoo", "2026-10-03", "read")])
    with pytest.raises(Exception):
        market.holdings_schema().validate(df)


def test_a_row_with_no_as_of_is_refused():
    """Every fetched value carries the day it was fetched. No exceptions."""
    df = _holdings([("QQQ", "NVDA", "NVIDIA Corp", 0.085, 0.49, "Yahoo", "", "read")])
    with pytest.raises(Exception):
        market.holdings_schema().validate(df)


def test_a_date_that_never_happened_is_refused():
    """A regex matches 2026-02-30 and no reader can ever resolve it.

    `etf_compositions.as_of` has always used `claims._is_real_date` for this;
    its sibling here was matching a shape instead, so the one column that
    says how old the holdings are could hold a day that does not exist.
    """
    df = _holdings([("QQQ", "NVDA", "NVIDIA Corp", 0.085, 0.49, "Yahoo",
                     "2026-02-30", "read")])
    with pytest.raises(Exception, match="as_of"):
        market.holdings_schema().validate(df)


def test_the_same_holding_cannot_appear_twice_in_one_fund():
    """A duplicate row double-counts that company in every exposure number."""
    df = _holdings([
        ("QQQ", "NVDA", "NVIDIA Corp", 0.085, 0.49, "Yahoo", "2026-10-03", "read"),
        ("QQQ", "NVDA", "NVIDIA Corp", 0.074, 0.49, "Yahoo", "2026-10-03", "read"),
    ])
    with pytest.raises(Exception):
        market.holdings_schema().validate(df)


def test_coverage_is_recorded_so_nobody_mistakes_ten_holdings_for_a_whole_fund():
    df = market.load_holdings(market.HOLDINGS_PATH)
    assert "coverage" in df.columns


def test_a_row_must_say_whether_its_date_was_published_or_only_read():
    """`as_of` means two different things depending on the source.

    SPDR states the day its holdings were true for. Yahoo states nothing, so
    the date on those rows is only the day we looked. Without this column the
    two are the same ISO string, and the stalest rows in the table sort as
    the freshest.
    """
    df = _holdings([("QQQ", "NVDA", "NVIDIA Corp", 0.085, 0.49, "Yahoo",
                     "2026-10-03", "guessed")])
    with pytest.raises(Exception):
        market.holdings_schema().validate(df)


def test_as_of_kind_cannot_be_left_blank():
    """A blank would be a third state meaning "nobody recorded which".

    An unknown provenance has to be assumed the worse of the two, not left
    for a reader to guess from the source prose.
    """
    df = _holdings([("QQQ", "NVDA", "NVIDIA Corp", 0.085, 0.49, "Yahoo",
                     "2026-10-03", "")])
    with pytest.raises(Exception):
        market.holdings_schema().validate(df)


# ─── one company, two spellings ───

def _book():
    """His book: Samsung carries its exchange suffix, Berkshire does not."""
    return pd.DataFrame({
        "instrument_id": ["005930.KS", "BRKB", "NVDA", "SGOV"],
        "name": ["Samsung Electronics Co., Ltd.", "Berkshire Hathaway B",
                 "NVIDIA Corp", "iShares 0-3 Month Treasury"],
        "instrument_type": ["stock", "stock", "stock", "etf"],
        "price_ticker": ["", "BRK-B", "", ""],
        "av_ticker": ["", "", "NVDA", ""],
    })


def test_an_issuers_spelling_of_a_foreign_listing_finds_his_instrument():
    """DRAM and EEM hold `005930 KS`; his book holds `005930.KS`.

    Unjoined, his real Samsung exposure reads $1,240 short — what he bought
    on purpose and what his funds bought for him, counted as two companies.
    """
    assert market.holding_instrument("005930 KS", _book()) == "005930.KS"


def test_his_own_spelling_still_resolves_to_itself():
    assert market.holding_instrument("005930.KS", _book()) == "005930.KS"


def test_a_providers_share_class_resolves_through_price_ticker():
    """`BRK-B` is what a provider writes; `BRKB` is the id it points at."""
    assert market.holding_instrument("BRK-B", _book()) == "BRKB"


def test_a_company_he_does_not_hold_resolves_to_nothing():
    """Most ETF holdings are companies he owns no position in."""
    assert market.holding_instrument("OKTA", _book()) is None


def test_an_unknown_exchange_code_fails_to_join_rather_than_guessing_wrong():
    """A code not in the table must not invent an instrument."""
    assert market.holding_instrument("005930 ZZ", _book()) is None


def test_a_right_code_on_a_company_he_does_not_hold_still_returns_nothing():
    """The guarantee: only ever an id already in his book."""
    assert market.holding_instrument("BOL SS", _book()) is None


@pytest.mark.parametrize("symbol", ["", "   ", "nan"])
def test_an_empty_symbol_is_not_an_instrument(symbol):
    assert market.holding_instrument(symbol, _book()) is None


def test_a_bare_root_finds_the_one_instrument_that_carries_it():
    """iShares names the exchange in a column, not in the ticker: `005930`."""
    assert market.holding_instrument("005930", _book()) == "005930.KS"


def test_a_root_listed_twice_is_left_unjoined_rather_than_guessed():
    """Two listings of one root are a real choice — not one to make here."""
    book = pd.concat([_book(), pd.DataFrame({
        "instrument_id": ["005930.KQ"], "name": ["Samsung, other listing"],
        "instrument_type": ["stock"], "price_ticker": [""], "av_ticker": [""]})])
    assert market.holding_instrument("005930", book) is None


def test_a_currency_pair_does_not_swallow_a_holding_named_for_its_currency():
    """`EUR.USD` is an FX row; a fund's `EUR` cash line is not that."""
    book = pd.concat([_book(), pd.DataFrame({
        "instrument_id": ["EUR.USD"], "name": ["Euro / US Dollar"],
        "instrument_type": ["fx"], "price_ticker": [""], "av_ticker": [""]})])
    assert market.holding_instrument("EUR", book) is None


def test_a_preferred_share_class_stays_its_own_company():
    """`005935` is Samsung's preferred line — a different security."""
    assert market.holding_instrument("005935", _book()) is None


# ─── a share class the issuer writes with a separator ───

def test_a_dot_separated_share_class_finds_his_instrument():
    """XLF's largest holding is `BRK.B`; his book says `BRKB`.

    Unjoined, his Berkshire read $1,940 when $2,210 of it is his — the
    biggest single line of a fund he owns, missing entirely.
    """
    assert market.holding_instrument("BRK.B", _book()) == "BRKB"


def test_a_dash_separated_share_class_finds_it_too():
    assert market.holding_instrument("BRK-B", _book()) == "BRKB"


def test_squashing_does_not_strip_an_exchange_suffix_into_a_collision():
    """`005930.KS` must not squash into something that matches a US ticker."""
    book = pd.concat([_book(), pd.DataFrame({
        "instrument_id": ["005930KS"], "name": ["Decoy"],
        "instrument_type": ["stock"], "price_ticker": [""], "av_ticker": [""]})])
    # Two instruments now squash to the same thing, so it refuses rather than
    # picking one.
    assert market.holding_instrument("005930.KS", book) == "005930.KS"


# ─── blanks must never resolve to the first row ───

def _book_as_pandas_2_renders_it():
    """Blanks exactly as pandas 2's `astype(str)` leaves them: "nan".

    `src/instruments.py` reads with `na_values=[""]`, so every blank ticker
    is NaN. pandas 2 renders that as the literal string "nan"; pandas 3
    preserves NA. The repo allows both (`requirements.txt`: pandas>=2.2), so
    the string form is written out here — a test that only ever sees the
    installed version's behaviour proves nothing about the other one.
    """
    book = _book()
    book["price_ticker"] = ["nan", "BRK-B", "nan", "nan"]
    book["av_ticker"] = ["nan", "nan", "NVDA", "nan"]
    return book


@pytest.mark.parametrize("symbol", ["nan", "None", float("nan"), None, ""])
def test_a_missing_symbol_never_resolves_to_the_first_blank_row(symbol):
    """Unguarded, every blank-ticker row joins to one instrument.

    A caller handing this an issuer file's empty footer cell would get a
    fabricated position in whichever company happens to sit first in
    instruments.csv — on his real book, a stock he closed in August.
    """
    assert market.holding_instrument(symbol, _book_as_pandas_2_renders_it()) \
        is None


def test_a_real_symbol_still_resolves_beside_those_blanks():
    book = _book_as_pandas_2_renders_it()
    assert market.holding_instrument("BRK-B", book) == "BRKB"
    assert market.holding_instrument("005930 KS", book) == "005930.KS"


# ─── the bare-root branch is numeric-only, and that is its safety ───

def test_a_lettered_us_ticker_never_joins_a_foreign_listing():
    """The branch consults no exchange, so letters would be a false join.

    XLP holds `EL` (Estée Lauder). The day he files EssilorLuxottica as
    `EL.PA`, a lettered bare-root rule would credit him $22 of a company he
    does not own and delete one he does.
    """
    book = pd.concat([_book(), pd.DataFrame({
        "instrument_id": ["EL.PA"], "name": ["EssilorLuxottica"],
        "instrument_type": ["stock"], "price_ticker": [""], "av_ticker": [""]})])
    assert market.holding_instrument("EL", book) is None


def test_a_bare_numeric_code_still_joins():
    """The case the branch exists for: iShares' Samsung."""
    assert market.holding_instrument("005930", _book()) == "005930.KS"
