"""
Tests for the sector reference table.

Two tables carried a free-text `sector` column with nothing connecting them —
37 labels in etf_compositions.csv, 13 in instruments.csv, 7 shared. Every one
of the 43 in use was verified to roll up correctly before this was built, so
no exposure number was wrong; this stops the next label being wrong.

Run from project root:
    pytest tests/test_sectors.py
"""

from __future__ import annotations

import csv

import pandas as pd
import pandera.pandas as pa
import pytest

from src import claims, sectors


def _store(tmp_path, rows=None):
    rows = rows if rows is not None else [
        {"sector_id": "Tech - Semiconductors", "parent": "Technology",
         "status": "active", "added": "2026-08-10"},
        {"sector_id": "Materials - Agriculture", "parent": "Materials",
         "status": "active", "added": "2026-08-10"},
        {"sector_id": "Industrials - Ag/Food Security", "parent": "Industrials",
         "status": "active", "added": "2026-08-10"},
    ]
    p = tmp_path / "sectors.csv"
    pd.DataFrame(rows, columns=sectors.COLUMNS).to_csv(
        p, index=False, quoting=csv.QUOTE_NONNUMERIC
    )
    return p


def test_parent_comes_from_the_table_not_the_spelling(tmp_path):
    """The rollup is data Yaron can see and change, not prefix matching."""
    p = _store(tmp_path)
    assert sectors.parent("Tech - Semiconductors", p) == "Technology"


def test_agriculture_is_a_cluster_not_a_sector(tmp_path):
    """
    Yaron's taxonomy, 2026-08-10: sectors are the 11 SPY sectors; food
    security is one of HIS clusters and legitimately spans several of them.

    So DE landing in Industrials while DBA lands in Materials is correct, not
    a mismatch to be merged away.
    """
    p = _store(tmp_path)
    assert sectors.parent("Industrials - Ag/Food Security", p) == "Industrials"
    assert sectors.parent("Materials - Agriculture", p) == "Materials"


def test_unknown_label_raises_with_the_command(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(KeyError) as e:
        sectors.parent("Tech - Quantum", p)
    assert "add_sector" in str(e.value)


def test_check_known_catches_a_typo(tmp_path):
    """'Semiconducters' must not quietly become label 44."""
    p = _store(tmp_path)
    with pytest.raises(ValueError) as e:
        sectors.check_known(["Tech - Semiconductors", "Tech - Semiconducters"], p)
    assert "Tech - Semiconducters" in str(e.value)


def test_blank_sector_is_allowed(tmp_path):
    """An ETF has no sector by design — blank is not a violation."""
    p = _store(tmp_path)
    sectors.check_known(["Tech - Semiconductors", "", "   "], p)  # must not raise


def test_parent_must_be_one_of_the_eleven_plus_two(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(pa.errors.SchemaErrors) as e:
        sectors.add_sector("Tech - Quantum", parent="Deep Tech", path=p)
    msg = claims.explain_schema_error(e.value)
    assert "ops/vocab/sector_parent" in msg


def test_a_new_label_can_be_added_in_one_call(tmp_path):
    p = _store(tmp_path)
    sectors.add_sector("Tech - Quantum", parent="Technology", path=p)
    assert sectors.parent("Tech - Quantum", p) == "Technology"


def test_duplicate_label_rejected(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        sectors.add_sector("Tech - Semiconductors", parent="Technology", path=p)


def test_every_label_in_use_is_defined():
    """The live tables and the live sector list must not drift apart."""
    from src import exposure, instruments
    comp = exposure.load()
    inst = instruments.load().fillna("")
    in_use = set(comp["sector"]) | {s for s in inst["sector"] if str(s).strip()}
    sectors.check_known(in_use)   # raises if any label is undefined


def test_no_label_in_use_lands_outside_the_eleven():
    """Every label must roll up to a real parent — none may vanish."""
    from src import exposure, instruments
    comp = exposure.load()
    inst = instruments.load().fillna("")
    allowed = set(claims.vocab("sector_parent"))
    for label in set(comp["sector"]) | {s for s in inst["sector"] if str(s).strip()}:
        assert sectors.parent(label) in allowed, label


def test_every_parent_is_itself_a_sector_row():
    """A parent named only in the vocab file is not reachable.

    `parent` is validated against `ops/vocab/sector_parent`, a text file — not
    against the table. So three tops (`Communication Services`,
    `Crypto / Digital Assets`, `Fixed Income / Cash`) sat in live use with no
    row of their own, and `evidence.link("sector", …)` refused them: eleven
    instruments pointed up at a theme research could not be filed against.
    The ten other tops each carry a row whose parent is itself.
    """
    live = sectors.load()
    ids = set(live["sector_id"])
    dangling = sorted({p for p in live["parent"] if str(p).strip() and p not in ids})
    assert not dangling, (
        f"{dangling} are used as a parent but have no row in data/sectors.csv. "
        f"Add one per name with parent set to itself, as the other tops have."
    )
