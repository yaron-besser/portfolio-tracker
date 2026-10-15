"""Tests for the read layer — one holding, and the ones nothing explains.

Run: venv/bin/python -m pytest tests/test_holding.py -q

Every test builds its own positions/transactions/claims/instruments files and
points the modules at them. Nothing here reads the real portfolio, so writing a
claim for DRAM tomorrow cannot turn these red — the trap the earlier
`test_positions_fundamentals` had, where a test quietly depended on live data.
"""

from __future__ import annotations

import csv

import pandas as pd
import pytest

from src import claims, holding, instruments


def _csv(path, rows, columns):
    pd.DataFrame(rows, columns=columns).to_csv(
        path, index=False, quoting=csv.QUOTE_NONNUMERIC
    )
    return path


def _world(tmp_path, *, instruments_rows, positions_rows,
           trades_rows, claims_rows=()):
    """Point every module at a self-contained set of files."""
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)

    _csv(d / "instruments.csv", instruments_rows, instruments.COLUMNS)
    _csv(d / "positions.csv", positions_rows,
         ["ticker", "instrument_id", "shares", "cost_basis", "last_price",
          "market_value", "priced_at", "notes"])
    _csv(d / "transactions.csv", trades_rows,
         ["trade_id", "trade_date", "symbol", "buy_sell", "quantity", "price",
          "currency"])
    _csv(d / "claims.csv", claims_rows, claims.COLUMNS)

    # levels/setups are unused by these tests but summary() reads them
    _csv(d / "levels.csv", [], __import__("src.levels", fromlist=["x"]).COLUMNS)
    _csv(d / "claim_setups.csv", [], claims.SETUP_COLUMNS)
    return d


def _inst(iid, itype="etf", note_ref=""):
    return {"instrument_id": iid, "name": f"{iid} Fund", "instrument_type": itype,
            "sector": "", "cluster_id": "", "provider_sector": "", "isin": "",
            "av_ticker": "", "price_ticker": "", "note_ref": note_ref,
            "status": "active", "added": "2026-08-01"}


def _pos(iid, shares="10", mv="1000"):
    return {"ticker": iid, "instrument_id": iid, "shares": shares,
            "cost_basis": "90", "last_price": "100", "market_value": mv,
            "priced_at": "2026-08-13", "notes": ""}


def _trade(tid, symbol, when):
    return {"trade_id": tid, "trade_date": when, "symbol": symbol,
            "buy_sell": "BUY", "quantity": "10", "price": "100",
            "currency": "USD"}


def _claim(cid, iid):
    row = {c: "" for c in claims.COLUMNS}
    row.update(id=cid, date="2026-08-01", recorded="2026-08-01", ticker=iid,
               instrument_id=iid, origin="mine", kind="claim",
               claim="a bet", wrong_if="it breaks", check_date="2026-12-01",
               price="100", conviction="high", intent="hold")
    return row


# ─── unexplained() ───

def test_a_recent_trade_with_no_claim_is_flagged(tmp_path):
    d = _world(tmp_path,
           instruments_rows=[_inst("DRAM")], positions_rows=[_pos("DRAM")],
           trades_rows=[_trade("T1", "DRAM", "2026-08-13")])
    out = holding.unexplained(data_dir=d, as_of="2026-08-15")
    assert list(out["instrument_id"]) == ["DRAM"]
    assert out.loc[0, "why"] == "traded, no claim"


def test_a_recent_trade_WITH_a_claim_is_not_flagged(tmp_path):
    d = _world(tmp_path,
           instruments_rows=[_inst("IWM")], positions_rows=[_pos("IWM")],
           trades_rows=[_trade("T1", "IWM", "2026-08-13")],
           claims_rows=[_claim("c0001", "IWM")])
    assert holding.unexplained(data_dir=d, as_of="2026-08-15").empty


def test_an_older_trade_is_not_flagged(tmp_path):
    """45 of 47 holdings have no claim; flagging all of them would be noise
    nobody reads. Seven days is Yaron's window (2026-08-15)."""
    d = _world(tmp_path,
           instruments_rows=[_inst("OLD")], positions_rows=[_pos("OLD")],
           trades_rows=[_trade("T1", "OLD", "2026-06-01")])
    assert holding.unexplained(data_dir=d, as_of="2026-08-15").empty
    assert len(holding.unexplained(data_dir=d, days=365, as_of="2026-08-15")) == 1


def test_a_closed_position_traded_recently_still_appears(tmp_path):
    """Buying AND selling with no record of why is more worth surfacing than a
    position still sitting there, not less. HST is the real case."""
    d = _world(tmp_path,
           instruments_rows=[_inst("HST", itype="stock")],
           positions_rows=[],                       # sold: no row at all
           trades_rows=[_trade("T1", "HST", "2026-08-10")])
    out = holding.unexplained(data_dir=d, as_of="2026-08-15")
    assert list(out["instrument_id"]) == ["HST"]
    assert out.loc[0, "shares"] == 0.0


def test_two_broker_lots_of_one_instrument_give_one_row(tmp_path):
    """ASML_USD and ASML_EUR are one instrument. Two rows would say a lot is
    unexplained while its sibling carries the claim."""
    d = _world(tmp_path,
           instruments_rows=[_inst("ASML", itype="stock")],
           positions_rows=[{**_pos("ASML"), "ticker": "ASML_USD"},
                           {**_pos("ASML"), "ticker": "ASML_EUR"}],
           trades_rows=[_trade("T1", "ASML_USD", "2026-08-11"),
                        _trade("T2", "ASML_EUR", "2026-08-12")])
    out = holding.unexplained(data_dir=d, as_of="2026-08-15")
    assert len(out) == 1 and out.loc[0, "instrument_id"] == "ASML"


def test_cash_and_fx_never_appear(tmp_path):
    """There is no thesis to write about the euro balance."""
    d = _world(tmp_path,
           instruments_rows=[_inst("USD_CASH", itype="cash"),
                             _inst("EUR.USD", itype="fx")],
           positions_rows=[_pos("USD_CASH")],
           trades_rows=[_trade("T1", "USD_CASH", "2026-08-13")])
    assert holding.unexplained(data_dir=d, as_of="2026-08-15", all_holdings=True).empty


def test_all_holdings_widens_to_the_backlog(tmp_path):
    """The full list stays queryable — it is just not what gets flagged."""
    d = _world(tmp_path,
           instruments_rows=[_inst("OLD")], positions_rows=[_pos("OLD")],
           trades_rows=[])
    assert holding.unexplained(data_dir=d, as_of="2026-08-15").empty
    out = holding.unexplained(data_dir=d, as_of="2026-08-15", all_holdings=True)
    assert list(out["instrument_id"]) == ["OLD"]
    assert out.loc[0, "why"] == "held, no claim"


def test_it_reports_whether_a_note_exists(tmp_path):
    d = _world(tmp_path,
           instruments_rows=[_inst("DRAM", note_ref="notes/positions/DRAM.md")],
           positions_rows=[_pos("DRAM")],
           trades_rows=[_trade("T1", "DRAM", "2026-08-13")])
    out = holding.unexplained(data_dir=d, as_of="2026-08-15")
    assert bool(out.loc[0, "has_note"]) is True     # the real file exists


def test_a_traded_instrument_missing_from_instruments_csv_is_flagged(tmp_path):
    """The path DRAM actually arrived by.

    ibkr_sync writes a brand-new lot with "AUTO-ADDED — add it to
    instruments.csv", so a freshly bought ticker is in transactions before it is
    in instruments. Dropping it here hid the newest trades — the ones most worth
    flagging (found by review 2026-08-15).
    """
    d = _world(tmp_path, instruments_rows=[], positions_rows=[],
               trades_rows=[_trade("T1", "NEWCO", "2026-08-13")])
    out = holding.unexplained(data_dir=d, as_of="2026-08-15")
    assert list(out["instrument_id"]) == ["NEWCO"]
    assert "not in instruments.csv" in out.loc[0, "why"]


def test_a_truncated_transactions_file_refuses_rather_than_reassures(tmp_path):
    """"I read nothing" must never look like "I found nothing".

    A sync interrupted between truncate and write leaves a zero-byte file, and
    the next session start would otherwise report a clean bill of health.
    """
    d = _world(tmp_path, instruments_rows=[], positions_rows=[], trades_rows=[])
    (d / "transactions.csv").write_text("")
    with pytest.raises(ValueError, match="missing or empty"):
        holding.unexplained(data_dir=d, as_of="2026-08-15")


# ─── summary() ───

def test_summary_refuses_an_unknown_instrument(tmp_path):
    """A typo should say so, not look like a holding nobody wrote about."""
    d = _world(tmp_path, instruments_rows=[_inst("IWM")],
           positions_rows=[], trades_rows=[])
    with pytest.raises(KeyError, match="Unknown cluster|Unknown instrument"):
        holding.summary("NOPE", data_dir=d)


def test_a_stub_note_is_not_a_written_one(tmp_path):
    """A TODO-only note passed check() and reported as "ok", so it left the
    writing queue and satisfied a claim's note_ref — the exact opposite of why
    the stub exists (found by review 2026-08-15)."""
    from src import notes

    note = tmp_path / "STUB.md"
    notes.stub("STUB", path=note)
    ref = "notes/positions/STUB.md"
    assert notes.is_unwritten(note.read_text(), ref)
    note.write_text(note.read_text().replace("TODO", "a real bear case", 1))
    assert not notes.is_unwritten(note.read_text(), ref)


def test_is_unwritten_needs_the_path_to_know_which_rules_apply(tmp_path):
    """`ref` used to default to a positions path, so a cluster note that was
    100% TODO came back False — the position headings simply were not there,
    nothing got checked, and the answer looked confident (review 2026-08-15)."""
    from src import notes

    note = tmp_path / "core-us.md"
    notes.stub("core-us", kind="notes/clusters", path=note)
    assert notes.is_unwritten(note.read_text(), "notes/clusters/core-us.md")
    with pytest.raises(TypeError):
        notes.is_unwritten(note.read_text())          # no silent wrong answer


def test_summary_names_the_gaps(tmp_path):
    """A gap he cannot see is a gap he will not fill (P2)."""
    d = _world(tmp_path, instruments_rows=[_inst("DRAM")],
           positions_rows=[_pos("DRAM")], trades_rows=[])
    gaps = holding.summary("DRAM", data_dir=d)["gaps"]
    assert any("no claim" in g for g in gaps)
    assert any("no note" in g for g in gaps)


def test_summary_is_quiet_when_the_record_is_complete():
    """IWM was written up properly on 2026-08-15 — it must report no gaps.

    Reads the real store deliberately: the one test proving the finished
    article stays finished.

    ⚠️ `as_of` is pinned. Without it this fails by itself on 2026-09-26, the day
    after c0003's check date, telling a future session it broke something it
    never touched (found by review 2026-08-15).
    """
    s = holding.summary("IWM", as_of="2026-08-15")
    assert s["gaps"] == [], f"IWM should be complete, got {s['gaps']}"
    assert s["claims"] and s["screenshots"] and s["note_state"] == "ok"


# ─── clusters inside groups ───

def _cluster_row(cid, parent="", layer="thematic"):
    return {"cluster_id": cid, "parent": parent, "layer": layer,
            "in_equity_base": True, "note_ref": "", "status": "active",
            "added": "2026-08-16"}


def _tech_world(tmp_path, monkeypatch):
    """Technology(group) over AI Infrastructure ($600) and Software ($300).

    `clusters.weights()` maps a position to its cluster through
    `instruments.load()` called with NO path, so the instruments.csv written
    into tmp_path is ignored and the rollup silently reads the real book. The
    first version of these tests passed only because the live table happens to
    say NVDA -> AI Infrastructure today; reclassifying MSFT would have turned
    them red for a reason unrelated to the code they cover
    (found by review, 2026-08-16). tests/test_clusters.py::_grouped patches it
    for exactly this reason.
    """
    from src import clusters as _clusters
    from src import instruments as _instruments

    rows = [
        dict(_inst("NVDA", "stock"), cluster_id="AI Infrastructure"),
        dict(_inst("MSFT", "stock"), cluster_id="Software"),
    ]
    d = _world(
        tmp_path,
        instruments_rows=rows,
        positions_rows=[_pos("NVDA", mv="600"), _pos("MSFT", mv="300")],
        trades_rows=[],
    )
    _csv(d / "clusters.csv", [
        _cluster_row("Technology", layer="group"),
        _cluster_row("AI Infrastructure", parent="Technology"),
        _cluster_row("Software", parent="Technology"),
    ], _clusters.COLUMNS)
    monkeypatch.setattr(_instruments, "load",
                        lambda *a, **k: pd.DataFrame(rows))
    return d


def test_cluster_summary_names_the_children_of_a_group(tmp_path, monkeypatch):
    c = holding.cluster_summary("Technology",
                                data_dir=_tech_world(tmp_path, monkeypatch))
    assert c["children"] == ["AI Infrastructure", "Software"]
    assert c["parent"] == ""
    assert c["weights"].iloc[0]["market_value"] == 900.0


def test_cluster_summary_names_the_group_a_cluster_sits_in(tmp_path, monkeypatch):
    c = holding.cluster_summary("Software",
                                data_dir=_tech_world(tmp_path, monkeypatch))
    assert c["parent"] == "Technology"
    assert c["children"] == []


def test_a_group_holding_no_instruments_is_not_a_gap(tmp_path, monkeypatch):
    """`named but nothing is filed under it` is right for a cluster and wrong
    for a group — a group holds clusters, not instruments."""
    c = holding.cluster_summary("Technology",
                                data_dir=_tech_world(tmp_path, monkeypatch))
    assert not any("nothing is filed" in g for g in c["gaps"])


# ─── a theme's own research, and the pointer to what sits above ───
#
# The first two read the real store on purpose, like
# test_summary_is_quiet_when_the_record_is_complete: they prove the keys are
# there for the two commands to print. The third builds its own world and proves
# what the keys MEAN.

def test_cluster_summary_carries_its_documents():
    got = holding.cluster_summary("Technology")
    assert "evidence" in got


def test_holding_summary_carries_inherited_counts():
    got = holding.summary("ASML")
    assert "inherited" in got
    assert isinstance(got["inherited"], dict)


def test_a_themes_document_is_counted_for_a_holding_never_filed_under_it(
        tmp_path):
    """A report on the cluster reaches the holding as a count, and only as a count.

    Merging it in is the failure mode: one broad report would then read as
    research on twelve companies. So `inherited` says 1 and `evidence` — what is
    filed on the instrument itself — stays empty.

    ⚠️ **Every id here is deliberately absent from the real tables.** With the
    default paths, `ancestors()` would look ZZTEST up in the real
    `instruments.csv` and raise, and `for_target()` would answer from the real
    `evidence.csv`. That is what pins the `paths` threading: mirroring live names
    (an earlier version used NVDA -> AI Infrastructure -> Technology) let the
    test pass while reading the real portfolio, which is the trap this module's
    docstring warns about.
    """
    from src import clusters as _clusters
    from src import evidence as _evidence

    rows = [dict(_inst("ZZTEST", "stock"), cluster_id="Test Theme",
                 sector="Test Sector")]
    d = _world(tmp_path, instruments_rows=rows,
               positions_rows=[_pos("ZZTEST")], trades_rows=[],
               claims_rows=[_claim("c0001", "ZZTEST")])
    _csv(d / "clusters.csv", [
        _cluster_row("Test Group", layer="group"),
        _cluster_row("Test Theme", parent="Test Group"),
    ], _clusters.COLUMNS)
    _csv(d / "evidence.csv", [
        {"id": "e0001", "kind": "research", "title": "a broad AI report",
         "source": "fundstrat", "published": "2026-08-01", "storage": "link",
         "location": "https://example.com/ai", "added": "2026-08-17"},
    ], _evidence.EVIDENCE_COLUMNS)
    _csv(d / "evidence_links.csv", [
        {"target_type": "cluster", "target_id": "Test Theme",
         "source_type": "document", "source_id": "e0001", "page": "4",
         "why": "the theme, not the stock", "added": "2026-08-17"},
    ], _evidence.LINK_COLUMNS)

    s = holding.summary("ZZTEST", data_dir=d)
    assert s["inherited"] == {("cluster", "Test Theme"): 1}
    assert len(s["evidence"]) == 0
    # And it fills no gap: c0001 still has nothing of its own filed against it.
    assert "no chart filed against any claim" in s["gaps"]

    c = holding.cluster_summary("Test Theme", data_dir=d)
    assert list(c["evidence"]["title"]) == ["a broad AI report"]
    assert list(c["evidence"]["page"]) == ["4"]


def test_a_markdown_summary_is_not_a_filed_chart(tmp_path):
    """A summary must never silence the "no chart" gap.

    `_source_index()` stamps every summary `storage="repo"` — truthfully, the
    write-up IS committed — and `screenshots` filtered on that alone. So a claim
    whose only attached source was a `notes/picks/*.md` write-up reported a
    screenshot it did not have, `_gaps()` then found nothing missing, and /show
    printed `(none — the record is complete)` for a claim with no chart at all.
    A silent all-clear is the one thing gap-reporting exists to prevent (P2).
    """
    from src import evidence as _evidence

    picks = tmp_path / "notes" / "picks" / "2026-08-17-13d-technology.md"
    picks.parent.mkdir(parents=True)
    picks.write_text("# the capex case\n")

    d = _world(tmp_path, instruments_rows=[_inst("ZZSHOT", "stock")],
               positions_rows=[_pos("ZZSHOT")], trades_rows=[],
               claims_rows=[_claim("c0001", "ZZSHOT")])
    _csv(d / "evidence.csv", [
        {"id": "e0001", "kind": "research", "title": "13D, August",
         "source": "13d", "published": "2026-08-01", "storage": "private",
         "location": "2026-08-01-13d-ai-capex.pdf", "added": "2026-08-17"},
    ], _evidence.EVIDENCE_COLUMNS)
    _csv(d / "evidence_summaries.csv", [
        {"id": "s0001", "evidence_id": "e0001",
         "location": "notes/picks/2026-08-17-13d-technology.md",
         "added": "2026-08-17"},
    ], _evidence.SUMMARY_COLUMNS)
    _csv(d / "evidence_links.csv", [
        {"target_type": "claim", "target_id": "c0001", "source_type": "summary",
         "source_id": "s0001", "page": "14", "why": "the capex case",
         "added": "2026-08-17"},
    ], _evidence.LINK_COLUMNS)

    s = holding.summary("ZZSHOT", data_dir=d, as_of="2026-08-15")
    assert s["screenshots"] == []
    assert "no chart filed against any claim" in s["gaps"]
    # The summary itself is still attached and still readable — it is counted as
    # what it is, not thrown away.
    assert [dc["source_id"] for dc in s["claims"][0]["evidence"]] == ["s0001"]


def test_a_committed_chart_on_a_claim_still_counts_as_one(tmp_path):
    """The other half of the rule above: narrowing the filter must not stop a
    real repo document from filling the gap."""
    from src import evidence as _evidence

    shot = tmp_path / "notes" / "screenshots" / "2026-08-15-ZZSHOT-price-1d.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"png")

    d = _world(tmp_path, instruments_rows=[_inst("ZZSHOT", "stock")],
               positions_rows=[_pos("ZZSHOT")], trades_rows=[],
               claims_rows=[_claim("c0001", "ZZSHOT")])
    _csv(d / "evidence.csv", [
        {"id": "e0001", "kind": "chart", "title": "ZZSHOT daily",
         "source": "mine", "published": "", "storage": "repo",
         "location": "notes/screenshots/2026-08-15-ZZSHOT-price-1d.png",
         "added": "2026-08-17"},
    ], _evidence.EVIDENCE_COLUMNS)
    _csv(d / "evidence_links.csv", [
        {"target_type": "claim", "target_id": "c0001", "source_type": "document",
         "source_id": "e0001", "page": "", "why": "the setup",
         "added": "2026-08-17"},
    ], _evidence.LINK_COLUMNS)

    s = holding.summary("ZZSHOT", data_dir=d, as_of="2026-08-15")
    assert s["screenshots"] == ["notes/screenshots/2026-08-15-ZZSHOT-price-1d.png"]
    assert "no chart filed against any claim" not in s["gaps"]


def test_a_groups_document_reaches_its_cluster_as_a_count_and_nothing_else(
        tmp_path, monkeypatch):
    """Spec, Settled §2, both halves at once.

    A document filed on the Technology group must show up on Software as a
    pointer — without it, research on the whole theme is invisible from
    `/show Software`, which is the problem this build exists to fix — and it
    must NOT show up in Software's own documents, or one report would read as
    research on every cluster underneath it. A future change cannot satisfy one
    half by breaking the other while this test stands.

    The names are absent from the real `clusters.csv` on purpose, so dropping
    `paths` from the `inherited()` call fails here instead of quietly resolving
    the real Software -> Technology parentage.
    """
    from src import clusters as _clusters
    from src import evidence as _evidence
    from src import instruments as _instruments

    rows = [dict(_inst("MSFT", "stock"), cluster_id="Test Software")]
    d = _world(tmp_path, instruments_rows=rows,
               positions_rows=[_pos("MSFT")], trades_rows=[])
    # weights() reaches instruments.load() with no path, so without this the
    # rollup reads the real book — see _tech_world().
    monkeypatch.setattr(_instruments, "load", lambda *a, **k: pd.DataFrame(rows))
    _csv(d / "clusters.csv", [
        _cluster_row("Test Group", layer="group"),
        _cluster_row("Test Software", parent="Test Group"),
    ], _clusters.COLUMNS)
    _csv(d / "evidence.csv", [
        {"id": "e0001", "kind": "research", "title": "why own technology",
         "source": "13d", "published": "2026-08-01", "storage": "link",
         "location": "https://example.com/tech", "added": "2026-08-17"},
    ], _evidence.EVIDENCE_COLUMNS)
    _csv(d / "evidence_links.csv", [
        {"target_type": "cluster", "target_id": "Test Group",
         "source_type": "document", "source_id": "e0001", "page": "",
         "why": "the wide question", "added": "2026-08-17"},
    ], _evidence.LINK_COLUMNS)

    kid = holding.cluster_summary("Test Software", data_dir=d)
    assert kid["inherited"] == {("cluster", "Test Group"): 1}
    assert len(kid["evidence"]) == 0

    # And the group itself holds it outright, not as a count of something above.
    parent = holding.cluster_summary("Test Group", data_dir=d)
    assert list(parent["evidence"]["title"]) == ["why own technology"]
    assert parent["inherited"] == {}


# ─── how the two commands render it ───
#
# /show and /note held byte-identical copies of this formatting for two commits.
# These tests are what makes CLAUDE.md's promise — that the two can never
# disagree about what the record says — a constraint rather than a habit.

def test_sources_line_names_the_document_its_page_and_marks_a_summary():
    line = holding.sources_line([
        {"source_type": "document", "source_id": "e0001",
         "title": "Fundstrat AI, Aug", "page": "12"},
        {"source_type": "summary", "source_id": "s0001",
         "title": "Fundstrat AI, Aug", "page": float("nan")},
    ])
    assert line == "Fundstrat AI, Aug p.12; Fundstrat AI, Aug (summary)"


def test_sources_line_accepts_a_frame_as_well_as_records():
    """`summary()["claims"]` carries records; the new keys carry frames."""
    frame = pd.DataFrame([{"source_type": "document", "source_id": "e0001",
                           "title": "a chart", "page": None}])
    assert holding.sources_line(frame) == "a chart"
    assert holding.sources_line([]) == "(none)"


def test_sources_line_says_so_when_what_a_link_points_at_is_gone():
    """A left join yields a NaN title; `NaN + str` is a traceback where P2
    wants a sentence."""
    line = holding.sources_line([
        {"source_type": "summary", "source_id": "s0009",
         "title": float("nan"), "page": "3"},
    ])
    assert line == "⚠ s0009 — what this points at is missing p.3 (summary)"


def test_sources_line_says_so_when_a_summarys_document_row_is_gone():
    """`_source_index()` yields "" — not NaN — for a summary whose parent
    document row was deleted, so `pd.notna` alone was true and the line printed
    a bare " p.3" with nothing in front of it."""
    line = holding.sources_line([
        {"source_type": "summary", "source_id": "s0009", "title": "", "page": "3"},
    ])
    assert line == "⚠ s0009 — what this points at is missing p.3 (summary)"


def test_inherited_line_reads_as_one_sentence_for_four_ancestors():
    """A holding has four ancestors now — its cluster and its sector, then the
    group above the cluster and the parent above the sector. The line has to
    stay readable, and has to say which kind each one is: a name that is both a
    cluster id and a sector id appears twice here, and only the kind tells the
    two apart."""
    line = holding.inherited_line({("cluster", "AI Infrastructure"): 2,
                                   ("sector", "Tech - Semiconductors"): 1,
                                   ("cluster", "Technology"): 3,
                                   ("sector", "Technology"): 1})
    assert line == ("2 more on the AI Infrastructure cluster, "
                    "1 more on the Tech - Semiconductors sector, "
                    "3 more on the Technology cluster, "
                    "1 more on the Technology sector — ask to see them")


def test_inherited_line_says_which_kind_each_ancestor_is():
    """A cluster and a sector can share a name — seven do — so "Healthcare"
    alone would be ambiguous. Nearest first, as ancestors() ordered them."""
    line = holding.inherited_line({("cluster", "Healthcare"): 2,
                                   ("sector", "Healthcare"): 1})
    assert line == ("2 more on the Healthcare cluster, "
                   "1 more on the Healthcare sector — ask to see them")


def test_inherited_line_is_empty_when_nothing_sits_above():
    """Empty in, empty out: the caller prints nothing rather than a line that
    says nothing."""
    assert holding.inherited_line({}) == ""


# ─── what_is must accept the spellings his broker actually uses ───

def test_a_broker_lot_spelling_routes_to_its_instrument():
    """`/show NOV` reported a holding he owns as not existing.

    positions.csv carries four lots whose ticker is not the instrument id —
    ASML_USD, ASML_EUR, NOV and 'BRK B'. what_is() matched instrument_id
    exactly and never called resolve(), so all four came back "unknown": the
    read half then shows no claims, no note and no gaps, which invites writing
    a second record for something already documented. Found by audit
    2026-09-27; CLAUDE.md teaches those exact broker spellings.
    """
    for lot in ("ASML_USD", "ASML_EUR", "NOV", "BRK B"):
        assert holding.what_is(lot) == "instrument", lot


def test_a_cluster_is_matched_before_resolve_strips_its_spaces():
    """resolve() is a pure string rule: 'AI Infrastructure' -> 'AIInfrastructure'.

    Clusters must therefore be matched first, or a two-word theme would be
    mangled into a lookup that finds nothing.
    """
    assert holding.what_is("AI Infrastructure") == "cluster"
    assert holding.what_is("Metals") == "cluster"


def test_something_that_is_neither_is_still_unknown():
    assert holding.what_is("ZZZ-NOT-A-THING") == "unknown"
    assert holding.what_is("") == "unknown"


def test_summary_accepts_the_same_broker_spellings_what_is_accepts():
    """what_is() and summary() must agree, or routing succeeds and reading dies.

    Fixing what_is() alone would turn a wrong-but-polite "not a known ticker"
    into a KeyError halfway through /show — louder and worse. Both now go
    through instruments.resolve().
    """
    for lot, iid in (("NOV", "NVO"), ("ASML_EUR", "ASML"), ("BRK B", "BRKB")):
        s = holding.summary(lot)
        assert s["instrument"]["instrument_id"] == iid, lot


def test_summary_of_a_multi_lot_instrument_shows_every_lot():
    """ASML is one instrument and two broker lots; asking by either spelling
    must return both, never the one whose name was typed."""
    for spelling in ("ASML", "ASML_USD", "ASML_EUR"):
        assert len(holding.summary(spelling)["lots"]) == 2, spelling


# ─── Routing: a thought can be about a sector, or about the market ───

def test_what_is_knows_a_sector():
    """Added 2026-09-27 so /note can file a thought against a sector."""
    assert holding.what_is("Tech - Semiconductors") == "sector"


def test_what_is_knows_the_market():
    """The one scope that names nothing. A pseudo-cluster for it would land in
    clusters.weights() and corrupt every NAV percentage."""
    assert holding.what_is("market") == "market"
    assert holding.what_is("Market") == "market"


def test_a_name_that_is_both_a_cluster_and_a_sector_still_reads_as_a_cluster():
    """Seven live names are both. The cluster is what he means when he types
    one — the sector is a fact about exposure, the cluster is his reason."""
    from src import commentary
    assert holding.what_is("Technology") == "cluster"
    assert commentary.is_ambiguous("Technology")


# ─── What he has SAID, as opposed to what he read ───

def _said(d, rows):
    from src import commentary
    return _csv(d / "commentary.csv", rows, commentary.COLUMNS)


def _comment(cmid, target_type, target_id, body="a thought"):
    return {"id": cmid, "date": "2026-09-27", "recorded": "2026-09-27",
            "kind": "thought", "target_type": target_type,
            "target_id": target_id, "origin": "mine", "said_by": "",
            "body": body, "promoted_to": "", "note_ref": ""}


def test_summary_shows_what_he_said_about_the_holding(tmp_path):
    d = _world(tmp_path, instruments_rows=[_inst("IWM")],
               positions_rows=[_pos("IWM")], trades_rows=[])
    _said(d, [_comment("cm0001", "instrument", "IWM", "Small caps look heavy."),
              _comment("cm0002", "instrument", "QQQ", "Not about IWM.")])
    said = holding.summary("IWM", data_dir=d)["said"]
    assert len(said) == 1 and said.iloc[0]["body"] == "Small caps look heavy."


def test_summary_said_is_empty_when_the_store_does_not_exist(tmp_path):
    """Every existing fixture predates the store; a missing file is an empty
    store, never an error."""
    d = _world(tmp_path, instruments_rows=[_inst("IWM")],
               positions_rows=[_pos("IWM")], trades_rows=[])
    assert holding.summary("IWM", data_dir=d)["said"].empty


def test_a_comment_is_not_a_claim(tmp_path):
    """They are separate keys because they are separate things: a bet is
    scoreable and permanent, a comment is neither and may be deleted."""
    d = _world(tmp_path, instruments_rows=[_inst("IWM")],
               positions_rows=[_pos("IWM")], trades_rows=[],
               claims_rows=[_claim("c0001", "IWM")])
    _said(d, [_comment("cm0001", "instrument", "IWM")])
    got = holding.summary("IWM", data_dir=d)
    assert len(got["claims"]) == 1 and len(got["said"]) == 1


# ─── said_lines: the ONE formatter both skills call ───

def _row(**over):
    from src import commentary
    r = {c: "" for c in commentary.COLUMNS}
    r.update(id="cm0001", date="2026-09-27", kind="thought",
             target_type="cluster", target_id="Metals", origin="mine",
             body="Metals feel early.")
    r.update(over)
    return r


def test_said_lines_marks_his_own_words_as_his():
    line = holding.said_lines(pd.DataFrame([_row()]))[0]
    assert " me " in line and "Metals feel early." in line


def test_said_lines_always_names_the_speaker():
    """Newton and Lee are both origin=fundstrat — the name is the only thing
    that separates them, so it can never be dropped from the line."""
    line = holding.said_lines(pd.DataFrame([
        _row(kind="commentary", origin="fundstrat", said_by="Mark Newton")]))[0]
    assert "Mark Newton" in line and "fundstrat" in line


def test_said_lines_says_so_when_nobody_is_named():
    """Blank must not silently read as his own words — that is the whole
    distinction `kind` exists to protect."""
    line = holding.said_lines(pd.DataFrame([
        _row(kind="commentary", origin="13d", said_by="")]))[0]
    assert "(nobody named)" in line


def test_said_lines_shows_a_thought_that_became_a_bet():
    """Without this the same idea reads twice — once as a remark, once as
    the claim it grew into."""
    line = holding.said_lines(pd.DataFrame([_row(promoted_to="c0031")]))[0]
    assert "c0031" in line


def test_said_lines_names_the_market_rather_than_printing_a_blank():
    line = holding.said_lines(pd.DataFrame([
        _row(target_type="market", target_id="")]))[0]
    assert "the whole market" in line


def test_claim_lines_names_the_call_a_bet_came_from():
    out = "\n".join(holding.claim_lines({
        "id": "c0029", "date": "2026-09-15", "kind": "claim", "intent": "add",
        "origin": "fundstrat", "conviction": "high", "price": "705.02",
        "nav_pct": "6.45", "claim": "technology is breaking out",
        "decision": "technology-sep-26", "setups": [], "evidence": []}))
    assert "part of the technology-sep-26 call" in out


def test_claim_lines_says_nothing_about_a_call_when_there_is_none():
    """Most bets stand alone; an untagged one must print exactly as before."""
    out = "\n".join(holding.claim_lines({
        "id": "c0024", "date": "2026-09-08", "kind": "watch", "intent": "open",
        "origin": "fundstrat", "claim": "a momentum trade",
        "setups": [], "evidence": []}))
    assert "part of the" not in out


def test_said_lines_on_an_empty_frame_says_so():
    from src import commentary
    assert holding.said_lines(
        pd.DataFrame(columns=commentary.COLUMNS)) == ["(nothing said yet)"]


# ─── claim_lines: a watch row has no price, an update row no wrong_if ───

def test_claim_lines_never_prints_nan():
    """/show ASML printed `watch · add · mine · nan · $nan · nan% of NAV` and
    `wrong if: nan` — to a man with $110K in the account. Found by review."""
    out = "\n".join(holding.claim_lines({
        "id": "c0001", "date": "2026-06-09", "kind": "watch", "intent": "add",
        "origin": "mine", "claim": "re-enter near support",
        "conviction": float("nan"), "price": float("nan"),
        "nav_pct": float("nan"), "wrong_if": float("nan"),
        "check_date": float("nan"), "setups": [], "evidence": [],
    }))
    assert "nan" not in out.lower()
    assert "nothing scheduled" in out


def test_claim_lines_shows_every_field_it_has():
    out = "\n".join(holding.claim_lines({
        "id": "c0003", "date": "2026-08-15", "kind": "claim", "intent": "hold",
        "origin": "fundstrat", "said_by": "Mark Newton", "conviction": "high",
        "price": "214.60", "nav_pct": "4.20", "claim": "falling inflation",
        "wrong_if": "weekly close below $198", "check_date": "2026-09-25",
        "setups": ["macro", "trendline"], "evidence": [],
    }))
    assert "Mark Newton" in out and "high conviction" in out and "$214.60" in out
    assert "macro, trendline" in out and "2026-09-25" in out


def test_claim_lines_says_so_when_there_are_no_words():
    out = "\n".join(holding.claim_lines({"id": "c0001", "date": "2026-01-01",
                                        "kind": "claim", "setups": [],
                                        "evidence": []}))
    assert "(no words recorded)" in out


def test_a_level_ended_later_does_not_make_the_past_look_incomplete():
    """`as_of` must apply to levels, not only to claims.

    The check was "is any level un-ended RIGHT NOW", which ignored as_of. So
    ending l0001 on 2026-09-28 made IWM report "no active level" when asked
    about 2026-08-15 — a date on which l0001 was live. A record of the past
    must not change because of something that happened later.

    Found 2026-09-28 by test_summary_is_quiet_when_the_record_is_complete,
    which is pinned to 2026-08-15 for exactly this class of reason.
    """
    was_live = holding.summary("IWM", as_of="2026-08-15")["gaps"]
    assert "no active level — nothing would tell you if the price moved" not in was_live

    # ...and once l0001 is ended, the gap is real and must be reported.
    # `as_of` is pinned here for the reason the neighbouring test's docstring
    # gives: unpinned, this goes red the day he sets a new IWM level — which is
    # exactly what this gap message tells him to do — and a future session gets
    # told it broke something it never touched.
    after = holding.summary("IWM", as_of="2026-09-28")["gaps"]
    assert "no active level — nothing would tell you if the price moved" in after


def test_a_retired_cluster_is_not_asked_to_explain_itself():
    """Nothing is missing from a theme he closed on purpose. Retiring Metals
    and Water on 2026-09-28 otherwise left two clusters permanently asking for
    a thesis and reporting themselves empty."""
    for closed in ("Metals", "Water"):
        assert holding.cluster_summary(closed)["gaps"] == [], closed
    # ...and a cluster that is genuinely empty but still ACTIVE must still say so
    assert holding.cluster_summary("Clean Energy")["gaps"] != []


# ─── claim_lines() and a bet that has already been judged ───
#
# Found by review 2026-09-29: `claim_lines()` read `outcome`, `resolved_date`
# and `resolution_note` never. Six resolved rows — including IWM's c0003,
# judged `wrong` with a resolution note he wrote himself — printed exactly
# like live bets, with `next: <check_date>` still telling him to go and check
# something already closed. CLAUDE.md's second job is "read it back — what he
# said, when, and what he wrote for himself", and his verdict was the one
# thing the reader never saw.

def _resolved(**over):
    row = {"id": "c0003", "date": "2026-08-15", "kind": "claim",
           "intent": "hold", "origin": "fundstrat", "conviction": "high",
           "price": 214.6, "nav_pct": 4.20, "claim": "Falling inflation.",
           "wrong_if": "Weekly close below $198", "check_date": "2026-09-25",
           "setups": ["macro"], "evidence": [],
           "outcome": "wrong", "resolved_date": "2026-09-28",
           "resolution_note": "Wrong by the condition I set."}
    row.update(over)
    return row


def test_a_judged_bet_says_so():
    """⚠️ Asserts on a `verdict:` marker, not on the word "wrong" — that word
    already appears in every bet's `wrong if:` line, so the obvious assertion
    passes against an unfixed formatter and cannot fail for the bug it names."""
    lines = holding.claim_lines(_resolved())
    verdict = [l for l in lines if "verdict" in l.lower()]
    assert verdict, lines
    assert "wrong" in verdict[0], verdict


def test_a_judged_bet_does_not_ask_to_be_checked_again():
    """`next: 2026-09-25` on a bet judged three days later is an instruction
    to do work that is already done."""
    text = "\n".join(holding.claim_lines(_resolved()))
    assert "next" not in text, text
    assert "2026-09-28" in text, "the date he judged it must be shown"


def test_his_resolution_note_is_printed():
    """It is his verdict in his own words and nothing else displays it."""
    text = "\n".join(holding.claim_lines(_resolved(
        resolution_note="The right exit was the trend line break.")))
    assert "The right exit was the trend line break." in text, text


def test_an_unjudged_bet_is_unchanged():
    live = _resolved(outcome=None, resolved_date=None, resolution_note=None)
    text = "\n".join(holding.claim_lines(live))
    assert "next    : 2026-09-25" in text, text
    assert "verdict" not in text.lower(), text


def test_a_judged_bet_with_no_note_still_prints_the_verdict():
    """resolution_note is optional — c0001 has a verdict and no words."""
    text = "\n".join(holding.claim_lines(_resolved(resolution_note=None)))
    assert "unclear" in text or "wrong" in text, text


# ─── A price is only a number until it says what currency it is ──────────


def test_a_non_usd_claim_price_is_not_printed_with_a_dollar_sign():
    """Found by review 2026-10-01. `claim_lines()` hard-coded `$`, so c0027 —
    his Samsung entry at ₩240,000, about $173 — read back as `$240000.0` on a
    $1,530 position. ASML and IMAE, both priced in euros, read as dollars too.

    `levels.csv` fixed this exact class of bug on 2026-09-29 by giving the
    number a currency. The same fix had not been carried across to the table
    that records what he paid.
    """
    krw = holding.claim_lines({"id": "c0027", "date": "2026-09-09",
                               "kind": "claim", "price": "240000.0",
                               "currency": "KRW"})[0]
    assert "$240000" not in krw
    assert "₩240000" in krw or "KRW" in krw

    eur = holding.claim_lines({"id": "c0010", "date": "2026-08-18",
                               "kind": "claim", "price": "1609.4",
                               "currency": "EUR"})[0]
    assert "$1609.4" not in eur
    assert "€1609.4" in eur or "EUR" in eur


def test_a_blank_currency_still_prints_dollars():
    """55 of 59 existing rows are USD and carry no currency cell. They must
    keep reading exactly as they did, or the fix rewrites his whole history."""
    usd = holding.claim_lines({"id": "c0014", "date": "2026-08-18",
                               "kind": "claim", "price": "219.51"})[0]
    assert "$219.51" in usd


def test_a_comment_filed_against_a_bet_is_read_back_with_the_holding(tmp_path):
    """`commentary.csv` accepts target_type='claim' and `/note` documents it
    as the way to correct something in a bet that can no longer be edited —
    but nothing ever read those rows back. A write with no reader.

    Zero existed when review found it on 2026-10-01, so nothing was lost; it
    was a trap, not a wound. His call: make /show display them.
    """
    d = _world(tmp_path,
               instruments_rows=[_inst("QQQ")],
               positions_rows=[_pos("QQQ")],
               trades_rows=[],
               claims_rows=[_claim("c0001", "QQQ")])
    cid, iid = "c0001", "QQQ"

    pd.DataFrame([{
        "id": "cm0001", "date": "2026-09-01", "recorded": "2026-09-01",
        "kind": "thought", "target_type": "claim", "target_id": cid,
        "origin": "mine", "said_by": "", "body": "I worded this bet wrong.",
        "promoted_to": "", "note_ref": "",
    }]).to_csv(d / "commentary.csv", index=False)

    out = holding.summary(iid, data_dir=d)
    said = out["said_on_claims"]
    assert len(said) == 1, "a comment on a bet must come back with the holding"
    assert said.iloc[0]["target_id"] == cid
    assert "worded this bet wrong" in said.iloc[0]["body"]
