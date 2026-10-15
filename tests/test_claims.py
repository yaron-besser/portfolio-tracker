"""Tests for src.claims — the claim store's validation rules (_archive/design-record.md §5.5).

Run: venv/bin/python -m pytest tests/test_claims.py -q

Each rule gets one test; the whole-file test at the bottom is the drift
catcher for hand-edits. Writes go to tmp_path — the real store is never
touched by tests.
"""
from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
import pytest

from src import claims


def _good_row(**over) -> dict:
    """A fully valid claim row; tests break exactly one field at a time."""
    row = {
        "date": "2026-08-08",
        "ticker": "NVDA",
        "origin": "mine",
        "kind": "claim",
        "claim": "AI capex holds through 2027; NVDA data-center revenue keeps growing",
        "wrong_if": "two consecutive quarters of declining data-center revenue",
        "check_date": "2026-11-08",
        "price": 181.50,
        "conviction": "high",
    }
    row.update(over)
    return row


def _store(tmp_path):
    return tmp_path / "claims.csv"


# ─── Vocabularies ───

def test_vocab_reads_ops_files():
    """The vocab loader is the single source all enforcement points read.

    Membership, not equality: §5.6 says adding a value is a one-line append,
    so pinning exact contents would make the sanctioned edit turn the suite red.
    """
    assert "mine" in claims.vocab("origin")
    assert {"claim", "watch"} <= set(claims.vocab("kind"))
    assert {"low", "med", "high"} <= set(claims.vocab("conviction"))


def test_vocab_missing_file_is_loud():
    with pytest.raises(FileNotFoundError, match="no_such_vocab"):
        claims.vocab("no_such_vocab")


def test_schema_error_explains_instead_of_dumping_json(tmp_path):
    """
    A rejection must teach the fix (P2), not print Pandera's JSON.

    The commit guard is the place Yaron meets these messages, so the column,
    the row and the command to run have to be on the visible line.
    """
    p = _store(tmp_path)
    claims.add_claim(path=p, **_good_row())
    df = pd.read_csv(p, dtype=str)
    df["price"] = pd.to_numeric(df["price"])
    df.loc[0, "kind"] = "hunch"  # not in ops/vocab/kind
    with pytest.raises(pa.errors.SchemaErrors) as e:
        claims.validate(df)

    msg = claims.explain_schema_error(e.value)
    assert "kind" in msg and "hunch" in msg
    assert "echo <value> >> ops/vocab/kind" in msg
    assert "DATAFRAME_CHECK" not in msg  # the raw JSON dump


def test_explain_passes_through_a_plain_error():
    """Not every block is a schema failure — append-only raises ValueError."""
    assert claims.explain_schema_error(ValueError("history rewritten")) == (
        "history rewritten"
    )


# ─── add_claim happy path ───

def test_add_claim_returns_id_and_lands(tmp_path):
    path = _store(tmp_path)
    new_id = claims.add_claim(path=path, **_good_row())
    assert new_id == "c0001"
    df = claims.load_claims(path)
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "NVDA"
    assert df.iloc[0]["recorded"] >= df.iloc[0]["date"]  # stamped today


def test_ids_increment(tmp_path):
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row())
    second = claims.add_claim(path=path, **_good_row(ticker="ASML", price=760.0))
    assert second == "c0002"


def test_watch_row_needs_no_price_conviction_wrong_if(tmp_path):
    """A watchlist item is a claim with no position — lighter requirements."""
    path = _store(tmp_path)
    new_id = claims.add_claim(
        path=path,
        **_good_row(kind="watch", price=None, conviction=None, wrong_if=None),
    )
    assert new_id == "c0001"


# ─── Rejection: one rule per test, message must name the problem (P2) ───

def test_missing_wrong_if_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="wrong_if is required"):
        claims.add_claim(path=_store(tmp_path), **_good_row(wrong_if=None))


def test_missing_price_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="price is required"):
        claims.add_claim(path=_store(tmp_path), **_good_row(price=None))


def test_negative_price_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="positive"):
        claims.add_claim(path=_store(tmp_path), **_good_row(price=-5.0))


def test_missing_conviction_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="conviction is required"):
        claims.add_claim(path=_store(tmp_path), **_good_row(conviction=None))


def test_bad_conviction_names_vocab_file(tmp_path):
    """The rejection teaches the fix: allowed values + the file to extend."""
    with pytest.raises(pa.errors.SchemaErrors, match="ops/vocab/conviction"):
        claims.add_claim(path=_store(tmp_path), **_good_row(conviction="strong"))


def test_bad_origin_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="ops/vocab/origin"):
        claims.add_claim(path=_store(tmp_path), **_good_row(origin="reddit"))


def test_check_date_before_date_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="check_date must be later"):
        claims.add_claim(path=_store(tmp_path), **_good_row(check_date="2026-08-01"))


def test_non_iso_date_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="ISO"):
        claims.add_claim(path=_store(tmp_path), **_good_row(date="08/08/2026"))


def test_impossible_calendar_date_rejected(tmp_path):
    """A regex alone would accept 2026-02-30 — a date that can never arrive."""
    with pytest.raises(pa.errors.SchemaErrors, match="real calendar date"):
        claims.add_claim(path=_store(tmp_path), **_good_row(check_date="2026-02-30"))


def test_missing_required_field_rejected(tmp_path):
    """Every field §5.5 requires on a claim row is actually enforced."""
    for field in ("date", "ticker", "claim", "check_date", "origin", "kind"):
        with pytest.raises((pa.errors.SchemaErrors, pa.errors.SchemaError, ValueError)):
            claims.add_claim(path=_store(tmp_path), **_good_row(**{field: None}))


def test_bad_kind_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="ops/vocab/kind"):
        claims.add_claim(path=_store(tmp_path), **_good_row(kind="idea"))


def test_bad_outcome_rejected(tmp_path):
    """The rejection now names the file to extend, like every other vocabulary."""
    with pytest.raises(pa.errors.SchemaErrors, match="ops/vocab/outcome"):
        claims.add_claim(
            path=_store(tmp_path),
            **_good_row(outcome="maybe", resolved_date="2026-12-01"),
        )


def test_outcome_without_resolved_date_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="together or absent together"):
        claims.add_claim(path=_store(tmp_path), **_good_row(outcome="right"))


def test_resolved_date_without_outcome_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="together or absent together"):
        claims.add_claim(path=_store(tmp_path), **_good_row(resolved_date="2026-12-01"))


def test_hash_in_text_survives_round_trip(tmp_path):
    """A '#' in the claim must not truncate the row on read — that would
    brick the store after a write that validated fine."""
    path = _store(tmp_path)
    text = "NVDA becomes #1 by market cap; capex holds"
    claims.add_claim(path=path, **_good_row(claim=text))
    df = claims.load_claims(path)
    assert df.iloc[0]["claim"] == text
    assert df.iloc[0]["check_date"] == "2026-11-08"  # not truncated away


# `test_evidence_path_must_exist` lived here until 2026-08-16. The rule moved
# to evidence.check_location() with the column, and is covered there by
# tests/test_evidence.py::test_repo_storage_needs_a_real_file.


def test_note_ref_must_exist(tmp_path):
    with pytest.raises(ValueError, match="does not point to a file"):
        claims.add_claim(path=_store(tmp_path), **_good_row(note_ref="notes/positions/NOPE.md"))


def test_absolute_ref_path_rejected(tmp_path):
    """An absolute path is valid only on this laptop — repo-relative only."""
    note = tmp_path / "NVDA.md"
    note.write_text("# NVDA\n")
    with pytest.raises(ValueError, match="is absolute"):
        claims.add_claim(path=_store(tmp_path), **_good_row(note_ref=str(note)))


def test_directory_ref_rejected(tmp_path):
    """A directory is not a file (is_file, not exists)."""
    with pytest.raises(ValueError, match="does not point to a file"):
        claims.add_claim(path=_store(tmp_path), **_good_row(note_ref="notes/positions"))


def test_repo_relative_ref_accepted(tmp_path):
    """A real repo-relative note reference is the supported form."""
    new_id = claims.add_claim(path=_store(tmp_path), **_good_row(note_ref="notes/README.md"))
    assert new_id == "c0001"


def test_archived_note_does_not_brick_the_store(tmp_path):
    """Closing a position archives its note. That must never block writes to
    unrelated claims — path existence is a write-time check, not a load gate."""
    path = _store(tmp_path)
    note = PROJECT_ROOT_NOTE = tmp_path / "moved.md"
    note.write_text("# temp\n")
    # Hand-write a row whose note_ref no longer resolves.
    claims.add_claim(path=path, **_good_row(ticker="TSLA"))
    df = pd.read_csv(path, dtype=str)
    df.loc[0, "note_ref"] = "notes/positions/GONE.md"
    df.to_csv(path, index=False)

    claims.load_claims(path)  # still loads
    assert claims.add_claim(path=path, **_good_row(ticker="NVDA")) == "c0002"
    stale = claims.stale_refs(path=path)
    assert list(stale["ticker"]) == ["TSLA"]  # visible, not blocking (P2)


def test_unknown_field_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown field"):
        claims.add_claim(path=_store(tmp_path), **_good_row(chart_tf="1D"))


# ─── Resolution ───

def test_resolve_fills_outcome_and_date(tmp_path):
    path = _store(tmp_path)
    original = _good_row()["claim"]
    cid = claims.add_claim(path=path, **_good_row(check_date="2026-08-09"))
    claims.resolve_claim(cid, "right", note="capex held", path=path)
    df = claims.load_claims(path)
    assert df.iloc[0]["outcome"] == "right"
    assert pd.notna(df.iloc[0]["resolved_date"])
    assert df.iloc[0]["resolution_note"] == "capex held"
    # The point-in-time claim text is never rewritten (R1).
    assert df.iloc[0]["claim"] == original


def test_resolve_twice_rejected(tmp_path):
    path = _store(tmp_path)
    cid = claims.add_claim(path=path, **_good_row())
    claims.resolve_claim(cid, "wrong", path=path)
    with pytest.raises(ValueError, match="already resolved"):
        claims.resolve_claim(cid, "right", path=path)


def test_resolve_unknown_id_rejected(tmp_path):
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row())
    with pytest.raises(ValueError, match="c9999"):
        claims.resolve_claim("c9999", "right", path=path)


# ─── Derived views (no status column — §5.4) ───

def test_due_open_resolved_partition(tmp_path):
    path = _store(tmp_path)
    # NVDA: check date already passed -> due. ASML: far future -> open.
    claims.add_claim(path=path, **_good_row(date="2026-06-01", check_date="2026-07-01"))
    claims.add_claim(path=path, **_good_row(ticker="ASML", check_date="2099-01-01"))
    cid = claims.add_claim(path=path, **_good_row(ticker="PM", check_date="2026-11-01"))
    claims.resolve_claim(cid, "unclear", path=path)

    due = claims.due_claims(as_of="2099-12-31", path=path)
    assert set(due["ticker"]) == {"NVDA", "ASML"}
    assert set(claims.open_claims(path=path)["ticker"]) == {"ASML"}
    assert set(claims.resolved_claims(path=path)["ticker"]) == {"PM"}


def test_due_respects_as_of(tmp_path):
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row(check_date="2026-09-01"))
    assert claims.due_claims(as_of="2026-08-31", path=path).empty
    assert len(claims.due_claims(as_of="2026-09-01", path=path)) == 1


# `test_unevidenced_lists_blank_evidence` lived here until 2026-08-16.
# unevidenced_claims() now reads the link table, so it moved to
# tests/test_evidence.py::test_unevidenced_claims_lists_claims_with_no_link.


# ─── Append-only (§5.5) — enforced by the commit guard ───

def test_append_only_allows_new_rows(tmp_path):
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row())
    old = claims.load_claims(path)
    claims.add_claim(path=path, **_good_row(ticker="ASML"))
    claims.check_append_only(old, claims.load_claims(path))  # no raise


def test_append_only_allows_resolution(tmp_path):
    path = _store(tmp_path)
    cid = claims.add_claim(path=path, **_good_row())
    old = claims.load_claims(path)
    claims.resolve_claim(cid, "right", note="held up", path=path)
    claims.check_append_only(old, claims.load_claims(path))  # no raise


def test_append_only_blocks_deleted_row(tmp_path):
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row())
    old = claims.load_claims(path)
    with pytest.raises(ValueError, match="Rows deleted"):
        claims.check_append_only(old, old.iloc[0:0])


def test_append_only_survives_the_commit_hook_path(tmp_path):
    """The guard compares a git HEAD copy (raw text) against a typed frame.

    Regression for the 2026-08-14 review finding. This never fired in the wild
    only because both live claims are `watch` rows with a blank price — the
    first `claim` row, which requires a price, would have blocked every later
    commit touching any data table.
    """
    import io

    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row())
    head_copy = pd.read_csv(io.StringIO(path.read_text()), dtype=str)
    staged = claims.load_claims(path)
    assert isinstance(head_copy.loc[0, "price"], str)     # text on one side
    assert isinstance(staged.loc[0, "price"], float)      # float on the other
    claims.check_append_only(head_copy, staged)           # must not raise


def test_append_only_still_catches_a_price_edit_across_types(tmp_path):
    """The type fix must not blind the guard to a genuine rewrite."""
    import io

    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row())
    head_copy = pd.read_csv(io.StringIO(path.read_text()), dtype=str)
    edited = claims.load_claims(path)
    edited.loc[0, "price"] = 999.0
    with pytest.raises(ValueError, match="Point-in-time facts are permanent"):
        claims.check_append_only(head_copy, edited)


def test_type_tolerance_does_not_blind_text_columns():
    """Only genuinely numeric columns may be compared as numbers.

    The first version of this fix coerced every field, so a frozen text cell
    edited from '1e3' to '1000' — or a ticker from ' 700 ' to '700' — read as
    unchanged. A guard that quietly stops guarding is worse than a noisy one.
    """
    assert claims.same_value("285.0", 285.0, numeric=True)      # the real fix
    assert not claims.same_value("285.0", 295.0, numeric=True)  # still catches edits
    for a, b in [("1e3", "1000"), (" 285 ", "285"), ("0", "-0")]:
        assert not claims.same_value(a, b), f"{a!r} vs {b!r} must not be equal"


def test_follow_through_counts_watch_rows_too(tmp_path, monkeypatch):
    """A watch row is a written intention with a price on it.

    Counting claims only hid the clearest case in the real store: c0001 said
    "re-enter near $1,232" in June and the trade landed in August. `update`
    rows stay out — they state no intention, so there is nothing to act on.
    """
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    _tx(tmp_path / "data", [{"trade_id": "T1", "trade_date": "2026-08-11"}])
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row(kind="watch", price=None,
                                            conviction=None, wrong_if=None,
                                            date="2026-06-09"))
    claims.add_claim(path=path, **_update(date="2026-06-10"))
    claims.link_trades("c0001", "T1", path=path)

    out = claims.follow_through(path).set_index("id")
    assert "c0001" in out.index, "a watch row must be counted"
    assert "c0002" not in out.index, "an update states no intention"
    assert bool(out.at["c0001", "happened"]) is True
    assert out.at["c0001", "days_to_act"] == 63
    assert out.at["c0001", "kind"] == "watch"     # readable apart, not hidden


def _setups(tmp_path):
    return tmp_path / "claim_setups.csv"


def test_a_claim_can_have_several_reasons(tmp_path):
    """The whole reason this is a table: one bet, several reasons at once."""
    cp, sp = _store(tmp_path), _setups(tmp_path)
    claims.add_claim(path=cp, **_good_row())
    claims.add_setups("c0001", ["trendline", "fundamental", "research-call"],
                      path=sp, claims_path=cp)
    assert claims.setups_for("c0001", sp) == ["fundamental", "research-call",
                                              "trendline"]


def test_bad_setup_names_its_vocab_file(tmp_path):
    cp, sp = _store(tmp_path), _setups(tmp_path)
    claims.add_claim(path=cp, **_good_row())
    with pytest.raises(pa.errors.SchemaErrors, match="ops/vocab/setup"):
        claims.add_setups("c0001", "cup and handle", path=sp, claims_path=cp)


def test_setup_for_a_claim_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(ValueError, match="must belong to a real claim"):
        claims.add_setups("c9999", "trendline", path=_setups(tmp_path),
                          claims_path=_store(tmp_path))


def test_the_same_setup_cannot_be_listed_twice(tmp_path):
    cp, sp = _store(tmp_path), _setups(tmp_path)
    claims.add_claim(path=cp, **_good_row())
    claims.add_setups("c0001", "trendline", path=sp, claims_path=cp)
    with pytest.raises(ValueError, match="already lists"):
        claims.add_setups("c0001", "trendline", path=sp, claims_path=cp)


def test_a_claim_needs_no_setup_at_all(tmp_path):
    """A bet with no clean pattern records none, rather than being forced into
    a category that would flatter the record."""
    cp = _store(tmp_path)
    claims.add_claim(path=cp, **_good_row())
    assert claims.setups_for("c0001", _setups(tmp_path)) == []


def test_scoreboard_slices_by_setup(tmp_path):
    """A claim with two reasons counts under BOTH — 'when a trendline was one
    of my reasons, how often was I right?'"""
    cp, sp = _store(tmp_path), _setups(tmp_path)
    a = claims.add_claim(path=cp, **_good_row())
    b = claims.add_claim(path=cp, **_good_row(ticker="ASML"))
    claims.add_setups(a, ["trendline", "fundamental"], path=sp, claims_path=cp)
    claims.add_setups(b, ["trendline"], path=sp, claims_path=cp)
    claims.resolve_claim(a, "right", path=cp)
    claims.resolve_claim(b, "wrong", path=cp)

    by_setup = claims.scoreboard(cp, sp).query("cut == 'setup'").set_index("value")
    assert by_setup.at["trendline", "n"] == 2          # counted in both claims
    assert by_setup.at["trendline", "hit_rate"] == 50.0
    assert by_setup.at["fundamental", "hit_rate"] == 100.0


def test_setup_link_to_a_missing_claim_is_caught(tmp_path):
    """The foreign key the commit guard calls."""
    cp, sp = _store(tmp_path), _setups(tmp_path)
    claims.add_claim(path=cp, **_good_row())
    claims.add_setups("c0001", "trendline", path=sp, claims_path=cp)
    sp.write_text(sp.read_text().replace("c0001", "c9999"))
    with pytest.raises(ValueError, match="do not exist"):
        claims.check_setup_claims(sp, cp)


def test_position_size_is_recorded_without_being_typed(tmp_path):
    """"Was I right" and "did it matter" are different questions.

    Regression: nav_pct was computed before instrument_id was resolved, so it
    read a blank and silently stored nothing — worse than refusing.
    """
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row(ticker="IWM", instrument_id=None))
    got = claims.load_claims(path).iloc[0]["nav_pct"]
    assert got == got, "nav_pct must not be NaN — the size was silently lost"
    assert 0 < got <= 100


def test_size_is_none_for_something_not_held(tmp_path):
    """A watch row on a name he does not own has no size, and that is honest."""
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row(ticker="BG", kind="watch",
                                            price=None, conviction=None,
                                            wrong_if=None))
    got = claims.load_claims(path).iloc[0]["nav_pct"]
    assert got != got, "not held means blank, not zero"


def test_hold_and_watch_have_nothing_to_happen(tmp_path):
    """Yaron, 2026-08-15: "I have the position already, it's about ADDING."

    Owning something does not mean there is nothing to do. Only the stated
    intent can say that, so `hold` and `watch` leave `happened` blank while every
    other intent is measured.
    """
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row(ticker="IWM", intent="hold"))
    assert claims.follow_through(path).iloc[0]["happened"] is None


def test_an_add_on_a_position_already_held_is_still_counted(tmp_path, monkeypatch):
    """The ASML case: he held it, planned to add, and did. The row must record
    that it happened.

    The premise is now stated rather than borrowed. Until 2026-09-27 this
    passed because `already_held` came from `nav_pct`, which read the REAL
    positions.csv — so a tmp_path test silently depended on IWM still being
    held in production. Selling it would have turned this red for no reason.
    """
    from src import instruments  # noqa: F401  — see the note above _ft_tx
    path = _store(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    pd.DataFrame([{"trade_id": "T0", "trade_date": "2026-01-02",
                   "symbol": "IWM", "quantity": "10"}]).to_csv(
        tmp_path / "data" / "transactions.csv", index=False)
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)

    claims.add_claim(path=path, **_good_row(ticker="IWM", intent="add"))
    row = claims.follow_through(path).iloc[0]
    assert row["already_held"], "the 2026-01-02 fill predates the claim"
    # not None (blank) and falsy — a plan that is counted, and did not happen
    assert row["happened"] is not None and not row["happened"]


def test_a_missing_intent_never_removes_a_row_from_the_count(tmp_path):
    """An unlabelled claim falls back to 'there was a plan'. The safe direction:
    a forgotten label must not quietly improve the score."""
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row(ticker="IWM", intent=None))
    happened = claims.follow_through(path).iloc[0]["happened"]
    assert happened is not None and not happened


def test_a_plan_that_did_not_happen_is_still_counted(tmp_path):
    """The metric must keep working for the case it exists for: a written plan
    on a name he does not own, never executed."""
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row(ticker="BG", kind="watch",
                                            price=None, conviction=None,
                                            wrong_if=None))
    row = claims.follow_through(path).iloc[0]
    assert not row["already_held"]
    assert row["happened"] is False or row["happened"] == False


def test_reconstructed_rows_are_flagged(tmp_path):
    """recorded > date means it was written after the fact — backfill must
    never read as foresight."""
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row(date="2026-06-09",
                                            recorded="2026-08-08"))
    assert claims.follow_through(path).iloc[0]["reconstructed"]


def test_why_not_separates_patience_from_forgetting(tmp_path):
    """The value that earns the column: a trigger that has not hit is not a miss.

    Without it, "I am waiting for my price" and "I forgot" are the same row.
    """
    path = _store(tmp_path)
    a = claims.add_claim(path=path, **_good_row(ticker="BG", kind="watch",
                                                price=None, conviction=None,
                                                wrong_if=None, intent="open"))
    b = claims.add_claim(path=path, **_good_row(ticker="MOO", kind="watch",
                                                price=None, conviction=None,
                                                wrong_if=None, intent="open"))
    claims.record_why_not(a, "still-waiting", path=path)
    claims.record_why_not(b, "forgot", path=path)

    ft = claims.follow_through(path).set_index("id")
    assert not ft.at[a, "happened"] and not ft.at[b, "happened"]
    assert ft.at[a, "why_not"] == "still-waiting"   # nothing went wrong
    assert ft.at[b, "why_not"] == "forgot"          # something did


def test_why_not_cannot_be_softened_later(tmp_path):
    path = _store(tmp_path)
    cid = claims.add_claim(path=path, **_good_row(intent="open"))
    claims.record_why_not(cid, "forgot", path=path)
    with pytest.raises(ValueError, match="already records why"):
        claims.record_why_not(cid, "still-waiting", path=path)


def test_bad_why_not_names_its_vocab_file(tmp_path):
    path = _store(tmp_path)
    cid = claims.add_claim(path=path, **_good_row(intent="open"))
    with pytest.raises(pa.errors.SchemaErrors, match="ops/vocab/why_not"):
        claims.record_why_not(cid, "couldn't be bothered", path=path)


def test_why_not_is_separate_from_the_verdict(tmp_path):
    """A good idea he never bought: the thesis was right, the trade did not
    happen. Two questions, two answers."""
    path = _store(tmp_path)
    cid = claims.add_claim(path=path, **_good_row(intent="open"))
    claims.record_why_not(cid, "no-capital", path=path)
    claims.resolve_claim(cid, "right", "it ran without me", path=path)
    r = claims.load_claims(path).iloc[0]
    assert r["why_not"] == "no-capital" and r["outcome"] == "right"


def test_a_trade_in_the_wrong_instrument_is_refused(tmp_path, monkeypatch):
    """An ASML fill must not count as executing an IWM plan.

    Found by review 2026-08-15: check_trade_ids verified only that the id
    existed, so any trade could be linked to any claim and follow_through()
    would report it as done.
    """
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    _tx(tmp_path / "data", [{"trade_id": "T1", "trade_date": "2026-08-11",
                             "symbol": "ASML"}])
    path = _store(tmp_path)
    cid = claims.add_claim(path=path, **_good_row(ticker="IWM"))
    with pytest.raises(ValueError, match="wrong instrument"):
        claims.link_trades(cid, "T1", path=path)


def test_a_fill_in_the_other_broker_lot_is_accepted(tmp_path, monkeypatch):
    """ASML_USD and ASML_EUR are two lots of ONE instrument. A plan written
    against one is legitimately executed in the other — that is exactly what
    happened on 2026-08-11, and the check must not break it."""
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    _tx(tmp_path / "data", [{"trade_id": "T1", "trade_date": "2026-08-11",
                             "symbol": "ASML"}])
    path = _store(tmp_path)
    cid = claims.add_claim(path=path, **_good_row(ticker="ASML_USD"))
    claims.link_trades(cid, "T1", path=path)          # must not raise
    assert claims.load_claims(path).iloc[0]["trade_ids"] == "T1"


def test_append_only_blocks_rewritten_history(tmp_path):
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row())
    old = claims.load_claims(path)
    edited = old.copy()
    edited.loc[0, "wrong_if"] = "something easier to be right about"
    with pytest.raises(ValueError, match="wrong_if changed"):
        claims.check_append_only(old, edited)


def test_a_verdict_can_be_more_than_right_or_wrong(tmp_path):
    """Yaron, 2026-08-15: he decides right or wrong when he closes, and a
    thesis is often neither. The vocabulary is his to extend."""
    path = _store(tmp_path)
    cid = claims.add_claim(path=path, **_good_row())
    claims.resolve_claim(cid, "partly-right", "the macro call landed, the timing did not", path=path)
    assert claims.load_claims(path).iloc[0]["outcome"] == "partly-right"


def test_append_only_blocks_rewritten_verdict(tmp_path):
    path = _store(tmp_path)
    cid = claims.add_claim(path=path, **_good_row())
    claims.resolve_claim(cid, "wrong", path=path)
    old = claims.load_claims(path)
    edited = old.copy()
    edited.loc[0, "outcome"] = "right"
    with pytest.raises(ValueError, match="verdict cannot be rewritten"):
        claims.check_append_only(old, edited)


# ─── Whole-file validation — the hand-edit drift catcher ───

def test_load_empty_or_missing_store_is_valid(tmp_path):
    df = claims.load_claims(_store(tmp_path))
    assert df.empty
    assert list(df.columns) == claims.COLUMNS


def test_hand_edited_bad_file_rejected(tmp_path):
    """A file Excel or a stray edit broke must fail loudly on load."""
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row())
    text = path.read_text().replace("high", "extreme")  # corrupt conviction
    path.write_text(text)
    with pytest.raises(pa.errors.SchemaErrors, match="ops/vocab/conviction"):
        claims.load_claims(path)


def test_duplicate_id_rejected(tmp_path):
    path = _store(tmp_path)
    claims.add_claim(path=path, **_good_row())
    df = pd.read_csv(path, dtype=str)
    dup = pd.concat([df, df], ignore_index=True)
    dup.to_csv(path, index=False)
    with pytest.raises(pa.errors.SchemaErrors):
        claims.load_claims(path)


def test_real_store_validates_if_present():
    """The actual data/claims.csv must always pass its own schema."""
    if not claims.CLAIMS_PATH.exists():
        pytest.skip("claims.csv not created yet")
    claims.load_claims()  # raises on drift


# ─── kind=update: the timeline of a holding ───

def _update(**over) -> dict:
    """A minimal update row: something happened, or someone said something."""
    row = {"date": "2026-07-02", "ticker": "NVDA", "origin": "13d",
           "kind": "update", "claim": "13D flags the robotics angle in Q3"}
    row.update(over)
    return row


def test_update_needs_only_date_source_and_text(tmp_path):
    """
    An update is not a bet, so it needs none of a bet's fields.

    This is what makes the store usable as a running record: reading a
    Fundstrat note is not an act of conviction and must not be forced to
    look like one.
    """
    p = _store(tmp_path)
    cid = claims.add_claim(path=p, **_update())
    row = claims.load_claims(p).iloc[0]
    assert row["kind"] == "update"
    assert row["origin"] == "13d"
    assert pd.isna(row["check_date"]) and pd.isna(row["conviction"])
    assert cid == "c0001"


@pytest.mark.parametrize("field,value", [
    ("conviction", "high"), ("price", 181.5),
    ("wrong_if", "revenue falls"), ("check_date", "2026-12-01"),
])
def test_update_may_not_carry_a_bet(tmp_path, field, value):
    """
    The "two things, not one" rule (Phase 2f, user's decision 2026-08-10).

    If a Fundstrat note changes his mind, that is two acts: record the
    update, then resolve the old claim and write a new one. Allowing an
    update to carry conviction would merge "what I read" with "what I
    decided" into one row, and the question "do these sources actually
    improve my decisions?" could never be answered afterwards.
    """
    p = _store(tmp_path)
    with pytest.raises(pa.errors.SchemaErrors) as e:
        claims.add_claim(path=p, **_update(**{field: value}))
    msg = claims.explain_schema_error(e.value)
    assert "must leave wrong_if, price, conviction and check_date blank" in msg
    assert "resolve_claim()" in msg  # the message names the way out


def test_claim_still_requires_a_check_date(tmp_path):
    """Relaxing check_date for updates must not relax it for bets."""
    p = _store(tmp_path)
    row = _good_row()
    del row["check_date"]
    with pytest.raises(pa.errors.SchemaErrors) as e:
        claims.add_claim(path=p, **row)
    assert "check_date is required on every claim row" in claims.explain_schema_error(e.value)


def test_holding_timeline_is_ordered_and_keeps_each_source(tmp_path):
    """The whole point: one holding's history, dated, with each row's origin."""
    p = _store(tmp_path)
    claims.add_claim(path=p, **_good_row(date="2026-03-01", origin="13d"))
    claims.add_claim(path=p, **_update(date="2026-05-14", origin="fundstrat",
                                       claim="FS comments on Q2"))
    claims.add_claim(path=p, **_update(date="2026-07-02", origin="mine",
                                       claim="I noticed the Shanghai number"))

    hist = claims.load_claims(p)
    hist = hist[hist["instrument_id"] == "NVDA"].sort_values("date")
    assert list(hist["origin"]) == ["13d", "fundstrat", "mine"]
    assert list(hist["kind"]) == ["claim", "update", "update"]


def test_table_level_failure_is_reported_once(tmp_path):
    """
    A whole-table rule fails once per column; Pandera reports it 9 times.

    Nine identical paragraphs teach nothing, and the commit guard is where
    Yaron meets these messages (P2).
    """
    p = _store(tmp_path)
    with pytest.raises(pa.errors.SchemaErrors) as e:
        claims.add_claim(path=p, **_update(conviction="high"))
    msg = claims.explain_schema_error(e.value)
    assert msg.count("must leave wrong_if") == 1, msg


# ─── trade_ids: the join between what he intended and what he did ───

def _tx(tmp_path, rows):
    """A fake transactions.csv. `symbol` defaults to the ticker _good_row uses,
    so the instrument check in check_trade_ids() is actually exercised."""
    rows = [{"symbol": "NVDA", **r} for r in rows]
    p = tmp_path / "transactions.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_trade_ids_is_not_frozen():
    """
    A plan is written before the trade exists.

    If trade_ids were frozen, the link could never be filled in, and
    follow-through — the thing he named as his constraint — would be
    permanently unmeasurable.
    """
    assert "trade_ids" not in claims.FROZEN
    assert "trade_ids" in claims.COLUMNS


def test_link_trades_fills_it_in_after_the_fact(tmp_path, monkeypatch):
    p = _store(tmp_path)
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    _tx(tmp_path / "data", [{"trade_id": "T1", "trade_date": "2026-08-20"}])

    cid = claims.add_claim(path=p, **_good_row(date="2026-08-13"))
    claims.link_trades(cid, "T1", path=p)
    assert claims.load_claims(p).loc[0, "trade_ids"] == "T1"


def test_link_trades_accumulates_fills(tmp_path, monkeypatch):
    """A position built in three buys has three trades against one decision."""
    p = _store(tmp_path)
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    _tx(tmp_path / "data", [{"trade_id": t, "trade_date": "2026-08-20"}
                            for t in ("T1", "T2")])

    cid = claims.add_claim(path=p, **_good_row())
    claims.link_trades(cid, "T1", path=p)
    claims.link_trades(cid, ["T1", "T2"], path=p)      # T1 not duplicated
    assert claims.load_claims(p).loc[0, "trade_ids"] == "T1;T2"


def test_a_trade_that_never_happened_is_rejected(tmp_path, monkeypatch):
    """
    A bogus link would count as executed in the follow-through numbers —
    worse than no link, because it would say he acted when he did not.
    """
    p = _store(tmp_path)
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    _tx(tmp_path / "data", [{"trade_id": "T1", "trade_date": "2026-08-20"}])

    cid = claims.add_claim(path=p, **_good_row())
    with pytest.raises(ValueError, match="not in data/transactions.csv"):
        claims.link_trades(cid, "NOPE", path=p)


def test_follow_through_separates_acted_from_not(tmp_path, monkeypatch):
    """The MCL pattern: written actions that were never executed."""
    p = _store(tmp_path)
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    _tx(tmp_path / "data", [{"trade_id": "T1", "trade_date": "2026-08-20"}])

    acted = claims.add_claim(path=p, **_good_row(date="2026-08-13"))
    claims.add_claim(path=p, **_good_row(date="2026-08-14", ticker="ASML"))
    claims.link_trades(acted, "T1", path=p)

    ft = claims.follow_through(p).set_index("id")
    assert ft.loc[acted, "happened"] is True or ft.loc[acted, "happened"]
    assert ft.loc[acted, "days_to_act"] == 7      # 13 Aug decided, 20 Aug done
    assert not ft.loc["c0002", "happened"]


def test_scoreboard_only_counts_resolved_claims(tmp_path):
    """An unresolved claim is a follow-up he owes, not a draw."""
    p = _store(tmp_path)
    a = claims.add_claim(path=p, **_good_row(conviction="high"))
    claims.add_claim(path=p, **_good_row(conviction="low", ticker="ASML"))
    claims.resolve_claim(a, "right", "played out", path=p)

    s = claims.scoreboard(p)
    conv = s[s["cut"] == "conviction"].set_index("value")
    assert list(conv.index) == ["high"]        # the unresolved 'low' is absent
    assert conv.loc["high", "hit_rate"] == 100.0


# ─── scoreboard: a verdict that is not "right" is not automatically a miss ───

def test_unclear_does_not_read_as_a_miss(tmp_path):
    """`unclear` means "I cannot say", not "I was wrong".

    This is the bug the 2026-09-27 audit found in the live store: one resolved
    claim, outcome `unclear`, and the scoreboard asserted his medium-conviction
    own ideas hit 0.0%. Nothing had ever been judged wrong.
    """
    p = _store(tmp_path)
    a = claims.add_claim(path=p, **_good_row(conviction="med"))
    claims.resolve_claim(a, "unclear", "the window has not run out", path=p)

    conv = claims.scoreboard(p).query("cut == 'conviction'").set_index("value")
    assert conv.loc["med", "judged"] == 0
    assert pd.isna(conv.loc["med", "hit_rate"]), "no verdict is not a 0% verdict"


def test_too_early_leaves_the_denominator(tmp_path):
    p = _store(tmp_path)
    a = claims.add_claim(path=p, **_good_row(conviction="high"))
    b = claims.add_claim(path=p, **_good_row(conviction="high", ticker="ASML"))
    claims.resolve_claim(a, "right", "played out", path=p)
    claims.resolve_claim(b, "too-early", "still waiting", path=p)

    conv = claims.scoreboard(p).query("cut == 'conviction'").set_index("value")
    assert conv.loc["high", "n"] == 2          # both are resolved
    assert conv.loc["high", "judged"] == 1     # only one was actually judged
    assert conv.loc["high", "hit_rate"] == 100.0


def test_partly_right_is_counted_but_not_scored_as_right(tmp_path):
    """Shown in its own column so he can read it; never silently half-credited."""
    p = _store(tmp_path)
    a = claims.add_claim(path=p, **_good_row(conviction="med"))
    claims.resolve_claim(a, "partly-right", "process worked, idea did not", path=p)

    conv = claims.scoreboard(p).query("cut == 'conviction'").set_index("value")
    assert conv.loc["med", "judged"] == 1
    assert conv.loc["med", "partly"] == 1
    assert conv.loc["med", "right"] == 0
    assert conv.loc["med", "hit_rate"] == 0.0


def test_a_resolved_watch_row_reaches_the_scoreboard(tmp_path):
    """Two of the three verdicts in the live store sat on `watch` rows.

    A watch with a verdict is a judgement he made; excluding it hid c0001 and
    c0024 from the only function that scores judgements.
    """
    p = _store(tmp_path)
    w = claims.add_claim(path=p, date="2026-08-13", ticker="XOP", origin="mine",
                         kind="watch", intent="open", claim="watching XOP")
    claims.resolve_claim(w, "right", "bought it and it worked", path=p)

    origin = claims.scoreboard(p).query("cut == 'origin'").set_index("value")
    assert origin.loc["mine", "judged"] == 1
    assert origin.loc["mine", "hit_rate"] == 100.0


def test_a_literal_na_survives_the_round_trip(tmp_path):
    """"N/A" in a cell must not come back as missing.

    pandas treats N/A, NA, NULL, None and n/a as missing by default. A claim
    whose `wrong_if` read "N/A" was written successfully, then failed
    validation on the NEXT read with "wrong_if is required on every claim row"
    — and load_claims() raises, so /show, /note, every view and every commit
    touching any data table would be blocked at once. The only repair would be
    hand-editing an append-only file. Found by audit 2026-09-27.
    """
    p = _store(tmp_path)
    cid = claims.add_claim(path=p, **_good_row(wrong_if="N/A"))
    back = claims.load_claims(p).set_index("id")
    assert back.loc[cid, "wrong_if"] == "N/A"


def test_a_blank_cell_still_reads_as_missing(tmp_path):
    """The counterpart: only the empty string means missing."""
    p = _store(tmp_path)
    cid = claims.add_claim(path=p, **_good_row())
    back = claims.load_claims(p).set_index("id")
    assert pd.isna(back.loc[cid, "outcome"])


# ─── already_held must mean "the position predates the claim" ───

def _ft_tx(tmp_path, rows):
    """A transactions.csv shaped the way follow_through() reads it."""
    p = tmp_path / "transactions.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_opening_a_position_is_not_already_held(tmp_path, monkeypatch):
    """ANET and Samsung read `already_held=True` on `intent=open` rows.

    `nav_pct` is computed from CURRENT positions, so a bet written up after the
    fill came back filled and the flag said he already owned what he was
    opening. Found by audit 2026-09-27.
    """
    # `instruments` binds INSTRUMENTS_PATH from claims.PROJECT_ROOT at IMPORT
    # time, and add_claim() imports it lazily — so it must be loaded before the
    # patch, or the foreign-key check looks for instruments.csv in tmp_path.
    from src import instruments  # noqa: F401

    p = _store(tmp_path)
    tx = _ft_tx(tmp_path, [{"trade_id": "T1", "trade_date": "2026-09-15",
                            "symbol": "ANET", "quantity": "20"}])
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    tx.rename(tmp_path / "data" / "transactions.csv")

    cid = claims.add_claim(path=p, date="2026-09-15", ticker="ANET",
                           origin="mine", kind="claim", intent="open",
                           claim="bought it", wrong_if="the trend breaks",
                           check_date="2026-12-15", price=191.15,
                           conviction="high")
    claims.link_trades(cid, "T1", path=p)
    ft = claims.follow_through(p).set_index("id")
    assert ft.loc[cid, "already_held"] is False or not ft.loc[cid, "already_held"]


def test_exiting_a_position_means_it_was_held(tmp_path, monkeypatch):
    """XLRE and AXP read `already_held=False` on `intent=exit` rows."""
    # `instruments` binds INSTRUMENTS_PATH from claims.PROJECT_ROOT at IMPORT
    # time, and add_claim() imports it lazily — so it must be loaded before the
    # patch, or the foreign-key check looks for instruments.csv in tmp_path.
    from src import instruments  # noqa: F401

    p = _store(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    pd.DataFrame([
        {"trade_id": "T1", "trade_date": "2026-06-09", "symbol": "XLRE",
         "quantity": "30"},
        {"trade_id": "T2", "trade_date": "2026-09-09", "symbol": "XLRE",
         "quantity": "-30"},
    ]).to_csv(tmp_path / "data" / "transactions.csv", index=False)
    monkeypatch.setattr(claims, "PROJECT_ROOT", tmp_path)

    cid = claims.add_claim(path=p, date="2026-09-09", ticker="XLRE",
                           origin="mine", kind="claim", intent="exit",
                           claim="stopped out", wrong_if="it reclaims the line",
                           check_date="2026-12-09", price=43.41,
                           conviction="low")
    claims.link_trades(cid, "T2", path=p)
    ft = claims.follow_through(p).set_index("id")
    assert ft.loc[cid, "already_held"], "he held it before he decided to sell"


# ─── said_by — who said it, as opposed to which firm ───

def test_his_own_idea_may_not_name_someone_else(tmp_path):
    """The only direction enforceable: 16 live rows are origin=fundstrat with
    said_by blank because the column postdates them, and history is never
    rewritten to satisfy a new rule."""
    p = _store(tmp_path)
    with pytest.raises((ValueError, pa.errors.SchemaErrors), match="said_by|mine"):
        claims.add_claim(path=p, **_good_row(origin="mine", said_by="Mark Newton"))


def test_a_bet_from_a_source_may_name_the_person(tmp_path):
    p = _store(tmp_path)
    cid = claims.add_claim(path=p, **_good_row(origin="fundstrat",
                                               said_by="Mark Newton"))
    assert claims.load_claims(p).set_index("id").loc[cid]["said_by"] == "Mark Newton"


def test_unattributed_names_bets_with_nobody_credited(tmp_path):
    """Visible, never blocking — the contract instruments.gaps() has. Until
    these are filled in, "is Newton better for me than Lee?" stays unanswerable."""
    p = _store(tmp_path)
    claims.add_claim(path=p, **_good_row(origin="fundstrat", said_by=""))
    claims.add_claim(path=p, **_good_row(ticker="IWM", origin="mine"))
    got = claims.unattributed(p)
    assert len(got) == 1 and got.iloc[0]["origin"] == "fundstrat"


# ─── decision — one call, several trades ───

def test_a_bet_can_be_tagged_with_the_call_it_came_from(tmp_path):
    """2026-09-15: one decision about technology became three bets, because a
    bet names one instrument. Yaron's call — keep three rows, because QQQ, MAGS
    and XLK are genuinely different bets with different sizes that will have
    different outcomes. The tag is what makes "how did the technology call do?"
    answerable without pretending they were one trade."""
    p = _store(tmp_path)
    cid = claims.add_claim(path=p, **_good_row(decision="technology-sep-26"))
    assert claims.load_claims(p).set_index("id").loc[cid]["decision"] == "technology-sep-26"


def test_a_bet_that_stands_alone_carries_no_call(tmp_path):
    p = _store(tmp_path)
    cid = claims.add_claim(path=p, **_good_row())
    assert pd.isna(claims.load_claims(p).set_index("id").loc[cid]["decision"])


def test_the_label_is_normalised_so_a_typo_cannot_split_a_call(tmp_path):
    """The one real failure mode: "Technology Sep 26" and "technology-sep-26"
    would be two groups, and nothing would say so."""
    p = _store(tmp_path)
    a = claims.add_claim(path=p, **_good_row(decision="  Technology Sep 26 "))
    b = claims.add_claim(path=p, **_good_row(ticker="IWM", decision="technology-sep-26"))
    got = claims.load_claims(p).set_index("id")
    assert got.loc[a]["decision"] == got.loc[b]["decision"] == "technology-sep-26"


def test_decisions_groups_the_bets_one_call_produced(tmp_path):
    p = _store(tmp_path)
    claims.add_claim(path=p, **_good_row(decision="technology-sep-26"))
    claims.add_claim(path=p, **_good_row(ticker="IWM", decision="technology-sep-26"))
    claims.add_claim(path=p, **_good_row(ticker="GDX"))          # its own call
    got = claims.decisions(p)
    assert len(got) == 1
    assert got.iloc[0]["decision"] == "technology-sep-26"
    assert got.iloc[0]["bets"] == 2


def test_decisions_ignores_untagged_bets(tmp_path):
    p = _store(tmp_path)
    claims.add_claim(path=p, **_good_row())
    assert claims.decisions(p).empty


def test_scoreboard_rolls_a_call_up_without_merging_the_bets(tmp_path):
    """Both questions at once: how did the call do, and which of them was best.
    Each bet keeps its own verdict — that is the whole point of not using a
    parent table."""
    p = _store(tmp_path)
    for tk, out in (("NVDA", "right"), ("IWM", "right"), ("GDX", "partly-right")):
        cid = claims.add_claim(path=p, **_good_row(ticker=tk,
                                                  decision="technology-sep-26"))
        claims.resolve_claim(cid, outcome=out, note="judged", path=p)
    sb = claims.scoreboard(p)
    call = sb[(sb.cut == "decision") & (sb.value == "technology-sep-26")]
    assert len(call) == 1
    assert int(call.iloc[0]["n"]) == 3 and int(call.iloc[0]["right"]) == 2
    # and the individual verdicts are still individual
    assert set(claims.load_claims(p)["outcome"]) == {"right", "partly-right"}


def test_an_untagged_bet_never_forms_a_call_in_the_scoreboard(tmp_path):
    p = _store(tmp_path)
    cid = claims.add_claim(path=p, **_good_row())
    claims.resolve_claim(cid, outcome="right", note="judged", path=p)
    sb = claims.scoreboard(p)
    assert "decision" not in set(sb["cut"])


def test_a_call_can_be_tagged_after_the_bet_was_written(tmp_path):
    """c0029/c0030/c0031 already exist, so `decision` cannot be frozen."""
    assert "decision" not in claims.FROZEN


# ─── what a read-only review broke, 2026-09-28 ───

def test_a_missing_label_never_becomes_the_text_NA(tmp_path):
    """pd.NA is not a float, so the isinstance guard let it through and it
    became the literal label '<na>' — three untagged bets then reported as one
    call with a 100% hit rate. The same bug I had already fixed in
    commentary._blank() and failed to carry across."""
    for blank in (pd.NA, pd.NaT, None, float("nan")):
        assert claims._normalise_decision(blank) == ""


def test_a_number_is_not_a_name_for_a_call(tmp_path):
    with pytest.raises(ValueError, match="number|name"):
        claims._normalise_decision(26)


def test_punctuation_cannot_split_a_call():
    """Whitespace collapsed but punctuation did not, so a trailing hyphen, a
    doubled hyphen or a macOS smart dash each made a second call of one — the
    exact failure the normaliser exists to prevent."""
    one = "technology-sep-26"
    for variant in ("technology-sep-26-", "technology--sep--26",
                    "technology—sep—26", "  Technology  Sep  26 ",
                    "​technology-sep-26"):
        assert claims._normalise_decision(variant) == one, variant


def test_a_call_cannot_be_moved_once_it_is_set(tmp_path):
    """`decision` cannot be FROZEN — the rows it exists for predate it, so
    blank -> filled has to stay legal. But scoreboard() GROUPS on it, so
    erasing or moving a tag rewrites a past answer to "how did that call do?".
    Blank -> filled once, then permanent — the shape of the outcome rule."""
    p = _store(tmp_path)
    claims.add_claim(path=p, **_good_row(decision="technology-sep-26"))
    old = claims.load_claims(p)

    moved = old.copy()
    moved.loc[moved["id"] == "c0001", "decision"] = "energy-oct-26"
    with pytest.raises(ValueError, match="decision changed|never be moved|groups on it"):
        claims.check_append_only(old, moved)

    erased = old.copy()
    erased.loc[erased["id"] == "c0001", "decision"] = None
    with pytest.raises(ValueError, match="decision changed|never be moved|groups on it"):
        claims.check_append_only(old, erased)


def test_an_untagged_bet_can_still_be_tagged_later(tmp_path):
    """The whole reason the column is not frozen: c0029-c0031 were written
    before it existed."""
    p = _store(tmp_path)
    claims.add_claim(path=p, **_good_row())
    old = claims.load_claims(p)
    tagged = old.copy()
    tagged.loc[tagged["id"] == "c0001", "decision"] = "technology-sep-26"
    claims.check_append_only(old, tagged)          # must not raise


def test_decisions_counts_only_bets_not_updates(tmp_path):
    """/show reads this number out loud as "three bets came from that call",
    so counting an update would put a false sentence in his mouth."""
    p = _store(tmp_path)
    claims.add_claim(path=p, **_good_row(decision="technology-sep-26"))
    claims.add_claim(path=p, ticker="IWM", origin="mine", kind="update",
                     date="2026-08-08", claim="a note, not a bet",
                     decision="technology-sep-26")
    got = claims.decisions(p)
    assert int(got.iloc[0]["bets"]) == 1


def test_a_whitespace_label_forms_no_call_in_either_view(tmp_path):
    """It used to make a nameless call of one in scoreboard() while
    decisions() filtered it — the two views disagreed that it existed."""
    p = _store(tmp_path)
    cid = claims.add_claim(path=p, **_good_row())
    df = claims.load_claims(p)
    df.loc[df["id"] == cid, "decision"] = "   "
    df.to_csv(p, index=False, quoting=__import__("csv").QUOTE_NONNUMERIC)
    claims.resolve_claim(cid, outcome="right", note="judged", path=p)
    assert "decision" not in set(claims.scoreboard(p)["cut"])
    assert claims.decisions(p).empty


def test_the_scoreboard_reports_both_bounds_not_one_number(tmp_path):
    """Reporting only `right / judged` said "never right" on every line of the
    live store — 0.0 against 4 partly-rights in 8 judged bets.

    Whether a partly-right counts as a hit is his call, so the table gives
    the bracket and refuses to pick a point inside it. The strict rate keeps
    its existing meaning exactly; the second is added beside it.
    """
    p = _store(tmp_path)
    a = claims.add_claim(path=p, **_good_row(conviction="med"))
    claims.resolve_claim(a, "partly-right", "process worked, idea did not", path=p)

    conv = claims.scoreboard(p).query("cut == 'conviction'").set_index("value")
    assert conv.loc["med", "hit_rate"] == 0.0, "strict rate is unchanged"
    assert conv.loc["med", "hit_rate_with_partly"] == 100.0


def test_both_bounds_are_blank_when_nothing_is_judged(tmp_path):
    """No verdict is not a 0% verdict — the rule that already governs
    `hit_rate` must govern the second rate too, or the new column reintroduces
    the bug the first one was fixed for."""
    p = _store(tmp_path)
    a = claims.add_claim(path=p, **_good_row(conviction="med"))
    claims.resolve_claim(a, "unclear", "too early to say", path=p)

    conv = claims.scoreboard(p).query("cut == 'conviction'").set_index("value")
    assert pd.isna(conv.loc["med", "hit_rate"])
    assert pd.isna(conv.loc["med", "hit_rate_with_partly"])


def test_a_short_position_gets_a_size_not_a_blank(tmp_path):
    """`held <= 0` returned None, and a short lot has a NEGATIVE market value
    — so nav_pct was silently blank for exactly the positions whose size
    matters most. The repo carries 296 rows of borrow fees, so this path is
    not hypothetical.

    Size is magnitude. A 5%-of-NAV short is a 5% bet whichever way it points,
    and the direction is already recorded in `intent`.
    """
    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "QQQ", "instrument_id": "QQQ", "market_value": "9000"},
        {"ticker": "CQQQ", "instrument_id": "CQQQ", "market_value": "-1000"},
    ]).to_csv(pos, index=False)

    # denominator is total NET assets: 9000 + (-1000) = 8000
    assert claims.position_pct_of_nav("CQQQ", positions_path=pos) == 12.5
    assert claims.position_pct_of_nav("QQQ", positions_path=pos) == 112.5


def test_a_position_not_held_is_still_none(tmp_path):
    """Sizing something he does not own has no answer, and must not become 0."""
    pos = tmp_path / "positions.csv"
    pd.DataFrame([{"ticker": "QQQ", "instrument_id": "QQQ",
                   "market_value": "9000"}]).to_csv(pos, index=False)
    assert claims.position_pct_of_nav("NVDA", positions_path=pos) is None


def test_position_pct_of_nav_agrees_with_book_nav_when_nothing_is_unclassified(
    tmp_path,
):
    """Two denominators, deliberately different (see both docstrings) — but
    the difference only has a reason to exist when `book.unclassified()` is
    non-empty. When every instrument_id is classified, there is nothing for
    the two totals to disagree ABOUT, so they must not. This bounds the
    divergence to exactly the case that explains it, instead of leaving two
    silently different totals in every other case too."""
    import csv

    from src import book
    from src import clusters as clusters_mod
    from src import instruments as instruments_mod

    pos = tmp_path / "positions.csv"
    pd.DataFrame([
        {"ticker": "AAA", "instrument_id": "AAA", "market_value": "900"},
        {"ticker": "BBB", "instrument_id": "BBB", "market_value": "100"},
    ]).to_csv(pos, index=False)

    inst = tmp_path / "instruments.csv"
    rows = []
    for iid in ("AAA", "BBB"):
        row = {c: "" for c in instruments_mod.COLUMNS}
        row.update({"instrument_id": iid, "name": iid,
                    "instrument_type": "stock", "cluster_id": "Theme",
                    "status": "active", "added": "2026-01-01"})
        rows.append(row)
    pd.DataFrame(rows)[instruments_mod.COLUMNS].to_csv(
        inst, index=False, quoting=csv.QUOTE_NONNUMERIC)

    cl = tmp_path / "clusters.csv"
    pd.DataFrame([{
        "cluster_id": "Theme", "parent": "", "layer": "thematic",
        "in_equity_base": True, "note_ref": "", "status": "active",
        "added": "2026-01-01",
    }])[clusters_mod.COLUMNS].to_csv(cl, index=False,
                                     quoting=csv.QUOTE_NONNUMERIC)

    assert book.unclassified(positions_path=pos, instruments_path=inst).empty

    pct = claims.position_pct_of_nav("AAA", positions_path=pos)
    nav = book.nav("everything", positions_path=pos, instruments_path=inst,
                   clusters_path=cl)
    assert pct == pytest.approx(round(900 / nav * 100, 2))


# ─── load_setups — called by the commit guard, and untested until now ─────
#
# `ops/hooks/pre-commit:197` validates every staged claim_setups.csv through
# this. Untested until the audit of 2026-09-29.


def test_load_setups_treats_a_missing_file_as_empty(tmp_path):
    """A repo without the file yet must not crash the commit guard."""
    out = claims.load_setups(tmp_path / "nope.csv")
    assert out.empty
    assert list(out.columns) == claims.SETUP_COLUMNS


def test_load_setups_treats_an_empty_file_as_empty(tmp_path):
    """Zero bytes is different from missing, and both mean no setups."""
    p = tmp_path / "claim_setups.csv"
    p.write_text("")
    assert claims.load_setups(p).empty


def test_load_setups_reads_real_rows(tmp_path):
    p = tmp_path / "claim_setups.csv"
    p.write_text("claim_id,setup,added\nc0001,trendline,2026-08-08\n")
    out = claims.load_setups(p)
    assert len(out) == 1
    assert out.iloc[0]["setup"] == "trendline"


def test_load_setups_refuses_a_setup_outside_the_vocabulary(tmp_path):
    """The reason this runs in the commit guard at all. A typo'd setup would
    split a category silently — `scoreboard()` would report "trendlin" and
    "trendline" as two different kinds of bet."""
    p = tmp_path / "claim_setups.csv"
    p.write_text("claim_id,setup,added\nc0001,trendlin,2026-08-08\n")
    with pytest.raises(Exception):
        claims.load_setups(p)


# ─── "already held" must not lie about positions older than the log ──────


def test_a_position_with_no_trades_in_the_log_is_not_reported_as_new(tmp_path):
    """IWM, XLF and IMAE — $11,080 of stock — have no row in transactions.csv
    at all, because he bought them before the broker's rolling window begins.
    `_held_before()` returned a plain False for them, so follow_through()
    reported he opened three long-held positions on the day he wrote them up.

    The column exists to prevent exactly this. Its own docstring quotes him
    about IWM: "It's a trade from a long time ago. I recorded it yesterday
    because that is when we started to implement all the changes."

    His call, 2026-10-01: answer "held before the log" — the same wording
    ibkr_sync.unreconciled_shares() already uses for these same names, so the
    two halves of the project stop disagreeing. It states the one thing that
    is certainly true and never guesses a date.
    """
    pos = tmp_path / "positions.csv"
    pd.DataFrame([{"ticker": "IWM", "instrument_id": "IWM",
                   "market_value": "7000.50"}]).to_csv(pos, index=False)

    assert claims._held_before({}, "IWM", "2026-08-15",
                               positions_path=pos) == "before-the-log"


def test_an_instrument_that_is_not_held_and_never_traded_is_still_no(tmp_path):
    """Never owned is not the same as owned longer than the records go."""
    pos = tmp_path / "positions.csv"
    pd.DataFrame([{"ticker": "IWM", "instrument_id": "IWM",
                   "market_value": "7000.50"}]).to_csv(pos, index=False)
    assert claims._held_before({}, "NVDA", "2026-08-15", positions_path=pos) is False


def test_a_real_earlier_fill_still_answers_plainly_yes(tmp_path):
    """The ordinary case must not change: a fill before the claim date is a
    plain True, and the strictly-before rule still holds."""
    fills = {"QQQ": [("2026-07-01", 10.0)]}
    assert claims._held_before(fills, "QQQ", "2026-08-15") is True
    # bought the same day he wrote it: not a position he already held
    assert claims._held_before({"QQQ": [("2026-08-15", 10.0)]}, "QQQ", "2026-08-15") is False
