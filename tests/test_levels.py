"""Tests for the price-level store.

Run: venv/bin/python -m pytest tests/test_levels.py -q

Each rule gets one test; the whole-file test at the bottom is the drift catcher
for hand-edits. Writes go to tmp_path — the real store is never touched.

The held-position tests build their own positions.csv rather than reading the
live one, so selling a real holding can never turn them red.
"""

from __future__ import annotations

import csv

import pandas as pd
import pandera.pandas as pa
import pytest

from src import claims, levels


def _claim_row(**over) -> dict:
    """A fully valid claim, for the levels that have to point at one."""
    row = {
        "date": "2026-08-08", "ticker": "NVDA", "origin": "mine",
        "kind": "claim",
        "claim": "AI capex holds through 2027",
        "wrong_if": "two consecutive quarters of declining data-center revenue",
        "check_date": "2026-11-08", "price": 181.50, "conviction": "high",
    }
    row.update(over)
    return row


def _good_row(**over) -> dict:
    """A fully valid soft level; tests break exactly one field at a time."""
    row = {
        "date": "2026-08-14",
        "instrument_id": "IWM",
        "level": 285,
        "trigger_when": "at_or_below",
        "kind": "soft",
        "action": "re_evaluate",
        "instruction": "look again at the inflation thesis",
        "note_ref": "notes/positions/IWM.md",
    }
    row.update(over)
    return row


def _store(tmp_path):
    """A path that does not exist — load_levels treats that as an empty store."""
    return tmp_path / "levels.csv"


def _positions(tmp_path, rows, currency="USD"):
    """A minimal positions.csv, so held-vs-not is deterministic.

    Rows may carry a 5th element for the price currency; otherwise every lot is
    priced in `currency`.
    """
    p = tmp_path / "positions.csv"
    cols = ["ticker", "instrument_id", "shares", "last_price", "currency"]
    prepared = []
    for r in rows:
        if isinstance(r, dict):
            prepared.append({"currency": currency, **r})
        else:
            prepared.append(dict(zip(cols, r)) | (
                {} if len(r) == 5 else {"currency": currency}))
    pd.DataFrame(prepared, columns=cols).to_csv(
        p, index=False, quoting=csv.QUOTE_NONNUMERIC
    )
    return p


# ─── The happy paths ───

def test_add_returns_first_id(tmp_path):
    assert levels.add_level(path=_store(tmp_path), **_good_row()) == "l0001"


def test_ids_increment(tmp_path):
    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row())
    assert levels.add_level(path=store, **_good_row(level=270)) == "l0002"


def test_recorded_is_stamped_when_absent(tmp_path):
    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row())
    assert levels.load_levels(store).loc[0, "recorded"]


def test_load_empty_or_missing_store_is_valid(tmp_path):
    assert list(levels.load_levels(_store(tmp_path)).columns) == levels.COLUMNS


# ─── Required fields ───

@pytest.mark.parametrize(
    "field",
    ["date", "instrument_id", "level", "trigger_when", "kind", "action", "instruction"],
)
def test_required_field_missing_is_rejected(tmp_path, field):
    with pytest.raises((pa.errors.SchemaErrors, ValueError, TypeError)):
        levels.add_level(path=_store(tmp_path), **_good_row(**{field: None}))


def test_empty_instruction_rejected(tmp_path):
    """The action is countable; the sentence is why. Both are required."""
    with pytest.raises(pa.errors.SchemaErrors):
        levels.add_level(path=_store(tmp_path), **_good_row(instruction=""))


# ─── Vocabularies — a rejection must name the file to extend ───

@pytest.mark.parametrize(
    "field,bad,vocab_file",
    [
        ("trigger_when", "below", "trigger_when"),
        ("kind", "stop", "level_kind"),
        ("action", "sell", "level_action"),
    ],
)
def test_bad_vocab_names_its_file(tmp_path, field, bad, vocab_file):
    with pytest.raises(pa.errors.SchemaErrors, match=f"ops/vocab/{vocab_file}"):
        levels.add_level(path=_store(tmp_path), **_good_row(**{field: bad}))


# ─── The level itself ───

@pytest.mark.parametrize("bad", [0, -5])
def test_non_positive_level_rejected(tmp_path, bad):
    with pytest.raises(pa.errors.SchemaErrors, match="level must be a positive number"):
        levels.add_level(path=_store(tmp_path), **_good_row(level=bad))


def test_non_numeric_level_in_file_raises_loudly(tmp_path):
    """Not coerced: silently NaN-ing '285 USD' would erase it on the next write."""
    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row())
    store.write_text(store.read_text().replace("285.0", '"285 USD"'))
    with pytest.raises(ValueError, match="non-numeric"):
        levels.load_levels(store)


def test_bad_date_rejected(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="real calendar date"):
        levels.add_level(path=_store(tmp_path), **_good_row(date="2026-02-30"))


# ─── hard vs soft ───

def test_hard_requires_broker_verified(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="broker_verified is required"):
        levels.add_level(path=_store(tmp_path),
                         **_good_row(kind="hard", action="sell_all"))


def test_soft_forbids_broker_verified(tmp_path):
    with pytest.raises(pa.errors.SchemaErrors, match="must be blank on a soft row"):
        levels.add_level(path=_store(tmp_path),
                         **_good_row(broker_verified="2026-08-14"))


def test_hard_level_accepted_with_broker_verified(tmp_path):
    assert levels.add_level(
        path=_store(tmp_path),
        **_good_row(kind="hard", action="sell_all", broker_verified="2026-08-14"),
    ) == "l0001"


def test_verify_broker_refuses_a_soft_level(tmp_path):
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row())
    with pytest.raises(ValueError, match="no broker order to verify"):
        levels.verify_broker(lid, path=store)


# ─── The held-position rule (Yaron, 2026-08-14) ───

@pytest.mark.parametrize("action", levels.ACTIONS_NEEDING_POSITION)
def test_position_actions_refused_when_nothing_held(tmp_path, action):
    pos = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                 "shares": "0", "last_price": "303.5"}])
    with pytest.raises(ValueError, match="no shares held"):
        levels.check_held("IWM", action, path=pos)


@pytest.mark.parametrize("action", ["buy", "re_evaluate"])
def test_buy_and_re_evaluate_allowed_with_no_position(tmp_path, action):
    """A buy trigger on a watchlist name is the whole point of allowing these."""
    pos = _positions(tmp_path, [])
    levels.check_held("BG", action, path=pos)      # must not raise


def test_position_action_allowed_when_held(tmp_path):
    pos = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                 "shares": "25", "last_price": "303.5"}])
    levels.check_held("IWM", "trim_half", path=pos)   # must not raise


def test_shares_summed_across_broker_lots(tmp_path):
    """ASML_USD and ASML_EUR are two lots of one instrument — reading one
    would understate the position and wrongly refuse a trim."""
    pos = _positions(tmp_path, [
        {"ticker": "ASML_USD", "instrument_id": "ASML", "shares": "1", "last_price": "1564"},
        {"ticker": "ASML_EUR", "instrument_id": "ASML", "shares": "2", "last_price": "1564"},
    ])
    assert levels.shares_held("ASML", path=pos) == 3.0


def test_add_level_enforces_the_held_rule(tmp_path):
    """The rule must be wired into add_level, not just callable on its own.

    Regression: every earlier test called check_held() directly, so deleting
    the call inside add_level left the suite green.
    """
    sold = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                  "shares": "0", "last_price": "303.5"}])
    with pytest.raises(ValueError, match="no shares held"):
        levels.add_level(path=_store(tmp_path), positions_path=sold,
                         **_good_row(action="trim_half"))


def test_add_level_allows_a_buy_on_an_unheld_name(tmp_path):
    empty = _positions(tmp_path, [])
    assert levels.add_level(path=_store(tmp_path), positions_path=empty,
                            **_good_row(instrument_id="BG", action="buy",
                                        note_ref=None)) == "l0001"


def test_sold_position_still_loads(tmp_path):
    """The rule is write-time only. If it ran on load, selling a holding would
    make the whole file unreadable — the note_ref lesson, applied here.

    The sale is simulated with an explicit positions file, so this proves the
    load path ignores holdings rather than accidentally passing because the
    live positions.csv happens to still hold IWM.
    """
    store = _store(tmp_path)
    held = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                  "shares": "25", "last_price": "303.5"}])
    levels.add_level(path=store, positions_path=held,
                     **_good_row(action="trim_half"))
    sold = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                  "shares": "0", "last_price": "303.5"}])
    df = levels.load_levels(store)              # must not raise
    assert df.loc[0, "action"] == "trim_half"
    # ...and the same file would now refuse a NEW trim level
    with pytest.raises(ValueError, match="no shares held"):
        levels.add_level(path=store, positions_path=sold,
                         **_good_row(action="trim_half", level=270))


def test_orphaned_levels_reports_rather_than_deletes(tmp_path):
    """A trim level survives the position being sold — it is reported, never
    removed, because the sync not having run looks identical to a real close."""
    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row(action="re_evaluate"))
    store.write_text(store.read_text().replace('"re_evaluate"', '"trim_half"'))

    held = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                  "shares": "25", "last_price": "303.5"}])
    assert levels.orphaned_levels(store, held).empty

    sold = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                  "shares": "0", "last_price": "303.5"}])
    orphans = levels.orphaned_levels(store, sold)
    assert len(orphans) == 1
    assert orphans.loc[0, "action"] == "trim_half"
    assert len(levels.load_levels(store)) == 1      # nothing was deleted


# ─── References and foreign keys ───

def test_absolute_note_ref_rejected(tmp_path):
    with pytest.raises(ValueError, match="is absolute"):
        levels.add_level(path=_store(tmp_path), **_good_row(note_ref="/tmp/x.md"))


def test_missing_note_ref_rejected(tmp_path):
    with pytest.raises(ValueError, match="does not point to a file"):
        levels.add_level(path=_store(tmp_path),
                         **_good_row(note_ref="notes/positions/NOPE.md"))


def test_blank_note_ref_allowed(tmp_path):
    assert levels.add_level(path=_store(tmp_path), **_good_row(note_ref=None)) == "l0001"


def test_unknown_instrument_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown instrument"):
        levels.add_level(path=_store(tmp_path), **_good_row(instrument_id="ZZZZ"))


def test_unknown_claim_id_rejected(tmp_path):
    with pytest.raises(ValueError, match="not in data/claims.csv"):
        levels.add_level(path=_store(tmp_path), **_good_row(claim_id="c9999"))


def test_unknown_kwarg_names_the_columns(tmp_path):
    with pytest.raises(ValueError, match="Unknown field"):
        levels.add_level(path=_store(tmp_path), **_good_row(stop_price=100))


# ─── Ending a level ───

def test_end_level_fills_both_fields(tmp_path):
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row())
    levels.end_level(lid, "raised it after the breakout held", path=store)
    row = levels.load_levels(store).iloc[0]
    assert row["ended_date"] and row["ended_reason"]


def test_ended_level_leaves_active_view(tmp_path):
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row())
    levels.end_level(lid, "position closed", path=store)
    assert levels.active_levels(store).empty
    assert len(levels.load_levels(store)) == 1      # the row itself stays


def test_end_level_refuses_an_already_ended_row(tmp_path):
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row())
    levels.end_level(lid, "first", path=store)
    with pytest.raises(ValueError, match="already ended"):
        levels.end_level(lid, "second", path=store)


def test_end_level_requires_a_reason(tmp_path):
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row())
    with pytest.raises(ValueError, match="reason is required"):
        levels.end_level(lid, "   ", path=store)


def test_end_level_unknown_id(tmp_path):
    with pytest.raises(ValueError, match="No level with id"):
        levels.end_level("l9999", "x", path=_store(tmp_path))


def test_end_level_records_the_day_it_really_ended(tmp_path):
    """A stop that filled on the 21st did not stop applying on the 26th.

    Recording the write-up date instead is what forced a hand-edit of the
    guarded store on 2026-09-26 — the exact thing this argument removes.
    """
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row(date="2026-08-14"))
    levels.end_level(lid, "Stop filled at $198.40.", on="2026-08-20", path=store)
    assert levels.load_levels(store).iloc[0]["ended_date"] == "2026-08-20"


def test_end_level_still_defaults_to_today(tmp_path):
    from datetime import date as _date
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row())
    levels.end_level(lid, "position closed", path=store)
    assert levels.load_levels(store).iloc[0]["ended_date"] == _date.today().isoformat()


def test_end_level_refuses_a_date_before_the_level_existed(tmp_path):
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row(date="2026-08-14"))
    with pytest.raises(ValueError, match="before the level"):
        levels.end_level(lid, "x", on="2026-08-13", path=store)


def test_end_level_refuses_a_future_date(tmp_path):
    from datetime import date as _date, timedelta
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row())
    tomorrow = (_date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="has not happened yet"):
        levels.end_level(lid, "x", on=tomorrow, path=store)


def test_end_level_refuses_a_malformed_date(tmp_path):
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row())
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        levels.end_level(lid, "x", on="21/09/2026", path=store)


def test_ended_fields_must_be_paired(tmp_path):
    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row())
    df = levels.load_levels(store)
    df.loc[0, "ended_date"] = "2026-09-01"
    with pytest.raises(pa.errors.SchemaErrors, match="required together"):
        levels.validate(df)


# ─── Append-only ───

def test_append_only_refuses_a_deleted_row(tmp_path):
    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row())
    old = levels.load_levels(store)
    with pytest.raises(ValueError, match="Rows deleted"):
        levels.check_append_only(old, old.iloc[0:0])


def test_append_only_refuses_a_rewritten_level(tmp_path):
    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row())
    old = levels.load_levels(store)
    new = old.copy()
    new.loc[0, "level"] = 999.0
    with pytest.raises(ValueError, match="end it and add a new one"):
        levels.check_append_only(old, new)


def test_append_only_allows_broker_verified_restamp(tmp_path):
    """The one documented exemption: it records when he last LOOKED."""
    store = _store(tmp_path)
    levels.add_level(path=store,
                     **_good_row(kind="hard", action="sell_all",
                                 broker_verified="2026-08-14"))
    old = levels.load_levels(store)
    new = old.copy()
    new.loc[0, "broker_verified"] = "2026-09-30"
    levels.check_append_only(old, new)          # must not raise


def test_append_only_refuses_reopening_an_ended_level(tmp_path):
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row())
    levels.end_level(lid, "done", path=store)
    old = levels.load_levels(store)
    new = old.copy()
    new.loc[0, "ended_date"] = None
    with pytest.raises(ValueError, match="cannot be re-opened"):
        levels.check_append_only(old, new)


def test_append_only_survives_the_commit_hook_path(tmp_path):
    """The guard compares a git HEAD copy (raw text) against a typed frame.

    Regression for the 2026-08-14 review finding: '285.0' != 285.0 reported a
    rewrite that never happened, which would have blocked every later commit
    touching any data table, with --no-verify as the only escape.
    """
    import io

    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row())
    head_copy = pd.read_csv(io.StringIO(store.read_text()), dtype=str)   # as the hook reads HEAD
    staged = levels.load_levels(store)                                   # as the hook reads staged
    assert head_copy.loc[0, "level"] == "285.0"          # string on one side
    assert staged.loc[0, "level"] == 285.0               # float on the other
    levels.check_append_only(head_copy, staged)          # must not raise


def test_append_only_still_catches_a_real_edit_across_types(tmp_path):
    """Fixing the type mismatch must not blind the guard to a genuine change."""
    import io

    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row())
    head_copy = pd.read_csv(io.StringIO(store.read_text()), dtype=str)
    edited = levels.load_levels(store)
    edited.loc[0, "level"] = 295.0
    with pytest.raises(ValueError, match="end it and add a new one"):
        levels.check_append_only(head_copy, edited)


def test_append_only_tolerates_a_new_column(tmp_path):
    """Without this the first schema change would block its own migration."""
    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row())
    old = levels.load_levels(store).drop(columns=["instruction"])
    new = levels.load_levels(store)
    levels.check_append_only(old, new)          # must not raise


# ─── Views ───

def test_breached_reports_a_reached_level_with_the_price_age(tmp_path):
    store = _store(tmp_path)
    pos = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                 "shares": "25", "last_price": "303.5"}])
    levels.add_level(path=store, **_good_row(level=310))
    out = levels.breached_levels(store, pos)
    assert len(out) == 1
    assert out.loc[0, "last_price"] == 303.5
    # the note must carry the real freshness answer, not merely be non-empty
    assert out.loc[0, "price_note"] in ("prices are current",) or \
        "old" in out.loc[0, "price_note"]


def test_dual_lot_prices_refuse_to_guess(tmp_path):
    """ASML is held in USD and EUR at prices 15% apart with no currency column.

    Picking whichever lot came first would compare a USD level against a EUR
    price. The level must land in unchecked_levels() naming both prices.
    """
    store = _store(tmp_path)
    pos = _positions(tmp_path, [
        {"ticker": "ASML_USD", "instrument_id": "ASML", "shares": "0.5", "last_price": "1847.9"},
        {"ticker": "ASML_EUR", "instrument_id": "ASML", "shares": "2", "last_price": "1609.4"},
    ])
    levels.add_level(path=store, positions_path=pos,
                     **_good_row(instrument_id="ASML", level=1599,
                                 action="buy", note_ref=None))
    assert levels.breached_levels(store, pos).empty
    unchecked = levels.unchecked_levels(store, pos)
    assert len(unchecked) == 1
    why = unchecked.loc[0, "why"]
    assert "1,609.40" in why and "1,847.90" in why


def test_one_unpriced_lot_still_refuses_to_guess(tmp_path):
    """An unpriced lot is disagreement, not something to drop.

    Regression: the first version called .dropna() BEFORE counting, so ASML
    with a blank USD lot silently returned the EUR price — the wrong-currency
    answer the check exists to prevent.
    """
    store = _store(tmp_path)
    pos = _positions(tmp_path, [
        {"ticker": "ASML_USD", "instrument_id": "ASML", "shares": "0.5", "last_price": ""},
        {"ticker": "ASML_EUR", "instrument_id": "ASML", "shares": "2", "last_price": "1609.4"},
    ])
    levels.add_level(path=store, positions_path=pos,
                     **_good_row(instrument_id="ASML", level=1599,
                                 action="buy", note_ref=None))
    assert levels.breached_levels(store, pos).empty
    assert "only 1 priced" in levels.unchecked_levels(store, pos).loc[0, "why"]


def test_zero_price_is_not_a_price(tmp_path):
    """IBKR reports 0 for a halted instrument. Treating it as real would mark
    every downside stop breached at once."""
    store = _store(tmp_path)
    pos = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                 "shares": "25", "last_price": "0"}])
    levels.add_level(path=store, **_good_row(level=285))
    assert levels.breached_levels(store, pos).empty
    assert "no usable last_price" in levels.unchecked_levels(store, pos).loc[0, "why"]


def test_zero_byte_positions_file_does_not_crash(tmp_path):
    """A truncated positions.csv must read as empty, not take out every view."""
    store = _store(tmp_path)
    broken = tmp_path / "empty_positions.csv"
    broken.write_text("")
    levels.add_level(path=store, **_good_row())
    assert levels.breached_levels(store, broken).empty
    assert len(levels.unchecked_levels(store, broken)) == 1
    assert levels.shares_held("IWM", broken) == 0.0


def test_unpriced_stop_surfaces_rather_than_vanishing(tmp_path):
    """An empty breach report must never be read as "nothing triggered" when a
    level simply could not be priced."""
    store = _store(tmp_path)
    held = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                  "shares": "25", "last_price": "303.5"}])
    levels.add_level(path=store, positions_path=held,
                     **_good_row(action="trim_half", level=310))
    blank = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                   "shares": "25", "last_price": ""}])
    assert levels.breached_levels(store, blank).empty        # would have been silent
    out = levels.unchecked_levels(store, blank)
    assert len(out) == 1 and "no usable last_price" in out.loc[0, "why"]


def test_level_on_an_unheld_name_is_unchecked_not_dropped(tmp_path):
    store = _store(tmp_path)
    empty = _positions(tmp_path, [])
    levels.add_level(path=store, positions_path=empty,
                     **_good_row(instrument_id="BG", action="buy", note_ref=None))
    out = levels.unchecked_levels(store, empty)
    assert len(out) == 1 and "not in positions.csv" in out.loc[0, "why"]


def test_verify_broker_refuses_an_ended_level(tmp_path):
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row(kind="hard", action="sell_all",
                                                   broker_verified="2026-08-14"))
    levels.end_level(lid, "stopped out", path=store)
    with pytest.raises(ValueError, match="has ended"):
        levels.verify_broker(lid, path=store)


def test_verify_broker_stamps_a_live_hard_level(tmp_path):
    """The documented exemption must actually work, not just refuse wrong cases."""
    store = _store(tmp_path)
    lid = levels.add_level(path=store, **_good_row(kind="hard", action="sell_all",
                                                   broker_verified="2026-08-14"))
    levels.verify_broker(lid, path=store)
    assert levels.load_levels(store).loc[0, "broker_verified"] != "2026-08-14"


def test_unreached_level_is_not_breached(tmp_path):
    store = _store(tmp_path)
    pos = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                 "shares": "25", "last_price": "303.5"}])
    levels.add_level(path=store, **_good_row(level=285))
    assert levels.breached_levels(store, pos).empty


def test_at_or_above_triggers_upward(tmp_path):
    store = _store(tmp_path)
    pos = _positions(tmp_path, [{"ticker": "IWM", "instrument_id": "IWM",
                                 "shares": "25", "last_price": "303.5"}])
    levels.add_level(path=store, **_good_row(level=300, trigger_when="at_or_above"))
    assert len(levels.breached_levels(store, pos)) == 1


def test_stale_hard_levels_measures_age(tmp_path):
    store = _store(tmp_path)
    levels.add_level(path=store, **_good_row(kind="hard", action="sell_all",
                                             broker_verified="2026-08-14"))
    assert levels.stale_hard_levels(30, store, as_of="2026-08-20").empty
    out = levels.stale_hard_levels(30, store, as_of="2026-10-14")
    assert out.loc[0, "age_days"] == 61


def test_views_return_named_columns_when_empty(tmp_path):
    """An empty view still carries its columns, so a caller reading
    result['action'] gets an empty series rather than a KeyError."""
    store = _store(tmp_path)
    empty_pos = _positions(tmp_path, [])
    for out in (levels.breached_levels(store, empty_pos),
                levels.orphaned_levels(store, empty_pos),
                levels.stale_hard_levels(30, store),
                levels.active_levels(store)):
        assert out.empty
        assert "id" in out.columns and "level" in out.columns


# ─── Drift catcher ───

def test_real_store_validates_if_present():
    """The actual data/levels.csv must always pass its own schema."""
    if not levels.LEVELS_PATH.exists():
        pytest.skip("levels.csv not created yet")
    levels.load_levels()


def test_columns_match_the_file_if_present():
    if not levels.LEVELS_PATH.exists():
        pytest.skip("levels.csv not created yet")
    assert list(pd.read_csv(levels.LEVELS_PATH, dtype=str).columns) == levels.COLUMNS


# ─── Currency: a level has no currency, so a non-USD price cannot be judged ───

def test_a_level_on_a_non_usd_lot_is_refused(tmp_path):
    """Samsung is quoted in won; levels.csv has no currency column.

    `005930.KS` marks at 285,500 KRW against a ~$210 share. A stop typed as 190
    dollars sat `at_or_below` 285,500 and could never fire; written
    `at_or_above` it fires the instant it is created. One lot, so the
    disagreeing-lots check never saw it. Found by audit 2026-09-27.
    """
    pos = _positions(tmp_path, [("005930.KS", "005930.KS", 10, 285500.0, "KRW")])
    frame = levels._read_positions(pos)
    frame["last_price"] = pd.to_numeric(frame["last_price"], errors="coerce")
    price, why = levels._price_for("005930.KS", frame)
    assert price is None
    # Wording changed 2026-09-29 when levels gained a currency column. The
    # behaviour this test exists for is unchanged: a USD level must never be
    # compared against a KRW mark. Samsung marks at 285,500 KRW against a
    # ~$210 share, so a $190 stop could never fire.
    assert "KRW" in why and "USD" in why


def test_a_usd_lot_still_prices_normally(tmp_path):
    pos = _positions(tmp_path, [("IWM", "IWM", 10, 285.0)])
    frame = levels._read_positions(pos)
    frame["last_price"] = pd.to_numeric(frame["last_price"], errors="coerce")
    price, why = levels._price_for("IWM", frame)
    assert price == 285.0 and why == ""


def test_a_breached_non_usd_level_is_reported_as_uncheckable(tmp_path):
    """It must surface in unchecked_levels(), never silently in breached."""
    store = _store(tmp_path)
    pos = _positions(tmp_path, [("005930.KS", "005930.KS", 10, 285500.0, "KRW")])
    levels.add_level(path=store, positions_path=pos, date="2026-09-26",
                     instrument_id="005930.KS", level=190,
                     trigger_when="at_or_below", kind="soft",
                     action="re_evaluate", instruction="watch it")
    assert levels.breached_levels(store, pos).empty
    unchecked = levels.unchecked_levels(store, pos)
    assert len(unchecked) == 1
    assert "KRW" in unchecked.iloc[0]["why"]


# ─── A level has a currency ──────────────────────────────────────────────
#
# Added 2026-09-29. Until then a level was a bare number and `_price_for()`
# refused any lot not priced in USD. That was the safe answer to "what does
# this number mean", but it meant l0029 — a HARD stop, live at IBKR, on 40
# shares of IMAE at €102 — could never be evaluated. The end-of-turn hook
# reported "2 level(s) could not be checked" and named neither.


def test_a_euro_level_is_compared_against_a_euro_price(tmp_path):
    """The whole point. €102 against a €103.80 mark is not breached; the same
    numbers read as USD would be nonsense in whichever direction hurts."""
    pos = _positions(tmp_path, [("IMAE", "IMAE", 40, 103.80, "EUR")])
    price, why = levels._price_for("IMAE", pd.read_csv(pos), "EUR")
    assert why == ""
    assert price == 103.80


def test_a_usd_level_still_refuses_a_euro_price(tmp_path):
    """The guard that existed before must survive the new column. Comparing
    a USD number to a EUR mark is the bug, not the feature."""
    pos = _positions(tmp_path, [("IMAE", "IMAE", 40, 103.80, "EUR")])
    price, why = levels._price_for("IMAE", pd.read_csv(pos), "USD")
    assert price is None
    assert "EUR" in why


def test_a_blank_currency_means_usd(tmp_path):
    """30 rows predate the column. They were all written as USD and must keep
    meaning that, or the backfill silently re-points every existing stop."""
    pos = _positions(tmp_path, [("XLF", "XLF", 50, 54.19, "USD")])
    assert levels._price_for("XLF", pd.read_csv(pos), "")[0] == 54.19


def test_disagreeing_lots_are_still_refused_even_with_a_currency(tmp_path):
    """ASML is two lots in two currencies. Naming the level's currency does
    not say WHICH lot it refers to, so it stays unevaluable — for a second,
    different reason than IMAE, and the message must say so."""
    pos = _positions(tmp_path, [("ASML_USD", "ASML", 0.5, 1771.41, "USD"),
                                ("ASML_EUR", "ASML", 2.0, 1537.00, "EUR")])
    price, why = levels._price_for("ASML", pd.read_csv(pos), "EUR")
    assert price is None
    assert "lot" in why.lower()


def test_stale_refs_checks_each_path_in_a_multi_path_cell(tmp_path):
    """`note_ref` may hold several `;`-separated paths. `claims.stale_refs()`
    has always split them with `split_paths`; this one compared the whole cell
    as a single filename, so a row with two paths could never resolve — no
    file is named "a.md;b.md" — and a genuinely missing second note was
    indistinguishable from a healthy pair.
    """
    # Written directly, not via add_level(): check_refs refuses a missing
    # path at write time, which is the point — a ref goes stale AFTER the row
    # exists, when the note it names is archived or renamed.
    p = tmp_path / "levels.csv"
    rows = []
    for lid, ref in (("l0001", "README.md;CLAUDE.md"),
                     ("l0002", "README.md;notes/positions/NO_SUCH_NOTE.md")):
        r = dict.fromkeys(levels.COLUMNS, "")
        r.update({"id": lid, "date": "2026-08-14", "recorded": "2026-08-14",
                  "instrument_id": "IWM", "level": 285.0,
                  "trigger_when": "at_or_below", "kind": "soft",
                  "action": "re_evaluate", "instruction": "look again",
                  "note_ref": ref})
        rows.append(r)
    pd.DataFrame(rows)[levels.COLUMNS].to_csv(p, index=False)

    out = levels.stale_refs(p)
    assert list(out["id"]) == ["l0002"], (
        "only the row with a path that does not exist should be reported"
    )
    assert out.iloc[0]["missing"] == "notes/positions/NO_SUCH_NOTE.md", (
        "report the path that is missing, not the whole cell"
    )


# ─── check_claim_id — called by the commit guard, and untested until now ──
#
# `ops/hooks/pre-commit:347` runs this on every staged levels.csv. An
# untested function inside the only thing standing between bad data and
# permanent history is worth covering (audit, 2026-09-29).


def test_check_claim_id_accepts_a_claim_that_exists(tmp_path):
    p = tmp_path / "claims.csv"
    cid = claims.add_claim(path=p, **_claim_row())
    levels.check_claim_id(cid, p)          # must not raise


def test_check_claim_id_rejects_one_that_does_not(tmp_path):
    """The join between what he planned and what he set himself to watch."""
    p = tmp_path / "claims.csv"
    claims.add_claim(path=p, **_claim_row())
    with pytest.raises(ValueError, match="c9999"):
        levels.check_claim_id("c9999", p)


@pytest.mark.parametrize("blank", ["", "   ", None, float("nan")])
def test_check_claim_id_allows_blank_in_every_form_it_arrives_in(tmp_path, blank):
    """Most levels have no claim, and a blank reaches this function as an
    empty string from CSV, as None from a keyword argument, and as NaN from
    pandas. All three mean the same thing."""
    p = tmp_path / "claims.csv"
    claims.add_claim(path=p, **_claim_row())
    levels.check_claim_id(blank, p)        # must not raise
