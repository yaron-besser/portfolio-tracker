"""
Refresh an ETF's sector breakdown from a real provider, not a guess.

Why: a hand-typed sector split goes stale the moment the fund's real mix
drifts, and some rows were never sourced at all — just a plausible split
guessed from memory. `exposure.coverage()` names which ETFs an answer
depends on and how old each breakdown is; this script is how a stale or
guessed one gets replaced with a real provider's own numbers.

Two providers, because neither covers everything:

    Alpha Vantage ETF_PROFILE   issuer-reported sector weights
    yfinance funds_data         Morningstar sector weights

Both report at parent-sector level (the 11 SPY sectors), which is coarser than
the hand-written sub-sector rows it replaces. That is a deliberate trade: real
parent-level data beats invented sub-sector data, and exposure rolls up to
parents anyway.

**Three rules keep this honest.**

1. A provider's own vocabulary is mapped onto `data/sectors.csv` labels
   explicitly. An unmapped name discards that provider's whole breakdown — it
   is never dropped from one, which would silently shrink the fund. The run
   continues on whatever other provider is usable, says so loudly, and exits
   non-zero, so the gap is dealt with without throwing away a dozen funds
   already fetched and the API quota they cost.
2. A breakdown covering less than MIN_COVERAGE of the fund is **refused**.
   Alpha Vantage returned 28.9% for MAGS and 0.5% for EEM; normalising those
   would have meant inventing most of a fund while the numbers still summed
   to 100%. Where coverage is merely incomplete (89-99%, cash and unclassified
   holdings), the weights are scaled and the original coverage is recorded in
   `source` so the adjustment is auditable.

Run from the repo root:

    venv/bin/python ops/refresh_etf_compositions.py QQQ IWM        # report only
    venv/bin/python ops/refresh_etf_compositions.py QQQ IWM --write
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import exposure, sectors  # noqa: E402
from src.positions_fundamentals import _load_api_key  # noqa: E402

COMPOSITIONS_PATH = (Path(__file__).resolve().parent.parent
                     / "data" / "market" / "etf_compositions.csv")

# A breakdown thinner than this is a provider failure, not a thin fund.
MIN_COVERAGE = 0.85

# Above this, a provider is reporting percentages where fractions are expected.
# Without the ceiling such a provider wins every comparison by construction,
# because coverage is now what picks the winner.
MAX_COVERAGE = 1.02

# Coverage this close is the same answer, so PROVIDERS order decides instead.
# Two complete breakdowns can differ by one floating-point step — 1.0 against
# 0.9999999999999999 — and letting that pick the source rewrites every row for
# the fund, with a different `source` and `as_of`, for no real change.
TIE_TOLERANCE = 0.005

# Every (ticker, provider, label) a provider returned that we cannot map.
# Collected rather than raised mid-run, so one vocabulary gap on the last
# ticker cannot discard a dozen funds already fetched. main() resets it and
# exits non-zero if anything is in it, so the gap is never silently lost.
UNMAPPED_SEEN: list[tuple[str, str, str]] = []

# Provider sector name -> sector_id in data/sectors.csv. Explicit on purpose:
# an unmapped name must stop the run, not vanish.
PROVIDER_SECTORS: dict[str, str] = {
    # Alpha Vantage (GICS, uppercase)
    "INFORMATION TECHNOLOGY": "Technology",
    "COMMUNICATION SERVICES": "Comm Services",
    "CONSUMER DISCRETIONARY": "Consumer Discretionary",
    "CONSUMER STAPLES": "Consumer Staples",
    "HEALTHCARE": "Healthcare",
    "HEALTH CARE": "Healthcare",
    "INDUSTRIALS": "Industrials",
    "UTILITIES": "Utilities",
    "MATERIALS": "Materials",
    "ENERGY": "Energy",
    "FINANCIALS": "Financials",
    "REAL ESTATE": "Real Estate",
    # yfinance (Morningstar, snake_case)
    "technology": "Technology",
    "communication_services": "Comm Services",
    "consumer_cyclical": "Consumer Discretionary",
    "consumer_defensive": "Consumer Staples",
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "utilities": "Utilities",
    "basic_materials": "Materials",
    "energy": "Energy",
    "financial_services": "Financials",
    "real_estate": "Real Estate",
    "realestate": "Real Estate",   # yfinance uses both spellings
}


def _map_sector(name: str) -> str:
    key = str(name).strip()
    if key not in PROVIDER_SECTORS:
        raise KeyError(
            f"Provider sector {name!r} is not mapped.\n"
            f"  Add it to PROVIDER_SECTORS in this file, pointing at a "
            f"sector_id that exists in data/sectors.csv.\n"
            f"  Dropping it would silently shrink the fund."
        )
    return PROVIDER_SECTORS[key]


def from_alpha_vantage(ticker: str) -> tuple[dict[str, float], str]:
    """(sector_id -> weight, provider label). Empty dict if no usable data."""
    r = requests.get(
        "https://www.alphavantage.co/query",
        params={"function": "ETF_PROFILE", "symbol": ticker, "apikey": _load_api_key()},
        timeout=30,
    )
    rows = (r.json() or {}).get("sectors") or []
    out: dict[str, float] = {}
    for s in rows:
        w = float(s.get("weight", 0) or 0)
        if w > 0:
            out[_map_sector(s.get("sector"))] = out.get(_map_sector(s.get("sector")), 0) + w
    return out, "Alpha Vantage ETF_PROFILE"


def from_yfinance(ticker: str) -> tuple[dict[str, float], str]:
    import yfinance as yf

    sw = yf.Ticker(ticker).funds_data.sector_weightings or {}
    out: dict[str, float] = {}
    for k, v in sw.items():
        w = float(v or 0)
        if w > 0:
            out[_map_sector(k)] = out.get(_map_sector(k), 0) + w
    return out, "Morningstar via yfinance"


PROVIDERS = [
    (from_alpha_vantage, "issuer"),   # issuer-reported
    (from_yfinance, "vendor"),        # third-party
]


def fetch(ticker: str) -> dict | None:
    """Best available breakdown for one ETF, or None if nobody has one.

    Every provider is asked and the one covering most of the fund wins;
    PROVIDERS order only breaks a tie.

    Why not a fixed order. Until 2026-10-01 Alpha Vantage was preferred
    unconditionally because it is issuer-reported. For IWM it returned no Real
    Estate row at all and covered 89.7%; that was normalised to 100%, spreading
    the missing 6.9% of the fund across the sectors that remained, and it
    replaced a complete Morningstar breakdown. The account's Real Estate
    exposure fell from 0.40% to 0.09% with nothing having happened in the
    market. Coverage is what makes a breakdown trustworthy, so coverage decides.
    """
    attempts = []
    candidates = []
    unmapped = []
    for fn, quality in PROVIDERS:
        try:
            weights, label = fn(ticker)
        except KeyError as e:
            # A provider using a word we have no mapping for. Its breakdown is
            # discarded whole, so nothing is silently shrunk — but the gap must
            # still be dealt with, so it is reported and main() exits non-zero.
            # It is no longer raised here: once every provider is asked, a
            # loser's unmapped label would abort a run that had already fetched
            # a dozen good funds and spent their API quota.
            unmapped.append((fn.__name__, str(e)))
            attempts.append(f"{fn.__name__}: unmapped sector {e}")
            continue
        except Exception as e:          # noqa: BLE001 — a provider being down is not fatal
            attempts.append(f"{fn.__name__}: {type(e).__name__}")
            continue
        coverage = sum(weights.values())
        attempts.append(f"{fn.__name__}: {coverage:.1%}")
        if weights and MIN_COVERAGE <= coverage <= MAX_COVERAGE:
            candidates.append((coverage, quality, label, weights))

    UNMAPPED_SEEN.extend((ticker, *u) for u in unmapped)
    for name, label in unmapped:
        print(f"  ⚠ {ticker:5} {name} returned the unmapped sector {label}; its "
              f"whole breakdown was discarded. Add the label to PROVIDER_SECTORS.")

    if not candidates:
        if unmapped:
            # Nobody usable AND a vocabulary gap: the original rule applies.
            raise KeyError(unmapped[0][1])
        print(f"  {ticker:5} NO USABLE DATA — {'; '.join(attempts)}")
        return None

    # Highest coverage wins, but anything within TIE_TOLERANCE of the best is
    # treated as equal, and then PROVIDERS order decides — issuer over vendor.
    best = max(c[0] for c in candidates)
    coverage, quality, label, weights = next(
        c for c in candidates if c[0] >= best - TIE_TOLERANCE)
    scaled = {k: v / coverage for k, v in weights.items()}
    src = f"{label} {date.today().isoformat()}"
    if coverage < 0.995:
        src += f" (normalised from {coverage:.1%} coverage)"
    return {"ticker": ticker, "weights": scaled, "source": src,
            "source_quality": quality, "coverage": coverage}


def reportable_parents(path: Path | None = None) -> set[str]:
    """Parent sectors some provider is actually capable of returning.

    Cash is not one of them, and neither is crypto: a cash sleeve is exactly
    the uncovered residual that MIN_COVERAGE tolerates and normalisation
    spreads away. Without this set, `dropped_parents` fires on every fund
    carrying a hand-typed `Cash` row — which is precisely the stale
    `index estimate` data this script exists to replace. A warning that cries
    wolf on the funds most in need of refreshing is a warning nobody reads,
    and the next IWM goes through unnoticed.
    """
    rollup = sectors.parents_map(path or sectors.SECTORS_PATH)
    return {rollup.get(s, s) for s in PROVIDER_SECTORS.values()}


# A parent sector that survives a refresh but keeps less than this share of
# the weight it used to carry has not been relabelled — it has been replaced
# by a different answer, and it is reported like a sector that vanished.
#
# Why half. The vanish-only version of this check was built from IWM, where
# Real Estate disappeared outright, and it was structurally blind to the case
# that actually cost the most: DBA's single `Materials - Agriculture` row at
# 100% of the fund became eleven Morningstar equity sectors with Materials at
# 10.7%, Materials stayed in both sets, nothing fired, and $3,180 of a $110K
# book left Materials silently. A real fund's parent weight moves a few points
# between two fetches; it does not halve. So the threshold sits far above
# ordinary drift and far below the collapse — the same reasoning as
# `reportable_parents()`: a warning that cries wolf is a warning nobody reads.
MAX_PARENT_SHRINK = 0.5


def dropped_parents(old: pd.DataFrame, results: list[dict],
                    path: Path | None = None) -> dict[str, list[str]]:
    """Parent sectors a refresh would take away from a fund, keyed by ticker.

    Two shapes of the same loss, because a sector can leave a fund without
    leaving its breakdown:

        the parent is gone        -> the sector's name, e.g. "Real Estate"
        the parent kept the label
        and lost the weight       -> "most of Materials (100.0% -> 10.7%)"

    A sub-sector collapsing into its own parent is neither — XLK going from
    three `Tech - *` labels to one `Technology` is the documented trade this
    script makes, and the parent holds exactly what it held before, which is
    why the comparison is made on summed parent weight rather than per label.
    Nor is losing a residual no provider reports (`reportable_parents()`).
    """
    rollup = sectors.parents_map(path or sectors.SECTORS_PATH)
    reportable = reportable_parents(path)
    out: dict[str, list[str]] = {}
    for r in results:
        rows = old.loc[old["ticker"] == r["ticker"]]
        before = _by_parent(zip(rows["sector"], pd.to_numeric(rows["weight"])), rollup)
        after = _by_parent(r["weights"].items(), rollup)
        lost = sorted((set(before) - set(after)) & reportable)
        shrunk = sorted(
            f"most of {p} ({before[p]:.1%} -> {after[p]:.1%})"
            for p in (set(before) & set(after) & reportable)
            if after[p] < MAX_PARENT_SHRINK * before[p]
        )
        if lost or shrunk:
            out[r["ticker"]] = lost + shrunk
    return out


def _by_parent(pairs, rollup: dict[str, str]) -> dict[str, float]:
    """Sector weights summed up to their parents.

    Summed, never set-membership: three `Tech - *` rows and one `Technology`
    row are the same exposure, and only the total says so.
    """
    out: dict[str, float] = {}
    for sector, weight in pairs:
        parent = rollup.get(sector, sector)
        out[parent] = out.get(parent, 0.0) + float(weight)
    return out


def apply(results: list[dict], path: Path = COMPOSITIONS_PATH) -> pd.DataFrame:
    """Replace each refreshed ETF's rows; leave every other ETF untouched."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df["weight"] = pd.to_numeric(df["weight"])
    refreshed = {r["ticker"] for r in results}
    kept = df[~df["ticker"].isin(refreshed)]

    new_rows = []
    for r in results:
        sectors.check_known(r["weights"].keys())     # foreign key, before writing
        for sector_id, w in sorted(r["weights"].items(), key=lambda x: -x[1]):
            new_rows.append({
                "ticker": r["ticker"], "sector": sector_id, "weight": round(w, 6),
                "top_holding": "", "source": r["source"],
                "source_quality": r["source_quality"],
                "as_of": date.today().isoformat(),
            })
    out = pd.concat([kept, pd.DataFrame(new_rows)], ignore_index=True)
    return out.sort_values(["ticker", "weight"], ascending=[True, False])


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("Usage: refresh_etf_compositions.py TICKER [TICKER ...] [--write]")

    UNMAPPED_SEEN.clear()
    results = []
    for i, t in enumerate(args):
        r = fetch(t)
        if r:
            results.append(r)
            print(f"  {t:5} {len(r['weights']):2} sectors, {r['coverage']:.1%} coverage "
                  f"-> {r['source_quality']:7} {r['source']}")
        if i < len(args) - 1:
            time.sleep(13)              # Alpha Vantage free tier: 5 calls/min

    if not results:
        raise SystemExit("\nNothing refreshed.")
    print(f"\n{len(results)} of {len(args)} refreshed.")

    # Reporting only, so a missing or empty file must not cost the fetches.
    try:
        existing = pd.read_csv(COMPOSITIONS_PATH, dtype=str, keep_default_na=False)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        existing = pd.DataFrame(columns=["ticker", "sector"])
    for t, gone in dropped_parents(existing, results).items():
        print(f"  ⚠ {t:5} loses {', '.join(gone)} — the new source puts far "
              f"less of the fund there than the old one did. That is a "
              f"different answer, not a relabel.")

    if "--write" in sys.argv:
        out = apply(results)
        exposure._schema().validate(out.assign(weight=pd.to_numeric(out["weight"])), lazy=True)
        out.to_csv(COMPOSITIONS_PATH, index=False, quoting=csv.QUOTE_NONNUMERIC)
        print(f"Wrote {len(out)} rows to {COMPOSITIONS_PATH.name}")
    else:
        print("Report only. Re-run with --write to update the file.")

    # Last, so the data is already saved: a vocabulary gap must not be lost,
    # but it must not cost the work either.
    if UNMAPPED_SEEN:
        raise SystemExit(
            f"\n{len(UNMAPPED_SEEN)} unmapped provider sector(s). Each discarded one "
            f"provider's whole breakdown:\n" +
            "\n".join(f"  {t} via {fn}: {lbl.splitlines()[0]}" for t, fn, lbl in UNMAPPED_SEEN) +
            "\n  Add them to PROVIDER_SECTORS and re-run those tickers.")


if __name__ == "__main__":
    main()
