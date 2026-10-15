"""The sector reference table — one row per sector label, with its parent.

Why this exists (Phase 2g). Two tables carried a free-text `sector` column and
nothing connected them: `etf_compositions.csv` used 37 labels,
`instruments.csv` used 13, and only 7 were shared. Nothing stopped a typo
becoming label 44, and the sub-sector -> parent rollup lived as prefix matching
inside `exposure.py`, where Yaron could neither see nor change it.

All 43 labels in use were checked before this was built and every one rolled up
correctly, so no exposure number was wrong. This stops the *next* label being
wrong, and moves the taxonomy out of code into data.

**Sector and cluster answer different questions** (Yaron, 2026-08-10):

    sector   the 11 SPY sectors — standard, comparable to the benchmark
    cluster  his own themes — "food security" spans Industrials (DE),
             Materials (DBA) and Consumer Staples (VEGI), and that is correct

So agriculture is not a sector problem. DE is Industrials *and* in the
Agriculture cluster; both are true, and neither needs to bend for the other.

Usage:
    from src import sectors
    sectors.parent("Tech - Semiconductors")   # -> "Technology"
    sectors.check_known(df["sector"])         # the foreign key
    sectors.add_sector("Tech - Quantum", parent="Technology")
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from src.claims import PROJECT_ROOT, _is_real_date, _vocab_check, vocab

SECTORS_PATH = PROJECT_ROOT / "data" / "sectors.csv"

# `sector_id` IS the readable label ("Tech - Semiconductors"), matching the
# instrument_id and cluster_id decisions.
COLUMNS = ["sector_id", "parent", "status", "added"]


def _schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "sector_id": pa.Column(str, unique=True),
            "parent": pa.Column(str, _vocab_check("sector_parent")),
            "status": pa.Column(str, _vocab_check("instrument_status")),
            "added": pa.Column(
                str,
                pa.Check(lambda s: s.map(_is_real_date),
                         error="added must be a real calendar date, ISO YYYY-MM-DD"),
            ),
        },
        strict=True,
        ordered=True,
    )


def validate(df: pd.DataFrame) -> pd.DataFrame:
    return _schema().validate(df, lazy=True)


def load(path: Path = SECTORS_PATH) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return validate(df) if not df.empty else pd.DataFrame(columns=COLUMNS)


def parent(sector_id: str, path: Path = SECTORS_PATH) -> str:
    """The parent sector for a label. Raises if the label is unknown.

    Deliberately raises rather than returning 'Other'. The old prefix-matching
    version quietly sent anything unrecognised to 'Other', where its money
    stopped counting toward any sector while the totals still looked tidy.
    """
    df = load(path)
    row = df[df["sector_id"] == str(sector_id).strip()]
    if row.empty:
        raise KeyError(
            f"Unknown sector {sector_id!r}.\n"
            f"  Add it with:  sectors.add_sector({sector_id!r}, parent='...')\n"
            f"  Allowed parents: {vocab('sector_parent')}"
        )
    return row.iloc[0]["parent"]


def add_sector(sector_id: str, parent: str, path: Path = SECTORS_PATH, **optional) -> str:
    """Add a sector label. Only the label and its parent are ever required."""
    df = load(path)
    if (df["sector_id"] == sector_id).any():
        raise ValueError(f"Sector {sector_id!r} already exists.")
    row = {
        "sector_id": sector_id,
        "parent": parent,
        "status": optional.get("status", "active"),
        "added": optional.get("added", date.today().isoformat()),
    }
    candidate = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    validate(candidate)
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return sector_id


def check_known(labels, path: Path = SECTORS_PATH) -> None:
    """Raise unless every label exists — the foreign key both tables call."""
    known = set(load(path)["sector_id"])
    missing = sorted({str(s).strip() for s in labels
                      if str(s).strip() and str(s).strip() not in known})
    if missing:
        raise ValueError(
            f"Unknown sector(s) {missing} — not in data/sectors.csv.\n"
            f"  Add one with:  sectors.add_sector('{missing[0]}', parent='...')\n"
            f"  Allowed parents: {vocab('sector_parent')}"
        )


def parents_map(path: Path = SECTORS_PATH) -> dict[str, str]:
    """label -> parent, for callers rolling up a whole column at once."""
    df = load(path)
    return dict(zip(df["sector_id"], df["parent"]))
