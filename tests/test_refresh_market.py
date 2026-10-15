"""Tests for the one command that refreshes everything.

`ops/refresh_market.py` drives the three existing refreshers in order and
must never reimplement their fetch logic, so most of what is worth testing
here is the glue: the ticker list fed to the ETF refreshers, and the rule
that one refresher refusing never costs the other two. Real network calls
belong to the refreshers' own test files
(`tests/test_refresh_etf_compositions.py`, `test_refresh_etf_holdings.py`,
`test_refresh_classification.py`) and are not repeated here.

`ops/` is deliberately not a package (ruling, Task 6), so the script under
test is loaded by file path, the way `tests/test_refresh_etf_compositions.py`
already does.

Run from project root:
    venv/bin/python -m pytest tests/test_refresh_market.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "refresh_market.py"


def _load():
    spec = importlib.util.spec_from_file_location("refresh_market", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def refresh_market():
    return _load()


# ─── the ticker list: every held ETF plus the benchmark ───

def test_the_benchmark_is_spy(refresh_market):
    assert refresh_market.BENCHMARK_TICKERS == ["SPY"]


def test_held_etfs_get_the_benchmark_appended(refresh_market):
    assert refresh_market.tickers_to_refresh(["QQQ", "XLK"]) == ["QQQ", "XLK", "SPY"]


def test_the_benchmark_is_never_asked_for_twice(refresh_market):
    """If SPY were ever held, the list must still name it once — a provider
    should never be asked for the same breakdown twice in one run."""
    assert refresh_market.tickers_to_refresh(["SPY", "QQQ"]) == ["SPY", "QQQ"]


def test_an_empty_book_still_gets_the_benchmark(refresh_market):
    assert refresh_market.tickers_to_refresh([]) == ["SPY"]


# ─── a fund with nothing equity inside never reaches the composition providers ───

def test_a_fund_with_nothing_equity_inside_is_skipped_for_compositions(refresh_market):
    """SGOV/IBIT/SLV/DBA can never get a usable sector answer from an equity
    provider — confirmed live, 2026-10-04 (0.0% from both providers for all
    three). Asking anyway spends Alpha Vantage's shared daily quota on a
    question with no answer."""
    no_equity_inside = {"SGOV": "Treasury bills", "IBIT": "bitcoin"}
    got = refresh_market.tickers_for_compositions(
        ["QQQ", "SGOV", "IBIT", "SPY"], no_equity_inside)
    assert got == ["QQQ", "SPY"]


def test_the_benchmark_is_not_in_no_equity_inside(refresh_market):
    """`tickers_for_compositions()` is a plain filter with no SPY-specific
    case — it reaches the composition providers only because it is genuinely
    not a member of the real exclusion list. Checked against the real list
    `ops/refresh_etf_holdings.py` owns, not a copy, so the two cannot drift."""
    hold_mod = refresh_market._load("refresh_etf_holdings")
    assert "SPY" not in hold_mod.NO_EQUITY_INSIDE
    assert refresh_market.tickers_for_compositions(
        ["SPY"], hold_mod.NO_EQUITY_INSIDE) == ["SPY"]


def test_a_no_equity_fund_carrying_an_equity_breakdown_is_refused(
        refresh_market, tmp_path):
    """The DBA failure, made impossible to repeat quietly.

    Skipping a fund is right when its row says what the fund really is. It
    is wrong when the row is already a wrong answer: on 2026-10-01 a refresh
    replaced DBA's single `Materials - Agriculture` row with eleven
    Morningstar equity sectors for a fund that holds agricultural futures
    and T-bills, and the skip meant no later run could ever reach it. A
    frozen wrong answer wearing a fresh date is worse than a stale one,
    because nothing about it looks stale.
    """
    comp = tmp_path / "etf_compositions.csv"
    comp.write_text(
        '"ticker","sector","weight","top_holding","source","source_quality","as_of"\n'
        '"DBA","Healthcare",0.6,"","Morningstar via yfinance","vendor","2026-10-01"\n'
        '"DBA","Materials",0.4,"","Morningstar via yfinance","vendor","2026-10-01"\n'
    )
    with pytest.raises(SystemExit) as e:
        refresh_market.tickers_for_compositions(
            ["QQQ", "DBA"], {"DBA": "agricultural futures"}, comp)
    assert "DBA" in str(e.value)
    assert "agricultural futures" in str(e.value)


def test_a_no_equity_fund_with_one_honest_row_is_still_just_skipped(
        refresh_market, tmp_path):
    """The normal case, and the one the refusal must not break.

    SGOV's single 100% row is what the fund IS. There is nothing to fix, so
    the skip stands and the run goes on — a refusal here would make /market
    unusable for a book holding any cash fund at all.
    """
    comp = tmp_path / "etf_compositions.csv"
    comp.write_text(
        '"ticker","sector","weight","top_holding","source","source_quality","as_of"\n'
        '"SGOV","Cash",1.0,"","iShares issuer","issuer",""\n'
    )
    assert refresh_market.tickers_for_compositions(
        ["QQQ", "SGOV"], {"SGOV": "Treasury bills"}, comp) == ["QQQ"]


def test_a_no_equity_fund_with_no_breakdown_at_all_is_still_just_skipped(
        refresh_market, tmp_path):
    """Nothing on file is not a wrong answer on file."""
    comp = tmp_path / "etf_compositions.csv"
    comp.write_text(
        '"ticker","sector","weight","top_holding","source","source_quality","as_of"\n'
    )
    assert refresh_market.tickers_for_compositions(
        ["QQQ", "IBIT"], {"IBIT": "bitcoin"}, comp) == ["QQQ"]


def test_the_real_book_has_no_no_equity_fund_wearing_an_equity_breakdown(
        refresh_market):
    """The live data, through the live default path. This is the assertion
    that would have been red on 2026-10-01 and is green now that DBA's own
    row is back."""
    hold_mod = refresh_market._load("refresh_etf_holdings")
    refresh_market.tickers_for_compositions(
        list(hold_mod.NO_EQUITY_INSIDE), hold_mod.NO_EQUITY_INSIDE)


# ─── a refusal in one step must never stop the other two ───

def test_a_refusal_in_one_step_does_not_stop_the_rest(refresh_market):
    """Yaron should get one run and one complete picture, never a
    half-finished one that died on step two (ruling, Task 7 brief)."""
    calls = []

    def ok():
        calls.append("ok")

    def refuses():
        calls.append("refuses")
        raise SystemExit("nothing refreshed")

    def crashes():
        calls.append("crashes")
        raise ValueError("boom")

    failed, warnings = refresh_market.run_steps(
        [("first", ok), ("second", refuses), ("third", crashes)]
    )
    assert calls == ["ok", "refuses", "crashes"]      # every step ran
    assert failed == ["second", "third"]
    assert warnings == []


def test_a_clean_run_reports_no_failures(refresh_market):
    assert refresh_market.run_steps([("first", lambda: None)]) == ([], [])


def test_a_warning_is_collected_even_though_the_step_still_succeeded(
    refresh_market, capsys
):
    """The real shape of the XLF case: the step neither refuses nor crashes,
    it just prints a line with the "⚠" convention the refreshers already use
    for a real but non-fatal problem — a whole sector lost on refresh, say.
    `run_steps()` must not need that line to be the LAST thing printed to
    notice it."""
    def warns_but_succeeds():
        print("  some ordinary progress line")
        print("  ⚠ XLF   loses Consumer Discretionary — a whole sector")
        print("  some more ordinary progress after it")

    failed, warnings = refresh_market.run_steps([("first", warns_but_succeeds)])
    assert failed == []
    assert warnings == ["⚠ XLF   loses Consumer Discretionary — a whole sector"]

    # the real terminal still sees everything, in order — collecting a copy
    # must never swallow the original
    out = capsys.readouterr().out
    assert "some ordinary progress line" in out
    assert "loses Consumer Discretionary" in out
    assert "some more ordinary progress after it" in out


def test_a_warning_from_a_step_that_then_refuses_is_still_collected(refresh_market):
    """A provider can print a real warning and still end the step in a
    refusal (or a crash) — the warning must survive either way, since
    `failed` alone cannot carry the detail of what specifically went wrong."""
    def warns_then_refuses():
        print("  ⚠ something real happened before the refusal")
        raise SystemExit("nothing refreshed")

    failed, warnings = refresh_market.run_steps([("first", warns_then_refuses)])
    assert failed == ["first"]
    assert warnings == ["⚠ something real happened before the refusal"]


def test_main_refuses_an_unknown_positional_argument(refresh_market):
    """`ops/refresh_market.py XLF --write` must not silently refresh and
    write all thirty funds just because only `--write` was ever checked —
    all three sibling scripts DO take tickers, so typing one here expecting
    a one-fund scope is a plausible mistake (found by review). Raised before
    anything is loaded or fetched, so no mocking is needed here."""
    with pytest.raises(SystemExit, match="takes no tickers"):
        refresh_market.main(argv=["XLF", "--write"])


def test_main_runs_every_step_even_when_the_first_two_refuse(
    refresh_market, monkeypatch, capsys
):
    """End-to-end through `main()`, with the three refreshers faked out so
    this exercises the wiring (argument pass-through, order, continuation)
    without touching a network."""
    calls = []

    class FakeComp:
        def main(self):
            calls.append(("comp", tuple(sys.argv)))
            raise SystemExit("AV quota used up")

    class FakeHold:
        NO_EQUITY_INSIDE = {}   # none of the fakes below need the exclusion

        def held_etfs(self):
            return ["QQQ"]

        def main(self, argv):
            calls.append(("hold", tuple(argv)))
            raise ValueError("SPDR site down")

    class FakeClassify:
        def main(self):
            calls.append(("classify", tuple(sys.argv)))
            # the step itself succeeds — a warning is not a refusal, and
            # must still reach the closing summary (the XLF case, found by
            # review: "All 3 refreshers ran" must not bury it)
            print("  ⚠ XLF   loses Consumer Discretionary — a whole sector")

    class FakeGaps:
        def main(self):
            calls.append(("gaps", ()))

    fakes = {"refresh_etf_compositions": FakeComp(), "refresh_etf_holdings": FakeHold(),
             "refresh_classification": FakeClassify(), "data_gaps": FakeGaps()}
    monkeypatch.setattr(refresh_market, "_load", lambda name: fakes[name])

    refresh_market.main(argv=["--write"])

    names = [c[0] for c in calls]
    assert names == ["comp", "hold", "classify", "gaps"]   # every step ran, in order

    # the ETF refreshers were asked for the held fund AND the benchmark
    assert calls[0][1][1:] == ("QQQ", "SPY", "--write")
    assert calls[1][1] == ("QQQ", "SPY", "--write")
    # the classifier takes no tickers — it scopes itself to every held
    # non-ETF instrument — but --write must still reach it
    assert calls[2][1][1:] == ("--write",)

    out = capsys.readouterr().out
    assert "2 of 3 refresher(s) did not complete cleanly" in out
    # the warning from the step that SUCCEEDED still reaches the closing
    # summary, not just the scrollback
    assert "1 warning(s) from the refreshers above" in out
    assert "loses Consumer Discretionary" in out.split("warning(s) from")[1]
    assert "--write was set" in out


# ─── SPY must never leak into an exposure or holdings figure ───

def test_the_benchmark_has_no_position_in_the_real_book():
    """Today's baseline fact this whole feature rests on: SPY is not a row
    in `data/positions.csv`. On its own this does NOT prove `by_sector()`
    cannot be fooled once SPY has a composition breakdown on file (today's
    `data/market/etf_compositions.csv` carries zero SPY rows, since no
    `--write` has run yet) — see
    `test_the_benchmark_cannot_leak_into_exposure_even_with_a_breakdown_on_file`
    for that proof, built against the state a real `--write` produces."""
    held = set(pd.read_csv(REPO / "data" / "positions.csv")["instrument_id"])
    assert "SPY" not in held


def test_the_benchmark_cannot_leak_into_exposure_even_with_a_breakdown_on_file(
    tmp_path, monkeypatch
):
    """The real regression this guards against. After the first `--write`,
    `etf_compositions.csv` carries SPY's own sector rows same as any held
    ETF's — this is the state `test_the_benchmark_has_no_position_in_the_real_book`
    cannot exercise, since no `--write` has happened against the real file.
    Built here directly: SPY has a full breakdown on file and is NOT in
    `positions.csv`, and the total must still equal only what is actually
    held. If `by_sector()` (or anything upstream of it) ever iterated
    composition tickers instead of held positions, this is what would catch
    it — every sector figure would be inflated by a fund he does not own."""
    import csv

    from src import exposure

    comp_rows = [
        {"ticker": "QQQ", "sector": "Technology", "weight": 1.0,
         "top_holding": "", "source": "test", "source_quality": "vendor",
         "as_of": "2026-10-04"},
        # SPY's breakdown — on file, exactly as a real --write leaves it,
        # and deliberately summing to a different, larger total than QQQ's
        # so a leak would be impossible to miss as rounding.
        {"ticker": "SPY", "sector": "Technology", "weight": 0.3,
         "top_holding": "", "source": "test", "source_quality": "vendor",
         "as_of": "2026-10-04"},
        {"ticker": "SPY", "sector": "Financials", "weight": 0.7,
         "top_holding": "", "source": "test", "source_quality": "vendor",
         "as_of": "2026-10-04"},
    ]
    cp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(comp_rows, columns=exposure.COLUMNS).to_csv(
        cp, index=False, quoting=csv.QUOTE_NONNUMERIC)

    pp = tmp_path / "positions.csv"
    pd.DataFrame([{"instrument_id": "QQQ", "market_value": 1000.0}]).to_csv(
        pp, index=False)

    from src import instruments
    monkeypatch.setattr(instruments, "load", lambda *a, **k: pd.DataFrame([
        {"instrument_id": "QQQ", "instrument_type": "etf", "sector": "",
         "cluster_id": "Core US"},
        {"instrument_id": "SPY", "instrument_type": "etf", "sector": "",
         "cluster_id": "", "status": "benchmark"},
    ]))

    out = exposure.by_sector(cp, pp)
    assert out["market_value"].sum() == pytest.approx(1000.0)
    assert out.set_index("sector").loc["Technology", "market_value"] == 1000.0


def test_the_benchmark_instrument_row_is_a_benchmark_not_a_holding_status():
    """`status=benchmark` is none of active/closed/incomplete/watchlist
    (`ops/vocab/instrument_status`) — it must stay that way, or SPY starts
    reading as something he holds or is tracking toward a decision."""
    from src import instruments

    row = instruments.get("SPY")
    assert row["instrument_type"] == "etf"
    assert row["status"] == "benchmark"


def test_a_benchmark_row_does_not_show_up_as_an_incomplete_instrument():
    """SPY is never held and never will be, so it has no thesis to write —
    flagging it would put a permanently unclearable row in `instruments.gaps()`,
    the same failure already fixed once for ETFs and cash/FX."""
    from src import instruments

    g = instruments.gaps()
    assert "SPY" not in set(g["instrument_id"])
