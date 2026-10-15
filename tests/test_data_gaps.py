"""Every hole in the market data, biggest money first.

The ordering is the point. A missing breakdown on a fund holding a lot of
money matters; the same hole on a position of a few hundred dollars does not,
and a report that lists them alphabetically makes the two look identical.

`ops/` is a directory of standalone scripts, not a package — adding
`ops/__init__.py` would change how every script in it resolves its own
imports — so `data_gaps.py` is loaded the way
`tests/test_refresh_etf_compositions.py` already loads a script under test:
by file path, with `importlib.util.spec_from_file_location`.

Run from project root:
    venv/bin/python -m pytest tests/test_data_gaps.py -v
"""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pandas as pd
import pytest
from datetime import date

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "data_gaps.py"


def _load():
    spec = importlib.util.spec_from_file_location("data_gaps", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def data_gaps():
    return _load()


# ─── fixture builders, following tests/test_exposure.py's style ───

from src import exposure, market


def _positions(tmp_path, rows):
    p = tmp_path / "positions.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _compositions(tmp_path, rows):
    p = tmp_path / "etf_compositions.csv"
    pd.DataFrame(rows, columns=exposure.COLUMNS).to_csv(
        p, index=False, quoting=csv.QUOTE_NONNUMERIC
    )
    return p


def _holdings(tmp_path, rows):
    p = tmp_path / "etf_holdings.csv"
    pd.DataFrame(rows, columns=market.HOLDINGS_COLUMNS).to_csv(
        p, index=False, quoting=csv.QUOTE_NONNUMERIC
    )
    return p


# Every column `gaps()` or something it calls might read off a row, blanked
# by default so a test only has to say what it cares about — the same reason
# instruments.load() always returns every column, never a subset.
_INSTRUMENT_DEFAULTS = {
    "instrument_id": "", "name": "", "instrument_type": "stock", "issuer": "",
    "sector": "", "cluster_id": "", "provider_sector": "",
    "provider_sector_as_of": "", "provider_sector_source": "",
    "sector_settled": "", "isin": "",
    "av_ticker": "", "price_ticker": "", "note_ref": "", "status": "active",
    "added": "2026-01-01",
}


def _instruments(monkeypatch, rows):
    from src import instruments
    full = [{**_INSTRUMENT_DEFAULTS, **r} for r in rows]
    # `columns=` explicitly, so an empty `rows` still produces every column
    # instruments.load() always carries — a bare `pd.DataFrame([])` has NO
    # columns at all, and exposure.unknown_holdings() (which gaps() now
    # calls) reads `instrument_id` off it unconditionally.
    frame = pd.DataFrame(full, columns=list(_INSTRUMENT_DEFAULTS.keys()))
    monkeypatch.setattr(instruments, "load", lambda *a, **k: frame)


_FULL_HOLDING_ROW = {"holding": "ABC", "holding_name": "Some Co", "weight": 0.5,
                    "coverage": 0.5, "source": "Yahoo top holdings",
                    "as_of": "2026-10-01", "as_of_kind": "read"}


def _one_composition_row(ticker, *, as_of, source_quality="vendor", weight=1.0):
    """A single-sector breakdown row. `source_quality="vendor"` by default —
    NOT "issuer" — so an ordinary undated fixture does not accidentally match
    `_is_definitional()`'s single-row/issuer/~100% shape and get suppressed;
    tests of that suppression build their own rows explicitly.
    """
    return {"ticker": ticker, "sector": "Tech - Software", "weight": weight,
            "top_holding": "", "source": source_quality, "source_quality": source_quality,
            "as_of": as_of}


# ─── the six required behaviours ───

def test_a_held_etf_with_no_breakdown_is_a_gap(tmp_path, monkeypatch, data_gaps):
    pp = _positions(tmp_path, [{"instrument_id": "XLK", "market_value": 1000.0}])
    cp = _compositions(tmp_path, [])                     # no row for XLK at all
    hp = _holdings(tmp_path, [{"ticker": "XLK", **_FULL_HOLDING_ROW}])
    _instruments(monkeypatch, [
        {"instrument_id": "XLK", "instrument_type": "etf"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[g["subject"] == "XLK"]
    assert (row["kind"] == "no sector breakdown").any()
    assert (row["market_value"] == 1000.0).any()


def test_a_held_etf_with_an_undated_breakdown_is_a_gap(tmp_path, monkeypatch, data_gaps):
    """Undated is not the same as missing, and both are reported."""
    pp = _positions(tmp_path, [{"instrument_id": "XLK", "market_value": 1000.0}])
    cp = _compositions(tmp_path, [_one_composition_row("XLK", as_of="")])
    hp = _holdings(tmp_path, [{"ticker": "XLK", **_FULL_HOLDING_ROW}])
    _instruments(monkeypatch, [
        {"instrument_id": "XLK", "instrument_type": "etf"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[g["subject"] == "XLK"]
    assert (row["kind"] == "undated sector breakdown").any()
    # and NOT also reported as missing — it has a row, just no date
    assert not (row["kind"] == "no sector breakdown").any()


def test_a_held_stock_with_no_vendor_sector_is_a_gap(tmp_path, monkeypatch, data_gaps):
    pp = _positions(tmp_path, [{"instrument_id": "ABCD", "market_value": 500.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "ABCD", "instrument_type": "stock",
         "sector": "Tech - Software", "provider_sector": "", "provider_sector_as_of": ""},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[g["subject"] == "ABCD"]
    assert (row["kind"] == "no dated vendor sector").any()
    assert (row["market_value"] == 500.0).any()


def test_cash_is_never_a_gap(tmp_path, monkeypatch, data_gaps):
    """A currency balance has no sector and no breakdown. Not a hole."""
    pp = _positions(tmp_path, [
        {"instrument_id": "USD_CASH", "market_value": 5000.0},
        {"instrument_id": "XLK", "market_value": 1000.0},   # a real gap, for contrast
    ])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "USD_CASH", "instrument_type": "cash", "sector": "Cash / FX"},
        {"instrument_id": "XLK", "instrument_type": "etf"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    assert "USD_CASH" not in set(g["subject"])
    assert "XLK" in set(g["subject"]), "the fixture must still be capable of finding a gap"


def test_gaps_are_ordered_by_money(tmp_path, monkeypatch, data_gaps):
    """Instrument ids chosen so alphabetical order CONTRADICTS money order:
    `_held_market_values()` groups by instrument_id, which sorts the index
    alphabetically ascending (AAA, MMM, ZZZ) — the opposite of the money-
    descending order the test checks for. A fixture named BIG/MID/SMALL would
    pass even with the sort deleted, because "B" < "M" < "S" already happens
    to land in money-descending order by coincidence; this fixture does not
    let that happen.
    """
    pp = _positions(tmp_path, [
        {"instrument_id": "AAA_small", "market_value": 300.0},
        {"instrument_id": "ZZZ_big", "market_value": 9000.0},
        {"instrument_id": "MMM_mid", "market_value": 2000.0},
    ])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "AAA_small", "instrument_type": "stock", "sector": "Tech - Software"},
        {"instrument_id": "ZZZ_big", "instrument_type": "stock", "sector": "Tech - Software"},
        {"instrument_id": "MMM_mid", "instrument_type": "stock", "sector": "Tech - Software"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    assert list(g["subject"]) == ["ZZZ_big", "MMM_mid", "AAA_small"]
    assert list(g["market_value"]) == sorted(g["market_value"], reverse=True)


def test_a_short_position_sorts_by_size_not_sign(tmp_path, monkeypatch, data_gaps):
    """A short is still money at risk. `-$9,000` must outrank `$300`, and the
    printed figure must keep its sign rather than being folded into abs()."""
    pp = _positions(tmp_path, [
        {"instrument_id": "TINYLONG", "market_value": 300.0},
        {"instrument_id": "BIGSHORT", "market_value": -9000.0},
    ])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "TINYLONG", "instrument_type": "stock", "sector": "Tech - Software"},
        {"instrument_id": "BIGSHORT", "instrument_type": "short", "sector": "Tech - Software"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    assert list(g["subject"]) == ["BIGSHORT", "TINYLONG"]
    assert g.loc[g["subject"] == "BIGSHORT", "market_value"].iloc[0] == -9000.0


def test_a_clean_book_reports_nothing(tmp_path, monkeypatch, data_gaps):
    """The report must be capable of being empty, or it is noise."""
    pp = _positions(tmp_path, [
        {"instrument_id": "USD_CASH", "market_value": 5000.0},
        {"instrument_id": "XLK", "market_value": 1000.0},
        {"instrument_id": "NVDA", "market_value": 500.0},
    ])
    cp = _compositions(tmp_path, [_one_composition_row("XLK", as_of="2026-10-01")])
    hp = _holdings(tmp_path, [{"ticker": "XLK", **_FULL_HOLDING_ROW}])
    _instruments(monkeypatch, [
        {"instrument_id": "USD_CASH", "instrument_type": "cash", "sector": "Cash / FX"},
        {"instrument_id": "XLK", "instrument_type": "etf"},
        {"instrument_id": "NVDA", "instrument_type": "stock",
         "sector": "Tech - Semiconductors", "provider_sector": "Technology",
         "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    assert g.empty
    assert list(g.columns) == ["kind", "subject", "market_value", "detail"]


# ─── the big one: a lot with no instrument row at all ───

def test_a_lot_with_no_instrument_row_is_a_gap(tmp_path, monkeypatch, data_gaps):
    """`src/ibkr_sync.py:237` writes exactly this shape for every freshly
    bought ticker — a positions.csv row with no instruments.csv entry yet
    ("AUTO-ADDED by ibkr_sync — add it to instruments.csv"). Every other
    check in this module needs an instrument row to even run, so silently
    skipping these let the report say "no gaps" while real, uncategorised
    money sat completely unchecked — the one failure this report exists to
    prevent, happening to the newest and least-understood position in the
    book.
    """
    pp = _positions(tmp_path, [{"instrument_id": "NEWCO", "market_value": 12000.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [])          # instruments.csv has never heard of NEWCO

    g = data_gaps.gaps(cp, hp, pp)
    assert not g.empty, "must not report nothing while $12,000 is unaccounted for"
    row = g[g["subject"] == "NEWCO"]
    assert (row["kind"] == "no instrument row").any()
    assert (row["market_value"] == 12000.0).any()


# ─── kinds that worked but were never asserted ───

def test_a_held_etf_with_no_company_holdings_is_a_gap(tmp_path, monkeypatch, data_gaps):
    pp = _positions(tmp_path, [{"instrument_id": "XLK", "market_value": 1000.0}])
    cp = _compositions(tmp_path, [_one_composition_row("XLK", as_of="2026-10-01")])
    hp = _holdings(tmp_path, [])            # no company-level rows at all
    _instruments(monkeypatch, [
        {"instrument_id": "XLK", "instrument_type": "etf"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[(g["subject"] == "XLK") & (g["kind"] == "no company holdings")]
    assert len(row) == 1
    assert row.iloc[0]["market_value"] == 1000.0
    assert "etf_holdings.csv" in row.iloc[0]["detail"]


def test_holdings_thinner_than_the_breakdown_are_a_gap(tmp_path, monkeypatch,
                                                       data_gaps):
    """Two tables in data/market/ describing one fund and disagreeing.

    MAGS is the real case: etf_compositions.top_holding names all seven of
    the Magnificent Seven at about a seventh each, while etf_holdings.csv
    held a single Yahoo row. The thin table is the one look-through company
    exposure reads, so the floor implied about 0.02% of NAV in META against
    a real figure near 0.71%.
    """
    names = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA"]
    pp = _positions(tmp_path, [{"instrument_id": "MAGS", "market_value": 7259.0}])
    cp = _compositions(tmp_path, [
        {"ticker": "MAGS", "sector": "Tech - Software", "weight": w,
         "top_holding": n, "source": "Roundhill equal-weight",
         "source_quality": "issuer", "as_of": "2026-10-01"}
        for n, w in zip(names, [0.1429] * 6 + [0.1426])])
    hp = _holdings(tmp_path, [{**_FULL_HOLDING_ROW, "ticker": "MAGS",
                               "holding": "MSFT", "weight": 0.069,
                               "coverage": 0.192}])
    _instruments(monkeypatch, [{"instrument_id": "MAGS", "instrument_type": "etf"}])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[(g["subject"] == "MAGS")
            & (g["kind"] == "holdings thinner than the breakdown")]
    assert len(row) == 1
    assert "1 company" in row.iloc[0]["detail"]
    assert "names 7" in row.iloc[0]["detail"]


def test_a_broad_fund_names_no_companies_so_it_can_never_be_thin(
    tmp_path, monkeypatch, data_gaps
):
    """`top_holding` is blank for every broad fund, so this check binds only
    where the breakdown already knows the company names. A ten-row Yahoo
    floor on QQQ is the expected answer, not a gap."""
    pp = _positions(tmp_path, [{"instrument_id": "QQQ", "market_value": 1000.0}])
    cp = _compositions(tmp_path, [_one_composition_row("QQQ", as_of="2026-10-01")])
    hp = _holdings(tmp_path, [{**_FULL_HOLDING_ROW, "ticker": "QQQ"}])
    _instruments(monkeypatch, [{"instrument_id": "QQQ", "instrument_type": "etf"}])

    g = data_gaps.gaps(cp, hp, pp)
    assert "holdings thinner than the breakdown" not in set(g["kind"])


def test_a_misspelt_issuer_that_misses_its_downloader_is_a_gap(
    tmp_path, monkeypatch, data_gaps
):
    """`instruments.issuer` is free text AND the key into ISSUER_DOWNLOADERS.

    The lookup is an exact dict hit, so one wrong character on XLK stops it
    being fetched from SPDR and drops it to a thin Yahoo floor — no error,
    no warning, and the only visible difference is a worse answer.
    """
    pp = _positions(tmp_path, [{"instrument_id": "XLK", "market_value": 9000.0}])
    cp = _compositions(tmp_path, [_one_composition_row("XLK", as_of="2026-10-01")])
    hp = _holdings(tmp_path, [{**_FULL_HOLDING_ROW, "ticker": "XLK"}])
    _instruments(monkeypatch, [{"instrument_id": "XLK", "instrument_type": "etf",
                                "issuer": "Spdr"}])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[(g["subject"] == "XLK")
            & (g["kind"] == "issuer spelling misses its downloader")]
    assert len(row) == 1
    assert "'SPDR'" in row.iloc[0]["detail"]


def test_an_issuer_with_no_downloader_at_all_is_not_a_gap(
    tmp_path, monkeypatch, data_gaps
):
    """iShares misses the dict too, and that is honest — no downloader is
    written for it. Reporting every such fund would be a queue that can
    never reach zero, the failure instruments.gaps() was fixed for."""
    pp = _positions(tmp_path, [{"instrument_id": "IWM", "market_value": 9000.0}])
    cp = _compositions(tmp_path, [_one_composition_row("IWM", as_of="2026-10-01")])
    hp = _holdings(tmp_path, [{**_FULL_HOLDING_ROW, "ticker": "IWM"}])
    _instruments(monkeypatch, [{"instrument_id": "IWM", "instrument_type": "etf",
                                "issuer": "iShares"}])

    g = data_gaps.gaps(cp, hp, pp)
    assert "issuer spelling misses its downloader" not in set(g["kind"])


def test_a_vendor_sector_whose_date_stopped_advancing_is_a_gap(
    tmp_path, monkeypatch, data_gaps
):
    """A disagreement leaves the sector AND its date untouched, on purpose.

    `ops/refresh_classification.py` prints it at the time and then nothing
    reports it ever again — the single remaining trace is a date that stops
    moving while every other row's advances.
    """
    pp = _positions(tmp_path, [
        {"instrument_id": "BMNR", "market_value": 4000.0},
        {"instrument_id": "NVDA", "market_value": 6000.0},
    ])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "BMNR", "sector": "Financials",
         "provider_sector": "Financials", "provider_sector_as_of": "2026-09-01",
         "provider_sector_source": "Yahoo"},
        {"instrument_id": "NVDA", "sector": "Technology",
         "provider_sector": "Technology", "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    left = g[g["kind"] == "vendor sector left behind"]
    assert list(left["subject"]) == ["BMNR"]
    assert "2026-09-01" in left.iloc[0]["detail"]
    assert "2026-10-04" in left.iloc[0]["detail"]


def test_a_table_refreshed_all_at_once_leaves_nobody_behind(
    tmp_path, monkeypatch, data_gaps
):
    """Every row written by the same run shares a date, so the check is
    silent on a healthy table — which is the point of it being a gap kind
    rather than a line that always prints."""
    pp = _positions(tmp_path, [
        {"instrument_id": "BMNR", "market_value": 4000.0},
        {"instrument_id": "NVDA", "market_value": 6000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BMNR", "sector": "Financials",
         "provider_sector": "Financials", "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo"},
        {"instrument_id": "NVDA", "sector": "Technology",
         "provider_sector": "Technology", "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo"},
    ])

    g = data_gaps.gaps(_compositions(tmp_path, []), _holdings(tmp_path, []), pp)
    assert "vendor sector left behind" not in set(g["kind"])


def test_an_unresolvable_ticker_is_a_gap(tmp_path, monkeypatch, data_gaps):
    """BRKB is the one real entry in market._KNOWN_SHARE_CLASSES. With
    neither price_ticker nor av_ticker set, market.unresolvable() suspects a
    provider wants "BRK-B" instead."""
    pp = _positions(tmp_path, [{"instrument_id": "BRKB", "market_value": 2500.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "BRKB", "instrument_type": "stock",
         "sector": "Financials - Diversified", "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[(g["subject"] == "BRKB") & (g["kind"] == "unresolvable ticker")]
    assert len(row) == 1
    assert row.iloc[0]["market_value"] == 2500.0
    assert "BRK-B" in row.iloc[0]["detail"]


# ─── "held" is positions.csv, never instruments.status ───

def test_held_is_decided_by_positions_csv_not_instrument_status(
    tmp_path, monkeypatch, data_gaps
):
    """A row marked `closed` in instruments.csv that positions.csv still
    carries money for must still be checked — the two can disagree, and the
    position file is the truth (CLAUDE.md)."""
    pp = _positions(tmp_path, [{"instrument_id": "ABCD", "market_value": 777.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "ABCD", "instrument_type": "stock", "status": "closed",
         "sector": "Tech - Software", "provider_sector": "", "provider_sector_as_of": ""},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[g["subject"] == "ABCD"]
    assert (row["kind"] == "no dated vendor sector").any(), (
        "status='closed' must not exempt a lot positions.csv still holds money in"
    )


# ─── the two-vocabulary trap: provider_sector_as_of says which one you hold ───

def test_a_legacy_undated_vendor_sector_is_no_dated_vendor_sector_not_a_crash(
    tmp_path, monkeypatch, data_gaps
):
    """A row with provider_sector_as_of blank holds a raw vendor word that
    predates data/sectors.csv ('FINANCIAL SERVICES', never 'Financials') and
    is NOT in the taxonomy. sectors.parent() would raise on it — gaps() must
    never attempt the rollup for this row, only report it as undated.
    """
    pp = _positions(tmp_path, [{"instrument_id": "ALL", "market_value": 900.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "ALL", "instrument_type": "stock",
         "sector": "Financials - Insurance",
         "provider_sector": "FINANCIAL SERVICES",    # legacy, pre-taxonomy
         "provider_sector_as_of": ""},                # the tell: undated
    ])

    g = data_gaps.gaps(cp, hp, pp)                    # must not raise
    row = g[g["subject"] == "ALL"]
    assert (row["kind"] == "no dated vendor sector").any()
    assert not (row["kind"] == "sector disagreement").any()
    assert not (row["kind"] == "unmappable sector").any()


def test_an_unmappable_sector_is_reported_not_swallowed(tmp_path, monkeypatch, data_gaps):
    """A DATED provider_sector that still is not in data/sectors.csv (a typo,
    or a vendor word nobody has mapped yet) used to vanish: `_parent_or_none`
    returned None and the row produced no output at all — neither a
    disagreement nor a "cannot compare". Silence is not the right answer to
    "I cannot tell".
    """
    pp = _positions(tmp_path, [{"instrument_id": "ZZZ", "market_value": 650.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "ZZZ", "instrument_type": "stock",
         "sector": "Tech - Semiconductors",
         "provider_sector": "Nonexistent Vendor Label",
         "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[g["subject"] == "ZZZ"]
    assert (row["kind"] == "unmappable sector").any()
    assert not (row["kind"] == "sector disagreement").any()
    detail = row[row["kind"] == "unmappable sector"].iloc[0]["detail"]
    assert "Nonexistent Vendor Label" in detail


def test_an_unmappable_own_label_is_named_too_not_just_the_vendors(
    tmp_path, monkeypatch, data_gaps
):
    """The mirror of the test above: a garbage OWN label (not the vendor's)
    must also be named, never silently matched to an empty list — the
    regression this fix round found was specifically a blank/bad `mine`
    rendering as 'not in data/sectors.csv' with nothing named at all."""
    pp = _positions(tmp_path, [{"instrument_id": "YYY", "market_value": 500.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "YYY", "instrument_type": "stock",
         "sector": "Not A Real Sector",
         "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[g["subject"] == "YYY"]
    assert (row["kind"] == "unmappable sector").any()
    detail = row[row["kind"] == "unmappable sector"].iloc[0]["detail"]
    assert "Not A Real Sector" in detail


def test_a_blank_own_sector_is_its_own_gap_naming_the_vendor(
    tmp_path, monkeypatch, data_gaps
):
    """F1. The sync auto-adds a lot with no sector; the classification
    refresh can still date a vendor label for it before anyone has typed his
    own. That used to render as 'unmappable sector' with NOTHING named
    (a blank `mine` and a perfectly valid `vendor` both failed to appear in
    the old code's bad-label list) — a sentence that named nothing is not an
    acceptable answer to "what's wrong here"."""
    pp = _positions(tmp_path, [{"instrument_id": "NEWCO", "market_value": 250.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "NEWCO", "instrument_type": "stock", "sector": "",
         "provider_sector": "Technology", "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[g["subject"] == "NEWCO"]
    assert (row["kind"] == "no sector of his own").any()
    assert not (row["kind"] == "unmappable sector").any()
    detail = row[row["kind"] == "no sector of his own"].iloc[0]["detail"]
    assert "Technology" in detail and "2026-10-04" in detail


# ─── option/future never carry a vendor sector, and never should ───

def test_an_option_never_reports_no_dated_vendor_sector(tmp_path, monkeypatch, data_gaps):
    """No provider classifies a VIX call into a SPY sector. Treating a blank
    provider_sector as a gap here would be a queue that can never clear —
    the same failure instruments.gaps() was fixed for on 2026-09-29."""
    pp = _positions(tmp_path, [{"instrument_id": "VIXCALL", "market_value": 400.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "VIXCALL", "instrument_type": "option",
         "sector": "", "provider_sector": "", "provider_sector_as_of": ""},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    assert "VIXCALL" not in set(g["subject"])


def test_a_future_never_reports_no_dated_vendor_sector(tmp_path, monkeypatch, data_gaps):
    pp = _positions(tmp_path, [{"instrument_id": "MCLQ6", "market_value": 400.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "MCLQ6", "instrument_type": "future",
         "sector": "Energy - Crude Oil", "provider_sector": "", "provider_sector_as_of": ""},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    assert "MCLQ6" not in set(g["subject"])


# ─── two of the rulings this report must not re-break ───

def test_a_fund_with_no_equity_inside_is_not_a_company_holdings_gap(
    tmp_path, monkeypatch, data_gaps
):
    """SGOV holds treasury bills. No row for it in etf_holdings.csv is
    correct, not missing — ops/refresh_etf_holdings.py's NO_EQUITY_INSIDE."""
    assert "SGOV" in data_gaps.NO_EQUITY_INSIDE, "the real ruling this test checks against"
    pp = _positions(tmp_path, [{"instrument_id": "SGOV", "market_value": 14000.0}])
    cp = _compositions(tmp_path, [_one_composition_row(
        "SGOV", as_of="2026-10-01", source_quality="vendor")])
    hp = _holdings(tmp_path, [])             # no holdings rows, and that's correct
    _instruments(monkeypatch, [
        {"instrument_id": "SGOV", "instrument_type": "etf"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    assert g.empty

    no_equity = data_gaps.no_equity_etfs_held(pp)
    assert list(no_equity["ticker"]) == ["SGOV"]
    assert no_equity.iloc[0]["market_value"] == 14000.0


def test_a_sector_disagreement_is_reported_without_correcting_him(
    tmp_path, monkeypatch, data_gaps
):
    """Real case in the data: BMNR, which he files under crypto and the
    vendor calls financials. A fact to surface, not an error to fix — his
    label still wins. Real taxonomy labels, so sectors.parent() resolves
    them without a path override, same as exposure.parent_sector()'s tests.
    """
    pp = _positions(tmp_path, [{"instrument_id": "BMNR", "market_value": 3400.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "BMNR", "instrument_type": "stock",
         "sector": "Crypto - Miners / Treasury", "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[g["subject"] == "BMNR"]
    assert (row["kind"] == "sector disagreement").any()
    detail = row[row["kind"] == "sector disagreement"].iloc[0]["detail"]
    assert "Crypto / Digital Assets" in detail
    assert "Financials" in detail


def test_a_settled_sector_disagreement_is_never_reported_again(
    tmp_path, monkeypatch, data_gaps
):
    """His verdict, 2026-10-04 (commentary.csv cm0017): "BMNR can stay as
    crypto... Sometimes the vendor is not right." Once `sector_settled` is
    filled, this is permanent, not conditional — the disagreement must not
    resurface even though his sector and the vendor's still roll up
    differently.
    """
    pp = _positions(tmp_path, [{"instrument_id": "BMNR", "market_value": 3400.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "BMNR", "instrument_type": "stock",
         "sector": "Crypto - Miners / Treasury", "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance",
         "sector_settled": "2026-10-04"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    assert "BMNR" not in set(g["subject"])


def test_the_disagreement_check_is_the_one_in_exposure(
    monkeypatch, data_gaps, tmp_path
):
    """P7. Two comparisons of his label against the vendor's would drift, and
    the one the page shows and the one /market reads out must be the same
    fact."""
    assert not hasattr(data_gaps, "_parent_or_none"), (
        "a second, independent comparison here could drift from "
        "exposure.sector_disagreements() — it must not exist any more"
    )

    pp = _positions(tmp_path, [{"instrument_id": "ZZZ", "market_value": 111.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    # Blank sector/provider_sector by default — if gaps() still computed its
    # own comparison from this instrument row (rather than reading
    # exposure.sector_disagreements()'s stubbed answer below) it would read
    # "no dated vendor sector", never "sector disagreement".
    _instruments(monkeypatch, [{"instrument_id": "ZZZ", "instrument_type": "stock"}])

    stub = pd.DataFrame([{"instrument_id": "ZZZ", "mine": "Tech - Software",
                          "vendor": "Financials", "market_value": 111.0,
                          "state": "disagree"}])
    monkeypatch.setattr(data_gaps.exposure, "sector_disagreements",
                        lambda *a, **k: stub)

    g = data_gaps.gaps(cp, hp, pp)
    row = g[g["subject"] == "ZZZ"]
    assert (row["kind"] == "sector disagreement").any(), (
        "gaps() must build its row from exposure.sector_disagreements()'s "
        "own output, not recompute the comparison from instruments.csv"
    )
    detail = row[row["kind"] == "sector disagreement"].iloc[0]["detail"]
    assert "Tech - Software" in detail and "Financials" in detail


# ─── the suppression is a CONJUNCTION: shape alone and the set alone both fail ───

def test_a_single_row_issuer_breakdown_in_no_equity_inside_is_not_an_undated_gap(
    tmp_path, monkeypatch, data_gaps
):
    """SGOV is one issuer row at ~100% of the fund AND is in NO_EQUITY_INSIDE
    (treasury bills) — nothing inside it could ever make the row stale, so it
    needs no as_of date to be trusted.
    """
    assert "SGOV" in data_gaps.NO_EQUITY_INSIDE
    pp = _positions(tmp_path, [{"instrument_id": "SGOV", "market_value": 4000.0}])
    cp = _compositions(tmp_path, [_one_composition_row(
        "SGOV", as_of="", source_quality="issuer", weight=1.0)])
    hp = _holdings(tmp_path, [])             # no holdings rows, and that's correct
    _instruments(monkeypatch, [
        {"instrument_id": "SGOV", "instrument_type": "etf"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    assert g.empty, "a single definitional row in a fund with nothing inside it needs no date"


def test_a_single_row_issuer_breakdown_not_in_no_equity_inside_is_still_reported(
    tmp_path, monkeypatch, data_gaps
):
    """The failure shape alone cannot catch: KIE is a REAL row in
    etf_compositions.csv today — one issuer row at 100%, undated — and is an
    ordinary equity ETF (not in NO_EQUITY_INSIDE). The companies inside it
    drift between sub-sectors constantly even though the row itself never
    changes, so shape alone would make that drift invisible forever the
    moment it is bought. The set is what says there is really nothing inside
    a fund that could move; shape alone is not enough to say that.
    """
    assert "KIE" not in data_gaps.NO_EQUITY_INSIDE
    pp = _positions(tmp_path, [{"instrument_id": "KIE", "market_value": 4000.0}])
    cp = _compositions(tmp_path, [_one_composition_row(
        "KIE", as_of="", source_quality="issuer", weight=1.0)])
    hp = _holdings(tmp_path, [{"ticker": "KIE", **_FULL_HOLDING_ROW}])
    _instruments(monkeypatch, [
        {"instrument_id": "KIE", "instrument_type": "etf"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[(g["subject"] == "KIE") & (g["kind"] == "undated sector breakdown")]
    assert len(row) == 1, "a single-row equity fund not in NO_EQUITY_INSIDE must still be flagged"


def test_a_multi_row_vendor_breakdown_still_flags_when_undated(
    tmp_path, monkeypatch, data_gaps
):
    """The legitimate case: an ordinary equity fund whose mix really drifts.

    QQQ is not in NO_EQUITY_INSIDE and holds a hundred companies that move
    between sectors continuously, so an eleven-row vendor breakdown is
    exactly the right shape for it — and with no date on it, nobody can say
    how far behind that mix has fallen. This is the gap the "undated" kind
    exists for, and it is the fund to argue it from: the drift is real.
    """
    assert "QQQ" not in data_gaps.NO_EQUITY_INSIDE
    pp = _positions(tmp_path, [{"instrument_id": "QQQ", "market_value": 20000.0}])
    cp = _compositions(tmp_path, [
        {"ticker": "QQQ", "sector": "Technology", "weight": 0.6, "top_holding": "",
         "source": "Morningstar via yfinance", "source_quality": "vendor", "as_of": ""},
        {"ticker": "QQQ", "sector": "Consumer Discretionary", "weight": 0.4,
         "top_holding": "", "source": "Morningstar via yfinance",
         "source_quality": "vendor", "as_of": ""},
    ])
    hp = _holdings(tmp_path, [{"ticker": "QQQ", **_FULL_HOLDING_ROW}])
    _instruments(monkeypatch, [
        {"instrument_id": "QQQ", "instrument_type": "etf"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[(g["subject"] == "QQQ") & (g["kind"] == "undated sector breakdown")]
    assert len(row) == 1, "a drifting, undated multi-row breakdown must be flagged"


def test_a_no_equity_fund_wearing_an_equity_breakdown_is_not_suppressed(
    tmp_path, monkeypatch, data_gaps
):
    """The other half of `_is_definitional`, re-argued after 2026-10-01.

    This test used to claim DBA's eleven Morningstar equity sectors were its
    real breakdown and genuinely drifted. They were not and they do not: DBA
    holds agricultural futures and T-bill collateral, holds no companies at
    all, and a refresh that gave it eleven equity sectors led by Healthcare
    moved $3,180 of his Materials exposure into sectors the fund does not
    hold. The single Invesco row is back.

    So the shape half of the conjunction is still needed, for the opposite
    reason: a fund in NO_EQUITY_INSIDE wearing a multi-sector equity
    breakdown is not a fund whose row is definitional — it is a fund whose
    row is WRONG, and suppressing its date would hide the one signal that
    something put it there. Set membership alone would suppress exactly the
    case that most needs reporting.
    """
    assert "DBA" in data_gaps.NO_EQUITY_INSIDE
    pp = _positions(tmp_path, [{"instrument_id": "DBA", "market_value": 5000.0}])
    cp = _compositions(tmp_path, [
        {"ticker": "DBA", "sector": "Healthcare", "weight": 0.6, "top_holding": "",
         "source": "Morningstar via yfinance", "source_quality": "vendor", "as_of": ""},
        {"ticker": "DBA", "sector": "Materials", "weight": 0.4, "top_holding": "",
         "source": "Morningstar via yfinance", "source_quality": "vendor", "as_of": ""},
    ])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "DBA", "instrument_type": "etf"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[(g["subject"] == "DBA") & (g["kind"] == "undated sector breakdown")]
    assert len(row) == 1, "a wrong multi-row breakdown must not be suppressed as definitional"
    # and it is correctly NOT also flagged for missing company holdings —
    # that exemption is the one NO_EQUITY_INSIDE is actually right for.
    assert not ((g["subject"] == "DBA") & (g["kind"] == "no company holdings")).any()


def test_a_named_holdings_breakdown_warns_against_the_refresher(
    tmp_path, monkeypatch, data_gaps
):
    """MAGS-shaped: every sector row IS one company (top_holding filled).
    Running the composition refresher would overwrite those rows with coarse
    parent sectors, destroying the only top_holding data in the table — so
    the gap's own detail must say so and name the actual fix.
    """
    pp = _positions(tmp_path, [{"instrument_id": "SEVEN", "market_value": 7000.0}])
    cp = _compositions(tmp_path, [
        {"ticker": "SEVEN", "sector": "Tech - Software", "weight": 0.5,
         "top_holding": "AAA", "source": "issuer", "source_quality": "issuer", "as_of": ""},
        {"ticker": "SEVEN", "sector": "Tech - Hardware / Network", "weight": 0.5,
         "top_holding": "BBB", "source": "issuer", "source_quality": "issuer", "as_of": ""},
    ])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "SEVEN", "instrument_type": "etf", "issuer": "Acme Funds"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    row = g[(g["subject"] == "SEVEN") & (g["kind"] == "undated sector breakdown")]
    assert len(row) == 1
    detail = row.iloc[0]["detail"]
    assert "NOT fix this" in detail
    assert "Acme Funds" in detail
    assert "2 holdings" in detail


# ─── orphaned_holdings() must never invite deleting the benchmark (Task 7 review) ───

def test_orphaned_holdings_names_a_fund_he_really_no_longer_holds(
    tmp_path, monkeypatch, data_gaps
):
    """The ordinary case this function exists for: IMAE's rows outlive the
    position, and that is real — his call whether to clear them."""
    pp = _positions(tmp_path, [{"instrument_id": "XLK", "market_value": 1000.0}])
    hp = _holdings(tmp_path, [
        {"ticker": "XLK", **_FULL_HOLDING_ROW},
        {"ticker": "IMAE", **_FULL_HOLDING_ROW},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "XLK", "instrument_type": "etf", "status": "active"},
        {"instrument_id": "IMAE", "instrument_type": "etf", "status": "closed"},
    ])

    out = data_gaps.orphaned_holdings(hp, pp)
    assert list(out["ticker"]) == ["IMAE"]


def test_orphaned_holdings_never_names_a_benchmark(tmp_path, monkeypatch, data_gaps):
    """SPY is never held and never will be — after the first `--write`,
    `etf_holdings.csv` carries its 288 real holdings on purpose. Without this
    exclusion, SPY would sit in this list forever, which is the exact
    permanently-unclearable-row failure already fixed once in
    `instruments.gaps()` — and the line it sits on is the last thing
    `ops/refresh_market.py` prints, inviting exactly the action (clearing it)
    that destroys the benchmark data this task exists to create."""
    pp = _positions(tmp_path, [{"instrument_id": "XLK", "market_value": 1000.0}])
    hp = _holdings(tmp_path, [
        {"ticker": "XLK", **_FULL_HOLDING_ROW},
        {"ticker": "SPY", **_FULL_HOLDING_ROW},
        {"ticker": "IMAE", **_FULL_HOLDING_ROW},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "XLK", "instrument_type": "etf", "status": "active"},
        {"instrument_id": "SPY", "instrument_type": "etf", "status": "benchmark"},
        {"instrument_id": "IMAE", "instrument_type": "etf", "status": "closed"},
    ])

    out = data_gaps.orphaned_holdings(hp, pp)
    assert list(out["ticker"]) == ["IMAE"]
    assert "SPY" not in set(out["ticker"])


# ─── the two answers to "how much is unknown" ───

def test_unnamed_money_reports_the_conservative_figure(data_gaps, tmp_path,
                                                       monkeypatch):
    """A fund can account for all of itself and still name almost none of it.

    `coverage` says how much the SOURCE accounted for; the stored weights
    say how much is written down company by company, because rows under the
    0.05% floor are dropped AFTER coverage is computed. IWM's source
    accounts for 99.9% of it while the table names 79%. Quoting the first
    number — which a session did, four times, on 2026-10-06 — understates
    what is unknown by fifty times.
    """
    holdings = tmp_path / "etf_holdings.csv"
    holdings.write_text(
        '"ticker","holding","holding_name","weight","coverage","source",'
        '"as_of","as_of_kind"\n'
        '"BIGFUND","AAA","A Corp",0.4,1.0,"test","2026-10-02","published"\n'
        '"BIGFUND","BBB","B Corp",0.4,1.0,"test","2026-10-02","published"\n')
    monkeypatch.setattr(market, "HOLDINGS_PATH", holdings)
    positions = _positions(tmp_path, [
        {"instrument_id": "BIGFUND", "market_value": 1000.0}])

    out = data_gaps.unnamed_money(positions, holdings)
    row = out[out["ticker"] == "BIGFUND"].iloc[0]

    assert row["by_coverage"] == pytest.approx(0.0)      # the flattering one
    assert row["unnamed"] == pytest.approx(200.0)        # the honest one


def test_unnamed_money_ignores_a_fund_he_no_longer_holds(data_gaps, tmp_path,
                                                         monkeypatch):
    """A closed fund's leftover rows must not invent unknown money."""
    holdings = tmp_path / "etf_holdings.csv"
    holdings.write_text(
        '"ticker","holding","holding_name","weight","coverage","source",'
        '"as_of","as_of_kind"\n'
        '"GONE","AAA","A Corp",0.1,1.0,"test","2026-10-02","published"\n')
    monkeypatch.setattr(market, "HOLDINGS_PATH", holdings)
    positions = _positions(tmp_path, [
        {"instrument_id": "HELD", "market_value": 500.0}])

    assert data_gaps.unnamed_money(positions, holdings).empty


# ─── the day he buys a fund the repo has never seen ───

def _one_etf(tmp_path, ticker, name):
    """An instruments.csv holding one ETF and nothing else.

    Built from `instruments.COLUMNS` rather than a hand-typed subset — the
    schema is strict AND ordered, so a literal here would break the day a
    column is added, which is noise rather than a finding.
    """
    from src import instruments as inst_mod
    row = {c: "" for c in inst_mod.COLUMNS}
    row.update({"instrument_id": ticker, "name": name,
                "instrument_type": "etf", "issuer": "Schwab",
                "status": "active", "added": "2026-10-06"})
    path = tmp_path / "instruments.csv"
    pd.DataFrame([row], columns=inst_mod.COLUMNS).to_csv(path, index=False)
    return path

def test_a_newly_bought_etf_reports_both_of_its_holes(data_gaps, tmp_path):
    """The scenario worth rehearsing, and the one that was half-reported.

    `exposure.coverage()` is built FROM etf_compositions.csv, so a fund with
    no row there is absent from it entirely — and the sector check was gated
    on being present. The one case the heading exists for was the one case
    it could not report: the report named the missing holdings, said nothing
    about the missing breakdown, and the sector view would have quietly
    excluded a real position.
    """
    positions = _positions(tmp_path, [
        {"instrument_id": "NEWF", "market_value": 4200.0}])
    instruments_csv = _one_etf(tmp_path, "NEWF", "A Fund Bought Yesterday")
    compositions = _compositions(tmp_path, [])
    holdings = tmp_path / "etf_holdings.csv"
    holdings.write_text(
        '"ticker","holding","holding_name","weight","coverage","source",'
        '"as_of","as_of_kind"\n')

    g = data_gaps.gaps(compositions_path=compositions, holdings_path=holdings,
                       positions_path=positions,
                       instruments_path=instruments_csv)

    kinds = set(g[g["subject"] == "NEWF"]["kind"])
    assert kinds == {"no sector breakdown", "no company holdings"}


def test_the_report_reads_one_instruments_file_not_two(data_gaps, tmp_path):
    """A fund cannot be unknown in one line and described in the next.

    The no-instrument-row check loaded instruments separately from every
    other check, so the report could hold two views of what exists.
    """
    positions = _positions(tmp_path, [
        {"instrument_id": "NEWF", "market_value": 4200.0}])
    instruments_csv = _one_etf(tmp_path, "NEWF", "A Fund Bought Yesterday")
    holdings = tmp_path / "etf_holdings.csv"
    holdings.write_text(
        '"ticker","holding","holding_name","weight","coverage","source",'
        '"as_of","as_of_kind"\n')

    g = data_gaps.gaps(compositions_path=_compositions(tmp_path, []),
                       holdings_path=holdings, positions_path=positions,
                       instruments_path=instruments_csv)

    assert "no instrument row" not in set(g["kind"])


# ─── what /market offers him, and what it must never offer ───

def _aged_holdings(tmp_path, ticker, as_of):
    path = tmp_path / "etf_holdings.csv"
    path.write_text(
        '"ticker","holding","holding_name","weight","coverage","source",'
        '"as_of","as_of_kind"\n'
        f'"{ticker}","AAA","A Corp",1.0,1.0,"issuer file","{as_of}",'
        '"published"\n')
    return path


def test_a_complete_and_recent_fund_is_never_offered(data_gaps, tmp_path):
    """Offering a good fund is how a full refresh happens by accident.

    A re-fetch can only make a complete fund worse — its file may be gone
    from the git-ignored inbox, and Yahoo's ten names would replace it.
    """
    positions = _positions(tmp_path, [
        {"instrument_id": "GOOD", "market_value": 5000.0}])
    out = data_gaps.needs_refresh(
        compositions_path=_compositions(tmp_path, [
            {"ticker": "GOOD", "sector": "Technology", "weight": 1.0,
             "top_holding": "", "source": "issuer", "source_quality": "issuer",
             "as_of": "2026-10-01"}]),
        holdings_path=_aged_holdings(tmp_path, "GOOD", "2026-10-01"),
        positions_path=positions,
        instruments_path=_one_etf(tmp_path, "GOOD", "A Good Fund"),
        today=date(2026, 10, 6))

    assert out.empty


def test_a_fund_whose_data_has_aged_past_the_threshold_is_offered(data_gaps,
                                                                  tmp_path):
    """Same threshold the end-of-turn hook nags at — one number, one meaning."""
    positions = _positions(tmp_path, [
        {"instrument_id": "OLD", "market_value": 5000.0}])
    out = data_gaps.needs_refresh(
        compositions_path=_compositions(tmp_path, [
            {"ticker": "OLD", "sector": "Technology", "weight": 1.0,
             "top_holding": "", "source": "issuer", "source_quality": "issuer",
             "as_of": "2026-01-01"}]),
        holdings_path=_aged_holdings(tmp_path, "OLD", "2026-01-01"),
        positions_path=positions,
        instruments_path=_one_etf(tmp_path, "OLD", "An Old Fund"),
        today=date(2026, 10, 6))

    assert list(out["ticker"]) == ["OLD"]
    assert "days old" in out.iloc[0]["why"]


def test_a_fund_he_no_longer_holds_is_never_offered(data_gaps, tmp_path):
    """Stale rows for a closed fund are not work; they are history."""
    positions = _positions(tmp_path, [
        {"instrument_id": "HELD", "market_value": 100.0}])
    out = data_gaps.needs_refresh(
        compositions_path=_compositions(tmp_path, []),
        holdings_path=_aged_holdings(tmp_path, "CLOSED", "2020-01-01"),
        positions_path=positions,
        instruments_path=_one_etf(tmp_path, "HELD", "Still Held"),
        today=date(2026, 10, 6))

    assert "CLOSED" not in set(out["ticker"])


# ─── the benchmark's own data: gaps() is scoped to what he holds, and SPY
# never is (Task 2) ───

def _benchmark_etf(tmp_path, ticker, name):
    """An instruments.csv holding one ETF with status=benchmark.

    Built from `instruments.COLUMNS`, the same way `_one_etf()` above is, but
    kept separate rather than shared with it: these tests pass
    `instruments_path` straight to `benchmark_gaps()` (never through
    `_instruments()`'s `monkeypatch.setattr(instruments, "load", ...)`)
    because the thing worth proving here is that the function actually reads
    the path it is given, not a lambda that would return the same frame
    whatever path arrived.
    """
    from src import instruments as inst_mod
    row = {c: "" for c in inst_mod.COLUMNS}
    row.update({"instrument_id": ticker, "name": name,
                "instrument_type": "etf", "issuer": "SPDR",
                "status": "benchmark", "added": "2026-10-04"})
    path = tmp_path / "instruments.csv"
    pd.DataFrame([row], columns=inst_mod.COLUMNS).to_csv(path, index=False)
    return path


def test_a_benchmark_with_no_sector_breakdown_is_a_gap(tmp_path, data_gaps):
    """The by-sector panel compares his look-through against the S&P's own
    weights, and the comparison column cannot exist without them. Nothing
    reported this: gaps() scopes to what he HOLDS, and the benchmark is never
    held — so /market printed 'nothing is missing' while the page waited on a
    fetch nothing would ever offer."""
    ip = _benchmark_etf(tmp_path, "SPY", "SPDR S&P 500 ETF Trust")
    cp = _compositions(tmp_path, [])                        # no row for SPY
    hp = _holdings(tmp_path, [{"ticker": "SPY", **_FULL_HOLDING_ROW}])

    g = data_gaps.benchmark_gaps(cp, hp, ip)
    row = g[g["subject"] == "SPY"]
    assert (row["kind"] == "benchmark sector breakdown missing").any()
    # and NOT also reported for holdings — those are on file for this fixture
    assert not (row["kind"] == "benchmark holdings missing").any()


def test_a_benchmark_with_no_company_holdings_is_a_gap(tmp_path, data_gaps):
    """His instruction, 2026-10-06: the company-by-company comparison against
    the S&P belongs on the main page. SPY's own holdings file is what the
    index side of it is built from."""
    ip = _benchmark_etf(tmp_path, "SPY", "SPDR S&P 500 ETF Trust")
    cp = _compositions(tmp_path, [_one_composition_row("SPY", as_of="2026-10-01")])
    hp = _holdings(tmp_path, [])                             # no holdings at all

    g = data_gaps.benchmark_gaps(cp, hp, ip)
    row = g[g["subject"] == "SPY"]
    assert (row["kind"] == "benchmark holdings missing").any()
    # and NOT also reported for the sector breakdown — that is on file here
    assert not (row["kind"] == "benchmark sector breakdown missing").any()


def test_the_benchmark_gap_carries_no_market_value(tmp_path, monkeypatch, data_gaps):
    """It is not held, so it has no money, and every other row in this report
    is ordered by money. A fabricated value would sort the benchmark among his
    real positions; zero sorts it last, which is where a thing he does not own
    belongs."""
    pp = _positions(tmp_path, [{"instrument_id": "ABCD", "market_value": 300.0}])
    cp = _compositions(tmp_path, [])
    hp = _holdings(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "ABCD", "instrument_type": "stock",
         "sector": "Tech - Software"},
        {"instrument_id": "SPY", "instrument_type": "etf",
         "status": "benchmark", "issuer": "SPDR"},
    ])

    g = data_gaps.gaps(cp, hp, pp)
    bench = g[g["subject"] == "SPY"]
    assert not bench.empty, "SPY must still be reported even though it is unheld"
    assert (bench["market_value"] == 0.0).all(), "0.0, not a fabricated figure"
    # a real $300 gap outranks both of SPY's $0 rows, which keep their
    # discovery order behind it (kind="stable")
    assert list(g["subject"]) == ["ABCD", "SPY", "SPY"]
    assert list(g["market_value"]) == [300.0, 0.0, 0.0]


def test_a_benchmark_with_both_on_file_is_not_a_gap(tmp_path, data_gaps):
    """A queue that cannot reach zero stops being read, and then the real
    gaps in it go unread too — the failure instruments.gaps() was fixed for
    on 2026-09-29."""
    ip = _benchmark_etf(tmp_path, "SPY", "SPDR S&P 500 ETF Trust")
    cp = _compositions(tmp_path, [_one_composition_row("SPY", as_of="2026-10-01")])
    hp = _holdings(tmp_path, [{"ticker": "SPY", **_FULL_HOLDING_ROW}])

    g = data_gaps.benchmark_gaps(cp, hp, ip)
    assert g.empty, "both tables on file means SPY is no longer offered"


def test_needs_refresh_offers_the_benchmark(tmp_path, monkeypatch, data_gaps):
    """This is the half that matters: /market asks him to choose from
    needs_refresh(), so a gap that does not reach it is a gap he is never
    offered."""
    pp = _positions(tmp_path, [{"instrument_id": "XLK", "market_value": 1000.0}])
    cp = _compositions(tmp_path, [_one_composition_row("XLK", as_of="2026-10-01")])
    hp = _holdings(tmp_path, [{"ticker": "XLK", **_FULL_HOLDING_ROW}])
    _instruments(monkeypatch, [
        {"instrument_id": "XLK", "instrument_type": "etf"},
        {"instrument_id": "SPY", "instrument_type": "etf",
         "status": "benchmark", "issuer": "SPDR"},
    ])

    out = data_gaps.needs_refresh(compositions_path=cp, holdings_path=hp,
                                  positions_path=pp, today=date(2026, 10, 6))
    assert list(out["ticker"]) == ["SPY"], (
        "XLK is complete and recent and must not be offered; SPY has neither "
        "table and must be"
    )
    assert "benchmark" in out.iloc[0]["why"]


def test_orphaned_holdings_still_excludes_the_benchmark(tmp_path, monkeypatch,
                                                         data_gaps):
    """The opposite check, and it must not have moved. SPY's holdings rows are
    data the page needs, not orphans to clear — it would otherwise sit on the
    last line of every refresh inviting the one action that destroys them."""
    pp = _positions(tmp_path, [{"instrument_id": "XLK", "market_value": 1000.0}])
    hp = _holdings(tmp_path, [
        {"ticker": "XLK", **_FULL_HOLDING_ROW},
        {"ticker": "SPY", **_FULL_HOLDING_ROW},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "XLK", "instrument_type": "etf", "status": "active"},
        {"instrument_id": "SPY", "instrument_type": "etf",
         "status": "benchmark", "issuer": "SPDR"},
    ])

    out = data_gaps.orphaned_holdings(hp, pp)
    assert "SPY" not in set(out["ticker"]), (
        "orphaned_holdings() must keep excluding the benchmark — "
        "benchmark_gaps() is the function that reports on it now"
    )
