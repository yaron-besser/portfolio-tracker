"""
Tests for note shape.

Yaron's complaint, 2026-08-13: "every note looks different." He was right —
19 position notes used 75 distinct headings, 57 of them appearing in exactly
one note. A template already existed; nothing checked it, so it drifted.

The two things these tests protect are in tension, and both matter:
the rule must bite on notes being written, and must NOT bite on the 19 old
notes sitting untouched — or every commit is blocked until all of them are
rewritten.

Run from project root:
    pytest tests/test_notes.py
"""

from __future__ import annotations

import pandas as pd
import pytest

from src import notes


GOOD_POSITION = """# NVDA — NVIDIA

## Bear case
Someone smart is short because ___

## Catalysts
- [ ] Q3 earnings
"""

GOOD_CLUSTER = """# Cluster: Metals

## Why this cluster exists
Precious metals as a monetary hedge.

## What would end it
Real rates above 3% for two quarters.
"""


def test_a_well_shaped_position_note_passes():
    assert notes.check("notes/positions/NVDA.md", GOOD_POSITION) == []


def test_a_well_shaped_cluster_note_passes():
    assert notes.check("notes/clusters/metals.md", GOOD_CLUSTER) == []


def test_missing_headings_are_named_with_the_fix():
    problems = notes.check("notes/positions/NVDA.md", "# NVDA\n\n## Thoughts\n")
    assert len(problems) == 1
    assert "Bear case" in problems[0] and "Catalysts" in problems[0]
    assert "/note" in problems[0]          # names the way to write one


def test_extra_headings_are_allowed():
    """
    The rigid part is deliberately small.

    Yaron's other requirement is that a note can differ per holding — an ETF
    needs "why this wrapper", a stock does not. Only the two required headings
    are enforced; everything else is his.
    """
    text = GOOD_POSITION + "\n## Why this wrapper and not another\n\nfees\n"
    assert notes.check("notes/positions/IWM.md", text) == []


def test_a_cluster_note_is_not_judged_by_position_rules():
    """Two note types, two different required sets."""
    assert notes.check("notes/clusters/metals.md", GOOD_POSITION) != []
    assert notes.check("notes/positions/NVDA.md", GOOD_CLUSTER) != []


def test_archived_notes_are_never_reshaped():
    """
    A closed position's note is history. Rewriting it would be editing the
    record of what was believed at the time.
    """
    assert notes.check("notes/positions/_archive/CRM.md", "# CRM\n\n## Anything\n") == []


def test_readme_is_not_a_note():
    assert notes.check("notes/positions/README.md", "# Index\n") == []


def test_files_outside_the_two_folders_are_ignored():
    assert notes.check("notes/market/2026-W31.md", "# Week\n") == []
    assert notes.check("notes/portfolio_thesis.md", "# Constitution\n") == []


def test_check_paths_reports_every_bad_note_not_just_the_first(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "PROJECT_ROOT", tmp_path)
    for name in ("A", "B"):
        d = tmp_path / "notes" / "positions"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text("# x\n\n## Nope\n")
    with pytest.raises(ValueError) as e:
        notes.check_paths(["notes/positions/A.md", "notes/positions/B.md"])
    assert "A.md" in str(e.value) and "B.md" in str(e.value)


def test_status_covers_holdings_and_clusters_together():
    """One tracker for the whole rewrite — 47 holdings plus the live clusters."""
    s = notes.status()
    # `group` joined the vocabulary on 2026-08-16 when Technology and
    # Commodities were created. Split in two rather than widened to one
    # `<=`, so "a holding and a cluster are both present" stays asserted.
    assert set(s["type"]) <= {"holding", "cluster", "group"}
    assert {"holding", "cluster"} <= set(s["type"])
    known = {"ok", "missing", "needs reshaping", "broken link"}
    for st in set(s["state"]):
        assert st in known or st.startswith("TODO: "), f"unknown state {st!r}"


def test_status_puts_the_cluster_before_its_holdings():
    """
    Yaron's decision: write the cluster thesis first, then the holdings in it.

    So the tracker must not send him to a holding whose cluster is unwritten.
    """
    s = notes.status()
    core = s[s["cluster"] == "Core US"].reset_index(drop=True)
    if len(core) > 1 and (core["type"] == "cluster").any():
        assert core.loc[0, "type"] == "cluster", core[["what", "type"]].to_string()


def test_no_live_holding_points_at_an_archived_note():
    """
    MAGS and PLTR were closed, then re-entered — and their theses stayed in
    _archive/. Nothing detected it until the tracker existed, and an archived
    note is exempt from reshaping, so they would have stayed invisible.
    """
    s = notes.status()
    held = s[s["type"] == "holding"]
    archived = held[held["note"].str.contains("_archive", na=False)]
    assert archived.empty, archived[["what", "note"]].to_string(index=False)


# ─── stub(): the one thing a machine may write into notes/ (P1, 2026-08-15) ───

def test_stub_writes_only_headings_and_todo(tmp_path):
    """Yaron: "an empty form isn't writing." Nothing in it can be mistaken for
    his words, which is the whole reason P1 was narrowed to allow it."""
    f = notes.stub("DRAM", "Roundhill Memory ETF", path=tmp_path / "DRAM.md")
    text = f.read_text()
    assert notes.check("notes/positions/DRAM.md", text) == []   # shape is valid
    assert text.count("TODO") == 2
    # nothing but the title, the headings, TODO and blank lines
    meaningful = [l for l in text.splitlines()
                  if l.strip() and not l.startswith("#") and l.strip() != "TODO"]
    assert meaningful == [], f"stub wrote content: {meaningful}"


def test_stub_never_overwrites_his_words(tmp_path):
    """The moment it clobbers something he wrote, the record is untrustworthy."""
    f = tmp_path / "DRAM.md"
    f.write_text("# DRAM\n\n## Bear case\n\nmy actual thinking\n\n## Catalysts\n\nmine too\n")
    notes.stub("DRAM", path=f)
    assert "my actual thinking" in f.read_text()


def test_stub_makes_a_position_visible_in_the_queue(tmp_path):
    """The point of it: an unexplained buy stops being invisible.

    It must pass check() (the headings exist) AND report as unwritten, or it
    would leave the queue while saying nothing — which is what happened until
    2026-08-15.
    """
    f = notes.stub("DRAM", path=tmp_path / "DRAM.md")
    text = f.read_text()
    assert notes.check("notes/positions/DRAM.md", text) == []
    assert notes.unwritten_sections("notes/positions/DRAM.md", text) == [
        "Bear case", "Catalysts"]


def test_a_half_written_note_is_reported_per_section(tmp_path):
    """VTI, QQQ, RSP and core-us.md all read "ok" while five required sections
    said only TODO. The check is per heading now, and names which."""
    f = tmp_path / "QQQ.md"
    f.write_text("# QQQ\n\n## Bear case\n\nit is a bubble\n\n## Catalysts\n\nTODO\n")
    assert notes.unwritten_sections("notes/positions/QQQ.md", f.read_text()) == ["Catalysts"]
    assert not notes.is_unwritten(f.read_text(), "notes/positions/QQQ.md")


def U(body: str) -> list[str]:
    """unwritten_sections() for a one-section position note."""
    return notes.unwritten_sections(
        "notes/positions/X.md", f"# X\n\n## Bear case\n\n{body}\n\n## Catalysts\n\nreal\n")


def test_a_placeholder_is_recognised_however_it_is_dressed():
    """The first per-heading version only knew the bare word TODO. The spelling
    actually in this repo wraps onto a second line and is often bolded, so 13
    notes — ASML, MAGS, PLTR, agriculture.md and ten more — still read `ok`
    while saying nothing (second review, 2026-08-15)."""
    assert U("TODO") == ["Bear case"]
    assert U("TODO — not written yet. This note predates the required\n"
             "shape; the section is here so the gap is visible rather than silent.") \
        == ["Bear case"]
    assert U("**TODO — write this in your own words.**") == ["Bear case"]
    assert U("TBD") == ["Bear case"] and U("N/A") == ["Bear case"]
    # and it must not swallow real writing that merely mentions the word
    assert U("The bear case is crowding; TODO check the Q3 number.") == []


def test_draft_prompts_under_a_TODO_are_not_his_writing():
    """agriculture.md opens "**TODO — write this in your own words.** Strong
    prompts to help draft:" and then four paragraphs of drafting. Counting those
    as written is the P1 failure this module exists to prevent."""
    assert U("**TODO — write this in your own words.** Prompts to help:\n\n"
             "1. **Food security under stress** — fertilizer supply disruption\n"
             "2. **Climate change** — Super El Niño risk") == ["Bear case"]


def test_template_scaffolding_left_in_place_is_not_an_answer():
    """notes/templates/position.md puts a `___` rule inside `## Bear case` and
    `- [ ] …` placeholders under `## Catalysts`. A note started from it, TODO
    typed, separator left behind, used to read `ok`."""
    assert U("The smartest person on the other side is short, or refusing to buy, "
             "because:\n\n___\n\nTODO") == ["Bear case"]
    assert U("- [ ] …\n- [ ] …") == ["Bear case"]
    # but boilerplate ABOVE real prose must not hide the prose
    assert U("The smartest person on the other side is short, or refusing to buy, "
             "because:\n\nASML's monopoly invites state-sponsored competition.") == []


def test_a_heading_that_appears_twice_is_read_at_both_places():
    """Notes grow by appending dated sections. Keeping only the last occurrence
    computed the wrong end boundary and misreported in BOTH directions — a
    written section flagged, and an empty one cleared by its neighbour's prose."""
    two = ("# X\n\n## Bear case\n\nthe real bear case\n\n## Catalysts\n\nreal\n\n"
           "## Bear case\n\nTODO\n")
    assert notes.unwritten_sections("notes/positions/X.md", two) == []
    swapped = ("# X\n\n## Catalysts\n\nTODO\n\n## Bear case\n\nthe real one\n\n"
               "## Notes\n\nsomething\n")
    assert notes.unwritten_sections("notes/positions/X.md", swapped) == ["Catalysts"]


def test_a_heading_inside_a_code_block_is_not_a_heading():
    """A fenced example of the note shape must not become a section boundary."""
    text = ("# X\n\n## Bear case\n\n```\n## Catalysts\n\nnot real\n```\n\n"
            "the actual bear case\n\n## Catalysts\n\nTODO\n")
    assert notes.unwritten_sections("notes/positions/X.md", text) == ["Catalysts"]


def test_archived_notes_are_never_asked_to_be_written():
    """`_kind()` exempts `_archive/` as frozen history, and this must use the
    same rule — 21 instruments already point their note_ref there. A stubbed
    position that gets closed and archived would otherwise show a gap forever,
    with no way to clear it."""
    stub = "# X\n\n## Bear case\n\nTODO\n\n## Catalysts\n\nTODO\n"
    assert notes.unwritten_sections("notes/positions/_archive/X.md", stub) == []
    assert notes.unwritten_sections("notes/positions/README.md", stub) == []
    assert notes.unwritten_sections("notes/positions/X.md", stub) == ["Bear case", "Catalysts"]


def test_a_placeholder_wearing_a_bullet_is_still_a_placeholder():
    """`- TODO` appears 17 times across live notes. An emphasis-only prefix
    class missed every one, so a Bear case of three `- TODO` bullets under the
    template's lead-in read `ok` (third review, 2026-08-15)."""
    assert U("- TODO\n- TODO\n- TODO") == ["Bear case"]
    assert U("1. TODO\n2. TODO") == ["Bear case"]
    assert U("- [ ] TODO") == ["Bear case"]
    # but a list is not prose: one filled-in item is writing, and calling the
    # whole list a placeholder would hide it
    assert U("- TODO\n- the real reason it could fail") == []


def test_a_machine_written_marker_is_never_his_writing():
    """`write_fundamentals_to_notes()` appends `<!-- AUTO:FUNDAMENTALS-START -->`
    at end of file. A one-line comment used to pass straight through, so running
    the fundamentals writer moved an empty section to `ok` — a machine turning
    its own marker into his answer, which is the P1 failure this prevents."""
    assert U("<!-- AUTO:FUNDAMENTALS-START -->") == ["Bear case"]
    assert U("<!-- TODO: write this -->") == ["Bear case"]


def test_a_swallowed_heading_is_reported_not_silently_dropped():
    """check() reads the raw text, this reads the text with fences and comments
    blanked. When they disagreed, check() passed, this found no heading, and a
    note that was 100% TODO reported `ok` with nothing left to catch it."""
    fence = "# X\n\n```text\npasted log\n\n## Bear case\n\nTODO\n\n## Catalysts\n\nTODO\n"
    assert notes.unwritten_sections("notes/positions/X.md", fence) == [
        "Bear case", "Catalysts"]
    comment = "# X\n\n<!-- note to self\n\n## Bear case\n\nTODO\n\n## Catalysts\n\nTODO\n"
    assert notes.unwritten_sections("notes/positions/X.md", comment) == [
        "Bear case", "Catalysts"]
    # `~~~` is a code fence, not a horizontal rule — _RULE agrees, so an
    # unterminated one swallows what follows and that is reported, not hidden
    tilde = "# X\n\n## Bear case\n\nreal\n\n~~~\n\n## Catalysts\n\nreal\n"
    assert notes.unwritten_sections("notes/positions/X.md", tilde) == ["Catalysts"]


def test_a_stated_decision_not_to_write_is_an_answer():
    """GRNY says "Not written — this is a fund I don't do anything on." That is
    a decision, not a gap. A queue item he cannot clear is one he stops reading
    — the same reason the unexplained-position flag is not on every turn."""
    assert U("Not written — this is a fund I don't do anything on. (2026-08-14)") == []
    # same species: `n/a` alone is a placeholder, `n/a — because…` is an answer
    assert U("n/a") == ["Bear case"]
    assert U("n/a — I hold this as pure beta, deliberately.") == []


# ─── groups in the writing queue ───

def _queue(tmp_path, monkeypatch):
    """A book of Technology(group) > AI Infra + Software, and Crypto alone.

    Crypto ($700) is worth more than either cluster inside Technology but less
    than Technology itself ($900) — the exact case that ordering by money
    alone gets wrong.
    """
    from src import clusters, instruments

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"instrument_id": "NVDA", "market_value": "600"},
        {"instrument_id": "MSFT", "market_value": "300"},
        {"instrument_id": "IBIT", "market_value": "700"},
    ]).to_csv(tmp_path / "data" / "positions.csv", index=False)

    inst = pd.DataFrame([
        {"instrument_id": "NVDA", "instrument_type": "stock",
         "cluster_id": "AI Infrastructure", "note_ref": ""},
        {"instrument_id": "MSFT", "instrument_type": "stock",
         "cluster_id": "Software", "note_ref": ""},
        {"instrument_id": "IBIT", "instrument_type": "etf",
         "cluster_id": "Crypto", "note_ref": ""},
    ])
    cl = pd.DataFrame([
        {"cluster_id": "Technology", "parent": "", "layer": "group",
         "note_ref": "", "status": "active"},
        {"cluster_id": "AI Infrastructure", "parent": "Technology",
         "layer": "thematic", "note_ref": "", "status": "active"},
        {"cluster_id": "Software", "parent": "Technology",
         "layer": "thematic", "note_ref": "", "status": "active"},
        {"cluster_id": "Crypto", "parent": "", "layer": "thematic",
         "note_ref": "", "status": "active"},
    ])
    monkeypatch.setattr(notes, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(instruments, "load", lambda *a, **k: inst)
    monkeypatch.setattr(clusters, "load", lambda *a, **k: cl)


def test_a_group_is_written_before_the_clusters_inside_it(tmp_path, monkeypatch):
    """The rewrite writes the theme before its holdings. A group is one more
    level of the same rule: the wide question before the narrow one."""
    _queue(tmp_path, monkeypatch)
    order = notes.status()["what"].tolist()
    assert order == ["Technology", "AI Infrastructure", "NVDA",
                     "Software", "MSFT", "Crypto", "IBIT"]


def test_a_group_is_worth_what_is_inside_it(tmp_path, monkeypatch):
    _queue(tmp_path, monkeypatch)
    s = notes.status().set_index("what")
    assert s.at["Technology", "market_value"] == 900.0
    assert s.at["Technology", "type"] == "group"
    assert s.at["AI Infrastructure", "type"] == "cluster"


def test_an_ungrouped_cluster_is_ordered_exactly_as_before(tmp_path, monkeypatch):
    """The twelve ungrouped clusters are the regression surface."""
    _queue(tmp_path, monkeypatch)
    order = notes.status()["what"].tolist()
    assert order.index("Crypto") < order.index("IBIT")


def test_a_family_is_not_sunk_when_its_group_is_not_active(tmp_path, monkeypatch):
    """`money` is built from active rows only, so a child of a retired or
    empty group keyed off a group worth 0.0 and the whole family sorted below
    everything — a $600 theme under a $50 one (found by review, 2026-08-16).

    `empty` is the honest status for a group the moment it is created, so this
    was reachable by the ordinary create-then-assign order.
    """
    from src import clusters, instruments

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"instrument_id": "NVDA", "market_value": "600"},
        {"instrument_id": "IBIT", "market_value": "50"},
    ]).to_csv(tmp_path / "data" / "positions.csv", index=False)

    monkeypatch.setattr(notes, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(instruments, "load", lambda *a, **k: pd.DataFrame([
        {"instrument_id": "NVDA", "instrument_type": "stock",
         "cluster_id": "AI Infrastructure", "note_ref": ""},
        {"instrument_id": "IBIT", "instrument_type": "etf",
         "cluster_id": "Crypto", "note_ref": ""},
    ]))
    monkeypatch.setattr(clusters, "load", lambda *a, **k: pd.DataFrame([
        {"cluster_id": "Technology", "parent": "", "layer": "group",
         "note_ref": "", "status": "retired"},
        {"cluster_id": "AI Infrastructure", "parent": "Technology",
         "layer": "thematic", "note_ref": "", "status": "active"},
        {"cluster_id": "Crypto", "parent": "", "layer": "thematic",
         "note_ref": "", "status": "active"},
    ]))
    order = notes.status()["what"].tolist()
    assert order == ["AI Infrastructure", "NVDA", "Crypto", "IBIT"]


# ─── notes/positions/closed/ — completed trades, kept in view ───
#
# His call, 2026-09-28: a position he has closed is not dead machinery, so it
# does not belong in `_archive/`. It is the record of a trade he might want to
# learn from, and it should be somewhere he would look. The shape rule is
# waived for the same reason `_archive/` waives it — history is never
# reshaped, and 15 of the notes involved predate `## Bear case` entirely.

def test_closed_position_notes_are_never_reshaped():
    """Without this, moving a pre-2026-08 note out of _archive/ makes the
    commit guard refuse it: 15 of the 19 closed notes have no `## Bear case`
    and no `## Catalysts`, and writing them would mean inventing his thesis."""
    assert notes.check("notes/positions/closed/KWEB.md",
                       "# KWEB\n\nIt broke, so I never opened it.\n") == []


def test_closed_position_notes_are_never_asked_to_be_written():
    """The same exemption must reach unwritten_sections(), or a closed note
    lands back in the writing queue the day it moves."""
    stub = "# RSP\n\n## Bear case\n\nTODO\n"
    assert notes.unwritten_sections("notes/positions/closed/RSP.md", stub) == []


def test_a_live_position_note_is_still_shaped():
    """The exemption must be the `closed/` FOLDER, not the word anywhere in
    the path.

    ⚠️ The three inputs are chosen to fail against a naive `"closed" not in p`.
    An earlier version of this test used `CLOSEDEND.md` — uppercase, which the
    naive predicate also rejects, so it could not fail for the bug it names.
    Review caught that on 2026-09-28.
    """
    for path in ("notes/positions/CLOSEDEND.md",      # the word, uppercase
                 "notes/positions/closed.md",          # the word, as a file
                 "notes/positions/closed-end-funds/X.md"):   # the word, a folder
        assert notes.check(path, "# X\n\n## Nothing\n"), (
            f"{path} is a live note and must still require its headings")


def test_no_held_position_points_at_a_closed_note():
    """The mirror of the _archive/ rule. A name he re-enters keeps its old
    note in closed/ unless someone moves it back, and nothing else would say
    so — MAGS and PLTR already did exactly this via _archive/."""
    s = notes.status()
    held = s[s["type"] == "holding"]
    stale = held[held["note"].str.contains("positions/closed/", na=False)]
    assert stale.empty, stale[["what", "note"]].to_string(index=False)


# ─── notes/positions/watchlist/ — names he is considering, not holding ───
#
# The four that moved out of `_archive/` on 2026-09-29 (PEJ, BG, MOO, SETM)
# are rows in instruments.csv with status=watchlist, so something already
# points at them. They carry the pre-2026-08 long shape and none has a
# `## Bear case`.

def test_watchlist_notes_are_never_reshaped():
    """A name he has not bought has no position to bear-case. Without the
    waiver the guard refuses all four the moment they leave `_archive/`."""
    assert notes.check("notes/positions/watchlist/MOO.md",
                       "# MOO\n\n## What this ETF gets me\n\nAg equipment.\n") == []


def test_watchlist_notes_are_never_asked_to_be_written():
    """The exemption must reach unwritten_sections() too, or the four land in
    a writing queue that is ordered by money he has not spent."""
    stub = "# BG\n\n## Bear case\n\nTODO\n"
    assert notes.unwritten_sections("notes/positions/watchlist/BG.md", stub) == []


def test_a_live_position_note_is_not_exempted_by_the_word_watchlist():
    """The exemption must be the `watchlist/` FOLDER, not the word anywhere in
    the path — the same bug review caught for `closed/` on 2026-09-28."""
    for path in ("notes/positions/WATCHLIST.md",          # the word, as a file
                 "notes/positions/watchlisted/X.md"):     # the word, a folder
        assert notes.check(path, "# X\n\n## Nothing\n"), (
            f"{path} is a live note and must still require its headings")
