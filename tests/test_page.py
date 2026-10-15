"""Shaping, and the three promises the shape has to keep.

This module computes nothing (spec D2) — every number comes from a function in
src/, and anything missing goes into the module that owns it. These tests are
mostly about that: that the page cannot invent a number, that every number
carries where it came from, and that a missing input reaches the screen as a
sentence rather than as a zero.

Several tests here run against the REAL project data on purpose (no monkeypatch)
— that is deliberate, not an oversight: panels 4/6/7 of this plan found three
real defects that only existed in the live book (MAGS's hand-typed rows, SPY's
152-constituent floor, a cash balance reading as a company), and a page module
whose own tests only ever see a clean three-row fixture would have shipped
every one of them.

Run from project root:
    pytest tests/test_page.py
"""

from __future__ import annotations

import csv

import pandas as pd
import pytest

from src import notes as notes_mod
from src import page


# ─── fixtures ───────────────────────────────────────────────────────────────

def _positions(tmp_path, rows):
    p = tmp_path / "positions.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _clusters(tmp_path, rows):
    from src import clusters
    p = tmp_path / "clusters.csv"
    df = pd.DataFrame(rows)
    for col in clusters.COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[clusters.COLUMNS].to_csv(p, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return p


def _holdings_table(tmp_path, rows):
    from src import market
    p = tmp_path / "etf_holdings.csv"
    pd.DataFrame(rows, columns=market.HOLDINGS_COLUMNS).to_csv(
        p, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return p


def _instruments(monkeypatch, rows):
    """Monkeypatch `instruments.load()` with a tiny store.

    Same shape as `tests/test_exposure.py`'s own helper — defaults filled in
    for every column `book.lots()`/`clusters.tree()`/this module's own
    `_instrument_lookup()` read, so a test only has to name what it cares
    about.
    """
    from src import instruments
    defaults = {
        "instrument_type": "stock", "sector": "", "provider_sector": "",
        "provider_sector_as_of": "", "cluster_id": "", "note_ref": "",
        "name": "", "price_ticker": "", "av_ticker": "", "sector_settled": "",
        "provider_sector_source": "",
    }
    filled = [{**defaults, **row} for row in rows]
    monkeypatch.setattr(instruments, "load",
                        lambda *a, **k: pd.DataFrame(filled))


@pytest.fixture(scope="module")
def real_context():
    """`page.context()` built ONCE for this file, against the real files on
    disk, shared by every test below that only READS it (fix round 3,
    Important A). A read-only snapshot of the same data every one of these
    tests was already reading — sharing it changes no assertion, since
    nothing here mutates it — but before this fixture existed, three tests
    each built their own `page.context()` from scratch: one of them three
    times over (once per basis, in a loop), the other two once each.
    `page.context()` costs roughly 20 seconds on its own, so that alone put
    a ~2.5-minute gate on every future edit under `src/` or `tests/` in a
    repository whose own hook runs this suite after each one, forever.

    ⚠️ **Do NOT add a test here that needs different inputs.** Any test
    that monkeypatches a loader, passes its own `positions_path`/
    `clusters_path`/`holdings_path`/etc., or otherwise needs a context
    built from something other than the real files must keep building its
    own `page.context(...)` call, inline or on a fixture of its own — a
    module-scoped fixture is built exactly once, before any per-test
    monkeypatch exists, so sharing this one with such a test would either
    silently ignore the monkeypatch or leak one test's fixture data into
    another's assertions. (See
    `test_the_honesty_block_is_assembled_per_basis_not_shipped_once`, which
    deliberately does NOT use this fixture, for exactly that reason.)
    """
    return page.context()


# ─── D12: every figure names the call that produced it ────────────────────

def test_every_figure_carries_the_function_that_produced_it(real_context):
    """Spec D12. The value is not tidiness: when the page and the chat
    disagree, both he and the session know instantly which to trust. Structural
    — a shaping function attaches it — never typed per number."""
    ctx = real_context
    figures = []
    for basis in ctx["bases"]:
        view = ctx["bases"][basis]
        figures += [b.figure for b in view["theme_bars"]]
        figures += [b.figure for b in view["header"]["biggest"]]
        figures += [b.figure for b in view["company_bars"]]
    assert figures
    assert all(f.source for f in figures)
    assert all("(" in f.source for f in figures), \
        "a source must name a call, e.g. clusters.tree(), not a prose phrase"


# ─── D5: a bar's width is relative to ITS OWN DEPTH, never the whole page ──

def test_a_bar_width_is_relative_to_the_widest_bar_at_its_own_depth(tmp_path, monkeypatch):
    """A theme bar and a holding bar scaled against the same maximum makes
    every holding invisible. Depth is the scale.

    Fixture engineered so the failure mode is unmissable if it regresses:
    `X` is a single huge standalone theme ($1,000,000, one holding) sitting
    at depth 0/1; `Y` is a GROUP holding a tiny child theme `Y1` ($1,000)
    whose own two holdings ($600/$400) sit at depth 2 — nowhere near `X`'s
    million-dollar bar in the tree, but sharing the same literal depth
    number as other depth-2 rows. If `_width()` were ever called with the
    GLOBAL widest bar on the page ($1,000,000) instead of the widest bar at
    depth 2 ($600), Y1's bigger holding would read ~0.06 wide instead of
    100 — a sliver nobody could see, exactly the failure the brief names.
    """
    pos = _positions(tmp_path, [
        {"ticker": "XCO", "instrument_id": "XCO", "market_value": 1_000_000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "Y1A", "instrument_id": "Y1A", "market_value": 600.0,
         "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "Y1B", "instrument_id": "Y1B", "market_value": 400.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "X", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
        {"cluster_id": "Y", "parent": "", "layer": "group",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
        {"cluster_id": "Y1", "parent": "Y", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "XCO", "name": "X Co", "cluster_id": "X"},
        {"instrument_id": "Y1A", "name": "Y1 A", "cluster_id": "Y1"},
        {"instrument_id": "Y1B", "name": "Y1 B", "cluster_id": "Y1"},
    ])

    bars = page.theme_bars("everything", positions_path=pos, clusters_path=cl)
    by_label = {b.label: b for b in bars}

    assert by_label["X Co"].depth == 1
    assert by_label["Y1 A"].depth == 2
    assert by_label["Y1 B"].depth == 2

    # The two depth-2 holdings scale against EACH OTHER (widest = 600) —
    # never against X Co's $1,000,000 bar two families over.
    assert by_label["Y1 A"].width == 100.0
    assert by_label["Y1 B"].width == pytest.approx(66.67, abs=0.01)
    # The number a global-max bug would have produced instead — pinned so a
    # regression that lands near-zero cannot be mistaken for a rounding diff.
    assert by_label["Y1 A"].width > 50.0


# ─── D8: a short is negative exposure, flagged, and ranked by magnitude ───

def test_a_negative_bar_is_flagged_and_keeps_its_sign(tmp_path, monkeypatch):
    """Spec D8: a short is negative exposure and is drawn that way. The
    instrument taxonomy for shorts, futures and options is still open
    (REBUILD.md §2c) and the page says so rather than implying it is settled."""
    pos = _positions(tmp_path, [
        {"ticker": "LONG", "instrument_id": "LONG", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "SHORT", "instrument_id": "SHORT", "market_value": -500.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "LONG", "name": "Long Co"},
        {"instrument_id": "SHORT", "name": "Short Co"},
    ])

    bars = page.company_bars("everything", positions_path=pos, holdings_path=hold)
    by_label = {b.label: b for b in bars}
    long_bar, short_bar = by_label["Long Co"], by_label["Short Co"]

    assert short_bar.negative is True
    assert long_bar.negative is False
    assert short_bar.figure.value == -500.0
    assert short_bar.figure.text == "-$500.00"       # keeps the sign (D8)
    # Ranked/scaled by MAGNITUDE, not signed value — a short worth half the
    # long is drawn at half the long's width, not treated as "negative
    # space" that shrinks everything else or vanishes from the ranking.
    assert 0 < short_bar.width < long_bar.width == 100.0


# ─── D7/D9: money is tabular text, never coloured, sign kept ──────────────

def test_money_is_tabular_and_never_coloured_by_sign():
    """D7: P&L is grey, never coloured. A map of the spread is not a
    scoreboard."""
    positive = page.money(1234.5)
    negative = page.money(-1234.5)

    assert positive == "$1,234.50"
    assert negative == "-$1,234.50"
    # Tabular: identical digit grouping and decimal places on both sides of
    # zero, so a column of these lines up — only the leading sign differs.
    assert positive.lstrip("-") == negative.lstrip("-")
    assert page.money(0) == "$0.00"
    # D7 — never coloured: no markup or colour hint leaks into plain text,
    # on either side of zero, that a renderer could key a colour off of.
    for text in (positive, negative):
        lowered = text.lower()
        for marker in ("color", "red", "green", "<span", "<font", "\x1b", "class="):
            assert marker not in lowered


# ─── D6: the state line is a sentence, not a key ───────────────────────────

def test_the_state_line_says_what_the_number_is_a_share_of(real_context):
    """D6. He is not a developer; 'pure-equity' is a key, not a sentence.

    Fix round 3, Important A: the original version called `page.context()`
    fresh on every loop iteration — three full rebuilds for a test that
    reads three pre-existing strings. Shares `real_context` instead; the
    assertion is unchanged.
    """
    for basis in page.BASES_ORDER:
        line = real_context["bases"][basis]["header"]["state_line"]
        assert len(line.split()) > 3


# ─── the honesty contract ───────────────────────────────────────────────────

def test_the_honesty_lines_name_price_age_estimation_and_unclassified_money():
    """The honesty contract is not optional and not droppable for space. Each
    of these is its own sentence because each is a different kind of doubt."""
    lines = " ".join(page.honesty("everything")).lower()
    assert "price" in lines
    assert "floor" in lines or "at least" in lines
    assert "vendor" in lines
    assert "gics" in lines


def test_a_floor_funds_own_magnitude_reaches_the_honesty_block(tmp_path,
                                                               monkeypatch):
    """Fix, 2026-10-07 final review (I7) — an explicit instruction
    ('the panel must also say HOW MUCH is unstored') that did not land.
    `src/page.py:942-950` used to read only `company_coverage()`'s `ticker`
    column and print a bare list of floor-fund names — with almost every
    fund a floor, naming WHICH ones carries almost no information.
    `company_coverage()`'s own `why` already states the magnitude in his
    language; this test pins that it actually reaches the screen, not just
    that the word 'floor' appears somewhere (the weak, pre-existing
    assertion above would pass even if every fund's own number were
    deleted)."""
    pos = _positions(tmp_path, [
        {"ticker": "BROAD", "instrument_id": "BROAD", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "BROAD", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.79, "coverage": 1.0, "source": "issuer file",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    _instruments(monkeypatch, [{"instrument_id": "BROAD", "name": "Broad Fund",
                                "instrument_type": "etf"}])
    lines = page.honesty("everything", positions_path=pos, holdings_path=hold)
    assert any(line.startswith("BROAD: ") and "79%" in line for line in lines), (
        f"company_coverage()'s own per-fund magnitude never reached the "
        f"honesty block — lines were: {lines}"
    )


def test_the_sp_comparison_says_which_basis_it_was_made_under():
    """His percentages are of the basis he selected; the index's are of an
    all-equity fund. On Everything his cash sits in his denominator and not
    in the index's, so every difference reads low for a reason that is
    arithmetic and not construction. The note travels from the frame —
    page.py prints it, it does not decide it."""
    for basis in page.BASES_ORDER:
        rows, note = page.benchmark_company_rows(basis, with_note=True)
        if rows:
            assert note


def test_the_sp_comparison_states_pass_through_the_frame_unchanged(tmp_path, monkeypatch):
    """Fix round 1 (Important G, feeding Critical A). The original version
    of this test asserted membership in a hard-coded four-value tuple that
    still contained the RETIRED `only-mine` relabelling — a test built
    around the very mapping it was meant to catch cannot fail when that
    mapping is the bug, and its `unknown` branch never ran because no such
    row exists anywhere in the live book (SPY's own file claims full
    coverage, so `below-floor` is the only "absent" state real data ever
    produces). This version computes `exposure.benchmark_companies()`
    directly and asserts the page's own `state` column is IDENTICAL to the
    frame's, row for row — the only assertion that can actually catch a
    page-level rename.

    Two small fixtures, not one: `below-floor` needs the index's SOURCE to
    claim completeness and `unknown` needs it not to, and that completeness
    is one scalar for an entire ticker's fixture (`coverage.max()`), so the
    two states cannot coexist inside a single one. Together the two
    fixtures below exercise `both`, `only-index`, `below-floor` AND
    `unknown` — all four raw states `exposure.benchmark_companies()` can
    produce, including the one the live book never reaches.
    """
    from src import exposure

    # Fixture 1: a COMPLETE index (coverage 1.0) -> both / below-floor / only-index.
    d1 = tmp_path / "f1"
    d1.mkdir()
    pos1 = _positions(d1, [
        {"ticker": "BOTHCO", "instrument_id": "BOTHCO", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "ONLYMINE", "instrument_id": "ONLYMINE", "market_value": 500.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    hold1 = _holdings_table(d1, [
        {"ticker": "SPY", "holding": "BOTHCO", "holding_name": "Both Co",
         "weight": 0.01, "coverage": 1.0, "source": "test",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "INDEXONLY", "holding_name": "Index Only Co",
         "weight": 0.01, "coverage": 1.0, "source": "test",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BOTHCO", "name": "Both Co"},
        {"instrument_id": "ONLYMINE", "name": "Only Mine Co"},
    ])

    raw1 = exposure.benchmark_companies(basis="everything",
                                        positions_path=pos1, holdings_path=hold1)
    page1 = page.benchmark_company_rows("everything",
                                        positions_path=pos1, holdings_path=hold1)
    raw_state_1 = {r["key"]: r["state"] for _, r in raw1.iterrows()}
    assert raw_state_1  # the fixture actually produced rows
    for row in page1:
        assert row["state"] == raw_state_1[row["key"]]
    assert set(raw_state_1.values()) == {"both", "below-floor", "only-index"}

    # Fixture 2: a THIN index (coverage 0.3) -> unknown.
    d2 = tmp_path / "f2"
    d2.mkdir()
    pos2 = _positions(d2, [
        {"ticker": "BOTHCO2", "instrument_id": "BOTHCO2", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "UNKNOWNCO", "instrument_id": "UNKNOWNCO", "market_value": 500.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    hold2 = _holdings_table(d2, [
        {"ticker": "SPY", "holding": "BOTHCO2", "holding_name": "Both Co 2",
         "weight": 0.01, "coverage": 0.3, "source": "test",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BOTHCO2", "name": "Both Co 2"},
        {"instrument_id": "UNKNOWNCO", "name": "Unknown Co"},
    ])

    raw2 = exposure.benchmark_companies(basis="everything",
                                        positions_path=pos2, holdings_path=hold2)
    page2 = page.benchmark_company_rows("everything",
                                        positions_path=pos2, holdings_path=hold2)
    raw_state_2 = {r["key"]: r["state"] for _, r in raw2.iterrows()}
    for row in page2:
        assert row["state"] == raw_state_2[row["key"]]
    assert raw_state_2["UNKNOWNCO"] == "unknown"
    unknown_row = next(r for r in page2 if r["key"] == "UNKNOWNCO")
    assert ("unknown" in unknown_row["note"].lower()
           or "not know" in unknown_row["note"].lower())

    # No state from EITHER fixture is the retired `only-mine` — proof A
    # cannot silently come back.
    all_states = set(raw_state_1.values()) | set(raw_state_2.values())
    assert "only-mine" not in all_states


def test_what_the_sp_comparison_cannot_cover_is_shown_under_it():
    """by_company()'s per-fund unaccounted rows have no index counterpart and
    are dropped from the comparison. Dropping them from the SCREEN would make a
    comparison that covers part of his book read as if it covered all of it."""
    rows = page.benchmark_company_rows("pure-equity")
    if rows:
        assert any("not named" in line.lower() or "unaccounted" in line.lower()
                   for line in page.honesty("pure-equity"))


def test_a_both_row_through_a_floor_fund_withholds_the_exact_difference(
        tmp_path, monkeypatch):
    """Fix, pre-push review 2026-10-07 (finding 2). `is_floor` reached this
    row and was then never read by the renderer — `exposure.
    benchmark_companies()` used to publish an exact `difference` for every
    `both` row regardless, which 272 of 279 live rows are. His own side here
    comes through BROAD, a fund whose stored holdings stop at a 50% weight
    for NVDA — a real gap, not a complete picture — so the exact difference
    must be withheld in favour of the lower-bound `difference_floor`, and the
    note must say so in words, not just in a blank `benchmark` cell the way
    `below-floor` does."""
    pos = _positions(tmp_path, [
        {"ticker": "BROAD", "instrument_id": "BROAD", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "BROAD", "name": "Broad Fund", "instrument_type": "etf"},
    ])
    hold = _holdings_table(tmp_path, [
        {"ticker": "BROAD", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.5, "coverage": 1.0, "source": "issuer file, long tail "
         "dropped below the 0.05% floor", "as_of": "2026-10-01",
         "as_of_kind": "published"},
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.08, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    rows = page.benchmark_company_rows("everything", positions_path=pos,
                                       holdings_path=hold)
    row = next(r for r in rows if r["key"] == "NVDA")
    assert row["state"] == "both"
    assert row["is_floor"] is True
    assert row["difference"] == "", (
        "an exact difference was published for a row whose own side is a "
        "floor — the live bug, 272 of 279 rows"
    )
    assert isinstance(row["difference_floor"], page.Figure)
    assert row["difference_floor"].value == pytest.approx(42.0)
    assert row["note"], "a floor both-row rendered with a blank note"
    assert "floor" in row["note"].lower() or "least" in row["note"].lower()


def test_a_missing_benchmark_reaches_the_screen_as_a_sentence(monkeypatch):
    """Not a zero, which states that the S&P holds none of that sector, and not
    a blank, which says there is nothing to say."""
    from src import exposure
    empty = pd.DataFrame(columns=["sector", "weight_pct", "as_of",
                                  "source_quality"])
    empty.attrs["why"] = "not fetched yet — run /market"
    monkeypatch.setattr(exposure, "benchmark_weights", lambda *a, **k: empty)
    rows = page.sector_rows("everything")
    assert all(r["benchmark"] == "" for r in rows)
    assert any("/market" in note for note in page.honesty("everything"))


def test_a_missing_benchmark_invents_no_state_either(monkeypatch):
    """Fix round 1, Critical B. The first version fell back to
    `state="only-mine"` for EVERY sector when the comparison was never
    built — exactly the `fillna(0.0)`-shaped move `exposure.
    benchmark_sector_comparison()` exists to refuse, recommitted one layer
    up. No benchmark data means no state at all, not a confident one."""
    from src import exposure
    empty = pd.DataFrame(columns=["sector", "weight_pct", "as_of",
                                  "source_quality"])
    empty.attrs["why"] = "not fetched yet — run /market"
    monkeypatch.setattr(exposure, "benchmark_weights", lambda *a, **k: empty)
    rows = page.sector_rows("everything")
    assert rows  # his own by_sector() shape is unaffected by the patch
    assert all(r["state"] == "" for r in rows)
    assert all(r["difference"] == "" for r in rows)


def test_a_sector_the_index_holds_and_he_does_not_still_reaches_the_panel(
        tmp_path, monkeypatch):
    """Fix, 2026-10-07 final review (I3). `sector_rows()` used to iterate
    `exposure.by_sector()`'s OWN rows only, so a sector the S&P reports and
    he has zero exposure to (`state="only-index"`, computed and carried by
    `exposure.benchmark_sector_comparison()`) was silently dropped — the one
    row this panel exists to show: "the index has this much and you have
    nothing". Unlike a company, his own absence from a sector IS a
    confirmed zero (no small-weight floor hiding a sector the way there is
    hiding a company), so the added row states his side as a real `0.0`,
    never a blank dash."""
    from src import exposure

    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 700.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock",
         "sector": "Tech - Semiconductors"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(
        [{"ticker": "SPY", "sector": "Technology", "weight": 0.5,
          "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
          "source_quality": "vendor", "as_of": "2026-10-06"},
         {"ticker": "SPY", "sector": "Financials", "weight": 0.5,
          "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
          "source_quality": "vendor", "as_of": "2026-10-06"}],
        columns=exposure.COLUMNS,
    ).to_csv(comp, index=False, quoting=csv.QUOTE_NONNUMERIC)

    rows = page.sector_rows("everything", positions_path=pos,
                            compositions_path=comp, classification="mine")
    by_sector = {r["sector"]: r for r in rows}

    assert "Financials" in by_sector, (
        "the index-only sector was dropped from the panel instead of "
        "reaching it with his side stated as a true zero"
    )
    fin = by_sector["Financials"]
    assert fin["state"] == "only-index"
    assert fin["value"].value == 0.0
    assert fin["pct"].value == 0.0
    assert fin["direct_value"].value == 0.0
    assert fin["estimated_pct"].value == 0.0
    assert fin["benchmark"].value == pytest.approx(50.0)
    # His own side is a CONFIRMED zero here (unlike the company panel) —
    # real Figures, never the blank "" an absent/unmeasured value gets.
    for key in ("value", "pct", "direct_value", "estimated_pct"):
        assert isinstance(fin[key], page.Figure), (
            f"sector {key!r} must be a real zero Figure, not a blank — his "
            f"absence from a sector is certain, not merely unmeasured"
        )

    # Technology (both sides present) is unaffected by the added row.
    tech = by_sector["Technology"]
    assert tech["state"] == "both"
    assert tech["value"].value > 0.0

    # Fix, residual review (N3): these four zeros are not read out of
    # exposure.by_sector() — that function returned no row for Financials
    # at all, which is the entire reason this block exists — so the hover
    # text must not claim it did, and the row must not render with nothing
    # explaining the zero.
    for key in ("value", "pct", "direct_value", "estimated_pct"):
        assert fin[key].source != "exposure.by_sector()", (
            f"sector {key!r}'s source still names exposure.by_sector() — "
            f"that function produced no row for this sector at all, so "
            f"this is a false attribution"
        )
    assert fin["note"], (
        "an only-index sector row rendered with a blank note — nothing on "
        "screen explains why his side is a confirmed zero"
    )


def test_unclassified_money_stops_a_sibling_sector_claiming_a_confirmed_zero(
        tmp_path, monkeypatch):
    """Fix, pre-push review 2026-10-07 (finding 1) — the critical one,
    reproduced on this exact shape: a newly bought real-estate ETF held
    before `/market` ever fetched its breakdown. Before the fix, its money
    read under 'Other' captioned 'SPY is an all-equity fund, so zero here is
    certain, not a gap' — false; the money's sector is simply unknown — and
    the real `Real Estate` row read his side as a confirmed `$0.00` one row
    away, while he actually held every one of those dollars. Two
    contradictory statements about the same money."""
    from src import exposure

    pos = _positions(tmp_path, [
        {"ticker": "NEWREIT", "instrument_id": "NEWREIT",
         "market_value": 20000.0, "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 10000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "NEWREIT", "name": "New REIT Fund",
         "instrument_type": "etf"},   # no composition row filed yet
        {"instrument_id": "NVDA", "name": "NVIDIA", "instrument_type": "stock",
         "sector": "Tech - Semiconductors"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(
        [{"ticker": "SPY", "sector": "Real Estate", "weight": 0.1,
          "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
          "source_quality": "vendor", "as_of": "2026-10-06"},
         {"ticker": "SPY", "sector": "Technology", "weight": 0.9,
          "top_holding": "", "source": "Morningstar via yfinance 2026-10-06",
          "source_quality": "vendor", "as_of": "2026-10-06"}],
        columns=exposure.COLUMNS,
    ).to_csv(comp, index=False, quoting=csv.QUOTE_NONNUMERIC)

    rows = page.sector_rows("everything", positions_path=pos,
                            compositions_path=comp, classification="mine")
    by_sector = {r["sector"]: r for r in rows}

    other = by_sector["Other"]
    assert other["state"] == "unclassified"
    assert other["value"].value == pytest.approx(20000.0), (
        "NEWREIT's money must still be counted, just not certified"
    )
    assert "cannot hold this sector" not in other["note"], (
        "the certain-zero caption (true only of Crypto/Cash) leaked onto "
        "unclassified money"
    )
    assert "unknown" in other["note"].lower()

    re_row = by_sector["Real Estate"]
    assert re_row["state"] == "only-index"
    assert re_row["value"].value == 0.0   # his CLASSIFIED money there really is 0
    assert "confirmed a true zero" not in re_row["note"], (
        "Real Estate still claimed a confirmed zero while $20,000 of "
        "unclassified money — which could belong here — sat in 'Other'"
    )
    assert "not a confirmed zero" in re_row["note"].lower()


# ─── D2's own guard ──────────────────────────────────────────────────────

def test_shaping_computes_no_arithmetic_of_its_own():
    """The guard for D2. Totals, percentages and differences all arrive
    computed; page.py may format, order and scale a bar, and may not add.

    Written as a rule about WHERE an arithmetic operator may appear —
    confined to `_width()`, the one bar-scaling helper — rather than as a
    cap on how many `/` characters the file contains (the plan's own
    ruling, 2026-10-07: a bare numeric cap is itself a magic number, and
    the first legitimate division anywhere else would pressure someone to
    raise it). Built on `ast`, not a regex over raw text, so a `/` inside a
    docstring or a file-path string (this module is full of them —
    `data/clusters.csv`, `notes/positions/<T>.md`) is never mistaken for an
    arithmetic operator: only a real AST node in the parsed CODE counts as
    "the page is computing".

    Fix round 1 (2026-10-07): extended beyond division alone to
    multiplication, subtraction, and `sum()`/`.sum()`/`mean()`/`.mean()`
    calls — the other ways a total, a difference or an average could be
    invented here. Addition is deliberately NOT flagged: this module's own
    sentence-building legitimately concatenates strings with `+`
    (`"a" + "b"`), and flagging it would either break every honesty
    sentence or force a false exemption into the guard.

    🚨 **Fix, 2026-10-07 final review (I9).** The check itself moved to
    `tests/_arithmetic_guard.py`, shared with `ops/build_page.py`'s own copy
    of this test below — one definition of "the page is computing",
    checked against both files that must never do it, rather than two that
    could drift. The review proved the OLD, file-local version of this
    check (a BinOp/`sum`/`mean` walk only) let four real evasions through a
    scratch mutation: a running `total +=` built in a loop,
    `df["x"].agg("sum")`, `df["x"].cumsum()`, and `statistics.fmean()` — see
    the shared module's own docstring for why each is now caught.

    🚨 **Fix, residual review (N2).** The self-check used to live HERE as a
    second, inline `ast.parse` + manual walk — proving `src/page.py` still
    contains a division somewhere, never that `arithmetic_offenders()`
    itself found it. A stub that returned `[]` unconditionally would have
    passed the line above and this inline walk both. The real self-check —
    that the guard still fires on `src/page.py` when `_width()` is not
    exempted, through the SAME `arithmetic_offenders()` entry point this
    test already calls above, not a second re-implementation — now lives
    in `tests/test_arithmetic_guard.py` (alongside the synthetic-source
    tests that prove the guard can catch each of the four named evasions,
    and tolerate a path join / string concatenation / a clamp), where it
    also covers `ops/build_page.py`, which never had a self-check at all.
    """
    from pathlib import Path

    from tests._arithmetic_guard import arithmetic_offenders

    path = Path("src/page.py")
    offenders = arithmetic_offenders(path, exempt_function="_width")
    assert not offenders, (
        f"page.py computes outside _width() at line(s) {offenders} — a "
        f"total, difference or average computed here is a second "
        f"definition of a number a src/ module already owns (D2); put it "
        f"in that module instead. Bar scaling in _width() is the only "
        f"exception."
    )


def test_nothing_in_the_page_layer_opens_a_file_for_writing():
    """The page is read-only over the settled data layer, and that is a
    property of the code, not a promise in a docstring.

    `src/page.py` gets none of the tokens below, not even the one write
    `ops/build_page.py` is allowed (it has no business writing anything at
    all — it only shapes numbers someone else already wrote).

    `ops/build_page.py` (Task 11) gets the same check MINUS `.write_text(` —
    it is the thing that renders the page to disk, so one write is its
    whole job (D1) — and that one write is itself pinned to `OUTPUT_PATH`
    by name, so a second call writing somewhere else would still be caught:
    the count must be exactly 1, not merely "at least one write exists".
    """
    from pathlib import Path
    text = Path("src/page.py").read_text(encoding="utf-8")
    for bad in ("to_csv(", ".write_text(", "mode='w'", 'mode="w"',
                "add_claim", "set_", "resolve_claim"):
        assert bad not in text, f"src/page.py contains {bad!r}"

    build_text = Path("ops/build_page.py").read_text(encoding="utf-8")
    for bad in ("to_csv(", "mode='w'", 'mode="w"',
                "add_claim", "set_", "resolve_claim"):
        assert bad not in build_text, f"ops/build_page.py contains {bad!r}"
    writes = build_text.count(".write_text(")
    assert writes == 1, (
        f"ops/build_page.py calls .write_text() {writes} times — exactly "
        f"one write (the built page itself, D1) is the whole point of this "
        f"file; any other count means it is writing somewhere this guard "
        f"was never told about."
    )
    assert "OUTPUT_PATH.write_text(" in build_text, (
        "the one write in ops/build_page.py should target OUTPUT_PATH "
        "directly, so a reviewer can see at a glance it is the only path "
        "ever written"
    )


# ─── extra coverage for the produced shapes the brief names but the given
#     tests above do not otherwise touch ───────────────────────────────────

def test_context_shape_for_every_basis(real_context):
    """`context()` ships all three bases in one file (D1) — no basis is
    computed on demand by the control."""
    ctx = real_context
    assert set(ctx["bases"]) == set(page.BASES_ORDER)
    assert "built_at" in ctx and ctx["built_at"]
    # Fix round 1, Important H: `priced_at` would lie about its own shape —
    # that name means an ISO date everywhere else in this project. Renamed
    # to say what it actually is: an integer day-count straight from
    # `instruments.price_age_days()`.
    assert "price_age_days" in ctx
    assert "priced_at" not in ctx
    assert isinstance(ctx["price_age_days"], int) or ctx["price_age_days"] is None


def test_the_honesty_block_is_assembled_per_basis_not_shipped_once(tmp_path, monkeypatch):
    """Fix round 1, Important C. The first version computed the honesty
    block once for "everything" and shipped it top-level, so the
    `pure-equity` view would have displayed a basis note that said
    "everything" at the top of the page — the exact denominator confusion
    the basis note exists to prevent. A fixture with real non-equity money
    makes the basis-note text differ in a way a single shared block could
    not satisfy for both bases at once.

    Deliberately does NOT use the `real_context` fixture (fix round 3,
    Important A) — it needs a `page.context()` built from ITS OWN tiny
    fixture, not the real files on disk, which is exactly the case that
    fixture's own docstring says must keep building its own.
    """
    pos = _positions(tmp_path, [
        {"ticker": "STOCK", "instrument_id": "STOCK", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "GOLD", "instrument_id": "GOLD", "market_value": 500.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "Equities", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
        {"cluster_id": "Bullion", "parent": "", "layer": "thematic",
         "in_equity_base": False, "status": "active", "added": "2026-01-01"},
    ])
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "STOCK", "name": "Stock Co", "cluster_id": "Equities"},
        {"instrument_id": "GOLD", "name": "Gold Fund", "instrument_type": "etf",
         "cluster_id": "Bullion"},
    ])

    ctx = page.context(positions_path=pos, clusters_path=cl, holdings_path=hold)
    everything_text = " ".join(ctx["bases"]["everything"]["honesty"])
    pure_equity_text = " ".join(ctx["bases"]["pure-equity"]["honesty"])

    assert "'everything' basis" in everything_text
    assert "'everything' basis" not in pure_equity_text
    assert "'pure-equity' basis" in pure_equity_text


def test_the_sp_company_panel_and_overlap_rows_carry_provenance_too(tmp_path, monkeypatch):
    """Fix round 1, Important E. The whole S&P company panel and the
    overlap rows used to ship bare formatted strings — EVERY figure on the
    page must say which call produced it (D12), not just the ones that
    happen to be `Bar`s."""
    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "BBB", "instrument_id": "BBB", "market_value": 500.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "ThemeA", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
        {"cluster_id": "ThemeB", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
    ])
    # `benchmark_companies()` needs the INDEX's own holdings too, or it
    # takes the early-empty path (no rows at all) regardless of his side.
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "AAA", "holding_name": "A",
         "weight": 0.01, "coverage": 1.0, "source": "test",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "AAA", "name": "A", "cluster_id": "ThemeA"},
        {"instrument_id": "BBB", "name": "B", "cluster_id": "ThemeB"},
    ])

    bc_rows = page.benchmark_company_rows("everything", positions_path=pos,
                                          holdings_path=hold)
    assert bc_rows
    for row in bc_rows:
        assert isinstance(row["mine"], page.Figure)
        assert "(" in row["mine"].source


def test_overlap_rows_carry_provenance(tmp_path, monkeypatch):
    """The overlap panel's own figures, same requirement as above."""
    hold = _holdings_table(tmp_path, [
        {"ticker": "FUND1", "holding": "SHARED", "holding_name": "Shared Co",
         "weight": 1.0, "coverage": 1.0, "source": "test",
         "as_of": "2026-10-01", "as_of_kind": "published"},
        {"ticker": "FUND2", "holding": "SHARED", "holding_name": "Shared Co",
         "weight": 1.0, "coverage": 1.0, "source": "test",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "ThemeA", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
        {"cluster_id": "ThemeB", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "FUND1", "name": "Fund One", "instrument_type": "etf",
         "cluster_id": "ThemeA"},
        {"instrument_id": "FUND2", "name": "Fund Two", "instrument_type": "etf",
         "cluster_id": "ThemeB"},
    ])
    pos = _positions(tmp_path, [
        {"ticker": "FUND1", "instrument_id": "FUND1", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "FUND2", "instrument_id": "FUND2", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])

    rows = page.overlap_rows("everything", positions_path=pos,
                             holdings_path=hold, clusters_path=cl)
    assert rows
    for row in rows:
        assert isinstance(row["shared"], page.Figure)
        assert "(" in row["shared"].source


def test_the_mine_pass_down_does_not_change_the_answer(tmp_path, monkeypatch):
    """Fix round 1, Important I. `mine=` is a per-call pass-down to avoid
    recomputing `exposure.by_company()` within one `context()` call — it
    must never change what gets returned, only how many times it gets
    computed."""
    from src import exposure

    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [{"instrument_id": "AAA", "name": "A"}])

    without = page.company_bars("everything", positions_path=pos, holdings_path=hold)
    mine = exposure.by_company("everything", positions_path=pos, holdings_path=hold)
    with_mine = page.company_bars("everything", positions_path=pos,
                                  holdings_path=hold, mine=mine)
    assert without == with_mine


# ─── Fix round 1, Important F: two honesty lines that had no test ─────────

def test_weight_residue_reaches_the_honesty_block(tmp_path, monkeypatch):
    """`exposure.weight_residue()` had no caller anywhere in the codebase
    until this module — delete its three lines from `honesty()` and the
    suite stayed green, which is exactly the shape of defect this test
    exists to close off (the plan's own "sixth instance" of a behaviour
    being correct and unprotected)."""
    from src import exposure

    pos = _positions(tmp_path, [
        {"ticker": "ZZZ", "instrument_id": "ZZZ", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    # Two rows, each a valid fraction on its own (the per-row check is
    # `in_range(0, 1)`) — their SUM is the 1.003 residue, which only the
    # per-ticker schema check (0.98-1.02) and `weight_residue()` itself
    # (±1e-4) care about.
    pd.DataFrame(
        [{"ticker": "ZZZ", "sector": "Technology", "weight": 0.6,
          "top_holding": "", "source": "issuer page",
          "source_quality": "issuer-by-hand", "as_of": "2026-10-01"},
         {"ticker": "ZZZ", "sector": "Financials", "weight": 0.403,
          "top_holding": "", "source": "issuer page",
          "source_quality": "issuer-by-hand", "as_of": "2026-10-01"}],
        columns=exposure.COLUMNS,
    ).to_csv(comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [{"instrument_id": "ZZZ", "name": "Z Fund",
                                "instrument_type": "etf"}])

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    assert any("ZZZ" in line and "1.0030" in line for line in lines), (
        "weight_residue()'s own ZZZ sentence never reached the honesty "
        "block — a silently deleted call here would be invisible to every "
        "other test in this suite."
    )


def test_the_benchmarks_own_date_reaches_the_honesty_block(tmp_path, monkeypatch):
    """Fix, 2026-10-07 final review (I4) — a ruling of mine corrected. I had
    parked `ops/data_gaps.py`'s presence-only benchmark-staleness check on
    the stated ground that this module already showed the benchmark's own
    `as_of` somewhere on the page. It did not, anywhere: the S&P comparison
    — the panel he called the most important one — could age indefinitely
    with no date on screen and no nag. Sourced from `exposure.
    benchmark_weights()`, the frame that already carries it.

    Holdings table is empty here (only the sector side is fetched), so this
    pins the sector-only half of the N1 fix below — the merged/both-dated
    sentence is pinned separately."""
    from src import exposure

    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(
        [{"ticker": "SPY", "sector": "Technology", "weight": 1.0,
          "top_holding": "", "source": "SPDR holdings file",
          "source_quality": "vendor", "as_of": "2026-09-15"}],
        columns=exposure.COLUMNS,
    ).to_csv(comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [{"instrument_id": "NVDA", "name": "NVIDIA",
                                "instrument_type": "stock"}])

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    assert any("2026-09-15" in line for line in lines), (
        "the benchmark's own as_of date never reached the honesty block — "
        "the S&P comparison can age indefinitely with no date on screen "
        "and no nag otherwise"
    )


def test_a_never_fetched_benchmark_adds_no_stale_date_claim(monkeypatch):
    """The other half of I4: when the benchmark has never been fetched at
    all (NEITHER the sector nor the company side — fix, residual review N1,
    both must be mocked away or the real on-disk holdings file would still
    carry a date), there is no `as_of` to report — the existing
    'run /market' sentences (sourced from `benchmark_sector_comparison().
    attrs['why']` and `benchmark_companies().attrs['why']`) already cover
    that case, and this must not additionally claim a date exists."""
    from src import exposure
    empty_weights = pd.DataFrame(columns=["sector", "weight_pct", "as_of",
                                          "source_quality"])
    empty_weights.attrs["why"] = "not fetched yet — run /market"
    monkeypatch.setattr(exposure, "benchmark_weights",
                        lambda *a, **k: empty_weights)
    empty_companies = pd.DataFrame(columns=exposure.BENCHMARK_COMPANY_COLUMNS)
    empty_companies.attrs["why"] = "not fetched yet — run /market"
    empty_companies.attrs["as_of"] = ""
    empty_companies.attrs["compare_note"] = ""
    empty_companies.attrs["excluded_by_kind"] = {}
    monkeypatch.setattr(exposure, "benchmark_companies",
                        lambda *a, **k: empty_companies)
    lines = page.honesty("everything")
    assert not any("is dated" in line and "S&P" in line for line in lines), (
        "no date claim should appear anywhere when neither benchmark file "
        "has been fetched"
    )


def test_two_differently_dated_benchmark_files_are_never_merged(
    tmp_path, monkeypatch,
):
    """Fix, residual review (N1). The honesty block used to read ONE date
    (`benchmark_weights()`'s, i.e. `etf_compositions.csv`) and report it as
    if it also dated the company comparison (`etf_holdings.csv`, a
    different file, refreshed by a different path). On real data the two
    already differ by a day. This pins that they render as two separate,
    correctly-labelled sentences rather than one merged claim — and that
    the sentence never states a single shared date it did not verify."""
    from src import exposure

    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(
        [{"ticker": "SPY", "sector": "Technology", "weight": 1.0,
          "top_holding": "", "source": "SPDR holdings file",
          "source_quality": "vendor", "as_of": "2026-10-06"}],
        columns=exposure.COLUMNS,
    ).to_csv(comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.1, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-05", "as_of_kind": "published"},
    ])
    _instruments(monkeypatch, [{"instrument_id": "NVDA", "name": "NVIDIA",
                                "instrument_type": "stock"}])

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    sector_line = next((l for l in lines if "sector breakdown is dated"
                        in l), None)
    company_line = next((l for l in lines if "company breakdown is dated"
                         in l), None)
    assert sector_line is not None and "2026-10-06" in sector_line
    assert company_line is not None and "2026-10-05" in company_line
    # The two dates differ, so no sentence may claim they are "both dated".
    assert not any("are both dated" in l for l in lines), (
        "two differently-dated files were merged into one claimed date — "
        "exactly the defect N1 found"
    )


def test_two_identically_dated_benchmark_files_are_reported_as_one_sentence(
    tmp_path, monkeypatch,
):
    """The other half of N1: when the two files genuinely agree, the
    honesty block MAY say so in one sentence — but only because it compared
    the two strings and found them equal, never because it assumed one
    file's date covers the other."""
    from src import exposure

    pos = _positions(tmp_path, [
        {"ticker": "NVDA", "instrument_id": "NVDA", "market_value": 500.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(
        [{"ticker": "SPY", "sector": "Technology", "weight": 1.0,
          "top_holding": "", "source": "SPDR holdings file",
          "source_quality": "vendor", "as_of": "2026-10-06"}],
        columns=exposure.COLUMNS,
    ).to_csv(comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "NVDA", "holding_name": "NVIDIA CORP",
         "weight": 0.1, "coverage": 1.0, "source": "SPDR holdings file",
         "as_of": "2026-10-06", "as_of_kind": "published"},
    ])
    _instruments(monkeypatch, [{"instrument_id": "NVDA", "name": "NVIDIA",
                                "instrument_type": "stock"}])

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    assert any("are both dated 2026-10-06" in l for l in lines)


def test_sector_disagreement_reaches_the_honesty_block_with_all_three_parts(
    tmp_path, monkeypatch,
):
    """`exposure.sector_disagreements()`'s panel promise is that it names
    the vendor, the date and the dollar amount that moves — the honesty
    block must say all three, or a reader of the sector panel has no way
    to tell it apart from one where the two sides simply agree."""
    from src import exposure

    pos = _positions(tmp_path, [
        {"ticker": "DISAGREECO", "instrument_id": "DISAGREECO",
         "market_value": 3400.0, "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame([], columns=exposure.COLUMNS).to_csv(
        comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "DISAGREECO", "instrument_type": "stock",
         "sector": "Crypto - Miners / Treasury", "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance"},
    ])

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    match = next((line for line in lines if "DISAGREECO" in line), None)
    assert match, "the disagreement never reached the honesty block"
    assert "Yahoo via yfinance" in match      # the vendor's name
    assert "2026-10-04" in match              # the date
    assert "$3,400.00" in match               # the dollar amount that moves


def test_an_undated_vendor_sector_reaches_the_honesty_block_too(tmp_path,
                                                                monkeypatch):
    """Fix, pre-push review 2026-10-07 (finding 1, third part) — the exact
    case the review reproduced. `honesty()` used to stop at `disagree`/
    `kept-mine` and silently drop `no-vendor-sector` (an undated vendor
    label — the money that lands in 'Other' under the panel's own default
    classification), `no-own-sector` and `unmappable`. Before this fix, NO
    sentence anywhere on the page said this holding's sector was unknown —
    the "what could not be classified" half of the honesty contract,
    dropped."""
    pos = _positions(tmp_path, [
        {"ticker": "UNDATEDCO", "instrument_id": "UNDATEDCO",
         "market_value": 500.0, "currency": "USD", "priced_at": "2026-10-01"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "UNDATEDCO", "instrument_type": "stock",
         "sector": "Tech - Semiconductors",
         "provider_sector": "FINANCIAL SERVICES",   # legacy, pre-taxonomy
         "provider_sector_as_of": ""},                # the tell: undated
    ])

    lines = page.honesty("everything", positions_path=pos)
    match = next((line for line in lines if "UNDATEDCO" in line), None)
    assert match, (
        "the undated-vendor-sector case reached no sentence anywhere on "
        "the page"
    )
    assert "unknown" in match.lower() or "not a confirmed zero" in match.lower()


# ─── Fix round 2 ────────────────────────────────────────────────────────────

def _disagreement_fixture(tmp_path, monkeypatch):
    """One holding, settled his-way, with labels that roll up to different
    parents — the BMNR shape. Shared by the two tests below."""
    from src import exposure

    pos = _positions(tmp_path, [
        {"ticker": "DISAGREECO", "instrument_id": "DISAGREECO",
         "market_value": 3400.0, "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame([], columns=exposure.COLUMNS).to_csv(
        comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [
        {"instrument_id": "DISAGREECO", "instrument_type": "stock",
         "sector": "Crypto - Miners / Treasury", "provider_sector": "Financials",
         "provider_sector_as_of": "2026-10-04",
         "provider_sector_source": "Yahoo via yfinance",
         "sector_settled": "2026-10-04"},
    ])
    return pos, comp, hold


def test_the_disagreement_sentence_names_which_label_the_panel_actually_counts(
    tmp_path, monkeypatch,
):
    """Fix round 2, Critical A. Honesty item 12 (round 1) said the money
    was "counted under his own label" — backwards: the sector panel's
    default classification is the vendor's, so it counts a settled
    disagreement under the VENDOR's label, not his. The sentence must
    DERIVE which label the panel counts money under from the
    `classification` actually in force, never hard-code an answer —
    because that classification is a parameter with a default (D13), and a
    hard-coded sentence is wrong the day someone passes the other one.
    Tested in both directions, which is the test that would have caught
    the round-1 bug.
    """
    pos, comp, hold = _disagreement_fixture(tmp_path, monkeypatch)

    provider_lines = page.honesty("everything", positions_path=pos,
                                  compositions_path=comp, holdings_path=hold)
    mine_lines = page.honesty("everything", positions_path=pos,
                              compositions_path=comp, holdings_path=hold,
                              classification="mine")

    provider_line = next(l for l in provider_lines if "DISAGREECO" in l)
    mine_line = next(l for l in mine_lines if "DISAGREECO" in l)

    # Provider (the panel's own default): the VENDOR's label wins on screen.
    assert "under 'Financials'" in provider_line
    assert "the vendor's classification" in provider_line
    assert "under 'Crypto - Miners / Treasury'" not in provider_line

    # Mine: flips completely — his own label wins on screen instead.
    assert "under 'Crypto - Miners / Treasury'" in mine_line
    assert "his own classification" in mine_line
    assert "under 'Financials'" not in mine_line

    # Both versions still say plainly that he kept his own label and that
    # the two disagree — those two facts never change with classification.
    for line in (provider_line, mine_line):
        assert "keeping his own label" in line
        assert "disagree on screen" in line


def test_a_zero_excluded_kind_is_not_listed(tmp_path, monkeypatch):
    """Fix round 2, Minor D. A zero is not information — three real
    numbers read better than three real numbers and a nought. Under a
    fixture with no cash lots and no no-companies fund, only the
    unaccounted component is real, and it must be the only one named."""
    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 500.0,
         "currency": "USD", "priced_at": "2026-10-01"},
        {"ticker": "FUND1", "instrument_id": "FUND1", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    # FUND1 has no row in the holdings table at all, and is not in
    # NO_EQUITY_INSIDE, so its whole value becomes an `unaccounted` bucket.
    # AAA matches the index's own row so `bc` is non-empty.
    hold = _holdings_table(tmp_path, [
        {"ticker": "SPY", "holding": "AAA", "holding_name": "A",
         "weight": 0.01, "coverage": 1.0, "source": "test",
         "as_of": "2026-10-01", "as_of_kind": "published"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "AAA", "name": "A"},
        {"instrument_id": "FUND1", "name": "Fund One", "instrument_type": "etf"},
    ])

    lines = page.honesty("everything", positions_path=pos, holdings_path=hold)
    excl_line = next((l for l in lines if "S&P company comparison" in l), None)
    assert excl_line is not None
    assert "cash/FX" not in excl_line
    assert "no companies at all" not in excl_line
    assert "unaccounted" in excl_line
    assert "$0.00" not in excl_line


def test_an_empty_theme_is_muted_not_dropped(tmp_path, monkeypatch):
    """D5/trap 4: a theme holding nothing under THIS basis still gets a bar
    — `clusters.tree()` already tells an empty theme apart from a basis that
    merely emptied it, and `theme_bars()` must not re-derive that itself."""
    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 100.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "Held", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
        {"cluster_id": "Empty", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
    ])
    _instruments(monkeypatch, [{"instrument_id": "AAA", "name": "A",
                                "cluster_id": "Held"}])

    bars = page.theme_bars("everything", positions_path=pos, clusters_path=cl)
    by_label = {b.label: b for b in bars}
    assert by_label["Held"].muted is False
    assert by_label["Empty"].muted is True
    assert by_label["Empty"].figure.value == 0.0


def test_a_blank_note_ref_is_no_link(tmp_path, monkeypatch):
    """Links: an empty note_ref means no link — a bar he cannot click
    because nothing is written is itself information, never a broken link."""
    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 100.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "Held", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
    ])
    _instruments(monkeypatch, [{"instrument_id": "AAA", "name": "A",
                                "cluster_id": "Held", "note_ref": ""}])

    bars = page.theme_bars("everything", positions_path=pos, clusters_path=cl)
    holding_bar = next(b for b in bars if b.label == "A")
    assert holding_bar.href == ""

    hold = _holdings_table(tmp_path, [])
    cbars = page.company_bars("everything", positions_path=pos, holdings_path=hold)
    assert cbars[0].href == ""


def test_a_real_note_ref_becomes_a_relative_link(tmp_path, monkeypatch):
    """Links: relative to the built file's directory — `ops/`, one level
    below the project root that a note_ref is itself stored relative to."""
    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 100.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "Held", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
    ])
    _instruments(monkeypatch, [{"instrument_id": "AAA", "name": "A",
                                "cluster_id": "Held",
                                "note_ref": "notes/positions/AAA.md"}])

    bars = page.theme_bars("everything", positions_path=pos, clusters_path=cl)
    holding_bar = next(b for b in bars if b.label == "A")
    assert holding_bar.href == "../notes/positions/AAA.md"


# ─── 2026-10-07 addition: the By-theme panel's own total ───────────────────

def test_theme_total_equals_book_nav_never_a_naive_sum_of_tree_rows(tmp_path, monkeypatch):
    """CLAUDE.md: "the renderer may not sum anything" — and this module may
    not either. `clusters.tree()`'s own docstring warns that summing every
    row over-counts, because a group row carries its children's money too
    — this fixture makes that concrete: group `G` holds child `C1`, which
    holds `AAA` ($100), so `theme_bars()`'s own rows sum to $300 (G + C1 +
    AAA) while the real NAV is $100. `theme_total()` must report the real
    $100 (`book.nav()`), proving it is not derived by adding up the rows
    this very panel shows."""
    from src import book

    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 100.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "G", "parent": "", "layer": "group",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
        {"cluster_id": "C1", "parent": "G", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "AAA", "name": "A", "cluster_id": "C1"},
    ])

    total = page.theme_total("everything", positions_path=pos, clusters_path=cl)
    nav = book.nav("everything", positions_path=pos, clusters_path=cl)
    bars = page.theme_bars("everything", positions_path=pos, clusters_path=cl)
    naive_sum = sum(b.figure.value for b in bars)

    assert nav == 100.0
    assert naive_sum == 300.0, "fixture sanity check — G + C1 + AAA"
    assert isinstance(total, page.Figure)
    assert total.value == nav
    assert total.value != naive_sum
    assert total.source == "book.nav()"


def test_theme_coverage_pct_reads_clusters_own_derived_number(tmp_path, monkeypatch):
    """Fix, pre-push review 2026-10-07 (finding 3). `theme_coverage_pct()`
    must be `clusters.tree_coverage_pct()`'s own figure — never a constant,
    which is exactly what `ops/build_page.py` hard-coded before this fix,
    the one row that could have revealed a cluster-tree defect losing real
    money and could not."""
    from src import clusters

    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 100.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    cl = _clusters(tmp_path, [
        {"cluster_id": "C1", "parent": "", "layer": "thematic",
         "in_equity_base": True, "status": "active", "added": "2026-01-01"},
    ])
    _instruments(monkeypatch, [
        {"instrument_id": "AAA", "name": "A", "cluster_id": "C1"},
    ])

    got = page.theme_coverage_pct("everything", positions_path=pos,
                                  clusters_path=cl)
    direct = clusters.tree_coverage_pct("everything", path=cl,
                                        positions_path=pos)
    assert isinstance(got, page.Figure)
    assert got.value == pytest.approx(direct)
    assert got.source == "clusters.tree_coverage_pct()"


def test_context_carries_theme_coverage_pct_per_basis(real_context):
    """`context()` wires `theme_coverage_pct()` into every basis alongside
    `theme_total` — shares the module's own `real_context` fixture rather
    than paying `page.context()`'s ~20 second cost a second time in this
    file."""
    for basis in page.BASES_ORDER:
        cov = real_context["bases"][basis]["theme_coverage_pct"]
        assert isinstance(cov, page.Figure)
        assert 0.0 <= cov.value <= 100.0 + 1e-6


# ─── 2026-10-06 brief: the note pop-up ──────────────────────────────────────

def test_embedded_notes_reads_exactly_the_hrefs_bars_produce(tmp_path, monkeypatch):
    """`page.embedded_notes()` must derive its set from the hrefs the
    shaped context actually produced — never a directory scan. Proved by
    mutation: a real note file sits on disk at a path NO bar links to
    (`UNLINKED.md`) and must never appear in the result; a path a bar DOES
    link to, with a real file behind it, must — and a path a bar links to
    with NO file behind it must come back `ok=False`, never raise."""
    notes_dir = tmp_path / "notes" / "positions"
    notes_dir.mkdir(parents=True)
    (notes_dir / "AAA.md").write_text("# AAA\n\nLinked by a bar.\n")
    (notes_dir / "UNLINKED.md").write_text("# UNLINKED\n\nNo bar points here.\n")

    monkeypatch.setattr(notes_mod, "PROJECT_ROOT", tmp_path)

    linked_bar = page.Bar(
        label="A", figure=page.Figure(1.0, "$1.00", "x"), pct=1.0,
        width=1.0, depth=0, negative=False, muted=False,
        href="../notes/positions/AAA.md", note="")
    missing_bar = page.Bar(
        label="B", figure=page.Figure(1.0, "$1.00", "x"), pct=1.0,
        width=1.0, depth=0, negative=False, muted=False,
        href="../notes/positions/GONE.md", note="")
    bases = {
        "everything": {
            "header": {"biggest": []},
            "theme_bars": [linked_bar],
            "company_bars": [missing_bar],
        },
    }

    result = page.embedded_notes(bases)

    assert set(result.keys()) == {
        "notes/positions/AAA.md", "notes/positions/GONE.md",
    }, "the directory's own UNLINKED.md must never appear — no bar links to it"
    assert result["notes/positions/AAA.md"].ok
    assert result["notes/positions/AAA.md"].text == "# AAA\n\nLinked by a bar.\n"
    assert not result["notes/positions/GONE.md"].ok


# ─── profit and loss (CLAUDE.md, 2026-10-08) ─────────────────────────────

def test_pnl_panel_takes_no_basis_at_all(real_context):
    """D7/`pnl_panel()`'s own docstring: a realised gain has no basis to be
    a share of, and `book.lot_cost_usd()`'s own sizing decision does not
    change with the basis toggle — so, alone among this module's panel
    functions, `pnl_panel()` has no `basis` parameter to forget to pass.
    Proved structurally (the signature itself), not just by habit."""
    import inspect
    assert "basis" not in inspect.signature(page.pnl_panel).parameters


def test_context_hands_every_basis_the_same_pnl_panel(real_context):
    """`context()`'s own docstring: computed once, shared by all three —
    never recomputed per basis for a figure that cannot differ."""
    bases = real_context["bases"]
    first = bases[page.BASES_ORDER[0]]["pnl"]
    for basis in page.BASES_ORDER[1:]:
        assert bases[basis]["pnl"] is first


def test_pnl_panel_separates_priced_lots_from_uncosted_ones(monkeypatch):
    """Every `Figure` in `rows`/`realized`/`unrealized`/`total` names the
    `book.py` call that produced it (D12); an uncosted lot gets no row and
    no figure at all, named instead in `uncosted` — CLAUDE.md's "never
    invent a value", proved at the shaping layer rather than only in
    `book.py` itself."""
    from src import book

    priced = pd.DataFrame([
        {"ticker": "BIGCO", "instrument_id": "BIGCO", "currency": "USD",
         "shares": 10.0, "market_value": 1200.0, "cost_usd": 1000.0,
         "unrealized": 200.0, "method": "ibkr_total", "flagged": False,
         "reason": "", "established": True},
        {"ticker": "SMALLCO", "instrument_id": "SMALLCO", "currency": "USD",
         "shares": 1.0, "market_value": 50.0, "cost_usd": 60.0,
         "unrealized": -10.0, "method": "ibkr_total", "flagged": False,
         "reason": "", "established": True},
        {"ticker": "NOCOST", "instrument_id": "NOCOST", "currency": "USD",
         "shares": 5.0, "market_value": 500.0, "cost_usd": float("nan"),
         "unrealized": float("nan"), "method": "", "flagged": False,
         "reason": "NOCOST: IBKR reports no cost basis for this lot",
         "established": False},
    ], columns=book.PNL_LOT_COLUMNS)

    monkeypatch.setattr(book, "realized_pnl", lambda *a, **k: 500.0)
    monkeypatch.setattr(book, "unrealized_pnl", lambda *a, **k: 190.0)
    monkeypatch.setattr(book, "total_pnl", lambda *a, **k: 690.0)
    monkeypatch.setattr(book, "lot_cost_usd", lambda *a, **k: priced)
    monkeypatch.setattr(
        book, "uncosted_lots",
        lambda *a, **k: priced.loc[~priced["established"],
                                  ["ticker", "instrument_id", "reason"]],
    )

    result = page.pnl_panel()

    assert result["realized"] == page.Figure(500.0, page.money(500.0),
                                             "book.realized_pnl()")
    assert result["unrealized"] == page.Figure(190.0, page.money(190.0),
                                               "book.unrealized_pnl()")
    assert result["total"] == page.Figure(690.0, page.money(690.0),
                                          "book.total_pnl()")

    tickers = {r["ticker"] for r in result["rows"]}
    assert tickers == {"BIGCO", "SMALLCO"}, "the uncosted lot must not get a row"
    bigco = next(r for r in result["rows"] if r["ticker"] == "BIGCO")
    assert bigco["market_value"].value == 1200.0
    assert bigco["cost"].value == 1000.0
    assert bigco["unrealized"].value == 200.0
    assert bigco["flagged"] is False

    assert [u["ticker"] for u in result["uncosted"]] == ["NOCOST"]
    assert "no cost basis" in result["uncosted"][0]["reason"]


def test_pnl_panel_rows_are_ranked_by_size_biggest_first(monkeypatch):
    """Same size-ranks-everything convention every other ranked panel on
    this page already uses (`book.holdings()`'s own `size` column) — a
    reader scanning top to bottom meets the biggest position first here
    too, never the order `lot_cost_usd()` happened to emit rows in."""
    from src import book

    df = pd.DataFrame([
        {"ticker": "SMALL", "instrument_id": "SMALL", "currency": "USD",
         "shares": 1.0, "market_value": 100.0, "cost_usd": 90.0,
         "unrealized": 10.0, "method": "ibkr_total", "flagged": False,
         "reason": "", "established": True},
        {"ticker": "BIG", "instrument_id": "BIG", "currency": "USD",
         "shares": 1.0, "market_value": 900.0, "cost_usd": 800.0,
         "unrealized": 100.0, "method": "ibkr_total", "flagged": False,
         "reason": "", "established": True},
    ], columns=book.PNL_LOT_COLUMNS)
    monkeypatch.setattr(book, "realized_pnl", lambda *a, **k: 0.0)
    monkeypatch.setattr(book, "unrealized_pnl", lambda *a, **k: 110.0)
    monkeypatch.setattr(book, "total_pnl", lambda *a, **k: 110.0)
    monkeypatch.setattr(book, "lot_cost_usd", lambda *a, **k: df)
    monkeypatch.setattr(book, "uncosted_lots",
                        lambda *a, **k: df.iloc[0:0][["ticker", "instrument_id", "reason"]])

    result = page.pnl_panel()
    assert [r["ticker"] for r in result["rows"]] == ["BIG", "SMALL"]


def test_pnl_panel_renders_absent_not_zero_when_nothing_is_costed(monkeypatch):
    """🚨 I3 (review, 2026-10-08): `book.unrealized_pnl()`/`total_pnl()`
    return `None` when every lot is uncosted — this must reach the page as
    an ABSENT figure (`""`, `_fig()`'s own dash), never `page.Figure(0.0,
    ...)`. `money(None)` has no float to call, which is exactly why the
    old code's plain `Figure(value=unrealized_value, ...)` could only have
    worked by accident (every real lot happened to cost), and why the bug
    it hid — a plausible $0.00 — was never a crash to notice instead."""
    from src import book

    empty_df = pd.DataFrame(columns=book.PNL_LOT_COLUMNS)
    monkeypatch.setattr(book, "realized_pnl", lambda *a, **k: 500.0)
    monkeypatch.setattr(book, "unrealized_pnl", lambda *a, **k: None)
    monkeypatch.setattr(book, "total_pnl", lambda *a, **k: None)
    monkeypatch.setattr(book, "lot_cost_usd", lambda *a, **k: empty_df)
    monkeypatch.setattr(book, "uncosted_lots", lambda *a, **k: empty_df)

    result = page.pnl_panel()
    assert result["realized"] == page.Figure(500.0, page.money(500.0),
                                             "book.realized_pnl()")
    assert result["unrealized"] == ""
    assert result["total"] == ""
    assert result["rows"] == []


def test_the_fx_fallback_sentence_names_the_lot_and_what_it_means(
    tmp_path, monkeypatch,
):
    """CLAUDE.md: the honesty block gains a sentence whenever any lot
    falls back to the report-date rate, naming which lot and what the
    figure then means. `book.fx_fallback_lots()`'s own `reason` already
    says both — proved here by a monkeypatch rather than a fixture that
    happens to trigger the real fallback, since the real book has none."""
    from src import book, exposure

    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(columns=exposure.COLUMNS).to_csv(
        comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [{"instrument_id": "AAA", "name": "A Co"}])

    sentinel = (
        "FXBAD: its trades on file do not reconcile to the 2 shares held, "
        "so each purchase's own exchange rate cannot be reconstructed — "
        "using IBKR's cost basis at today's exchange rate instead, which "
        "measures the share price's own gain, not dollars in."
    )
    monkeypatch.setattr(
        book, "fx_fallback_lots",
        lambda *a, **k: pd.DataFrame(
            [{"ticker": "FXBAD", "instrument_id": "FXBAD", "currency": "EUR",
              "reason": sentinel}]
        ),
    )

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    assert sentinel in lines


def test_no_fx_fallback_sentence_when_nothing_is_flagged(tmp_path, monkeypatch):
    """"If no lot does, it says nothing" — the empty case, which is every
    real lot on his book today (`book.fx_fallback_lots()` itself proves
    that in `tests/test_book.py`; this proves `honesty()` adds nothing
    when told so, rather than a placeholder sentence)."""
    from src import book, exposure

    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(columns=exposure.COLUMNS).to_csv(
        comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [{"instrument_id": "AAA", "name": "A Co"}])

    monkeypatch.setattr(book, "fx_fallback_lots",
                        lambda *a, **k: pd.DataFrame(
                            columns=["ticker", "instrument_id", "currency", "reason"]))

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    assert not any("exchange rate" in line for line in lines)


def test_the_rule_a_sentence_fires_for_the_ordinary_reconstructed_case(
    tmp_path, monkeypatch,
):
    """🚨 I2 (review, 2026-10-08): the sentence that was missing for the
    NORMAL case — a foreign lot rule A actually priced, not the flagged
    fallback. He would otherwise compare his page's unrealised figure
    against his broker's own and find nothing here explaining the gap,
    the precise FX worry he raised when he asked for this feature."""
    from src import book, exposure

    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(columns=exposure.COLUMNS).to_csv(
        comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [{"instrument_id": "AAA", "name": "A Co"}])

    reconstructed = pd.DataFrame([
        {"ticker": "FXGOOD", "instrument_id": "FXGOOD", "currency": "EUR",
         "shares": 1.0, "market_value": 700.0, "cost_usd": 650.0,
         "unrealized": 50.0, "method": "trade_reconstruction",
         "flagged": False, "reason": "", "established": True},
    ], columns=book.PNL_LOT_COLUMNS)
    monkeypatch.setattr(book, "lot_cost_usd", lambda *a, **k: reconstructed)
    monkeypatch.setattr(book, "fx_choice_effect", lambda *a, **k: 12.34)
    monkeypatch.setattr(book, "fx_fallback_lots",
                        lambda *a, **k: pd.DataFrame(
                            columns=["ticker", "instrument_id", "currency", "reason"]))
    monkeypatch.setattr(book, "excluded_cash_flows", lambda *a, **k: 0.0)

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    sentinel = (
        "FXGOOD: priced at the exchange rate on each day he actually "
        "bought, as he chose, so currency movement since then counts as "
        "part of the gain — the unrealised figure here reads $12.34 lower "
        "than IBKR's own number for these same holdings. That gap is the "
        "choice working as intended, not a mistake."
    )
    assert sentinel in lines


def test_the_excluded_cash_flows_sentence_names_the_derived_amount(
    tmp_path, monkeypatch,
):
    """🚨 I2 ("while you are there"): dividends, withholding, interest and
    fees are real money this total leaves out entirely — said once,
    derived, never typed."""
    from src import book, exposure

    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(columns=exposure.COLUMNS).to_csv(
        comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [{"instrument_id": "AAA", "name": "A Co"}])

    monkeypatch.setattr(book, "fx_fallback_lots",
                        lambda *a, **k: pd.DataFrame(
                            columns=["ticker", "instrument_id", "currency", "reason"]))
    monkeypatch.setattr(book, "excluded_cash_flows", lambda *a, **k: 1949.08)

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    sentinel = (
        "Dividends, withholding tax, interest and borrow fees are not part "
        "of either figure above — a net income of $1,949.08 sits in his "
        "cash ledger instead."
    )
    assert sentinel in lines


def test_the_excluded_cash_flows_sentence_is_silent_on_a_structural_zero(
    tmp_path, monkeypatch,
):
    """No cash activity on file at all is a real, confirmed zero — not
    information worth a line, the same "omit a zero" rule the S&P-
    exclusion sentence already follows."""
    from src import book, exposure

    pos = _positions(tmp_path, [
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": 1000.0,
         "currency": "USD", "priced_at": "2026-10-01"},
    ])
    comp = tmp_path / "etf_compositions.csv"
    pd.DataFrame(columns=exposure.COLUMNS).to_csv(
        comp, index=False, quoting=csv.QUOTE_NONNUMERIC)
    hold = _holdings_table(tmp_path, [])
    _instruments(monkeypatch, [{"instrument_id": "AAA", "name": "A Co"}])

    monkeypatch.setattr(book, "fx_fallback_lots",
                        lambda *a, **k: pd.DataFrame(
                            columns=["ticker", "instrument_id", "currency", "reason"]))
    monkeypatch.setattr(book, "excluded_cash_flows", lambda *a, **k: 0.0)

    lines = page.honesty("everything", positions_path=pos,
                         compositions_path=comp, holdings_path=hold)
    assert not any("cash ledger" in line for line in lines)
