"""
IBKR Flex Web Service importer — pull positions and trade history from IBKR.

Calls IBKR's Flex Web Service via the `ibflex` library, then parses the
returned XML with Python's stdlib ElementTree. Using stdlib parsing (instead
of `ibflex.parser`) makes us tolerant of IBKR adding new fields to the XML
schema — extra attributes are silently ignored rather than crashing.

Credentials live in `.env` (gitignored) — never in code, never logged.

This is the Phase 1 exploratory importer:
- Downloads the latest Flex XML
- Saves a timestamped raw copy to data/raw/ (also gitignored)
- Parses sections of interest and prints counts + a sample record from each

Once we've confirmed the data, the next phase maps it into
`data/positions.csv` and a new `data/transactions.csv`.

Run from project root:
    venv/bin/python -m src.ibkr_import
"""

from __future__ import annotations

import argparse
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from ibflex import client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
ENV_PATH = PROJECT_ROOT / ".env"

# Sections we report on. Each maps to a child container under <FlexStatement>.
# Listed in roughly the order we want to see in the summary.
SECTIONS_TO_REPORT: tuple[str, ...] = (
    "AccountInformation",
    "EquitySummaryInBase",
    "ChangeInNAV",
    "FIFOPerformanceSummaryInBase",
    "MTMPerformanceSummaryInBase",
    "OpenPositions",
    "FxPositions",
    "Trades",
    "OptionEAE",
    "CashReport",
    "CashTransactions",
    "FxTransactions",
    "InterestAccruals",
    "OpenDividendAccruals",
    "CorporateActions",
    "Transfers",
    "HardToBorrowDetails",
    "SLBOpenContracts",
    "SLBActivities",
    "SLBFees",
    "TransactionTaxes",
    "UnbundledCommissionDetails",
    "ConversionRates",
)


def load_credentials(env_path: Path = ENV_PATH) -> tuple[str, str]:
    """Load Flex token and query ID from .env. Errors loudly if either missing."""
    load_dotenv(env_path)
    token = os.getenv("IBKR_FLEX_TOKEN", "").strip()
    query_id = os.getenv("IBKR_FLEX_QUERY_ID", "").strip()
    if not token:
        raise RuntimeError(
            f"IBKR_FLEX_TOKEN not set in {env_path}. "
            "See docs/ibkr-flex-setup.md."
        )
    if not query_id:
        raise RuntimeError(
            f"IBKR_FLEX_QUERY_ID not set in {env_path}. "
            "See docs/ibkr-flex-setup.md."
        )
    return token, query_id


# Whichever comes first. Eight tries is room for a genuinely busy server —
# IBKR sleeps 5s on busy and 10s on throttled — without becoming the forever
# loop. The wall-clock cap exists because the delays are the SERVER's to
# choose: it can answer "try again in 600" and blow the budget in one go, so
# counting attempts alone would not bound the wait.
FETCH_MAX_ATTEMPTS = 8
FETCH_MAX_SECONDS = 120


def _download_bounded(token: str, query_id: str) -> bytes:
    """IBKR's two-step Flex download, with a cap on the polling.

    Same conversation as `ibflex.client.download()` — request a statement,
    then poll for it — built from the library's own pieces so nothing about
    how we talk to IBKR changes. The only difference is that this one stops.

    Raises RuntimeError naming the wait rule when it gives up, because a bare
    timeout reads as "try again now", and trying again now is what extends a
    lockout.
    """
    access = client.request_statement(token, query_id)
    url = access.Url or client.STMT_URL
    started = time.monotonic()

    for attempt in range(1, FETCH_MAX_ATTEMPTS + 1):
        response = client.submit_request(url=url, token=token,
                                         query=access.ReferenceCode)
        status = client.check_statement_response(response)
        if status is True:
            return response.content
        # `status` is the server's requested delay in seconds. Check the
        # budget BEFORE sleeping: a 600-second ask should fail now, not in
        # ten minutes.
        elapsed = time.monotonic() - started
        if attempt == FETCH_MAX_ATTEMPTS or elapsed + status > FETCH_MAX_SECONDS:
            break
        time.sleep(status)

    waited = time.monotonic() - started
    raise RuntimeError(
        f"IBKR never returned the statement — {attempt} attempt(s) over "
        f"{waited:.0f}s. It is busy or throttling this token. "
        f"Wait 15 minutes and try ONCE; retrying sooner is what turns a "
        f"temporary refusal into a lockout (docs/data-conventions.md)."
    )


def fetch_flex_xml(token: str, query_id: str) -> bytes:
    """Pull the latest Flex Query XML response from IBKR. Returns raw bytes.

    Any failure is re-raised with the token masked. The ibflex library puts
    the token in the request URL as a query parameter, and `requests` quotes
    the full URL in its ConnectionError — so on 2026-09-29 a DNS failure
    printed the live token into a log file and a session transcript, and it
    had to be rotated.

    We cannot stop the library building that URL. We can stop it leaving this
    function. The last four characters survive so a human can still tell which
    token failed, and the underlying cause is preserved verbatim otherwise.

    ⚠️ **This does NOT call `ibflex.client.download()`**, and that is the whole
    point. Its loop is `while status is not True: time.sleep(status)` with no
    cap — a busy IBKR means re-sending every 5-10 seconds for ever. On
    2026-10-08 that ran seven minutes before anyone noticed, and
    docs/data-conventions.md is explicit that this is how a transient `1001`
    becomes a `1025` lockout. The two-step download below is the library's
    own, bounded.
    """
    try:
        return _download_bounded(token, query_id)
    except Exception as e:
        masked = str(e).replace(token, _mask_token(token))
        try:
            # Same type where it can be rebuilt from one message — the common
            # case, and it keeps `except ConnectionError` working upstream.
            rebuilt = type(e)(masked)
        except Exception:
            # ibflex's own ResponseCodeError takes (code, msg), so a one-arg
            # rebuild raises TypeError *inside the handler* and replaces the
            # real cause with a confusing one. Name the original type in the
            # message instead of losing it.
            rebuilt = RuntimeError(f"{type(e).__name__}: {masked}")
        raise rebuilt from None


def save_raw_xml(xml_bytes: bytes, directory: Path = RAW_DIR) -> Path:
    """
    Save raw XML to data/raw/ with a UTC timestamped filename.

    data/raw/ is gitignored — these files never leave the machine.
    Keeping the raw responses lets us re-parse historically without
    re-fetching from IBKR (which is rate-limited).
    """
    directory.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"ibkr_flex_{ts}.xml"
    path.write_bytes(xml_bytes)
    return path


def parse_xml(xml_bytes: bytes) -> ET.Element:
    """Parse raw XML bytes into an ElementTree root element."""
    return ET.fromstring(xml_bytes)


def iter_statements(root: ET.Element):
    """Yield one <FlexStatement> element per account in the response."""
    yield from root.iter("FlexStatement")


def section_records(stmt: ET.Element, section: str) -> list[dict]:
    """
    Return all records in a given section as a list of attribute dicts.

    Each record is one child element under <section>. We return
    `elem.attrib` (a plain dict) so callers don't have to know about XML.
    AccountInformation is special: the section element itself carries
    the attributes, with no children.
    """
    container = stmt.find(section)
    if container is None:
        return []
    if section == "AccountInformation":
        return [dict(container.attrib)]
    return [dict(child.attrib) for child in container]


def summarize_statement(stmt: ET.Element) -> dict:
    """
    Count records in every section listed in SECTIONS_TO_REPORT for one
    account. Returns a dict with account id, period, and per-section counts.
    """
    sections: dict[str, int] = {}
    for name in SECTIONS_TO_REPORT:
        container = stmt.find(name)
        if container is None:
            continue
        if name == "AccountInformation":
            sections[name] = 1
        else:
            sections[name] = len(container)
    return {
        "account": stmt.attrib.get("accountId"),
        "from_date": stmt.attrib.get("fromDate"),
        "to_date": stmt.attrib.get("toDate"),
        "sections": sections,
    }


# ─── Extraction into DataFrames ──────────────────────────────────────────
#
# Each extract_* function converts one section of the XML into a clean
# DataFrame with typed columns. These are the inputs to the merge logic
# in src/ibkr_sync.py — they know nothing about ticker conventions or
# the shape of positions.csv.


def _to_float(value: str) -> float:
    """Cast an XML attribute string to float; empty string returns NaN."""
    return float(value) if value else float("nan")


def extract_open_positions(stmt: ET.Element) -> pd.DataFrame:
    """
    Return one row per open position with typed columns.

    Skips zero-position rows (closed lots that IBKR sometimes echoes back).
    """
    rows = []
    for rec in section_records(stmt, "OpenPositions"):
        pos = _to_float(rec.get("position", ""))
        if pos == 0:
            continue
        rows.append(
            {
                "symbol": rec.get("symbol", ""),
                "description": rec.get("description", ""),
                "currency": rec.get("currency", ""),
                "asset_category": rec.get("assetCategory", ""),
                "sub_category": rec.get("subCategory", ""),
                "listing_exchange": rec.get("listingExchange", ""),
                "position": pos,
                "mark_price": _to_float(rec.get("markPrice", "")),
                "cost_basis_price": _to_float(rec.get("costBasisPrice", "")),
                "cost_basis_money": _to_float(rec.get("costBasisMoney", "")),
                "position_value": _to_float(rec.get("positionValue", "")),
                "percent_of_nav": _to_float(rec.get("percentOfNAV", "")),
                "fx_rate_to_base": _to_float(rec.get("fxRateToBase", "")),
                "multiplier": _to_float(rec.get("multiplier", "")),
                "security_id": rec.get("securityID", ""),
                "report_date": rec.get("reportDate", ""),
            }
        )
    return pd.DataFrame(rows)


def extract_cash_positions(stmt: ET.Element) -> pd.DataFrame:
    """
    Return one row per non-zero cash position with the base-currency value.

    Pulls from <FxPositions> (per-currency native qty + base-currency value).
    """
    rows = []
    for rec in section_records(stmt, "FxPositions"):
        qty = _to_float(rec.get("quantity", ""))
        if qty == 0:
            continue
        rows.append(
            {
                "currency": rec.get("fxCurrency", ""),
                "quantity": qty,
                "close_price": _to_float(rec.get("closePrice", "")),
                "value_in_base": _to_float(rec.get("value", "")),
                "cost_basis_in_base": _to_float(rec.get("costBasis", "")),
                "unrealized_pl": _to_float(rec.get("unrealizedPL", "")),
                "report_date": rec.get("reportDate", ""),
            }
        )
    return pd.DataFrame(rows)


def extract_trades(stmt: ET.Element) -> pd.DataFrame:
    """
    Return one row per real trade. Skips the section-total row
    (the first <Trade> element under <Trades>, which has no symbol or tradeID).
    """
    rows = []
    for rec in section_records(stmt, "Trades"):
        # Section-total row has no tradeID and no symbol; drop it.
        if not rec.get("tradeID") or not rec.get("symbol"):
            continue
        rows.append(
            {
                "trade_id": rec.get("tradeID", ""),
                "transaction_id": rec.get("transactionID", ""),
                "trade_date": rec.get("tradeDate", ""),
                "datetime": rec.get("dateTime", ""),
                "symbol": rec.get("symbol", ""),
                "description": rec.get("description", ""),
                "asset_category": rec.get("assetCategory", ""),
                "sub_category": rec.get("subCategory", ""),
                "currency": rec.get("currency", ""),
                "listing_exchange": rec.get("listingExchange", ""),
                "exchange": rec.get("exchange", ""),
                "buy_sell": rec.get("buySell", ""),
                "quantity": _to_float(rec.get("quantity", "")),
                "price": _to_float(rec.get("tradePrice", "")),
                "gross_money": _to_float(rec.get("tradeMoney", "")),
                "commission": _to_float(rec.get("ibCommission", "")),
                "commission_currency": rec.get("ibCommissionCurrency", ""),
                "net_cash": _to_float(rec.get("netCash", "")),
                "proceeds": _to_float(rec.get("proceeds", "")),
                "open_close": rec.get("openCloseIndicator", ""),
                "order_type": rec.get("orderType", ""),
                "transaction_type": rec.get("transactionType", ""),
                "fifo_pnl": _to_float(rec.get("fifoPnlRealized", "")),
                "mtm_pnl": _to_float(rec.get("mtmPnl", "")),
                "multiplier": _to_float(rec.get("multiplier", "")),
                "fx_rate_to_base": _to_float(rec.get("fxRateToBase", "")),
                "security_id": rec.get("securityID", ""),
            }
        )
    return pd.DataFrame(rows)


def extract_cash_transactions(stmt: ET.Element) -> pd.DataFrame:
    """
    Return one row per cash transaction — dividends, interest, taxes,
    deposits/withdrawals, payments-in-lieu.

    Feeds the YTD income / cost analytics.
    """
    rows = []
    for rec in section_records(stmt, "CashTransactions"):
        if not rec.get("type"):
            continue
        rows.append(
            {
                "transaction_id": rec.get("transactionID", ""),
                "date": rec.get("dateTime", "").split(";")[0],
                "report_date": rec.get("reportDate", ""),
                "type": rec.get("type", ""),
                "symbol": rec.get("symbol", ""),
                "description": rec.get("description", ""),
                "currency": rec.get("currency", ""),
                "amount": _to_float(rec.get("amount", "")),
                "fx_rate_to_base": _to_float(rec.get("fxRateToBase", "")),
                "dividend_type": rec.get("dividendType", ""),
                "ex_date": rec.get("exDate", ""),
                "security_id": rec.get("securityID", ""),
                "action_id": rec.get("actionID", ""),
            }
        )
    return pd.DataFrame(rows)


def extract_corporate_actions(stmt: ET.Element) -> pd.DataFrame:
    """
    Return one row per corporate action — splits, mergers, spin-offs.

    Requested and counted since this importer was written, and never
    extracted until 2026-09-29. The cost of that gap was concrete: a 2-for-1
    XLK split on 2025-12-05 made the trade log read "bought 15, sold 17,
    while holding 8", and both `REBUILD.md` and
    `docs/data-model-open-questions.md` #9 recorded it as an unexplained
    bug for weeks. The explanation was in every raw file all along.

    `quantity` is signed and must stay so: a split adds shares, a merger
    removes them, and the `type` code alone does not separate every case.
    The ratio ("SPLIT 2 FOR 1") exists ONLY inside `description`, so that
    column is load-bearing rather than decorative.
    """
    rows = []
    for rec in section_records(stmt, "CorporateActions"):
        # Same section-total row shape as Trades: no ids, no symbol.
        if not rec.get("transactionID") or not rec.get("symbol"):
            continue
        rows.append(
            {
                "transaction_id": rec.get("transactionID", ""),
                "action_id": rec.get("actionID", ""),
                "report_date": rec.get("reportDate", ""),
                "datetime": rec.get("dateTime", ""),
                "symbol": rec.get("symbol", ""),
                "type": rec.get("type", ""),
                "description": rec.get("description", ""),
                "asset_category": rec.get("assetCategory", ""),
                "sub_category": rec.get("subCategory", ""),
                "currency": rec.get("currency", ""),
                "quantity": _to_float(rec.get("quantity", "")),
                "proceeds": _to_float(rec.get("proceeds", "")),
                "value": _to_float(rec.get("value", "")),
                "security_id": rec.get("securityID", ""),
            }
        )
    return pd.DataFrame(rows)


def extract_option_eae(stmt: ET.Element) -> pd.DataFrame:
    """
    Return one row per option/future exercise, assignment or expiration.

    Despite the section name IBKR reports FUTURES expiries here too — the
    A crude future closes this way, and so do expiring options.

    ⚠️ Most of these rows ALSO appear in `Trades`, as `BookTrade` entries with
    the same `tradeID` and the same realized P&L. An earlier version of this
    docstring claimed the opposite — including that the figure for it existed
    in no table, when it was in `transactions.csv` all along — and review
    caught it on 2026-09-29.

    What the section adds is the CONTRACT: strike, expiry, put/call, mark
    price and cost basis, none of which `Trades` carries. It also holds
    settlement rows `Trades` does not, such as the $1,200 cash settlement of
    an in-the-money VIX 20 call.
    """
    rows = []
    for rec in section_records(stmt, "OptionEAE"):
        # The section's first element carries no attributes at all.
        if not rec.get("tradeID") or not rec.get("symbol"):
            continue
        rows.append(
            {
                "trade_id": rec.get("tradeID", ""),
                "date": rec.get("date", ""),
                "symbol": rec.get("symbol", ""),
                "description": rec.get("description", ""),
                "asset_category": rec.get("assetCategory", ""),
                "sub_category": rec.get("subCategory", ""),
                "currency": rec.get("currency", ""),
                "put_call": rec.get("putCall", ""),
                "strike": _to_float(rec.get("strike", "")),
                "expiry": rec.get("expiry", ""),
                "multiplier": _to_float(rec.get("multiplier", "")),
                "quantity": _to_float(rec.get("quantity", "")),
                "trade_price": _to_float(rec.get("tradePrice", "")),
                "mark_price": _to_float(rec.get("markPrice", "")),
                "cost_basis": _to_float(rec.get("costBasis", "")),
                "proceeds": _to_float(rec.get("proceeds", "")),
                "realized_pnl": _to_float(rec.get("realizedPnl", "")),
                "mtm_pnl": _to_float(rec.get("mtmPnl", "")),
                "security_id": rec.get("securityID", ""),
            }
        )
    return pd.DataFrame(rows)


# Every section this module turns into a table, and the extractor that does
# it. Read by unparsed_sections(); keep it in step when an extractor lands,
# or the report will argue for switching off data the project runs on.
PARSED_SECTIONS: dict[str, str] = {
    "AccountInformation": "section_records",
    "OpenPositions": "extract_open_positions",
    "FxPositions": "extract_cash_positions",
    "Trades": "extract_trades",
    "CashTransactions": "extract_cash_transactions",
    "EquitySummaryInBase": "extract_nav_history",
    "HardToBorrowDetails": "extract_borrow_fees",
    "CorporateActions": "extract_corporate_actions",
    "OptionEAE": "extract_option_eae",
}


def unparsed_sections(stmt: ET.Element) -> list[dict]:
    """
    What the broker actually sent, how much of it, and whether anything reads
    it. One row per section PRESENT in the statement, biggest first.

    His question on 2026-09-29 was whether he is requesting too much from
    IBKR or too little. Answering it once meant reading raw XML by hand; this
    keeps it answerable. The first run found 12,207 `ConversionRate` records
    per file — about 90% of a 2.2 MB download — read by nothing, while
    `CorporateActions` sat unparsed three feet away.

    Reports only sections the file CONTAINS. A requested section that arrives
    empty is absent here on purpose: listing it as "0 records, unparsed" reads
    as "costs nothing", when the fact worth knowing is that it is missing.
    """
    rows = []
    for container in stmt:
        name = container.tag
        # A container with no children carries its own attributes as the
        # record — AccountInformation is the standing example.
        count = len(container) or (1 if container.attrib else 0)
        rows.append(
            {
                "section": name,
                "records": count,
                "parsed": name in PARSED_SECTIONS,
                "reader": PARSED_SECTIONS.get(name, ""),
            }
        )
    return sorted(rows, key=lambda r: (-r["records"], r["section"]))


def extract_nav_history(stmt: ET.Element) -> pd.DataFrame:
    """
    Return one row per business day with total NAV in base currency.

    Source: <EquitySummaryInBase>. The `nav` column name matches what
    `src.returns.load_nav_history` expects so the result drops directly
    into the existing TWR pipeline. Extra columns (cash/stock/options/
    accruals) are kept for visual decomposition.
    """
    rows = []
    for rec in section_records(stmt, "EquitySummaryInBase"):
        if not rec.get("reportDate"):
            continue
        rows.append(
            {
                "date": rec.get("reportDate", ""),
                "nav": _to_float(rec.get("total", "")),
                "cash": _to_float(rec.get("cash", "")),
                "stock": _to_float(rec.get("stock", "")),
                "options": _to_float(rec.get("options", "")),
                "dividend_accruals": _to_float(rec.get("dividendAccruals", "")),
                "interest_accruals": _to_float(rec.get("interestAccruals", "")),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
    return df


def extract_borrow_fees(stmt: ET.Element) -> pd.DataFrame:
    """
    Return one row per hard-to-borrow record — daily borrow-fee accruals
    on short positions.

    Source: <HardToBorrowDetails>. `borrowFeeRate` is annualized %;
    `borrowFee` is the per-day fee charged (negative = cost) in the
    security's currency.
    """
    rows = []
    for rec in section_records(stmt, "HardToBorrowDetails"):
        if not rec.get("symbol"):
            continue
        rows.append(
            {
                "date": rec.get("valueDate", ""),
                "symbol": rec.get("symbol", ""),
                "description": rec.get("description", ""),
                "currency": rec.get("currency", ""),
                "quantity": _to_float(rec.get("quantity", "")),
                "price": _to_float(rec.get("price", "")),
                "value": _to_float(rec.get("value", "")),
                "borrow_fee_rate": _to_float(rec.get("borrowFeeRate", "")),
                "borrow_fee": _to_float(rec.get("borrowFee", "")),
                "fx_rate_to_base": _to_float(rec.get("fxRateToBase", "")),
                "security_id": rec.get("securityID", ""),
            }
        )
    return pd.DataFrame(rows)


# ─── CLI helpers ─────────────────────────────────────────────────────────


def _mask_token(token: str) -> str:
    """Show only the last 4 chars of the token for safe logging."""
    if len(token) <= 4:
        return "*" * len(token)
    return "*" * (len(token) - 4) + token[-4:]


def _print_sample(stmt: ET.Element, section: str, n: int = 1) -> None:
    """Print the first n records of a section so we can see real field names."""
    records = section_records(stmt, section)
    if not records:
        return
    print(f"\n  Sample {section} record(s):")
    for rec in records[:n]:
        for k, v in rec.items():
            # truncate long values for legibility
            sval = str(v)
            if len(sval) > 80:
                sval = sval[:77] + "..."
            print(f"    {k}: {sval}")


def _latest_cached_xml(directory: Path = RAW_DIR) -> Path | None:
    """Return the most recent ibkr_flex_*.xml in data/raw/, or None."""
    if not directory.exists():
        return None
    files = sorted(directory.glob("ibkr_flex_*.xml"))
    return files[-1] if files else None


def main(use_cached: bool = False) -> None:
    if use_cached:
        cached = _latest_cached_xml()
        if cached is None:
            raise RuntimeError(
                f"--cached given but no ibkr_flex_*.xml in {RAW_DIR}. "
                "Run without --cached first to fetch from IBKR."
            )
        print(f"Loading cached XML: {cached.relative_to(PROJECT_ROOT)}")
        xml_bytes = cached.read_bytes()
        print(f"  {len(xml_bytes):,} bytes")
    else:
        print("Loading credentials from .env...")
        token, query_id = load_credentials()
        print(f"  Query ID: {query_id}")
        print(f"  Token:    {_mask_token(token)}")

        print("\nFetching from IBKR Flex Web Service (this can take 5-30 sec)...")
        xml_bytes = fetch_flex_xml(token, query_id)
        print(f"  Received {len(xml_bytes):,} bytes")

        print("\nSaving raw XML...")
        path = save_raw_xml(xml_bytes)
        print(f"  -> {path.relative_to(PROJECT_ROOT)}")

    print("\nParsing...")
    root = parse_xml(xml_bytes)
    print(f"  Query name: {root.attrib.get('queryName')}")

    for stmt in iter_statements(root):
        summary = summarize_statement(stmt)
        print(
            f"\nAccount {summary['account']}  "
            f"({summary['from_date']} -> {summary['to_date']})"
        )
        if not summary["sections"]:
            print("  (no populated sections)")
            continue
        width = max(len(k) for k in summary["sections"])
        for name, count in summary["sections"].items():
            print(f"  {name:<{width}}  {count:>6} records")

        # Show one sample record from the headline sections so we can see
        # which fields IBKR actually returned for our query.
        for section in ("OpenPositions", "Trades", "CashReport"):
            _print_sample(stmt, section, n=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--cached",
        action="store_true",
        help="Skip the IBKR fetch and re-parse the latest XML in data/raw/.",
    )
    args = ap.parse_args()
    main(use_cached=args.cached)
