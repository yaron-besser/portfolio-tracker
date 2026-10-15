"""
Print every data table with its row count, and the writing queue.

Exists so no document has to state a number the code can compute. Ten of the
sixteen counts written into `docs/data-model.md` were wrong by 2026-09-29 —
claims said 33 against 59, commentary said 0 against 14 — and the same stale
"24 of 70 notes written" appeared in three files while the real figure was
58 of 73 and the work was finished.

    venv/bin/python ops/counts.py

A doc that wants a count should name this command instead of quoting one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
# Not a table directory: see table_counts().
INBOX_DIR_NAME = "holdings_inbox"


def table_counts() -> pd.DataFrame:
    """One row per data/**/*.csv: name, rows, columns.

    `rglob`, not `glob` — a table nested under data/market/ (Task 1,
    2026-10-04) is still a table. `glob` silently dropped it: this script
    reported 17 tables where there were 18, with no error and no warning,
    which is exactly the failure class this file exists to prevent, just
    moved one level up from the data it counts.

    `holdings_inbox/` is skipped, and is not an exception to that rule: the
    spreadsheets he drops there are an issuer's delivery, not a table of his.
    They carry a title line above their header, so reading one as a table
    does not under-report — it raises, and took this whole command down the
    first time a file was sitting in there.
    """
    rows = []
    for csv in sorted(DATA_DIR.rglob("*.csv")):
        if INBOX_DIR_NAME in csv.parts:
            continue
        df = pd.read_csv(csv, dtype=str, keep_default_na=False)
        name = str(csv.relative_to(DATA_DIR))
        rows.append({"table": name, "rows": len(df), "columns": len(df.columns)})
    return pd.DataFrame(rows, columns=["table", "rows", "columns"])


def main() -> None:
    counts = table_counts()
    print("Tables\n")
    print(counts.to_string(index=False))
    print(f"\n{len(counts)} tables, {counts['rows'].sum():,} rows total")

    from src import notes

    status = notes.status()
    print("\nWriting queue\n")
    print(status["state"].value_counts().to_string())
    print(f"\n{len(status)} things that can carry a note")

    from src import claims, levels, instruments, evidence, ibkr_sync

    print("\nWhat is incomplete — visible, never blocking\n")
    for label, n in (
        ("claims with no attribution", len(claims.unattributed())),
        ("claims due for a verdict", len(claims.due_claims())),
        ("levels that cannot be checked", len(levels.unchecked_levels())),
        ("levels on something no longer held", len(levels.orphaned_levels())),
        ("note_refs that no longer resolve",
         len(claims.stale_refs()) + len(levels.stale_refs())),
        ("documents filed but never linked", len(evidence.unlinked())),
        ("instrument gaps", len(instruments.gaps())),
    ):
        print(f"  {n:>4}  {label}")

    shares = ibkr_sync.unreconciled_shares(
        pd.read_csv(DATA_DIR / "transactions.csv"),
        pd.read_csv(DATA_DIR / "positions.csv"),
        pd.read_csv(DATA_DIR / "corporate_actions.csv"),
    )
    odd = shares[shares["why"] != "consistent with pre-log holding"]
    print(f"  {len(odd):>4}  share counts trades cannot explain")
    for _, r in odd.iterrows():
        print(f"        {r['symbol']}: {r['gap']:+g} unaccounted for")


if __name__ == "__main__":
    main()
