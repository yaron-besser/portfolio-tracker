"""Yahoo answers in two different shapes, and only one of them is mapped.

`funds_data.sector_weightings` returns `consumer_cyclical`.
`Ticker.info["sector"]` returns `Consumer Cyclical` — same vendor, same fact,
different casing and separator. PROVIDER_SECTORS holds only the first form,
so the very first real stock fetch would discard its whole answer. This is
the test that would have caught that before it reached the data.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "refresh_classification.py"


@pytest.fixture(scope="module")
def refresh():
    spec = importlib.util.spec_from_file_location("refresh_classification", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("given,expected", [
    ("Consumer Cyclical", "Consumer Discretionary"),
    ("consumer_cyclical", "Consumer Discretionary"),
    ("Technology", "Technology"),
    ("Financial Services", "Financials"),
    ("Basic Materials", "Materials"),
    ("Consumer Defensive", "Consumer Staples"),
    ("Communication Services", "Comm Services"),
    ("Real Estate", "Real Estate"),
    ("  technology  ", "Technology"),
])
def test_both_of_yahoos_spellings_map(refresh, given, expected):
    assert refresh.normalise(given) == expected


def test_an_unknown_sector_name_raises_rather_than_guessing(refresh):
    with pytest.raises(KeyError):
        refresh.normalise("Nanotechnology")


def test_every_mapped_value_is_a_real_sector_label(refresh):
    """The map's targets are foreign keys into sectors.csv, so prove it."""
    from src import sectors
    sectors.check_known(set(refresh.SECTOR_ALIASES.values()))


# ─── The pre-existing-spelling trap: a vendor's new wording is not a
#     vendor's new mind ───
#
# Some rows in data/instruments.csv already carry a `provider_sector` cell
# from an earlier fetch of unknown provenance — raw, upper-case,
# space-separated ("FINANCIAL SERVICES"). The refresher must not read that
# spelling difference as the vendor changing its answer; only a genuinely
# different sector, or a cell this vocabulary cannot parse at all, may count
# as one.

def test_an_older_uppercase_spelling_is_not_a_disagreement(refresh):
    assert refresh.agrees_with_existing("FINANCIAL SERVICES", "Financials") is True


def test_a_real_vendor_change_is_a_disagreement(refresh):
    assert refresh.agrees_with_existing("TECHNOLOGY", "Healthcare") is False


def test_an_existing_cell_this_vocabulary_cannot_parse_is_a_disagreement(refresh):
    """Cannot confirm it agrees, so it must not be treated as agreeing."""
    assert refresh.agrees_with_existing("Nanotechnology", "Technology") is False


def test_every_alias_output_round_trips_through_normalise(refresh):
    """This script's own output becomes next run's input.

    agrees_with_existing() re-normalises an already-written cell to compare
    it against a fresh fetch, so every label SECTOR_ALIASES can PRODUCE must
    also be a word it can ACCEPT. Found 2026-10-04: five of the eleven
    labels — Financials, Consumer Discretionary, Consumer Staples,
    Comm Services, Materials — failed this, so a cell already holding one of
    them, fetched again and given the identical answer, raised KeyError
    inside the comparison, read as a disagreement, and froze that row's date
    while reporting the vendor as contradicting itself.
    """
    for label in set(refresh.SECTOR_ALIASES.values()):
        assert refresh.normalise(label) == label


def test_a_second_run_over_already_written_data_is_idempotent(
    refresh, tmp_path, monkeypatch, capsys
):
    """The exact regression, end to end: a cell already holding today's
    canonical label, fetched again and given the identical vendor answer,
    must be confirmed and redated on the next run — never reported as the
    vendor disagreeing with its own value, which is what froze
    `provider_sector_as_of` forever.
    """
    import datetime as dt
    import csv as csv_mod

    import pandas as pd

    from src import instruments as inst

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    inst_path = data_dir / "instruments.csv"
    pos_path = data_dir / "positions.csv"

    row = {c: "" for c in inst.COLUMNS}
    row.update(instrument_id="BMNR", name="BitMine", instrument_type="stock",
               sector="Crypto - Miners / Treasury", cluster_id="Crypto",
               av_ticker="BMNR", status="active", added="2026-08-10")
    pd.DataFrame([row])[inst.COLUMNS].to_csv(
        inst_path, index=False, quoting=csv_mod.QUOTE_NONNUMERIC)
    pd.DataFrame([{"ticker": "BMNR", "instrument_id": "BMNR", "shares": "1",
                  "cost_basis": "1", "last_price": "1", "market_value": "100",
                  "priced_at": "2026-10-04", "notes": ""}]).to_csv(
        pos_path, index=False)

    # The identical vendor answer both times — the point of the test.
    monkeypatch.setattr(refresh, "vendor_sector", lambda ticker: ("Financials", ""))
    monkeypatch.setattr(sys, "argv", ["refresh_classification.py", "--write"])

    class _FixedDate:
        _today = dt.date(2026, 10, 4)

        @classmethod
        def today(cls):
            return cls._today

    monkeypatch.setattr(refresh, "date", _FixedDate)

    refresh.main(instruments_path=inst_path, positions_path=pos_path)
    first = inst.get("BMNR", path=inst_path)
    assert first["provider_sector"] == "Financials"
    assert first["provider_sector_as_of"] == "2026-10-04"

    _FixedDate._today = dt.date(2026, 10, 5)      # a later run, same answer
    capsys.readouterr()                           # discard the first run's output
    refresh.main(instruments_path=inst_path, positions_path=pos_path)
    out = capsys.readouterr().out

    assert "DISAGREE" not in out, (
        "the vendor repeating itself must not read as the vendor "
        f"contradicting itself:\n{out}"
    )
    second = inst.get("BMNR", path=inst_path)
    assert second["provider_sector"] == "Financials"
    assert second["provider_sector_as_of"] == "2026-10-05", (
        "the date must refresh on a confirmed run, not freeze at the first one"
    )
