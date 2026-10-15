"""The broker knows what it sold him better than a typed cell does."""
from __future__ import annotations

import importlib.util
import csv as csv_mod
from pathlib import Path

import pandas as pd
import pytest

from src import instruments as inst
from src.ibkr_import import iter_statements, parse_xml

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops" / "sync_instrument_types.py"
VOCAB_PATH = REPO / "ops" / "vocab" / "instrument_type"
XML_FIXTURE = Path(__file__).parent / "fixtures" / "ibkr_flex_sample.xml"


@pytest.fixture(scope="module")
def sync():
    spec = importlib.util.spec_from_file_location("sync_instrument_types", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def stmt():
    """The single FlexStatement in the shared test fixture. Carries, among
    other positions, AAPL (STK/COMMON) and ASML held as both a EUR and a USD
    lot (STK/COMMON each) — real multi-currency shape, synthetic numbers.
    `toDate="2025-12-31"`."""
    root = parse_xml(XML_FIXTURE.read_bytes())
    return next(iter_statements(root))


def _instruments_csv(tmp_path, rows: list[dict]) -> Path:
    """A throwaway instruments.csv with only the named fields set; every
    other column is blanked the way `instruments.load()` always returns it.
    Every row needs a real `instrument_type` — the column is non-nullable.
    """
    p = tmp_path / "instruments.csv"
    defaults = {c: "" for c in inst.COLUMNS}
    full = [{**defaults, "status": "active", "added": "2026-08-10", **r}
           for r in rows]
    pd.DataFrame(full)[inst.COLUMNS].to_csv(p, index=False,
                                            quoting=csv_mod.QUOTE_NONNUMERIC)
    return p


# ─── broad_type() — the pure mapping, the broker's word taken as given ───

@pytest.mark.parametrize("asset,sub,expected", [
    ("STK", "ETF", "etf"),
    ("STK", "COMMON", "stock"),
    ("STK", "ADR", "stock"),
    ("STK", "REIT", "stock"),
    ("STK", "NY REG SHRS", "stock"),
    ("CASH", "", "cash"),
    ("OPT", "C", "option"),
    ("FUT", "", "future"),
])
def test_the_broad_type_comes_from_the_broker(sync, asset, sub, expected):
    assert sync.broad_type(asset, sub) == expected


def test_an_unknown_asset_category_raises(sync):
    """A category we have never seen must stop, not become 'stock' by default."""
    with pytest.raises(KeyError):
        sync.broad_type("BOND", "")


def test_every_broad_type_is_in_the_vocabulary(sync):
    """These are foreign keys into ops/vocab/instrument_type."""
    allowed = set(VOCAB_PATH.read_text().split())
    assert set(sync.BROAD_TYPES.values()) <= allowed


# ─── disagreements() — named, never resolved ───

def test_disagreements_reports_a_typed_cell_the_broker_contradicts(sync):
    instruments_df = pd.DataFrame([
        {"instrument_id": "AAPL", "instrument_type": "etf"},   # wrong, on purpose
    ])
    broker_df = pd.DataFrame([
        {"instrument_id": "AAPL", "broad_type": "stock",
         "broker_sub_category": "COMMON"},
    ])
    out = sync.disagreements(instruments_df, broker_df)
    assert list(out["instrument_id"]) == ["AAPL"]
    assert out.iloc[0]["instrument_type"] == "etf"
    assert out.iloc[0]["broker_broad_type"] == "stock"


def test_disagreements_does_not_flag_a_blank_typed_cell(sync):
    """`instrument_type` is required by the real schema, so this cannot occur
    on a table `instruments.load()` would accept — but `disagreements()` is a
    pure function that takes whatever DataFrame it is given, and a blank cell
    is still "nothing to compare", never a disagreement to report."""
    instruments_df = pd.DataFrame([{"instrument_id": "ASML", "instrument_type": ""}])
    broker_df = pd.DataFrame([{"instrument_id": "ASML", "broad_type": "stock",
                              "broker_sub_category": "COMMON"}])
    assert sync.disagreements(instruments_df, broker_df).empty


def test_disagreements_does_not_flag_agreement(sync):
    instruments_df = pd.DataFrame([{"instrument_id": "ASML", "instrument_type": "stock"}])
    broker_df = pd.DataFrame([{"instrument_id": "ASML", "broad_type": "stock",
                              "broker_sub_category": "ADR"}])
    assert sync.disagreements(instruments_df, broker_df).empty


def test_disagreements_does_not_flag_an_unmapped_broad_type(sync):
    """A blank `broad_type` means the broker's assetCategory was never
    mapped (see broker_broad_types()) — there is nothing to compare a typed
    cell against, so this must never read as a disagreement."""
    instruments_df = pd.DataFrame([{"instrument_id": "BND1", "instrument_type": "stock"}])
    broker_df = pd.DataFrame([{"instrument_id": "BND1", "broad_type": "",
                              "broker_sub_category": "CORP"}])
    assert sync.disagreements(instruments_df, broker_df).empty


# ─── broker_broad_types() — one row per instrument, lots collapsed ───

def test_multiple_currency_lots_of_one_instrument_collapse_to_one_row(sync, stmt):
    """ASML_EUR and ASML_USD are two broker lots of the same company — the
    broad type does not change by currency, so it must not be reported twice.
    """
    broker_df, unresolved, unmapped = sync.broker_broad_types(stmt)
    asml_rows = broker_df[broker_df["instrument_id"] == "ASML"]
    assert len(asml_rows) == 1
    assert asml_rows.iloc[0]["broad_type"] == "stock"
    assert not unresolved
    assert not unmapped


_TWO_LOT_DIFFERENT_SUBCATEGORY_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="Test" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="UTEST" fromDate="2026-01-01" toDate="2026-12-31">
      <OpenPositions>
        <OpenPosition symbol="ASML" currency="EUR" assetCategory="STK" subCategory="COMMON" position="1" />
        <OpenPosition symbol="ASML" currency="USD" assetCategory="STK" subCategory="NY REG SHRS" position="2" />
      </OpenPositions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


def test_lots_that_disagree_on_sub_category_keep_both_labels(sync):
    """His real statement files ASML as COMMON on AEB in EUR and NY REG SHRS
    on NASDAQ in USD — the same company, two different IBKR labels. Picking
    one and dropping the other would silently lose real broker information,
    exactly what this task exists to stop.
    """
    root = parse_xml(_TWO_LOT_DIFFERENT_SUBCATEGORY_XML)
    two_lot_stmt = next(iter_statements(root))
    broker_df, unresolved, unmapped = sync.broker_broad_types(two_lot_stmt)
    assert not unresolved
    assert not unmapped
    row = broker_df[broker_df["instrument_id"] == "ASML"].iloc[0]
    assert row["broad_type"] == "stock"
    assert "COMMON" in row["broker_sub_category"]
    assert "NY REG SHRS" in row["broker_sub_category"]


def test_the_multi_value_separator_has_no_space(sync):
    """This project already has a convention for a multi-valued cell —
    `isin` (`NL0010273215;USN070592100`) and `claims.trade_ids`
    (`1000000006;1000000019`) both join with a bare `;`, no space. One table
    must not grow a second convention."""
    root = parse_xml(_TWO_LOT_DIFFERENT_SUBCATEGORY_XML)
    two_lot_stmt = next(iter_statements(root))
    broker_df, _, _ = sync.broker_broad_types(two_lot_stmt)
    row = broker_df[broker_df["instrument_id"] == "ASML"].iloc[0]
    assert row["broker_sub_category"] == "COMMON;NY REG SHRS"


_MIXED_PROBLEMS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="Test" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="UTEST" fromDate="2026-01-01" toDate="2026-10-04">
      <OpenPositions>
        <OpenPosition symbol="AAPL" currency="USD" assetCategory="STK" subCategory="COMMON" position="10" />
        <OpenPosition symbol="BND1" currency="USD" assetCategory="BOND" subCategory="CORP" position="5" />
        <OpenPosition symbol="" currency="USD" assetCategory="STK" subCategory="COMMON" position="3" />
      </OpenPositions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


@pytest.fixture(scope="module")
def mixed_stmt():
    """One ordinary position (AAPL), one resolvable instrument whose
    assetCategory this file does not map (BND1, a bond), and one position
    with no symbol at all — three different outcomes in one statement."""
    root = parse_xml(_MIXED_PROBLEMS_XML)
    return next(iter_statements(root))


def test_an_unmapped_asset_category_still_returns_a_row(sync, mixed_stmt):
    """BND1 resolves fine; only its assetCategory is unmapped. The
    sub_category WAS read successfully, so it must not be silently dropped —
    it still gets a row, with broad_type blank, and is also named in
    `unmapped` for visibility."""
    broker_df, unresolved, unmapped = sync.broker_broad_types(mixed_stmt)
    bnd1 = broker_df[broker_df["instrument_id"] == "BND1"]
    assert len(bnd1) == 1
    assert bnd1.iloc[0]["broad_type"] == ""
    assert bnd1.iloc[0]["broker_sub_category"] == "CORP"
    assert any(iid == "BND1" for iid, _why in unmapped)


def test_a_blank_symbol_is_unresolved_not_in_the_table(sync, mixed_stmt):
    broker_df, unresolved, unmapped = sync.broker_broad_types(mixed_stmt)
    assert "" not in set(broker_df["instrument_id"])
    assert len(unresolved) == 1


# ─── main(): a disagreement is reported and not overwritten ───

def test_a_disagreement_is_reported_and_not_overwritten(tmp_path, sync, capsys):
    """Both silent options are wrong: overwriting hides a real disagreement,
    keeping hides a real error. So neither — he settles it.

    AAPL is hand-typed `etf` (wrong — the broker says STK/COMMON, a stock).
    ASML is hand-typed `stock` (agrees). A real run must: leave AAPL's
    instrument_type exactly as typed, print the AAPL disagreement, leave
    ASML's instrument_type exactly as typed too (it already agrees — nothing
    to do), and write broker_sub_category (dated) for BOTH regardless.
    """
    p = _instruments_csv(tmp_path, [
        {"instrument_id": "AAPL", "name": "Apple Inc", "instrument_type": "etf"},
        {"instrument_id": "ASML", "name": "ASML Holding NV", "instrument_type": "stock"},
    ])

    sync.main(instruments_path=p, xml_path=XML_FIXTURE)
    out = capsys.readouterr().out

    aapl = inst.get("AAPL", path=p)
    asml = inst.get("ASML", path=p)

    assert aapl["instrument_type"] == "etf", (
        "a typed cell the broker disagrees with must survive untouched"
    )
    assert aapl["broker_sub_category"] == "COMMON", (
        "broker_sub_category is written always, disagreement or not"
    )
    assert aapl["broker_sub_category_as_of"] == "2025-12-31", (
        "dated from the statement's toDate, like provider_sector_as_of"
    )
    assert asml["instrument_type"] == "stock", (
        "an agreeing cell is never touched either — this script writes "
        "instrument_type nowhere, ever"
    )
    assert asml["broker_sub_category"] == "COMMON"

    assert "AAPL" in out and "etf" in out and "stock" in out, (
        f"the AAPL disagreement must be printed:\n{out}"
    )


def test_broker_sub_category_is_written_even_when_types_agree(tmp_path, sync):
    """The 'always' half of the rule, isolated from the disagreement case:
    a cell that already agrees with the broker still gets its finer label."""
    p = _instruments_csv(tmp_path, [
        {"instrument_id": "AAPL", "name": "Apple Inc", "instrument_type": "stock"},
        {"instrument_id": "ASML", "name": "ASML Holding NV", "instrument_type": "stock"},
    ])
    sync.main(instruments_path=p, xml_path=XML_FIXTURE)
    assert inst.get("AAPL", path=p)["broker_sub_category"] == "COMMON"
    assert inst.get("ASML", path=p)["broker_sub_category"] == "COMMON"


def test_a_broker_position_with_no_instrument_row_is_reported_not_fatal(tmp_path, sync, capsys):
    """AAPL exists at the broker but was never added to instruments.csv —
    the same gap instruments.gaps() surfaces. One missing row must not stop
    ASML, which IS known, from being processed."""
    p = _instruments_csv(tmp_path, [
        {"instrument_id": "ASML", "name": "ASML Holding NV", "instrument_type": "stock"},
    ])
    sync.main(instruments_path=p, xml_path=XML_FIXTURE)
    out = capsys.readouterr().out
    assert inst.get("ASML", path=p)["broker_sub_category"] == "COMMON"
    assert "AAPL" in out


def test_an_unmapped_asset_category_still_gets_its_sub_category_written(tmp_path, sync, capsys):
    """BND1's assetCategory is unmapped, but its sub_category was read just
    fine — writing that is not the thing that failed, so it must not be
    silently skipped."""
    p = _instruments_csv(tmp_path, [
        {"instrument_id": "AAPL", "name": "Apple Inc", "instrument_type": "stock"},
        {"instrument_id": "BND1", "name": "Some Bond", "instrument_type": "stock"},
    ])
    xml_path = tmp_path / "mixed.xml"
    xml_path.write_bytes(_MIXED_PROBLEMS_XML)

    sync.main(instruments_path=p, xml_path=xml_path)
    out = capsys.readouterr().out

    assert inst.get("BND1", path=p)["broker_sub_category"] == "CORP"
    assert inst.get("BND1", path=p)["instrument_type"] == "stock", (
        "never touched — this script writes instrument_type nowhere"
    )
    assert "BND1" in out   # named under the unmapped-category section


def test_the_matched_count_includes_every_position_the_statement_named(tmp_path, sync, capsys):
    """The denominator must not quietly shrink to only the rows this run
    could fully process — a symbol that never resolved to anything is still
    a broker position, and the total must say so."""
    p = _instruments_csv(tmp_path, [
        {"instrument_id": "AAPL", "name": "Apple Inc", "instrument_type": "stock"},
        {"instrument_id": "BND1", "name": "Some Bond", "instrument_type": "stock"},
    ])
    xml_path = tmp_path / "mixed.xml"
    xml_path.write_bytes(_MIXED_PROBLEMS_XML)

    sync.main(instruments_path=p, xml_path=xml_path)
    out = capsys.readouterr().out

    # AAPL + BND1 matched an instrument; the blank-symbol row never resolved
    # to anything at all — three positions total, not two.
    assert out.splitlines()[0] == (
        "2 of 3 broker position(s) matched an instrument; "
        "broker_sub_category written for each."
    )
