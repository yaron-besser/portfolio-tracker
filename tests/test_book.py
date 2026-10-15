"""The book, and the three nested bases every percentage on the page is of.

Why this module exists. `exposure.by_sector()` and `clusters.weights()` each
read positions.csv and summed their own NAV. Two definitions of the one number
every panel divides by is the P7 failure, and the page would have been a third.

The tests here are mostly about the denominator, because that is where a
numerator and a denominator drift apart and every percentage in the book
rescales with nothing saying so.
"""

from __future__ import annotations

import csv

import pandas as pd
import pytest

from src import book


def _positions(tmp_path, rows):
    p = tmp_path / "positions.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _instruments(tmp_path, rows):
    from src import instruments
    p = tmp_path / "instruments.csv"
    df = pd.DataFrame(rows)
    for col in instruments.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[instruments.COLUMNS].to_csv(p, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return p


def _clusters(tmp_path, rows):
    from src import clusters
    p = tmp_path / "clusters.csv"
    df = pd.DataFrame(rows)
    for col in clusters.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[clusters.COLUMNS].to_csv(p, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return p


# One stock, one T-bill fund, one cash balance. The three bases each strip one.
def _book(tmp_path):
    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "shares": 10,
         "cost_basis": 900, "last_price": 100, "currency": "USD",
         "market_value": 1000, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "BILL", "instrument_id": "BILL", "shares": 1,
         "cost_basis": 100, "last_price": 100, "currency": "USD",
         "market_value": 100, "priced_at": "2026-10-02", "notes": ""},
        {"ticker": "USD_CASH", "instrument_id": "USD_CASH", "shares": 1,
         "cost_basis": 10, "last_price": 10, "currency": "USD",
         "market_value": 10, "priced_at": "2026-10-02", "notes": ""},
    ])
    inst = _instruments(tmp_path, [
        {"instrument_id": "AAA", "name": "A", "instrument_type": "stock",
         "sector": "Technology", "provider_sector": "Technology",
         "cluster_id": "Theme", "status": "active", "added": "2026-01-01"},
        {"instrument_id": "BILL", "name": "B", "instrument_type": "etf",
         "cluster_id": "Dry Powder", "status": "active", "added": "2026-01-01"},
        {"instrument_id": "USD_CASH", "name": "Cash", "instrument_type": "cash",
         "sector": "Cash / FX", "cluster_id": "Dry Powder",
         "status": "active", "added": "2026-01-01"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "Theme", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
        {"cluster_id": "Dry Powder", "parent": "", "layer": "role",
         "in_equity_base": False, "status": "active", "added": "2026-01-01"},
    ])
    return {"positions_path": pos, "instruments_path": inst, "clusters_path": cl}


def test_the_three_bases_are_nested_each_stripping_one_more_thing(tmp_path):
    """D6: one control with three settings, not two switches.

    Nested is the whole reason it is one control. If 'pure equity' could ever
    be larger than 'minus cash', two independent switches would be the honest
    shape and the page would need a four-state home it does not have.
    """
    paths = _book(tmp_path)
    everything = book.nav("everything", **paths)
    minus_cash = book.nav("minus-cash", **paths)
    pure = book.nav("pure-equity", **paths)
    assert everything > minus_cash > pure > 0


def test_each_basis_parts_sum_to_that_basis_nav(tmp_path):
    """The failure this module exists to prevent: a numerator from one basis
    divided by a denominator from another. Every percentage rescales and
    nothing says so.

    Checked against totals computed independently from the fixture itself —
    AAA $1,000, BILL $100, USD_CASH $10 — not against `nav()` alone: `nav()`
    is *implemented* as `holdings(basis)["market_value"].sum()`, so comparing
    only the two of them to each other is an identity that cannot fail
    (found by review, round 1)."""
    paths = _book(tmp_path)
    expected = {
        "everything": 1000 + 100 + 10,    # every lot, cash included
        "minus-cash": 1000 + 100,         # USD_CASH is cash, stripped
        "pure-equity": 1000,              # BILL and USD_CASH are Dry Powder,
    }                                      # which is not in the equity base
    for basis, total in expected.items():
        held = book.holdings(basis, **paths)
        assert held["market_value"].sum() == pytest.approx(total)
        assert book.nav(basis, **paths) == pytest.approx(total)


def test_minus_cash_strips_only_cash_and_fx_balances(tmp_path):
    paths = _book(tmp_path)
    ids = set(book.holdings("minus-cash", **paths)["instrument_id"])
    assert "USD_CASH" not in ids
    assert {"AAA", "BILL"} <= ids


def test_pure_equity_also_strips_what_is_not_in_the_equity_base(tmp_path):
    """SGOV is cash, not equity — his call. The flag lives on the cluster, so
    the T-bill fund leaves at the third setting and not the second."""
    paths = _book(tmp_path)
    ids = set(book.holdings("pure-equity", **paths)["instrument_id"])
    assert ids == {"AAA"}


def test_a_holding_with_no_cluster_stays_in_the_equity_base(tmp_path):
    """Matches what clusters.weights() has always done: `.get(cid, True)`.

    A cluster-less holding is refused by the commit guard, so this is the
    shape of a file edited by hand or a lot the sync has just auto-added. It
    must not silently leave the denominator — that would write up every other
    holding in the book.
    """
    paths = _book(tmp_path)
    pd.DataFrame([
        {"ticker": "CCC", "instrument_id": "CCC", "shares": 1,
         "cost_basis": 50, "last_price": 50, "currency": "USD",
         "market_value": 50, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(paths["positions_path"], mode="a", header=False, index=False)
    from src import instruments
    inst = pd.read_csv(paths["instruments_path"], dtype=str,
                       keep_default_na=False)
    row = {c: "" for c in instruments.COLUMNS}
    row.update({"instrument_id": "CCC", "name": "C", "instrument_type": "stock",
                "sector": "Technology", "status": "active",
                "added": "2026-01-01"})
    pd.concat([inst, pd.DataFrame([row])])[instruments.COLUMNS].to_csv(
        paths["instruments_path"], index=False, quoting=csv.QUOTE_NONNUMERIC)
    ids = set(book.holdings("pure-equity", **paths)["instrument_id"])
    assert "CCC" in ids


def _book_with_a_short(tmp_path):
    """The Step-1 fixture plus one short, $500 by magnitude — bigger than
    BILL ($100) and USD_CASH ($10), smaller than AAA ($1,000). Shared by the
    size test below and by the `biggest()` tests, which need the same ranking
    question: does a short's RANK follow its magnitude or its sign."""
    paths = _book(tmp_path)
    pd.DataFrame([
        {"ticker": "SHRT", "instrument_id": "SHRT", "shares": -5,
         "cost_basis": -500, "last_price": 100, "currency": "USD",
         "market_value": -500, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(paths["positions_path"], mode="a", header=False, index=False)
    from src import instruments
    inst = pd.read_csv(paths["instruments_path"], dtype=str,
                       keep_default_na=False)
    row = {c: "" for c in instruments.COLUMNS}
    row.update({"instrument_id": "SHRT", "name": "S", "instrument_type": "short",
                "sector": "Technology", "cluster_id": "Theme",
                "status": "active", "added": "2026-01-01"})
    pd.concat([inst, pd.DataFrame([row])])[instruments.COLUMNS].to_csv(
        paths["instruments_path"], index=False, quoting=csv.QUOTE_NONNUMERIC)
    return paths


def test_size_is_magnitude_so_a_short_ranks_by_how_big_it_is(tmp_path):
    """A short carries a negative market_value. Ranking bars by value puts the
    biggest short at the bottom of the page; `size` is what the ranking uses
    and `market_value` is what is drawn. claims.position_pct_of_nav() made the
    same split for the same reason."""
    paths = _book_with_a_short(tmp_path)
    held = book.holdings("everything", **paths).set_index("instrument_id")
    assert held.at["SHRT", "market_value"] < 0
    assert held.at["SHRT", "size"] > 0
    assert held["size"].idxmax() == "AAA"


# ─── biggest(): the top n, ranked the same way, scaled to the basis asked for ───

def test_biggest_returns_n_rows_ordered_by_magnitude(tmp_path):
    """AAA $1,000, SHRT -$500, BILL $100, USD_CASH $10. The short outranks
    BILL and USD_CASH — biggest() must rank the same way holdings() does, not
    re-sort by signed value once it slices to n."""
    paths = _book_with_a_short(tmp_path)
    top = book.biggest(2, "everything", **paths)
    assert list(top["instrument_id"]) == ["AAA", "SHRT"]


def test_biggest_pct_of_basis_is_a_share_of_the_basis_asked_for(tmp_path):
    """Not always a share of 'everything'. pure-equity drops BILL and
    USD_CASH, so AAA's own $1,000 is a different percentage of a different,
    smaller total — and the short counts in that total too."""
    paths = _book_with_a_short(tmp_path)

    equity = book.biggest(5, "pure-equity", **paths).set_index("instrument_id")
    # pure-equity total: AAA 1000 + SHRT -500 = 500 (BILL, USD_CASH are out).
    assert set(equity.index) == {"AAA", "SHRT"}
    assert equity.at["AAA", "pct_of_basis"] == pytest.approx(200.0)

    everything = book.biggest(5, "everything", **paths).set_index("instrument_id")
    # everything total: 1000 + 100 + 10 - 500 = 610.
    assert everything.at["AAA", "pct_of_basis"] == pytest.approx(
        round(1000 / 610 * 100, 2))


def test_biggest_ranks_a_short_by_how_big_it_is_not_to_the_bottom(tmp_path):
    """Ranking by signed value would put a $500 short below a $10 cash
    balance. biggest() ranks by size, the same way holdings() does, so it
    lands second — this is the test that would catch biggest() diverging
    from holdings()'s own ranking rule."""
    paths = _book_with_a_short(tmp_path)
    order = list(book.biggest(4, "everything", **paths)["instrument_id"])
    assert order.index("SHRT") < order.index("BILL")
    assert order.index("SHRT") < order.index("USD_CASH")


def test_one_instrument_many_lots_is_one_row(tmp_path):
    """ASML_USD and ASML_EUR are two broker lots of one instrument."""
    paths = _book(tmp_path)
    pd.DataFrame([
        {"ticker": "AAA_EUR", "instrument_id": "AAA", "shares": 5,
         "cost_basis": 400, "last_price": 90, "currency": "EUR",
         "market_value": 500, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(paths["positions_path"], mode="a", header=False, index=False)
    held = book.holdings("everything", **paths)
    assert list(held["instrument_id"]).count("AAA") == 1


def test_currency_split_is_by_lot_not_by_instrument(tmp_path):
    """`currency` says what the LOT is quoted in; market_value is always USD.
    Summing per instrument would file a euro lot under the instrument's first
    currency and hide the split the panel exists to show."""
    paths = _book(tmp_path)
    pd.DataFrame([
        {"ticker": "AAA_EUR", "instrument_id": "AAA", "shares": 5,
         "cost_basis": 400, "last_price": 90, "currency": "EUR",
         "market_value": 500, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(paths["positions_path"], mode="a", header=False, index=False)
    cur = book.by_currency("everything", **paths).set_index("currency")
    assert set(cur.index) == {"USD", "EUR"}
    assert cur["market_value"].sum() == pytest.approx(
        book.nav("everything", **paths))


def test_words_name_what_the_basis_excludes_in_plain_language(tmp_path):
    """He is not a developer. 'pure-equity' is a key, never something he reads.

    The third setting is his phrase and it does NOT mean only equities — it
    keeps crypto, bullion and commodity funds. The sentence has to say what is
    actually out, or the label overstates it.
    """
    assert "cash" in book.words("minus-cash").lower()
    pure = book.words("pure-equity").lower()
    assert "cash" in pure and ("t-bill" in pure or "treasury" in pure)
    assert "equity" not in book.words("everything").lower()


def test_an_unknown_basis_says_what_the_three_are(tmp_path):
    """P2: a refusal names the problem and the fix."""
    with pytest.raises(ValueError) as e:
        book.nav("equities-only", **_book(tmp_path))
    for basis in book.BASES:
        assert basis in str(e.value)


def test_an_unknown_basis_refuses_even_on_an_empty_book(tmp_path):
    """check_basis() used to be reached only from inside in_basis(), which
    `holdings()` and `by_currency()` call once per LOT — so an empty
    positions file meant zero lots, meant it was never reached at all, and a
    typo'd basis quietly answered 0.0 / an empty frame instead of refusing.
    Both now check first, before looking at a single row."""
    paths = _book(tmp_path)
    empty = tmp_path / "empty_positions.csv"
    pd.DataFrame(columns=["ticker", "instrument_id", "shares", "cost_basis",
                          "last_price", "currency", "market_value",
                          "priced_at", "notes"]).to_csv(empty, index=False)
    empty_paths = {**paths, "positions_path": empty}
    with pytest.raises(ValueError, match="equities-only"):
        book.nav("equities-only", **empty_paths)
    with pytest.raises(ValueError, match="equities-only"):
        book.by_currency("equities-only", **empty_paths)


def test_money_under_an_unknown_instrument_is_reported_not_dropped(tmp_path):
    """ibkr_sync writes a brand-new lot with AUTO-ADDED in its notes, so this
    is exactly how a ticker bought this week arrives. Dropping it removes it
    from the numerator AND the denominator."""
    paths = _book(tmp_path)
    pd.DataFrame([
        {"ticker": "ZZZ", "instrument_id": "ZZZ", "shares": 1,
         "cost_basis": 7, "last_price": 7, "currency": "USD",
         "market_value": 7, "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(paths["positions_path"], mode="a", header=False, index=False)
    # unclassified() takes explicit keyword parameters, not **paths — passing
    # the whole dict would TypeError on clusters_path, which is the point.
    unknown = book.unclassified(positions_path=paths["positions_path"],
                                instruments_path=paths["instruments_path"])
    assert list(unknown["instrument_id"]) == ["ZZZ"]
    assert "ZZZ" not in set(book.holdings("everything", **paths)["instrument_id"])


def test_an_unreadable_market_value_refuses_rather_than_shortening_nav(tmp_path):
    """One blank SGOV row moved IWM from 16.3% to 56.4% when
    position_pct_of_nav() coerced instead of refusing. Same rule here: every
    percentage on the page divides by this number."""
    paths = _book(tmp_path)
    pd.DataFrame([
        {"ticker": "BAD", "instrument_id": "AAA", "shares": 1,
         "cost_basis": 1, "last_price": 1, "currency": "USD",
         "market_value": "", "priced_at": "2026-10-02", "notes": ""},
    ]).to_csv(paths["positions_path"], mode="a", header=False, index=False)
    with pytest.raises(ValueError) as e:
        book.nav("everything", **paths)
    assert "BAD" in str(e.value)


# ─── proving the delegation changed no number (Steps 5 and 6) ───

def test_the_equity_denominator_is_the_pure_equity_basis(tmp_path):
    """clusters.weights() and book.nav() must not be able to disagree about
    how big the equity book is. One definition (P7), proved rather than
    asserted in a comment."""
    from src import clusters
    paths = _book(tmp_path)
    w = clusters.weights(paths["clusters_path"], paths["positions_path"],
                         paths["instruments_path"])
    equity_pct = w.loc[w["cluster_id"] == "Theme", "pct_of_equity"].iloc[0]
    held = book.holdings("pure-equity", **paths)
    expected = (held["market_value"].sum()
                and held.loc[held["cluster_id"] == "Theme",
                             "market_value"].sum()
                / book.nav("pure-equity", **paths) * 100)
    assert equity_pct == pytest.approx(round(expected, 2))


def test_a_cluster_outside_the_equity_base_has_no_equity_percentage(tmp_path):
    """'Cash is a percentage of equity' is a sentence with no meaning. weights() left
    it blank before the delegation and must still."""
    from src import clusters
    paths = _book(tmp_path)
    w = clusters.weights(paths["clusters_path"], paths["positions_path"],
                         paths["instruments_path"])
    assert pd.isna(w.loc[w["cluster_id"] == "Dry Powder",
                         "pct_of_equity"].iloc[0])


# ─── profit and loss (CLAUDE.md, 2026-10-08) ─────────────────────────────
#
# His two decisions, implemented rather than revisited here:
#   A — a foreign lot's cost is dollars in, at each purchase's own rate,
#       whenever the trade log can rebuild the purchases.
#   B — everywhere else (a dollar lot always; a foreign lot the log cannot
#       rebuild), IBKR's own total — flagged only for the foreign case,
#       because that is the one where "today's rate" and "the rate he
#       actually paid" can disagree.
# Every fixture below is engineered to land in exactly one branch of
# `lot_cost_usd()`; none of these shapes exists in his real book today
# (`uncosted_lots()`/`fx_fallback_lots()` are both empty there), which is
# the whole reason a fixture, not real data, is what proves the fallback
# and the flag actually fire.

def _transactions(tmp_path, rows):
    p = tmp_path / "transactions.csv"
    cols = ["trade_id", "symbol", "currency", "asset_category", "quantity",
           "net_cash", "fx_rate_to_base", "fifo_pnl"]
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df[cols].to_csv(p, index=False)
    return p


def _corporate_actions(tmp_path, rows=()):
    p = tmp_path / "corporate_actions.csv"
    cols = ["symbol", "quantity"]
    df = pd.DataFrame(list(rows), columns=cols) if rows else pd.DataFrame(columns=cols)
    df.to_csv(p, index=False)
    return p


def _pnl_instruments(tmp_path, rows):
    """`instrument_type` is the one column `lot_cost_usd()` reads off
    instruments.csv — everything else is irrelevant to these tests, so
    only that column is set per row, the rest left to `_instruments()`'s
    own blank-fill."""
    return _instruments(tmp_path, [
        {"instrument_id": iid, "name": iid, "instrument_type": itype,
         "status": "active", "added": "2026-01-01"}
        for iid, itype in rows
    ])


def _pnl_paths(tmp_path, pos_rows, inst_rows, tx_rows=(), ca_rows=()):
    return {
        "positions_path": _positions(tmp_path, pos_rows),
        "instruments_path": _pnl_instruments(tmp_path, inst_rows),
        "transactions_path": _transactions(tmp_path, list(tx_rows)),
        "corporate_actions_path": _corporate_actions(tmp_path, ca_rows),
    }


def test_a_dollar_lot_uses_ibkrs_own_total_regardless_of_the_trade_log(tmp_path):
    """Rule A is only ever about currency movement, and a dollar never
    moves against itself — so a dollar lot's cost is `cost_basis_money` even
    when the trade log on file cannot account for a single share of it
    (no `transactions.csv` row at all here), and it is never flagged."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "DOLLAR", "instrument_id": "DOLLAR", "shares": 10,
          "cost_basis": 100, "last_price": 120, "currency": "USD",
          "market_value": 1200, "priced_at": "2026-10-02",
          "cost_basis_money": 1000.0, "fx_rate_to_base": 1.0, "notes": ""}],
        [("DOLLAR", "stock")],
    )
    df = book.lot_cost_usd(**paths)
    row = df[df["ticker"] == "DOLLAR"].iloc[0]
    assert row["established"]
    assert not row["flagged"]
    assert row["method"] == "ibkr_total"
    assert row["cost_usd"] == pytest.approx(1000.0)
    assert row["unrealized"] == pytest.approx(200.0)


def test_a_reconciling_foreign_lot_uses_each_purchases_own_rate(tmp_path):
    """Rule A: two buys at two different rates, no sell — the USD cost is
    the sum of each trade's own `net_cash * fx_rate_to_base`, never the
    lot's `fx_rate_to_base` (the REPORT-date rate, deliberately set to a
    third, different value here so a reversion to it would be caught)."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "FXGOOD", "instrument_id": "FXGOOD", "shares": 2,
          "cost_basis": 500, "last_price": 600, "currency": "EUR",
          "market_value": 1400, "priced_at": "2026-10-02",
          "cost_basis_money": 1000.0, "fx_rate_to_base": 1.30, "notes": ""}],
        [("FXGOOD", "stock")],
        tx_rows=[
            {"trade_id": "1", "symbol": "FXGOOD", "currency": "EUR",
             "asset_category": "STK", "quantity": 1, "net_cash": -400,
             "fx_rate_to_base": 1.10, "fifo_pnl": 0.0},
            {"trade_id": "2", "symbol": "FXGOOD", "currency": "EUR",
             "asset_category": "STK", "quantity": 1, "net_cash": -600,
             "fx_rate_to_base": 1.20, "fifo_pnl": 0.0},
        ],
    )
    df = book.lot_cost_usd(**paths)
    row = df[df["ticker"] == "FXGOOD"].iloc[0]
    assert row["established"]
    assert not row["flagged"]
    assert row["method"] == "trade_reconstruction"
    # 400*1.10 + 600*1.20 = 440 + 720 = 1160 — NOT 1000*1.30 = 1300, the
    # report-date-rate answer a bug would quietly fall back to.
    assert row["cost_usd"] == pytest.approx(1160.0)
    assert row["unrealized"] == pytest.approx(1400 - 1160.0)


def test_a_foreign_lot_whose_shares_do_not_reconcile_falls_back_flagged(tmp_path):
    """Rule B, the share-count case: the log only accounts for 1 of 2 shares
    held, so rule A cannot be trusted — the fallback fires, and it is the
    one case `page.honesty()` must name."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "FXBAD", "instrument_id": "FXBAD", "shares": 2,
          "cost_basis": 500, "last_price": 600, "currency": "EUR",
          "market_value": 1400, "priced_at": "2026-10-02",
          "cost_basis_money": 1000.0, "fx_rate_to_base": 1.25, "notes": ""}],
        [("FXBAD", "stock")],
        tx_rows=[
            {"trade_id": "1", "symbol": "FXBAD", "currency": "EUR",
             "asset_category": "STK", "quantity": 1, "net_cash": -400,
             "fx_rate_to_base": 1.10, "fifo_pnl": 0.0},
        ],
    )
    df = book.lot_cost_usd(**paths)
    row = df[df["ticker"] == "FXBAD"].iloc[0]
    assert row["established"]
    assert row["flagged"]
    assert row["method"] == "ibkr_report_date_fx"
    assert row["cost_usd"] == pytest.approx(1000.0 * 1.25)
    assert "do not reconcile" in row["reason"]
    assert "FXBAD" in row["reason"]


def test_a_reconciling_foreign_lot_with_a_sale_falls_back_flagged(tmp_path):
    """Rule A is a plain sum of every BUY, which is only a safe stand-in for
    the FIFO cost of what remains when nothing was ever sold. A lot that
    reconciles in share count but includes a sale must still fall back,
    with its own distinct reason — never silently summed as if the sale
    had not happened."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "FXSELL", "instrument_id": "FXSELL", "shares": 1,
          "cost_basis": 500, "last_price": 600, "currency": "EUR",
          "market_value": 650, "priced_at": "2026-10-02",
          "cost_basis_money": 500.0, "fx_rate_to_base": 1.15, "notes": ""}],
        [("FXSELL", "stock")],
        tx_rows=[
            {"trade_id": "1", "symbol": "FXSELL", "currency": "EUR",
             "asset_category": "STK", "quantity": 2, "net_cash": -800,
             "fx_rate_to_base": 1.10, "fifo_pnl": 0.0},
            {"trade_id": "2", "symbol": "FXSELL", "currency": "EUR",
             "asset_category": "STK", "quantity": -1, "net_cash": 420,
             "fx_rate_to_base": 1.12, "fifo_pnl": 20.0},
        ],
    )
    df = book.lot_cost_usd(**paths)
    row = df[df["ticker"] == "FXSELL"].iloc[0]
    assert row["established"]
    assert row["flagged"]
    assert row["method"] == "ibkr_report_date_fx"
    assert "sale" in row["reason"]


def test_a_reconciling_foreign_lot_whose_cost_disagrees_with_ibkr_falls_back_flagged(tmp_path):
    """'Refuse rather than mislead' (CLAUDE.md): the trades reconcile in
    SHARE COUNT, but their own native-currency total is nowhere near
    IBKR's own `cost_basis_money` — a real disagreement, not rounding.
    Neither number is silently picked; the fallback fires and the reason
    names both figures."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "FXDISAGREE", "instrument_id": "FXDISAGREE", "shares": 1,
          "cost_basis": 500, "last_price": 600, "currency": "EUR",
          "market_value": 700, "priced_at": "2026-10-02",
          "cost_basis_money": 500.0, "fx_rate_to_base": 1.15, "notes": ""}],
        [("FXDISAGREE", "stock")],
        tx_rows=[
            # The log's own buy totals 300 EUR; IBKR's cost_basis_money
            # says 500 EUR for the same one share — a 200 EUR gap, far
            # past any rounding tolerance.
            {"trade_id": "1", "symbol": "FXDISAGREE", "currency": "EUR",
             "asset_category": "STK", "quantity": 1, "net_cash": -300,
             "fx_rate_to_base": 1.10, "fifo_pnl": 0.0},
        ],
    )
    df = book.lot_cost_usd(**paths)
    row = df[df["ticker"] == "FXDISAGREE"].iloc[0]
    assert row["established"]
    assert row["flagged"]
    assert row["method"] == "ibkr_report_date_fx"
    assert row["cost_usd"] == pytest.approx(500.0 * 1.15)
    assert "disagrees" in row["reason"]
    assert "300.00" in row["reason"] and "500.00" in row["reason"]


def test_a_foreign_lot_from_a_corporate_action_alone_falls_back_flagged(tmp_path):
    """Reconciles, but there is no purchase anywhere in the log to
    reconstruct a rate from — a split-only lot. Still flagged: rule A
    needs an actual purchase, not just a share count that adds up."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "FXSPLIT", "instrument_id": "FXSPLIT", "shares": 4,
          "cost_basis": 100, "last_price": 120, "currency": "EUR",
          "market_value": 500, "priced_at": "2026-10-02",
          "cost_basis_money": 400.0, "fx_rate_to_base": 1.10, "notes": ""}],
        [("FXSPLIT", "stock")],
        ca_rows=[{"symbol": "FXSPLIT", "quantity": 4}],
    )
    df = book.lot_cost_usd(**paths)
    row = df[df["ticker"] == "FXSPLIT"].iloc[0]
    assert row["established"]
    assert row["flagged"]
    assert "no purchase on file" in row["reason"]


def test_a_lot_with_no_cost_basis_at_all_gets_no_figure_and_is_named(tmp_path):
    """CLAUDE.md's hard constraint: never invent a value. A pre-migration
    row (no `cost_basis_money`) is named by `uncosted_lots()`, left out of
    `unrealized_pnl()`'s total, and carries `nan`, not a guessed number.

    🚨 Review, 2026-10-08 (I3): with NOBASIS the only lot, NOTHING could be
    costed — `unrealized_pnl()`/`total_pnl()` must read `None`, never the
    `0.0` a selection-of-nothing's `.sum()` would otherwise quietly give,
    which is indistinguishable on screen from a real, confirmed zero."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "NOBASIS", "instrument_id": "NOBASIS", "shares": 10,
          "cost_basis": 100, "last_price": 110, "currency": "USD",
          "market_value": 1100, "priced_at": "2026-10-02",
          "cost_basis_money": "", "fx_rate_to_base": 1.0, "notes": ""}],
        [("NOBASIS", "stock")],
    )
    df = book.lot_cost_usd(**paths)
    row = df[df["ticker"] == "NOBASIS"].iloc[0]
    assert not row["established"]
    assert pd.isna(row["cost_usd"])
    assert pd.isna(row["unrealized"])
    named = book.uncosted_lots(**paths)
    assert named["ticker"].tolist() == ["NOBASIS"]
    assert book.unrealized_pnl(**paths) is None
    assert book.total_pnl(**paths) is None


def test_a_foreign_lot_with_no_exchange_rate_is_also_uncosted(tmp_path):
    """The fallback itself (rule B) needs `fx_rate_to_base` — a foreign lot
    missing THAT, even with a cost in its own currency, still cannot be
    priced in dollars at all."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "NOFX", "instrument_id": "NOFX", "shares": 3,
          "cost_basis": 100, "last_price": 110, "currency": "EUR",
          "market_value": 400, "priced_at": "2026-10-02",
          "cost_basis_money": 300.0, "fx_rate_to_base": "", "notes": ""}],
        [("NOFX", "stock")],
    )
    df = book.lot_cost_usd(**paths)
    row = df[df["ticker"] == "NOFX"].iloc[0]
    assert not row["established"]


def test_a_cash_lot_carries_no_pnl_at_all(tmp_path):
    """A cash balance has no purchase price — `lot_cost_usd()` must not
    even mention it, established or not, the same way `in_basis()` already
    excludes `instrument_type in CASH_KINDS` from every basis."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "USD_CASH", "instrument_id": "USD_CASH", "shares": 1,
          "cost_basis": 500, "last_price": 500, "currency": "USD",
          "market_value": 500, "priced_at": "2026-10-02",
          "cost_basis_money": 500.0, "fx_rate_to_base": 1.0, "notes": ""}],
        [("USD_CASH", "cash")],
    )
    df = book.lot_cost_usd(**paths)
    assert df.empty
    assert book.unrealized_pnl(**paths) == 0.0


def test_unrealized_and_total_are_absent_not_zero_when_every_lot_is_uncosted(
    tmp_path,
):
    """🚨 I3 (review, 2026-10-08), the exact repro: run the panel against a
    positions.csv from before this feature's own sync change — no
    `cost_basis_money` column on any row at all. Every lot (not just one,
    the way the test above happens to have only one) reads `established=
    False`, `market_value` is still perfectly real, and the OLD code
    returned a plausible-looking `$0.00` unrealised and a `total` equal to
    realised alone — exactly the shape of a page that "looks perfect"
    while quietly having priced nothing. Both must come back `None`."""
    paths = _pnl_paths(
        tmp_path,
        [
            {"ticker": "A", "instrument_id": "A", "shares": 10,
             "cost_basis": 100, "last_price": 110, "currency": "USD",
             "market_value": 1100, "priced_at": "2026-10-02",
             "cost_basis_money": "", "fx_rate_to_base": "", "notes": ""},
            {"ticker": "B", "instrument_id": "B", "shares": 5,
             "cost_basis": 50, "last_price": 60, "currency": "USD",
             "market_value": 300, "priced_at": "2026-10-02",
             "cost_basis_money": "", "fx_rate_to_base": "", "notes": ""},
        ],
        [("A", "stock"), ("B", "stock")],
    )
    df = book.lot_cost_usd(**paths)
    assert len(df) == 2
    assert not df["established"].any()
    assert book.unrealized_pnl(**paths) is None
    assert book.total_pnl(**paths) is None
    # Named, not silently dropped — the other half of the honesty contract.
    assert sorted(book.uncosted_lots(**paths)["ticker"]) == ["A", "B"]


def test_realized_pnl_sums_fifo_pnl_once(tmp_path):
    """IBKR's own realised figure, each trade's currency, summed once —
    every row here is USD (rate 1.0) on purpose, so this test alone
    proves nothing about currency conversion; see the dedicated foreign-
    currency test below for that, and
    `test_the_fx_fallback_sentence_names_the_lot_and_what_it_means`-style
    mutation proof in that same test."""
    p = _transactions(tmp_path, [
        {"trade_id": "1", "symbol": "A", "currency": "USD",
         "asset_category": "STK", "quantity": 10, "net_cash": -1000,
         "fx_rate_to_base": 1.0, "fifo_pnl": 0.0},
        {"trade_id": "2", "symbol": "A", "currency": "USD",
         "asset_category": "STK", "quantity": -10, "net_cash": 1200,
         "fx_rate_to_base": 1.0, "fifo_pnl": 200.0},
        {"trade_id": "3", "symbol": "B", "currency": "USD",
         "asset_category": "STK", "quantity": -5, "net_cash": 300,
         "fx_rate_to_base": 1.0, "fifo_pnl": 50.0},
    ])
    assert book.realized_pnl(p) == pytest.approx(250.0)


def test_realized_pnl_converts_a_foreign_trades_fifo_pnl_to_base(tmp_path):
    """🚨 C1 (review, 2026-10-08): `fifo_pnl` is denominated in the TRADE'S
    OWN currency, never base — proven on the real IMAE close in
    `data/transactions.csv` (EUR, `fifo_pnl=585.0776`, nowhere near a
    plausible dollar figure for that fill). A bare `.sum()` would read
    this KRW-shaped fixture as a dollar amount nearly 150x too large —
    his own warning about foreign currency "screwing the program",
    concretely. Every dollar trade elsewhere in this suite carries rate
    1.0, which is exactly why this bug shipped with every other test
    green; this fixture is the one that cannot pass by accident."""
    p = _transactions(tmp_path, [
        {"trade_id": "1", "symbol": "KRWSTOCK", "currency": "KRW",
         "asset_category": "STK", "quantity": -10, "net_cash": 2716000,
         "fx_rate_to_base": 0.00074624, "fifo_pnl": 271600.0},
    ])
    # 271,600 KRW at today's rate (~$202.68) — nowhere near the bare
    # "271600" a currency-blind sum would have produced, nearly 1,343x
    # too large on a real Samsung-sized position (the review's own
    # worked example).
    assert book.realized_pnl(p) == pytest.approx(271600.0 * 0.00074624)
    assert book.realized_pnl(p) == pytest.approx(202.68, abs=0.01)


def test_realized_pnl_refuses_an_unreadable_row_rather_than_undercounting(tmp_path):
    """The same loud-not-coerced shape `lots()` already uses for
    `market_value` — a quietly dropped bad row would look like a clean,
    smaller total instead of a wrong one."""
    p = _transactions(tmp_path, [
        {"trade_id": "1", "symbol": "A", "currency": "USD",
         "asset_category": "STK", "quantity": 10, "net_cash": -1000,
         "fx_rate_to_base": 1.0, "fifo_pnl": "not-a-number"},
    ])
    with pytest.raises(ValueError) as e:
        book.realized_pnl(p)
    assert "1" in str(e.value)


def test_realized_pnl_refuses_an_unreadable_fx_rate_too(tmp_path):
    """The conversion rate is just as load-bearing as `fifo_pnl` itself
    now — an unreadable rate must refuse exactly like an unreadable P&L,
    never fall back to treating the row as already-base (rate 1)."""
    p = _transactions(tmp_path, [
        {"trade_id": "1", "symbol": "A", "currency": "EUR",
         "asset_category": "STK", "quantity": -10, "net_cash": 1000,
         "fx_rate_to_base": "not-a-rate", "fifo_pnl": 50.0},
    ])
    with pytest.raises(ValueError) as e:
        book.realized_pnl(p)
    assert "1" in str(e.value)


def test_total_pnl_is_realized_plus_unrealized(tmp_path):
    """The one number that answers "how is this book doing, all in" —
    computed exactly once, in the one module allowed to add at all."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "DOLLAR", "instrument_id": "DOLLAR", "shares": 10,
          "cost_basis": 100, "last_price": 120, "currency": "USD",
          "market_value": 1200, "priced_at": "2026-10-02",
          "cost_basis_money": 1000.0, "fx_rate_to_base": 1.0, "notes": ""}],
        [("DOLLAR", "stock")],
        tx_rows=[
            {"trade_id": "1", "symbol": "DOLLAR", "currency": "USD",
             "asset_category": "STK", "quantity": -1, "net_cash": 150,
             "fx_rate_to_base": 1.0, "fifo_pnl": 50.0},
        ],
    )
    assert book.realized_pnl(paths["transactions_path"]) == pytest.approx(50.0)
    assert book.unrealized_pnl(**paths) == pytest.approx(200.0)
    assert book.total_pnl(**paths) == pytest.approx(250.0)


# ─── I2 (review, 2026-10-08): the FX-choice effect, derived not typed ────

def test_fx_choice_effect_is_zero_when_no_lot_used_rule_a(tmp_path):
    """Nothing to explain when every lot is a dollar lot — the ordinary
    shape of most of his book."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "DOLLAR", "instrument_id": "DOLLAR", "shares": 10,
          "cost_basis": 100, "last_price": 120, "currency": "USD",
          "market_value": 1200, "priced_at": "2026-10-02",
          "cost_basis_money": 1000.0, "fx_rate_to_base": 1.0, "notes": ""}],
        [("DOLLAR", "stock")],
    )
    assert book.fx_choice_effect(**paths) == 0.0


def test_fx_choice_effect_matches_the_reconstructed_vs_report_date_gap(tmp_path):
    """The exact figure a reviewer cross-checked against IBKR's own
    FIFOPerformanceSummaryInBase, reproduced on a small fixture: rule A's
    dollars-in cost (each trade's own rate) versus what the SAME
    `cost_basis_money` would cost at today's `fx_rate_to_base` instead.
    Positive means his own figure reads lower than IBKR's would."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "FXGOOD", "instrument_id": "FXGOOD", "shares": 2,
          "cost_basis": 500, "last_price": 600, "currency": "EUR",
          "market_value": 1400, "priced_at": "2026-10-02",
          "cost_basis_money": 1000.0, "fx_rate_to_base": 1.30, "notes": ""}],
        [("FXGOOD", "stock")],
        tx_rows=[
            {"trade_id": "1", "symbol": "FXGOOD", "currency": "EUR",
             "asset_category": "STK", "quantity": 1, "net_cash": -400,
             "fx_rate_to_base": 1.10, "fifo_pnl": 0.0},
            {"trade_id": "2", "symbol": "FXGOOD", "currency": "EUR",
             "asset_category": "STK", "quantity": 1, "net_cash": -600,
             "fx_rate_to_base": 1.20, "fifo_pnl": 0.0},
        ],
    )
    # Rule A's cost: 400*1.10 + 600*1.20 = 1160.0. Report-date cost:
    # 1000.0*1.30 = 1300.0. effect = 1160 - 1300 = -140 (his own figure
    # reads HIGHER here, because the dollar was weaker on his purchase
    # days than it is today in this fixture).
    assert book.fx_choice_effect(**paths) == pytest.approx(-140.0)


def test_excluded_cash_flows_sums_dividends_withholding_interest_and_fees(
    tmp_path,
):
    """Real money, on file, that neither `realized_pnl()` nor
    `unrealized_pnl()` touches at all — deposits/withdrawals excluded
    (an owner's own cash flow, not income or cost)."""
    ct = tmp_path / "cash_transactions.csv"
    pd.DataFrame([
        {"transaction_id": "1", "type": "Dividends", "amount": 100.0,
         "fx_rate_to_base": 1.0},
        {"transaction_id": "2", "type": "Withholding Tax", "amount": -15.0,
         "fx_rate_to_base": 1.0},
        {"transaction_id": "3", "type": "Broker Interest Paid",
         "amount": -2.0, "fx_rate_to_base": 1.0},
        {"transaction_id": "4", "type": "Deposits/Withdrawals",
         "amount": 5000.0, "fx_rate_to_base": 1.0},
    ]).to_csv(ct, index=False)
    bf = tmp_path / "borrow_fees.csv"
    pd.DataFrame([
        {"date": "2026-01-01", "symbol": "X", "borrow_fee": -3.0,
         "fx_rate_to_base": 1.0},
    ]).to_csv(bf, index=False)

    result = book.excluded_cash_flows(cash_transactions_path=ct,
                                      borrow_fees_path=bf)
    # 100 - 15 - 2 - 3 = 80; the 5000 deposit must not appear at all.
    assert result == pytest.approx(80.0)


def test_excluded_cash_flows_refuses_an_unreadable_row(tmp_path):
    """Loud, not coerced — the same shape every other total in this
    module already refuses on, so a bad row never quietly shrinks the
    disclosed amount."""
    ct = tmp_path / "cash_transactions.csv"
    pd.DataFrame([
        {"transaction_id": "1", "type": "Dividends", "amount": "oops",
         "fx_rate_to_base": 1.0},
    ]).to_csv(ct, index=False)
    bf = tmp_path / "borrow_fees.csv"
    pd.DataFrame(columns=["date", "symbol", "borrow_fee",
                          "fx_rate_to_base"]).to_csv(bf, index=False)
    with pytest.raises(ValueError) as e:
        book.excluded_cash_flows(cash_transactions_path=ct, borrow_fees_path=bf)
    assert "1" in str(e.value)


def test_fx_fallback_lots_is_empty_when_every_lot_reconstructs_cleanly(tmp_path):
    """His real book today: both foreign lots reconstruct from their own
    purchases, so nothing should be named here. Proved on a small, clean
    fixture rather than asserted only against the live `data/` files, which
    tests must never read (`tests/conftest.py`)."""
    paths = _pnl_paths(
        tmp_path,
        [{"ticker": "FXGOOD", "instrument_id": "FXGOOD", "shares": 1,
          "cost_basis": 500, "last_price": 600, "currency": "EUR",
          "market_value": 700, "priced_at": "2026-10-02",
          "cost_basis_money": 500.0, "fx_rate_to_base": 1.30, "notes": ""}],
        [("FXGOOD", "stock")],
        tx_rows=[
            {"trade_id": "1", "symbol": "FXGOOD", "currency": "EUR",
             "asset_category": "STK", "quantity": 1, "net_cash": -500,
             "fx_rate_to_base": 1.10, "fifo_pnl": 0.0},
        ],
    )
    assert book.fx_fallback_lots(**paths).empty
