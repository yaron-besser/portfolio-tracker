"""The provider-choosing rule in ops/refresh_etf_compositions.py.

Why this file exists: on 2026-10-01 a full refresh replaced IWM's complete
11-sector Morningstar breakdown with an Alpha Vantage one that omitted Real
Estate entirely, covered 89.7% of the fund, and was then normalised to 100% —
silently spreading 6.9% of the fund across the sectors that remained. The
account's Real Estate exposure fell from 0.40% to 0.09% with nothing having
happened in the market.

The cause was a fixed provider order: Alpha Vantage was preferred because it is
issuer-reported, regardless of how little of the fund it covered. Coverage is
the thing that decides whether a breakdown is trustworthy, so coverage decides.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "refresh_etf_compositions.py"


def _load():
    spec = importlib.util.spec_from_file_location("refresh_etf_compositions", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def refresh():
    return _load()


def _provider(weights: dict[str, float], label: str):
    """A stand-in provider returning a fixed breakdown."""
    def fn(ticker: str):
        return dict(weights), label
    fn.__name__ = label.replace(" ", "_")
    return fn


# The real shape of the IWM failure: a thinner breakdown listed first.
THIN = {"Technology": 0.40, "Financials": 0.30, "Healthcare": 0.197}   # 89.7%
FULL = {"Technology": 0.38, "Financials": 0.28, "Healthcare": 0.271,
        "Real Estate": 0.069}                                          # 100%


def test_better_coverage_wins_even_when_listed_second(refresh, monkeypatch):
    """The thin breakdown is first in the list and must still lose."""
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (_provider(THIN, "thin issuer"), "issuer"),
        (_provider(FULL, "full vendor"), "vendor"),
    ])
    got = refresh.fetch("IWM")
    assert got is not None
    assert got["source_quality"] == "vendor"
    assert "Real Estate" in got["weights"], "the sector the old rule silently dropped"
    assert got["coverage"] == pytest.approx(1.0)


def test_provider_order_breaks_a_tie(refresh, monkeypatch):
    """Equal coverage: the issuer-reported one is still preferred."""
    same_a = {"Technology": 0.5, "Financials": 0.5}
    same_b = {"Technology": 0.6, "Financials": 0.4}
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (_provider(same_a, "issuer feed"), "issuer"),
        (_provider(same_b, "vendor feed"), "vendor"),
    ])
    assert refresh.fetch("XLK")["source_quality"] == "issuer"


def test_a_failing_provider_does_not_hide_a_working_one(refresh, monkeypatch):
    def broken(ticker: str):
        raise RuntimeError("provider down")
    broken.__name__ = "broken"
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (broken, "issuer"),
        (_provider(FULL, "full vendor"), "vendor"),
    ])
    assert refresh.fetch("VTI")["source_quality"] == "vendor"


def test_nothing_usable_returns_none(refresh, monkeypatch):
    """Below MIN_COVERAGE from everyone is a refusal, never a scaled guess."""
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (_provider({"Technology": 0.378}, "partial"), "issuer"),
        (_provider({}, "empty"), "vendor"),
    ])
    assert refresh.fetch("MAGS") is None


def _raiser(name: str = "unmapped"):
    def fn(ticker: str):
        raise KeyError("Nanotechnology")
    fn.__name__ = name
    return fn


def test_an_unmapped_sector_with_no_usable_alternative_still_stops(refresh, monkeypatch):
    """Nobody usable plus a vocabulary gap is the original stop-everything rule."""
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (_raiser(), "issuer"),
        (_provider({"Technology": 0.2}, "too thin"), "vendor"),
    ])
    refresh.UNMAPPED_SEEN.clear()
    with pytest.raises(KeyError):
        refresh.fetch("QQQ")


def test_an_unmapped_sector_does_not_discard_a_usable_breakdown(refresh, monkeypatch):
    """The behaviour that changed: a loser's bad label must not abort the run.

    Before best-coverage-wins, a provider that raised was always the first one
    tried, so raising cost one ticker. Now every provider is asked, so raising
    here would throw away every fund already fetched in this run — and the
    Alpha Vantage quota they cost — for a breakdown that was going to lose.
    Nothing is silently shrunk: the whole breakdown is discarded, not part of
    one, and main() still exits non-zero.
    """
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (_provider(FULL, "full issuer"), "issuer"),
        (_raiser("late_vendor"), "vendor"),
    ])
    refresh.UNMAPPED_SEEN.clear()
    got = refresh.fetch("QQQ")
    assert got is not None, "a good breakdown must survive another provider's bad label"
    assert got["source_quality"] == "issuer"
    assert [t for t, _, _ in refresh.UNMAPPED_SEEN] == ["QQQ"], "the gap is still recorded"


def test_coverage_far_above_one_is_refused(refresh, monkeypatch):
    """A provider on a percent scale would otherwise win every comparison."""
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (_provider({"Technology": 60.0, "Financials": 40.0}, "percent scale"), "issuer"),
        (_provider(FULL, "full vendor"), "vendor"),
    ])
    refresh.UNMAPPED_SEEN.clear()
    assert refresh.fetch("XLK")["source_quality"] == "vendor"


def test_a_hair_more_coverage_does_not_flip_the_source(refresh, monkeypatch):
    """Two complete breakdowns differing by a rounding step are the same answer.

    Letting 1e-16 decide rewrites every row for the fund with a different
    source and date, for no change in the fund.
    """
    nearly = {"Technology": 0.38, "Financials": 0.28, "Healthcare": 0.2709,
              "Real Estate": 0.069}                       # 99.99%
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (_provider(nearly, "issuer feed"), "issuer"),
        (_provider(FULL, "vendor feed"), "vendor"),       # 100%, very slightly more
    ])
    refresh.UNMAPPED_SEEN.clear()
    assert refresh.fetch("IWM")["source_quality"] == "issuer"


def test_a_real_coverage_gap_still_flips_the_source(refresh, monkeypatch):
    """The tie tolerance must not swallow the IWM-sized gap it sits next to."""
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (_provider(THIN, "issuer feed"), "issuer"),       # 89.7%
        (_provider(FULL, "vendor feed"), "vendor"),       # 100%
    ])
    refresh.UNMAPPED_SEEN.clear()
    assert refresh.fetch("IWM")["source_quality"] == "vendor"


def test_the_winners_thinness_is_recorded_in_the_source(refresh, monkeypatch):
    """Scaling a partial breakdown to 100% is the mechanism that caused the harm.

    It is allowed above MIN_COVERAGE, but it must never be invisible.
    """
    monkeypatch.setattr(refresh, "PROVIDERS", [
        (_provider(THIN, "thin issuer"), "issuer"),
    ])
    refresh.UNMAPPED_SEEN.clear()
    got = refresh.fetch("IWM")
    assert "normalised from 89.7% coverage" in got["source"]
    assert sum(got["weights"].values()) == pytest.approx(1.0)


# --- dropped_parents, against its own taxonomy rather than production data ---

@pytest.fixture
def taxonomy(tmp_path):
    """A sectors.csv holding only what these tests need."""
    import pandas as pd
    rows = [("Cash", "Fixed Income / Cash"),
            ("Crypto - Bitcoin", "Crypto / Digital Assets"),
            ("Tech - Semiconductors", "Technology"),
            ("Tech - Software", "Technology")]
    # Every parent a provider can report must resolve to itself.
    rows += [(s, p) for s, p in [
        ("Technology", "Technology"), ("Comm Services", "Communication Services"),
        ("Consumer Discretionary", "Consumer Discretionary"),
        ("Consumer Staples", "Consumer Staples"), ("Healthcare", "Healthcare"),
        ("Industrials", "Industrials"), ("Utilities", "Utilities"),
        ("Materials", "Materials"), ("Energy", "Energy"),
        ("Financials", "Financials"), ("Real Estate", "Real Estate")]]
    p = tmp_path / "sectors.csv"
    pd.DataFrame([{"sector_id": s, "parent": par, "status": "active",
                   "added": "2026-01-01"} for s, par in rows]).to_csv(p, index=False)
    return p


def _old(rows):
    import pandas as pd
    return pd.DataFrame(
        [{"ticker": t, "sector": s, "weight": w} for t, s, w in rows],
        columns=["ticker", "sector", "weight"])


def test_dropped_parents_names_a_lost_sector(refresh, taxonomy):
    """IWM losing Real Estate must be reported, not absorbed."""
    old = _old([("IWM", "Healthcare", 0.2), ("IWM", "Real Estate", 0.0686)])
    results = [{"ticker": "IWM", "weights": {"Healthcare": 1.0}}]
    assert refresh.dropped_parents(old, results, taxonomy) == {"IWM": ["Real Estate"]}


def test_dropped_parents_ignores_a_sub_sector_becoming_its_parent(refresh, taxonomy):
    """XLK going from three Tech sub-labels to one 'Technology' is not a loss."""
    old = _old([("XLK", "Tech - Semiconductors", 0.5), ("XLK", "Tech - Software", 0.5)])
    results = [{"ticker": "XLK", "weights": {"Technology": 1.0}}]
    assert refresh.dropped_parents(old, results, taxonomy) == {}


def test_dropped_parents_ignores_a_cash_sleeve(refresh, taxonomy):
    """The false alarm that would have made the warning worthless.

    No provider reports cash — it is the uncovered residual MIN_COVERAGE
    tolerates and normalisation spreads away. So every fund carrying a
    hand-typed Cash row would warn on every refresh, which is exactly the
    stale `index estimate` data this script exists to replace. A warning that
    cries wolf on those funds is one nobody reads, and the next IWM goes
    through unnoticed.
    """
    old = _old([("JETS", "Industrials", 0.98), ("JETS", "Cash", 0.02)])
    results = [{"ticker": "JETS", "weights": {"Industrials": 1.0}}]
    assert refresh.dropped_parents(old, results, taxonomy) == {}


def test_dropped_parents_ignores_a_crypto_sleeve(refresh, taxonomy):
    """Same reasoning as cash: no provider returns a crypto weight."""
    old = _old([("X", "Crypto - Bitcoin", 1.0)])
    results = [{"ticker": "X", "weights": {"Technology": 1.0}}]
    assert refresh.dropped_parents(old, results, taxonomy) == {}


def test_dropped_parents_reports_nothing_for_a_brand_new_fund(refresh, taxonomy):
    """A ticker with no previous rows has lost nothing."""
    results = [{"ticker": "ZZZZ", "weights": {"Technology": 1.0}}]
    assert refresh.dropped_parents(_old([]), results, taxonomy) == {}


def test_dropped_parents_names_a_sector_that_survives_but_collapses(refresh, taxonomy):
    """The DBA case the vanish-only guard was structurally blind to.

    IWM lost Real Estate outright, so `before - after` caught it. DBA kept
    the LABEL: a one-row `Materials - Agriculture` breakdown at 100% of the
    fund was replaced by eleven Morningstar equity sectors with Materials at
    10.7%, and Materials was in both sets, so nothing fired. A sector
    surviving with a tenth of the weight it carried is a different answer,
    not a relabel, and it moved $3,180 of a $110K book out of Materials
    without a word.
    """
    old = _old([("DBA", "Materials", 1.0)])
    results = [{"ticker": "DBA", "weights": {"Materials": 0.107,
                                             "Healthcare": 0.6, "Technology": 0.293}}]
    got = refresh.dropped_parents(old, results, taxonomy)
    assert list(got) == ["DBA"]
    assert "Materials" in got["DBA"][0]
    assert "100.0%" in got["DBA"][0] and "10.7%" in got["DBA"][0]


def test_dropped_parents_ignores_ordinary_drift(refresh, taxonomy):
    """A fund's real mix moves between refreshes; that is not a loss.

    The threshold has to sit above anything a market can do between two
    fetches, or the warning fires every quarter and stops being read — the
    same way a cash-sleeve false alarm would have made it worthless.
    """
    old = _old([("QQQ", "Technology", 0.60), ("QQQ", "Healthcare", 0.40)])
    results = [{"ticker": "QQQ", "weights": {"Technology": 0.55, "Healthcare": 0.45}}]
    assert refresh.dropped_parents(old, results, taxonomy) == {}


def test_dropped_parents_reports_a_vanish_and_a_collapse_together(refresh, taxonomy):
    """Both kinds in one fund, and neither hides the other."""
    old = _old([("X", "Materials", 0.5), ("X", "Real Estate", 0.3),
                ("X", "Technology", 0.2)])
    results = [{"ticker": "X", "weights": {"Materials": 0.02, "Technology": 0.98}}]
    got = refresh.dropped_parents(old, results, taxonomy)["X"]
    assert any(g == "Real Estate" for g in got), got
    assert any("Materials" in g and "->" in g for g in got), got


def test_a_sub_sector_collapsing_into_its_parent_is_still_not_a_loss(refresh, taxonomy):
    """Unchanged by the widening, and the case most likely to break it.

    XLK's three `Tech - *` rows become one `Technology` row. Compared sector
    by sector that looks like two sectors vanishing and one appearing; rolled
    up to parents and summed, the parent holds exactly what it held before.
    The widened check compares summed parent weight, so it stays silent.
    """
    old = _old([("XLK", "Tech - Semiconductors", 0.5), ("XLK", "Tech - Software", 0.5)])
    results = [{"ticker": "XLK", "weights": {"Technology": 1.0}}]
    assert refresh.dropped_parents(old, results, taxonomy) == {}
