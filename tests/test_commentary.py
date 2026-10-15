"""Tests for src.commentary — the home for a thought or someone else's words.

Run: venv/bin/python -m pytest tests/test_commentary.py -q

Why this store exists (2026-09-27): every `claims.csv` row needs one
instrument, so a view about a theme or about the market had nowhere to go —
`add_claim(ticker="Metals")` raises `Unknown instrument(s) ['Metals']`. And a
comment is something Yaron may want to DELETE later, which `claims.csv`
physically refuses: it is append-only. A bet is history; a comment is
disposable. They cannot share a file.

Writes go to tmp_path — the real store is never touched.
"""
from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
import pytest

from src import commentary


def _store(tmp_path):
    return tmp_path / "commentary.csv"


def _thought(**over) -> dict:
    """His own view about a theme — the case that had no home before."""
    row = {
        "date": "2026-09-27",
        "kind": "thought",
        "target_type": "cluster",
        "target_id": "Metals",
        "origin": "mine",
        "body": "Metals feel early to me — the miners have not caught up to the metal.",
    }
    row.update(over)
    return row


def _commentary(**over) -> dict:
    """Something someone said, with no link and no document behind it."""
    row = {
        "date": "2026-09-26",
        "kind": "commentary",
        "target_type": "market",
        "target_id": "",
        "origin": "fundstrat",
        "said_by": "Mark Newton",
        "body": "Newton said on the webinar that breadth is improving into year-end.",
    }
    row.update(over)
    return row


# ─── The four cases that drove this store ───

def test_a_thought_about_a_cluster(tmp_path):
    """The case that raised `Unknown instrument(s) ['Metals']` in claims.csv."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought())
    row = commentary.load(p).set_index("id").loc[cid]
    assert row["target_type"] == "cluster" and row["target_id"] == "Metals"


def test_a_thought_about_the_whole_market_needs_no_target_id(tmp_path):
    """`market` is the one scope that names nothing — there is no row for it."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought(target_type="market", target_id=""))
    assert commentary.load(p).set_index("id").loc[cid]["target_type"] == "market"


def test_spoken_words_with_no_link_are_recordable(tmp_path):
    """A webinar is not a document: evidence.csv needs a file, a URL or a
    PDF name, and a webinar has none of the three."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_commentary())
    assert commentary.load(p).set_index("id").loc[cid]["said_by"] == "Mark Newton"


def test_a_thought_about_one_stock_still_works(tmp_path):
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought(target_type="instrument", target_id="NVDA"))
    assert commentary.load(p).set_index("id").loc[cid]["target_id"] == "NVDA"


# ─── The two cross-checks: kind and origin keep each other honest ───

def test_a_thought_must_be_his_own(tmp_path):
    """`kind=thought` means HIS words. Filing Newton as a thought would make
    `scoreboard()`'s origin cut — "does Fundstrat help me?" — a lie."""
    p = _store(tmp_path)
    with pytest.raises((ValueError, pa.errors.SchemaErrors), match="thought|mine|origin"):
        commentary.add(path=p, **_thought(origin="fundstrat"))


def test_a_thought_may_not_name_someone_else(tmp_path):
    p = _store(tmp_path)
    with pytest.raises((ValueError, pa.errors.SchemaErrors), match="thought|said_by"):
        commentary.add(path=p, **_thought(said_by="Mark Newton"))


def test_commentary_may_not_claim_to_be_his_own(tmp_path):
    p = _store(tmp_path)
    with pytest.raises((ValueError, pa.errors.SchemaErrors), match="commentary|mine|origin"):
        commentary.add(path=p, **_commentary(origin="mine"))


def test_commentary_without_a_name_is_allowed_but_reported(tmp_path):
    """He may not remember who said it. Never block a record over that —
    `unattributed()` reports it instead, the way gaps() does for instruments."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_commentary(said_by=""))
    assert cid in set(commentary.unattributed(p)["id"])


# ─── Targets ───

def test_a_named_scope_requires_a_target(tmp_path):
    """Only `market` names nothing. A blank cluster would file the thought
    against everything and nothing."""
    p = _store(tmp_path)
    with pytest.raises((ValueError, pa.errors.SchemaErrors), match="target_id|market"):
        commentary.add(path=p, **_thought(target_type="cluster", target_id=""))


def test_market_refuses_a_target_id(tmp_path):
    p = _store(tmp_path)
    with pytest.raises((ValueError, pa.errors.SchemaErrors), match="target_id|market"):
        commentary.add(path=p, **_thought(target_type="market", target_id="Metals"))


def test_an_unknown_scope_is_refused(tmp_path):
    p = _store(tmp_path)
    with pytest.raises((ValueError, pa.errors.SchemaErrors)):
        commentary.add(path=p, **_thought(target_type="portfolio"))


def test_body_may_not_be_blank(tmp_path):
    """The words are the whole point of the row."""
    p = _store(tmp_path)
    with pytest.raises((ValueError, pa.errors.SchemaErrors)):
        commentary.add(path=p, **_thought(body="   "))


# ─── Reading it back ───

def test_about_gathers_everything_said_on_one_theme(tmp_path):
    p = _store(tmp_path)
    commentary.add(path=p, **_thought())
    commentary.add(path=p, **_commentary(target_type="cluster", target_id="Metals"))
    commentary.add(path=p, **_thought(target_type="instrument", target_id="NVDA"))
    got = commentary.about("Metals", path=p)
    assert len(got) == 2 and set(got["kind"]) == {"thought", "commentary"}


def test_since_answers_what_did_i_think_in_august(tmp_path):
    p = _store(tmp_path)
    commentary.add(path=p, **_thought(date="2026-08-14"))
    commentary.add(path=p, **_thought(date="2026-09-27"))
    assert len(commentary.since("2026-09-01", path=p)) == 1


# ─── A thought that became a bet ───

def test_promote_links_the_thought_to_the_claim(tmp_path):
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought())
    commentary.promote(cid, "c0031", path=p)   # c0031 is a real live claim
    assert commentary.load(p).set_index("id").loc[cid]["promoted_to"] == "c0031"


def test_promote_refuses_a_bet_that_does_not_exist(tmp_path):
    """Caught on write, not only at commit: promoted_to is what stops one idea
    reading twice, once as a remark and once as the claim it became."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought())
    with pytest.raises(ValueError, match="c9999|does not exist|only be promoted"):
        commentary.promote(cid, "c9999", path=p)


def test_a_thought_can_only_be_promoted_once(tmp_path):
    """Two bets from one thought means the second is a different thought."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought())
    commentary.promote(cid, "c0031", path=p)
    with pytest.raises(ValueError, match="already"):
        commentary.promote(cid, "c0032", path=p)


# ─── Deleting — the reason this is not claims.csv ───

def test_forget_removes_the_row(tmp_path):
    p = _store(tmp_path)
    keep = commentary.add(path=p, **_thought())
    drop = commentary.add(path=p, **_commentary())
    commentary.forget(drop, path=p)
    assert set(commentary.load(p)["id"]) == {keep}


def test_forget_leaves_every_other_row_untouched(tmp_path):
    """Deleting one comment must not rewrite the words of another."""
    p = _store(tmp_path)
    commentary.add(path=p, **_thought())
    drop = commentary.add(path=p, **_commentary())
    before = commentary.load(p)
    before = before[before["id"] != drop].reset_index(drop=True)
    commentary.forget(drop, path=p)
    pd.testing.assert_frame_equal(before, commentary.load(p).reset_index(drop=True))


def test_forget_refuses_an_id_that_is_not_there(tmp_path):
    p = _store(tmp_path)
    commentary.add(path=p, **_thought())
    with pytest.raises(ValueError, match="cm9999|no row|not found"):
        commentary.forget("cm9999", path=p)


def test_ids_do_not_collide_with_claim_ids(tmp_path):
    """`c0001` is a bet and `cm0001` is a comment — never the same string."""
    p = _store(tmp_path)
    assert commentary.add(path=p, **_thought()).startswith("cm")


def test_an_empty_store_is_valid(tmp_path):
    assert commentary.load(_store(tmp_path)).empty


# ─── Seven names are both a cluster and a sector ───

def test_seven_live_names_are_both_a_theme_and_a_sector():
    """The reason about() may not quietly merge them. CLAUDE.md: the cluster
    is WHY he bought it, the sector is WHAT he is exposed to — two questions."""
    assert commentary.is_ambiguous("Technology")
    assert commentary.is_ambiguous("Healthcare")
    assert not commentary.is_ambiguous("Metals")
    assert not commentary.is_ambiguous("NVDA")


def test_about_returns_both_readings_and_keeps_them_labelled(tmp_path):
    p = _store(tmp_path)
    commentary.add(path=p, **_thought(target_type="cluster", target_id="Technology"))
    commentary.add(path=p, **_thought(target_type="sector", target_id="Technology"))
    got = commentary.about("Technology", path=p)
    assert len(got) == 2
    # Both readings come back, and the caller can still tell them apart.
    assert set(got["target_type"]) == {"cluster", "sector"}


def test_about_can_be_narrowed_to_one_reading(tmp_path):
    p = _store(tmp_path)
    commentary.add(path=p, **_thought(target_type="cluster", target_id="Technology"))
    commentary.add(path=p, **_thought(target_type="sector", target_id="Technology"))
    got = commentary.about("Technology", scope="cluster", path=p)
    assert len(got) == 1 and got.iloc[0]["target_type"] == "cluster"


def test_a_blank_cell_never_reads_back_as_the_word_nan(tmp_path):
    """`na_values=[""]` turns a blank into NaN, and `nan or ""` is NaN because
    NaN is truthy — so a naive read produced the string "nan". Same class as
    the `N/A` round-trip bug found in the 2026-09-27 audit."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought())
    row = commentary.load(p).set_index("id").loc[cid]
    for col in ("said_by", "promoted_to", "note_ref"):
        assert commentary._blank(row[col]) == "", f"{col} read back as {row[col]!r}"


# ─── Everything a read-only review pass broke, 2026-09-27 ───

def test_add_refuses_a_promotion_to_a_bet_that_does_not_exist(tmp_path):
    """add() documented this check and did not call it — only the guard did."""
    p = _store(tmp_path)
    with pytest.raises(ValueError, match="c9999|does not exist|only be promoted"):
        commentary.add(path=p, **_thought(promoted_to="c9999"))


def test_promote_stores_the_id_without_surrounding_space(tmp_path):
    """'  c0001  ' validated, committed and printed correctly while matching no
    claim id in a join — every checker stripped, the cell did not."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought())
    commentary.promote(cid, "  c0031  ", path=p)
    assert commentary.load(p).set_index("id").loc[cid]["promoted_to"] == "c0031"


def test_promote_refuses_a_blank_target(tmp_path):
    """It used to be a silent no-op that left the row promotable."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought())
    with pytest.raises(ValueError, match="needs the id|blank"):
        commentary.promote(cid, "   ", path=p)


def test_pandas_na_is_blank_not_the_text_NA():
    """_blank() tested isinstance(float), so pd.NA became the literal '<NA>' —
    a bad body accepted, and a good said_by refused for naming nobody."""
    assert commentary._blank(pd.NA) == ""
    assert commentary._blank(pd.NaT) == ""
    assert commentary._blank(float("nan")) == ""
    assert commentary._blank(None) == ""


def test_an_invisible_character_is_not_a_name_or_a_body(tmp_path):
    """U+200B is not whitespace to str.strip(), so it passed as both — the two
    rules that must never be defeatable by whitespace."""
    p = _store(tmp_path)
    with pytest.raises((ValueError, pa.errors.SchemaErrors)):
        commentary.add(path=p, **_thought(body="​"))
    cid = commentary.add(path=p, **_commentary(said_by="​"))
    assert cid in set(commentary.unattributed(p)["id"])


def test_about_nothing_returns_nothing(tmp_path):
    """about("") returned every market row — the one scope whose target_id is
    legitimately blank."""
    p = _store(tmp_path)
    commentary.add(path=p, **_thought(target_type="market", target_id=""))
    assert commentary.about("", path=p).empty
    assert commentary.about("   ", path=p).empty
    assert len(commentary.on_market(path=p)) == 1


def test_a_mangled_header_is_refused_even_with_no_rows(tmp_path):
    """`"banana","split"` was a valid empty store and committed cleanly. This
    is the one table exempt from the append-only rule, so nothing else would
    have caught a truncating write."""
    p = _store(tmp_path)
    p.write_text('"banana","split"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        commentary.load(p)


def test_forget_can_remove_the_row_that_is_breaking_the_store(tmp_path):
    """One bad hand-edited row broke /show and /note AND blocked forget() —
    the only store designed to lose a row could not lose the row breaking it."""
    p = _store(tmp_path)
    good = commentary.add(path=p, **_thought())
    raw = p.read_text().replace('"mine","","Metals feel early to me',
                                '"fundstrat","","Metals feel early to me', 1)
    p.write_text(raw, encoding="utf-8")
    with pytest.raises((ValueError, pa.errors.SchemaErrors)):
        commentary.load(p)                     # the store is broken
    commentary.forget(good, path=p)            # and can still be repaired
    assert commentary.load(p).empty


def test_forget_previews_without_the_word_nan(tmp_path):
    """The returned dict feeds a human preview, and str(nan) is 'nan'."""
    p = _store(tmp_path)
    cid = commentary.add(path=p, **_thought())
    gone = commentary.forget(cid, path=p)
    assert gone["said_by"] == "" and gone["promoted_to"] == ""
    assert "nan" not in str(gone.values()).lower()
