"""
Tests for look-through exposure.

The module exists because a cluster label answers "why I bought it" and cannot
also answer "what am I exposed to". Yaron hit that asking where QQQ belongs: it
is in Core US because he bought it for US beta, and it is also 50% technology.

So most of these tests are about the two failure modes that make an exposure
number worse than no number — money that quietly falls out of every total, and
a confident figure that is really a guess.

Run from project root:
    pytest tests/test_exposure.py
"""

from __future__ import annotations

import csv

import pandas as pd
import pandera.pandas as pa
import pytest

from src import claims, exposure


def _comp(tmp_path, rows):
    p = tmp_path / "etf_compositions.csv"
    pd.DataFrame(rows, columns=exposure.COLUMNS).to_csv(
        p, index=False, quoting=csv.QUOTE_NONNUMERIC
    )
    return p


def _positions(tmp_path, rows):
    p = tmp_path / "positions.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _instruments(monkeypatch, rows):
    """Monkeypatch `instruments.load()` with a tiny store.

    Fills in every column a caller's row leaves out, rather than making every
    call site repeat them: `book.lots()` requires `instrument_type` and reads
    the label fields (`sector`, `provider_sector`, `provider_sector_as_of`,
    `cluster_id`); `by_company()` additionally reads `name` directly (for a
    company with no row of its own) and passes the whole frame to
    `market.holding_instrument()`, which looks for `price_ticker`/`av_ticker`
    columns and `note_ref` is along for the ride as a real instruments.csv row
    always carries it too. `sector_settled` and `provider_sector_source` are
    for `sector_disagreements()` (spec D13) — blank unless a test is pinning
    BMNR's settled case or checking the vendor's name travels with a row.
    """
    from src import instruments
    defaults = {
        "instrument_type": "stock", "sector": "", "provider_sector": "",
        "provider_sector_as_of": "", "cluster_id": "", "note_ref": "",
        "name": "", "price_ticker": "", "av_ticker": "", "sector_settled": "",
        "provider_sector_source": "",
    }
    filled = [{**defaults, **row} for row in rows]
    monkeypatch.setattr(instruments, "load",
                        lambda *a, **k: pd.DataFrame(filled))


def _holdings_table(tmp_path, rows):
    from src import market
    p = tmp_path / "etf_holdings.csv"
    pd.DataFrame(rows, columns=market.HOLDINGS_COLUMNS).to_csv(
        p, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return p


QQQ_ROWS = [
    {"ticker": "QQQ", "sector": "Tech - Software", "weight": 0.5,
     "top_holding": "", "source": "index estimate", "source_quality": "estimate",
     "as_of": ""},
    {"ticker": "QQQ", "sector": "Consumer Disc - Retail/EC", "weight": 0.5,
     "top_holding": "", "source": "index estimate", "source_quality": "estimate",
     "as_of": ""},
]


# ─── rolling sub-sectors up ───

def test_parent_sector_rolls_up_yarons_taxonomy():
    assert exposure.parent_sector("Tech - Semiconductors") == "Technology"
    assert exposure.parent_sector("Financials - Payments") == "Financials"
    assert exposure.parent_sector("Cash / FX") == "Fixed Income / Cash"


def test_unknown_sector_raises_instead_of_vanishing():
    """
    It used to return 'Other', which sounds honest and isn't.

    A label nobody recognised took its money out of every sector total while
    the totals still summed tidily — the failure you cannot see. It now names
    the label and the command to classify it.
    """
    with pytest.raises(KeyError) as e:
        exposure.parent_sector("Wibble")
    assert "add_sector" in str(e.value)
    assert "Technology" in str(e.value)  # the message lists the allowed parents


# ─── the QQQ case, which started this ───

def test_an_etf_is_split_across_sectors_not_filed_under_its_cluster(
    tmp_path, monkeypatch
):
    """QQQ sits in Core US by intent and is still half technology by fact."""
    cp = _comp(tmp_path, QQQ_ROWS)
    pp = _positions(tmp_path, [{"instrument_id": "QQQ", "market_value": 1000.0}])
    _instruments(monkeypatch, [{"instrument_id": "QQQ", "instrument_type": "etf",
                                "sector": "", "cluster_id": "Core US"}])

    s = exposure.by_sector(cp, pp).set_index("sector")
    assert s.loc["Technology", "market_value"] == 500.0
    assert s.loc["Consumer Discretionary", "market_value"] == 500.0
    # and none of it counts as something you could see directly
    assert s.loc["Technology", "direct_value"] == 0.0


def test_direct_value_shows_what_was_already_visible(tmp_path, monkeypatch):
    """The gap between market_value and direct_value is the hidden exposure.

    NVDA carries a DATED `provider_sector` — D13's default classification
    reads that, not `sector`, for a non-fund holding. Blank/undated would
    file it under `Other` instead (its own test below), which is not what
    this test is checking.
    """
    cp = _comp(tmp_path, QQQ_ROWS)
    pp = _positions(tmp_path, [
        {"instrument_id": "QQQ", "market_value": 1000.0},
        {"instrument_id": "NVDA", "market_value": 200.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "QQQ", "instrument_type": "etf", "sector": "",
         "cluster_id": "Core US"},
        {"instrument_id": "NVDA", "instrument_type": "stock",
         "sector": "Tech - Semiconductors", "cluster_id": "AI Infrastructure",
         "provider_sector": "Technology", "provider_sector_as_of": "2026-10-04"},
    ])
    s = exposure.by_sector(cp, pp).set_index("sector")
    assert s.loc["Technology", "market_value"] == 700.0   # 500 hidden + 200 direct
    assert s.loc["Technology", "direct_value"] == 200.0


def test_by_sector_forwards_instruments_and_clusters_overrides_to_its_fixture(
        tmp_path, monkeypatch):
    """Fix, 2026-10-07 final review (I5). `_holdings()` used to accept only
    `positions_path` and silently drop `instruments_path`/`clusters_path`,
    so `by_sector(basis=...)` under `pure-equity`/`minus-cash` could only
    ever read `in_equity_base` off the REAL `data/clusters.csv` — harmless
    on the real build (no overrides, both read the same files on disk) but
    it meant a fixture-based test of this exact behaviour was impossible:
    passing `clusters_path` either raised (no such parameter) or, after it
    gained one, would have been silently ignored one call frame down.

    Demonstrated here the same way the review's own probe did: a cluster
    explicitly marked `in_equity_base=False` must be excluded from the
    `pure-equity` basis, and the total must agree with `book.nav()` given
    the SAME fixture — the two must never answer a different question
    about what is 'in' a basis.
    """
    from src import book

    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 1000.0},
        {"ticker": "GLD", "instrument_id": "GLD", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock",
         "sector": "Tech - Semiconductors", "cluster_id": "TechCluster"},
        {"instrument_id": "GLD", "name": "Gold Trust", "instrument_type": "etf",
         "sector": "", "cluster_id": "NonEquity"},
    ])
    clp = tmp_path / "clusters.csv"
    pd.DataFrame([
        {"cluster_id": "TechCluster", "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "NonEquity", "parent": "", "layer": "thematic",
         "in_equity_base": False, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ]).to_csv(clp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    cp = _comp(tmp_path, [])

    nav = book.nav("pure-equity", positions_path=pos, clusters_path=clp)
    assert nav == pytest.approx(1000.0)   # GLD's cluster excludes it

    s = exposure.by_sector(cp, pos, basis="pure-equity", classification="mine",
                           clusters_path=clp)
    # Before the fix this total was $1,500 — GLD's cluster override was
    # dropped, so `in_equity_base` fell back to the REAL data/clusters.csv,
    # where an unrecognised cluster id defaults to `True` (book.py's own
    # rule) and GLD was wrongly counted as equity.
    assert float(s["market_value"].sum()) == pytest.approx(nav)
    assert float(s["market_value"].sum()) == pytest.approx(1000.0)


# ─── spec D13: the sector panel reads the vendor's classification ─────────


def test_the_sector_view_reads_the_vendors_label_by_default(tmp_path, monkeypatch):
    """Spec D13, his words: 'it's not my decision, it's how the S&P does their
    decisions, not mine.' BMNR is the live case — he files it as crypto because
    holding crypto is what the business does; Yahoo files it as Financials
    because its balance sheet holds crypto."""
    cp = _comp(tmp_path, [])
    pp = _positions(tmp_path, [{"instrument_id": "BMNR", "market_value": 3400.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "BMNR", "instrument_type": "stock",
         "sector": "Crypto - Miners / Treasury", "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04"},
    ])

    s = exposure.by_sector(cp, pp).set_index("sector")   # classification defaults to "provider"
    assert s.loc["Financials", "market_value"] == 3400.0
    assert "Crypto / Digital Assets" not in s.index


def test_his_own_label_is_still_available_and_still_differs(tmp_path, monkeypatch):
    """D13 narrows ONE consumer. `sector` keeps its sub-sector granularity,
    keeps driving sectors.csv and keeps winning everywhere else, so the
    function must still be able to answer with it."""
    cp = _comp(tmp_path, [])
    pp = _positions(tmp_path, [{"instrument_id": "BMNR", "market_value": 3400.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "BMNR", "instrument_type": "stock",
         "sector": "Crypto - Miners / Treasury", "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04"},
    ])

    vendor = exposure.by_sector(cp, pp).set_index("sector")
    mine = exposure.by_sector(cp, pp, classification="mine").set_index("sector")
    assert vendor.loc["Financials", "market_value"] == 3400.0
    assert mine.loc["Crypto / Digital Assets", "market_value"] == 3400.0


def test_a_holding_with_no_vendor_label_is_named_not_silently_his(
    tmp_path, monkeypatch
):
    """Falling back to his label would make the panel claim a vendor said
    something nobody said. The money goes to Other and the holding is named."""
    cp = _comp(tmp_path, [])
    pp = _positions(tmp_path, [{"instrument_id": "NVDA", "market_value": 500.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "instrument_type": "stock",
         "sector": "Tech - Semiconductors", "provider_sector": "",
         "provider_sector_as_of": ""},
    ])

    s = exposure.by_sector(cp, pp).set_index("sector")
    assert s.loc["Other", "market_value"] == 500.0
    assert "Technology" not in s.index

    d = exposure.sector_disagreements(positions_path=pp)
    row = d[d["instrument_id"] == "NVDA"].iloc[0]
    assert row["state"] == "no-vendor-sector"


def test_an_undated_vendor_label_is_not_used(tmp_path, monkeypatch):
    """provider_sector holds two vocabularies and provider_sector_as_of says
    which. An undated cell holds a raw vendor word that is not a sectors.csv
    label — rolling it up would raise, or worse, land somewhere."""
    cp = _comp(tmp_path, [])
    pp = _positions(tmp_path, [{"instrument_id": "ALL", "market_value": 900.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "ALL", "instrument_type": "stock",
         "sector": "Financials - Insurance",
         "provider_sector": "FINANCIAL SERVICES",    # legacy, pre-taxonomy
         "provider_sector_as_of": ""},                # the tell: undated
    ])

    s = exposure.by_sector(cp, pp).set_index("sector")   # must not raise
    assert s.loc["Other", "market_value"] == 900.0
    assert "Financials" not in s.index


def test_every_disagreement_is_listed(tmp_path, monkeypatch):
    """The panel lists them; it does not correct either side.

    Also pins F3's new columns: the vendor's NAME and its AS-OF DATE travel
    with every row (the panel promises to name both), and the dollar amount
    that moves between the two sectors is returned computed, not left for a
    later reader to re-derive — for a single stock that is the whole
    position, since nothing here is split across sectors.
    """
    pp = _positions(tmp_path, [{"instrument_id": "BMNR", "market_value": 3400.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "BMNR", "instrument_type": "stock",
         "sector": "Crypto - Miners / Treasury", "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance"},
    ])

    d = exposure.sector_disagreements(positions_path=pp)
    row = d[d["instrument_id"] == "BMNR"].iloc[0]
    assert row["state"] == "disagree"
    assert row["mine"] == "Crypto - Miners / Treasury"
    assert row["vendor"] == "Financials"
    assert row["vendor_source"] == "Yahoo via yfinance"
    assert row["vendor_as_of"] == "2026-10-04"
    assert row["market_value"] == 3400.0
    assert row["dollar_effect"] == 3400.0


def test_a_kept_mine_disagreement_is_shown_not_hidden(tmp_path, monkeypatch):
    """instruments.sector_settled is his decision that his own label stands —
    the two labels still roll up differently, so this is NOT agreement.
    Hiding the row, or naming it 'settled' (which reads as the disagreement
    having gone away), would make the panel look like the two agree."""
    pp = _positions(tmp_path, [{"instrument_id": "BMNR", "market_value": 3400.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "BMNR", "instrument_type": "stock",
         "sector": "Crypto - Miners / Treasury", "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04", "sector_settled": "2026-10-04"},
    ])

    d = exposure.sector_disagreements(positions_path=pp)
    row = d[d["instrument_id"] == "BMNR"].iloc[0]
    assert row["state"] == "kept-mine"
    # still carries both labels — shown, not hidden, and not "settled" (which
    # reads as the disagreement having gone away rather than been decided)
    assert row["mine"] == "Crypto - Miners / Treasury"
    assert row["vendor"] == "Financials"
    assert "settled" not in set(d["state"])


def test_a_blank_own_sector_is_its_own_state_not_unmappable(tmp_path, monkeypatch):
    """F1. A holding the sync auto-added and the classification refresh then
    dated has a perfectly good vendor label and NO sector of his own at all —
    that is a different problem from a label data/sectors.csv rejects, and
    the old code folded the two together into a sentence that, for a blank
    label, named nothing."""
    pp = _positions(tmp_path, [{"instrument_id": "NEWCO", "market_value": 250.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "NEWCO", "instrument_type": "stock", "sector": "",
         "provider_sector": "Technology", "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance"},
    ])

    d = exposure.sector_disagreements(positions_path=pp)
    row = d[d["instrument_id"] == "NEWCO"].iloc[0]
    assert row["state"] == "no-own-sector"
    assert row["mine"] == ""
    assert row["vendor"] == "Technology"


def test_an_agreement_is_never_reported_even_when_settled(tmp_path, monkeypatch):
    """F6. sector_settled can be filled from an old disagreement and then
    the vendor's own classification catches up to his — there is nothing
    left to disagree about, settled or not, so no row at all."""
    pp = _positions(tmp_path, [{"instrument_id": "BMNR", "market_value": 3400.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "BMNR", "instrument_type": "stock",
         "sector": "Crypto - Miners / Treasury",
         "provider_sector": "Crypto - Miners / Treasury",
         "provider_sector_as_of": "2026-10-06", "sector_settled": "2026-10-04"},
    ])

    d = exposure.sector_disagreements(positions_path=pp)
    assert "BMNR" not in set(d["instrument_id"])


def test_a_missing_settled_or_source_column_refuses_with_a_fix(
    tmp_path, monkeypatch
):
    """F5. No missing-column guard used to exist — a caller's narrower
    instruments frame would raise a bare KeyError naming neither the problem
    nor the fix."""
    from src import instruments

    pp = _positions(tmp_path, [{"instrument_id": "BMNR", "market_value": 3400.0}])
    narrow = pd.DataFrame([{
        "instrument_id": "BMNR", "instrument_type": "stock",
        "sector": "Crypto - Miners / Treasury", "provider_sector": "Financials",
        "provider_sector_as_of": "2026-10-04",
        # no sector_settled, no provider_sector_source
    }])
    monkeypatch.setattr(instruments, "load", lambda *a, **k: narrow)

    with pytest.raises(ValueError) as e:
        exposure.sector_disagreements(positions_path=pp)
    msg = str(e.value)
    assert "sector_settled" in msg and "provider_sector_source" in msg


# ─── money must never fall out of the totals ───

def test_an_etf_with_no_composition_is_reported_not_dropped(tmp_path, monkeypatch):
    """
    Silently omitting it would under-report every sector it belongs to, and
    the totals would still look tidy.
    """
    cp = _comp(tmp_path, QQQ_ROWS)
    pp = _positions(tmp_path, [{"instrument_id": "NEWETF", "market_value": 500.0}])
    _instruments(monkeypatch, [{"instrument_id": "NEWETF", "instrument_type": "etf",
                                "sector": "", "cluster_id": "Core US"}])

    s = exposure.by_sector(cp, pp)
    assert s.attrs["uncovered_etfs"] == ["NEWETF"]
    assert s.set_index("sector").loc["Other", "market_value"] == 500.0  # still counted

    cov = exposure.coverage(cp, pp).set_index("ticker")
    assert cov.loc["NEWETF", "source_quality"] == "MISSING"
    assert "no sector total" in cov.loc["NEWETF", "why"]


def test_coverage_on_a_book_with_zero_held_etfs_is_empty_not_a_crash(
    tmp_path, monkeypatch
):
    """`pd.DataFrame([])` has no `market_value` column to sort by, so a book
    holding no ETF at all used to raise `KeyError: 'market_value'` instead of
    reporting that there is nothing to say. `ops/data_gaps.py` carried a
    call-site workaround for exactly this until this fix landed (Task 7)."""
    cp = _comp(tmp_path, QQQ_ROWS)
    pp = _positions(tmp_path, [{"instrument_id": "NVDA", "market_value": 500.0}])
    _instruments(monkeypatch, [{"instrument_id": "NVDA", "instrument_type": "stock",
                                "sector": "Tech - Semiconductors",
                                "cluster_id": "AI Infrastructure"}])

    cov = exposure.coverage(cp, pp)
    assert cov.empty
    assert list(cov.columns) == exposure.COVERAGE_COLUMNS


def test_coverage_on_a_book_with_no_positions_at_all_is_also_empty(tmp_path):
    """`_holdings()` itself returns a columnless frame when nothing is held
    at all — a stricter version of the ETF-free case above, and the shape
    `held["type"]` could otherwise be asked to index into."""
    cp = _comp(tmp_path, QQQ_ROWS)
    pp = tmp_path / "positions.csv"
    pd.DataFrame(columns=["instrument_id", "market_value"]).to_csv(pp, index=False)

    cov = exposure.coverage(cp, pp)
    assert cov.empty
    assert list(cov.columns) == exposure.COVERAGE_COLUMNS


def test_partial_weights_are_rejected(tmp_path):
    """Weights summing to 0.6 would under-report by 40% and look fine."""
    cp = _comp(tmp_path, [
        {"ticker": "QQQ", "sector": "Tech - Software", "weight": 0.6,
         "top_holding": "", "source": "issuer", "source_quality": "issuer",
         "as_of": ""},
    ])
    with pytest.raises(pa.errors.SchemaErrors) as e:
        exposure.load(cp)
    assert "must sum to 1.0" in claims.explain_schema_error(e.value)


# ─── confidence travels with the number ───

def test_estimated_share_is_reported(tmp_path, monkeypatch):
    """A seventh of the book resting on undated guesses; a number that hides that lies."""
    cp = _comp(tmp_path, QQQ_ROWS + [
        {"ticker": "XLK", "sector": "Tech - Software", "weight": 1.0,
         "top_holding": "", "source": "SPDR issuer", "source_quality": "issuer",
         "as_of": "2026-07-01"},
    ])
    pp = _positions(tmp_path, [
        {"instrument_id": "QQQ", "market_value": 1000.0},
        {"instrument_id": "XLK", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "QQQ", "instrument_type": "etf", "sector": "",
         "cluster_id": "Core US"},
        {"instrument_id": "XLK", "instrument_type": "etf", "sector": "",
         "cluster_id": "Technology (broad)"},
    ])
    s = exposure.by_sector(cp, pp).set_index("sector")
    # Technology = 500 estimated (QQQ) + 1000 issuer (XLK)
    assert s.loc["Technology", "estimated_value"] == 500.0
    assert s.loc["Technology", "pct_estimated"] == pytest.approx(33.3, abs=0.1)


def test_a_guessed_date_is_never_invented(tmp_path):
    """Most issuer pages carry no date. Blank is honest; a made-up date is not."""
    cp = _comp(tmp_path, [
        {"ticker": "XLK", "sector": "Tech - Software", "weight": 1.0,
         "top_holding": "", "source": "SPDR issuer", "source_quality": "issuer",
         "as_of": ""},
    ])
    assert exposure.load(cp)["as_of"].iloc[0] == ""


def test_issuer_by_hand_is_issuer_data_not_an_estimate(tmp_path, monkeypatch):
    """The value that exists so MAGS can be hand-checked without being lied
    about.

    `ops/data_gaps.py` tells him to copy a fund's holdings off the issuer's
    own page and date them. There was no value for that: `issuer` hid that
    nobody fetched them, and `estimate` would have flipped the fund's whole
    market value into `pct_estimated` forever — for figures that are not
    estimates at all. Three documents gave two different answers until this
    value existed.
    """
    cp = _comp(tmp_path, [
        {"ticker": "MAGS", "sector": "Technology", "weight": 1.0,
         "top_holding": "NVDA", "source": "Roundhill page",
         "source_quality": "issuer-by-hand", "as_of": "2026-09-30"},
    ])
    pp = _positions(tmp_path, [{"instrument_id": "MAGS", "market_value": 1000.0}])
    _instruments(monkeypatch, [{"instrument_id": "MAGS", "instrument_type": "etf",
                                "sector": "", "cluster_id": "Core US"}])

    s = exposure.by_sector(cp, pp).set_index("sector")
    assert s.loc["Technology", "estimated_value"] == 0.0
    assert s.loc["Technology", "pct_estimated"] == 0.0

    cov = exposure.coverage(cp, pp).set_index("ticker")
    assert cov.loc["MAGS", "source_quality"] == "issuer-by-hand"
    assert "typed in by hand" in cov.loc["MAGS", "why"]
    assert "estimate" not in cov.loc["MAGS", "why"]


def test_bad_source_quality_names_the_vocab_file(tmp_path):
    cp = _comp(tmp_path, [
        {"ticker": "XLK", "sector": "Tech - Software", "weight": 1.0,
         "top_holding": "", "source": "somewhere", "source_quality": "vibes",
         "as_of": ""},
    ])
    with pytest.raises(pa.errors.SchemaErrors) as e:
        exposure.load(cp)
    assert "ops/vocab/composition_source" in claims.explain_schema_error(e.value)


# ─── A position nothing knows about must not be dropped silently ─────────


def test_an_unknown_instrument_is_reported_not_silently_dropped(tmp_path, monkeypatch):
    """`_holdings()` skipped any instrument_id missing from instruments.csv,
    removing it from the numerator AND the NAV denominator, so every
    `pct_of_nav` in the book silently rescaled.

    `holding.consider()` (src/holding.py:127-140) refuses to do this and says
    why: ibkr_sync writes a brand-new lot with "AUTO-ADDED — add it to
    instruments.csv", so an unknown id is exactly how a freshly bought ticker
    arrives. That is the newest trade, the one most worth flagging.

    Same repo, same situation, opposite decisions — until now.
    """
    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "QQQ", "instrument_id": "QQQ", "market_value": "1000"},
        {"ticker": "NEWCO", "instrument_id": "NEWCO", "market_value": "250"},
    ]).to_csv(pos, index=False)

    unknown = exposure.unknown_holdings(positions_path=pos)
    assert list(unknown["instrument_id"]) == ["NEWCO"]
    assert float(unknown.iloc[0]["mv"]) == 250.0


# ─── Task 5: look-through exposure to a COMPANY, as a floor ───────────────


def test_true_exposure_adds_the_direct_holding_to_what_the_funds_hold(tmp_path,
                                                                     monkeypatch):
    """The panel that justified itself in the design session: a name he barely
    holds directly can be one of his largest real exposures, and nothing in the
    record showed it."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "shares": 1,
         "cost_basis": 100, "last_price": 100, "currency": "USD",
         "market_value": 100, "priced_at": "2026-10-02"},
        {"ticker": "FUND", "instrument_id": "FUND", "shares": 1,
         "cost_basis": 900, "last_price": 900, "currency": "USD",
         "market_value": 900, "priced_at": "2026-10-02"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock",
         "sector": "Tech - Semiconductors", "cluster_id": "AI",
         "price_ticker": "", "av_ticker": "", "note_ref": ""},
        {"instrument_id": "FUND", "name": "A Fund", "instrument_type": "etf",
         "sector": "", "cluster_id": "AI", "price_ticker": "",
         "av_ticker": "", "note_ref": ""},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "FUND", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "FUND", "holding": "MSFT", "holding_name": "MICROSOFT CORP",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["NVDA", "direct_value"] == pytest.approx(100.0)
    assert out.at["NVDA", "via_funds_value"] == pytest.approx(450.0)
    assert out.at["NVDA", "market_value"] == pytest.approx(550.0)
    assert out.at["NVDA", "funds"] == "FUND"
    assert not bool(out.at["NVDA", "is_floor"])


def test_the_same_company_under_two_spellings_is_one_row(tmp_path, monkeypatch):
    """`005930 KS` (iShares), `005930` (bare) and `005930.KS` (his book) are
    one company. Split, his real Samsung exposure reads short — the money he
    bought on purpose counted as a different thing from the chunk his funds
    bought for him.

    market.holding_instrument() already joins all three TO HIS BOOK. This test
    is that by_company() uses it rather than grouping on the raw symbol."""
    pos = _positions(tmp_path, [
        {"ticker": "005930.KS", "instrument_id": "005930.KS",
         "market_value": 100.0},
        {"ticker": "DRAM", "instrument_id": "DRAM", "market_value": 500.0},
        {"ticker": "EEM", "instrument_id": "EEM", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "005930.KS", "name": "Samsung Electronics",
         "instrument_type": "stock"},
        {"instrument_id": "DRAM", "name": "A Korea Fund", "instrument_type": "etf"},
        {"instrument_id": "EEM", "name": "An EM Fund", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "DRAM", "holding": "005930 KS",
         "holding_name": "SAMSUNG ELECTRONICS CO LTD", "weight": 1.0,
         "coverage": 1.0, "source": "issuer file", "as_of": "2026-10-01",
         "as_of_kind": "published"},
        {"ticker": "EEM", "holding": "005930",
         "holding_name": "SAMSUNG ELECTRONICS CO LTD", "weight": 1.0,
         "coverage": 1.0, "source": "issuer file", "as_of": "2026-10-01",
         "as_of_kind": "published"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert "005930.KS" in out.index
    assert out.at["005930.KS", "direct_value"] == pytest.approx(100.0)
    assert out.at["005930.KS", "via_funds_value"] == pytest.approx(1000.0)
    assert set(out.at["005930.KS", "funds"].split(", ")) == {"DRAM", "EEM"}
    # the two spellings never show up as rows of their own
    assert "005930 KS" not in out.index
    assert "005930" not in out.index


def test_a_different_share_class_stays_a_different_row(tmp_path, monkeypatch):
    """`005935` is Samsung's preferred share, a different security. The join
    must not reach it — the safety of the numeric branch in
    holding_instrument() is exactly that it compares the number, not the name."""
    pos = _positions(tmp_path, [
        {"ticker": "005930.KS", "instrument_id": "005930.KS",
         "market_value": 100.0},
        {"ticker": "EEM", "instrument_id": "EEM", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "005930.KS", "name": "Samsung Electronics",
         "instrument_type": "stock"},
        {"instrument_id": "EEM", "name": "An EM Fund", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "EEM", "holding": "005935",
         "holding_name": "SAMSUNG ELECTRONICS CO LTD PFD", "weight": 1.0,
         "coverage": 1.0, "source": "issuer file", "as_of": "2026-10-01",
         "as_of_kind": "published"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert "005930.KS" in out.index
    assert out.at["005930.KS", "direct_value"] == pytest.approx(100.0)
    assert out.at["005930.KS", "via_funds_value"] == pytest.approx(0.0)
    assert "005935" in out.index
    assert out.at["005935", "instrument_id"] == ""
    assert out.at["005935", "via_funds_value"] == pytest.approx(500.0)


def test_a_fund_whose_stored_weights_are_partial_makes_the_answer_a_floor(
        tmp_path, monkeypatch):
    """Providers return roughly a fund's top ten. For a broad fund that is
    about half of it, so a total built across it is 'at least', never a
    figure.

    `coverage` is deliberately 1.0 here while `weight` is 0.5 — the real
    shape of VTI and IWM in his own book, where the source claims it covered
    the whole fund but the small-weight floor dropped most of the stored
    rows afterward. Round 0's fixture had weight and coverage both 0.5,
    which could not tell a reader (or a future edit) which column actually
    drives `by_company()`'s floor test; this one can (C-1, 2026-10-06
    review)."""
    pos = _positions(tmp_path, [
        {"ticker": "BROAD", "instrument_id": "BROAD", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BROAD", "name": "Broad Fund", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "BROAD", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file, long tail "
         "dropped below the 0.05% floor", "as_of": "2026-10-01",
         "as_of_kind": "published"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert bool(out.at["AAPL", "is_floor"])
    unaccounted_key = f"BROAD {exposure.UNACCOUNTED}"
    assert unaccounted_key in out.index
    assert out.at[unaccounted_key, "via_funds_value"] == pytest.approx(500.0)
    assert bool(out.at[unaccounted_key, "is_floor"])


def test_a_fund_with_nothing_but_companies_in_it_is_not_a_floor(tmp_path,
                                                               monkeypatch):
    """An issuer's own complete holdings file accounts for the fund. Calling
    that a floor would make every number on the panel look like a guess."""
    pos = _positions(tmp_path, [
        {"ticker": "MAGS", "instrument_id": "MAGS", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "MAGS", "name": "Magnificent Seven Fund",
         "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "MAGS", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "MAGS", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert not bool(out.at["NVDA", "is_floor"])
    assert not bool(out.at["AAPL", "is_floor"])
    unaccounted_key = f"MAGS {exposure.UNACCOUNTED}"
    assert unaccounted_key not in out.index


def test_a_fund_that_holds_no_companies_is_correct_not_missing(tmp_path,
                                                              monkeypatch):
    """SGOV holds T-bills; IBIT holds bitcoin; SLV holds bullion; DBA holds
    futures. No holdings row for them is the right answer. Counting them as
    uncovered makes the floor look worse than it is; counting them as covered
    hides real money. Their own state, their own line on the page."""
    pos = _positions(tmp_path, [
        {"ticker": "SGOV", "instrument_id": "SGOV", "market_value": 500.0},
        {"ticker": "GHOST", "instrument_id": "GHOST", "market_value": 300.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "SGOV", "name": "iShares 0-3 Month Treasury Bond",
         "instrument_type": "etf"},
        {"instrument_id": "GHOST", "name": "A Fund Nobody Fetched",
         "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [])
    cov = exposure.company_coverage(positions_path=pos,
                                    holdings_path=hold).set_index("ticker")
    assert cov.at["SGOV", "state"] == "no-companies"
    assert "Treasury bills" in cov.at["SGOV", "why"]
    assert cov.at["GHOST", "state"] == "missing"
    assert "no holdings on file" in cov.at["GHOST", "why"]
    # a hole and a correct absence must never share the same accounted_pct
    # by accident of one being used to mean two different things
    assert cov.at["SGOV", "accounted_pct"] == 0.0
    assert cov.at["GHOST", "accounted_pct"] == 0.0


def test_a_funds_unaccounted_remainder_is_named_and_not_spread(tmp_path,
                                                              monkeypatch):
    """The part of a fund its stored holdings do not reach is money with no
    company. Spreading it pro-rata would invent exposure; dropping it silently
    would make the percentages add to less than 100 with nothing saying so."""
    pos = _positions(tmp_path, [
        {"ticker": "BROAD", "instrument_id": "BROAD", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BROAD", "name": "Broad Fund", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "BROAD", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.3, "coverage": 0.5, "source": "Yahoo top holdings",
         "as_of": "2026-10-01", "as_of_kind": "read"},
        {"ticker": "BROAD", "holding": "MSFT", "holding_name": "MICROSOFT CORP",
         "weight": 0.2, "coverage": 0.5, "source": "Yahoo top holdings",
         "as_of": "2026-10-01", "as_of_kind": "read"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["AAPL", "via_funds_value"] == pytest.approx(300.0)
    assert out.at["MSFT", "via_funds_value"] == pytest.approx(200.0)
    unaccounted_key = f"BROAD {exposure.UNACCOUNTED}"
    assert out.at[unaccounted_key, "via_funds_value"] == pytest.approx(500.0)
    # named rows plus the unaccounted row reconstruct the whole fund: none of
    # the untracked 500 leaked pro-rata into AAPL or MSFT's share, and none
    # of it silently vanished either.
    assert (out.at["AAPL", "market_value"] + out.at["MSFT", "market_value"]
            + out.at[unaccounted_key, "market_value"]) == pytest.approx(1000.0)


def test_company_exposure_percentages_are_of_the_basis(tmp_path, monkeypatch):
    """`pct_of_basis` divides by the whole basis, not by the sum of company
    rows alone — the same total `book.nav()` reports."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 300.0},
        {"ticker": "AAPL", "instrument_id": "AAPL", "market_value": 700.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
        {"instrument_id": "AAPL", "name": "Apple", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["NVDA", "pct_of_basis"] == pytest.approx(30.0)
    assert out.at["AAPL", "pct_of_basis"] == pytest.approx(70.0)


def test_two_unheld_symbols_for_one_company_are_reported(tmp_path, monkeypatch):
    """The dangerous direction. A company he does not hold directly has no id
    to join to, so two funds spelling it differently split it — which
    understates overlap and OVERSTATES how many bets he has. Nothing can
    safely merge them (letting the join see letters would marry Estée Lauder
    to EssilorLuxottica), so the page states the size of the effect instead."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 300.0},
        {"ticker": "EPI", "instrument_id": "EPI", "market_value": 400.0},
        {"ticker": "DEM", "instrument_id": "DEM", "market_value": 300.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
        {"instrument_id": "EPI", "name": "WisdomTree India Fund",
         "instrument_type": "etf"},
        {"instrument_id": "DEM", "name": "WisdomTree EM Fund",
         "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "EPI", "holding": "500325",
         "holding_name": "RELIANCE INDUSTRIES LTD", "weight": 0.08,
         "coverage": 0.9, "source": "issuer file", "as_of": "2026-10-01",
         "as_of_kind": "published"},
        {"ticker": "DEM", "holding": "RELIANCE IN",
         "holding_name": "RELIANCE INDUSTRIES LTD", "weight": 0.03,
         "coverage": 0.8, "source": "issuer file", "as_of": "2026-10-01",
         "as_of_kind": "published"},
        {"ticker": "EPI", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.05, "coverage": 0.9, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    out = exposure.symbol_collisions(positions_path=pos, holdings_path=hold)
    assert list(out["name"]) == ["RELIANCE INDUSTRIES"]
    row = out.set_index("name").loc["RELIANCE INDUSTRIES"]
    assert set(row["keys"].split(", ")) == {"500325", "RELIANCE IN"}
    assert set(row["funds"].split(", ")) == {"DEM", "EPI"}
    # NVDA is held directly, so it joins to his own instrument and never
    # shows up here even though it, too, appears in only one fund.
    assert "NVDA" not in "".join(out["keys"])


def test_symbol_collisions_ignores_a_fund_he_does_not_hold(tmp_path, monkeypatch):
    """I-4 (2026-10-06 review): before the held-funds filter, SPY (filed for
    the benchmark panel) and any fund he has since sold could pad the
    collision count with ambiguity that has nothing to do with his own book."""
    pos = _positions(tmp_path, [
        {"ticker": "EPI", "instrument_id": "EPI", "market_value": 400.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "EPI", "name": "WisdomTree India Fund",
         "instrument_type": "etf"},
        {"instrument_id": "SPY", "name": "SPDR S&P 500 ETF Trust",
         "instrument_type": "etf", "status": "benchmark"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "EPI", "holding": "500325",
         "holding_name": "RELIANCE INDUSTRIES LTD", "weight": 0.08,
         "coverage": 0.9, "source": "issuer file", "as_of": "2026-10-01",
         "as_of_kind": "published"},
        # SPY is not a position — this row must be ignored entirely, even
        # though it would otherwise "collide" with EPI's spelling above.
        {"ticker": "SPY", "holding": "RELIANCE IN",
         "holding_name": "RELIANCE INDUSTRIES LTD", "weight": 0.01,
         "coverage": 1.0, "source": "SPDR holdings file", "as_of": "2026-10-05",
         "as_of_kind": "published"},
    ])
    out = exposure.symbol_collisions(positions_path=pos, holdings_path=hold)
    assert out.empty


# ─── SPY is in the market tables now, and must never be mistaken for a holding ──


def test_spy_never_reaches_by_company(tmp_path, monkeypatch):
    """SPY is in `etf_holdings.csv` now too — its own 283 constituent
    companies, fetched for the benchmark panel — but it carries
    `status=benchmark` and is never a position. `by_company()` walks what he
    actually holds (`book.holdings()`), never every ticker the holdings table
    happens to have, so none of SPY's constituents can leak into his numbers."""
    pos = _positions(tmp_path, [
        {"ticker": "FUND", "instrument_id": "FUND", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "FUND", "name": "A Fund", "instrument_type": "etf"},
        {"instrument_id": "SPY", "name": "SPDR S&P 500 ETF Trust",
         "instrument_type": "etf", "status": "benchmark"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "FUND", "holding": "MSFT", "holding_name": "MICROSOFT CORP",
         "weight": 1.0, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.07, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.09, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold)
    assert "SPY" not in set(out["key"]) | set(out["instrument_id"])
    assert "AAPL" not in set(out["key"])
    assert "NVDA" not in set(out["key"])
    # MSFT, via the fund he actually holds, is the only money on the page.
    assert set(out["key"]) == {"MSFT"}
    assert float(out.set_index("key").at["MSFT", "market_value"]) == \
        pytest.approx(1000.0)

    cov = exposure.company_coverage(positions_path=pos, holdings_path=hold)
    assert "SPY" not in set(cov["ticker"])


def test_an_exact_fund_that_falls_short_of_1_still_gets_an_unaccounted_row(
        tmp_path, monkeypatch):
    """Found verifying the sum-to-basis invariant (Task 5 review): a fund can
    clear the 0.995 'exact' threshold and still not sum to precisely 1.0 — on
    2026-10-06 QQQ's own stored weights were 0.9975, not 1.0, and fifteen
    other 'exact' funds the same. Gating the remainder row on `floor` alone
    (accounted < 0.995) silently dropped that sliver from every one of them —
    a few dollars book-wide — contradicting this function's own promise
    that dropping it would make the column add to less than the basis with
    nothing saying so."""
    pos = _positions(tmp_path, [
        {"ticker": "FUND", "instrument_id": "FUND", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "FUND", "name": "Nearly Complete Fund",
         "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "FUND", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.997, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert not bool(out.at["AAPL", "is_floor"])   # classified exact, not a floor
    unaccounted_key = f"FUND {exposure.UNACCOUNTED}"
    assert unaccounted_key in out.index
    assert out.at[unaccounted_key, "via_funds_value"] == pytest.approx(3.0)
    assert (out.at["AAPL", "market_value"]
            + out.at[unaccounted_key, "market_value"]) == pytest.approx(1000.0)


def test_a_fund_whose_weights_sum_to_exactly_1_gets_no_phantom_row(tmp_path,
                                                                  monkeypatch):
    """The flip side of the fix above: a fund that really is complete must not
    grow a zero-dollar UNACCOUNTED row from floating-point noise, and a fund a
    hand-typed sheet over-covers (weights summing above 1.0) must not get a
    negative one."""
    pos = _positions(tmp_path, [
        {"ticker": "WHOLE", "instrument_id": "WHOLE", "market_value": 1000.0},
        {"ticker": "OVER", "instrument_id": "OVER", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "WHOLE", "name": "Complete Fund",
         "instrument_type": "etf"},
        {"instrument_id": "OVER", "name": "Over-Covered Fund",
         "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "WHOLE", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "WHOLE", "holding": "MSFT", "holding_name": "MICROSOFT CORP",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "OVER", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.6, "coverage": 1.0, "source": "issuer-by-hand sheet",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "OVER", "holding": "AMD", "holding_name": "AMD INC",
         "weight": 0.401, "coverage": 1.0, "source": "issuer-by-hand sheet",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold)
    assert f"WHOLE {exposure.UNACCOUNTED}" not in set(out["key"])
    assert f"OVER {exposure.UNACCOUNTED}" not in set(out["key"])


# ─── Fix round 1 (2026-10-06 review) ──────────────────────────────────────


def test_company_coverage_floor_reads_stored_weights_not_source_coverage(
        tmp_path, monkeypatch):
    """C-1 (Critical). The reversed ruling of 2026-10-06: the floor test must
    read the STORED company rows, never the source's own `coverage` claim —
    a fund whose source says it covered the fund completely can still have
    named less than half of it once the small-weight floor dropped the tail.
    This fixture is built to DISCRIMINATE: weight sums to 0.80 while coverage
    says 1.0, so a reviewer who swaps `g["weight"].sum()` for
    `g["coverage"].max()` on line ~549 gets a different, wrong answer here —
    verified by actually making that swap; see task-5-report.md, Fix round 1,
    for both outcomes."""
    pos = _positions(tmp_path, [
        {"ticker": "BROAD", "instrument_id": "BROAD", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BROAD", "name": "Broad Fund", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "BROAD", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file, long tail "
         "dropped", "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "BROAD", "holding": "MSFT", "holding_name": "MICROSOFT CORP",
         "weight": 0.3, "coverage": 1.0, "source": "issuer file, long tail "
         "dropped", "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    cov = exposure.company_coverage(positions_path=pos,
                                    holdings_path=hold).set_index("ticker")
    assert cov.at["BROAD", "state"] == "floor"
    assert cov.at["BROAD", "accounted_pct"] == pytest.approx(80.0)
    assert cov.at["BROAD", "source_coverage_pct"] == pytest.approx(100.0)
    assert cov.at["BROAD", "accounted_pct"] != cov.at["BROAD", "source_coverage_pct"]
    assert "floor" in cov.at["BROAD", "why"]


def test_an_overstated_funds_why_says_so_in_plain_words(tmp_path, monkeypatch):
    """I-2. MAGS's own holdings file sums to just over 1.0 — not a code bug,
    a hand-entered sheet whose seven rounded percentages do not quite cancel.
    `accounted_pct` must keep reporting the real, over-100 number (no clamp),
    and `why` must say so instead of reading as a blank, perfect fund — the
    one fund with a known-bad file was the one the report said nothing
    about.

    Round 1's wording ("adds to 100% ... over 100%") self-contradicted the
    moment the real figure rounded back to the number it claimed to exceed
    (review round 2, 2026-10-06). The fixture here reproduces exactly that
    rounding trap — weight sums to 1.001, which is `{:.0%}`-formatted as
    "100%" — so this test pins the dollar-based replacement instead, which
    cannot collapse the same way."""
    pos = _positions(tmp_path, [
        {"ticker": "OVER", "instrument_id": "OVER", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "OVER", "name": "Over-Covered Fund",
         "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "OVER", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.6, "coverage": 1.0, "source": "issuer-by-hand sheet",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "OVER", "holding": "AMD", "holding_name": "AMD INC",
         "weight": 0.401, "coverage": 1.0, "source": "issuer-by-hand sheet",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    cov = exposure.company_coverage(positions_path=pos,
                                    holdings_path=hold).set_index("ticker")
    assert cov.at["OVER", "state"] == "exact"
    assert cov.at["OVER", "accounted_pct"] == pytest.approx(100.1)
    why = cov.at["OVER", "why"]
    assert why != ""
    assert "overstated" in why
    assert "$1.00" in why   # $1,000.00 fund * (1.001 - 1.0) = $1.00
    # The round-1 defect, pinned so it cannot come back: no rounded
    # percentage claim that could collapse into its own threshold.
    assert "100%" not in why


def test_kind_distinguishes_company_from_a_funds_two_kinds_of_bucket(
        tmp_path, monkeypatch):
    """I-3. A consumer must not have to match on the UNACCOUNTED label string
    to tell a real company row from a fund's own bucket — and the two bucket
    kinds mean different things: SGOV's bucket is correct (it owns no
    companies, `is_floor=False`); BROAD's partial-fund bucket is a real gap
    (`is_floor=True`)."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 100.0},
        {"ticker": "BROAD", "instrument_id": "BROAD", "market_value": 1000.0},
        {"ticker": "SGOV", "instrument_id": "SGOV", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
        {"instrument_id": "BROAD", "name": "Broad Fund", "instrument_type": "etf"},
        {"instrument_id": "SGOV", "name": "iShares 0-3 Month Treasury",
         "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "BROAD", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["NVDA", "kind"] == "company"
    assert out.at["AAPL", "kind"] == "company"

    broad_bucket = f"BROAD {exposure.UNACCOUNTED}"
    assert out.at[broad_bucket, "kind"] == "unaccounted"
    assert bool(out.at[broad_bucket, "is_floor"])

    sgov_bucket = f"SGOV {exposure.UNACCOUNTED}"
    assert out.at[sgov_bucket, "kind"] == "no-companies"
    assert not bool(out.at[sgov_bucket, "is_floor"])


def test_is_floor_ors_across_every_contributing_fund(tmp_path, monkeypatch):
    """M-1. A company reached through one EXACT fund and one FLOOR fund must
    come out a floor — `is_floor` is an OR across every fund that touches the
    row, never just the last one processed."""
    pos = _positions(tmp_path, [
        {"ticker": "EXACTFUND", "instrument_id": "EXACTFUND",
         "market_value": 1000.0},
        {"ticker": "FLOORFUND", "instrument_id": "FLOORFUND",
         "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "EXACTFUND", "name": "Exact Fund",
         "instrument_type": "etf"},
        {"instrument_id": "FLOORFUND", "name": "Floor Fund",
         "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "EXACTFUND", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 1.0, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "FLOORFUND", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.3, "coverage": 1.0, "source": "Yahoo top holdings",
         "as_of": "2026-10-01", "as_of_kind": "read"},
    ])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert bool(out.at["NVDA", "is_floor"])


def test_by_company_on_an_empty_book_is_empty_not_a_crash(tmp_path):
    """M-2. No positions at all must report nothing, not raise."""
    pos = tmp_path / "positions.csv"
    pd.DataFrame(columns=["instrument_id", "market_value"]).to_csv(pos, index=False)
    hold = _holdings_table(tmp_path, [])
    out = exposure.by_company(positions_path=pos, holdings_path=hold)
    assert out.empty
    assert list(out.columns) == exposure.COMPANY_COLUMNS


# ─── Fix round 2 (2026-10-06 review) ──────────────────────────────────────


def test_a_cash_balance_is_its_own_kind_not_a_company(tmp_path, monkeypatch):
    """FINDING 1 (review round 2). A panel headed "true exposure to one
    name" must not open with the dollar as his biggest single-name bet —
    `USD Cash` and `EUR Cash` were the top two `kind == "company"` rows in
    the live book before this fix. A cash/FX lot still has to count toward
    the total (the column must keep summing to the basis), it simply must
    not be called a company."""
    from src import book

    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 300.0},
        {"ticker": "USD_CASH", "instrument_id": "USD_CASH",
         "market_value": 700.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
        {"instrument_id": "USD_CASH", "name": "USD Cash",
         "instrument_type": "cash"},
    ])
    hold = _holdings_table(tmp_path, [])
    out = exposure.by_company(positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["USD_CASH", "kind"] == "cash"
    assert out.at["NVDA", "kind"] == "company"
    assert "USD_CASH" not in set(out[out["kind"] == "company"].index)
    assert float(out["market_value"].sum()) == pytest.approx(
        book.nav("everything", positions_path=pos))


# ─── Task 7 addition: does a fund's own weights sum to the whole fund? ─────


def test_weight_residue_reports_a_fund_over_in_both_tables(tmp_path, monkeypatch):
    """MAGS's real shape: a handful of large, hand-typed rows that round to
    slightly more than the whole fund in BOTH tables. Neither stored weight
    is touched — the residue is reported, not corrected."""
    cp = _comp(tmp_path, [
        {"ticker": "OVR", "sector": "Tech - Software", "weight": 0.5004,
         "top_holding": "AAA", "source": "issuer sheet",
         "source_quality": "issuer-by-hand", "as_of": "2026-10-04"},
        {"ticker": "OVR", "sector": "Tech - Hardware / Network", "weight": 0.5004,
         "top_holding": "BBB", "source": "issuer sheet",
         "source_quality": "issuer-by-hand", "as_of": "2026-10-04"},
    ])
    hp = _holdings_table(tmp_path, [
        {"ticker": "OVR", "holding": "AAA", "holding_name": "A Co",
         "weight": 0.5004, "coverage": 1.0, "source": "issuer sheet",
         "as_of": "2026-10-04", "as_of_kind": "published"},
        {"ticker": "OVR", "holding": "BBB", "holding_name": "B Co",
         "weight": 0.5004, "coverage": 1.0, "source": "issuer sheet",
         "as_of": "2026-10-04", "as_of_kind": "published"},
    ])
    pp = _positions(tmp_path, [{"instrument_id": "OVR", "market_value": 10000.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "OVR", "instrument_type": "etf"},
    ])

    out = exposure.weight_residue(cp, hp, pp).set_index("table")
    for table in ("etf_compositions.csv", "etf_holdings.csv"):
        row = out.loc[table]
        assert row["ticker"] == "OVR"
        assert row["direction"] == "over"
        assert row["weight_sum"] == pytest.approx(1.0008)
        assert row["dollar_effect"] == pytest.approx(8.0, abs=0.01)   # 0.0008 * 10000


def test_weight_residue_reports_a_fund_under_in_both_tables(tmp_path, monkeypatch):
    """The same rounding slip, the other way: hand-typed rows that fall
    slightly short of the whole fund instead of over it."""
    cp = _comp(tmp_path, [
        {"ticker": "UND", "sector": "Tech - Software", "weight": 0.4996,
         "top_holding": "AAA", "source": "issuer sheet",
         "source_quality": "issuer-by-hand", "as_of": "2026-10-04"},
        {"ticker": "UND", "sector": "Tech - Hardware / Network", "weight": 0.4996,
         "top_holding": "BBB", "source": "issuer sheet",
         "source_quality": "issuer-by-hand", "as_of": "2026-10-04"},
    ])
    hp = _holdings_table(tmp_path, [
        {"ticker": "UND", "holding": "AAA", "holding_name": "A Co",
         "weight": 0.4, "coverage": 1.0, "source": "issuer sheet",
         "as_of": "2026-10-04", "as_of_kind": "published"},
        {"ticker": "UND", "holding": "BBB", "holding_name": "B Co",
         "weight": 0.3, "coverage": 1.0, "source": "issuer sheet",
         "as_of": "2026-10-04", "as_of_kind": "published"},
        {"ticker": "UND", "holding": "CCC", "holding_name": "C Co",
         "weight": 0.2993, "coverage": 1.0, "source": "issuer sheet",
         "as_of": "2026-10-04", "as_of_kind": "published"},
    ])
    pp = _positions(tmp_path, [{"instrument_id": "UND", "market_value": 10000.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "UND", "instrument_type": "etf"},
    ])

    out = exposure.weight_residue(cp, hp, pp).set_index("table")

    comp_row = out.loc["etf_compositions.csv"]
    assert comp_row["direction"] == "under"
    assert comp_row["weight_sum"] == pytest.approx(0.9992)
    assert comp_row["dollar_effect"] == pytest.approx(-8.0, abs=0.01)

    hold_row = out.loc["etf_holdings.csv"]
    assert hold_row["direction"] == "under"
    assert hold_row["weight_sum"] == pytest.approx(0.9993)
    # every holding clears the floor-immunity bar (0.05), so this small,
    # genuine shortfall is reported rather than swallowed as a floor
    assert hold_row["dollar_effect"] == pytest.approx(-7.0, abs=0.01)


def test_weight_residue_does_not_mistake_a_real_floor_for_a_residue(
    tmp_path, monkeypatch
):
    """IWM's real shape: a provider fetch that claims near-complete coverage
    but whose stored rows undershoot it by a lot because a long tail of
    positions each sit below the small-weight floor. This is
    `unnamed_money()`'s and `company_coverage()`'s subject, not this
    function's — reporting it here would be a second, noisier opinion on
    the same fact."""
    cp = _comp(tmp_path, [])
    hp = _holdings_table(tmp_path, [
        {"ticker": "FLOORED", "holding": "AAA", "holding_name": "A Co",
         "weight": 0.5, "coverage": 0.999, "source": "Yahoo top holdings",
         "as_of": "2026-10-04", "as_of_kind": "read"},
        {"ticker": "FLOORED", "holding": "BBB", "holding_name": "B Co",
         "weight": 0.3, "coverage": 0.999, "source": "Yahoo top holdings",
         "as_of": "2026-10-04", "as_of_kind": "read"},
        # a long-tail position under the floor-immunity bar: evidence a real
        # small-weight floor was in play for this fund
        {"ticker": "FLOORED", "holding": "CCC", "holding_name": "C Co",
         "weight": 0.001, "coverage": 0.999, "source": "Yahoo top holdings",
         "as_of": "2026-10-04", "as_of_kind": "read"},
    ])
    pp = _positions(tmp_path, [{"instrument_id": "FLOORED", "market_value": 5000.0}])
    _instruments(monkeypatch, [
        {"instrument_id": "FLOORED", "instrument_type": "etf"},
    ])

    out = exposure.weight_residue(cp, hp, pp)
    assert out.empty, (
        "a fund with a tiny stored holding is evidence of a real small-"
        "weight floor — its under-coverage belongs to unnamed_money(), "
        "never to weight_residue()"
    )


def test_weight_residue_reports_nothing_for_a_fund_that_sums_cleanly(
    tmp_path, monkeypatch
):
    cp = _comp(tmp_path, QQQ_ROWS)   # 0.5 + 0.5 exactly
    pp = _positions(tmp_path, [{"instrument_id": "QQQ", "market_value": 1000.0}])
    _instruments(monkeypatch, [{"instrument_id": "QQQ", "instrument_type": "etf"}])

    out = exposure.weight_residue(cp, tmp_path / "etf_holdings.csv", pp)
    assert out.empty
    assert list(out.columns) == exposure.WEIGHT_RESIDUE_COLUMNS


# ─── Task 8: against the S&P — by sector, and company by company ─────────


def test_the_benchmark_column_is_absent_with_a_reason_not_with_zeroes(tmp_path):
    """A zero in a difference column reads as 'the S&P holds none of this',
    which is a false statement about the benchmark rather than a missing
    number. Blank reads as nothing to say. Both are worse than the sentence."""
    b = exposure.benchmark_weights(path=_comp(tmp_path, []))
    assert b.empty
    assert "/market" in b.attrs["why"]


def test_the_benchmark_rolls_up_the_same_way_his_book_does(tmp_path):
    """Comparing a parent sector against a sub-sector would be a difference
    between two different questions."""
    rows = [
        {"ticker": "SPY", "sector": "Tech - Software", "weight": 0.25,
         "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
         "source_quality": "vendor", "as_of": "2026-10-06"},
        {"ticker": "SPY", "sector": "Tech - Semiconductors", "weight": 0.15,
         "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
         "source_quality": "vendor", "as_of": "2026-10-06"},
        {"ticker": "SPY", "sector": "Financials - Payments", "weight": 0.6,
         "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
         "source_quality": "vendor", "as_of": "2026-10-06"},
    ]
    b = exposure.benchmark_weights(path=_comp(tmp_path, rows)).set_index("sector")
    # Two Tech sub-sectors must land in ONE Technology row, summed — never
    # two rows that would leave a reader to compare a sub-sector against his
    # book's parent sector.
    assert "Tech - Software" not in b.index
    assert "Tech - Semiconductors" not in b.index
    assert b.at["Technology", "weight_pct"] == pytest.approx(40.0)
    assert b.at["Financials", "weight_pct"] == pytest.approx(60.0)


def test_the_benchmark_is_never_counted_as_a_holding(tmp_path, monkeypatch):
    """SPY has status=benchmark and is not held. Its composition and holdings
    rows must not reach by_sector(), by_company(), effective_bets() or any
    basis — tests/test_refresh_market.py pins the fetcher's half of this; this
    is the reader's half, and this task is the first to put SPY rows in both
    tables, so it is the first time the reader's half can actually fail."""
    pos = _positions(tmp_path, [
        {"ticker": "FUND", "instrument_id": "FUND", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "FUND", "name": "A Fund", "instrument_type": "etf"},
        {"instrument_id": "SPY", "name": "SPDR S&P 500 ETF Trust",
         "instrument_type": "etf", "status": "benchmark"},
    ])
    cp = _comp(tmp_path, [
        {"ticker": "SPY", "sector": "Technology", "weight": 1.0,
         "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
         "source_quality": "vendor", "as_of": "2026-10-06"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "FUND", "holding": "MSFT", "holding_name": "MICROSOFT CORP",
         "weight": 1.0, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.07, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])

    sectors = exposure.by_sector(cp, pos)
    # SPY's own Technology row would double the sector's market value if it
    # ever leaked in — FUND's $1,000 is the whole book, and the sector total
    # must equal exactly that, not that plus SPY's.
    assert sectors["market_value"].sum() == pytest.approx(1000.0)

    companies = exposure.by_company(positions_path=pos, holdings_path=hold)
    assert "SPY" not in set(companies["key"]) | set(companies["instrument_id"])
    assert "AAPL" not in set(companies["key"])

    # Positive control: the SAME two tables, read on purpose by the two
    # benchmark functions, are not simply unreachable in this fixture.
    assert not exposure.benchmark_weights(path=cp).empty
    assert not exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).empty


def test_the_difference_is_stated_as_a_difference(tmp_path, monkeypatch):
    """There are no sector caps or cluster targets. 'Technology is 12 points
    above the S&P' is a fact; 'Technology should be 25%' is advice."""
    pos = _positions(tmp_path, [
        {"ticker": "AAPL", "instrument_id": "AAPL", "market_value": 100.0},
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 900.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "AAPL", "name": "Apple", "instrument_type": "stock"},
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "AAPL", "holding_name": "APPLE INC",
         "weight": 0.50, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.02, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    # AAPL: he is light (10% vs the index's 50%) — a NEGATIVE difference,
    # never clamped to zero and never flagged as a problem.
    assert out.at["AAPL", "mine_pct"] == pytest.approx(10.0)
    assert out.at["AAPL", "benchmark_pct"] == pytest.approx(50.0)
    assert out.at["AAPL", "difference"] == pytest.approx(-40.0)
    # NVDA: he is heavy — a POSITIVE difference, not flagged as good either.
    assert out.at["NVDA", "mine_pct"] == pytest.approx(90.0)
    assert out.at["NVDA", "benchmark_pct"] == pytest.approx(2.0)
    assert out.at["NVDA", "difference"] == pytest.approx(88.0)
    # Exactly the columns this function names — nothing added to colour
    # either row a target met or missed (D7).
    assert list(out.reset_index().columns) == exposure.BENCHMARK_COMPANY_COLUMNS


def test_a_company_he_holds_and_the_index_holds_is_compared(tmp_path, monkeypatch):
    """His addition, 2026-10-06. The direct question: am I heavier or lighter
    in this company than the index is."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["NVDA", "state"] == "both"
    assert out.at["NVDA", "mine_pct"] == pytest.approx(100.0)
    assert out.at["NVDA", "benchmark_pct"] == pytest.approx(8.0)
    assert out.at["NVDA", "difference"] == pytest.approx(92.0)


def test_a_company_the_index_holds_and_he_does_not_shows_as_the_index_only(
        tmp_path, monkeypatch):
    """A real fact about his construction, not a missing number: the index owns
    this and he owns none of it. mine_pct is 0.0 because it is zero."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "JPM", "holding_name": "JPMORGAN CHASE & CO",
         "weight": 0.03, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["JPM", "state"] == "only-index"
    assert out.at["JPM", "mine_pct"] == 0.0
    assert out.at["JPM", "benchmark_pct"] == pytest.approx(3.0)
    assert out.at["JPM", "is_floor"] == False  # noqa: E712 — a real bool, not a float/None
    # The bound columns are tight (not blank) when the exact figure is
    # already known — "at most 3.0" is trivially true of an exact 3.0.
    assert out.at["JPM", "benchmark_pct_at_most"] == pytest.approx(3.0)
    assert out.at["JPM", "difference_floor"] == pytest.approx(-3.0)


def test_a_company_below_the_floor_reads_as_a_bound_not_a_confirmed_absence(
        tmp_path, monkeypatch):
    """Fix round 1, C-A — the bug this function existed to avoid, shipped
    anyway. The first version read ASML's absence from SPY's stored rows as
    a confirmed 'the index does not hold this' — true of ASML, but false for
    152 real S&P 500 members measured against the union of his seven held
    Select Sector SPDRs (Akamai among them), because SPY's stored rows stop
    at a 0.05% floor and absence from them is not absence from the index.
    'At most the stored floor, or none' is true of both ASML and Akamai —
    the most this function can honestly say without a source it lacks."""
    pos = _positions(tmp_path, [
        {"ticker": "ASML", "instrument_id": "ASML", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "ASML", "name": "ASML Holding", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        # coverage 1.0 on both rows: SPY's own SOURCE claims the whole fund
        # — the real shape of the SPDR file on file today. TINY sets a
        # realistic small-weight floor (0.05%, SPY's own real figure).
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "TINY", "holding_name": "TINY CO",
         "weight": 0.0005, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["ASML", "state"] == "below-floor"
    # The exact figure is genuinely absent — never a zero standing in for it.
    assert pd.isna(out.at["ASML", "benchmark_pct"])
    assert pd.isna(out.at["ASML", "difference"])
    # The bound: the index holds AT MOST the stored floor of this, or none.
    assert out.at["ASML", "benchmark_pct_at_most"] == pytest.approx(0.05)
    assert out.at["ASML", "difference_floor"] == pytest.approx(99.95)


def test_difference_floor_is_absent_when_his_own_share_is_itself_below_the_floor(
        tmp_path, monkeypatch):
    """Fix round 2, NEW-2. When mine_pct < floor_pct, the true benchmark_pct
    (somewhere in [0, floor_pct)) could be smaller OR bigger than mine_pct —
    the interval straddles zero and no direction is knowable, so stating
    `mine_pct - floor_pct` there would assert he is underweight when the
    truth could just as well be overweight. The overwhelming majority of
    below-floor rows on the real book are this case (a tiny stake reached
    through a broad fund). Only when mine_pct >= floor_pct is every value in
    the interval the same sign — that is the case the column exists for,
    and it is where ASML and Samsung sit."""
    pos = _positions(tmp_path, [
        {"ticker": "BIG", "instrument_id": "BIG", "market_value": 4999.0},
        {"ticker": "SMALL", "instrument_id": "SMALL", "market_value": 1.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BIG", "name": "Big Co", "instrument_type": "stock"},
        {"instrument_id": "SMALL", "name": "Small Co", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "TINY", "holding_name": "TINY CO",
         "weight": 0.0005, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    # floor_pct is 0.05. BIG is 99.98% of basis (>= floor) -> sign
    # determined, bound kept. SMALL is 0.02% of basis (< floor) -> sign
    # undetermined, bound withheld.
    assert out.at["BIG", "state"] == "below-floor"
    assert out.at["BIG", "mine_pct"] > 0.05
    assert out.at["BIG", "difference_floor"] == pytest.approx(99.93)

    assert out.at["SMALL", "state"] == "below-floor"
    assert out.at["SMALL", "mine_pct"] < 0.05
    assert pd.isna(out.at["SMALL", "difference_floor"])
    # benchmark_pct_at_most is a bound on a DIFFERENT quantity (the index's
    # own weight) and stays populated regardless — only the DIRECTIONAL
    # claim (difference_floor) is withheld.
    assert out.at["SMALL", "benchmark_pct_at_most"] == pytest.approx(0.05)

    # And SMALL's absent bound must never outrank BIG's measured one.
    out_reset = out.reset_index()
    big_pos = out_reset.index[out_reset["key"] == "BIG"][0]
    small_pos = out_reset.index[out_reset["key"] == "SMALL"][0]
    assert big_pos < small_pos


def test_the_same_company_absent_from_a_partial_index_list_is_unknown_not_absent(
        tmp_path, monkeypatch):
    """The one way this panel could lie outright. If SPY's rows were a ten-name
    floor, every company outside the top ten would read as confirmed absent,
    false for roughly 490 companies. Unlike `below-floor`, there is not even
    a reliable bound here — the index's own SOURCE never claimed the whole
    fund, so `benchmark_pct_at_most` stays blank too."""
    pos = _positions(tmp_path, [
        {"ticker": "ASML", "instrument_id": "ASML", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "ASML", "name": "ASML Holding", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        # coverage 0.30: the index's own SOURCE never claimed more than a
        # third of the fund — a ten-name-style floor, not SPY's real file.
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 0.30, "source": "Yahoo top holdings",
         "as_of": "2026-10-05", "as_of_kind": "read"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["ASML", "state"] == "unknown"
    assert pd.isna(out.at["ASML", "benchmark_pct"])
    assert pd.isna(out.at["ASML", "benchmark_pct_at_most"])
    assert pd.isna(out.at["ASML", "difference"])
    assert pd.isna(out.at["ASML", "difference_floor"])


def test_two_index_rows_resolving_to_one_key_add_not_overwrite(tmp_path,
                                                                monkeypatch):
    """Two spellings of one security in the index file — BRK.B and BRK-B,
    both squashing to his own BRKB — must sum their weight. A second row
    silently overwriting the first would understate (or, had the bigger one
    loaded first, overstate) the index's real weight in Berkshire."""
    pos = _positions(tmp_path, [
        {"ticker": "BRKB", "instrument_id": "BRKB", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BRKB", "name": "Berkshire Hathaway",
         "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "BRK.B",
         "holding_name": "BERKSHIRE HATHAWAY INC CL B", "weight": 0.01,
         "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "BRK-B",
         "holding_name": "BERKSHIRE HATHAWAY INC CL B", "weight": 0.02,
         "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    assert "BRKB" in out.index
    # 0.01 + 0.02 = 0.03 -> 3.0%. Overwriting would read 1.0 or 2.0 instead.
    assert out.at["BRKB", "benchmark_pct"] == pytest.approx(3.0)


def test_the_comparison_joins_on_his_spelling(tmp_path, monkeypatch):
    """BRK.B in the index file and BRKB in his book are one company.
    company_key() is the join, the same one by_company() uses, so the two
    panels cannot disagree about what a company is."""
    pos = _positions(tmp_path, [
        {"ticker": "BRKB", "instrument_id": "BRKB", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BRKB", "name": "Berkshire Hathaway",
         "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "BRK.B",
         "holding_name": "BERKSHIRE HATHAWAY INC", "weight": 0.015,
         "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(positions_path=pos, holdings_path=hold)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["key"] == "BRKB"
    assert row["state"] == "both"
    assert row["benchmark_pct"] == pytest.approx(1.5)


def test_his_side_is_a_floor_when_it_comes_through_a_partial_fund(tmp_path,
                                                                 monkeypatch):
    """is_floor travels from by_company(). A difference between a floor and an
    exact figure is itself a floor, in the direction of 'at least this much'."""
    pos = _positions(tmp_path, [
        {"ticker": "BROAD", "instrument_id": "BROAD", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BROAD", "name": "Broad Fund", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        # coverage 1.0, stored weight 0.5 — his OWN side is a floor by
        # by_company()'s existing rule, independent of anything the
        # benchmark's own rows say.
        {"ticker": "BROAD", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file, long tail "
         "dropped below the 0.05% floor", "as_of": "2026-10-01",
         "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["NVDA", "state"] == "both"
    assert bool(out.at["NVDA", "is_floor"])
    # Fix, pre-push review 2026-10-07 (finding 2) — the assertion this test
    # lacked: it checked the state and the flag and said nothing about
    # `difference` itself, so removing the flag's whole effect (publishing
    # an exact difference regardless of `is_floor`, the live bug on 272 of
    # 279 real rows) would have changed nothing on screen and left this
    # test green. mine_pct 50.0 (500/1000), benchmark_pct 8.0 -> the exact
    # subtraction (42.0) is withheld; only the lower-bound
    # `difference_floor` is published.
    assert pd.isna(out.at["NVDA", "difference"])
    assert out.at["NVDA", "difference_floor"] == pytest.approx(42.0)


def test_only_index_is_a_bound_not_a_confirmed_zero_when_his_own_side_is_a_floor(
        tmp_path, monkeypatch):
    """Fix, 2026-10-07 final review (I2) — the mirror of C-A, on his own
    side this time, and the worst defect on the branch mirrored. His side
    (`by_company()`) is ALSO a floor for most held funds — a fund's own
    stored company rows can stop short of the whole fund the same way the
    index's do. A company he reaches only through such a fund, below ITS
    floor, is indistinguishable from one he truly holds none of — exactly
    the live case the reviewer found (DR Horton and Iron Mountain, S&P
    members, reached only through VTI below VTI's own stored floor).

    `mine_pct=0.0` must stop reading as a confirmed fact the moment ANY
    held fund is itself a floor: the true answer is 'at least this much
    underweight', never an exact figure, the same bound/exact split
    `below-floor` already makes for the index's own missing side."""
    pos = _positions(tmp_path, [
        {"ticker": "BROAD", "instrument_id": "BROAD", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BROAD", "name": "Broad Fund", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        # BROAD's own stored rows sum to 0.5 — a floor by by_company()'s
        # existing rule — and never mention JPM at all.
        {"ticker": "BROAD", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file, long tail "
         "dropped below the 0.05% floor", "as_of": "2026-10-01",
         "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "JPM", "holding_name": "JPMORGAN CHASE & CO",
         "weight": 0.03, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")

    assert out.at["JPM", "state"] == "only-index"
    assert out.at["JPM", "mine_pct"] == 0.0        # still the observed figure
    assert bool(out.at["JPM", "is_floor"])          # BUT no longer confirmed
    assert pd.isna(out.at["JPM", "difference"])     # the exact claim: withheld
    # The bound survives: a hidden sliver can only ADD to mine_pct, never
    # subtract, so `mine_pct - benchmark_pct` is still a safe LOWER bound.
    assert out.at["JPM", "difference_floor"] == pytest.approx(-3.0)

    wording = out.attrs["state_wording"]
    assert wording["only-index"] == exposure.ONLY_INDEX_FLOOR_WORDING
    assert wording["only-index"] != ""


def test_only_index_stays_a_confirmed_zero_when_nothing_he_holds_is_a_floor(
        tmp_path, monkeypatch):
    """The negative case, same fix (I2): when NONE of his held funds is a
    floor, `only-index` is exactly as certain as it always was — this must
    not regress into hedging every `only-index` row regardless of his own
    data's quality."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "JPM", "holding_name": "JPMORGAN CHASE & CO",
         "weight": 0.03, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")

    assert out.at["JPM", "state"] == "only-index"
    assert out.at["JPM", "is_floor"] == False  # noqa: E712 — a real bool
    assert out.at["JPM", "difference"] == pytest.approx(-3.0)   # exact, kept
    assert out.attrs["state_wording"]["only-index"] == ""


def test_the_rows_are_ordered_by_how_big_the_difference_is(tmp_path,
                                                           monkeypatch):
    """The index has hundreds of names and he holds dozens. What he cannot see
    anywhere else is where he is most unlike the index, so that is the top of
    the panel — not alphabetical, and not by his own size, which the company
    panel above already ranks."""
    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 10.0},
        {"ticker": "BBB", "instrument_id": "BBB", "market_value": 900.0},
        {"ticker": "CCC", "instrument_id": "CCC", "market_value": 90.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "AAA", "name": "A Co", "instrument_type": "stock"},
        {"instrument_id": "BBB", "name": "B Co", "instrument_type": "stock"},
        {"instrument_id": "CCC", "name": "C Co", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "AAA", "holding_name": "A CO",
         "weight": 0.009, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "BBB", "holding_name": "B CO",
         "weight": 0.001, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "CCC", "holding_name": "C CO",
         "weight": 0.085, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(positions_path=pos, holdings_path=hold)
    # AAA: 1% vs 0.9% (diff 0.1, smallest). CCC: 9% vs 8.5% (diff 0.5). BBB:
    # 90% vs 0.1% (diff 89.9, biggest) — even though BBB is also his biggest
    # HOLDING, which is not what this ordering is about (the company panel
    # above already ranks by that).
    assert list(out["key"]) == ["BBB", "CCC", "AAA"]
    diffs = out["difference"].abs().tolist()
    assert diffs == sorted(diffs, reverse=True)

    limited = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold, limit=2)
    assert list(limited["key"]) == ["BBB", "CCC"]


def test_which_basis_the_comparison_was_made_under_travels_with_it(tmp_path,
                                                                  monkeypatch):
    """His percentages are of the selected basis; the index's are of an
    all-equity fund. On Everything his cash is in his denominator and not in
    the index's, so every difference reads low for an arithmetic reason. The
    frame has to carry which basis it used, or the page cannot say it."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0},
        {"ticker": "USD", "instrument_id": "USD", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
        {"instrument_id": "USD", "name": "USD Cash", "instrument_type": "cash"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    everything = exposure.benchmark_companies(
        "everything", positions_path=pos, holdings_path=hold)
    pure = exposure.benchmark_companies(
        "pure-equity", positions_path=pos, holdings_path=hold)
    assert everything.attrs["basis"] == "everything"
    assert pure.attrs["basis"] == "pure-equity"
    assert "cash" in everything.attrs["compare_note"].lower()
    # His cash sits in HIS denominator only on 'everything' — the exact
    # arithmetic the compare_note is warning about.
    assert (everything.set_index("key").at["NVDA", "mine_pct"]
            == pytest.approx(50.0))
    assert (pure.set_index("key").at["NVDA", "mine_pct"]
            == pytest.approx(100.0))

    with pytest.raises(ValueError):
        exposure.benchmark_companies("not-a-basis", positions_path=pos,
                                     holdings_path=hold)


def test_the_company_comparison_is_also_absent_with_a_reason(tmp_path,
                                                             monkeypatch):
    """Empty index rows get the same treatment as `benchmark_weights()` —
    `attrs['why']` names /market, never a zero that would claim the index
    holds nothing."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [])
    out = exposure.benchmark_companies(positions_path=pos, holdings_path=hold)
    assert out.empty
    assert "/market" in out.attrs["why"]


def test_cash_and_unaccounted_money_are_excluded_but_not_hidden(tmp_path,
                                                                monkeypatch):
    """Cash and a fund's unexplained remainder have no index counterpart and
    must not be compared as companies — but the comparison must not read as
    though it covered his whole book either, so their dollar total is
    reported, never dropped."""
    pos = _positions(tmp_path, [
        {"ticker": "USD", "instrument_id": "USD", "market_value": 200.0},
        {"ticker": "BROAD", "instrument_id": "BROAD", "market_value": 1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "USD", "name": "USD Cash", "instrument_type": "cash"},
        {"instrument_id": "BROAD", "name": "Broad Fund", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "BROAD", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file, long tail "
         "dropped below the 0.05% floor", "as_of": "2026-10-01",
         "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(positions_path=pos, holdings_path=hold)
    assert "USD" not in set(out["key"])
    assert f"BROAD {exposure.UNACCOUNTED}" not in set(out["key"])
    # Fix round 1, I-A: split by kind, not lumped under one caption that was
    # only ever true of the smallest of the three. $200 cash, $500 of
    # BROAD's own unaccounted half, nothing in a no-companies fund here.
    assert out.attrs["excluded_by_kind"] == {
        exposure.KIND_CASH: pytest.approx(200.0),
        exposure.KIND_UNACCOUNTED: pytest.approx(500.0),
        exposure.KIND_NO_COMPANIES: pytest.approx(0.0),
    }


def test_attrs_survive_on_the_non_empty_path(tmp_path, monkeypatch):
    """Fix round 1, I-C. Every attrs key this function promises must still be
    there once rows exist — easy to set one only on the empty-frame branch
    and have it silently vanish the moment there is real data."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(positions_path=pos, holdings_path=hold)
    assert not out.empty
    for key in ("why", "index_accounted_pct", "index_source_coverage_pct",
               "floor_pct", "basis", "compare_note", "excluded_by_kind",
               "state_wording"):
        assert key in out.attrs, f"attrs[{key!r}] missing on the non-empty path"
    assert out.attrs["why"] == ""
    assert out.attrs["index_source_coverage_pct"] == pytest.approx(100.0)
    assert out.attrs["index_accounted_pct"] == pytest.approx(8.0)
    assert out.attrs["floor_pct"] == pytest.approx(8.0)


def test_the_state_wording_travels_with_the_frame(tmp_path, monkeypatch):
    """Fix round 1, C-D: `BELOW_FLOOR_WORDING` used to be a comment with no
    consumer. `attrs['state_wording']` must carry the real module constants
    — never a second, divergeable copy of the same sentence — and cover
    every state value the frame actually used."""
    pos = _positions(tmp_path, [
        {"ticker": "ASML", "instrument_id": "ASML", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "ASML", "name": "ASML Holding", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(positions_path=pos, holdings_path=hold)
    wording = out.attrs["state_wording"]
    assert wording["below-floor"] == exposure.BELOW_FLOOR_WORDING
    assert wording["unknown"] == exposure.UNKNOWN_WORDING
    assert wording["both"] == ""
    assert wording["only-index"] == ""
    # Every state value the frame actually produced has an entry here.
    for state in out["state"]:
        assert state in wording


def test_issuer_group_groups_share_classes_using_the_indexs_own_label(
        tmp_path, monkeypatch):
    """Fix round 1, the Alphabet problem. GOOG and GOOGL stay two rows —
    different securities, different weights, correctly — but their combined
    difference would rank third on a real book and never appear anywhere
    without a column grouping them. The index's own holding_name carries
    the class marker; nothing here is guessed from his own label."""
    pos = _positions(tmp_path, [
        {"ticker": "GOOGL", "instrument_id": "GOOGL", "market_value": 300.0},
        {"ticker": "GOOG", "instrument_id": "GOOG", "market_value": 200.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "GOOGL", "name": "Alphabet Inc Class A",
         "instrument_type": "stock"},
        {"instrument_id": "GOOG", "name": "Alphabet Inc Class C",
         "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "GOOGL",
         "holding_name": "ALPHABET INC CL A", "weight": 0.03, "coverage": 1.0,
         "source": "SPDR holdings file", "as_of": "2026-10-05",
         "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "GOOG",
         "holding_name": "ALPHABET INC CL C", "weight": 0.024, "coverage": 1.0,
         "source": "SPDR holdings file", "as_of": "2026-10-05",
         "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["GOOGL", "issuer_group"] == out.at["GOOG", "issuer_group"]
    assert out.at["GOOGL", "issuer_group"] == "ALPHABET INC"
    # The two rows themselves stay separate — different securities.
    assert out.at["GOOGL", "mine_pct"] != out.at["GOOG", "mine_pct"]


def test_issuer_group_falls_back_to_key_not_label_for_unmatched_companies(
        tmp_path, monkeypatch):
    """Fix round 2, NEW-1. The fallback used to be `label` — free text — so
    two different companies sharing a name (First Bancorp, North Carolina,
    vs First BanCorp, Puerto Rico) would merge into one issuer_group. `key`
    is unique by construction (`company_key()`'s whole job), so two
    companies that happen to be labelled identically must still read as two
    groups when neither is in the index's own stored rows at all."""
    pos = _positions(tmp_path, [
        {"ticker": "FBNC", "instrument_id": "FBNC", "market_value": 300.0},
        {"ticker": "FBP", "instrument_id": "FBP", "market_value": 200.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "FBNC", "name": "First Bancorp",
         "instrument_type": "stock"},
        {"instrument_id": "FBP", "name": "First Bancorp",
         "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        # Neither FBNC nor FBP is in SPY's stored rows — both must read
        # below-floor, and neither has an index holding_name to derive an
        # issuer_group from.
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(
        positions_path=pos, holdings_path=hold).set_index("key")
    assert out.at["FBNC", "issuer_group"] == "FBNC"
    assert out.at["FBP", "issuer_group"] == "FBP"
    assert out.at["FBNC", "issuer_group"] != out.at["FBP", "issuer_group"]


def test_the_sort_breaks_ties_deterministically_by_key(tmp_path, monkeypatch):
    """Fix round 1, M-A. Over a thousand rows tie at a zero or blank
    difference on the real book, and a frame built by walking a bare set's
    iteration order reordered the page between runs with no data change —
    Python randomises string hashing per process, so even a 'stable' sort
    was only ever stable around whatever order that run's set produced. An
    explicit tiebreak by key makes the order the same every time."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.5, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "ZZZ", "holding_name": "Z CO",
         "weight": 0.01, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "AAA", "holding_name": "A CO",
         "weight": 0.01, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "MMM", "holding_name": "M CO",
         "weight": 0.01, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    out = exposure.benchmark_companies(positions_path=pos, holdings_path=hold)
    tied = out[out["key"].isin(["ZZZ", "AAA", "MMM"])]
    assert list(tied["key"]) == ["AAA", "MMM", "ZZZ"]
    # Calling it again must give the identical order every time, not just an
    # internally-consistent one.
    again = exposure.benchmark_companies(positions_path=pos, holdings_path=hold)
    assert list(out["key"]) == list(again["key"])


def test_non_equity_in_basis_sums_funds_that_own_no_equities(tmp_path,
                                                             monkeypatch):
    """Fix round 1, C-C's supporting function. DBA, IBIT and SLV own no
    equities at all; `pure-equity` still contains them because
    `in_equity_base` is a cluster flag, not an instrument-type filter.
    Called by the compare-note text, never typed there."""
    pos = _positions(tmp_path, [
        {"ticker": "DBA", "instrument_id": "DBA", "market_value": 400.0},
        {"ticker": "AAPL", "instrument_id": "AAPL", "market_value": 600.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "DBA", "name": "Agriculture Fund",
         "instrument_type": "etf"},
        {"instrument_id": "AAPL", "name": "Apple", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [])  # DBA: no rows -> no-companies, not missing
    assert exposure.non_equity_in_basis(
        "pure-equity", positions_path=pos, holdings_path=hold
    ) == pytest.approx(400.0)
    # AAPL is a real company — never counted as non-equity.
    assert exposure.non_equity_in_basis(
        "everything", positions_path=pos, holdings_path=hold
    ) == pytest.approx(400.0)


def test_no_basis_note_claims_to_be_honest(tmp_path, monkeypatch):
    """Fix round 1, C-C. The 'pure-equity' note used to call itself the
    honest, equity-to-equity comparison while about a tenth of that basis
    was a bitcoin, bullion or commodity-futures fund — false, and
    `src/book.py`'s own module docstring already said so. No basis is
    called 'honest' any more, and the pure-equity note names the non-equity
    dollar figure by calling `non_equity_in_basis()`, never by typing it."""
    pos = _positions(tmp_path, [
        {"ticker": "DBA", "instrument_id": "DBA", "market_value": 400.0},
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 600.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "DBA", "name": "Agriculture Fund",
         "instrument_type": "etf"},
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    for basis in ("everything", "minus-cash", "pure-equity"):
        note = exposure.benchmark_companies(
            basis, positions_path=pos, holdings_path=hold
        ).attrs["compare_note"]
        assert "honest" not in note.lower()

    # Fix round 2: a SHARE of the basis, not dollars to the cent — the
    # sentence never states the base, so a bare dollar figure gives him
    # nothing to judge it against, and cent precision on a rounding-level
    # number implies an accuracy that is not there.
    from src import book
    non_equity = exposure.non_equity_in_basis(
        "pure-equity", positions_path=pos, holdings_path=hold)
    nav = book.nav("pure-equity", positions_path=pos)
    expected_pct = round(non_equity / nav * 100, 1)
    pure_note = exposure.benchmark_companies(
        "pure-equity", positions_path=pos, holdings_path=hold
    ).attrs["compare_note"]
    assert f"{expected_pct:.1f}%" in pure_note
    assert "$" not in pure_note


def test_the_minus_cash_note_also_sizes_its_non_equity_slice(tmp_path,
                                                             monkeypatch):
    """Fix round 2. 'minus-cash' is the biggest of the three non-equity
    slices (it still keeps T-bills, which 'pure-equity' excludes) and used
    to name the slice without sizing it at all."""
    pos = _positions(tmp_path, [
        {"ticker": "DBA", "instrument_id": "DBA", "market_value": 400.0},
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 600.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "DBA", "name": "Agriculture Fund",
         "instrument_type": "etf"},
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    from src import book
    non_equity = exposure.non_equity_in_basis(
        "minus-cash", positions_path=pos, holdings_path=hold)
    nav = book.nav("minus-cash", positions_path=pos)
    expected_pct = round(non_equity / nav * 100, 1)
    note = exposure.benchmark_companies(
        "minus-cash", positions_path=pos, holdings_path=hold
    ).attrs["compare_note"]
    assert f"{expected_pct:.1f}%" in note


def test_benchmark_sector_comparison_never_writes_a_bare_zero_for_absence(
        tmp_path, monkeypatch):
    """Fix round 1, I-B. The sector side of C-A's own risk: an outer join
    filled with zero would state the index holds none of a sector nobody
    built a row for. This function does the join itself and names a state
    instead of ever writing a blank as a number."""
    pos = _positions(tmp_path, [
        {"ticker": "BTC", "instrument_id": "BTC", "market_value": 300.0},
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 700.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BTC", "name": "Bitcoin", "instrument_type": "stock",
         "sector": "Crypto / Digital Assets"},
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock",
         "sector": "Tech - Semiconductors"},
    ])
    cp = _comp(tmp_path, [
        {"ticker": "SPY", "sector": "Technology", "weight": 0.5,
         "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
         "source_quality": "vendor", "as_of": "2026-10-06"},
        {"ticker": "SPY", "sector": "Financials", "weight": 0.5,
         "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
         "source_quality": "vendor", "as_of": "2026-10-06"},
    ])
    out = exposure.benchmark_sector_comparison(
        "pure-equity", path=cp, positions_path=pos,
        classification="mine").set_index("sector")

    # Technology: he holds NVDA there (70% of his basis), the index reports
    # 50% — both sides present, a real difference.
    assert out.at["Technology", "state"] == "both"
    assert out.at["Technology", "mine_pct"] == pytest.approx(70.0)
    assert out.at["Technology", "benchmark_pct"] == pytest.approx(50.0)
    assert out.at["Technology", "difference"] == pytest.approx(20.0)

    # Crypto: he has it, SPY structurally cannot (an all-equity fund) — a
    # confident zero, but still never written as a bare 0.0.
    assert out.at["Crypto / Digital Assets", "state"] == "only-mine"
    assert pd.isna(out.at["Crypto / Digital Assets", "benchmark_pct"])

    # Financials: the index reports it, he has zero exposure to it at all.
    assert out.at["Financials", "state"] == "only-index"
    assert pd.isna(out.at["Financials", "mine_pct"])


def test_unclassified_money_is_not_only_mine(tmp_path, monkeypatch):
    """Fix, pre-push review 2026-10-07 (finding 1). `by_sector()`'s `Other`
    bucket (`exposure.UNCLASSIFIED_SECTOR`) is money it could NOT classify
    — an uncovered ETF here — never a sector the index structurally cannot
    hold. Before this fix the two shared one state (`only-mine`), which let
    `Other` inherit Crypto's "SPY cannot hold this, so zero is certain"
    caption — backwards, since 'Other' is exactly the unknown case."""
    pos = _positions(tmp_path, [
        {"ticker": "NEWETF", "instrument_id": "NEWETF", "market_value": 20000.0},
        {"ticker": "BTC", "instrument_id": "BTC", "market_value": 300.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NEWETF", "instrument_type": "etf"},   # no comp row
        {"instrument_id": "BTC", "instrument_type": "stock",
         "sector": "Crypto / Digital Assets"},
    ])
    cp = _comp(tmp_path, [
        {"ticker": "SPY", "sector": "Real Estate", "weight": 1.0,
         "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
         "source_quality": "vendor", "as_of": "2026-10-06"},
    ])
    out = exposure.benchmark_sector_comparison(
        path=cp, positions_path=pos, classification="mine").set_index("sector")

    assert out.at[exposure.UNCLASSIFIED_SECTOR, "state"] == "unclassified"
    assert pd.isna(out.at[exposure.UNCLASSIFIED_SECTOR, "benchmark_pct"])
    # Crypto is unaffected — still the structural, certain-zero state.
    assert out.at["Crypto / Digital Assets", "state"] == "only-mine"
    # Real Estate: the index holds it, no classified holding of his does.
    assert out.at["Real Estate", "state"] == "only-index"


def test_benchmark_sector_comparison_propagates_the_empty_reason(tmp_path,
                                                                  monkeypatch):
    """If the benchmark's own sector breakdown has never been fetched, this
    must say so — never zero every sector via a silent outer-join fillna."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    out = exposure.benchmark_sector_comparison(
        path=_comp(tmp_path, []), positions_path=pos)
    assert out.empty
    assert "/market" in out.attrs["why"]


def test_benchmark_sector_comparison_carries_the_basis_caveat_too(tmp_path,
                                                                   monkeypatch):
    """Fix round 2, NEW-3. The company frame carries `attrs['compare_note']`
    because a basis containing non-equity funds dilutes every percentage —
    the sector frame has exactly the same hazard and used to carry no note
    at all. Forwarding this function's own `**paths` (which carries
    `path`/`classification`, not `by_company()`'s kwargs) straight into the
    note builder would raise; only the keys `by_company()` actually accepts
    may cross that boundary."""
    pos = _positions(tmp_path, [
        {"ticker": "DBA", "instrument_id": "DBA", "market_value": 400.0},
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 600.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "DBA", "name": "Agriculture Fund",
         "instrument_type": "etf"},
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    cp = _comp(tmp_path, [
        {"ticker": "SPY", "sector": "Technology", "weight": 1.0,
         "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
         "source_quality": "vendor", "as_of": "2026-10-06"},
    ])
    # This must not raise, despite `path` and `classification` being present
    # in **paths alongside `positions_path`/`holdings_path`.
    out = exposure.benchmark_sector_comparison(
        "pure-equity", path=cp, positions_path=pos,
        classification="mine", holdings_path=_holdings_table(tmp_path, []))
    assert "compare_note" in out.attrs
    assert "pure-equity" in out.attrs["compare_note"]
    assert "honest" not in out.attrs["compare_note"].lower()


def test_benchmark_sector_comparison_forwards_instruments_and_clusters_too(
        tmp_path, monkeypatch):
    """Fix, 2026-10-07 final review (I5), extended to this function's own
    internal `by_sector()` call. It already built a `company_paths` dict
    carrying `instruments_path`/`clusters_path` for `_compare_note()`'s
    sake, but its `by_sector(...)` call two lines above hard-coded only
    `positions_path`/`classification` — the identical dropped-override bug
    as `page._by_sector_paths()`, in a second place in the same file."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 1000.0},
        {"ticker": "GLD", "instrument_id": "GLD", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock",
         "sector": "Tech - Semiconductors", "cluster_id": "TechCluster"},
        {"instrument_id": "GLD", "name": "Gold Trust", "instrument_type": "etf",
         "sector": "", "cluster_id": "NonEquity"},
    ])
    clp = tmp_path / "clusters.csv"
    pd.DataFrame([
        {"cluster_id": "TechCluster", "parent": "", "layer": "thematic",
         "in_equity_base": True, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
        {"cluster_id": "NonEquity", "parent": "", "layer": "thematic",
         "in_equity_base": False, "note_ref": "", "status": "active",
         "added": "2026-01-01"},
    ]).to_csv(clp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    cp = _comp(tmp_path, [
        {"ticker": "SPY", "sector": "Technology", "weight": 1.0,
         "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
         "source_quality": "vendor", "as_of": "2026-10-06"},
    ])
    out = exposure.benchmark_sector_comparison(
        "pure-equity", path=cp, positions_path=pos, classification="mine",
        clusters_path=clp)
    # GLD's cluster excludes it from pure-equity: his Technology share
    # (all-NVDA) must read 100%, not diluted by GLD still being counted.
    assert out.set_index("sector").at["Technology", "mine_pct"] == pytest.approx(100.0)


def test_by_company_is_called_once_not_twice_for_the_compare_note(tmp_path,
                                                                   monkeypatch):
    """Fix round 2, MIN-A. `benchmark_companies()` already holds a
    `by_company()` frame by the time it builds its compare-note;
    recomputing it there roughly doubled the call's own cost under
    pure-equity/minus-cash. Counts real calls rather than trusting that the
    code merely looks right."""
    import pandas as pd

    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])

    calls = []
    real_by_company = exposure.by_company

    def counting_by_company(*a, **k):
        calls.append((a, k))
        return real_by_company(*a, **k)

    monkeypatch.setattr(exposure, "by_company", counting_by_company)
    exposure.benchmark_companies(
        "pure-equity", positions_path=pos, holdings_path=hold)
    assert len(calls) == 1, (
        f"by_company() was called {len(calls)} times, expected exactly 1 — "
        f"the compare-note must reuse the frame already computed, not "
        f"fetch a second one"
    )


# ─── Task 9: how many bets this really is ──────────────────────────────────


def test_effective_bets_counts_one_company_held_twice_once(tmp_path, monkeypatch):
    """The figure exists because a direct holding and the same company inside
    three funds is one bet, not four."""
    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 100.0},
        {"ticker": "FUND1", "instrument_id": "FUND1", "market_value": 300.0},
        {"ticker": "FUND2", "instrument_id": "FUND2", "market_value": 300.0},
        {"ticker": "FUND3", "instrument_id": "FUND3", "market_value": 300.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock"},
        {"instrument_id": "FUND1", "name": "Fund One", "instrument_type": "etf"},
        {"instrument_id": "FUND2", "name": "Fund Two", "instrument_type": "etf"},
        {"instrument_id": "FUND3", "name": "Fund Three", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": f"FUND{i}", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 1.0, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"}
        for i in (1, 2, 3)
    ])
    # Sanity check on the premise: by_company() really does fold the direct
    # holding and all three funds' worth of NVDA into one row before
    # effective_bets() ever sees it.
    rows = exposure.by_company(positions_path=pos, holdings_path=hold)
    assert len(rows) == 1
    assert rows.iloc[0]["key"] == "NVDA"

    out = exposure.effective_bets(positions_path=pos, holdings_path=hold)
    # One row, one weight of 1.0, Σw² = 1.0 → exactly one bet. Mistakenly
    # counting the direct holding and the three funds as four separate bets
    # (weights 0.1/0.3/0.3/0.3) would instead give Σw² = 0.28, count ≈ 3.57.
    assert out["count"] == pytest.approx(1.0)
    assert out["largest_pct"] == pytest.approx(100.0)
    assert out["is_upper_bound"] is False
    assert out["split_symbols"] == 0


def test_effective_bets_is_an_upper_bound_when_symbols_are_split(tmp_path,
                                                                 monkeypatch):
    """The one bias on this page that flatters him. A company he does not hold
    directly, spelled two ways by two funds, reads as two bets: 1/sum(w**2)
    rises, and the page looks more diversified than the book is. The figure
    carries the flag and the count, so the sentence beside it can say so."""
    pos = _positions(tmp_path, [
        {"ticker": "EPI", "instrument_id": "EPI", "market_value": 500.0},
        {"ticker": "DEM", "instrument_id": "DEM", "market_value": 500.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "EPI", "name": "WisdomTree India Fund",
         "instrument_type": "etf"},
        {"instrument_id": "DEM", "name": "WisdomTree EM Fund",
         "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        # One company — Reliance Industries — spelled two different ways by
        # two funds, neither of which he holds it directly. Each fund is
        # entirely this one (unheld) name, so the split is the only thing
        # under test, isolated from anything else in by_company()'s walk.
        {"ticker": "EPI", "holding": "500325",
         "holding_name": "RELIANCE INDUSTRIES LTD", "weight": 1.0,
         "coverage": 1.0, "source": "issuer file", "as_of": "2026-10-01",
         "as_of_kind": "published"},
        {"ticker": "DEM", "holding": "RELIANCE IN",
         "holding_name": "RELIANCE INDUSTRIES LTD", "weight": 1.0,
         "coverage": 1.0, "source": "issuer file", "as_of": "2026-10-01",
         "as_of_kind": "published"},
    ])
    collisions = exposure.symbol_collisions(positions_path=pos, holdings_path=hold)
    assert len(collisions) == 1   # the premise: one split name, confirmed

    out = exposure.effective_bets(positions_path=pos, holdings_path=hold)
    # Split into "500325" (w=0.5) and "RELIANCE IN" (w=0.5): Σw² = 0.5,
    # count = 2.0 — twice what it would read (1.0) had both funds' money
    # joined to one key the way it would if he held the name directly.
    assert out["count"] == pytest.approx(2.0)
    assert out["is_upper_bound"] is True
    assert out["split_symbols"] == 1
    assert "upper bound" in out["why"].lower()
    assert "1" in out["why"]   # the count travels with the sentence
    # Fix, 2026-10-07 final review (I1): the sentence must hedge the ROW
    # COUNT itself, not just call the metric an upper bound in the
    # abstract — `symbol_collisions()`'s own docstring forbids quoting its
    # row count as fact (the live book's row 0 today, ADCB/ADIB, is a known
    # false positive: two different Abu Dhabi banks matched on a two-word
    # name prefix). "at most 1", never a bare "1 unheld company".
    assert "at most 1 unheld company" in out["why"]


def test_a_short_does_not_make_the_denominator_meaningless(tmp_path, monkeypatch):
    """A negative weight squares to a positive one, so a short would ADD
    apparent diversification. Magnitudes, and the sentence says the figure is
    about size and not direction.

    Fix round 1 (2026-10-07), A: round 0's fixture used a long and a short of
    the SAME size, which cancels the signed total to exactly zero — removing
    `.abs()` then hits the `total <= 0` "nothing to divide by" guard instead
    of actually exercising magnitude-vs-signed weighting, so the mutation
    went red for the wrong reason. A long of 1000 against a short of 400
    keeps the signed total non-zero (600) but still different enough from
    the magnitude total (1400) that the two methods diverge sharply, so a
    mutation that drops `.abs()` is caught by THIS rule and not by the
    "nothing to divide by" branch (that branch has its own test below)."""
    from src import book

    pos = _positions(tmp_path, [
        {"ticker": "AAPL", "instrument_id": "AAPL", "market_value": 1000.0},
        {"ticker": "SHRT", "instrument_id": "SHRT", "market_value": -400.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "AAPL", "name": "Apple", "instrument_type": "stock"},
        {"instrument_id": "SHRT", "name": "A Short", "instrument_type": "short"},
    ])
    hold = _holdings_table(tmp_path, [])
    # The signed total (600) and the magnitude total (1400) are genuinely
    # different numbers here — neither zero nor equal — which is what makes
    # this fixture able to tell the two weighting schemes apart.
    assert book.nav("everything", positions_path=pos) == pytest.approx(600.0)

    out = exposure.effective_bets("everything", positions_path=pos,
                                  holdings_path=hold)
    # Weighted by magnitude (1000 and 400 out of 1400): w = 0.7143/0.2857,
    # Σw² ≈ 0.5918, count ≈ 1.69 — two real, differently-sized bets, neither
    # one nor zero. A signed-weight computation (dividing by the SIGNED
    # total of 600 instead) would give w = 1.6667/-0.6667, Σw² ≈ 3.22,
    # count ≈ 0.31 — below 1 for two actual positions, because the short's
    # negative value shrank the very denominator it was dividing by and
    # inflated every weight built on it.
    assert out["count"] == pytest.approx(1.7, abs=0.05)
    assert out["largest_pct"] == pytest.approx(71.43, abs=0.5)
    assert "direction" in out["why"].lower() or "short" in out["why"].lower()


def test_a_fully_offsetting_short_hits_the_division_guard_not_a_crash(
        tmp_path, monkeypatch):
    """A long and a short of the SAME size net the signed NAV to exactly
    zero. This is `effective_bets()`'s own "nothing to divide by" guard
    (the magnitude total is still 2000, never zero, so it does not fire) —
    a separate thing from the magnitude-vs-signed-weights rule above, which
    needs a fixture where the two totals actually differ to tell them
    apart (fix round 1, 2026-10-07, A)."""
    from src import book

    pos = _positions(tmp_path, [
        {"ticker": "AAPL", "instrument_id": "AAPL", "market_value": 1000.0},
        {"ticker": "SHRT", "instrument_id": "SHRT", "market_value": -1000.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "AAPL", "name": "Apple", "instrument_type": "stock"},
        {"instrument_id": "SHRT", "name": "A Short", "instrument_type": "short"},
    ])
    hold = _holdings_table(tmp_path, [])
    assert book.nav("everything", positions_path=pos) == pytest.approx(0.0)

    out = exposure.effective_bets("everything", positions_path=pos,
                                  holdings_path=hold)
    # Magnitude total is 2000 (never zero), so this does not hit the guard
    # at all — it is a clean, equally-sized two-bet book: Σw² = 0.5,
    # count = 2.0.
    assert out["count"] == pytest.approx(2.0)
    assert out["largest_pct"] == pytest.approx(50.0)


def test_an_unaccounted_remainder_counts_as_one_bet_per_fund(tmp_path,
                                                             monkeypatch):
    """The part of a fund its holdings do not reach is not a thousand tiny
    positions and is not nothing. One unknown block per fund is the
    assumption that cannot overstate diversification, which is the
    direction that matters."""
    pos = _positions(tmp_path, [
        {"ticker": "FUND1", "instrument_id": "FUND1", "market_value": 600.0},
        {"ticker": "FUND2", "instrument_id": "FUND2", "market_value": 400.0},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "FUND1", "name": "A Fund Nobody Fetched",
         "instrument_type": "etf"},
        {"instrument_id": "FUND2", "name": "Another Fund Nobody Fetched",
         "instrument_type": "etf"},
    ])
    # Neither fund has a single holdings row on file — each one's whole
    # value becomes its own UNACCOUNTED bucket.
    hold = _holdings_table(tmp_path, [])
    rows = exposure.by_company(positions_path=pos, holdings_path=hold)
    assert len(rows) == 2   # one unaccounted block per fund, not merged, not split
    assert set(rows["kind"]) == {exposure.KIND_UNACCOUNTED}

    out = exposure.effective_bets(positions_path=pos, holdings_path=hold)
    # Two blocks, weights 0.6 and 0.4: Σw² = 0.36 + 0.16 = 0.52,
    # count = 1/0.52 ≈ 1.923. Merging both funds' remainders into one block
    # would read as 1.0 bet (less diversified than the data supports);
    # fragmenting either fund's remainder into many tiny pieces would push
    # the count up past 2 — the dangerous direction this test exists to
    # catch, because it is the one that overstates diversification.
    assert out["count"] == pytest.approx(1.0 / 0.52, abs=0.05)
    assert out["largest_pct"] == pytest.approx(60.0)


def test_nothing_in_the_page_layer_reads_the_nav_history_columns():
    """Deferred to Stage 4, his call 2026-10-06.

    A drawdown off the raw nav column reports his own money paying in as a
    market move: the account-opening transfer is recorded in the free-text
    notes column with deposit reading 0.0, and the one recorded deposit is
    the largest single-day move in the whole series. It would look clean,
    which is exactly why it is the part of this project that can be subtly
    wrong.

    The honest version subtracts the flows reconciled against
    cash_transactions.csv and asserts no single-day move an unrecorded
    transfer could explain. That is the Performance tab's job and it is
    built there.

    Neither `src/page.py` nor `ops/build_page.py` exists yet — the page is
    built in a later task (Task 10/11 of this plan). This test exists so
    that, the day either file is created, it cannot read `nav_history` or
    compute a drawdown without going red — it is written now, ahead of the
    page, specifically so there is no window where that shortcut is free.

    Fix round 1 (2026-10-07), D: round 0 keyed the drawdown half of this on
    one literal phrase (`"drawdown arrives"`), stripped out before checking
    for the word — so the deferral SENTENCE could say "drawdown" without
    tripping it. A later task rewording that one sentence (which it is free
    to do; nothing pins its exact words) would have innocently broken the
    exemption and made this test fail for a page that computes nothing. The
    rule this test actually enforces is "no nav_history, no COMPUTED
    drawdown" — not "no occurrence of the word drawdown" — so it now checks
    for the pandas idiom an actual computation would use (`cummax`, the
    standard way to build a running peak to subtract from) rather than the
    English word, which can appear in the deferral sentence however it ends
    up phrased.
    """
    from pathlib import Path

    from src.claims import PROJECT_ROOT

    for name in ("src/page.py", "ops/build_page.py"):
        path = PROJECT_ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        assert "nav_history" not in lowered, (
            f"{name} reads nav_history — the drawdown figure is deferred to "
            f"the Performance tab (his call, 2026-10-06): the account-"
            f"opening transfer sits in the free-text notes column with "
            f"deposit reading 0.0, so a drawdown off the raw column would "
            f"report his own money paying in as a market move."
        )
        for idiom in ("cummax", "cumulative_max", "running_max",
                     "peak_to_trough"):
            assert idiom not in lowered, (
                f"{name} appears to compute a drawdown ({idiom!r} found) — "
                f"deferred to the Performance tab (his call, 2026-10-06), "
                f"where the deposit reconstruction is built properly "
                f"against cash_transactions.csv."
            )
    # Today neither file exists, so there is nothing yet to read — the
    # assertions above are the tripwire for the day one of them is written.
