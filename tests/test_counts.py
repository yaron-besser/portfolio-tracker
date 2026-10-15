"""The table-listing in ops/counts.py must never silently drop a table.

Why this file exists: `table_counts()` used `DATA_DIR.glob("*.csv")`, which is
not recursive. Task 1 (2026-10-04) moved `etf_compositions.csv` into
`data/market/`, and the non-recursive glob simply stopped seeing it — 17
tables printed where there were 18, no error, no warning. `ops/counts.py` is
the repo's named antidote to a stale or wrong number in prose (CLAUDE.md,
docs/data-model.md); a silent gap in the thing that is supposed to catch
silent gaps is the same failure this task's own grep test exists to prevent,
just one file over. This test is the mechanism so the next table moved into a
subdirectory cannot repeat it.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "counts.py"


def _load():
    spec = importlib.util.spec_from_file_location("counts", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def counts():
    return _load()


def test_every_tracked_data_table_is_reported(counts):
    """The set of tables counts.py reports must equal git's own list.

    Comparing against `git ls-files`, not against a hand-written set in this
    test, for the same reason the repo-shape guard reads the commit hook's
    own regex instead of copying it: a second list is a second place to go
    stale.
    """
    reported = set(counts.table_counts()["table"])
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "data/*.csv"],
        capture_output=True, text=True, check=True,
    )
    # His issuer downloads live under data/ and are tracked, but they are
    # deliveries rather than tables — `counts.py` skips them by the same
    # constant this reads, so the two cannot disagree about which is which.
    delivery = f"/{counts.INBOX_DIR_NAME}/"
    tracked = {
        line.removeprefix("data/") for line in out.stdout.splitlines()
        if line and delivery not in line
    }
    assert reported == tracked, (
        f"ops/counts.py reports {reported!r} but git tracks {tracked!r} — "
        "a table was added, moved or renamed without counts.py noticing."
    )


def test_nested_tables_are_named_with_their_directory(counts):
    """A table under data/market/ must print as `market/<name>.csv`, not
    just `<name>.csv` — the bare name is ambiguous about which table it is
    once two directories can hold a file called etf_compositions.csv."""
    reported = set(counts.table_counts()["table"])
    nested = {t for t in reported if "/" in t}
    assert nested, "expected at least one table nested under data/ (market/etf_compositions.csv)"
    assert "market/etf_compositions.csv" in reported


def test_a_spreadsheet_in_the_inbox_is_not_counted_as_a_table(counts, tmp_path,
                                                              monkeypatch):
    """His issuer downloads are a delivery, not a table of his.

    They carry a title line above their header, so reading one as a table
    raises rather than mis-counting — and took the whole command down the
    first time a file was sitting in there.
    """
    data = tmp_path / "data"
    (data / "market" / "holdings_inbox").mkdir(parents=True)
    (data / "positions.csv").write_text("instrument_id,shares\nAAA,1\n")
    (data / "market" / "holdings_inbox" / "IWM_holdings.csv").write_text(
        'iShares Russell 2000 ETF\nFund Holdings as of,"Oct 02, 2026"\n\n'
        "Ticker,Name,Sector,Asset Class,Weight (%)\n"
        '"TWST","TWIST BIOSCIENCE","Health Care","Equity","0.41"\n')
    monkeypatch.setattr(counts, "DATA_DIR", data)

    tables = counts.table_counts()

    assert list(tables["table"]) == ["positions.csv"]
