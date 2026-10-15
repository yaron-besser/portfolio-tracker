"""Take the instrument type from the broker, and keep its finer label.

Why this exists. `instruments.instrument_type` is typed by hand, and the
broker states it per position in every statement — `src.ibkr_import` already
parses `assetCategory` and `subCategory` off every `OpenPosition` and then
throws them away. A field the broker is authoritative about should not be one
Yaron can get wrong. IBKR also draws distinctions the hand-typed vocabulary
cannot: ADR, REIT and NY-registered shares all collapse to `stock` today.

Why a new column rather than new vocabulary values. ASML is a *stock* that
happens to be an ADR. `etf` and `adr` are not alternatives to each other, so
putting both in one field would make `instrument_type == "stock"` — which
other code relies on — start missing rows. The broad type stays in
`instrument_type`; the broker's finer label goes in `broker_sub_category`,
which nothing filters on yet.

⚠️ Disagreements are reported, never resolved. `broker_sub_category` is
written for every instrument the latest statement covers, unconditionally,
dated in `broker_sub_category_as_of`. `instrument_type` is NEVER written by
this script — a cell that is already typed, even wrongly, is left exactly as
it is, and the mismatch is printed so Yaron can decide. Overwriting would hide
a real disagreement; staying silent about it would hide a real error. Neither
is this script's call to make. (A review on 2026-10-04 found an earlier
version of this script filled a BLANK `instrument_type` cell, which required
making the schema column nullable — and that reopened the exact hole the
brief's `broker_sub_category` column exists to close: a blanked-out type
loaded, committed and vanished from every gap report in total silence.
`instrument_type` is required for every row, broker sync or not; the "fill
where blank" idea was deleted along with the nullability it needed.)

⚠️ Scope: this reads `OpenPositions` only (via `extract_open_positions()`),
because that is the one section `assetCategory`/`subCategory` are already
parsed from. A cash balance held as an `FxPosition` is out of scope — IBKR's
`FxPositions` section is parsed by `extract_cash_positions()`, which does not
carry those two fields today.

Run from the repo root:

    venv/bin/python ops/sync_instrument_types.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import instruments as inst  # noqa: E402
from src.ibkr_import import (  # noqa: E402
    _latest_cached_xml,
    extract_open_positions,
    iter_statements,
    parse_xml,
)

# IBKR's (assetCategory, subCategory) -> this project's broad `instrument_type`
# vocabulary (ops/vocab/instrument_type). STK is the only category IBKR
# splits further: a fund is wrapped in the same STK category as every other
# listed security, with ETF visible only in subCategory. Every other category
# maps on assetCategory alone, whatever its subCategory says — an ADR, a REIT
# and an NY-registered share are all still `stock` (CLAUDE.md: that finer
# distinction belongs in `broker_sub_category`, never in this field).
BROAD_TYPES: dict[str, str] = {
    "STK": "stock",
    "STK:ETF": "etf",
    "CASH": "cash",
    "FUT": "future",
    "OPT": "option",
}


def broad_type(asset_category: str, sub_category: str) -> str:
    """IBKR's (assetCategory, subCategory) -> the broad type this project
    tracks in `instruments.instrument_type`.

    Raises on an asset_category never mapped here, rather than defaulting to
    'stock' — a category this file does not understand is a gap in the
    mapping, not evidence the position is common stock. A wrong instrument
    type is worse than a missing one.
    """
    cat = str(asset_category).strip()
    sub = str(sub_category).strip()
    if cat == "STK" and sub == "ETF":
        return BROAD_TYPES["STK:ETF"]
    if cat not in BROAD_TYPES:
        raise KeyError(
            f"Unknown IBKR assetCategory {asset_category!r} (subCategory "
            f"{sub_category!r}). Add it to BROAD_TYPES in this file once you "
            f"know what broad type it should map to — guessing 'stock' would "
            f"be worse than leaving it unresolved."
        )
    return BROAD_TYPES[cat]


def broker_broad_types(
    stmt,
) -> tuple[pd.DataFrame, list[tuple[str, str]], list[tuple[str, str]]]:
    """One row per instrument the statement's `OpenPositions` covers:
    `instrument_id`, `broad_type` (blank if `assetCategory` is unmapped), and
    the broker's own `broker_sub_category`, verbatim.

    Also returns, separately:
    - `unresolved`: symbols that could not be matched to any instrument at
      all (no row in the returned table).
    - `unmapped`: instruments that WERE resolved but whose `assetCategory`
      `broad_type()` does not understand — these DO get a row (blank
      `broad_type`), because `broker_sub_category` was read just fine and
      writing it is not the thing that failed. A silent skip here would be
      exactly the kind of quiet loss this script exists to stop.

    Collapsed by instrument_id: a holding split across currencies (`ASML` in
    EUR and in USD) is one company, and `broad_type` does not change by
    currency, so the first successful mapping seen settles it for the group.
    But the lots are NOT guaranteed to share a `broker_sub_category` — his
    real statement has `ASML` filed as `COMMON` on AEB in EUR and
    `NY REG SHRS` on NASDAQ in USD, the same company under two different IBKR
    labels. Picking either one and discarding the other would itself be the
    kind of silent loss this task exists to stop, so every distinct label
    seen is kept, `;`-separated — the same convention `isin` and
    `claims.trade_ids` already use in this project, so it reads with
    `claims.split_paths()`, never `==`.
    """
    positions = extract_open_positions(stmt)
    unresolved: list[tuple[str, str]] = []
    unmapped: list[tuple[str, str]] = []
    unresolved_seen: set[str] = set()
    unmapped_seen: set[str] = set()
    by_instrument: dict[str, dict] = {}
    for _, r in positions.iterrows():
        symbol = r["symbol"]
        try:
            iid = inst.resolve(symbol)
        except ValueError as e:
            key = symbol or "(blank symbol)"
            if key not in unresolved_seen:
                unresolved.append((key, str(e)))
                unresolved_seen.add(key)
            continue

        entry = by_instrument.setdefault(
            iid, {"broad_type": "", "sub_categories": []})
        sub = str(r["sub_category"]).strip()
        if sub and sub not in entry["sub_categories"]:
            entry["sub_categories"].append(sub)

        if not entry["broad_type"]:
            try:
                entry["broad_type"] = broad_type(r["asset_category"], r["sub_category"])
            except KeyError as e:
                if iid not in unmapped_seen:
                    unmapped.append((iid, str(e).splitlines()[0]))
                    unmapped_seen.add(iid)

    rows = [
        {"instrument_id": iid, "broad_type": v["broad_type"],
         "broker_sub_category": ";".join(v["sub_categories"])}
        for iid, v in by_instrument.items()
    ]
    cols = ["instrument_id", "broad_type", "broker_sub_category"]
    return pd.DataFrame(rows, columns=cols), unresolved, unmapped


def disagreements(instruments_df: pd.DataFrame, broker_df: pd.DataFrame) -> pd.DataFrame:
    """Instruments where a TYPED `instrument_type` does not match what the
    broker's latest statement says it is.

    A blank `instrument_type` is not a disagreement to report — `instrument_type`
    is required for every row (CLAUDE.md), so a blank one is a data error for
    him to fix directly, not something this sync resolves. A blank
    `broad_type` (an unmapped `assetCategory`, see `broker_broad_types()`) is
    also never a disagreement — there is nothing to compare it against. Only
    a cell that was already typed, to something the broker contradicts,
    counts. Never resolved here, only named: both silent options are wrong
    (overwriting hides a real disagreement, keeping hides a real error), so
    the choice of which one is right stays his.
    """
    typed = instruments_df.set_index("instrument_id")["instrument_type"]
    rows = []
    for _, r in broker_df.iterrows():
        if not r["broad_type"]:
            continue
        iid = r["instrument_id"]
        if iid not in typed.index:
            continue
        existing = typed.loc[iid]
        existing_t = "" if pd.isna(existing) else str(existing).strip()
        if existing_t and existing_t != r["broad_type"]:
            rows.append({
                "instrument_id": iid,
                "instrument_type": existing_t,
                "broker_broad_type": r["broad_type"],
                "broker_sub_category": r["broker_sub_category"],
            })
    cols = ["instrument_id", "instrument_type", "broker_broad_type",
           "broker_sub_category"]
    return pd.DataFrame(rows, columns=cols)


def main(instruments_path: Path | None = None,
        xml_path: Path | None = None) -> None:
    """Compare every instrument the latest IBKR statement reports a position
    in against `instruments.csv`.

    Writes `broker_sub_category` (and its date) for every instrument the
    statement resolves, unconditionally. Never writes `instrument_type` —
    prints every disagreement between the broker and an already-typed cell
    instead, because which one is right is his call, not this script's.

    `instruments_path`/`xml_path` default to the real table and the newest
    file in `data/raw/`; a test passes throwaway ones instead.
    """
    instruments_path = instruments_path or inst.INSTRUMENTS_PATH
    if xml_path is None:
        xml_path = _latest_cached_xml()
        if xml_path is None:
            raise RuntimeError(
                "No cached IBKR statement in data/raw/. Run "
                "`venv/bin/python -m src.ibkr_sync` (or --cached) first."
            )

    root = parse_xml(Path(xml_path).read_bytes())
    stmt = next(iter_statements(root))
    as_of = stmt.attrib.get("toDate", "")
    broker_df, unresolved, unmapped = broker_broad_types(stmt)

    instruments_df = inst.load(instruments_path)
    known = set(instruments_df["instrument_id"])
    disagreed = disagreements(instruments_df, broker_df)

    recorded: list[str] = []
    unknown: list[str] = []
    failed: list[tuple[str, str]] = []

    for _, r in broker_df.iterrows():
        iid = r["instrument_id"]
        if iid not in known:
            # Held at the broker, never added to instruments.csv — the same
            # gap `instruments.gaps()` and `ops/data_gaps.py` already surface.
            # Reported, not fatal: nothing here should block every other row.
            unknown.append(iid)
            continue
        try:
            inst.set_broker_sub_category(iid, r["broker_sub_category"], as_of,
                                        path=instruments_path)
            recorded.append(iid)
        except Exception as e:  # noqa: BLE001 — one bad row must not cost
            # every row already written before it (same reasoning as
            # refresh_classification.py's per-instrument try/except).
            failed.append((iid, f"broker_sub_category write failed: "
                               f"{type(e).__name__}: {e}"))

    # Every distinct position the statement named, whether or not this run
    # could do anything with it — `len(broker_df)` alone would understate the
    # total by silently dropping every symbol that never resolved to an
    # instrument at all.
    total = len(broker_df) + len(unresolved)
    print(f"{len(recorded)} of {total} broker position(s) matched an "
         f"instrument; broker_sub_category written for each.")

    if unknown:
        print(f"\n{len(unknown)} broker position(s) not yet in "
             f"instruments.csv (see instruments.gaps()):")
        for iid in unknown:
            print(f"  {iid}")

    if not disagreed.empty:
        print(f"\n{len(disagreed)} disagreement(s) between the broker and an "
             f"already-typed instrument_type — left untouched, his record "
             f"unchanged:")
        for _, r in disagreed.iterrows():
            print(f"  {r['instrument_id']:10} has instrument_type="
                 f"{r['instrument_type']!r}, broker says "
                 f"{r['broker_broad_type']!r} (subCategory "
                 f"{r['broker_sub_category']!r})")

    if unmapped:
        print(f"\n{len(unmapped)} instrument(s) held an assetCategory this "
             f"file does not map — broker_sub_category was still written, "
             f"instrument_type was not touched:")
        for iid, why in unmapped:
            print(f"  {iid}: {why}")

    if unresolved:
        print(f"\n{len(unresolved)} broker symbol(s) could not be resolved "
             f"to any instrument at all:")
        for symbol, why in unresolved:
            print(f"  {symbol}: {why}")

    if failed:
        print(f"\n{len(failed)} write(s) failed:")
        for iid, why in failed:
            print(f"  {iid}: {why}")


if __name__ == "__main__":
    main()
