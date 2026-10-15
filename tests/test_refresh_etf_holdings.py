"""A provider's holding 'symbol' is not always a company.

Cash lines, placeholders and blanks come back in the same column as real
tickers. Written straight into the table they become rows that look like
instruments and resolve to nothing — and because they carry weight, they
quietly shrink every real holding's share of the fund.

The second thing under test here is the floor. These weights are never scaled
to 100%, unlike the sector breakdowns, so a fund covered by ten holdings must
still read as ten holdings and not as a whole fund.
"""
from __future__ import annotations

import importlib.util
import io
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import market

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "refresh_etf_holdings.py"


@pytest.fixture(scope="module")
def refresh():
    spec = importlib.util.spec_from_file_location("refresh_etf_holdings", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _offline_and_empty_inbox(refresh, monkeypatch, tmp_path):
    """No test may touch the network or whatever is sitting in his inbox.

    The inbox is a real directory on his laptop that is not committed, so a
    test reading the default one would pass or fail depending on which
    spreadsheets he happened to have downloaded that week. And a download
    reached by accident would make a test that is supposed to be exercising
    the Yahoo fallback quietly exercise an issuer instead.
    """
    inbox = tmp_path / "holdings_inbox"
    inbox.mkdir()
    monkeypatch.setattr(refresh, "INBOX_DIR", inbox)

    # ...nor whatever is already STORED. `fetch_holdings` refuses a floor
    # that would make the record worse, so a test building a synthetic floor
    # for a fund his real table covers at 100% would be refused by his own
    # data — a pass or fail depending on what he happens to own this week.
    empty = tmp_path / "etf_holdings.csv"
    empty.write_text(
        '"ticker","holding","holding_name","weight","coverage","source",'
        '"as_of","as_of_kind"\n')
    monkeypatch.setattr(market, "HOLDINGS_PATH", empty)

    def _no_network(url: str) -> bytes:
        raise AssertionError(f"a test tried to download {url}")

    monkeypatch.setattr(refresh, "_download", _no_network)


# ─── clean_symbol: what is a company and what is a placeholder ───

@pytest.mark.parametrize("junk", ["--", "N/A", "", "   ", "CASH", "USD", None])
def test_junk_symbols_are_dropped(refresh, junk):
    assert refresh.clean_symbol(junk) is None


@pytest.mark.parametrize("good", ["NVDA", "BRK-B", "005930.KS", "ASML"])
def test_real_symbols_survive(refresh, good):
    assert refresh.clean_symbol(good) == good


def test_a_blank_cell_read_by_pandas_is_not_a_company(refresh):
    """An empty spreadsheet row arrives as NaN, a float, not a string.

    str(nan) is 'nan', which matches no junk word and looks like a ticker.
    A whole fund's worth of trailing blank rows would become one holding
    called 'nan' carrying the sum of their weights.
    """
    assert refresh.clean_symbol(float("nan")) is None


# ─── coverage: computed before anything is dropped ───

def test_coverage_is_the_sum_before_anything_is_dropped(refresh, monkeypatch):
    """Dropping a cash line must not inflate what is left.

    If coverage were computed after the drop, a fund reported as 50% covered
    with a 3% cash line would read as 47% of a fund that sums to 47% — and
    the weights would then look like a complete picture of half a fund.
    """
    frame = pd.DataFrame(
        {"Name": ["NVIDIA", "Apple", "Cash"], "Holding Percent": [0.3, 0.2, 0.03]},
        index=pd.Index(["NVDA", "AAPL", "--"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings("QQQ")
    assert [r["holding"] for r in got["rows"]] == ["NVDA", "AAPL"]
    assert got["coverage"] == pytest.approx(0.53)


def test_weights_are_never_scaled_up(refresh, monkeypatch):
    """The opposite of the compositions table, on purpose.

    There, a partial breakdown is normalised to 100% because it claims to
    describe the whole fund. Here it does not: ten holdings are ten holdings.
    Scaling them would turn a floor into a fabricated total.
    """
    frame = pd.DataFrame(
        {"Name": ["NVIDIA"], "Holding Percent": [0.4]},
        index=pd.Index(["NVDA"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings("QQQ")
    assert got["rows"][0]["weight"] == pytest.approx(0.4)
    assert got["coverage"] == pytest.approx(0.4)


def test_a_fund_with_no_holdings_data_returns_none(refresh, monkeypatch):
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: pd.DataFrame())
    assert refresh.fetch_holdings("SGOV") is None


def test_the_tail_below_the_threshold_is_dropped_and_coverage_still_says_so(
        refresh, monkeypatch):
    """A kept fund must never look more complete than it is.

    The threshold exists because a holding worth nineteen cents cannot change
    a decision. Letting it also shrink `coverage` would hide how much of the
    fund the stored rows really are.
    """
    tiny = refresh.MIN_HOLDING_WEIGHT / 2
    frame = pd.DataFrame(
        {"Name": ["Big", "Dust"], "Holding Percent": [0.5, tiny]},
        index=pd.Index(["BIG", "DUST"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings("QQQ")
    assert [r["holding"] for r in got["rows"]] == ["BIG"]
    assert got["coverage"] == pytest.approx(0.5 + tiny)


def test_the_same_company_listed_twice_is_summed_not_duplicated(
        refresh, monkeypatch):
    """The table refuses two rows for one company in one fund.

    Crashing on the second row would lose the whole fund; dropping it would
    understate the company. Adding them up is the only answer that is true.
    """
    frame = pd.DataFrame(
        {"Name": ["Alphabet", "Alphabet"], "Holding Percent": [0.03, 0.02]},
        index=pd.Index(["GOOG", "GOOG"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings("QQQ")
    assert [(r["holding"], r["weight"]) for r in got["rows"]] == [("GOOG", 0.05)]


def test_a_yahoo_fund_is_labelled_a_floor(refresh, monkeypatch):
    """Nobody reading the stored row may mistake ten holdings for a fund."""
    frame = pd.DataFrame(
        {"Name": ["NVIDIA"], "Holding Percent": [0.4]},
        index=pd.Index(["NVDA"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings("QQQ")
    assert got["exact"] is False
    assert "floor" in got["source"].lower()


# ─── a floor thinner than what is already known is not an improvement ───

def _mags_compositions(tmp_path):
    """MAGS as it really is on file: seven equal slices, each one company."""
    p = tmp_path / "etf_compositions.csv"
    names = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA"]
    rows = [{"ticker": "MAGS", "sector": f"S{i}", "weight": w,
             "top_holding": n, "source": "Roundhill equal-weight",
             "source_quality": "issuer", "as_of": ""}
            for i, (n, w) in enumerate(zip(names, [0.1429] * 6 + [0.1426]))]
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_a_floor_thinner_than_the_breakdown_is_refused(refresh, monkeypatch,
                                                       tmp_path):
    """Yahoo returned ONE holding for a seven-name equal-weight fund.

    Stored, that row said the book owns about 0.02% of NAV in META when the truth
    is about 0.71% — two tables in data/market/ describing the same fund and
    disagreeing by thirty times. MIN_FULL_HOLDINGS guarded only the issuer
    path, so nothing caught it.
    """
    frame = pd.DataFrame(
        {"Name": ["Microsoft Corp"], "Holding Percent": [0.19]},
        index=pd.Index(["MSFT"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    assert refresh.fetch_holdings(
        "MAGS", compositions_path=_mags_compositions(tmp_path)) is None


def test_a_floor_as_wide_as_the_breakdown_is_kept(refresh, monkeypatch,
                                                  tmp_path):
    """The guard refuses a REGRESSION, not a floor. Seven for seven stands."""
    names = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA"]
    frame = pd.DataFrame(
        {"Name": names, "Holding Percent": [0.14] * 7},
        index=pd.Index(names, name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings(
        "MAGS", compositions_path=_mags_compositions(tmp_path))
    assert got is not None and got["named"] == 7


def test_a_broad_fund_names_nothing_so_the_guard_cannot_bind(refresh, tmp_path):
    """`top_holding` is blank for every broad fund, which is why this is a
    floor UNDER the floor and not a general completeness rule — QQQ's
    breakdown knows no company names at all."""
    p = tmp_path / "etf_compositions.csv"
    pd.DataFrame([{"ticker": "QQQ", "sector": "Tech - Software", "weight": 1.0,
                   "top_holding": "", "source": "Invesco",
                   "source_quality": "issuer", "as_of": ""}]).to_csv(p, index=False)
    assert refresh.named_in_composition("QQQ", p) == 0


# ─── the issuer's own file ───

def _spdr_bytes(rows, as_of="As of 01-Oct-2026",
                header=("Name", "Ticker", "Identifier", "SEDOL", "Weight",
                        "Sector", "Shares Held", "Local Currency")):
    """A spreadsheet shaped like the one SPDR publishes."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Fund Name:", "State Street Technology Select Sector SPDR ETF"])
    ws.append(["Ticker Symbol:", "XLK"])
    ws.append(["Holdings:", as_of])
    ws.append([])
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _whole_fund(n=12):
    """n holdings that add up to the whole fund, as a percentage column."""
    each = 100.0 / n
    return [(f"COMPANY {i}", f"C{i}", "", "", each, "-", 1, "USD")
            for i in range(n)]


def test_the_issuers_own_as_of_date_is_recorded_not_todays(refresh):
    """The day we fetched is not the day the holdings are true for."""
    pairs, as_of = refresh.parse_spdr(_spdr_bytes(_whole_fund()))
    assert as_of == "2026-10-01"


def test_a_percentage_column_becomes_a_fraction(refresh):
    """15.470648 in the file means 15.47% of the fund, not 1547%."""
    rows = _whole_fund(1)
    rows[0] = ("NVIDIA CORP", "NVDA", "", "", 100.0, "-", 1, "USD")
    pairs, _ = refresh.parse_spdr(_spdr_bytes(rows))
    assert pairs[0][2] == pytest.approx(1.0)


def test_the_header_row_is_found_wherever_the_preamble_ends(refresh):
    """A redesign that adds a line above the table must not shift every column."""
    content = _spdr_bytes(_whole_fund(), as_of="As of 01-Oct-2026")
    pairs, as_of = refresh.parse_spdr(content)
    assert len(pairs) == 12 and as_of == "2026-10-01"


def test_an_html_page_served_as_a_spreadsheet_is_refused(refresh):
    """What iShares returns today: a web page with a spreadsheet's name."""
    with pytest.raises(ValueError):
        refresh.parse_spdr(b"<!DOCTYPE html>\n<html><body>Sorry</body></html>")


def test_a_half_parsed_file_is_refused_rather_than_stored(refresh, monkeypatch):
    """An issuer file claims to be the whole fund, so it must add up to one.

    This is the silent-breakage case: a redesign that still returns a
    readable sheet, with three rows of it. Stored, it would read as a fund
    that is 12% invested and 88% nothing.
    """
    rows = [("COMPANY A", "AAA", "", "", 4.0, "-", 1, "USD"),
            ("COMPANY B", "BBB", "", "", 4.0, "-", 1, "USD"),
            ("COMPANY C", "CCC", "", "", 4.0, "-", 1, "USD")]
    monkeypatch.setattr(refresh, "_download", lambda url: _spdr_bytes(rows))
    assert refresh.from_spdr("XLK") is None


def test_a_file_with_only_a_couple_of_holdings_is_refused(refresh, monkeypatch):
    """Weights can add to 1 and the file still be a fragment."""
    rows = [("COMPANY A", "AAA", "", "", 60.0, "-", 1, "USD"),
            ("COMPANY B", "BBB", "", "", 40.0, "-", 1, "USD")]
    monkeypatch.setattr(refresh, "_download", lambda url: _spdr_bytes(rows))
    assert refresh.from_spdr("XLK") is None


def test_a_good_issuer_file_is_exact_and_fully_covered(refresh, monkeypatch):
    monkeypatch.setattr(refresh, "_download", lambda url: _spdr_bytes(_whole_fund()))
    got = refresh.from_spdr("XLK")
    assert got["exact"] is True
    assert got["coverage"] == pytest.approx(1.0)
    assert got["as_of"] == "2026-10-01"


def test_coverage_can_never_exceed_the_whole_fund(refresh, monkeypatch):
    """Issuer files round, and XBI's add up to a hair over 100%.

    The table says coverage is a fraction of a fund, and a fund cannot be
    more than all of itself — so the rounding is absorbed rather than
    refused, which would throw away a good file over the sixth decimal.
    """
    rows = _whole_fund(10)
    rows.append(("ROUNDING", "RND", "", "", 0.05, "-", 1, "USD"))
    monkeypatch.setattr(refresh, "_download", lambda url: _spdr_bytes(rows))
    assert refresh.from_spdr("XLK")["coverage"] == 1.0


def test_an_issuer_with_no_downloader_falls_through_to_the_floor(
        refresh, monkeypatch):
    """iShares and Invesco both refuse to be automated. That is not an error."""
    frame = pd.DataFrame(
        {"Name": ["NVIDIA"], "Holding Percent": [0.4]},
        index=pd.Index(["NVDA"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings("QQQ", issuer="Invesco")
    assert got["exact"] is False


# ─── a file he dropped in himself ───

def test_a_file_in_the_inbox_is_read(refresh, tmp_path, monkeypatch):
    """A website redesign cannot break a file that is already on disk."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pd.DataFrame({
        "Ticker": [f"C{i}" for i in range(10)],
        "Name": [f"Company {i}" for i in range(10)],
        "Weight (%)": [10.0] * 10,
    }).to_csv(inbox / "IWM.csv", index=False)
    got = refresh.fetch_holdings("IWM", issuer="iShares", inbox=inbox)
    assert got["exact"] is True
    assert got["coverage"] == pytest.approx(1.0)
    assert "IWM.csv" in got["source"]


def test_an_inbox_file_with_no_date_says_the_date_is_ours(refresh, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pd.DataFrame({
        "Symbol": [f"C{i}" for i in range(10)],
        "Weight": [0.1] * 10,
    }).to_csv(inbox / "IWM.csv", index=False)
    got = refresh.fetch_holdings("IWM", issuer="iShares", inbox=inbox)
    assert "read" in got["source"], got["source"]


def test_one_funds_file_is_never_read_for_another_fund(refresh, tmp_path,
                                                       monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pd.DataFrame({"Ticker": ["AAA"], "Weight": [1.0]}).to_csv(
        inbox / "IWM.csv", index=False)
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: pd.DataFrame())
    assert refresh.fetch_holdings("EEM", issuer="iShares", inbox=inbox) is None


# ─── the default scope ───

def _positions(tmp_path, rows):
    p = tmp_path / "positions.csv"
    pd.DataFrame(rows, columns=["ticker", "instrument_id", "market_value"]).to_csv(
        p, index=False)
    return p


def _instruments(tmp_path, rows):
    from src import instruments as ins

    p = tmp_path / "instruments.csv"
    frame = pd.DataFrame([
        {**{c: "" for c in ins.COLUMNS}, "instrument_id": i, "name": n,
         "instrument_type": t, "status": "active", "added": "2026-08-10"}
        for i, n, t in rows])
    frame[ins.COLUMNS].to_csv(p, index=False)
    return p


def test_the_default_scope_is_whatever_is_held_today(refresh, tmp_path):
    """Never a hardcoded list.

    A fund bought this morning that is not in some constant would be skipped
    in silence, and its money would vanish out of every look-through number
    while the report still said it had run.
    """
    pos = _positions(tmp_path, [("BRANDNEW", "BRANDNEW", "5000"),
                                ("NVDA", "NVDA", "9000"),
                                ("USD_CASH", "USD_CASH", "100")])
    inst = _instruments(tmp_path, [("BRANDNEW", "Some New ETF", "etf"),
                                   ("NVDA", "NVIDIA", "stock"),
                                   ("USD_CASH", "USD Cash", "cash")])
    assert refresh.held_etfs(pos, inst) == ["BRANDNEW"]


def test_the_biggest_fund_is_fetched_first(refresh, tmp_path):
    """An interrupted run must have covered the money that matters."""
    pos = _positions(tmp_path, [("SMALL", "SMALL", "100"),
                                ("BIG", "BIG", "9000")])
    inst = _instruments(tmp_path, [("SMALL", "Small ETF", "etf"),
                                   ("BIG", "Big ETF", "etf")])
    assert refresh.held_etfs(pos, inst) == ["BIG", "SMALL"]


# ─── the gap report ───

def test_a_fund_holding_no_companies_is_not_a_gap(refresh):
    """SGOV is treasury bills, SLV is bullion, IBIT is bitcoin.

    None of them has a company inside it, so none of them is a hole in the
    data. Reporting them would put permanently unclearable rows in a list
    that has to be able to reach zero to stay read.
    """
    report = refresh.gap_report({"SGOV": None, "SLV": None, "IBIT": None})
    assert report["missing"] == []
    assert sorted(report["no_equity_inside"]) == ["IBIT", "SGOV", "SLV"]


def test_a_fund_that_failed_to_fetch_is_a_gap(refresh):
    report = refresh.gap_report({"EEM": None})
    assert report["missing"] == ["EEM"]


def test_exact_and_floor_are_counted_apart(refresh):
    """'Incomplete' is fine. 'Incomplete and indistinguishable' is not."""
    report = refresh.gap_report({
        "XLK": {"exact": True}, "QQQ": {"exact": False}})
    assert report["exact"] == ["XLK"] and report["floor"] == ["QQQ"]


# ─── writing ───

def test_writing_replaces_a_funds_rows_rather_than_merging(refresh, tmp_path):
    """Yesterday's holding that the fund sold must not survive the refresh."""
    from src import market

    path = tmp_path / "etf_holdings.csv"
    pd.DataFrame([
        {"ticker": "QQQ", "holding": "SOLD", "holding_name": "Sold Co",
         "weight": 0.1, "coverage": 0.5, "source": "Yahoo", "as_of": "2026-09-01",
         "as_of_kind": "read"},
        {"ticker": "XLK", "holding": "NVDA", "holding_name": "NVIDIA",
         "weight": 0.15, "coverage": 1.0, "source": "SPDR", "as_of": "2026-10-01",
         "as_of_kind": "published"},
    ]).to_csv(path, index=False)

    refresh.write([{
        "ticker": "QQQ", "coverage": 0.5, "source": "Yahoo top holdings — floor",
        "as_of": "2026-10-04", "as_of_kind": "read", "exact": False,
        "rows": [{"holding": "AAPL", "holding_name": "Apple", "weight": 0.07}],
    }], path)

    out = market.load_holdings(path)
    assert set(out.loc[out["ticker"] == "QQQ", "holding"]) == {"AAPL"}
    assert set(out.loc[out["ticker"] == "XLK", "holding"]) == {"NVDA"}


def test_what_is_written_passes_the_tables_own_schema(refresh, tmp_path):
    from src import market

    path = tmp_path / "etf_holdings.csv"
    path.write_text(
        "ticker,holding,holding_name,weight,coverage,source,as_of,as_of_kind\n")
    refresh.write([{
        "ticker": "QQQ", "coverage": 0.49, "source": "Yahoo top holdings — floor",
        "as_of": "2026-10-04", "as_of_kind": "read", "exact": False,
        "rows": [{"holding": "NVDA", "holding_name": "NVIDIA", "weight": 0.085}],
    }], path)
    assert len(market.load_holdings(path)) == 1


# ─── the ceiling: nonsense must be refused, never clamped into looking good ──

def test_a_source_claiming_more_than_the_whole_fund_is_refused(
        refresh, monkeypatch):
    """The clamp must not be able to turn nonsense into reassurance.

    Yahoo answers with one real 8% holding and a junk cash line weighing
    140%. The junk is dropped, so the "a holding weighs more than the fund"
    guard never sees it; the raw sum is 148%; and a clamp with no ceiling
    above it would store `coverage 1.0` for a fund whose only row is 8% of
    it. Incomplete and indistinguishable from complete, on the one source
    that has no other validation at all.
    """
    frame = pd.DataFrame(
        {"Name": ["NVIDIA", "Cash"], "Holding Percent": [0.08, 1.4]},
        index=pd.Index(["NVDA", "--"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    assert refresh.fetch_holdings("QQQ") is None


def test_the_ceiling_binds_the_floor_as_well_as_the_issuer(refresh):
    """Not a check that only the validated sources run."""
    pairs = [("AAA", "A", 0.8), ("BBB", "B", 0.8)]
    assert refresh.assemble("X", pairs, "anywhere", "2026-10-04", "read",
                            exact=False) is None


def test_rounding_over_a_hundred_percent_is_still_absorbed(refresh):
    """The ceiling must not throw away a good issuer file over the decimals."""
    pairs = [(f"C{i}", f"Co {i}", 0.1 + 0.00002) for i in range(10)]
    got = refresh.assemble("X", pairs, "anywhere", "2026-10-04", "published",
                           exact=True)
    assert got["coverage"] == 1.0


# ─── where a fund parks its cash is not a company ───

@pytest.mark.parametrize("symbol,name", [
    ("XTSLA", "BlackRock Cash Funds Treasury SL Agency"),
    ("FGXXX", "First American Government Obligs X"),
    ("XBOX", "Roundhill Ultra Shrt Dur No Div TrgtETF"),
    ("AGPXX", "Invesco Shrt-Trm Inv Gov&Agcy Instl"),
    ("WHATEVER", "Some Money Market Fund Class I"),
])
def test_a_cash_sweep_is_not_stored_as_a_company(refresh, monkeypatch,
                                                 symbol, name):
    """They carry real-looking tickers, so the placeholder list misses them.

    Stored, they resolve to no instrument and count toward a look-through
    total as equity the fund does not hold.
    """
    frame = pd.DataFrame(
        {"Name": ["NVIDIA", name], "Holding Percent": [0.3, 0.12]},
        index=pd.Index(["NVDA", symbol], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings("QQQ")
    assert [r["holding"] for r in got["rows"]] == ["NVDA"]


def test_dropping_a_cash_sweep_does_not_inflate_what_is_left(refresh,
                                                             monkeypatch):
    """Same rule as a placeholder: the money is in the fund, just not in a
    company. Coverage still counts it, and `stored` says how much of the fund
    the company rows really describe."""
    frame = pd.DataFrame(
        {"Name": ["NVIDIA", "First American Government Obligs X"],
         "Holding Percent": [0.07, 0.12]},
        index=pd.Index(["NVDA", "FGXXX"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings("QQQ")
    assert got["coverage"] == pytest.approx(0.19)
    assert got["stored"] == pytest.approx(0.07)


def test_a_real_company_with_cash_in_its_name_survives(refresh, monkeypatch):
    """"Cash America International" was a listed company.

    A bare "cash" match would silently delete a real holding, which is worse
    than keeping a sweep: one understates, the other loses a position.
    """
    frame = pd.DataFrame(
        {"Name": ["Cash America International Inc"], "Holding Percent": [0.3]},
        index=pd.Index(["CSH"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    got = refresh.fetch_holdings("QQQ")
    assert [r["holding"] for r in got["rows"]] == ["CSH"]


# ─── whose date is on the row ───

def test_the_yahoo_rows_say_the_date_is_only_when_we_read_them(
        refresh, monkeypatch):
    """Yahoo publishes no as-of, and the cell is shaped exactly like SPDR's.

    Without this, a panel sorting on `as_of` shows the stalest rows in the
    table as the freshest — Yahoo's European holdings still name companies
    that were renamed years ago, stamped with today.
    """
    frame = pd.DataFrame(
        {"Name": ["NVIDIA"], "Holding Percent": [0.4]},
        index=pd.Index(["NVDA"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    source = refresh.fetch_holdings("QQQ")["source"]
    assert "no as-of published" in source and "read" in source


def test_an_issuer_date_is_never_called_a_read_date(refresh, monkeypatch):
    """The distinction is only worth anything if the honest case stays clean."""
    monkeypatch.setattr(refresh, "_download", lambda url: _spdr_bytes(_whole_fund()))
    assert "read" not in refresh.from_spdr("XLK")["source"]


# ─── a fund with no company inside it is never fetched ───

def test_a_fund_with_nothing_to_look_through_to_is_not_fetched(
        refresh, monkeypatch, tmp_path):
    """Not fetched, rather than fetched and found empty.

    These are among the largest positions on the book. A provider that one
    day answers with a bullion or a cash line would have it stored as a
    company holding, and nothing downstream could tell.
    """
    asked = []
    monkeypatch.setattr(refresh, "fetch_holdings",
                        lambda *a, **k: asked.append(a) or None)
    pos = _positions(tmp_path, [("SLV", "SLV", "2000")])
    inst = _instruments(tmp_path, [("SLV", "iShares Silver Trust", "etf")])
    with pytest.raises(SystemExit):
        refresh.main([], positions_path=pos, instruments_path=inst)
    assert asked == [], "a fund with no company inside it was fetched anyway"


def test_a_fund_reclassified_as_holding_no_companies_loses_its_rows(
        refresh, tmp_path):
    """Rows must not outlive the decision that said there is nothing there."""
    from src import market

    path = tmp_path / "etf_holdings.csv"
    pd.DataFrame([
        {"ticker": "DBA", "holding": "AGPXX", "holding_name": "A money fund",
         "weight": 0.43, "coverage": 0.47, "source": "Yahoo", "as_of": "2026-10-04",
         "as_of_kind": "read"},
        {"ticker": "XLK", "holding": "NVDA", "holding_name": "NVIDIA",
         "weight": 0.15, "coverage": 1.0, "source": "SPDR", "as_of": "2026-10-01",
         "as_of_kind": "published"},
    ]).to_csv(path, index=False)

    out = refresh.write([], path, clear={"DBA"})
    assert set(out["ticker"]) == {"XLK"}


def test_a_failed_fetch_does_not_delete_yesterdays_rows(refresh, tmp_path):
    """A provider outage must not cost data. Only a reclassification may."""
    path = tmp_path / "etf_holdings.csv"
    pd.DataFrame([
        {"ticker": "EEM", "holding": "TSM", "holding_name": "TSMC",
         "weight": 0.09, "coverage": 0.37, "source": "Yahoo", "as_of": "2026-10-04",
         "as_of_kind": "read"},
    ]).to_csv(path, index=False)
    out = refresh.write([], path)
    assert set(out["ticker"]) == {"EEM"}


# ─── published vs read: the column, not just the prose ───

def test_only_a_source_that_published_a_date_can_say_published(refresh):
    """The single place in the fetcher that can produce `published`.

    It cannot produce it without a date to point at, which is what makes the
    claim structurally true rather than a convention a later branch can
    forget.
    """
    assert refresh.dated("2026-10-01") == ("2026-10-01", "published")
    when, kind = refresh.dated("")
    assert kind == "read" and when == date.today().isoformat()


def test_the_yahoo_branch_never_claims_a_published_date(refresh, monkeypatch):
    frame = pd.DataFrame(
        {"Name": ["NVIDIA"], "Holding Percent": [0.4]},
        index=pd.Index(["NVDA"], name="Symbol"))
    monkeypatch.setattr(refresh, "_top_holdings", lambda t: frame)
    assert refresh.fetch_holdings("QQQ")["as_of_kind"] == "read"


def test_an_issuer_file_that_states_its_date_says_published(refresh, monkeypatch):
    monkeypatch.setattr(refresh, "_download", lambda url: _spdr_bytes(_whole_fund()))
    got = refresh.from_spdr("XLK")
    assert got["as_of_kind"] == "published" and got["as_of"] == "2026-10-01"


def test_an_issuer_file_with_no_date_in_it_says_read(refresh, monkeypatch):
    """A redesign that drops the "As of" line must not promote today's date.

    This is the regression that would make the column a lie: the branch is
    the one that normally publishes, so the fallback is where a wrong
    `published` would come from.
    """
    monkeypatch.setattr(refresh, "_download",
                        lambda url: _spdr_bytes(_whole_fund(), as_of="Holdings"))
    got = refresh.from_spdr("XLK")
    assert got["as_of_kind"] == "read"
    assert got["as_of"] == date.today().isoformat()
    assert "no as-of" in got["source"]


def test_an_inbox_file_with_no_date_says_read(refresh, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pd.DataFrame({"Symbol": [f"C{i}" for i in range(10)],
                  "Weight": [0.1] * 10}).to_csv(inbox / "IWM.csv", index=False)
    got = refresh.fetch_holdings("IWM", issuer="iShares", inbox=inbox)
    assert got["as_of_kind"] == "read"


def test_the_written_table_never_claims_a_date_nobody_published(refresh):
    """The real file, not a fixture.

    No row may say `published` while its own source prose says no as-of was
    published — the two would have to be written by different code paths to
    disagree, which is exactly the drift this guards.
    """
    from src import market

    df = market.load_holdings()
    if df.empty:
        pytest.skip("nothing fetched yet")
    claims_published = df[df["as_of_kind"] == "published"]
    assert not claims_published["source"].str.contains("no as-of").any()
    yahoo = df[df["source"].str.contains("Yahoo")]
    assert set(yahoo["as_of_kind"]) <= {"read"}


# ─── a CSV whose preamble is narrower than its table ───

def _globalx_bytes(as_of: str = "Fund Holdings Data as of 10/02/2026",
                   rows: int = 12) -> bytes:
    """A Global X holdings file, preamble and all.

    The shape that matters is the first line: one cell, where the table below
    it has seven. Every issuer writes a file like this.
    """
    body = "\n".join(
        f"{100 / rows:.4f},T{i:02d},COMPANY {i} INC,SED{i:04d},10.00,"
        f'"1,000.00","10,000.00"' for i in range(rows))
    return ("Global X Cybersecurity ETF\n"
            f"{as_of}\n"
            "% of Net Assets,Ticker,Name,SEDOL,Market Price ($),"
            "Shares Held,Market Value ($)\n"
            f"{body}\n"
            '"The information contained herein may not be reproduced."\n'
            ).encode()


def test_a_one_cell_title_above_a_seven_column_table_still_parses(refresh):
    """The bug that made every issuer file bounce out of the inbox.

    pandas sized a header-less read from the first line — the fund's own
    name, one cell — so the table below it died as "too many fields" before a
    single holding was read. Nothing reached the storable-rows stage, and the
    file was reported unusable rather than thin.
    """
    pairs, as_of = refresh.parse_csv(_globalx_bytes())
    assert as_of == "2026-10-02"
    result = refresh.assemble("BUG", pairs, "test", *refresh.dated(as_of),
                              exact=True)
    assert refresh.why_not_exact(result) == ""
    # Twelve companies, not thirteen: the disclaimer line below the table is
    # one cell wide and carries no symbol, so it is dropped like any
    # placeholder rather than stored as a holding named "nan".
    assert len(result["rows"]) == 12


def test_the_date_in_the_preamble_is_the_issuers_not_todays(refresh):
    """`as of 10/02/2026` is when the holdings were true, not when we looked."""
    _, as_of = refresh.parse_csv(_globalx_bytes())
    when, kind = refresh.dated(as_of)
    assert (when, kind) == ("2026-10-02", "published")


def test_a_csv_with_no_date_in_it_never_invents_one(refresh):
    """An undated file is read-dated, and the source string has to say so."""
    _, as_of = refresh.parse_csv(_globalx_bytes(as_of="Holdings"))
    assert as_of == ""


def test_his_inbox_and_the_downloader_read_a_file_identically(refresh, tmp_path):
    """One parser, so a file he fetches by hand cannot disagree with ours.

    They were separate routes until 2026-10-05; the inbox could not read what
    the issuer published, which is the whole reason this file exists.
    """
    content = _globalx_bytes()
    (tmp_path / "BUG_holdings.csv").write_bytes(content)
    by_hand = refresh.from_inbox("BUG", tmp_path)
    fetched_pairs, fetched_as_of = refresh.parse_csv(content)
    fetched = refresh.assemble("BUG", fetched_pairs, "test",
                               *refresh.dated(fetched_as_of), exact=True)
    assert by_hand is not None
    assert by_hand["rows"] == fetched["rows"]
    assert by_hand["coverage"] == fetched["coverage"]
    assert by_hand["as_of"] == fetched["as_of"] == "2026-10-02"


# ─── Global X ───

def test_global_x_follows_its_fund_page_to_the_dated_file(refresh, monkeypatch):
    """The file's name carries its date, so the address cannot be guessed."""
    asked = []

    def _fake(url: str) -> bytes:
        asked.append(url)
        if url.endswith("/bug/"):
            return (b'<a href="https://assets.globalxetfs.com/funds/holdings/'
                    b'bug_full-holdings_20261002.csv">Download</a>')
        return _globalx_bytes()

    monkeypatch.setattr(refresh, "_download", _fake)
    result = refresh.from_globalx("BUG")
    assert result is not None
    assert result["as_of"] == "2026-10-02" and result["as_of_kind"] == "published"
    assert result["coverage"] == pytest.approx(1.0, abs=1e-4)
    assert len(result["rows"]) == 12
    assert asked == ["https://globalxetfs.com/funds/bug/",
                     "https://assets.globalxetfs.com/funds/holdings/"
                     "bug_full-holdings_20261002.csv"]


def test_global_x_is_in_the_registry_under_the_issuer_his_data_uses(refresh):
    """The registry key must match `instruments.issuer` exactly, spaces and all."""
    assert refresh.ISSUER_DOWNLOADERS["Global X"] is refresh.from_globalx


def test_a_global_x_page_that_links_to_nothing_is_not_an_empty_fund(refresh,
                                                                    monkeypatch):
    """A redesigned page must read as unusable, never as a fund holding nothing."""
    monkeypatch.setattr(refresh, "_download",
                        lambda url: b"<html><body>no link here</body></html>")
    assert refresh.from_globalx("BUG") is None


def test_a_global_x_file_that_is_half_a_fund_is_refused(refresh, monkeypatch):
    """An issuer file claims to be all of it; half of one is not storable."""
    def _fake(url: str) -> bytes:
        if url.endswith("/bug/"):
            return (b'"https://assets.globalxetfs.com/funds/holdings/x.csv"')
        return _globalx_bytes(rows=24)[:0] + (
            "Global X Cybersecurity ETF\nas of 10/02/2026\n"
            "% of Net Assets,Ticker,Name\n"
            "25.0,AAA,A INC\n25.0,BBB,B INC\n").encode()

    monkeypatch.setattr(refresh, "_download", _fake)
    assert refresh.from_globalx("BUG") is None


# ─── a fund listed on exchanges that are not American ───

@pytest.mark.parametrize("symbol", ["ARG CN", "FFM AU", "1515 JP", "AMAK AB"])
def test_a_foreign_listing_is_a_symbol(refresh, symbol):
    """COPX holds miners in Toronto, Sydney, Tokyo and Riyadh.

    Rejecting the exchange code made most of its weight look like it had been
    read out of a column of company names, and its own file was refused.
    """
    assert refresh._looks_like_symbol(symbol)


@pytest.mark.parametrize("name", [
    "BELLAVISTA RESOURCES LTD",     # three tokens
    "AMERIGO RESOURCES",            # the tail is nine letters, not two
    "OTHER PAYABLE & RECEIVABLES",
])
def test_a_company_name_is_still_not_a_symbol(refresh, name):
    """The guard has to keep catching a column read out of the wrong place."""
    assert not refresh._looks_like_symbol(name)


def test_a_whole_column_of_names_is_still_refused(refresh):
    """The case the guard exists for, re-proven after widening the shape."""
    pairs = [(f"COMPANY {i} INCORPORATED LIMITED", "", 1 / 12) for i in range(12)]
    result = refresh.assemble("XXX", pairs, "test", "2026-10-02", "published",
                              exact=True)
    assert refresh.why_not_exact(result) == (
        "most of the weight sits in rows that are not symbols")


# ─── what issuers actually write in a weight column ───

def test_a_percent_sign_is_not_a_missing_weight(refresh):
    """`3.32%` read as not-a-number zeroed every weight in the file.

    Roundhill, Fundstrat and Invesco all write it this way. The fund then
    returned nothing at all, which reads as "the file was unusable" rather
    than "every holding in it weighs zero" — three of his funds refused their
    own issuer's file on this alone.
    """
    assert refresh.weights_of(["3.32%", "16.95%", "0.04%"]) == [3.32, 16.95, 0.04]


def test_a_thousands_comma_is_not_a_missing_weight(refresh):
    assert refresh.weights_of(["1,234.5", "2.0"]) == [1234.5, 2.0]


def test_a_blank_weight_is_zero_not_a_crash(refresh):
    assert refresh.weights_of(["", "--", "1.5"]) == [0.0, 0.0, 1.5]


def test_invescos_spelling_of_the_weight_column_is_known(refresh):
    """Invesco heads it `% TNA`; unknown, the file is not a holdings file."""
    frame = pd.DataFrame({"Ticker": ["AAA", "BBB", "CCC"],
                          "Company": ["A", "B", "C"],
                          "% TNA": ["50.0%", "30.0%", "20.0%"]})
    pairs = refresh.parse_sheet(frame)
    assert [p[0] for p in pairs] == ["AAA", "BBB", "CCC"]
    assert pairs[0][2] == pytest.approx(0.5)


# ─── where issuers put the date ───

def test_a_date_in_the_cell_after_the_label_is_found(refresh):
    """iShares writes `Fund Holdings as of | Oct 02, 2026` — two cells."""
    raw = pd.DataFrame([["iShares Russell 2000 ETF", ""],
                        ["Fund Holdings as of", "Oct 02, 2026"],
                        ["Ticker", "Weight (%)"]])
    assert refresh._as_of_near(raw, 2) == "2026-10-02"


def test_a_date_below_the_table_is_found(refresh):
    """Invesco writes `# as of 2026-10-02` after the holdings, not before."""
    raw = pd.DataFrame([["Ticker", "% TNA"],
                        ["AAA", "100.0%"],
                        ["# as of 2026-10-02", ""]])
    assert refresh._as_of_near(raw, 0) == "2026-10-02"


def test_a_label_above_still_wins_over_anything_below(refresh):
    """The preamble is where a label belongs; below is the fallback."""
    raw = pd.DataFrame([["Holdings as of", "Oct 02, 2026"],
                        ["Ticker", "Weight"],
                        ["as of 1999-01-01", ""]])
    assert refresh._as_of_near(raw, 1) == "2026-10-02"


def test_a_file_with_no_date_still_invents_none(refresh):
    raw = pd.DataFrame([["Some Fund", ""], ["Ticker", "Weight"]])
    assert refresh._as_of_near(raw, 1) == ""


# ─── a fund that holds companies through swaps ───

def _dram_rows():
    """DRAM's shape: Micron held three ways, CXMT only through a contract."""
    return pd.DataFrame({
        "Ticker": ["595112103 TRS 050427 NM", "595112103 TRS 052427 GS", "MU",
                   "BTMTQT8 TRS 052427 GS", "SNDK"],
        "Name": ["MICRON TECHNOLOGY INC SWAP NM",
                 "MICRON TECHNOLOGY, INC.-SWAP-GOLD-L", "Micron Technology Inc",
                 "CXMT CORPORATION-SWAP-GOLD-L", "Sandisk Corp"],
        "Identifier": ["595112103 TRS 050427 NM", "595112103 TRS 052427 GS",
                       "595112103", "BTMTQT8 TRS 052427 GS", "80004C200"],
        "Weight": ["15.58%", "9.88%", "0.64%", "5.01%", "4.56%"],
    })


def test_swap_legs_become_exposure_to_the_company_they_are_written_on(refresh):
    """Micron arrives as three rows and is one holding of 26.10%.

    Stored as three, the real exposure reads as a third of itself and two
    rows carry symbols that resolve to no company at all.
    """
    pairs = refresh.parse_sheet(_dram_rows())
    result = refresh.assemble("DRAM", pairs, "test", "2026-10-04", "published",
                              exact=False)
    by_symbol = {r["holding"]: r["weight"] for r in result["rows"]}
    assert by_symbol["MU"] == pytest.approx(0.2610)


def test_a_folded_row_carries_the_companys_name_not_the_contracts(refresh):
    """The largest leg would otherwise name it `MICRON ... SWAP-GOLD-L`."""
    pairs = refresh.parse_sheet(_dram_rows())
    result = refresh.assemble("DRAM", pairs, "test", "2026-10-04", "published",
                              exact=False)
    names = {r["holding"]: r["holding_name"] for r in result["rows"]}
    assert names["MU"] == "Micron Technology Inc"


def test_a_company_held_only_through_swaps_is_keyed_on_its_underlying(refresh):
    """DRAM holds CXMT through contracts and nowhere else — 5% of the fund.

    Keyed on the contract string, two legs with different banks or
    maturities keep two different symbols and assemble() has nothing to sum.
    Roundhill already splits MAGS across three counterparties, so this is the
    shape to expect, not a hypothetical.
    """
    pairs = refresh.parse_sheet(_dram_rows())
    result = refresh.assemble("DRAM", pairs, "test", "2026-10-04", "published",
                              exact=False)
    weights = {r["holding"]: r["weight"] for r in result["rows"]}
    assert weights["BTMTQT8"] == pytest.approx(0.0501)


def test_two_banks_legs_on_one_unheld_company_are_summed(refresh):
    """The same company through two counterparties is one holding."""
    frame = pd.DataFrame({
        "Ticker": ["BTMTQT8 TRS 052427 GS", "BTMTQT8 TRS 050427 NM", "SNDK"],
        "Name": ["CXMT CORPORATION-SWAP-GOLD-L", "CXMT CORPORATION-SWAP-NM",
                 "Sandisk Corp"],
        "Identifier": ["BTMTQT8 TRS 052427 GS", "BTMTQT8 TRS 050427 NM",
                       "80004C200"],
        "Weight": ["5.01%", "4.00%", "90.99%"]})
    result = refresh.assemble("DRAM", refresh.parse_sheet(frame), "t",
                              "2026-10-04", "published", exact=False)
    weights = {r["holding"]: r["weight"] for r in result["rows"]}
    assert weights["BTMTQT8"] == pytest.approx(0.0901)


def test_a_swap_leg_with_no_identifier_still_folds(refresh):
    """An empty cell is NaN, and `str(NaN)` is the truthy "nan".

    A swap leg is the row most likely to carry no identifier of its own — it
    has no CUSIP. Unguarded, Micron stored at 0.64% of DRAM instead of
    26.10%, a fortyfold understatement of the fund's largest position, with
    coverage still reading 100%.
    """
    frame = pd.DataFrame({
        "Ticker": ["595112103 TRS 050427 NM", "595112103 TRS 052427 GS", "MU"],
        "Name": ["MICRON SWAP NM", "MICRON SWAP GS", "Micron Technology Inc"],
        "Identifier": [float("nan"), float("nan"), "595112103"],
        "Weight": ["15.58%", "9.88%", "0.64%"]})
    result = refresh.assemble("DRAM", refresh.parse_sheet(frame), "t",
                              "2026-10-04", "published", exact=False)
    weights = {r["holding"]: r["weight"] for r in result["rows"]}
    assert weights["MU"] == pytest.approx(0.2610)


def test_a_file_with_no_identifier_column_is_left_alone(refresh):
    """Most issuers publish no identifier; folding must not need one."""
    frame = pd.DataFrame({"Ticker": ["AAA", "BBB"], "Name": ["A", "B"],
                          "Weight": ["60%", "40%"]})
    assert [p[0] for p in refresh.parse_sheet(frame)] == ["AAA", "BBB"]


def test_the_bills_that_collateralise_a_swap_fund_are_not_companies(refresh):
    """DRAM parks 22% of itself in Treasury bills to fund its contracts."""
    assert refresh._is_cash_vehicle("912797VP9",
                                    "United States Treasury Bill 11/03/2026")
    assert not refresh._is_cash_vehicle("TRY", "Treasury Wine Estates Ltd")


# ─── two more issuer spellings, and a date in a column ───

def test_wisdomtrees_spelling_of_the_ticker_column_is_known(refresh):
    """WisdomTree heads it `Security Ticker`; unknown, EPI is not a fund."""
    frame = pd.DataFrame({"Group": ["Energy", "Financials", "Materials"],
                          "Name": ["A", "B", "C"],
                          "Security Ticker": ["RIL IB", "ICICIBC IB", "TATA IB"],
                          "Weight": ["50.0%", "30.0%", "20.0%"]})
    assert [p[0] for p in refresh.parse_sheet(frame)] == [
        "RIL IB", "ICICIBC IB", "TATA IB"]


def test_amplifys_spelling_of_the_weight_column_is_known(refresh):
    """Amplify heads it `Market Value (%)`."""
    frame = pd.DataFrame({"Holding Ticker": ["HL", "AG", "CDE"],
                          "Holding Name": ["A", "B", "C"],
                          "Market Value (%)": ["50.0%", "30.0%", "20.0%"]})
    pairs = refresh.parse_sheet(frame)
    assert pairs[0][2] == pytest.approx(0.5)


def test_a_date_in_a_column_of_its_own_is_the_issuers(refresh):
    """Amplify repeats `As of Date` down a column — no preamble to read.

    Without this the fund is stamped with the day we looked, while the file
    in front of us says which day it describes.
    """
    raw = pd.DataFrame([["As of Date", "Holding Ticker", "Market Value (%)"],
                        ["2026-10-06", "HL", "10.49%"],
                        ["2026-10-06", "AG", "10.15%"]])
    assert refresh._as_of_near(raw, 0) == "2026-10-06"


def test_a_column_of_dates_with_no_as_of_label_is_not_a_date(refresh):
    """An accrual or inception column must not be read as the fund's as-of."""
    raw = pd.DataFrame([["Accrual Date", "Ticker", "Weight"],
                        ["1970-01-01", "HL", "100%"]])
    assert refresh._as_of_near(raw, 0) == ""


def test_a_spreadsheet_he_drops_in_takes_the_same_path_as_a_csv(refresh,
                                                                tmp_path):
    """His instruction, 2026-10-06: convert xlsx, keep one format.

    The point is not tidiness. Converting at the door means every file is
    parsed by the one routine whose preamble, percent-sign and as-of
    handling are under test — not a second, quieter one.
    """
    content = _globalx_bytes()
    csv_path = tmp_path / "BUG_holdings.csv"
    csv_path.write_bytes(content)

    xlsx_path = tmp_path / "sheet.xlsx"
    pd.read_csv(io.BytesIO(content), header=None,
                names=range(refresh._widest(content.decode())),
                engine="python").to_excel(xlsx_path, index=False, header=False)

    from_csv = refresh.parse_csv(refresh.as_csv(csv_path))
    from_sheet = refresh.parse_csv(refresh.as_csv(xlsx_path))

    assert [(s, round(w, 6)) for s, _, w in from_sheet[0]] == \
           [(s, round(w, 6)) for s, _, w in from_csv[0]]
    assert from_sheet[1] == from_csv[1] == "2026-10-02"


# ─── a file that came from an aggregator, not the issuer ───

def _etfdb_bytes(as_of: str = "2026-10-02") -> bytes:
    """etfdb's shape: a labelled preamble, then Holding/Symbol/Weighting."""
    return (f"QQQ: Invesco QQQ Trust Series I\n"
            f"Inception Date: 1999-03-10\n"
            f"Fund Holdings as of: {as_of}\n"
            f"ETF Home Page: https://etfdb.com/etf/QQQ\n\n\n"
            "Holding,Symbol,Weighting\n"
            "NVIDIA Corporation,NVDA,50.0%\n"
            "Apple Inc.,AAPL,30.0%\n"
            "Microsoft Corporation,MSFT,19.5%\n"
            "U.S. Dollar,Other,0.5%\n").encode()


def test_the_site_a_file_came_from_is_recorded(refresh):
    """QQQ and VTI arrived from etfdb, not from Invesco and Vanguard."""
    assert refresh.published_by(_etfdb_bytes()) == "etfdb.com"


def test_an_issuers_own_file_names_no_publisher(refresh):
    """Global X's file carries no address; the source line stays clean."""
    assert refresh.published_by(_globalx_bytes()) == ""


def test_a_url_in_the_table_is_not_mistaken_for_the_publisher(refresh):
    """A holding's own website is not who published the file."""
    content = ("Some Fund\nas of 2026-10-02\nTicker,Name,Weight\n"
               "AAA,https://aaa-corp.com,100%\n").encode()
    assert refresh.published_by(content) == ""


def test_the_aggregators_placeholder_is_not_a_company(refresh):
    """`Other` is US dollars in QQQ and futures in VTI — and MERGES.

    Several such rows share the symbol, so assemble() sums them into one
    invented company rather than leaving them separately ignorable.
    """
    pairs, as_of = refresh.parse_csv(_etfdb_bytes())
    result = refresh.assemble("QQQ", pairs, "test", *refresh.dated(as_of),
                              exact=True)
    assert [r["holding"] for r in result["rows"]] == ["NVDA", "AAPL", "MSFT"]
    # The half-percent it carried still counts as covered — it is really in
    # the fund, it is just not a company.
    assert result["coverage"] == pytest.approx(1.0)
    assert result["stored"] == pytest.approx(0.995)


# ─── two more things that are not companies ───

def test_the_company_column_is_found_when_it_is_headed_holding(refresh):
    """etfdb heads it `Holding` and the ticker `Symbol`.

    Unmatched, QQQ and VTI stored 390 rows naming no company at all — every
    figure still right, every row unreadable by a person.
    """
    frame = pd.DataFrame({"Holding": ["NVIDIA Corporation", "Apple Inc."],
                          "Symbol": ["NVDA", "AAPL"],
                          "Weighting": ["60%", "40%"]})
    pairs = refresh.parse_sheet(frame)
    assert [(s, n) for s, n, _ in pairs] == [
        ("NVDA", "NVIDIA Corporation"), ("AAPL", "Apple Inc.")]


@pytest.mark.parametrize("symbol,name", [
    ("KRW", "KRW CASH"), ("TWD", "TWD CASH"), ("EUR", "EUR CASH"),
    ("CAD", "CAD CASH"), ("SAR", "SAR CASH(COMMITTED)"),
])
def test_a_currency_balance_is_not_a_company(refresh, symbol, name):
    """A three-letter code looks exactly like a ticker, so it slipped past."""
    assert refresh._is_cash_vehicle(symbol, name)


@pytest.mark.parametrize("symbol,name", [
    ("CSH", "Cash America International"),
    ("GPN", "Global Payments Cash Solutions"),
    ("CASY", "Casey's General Stores"),
])
def test_a_company_whose_name_contains_cash_survives(refresh, symbol, name):
    """The anchor is what keeps a real company out of the cash rule."""
    assert not refresh._is_cash_vehicle(symbol, name)


@pytest.mark.parametrize("symbol,name", [
    ("CAD", "CANADIAN DOLLAR"), ("EUR", "EURO"), ("GBP", "BRITISH POUNDS"),
])
def test_a_currency_written_out_in_full_is_not_a_company(refresh, symbol, name):
    """Amplify spells the currency rather than tagging it CASH."""
    assert refresh._is_cash_vehicle(symbol, name)


@pytest.mark.parametrize("symbol,name", [
    ("ERF", "Eurofins Scientific"),          # starts with EURO
    ("EEFT", "Euronet Worldwide, Inc."),     # starts with EURO
    ("COP", "ConocoPhillips"),               # symbol is a currency code
    ("CNH", "CNH Industrial N.V."),          # symbol is a currency code
])
def test_a_real_company_is_not_dropped_as_a_currency(refresh, symbol, name):
    """Whole-name matching is what makes the currency list safe.

    COP and CNH are a real position and a real holding whose tickers happen
    to be ISO currency codes; a symbol-based rule would delete both.
    """
    assert not refresh._is_cash_vehicle(symbol, name)


# ─── a file whose data rows are wider than its header ───

def _wider_than_header() -> bytes:
    """A header naming three columns above rows carrying four.

    Real shape: an issuer adds a trailing column and forgets its label. The
    second read sizes itself from the header, promotes the first field to the
    index, and slides every label one column right of the values it names.
    """
    return ("EURO STOXX FUND\n"
            'Fund Holdings as of,"Oct 02, 2026"\n'
            "Ticker,Name,Weight (%)\n"
            "DE0007236101,SIEMENS AG,12.00,0.1200\n"
            "DE0008404005,ALLIANZ SE,11.00,0.1100\n"
            "NL0000009165,HEINEKEN NV,11.00,0.1100\n"
            "FR0000120578,SANOFI SA,11.00,0.1100\n"
            "DE000BAY0017,BAYER AG,11.00,0.1100\n"
            "DE000A1EWWW0,ADIDAS AG,11.00,0.1100\n"
            "NL0000009538,PHILIPS NV,11.00,0.1100\n"
            "DE0007664039,VOLKSWAGEN AG,11.00,0.1100\n"
            "NL0000303709,AEGON NV,11.00,0.1100\n").encode()


def test_a_file_wider_than_its_header_does_not_shift_columns(refresh):
    """The silent corruption: company NAMES stored in the ticker column.

    Shifted, it parses, sums to 100%, passes every exactness check, and
    asserts it accounts for the whole fund — while every company in it
    resolves to nothing. On the previous code this file raised and the fund
    fell safely to Yahoo's floor; `_widest` removed the error without fixing
    the read that builds the frame.
    """
    pairs, _ = refresh.parse_csv(_wider_than_header())
    symbols = [s for s, _, _ in pairs]
    assert "SIEMENS AG" not in symbols
    assert symbols[0] == "DE0007236101"


def test_a_column_of_european_company_names_is_still_refused(refresh):
    """The guard that was supposed to catch the shift, and did not.

    `SIEMENS AG` and `ALLIANZ SE` passed a bare two-letter-tail rule, so a
    column of names cleared the "most of the weight is not symbols" check.
    """
    pairs = [(n, "", 1 / 9) for n in
             ["SIEMENS AG", "ALLIANZ SE", "HEINEKEN NV", "SANOFI SA",
              "BAYER AG", "ADIDAS AG", "PHILIPS NV", "AEGON NV", "TOTAL SA"]]
    result = refresh.assemble("XXX", pairs, "t", "2026-10-02", "published",
                              exact=True)
    assert refresh.why_not_exact(result) == (
        "most of the weight sits in rows that are not symbols")


@pytest.mark.parametrize("symbol", [
    "ARG CN", "FFM AU", "1515 JP", "AMAK AB", "APOLLOHOSP IB", "TECK/B CN"])
def test_a_real_foreign_listing_is_still_a_symbol(refresh, symbol):
    """Every one of these is in a file he actually downloaded."""
    assert refresh._looks_like_symbol(symbol)


@pytest.mark.parametrize("name", [
    "SIEMENS AG", "ALLIANZ SE", "HEINEKEN NV", "SANOFI SA", "ADIDAS AG"])
def test_a_company_name_with_a_legal_suffix_is_not_a_symbol(refresh, name):
    """`AG`, `SE`, `NV`, `SA` are corporate forms, not exchanges."""
    assert not refresh._looks_like_symbol(name)


# ─── which of a file's several dates is the fund's ───

def test_an_accrual_date_in_a_holding_row_is_not_the_funds_date(refresh):
    """A bond's accrued-interest date sorts for ever as the freshest row.

    iShares files carry an `Accrual Date` column reaching back to 1994, and
    a maturity can be in the future — stored as `published`, that is the
    issuer's word for a date it never gave.
    """
    raw = pd.DataFrame([
        ["Big Fund", "", ""],
        ["Fund Holdings as of", "Oct 02, 2026", ""],
        ["Ticker", "Name", "Weight"],
        ["TBILL", "US TREASURY BILL interest accrued as of", "01-Feb-2027"]])
    assert refresh._as_of_near(raw, 2) == "2026-10-02"


def test_an_inception_date_never_outranks_the_holdings_date(refresh):
    """QQQ's preamble states both; one word apart, the wrong one won."""
    raw = pd.DataFrame([
        ["Fund inception as of: 1999-03-10", ""],
        ["Fund Holdings as of: 2026-10-02", ""],
        ["Ticker", "Weight"]])
    assert refresh._as_of_near(raw, 2) == "2026-10-02"


def test_a_price_as_of_column_never_outranks_the_funds_own_label(refresh):
    """Any column headed "... as of ..." used to win, unconditionally."""
    raw = pd.DataFrame([
        ["Big Fund", "", "", ""],
        ["Fund Holdings as of", "Oct 02, 2026", "", ""],
        ["Ticker", "Name", "Weight", "Price as of Date"],
        ["AAPL", "Apple", "30.0%", "2026-09-30"]])
    assert refresh._as_of_near(raw, 2) == "2026-10-02"


def test_a_footer_date_is_still_found(refresh):
    """Invesco puts it below the table; that must keep working."""
    raw = pd.DataFrame([["Ticker", "% TNA"], ["AAA", "100.0%"],
                        ["# as of 2026-10-02", ""]])
    assert refresh._as_of_near(raw, 0) == "2026-10-02"


def test_a_spreadsheet_date_cell_survives_conversion(refresh):
    """`read_excel` -> `to_csv` renders a date as `2026-10-02 00:00:00`.

    Unparsed, converting an xlsx threw away the issuer's published date on
    exactly the path added to make spreadsheets and CSVs behave the same.
    """
    raw = pd.DataFrame([["Fund Holdings as of", "2026-10-02 00:00:00"],
                        ["Ticker", "Weight"]])
    assert refresh._as_of_near(raw, 1) == "2026-10-02"


def test_the_footer_search_does_not_walk_the_whole_table(refresh):
    """Scanning every holding bottom-up reads dates that belong to bonds.

    With no date in the preamble, a long table whose middle rows mention a
    date would otherwise supply one — and it would be stored as `published`.
    """
    rows = [["Some Fund", "", ""], ["Ticker", "Name", "Weight"]]
    rows += [[f"T{i:02d}", f"COMPANY {i}", "1.0%"] for i in range(20)]
    rows[8] = ["TBILL", "US TREASURY BILL as of", "01-Feb-2027"]
    assert refresh._as_of_near(pd.DataFrame(rows), 1) == ""


def test_a_spreadsheet_date_with_a_real_clock_time_still_parses(refresh):
    raw = pd.DataFrame([["Fund Holdings as of", "2026-10-02 14:30:00"],
                        ["Ticker", "Weight"]])
    assert refresh._as_of_near(raw, 1) == "2026-10-02"


@pytest.mark.parametrize("raw,expected", [
    ("(1.50%)", -1.5), ("(0.89%)", -0.89), ("(1,234.5)", -1234.5),
    ("-1.50%", -1.5), ("1.50%", 1.5),
])
def test_a_weight_in_brackets_is_a_negative_not_a_zero(refresh, raw, expected):
    """`(0.89%)` is how an accountant writes minus 0.89.

    Read as zero, a fund's negative cash line disappears and everything it
    offsets is left overstating how much of the fund was accounted for — and
    a zero is indistinguishable from a row that was never there.
    """
    assert refresh.weights_of([raw]) == [expected]


def test_a_quoted_comma_in_a_header_does_not_leak_the_table(refresh):
    """Raised in review; does not reproduce — the quote is stripped first.

    `Ticker,"Weight, %"` splits into `"Weight` and ` %"`, and stripping the
    quote recovers `weight`, so the table start is still found and the
    publisher scan stops above it.
    """
    content = (b'Fund\nTicker,"Weight, %"\nAAA,100%\n'
               b'https://aaa-corp.com,0%\n')
    assert refresh.published_by(content) == ""


# ─── a refresh must never make the record worse ───

def test_a_floor_cannot_replace_a_better_answer_already_on_file(refresh,
                                                                tmp_path,
                                                                monkeypatch):
    """His inbox is git-ignored: those files exist only on his laptop.

    Clear them, run a full refresh, and every fund would fall to Yahoo's ten
    names — IWM from 714 companies back to 10 — silently, because `write()`
    replaces a fund's rows wholesale and a floor always "succeeds". The old
    guard compared against `etf_compositions.top_holding`, which is blank
    for every fund but MAGS, so it read zero and stopped nothing.
    """
    stored = tmp_path / "etf_holdings.csv"
    rows = "".join(
        f'"IWM","T{i:03d}","Company {i}",0.0014,0.999,"issuer file",'
        f'"2026-10-02","published"\n' for i in range(700))
    stored.write_text(
        '"ticker","holding","holding_name","weight","coverage","source",'
        '"as_of","as_of_kind"\n' + rows)
    monkeypatch.setattr(market, "HOLDINGS_PATH", stored)

    # 10 names at 0.32% each — IWM's real Yahoo floor, and every weight
    # comfortably above MIN_HOLDING_WEIGHT so assemble() keeps them. (Set
    # below the threshold, the rows are dropped as dust and the fund returns
    # None for a reason that has nothing to do with this guard — which is
    # how the first version of this test passed without exercising it.)
    thin = refresh.assemble("IWM", [(f"T{i:03d}", "", 0.0032)
                                    for i in range(10)],
                            "Yahoo top holdings", "2026-10-06", "read",
                            exact=False)
    assert thin is not None and thin["coverage"] == pytest.approx(0.032)
    monkeypatch.setattr(refresh, "from_yahoo", lambda t: thin)

    assert refresh.fetch_holdings("IWM", issuer="iShares") is None


def test_a_better_floor_than_what_is_stored_is_still_accepted(refresh,
                                                              tmp_path,
                                                              monkeypatch):
    """The guard blocks a step BACKWARDS, never an improvement."""
    stored = tmp_path / "etf_holdings.csv"
    stored.write_text(
        '"ticker","holding","holding_name","weight","coverage","source",'
        '"as_of","as_of_kind"\n'
        '"IWM","AAA","A Corp",0.03,0.032,"Yahoo","2026-10-04","read"\n')
    monkeypatch.setattr(market, "HOLDINGS_PATH", stored)

    better = refresh.assemble("IWM", [(f"T{i:03d}", "", 0.09) for i in range(10)],
                              "Yahoo top holdings", "2026-10-06", "read",
                              exact=False)
    monkeypatch.setattr(refresh, "from_yahoo", lambda t: better)

    assert refresh.fetch_holdings("IWM", issuer="iShares") is not None


def test_the_first_refresh_of_a_fund_is_never_blocked(refresh, monkeypatch):
    """Nothing on file means nothing to make worse."""
    thin = refresh.assemble("NEWF", [("AAA", "", 0.1), ("BBB", "", 0.1)],
                            "Yahoo top holdings", "2026-10-06", "read",
                            exact=False)
    monkeypatch.setattr(refresh, "from_yahoo", lambda t: thin)
    assert refresh.fetch_holdings("NEWF", issuer="Schwab") is not None
