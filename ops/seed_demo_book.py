"""
Build the demo book this public repository ships with.

Every figure in `data/` is generated here. There is no real portfolio behind
it: no positions, no trades, no account value, no bets, no price levels. The
real book is private and never left the machine it lives on.

What is NOT generated here, because it is real and published by someone else:

    data/sectors.csv                   the sector taxonomy
    data/market/etf_compositions.csv   each fund's own sector breakdown
    data/market/etf_holdings.csv       each fund's own holdings, as filed

Run it:

    venv/bin/python ops/seed_demo_book.py          # rebuild every demo table
    venv/bin/python ops/seed_demo_book.py --check   # validate, write nothing

It is deterministic: one seed, one book, byte for byte. Delete every table it
owns and run it again and the repository is back exactly where it was. That is
the point — a reader should be able to prove the book is generated rather than
take a README's word for it.

Prices follow a seeded random walk. They are not real quotes and no real
instrument ever traded at them, but they move like prices do, so every
derived figure on the page — weights, bases, look-through exposure — has
something plausible to be derived from.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"

SEED = 20261010
START = date(2025, 1, 2)
SNAPSHOT = date(2026, 10, 10)        # every price and position is "as of" this
LAST_TRADING_DAY = date(2026, 10, 9)

# A round opening deposit is itself a signal: real accounts do not start at
# exactly $100,000.
OPENING_DEPOSIT = 100_000.00
EUR_USD = 1.0850

# Every trade quantity is scaled by this. It exists for one reason: the book
# must never go onto margin. Cash is a real position here, the page shows it,
# and a demo that quietly borrowed to buy its own holdings would be teaching
# the reader something false about the account it claims to describe.
SIZE_SCALE = 0.72

# ---------------------------------------------------------------------------
# The instruments. One row per real-world thing; broker lots come later.
#
# Real tickers, because the look-through panel needs real fund holdings to
# mean anything and those are the issuers' own published files. The book
# itself — which of them, how many, at what price — is invented.
# ---------------------------------------------------------------------------
# id, name, type, sector, cluster, ccy, price, drift, vol, status, av, issuer
INSTRUMENTS = [
    ("NVDA", "NVIDIA Corporation", "stock", "Tech - Semiconductors",
     "AI Infrastructure", "USD", 118.0, 0.34, 0.38, "active", "NVDA", ""),
    ("MSFT", "Microsoft Corporation", "stock", "Tech - Software",
     "Software", "USD", 418.0, 0.14, 0.24, "active", "MSFT", ""),
    ("ASML", "ASML Holding NV", "stock", "Tech - Semiconductors",
     "AI Infrastructure", "USD", 690.0, 0.11, 0.33, "active", "ASML", ""),
    ("TSM", "Taiwan Semiconductor Manufacturing", "stock", "Tech - Semiconductors",
     "AI Infrastructure", "USD", 196.0, 0.24, 0.35, "active", "TSM", ""),
    ("PLTR", "Palantir Technologies", "stock", "Tech - Software",
     "AI Applications", "USD", 72.0, 0.30, 0.42, "active", "PLTR", ""),
    ("JPM", "JPMorgan Chase & Co", "stock", "Financials - Diversified",
     "Financials", "USD", 238.0, 0.12, 0.22, "active", "JPM", ""),
    ("DE", "Deere & Company", "stock", "Industrials - Ag Machinery",
     "Agriculture", "USD", 412.0, 0.08, 0.26, "active", "DE", ""),
    ("MRK", "Merck & Co", "stock", "Healthcare",
     "Healthcare", "USD", 99.0, 0.05, 0.23, "active", "MRK", ""),
    ("PM", "Philip Morris International", "stock", "Consumer Staples",
     "Consumer Staples", "USD", 121.0, 0.10, 0.20, "active", "PM", ""),

    ("QQQ", "Invesco QQQ Trust Series I", "etf", "",
     "Core US", "USD", 511.0, 0.15, 0.22, "active", "", "Invesco"),
    ("VTI", "Vanguard Total Stock Market ETF", "etf", "",
     "Core US", "USD", 288.0, 0.11, 0.17, "active", "", "Vanguard"),
    ("IWM", "iShares Russell 2000 ETF", "etf", "",
     "Core US", "USD", 223.0, 0.07, 0.24, "active", "", "iShares"),
    ("MOAT", "VanEck Morningstar Wide Moat ETF", "etf", "",
     "Core US", "USD", 95.0, 0.09, 0.18, "active", "", "VanEck"),
    ("COWZ", "Pacer US Cash Cows 100 ETF", "etf", "",
     "Core US", "USD", 58.0, 0.08, 0.19, "active", "", "Pacer"),
    ("IGV", "iShares Expanded Tech-Software Sector ETF", "etf", "Tech - Software",
     "Software", "USD", 93.0, 0.16, 0.28, "active", "", "iShares"),
    ("XLF", "Financial Select Sector SPDR Fund", "etf", "Financials",
     "Financials", "USD", 48.0, 0.11, 0.19, "active", "", "SPDR"),
    ("XLI", "Industrial Select Sector SPDR Fund", "etf", "Industrials",
     "Industrials", "USD", 136.0, 0.09, 0.19, "active", "", "SPDR"),
    ("GDX", "VanEck Gold Miners ETF", "etf", "Materials - Gold Miners",
     "Precious Metals", "USD", 39.0, 0.26, 0.30, "active", "", "VanEck"),
    ("SILJ", "Amplify Junior Silver Miners ETF", "etf", "Materials - Silver Miners",
     "Precious Metals", "USD", 12.0, 0.29, 0.34, "active", "", "Amplify"),
    ("VEGI", "iShares MSCI Agriculture Producers ETF", "etf", "Materials - Agriculture",
     "Agriculture", "USD", 38.0, 0.06, 0.20, "active", "", "iShares"),
    ("PHO", "Invesco Water Resources ETF", "etf", "Industrials",
     "Water Infrastructure", "USD", 66.0, 0.10, 0.19, "active", "", "Invesco"),
    ("EEM", "iShares MSCI Emerging Markets ETF", "etf", "",
     "International", "USD", 44.0, 0.08, 0.21, "active", "", "iShares"),
    ("SGOV", "iShares 0-3 Month Treasury Bond ETF", "etf",
     "Fixed Income - Short Treasury", "Dry Powder", "USD", 100.4, 0.045, 0.004,
     "active", "", "iShares"),

    ("XRT", "SPDR S&P Retail ETF", "short", "Consumer Disc - Retail/EC",
     "Core US", "USD", 78.0, 0.04, 0.30, "active", "", "SPDR"),

    ("USD_CASH", "US Dollar", "cash", "Cash", "Dry Powder", "USD",
     1.0, 0.0, 0.0, "active", "", ""),
    ("EUR_CASH", "Euro", "cash", "Cash / FX", "Dry Powder", "EUR",
     1.0, 0.0, 0.0, "active", "", ""),

    ("ADBE", "Adobe Inc", "stock", "Tech - Software", "Software", "USD",
     440.0, -0.06, 0.30, "closed", "ADBE", ""),
    ("CAT", "Caterpillar Inc", "stock", "Industrials", "Industrials", "USD",
     365.0, 0.13, 0.25, "closed", "CAT", ""),
    ("WPM", "Wheaton Precious Metals", "stock", "Materials - Silver Miners",
     "Precious Metals", "USD", 61.0, 0.22, 0.32, "closed", "WPM", ""),

    ("SPY", "SPDR S&P 500 ETF Trust", "etf", "", "", "USD",
     589.0, 0.12, 0.17, "benchmark", "", "SPDR"),

    # Named but never held. Several of the repository's own tests write a
    # claim or a thought against one of these, and add_claim() checks the
    # instrument_id against this table rather than its own fixture — so the
    # rows have to be here for the suite to pass. They carry no position, no
    # trade and no money.
    ("ANET", "Arista Networks", "stock", "Tech - Hardware / Network",
     "AI Infrastructure", "USD", 128.0, 0.15, 0.32, "watchlist", "ANET", ""),
    ("TSLA", "Tesla Inc", "stock", "Consumer Disc - Auto/EV",
     "Core US", "USD", 345.0, 0.05, 0.52, "watchlist", "TSLA", ""),
    ("BG", "Bunge Global SA", "stock", "Consumer Staples - Agribusiness",
     "Agriculture", "USD", 78.0, 0.04, 0.26, "watchlist", "BG", ""),
    ("DRAM", "A memory-sector fund", "etf", "Tech - Semiconductors",
     "AI Infrastructure", "USD", 34.0, 0.20, 0.44, "watchlist", "", "Demo"),
    ("DBA", "Invesco DB Agriculture Fund", "etf", "Materials - Agriculture",
     "Agriculture", "USD", 26.0, 0.03, 0.17, "watchlist", "", "Invesco"),
    ("COPX", "Global X Copper Miners ETF", "etf", "Materials - Copper Miners",
     "Critical Minerals", "USD", 44.0, 0.18, 0.34, "watchlist", "", "Global X"),
    ("IBIT", "iShares Bitcoin Trust", "etf", "Crypto - Bitcoin",
     "Crypto", "USD", 53.0, 0.30, 0.60, "watchlist", "", "iShares"),
    ("XOP", "SPDR S&P Oil & Gas Exploration", "etf", "Energy - Oil & Gas",
     "Energy", "USD", 138.0, 0.02, 0.33, "closed", "", "SPDR"),
    ("XLRE", "Real Estate Select Sector SPDR", "etf", "Real Estate",
     "Real Estate", "USD", 41.0, 0.04, 0.21, "closed", "", "SPDR"),
    # instruments.resolve() maps the broker spelling "NOV" onto NVO, and
    # "BRK B" onto BRKB. Both instruments have to exist for that routing to
    # land anywhere, which is what tests/test_holding.py checks.
    ("NVO", "Novo Nordisk A/S", "stock", "Healthcare", "Healthcare", "USD",
     61.0, 0.03, 0.30, "closed", "NVO", ""),
    ("BRKB", "Berkshire Hathaway Class B", "stock", "Financials - Diversified",
     "Financials", "USD", 470.0, 0.09, 0.16, "watchlist", "BRK-B", ""),
    ("MOO", "VanEck Agribusiness ETF", "etf", "Materials - Agriculture",
     "Agriculture", "USD", 82.0, 0.05, 0.19, "watchlist", "", "VanEck"),
    # A Korean listing, carried as the broker spells it. It is here because the
    # project has to cope with a ticker that is not three or four letters and a
    # price quoted in a currency with no decimals.
    ("005930.KS", "Samsung Electronics", "stock", "Tech - Semiconductors",
     "AI Infrastructure", "KRW", 74000.0, 0.09, 0.28, "watchlist", "", ""),
]

# A vendor's own sector label, where it differs from the taxonomy above. Two
# deliberate disagreements, so sector_disagreements() has something to report.
PROVIDER_SECTORS = {
    "NVDA": "Technology", "MSFT": "Technology", "ASML": "Technology",
    "TSM": "Technology", "PLTR": "Technology",          # disagrees: Tech - Software
    "JPM": "Financials",                                 # disagrees: Financials - Diversified
    "DE": "Industrials", "MRK": "Healthcare", "PM": "Consumer Staples",
    "ADBE": "Technology", "CAT": "Industrials", "WPM": "Materials",
}
PROVIDER_AS_OF = "2026-10-03"
PROVIDER_SOURCE = "vendor-sector-feed"

# id, parent, layer, in_equity_base
CLUSTERS = [
    ("Technology", "", "group", True),
    ("AI Infrastructure", "Technology", "thematic", True),
    ("AI Applications", "Technology", "thematic", True),
    ("Software", "Technology", "thematic", True),
    ("Commodities", "", "group", True),
    ("Precious Metals", "Commodities", "thematic", True),
    ("Agriculture", "Commodities", "thematic", True),
    ("Water Infrastructure", "Commodities", "thematic", True),
    ("Core US", "", "sector-breadth", True),
    ("Financials", "", "sector-breadth", True),
    ("Industrials", "", "sector-breadth", True),
    ("Healthcare", "", "sector-breadth", True),
    ("Consumer Staples", "", "sector-breadth", True),
    ("International", "", "sector-breadth", True),
    ("Critical Minerals", "Commodities", "thematic", True),
    ("Crypto", "", "thematic", True),
    ("Energy", "", "sector-breadth", True),
    ("Real Estate", "", "sector-breadth", True),
    ("Dry Powder", "", "role", False),
    # Two themes closed on purpose, and one open with nothing in it. The
    # difference matters: a retired theme is not missing a thesis, and an
    # active empty one is.
    ("Metals", "Commodities", "thematic", True),
    ("Water", "Commodities", "thematic", True),
    ("Clean Energy", "", "thematic", True),
]

RETIRED_CLUSTERS = {"Metals": "retired", "Water": "retired"}

# Broker lots: lot ticker -> (instrument_id, currency). One instrument can
# arrive from the broker as two lots; ASML is the usual example.
LOTS = {
    "ASML_USD": ("ASML", "USD"),
    "ASML_EUR": ("ASML", "EUR"),
}

# instrument/lot -> [(trade date, signed quantity)]. Positive buys, negative
# sells. Written out rather than randomised so the book reads like decisions.
TRADES = {
    "NVDA":     [("2025-02-11", 40), ("2025-08-14", 25), ("2026-05-19", -20)],
    "MSFT":     [("2025-01-28", 14), ("2025-11-12", 6)],
    "ASML_USD": [("2025-03-18", 6), ("2026-02-24", 3)],
    "ASML_EUR": [("2025-06-10", 4)],
    "TSM":      [("2025-04-08", 30), ("2026-01-20", 20)],
    "PLTR":     [("2025-05-13", 60), ("2026-04-14", -20)],
    "JPM":      [("2025-02-25", 18)],
    "DE":       [("2025-07-15", 7)],
    "MRK":      [("2025-09-09", 45), ("2026-06-16", 25)],
    "PM":       [("2025-10-07", 32)],
    "QQQ":      [("2025-01-02", 22), ("2025-12-09", 8)],
    "VTI":      [("2025-01-02", 40), ("2026-03-03", 15)],
    "IWM":      [("2025-03-04", 30), ("2026-07-21", -10)],
    "MOAT":     [("2026-02-10", 45)],
    "COWZ":     [("2026-02-10", 60)],
    "IGV":      [("2025-06-24", 28)],
    "XLF":      [("2025-04-22", 70)],
    "XLI":      [("2025-08-26", 22)],
    "GDX":      [("2025-05-20", 120), ("2026-01-13", 60)],
    "SILJ":     [("2025-05-20", 180)],
    "VEGI":     [("2025-07-15", 90)],
    "PHO":      [("2025-09-23", 40)],
    "EEM":      [("2025-11-18", 95)],
    "SGOV":     [("2025-01-02", 60), ("2026-08-11", 95), ("2026-09-15", -30)],
    "XRT":      [("2026-06-09", -45)],
    "ADBE":     [("2025-02-04", 12), ("2025-10-14", -12)],
    "CAT":      [("2025-03-11", 9), ("2026-04-07", -9)],
    "WPM":      [("2025-06-03", 50), ("2026-02-17", -50)],
}

# One split, applied to the ledger as well as recorded, so share counts and
# trades still reconcile across it.
SPLIT = {"symbol": "TSM", "date": date(2026, 1, 15), "ratio": 2}

CASH_FLOWS = [
    ("2025-01-02", "deposit", OPENING_DEPOSIT),
    ("2025-09-02", "deposit", 8_000.00),
    ("2026-03-02", "deposit", 6_000.00),
    ("2026-07-10", "withdrawal", 4_500.00),
]

# Which instruments and themes have a written note, and where it lives. The
# notes are authored prose and are committed as files; this map is the join,
# and `claims.check_refs()` refuses a row whose note_ref points at nothing —
# so a note deleted without updating this is caught at the next commit rather
# than becoming a dead link nobody notices.
NOTED = {
    "positions": [
        "NVDA", "MSFT", "ASML", "TSM", "PLTR", "JPM", "DE", "MRK", "PM",
        "QQQ", "VTI", "IWM", "MOAT", "COWZ", "IGV", "XLF", "XLI", "GDX",
        "SILJ", "VEGI", "PHO", "EEM", "SGOV", "XRT", "DRAM",
    ],
    "closed": ["ADBE", "CAT", "WPM"],
    "watchlist": ["BG", "TSLA", "ANET"],
}


def note_for(iid: str) -> str:
    """The note path for an instrument, or blank when nothing is written.

    Blank is a legitimate answer and is reported by instruments.gaps() rather
    than blocking anything: cash has no thesis to write, and a benchmark is
    not a holding at all.
    """
    for folder, ids in NOTED.items():
        if iid in ids:
            stem = "notes/positions" if folder == "positions" else f"notes/positions/{folder}"
            return f"{stem}/{iid}.md"
    return ""


def cluster_note(cluster_id: str) -> str:
    return f"notes/clusters/{cluster_id.lower().replace(' ', '-')}.md"


DIVIDEND_PAYERS = {       # instrument -> approximate annual yield
    "MSFT": 0.0075, "JPM": 0.021, "DE": 0.014, "MRK": 0.031, "PM": 0.042,
    "QQQ": 0.006, "VTI": 0.013, "IWM": 0.011, "XLF": 0.016, "XLI": 0.015,
    "EEM": 0.024, "SGOV": 0.048, "MOAT": 0.010, "COWZ": 0.019,
}


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
def stable_id(text: str, width: int = 9) -> int:
    """A deterministic pseudo-id from a string.

    Deliberately not the builtin string hash: Python salts that per process,
    so a generator using it produces a different `security_id` and a
    different `isin` on every run — which silently breaks the one promise
    this script makes. Found by diffing two consecutive runs.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % (10 ** width)


def business_days(first: date, last: date) -> list[date]:
    """Every weekday from `first` to `last` inclusive. No holiday calendar:
    a demo book does not need one, and inventing one would imply a precision
    the rest of these numbers do not have."""
    out, d = [], first
    while d <= last:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def price_walk(days: list[date]) -> dict[str, dict[date, float]]:
    """A seeded geometric random walk per instrument.

    Daily steps from one generator seeded once, so the whole book is a pure
    function of SEED. Prices are rounded to cents on the way out because every
    downstream figure is money, and a price with eight decimals would make the
    arithmetic look more exact than it is.
    """
    rng = random.Random(SEED)
    walks: dict[str, dict[date, float]] = {}
    for iid, _n, itype, *_rest in INSTRUMENTS:
        spec = next(i for i in INSTRUMENTS if i[0] == iid)
        start, drift, vol = spec[6], spec[7], spec[8]
        if itype == "cash":
            walks[iid] = {d: 1.0 for d in days}
            continue
        step = drift / 252.0
        shock = vol / math.sqrt(252.0)
        px, series = start, {}
        for d in days:
            px *= math.exp(step - 0.5 * shock ** 2 + shock * rng.gauss(0, 1))
            series[d] = round(px, 2)
        walks[iid] = series
    return walks


def on_or_before(series: dict[date, float], d: date) -> float:
    """The price on `d`, or the last one before it. A trade date that lands on
    a holiday still has to price against something."""
    while d not in series:
        d -= timedelta(days=1)
        if d < START:
            raise KeyError("no price on or before that date")
    return series[d]


# ---------------------------------------------------------------------------
# The ledger — one pass that every broker table is then read out of
#
# This is the whole reason the generator exists rather than hand-written CSVs.
# positions.csv, transactions.csv, cash_transactions.csv and nav_history.csv
# have to agree with each other: the shares in a position must be explained by
# the trades, and the last account value must equal the cash plus the stock.
# Typing four tables by hand and hoping is how you get a demo that fails its
# own reconciliation report. So all four come out of one simulated ledger.
# ---------------------------------------------------------------------------
class Ledger:
    """Shares and cash over time, and the rows that record how they moved."""

    def __init__(self, days: list[date], walks: dict[str, dict[date, float]]):
        self.days = days
        self.walks = walks
        self.shares: dict[str, float] = {}      # lot ticker -> shares
        self.cost: dict[str, float] = {}        # lot ticker -> total cost paid
        self.cash = 0.0                         # USD
        self.eur_cash = 0.0                     # EUR, held as a currency
        self.transactions: list[dict] = []
        self.cash_rows: list[dict] = []
        self.borrow_rows: list[dict] = []
        self.corporate_rows: list[dict] = []
        self.nav_rows: list[dict] = []
        self.realized = 0.0
        self._trade_seq = 1_000_000_000
        self._cash_seq = 2_000_000_000

    # -- identifiers -------------------------------------------------------
    def next_trade_id(self) -> str:
        self._trade_seq += 1
        return str(self._trade_seq)

    def next_cash_id(self) -> str:
        self._cash_seq += 1
        return str(self._cash_seq)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def instrument_of(lot: str) -> str:
        return LOTS.get(lot, (lot, "USD"))[0]

    @staticmethod
    def currency_of(lot: str) -> str:
        return LOTS.get(lot, (lot, "USD"))[1]

    def price_of(self, lot: str, d: date) -> float:
        """The lot's price in the lot's own currency.

        One instrument, two broker lots, two quote currencies: the euro line
        is the same company at the same moment, so its price is the dollar
        price converted, not the dollar number relabelled. Getting this wrong
        overstates the euro lot by the exchange rate and is invisible in every
        total that is already in dollars.
        """
        px = on_or_before(self.walks[self.instrument_of(lot)], d)
        return round(px / EUR_USD, 2) if self.currency_of(lot) == "EUR" else px

    def stock_value(self, d: date) -> float:
        """Market value of every open lot, in USD."""
        total = 0.0
        for lot, qty in self.shares.items():
            if abs(qty) < 1e-9:
                continue
            px = self.price_of(lot, d)
            fx = EUR_USD if self.currency_of(lot) == "EUR" else 1.0
            total += qty * px * fx
        return total

    # -- events ------------------------------------------------------------
    def trade(self, lot: str, d: date, qty: float) -> None:
        iid = self.instrument_of(lot)
        ccy = self.currency_of(lot)
        px = self.price_of(lot, d)
        fx = EUR_USD if ccy == "EUR" else 1.0
        gross = qty * px                      # in the lot's own currency
        commission = -max(1.0, round(abs(qty) * px * 0.00035, 2))
        proceeds = -gross
        net_cash = proceeds + commission

        held = self.shares.get(lot, 0.0)
        avg = (self.cost.get(lot, 0.0) / held) if abs(held) > 1e-9 else 0.0
        # An opening trade realises nothing, and the column says so with a
        # zero rather than a blank: book.realized_pnl() refuses a row it
        # cannot read rather than quietly summing one fewer trade.
        fifo = "0.00"
        if (held > 0 and qty < 0) or (held < 0 and qty > 0):
            closed = min(abs(qty), abs(held))
            sign = 1.0 if held > 0 else -1.0
            # In the TRADE'S OWN currency, never converted here. Each row is
            # multiplied by its own fx_rate_to_base when it is summed, so a
            # pre-converted figure would be counted in dollars twice.
            fifo_val = round((px - avg) * closed * sign, 2)
            fifo = f"{fifo_val:.2f}"
            self.realized += fifo_val * fx
            self.cost[lot] = self.cost.get(lot, 0.0) - avg * closed * sign
            self.shares[lot] = held + qty
        else:
            self.cost[lot] = self.cost.get(lot, 0.0) + gross
            self.shares[lot] = held + qty

        self.cash += net_cash * fx
        spec = next(i for i in INSTRUMENTS if i[0] == iid)
        self.transactions.append({
            "trade_id": self.next_trade_id(),
            "transaction_id": self.next_cash_id(),
            "trade_date": d.isoformat(),
            "datetime": f"{d.isoformat()} 15:42:11",
            "symbol": lot,
            "description": spec[1],
            "asset_category": "STK",
            "sub_category": "ETF" if spec[2] == "etf" else "COMMON",
            "currency": ccy,
            "listing_exchange": "NASDAQ" if spec[2] != "etf" else "ARCA",
            "exchange": "ISLAND" if spec[2] != "etf" else "ARCA",
            "buy_sell": "BUY" if qty > 0 else "SELL",
            "quantity": f"{qty:g}",
            "price": f"{px:.2f}",
            "gross_money": f"{-gross:.2f}",
            "commission": f"{commission:.2f}",
            "commission_currency": ccy,
            "net_cash": f"{net_cash:.2f}",
            "proceeds": f"{proceeds:.2f}",
            "open_close": "C" if fifo else "O",
            "order_type": "LMT",
            "transaction_type": "ExchTrade",
            "fifo_pnl": fifo,
            "mtm_pnl": "",
            "multiplier": "1",
            "fx_rate_to_base": f"{fx:.4f}",
            "security_id": f"DEMO{stable_id(iid):09d}",
        })

    def cash_flow(self, d: date, kind: str, amount: float) -> None:
        self.cash += amount if kind == "deposit" else -amount
        self.cash_rows.append({
            "transaction_id": self.next_cash_id(),
            "date": d.isoformat(),
            "report_date": d.isoformat(),
            "type": "Deposits/Withdrawals",
            "symbol": "",
            "description": "DEMO FUNDING" if kind == "deposit" else "DEMO WITHDRAWAL",
            "currency": "USD",
            "amount": f"{amount if kind == 'deposit' else -amount:.2f}",
            "fx_rate_to_base": "1.0000",
            "dividend_type": "",
            "ex_date": "",
            "security_id": "",
            "action_id": "",
        })

    def dividend(self, lot: str, d: date, amount: float) -> None:
        self.cash += amount
        iid = self.instrument_of(lot)
        self.cash_rows.append({
            "transaction_id": self.next_cash_id(),
            "date": d.isoformat(),
            "report_date": d.isoformat(),
            "type": "Dividends",
            "symbol": lot,
            "description": f"{iid} CASH DIVIDEND",
            "currency": "USD",
            "amount": f"{amount:.2f}",
            "fx_rate_to_base": "1.0000",
            "dividend_type": "Ordinary",
            "ex_date": (d - timedelta(days=14)).isoformat(),
            "security_id": f"DEMO{stable_id(iid):09d}",
            "action_id": "",
        })

    def interest(self, d: date, amount: float) -> None:
        self.cash += amount
        self.cash_rows.append({
            "transaction_id": self.next_cash_id(),
            "date": d.isoformat(),
            "report_date": d.isoformat(),
            "type": "Broker Interest Received",
            "symbol": "",
            "description": "USD CREDIT INTEREST",
            "currency": "USD",
            "amount": f"{amount:.2f}",
            "fx_rate_to_base": "1.0000",
            "dividend_type": "",
            "ex_date": "",
            "security_id": "",
            "action_id": "",
        })

    def borrow_fee(self, lot: str, d: date) -> None:
        qty = self.shares.get(lot, 0.0)
        if qty >= 0:
            return
        px = self.price_of(lot, d)
        value = qty * px
        rate = 2.85
        fee = round(value * rate / 100.0 / 12.0, 2)      # negative: a cost
        self.cash += fee
        self.borrow_rows.append({
            "date": d.isoformat(),
            "symbol": lot,
            "description": f"{lot} BORROW FEE",
            "currency": "USD",
            "quantity": f"{qty:g}",
            "price": f"{px:.2f}",
            "value": f"{value:.2f}",
            "borrow_fee_rate": f"{rate:.4f}",
            "borrow_fee": f"{fee:.2f}",
            "fx_rate_to_base": "1.0000",
            "security_id": f"DEMO{stable_id(lot):09d}",
        })

    def split(self, d: date) -> None:
        """A share split: the position doubles, the price halves, and the
        trades still explain the holding — which is the only reason this is
        in a demo book at all. A reconciliation report that has never seen a
        split has not been tested."""
        sym, ratio = SPLIT["symbol"], SPLIT["ratio"]
        before = self.shares.get(sym, 0.0)
        if abs(before) < 1e-9:
            return
        self.shares[sym] = before * ratio
        for day, px in self.walks[sym].items():
            if day >= d:
                self.walks[sym][day] = round(px / ratio, 2)
        self.corporate_rows.append({
            "transaction_id": self.next_cash_id(),
            "action_id": "9000001",
            "report_date": d.isoformat(),
            "datetime": f"{d.isoformat()} 20:15:00",
            "symbol": sym,
            "type": "FS",
            "description": f"{sym}({ratio} for 1 STOCK SPLIT)",
            "asset_category": "STK",
            "sub_category": "COMMON",
            "currency": "USD",
            "quantity": f"{before * (ratio - 1):g}",
            "proceeds": "0.00",
            "value": "0.00",
            "security_id": f"DEMO{stable_id(sym):09d}",
        })


def run_ledger() -> Ledger:
    """Walk every business day once, applying what happened on it.

    Order within a day matters: a trade before the dividend it is not yet
    entitled to, a split before the fee computed off the new share count.
    """
    days = business_days(START, LAST_TRADING_DAY)
    led = Ledger(days, price_walk(days))

    by_day: dict[date, list[tuple[str, object]]] = {}
    for lot, plan in TRADES.items():
        for iso, qty in plan:
            scaled = int(round(qty * SIZE_SCALE)) or (1 if qty > 0 else -1)
            by_day.setdefault(date.fromisoformat(iso), []).append(("trade", (lot, scaled)))
    for iso, kind, amount in CASH_FLOWS:
        by_day.setdefault(date.fromisoformat(iso), []).append(("cash", (kind, amount)))

    charged: set[tuple[int, int]] = set()      # months a borrow fee was taken
    for d in days:
        if d == SPLIT["date"]:
            led.split(d)
        for kind, payload in sorted(by_day.get(d, []), key=lambda e: e[0]):
            if kind == "cash":
                led.cash_flow(d, *payload)
            else:
                led.trade(payload[0], d, payload[1])

        # Quarterly dividends, on whatever is held that day.
        if d.month in (3, 6, 9, 12) and d.day == 15 or (
                d.month in (3, 6, 9, 12) and d.day in (16, 17) and d.weekday() == 0):
            for lot, yld in DIVIDEND_PAYERS.items():
                qty = led.shares.get(lot, 0.0)
                if qty > 0:
                    amt = round(qty * led.price_of(lot, d) * yld / 4.0, 2)
                    if amt >= 0.01:
                        led.dividend(lot, d, amt)

        # Interest on idle cash, once a month.
        if d.day == 28 and led.cash > 0:
            led.interest(d, round(led.cash * 0.041 / 12.0, 2))

        # Borrow fee on any short, once a month. Charged on the first
        # business day on or after the 20th, so a weekend never skips a
        # month and a short is never silently free to hold.
        if d.day >= 20 and (d.year, d.month) not in charged:
            shorts = [lot for lot in TRADES if led.shares.get(lot, 0.0) < 0]
            if shorts:
                charged.add((d.year, d.month))
                for lot in shorts:
                    led.borrow_fee(lot, d)

        stock = led.stock_value(d)
        flow = next((f for f in CASH_FLOWS if date.fromisoformat(f[0]) == d), None)
        led.nav_rows.append({
            "date": d.isoformat(),
            "nav": f"{led.cash + stock:.2f}",
            "cash": f"{led.cash:.2f}",
            "stock": f"{stock:.2f}",
            "options": "0.00",
            "dividend_accruals": "0.00",
            "interest_accruals": "0.00",
            "deposit": f"{flow[2]:.2f}" if flow and flow[1] == "deposit" else "0.00",
            "withdrawal": f"{flow[2]:.2f}" if flow and flow[1] == "withdrawal" else "0.00",
            "notes": "",
        })
    return led


def positions_rows(led: Ledger) -> list[dict]:
    """The book as of the snapshot, read straight out of the ledger.

    market_value is always USD; cost_basis and last_price stay in the lot's
    own currency, which is the convention the rest of the project reads.
    """
    rows = []
    for lot in list(TRADES) + ["USD_CASH", "EUR_CASH"]:
        if lot in ("USD_CASH", "EUR_CASH"):
            continue
        qty = led.shares.get(lot, 0.0)
        if abs(qty) < 1e-9:
            continue
        ccy = Ledger.currency_of(lot)
        fx = EUR_USD if ccy == "EUR" else 1.0
        px = led.price_of(lot, LAST_TRADING_DAY)
        basis_money = led.cost.get(lot, 0.0)
        rows.append({
            "ticker": lot,
            "instrument_id": Ledger.instrument_of(lot),
            "shares": f"{qty:g}",
            "cost_basis": f"{basis_money / qty:.4f}" if qty else "",
            "last_price": f"{px:.2f}",
            "currency": ccy,
            "market_value": f"{qty * px * fx:.2f}",
            "priced_at": SNAPSHOT.isoformat(),
            "cost_basis_money": f"{basis_money:.2f}",
            "fx_rate_to_base": f"{fx:.4f}",
            "notes": "",
        })
    # Cash is a position too, and leaving it out is how a denominator goes wrong.
    eur_part = 1_200.00
    rows.append({
        "ticker": "USD_CASH", "instrument_id": "USD_CASH",
        "shares": f"{led.cash - eur_part * EUR_USD:.2f}", "cost_basis": "1.0000",
        "last_price": "1.00", "currency": "USD",
        "market_value": f"{led.cash - eur_part * EUR_USD:.2f}",
        "priced_at": SNAPSHOT.isoformat(),
        "cost_basis_money": f"{led.cash - eur_part * EUR_USD:.2f}",
        "fx_rate_to_base": "1.0000", "notes": "",
    })
    rows.append({
        "ticker": "EUR_CASH", "instrument_id": "EUR_CASH",
        "shares": f"{eur_part:.2f}", "cost_basis": "1.0000",
        "last_price": "1.00", "currency": "EUR",
        "market_value": f"{eur_part * EUR_USD:.2f}",
        "priced_at": SNAPSHOT.isoformat(),
        "cost_basis_money": f"{eur_part:.2f}",
        "fx_rate_to_base": f"{EUR_USD:.4f}", "notes": "",
    })
    return rows


def instruments_rows() -> list[dict]:
    rows = []
    for (iid, name, itype, sector, cluster, ccy, _px, _dr, _vol,
         status, av, issuer) in INSTRUMENTS:
        prov = PROVIDER_SECTORS.get(iid, "")
        rows.append({
            "instrument_id": iid,
            "name": name,
            "instrument_type": itype,
            "broker_sub_category": {"etf": "ETF", "stock": "COMMON"}.get(itype, ""),
            "broker_sub_category_as_of": "2026-10-04" if itype in ("etf", "stock") else "",
            "issuer": issuer,
            "sector": sector,
            "cluster_id": cluster,
            "provider_sector": prov,
            "provider_sector_as_of": PROVIDER_AS_OF if prov else "",
            "provider_sector_source": PROVIDER_SOURCE if prov else "",
            "sector_settled": "",
            "isin": f"US{stable_id(iid):09d}1" if itype in ("stock", "etf") else "",
            "av_ticker": av,
            "price_ticker": iid if itype in ("stock", "etf") else "",
            "note_ref": note_for(iid),
            "status": status,
            "added": "2025-01-02",
        })
    return rows


def clusters_rows() -> list[dict]:
    return [{
        "cluster_id": cid, "parent": parent, "layer": layer,
        "in_equity_base": base, "note_ref": cluster_note(cid),
        "status": RETIRED_CLUSTERS.get(cid, "active"),
        "added": "2025-01-02",
    } for cid, parent, layer, base in CLUSTERS]


# ---------------------------------------------------------------------------
# The judgement tables
#
# A demo book has no judgement in it, so these rows are written to exercise
# the shapes the schemas enforce — one of every kind, every vocabulary value
# that matters, the resolved and the still-open, the acted-on and the
# never-acted-on. The wording is deliberately flat and structural. It is not
# anybody's view on anything, and it is not advice.
# ---------------------------------------------------------------------------
# Each entry: id, date, ticker, origin, kind, claim, and whatever the kind needs.
CLAIMS = [
    dict(id="c0004", date="2025-01-02", ticker="QQQ", origin="mine", kind="claim",
         decision="2025 opening allocation", intent="open", conviction="med",
         claim="Index exposure is the base of the book; everything else is a tilt on top of it.",
         wrong_if="The index base stops being the largest single line in the book.",
         check_date="2025-07-02", outcome="right", resolved_date="2025-07-03",
         resolution_note="Still the base. Held.", setups=["macro"], trade="QQQ"),
    dict(id="c0002", date="2025-01-02", ticker="VTI", origin="mine", kind="claim",
         decision="2025 opening allocation", intent="open", conviction="med",
         claim="A total-market fund covers what the Nasdaq-100 leaves out.",
         wrong_if="The two funds stop behaving differently enough to hold both.",
         check_date="2025-07-02", setups=["macro"], trade="VTI",
         outcome="right", resolved_date="2025-07-04",
         resolution_note="Held. The two funds did behave differently enough."),
    dict(id="c0005", date="2025-02-11", ticker="NVDA", origin="fundstrat",
         said_by="A Research Analyst", kind="claim",
         decision="AI infrastructure, first leg", intent="open", conviction="high",
         claim="Demand for accelerator hardware runs ahead of supply while the build-out lasts.",
         wrong_if="Order growth stops being reported, or the build-out is paused.",
         check_date="2025-08-11", setups=["research-call", "breakout"], trade="NVDA",
         outcome="right", resolved_date="2025-08-13",
         resolution_note="Held through the check and added later."),
    dict(id="c0001", date="2025-03-18", ticker="ASML_USD", origin="13d",
         said_by="A Research Letter", kind="claim",
         decision="AI infrastructure, first leg", intent="open", conviction="high",
         claim="The equipment makers are a chokepoint, and a chokepoint prices differently from a supplier.",
         wrong_if="A second credible supplier of the same tooling appears.",
         check_date="2025-09-18", setups=["fundamental"], trade="ASML_USD",
         outcome="partly-right", resolved_date="2025-09-22",
         resolution_note="The chokepoint held; the price did not reward it yet."),
    dict(id="c0003", date="2025-03-04", ticker="IWM", origin="mine", kind="claim",
         intent="open", conviction="low",
         claim="Small caps are the part of the market the index base does not reach.",
         wrong_if="Weekly close below the level recorded against this claim.",
         check_date="2025-09-04", outcome="too-early", resolved_date="2025-09-05",
         resolution_note="Nothing decided either way. Left open.",
         setups=["rotation"], trade="IWM"),
    dict(id="c0006", date="2025-04-08", ticker="TSM", origin="fundstrat",
         said_by="A Research Analyst", kind="claim",
         decision="AI infrastructure, first leg", intent="open", conviction="med",
         claim="One foundry makes most of the leading-edge supply, so it sees the demand before anyone else reports it.",
         wrong_if="Leading-edge share moves meaningfully to a competitor.",
         check_date="2025-10-08", setups=["research-call"], trade="TSM",
         outcome="right", resolved_date="2025-10-10",
         resolution_note="Held. Added on the next deposit."),
    dict(id="c0007", date="2025-04-22", ticker="XLF", origin="mine", kind="claim",
         intent="open", conviction="med",
         claim="Financials are the sector the thematic tilts leave out entirely.",
         wrong_if="The sector stops being a diversifier against the technology weight.",
         check_date="2025-10-22", setups=["rotation"], trade="XLF",
         outcome="unclear", resolved_date="2025-10-24",
         resolution_note="No evidence either way inside six months."),
    dict(id="c0008", date="2025-05-13", ticker="PLTR", origin="x",
         said_by="A Market Commentator", kind="claim",
         decision="AI applications", intent="open", conviction="low",
         claim="The application layer captures value later than the hardware layer does.",
         wrong_if="Revenue growth decelerates for two consecutive reported quarters.",
         check_date="2025-11-13", setups=["breakout"], trade="PLTR",
         outcome="partly-right", resolved_date="2025-11-17",
         resolution_note="Right on direction, wrong on size. Trimmed later."),
    dict(id="c0009", date="2025-05-20", ticker="GDX", origin="13d",
         said_by="A Research Letter", kind="claim",
         decision="Hard-asset tilt", intent="open", conviction="high",
         claim="Miners carry more leverage to the metal price than the metal does.",
         wrong_if="The metal rises and the miners do not follow for a full quarter.",
         check_date="2025-11-20", outcome="right", resolved_date="2025-11-21",
         resolution_note="They followed. Held, and added later.",
         setups=["macro", "trendline"], trade="GDX"),
    dict(id="c0010", date="2025-05-20", ticker="SILJ", origin="13d",
         said_by="A Research Letter", kind="claim",
         decision="Hard-asset tilt", intent="open", conviction="med",
         claim="The junior end of the same trade moves further in both directions.",
         wrong_if="The position stops being small enough to sit through a drawdown.",
         check_date="2025-11-20", setups=["macro"], trade="SILJ",
         outcome="wrong", resolved_date="2025-11-24",
         resolution_note="The junior end fell further than the position could justify."),
    dict(id="c0011", date="2025-06-24", ticker="IGV", origin="mine", kind="claim",
         decision="Software, undiluted", intent="open", conviction="med",
         claim="A software fund owns the theme without the hardware cycle attached to it.",
         wrong_if="The fund's largest holdings stop being software businesses.",
         check_date="2025-12-24", setups=["rotation"], trade="IGV",
         outcome="right", resolved_date="2025-12-29",
         resolution_note="Held. The holdings are still software businesses."),
    dict(id="c0012", date="2025-07-15", ticker="VEGI", origin="13d",
         said_by="A Research Letter", kind="claim",
         decision="Hard-asset tilt", intent="open", conviction="low",
         claim="Food production is a hard asset that is not a metal.",
         wrong_if="The fund turns out to track the broad market rather than the input costs.",
         check_date="2026-01-15", setups=["macro"], trade="VEGI",
         outcome="too-early", resolved_date="2026-01-19",
         resolution_note="Nothing resolved in six months. Left open, smaller."),
    dict(id="c0013", date="2025-07-15", ticker="DE", origin="mine", kind="claim",
         decision="Hard-asset tilt", intent="open", conviction="low",
         claim="The machinery sits upstream of the same demand the fund holds downstream.",
         wrong_if="Equipment orders fall while crop prices hold.",
         check_date="2026-01-15", setups=["fundamental"], trade="DE",
         outcome="unclear", resolved_date="2026-01-19",
         resolution_note="Orders and crop prices moved together, so the test never ran."),
    dict(id="c0014", date="2025-09-09", ticker="MRK", origin="wsj",
         said_by="A Newspaper Column", kind="claim",
         intent="open", conviction="low",
         claim="A defensive dividend payer is the part of the book that is not a bet on growth.",
         wrong_if="The dividend is cut, or the pipeline loses its lead product.",
         check_date="2026-03-09", setups=["fundamental"], trade="MRK",
         outcome="right", resolved_date="2026-03-11",
         resolution_note="Dividend intact. Held as the defensive line."),
    dict(id="c0015", date="2025-09-23", ticker="PHO", origin="13d",
         said_by="A Research Letter", kind="claim",
         decision="Water", intent="open", conviction="med",
         claim="Water infrastructure is spending that happens regardless of the cycle.",
         wrong_if="The fund's holdings turn out to be ordinary industrials.",
         check_date="2026-03-23", setups=["research-call"], trade="PHO"),
    dict(id="c0016", date="2025-11-18", ticker="EEM", origin="mine", kind="claim",
         intent="open", conviction="low",
         claim="Everything else in the book is American; this is the line that is not.",
         wrong_if="The fund stops being a diversifier against the US weight.",
         check_date="2026-05-18", setups=["rotation"], trade="EEM"),
    dict(id="c0017", date="2026-01-13", ticker="GDX", origin="mine", kind="claim",
         decision="Hard-asset tilt", intent="add", conviction="high",
         claim="The first leg worked, so the position is sized up rather than taken off.",
         wrong_if="The trend line recorded against this claim breaks on a weekly close.",
         check_date="2026-07-13", setups=["trendline"], trade="GDX"),
    dict(id="c0018", date="2026-02-10", ticker="MOAT", origin="ft",
         said_by="A Financial Newspaper", kind="claim",
         decision="2026 core rebuild", intent="open", conviction="low",
         claim="A screened core fund is a different bet from a market-cap one.",
         wrong_if="The screen stops producing a holdings list that differs from the index.",
         check_date="2026-08-10", setups=["fundamental"], trade="MOAT"),
    dict(id="c0019", date="2026-04-14", ticker="PLTR", origin="mine", kind="claim",
         decision="AI applications", intent="trim", conviction="med",
         claim="The position outgrew the conviction behind it, so half comes off.",
         wrong_if="The trim turns out to have been the top of a longer move.",
         check_date="2026-10-14", setups=["rotation"], trade="PLTR"),
    dict(id="c0020", date="2026-05-19", ticker="NVDA", origin="mine", kind="claim",
         decision="AI infrastructure, first leg", intent="trim", conviction="med",
         claim="Taking a third off does not end the thesis; it ends the concentration.",
         wrong_if="The remaining position stops being the largest single line.",
         check_date="2026-11-19", setups=["rotation"], trade="NVDA"),
    dict(id="c0021", date="2026-06-09", ticker="XRT", origin="friend",
         said_by="Someone I Know", kind="claim",
         intent="open", conviction="low",
         claim="A small short against a weak part of the consumer is a hedge, not a view.",
         wrong_if="The short costs more in borrow than it saves in a drawdown.",
         check_date="2026-12-09", setups=["head-and-shoulders"], trade="XRT"),

    dict(id="c0022", date="2026-08-11", ticker="SGOV", origin="mine", kind="watch",
         intent="watch", claim="Cash is the position when nothing is worth buying."),
    dict(id="c0023", date="2026-09-01", ticker="COWZ", origin="mine", kind="watch",
         intent="watch", claim="Another screened core fund, held small to see how it behaves."),
    dict(id="c0024", date="2026-09-08", ticker="MSFT", origin="mine", kind="watch",
         intent="watch", claim="Watching whether the software weight is already enough without adding here."),
    dict(id="c0025", date="2026-09-15", ticker="JPM", origin="mine", kind="watch",
         intent="watch", claim="Watching whether one bank is a better expression than the sector fund."),
    dict(id="c0026", date="2026-09-22", ticker="CAT", origin="13d",
         said_by="A Research Letter", kind="watch", intent="watch",
         claim="On the list to look at again, having been sold earlier in the year.",
         why_not="still-waiting"),
    dict(id="c0027", date="2026-09-29", ticker="ADBE", origin="mine", kind="watch",
         intent="watch", claim="Sold once; watching for a reason to look again.",
         why_not="setup-broke"),
    dict(id="c0028", date="2026-10-06", ticker="WPM", origin="mine", kind="watch",
         intent="watch", claim="The single-name version of a theme already held through a fund.",
         why_not="better-opportunity"),

    dict(id="c0029", date="2026-03-03", ticker="VTI", origin="mine", kind="update",
         claim="Added to the core on the March deposit; nothing about the original reasoning changed.",
         trade="VTI"),
    dict(id="c0030", date="2026-07-21", ticker="IWM", origin="mine", kind="update",
         claim="Trimmed a third to fund the July withdrawal. The claim itself was already resolved.",
         trade="IWM"),
    # A live bet on something not yet owned: written down, not acted on. It is
    # the shape that makes claims.follow_through() mean anything, and the
    # reason why_not exists as a column rather than a shrug.
    dict(id="c0031", date="2026-10-06", ticker="COPX", origin="13d",
         said_by="A Research Letter", kind="claim",
         decision="Hard-asset tilt", intent="open", conviction="med",
         claim="Industrial metals are the part of the hard-asset tilt the book does not own yet.",
         wrong_if="The supply argument stops showing up in the price for two quarters.",
         check_date="2027-01-06", why_not="still-waiting",
         setups=["research-call", "macro"]),
]

# id, instrument, level, trigger, kind, action, instruction, claim, verified, ended
LEVELS = [
    # Ended, deliberately: a record of the past must not change because of
    # something that happened later, so this level was live through August and
    # ended in September rather than being edited in place.
    dict(id="l0001", date="2025-03-04", iid="IWM", mult=0.88, when="at_or_below",
         kind="soft", action="re_evaluate", claim_id="c0003",
         instruction="Not an order. A weekly close below this is a reason to look again.",
         ended="2026-09-26", ended_reason="Superseded; a new level has not been set."),
    dict(id="l0002", date="2025-02-11", iid="NVDA", mult=0.78, when="at_or_below",
         kind="hard", action="sell_all", claim_id="c0005", verified="2026-10-06",
         instruction="A real stop order at the broker. If it fills, the position is gone."),
    dict(id="l0003", date="2025-05-20", iid="GDX", mult=0.82, when="at_or_below",
         kind="soft", action="trim_half", claim_id="c0009",
         instruction="Half off if this gives way, because the thesis was the trend."),
    dict(id="l0004", date="2025-05-13", iid="PLTR", mult=0.70, when="at_or_below",
         kind="hard", action="sell_all", claim_id="c0008", verified="2026-10-06",
         instruction="A real stop at the broker on the whole line."),
    dict(id="l0005", date="2025-06-24", iid="IGV", mult=0.85, when="at_or_below",
         kind="soft", action="re_evaluate", claim_id="c0011",
         instruction="Look again, do not sell on the touch."),
    dict(id="l0006", date="2026-01-13", iid="GDX", mult=1.35, when="at_or_above",
         kind="soft", action="trim_half", claim_id="c0017",
         instruction="If it runs this far the position is oversized; take half."),
    dict(id="l0007", date="2025-04-08", iid="TSM", mult=0.80, when="at_or_below",
         kind="hard", action="sell_all", claim_id="c0006", verified="2026-10-06",
         instruction="A real stop at the broker, re-based after the split."),
    dict(id="l0008", date="2026-06-09", iid="XRT", mult=1.22, when="at_or_above",
         kind="hard", action="buy", claim_id="c0021", verified="2026-10-06",
         instruction="A short has no floor, so the stop sits above the entry, at the "
                     "broker, and closing it is a buy rather than a sale."),
    dict(id="l0009", date="2025-09-23", iid="PHO", mult=0.86, when="at_or_below",
         kind="soft", action="re_evaluate", claim_id="c0015",
         instruction="Look again if the infrastructure spending story stops showing up in the price."),
    dict(id="l0010", date="2026-02-10", iid="MOAT", mult=0.90, when="at_or_below",
         kind="soft", action="add", claim_id="c0018",
         instruction="A buy trigger, not a stop: add on weakness while the screen still holds."),
    dict(id="l0011", date="2025-02-04", iid="ADBE", mult=0.84, when="at_or_below",
         kind="hard", action="sell_all", verified="2025-10-10",
         instruction="A real stop that filled; the position is closed.",
         ended="2025-10-14", ended_reason="Stop filled; position closed."),
    dict(id="l0012", date="2025-03-11", iid="CAT", mult=1.28, when="at_or_above",
         kind="soft", action="trim_half",
         instruction="A target that was reached and acted on.",
         ended="2026-04-07", ended_reason="Reached and sold in full instead of trimmed."),
    dict(id="l0013", date="2025-06-03", iid="WPM", mult=0.80, when="at_or_below",
         kind="soft", action="re_evaluate",
         instruction="Looked again, and sold for a different reason than this level.",
         ended="2026-02-17", ended_reason="Position exited; the level never fired."),
    dict(id="l0014", date="2025-01-02", iid="QQQ", mult=0.80, when="at_or_below",
         kind="soft", action="re_evaluate", claim_id="c0004",
         instruction="The first level ever recorded here; replaced rather than edited.",
         ended="2026-03-03", ended_reason="Superseded by the 2026 core rebuild."),
]

# kind, date, target_type, target_id, origin, said_by, body
COMMENTARY = [
    ("thought", "2025-01-02", "market", "", "mine", "",
     "Starting the record today. The point is to know later what I thought now."),
    ("thought", "2025-03-19", "instrument", "ASML", "mine", "",
     "A chokepoint and a supplier look the same on a screen and behave differently."),
    ("commentary", "2025-04-09", "instrument", "TSM", "fundstrat", "A Research Analyst",
     "Said the foundry sees demand before the people who report it do."),
    ("thought", "2025-05-21", "cluster", "Precious Metals", "mine", "",
     "The miners are the leveraged version. That cuts both ways and I keep forgetting the second half."),
    ("commentary", "2025-06-11", "sector", "Tech - Software", "13d", "A Research Letter",
     "Argued the software layer monetises later than the hardware layer."),
    ("thought", "2025-07-16", "cluster", "Agriculture", "mine", "",
     "A theme that spans two sectors is not a tidy theme, and tidying it would be the mistake."),
    ("commentary", "2025-09-10", "instrument", "MRK", "wsj", "A Newspaper Column",
     "Described the dividend as the reason to own it and the pipeline as the risk."),
    ("thought", "2025-11-19", "instrument", "EEM", "mine", "",
     "Bought this because the rest of the book is American, which is a reason, not a thesis."),
    ("thought", "2026-01-14", "cluster", "Precious Metals", "mine", "",
     "Added after it worked. Worth asking later whether that was conviction or momentum."),
    ("commentary", "2026-02-11", "instrument", "MOAT", "ft", "A Financial Newspaper",
     "Made the case that a screened core is a different exposure, not a cheaper one."),
    ("thought", "2026-04-15", "claim", "c0019", "mine", "",
     "Trimmed because the size scared me, not because anything changed. Recording that honestly."),
    ("thought", "2026-05-20", "instrument", "NVDA", "mine", "",
     "A third off. The thesis is intact; the concentration was not."),
    ("commentary", "2026-06-10", "instrument", "XRT", "friend", "Someone I Know",
     "Suggested a small short as a hedge rather than a view. Took it small."),
    ("thought", "2026-07-22", "market", "", "mine", "",
     "Took money out for something unrelated. The book did not decide that; life did."),
    ("thought", "2026-09-02", "cluster", "Dry Powder", "mine", "",
     "Cash went up because nothing looked worth buying, which is itself a position."),
    ("commentary", "2026-10-07", "sector", "Materials - Gold Miners", "x", "A Market Commentator",
     "Pointed out that the trend has run a long way without a weekly close below its line."),
]

# id, kind, title, source, published, storage, location, author, gist
EVIDENCE = [
    ("e0001", "research", "Quarterly review, Q1 2026", "13d", "2026-04-02",
     "private", "2026-04-02-13d-q1-review.pdf", "A Research Letter",
     "The quarterly letter the three theme write-ups came out of."),
    ("e0002", "research", "Market update, July 2026", "fundstrat", "2026-07-16",
     "private", "2026-07-16-fundstrat-market-update.pdf", "A Research Analyst",
     "Monthly deck; the memory-supply section is the part that mattered."),
    ("e0003", "research", "Market update, August 2026", "fundstrat", "2026-08-19",
     "private", "2026-08-19-fundstrat-market-update.pdf", "A Research Analyst",
     "Follow-up deck revisiting the same supply argument."),
    ("e0004", "research", "Market update, September 2026", "fundstrat", "2026-09-23",
     "private", "2026-09-23-fundstrat-market-update.pdf", "A Research Analyst",
     "Deck with the digital-asset section."),
    ("e0005", "article", "Leverage in the hedge fund system", "ft", "2026-10-10",
     "private", "2026-10-10-ft-hedge-fund-leverage.pdf", "A Financial Newspaper",
     "Background on how levered the system is, filed against the market rather than a holding."),
    ("e0006", "article", "What a wide-moat screen actually selects", "ft", "2026-02-09",
     "link", "https://example.com/demo/wide-moat-screens", "A Financial Newspaper",
     "The piece behind the screened-core claim."),
    ("e0007", "article", "Foundry capacity and the leading edge", "wsj", "2025-04-07",
     "link", "https://example.com/demo/foundry-capacity", "A Newspaper Column",
     "Background on why leading-edge share concentrates."),
    ("e0008", "article", "Why miners lag the metal, and then do not", "13d", "2025-05-19",
     "link", "https://example.com/demo/miners-versus-metal", "A Research Letter",
     "The leverage argument, stated plainly."),
    ("e0009", "article", "Water infrastructure spending through a cycle", "13d", "2025-09-22",
     "link", "https://example.com/demo/water-infrastructure", "A Research Letter",
     "Why the spending is not cyclical."),
    ("e0010", "article", "Agricultural input costs and producer margins", "13d", "2025-07-14",
     "link", "https://example.com/demo/ag-input-costs", "A Research Letter",
     "The food-production-as-hard-asset case."),
    ("e0011", "note", "Why the index base is the base", "mine", "2025-01-02",
     "link", "https://example.com/demo/index-base-note", "",
     "A note to self, written the day the book was opened."),
    ("e0012", "article", "Borrow costs on a small short", "x", "2026-06-08",
     "link", "https://example.com/demo/borrow-costs", "A Market Commentator",
     "What a short actually costs to hold."),
    ("e0013", "article", "Dividend cover at a large pharma", "wsj", "2025-09-08",
     "link", "https://example.com/demo/dividend-cover", "A Newspaper Column",
     "The defensive case and its risk."),
    ("e0014", "article", "Emerging markets as a diversifier", "mine", "2025-11-17",
     "link", "https://example.com/demo/em-diversifier", "",
     "Read while deciding whether the book needed anything non-US."),
    ("e0015", "article", "Screened core funds versus market cap", "ft", "2026-02-09",
     "link", "https://example.com/demo/screened-versus-cap", "A Financial Newspaper",
     "The comparison that made the case concrete."),
    ("e0016", "article", "Reading a trend line on a weekly close", "x", "2026-10-07",
     "link", "https://example.com/demo/weekly-close-trendlines", "A Market Commentator",
     "On why the weekly close and not the intraday touch."),
    # storage=repo: the file itself is committed, so the row must point at a
    # path that exists. Everything above is either a link or a bare filename
    # for a document that is deliberately not in the repository.
    ("e0017", "chart", "Small caps, daily", "mine", "2026-08-15",
     "repo", "notes/screenshots/2026-08-15-IWM-price-1d.png", "",
     "The chart the small-cap claim was measured against."),
    ("e0018", "chart", "The broad market, monthly", "mine", "2026-10-09",
     "repo", "notes/screenshots/2026-10-09-SPX-macro-1d.png", "",
     "Filed against the market rather than any holding."),
    ("e0019", "chart", "Accelerator hardware, daily", "mine", "2026-05-19",
     "repo", "notes/screenshots/2026-05-19-NVDA-price-1d.png", "",
     "Read before taking a third of the position off."),
    ("e0020", "chart", "Lithography, daily", "mine", "2025-03-18",
     "repo", "notes/screenshots/2025-03-18-ASML-price-1d.png", "",
     "Read when the chokepoint claim was opened."),
    ("e0021", "chart", "Water infrastructure, daily", "mine", "2025-09-23",
     "repo", "notes/screenshots/2025-09-23-PHO-price-1d.png", "",
     "Read when the water line was opened."),
    ("e0022", "chart", "Financials, daily", "mine", "2025-04-22",
     "repo", "notes/screenshots/2025-04-22-XLF-price-1d.png", "",
     "Read when the financials line was opened."),
]

# The research write-ups that ship with this repository, each tied back to the
# document it came out of.
SUMMARIES = [
    ("s0001", "e0001", "notes/picks/2026-04-02-13d-automation-ai.md"),
    ("s0002", "e0001", "notes/picks/2026-04-02-13d-copper.md"),
    ("s0003", "e0001", "notes/picks/2026-04-02-13d-water.md"),
    ("s0004", "e0002", "notes/picks/2026-07-16-fundstrat-memory-bottleneck.md"),
    ("s0005", "e0003", "notes/picks/2026-08-19-fundstrat-memory-cisco.md"),
    ("s0006", "e0004", "notes/picks/2026-09-23-fundstrat-crypto.md"),
]

# target_type, target_id, source_type, source_id, page, why
LINKS = [
    ("claim", "c0005", "document", "e0002", "14", "The supply argument behind the first leg."),
    ("claim", "c0001", "document", "e0001", "23", "Where the chokepoint case is made."),
    ("claim", "c0006", "document", "e0007", "", "Background on leading-edge concentration."),
    ("claim", "c0009", "document", "e0008", "", "The leverage argument, as read at the time."),
    ("claim", "c0010", "document", "e0008", "2", "Same piece, the junior-miner paragraph."),
    ("claim", "c0012", "document", "e0010", "", "The input-cost case."),
    ("claim", "c0015", "document", "e0009", "", "Why the spending is not cyclical."),
    ("claim", "c0014", "document", "e0013", "", "Dividend cover and the pipeline risk."),
    ("claim", "c0016", "document", "e0014", "", "Read before opening the line."),
    ("claim", "c0018", "document", "e0006", "", "The screen-selection piece."),
    ("claim", "c0018", "document", "e0015", "", "And the comparison against market cap."),
    ("claim", "c0021", "document", "e0012", "", "What the borrow actually costs."),
    ("claim", "c0004", "document", "e0011", "", "The note written the day it was opened."),
    ("instrument", "ASML", "summary", "s0001", "", "Automation and AI, from the quarterly letter."),
    ("instrument", "NVDA", "summary", "s0004", "", "The memory-supply write-up."),
    ("instrument", "TSM", "summary", "s0004", "2", "Same write-up, the foundry paragraph."),
    ("instrument", "GDX", "summary", "s0002", "", "Filed here because the copper work covers the miners."),
    ("instrument", "PHO", "summary", "s0003", "", "The water write-up."),
    ("instrument", "MRK", "document", "e0013", "", "The defensive case."),
    ("instrument", "MOAT", "document", "e0006", "", "Why this fund and not a market-cap one."),
    ("instrument", "XRT", "document", "e0012", "", "Borrow cost on the short."),
    ("instrument", "EEM", "document", "e0014", "", "The diversifier argument."),
    ("cluster", "AI Infrastructure", "summary", "s0004", "", "The theme's own write-up."),
    ("cluster", "AI Infrastructure", "document", "e0002", "14", "And the deck it came from."),
    ("cluster", "AI Applications", "summary", "s0001", "", "The automation write-up."),
    ("cluster", "Water Infrastructure", "summary", "s0003", "", "The theme's own write-up."),
    ("cluster", "Precious Metals", "document", "e0008", "", "The leverage argument."),
    ("cluster", "Agriculture", "document", "e0010", "", "The input-cost argument."),
    ("cluster", "Software", "summary", "s0005", "", "The follow-up deck's software section."),
    ("cluster", "Core US", "document", "e0015", "", "Screened versus market cap."),
    ("cluster", "Dry Powder", "document", "e0005", "", "Why holding cash is a position."),
    ("sector", "Tech - Semiconductors", "summary", "s0004", "", "Memory supply."),
    ("sector", "Materials - Gold Miners", "document", "e0016", "", "On weekly closes."),
    ("sector", "Materials - Copper Miners", "summary", "s0002", "", "The copper write-up."),
    ("sector", "Industrials", "summary", "s0003", "", "Water infrastructure sits here."),
    ("sector", "Healthcare", "document", "e0013", "", "Dividend cover."),
    ("sector", "Crypto - Bitcoin", "summary", "s0006", "", "The digital-asset section."),
    ("claim", "c0003", "document", "e0017", "", "The chart this claim was read off."),
    ("claim", "c0020", "document", "e0019", "", "The chart read before the trim."),
    ("claim", "c0001", "document", "e0020", "", "The chart read when it was opened."),
    ("claim", "c0015", "document", "e0021", "", "The chart read when it was opened."),
    ("claim", "c0007", "document", "e0022", "", "The chart read when it was opened."),
    ("macro", "macro", "document", "e0018", "", "The broad-market chart."),
    ("macro", "macro", "document", "e0005", "", "System leverage, filed against the market."),
    ("macro", "macro", "document", "e0011", "", "The opening note."),
    ("macro", "macro", "summary", "s0006", "", "Read as a macro call rather than a holding one."),
]


def atr_of(series: dict[date, float], d: date, window: int = 90) -> float:
    """Average absolute daily move over `window` sessions.

    A true ATR needs a high and a low; a demo book has neither, so this is the
    close-to-close stand-in. It is stored on a claim as a point-in-time fact
    and never recomputed, which is the behaviour worth demonstrating.
    """
    days = [k for k in sorted(series) if k <= d][-window - 1:]
    if len(days) < 3:
        return round(series[days[-1]] * 0.02, 2)
    moves = [abs(series[b] - series[a]) for a, b in zip(days, days[1:])]
    return round(sum(moves) / len(moves), 2)


def sma_of(series: dict[date, float], d: date, window: int = 200) -> float:
    days = [k for k in sorted(series) if k <= d][-window:]
    return round(sum(series[k] for k in days) / len(days), 2)


def claims_rows(led: Ledger) -> tuple[list[dict], list[dict]]:
    """Claim rows and their setup rows.

    price, atr, sma_200 and nav_pct are all computed here, from the ledger, on
    the claim's own date — never typed, and never updated afterwards. That is
    the whole point of the four columns: they record what the market looked
    like when the decision was made.
    """
    nav_by_day = {date.fromisoformat(r["date"]): float(r["nav"]) for r in led.nav_rows}
    trade_index: dict[tuple[str, str], str] = {
        (t["symbol"], t["trade_date"]): t["trade_id"] for t in led.transactions
    }
    rows, setups = [], []
    for c in CLAIMS:
        d = date.fromisoformat(c["date"])
        iid = Ledger.instrument_of(c["ticker"])
        series = led.walks[iid]
        is_claim = c["kind"] == "claim"
        trade_ids = ""
        if c.get("trade"):
            found = [tid for (sym, iso), tid in trade_index.items()
                     if sym == c["trade"] and iso == c["date"]]
            trade_ids = " ".join(sorted(found))
        price = atr = sma = navpct = ""
        if is_claim:
            price = round(on_or_before(series, d), 2)
            atr = atr_of(series, d)
            sma = sma_of(series, d)
            nav = nav_by_day.get(d) or next(
                v for k, v in sorted(nav_by_day.items()) if k >= d)
            qty = abs(sum(q for iso, q in TRADES.get(c["ticker"], [])
                          if date.fromisoformat(iso) <= d))
            # Nothing held means no share of the book to state. A zero would
            # read as "sized at nothing" rather than "never bought".
            navpct = round(min(100.0, qty * price / nav * 100.0), 2) \
                if (nav and qty) else ""
        rows.append({
            "id": c["id"], "date": c["date"], "recorded": c["date"],
            "ticker": c["ticker"], "instrument_id": iid,
            "origin": c["origin"], "said_by": c.get("said_by", ""),
            "decision": c.get("decision", ""), "kind": c["kind"],
            "claim": c["claim"],
            "wrong_if": c.get("wrong_if", "") if is_claim else "",
            "check_date": c.get("check_date", "") if is_claim else "",
            "price": price,
            "currency": Ledger.currency_of(c["ticker"]) if is_claim else "",
            "conviction": c.get("conviction", "") if is_claim else "",
            "trade_ids": trade_ids,
            "atr": atr, "sma_200": sma, "nav_pct": navpct,
            "intent": c.get("intent", ""), "why_not": c.get("why_not", ""),
            "resolved_date": c.get("resolved_date", ""),
            "outcome": c.get("outcome", ""),
            "resolution_note": c.get("resolution_note", ""),
            "note_ref": "",
        })
        for s in c.get("setups", []):
            setups.append({"claim_id": c["id"], "setup": s, "added": c["date"]})
    return rows, setups


def levels_rows(led: Ledger) -> list[dict]:
    """Levels, priced off the instrument on the day the level was set.

    A level is never edited: the two that were superseded carry an
    `ended_date` and a reason instead, and a replacement is a new row.
    """
    rows = []
    for lv in LEVELS:
        d = date.fromisoformat(lv["date"])
        series = led.walks[lv["iid"]]
        level = round(on_or_before(series, d) * lv["mult"], 2)
        rows.append({
            "id": lv["id"], "date": lv["date"], "recorded": lv["date"],
            "instrument_id": lv["iid"], "level": level,
            "currency": "", "trigger_when": lv["when"], "kind": lv["kind"],
            "action": lv["action"], "instruction": lv["instruction"],
            "claim_id": lv.get("claim_id", ""),
            "broker_verified": lv.get("verified", ""),
            "ended_date": lv.get("ended", ""),
            "ended_reason": lv.get("ended_reason", ""),
            "note_ref": "",
        })
    return rows


def commentary_rows() -> list[dict]:
    rows = []
    for i, (kind, d, ttype, tid, origin, said_by, body) in enumerate(COMMENTARY, 1):
        rows.append({
            "id": f"m{i:04d}", "date": d, "recorded": d, "kind": kind,
            "target_type": ttype, "target_id": tid, "origin": origin,
            "said_by": said_by, "body": body, "promoted_to": "", "note_ref": "",
        })
    return rows


def evidence_rows() -> tuple[list[dict], list[dict], list[dict]]:
    ev = [{
        "id": i, "kind": k, "title": t, "source": s, "published": p,
        "storage": st, "location": loc, "added": p, "author": a, "gist": g,
    } for i, k, t, s, p, st, loc, a, g in EVIDENCE]
    summ = [{"id": i, "evidence_id": e, "location": loc, "added": "2026-10-08"}
            for i, e, loc in SUMMARIES]
    links = [{
        "target_type": tt, "target_id": ti, "source_type": st,
        "source_id": si, "page": pg, "why": why, "added": "2026-10-08",
    } for tt, ti, st, si, pg, why in LINKS]
    return ev, summ, links


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
# The curated tables are quoted; the broker tables are not. That is not a
# style choice — it is what the sync writes, and the commit guard compares a
# staged table against HEAD as raw text.
CURATED = {
    "clusters.csv", "instruments.csv", "claims.csv", "claim_setups.csv",
    "levels.csv", "commentary.csv", "evidence.csv", "evidence_links.csv",
    "evidence_summaries.csv",
}


def write(name: str, rows: list[dict], columns: list[str],
          numeric: tuple[str, ...] = ()) -> int:
    df = pd.DataFrame(rows, columns=columns)
    for col in numeric:
        df[col] = pd.to_numeric(df[col].replace("", pd.NA), errors="coerce")
    path = DATA_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if name in CURATED:
        df.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)
    else:
        df.to_csv(path, index=False)
    return len(df)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="validate the tables already on disk and write nothing")
    args = ap.parse_args()

    if args.check:
        validate()
        return

    from src import claims as claims_mod, clusters as clusters_mod
    from src import instruments as inst_mod, levels as levels_mod
    from src import commentary as comm_mod, evidence as ev_mod

    led = run_ledger()
    ev, summ, links = evidence_rows()
    claim_rows, setup_rows = claims_rows(led)

    written = [
        ("sectors.csv", "real data, shipped as published", None),
        ("clusters.csv", clusters_rows(), clusters_mod.COLUMNS),
        ("instruments.csv", instruments_rows(), inst_mod.COLUMNS),
        ("positions.csv", positions_rows(led), [
            "ticker", "instrument_id", "shares", "cost_basis", "last_price",
            "currency", "market_value", "priced_at", "cost_basis_money",
            "fx_rate_to_base", "notes"]),
        ("transactions.csv", led.transactions, list(led.transactions[0])),
        ("cash_transactions.csv", led.cash_rows, list(led.cash_rows[0])),
        ("borrow_fees.csv", led.borrow_rows, list(led.borrow_rows[0])),
        ("corporate_actions.csv", led.corporate_rows, list(led.corporate_rows[0])),
        ("nav_history.csv", led.nav_rows, list(led.nav_rows[0])),
        ("claims.csv", claim_rows, claims_mod.COLUMNS),
        ("claim_setups.csv", setup_rows, claims_mod.SETUP_COLUMNS),
        ("levels.csv", levels_rows(led), levels_mod.COLUMNS),
        ("commentary.csv", commentary_rows(), comm_mod.COLUMNS),
        ("evidence.csv", ev, ev_mod.EVIDENCE_COLUMNS),
        ("evidence_summaries.csv", summ, ev_mod.SUMMARY_COLUMNS),
        ("evidence_links.csv", links, ev_mod.LINK_COLUMNS),
    ]

    numeric_for = {
        "claims.csv": ("price", "atr", "sma_200", "nav_pct"),
        "levels.csv": ("level",),
    }
    print("Demo book\n")
    for name, rows, columns in written:
        if columns is None:
            print(f"  {name:<24} {rows}")
            continue
        n = write(name, rows, list(columns), numeric_for.get(name, ()))
        print(f"  {name:<24} {n:>5} rows")

    # option_events.csv is written with a header and nothing else: the demo
    # book holds no options, and a missing table and an empty one are not the
    # same thing to anything that reads it.
    write("option_events.csv", [], [
        "trade_id", "date", "symbol", "description", "asset_category",
        "sub_category", "currency", "put_call", "strike", "expiry",
        "multiplier", "quantity", "trade_price", "mark_price", "cost_basis",
        "proceeds", "realized_pnl", "mtm_pnl", "security_id"])
    print(f"  {'option_events.csv':<24} {0:>5} rows (header only — no options held)")

    last = led.nav_rows[-1]
    print(f"\n  account value {float(last['nav']):>12,.2f}"
          f"   cash {float(last['cash']):>11,.2f}"
          f"   stock {float(last['stock']):>11,.2f}")
    print(f"  realized to date {led.realized:>9,.2f}")
    print("\nNow run:  venv/bin/python ops/seed_demo_book.py --check")


def validate() -> None:
    """Put every table through the same code the commit guard runs."""
    from src import (claims as claims_mod, clusters as clusters_mod,
                     commentary as comm_mod, evidence as ev_mod,
                     instruments as inst_mod, levels as levels_mod,
                     sectors as sec_mod, ibkr_sync)
    checks = [
        ("sectors.csv", sec_mod.load),
        ("clusters.csv", clusters_mod.load),
        ("instruments.csv", inst_mod.load),
        ("claims.csv", claims_mod.load_claims),
        ("claim_setups.csv", claims_mod.load_setups),
        ("levels.csv", levels_mod.load_levels),
        ("commentary.csv", comm_mod.load),
        ("evidence.csv", ev_mod.load_evidence),
        ("evidence_summaries.csv", ev_mod.load_summaries),
        ("evidence_links.csv", ev_mod.load_links),
    ]
    bad = 0
    for name, loader in checks:
        try:
            df = loader()
            print(f"  ok     {name:<24} {len(df):>5} rows")
        except Exception as exc:                      # noqa: BLE001 - report all
            bad += 1
            print(f"  FAILED {name:<24} {type(exc).__name__}: {str(exc)[:200]}")

    inst_p = DATA_DIR / "instruments.csv"
    cl_p = DATA_DIR / "clusters.csv"
    sec_p = DATA_DIR / "sectors.csv"
    fks = [
        ("instruments.cluster_id -> clusters",
         lambda: clusters_mod.check_known(
             inst_mod.load(inst_p)["cluster_id"].dropna(), cl_p)),
        ("instruments.sector -> sectors",
         lambda: sec_mod.check_known(inst_mod.load(inst_p)["sector"].dropna(), sec_p)),
        ("instruments.provider_sector -> sectors",
         lambda: sec_mod.check_known(
             inst_mod.dated_provider_sectors(inst_mod.load(inst_p)), sec_p)),
        ("claims.instrument_id -> instruments",
         lambda: inst_mod.check_known(claims_mod.load_claims()["instrument_id"], inst_p)),
        ("claim_setups.claim_id -> claims",
         lambda: claims_mod.check_setup_claims()),
        ("levels.instrument_id -> instruments",
         lambda: inst_mod.check_known(levels_mod.load_levels()["instrument_id"], inst_p)),
        ("evidence locations", lambda: ev_mod.check_locations(require_file=True)),
        ("evidence summaries -> evidence",
         lambda: ev_mod.check_summaries()),
        ("evidence summary locations",
         lambda: ev_mod.check_summary_locations()),
        ("evidence links -> both ends", lambda: ev_mod.check_links()),
        ("one row per document",
         lambda: ev_mod.check_one_row_per_document(ev_mod.load_evidence())),
        ("claims note_refs resolve", lambda: _empty(claims_mod.stale_refs())),
        ("levels note_refs resolve", lambda: _empty(levels_mod.stale_refs())),
    ]
    for label, fn in fks:
        try:
            fn()
            print(f"  ok     {label}")
        except Exception as exc:                      # noqa: BLE001 - report all
            bad += 1
            print(f"  FAILED {label}: {type(exc).__name__}: {str(exc)[:200]}")

    shares = ibkr_sync.unreconciled_shares(
        pd.read_csv(DATA_DIR / "transactions.csv"),
        pd.read_csv(DATA_DIR / "positions.csv"),
        pd.read_csv(DATA_DIR / "corporate_actions.csv"))
    odd = shares[shares["why"] != "consistent with pre-log holding"]
    if len(odd):
        bad += 1
        print(f"  FAILED share reconciliation: {len(odd)} unexplained")
        for _, r in odd.iterrows():
            print(f"           {r['symbol']}: {r['gap']:+g}")
    else:
        print("  ok     share reconciliation — every holding explained by its trades")

    print(f"\n{'ALL CLEAN' if not bad else str(bad) + ' PROBLEM(S)'}")
    if bad:
        sys.exit(1)


def _empty(df) -> None:
    if len(df):
        raise ValueError(f"{len(df)} unresolved reference(s): {list(df.iloc[0])}")


if __name__ == "__main__":
    main()
