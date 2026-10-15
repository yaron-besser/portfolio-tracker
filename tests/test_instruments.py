"""Tests for src.instruments — the reference table every other table keys on.

Run: venv/bin/python -m pytest tests/test_instruments.py -q

The rules under test are the ones that used to live in five separate places
and disagree with each other. Writes go to tmp_path; the real table is only
ever read.
"""
from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
import pytest

from src import instruments as ins


def _store(tmp_path):
    """A tiny valid table to write against."""
    p = tmp_path / "instruments.csv"
    pd.DataFrame([{
        "instrument_id": "ASML", "name": "ASML Holding NV", "instrument_type": "stock",
        "broker_sub_category": "", "broker_sub_category_as_of": "", "issuer": "", "sector": "Tech - Semiconductors",
        "cluster_id": "AI Infrastructure",
        "provider_sector": "TECHNOLOGY", "provider_sector_as_of": "",
        "provider_sector_source": "", "sector_settled": "",
        "isin": "NL0010273215", "av_ticker": "ASML",
        "price_ticker": "ASML", "note_ref": "notes/positions/ASML.md",
        "status": "active", "added": "2026-08-10",
    }])[ins.COLUMNS].to_csv(p, index=False)
    return p


# ─── resolve() — the five alias systems collapsed into one rule ───

def test_currency_lots_resolve_to_one_instrument():
    """The whole point: two broker lots, one company."""
    assert ins.resolve("ASML_USD") == "ASML"
    assert ins.resolve("ASML_EUR") == "ASML"


def test_space_in_broker_symbol_removed():
    assert ins.resolve("BRK B") == "BRKB"
    assert ins.resolve("BF B") == "BFB"


def test_broker_alias_resolves_the_collision():
    """IBKR calls Novo Nordisk NOV; NOV is also a real, different US company."""
    assert ins.resolve("NOV") == "NVO"


def test_cash_keeps_its_own_id():
    assert ins.resolve("USD_CASH") == "USD_CASH"
    assert ins.resolve("EUR_CASH") == "EUR_CASH"


def test_plain_ticker_passes_through():
    assert ins.resolve("NVDA") == "NVDA"


def test_resolve_reads_no_files(tmp_path, monkeypatch):
    """Pure string rules — must work before the table exists."""
    monkeypatch.setattr(ins, "INSTRUMENTS_PATH", tmp_path / "nothing.csv")
    assert ins.resolve("ASML_EUR") == "ASML"


@pytest.mark.parametrize("empty", [None, float("nan"), "", "   "])
def test_resolve_refuses_an_empty_ticker(empty):
    """
    A blank cell must not become an id.

    str(None) is 'None' and a blank read by pandas is nan -> 'nan'. Either
    would pass as a plausible id and fail far away from the empty cell that
    caused it, so the failure is raised here where the cause is visible.
    """
    with pytest.raises(ValueError, match="empty ticker"):
        ins.resolve(empty)


# ─── The escape hatch — the constraint must never lose a decision ───

def test_minimal_row_is_enough(tmp_path):
    """11pm, a new instrument, nothing but a name and a type."""
    p = _store(tmp_path)
    ins.add_instrument("ABCD", name="New Co", instrument_type="stock", path=p)
    r = ins.get("ABCD", path=p)
    assert r["status"] == "incomplete"
    assert r["sector"] == "" and r["isin"] == ""


def test_incomplete_row_shows_in_gaps(tmp_path):
    p = _store(tmp_path)
    ins.add_instrument("ABCD", name="New Co", instrument_type="stock", path=p)
    assert "ABCD" in set(ins.gaps(path=p)["instrument_id"])


def test_duplicate_instrument_rejected(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        ins.add_instrument("ASML", name="dup", instrument_type="stock", path=p)


def test_bad_type_names_the_vocab_file(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="ops/vocab/instrument_type"):
        ins.add_instrument("ABCD", name="X", instrument_type="crypto", path=_store(tmp_path))


def test_unknown_field_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown field"):
        ins.add_instrument("ABCD", name="X", instrument_type="stock",
                           path=_store(tmp_path), colour="blue")


def test_note_ref_must_exist(tmp_path):
    with pytest.raises(ValueError, match="does not point to a file"):
        ins.add_instrument("ABCD", name="X", instrument_type="stock",
                           path=_store(tmp_path), note_ref="notes/positions/NOPE.md")


# ─── The foreign key ───

def test_check_known_passes_for_real_ids(tmp_path):
    ins.check_known(["ASML"], path=_store(tmp_path))  # no raise


def test_check_known_teaches_the_fix(tmp_path):
    with pytest.raises(ValueError, match="add_instrument"):
        ins.check_known(["ZZZZ"], path=_store(tmp_path))


def test_get_unknown_teaches_the_fix(tmp_path):
    with pytest.raises(KeyError, match="add_instrument"):
        ins.get("ZZZZ", path=_store(tmp_path))


# ─── Whole-file validation ───

def test_missing_file_is_an_empty_table(tmp_path):
    df = ins.load(tmp_path / "nothing.csv")
    assert df.empty and list(df.columns) == ins.COLUMNS


def test_duplicate_id_rejected(tmp_path):
    p = _store(tmp_path)
    df = pd.read_csv(p, dtype=str)
    pd.concat([df, df], ignore_index=True).to_csv(p, index=False)
    with pytest.raises(pa.errors.SchemaErrors):
        ins.load(p)


def test_hand_edited_bad_status_rejected(tmp_path):
    p = _store(tmp_path)
    p.write_text(p.read_text().replace("active", "live"))
    with pytest.raises(pa.errors.SchemaErrors, match="ops/vocab/instrument_status"):
        ins.load(p)


def test_impossible_added_date_rejected(tmp_path):
    p = _store(tmp_path)
    p.write_text(p.read_text().replace("2026-08-10", "2026-02-30"))
    with pytest.raises(pa.errors.SchemaErrors, match="real calendar date"):
        ins.load(p)


def test_hand_edited_broker_sub_category_as_of_must_be_a_date(tmp_path):
    """`set_broker_sub_category()` refuses a non-date, but it is not the only
    way a cell reaches the file. A hand edit went straight past it, and the
    whole job of this column is to say how old the broker's label is."""
    p = _store(tmp_path)
    p.write_text(p.read_text().replace(
        '"stock",,,', '"stock","ADR","nope",').replace(
        "stock,,,", "stock,ADR,nope,"))
    with pytest.raises(pa.errors.SchemaErrors, match="broker_sub_category_as_of"):
        ins.load(p)


def test_hand_edited_broker_sub_category_as_of_accepts_a_real_date(tmp_path):
    p = _store(tmp_path)
    p.write_text(p.read_text().replace(
        "stock,,,", "stock,ADR,2026-10-04,"))
    assert ins.get("ASML", p)["broker_sub_category_as_of"] == "2026-10-04"


def test_hand_edited_provider_sector_as_of_must_be_a_date(tmp_path):
    """A filled date is what puts the row inside the sectors.csv key, so a
    cell holding "last tuesday" both lies about the age and would drag a raw
    vendor word into a check it was never meant for."""
    p = _store(tmp_path)
    p.write_text(p.read_text().replace(
        "TECHNOLOGY,,,", "TECHNOLOGY,last tuesday,Yahoo,"))
    with pytest.raises(pa.errors.SchemaErrors, match="provider_sector_as_of"):
        ins.load(p)


# ─── dated_provider_sectors: which rows the sectors key may be applied to ───

def test_an_undated_provider_sector_is_outside_the_sector_key(tmp_path):
    """The raw vendor word every closed position still carries.

    "TECHNOLOGY" is not a data/sectors.csv label and never will be. Keying
    the whole column would refuse his real table outright.
    """
    df = ins.load(_store(tmp_path))
    assert ins.dated_provider_sectors(df) == []


def test_a_dated_provider_sector_is_inside_the_sector_key(tmp_path):
    """set_provider_sector() maps the vendor's answer onto his own taxonomy,
    so once a row carries a date its label must be a real sector."""
    p = _store(tmp_path)
    ins.set_provider_sector("ASML", "Technology", "Yahoo", "2026-10-04", path=p)
    assert ins.dated_provider_sectors(ins.load(p)) == ["Technology"]


def test_dated_provider_sectors_is_empty_on_an_empty_table():
    """The guard calls this on a staged frame that may have no rows at all."""
    assert ins.dated_provider_sectors(pd.DataFrame()) == []


def test_the_real_table_passes_the_conditional_sector_key():
    """Proof the key accepts his book as it stands, not only a fixture."""
    from src import sectors
    sectors.check_known(ins.dated_provider_sectors(ins.load()))


# ─── The real table, and the bug that started this ───

def test_real_table_validates():
    ins.load()  # raises on drift


def test_every_position_lot_resolves_to_a_known_instrument():
    """The foreign key holds for real data."""
    pos = pd.read_csv(ins.PROJECT_ROOT / "data" / "positions.csv")
    ins.check_known(pos["instrument_id"])


def test_selling_one_lot_does_not_close_the_instrument():
    """The ASML bug: `Removed: ['ASML_EUR']` must not read as 'ASML is gone'."""
    pos = pd.read_csv(ins.PROJECT_ROOT / "data" / "positions.csv")
    iid = ins.resolve("ASML_EUR")
    remaining = pos[(pos["instrument_id"] == iid) & (pos["ticker"] != "ASML_EUR")]
    assert len(remaining) > 0, "ASML_USD should still be held"


def test_claims_reference_known_instruments():
    """Every claim links to a real instrument. `ticker` keeps whatever the
    user typed at the time; `instrument_id` is the link that must resolve."""
    claims_csv = ins.PROJECT_ROOT / "data" / "claims.csv"
    if not claims_csv.exists():
        pytest.skip("no claims yet")
    ins.check_known(pd.read_csv(claims_csv, dtype=str)["instrument_id"])


def test_claim_keeps_the_ticker_the_user_typed():
    """ASML_USD stays in the record even though it resolves to ASML — what he
    called it at the time is part of the decision record (R1)."""
    claims_csv = ins.PROJECT_ROOT / "data" / "claims.csv"
    if not claims_csv.exists():
        pytest.skip("no claims yet")
    df = pd.read_csv(claims_csv, dtype=str)
    row = df[df["id"] == "c0001"]
    if row.empty:
        pytest.skip("c0001 not present")
    assert row.iloc[0]["ticker"] == "ASML_USD"
    assert row.iloc[0]["instrument_id"] == "ASML"


def test_stale_refs_finds_a_note_that_moved(tmp_path):
    """
    Nine notes moved to _archive/ in Phase 2f and silently broke five links.

    A view, not a blocking check: archiving a closed position's note is a
    normal act and must never make the table unreadable.
    """
    p = _store(tmp_path)
    ins.add_instrument("GONE", name="Moved Co", instrument_type="stock", path=p)
    df = ins.load(p)
    df.loc[df["instrument_id"] == "GONE", "note_ref"] = "notes/positions/GONE.md"
    df.to_csv(p, index=False)

    stale = ins.stale_refs(p)
    assert list(stale["instrument_id"]) == ["GONE"]
    assert ins.load(p) is not None          # still loads — not blocking
    assert "missing file" in ins.gaps(p).set_index("instrument_id").loc["GONE", "missing"]


def test_price_age_reports_the_oldest_lot_not_the_freshest(tmp_path):
    """One stale lot must make the whole book read stale.

    `sync_positions` deliberately leaves an ambiguous ticker's row untouched,
    so a single lot can fall weeks behind while the rest refresh. Taking
    `max()` reported the freshest stamp, so `price_warning()` said "current"
    and `breached_levels()` compared a stop against a stale price.
    """
    from datetime import date, timedelta
    p = tmp_path / "positions.csv"
    fresh = date.today().isoformat()
    stale = (date.today() - timedelta(days=30)).isoformat()
    p.write_text(
        "ticker,instrument_id,shares,cost_basis,last_price,market_value,priced_at,notes\n"
        f"IWM,IWM,10,100,110,1100,{fresh},\n"
        f"ASML,ASML,2,700,800,1600,{stale},\n",
        encoding="utf-8")
    assert ins.price_age_days(path=p) == 30
    assert "30 days old" in ins.price_warning(path=p)


# ─── set_provider_sector: dated and attributed, or not written at all ───

def test_a_provider_sector_cannot_be_written_without_its_date_and_source(tmp_path):
    """An undated vendor sector is indistinguishable from a fresh one."""
    p = _store(tmp_path)                     # the existing helper in this file
    with pytest.raises(ValueError):
        ins.set_provider_sector("ASML", "Technology", "", "", path=p)


def test_set_provider_sector_writes_all_three_columns_together(tmp_path):
    p = _store(tmp_path)
    ins.set_provider_sector("ASML", "Technology", "Yahoo via yfinance",
                            "2026-10-04", path=p)
    row = ins.get("ASML", path=p)
    assert row["provider_sector"] == "Technology"
    assert row["provider_sector_source"] == "Yahoo via yfinance"
    assert row["provider_sector_as_of"] == "2026-10-04"


def test_set_provider_sector_refuses_an_unreal_date(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(ValueError, match="calendar date"):
        ins.set_provider_sector("ASML", "Technology", "Yahoo via yfinance",
                                "2026-02-30", path=p)


def test_set_provider_sector_refuses_an_unknown_instrument(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(KeyError, match="ZZZ|Unknown"):
        ins.set_provider_sector("ZZZ", "Technology", "Yahoo via yfinance",
                                "2026-10-04", path=p)


def test_set_provider_sector_leaves_every_other_row_alone(tmp_path):
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf", path=p)
    before = ins.load(p).set_index("instrument_id").loc["GDX"].to_dict()
    ins.set_provider_sector("ASML", "Technology", "Yahoo via yfinance",
                            "2026-10-04", path=p)
    assert ins.load(p).set_index("instrument_id").loc["GDX"].to_dict() == before


# ─── set_sector_settled: his verdict on a sector disagreement, permanent ───

def test_set_sector_settled_writes_the_date(tmp_path):
    p = _store(tmp_path)
    ins.set_sector_settled("ASML", "2026-10-04", path=p)
    assert ins.get("ASML", path=p)["sector_settled"] == "2026-10-04"


def test_set_sector_settled_refuses_an_unreal_date(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(ValueError, match="calendar date"):
        ins.set_sector_settled("ASML", "2026-02-30", path=p)


def test_set_sector_settled_refuses_an_unknown_instrument(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(KeyError, match="ZZZ|Unknown"):
        ins.set_sector_settled("ZZZ", "2026-10-04", path=p)


def test_set_sector_settled_can_be_cleared_to_reopen_it(tmp_path):
    """Unlike set_cluster(), blank is allowed — an unsettled disagreement just
    resumes showing in the gap report, nothing drops out of a total."""
    p = _store(tmp_path)
    ins.set_sector_settled("ASML", "2026-10-04", path=p)
    ins.set_sector_settled("ASML", "", path=p)
    assert ins.get("ASML", path=p)["sector_settled"] == ""


def test_set_sector_settled_leaves_every_other_row_alone(tmp_path):
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf", path=p)
    before = ins.load(p).set_index("instrument_id").loc["GDX"].to_dict()
    ins.set_sector_settled("ASML", "2026-10-04", path=p)
    assert ins.load(p).set_index("instrument_id").loc["GDX"].to_dict() == before


def test_a_hand_typed_sector_settled_is_refused_by_the_schema(tmp_path):
    """`set_sector_settled()` is not the only guard: a value that bypasses the
    setter entirely — a hand edit in the file the commit guard validates from
    the index — must still be caught. This is the one cell in the table whose
    whole job is to make a check stop firing forever, so unlike
    `provider_sector_as_of` (deliberately unchecked at the schema level, same
    file), a word must not be able to pass as a date here.
    """
    p = _store(tmp_path)
    df = ins.load(p)
    df.loc[df["instrument_id"] == "ASML", "sector_settled"] = "yes"
    df.to_csv(p, index=False)
    with pytest.raises(pa.errors.SchemaErrors, match="sector_settled"):
        ins.load(p)


# ─── set_note_ref: an existing row had no writer for this column at all ───

def test_set_note_ref_links_an_existing_instrument(tmp_path):
    store = _store(tmp_path)
    ins.add_instrument("ANET", name="Arista Networks, Inc.",
                       instrument_type="stock", path=store)
    note = tmp_path / "ANET.md"
    note.write_text("# ANET\n", encoding="utf-8")

    ins.set_note_ref("ANET", str(note), path=store)
    assert ins.get("ANET", store)["note_ref"] == str(note)


def test_set_note_ref_refuses_an_unknown_instrument(tmp_path):
    with pytest.raises(KeyError, match="Unknown instrument"):
        ins.set_note_ref("ZZZ", "notes/positions/ZZZ.md", path=_store(tmp_path))


def test_set_note_ref_refuses_a_path_that_is_not_a_file(tmp_path):
    store = _store(tmp_path)
    ins.add_instrument("ANET", name="Arista", instrument_type="stock", path=store)
    with pytest.raises(ValueError, match="does not point to a file"):
        ins.set_note_ref("ANET", "notes/positions/never-written.md", path=store)


from src import clusters as _clusters


# ─── set_cluster — moving a holding between themes ───

def test_set_cluster_moves_a_holding(tmp_path):
    """Written for the Metals -> Precious Metals rename. There was no writer
    for this column, so the only route was hand-editing a guarded file."""
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf",
                               cluster_id="Metals", path=p)
    cl = tmp_path / "clusters.csv"
    _clusters.add_cluster("Metals", layer="thematic", path=cl)
    _clusters.add_cluster("Precious Metals", layer="thematic", path=cl)
    ins.set_cluster("GDX", "Precious Metals", path=p, clusters_path=cl)
    assert ins.load(p).set_index("instrument_id").loc["GDX"]["cluster_id"] \
        == "Precious Metals"


def test_set_cluster_refuses_a_theme_that_does_not_exist(tmp_path):
    """The commit guard checks this FK only at COMMIT, so a typo used to sit in
    the tree until he tried to save. Refused immediately now."""
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf", path=p)
    cl = tmp_path / "clusters.csv"
    _clusters.add_cluster("Metals", layer="thematic", path=cl)
    with pytest.raises((ValueError, KeyError), match="Watre|Unknown|not"):
        ins.set_cluster("GDX", "Watre", path=p, clusters_path=cl)


def test_set_cluster_refuses_a_group(tmp_path):
    """A group holds no positions of its own — filing a holding straight into
    one double-counts it. The commit guard says the same at :134-143."""
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf", path=p)
    cl = tmp_path / "clusters.csv"
    _clusters.add_cluster("Commodities", layer="group", path=cl)
    with pytest.raises(ValueError, match="group"):
        ins.set_cluster("GDX", "Commodities", path=p, clusters_path=cl)


def test_set_cluster_refuses_an_unknown_instrument(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(KeyError, match="ZZZ|Unknown"):
        ins.set_cluster("ZZZ", "Metals", path=p)


def test_set_cluster_refuses_to_leave_a_holding_in_no_cluster(tmp_path):
    """A blank leaves the holding in NAV but in NO cluster row, so
    clusters.weights() silently stops summing to NAV. Blanking PHO dropped
    $1,322.60 out of every cluster total with nothing saying so. Found by
    review 2026-09-28, the day the function was written."""
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf", path=p)
    cl = tmp_path / "clusters.csv"
    _clusters.add_cluster("Metals", layer="thematic", path=cl)
    for blank in ("", "   ", None):
        with pytest.raises(ValueError, match="no cluster|Name the cluster"):
            ins.set_cluster("GDX", blank, path=p, clusters_path=cl)


def test_set_cluster_survives_pandas_missing_values(tmp_path):
    """`cluster_id or ""` raises "boolean value of NA is ambiguous" on pd.NA,
    and turns float NaN into the literal "nan" — which then failed the foreign
    key naming the wrong problem entirely."""
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf", path=p)
    cl = tmp_path / "clusters.csv"
    _clusters.add_cluster("Metals", layer="thematic", path=cl)
    for missing in (pd.NA, float("nan")):
        with pytest.raises(ValueError, match="no cluster|Name the cluster"):
            ins.set_cluster("GDX", missing, path=p, clusters_path=cl)


def test_set_cluster_refuses_a_retired_cluster(tmp_path):
    """Otherwise a rename is silently undone by filing back into the slot
    that was just closed."""
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf", path=p)
    cl = tmp_path / "clusters.csv"
    _clusters.add_cluster("Metals", layer="thematic", path=cl)
    _clusters.set_status("Metals", "retired", path=cl,
                         positions_path=tmp_path / "nope.csv")
    with pytest.raises(ValueError, match="retired"):
        ins.set_cluster("GDX", "Metals", path=p, clusters_path=cl)


# ─── set_status — closing a position, or reopening one ───
#
# Written 2026-09-28 when RSP was sold. `clusters.set_status()` existed and
# this did not, so the only route to `status=closed` was hand-editing a file
# the commit guard watches. The guard below is the reason it is worth a
# function at all: `status` drives notes.status() and holding gaps, so a row
# marked closed while the lots are still there hides a real holding.

def _positions(tmp_path, rows):
    """`last_price` is not optional here: levels._read_positions() returns an
    EMPTY frame when any of instrument_id/shares/last_price is missing, so a
    fixture without it reads as zero shares and the guard under test would
    pass for the wrong reason."""
    p = tmp_path / "positions.csv"
    df = pd.DataFrame(rows)
    for col in ("instrument_id", "shares", "last_price"):
        if col not in df.columns:
            df[col] = 0.0
    df.to_csv(p, index=False)
    return p


def test_set_status_closes_an_instrument(tmp_path):
    p = _store(tmp_path)
    pos = _positions(tmp_path, [{"ticker": "ASML_USD", "instrument_id": "ASML",
                                 "shares": 0.0, "market_value": 0.0}])
    ins.set_status("ASML", "closed", path=p, positions_path=pos)
    assert ins.load(p).set_index("instrument_id").loc["ASML"]["status"] == "closed"


def test_set_status_refuses_closing_something_still_held(tmp_path):
    """The mistake worth preventing: `closed` drops the row out of
    notes.status() and reads as gone, while positions.csv still carries the
    money. Same shape as the clusters.set_status() retire guard."""
    p = _store(tmp_path)
    pos = _positions(tmp_path, [{"ticker": "ASML_USD", "instrument_id": "ASML",
                                 "shares": 3.0, "market_value": 5000.0}])
    with pytest.raises(ValueError, match="still holds|3"):
        ins.set_status("ASML", "closed", path=p, positions_path=pos)
    assert ins.load(p).set_index("instrument_id").loc["ASML"]["status"] == "active"


def test_set_status_refuses_marking_something_still_held_a_benchmark(tmp_path):
    """`benchmark` has the exact same silent-retirement effect as `closed`:
    `instruments.gaps()` skips both (Task 7), so marking a real holding a
    benchmark drops it from the incomplete list while positions.csv still
    carries the money, with nothing else saying so. Found by review: before
    this fix, `set_status('ANET', 'closed')` refused but
    `set_status('ANET', 'benchmark')` was silently accepted."""
    p = _store(tmp_path)
    pos = _positions(tmp_path, [{"ticker": "ASML_USD", "instrument_id": "ASML",
                                 "shares": 3.0, "market_value": 5000.0}])
    with pytest.raises(ValueError, match="still holds|3"):
        ins.set_status("ASML", "benchmark", path=p, positions_path=pos)
    assert ins.load(p).set_index("instrument_id").loc["ASML"]["status"] == "active"


def test_set_status_allows_a_benchmark_with_nothing_held(tmp_path):
    """The real SPY case: no position at all, so the guard must not block it."""
    p = _store(tmp_path)
    pos = _positions(tmp_path, [{"ticker": "ASML_USD", "instrument_id": "ASML",
                                 "shares": 0.0, "market_value": 0.0}])
    ins.set_status("ASML", "benchmark", path=p, positions_path=pos)
    assert ins.load(p).set_index("instrument_id").loc["ASML"]["status"] == "benchmark"


def test_set_status_sums_every_broker_lot_before_closing(tmp_path):
    """ASML_EUR going to zero is not ASML closing. Reading one lot would let
    a half-closed position be marked gone."""
    p = _store(tmp_path)
    pos = _positions(tmp_path, [
        {"ticker": "ASML_USD", "instrument_id": "ASML", "shares": 0.0, "market_value": 0.0},
        {"ticker": "ASML_EUR", "instrument_id": "ASML", "shares": 2.0, "market_value": 1800.0},
    ])
    with pytest.raises(ValueError, match="still holds|2"):
        ins.set_status("ASML", "closed", path=p, positions_path=pos)


def test_set_status_allows_reopening_without_the_held_check(tmp_path):
    """Going back to `active` is always safe — the guard is only about
    marking something gone that is not."""
    p = _store(tmp_path)
    pos = _positions(tmp_path, [{"ticker": "ASML_USD", "instrument_id": "ASML",
                                 "shares": 3.0, "market_value": 5000.0}])
    ins.set_status("ASML", "watchlist", path=p, positions_path=pos)
    assert ins.load(p).set_index("instrument_id").loc["ASML"]["status"] == "watchlist"


def test_set_status_refuses_an_unknown_value(tmp_path):
    """Vocabulary lives in ops/vocab/instrument_status; validate() catches it
    before a byte is written."""
    p = _store(tmp_path)
    with pytest.raises((pa.errors.SchemaError, pa.errors.SchemaErrors, ValueError)):
        ins.set_status("ASML", "sold", path=p)
    assert ins.load(p).set_index("instrument_id").loc["ASML"]["status"] == "active"


def test_set_status_refuses_an_unknown_instrument(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(KeyError, match="ZZZ|Unknown"):
        ins.set_status("ZZZ", "closed", path=p)


def test_set_status_leaves_every_other_row_alone(tmp_path):
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf", path=p)
    before = ins.load(p).set_index("instrument_id").loc["GDX"].to_dict()
    ins.set_status("ASML", "closed", path=p,
                   positions_path=_positions(tmp_path, [
                       {"ticker": "X", "instrument_id": "X", "shares": 1.0,
                        "market_value": 1.0}]))
    assert ins.load(p).set_index("instrument_id").loc["GDX"].to_dict() == before


def test_set_status_refuses_to_close_when_positions_cannot_be_read(tmp_path):
    """levels.shares_held() answers 0.0 for a file it cannot read, so an
    interrupted sync that truncates positions.csv would let a live holding be
    marked gone. Refuse rather than write silence as a fact."""
    p = _store(tmp_path)
    missing = tmp_path / "nope.csv"
    with pytest.raises(ValueError, match="missing or empty|Cannot confirm"):
        ins.set_status("ASML", "closed", path=p, positions_path=missing)
    assert ins.load(p).set_index("instrument_id").loc["ASML"]["status"] == "active"


def test_set_status_refuses_to_close_on_a_positions_file_missing_a_column(tmp_path):
    """The pre-2026-09 positions.csv had no `instrument_id` column at all, and
    levels._read_positions() returns an EMPTY frame for that shape — which
    reads as nothing held for every name in the book."""
    p = _store(tmp_path)
    bad = tmp_path / "positions.csv"
    pd.DataFrame([{"ticker": "ASML_USD", "shares": 3.0,
                   "last_price": 900.0}]).to_csv(bad, index=False)
    with pytest.raises(ValueError, match="instrument_id|Cannot confirm"):
        ins.set_status("ASML", "closed", path=p, positions_path=bad)
    assert ins.load(p).set_index("instrument_id").loc["ASML"]["status"] == "active"


def test_set_status_reopening_needs_no_positions_file_at_all(tmp_path):
    """The read only happens for `closed`. Bringing a name back must not
    depend on the broker file being present."""
    p = _store(tmp_path)
    ins.set_status("ASML", "watchlist", path=p, positions_path=tmp_path / "nope.csv")
    assert ins.load(p).set_index("instrument_id").loc["ASML"]["status"] == "watchlist"


def test_check_known_ignores_blanks_like_the_other_two_foreign_keys(tmp_path):
    """`clusters.check_known` and `sectors.check_known` both skip blank cells;
    `instruments.check_known` did not, so the same empty value produced three
    different outcomes depending on which table was being validated.

    A blank foreign key means "not set", which is legal in every table that
    has one. Only a real, unknown id is an error.
    """
    p = _store(tmp_path)
    ins.check_known(["ASML", "", "   ", None], path=p)   # must not raise

    with pytest.raises(ValueError, match="NOPE"):
        ins.check_known(["ASML", "NOPE"], path=p)


def test_gaps_does_not_report_an_etf_for_having_no_sector(tmp_path):
    """`docs/data-conventions.md` says an ETF MUST have no sector — its
    exposure is decomposed in etf_compositions.csv instead. Reporting that as
    a gap put 33 rows in a 47-row "what is incomplete" list that could never
    be cleared, and a list that can never reach zero stops being read.

    Cash and FX are the same case: `holding.unexplained()` already excludes
    them from needing a thesis, so this must too.
    """
    p = tmp_path / "instruments.csv"
    pd.DataFrame([
        {"instrument_id": "QQQ", "name": "Invesco QQQ", "instrument_type": "etf",
         "broker_sub_category": "", "broker_sub_category_as_of": "", "issuer": "Invesco", "sector": "", "cluster_id": "",
         "provider_sector": "",
         "provider_sector_as_of": "", "provider_sector_source": "",
         "sector_settled": "", "isin": "",
         "av_ticker": "", "price_ticker": "", "note_ref": "README.md",
         "status": "active", "added": "2026-08-10"},
        {"instrument_id": "USD_CASH", "name": "US Dollar", "instrument_type": "cash",
         "broker_sub_category": "", "broker_sub_category_as_of": "", "issuer": "", "sector": "", "cluster_id": "",
         "provider_sector": "",
         "provider_sector_as_of": "", "provider_sector_source": "",
         "sector_settled": "", "isin": "",
         "av_ticker": "", "price_ticker": "", "note_ref": "",
         "status": "active", "added": "2026-08-10"},
    ])[ins.COLUMNS].to_csv(p, index=False)

    out = ins.gaps(p)
    assert out.empty, (
        "an ETF without a sector and a cash balance without a thesis are both "
        f"correct, not incomplete:\n{out.to_string(index=False)}"
    )


def test_gaps_skips_a_benchmark_row_like_a_closed_one(tmp_path):
    """SPY (status=benchmark) is never held and never will be, so it has no
    thesis to write and no sector to decide — flagging it would put a
    permanently unclearable row in the list, the same failure already fixed
    once for ETFs and cash/FX (Task 7)."""
    p = tmp_path / "instruments.csv"
    pd.DataFrame([
        {"instrument_id": "SPY", "name": "SPDR S&P 500 ETF Trust",
         "instrument_type": "etf", "broker_sub_category": "", "broker_sub_category_as_of": "", "issuer": "SPDR",
         "sector": "", "cluster_id": "",
         "provider_sector": "", "provider_sector_as_of": "",
         "provider_sector_source": "", "sector_settled": "", "isin": "",
         "av_ticker": "", "price_ticker": "", "note_ref": "",
         "status": "benchmark", "added": "2026-10-04"},
    ])[ins.COLUMNS].to_csv(p, index=False)

    assert ins.gaps(p).empty


def test_gaps_still_reports_a_stock_with_no_sector(tmp_path):
    """The exemption must be the instrument TYPE, not a blanket silence."""
    p = tmp_path / "instruments.csv"
    pd.DataFrame([
        {"instrument_id": "NVO", "name": "Novo Nordisk", "instrument_type": "stock",
         "broker_sub_category": "", "broker_sub_category_as_of": "", "issuer": "", "sector": "", "cluster_id": "",
         "provider_sector": "",
         "provider_sector_as_of": "", "provider_sector_source": "",
         "sector_settled": "", "isin": "",
         "av_ticker": "", "price_ticker": "", "note_ref": "README.md",
         "status": "active", "added": "2026-08-10"},
    ])[ins.COLUMNS].to_csv(p, index=False)
    assert "no sector" in ins.gaps(p).iloc[0]["missing"]


# ─── issuer — who runs the fund, so a downloader is written once per family ───

@pytest.mark.parametrize("name,expected", [
    ("Tech Select SPDR", "SPDR"),
    ("SPDR S&P Regional Banking ETF", "SPDR"),
    ("SS HEALTH CARE SELECT SECTOR", "SPDR"),       # never says SPDR at all
    ("iShares Russell 2000 ETF", "iShares"),
    ("ISHARES MSCI AGRICULTURE PRO", "iShares"),    # the broker shouts
    ("Invesco DB Agriculture Fund", "Invesco"),
    ("GLOBAL X COPPER MINERS ETF", "Global X"),
    ("ROUNDHILL MAGNIFICENT SEVEN", "Roundhill"),
    ("VanEck Gold Miners ETF", "VanEck"),
    ("Vanguard Total Stock Market", "Vanguard"),
    ("WisdomTree India Earnings", "WisdomTree"),
    ("Amplify Junior Silver Miners", "Amplify"),
    ("Fundstrat Granny Shots US Large Cap", "Fundstrat"),
])
def test_the_fund_family_is_read_off_the_name(name, expected):
    assert ins.issuer_from_name(name) == expected


@pytest.mark.parametrize("name", [
    "American Express Company",      # contains "ss " mid-word
    "Clark Enterprises",             # contains "ark " mid-word
    "NVIDIA Corporation",
    "",
    "   ",
    None,
    float("nan"),
])
def test_a_company_has_no_issuer(name):
    """A wrong issuer is worse than none.

    The right answer to "who runs this" is usually "nobody, it is a company",
    and a bare substring match turns Express into a State Street fund. The
    guess only ever costs a fund its better data source, so it must never be
    made on a coincidence.
    """
    assert ins.issuer_from_name(name) == ""


def test_vaneck_is_not_vanguard():
    """Two families whose names start with the same three letters."""
    assert ins.issuer_from_name("VanEck Gold Miners ETF") != ins.issuer_from_name(
        "Vanguard Total Stock Market")


def test_set_issuer_writes_to_the_store_it_is_given(tmp_path):
    p = _store(tmp_path)
    ins.set_issuer("ASML", "SPDR", path=p)
    assert ins.get("ASML", p)["issuer"] == "SPDR"


def test_set_issuer_can_clear_a_wrong_guess(tmp_path):
    """Unlike set_cluster(), blank is allowed.

    Clearing an issuer loses a shortcut to a better data source and nothing
    else; clearing a cluster silently drops the holding out of every total.
    """
    p = _store(tmp_path)
    ins.set_issuer("ASML", "SPDR", path=p)
    ins.set_issuer("ASML", "", path=p)
    assert ins.get("ASML", p)["issuer"] == ""


@pytest.mark.parametrize("blank", [None, float("nan")])
def test_set_issuer_does_not_write_the_word_none(tmp_path, blank):
    """None and NaN stringify to 'None'/'nan', which read as real issuers."""
    p = _store(tmp_path)
    ins.set_issuer("ASML", blank, path=p)
    assert ins.get("ASML", p)["issuer"] == ""


def test_set_issuer_refuses_an_unknown_instrument(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(KeyError):
        ins.set_issuer("NOPE", "SPDR", path=p)


# ─── instrument_type: non-nullable, same as status and added ───

def test_a_blank_instrument_type_is_rejected(tmp_path):
    """The guarantee the whole column exists to keep: CLAUDE.md and other
    code (`instrument_type == "stock"`, `holding.py`, `market.py`,
    `notes.py`) rely on this field always being a real, typed vocabulary
    word. A review on 2026-10-04 found an earlier draft of this task made
    the column nullable so a sync script could fill a blank cell — and a
    hand-blanked ASML then loaded clean, committed clean, and silently
    dropped out of `instruments.gaps()`, `ops/data_gaps.py`'s ETF filter and
    the ISIN check below. `instrument_type` is required for every row, full
    stop; there is no writer that fills a blank one.
    """
    p = _store(tmp_path)
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    df.loc[df["instrument_id"] == "ASML", "instrument_type"] = ""
    df.to_csv(p, index=False)
    with pytest.raises(pa.errors.SchemaErrors, match="instrument_type"):
        ins.load(p)


# ─── broker_sub_category — the broker's finer label, written unconditionally,
#     dated the same way provider_sector is ───

def test_set_broker_sub_category_writes_both_cells(tmp_path):
    p = _store(tmp_path)
    ins.set_broker_sub_category("ASML", "ADR", "2026-10-04", path=p)
    row = ins.get("ASML", p)
    assert row["broker_sub_category"] == "ADR"
    assert row["broker_sub_category_as_of"] == "2026-10-04"


def test_set_broker_sub_category_refuses_a_label_without_a_date(tmp_path):
    """The two move together or not at all, the same pairing
    set_provider_sector() enforces for its three — a label with no date is of
    unknown age."""
    p = _store(tmp_path)
    with pytest.raises(ValueError, match="move together"):
        ins.set_broker_sub_category("ASML", "ADR", "", path=p)


def test_set_broker_sub_category_refuses_a_date_without_a_label(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(ValueError, match="move together"):
        ins.set_broker_sub_category("ASML", "", "2026-10-04", path=p)


def test_set_broker_sub_category_can_be_cleared_with_both_blank(tmp_path):
    """Blank/blank is allowed, the same allowance set_issuer() makes: nothing
    reads this column yet, so clearing it costs nothing."""
    p = _store(tmp_path)
    ins.set_broker_sub_category("ASML", "ADR", "2026-10-04", path=p)
    ins.set_broker_sub_category("ASML", "", "", path=p)
    row = ins.get("ASML", p)
    assert row["broker_sub_category"] == ""
    assert row["broker_sub_category_as_of"] == ""


def test_set_broker_sub_category_refuses_an_unreal_date(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(ValueError, match="calendar date"):
        ins.set_broker_sub_category("ASML", "ADR", "2026-02-30", path=p)


@pytest.mark.parametrize("blank", [None, float("nan")])
def test_set_broker_sub_category_does_not_write_the_word_none(tmp_path, blank):
    """None and NaN stringify to 'None'/'nan', which read as real labels."""
    p = _store(tmp_path)
    ins.set_broker_sub_category("ASML", blank, blank, path=p)
    row = ins.get("ASML", p)
    assert row["broker_sub_category"] == ""
    assert row["broker_sub_category_as_of"] == ""


def test_set_broker_sub_category_refuses_an_unknown_instrument(tmp_path):
    p = _store(tmp_path)
    with pytest.raises(KeyError):
        ins.set_broker_sub_category("NOPE", "ADR", "2026-10-04", path=p)


def test_set_broker_sub_category_leaves_every_other_row_alone(tmp_path):
    p = _store(tmp_path)
    ins.add_instrument("GDX", name="Gold Miners", instrument_type="etf", path=p)
    before = ins.load(p).set_index("instrument_id").loc["GDX"].to_dict()
    ins.set_broker_sub_category("ASML", "ADR", "2026-10-04", path=p)
    assert ins.load(p).set_index("instrument_id").loc["GDX"].to_dict() == before


def test_set_broker_sub_category_can_be_written_even_when_instrument_type_disagrees(tmp_path):
    """The whole point of the column: it is written regardless of whether
    `instrument_type` agrees with the broker. Writing it is never the thing
    that resolves — or hides — a disagreement."""
    p = _store(tmp_path)   # ASML is typed "stock"
    ins.set_broker_sub_category("ASML", "ETF", "2026-10-04", path=p)   # broker disagrees
    row = ins.get("ASML", p)
    assert row["instrument_type"] == "stock", "disagreement must not be touched"
    assert row["broker_sub_category"] == "ETF", "but the label is still recorded"
