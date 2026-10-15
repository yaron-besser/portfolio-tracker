"""Per-stock fundamentals, fetched once and cached.

Pulls fundamentals (PE, PBV, ROE, margins, beta, etc.) from Alpha Vantage's
OVERVIEW endpoint and caches them on disk.

⚠️ The comparison half is GONE. This module used to join those figures to
Damodaran industry data so a holding could be read against its sector median;
that chain was deleted on 2026-09-29 (see the note at the foot of this file).
What remains is fetch and cache. There is no bulk-refresh entry point —
``refresh_all_positions()`` was deleted with the rest, and this docstring went
on advertising it.

Architecture:
    .env                            ALPHA_VANTAGE_API_KEY lives here
    src/positions_fundamentals.py   this module — fetch + cache only
    data/fundamentals/<TICKER>.json one cached AV response per instrument

Rate limits:
    Alpha Vantage free tier = 25 calls/day, 5 calls/min, and a 7-day cache.
    Run ``instruments.gaps()`` or read ``av_ticker`` to see which instruments
    can be fetched at all — a blank one means no company fundamentals exist
    (cash, ETF, FX). Never write the count here; it changes with the book.

Provider-agnostic design:
    The wrapper hides Alpha Vantage. If we ever switch providers
    (FMP, EODHD, etc.), only ``_fetch_av_overview()`` needs to change.
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests


# ===== Paths =====

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
CACHE_DIR = PROJECT_ROOT / "data" / "fundamentals"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ===== Configuration =====

# Ticker translation and the skip list used to live here, as a dict and a set
# that nothing else could see. Both now come from data/instruments.csv via
# src/instruments.py — one place says what a ticker means (Phase 2c).
#   IBKR_TO_AV_TICKER -> the `av_ticker` column
#   SKIP_TICKERS      -> `av_ticker` left blank (cash, ETFs, futures, options)

# Alpha Vantage industry name -> Damodaran industry name. AV uses uppercase
# Yahoo-style labels; Damodaran uses his own taxonomy. Extend this map as
# you encounter new industries in your portfolio.
AV_TO_DAMODARAN_INDUSTRY: dict[str, str] = {
    "SEMICONDUCTORS": "Semiconductor",
    "SEMICONDUCTOR EQUIPMENT & MATERIALS": "Semiconductor Equip",
    "DRUG MANUFACTURERS - GENERAL": "Drugs (Pharmaceutical)",
    "DRUG MANUFACTURERS - SPECIALTY & GENERIC": "Drugs (Pharmaceutical)",
    "BIOTECHNOLOGY": "Drugs (Biotechnology)",
    "SOFTWARE - APPLICATION": "Software (System & Application)",
    "SOFTWARE - INFRASTRUCTURE": "Software (System & Application)",
    "INFORMATION TECHNOLOGY SERVICES": "Computer Services",
    "COMPUTER HARDWARE": "Computers/Peripherals",
    "COMMUNICATION EQUIPMENT": "Telecom. Equipment",
    "TELECOM SERVICES": "Telecom. Services",
    "AUTO MANUFACTURERS": "Auto & Truck",
    "AUTO PARTS": "Auto Parts",
    "AUTO & TRUCK DEALERSHIPS": "Retail (Automotive)",
    "ELECTRONIC COMPONENTS": "Electronics (General)",
    "ELECTRICAL EQUIPMENT & PARTS": "Electrical Equipment",
    "MEDICAL DEVICES": "Healthcare Products",
    "MEDICAL INSTRUMENTS & SUPPLIES": "Healthcare Products",
    "DIAGNOSTICS & RESEARCH": "Healthcare Support Services",
    "HEALTHCARE PLANS": "Healthcare Support Services",
    "MEDICAL CARE FACILITIES": "Hospitals/Healthcare Facilities",
    "TOBACCO": "Tobacco",
    "BEVERAGES - WINERIES & DISTILLERIES": "Beverage (Alcoholic)",
    "BEVERAGES - BREWERS": "Beverage (Alcoholic)",
    "BEVERAGES - NON-ALCOHOLIC": "Beverage (Soft)",
    "PACKAGED FOODS": "Food Processing",
    "RESTAURANTS": "Restaurant/Dining",
    "OIL & GAS INTEGRATED": "Oil/Gas (Integrated)",
    "OIL & GAS E&P": "Oil/Gas (Production and Exploration)",
    "OIL & GAS MIDSTREAM": "Oil/Gas Distribution",
    "OIL & GAS EQUIPMENT & SERVICES": "Oilfield Svcs/Equip.",
    "OIL & GAS REFINING & MARKETING": "Oil/Gas (Integrated)",
    "INDUSTRIAL DISTRIBUTION": "Retail (Distributors)",
    "SPECIALTY INDUSTRIAL MACHINERY": "Machinery",
    "FARM & HEAVY CONSTRUCTION MACHINERY": "Machinery",
    "BUILDING PRODUCTS & EQUIPMENT": "Building Materials",
    "ENGINEERING & CONSTRUCTION": "Engineering/Construction",
    "AEROSPACE & DEFENSE": "Aerospace/Defense",
    "AIRLINES": "Air Transport",
    "RAILROADS": "Transportation (Railroads)",
    "TRUCKING": "Trucking",
    "MARINE SHIPPING": "Shipbuilding & Marine",
    "ASSET MANAGEMENT": "Investments & Asset Management",
    "BANKS - DIVERSIFIED": "Bank (Money Center)",
    "BANKS - REGIONAL": "Banks (Regional)",
    "CAPITAL MARKETS": "Brokerage & Investment Banking",
    "INSURANCE - LIFE": "Insurance (Life)",
    "INSURANCE - PROPERTY & CASUALTY": "Insurance (Prop/Cas.)",
    "INSURANCE - DIVERSIFIED": "Insurance (General)",
    "INSURANCE - REINSURANCE": "Reinsurance",
    "REIT - RESIDENTIAL": "R.E.I.T.",
    "REIT - INDUSTRIAL": "R.E.I.T.",
    "REIT - OFFICE": "R.E.I.T.",
    "REIT - RETAIL": "Retail (REITs)",
    "REIT - HEALTHCARE FACILITIES": "R.E.I.T.",
    "REAL ESTATE - DIVERSIFIED": "Real Estate (General/Diversified)",
    "REAL ESTATE SERVICES": "Real Estate (Operations & Services)",
    "UTILITIES - REGULATED ELECTRIC": "Utility (General)",
    "UTILITIES - REGULATED WATER": "Utility (Water)",
    "UTILITIES - INDEPENDENT POWER PRODUCERS": "Power",
    "UTILITIES - RENEWABLE": "Green & Renewable Energy",
    "GOLD": "Precious Metals",
    "SILVER": "Precious Metals",
    "STEEL": "Steel",
    "ALUMINUM": "Metals & Mining",
    "OTHER INDUSTRIAL METALS & MINING": "Metals & Mining",
    "OTHER PRECIOUS METALS & MINING": "Precious Metals",
    "COPPER": "Metals & Mining",
    "THERMAL COAL": "Coal & Related Energy",
    "PAPER & PAPER PRODUCTS": "Paper/Forest Products",
    "LUMBER & WOOD PRODUCTION": "Paper/Forest Products",
    "RESORTS & CASINOS": "Hotel/Gaming",
    "LODGING": "Hotel/Gaming",
    "LEISURE": "Recreation",
    "GAMBLING": "Hotel/Gaming",
    "ENTERTAINMENT": "Entertainment",
    "BROADCASTING": "Broadcasting",
    "PUBLISHING": "Publishing & Newspapers",
    "ADVERTISING AGENCIES": "Advertising",
    "APPAREL MANUFACTURING": "Apparel",
    "APPAREL RETAIL": "Retail (Special Lines)",
    "FOOTWEAR & ACCESSORIES": "Shoe",
    "FURNISHINGS, FIXTURES & APPLIANCES": "Furn/Home Furnishings",
    "HOUSEHOLD & PERSONAL PRODUCTS": "Household Products",
    "RESIDENTIAL CONSTRUCTION": "Homebuilding",
    "GROCERY STORES": "Retail (Grocery and Food)",
    "DISCOUNT STORES": "Retail (General)",
    "DEPARTMENT STORES": "Retail (General)",
    "INTERNET RETAIL": "Retail (General)",
    "SPECIALTY RETAIL": "Retail (Special Lines)",
    "HOME IMPROVEMENT RETAIL": "Retail (Building Supply)",
    "FOOD DISTRIBUTION": "Food Wholesalers",
    "CHEMICALS": "Chemical (Basic)",
    "SPECIALTY CHEMICALS": "Chemical (Specialty)",
    "AGRICULTURAL INPUTS": "Farming/Agriculture",
    "PACKAGING & CONTAINERS": "Packaging & Container",
    "WASTE MANAGEMENT": "Environmental & Waste Services",
    "EDUCATION & TRAINING SERVICES": "Education",
    "STAFFING & EMPLOYMENT SERVICES": "Business & Consumer Services",
    "CONSULTING SERVICES": "Business & Consumer Services",
    "SECURITY & PROTECTION SERVICES": "Business & Consumer Services",
    "INFORMATION SERVICES": "Information Services",
    "SCIENTIFIC & TECHNICAL INSTRUMENTS": "Computers/Peripherals",
}

# Comparison metrics: (AV field, Damodaran dataset, Damodaran metric, display
# name, value type). Value types: "ratio" (pure number like PE), "fraction"
# (decimal like 0.30 = 30%), "factor" (like beta).
METRIC_MAP: list[tuple[str, str, str, str, str]] = [
    ("PERatio",              "us_industry_pe",           "trailing_pe",                       "PE (trailing)",     "ratio"),
    ("ForwardPE",            "us_industry_pe",           "forward_pe",                        "PE (forward)",      "ratio"),
    ("PEGRatio",             "us_industry_pe",           "peg_ratio",                         "PEG",               "ratio"),
    ("PriceToBookRatio",     "us_industry_pbv",          "pbv",                               "P/B",               "ratio"),
    ("PriceToSalesRatioTTM", "us_industry_ps",           "price_to_sales",                    "P/S (TTM)",         "ratio"),
    ("EVToEBITDA",           "us_industry_ev_multiples", "ev_to_ebitda_trailing",             "EV/EBITDA",         "ratio"),
    ("ReturnOnEquityTTM",    "us_industry_roe",          "roe_unadj",                         "ROE",               "fraction"),
    ("ProfitMargin",         "us_industry_margins",      "net_margin",                        "Net margin",        "fraction"),
    ("OperatingMarginTTM",   "us_industry_margins",      "pretax_unadj_operating_margin",     "Operating margin",  "fraction"),
    ("Beta",                 "us_industry_betas",        "beta",                              "Beta",              "factor"),
    ("DividendYield",        "us_industry_holdings",     None,                                "Dividend yield",    "fraction"),  # no direct industry-level dividend yield in our manifest yet
]


# ===== Internals =====

def _load_api_key() -> str:
    """Read ALPHA_VANTAGE_API_KEY from the project .env file."""
    if not ENV_PATH.exists():
        raise FileNotFoundError(f".env not found at {ENV_PATH}")
    for raw_line in ENV_PATH.read_text().splitlines():
        line = raw_line.strip()
        if line.startswith("ALPHA_VANTAGE_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if not value:
                raise ValueError("ALPHA_VANTAGE_API_KEY is empty in .env")
            return value
    raise ValueError("ALPHA_VANTAGE_API_KEY not found in .env")


def _ibkr_to_av(ticker: str) -> str:
    """Broker ticker -> Alpha Vantage ticker, from the reference table.

    A blank `av_ticker` means the instrument has no company fundamentals
    (cash, ETFs, futures, options) — this returns "" and callers skip it.
    That replaces the old SKIP_TICKERS set.
    """
    from src import instruments

    iid = instruments.resolve(ticker)
    try:
        return instruments.get(iid)["av_ticker"]
    except KeyError:
        # Unknown instrument: fall back to the raw ticker rather than crash a
        # fetch, but say so — silence here would cache the wrong company (P2).
        print(f"  ! {ticker} is not in data/instruments.csv; using {iid!r} as-is")
        return iid


def _cache_path(av_ticker: str) -> Path:
    return CACHE_DIR / f"{av_ticker}.json"


# Stamped into every cached file so its age is a fact, not a guess.
FETCHED_AT = "_fetched_at"
FETCHED_AT_IS_APPROX = "_fetched_at_approximate"


def fetched_at(cache_file: Path) -> str:
    """When this cached file was fetched, ISO date, or '' if unknowable.

    Reads the stamp inside the file first. File mtime is only a fallback for
    the 42 files cached before the stamp existed, and it is a clue rather than
    a fact — copying a file resets it, so a restored backup would look fetched
    today. Those rows carry `_fetched_at_approximate: true` and say so.
    """
    if not cache_file.exists():
        return ""
    try:
        stamp = json.loads(cache_file.read_text()).get(FETCHED_AT)
    except (json.JSONDecodeError, OSError):
        return ""
    if stamp:
        return str(stamp)
    return date.fromtimestamp(cache_file.stat().st_mtime).isoformat()


def age_days(cache_file: Path) -> int | None:
    """How old the cached data is, or None if the file has no readable date."""
    stamp = fetched_at(cache_file)
    if not stamp:
        return None
    try:
        return (date.today() - date.fromisoformat(stamp)).days
    except ValueError:
        return None


def staleness_warning(av_ticker: str, max_age_days: int = 7) -> str:
    """'' when the cache is fresh; otherwise a sentence saying how old it is.

    Same contract as instruments.price_warning(): a number with no age looks
    identical whether it is today's or three months old, and that silence is
    the danger (P2).
    """
    cache = _cache_path(av_ticker)
    if not cache.exists():
        return f"No fundamentals cached for {av_ticker}."
    age = age_days(cache)
    if age is None:
        return f"{av_ticker} fundamentals have no fetch date — treat as unknown age."
    if age <= max_age_days:
        return ""
    approx = ""
    try:
        if json.loads(cache.read_text()).get(FETCHED_AT_IS_APPROX):
            approx = " (approximate — taken from the file date)"
    except (json.JSONDecodeError, OSError):
        pass
    return (
        f"{av_ticker} fundamentals are {age} days old{approx}. "
        f"Refresh with: fetch_fundamentals('{av_ticker}', force_refresh=True)"
    )


def _is_fresh(cache_file: Path, max_age_days: int) -> bool:
    """Fresh by the stamp inside the file, falling back to its mtime."""
    age = age_days(cache_file)
    return age is not None and age < max_age_days


def _fetch_av_overview(av_ticker: str, api_key: str) -> dict:
    """One Alpha Vantage OVERVIEW call. Provider-specific code lives here."""
    response = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "OVERVIEW", "symbol": av_ticker, "apikey": api_key},
        timeout=30,
    )
    data = response.json()
    if "Symbol" not in data:
        msg = f"Alpha Vantage returned no data for {av_ticker}."
        if "Note" in data:
            msg += f" Note: {data['Note']}"
        if "Information" in data:
            msg += f" Information: {data['Information']}"
        raise RuntimeError(msg)
    return data


def _parse_float(value) -> Optional[float]:
    """Convert AV's mixed string/number/sentinel values into a float or None."""
    if value in (None, "None", "-", "", "0"):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ===== Public API =====

def get_fundamentals(
    ticker: str,
    *,
    max_age_days: int = 7,
    force_refresh: bool = False,
) -> dict:
    """Fetch fundamentals for one IBKR-side ticker, with disk cache.

    Translates IBKR ticker -> Alpha Vantage ticker, checks the disk
    cache, fetches from AV only if the cache is missing or stale,
    writes the response to disk, and returns the parsed dict.
    """
    av_ticker = _ibkr_to_av(ticker)
    if not av_ticker:
        # A blank av_ticker in data/instruments.csv means "no company
        # fundamentals exist for this" — cash, an ETF, a future, an option.
        # That is a real economic fact, not missing data (§5.6).
        from src import instruments

        itype = instruments.get(instruments.resolve(ticker))["instrument_type"]
        raise ValueError(
            f"{ticker} is a {itype}; company fundamentals do not apply. "
            f"Set av_ticker in data/instruments.csv if that is wrong."
        )
    cache = _cache_path(av_ticker)

    if not force_refresh and _is_fresh(cache, max_age_days):
        return json.loads(cache.read_text())

    data = _fetch_av_overview(av_ticker, _load_api_key())
    # Stamp the fetch date INTO the file. Alpha Vantage returns no date of
    # its own, so without this the only clue is the file's mtime — which a
    # copy or a restore silently resets.
    data[FETCHED_AT] = date.today().isoformat()
    cache.write_text(json.dumps(data, indent=2))
    return data


# ─── Removed 2026-09-29: the auto-fundamentals-into-notes writer ─────────
#
# `position_vs_industry()`, `write_fundamentals_to_notes()`,
# `write_fundamentals_for_all_positions()`, `refresh_all_positions()` and
# their two render helpers are gone. All five were dead:
#
#   - the first three raised `KeyError: 'kind'`, a positions.csv column
#     deleted in Phase 2d, so they crashed on the first line that touched
#     data;
#   - every one of them needed the archived `damodaran` module and refused
#     to run until it was restored;
#   - `refresh_all_positions` and `write_fundamentals_for_all_positions`
#     were referenced from nowhere in the repo at all.
#
# What survives above is the part that works: the Alpha Vantage fetch, its
# on-disk cache, and the staleness reporting.
#
# Restoring the comparison means restoring `_archive/src/damodaran.py` and
# `_archive/data/damodaran/` first, then rewriting against instruments.csv
# rather than the deleted column. Do not restore the note-writing half
# without deciding again whether a machine should write into a note — the
# markers it left behind are still readable in two closed notes.
