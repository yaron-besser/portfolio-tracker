"""
Rebuild data/nav_history.csv from every version git has ever held.

Why this exists: the sync used to overwrite the NAV file with whatever the
broker's 365-day window still covered, so each run silently dropped the oldest
day off the front. By 2026-08-10 the file had lost 60 days — the account's first
two months. The cause is fixed in `sync_nav_history()`; this recovers what was
already lost.

Nothing is invented. Every recovered row was written by an earlier sync straight
from an IBKR statement and is still in git; this only unions those versions back
together. Where two commits hold the same date, the newest wins — a later
statement is a restatement of that day, not a second opinion.

Run from the repo root:

    venv/bin/python ops/recover_nav_history.py            # report only
    venv/bin/python ops/recover_nav_history.py --write    # write the file
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NAV_PATH = PROJECT_ROOT / "data" / "nav_history.csv"
REL = "data/nav_history.csv"


def _git(*args: str) -> str:
    """Run a read-only git command from the repo root."""
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
    ).stdout


def versions() -> list[tuple[str, pd.DataFrame]]:
    """Every committed version of the NAV file, newest commit first.

    `--follow` so the history survives any rename. Versions that cannot be
    parsed are skipped loudly rather than silently: an unreadable old commit is
    worth knowing about, but must not stop the rest being recovered.
    """
    out: list[tuple[str, pd.DataFrame]] = []
    for sha in _git("log", "--format=%h", "--follow", "--", REL).split():
        raw = _git("show", f"{sha}:{REL}")
        if not raw.strip():
            continue
        try:
            # comment="#": some 2026-05 versions carried a commented header.
            df = pd.read_csv(
                io.StringIO(raw), dtype=str, keep_default_na=False, comment="#"
            )
        except Exception as e:  # noqa: BLE001 — report and continue
            print(f"  skipped {sha}: unparseable ({type(e).__name__}: {e})")
            continue
        if "date" in df.columns and len(df):
            out.append((sha, df))
    return out


def rebuild() -> pd.DataFrame:
    """Union every version on `date`, newest commit winning per date."""
    vs = versions()
    if not vs:
        raise SystemExit(f"No committed versions of {REL} found.")
    # Newest first, so keeping the FIRST row seen per date keeps the newest.
    merged = pd.concat([df for _, df in vs], ignore_index=True)
    merged = merged.drop_duplicates(subset="date", keep="first")
    return merged.sort_values("date").reset_index(drop=True)


def main() -> None:
    current = (
        pd.read_csv(NAV_PATH, dtype=str, keep_default_na=False)
        if NAV_PATH.exists()
        else pd.DataFrame(columns=["date"])
    )
    full = rebuild()
    recovered = sorted(set(full["date"]) - set(current["date"]))

    print(f"on disk now : {len(current):4} days  {current['date'].min()} -> {current['date'].max()}")
    print(f"recoverable : {len(full):4} days  {full['date'].min()} -> {full['date'].max()}")
    print(f"recovered   : {len(recovered):4} days", end="")
    print(f"  {recovered[0]} -> {recovered[-1]}" if recovered else "  (nothing to recover)")

    # A recovery must only ever ADD. If a date on disk is missing from the
    # rebuild, something is wrong with the assumptions — stop, do not write.
    lost = sorted(set(current["date"]) - set(full["date"]))
    if lost:
        raise SystemExit(
            f"REFUSING TO WRITE: {len(lost)} date(s) on disk are absent from the "
            f"rebuild, e.g. {lost[:5]}. This script may only add days."
        )

    if "--write" not in sys.argv:
        print("\nReport only. Re-run with --write to update the file.")
        return

    full.reindex(columns=list(current.columns) or list(full.columns)).to_csv(
        NAV_PATH, index=False
    )
    print(f"\nWrote {len(full)} rows to {REL}")


if __name__ == "__main__":
    main()
