"""
Sync IBKR Flex data into the curated CSV files in data/.

Two CSV outputs:
- `data/positions.csv` is refreshed in-place: shares, cost_basis, last_price,
  market_value, cost_basis_money and fx_rate_to_base are updated from IBKR.
  The user's per-lot `notes` column is preserved row-by-row by ticker. What
  the ticker *means* — name, type, sector, cluster — lives in
  `data/instruments.csv` and is reached via `instrument_id`.
- `data/transactions.csv` is append-only: new trades (by IBKR trade_id) are
  appended; existing rows are never modified. Creates the file on first run.

Match rules (existing ticker -> IBKR row):
- `{CCY}_CASH` matches an FxPosition in that currency.
- `{SYM}_{CCY}` matches an OpenPosition with that exact symbol+currency.
- bare `{SYM}` matches an OpenPosition with that symbol — but only if the
  symbol is unambiguous (a single IBKR row uses it). Multi-currency lots of
  the same symbol require the explicit `_CCY` suffix.

Before overwriting positions.csv, a `.bak` copy is saved. Re-run is safe.

Run from project root:
    venv/bin/python -m src.ibkr_sync           # fetch from IBKR, then sync
    venv/bin/python -m src.ibkr_sync --cached  # re-use latest data/raw/ xml
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path

import pandas as pd

from datetime import date

from src import instruments
from src.ibkr_import import (
    PROJECT_ROOT,
    extract_borrow_fees,
    extract_cash_positions,
    extract_cash_transactions,
    extract_corporate_actions,
    extract_nav_history,
    extract_option_eae,
    unparsed_sections,
    extract_open_positions,
    extract_trades,
    fetch_flex_xml,
    iter_statements,
    load_credentials,
    parse_xml,
    save_raw_xml,
    _latest_cached_xml,
)

DATA_DIR = PROJECT_ROOT / "data"
POSITIONS_PATH = DATA_DIR / "positions.csv"
TRANSACTIONS_PATH = DATA_DIR / "transactions.csv"
CASH_TRANSACTIONS_PATH = DATA_DIR / "cash_transactions.csv"
BORROW_FEES_PATH = DATA_DIR / "borrow_fees.csv"
CORPORATE_ACTIONS_PATH = DATA_DIR / "corporate_actions.csv"
OPTION_EVENTS_PATH = DATA_DIR / "option_events.csv"
NAV_HISTORY_PATH = DATA_DIR / "nav_history.csv"

# Columns the user curates by hand — preserved across sync.
#
# Only `notes` remains. name, kind, direct_sector and cluster used to live here
# too, but they describe the INSTRUMENT, and instruments.csv now owns those
# (Phase 2d). Keeping both copies had already produced disagreements, always
# with positions.csv holding the worse version: a name of
# 'EUR Cash (260.72 EUR)' with a balance frozen into it, and kind='direct'
# where the type is really 'cash'. `notes` stays because it is a per-LOT fact
# ('EUR lot', 'ADR lot') that a per-instrument table cannot express.
USER_CURATED_COLS: tuple[str, ...] = ("notes",)
def _as_of(row) -> str:
    """The date IBKR says the price is FOR, not the date we fetched it.

    The Flex statement stamps every position with `reportDate` — the session
    the mark belongs to. This used to write `date.today()`, so a file pulled
    mid-session on Friday claimed Friday's prices when it actually carried
    Thursday's close. That overstated freshness by a full session and is
    exactly what `priced_at` exists to prevent (found by review 2026-08-15).

    Blank when IBKR gives no date — `instruments.price_warning()` then says
    "prices carry no date", which is the honest answer. Falling back to today
    would reintroduce the bug quietly.
    """
    d = str(row.get("report_date", "") or "").strip()
    try:
        # A length check is not enough: "not-a-date" is exactly 10 characters.
        return d if date.fromisoformat(d).isoformat() == d else ""
    except (ValueError, TypeError):
        return ""


# Columns refreshed from IBKR each sync. `priced_at` rides with them because
# a price without a timestamp cannot be told apart from a stale one — IBKR is
# the only price source here, so its silence must be visible (P2).
IBKR_REFRESHED_COLS: tuple[str, ...] = (
    "shares", "cost_basis", "last_price", "market_value", "priced_at",
    "cost_basis_money", "fx_rate_to_base",
)
# Full positions.csv column order (kept stable so diffs stay clean).
# What a ticker MEANS is in instruments.csv; this file holds only what is true
# of one broker lot right now.
POSITIONS_COLS: tuple[str, ...] = (
    "ticker", "instrument_id", "shares", "cost_basis", "last_price",
    "currency", "market_value", "priced_at", "cost_basis_money",
    "fx_rate_to_base", "notes",
)
# `currency` is the currency `cost_basis` and `last_price` are quoted in;
# `market_value` is always the base currency, USD. Added 2026-09-27 because a
# price compared against the wrong currency is wrong in whichever direction
# hurts: Samsung marks at ~272,500 KRW against a ~$196 share.
#
# ⚠️ This comment used to end "...because a level is a bare number and
# levels.csv has no currency of its own". That stopped being true on
# 2026-09-29, when levels.csv gained a `currency` column — and claims.csv
# gained one on 2026-10-01. All three tables now say what their numbers mean.
# Do NOT restore the old "a level can only mean USD" rule in
# levels._price_for(): it would re-break l0029, the live IMAE hard stop.
#
# `cost_basis_money` and `fx_rate_to_base`, added for P&L (2026-10-08).
# `cost_basis` (above) is IBKR's `costBasisPrice` — a PRICE PER SHARE, in the
# lot's own `currency`, and it already includes neither commission nor a
# split adjustment reliably once a lot predates the broker's own trade
# window. `cost_basis_money` is the other IBKR field, `costBasisMoney` — the
# lot's TOTAL cost, same currency, commission and every split already
# folded in — and the two are named nothing alike on purpose: a name that
# differed only by a suffix (`cost_basis_total`, say) is exactly the kind of
# pair a tired re-read mixes up, which is what made the original per-share
# column dangerous to build a dollar figure from in the first place.
# `fx_rate_to_base` is the SAME quantity `transactions.csv` already carries
# per trade, here instead for the position itself, stamped at the report
# date — book.lot_cost_usd() uses a lot's own PURCHASE-date rate from
# `transactions.csv` whenever it can reconstruct one, and this report-date
# rate only when it cannot, flagging every lot where that happened (see
# that function's own docstring for why the two give different answers).


# ─── Ticker matching ─────────────────────────────────────────────────────


def _match_ticker_to_ibkr(
    ticker: str,
    securities: pd.DataFrame,
    cash: pd.DataFrame,
) -> tuple[str, pd.Series | None]:
    """
    Resolve an existing positions.csv ticker to an IBKR row.

    Returns (kind, row) where kind is one of:
      "cash"      — matched a cash row in `cash`
      "security"  — matched exactly one row in `securities`
      "missing"   — no match found (likely sold / withdrawn)
      "ambiguous" — bare ticker matched >1 IBKR row; needs `_CCY` suffix

    `row` is the matched Series, or None for missing/ambiguous.
    """
    if ticker.endswith("_CASH"):
        ccy = ticker[: -len("_CASH")]
        match = cash[cash["currency"] == ccy]
        if len(match) == 1:
            return "cash", match.iloc[0]
        return "missing", None

    # Try SYM_CCY pattern first (more specific).
    if "_" in ticker:
        sym, suffix = ticker.rsplit("_", 1)
        match = securities[
            (securities["symbol"] == sym) & (securities["currency"] == suffix)
        ]
        if len(match) == 1:
            return "security", match.iloc[0]

    # Fall back to bare symbol — only unambiguous if exactly one IBKR row.
    match = securities[securities["symbol"] == ticker]
    if len(match) == 1:
        return "security", match.iloc[0]
    if len(match) > 1:
        return "ambiguous", None
    return "missing", None


def _derive_new_ticker(ibkr_row: pd.Series, securities: pd.DataFrame) -> str:
    """
    Pick a ticker for an IBKR row that wasn't matched to any existing row.

    Bare symbol if unique in IBKR data; else `{SYM}_{CCY}` to disambiguate.
    """
    symbol = ibkr_row["symbol"]
    currency = ibkr_row["currency"]
    same_symbol = securities[securities["symbol"] == symbol]
    if len(same_symbol) > 1:
        return f"{symbol}_{currency}"
    return symbol


# ─── Positions sync ──────────────────────────────────────────────────────


def _refresh_security_row(
    existing: pd.Series, ibkr_row: pd.Series
) -> pd.Series:
    """Return a copy of `existing` with IBKR price/qty columns refreshed."""
    out = existing.copy()
    out["shares"] = ibkr_row["position"]
    out["cost_basis"] = ibkr_row["cost_basis_price"]
    out["last_price"] = ibkr_row["mark_price"]
    # The currency `cost_basis` and `last_price` are quoted in. `market_value`
    # is always USD. Without this, a won-priced mark and a dollar-priced level
    # were compared as if they were the same number (audit 2026-09-27).
    out["currency"] = ibkr_row["currency"]
    # IBKR positionValue is in native currency; multiply by FX to get base (USD).
    out["market_value"] = round(
        ibkr_row["position_value"] * ibkr_row["fx_rate_to_base"], 2
    )
    out["priced_at"] = _as_of(ibkr_row)
    # The lot's TOTAL cost (commission and splits already folded in, same
    # currency as `cost_basis` above) and the rate IBKR used to translate
    # this position to base today. `extract_open_positions()` already turns
    # a missing attribute into NaN rather than raising, so an older raw file
    # that never carried either field flows through as a blank cell here,
    # never a crash — `book.lot_cost_usd()` is what then has to decide
    # whether a lot's cost can be established at all.
    out["cost_basis_money"] = ibkr_row["cost_basis_money"]
    out["fx_rate_to_base"] = ibkr_row["fx_rate_to_base"]
    return out


def _refresh_cash_row(existing: pd.Series, cash_row: pd.Series) -> pd.Series:
    """Return a copy of `existing` cash row with USD-translated value refreshed."""
    out = existing.copy()
    value = round(cash_row["value_in_base"], 2)
    out["shares"] = 1
    out["cost_basis"] = value
    out["last_price"] = value
    out["market_value"] = value
    # Already FX-translated above, so every number on a cash row is USD even
    # when the balance itself is yen.
    out["currency"] = "USD"
    out["priced_at"] = _as_of(cash_row)
    # 🚨 Fix, review 2026-10-08 (M6). This used to write `cost_basis_money
    # = value` — asserting a cash balance's "cost" equals its current
    # value, i.e. exactly zero profit or loss, which is simply untrue: IBKR
    # reports a real `unrealizedPL` on every FX position (his JPY balance
    # carries one, non-zero) that this invented figure silently overwrote
    # with a confident zero. `book.py` already excludes `instrument_type in
    # CASH_KINDS` before `cost_basis_money` is ever read, so leaving it
    # blank changes no figure on screen today — it only stops the column
    # from asserting a fact nobody computed. `fx_rate_to_base` stays 1.0:
    # that one IS true, trivially — this row's own `currency` is already
    # forced to "USD" above, so the rate translating it to base is exactly
    # 1, not an invented fact.
    out["cost_basis_money"] = ""
    out["fx_rate_to_base"] = 1.0
    return out


def _new_security_row(
    ibkr_row: pd.Series, securities: pd.DataFrame
) -> dict:
    """Build a new positions.csv row for an unmatched IBKR security."""
    ticker = _derive_new_ticker(ibkr_row, securities)
    return {
        "ticker": ticker,
        "instrument_id": instruments.resolve(ticker),
        "name": ibkr_row["description"],
        "shares": ibkr_row["position"],
        "cost_basis": ibkr_row["cost_basis_price"],
        "last_price": ibkr_row["mark_price"],
        "currency": ibkr_row["currency"],
        "market_value": round(
            ibkr_row["position_value"] * ibkr_row["fx_rate_to_base"], 2
        ),
        "priced_at": _as_of(ibkr_row),
        "cost_basis_money": ibkr_row["cost_basis_money"],
        "fx_rate_to_base": ibkr_row["fx_rate_to_base"],
        # The lot is recorded now; what it MEANS is filled in later, in
        # instruments.csv. Never let missing metadata stop a holding landing.
        "notes": "AUTO-ADDED by ibkr_sync — add it to instruments.csv",
    }


def _new_cash_row(cash_row: pd.Series) -> dict:
    """Build a new positions.csv row for an unmatched IBKR cash currency."""
    ccy = cash_row["currency"]
    value = round(cash_row["value_in_base"], 2)
    return {
        "ticker": f"{ccy}_CASH",
        "instrument_id": f"{ccy}_CASH",
        "shares": 1,
        "cost_basis": value,
        "last_price": value,
        "currency": "USD",
        "market_value": value,
        "priced_at": _as_of(cash_row),
        # M6 (review, 2026-10-08): see `_refresh_cash_row()`'s own comment
        # — a cash balance has no acquisition cost, and asserting one here
        # equal to its own current value quietly overwrites the broker's
        # real (non-zero) unrealized FX gain/loss with an invented zero.
        "cost_basis_money": "",
        "fx_rate_to_base": 1.0,
        "notes": "FX-translated" if ccy != "USD" else "",
    }


def sync_positions(
    existing: pd.DataFrame,
    ibkr_securities: pd.DataFrame,
    ibkr_cash: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    """
    Merge IBKR data into the existing positions DataFrame.

    Returns (new_df, diff) where diff has lists of tickers in each bucket:
        updated, added, removed, ambiguous
    """
    updated_rows: list[pd.Series] = []
    diff = {
        "updated": [],
        "added": [],
        "removed": [],
        "ambiguous": [],
    }
    matched_cash: set[str] = set()
    matched_sec: set[tuple[str, str]] = set()  # (symbol, currency)

    for _, existing_row in existing.iterrows():
        kind, ibkr_row = _match_ticker_to_ibkr(
            existing_row["ticker"], ibkr_securities, ibkr_cash
        )
        if kind == "cash":
            updated_rows.append(_refresh_cash_row(existing_row, ibkr_row))
            diff["updated"].append(existing_row["ticker"])
            matched_cash.add(ibkr_row["currency"])
        elif kind == "security":
            updated_rows.append(_refresh_security_row(existing_row, ibkr_row))
            diff["updated"].append(existing_row["ticker"])
            matched_sec.add((ibkr_row["symbol"], ibkr_row["currency"]))
        elif kind == "ambiguous":
            diff["ambiguous"].append(existing_row["ticker"])
            updated_rows.append(existing_row)  # keep as-is; user must fix
        else:
            diff["removed"].append(existing_row["ticker"])

    # Add new IBKR securities not matched to any existing ticker.
    for _, ibkr_row in ibkr_securities.iterrows():
        key = (ibkr_row["symbol"], ibkr_row["currency"])
        if key in matched_sec:
            continue
        new = _new_security_row(ibkr_row, ibkr_securities)
        updated_rows.append(pd.Series(new))
        diff["added"].append(new["ticker"])

    # Add new IBKR cash currencies not matched to any existing ticker.
    for _, cash_row in ibkr_cash.iterrows():
        if cash_row["currency"] in matched_cash:
            continue
        new = _new_cash_row(cash_row)
        updated_rows.append(pd.Series(new))
        diff["added"].append(new["ticker"])

    new_df = pd.DataFrame(updated_rows)
    # Backfill columns a pre-Phase-2c positions.csv will not have. Derived,
    # never invented: instrument_id comes from the ticker by the one rule in
    # instruments.resolve(); priced_at is blank rather than today's date,
    # because claiming a price was checked today when it was not is exactly
    # the silent-staleness failure this column exists to prevent (P2).
    if "instrument_id" not in new_df.columns:
        new_df["instrument_id"] = new_df["ticker"].map(instruments.resolve)
    else:
        new_df["instrument_id"] = new_df["instrument_id"].fillna(
            new_df["ticker"].map(instruments.resolve)
        )
    if "priced_at" not in new_df.columns:
        new_df["priced_at"] = ""
    # Blank, never "USD": a row whose currency was never recorded is unknown,
    # and levels._price_for() must refuse it rather than assume the base.
    if "currency" not in new_df.columns:
        new_df["currency"] = ""
    else:
        new_df["currency"] = new_df["currency"].fillna("")

    # Blank, never 0 or 1: a lot the sync has not touched since these two
    # columns landed (the "ambiguous, kept as-is" branch above is the one
    # real way that happens) must read as "no cost on file", the same
    # un-established state `book.lot_cost_usd()` already gives a lot IBKR
    # itself sent with the attribute missing — never a number invented here
    # to fill the cell.
    for col in ("cost_basis_money", "fx_rate_to_base"):
        if col not in new_df.columns:
            new_df[col] = ""
        else:
            new_df[col] = new_df[col].fillna("")

    new_df = new_df[list(POSITIONS_COLS)]
    return new_df, diff


# ─── Transactions sync ───────────────────────────────────────────────────


def sync_transactions(
    new_trades: pd.DataFrame, existing_path: Path = TRANSACTIONS_PATH
) -> tuple[pd.DataFrame, int]:
    """
    Append new trades to the transactions log, deduped on trade_id.

    On first run (no file exists yet), seeds with all `new_trades` rows.
    Returns (merged_df, n_added). `n_added` counts only rows that were
    actually new (i.e., trade_ids not already in the file).
    """
    if existing_path.exists():
        existing = _read_append_only(existing_path)
    else:
        existing = pd.DataFrame(columns=new_trades.columns)

    new_trades = new_trades.copy()
    new_trades["trade_id"] = new_trades["trade_id"].astype(str)

    known_ids = set(existing["trade_id"].astype(str)) if len(existing) else set()
    new_only = new_trades[~new_trades["trade_id"].isin(known_ids)]
    merged = pd.concat([existing, new_only], ignore_index=True)
    merged = merged.sort_values(["trade_date", "trade_id"]).reset_index(drop=True)
    return merged, len(new_only)


def _read_append_only(path: Path) -> pd.DataFrame:
    """
    Read an existing broker record as text, never as numbers.

    These files are append-only history: a sync adds rows and writes the
    whole file back. If pandas is left to infer types, a column holding
    integer IDs with any blank becomes float64, and the round-trip rewrites
    every value — `172878813` silently becomes `172878813.0`. That is a
    rewrite of history, which is exactly what append-only forbids. Reading
    as text means untouched rows go back out byte-identical.
    """
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def sync_cash_transactions(
    new_txs: pd.DataFrame, existing_path: Path = CASH_TRANSACTIONS_PATH
) -> tuple[pd.DataFrame, int]:
    """Append-only sync for cash transactions, deduped on transaction_id."""
    if existing_path.exists():
        existing = _read_append_only(existing_path)
    else:
        existing = pd.DataFrame(columns=new_txs.columns)

    new_txs = new_txs.copy()
    new_txs["transaction_id"] = new_txs["transaction_id"].astype(str)
    known = set(existing["transaction_id"].astype(str)) if len(existing) else set()
    new_only = new_txs[~new_txs["transaction_id"].isin(known)]
    merged = pd.concat([existing, new_only], ignore_index=True)
    merged = merged.sort_values(["date", "transaction_id"]).reset_index(drop=True)
    return merged, len(new_only)


def sync_corporate_actions(
    new_actions: pd.DataFrame, existing_path: Path = CORPORATE_ACTIONS_PATH
) -> tuple[pd.DataFrame, int]:
    """
    Append-only sync for splits, mergers and spin-offs, deduped on
    transaction_id.

    A corporate action changes the share count without a trade. Until this
    landed on 2026-09-29 the only trace was a position that did not match its
    own trade history — which is exactly how the XLK 2-for-1 of 2025-12-05
    spent weeks filed as an unexplained bug.
    """
    # IBKR sends `<CorporateActions/>` with no children once the last one ages
    # out of the rolling 365-day window, and extract_* then returns a frame
    # with ZERO columns — so subscripting the key raised KeyError and killed
    # the sync AFTER five tables had already been written. The splits are
    # dated 2025-11/12, so this was due around 2026-12-05.
    if "transaction_id" not in new_actions.columns:
        if existing_path.exists():
            return _read_append_only(existing_path), 0
        return pd.DataFrame(), 0

    if existing_path.exists():
        existing = _read_append_only(existing_path)
    else:
        existing = pd.DataFrame(columns=new_actions.columns)

    new_actions = new_actions.copy()
    new_actions["transaction_id"] = new_actions["transaction_id"].astype(str)
    known = set(existing["transaction_id"].astype(str)) if len(existing) else set()
    new_only = new_actions[~new_actions["transaction_id"].isin(known)]
    merged = pd.concat([existing, new_only], ignore_index=True)
    merged = merged.sort_values(["report_date", "transaction_id"]).reset_index(drop=True)
    return merged, len(new_only)


def sync_option_events(
    new_events: pd.DataFrame, existing_path: Path = OPTION_EVENTS_PATH
) -> tuple[pd.DataFrame, int]:
    """
    Append-only sync for option and future exercise / assignment / expiry,
    deduped on trade_id.

    ⚠️ **This table OVERLAPS `transactions.csv`. Never sum the P&L of both.**

    Found by review 2026-09-29, after this docstring claimed the opposite: a
    row here usually shares a `trade_id` with a transaction and carries the
    SAME realized P&L, so adding the two double-counts. The crude future's
    realized gain was in `transactions.csv` all along.

    What this table uniquely holds is the CONTRACT — strike, expiry,
    put/call, mark price, cost basis, none of which `transactions.csv`
    records — and one row genuinely absent from it: the $1,200 cash
    settlement of the in-the-money VIX 20 call (trade_id 4970750659,
    quantity 0).
    """
    # IBKR sends `<CorporateActions/>` with no children once the last one ages
    # out of the rolling 365-day window, and extract_* then returns a frame
    # with ZERO columns — so subscripting the key raised KeyError and killed
    # the sync AFTER five tables had already been written. The splits are
    # dated 2025-11/12, so this was due around 2026-12-05.
    if "trade_id" not in new_events.columns:
        if existing_path.exists():
            return _read_append_only(existing_path), 0
        return pd.DataFrame(), 0

    if existing_path.exists():
        existing = _read_append_only(existing_path)
    else:
        existing = pd.DataFrame(columns=new_events.columns)

    new_events = new_events.copy()
    new_events["trade_id"] = new_events["trade_id"].astype(str)
    known = set(existing["trade_id"].astype(str)) if len(existing) else set()
    new_only = new_events[~new_events["trade_id"].isin(known)]
    merged = pd.concat([existing, new_only], ignore_index=True)
    merged = merged.sort_values(["date", "trade_id"]).reset_index(drop=True)
    return merged, len(new_only)


# Every table the sync owns, and the key that identifies a row in each.
# Read by check_broker_table() and by the commit guard, so all three
# enforcement points agree on what "the same row" means. Adding a table here
# is what puts it under the append-only guarantee — do it in the same commit
# as the sync function, never later.
BROKER_TABLES = {
    "data/transactions.csv": ["trade_id"],
    "data/cash_transactions.csv": ["transaction_id"],
    "data/borrow_fees.csv": ["date", "symbol"],
    "data/nav_history.csv": ["date"],
    "data/corporate_actions.csv": ["transaction_id"],
    "data/option_events.csv": ["trade_id"],
}


def check_broker_table(
    old: pd.DataFrame, new: pd.DataFrame, key: list[str], name: str
) -> None:
    """
    Raise unless `new` only ADDS to `old`. The guarantee every broker table
    is supposed to give, checked rather than assumed.

    Three ways a broker record can be damaged, and this catches all three:
    a row silently changes (the `172878813` -> `172878813.0` rewrite that hit
    285 dividend rows), a row disappears (the 60 NAV days lost to the broker's
    365-day window), or a key is duplicated so two rows claim to be the same
    fact. New rows are always allowed — that is what a sync is for.

    Only columns present in BOTH frames are compared, so adding a column stays
    possible; without that the guard would block its own next migration.
    """
    if new.empty:
        return
    missing = [k for k in key if k not in new.columns]
    if missing:
        raise ValueError(f"{name}: missing key column(s) {missing}.")

    dupes = new[new.duplicated(subset=key, keep=False)]
    if len(dupes):
        shown = dupes[key].drop_duplicates().head(5).to_dict("records")
        raise ValueError(
            f"{name}: {len(dupes)} rows share a key that must be unique — e.g. "
            f"{shown}. Two rows claiming the same {'+'.join(key)} means one of "
            f"them is wrong; remove the duplicate rather than renaming it."
        )
    if old.empty:
        return

    def _index(df: pd.DataFrame) -> pd.DataFrame:
        return df.set_index([df[k].astype(str) for k in key])

    o, n = _index(old), _index(new)
    # Hoisted. Rebuilding this set inside the comprehension made the check
    # quadratic: 342 rows took 0.26s, 3,420 took 5.67s — and nav_history gains
    # a row every calendar day whether or not he trades, so it gets there on
    # its own. The guard runs on every commit touching any data table.
    present = set(n.index)
    gone = [i for i in o.index if i not in present]
    if gone:
        raise ValueError(
            f"{name}: {len(gone)} row(s) present in the last commit are missing "
            f"— e.g. {gone[:5]}. This file may only gain rows. If a sync "
            f"produced this, the broker has stopped reporting those rows; they "
            f"must be carried forward, not dropped."
        )

    shared = [c for c in old.columns if c in new.columns and c not in key]
    if not shared:
        return

    # Compare column by column rather than row by row. The per-row .loc lookup
    # was the other half of the quadratic cost, and the message it produces is
    # identical — the first difference found, by row then column.
    dup_keys = o.index[o.index.duplicated(keep=False)]
    common = o.index.difference(dup_keys)           # duplicate keys in the OLD
    if not len(common):                             # file cannot be compared
        return
    a_all = o.loc[common, shared].astype(str)
    b_all = n.loc[common, shared].astype(str)
    for c in shared:
        differs = a_all[c].ne(b_all[c])
        if differs.any():
            i = differs.idxmax()
            raise ValueError(
                f"{name} row {i}: {c} changed {a_all[c][i]!r} -> {b_all[c][i]!r}. "
                f"This is the broker's record of what happened; it is "
                f"permanent. Add a correcting row instead of editing this one."
            )


def sync_borrow_fees(
    new_fees: pd.DataFrame, existing_path: Path = BORROW_FEES_PATH
) -> tuple[pd.DataFrame, int]:
    """Append-only sync for borrow-fee accruals, deduped on (date, symbol)."""
    if existing_path.exists():
        existing = _read_append_only(existing_path)
    else:
        existing = pd.DataFrame(columns=new_fees.columns)

    if len(existing):
        key_existing = set(zip(existing["date"], existing["symbol"]))
        new_only = new_fees[
            ~new_fees.apply(lambda r: (r["date"], r["symbol"]) in key_existing, axis=1)
        ]
    else:
        new_only = new_fees
    merged = pd.concat([existing, new_only], ignore_index=True)
    merged = merged.sort_values(["date", "symbol"]).reset_index(drop=True)
    return merged, len(new_only)


def sync_nav_history(
    nav: pd.DataFrame,
    cash_txs: pd.DataFrame,
    existing_path: Path = NAV_HISTORY_PATH,
) -> tuple[pd.DataFrame, int]:
    """
    Merge the statement's daily NAV into the existing file, keeping every day.

    IBKR is authoritative only for the days it still sends. The Flex query is
    `Last365CalendarDays`, a window that slides forward, so a day older than
    the window is not "changed to nothing" — it simply stops being mentioned.
    This function used to start from the statement and overwrite the file,
    which read that silence as deletion and dropped the account's oldest days
    one at a time: 60 of them (2025-05-15 to 2025-08-06) before it was caught.

    So: the statement wins for every date it covers — IBKR may legitimately
    restate a day — and dates it no longer mentions are carried forward
    untouched. Deposits and withdrawals come from CashTransactions where
    type='Deposits/Withdrawals'; IBKR records a deposit as a positive `amount`
    and a withdrawal as negative. The user's `notes` are preserved by date.
    """
    out = nav.copy()
    out["deposit"] = 0.0
    out["withdrawal"] = 0.0
    out["notes"] = ""

    flows = cash_txs[cash_txs["type"] == "Deposits/Withdrawals"]
    for _, row in flows.iterrows():
        amount = float(row["amount"])
        mask = out["date"] == row["date"]
        if not mask.any():
            continue
        if amount >= 0:
            out.loc[mask, "deposit"] = out.loc[mask, "deposit"] + amount
        else:
            out.loc[mask, "withdrawal"] = (
                out.loc[mask, "withdrawal"] + abs(amount)
            )

    if existing_path.exists():
        # Read as text, no comment= : a '#' typed in a note would otherwise
        # truncate the row on read — the trap that once corrupted claims.csv.
        old = _read_append_only(existing_path)
        if "date" in old.columns:
            if "notes" in old.columns:
                kept = dict(zip(old["date"], old["notes"]))
                out["notes"] = out["date"].map(kept).fillna("")
            # Days the window no longer reaches: carry them forward verbatim.
            carried = old[~old["date"].isin(set(out["date"]))]
            if len(carried):
                out = pd.concat(
                    [out, carried.reindex(columns=out.columns)], ignore_index=True
                )
            out = out.sort_values("date").reset_index(drop=True)

    n_rows = len(out)
    return out, n_rows


# ─── File IO ─────────────────────────────────────────────────────────────


def _backup(path: Path) -> Path | None:
    """Copy `path` to `path.bak` before overwriting. Returns bak path."""
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    return bak


def _report_unexplained() -> None:
    """Name anything traded recently that no claim explains.

    Yaron, 2026-08-15: "let's say I bought a few days ago something and it
    doesn't have anything, and then you do the synchronisation and you see
    something has been bought and there is no mention about it. Then your
    responsibility is to flag me up."

    Prints only. It never writes a claim — inventing one would corrupt the exact
    record the store exists to protect (REBUILD.md §8.5). The most it does is
    name the ticker and the command that captures it.
    """
    try:
        from src import holding

        gaps = holding.unexplained()
    except Exception as e:                 # never let a report break a sync
        print(f"\n  (could not check for unexplained trades — {type(e).__name__}: {e})")
        return
    if gaps.empty:
        return
    print("\n⚠ Traded recently with nothing on record explaining why:")
    for _, r in gaps.iterrows():
        # shares, not market_value: a held position priced at 0 is not a
        # closed one, and saying so would be a wrong answer about his money.
        held = ("now closed" if not r["shares"]
                else f"${r['market_value']:,.0f}" if r["market_value"]
                else "held, no price")
        print(f"    {r['instrument_id']:<8} {r['last_trade']}  {held:>11}"
              f"   ->  /note {r['instrument_id']}")


def _format_diff(diff: dict) -> str:
    """Pretty-print the sync diff."""
    lines = []
    lines.append(f"  Updated:   {len(diff['updated'])} tickers")
    if diff["added"]:
        lines.append(f"  Added:     {len(diff['added'])} -> {diff['added']}")
    if diff["removed"]:
        lines.append(
            f"  Removed:   {len(diff['removed'])} -> {diff['removed']}  "
            "(no longer in IBKR; was the position closed?)"
        )
    if diff["ambiguous"]:
        lines.append(
            f"  Ambiguous: {len(diff['ambiguous'])} -> {diff['ambiguous']}  "
            "(bare ticker matched multiple IBKR rows; rename to SYM_CCY)"
        )
    return "\n".join(lines)


# ─── CLI ─────────────────────────────────────────────────────────────────


def _get_xml_bytes(use_cached: bool) -> bytes:
    """Fetch fresh XML from IBKR, or load latest from data/raw/."""
    if use_cached:
        path = _latest_cached_xml()
        if path is None:
            raise RuntimeError(
                "No cached XML found. Run without --cached first."
            )
        print(f"Loading cached XML: {path.relative_to(PROJECT_ROOT)}")
        return path.read_bytes()

    print("Loading credentials from .env...")
    token, query_id = load_credentials()
    print(f"  Query ID: {query_id}")
    print("\nFetching from IBKR Flex Web Service (this can take 5-30 sec)...")
    xml_bytes = fetch_flex_xml(token, query_id)
    print(f"  Received {len(xml_bytes):,} bytes")
    print("\nSaving raw XML...")
    path = save_raw_xml(xml_bytes)
    print(f"  -> {path.relative_to(PROJECT_ROOT)}")
    return xml_bytes


def unreconciled_shares(
    transactions: pd.DataFrame,
    positions: pd.DataFrame,
    corporate_actions: pd.DataFrame,
) -> pd.DataFrame:
    """
    Symbols where shares held do not equal net traded plus corporate actions.

    Visible, never blocking — the same contract as `instruments.gaps()`. A
    mismatch here is a question for Yaron, not a reason to refuse a commit:
    the broker's Flex window is a rolling ~365 days, so anything older than
    that is simply not re-suppliable and no amount of syncing will close it.

    Two causes, and the `why` column separates them:

    - `consistent with pre-log holding` — the running share balance goes
      negative, so shares must have existed before `transactions.csv` begins.
      An INFERENCE, not a determination: a missing corporate action looks
      identical from the log. Most rows here are genuinely pre-log.
    - `unexplained` — the balance never goes negative, so the log should be
      complete, and it still does not add up. This is the one worth reading.
      One today: GRNY — 50 shares bought 2025-08-01, 250 held, and no
      trade or corporate action anywhere accounts for the other 200. The
      broker's window has rolled past whatever happened, so it cannot be
      recovered by syncing; it needs Yaron.

    Adding corporate actions on 2026-09-29 removed NFLX, XLK and XLU from
    this list — NFLX had been reporting -9 shares, which is not a quantity a
    portfolio can have.
    """
    # Numbers arrive as STRINGS here. `_read_append_only()` reads every broker
    # table with dtype=str to keep ids exact, so the sync hands this function
    # text while a test fixture hands it floats — which is why the whole suite
    # passed and `python -m src.ibkr_sync --cached` raised
    # "'>=' not supported between instances of 'str' and 'float'".
    transactions = transactions.copy()
    transactions["quantity"] = pd.to_numeric(transactions["quantity"],
                                             errors="coerce")
    positions = positions.copy()
    positions["shares"] = pd.to_numeric(positions["shares"], errors="coerce")
    if len(corporate_actions):
        corporate_actions = corporate_actions.copy()
        corporate_actions["quantity"] = pd.to_numeric(
            corporate_actions["quantity"], errors="coerce")

    stk = transactions[transactions["asset_category"] == "STK"].copy()
    # One instrument, many broker lots: ASML is traded as `ASML` and held as
    # `ASML_USD` + `ASML_EUR`. Comparing the raw ticker makes the same 2.5
    # shares look like three separate discrepancies.
    stk["key"] = [instruments.resolve(x) for x in stk["symbol"]]
    stk = stk.sort_values(["key", "trade_date"])
    pos = positions.copy()
    pos["key"] = [instruments.resolve(x) for x in pos["ticker"]]

    net = stk.groupby("key")["quantity"].sum()
    held = pos.groupby("key")["shares"].sum()
    if len(corporate_actions):
        ca = corporate_actions.copy()
        ca["key"] = [instruments.resolve(x) for x in ca["symbol"]]
        acts = ca.groupby("key")["quantity"].sum()
    else:
        acts = pd.Series(dtype=float)

    # Shares that existed before `transactions.csv` begins, two ways: the
    # running balance goes negative (sold what was never bought here), or the
    # symbol has no trades in the log at all — a pre-log holding, a transfer
    # in, or a cash balance that arrived as a deposit.
    traded_keys = set(stk["key"])
    # A quantity that will not parse is not a reconciled zero — say so.
    unreadable = set(stk.loc[stk["quantity"].isna(), "key"])
    # A share count that will not parse is not "held 0" either. groupby.sum()
    # drops NaN silently, so a blank read as zero — and where that happened to
    # balance, the row disappeared from the report altogether.
    unreadable_held = set(pos.loc[pos["shares"].isna(), "key"])
    # A split whose quantity will not parse was summed as 0.0, so the row read
    # "corporate_actions 0.0, gap +10" — a confident number built from a value
    # the code could not read. Found by review 2026-10-01.
    unreadable_act = (set(ca.loc[ca["quantity"].isna(), "key"])
                      if len(corporate_actions) else set())
    # ⚠️ A MISSING corporate action and a position bought before the log are
    # INDISTINGUISHABLE from the trade log alone, and this function does not
    # pretend otherwise.
    #
    # Review raised the right worry on 2026-09-29: XLK's real history runs
    # +15, -5, -12, +20 — cumulative 15, 10, -2, 18 — and dips negative only
    # because its 2-for-1 split is not in the running balance. So if that
    # split went unrecorded, XLK lands in this bucket and reads as "expected".
    #
    # The obvious fix — "goes negative at the FIRST trade means pre-log; dips
    # later means something is missing" — was tried against the live data and
    # is WRONG. META (+1, -6), MSTR (+5, -15, +2, -2) and SLV (+30, -25, -30)
    # all dip later and are all plainly pre-log holdings. Both stories need
    # the same thing — shares from before the log — and the log cannot say
    # which.
    #
    # So the label below is an INFERENCE, not a determination. What actually
    # defends against a missing split is that CorporateActions is parsed on
    # every sync now; this report is the backstop, not the guard.
    goes_negative = {
        k for k, g in stk.groupby("key")
        if not (g["quantity"].cumsum() >= -1e-9).all()
    }

    rows = []
    for key in sorted(set(net.index) | set(held.index) | set(acts.index)):
        expected = float(net.get(key, 0.0)) + float(acts.get(key, 0.0))
        actual = float(held.get(key, 0.0))
        # ⚠️ The unreadable checks run BEFORE the balance check, not after.
        # groupby().sum() has already skipped every NaN, so a row the code
        # could not read would balance to zero whenever its siblings happened
        # to add up — and vanish from the report entirely. A destroyed money
        # row reading as a clean book. Found by review 2026-10-01.
        #
        # Nothing numeric is reported for these: a gap computed from a value
        # we could not read is a confident number about nothing.
        bad = next((label for group, label in (
            (unreadable, "unreadable quantity"),
            (unreadable_held, "unreadable share count"),
            (unreadable_act, "unreadable corporate action"),
        ) if key in group), None)
        if bad:
            rows.append({"symbol": key, "traded": float("nan"),
                         "corporate_actions": float("nan"),
                         "expected": float("nan"), "held": float("nan"),
                         "gap": float("nan"), "why": bad})
            continue

        if abs(expected - actual) <= 1e-6:
            continue

        before_log = key in goes_negative or key not in traded_keys
        rows.append({
            "symbol": key,
            "traded": float(net.get(key, 0.0)),
            "corporate_actions": float(acts.get(key, 0.0)),
            "expected": expected,
            "held": actual,
            "gap": actual - expected,
            "why": "consistent with pre-log holding" if before_log else "unexplained",
        })
    return pd.DataFrame(rows, columns=[
        "symbol", "traded", "corporate_actions", "expected", "held", "gap", "why"
    ])


def _report_broker_coverage(stmt) -> None:
    """
    Print what the broker sent that nothing here reads.

    His question, 2026-09-29: am I asking IBKR for too much or too little?
    It stays answerable only if the answer is printed where he already looks.
    The first run said 12,592 of 13,586 records — 93% of a 2.2 MB download —
    are read by nothing, while CorporateActions sat unparsed beside them.

    Reporting only. Turning a section off is a manual edit to the Flex query
    at IBKR, and it is one-way: history for a section he stops requesting may
    not be re-suppliable.
    """
    rows = unparsed_sections(stmt)
    unread = [r for r in rows if not r["parsed"] and r["records"]]
    if not unread:
        return
    total = sum(r["records"] for r in rows) or 1
    waste = sum(r["records"] for r in unread)
    print(
        f"\nBroker data nothing reads: {waste:,} of {total:,} records "
        f"({waste / total:.0%} of the download)"
    )
    for r in unread[:5]:
        print(f"  {r['records']:>7,}  {r['section']}")
    if len(unread) > 5:
        print(f"  ... and {len(unread) - 5} more sections")


def _load_build_page():
    """Import `ops/build_page.py` by file path.

    `ops/` is deliberately not a package (ruling, Task 6 brief), so a sibling
    script is loaded this way — the same mechanism `ops/refresh_market.py`
    and `ops/data_gaps.py` already use to load one.
    """
    spec = importlib.util.spec_from_file_location(
        "build_page", PROJECT_ROOT / "ops" / "build_page.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rebuild_page() -> None:
    """D10: he can never be looking at a page older than his data.

    Not on a schedule — a background job that fails quietly belongs in
    Phase 5. So the page is rebuilt right here, at the end of every sync.

    Wrapped on purpose: the sync's job is his data, and a renderer raising
    must never turn a successful sync into a failed command. If the rebuild
    fails, the sync still stands — the message below names which of the two
    happened (P2), so the fix (re-run `ops/build_page.py` by hand) is never
    confused with a broken sync.
    """
    try:
        _load_build_page().main(["--no-open"])
    except Exception as e:
        print(
            f"\n⚠ The sync above succeeded; the page did not rebuild — "
            f"{type(e).__name__}: {e}\n"
            f"  Run `venv/bin/python ops/build_page.py` by hand once that's fixed."
        )


def main(use_cached: bool = False) -> None:
    xml_bytes = _get_xml_bytes(use_cached)

    print("\nParsing & extracting...")
    root = parse_xml(xml_bytes)
    stmt = next(iter_statements(root))
    securities = extract_open_positions(stmt)
    cash = extract_cash_positions(stmt)
    trades = extract_trades(stmt)
    cash_txs = extract_cash_transactions(stmt)
    nav = extract_nav_history(stmt)
    borrow_fees = extract_borrow_fees(stmt)
    corp_actions = extract_corporate_actions(stmt)
    option_events = extract_option_eae(stmt)
    print(
        f"  IBKR: {len(securities)} securities, {len(cash)} cash, "
        f"{len(trades)} trades, {len(cash_txs)} cash txs, "
        f"{len(nav)} NAV days, {len(borrow_fees)} borrow-fee rows, "
        f"{len(corp_actions)} corporate actions, {len(option_events)} option events"
    )

    # Positions sync
    print("\nSyncing positions.csv...")
    existing = pd.read_csv(POSITIONS_PATH)
    new_positions, diff = sync_positions(existing, securities, cash)
    print(_format_diff(diff))

    bak = _backup(POSITIONS_PATH)
    new_positions.to_csv(POSITIONS_PATH, index=False)
    if bak:
        print(f"  Backup: {bak.relative_to(PROJECT_ROOT)}")
    print(f"  Wrote:  {POSITIONS_PATH.relative_to(PROJECT_ROOT)}")

    # Transactions sync
    print("\nSyncing transactions.csv...")
    merged_trades, n_added = sync_transactions(trades)
    if TRANSACTIONS_PATH.exists():
        _backup(TRANSACTIONS_PATH)
    merged_trades.to_csv(TRANSACTIONS_PATH, index=False)
    print(
        f"  +{n_added} new trades (total: {len(merged_trades)}) -> "
        f"{TRANSACTIONS_PATH.relative_to(PROJECT_ROOT)}"
    )

    # Cash transactions sync
    print("\nSyncing cash_transactions.csv...")
    merged_ct, n_ct = sync_cash_transactions(cash_txs)
    if CASH_TRANSACTIONS_PATH.exists():
        _backup(CASH_TRANSACTIONS_PATH)
    merged_ct.to_csv(CASH_TRANSACTIONS_PATH, index=False)
    print(
        f"  +{n_ct} new cash txs (total: {len(merged_ct)}) -> "
        f"{CASH_TRANSACTIONS_PATH.relative_to(PROJECT_ROOT)}"
    )

    # Borrow fees sync (only useful if you've shorted anything)
    print("\nSyncing borrow_fees.csv...")
    merged_bf, n_bf = sync_borrow_fees(borrow_fees)
    if BORROW_FEES_PATH.exists():
        _backup(BORROW_FEES_PATH)
    merged_bf.to_csv(BORROW_FEES_PATH, index=False)
    print(
        f"  +{n_bf} new borrow-fee rows (total: {len(merged_bf)}) -> "
        f"{BORROW_FEES_PATH.relative_to(PROJECT_ROOT)}"
    )

    # NAV history (authoritative overwrite, preserves user `notes`)
    print("\nSyncing nav_history.csv...")
    merged_nav, n_nav = sync_nav_history(nav, cash_txs)
    if NAV_HISTORY_PATH.exists():
        _backup(NAV_HISTORY_PATH)
    merged_nav.to_csv(NAV_HISTORY_PATH, index=False)
    print(
        f"  {n_nav} daily NAV rows -> "
        f"{NAV_HISTORY_PATH.relative_to(PROJECT_ROOT)}"
    )

    # Corporate actions — splits and mergers change the share count with no
    # trade behind it, so without this a position stops matching its own
    # history and nothing says why.
    print("\nSyncing corporate_actions.csv...")
    merged_ca, n_ca = sync_corporate_actions(corp_actions)
    if CORPORATE_ACTIONS_PATH.exists():
        _backup(CORPORATE_ACTIONS_PATH)
    merged_ca.to_csv(CORPORATE_ACTIONS_PATH, index=False)
    print(
        f"  +{n_ca} new corporate actions (total: {len(merged_ca)}) -> "
        f"{CORPORATE_ACTIONS_PATH.relative_to(PROJECT_ROOT)}"
    )

    # Option/future exercise, assignment and expiry — how a contract leaves
    # the book. These are not in Trades.
    print("\nSyncing option_events.csv...")
    merged_oe, n_oe = sync_option_events(option_events)
    if OPTION_EVENTS_PATH.exists():
        _backup(OPTION_EVENTS_PATH)
    merged_oe.to_csv(OPTION_EVENTS_PATH, index=False)
    print(
        f"  +{n_oe} new option/future events (total: {len(merged_oe)}) -> "
        f"{OPTION_EVENTS_PATH.relative_to(PROJECT_ROOT)}"
    )

    # Visible, never blocking: a share count trades cannot explain.
    gaps = unreconciled_shares(merged_trades, new_positions, merged_ca)
    # Everything EXCEPT the expected case. Filtering to why == "unexplained"
    # meant "unreadable quantity" and "unreadable share count" were computed
    # and then silently discarded by the only two callers — the paths written
    # specifically to make bad data visible were unreachable by any human.
    odd = gaps[gaps["why"] != "consistent with pre-log holding"]
    if len(odd):
        print(f"\nShare counts nothing explains ({len(odd)}):")
        for _, r in odd.iterrows():
            print(f"  {r['symbol']}: {r['traded']:+g} traded, {r['held']:g} held "
                  f"— {r['gap']:+g} unaccounted for")
        print("  Not a sync problem: the broker's window is ~365 days, so an "
              "older event cannot be re-fetched. Ask Yaron what happened.")

    _report_broker_coverage(stmt)

    # Last, so it reads the trades that were just imported rather than the ones
    # from before this run.
    _report_unexplained()

    # D10: he can never be looking at a page older than his data. Not on a
    # schedule — a background job that fails quietly belongs in Phase 5.
    #
    # Wrapped, and deliberately: the sync's job is his data, and a renderer
    # raising must not turn a successful sync into a failed command. The
    # message names which of the two happened (P2).
    _rebuild_page()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--cached",
        action="store_true",
        help="Skip the IBKR fetch and re-parse the latest XML in data/raw/.",
    )
    args = ap.parse_args()
    main(use_cached=args.cached)
