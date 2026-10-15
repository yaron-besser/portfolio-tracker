"""
Tests for the IBKR -> CSV sync logic.

Covers ticker matching, position refresh-in-place (preserves the per-lot
note), append-only dedup, and the rule that broker tables only ever gain rows.

Run from project root:
    pytest tests/test_ibkr_sync.py
"""

from __future__ import annotations

import functools
from pathlib import Path

import pandas as pd
import pytest

from src.ibkr_import import (
    extract_cash_positions,
    extract_open_positions,
    extract_trades,
    iter_statements,
    parse_xml,
)
from src import ibkr_sync
from src.ibkr_sync import (
    POSITIONS_COLS,
    _as_of,
    _derive_new_ticker,
    _match_ticker_to_ibkr,
    check_broker_table,
    sync_cash_transactions,
    sync_nav_history,
    sync_positions,
    sync_transactions,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ibkr_flex_sample.xml"


# ─── Test data ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ibkr_data():
    """Parse the fixture once per module; return (securities, cash, trades)."""
    root = parse_xml(FIXTURE_PATH.read_bytes())
    stmt = next(iter_statements(root))
    return (
        extract_open_positions(stmt),
        extract_cash_positions(stmt),
        extract_trades(stmt),
    )


def _existing_positions() -> pd.DataFrame:
    """
    A hand-built positions.csv-shaped DataFrame matching the fixture.

    Uses bare AAPL (unique), AAPL_ALT (intentionally missing in IBKR — should
    be reported as removed), explicit ASML_EUR/ASML_USD, and USD_CASH +
    EUR_CASH. The notes column is non-empty so we can
    assert it survives a sync.
    """
    return pd.DataFrame(
        [
            {
                "ticker": "USD_CASH", "shares": 1,
                "cost_basis": 900, "last_price": 900, "market_value": 900, "notes": "",
            },
            {
                "ticker": "EUR_CASH", "shares": 1,
                "cost_basis": 100, "last_price": 100, "market_value": 100,
                "notes": "FX-translated",
            },
            {
                "ticker": "AAPL", "shares": 5,
                "cost_basis": 140, "last_price": 145, "market_value": 725,
                "notes": "core holding",
            },
            {
                "ticker": "ASML_EUR", "shares": 1,
                "cost_basis": 650, "last_price": 680, "market_value": 748, "notes": "EUR lot",
            },
            {
                "ticker": "ASML_USD", "shares": 2,
                "cost_basis": 800, "last_price": 820, "market_value": 1640, "notes": "ADR lot",
            },
            {
                "ticker": "AAPL_ALT", "shares": 1,
                "cost_basis": 100, "last_price": 100, "market_value": 100,
                "notes": "stale — sold last quarter",
            },
        ]
    )


# ─── _match_ticker_to_ibkr ───────────────────────────────────────────────


def test_match_cash_ticker(ibkr_data) -> None:
    """USD_CASH must resolve to the USD FxPosition row."""
    securities, cash, _ = ibkr_data
    kind, row = _match_ticker_to_ibkr("USD_CASH", securities, cash)
    assert kind == "cash"
    assert row["currency"] == "USD"
    assert row["value_in_base"] == 1000


def test_match_cash_missing_currency(ibkr_data) -> None:
    """A cash ticker for a currency the user no longer holds is 'missing'."""
    securities, cash, _ = ibkr_data
    kind, row = _match_ticker_to_ibkr("DKK_CASH", securities, cash)
    assert kind == "missing"
    assert row is None


def test_match_sym_ccy_exact(ibkr_data) -> None:
    """ASML_EUR must match the EUR-currency ASML row, not the USD one."""
    securities, cash, _ = ibkr_data
    kind, row = _match_ticker_to_ibkr("ASML_EUR", securities, cash)
    assert kind == "security"
    assert row["symbol"] == "ASML"
    assert row["currency"] == "EUR"


def test_match_bare_symbol_unique(ibkr_data) -> None:
    """Bare AAPL matches the single AAPL row."""
    securities, cash, _ = ibkr_data
    kind, row = _match_ticker_to_ibkr("AAPL", securities, cash)
    assert kind == "security"
    assert row["symbol"] == "AAPL"


def test_match_bare_symbol_ambiguous(ibkr_data) -> None:
    """Bare 'ASML' (no suffix) is ambiguous because two IBKR rows have it."""
    securities, cash, _ = ibkr_data
    kind, row = _match_ticker_to_ibkr("ASML", securities, cash)
    assert kind == "ambiguous"
    assert row is None


def test_match_missing_symbol(ibkr_data) -> None:
    """A ticker for a symbol not in IBKR returns 'missing'."""
    securities, cash, _ = ibkr_data
    kind, row = _match_ticker_to_ibkr("XYZ", securities, cash)
    assert kind == "missing"
    assert row is None


# ─── _derive_new_ticker ──────────────────────────────────────────────────


def test_derive_new_ticker_unique_symbol(ibkr_data) -> None:
    """Symbol with only one IBKR row gets a bare ticker."""
    securities, _, _ = ibkr_data
    aapl = securities[securities["symbol"] == "AAPL"].iloc[0]
    assert _derive_new_ticker(aapl, securities) == "AAPL"


def test_derive_new_ticker_multi_currency_suffixes(ibkr_data) -> None:
    """When the same symbol appears twice, ticker includes currency."""
    securities, _, _ = ibkr_data
    asml_eur = securities[
        (securities["symbol"] == "ASML") & (securities["currency"] == "EUR")
    ].iloc[0]
    assert _derive_new_ticker(asml_eur, securities) == "ASML_EUR"


# ─── sync_positions ──────────────────────────────────────────────────────


def test_sync_positions_refreshes_price_columns(ibkr_data) -> None:
    """IBKR data updates shares/cost_basis/last_price/market_value."""
    securities, cash, _ = ibkr_data
    existing = _existing_positions()
    new_df, _ = sync_positions(existing, securities, cash)
    aapl_new = new_df[new_df["ticker"] == "AAPL"].iloc[0]
    # IBKR fixture has AAPL at 10 shares, mark 200, cost basis 150
    assert aapl_new["shares"] == 10
    assert aapl_new["last_price"] == 200
    assert aapl_new["cost_basis"] == 150
    assert aapl_new["market_value"] == 2000  # 10 * 200 * 1


def test_sync_positions_refreshes_cost_basis_money_and_fx_rate_to_base(ibkr_data) -> None:
    """The two columns `book.lot_cost_usd()` needs and the old sync
    dropped: IBKR's own TOTAL cost (native currency, commission and splits
    already in) and the rate IBKR used for this position today. AAPL is
    USD (rate 1); ASML_EUR is the one real reason this column exists."""
    securities, cash, _ = ibkr_data
    existing = _existing_positions()
    new_df, _ = sync_positions(existing, securities, cash)
    aapl_new = new_df[new_df["ticker"] == "AAPL"].iloc[0]
    assert aapl_new["cost_basis_money"] == 1500
    assert aapl_new["fx_rate_to_base"] == 1
    asml_eur = new_df[new_df["ticker"] == "ASML_EUR"].iloc[0]
    assert asml_eur["cost_basis_money"] == 650
    assert asml_eur["fx_rate_to_base"] == 1.1


def test_sync_cash_row_invents_no_cost_basis(ibkr_data) -> None:
    """🚨 M6 (review, 2026-10-08): a cash balance has no acquisition cost.
    This used to write `cost_basis_money = market_value`, asserting a
    confident zero profit where IBKR's own `unrealizedPL` on the FX
    position can be, and for his real JPY balance is, genuinely non-zero.
    Blank, not a number nobody computed; `fx_rate_to_base` stays 1 — that
    one IS true, since this row's own `currency` is already forced to
    USD above it."""
    securities, cash, _ = ibkr_data
    existing = _existing_positions()
    new_df, _ = sync_positions(existing, securities, cash)
    usd_cash = new_df[new_df["ticker"] == "USD_CASH"].iloc[0]
    assert usd_cash["cost_basis_money"] == ""
    assert usd_cash["fx_rate_to_base"] == 1.0


def test_sync_new_cash_row_invents_no_cost_basis() -> None:
    """🚨 Re-review (2026-10-08): `_new_cash_row()` got the identical M6
    fix as `_refresh_cash_row()` but nothing exercised the branch that
    builds it — the one that fires the first time he trades in a currency
    the file has never seen. Same assertion as the sibling test above,
    on the path that was untested."""
    securities = pd.DataFrame(columns=[
        "symbol", "description", "currency", "asset_category",
        "sub_category", "listing_exchange", "position", "mark_price",
        "cost_basis_price", "cost_basis_money", "position_value",
        "percent_of_nav", "fx_rate_to_base", "multiplier", "security_id",
        "report_date",
    ])
    cash = pd.DataFrame(
        [{"currency": "GBP", "quantity": 100.0, "close_price": 1.27,
          "value_in_base": 127.0, "cost_basis_in_base": -120.0,
          "unrealized_pl": 7.0, "report_date": "2025-12-31"}]
    )
    existing = pd.DataFrame(
        [{"ticker": "USD_CASH", "shares": 1, "cost_basis": 900,
          "last_price": 900, "market_value": 900, "notes": ""}]
    )

    new_df, diff = sync_positions(existing, securities, cash)

    assert "GBP_CASH" in diff["added"]
    row = new_df[new_df["ticker"] == "GBP_CASH"].iloc[0]
    assert row["cost_basis_money"] == ""
    assert row["fx_rate_to_base"] == 1.0


def test_sync_positions_preserves_the_per_lot_note(ibkr_data) -> None:
    """`notes` is the user's own text about this lot and a sync must not touch it."""
    securities, cash, _ = ibkr_data
    existing = _existing_positions()
    new_df, _ = sync_positions(existing, securities, cash)
    for ticker in ("AAPL", "ASML_EUR", "ASML_USD"):
        old = existing[existing["ticker"] == ticker].iloc[0]["notes"]
        new = new_df[new_df["ticker"] == ticker].iloc[0]["notes"]
        assert new == old, f"{ticker}: notes was modified"


def test_sync_positions_does_not_resurrect_instrument_columns(ibkr_data) -> None:
    """
    name/kind/direct_sector/cluster belong to instruments.csv now.

    They were dropped because holding them in both places had already produced
    disagreements. If a sync ever writes them back, positions.csv starts
    drifting from the reference table again — so the absence is asserted, not
    assumed.
    """
    securities, cash, _ = ibkr_data
    new_df, _ = sync_positions(_existing_positions(), securities, cash)
    for col in ("name", "kind", "direct_sector", "cluster"):
        assert col not in new_df.columns, f"{col} came back into positions.csv"
    assert list(new_df.columns) == list(POSITIONS_COLS)


def test_sync_positions_reports_removed_tickers(ibkr_data) -> None:
    """Existing tickers without an IBKR row appear in diff['removed']."""
    securities, cash, _ = ibkr_data
    existing = _existing_positions()
    new_df, diff = sync_positions(existing, securities, cash)
    assert "AAPL_ALT" in diff["removed"]
    assert "AAPL_ALT" not in new_df["ticker"].values


def test_sync_positions_does_not_add_when_all_matched(ibkr_data) -> None:
    """If every IBKR row mapped to an existing ticker, diff['added'] is empty."""
    securities, cash, _ = ibkr_data
    existing = _existing_positions()
    _, diff = sync_positions(existing, securities, cash)
    assert diff["added"] == []


def test_sync_positions_translates_eur_market_value(ibkr_data) -> None:
    """ASML EUR position is held in EUR but market_value is USD-translated."""
    securities, cash, _ = ibkr_data
    existing = _existing_positions()
    new_df, _ = sync_positions(existing, securities, cash)
    asml_eur = new_df[new_df["ticker"] == "ASML_EUR"].iloc[0]
    # fixture: position=1, markPrice=700 EUR, fxRateToBase=1.1 -> 770 USD
    assert asml_eur["market_value"] == 770.0


def test_sync_positions_preserves_column_order(ibkr_data) -> None:
    """Stable column order avoids gratuitous CSV diffs."""
    securities, cash, _ = ibkr_data
    existing = _existing_positions()
    new_df, _ = sync_positions(existing, securities, cash)
    assert tuple(new_df.columns) == POSITIONS_COLS


def test_sync_positions_adds_unmatched_ibkr_rows() -> None:
    """When IBKR has a new symbol the user doesn't track, it's added."""
    # Manually build a minimal IBKR DataFrame with a new symbol
    securities = pd.DataFrame(
        [
            {
                "symbol": "NEWSTOCK", "description": "Brand New Inc",
                "currency": "USD", "asset_category": "STK",
                "sub_category": "COMMON", "listing_exchange": "NASDAQ",
                "position": 5.0, "mark_price": 50.0, "cost_basis_price": 40.0,
                "cost_basis_money": 200.0, "position_value": 250.0,
                "percent_of_nav": 5.0, "fx_rate_to_base": 1.0,
                "multiplier": 1.0, "security_id": "X", "report_date": "2025-12-31",
            },
        ]
    )
    cash = pd.DataFrame(
        [{"currency": "USD", "quantity": 0.0, "close_price": 1.0,
          "value_in_base": 0.0, "cost_basis_in_base": 0.0,
          "unrealized_pl": 0.0, "report_date": "2025-12-31"}]
    )
    existing = pd.DataFrame(
        [{"ticker": "USD_CASH", "instrument_id": "USD_CASH", "shares": 1,
          "cost_basis": 0, "last_price": 0, "market_value": 0, "notes": ""}]
    )
    new_df, diff = sync_positions(existing, securities, cash)
    assert "NEWSTOCK" in diff["added"]
    new_row = new_df[new_df["ticker"] == "NEWSTOCK"].iloc[0]
    assert "AUTO-ADDED" in new_row["notes"]
    # The lot lands even though nothing yet says what NEWSTOCK is — missing
    # metadata must never stop a holding being recorded. instruments.csv is
    # where the meaning gets filled in, and gaps() is what surfaces it.
    assert new_row["instrument_id"] == "NEWSTOCK"


def test_sync_positions_backfills_the_two_new_columns_for_an_untouched_row(
    ibkr_data,
) -> None:
    """A pre-migration positions.csv has neither column at all — and the
    "ambiguous, kept as-is" branch (`sync_positions`'s own comment: "keep
    as-is; user must fix") is the one real way a row can still be untouched
    after a sync that otherwise refreshes every matched row. That row must
    not make `new_df[list(POSITIONS_COLS)]` raise a KeyError, and it must
    read as "no cost on file" (blank), never a 0 or a 1 invented to fill
    the cell."""
    securities, cash, _ = ibkr_data
    existing = pd.DataFrame(
        [{"ticker": "ASML", "shares": 1, "cost_basis": 1, "last_price": 1,
          "market_value": 1, "notes": "pre-migration row"}]
    )
    new_df, diff = sync_positions(existing, securities, cash)
    assert "ASML" in diff["ambiguous"]
    row = new_df[new_df["ticker"] == "ASML"].iloc[0]
    assert row["cost_basis_money"] == "" or pd.isna(row["cost_basis_money"])
    assert row["fx_rate_to_base"] == "" or pd.isna(row["fx_rate_to_base"])
    assert tuple(new_df.columns) == POSITIONS_COLS


def test_sync_tolerates_a_raw_file_missing_cost_basis_money(ibkr_data) -> None:
    """An older Flex XML that never carried `costBasisMoney`/`fxRateToBase`
    at all still parses to NaN in those columns (`extract_open_positions()`
    already defaults a missing attribute rather than raising) — the sync
    itself must carry that NaN through rather than crash turning it into a
    row."""
    securities, cash, _ = ibkr_data
    securities = securities.copy()
    securities["cost_basis_money"] = float("nan")
    securities["fx_rate_to_base"] = float("nan")
    existing = _existing_positions()

    new_df, _ = sync_positions(existing, securities, cash)

    aapl_new = new_df[new_df["ticker"] == "AAPL"].iloc[0]
    # The per-row NaN survives the merge; the final blank-column fillna
    # (same shape `currency` already gets) turns it into the empty string
    # every other "no data" cell in this file already uses.
    assert aapl_new["cost_basis_money"] == "" or pd.isna(aapl_new["cost_basis_money"])
    # market_value still computes — `position_value * fx_rate_to_base` with
    # a NaN rate is itself NaN, which is the honest answer (the mark cannot
    # be translated to USD without a rate), never a silent 0 or the stale
    # mark from the OLD row.
    assert pd.isna(aapl_new["market_value"])


# ─── sync_transactions ───────────────────────────────────────────────────


def test_sync_transactions_first_run_writes_all(ibkr_data, tmp_path) -> None:
    """With no existing file, all new trades are added."""
    _, _, trades = ibkr_data
    target = tmp_path / "transactions.csv"
    merged, n_added = sync_transactions(trades, existing_path=target)
    assert n_added == len(trades)
    assert len(merged) == len(trades)


def test_sync_transactions_dedupes_on_rerun(ibkr_data, tmp_path) -> None:
    """Running twice with the same data adds zero rows the second time."""
    _, _, trades = ibkr_data
    target = tmp_path / "transactions.csv"
    merged, _ = sync_transactions(trades, existing_path=target)
    merged.to_csv(target, index=False)
    merged2, n_added = sync_transactions(trades, existing_path=target)
    assert n_added == 0
    assert len(merged2) == len(merged)


def test_sync_transactions_appends_new_rows(tmp_path) -> None:
    """A second pull with one new trade adds exactly one row."""
    cols = ["trade_id", "symbol", "trade_date", "quantity"]
    first = pd.DataFrame(
        [{"trade_id": "A", "symbol": "AAPL", "trade_date": "2025-01-01",
          "quantity": 1}]
    )
    target = tmp_path / "transactions.csv"
    first.to_csv(target, index=False)

    second = pd.DataFrame(
        [
            {"trade_id": "A", "symbol": "AAPL", "trade_date": "2025-01-01",
             "quantity": 1},
            {"trade_id": "B", "symbol": "MSFT", "trade_date": "2025-02-01",
             "quantity": 2},
        ]
    )
    merged, n_added = sync_transactions(second, existing_path=target)
    assert n_added == 1
    assert set(merged["trade_id"]) == {"A", "B"}


def test_sync_cash_transactions_does_not_rewrite_existing_ids(tmp_path) -> None:
    """
    A sync must not alter rows already on disk.

    An ID column with a blank used to be inferred as float, so re-writing
    the file turned `172878813` into `172878813.0` on every untouched row —
    285 of them before this was caught. Append-only means byte-identical.
    """
    target = tmp_path / "cash_transactions.csv"
    target.write_text(
        "transaction_id,date,amount,action_id\n"
        "T1,2025-01-01,7.25,172878813\n"
        "T2,2025-01-02,-1.81,\n"  # the blank that triggered float inference
    )
    before = target.read_text()

    new = pd.DataFrame(
        [{"transaction_id": "T3", "date": "2025-01-03", "amount": 1.0,
          "action_id": "173326078"}]
    )
    merged, n_added = sync_cash_transactions(new, existing_path=target)
    merged.to_csv(target, index=False)

    assert n_added == 1
    assert target.read_text().startswith(before)
    assert list(merged["action_id"]) == ["172878813", "", "173326078"]


# ─── NAV history: the broker's window must not delete the past ───

def _nav(dates, navs=None):
    """A statement-shaped NAV frame."""
    navs = navs or [100.0] * len(dates)
    return pd.DataFrame({"date": dates, "nav": navs})


NO_FLOWS = pd.DataFrame(columns=["type", "amount", "date"])


def test_nav_keeps_days_the_broker_no_longer_sends(tmp_path) -> None:
    """
    The bug this whole change exists for.

    IBKR's Flex query is a rolling 365-day window, so an old day stops being
    mentioned rather than being reported as gone. Overwriting the file with the
    statement read that silence as deletion and dropped 60 real days.
    """
    target = tmp_path / "nav_history.csv"
    pd.DataFrame(
        {"date": ["2025-05-15", "2025-06-10"], "nav": [0.0, 116810.227],
         "deposit": [0.0, 0.0], "withdrawal": [0.0, 0.0], "notes": ["", ""]}
    ).to_csv(target, index=False)

    # A later statement that no longer reaches back to those dates.
    merged, n = sync_nav_history(_nav(["2026-08-07"]), NO_FLOWS, existing_path=target)

    assert list(merged["date"]) == ["2025-05-15", "2025-06-10", "2026-08-07"]
    assert n == 3
    # The carried day keeps its real NAV, not a blank or a zero.
    assert str(merged.loc[merged["date"] == "2025-06-10", "nav"].iloc[0]) == "116810.227"


def test_nav_statement_wins_where_it_still_reports(tmp_path) -> None:
    """IBKR may restate a day it still covers; there it is authoritative."""
    target = tmp_path / "nav_history.csv"
    pd.DataFrame(
        {"date": ["2026-08-07"], "nav": [1.0], "deposit": [0.0],
         "withdrawal": [0.0], "notes": [""]}
    ).to_csv(target, index=False)

    merged, _ = sync_nav_history(_nav(["2026-08-07"], [999.0]), NO_FLOWS,
                                 existing_path=target)
    assert len(merged) == 1
    assert float(merged["nav"].iloc[0]) == 999.0


def test_nav_note_containing_a_hash_survives(tmp_path) -> None:
    """The '#' trap that once truncated claims.csv, in the NAV notes column."""
    target = tmp_path / "nav_history.csv"
    pd.DataFrame(
        {"date": ["2026-08-07"], "nav": [1.0], "deposit": [0.0],
         "withdrawal": [0.0], "notes": ["deposit #2 of the month"]}
    ).to_csv(target, index=False)

    merged, _ = sync_nav_history(_nav(["2026-08-07"]), NO_FLOWS, existing_path=target)
    assert merged["notes"].iloc[0] == "deposit #2 of the month"


# ─── check_broker_table: rows may be added, never changed or removed ───

def test_broker_table_allows_new_rows() -> None:
    old = pd.DataFrame({"trade_id": ["A"], "qty": ["1"]})
    new = pd.DataFrame({"trade_id": ["A", "B"], "qty": ["1", "2"]})
    check_broker_table(old, new, ["trade_id"], "t.csv")  # must not raise


def test_broker_table_blocks_a_changed_row() -> None:
    old = pd.DataFrame({"trade_id": ["A"], "qty": ["1"]})
    new = pd.DataFrame({"trade_id": ["A"], "qty": ["999"]})
    with pytest.raises(ValueError, match="qty changed"):
        check_broker_table(old, new, ["trade_id"], "t.csv")


def test_broker_table_blocks_a_vanished_row() -> None:
    """The NAV failure, generalised: a file may not lose rows."""
    old = pd.DataFrame({"date": ["2025-05-15", "2026-08-07"], "nav": ["0", "1"]})
    new = pd.DataFrame({"date": ["2026-08-07"], "nav": ["1"]})
    with pytest.raises(ValueError, match="missing"):
        check_broker_table(old, new, ["date"], "nav_history.csv")


def test_broker_table_blocks_a_duplicate_key() -> None:
    new = pd.DataFrame({"trade_id": ["A", "A"], "qty": ["1", "2"]})
    with pytest.raises(ValueError, match="unique"):
        check_broker_table(pd.DataFrame(), new, ["trade_id"], "t.csv")


def test_broker_table_tolerates_a_new_column() -> None:
    """Adding a column must stay possible, or the guard blocks its own future."""
    old = pd.DataFrame({"trade_id": ["A"], "qty": ["1"]})
    new = pd.DataFrame({"trade_id": ["A"], "qty": ["1"], "isin": ["US123"]})
    check_broker_table(old, new, ["trade_id"], "t.csv")  # must not raise


def test_broker_table_composite_key() -> None:
    """borrow_fees is keyed on two columns, not one."""
    old = pd.DataFrame({"date": ["2026-01-01"], "symbol": ["GIII"], "fee": ["1"]})
    new = pd.DataFrame({"date": ["2026-01-01"], "symbol": ["GIII"], "fee": ["2"]})
    with pytest.raises(ValueError, match="fee changed"):
        check_broker_table(old, new, ["date", "symbol"], "borrow_fees.csv")


# ─── priced_at is the broker's as-of date, not the fetch date ───

def test_priced_at_uses_the_brokers_report_date():
    """IBKR stamps every position with the session the mark belongs to.

    This used to write date.today(), so a file pulled mid-session on Friday
    claimed Friday's prices while carrying Thursday's close — overstating
    freshness by a full session, which is the one thing priced_at exists to
    prevent (found by review 2026-08-15).
    """
    assert _as_of({"report_date": "2026-08-13"}) == "2026-08-13"


def test_priced_at_is_blank_when_the_broker_gives_no_date():
    """Blank, never today's date. instruments.price_warning() then says
    "prices carry no date", which is honest; falling back to today would
    reintroduce the bug quietly."""
    for row in ({}, {"report_date": ""}, {"report_date": "not-a-date"},
                {"report_date": None}):
        assert _as_of(row) == ""


def test_a_duplicate_already_in_history_is_the_one_deletion_allowed() -> None:
    """The known blind spot, pinned rather than fixed.

    The duplicate-key rule runs on `new` only. So if a bad row that once
    reached a commit is later deduped, `old` has the key twice and `new` has it
    once: the vanished-row check sees the key still present, and the cell
    comparison skips it because o.loc[key] is a DataFrame rather than a Series.
    A row leaves an append-only file and nothing objects.

    Left as-is deliberately: removing a duplicate is a legitimate repair, and
    refusing it would leave a damaged file permanently uncommittable. This test
    exists so the behaviour is a decision rather than an accident.
    """
    old = pd.DataFrame([{"trade_id": "T1", "price": "1"},
                        {"trade_id": "T1", "price": "1"},
                        {"trade_id": "T2", "price": "3"}])
    deduped = pd.DataFrame([{"trade_id": "T1", "price": "1"},
                            {"trade_id": "T2", "price": "3"}])
    check_broker_table(old, deduped, ["trade_id"], "transactions.csv")  # allowed

    # Every OTHER row is still checked, including on the same call.
    bad = deduped.copy()
    bad.loc[bad["trade_id"] == "T2", "price"] = "99"
    with pytest.raises(ValueError, match="price changed"):
        check_broker_table(old, bad, ["trade_id"], "transactions.csv")



# ─── Corporate actions close the share-count gap ─────────────────────────
#
# `docs/data-model-open-questions.md` #9 and REBUILD.md both recorded XLK as
# "bought 15 and sold 17 while holding 8" — and disagreed with each other
# about the numbers. It was a 2-for-1 split on 2025-12-05, reported by IBKR in
# every raw file and parsed by nothing. NFLX read as -9 shares.


def _frames(trades, held, actions=()):
    tx = pd.DataFrame(
        [{"symbol": s, "trade_date": d, "quantity": q, "asset_category": "STK"}
         for s, d, q in trades],
        columns=["symbol", "trade_date", "quantity", "asset_category"])
    pos = pd.DataFrame([{"ticker": s, "shares": q} for s, q in held],
                       columns=["ticker", "shares"])
    ca = pd.DataFrame([{"symbol": s, "quantity": q} for s, q in actions],
                      columns=["symbol", "quantity"])
    return tx, pos, ca


def test_a_split_that_is_recorded_reconciles():
    """The XLK case: 35 bought, 17 sold, +10 from a 2-for-1 split, 28 held."""
    tx, pos, ca = _frames(
        [("XLK", "2025-07-25", 35.0), ("XLK", "2025-12-20", -17.0)],
        [("XLK", 28.0)],
        [("XLK", 10.0)],
    )
    assert ibkr_sync.unreconciled_shares(tx, pos, ca).empty


def test_a_split_that_is_missing_is_reported_as_unexplained():
    """The guard that matters. Drop the action and the gap must reappear —
    the earlier version of this test looped over recorded actions only, so a
    MISSING action was structurally invisible to it."""
    tx, pos, ca = _frames(
        [("XLK", "2025-07-25", 35.0), ("XLK", "2025-12-20", -17.0)],
        [("XLK", 28.0)],
        [],                                   # the split nobody recorded
    )
    out = ibkr_sync.unreconciled_shares(tx, pos, ca)
    assert list(out["symbol"]) == ["XLK"]
    assert out.iloc[0]["why"] == "unexplained"
    assert out.iloc[0]["gap"] == 10.0


def test_a_position_held_before_the_log_is_not_called_unexplained():
    """39 symbols sell shares they never bought here, because they were bought
    before transactions.csv begins. Calling those a defect would bury the two
    that are one."""
    tx, pos, ca = _frames([("AMZN", "2025-07-01", -12.0)], [("AMZN", 0.0)])
    out = ibkr_sync.unreconciled_shares(tx, pos, ca)
    assert out.iloc[0]["why"] == "consistent with pre-log holding"


def test_the_three_real_splits_no_longer_appear_as_unexplained():
    """Against the live tables, not a fixture. NFLX, XLK and XLU were all
    `unexplained` until the actions were parsed on 2026-09-29."""
    root = Path(__file__).resolve().parents[1]
    ca_path = root / "data" / "corporate_actions.csv"
    if not ca_path.exists():
        pytest.skip("no corporate actions recorded yet")
    out = ibkr_sync.unreconciled_shares(
        pd.read_csv(root / "data" / "transactions.csv"),
        pd.read_csv(root / "data" / "positions.csv"),
        pd.read_csv(ca_path),
    )
    unexplained = set(out[out["why"] == "unexplained"]["symbol"])
    assert not ({"NFLX", "XLK", "XLU"} & unexplained), (
        "a recorded corporate action stopped explaining its share count"
    )


def test_unreconciled_shares_accepts_the_string_columns_the_sync_hands_it():
    """The regression that the rest of this file could not catch.

    `_read_append_only()` reads every broker table with `dtype=str` so ids stay
    exact, so `main()` passes text where these fixtures pass floats. The whole
    suite was green while `python -m src.ibkr_sync --cached` died with
    "'>=' not supported between instances of 'str' and 'float'".

    Test the shape the caller actually uses, not the shape that is convenient.
    """
    tx = pd.DataFrame([{"symbol": "XLK", "trade_date": "2025-07-25",
                        "quantity": "35", "asset_category": "STK"},
                       {"symbol": "XLK", "trade_date": "2025-12-20",
                        "quantity": "-17", "asset_category": "STK"}])
    pos = pd.DataFrame([{"ticker": "XLK", "shares": "28"}])
    ca = pd.DataFrame([{"symbol": "XLK", "quantity": "10"}])
    assert ibkr_sync.unreconciled_shares(tx, pos, ca).empty


def test_a_quantity_that_will_not_parse_is_not_a_reconciled_zero():
    """Coercing bad text to 0 would make a broken row look balanced."""
    tx = pd.DataFrame([{"symbol": "XLK", "trade_date": "2025-07-25",
                        "quantity": "n/a", "asset_category": "STK"}])
    pos = pd.DataFrame([{"ticker": "XLK", "shares": "28"}])
    out = ibkr_sync.unreconciled_shares(tx, pos, pd.DataFrame())
    assert out.iloc[0]["why"] == "unreadable quantity"


# ─── Found by adversarial review, 2026-09-29/30 ──────────────────────────


def test_an_empty_broker_section_does_not_crash_the_sync():
    """IBKR sends `<CorporateActions/>` with no children once the last action
    ages out of the rolling 365-day window. extract_* then returns a frame
    with ZERO columns, and the sync subscripts a column that is not there.

    Dated, not hypothetical: the three splits are 2025-11-17 and 2025-12-05,
    so this fires around 2026-12-05. main() writes five tables BEFORE this
    point, so the run would half-finish and end in a bare KeyError.
    """
    empty = pd.DataFrame([])
    merged, n = ibkr_sync.sync_corporate_actions(empty, Path("/nonexistent.csv"))
    assert n == 0 and merged.empty
    merged, n = ibkr_sync.sync_option_events(empty, Path("/nonexistent.csv"))
    assert n == 0 and merged.empty


def test_a_missing_split_is_indistinguishable_from_a_pre_log_holding():
    """A NEGATIVE result, recorded so nobody re-attempts the fix that fails.

    Review raised the worry on 2026-09-29: if XLK's 2-for-1 split were not
    recorded, the gap would be labelled as an ordinary pre-log holding and
    the reader would be told to ignore it.

    The tempting fix is "goes negative at the FIRST trade means pre-log; dips
    negative later means an event is missing". It was tried against the live
    tables and it is WRONG — META (+1, -6), MSTR (+5, -15, +2, -2) and SLV
    (+30, -25, -30) all dip later and are all plainly pre-log holdings.

    Both stories require exactly the same thing — shares from before the log
    — so the trade log cannot separate them. This test asserts that the two
    produce the SAME answer, which is the honest one. What actually defends
    against a missing split is that CorporateActions is parsed on every sync.
    """
    # XLK with its split missing
    missing_split = pd.DataFrame([
        {"symbol": "XLK", "trade_date": "2025-07-25", "quantity": 15, "asset_category": "STK"},
        {"symbol": "XLK", "trade_date": "2025-09-02", "quantity": -5, "asset_category": "STK"},
        {"symbol": "XLK", "trade_date": "2026-01-10", "quantity": -12, "asset_category": "STK"},
        {"symbol": "XLK", "trade_date": "2026-03-01", "quantity": 20, "asset_category": "STK"},
    ])
    # SLV, a real pre-log holding with the same shape
    pre_log = pd.DataFrame([
        {"symbol": "SLV", "trade_date": "2025-07-31", "quantity": 30, "asset_category": "STK"},
        {"symbol": "SLV", "trade_date": "2026-02-27", "quantity": -25, "asset_category": "STK"},
        {"symbol": "SLV", "trade_date": "2026-08-10", "quantity": -30, "asset_category": "STK"},
    ])
    a = ibkr_sync.unreconciled_shares(
        missing_split, pd.DataFrame([{"ticker": "XLK", "shares": 28}]), pd.DataFrame())
    b = ibkr_sync.unreconciled_shares(
        pre_log, pd.DataFrame([{"ticker": "SLV", "shares": 40}]), pd.DataFrame())

    assert a.iloc[0]["why"] == b.iloc[0]["why"], (
        "these two cases are genuinely indistinguishable from the trade log; "
        "a classifier that separates them is guessing"
    )
    assert a.iloc[0]["why"] == "consistent with pre-log holding", (
        "the label must read as an inference, not a determination"
    )


def test_a_position_that_sells_first_is_labelled_pre_log():
    """The other side of the same rule — 34 symbols rely on it. Calling these
    suspicious would bury the six that are."""
    tx = pd.DataFrame([{"symbol": "AMZN", "trade_date": "2025-07-01",
                        "quantity": -12, "asset_category": "STK"}])
    pos = pd.DataFrame([{"ticker": "AMZN", "shares": 0}])
    out = ibkr_sync.unreconciled_shares(tx, pos, pd.DataFrame())
    assert out.iloc[0]["why"] == "consistent with pre-log holding"


def test_an_unreadable_share_count_in_positions_is_reported():
    """`shares` was coerced with errors="coerce" and the NaN dropped by
    groupby.sum(), so a blank or corrupt share count read as "held 0" — and
    where that happened to balance, the row vanished from the report
    entirely. Only transactions had an unreadable check; positions had none.
    """
    tx = pd.DataFrame([{"symbol": "NVDA", "trade_date": "2025-07-01",
                        "quantity": 6, "asset_category": "STK"}])
    pos = pd.DataFrame([{"ticker": "NVDA", "shares": "oops"}])
    out = ibkr_sync.unreconciled_shares(tx, pos, pd.DataFrame())
    assert len(out) == 1
    assert out.iloc[0]["why"] == "unreadable share count"


def test_an_unreadable_quantity_is_reported_even_when_the_rest_balances():
    """The gap check ran BEFORE the unreadable check, and groupby().sum() had
    already skipped the NaN — so a destroyed money row whose siblings happened
    to add up disappeared from the report entirely and the book read clean.

    ⚠️ My own earlier test for this could not fail: its fixture left a 28-share
    gap, so it only ever exercised the branch where the coercion was already
    visible. Found by review 2026-10-01.
    """
    tx = pd.DataFrame([
        {"symbol": "XLK", "trade_date": "2025-07-01", "quantity": "28", "asset_category": "STK"},
        {"symbol": "XLK", "trade_date": "2025-08-01", "quantity": "n/a", "asset_category": "STK"},
    ])
    out = ibkr_sync.unreconciled_shares(
        tx, pd.DataFrame([{"ticker": "XLK", "shares": "28"}]), pd.DataFrame())
    assert len(out) == 1, "a row the code could not read must never be silent"
    assert out.iloc[0]["why"] == "unreadable quantity"


def test_an_unreadable_corporate_action_is_not_reported_as_a_confident_gap():
    """A split whose quantity will not parse was counted as 0.0, so the row
    read `corporate_actions 0.0` and `gap +10` — a specific, confident number
    computed from a value the code could not read, telling him to go and ask
    about a discrepancy that may not exist."""
    out = ibkr_sync.unreconciled_shares(
        pd.DataFrame([{"symbol": "XLK", "trade_date": "2025-07-01",
                       "quantity": "18", "asset_category": "STK"}]),
        pd.DataFrame([{"ticker": "XLK", "shares": "28"}]),
        pd.DataFrame([{"symbol": "XLK", "quantity": "n/a"}]))
    assert out.iloc[0]["why"] == "unreadable corporate action"


# ─── main() rebuilds the page (D10) ──────────────────────────────────────


def _prepare_a_real_sync(monkeypatch, tmp_path) -> None:
    """Point every file `main()` touches at `tmp_path` and feed it the real
    sample statement, so a full call to `ibkr_sync.main()` is hermetic — no
    read or write reaches the real `data/` files.

    `positions.csv` is the one file `main()` reads unconditionally
    (`pd.read_csv(POSITIONS_PATH)`), so it alone must exist in advance; every
    other table is left absent, which `sync_transactions` and its five
    siblings already treat as a valid first run.
    """
    monkeypatch.setattr(
        ibkr_sync, "_get_xml_bytes",
        lambda use_cached: FIXTURE_PATH.read_bytes(),
    )
    # main() prints every path it touches via `.relative_to(PROJECT_ROOT)`;
    # tmp_path has to stand in for the project root for that to resolve.
    monkeypatch.setattr(ibkr_sync, "PROJECT_ROOT", tmp_path)

    positions_path = tmp_path / "positions.csv"
    _existing_positions().to_csv(positions_path, index=False)
    monkeypatch.setattr(ibkr_sync, "POSITIONS_PATH", positions_path)

    # Each secondary sync function defaults `existing_path` to the module's
    # real path, bound at import time — patching the module-level constant
    # alone would not reach that default. Rebinding the function itself via
    # `functools.partial` does.
    secondary = {
        "TRANSACTIONS_PATH": ibkr_sync.sync_transactions,
        "CASH_TRANSACTIONS_PATH": ibkr_sync.sync_cash_transactions,
        "BORROW_FEES_PATH": ibkr_sync.sync_borrow_fees,
        "CORPORATE_ACTIONS_PATH": ibkr_sync.sync_corporate_actions,
        "OPTION_EVENTS_PATH": ibkr_sync.sync_option_events,
        "NAV_HISTORY_PATH": ibkr_sync.sync_nav_history,
    }
    for const_name, original_func in secondary.items():
        path = tmp_path / f"{const_name.lower()}.csv"
        monkeypatch.setattr(ibkr_sync, const_name, path)
        monkeypatch.setattr(
            ibkr_sync, original_func.__name__,
            functools.partial(original_func, existing_path=path),
        )


def test_a_sync_rebuilds_the_page(monkeypatch, tmp_path) -> None:
    """D10: he can never be looking at a page older than his data."""
    _prepare_a_real_sync(monkeypatch, tmp_path)

    calls = []

    class FakeBuildPage:
        def main(self, argv):
            calls.append(tuple(argv))
            return 0

    monkeypatch.setattr(ibkr_sync, "_load_build_page", lambda: FakeBuildPage())

    ibkr_sync.main()

    assert calls == [("--no-open",)], (
        "a sync must rebuild the page exactly once, headless (no browser tab)"
    )


def test_a_page_that_fails_to_build_does_not_fail_the_sync(
    monkeypatch, tmp_path, capsys
) -> None:
    """The sync's job is his data. Losing a redraw is an inconvenience; losing
    a sync because a renderer raised is the sync failing for a reason that has
    nothing to do with the broker — and P2 means the message has to say which
    of the two happened."""
    _prepare_a_real_sync(monkeypatch, tmp_path)

    class FakeBuildPage:
        def main(self, argv):
            raise RuntimeError("boom: the renderer blew up")

    monkeypatch.setattr(ibkr_sync, "_load_build_page", lambda: FakeBuildPage())

    ibkr_sync.main()  # must not raise: a renderer failure is not a sync failure

    out = capsys.readouterr().out
    assert "sync" in out.lower() and "succeeded" in out.lower(), (
        "the message must say the sync is the half that worked"
    )
    assert "boom: the renderer blew up" in out, (
        "the message must say why the rebuild is the half that failed"
    )
