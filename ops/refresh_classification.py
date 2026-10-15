"""Fetch the vendor's sector for every instrument that can have one.

Why this exists. `instruments.sector` is Yaron's own label, typed by hand, and
CLAUDE.md says it wins. It still does. But his words on 2026-10-01 were:
"it's not my decision, it's how the S&P does their decisions, not mine" — so
the page's sector view needs an answer nobody in this repository typed.

What this is NOT. It is not GICS. Real GICS is a licensed product and cannot
be fetched. This is one vendor's classification, which tracks GICS closely and
is not the same thing, and every row records which vendor and when. Anything
reading it must say so rather than implying an authority it does not have.

Run from the repo root:

    venv/bin/python ops/refresh_classification.py            # report only
    venv/bin/python ops/refresh_classification.py --write
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import instruments as inst  # noqa: E402
from src import market  # noqa: E402

SOURCE = "Yahoo via yfinance"

# Both shapes the same vendor answers in, mapped onto data/sectors.csv labels.
# Explicit rather than normalised-by-algorithm: an unmapped name must stop,
# and a rule that lowercases and strips would quietly accept a new word.
#
# Every VALUE here must also work as a KEY (lower-cased), because this
# script's own output becomes next run's input: a written cell is read back
# by agrees_with_existing() -> normalise() to decide whether a fresh fetch
# confirms it. Found 2026-10-04: five labels this map produces —
# Financials, Consumer Discretionary, Consumer Staples, Comm Services,
# Materials — did not round-trip, so comparing an already-written cell
# against an identical fresh answer raised KeyError, read as False, and
# froze that row's date while reporting the vendor as disagreeing with
# itself. test_every_alias_output_round_trips_through_normalise guards this.
SECTOR_ALIASES: dict[str, str] = {
    "technology": "Technology",
    "financial services": "Financials",
    "financial_services": "Financials",
    "financials": "Financials",                        # round-trip: our own output
    "consumer cyclical": "Consumer Discretionary",
    "consumer_cyclical": "Consumer Discretionary",
    "consumer discretionary": "Consumer Discretionary", # round-trip: our own output
    "consumer defensive": "Consumer Staples",
    "consumer_defensive": "Consumer Staples",
    "consumer staples": "Consumer Staples",             # round-trip: our own output
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "utilities": "Utilities",
    "basic materials": "Materials",
    "basic_materials": "Materials",
    "materials": "Materials",                           # round-trip: our own output
    "energy": "Energy",
    "communication services": "Comm Services",
    "communication_services": "Comm Services",
    "comm services": "Comm Services",                   # round-trip: our own output
    "real estate": "Real Estate",
    "real_estate": "Real Estate",
    "realestate": "Real Estate",
}


def normalise(name: str) -> str:
    """A vendor sector name as a `data/sectors.csv` label. Raises if unknown."""
    key = str(name).strip().lower()
    if key not in SECTOR_ALIASES:
        raise KeyError(
            f"Vendor sector {name!r} is not mapped.\n"
            f"  Add it to SECTOR_ALIASES in this file, pointing at a sector_id "
            f"that exists in data/sectors.csv.\n"
            f"  Guessing would put a wrong sector somewhere nothing can "
            f"distinguish it from a right one."
        )
    return SECTOR_ALIASES[key]


def vendor_sector(ticker: str) -> tuple[str | None, str]:
    """(sector label or None, a note explaining a None)."""
    import yfinance as yf

    info = yf.Ticker(ticker).info or {}
    raw = info.get("sector")
    if not raw:
        kind = info.get("quoteType", "unknown")
        return None, f"no sector reported (quoteType {kind})"
    return normalise(raw), ""


def agrees_with_existing(existing_raw: str, new_sector: str) -> bool:
    """Whether a freshly fetched sector confirms what the cell already holds.

    Some rows in data/instruments.csv already carry a `provider_sector` cell
    from an earlier fetch of unknown provenance — raw and upper-case
    ("FINANCIAL SERVICES") rather than today's normalised label
    ("Financials"). That is the same fact in different spelling, not a
    disagreement, so it must not block a write the way a real one does.

    Only a cell that normalises to a genuinely different sector — or one this
    vocabulary cannot parse at all, so agreement cannot be confirmed — counts
    as a disagreement. A vendor changing its mind is information to show him,
    never a correction to apply unseen (hence the caller holds the write back
    rather than this function silently picking a winner).
    """
    try:
        return normalise(existing_raw) == new_sector
    except KeyError:
        return False


def _held_instrument_ids(positions_path: Path) -> set[str]:
    """Instrument ids carrying real market value right now.

    Summed across every broker lot, the same way `claims._is_held()` and
    `exposure._holdings()` do it: ASML_USD and ASML_EUR are one holding, not
    two, and reading a single lot could call a still-open position closed.
    Never the `status` column — CLAUDE.md is explicit that a position's
    status is typed and can lag what the broker actually shows.
    """
    if not positions_path.exists() or positions_path.stat().st_size == 0:
        return set()
    pos = pd.read_csv(positions_path, dtype=str, keep_default_na=False)
    if "instrument_id" not in pos.columns or "market_value" not in pos.columns:
        return set()
    mv = pd.to_numeric(pos["market_value"], errors="coerce").fillna(0.0)
    totals = mv.groupby(pos["instrument_id"]).sum()
    return set(totals[totals.abs() > 1e-9].index)


def main(instruments_path: Path | None = None,
        positions_path: Path | None = None) -> None:
    """Report, or with `--write` record, a vendor sector for every held,
    non-ETF instrument.

    `instruments_path`/`positions_path` default to the real tables; a test
    passes throwaway ones so this can run against a tmp_path fixture instead
    of the real portfolio.
    """
    write = "--write" in sys.argv
    instruments_path = instruments_path or inst.INSTRUMENTS_PATH
    positions_path = positions_path or (inst.PROJECT_ROOT / "data" / "positions.csv")

    instruments_df = inst.load(instruments_path)
    held = _held_instrument_ids(positions_path)

    today = date.today().isoformat()
    scope = instruments_df[
        instruments_df["instrument_id"].isin(held)
        & (instruments_df["instrument_type"] != "etf")
    ].fillna("")

    skipped: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    disagreed: list[tuple[str, str, str]] = []
    sectored = 0

    for _, row in scope.iterrows():
        iid = row["instrument_id"]
        ticker = market.provider_ticker(iid, instruments_df)
        if ticker is None:
            # cash/fx (market.NON_MARKET_KINDS) — no sector to fetch, not a failure.
            skipped.append((iid, "no provider ticker (cash/FX)"))
            continue

        try:
            sector, note = vendor_sector(ticker)
        except KeyError as e:
            # normalise() raised: the vendor used a word SECTOR_ALIASES does
            # not map. Reported, never guessed — a wrong sector is worse than
            # a missing one because nothing downstream can tell them apart.
            failed.append((iid, str(e).splitlines()[0]))
            continue
        except Exception as e:  # noqa: BLE001 — yfinance being slow/down is not fatal
            failed.append((iid, f"{type(e).__name__}: {e}"))
            continue

        if sector is None:
            skipped.append((iid, note))
            continue

        existing = row["provider_sector"]
        if existing and not agrees_with_existing(existing, sector):
            # Never silently overwritten: a vendor changing its mind is
            # information for him to see, not a correction to apply unseen.
            # The DATE is left alone too, which means that once this run's
            # output scrolls away the only remaining trace is a row whose
            # provider_sector_as_of has stopped advancing. `ops/data_gaps.py`
            # reports that as "vendor sector left behind" — without it the
            # disagreement was visible for one run and then invisible.
            disagreed.append((iid, existing, sector))
            print(f"  {iid:10} DISAGREE   had {existing!r}, vendor now says "
                  f"{sector!r} — left untouched")
            continue

        confirms = f" (confirms {existing!r})" if existing else ""
        if write:
            try:
                inst.set_provider_sector(iid, sector, SOURCE, today,
                                         path=instruments_path)
            except Exception as e:  # noqa: BLE001 — one bad row must not cost
                # every row already written before it. Previously this call
                # sat outside any try/except: one pre-existing problem
                # anywhere in instruments.csv raised out of the loop entirely,
                # which skipped every instrument still to come and the
                # trailing summary below, even though rows already written
                # stayed on disk (set_provider_sector() writes immediately,
                # per instrument — there is no larger transaction to lose).
                failed.append((iid, f"write failed: {type(e).__name__}: {e}"))
                continue
        print(f"  {iid:10} {sector}{confirms}")
        sectored += 1

    print(f"\n{sectored} of {len(scope)} held, non-ETF instrument(s) sectored.")
    if skipped:
        print(f"{len(skipped)} skipped (no sector to fetch):")
        for iid, why in skipped:
            print(f"  {iid}: {why}")
    if disagreed:
        print(f"{len(disagreed)} disagreement(s) with the vendor's current "
              f"answer — left untouched, his record unchanged:")
        for iid, old, new in disagreed:
            print(f"  {iid}: has {old!r}, vendor now says {new!r}")
    if failed:
        print(f"{len(failed)} failed to fetch:")
        for iid, why in failed:
            print(f"  {iid}: {why}")

    unresolved = len(failed) + sum(1 for _, why in skipped if "no sector reported" in why)
    print(f"{unresolved} left without a vendor sector.")

    if write:
        print(f"\nWrote {sectored} row(s) to {instruments_path.name}.")
    else:
        print("\nReport only. Re-run with --write to update the file.")


if __name__ == "__main__":
    main()
