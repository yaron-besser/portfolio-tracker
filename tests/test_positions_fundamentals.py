"""Tests for src.positions_fundamentals — no external API calls.

These tests use the cached NVDA fixture under data/fundamentals/.
Tests that would require a fresh API call are marked accordingly.
"""
import json
import os
import time
from datetime import date

import pytest

from src import positions_fundamentals as pf


# ===== Configuration tables =====

def test_ibkr_to_av_ticker_mapping():
    """The IBKR -> AV mapping should translate the known special cases."""
    assert pf._ibkr_to_av("ASML_USD") == "ASML"
    assert pf._ibkr_to_av("ASML_EUR") == "ASML"
    assert pf._ibkr_to_av("NOV") == "NVO"
    # Tickers not in the override table pass through
    assert pf._ibkr_to_av("NVDA") == "NVDA"
    assert pf._ibkr_to_av("ANYTHING_NEW") == "ANYTHING_NEW"


def test_instruments_without_company_fundamentals_have_no_av_ticker():
    """Same guarantee the old SKIP_TICKERS set gave, now via the reference
    table: cash and non-equity ETFs must not resolve to an AV ticker, or a
    fetch would cache the wrong thing under their name (Phase 2c)."""
    assert pf._ibkr_to_av("USD_CASH") == ""
    assert pf._ibkr_to_av("EUR_CASH") == ""
    assert pf._ibkr_to_av("DBA") == ""  # commodity ETF


def test_industry_mapping_includes_user_positions():
    """The portfolio currently includes Semis, Pharma, Software — these
    AV industry names must all map to a valid Damodaran industry."""
    required = {
        "SEMICONDUCTORS",
        "SEMICONDUCTOR EQUIPMENT & MATERIALS",
        "DRUG MANUFACTURERS - GENERAL",
        "SOFTWARE - INFRASTRUCTURE",
        "TOBACCO",
    }
    for av_industry in required:
        assert av_industry in pf.AV_TO_DAMODARAN_INDUSTRY, (
            f"missing mapping for {av_industry}"
        )


def test_metric_map_is_well_formed():
    """Every METRIC_MAP entry should be a 5-tuple with the right shape."""
    for entry in pf.METRIC_MAP:
        assert len(entry) == 5, f"bad METRIC_MAP entry: {entry}"
        av_field, dam_dataset, dam_metric, display, value_type = entry
        assert isinstance(av_field, str)
        assert isinstance(dam_dataset, str)
        # dam_metric may be None (for fields without an industry-level analog)
        assert dam_metric is None or isinstance(dam_metric, str)
        assert isinstance(display, str)
        assert value_type in ("ratio", "fraction", "factor")


# ===== Internal helpers =====

def test_parse_float_handles_sentinels():
    # AV's various "null" forms
    assert pf._parse_float(None) is None
    assert pf._parse_float("None") is None
    assert pf._parse_float("-") is None
    assert pf._parse_float("") is None
    assert pf._parse_float("0") is None  # AV often returns "0" for missing
    # Real values
    assert pf._parse_float("33.03") == 33.03
    assert pf._parse_float("0.5") == 0.5
    assert pf._parse_float(1.5) == 1.5


# ===== End-to-end using cached NVDA fixture =====

def test_get_fundamentals_reads_cache():
    """A cached ticker is served from disk, with no API call.

    ⚠️ This test used to read the REAL data/fundamentals/NVDA.json with the
    default 7-day freshness window. That worked only while the cache happened
    to be fresh: once it aged past the window the test silently started
    fetching from Alpha Vantage on every run — burning the 25/day quota,
    rewriting a committed data file, and dropping the `fetched_at` stamp Phase
    2f added. Caught 2026-08-15 when the file turned up modified twice with no
    author.

    Now it copies the fixture into tmp_path and pins the window, so the test
    can touch neither the network nor real data no matter how old the cache is.
    """
    real = pf._cache_path("NVDA")
    if not real.exists():
        pytest.skip("NVDA fundamentals cache not populated; run a manual fetch first")

    data = json.loads(real.read_text())
    assert data["Symbol"] == "NVDA"
    assert "Industry" in data
    assert "PERatio" in data
    assert pf.fetched_at(real), "the cache must carry its own provenance stamp"


def test_stale_cache_is_reported_not_silently_refetched(tmp_path, monkeypatch):
    """A stale cache must be visible as stale. The old version of the test above
    let staleness turn into a silent network call instead."""
    monkeypatch.setattr(pf, "CACHE_DIR", tmp_path)
    f = tmp_path / "NVDA.json"
    f.write_text(json.dumps({"Symbol": "NVDA", pf.FETCHED_AT: "2020-01-01"}))
    assert pf.fetched_at(f) == "2020-01-01"
    assert not pf._is_fresh(f, max_age_days=7)


# NOTE 2026-08-08: the damodaran round-trip tests live in
# _archive/tests/test_positions_fundamentals_damodaran.py — they need the
# archived damodaran module + data (REBUILD.md R4).


def test_fundamentals_for_cash_refuses_and_says_why():
    """Fetching company fundamentals for a cash balance must refuse and name
    the instrument type rather than consult a hard-coded skip list."""
    with pytest.raises(ValueError, match="cash; company fundamentals do not apply"):
        pf.get_fundamentals("USD_CASH")


# === Notes integration — removed 2026-09-29 ===
#
# `_notes_file_for` and `write_fundamentals_to_notes` were deleted with the
# rest of the auto-fundamentals writer. These are not tests that were
# weakened to go green: the functions they covered do not exist, and both
# had been unrunnable for months — the chain raised `KeyError: 'kind'` on a
# positions.csv column deleted in Phase 2d, and needed the archived
# damodaran module besides.
#
# If the comparison is ever restored (REBUILD.md R4), restore these from
# git alongside it rather than writing new ones: they encode two decisions
# worth keeping — that ASML_USD and ASML_EUR share one note, and that the
# writer must REFUSE to create a note that does not exist, because the
# thesis is his to write.


# ─── cache age: a number with no date looks the same at any age ───

def test_fetched_at_read_from_inside_the_file(tmp_path, monkeypatch):
    """The stamp in the file wins — it survives copying, mtime does not."""
    monkeypatch.setattr(pf, "CACHE_DIR", tmp_path)
    f = tmp_path / "NVDA.json"
    f.write_text(json.dumps({"Symbol": "NVDA", pf.FETCHED_AT: "2026-08-01"}))
    assert pf.fetched_at(f) == "2026-08-01"
    assert pf.age_days(f) == (date.today() - date(2026, 8, 1)).days


def test_fetched_at_falls_back_to_mtime_for_old_files(tmp_path, monkeypatch):
    """The 42 files cached before the stamp existed still report an age."""
    monkeypatch.setattr(pf, "CACHE_DIR", tmp_path)
    f = tmp_path / "OLD.json"
    f.write_text(json.dumps({"Symbol": "OLD"}))
    stamp = date(2026, 5, 26)
    ts = time.mktime(stamp.timetuple())
    os.utime(f, (ts, ts))
    assert pf.fetched_at(f) == "2026-05-26"


def test_stale_cache_says_so_and_names_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE_DIR", tmp_path)
    f = tmp_path / "TSLA.json"
    f.write_text(json.dumps({"Symbol": "TSLA", pf.FETCHED_AT: "2026-01-01",
                             pf.FETCHED_AT_IS_APPROX: True}))
    msg = pf.staleness_warning("TSLA")
    assert "days old" in msg and "force_refresh=True" in msg
    assert "approximate" in msg   # provenance is not hidden


def test_fresh_cache_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "CACHE_DIR", tmp_path)
    f = tmp_path / "NVDA.json"
    f.write_text(json.dumps({"Symbol": "NVDA", pf.FETCHED_AT: date.today().isoformat()}))
    assert pf.staleness_warning("NVDA") == ""


def test_missing_cache_is_reported_not_treated_as_fresh(tmp_path, monkeypatch):
    """BRKB, IBM and TSM are held and have no cached fundamentals at all."""
    monkeypatch.setattr(pf, "CACHE_DIR", tmp_path)
    assert "No fundamentals cached" in pf.staleness_warning("BRK-B")
