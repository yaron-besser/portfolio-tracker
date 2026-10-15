"""Price levels — a number, a direction, and what to do when it is reached.

Design: _archive/design-record.md §5.8. One row per level; `active` is *derived* from a blank
`ended_date`, never stored. A level is never edited: moving a stop is two acts,
`end_level()` then `add_level()`, because raising a stop is itself a decision
and belongs in the record.

Why this is not a column on claims.csv: a claim row can never change but a stop
moves; a holding can have a level and no claim; and one position can carry two
levels at once (a soft trigger and a hard stop).

Why it is not the old data/active_stops.csv: that file overwrote in place, so
the history of his own stop-moving vanished and stale rows went unnoticed —
PWR had stopped out, DGX was not held, IBIT's quantity was wrong.

`hard` means a real working order exists at IBKR; `soft` means the level exists
only here and he acts on it himself. A hard row is a *dated photograph* of a
broker order, never the order — truth lives at the broker, and `broker_verified`
makes the age of the copy visible rather than making the copy correct.

⚠️ Nothing fires. The alert job was archived 2026-08-10 and returns in Phase 5;
this is the record Phase 5 reads.

Usage:
    from src import levels
    levels.add_level(date="2026-08-14", instrument_id="IWM", level=285,
                     trigger_when="at_or_below", kind="soft",
                     action="re_evaluate", instruction="...")
    levels.active_levels()      # what I am watching now
    levels.breached_levels()    # reached, with the price's age stated
    levels.unchecked_levels()   # could NOT be checked, and why — read this
                                # too: an empty breached_levels() alone does
                                # not mean nothing triggered
    levels.orphaned_levels()    # levels on positions no longer held
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from src.claims import (
    PROJECT_ROOT,
    _is_real_date,
    _vocab_check,
    check_refs,
    split_paths,
    same_value,
)

LEVELS_PATH = PROJECT_ROOT / "data" / "levels.csv"
POSITIONS_PATH = PROJECT_ROOT / "data" / "positions.csv"

COLUMNS = [
    "id", "date", "recorded", "instrument_id", "level",
    # What `level` is quoted in. Blank means USD — the 30 rows written before
    # this column existed were all USD and must keep meaning that.
    #
    # Added 2026-09-29 because l0029 could not exist without it: a HARD stop,
    # live at IBKR, on 40 shares of IMAE at €102. `_price_for()` correctly
    # refused to compare a bare number against a euro mark, so the one kind of
    # level that must never be silent was the one that could never be checked.
    "currency",
    "trigger_when", "kind", "action", "instruction",
    # The claim this level came from, when there is one. Blank is normal: a
    # stop can exist without a written bet. When it is filled, it is the join
    # that answers "I wrote a buy trigger at $1,232 — did I ever act on it?"
    "claim_id",
    # hard rows only: when the order was last confirmed to exist at IBKR. The
    # one field re-stamped in place, because it records when he last LOOKED,
    # not a decision (§5.8).
    "broker_verified",
    "ended_date", "ended_reason", "note_ref",
]

# Actions that assume a position already exists — you cannot sell, trim or add
# to nothing (Yaron, 2026-08-14). `buy` and `re_evaluate` stay free so a buy
# trigger or a "look again at $X" works on a watchlist name.
ACTIONS_NEEDING_POSITION = ["sell_all", "trim_half", "add"]


def _schema() -> pa.DataFrameSchema:
    """Built per call so vocab edits apply without a reload."""
    date_check = pa.Check(
        lambda s: s.map(_is_real_date),
        error="dates must be a real calendar date in ISO YYYY-MM-DD form",
    )
    required_iso = pa.Column(str, date_check)
    optional_iso = pa.Column(str, date_check, nullable=True)
    return pa.DataFrameSchema(
        {
            "id": pa.Column(str, unique=True),
            "date": required_iso,
            "recorded": required_iso,
            "instrument_id": pa.Column(str, pa.Check.str_length(min_value=1)),
            "level": pa.Column(
                float,
                pa.Check.gt(0, error="level must be a positive number"),
                coerce=True,
            ),
            # Blank means USD. Not a vocab file: the set of currencies is
            # whatever his broker prices a lot in, and it comes from
            # positions.csv, not from a list somebody has to remember to edit.
            "currency": pa.Column(str, nullable=True),
            "trigger_when": pa.Column(str, _vocab_check("trigger_when")),
            "kind": pa.Column(str, _vocab_check("level_kind")),
            "action": pa.Column(str, _vocab_check("level_action")),
            # Required even though `action` already says what to do: the action
            # is countable, the sentence is why. Dropping it would repeat the
            # mistake the whole claim store exists to avoid.
            "instruction": pa.Column(str, pa.Check.str_length(min_value=1)),
            "claim_id": pa.Column(str, nullable=True),
            # Required on hard, forbidden on soft — carried by the
            # dataframe-level checks below, so the column stays nullable here.
            "broker_verified": optional_iso,
            "ended_date": optional_iso,
            "ended_reason": pa.Column(str, nullable=True),
            # Path *existence* is deliberately not checked here — only at write
            # time, so an archived note cannot brick the whole table.
            "note_ref": pa.Column(str, nullable=True),
        },
        strict=True,
        ordered=True,
        checks=[
            pa.Check(
                lambda df: (df["kind"] != "hard") | df["broker_verified"].notna(),
                error="broker_verified is required on every hard row — a hard "
                      "level asserts an order exists at IBKR, so it must say "
                      "when that was last true",
            ),
            pa.Check(
                lambda df: (df["kind"] != "soft") | df["broker_verified"].isna(),
                error="broker_verified must be blank on a soft row — there is no "
                      "broker order to have verified",
            ),
            pa.Check(
                lambda df: df["ended_date"].isna() == df["ended_reason"].isna(),
                error="ended_date and ended_reason are required together or "
                      "absent together",
            ),
            pa.Check(
                lambda df: df["ended_date"].isna() | (df["ended_date"] >= df["date"]),
                error="ended_date cannot be earlier than date",
            ),
            pa.Check(
                lambda df: df["broker_verified"].isna()
                | (df["broker_verified"] >= df["date"]),
                error="broker_verified cannot be earlier than date",
            ),
        ],
    )


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Validate a levels DataFrame. Raises SchemaErrors naming every failing
    row and field (P2 — a rejection must teach the fix)."""
    return _schema().validate(df, lazy=True)


def load_levels(path: Path = LEVELS_PATH) -> pd.DataFrame:
    """Read and validate the whole table. An empty/missing file is a valid
    empty table, not an error — that is also what the commit guard sees for
    HEAD the first time levels.csv is committed.

    Note what is NOT checked here: whether the instrument is still held. If it
    were, selling a position would make the file unreadable — the same reason
    note_ref existence is a write-time check only.
    """
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     na_values=[""])
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    # Loud, not coerced: errors="coerce" would silently turn "285 USD" into NaN
    # and the next rewrite would erase the original text (P2).
    try:
        df["level"] = pd.to_numeric(df["level"])
    except (ValueError, TypeError) as e:
        raise ValueError(f"{path}: level column has a non-numeric value — {e}") from None
    return validate(df)


def _write(df: pd.DataFrame, path: Path) -> None:
    """Quote every text field so '#', ';' or a newline can never corrupt a row."""
    df.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def _next_id(df: pd.DataFrame) -> str:
    """The next lNNNN id: one above the highest that parses as a number."""
    nums = pd.to_numeric(df["id"].str.removeprefix("l"), errors="coerce").dropna()
    next_num = int(nums.max()) + 1 if len(nums) else 1
    return f"l{next_num:04d}"


def _read_positions(path: Path) -> pd.DataFrame:
    """positions.csv, or an empty frame. A zero-byte file is empty, not an
    error — read_csv raises EmptyDataError on one, which would take out every
    view at once (load_levels already guards its own file the same way)."""
    empty = pd.DataFrame(columns=["instrument_id", "shares", "last_price"])
    # `currency` is required for the check below but optional in the file:
    # a positions.csv written before 2026-09-27 has no such column, and a
    # missing column must read as "unknown", never as "USD".
    if not path.exists() or path.stat().st_size == 0:
        return empty
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for col in empty.columns:
        if col not in df.columns:
            return empty
    return df


def shares_held(instrument_id: str, path: Path = POSITIONS_PATH) -> float:
    """Total shares across every broker lot of one instrument.

    Summed, not looked up: ASML_USD and ASML_EUR are two rows in positions.csv
    and both are instrument_id ASML, so reading one lot would understate it.
    """
    pos = _read_positions(path)
    lots = pos[pos["instrument_id"] == instrument_id]
    if lots.empty:
        return 0.0
    return float(pd.to_numeric(lots["shares"], errors="coerce").fillna(0.0).sum())


def check_held(instrument_id: str, action: str, path: Path = POSITIONS_PATH) -> None:
    """Refuse a sell/trim/add level on something not held. Write time only.

    Enforced here rather than in the schema on purpose: if this ran on load,
    the day a position is sold the whole file would stop loading. A level whose
    position later disappears is surfaced by orphaned_levels() instead — loudly,
    never tidied away, because silent cleanup is exactly how active_stops.csv
    came to describe positions that no longer existed.
    """
    if action not in ACTIONS_NEEDING_POSITION:
        return
    if shares_held(instrument_id, path) <= 0:
        raise ValueError(
            f"Cannot set a {action!r} level on {instrument_id} — no shares held.\n"
            f"  Actions {ACTIONS_NEEDING_POSITION} need an existing position; "
            f"use action='buy' for an entry trigger, or 're_evaluate' to just "
            f"watch a level."
        )


def check_claim_id(claim_id: str | float | None, path: Path | None = None) -> None:
    """A linked claim must exist. Blank is allowed — most levels have no claim."""
    if claim_id is None or (isinstance(claim_id, float) and pd.isna(claim_id)):
        return
    if not str(claim_id).strip():
        return
    from src import claims

    known = set(claims.load_claims(path or claims.CLAIMS_PATH)["id"])
    if str(claim_id) not in known:
        raise ValueError(
            f"claim_id {str(claim_id)!r} is not in data/claims.csv.\n"
            f"  A link to a claim that does not exist would break the join "
            f"between what he planned and what he watched."
        )


def add_level(path: Path = LEVELS_PATH, positions_path: Path = POSITIONS_PATH,
              **fields) -> str:
    """Validate one new row and append it. Returns the new id.

    The whole file (existing rows + the new one) is validated before a single
    byte is written, so a bad row can never land (§5.5).
    """
    existing = load_levels(path)
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"Unknown field(s) {sorted(unknown)}. Columns are: {COLUMNS}")
    row = {c: fields.get(c) for c in COLUMNS}
    row["id"] = _next_id(existing)
    row["recorded"] = row["recorded"] or date.today().isoformat()
    if row["level"] is not None:
        row["level"] = float(row["level"])
    check_refs(row["note_ref"], "note_ref")
    check_claim_id(row["claim_id"])
    # The instrument must be known, and a sell/trim/add level must have
    # something to sell. Imported locally because instruments.py reads the
    # claims vocab loader — the other direction.
    from src import instruments

    instruments.check_known([row["instrument_id"]])
    check_held(row["instrument_id"], row["action"], positions_path)
    candidate = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    validate(candidate)
    _write(candidate, path)
    return row["id"]


def end_level(level_id: str, reason: str, path: Path = LEVELS_PATH,
              on: str | None = None) -> None:
    """Stop a level applying. The reason is his, in his words (P1).

    The row stays. Moving a stop is end + add, which is what preserves the
    answer to "do I honour my stops, or do I keep sliding them down?"

    `on` is the day it really stopped applying, and defaults to today.
    ⚠️ **Pass it whenever the level ended before you wrote it up.** A stop
    fills at the broker the moment price reaches it, which is rarely the day
    anyone records that; stamping today instead makes the record claim a live
    order existed for days after it was gone. That happened on 2026-09-26 —
    XOP's stop filled on the 21st and the row said the 26th — and the fix was
    a hand-edit of a guarded file, which is worse than the bug.
    """
    df = load_levels(path)
    mask = df["id"] == level_id
    if not mask.any():
        raise ValueError(f"No level with id {level_id!r} in {path}")
    if df.loc[mask, "ended_date"].notna().any():
        raise ValueError(f"Level {level_id} has already ended (append-only store)")
    if not reason.strip():
        raise ValueError("A reason is required — a level that ended for no "
                         "recorded reason teaches nothing later.")

    ended = date.today().isoformat() if on is None else str(on).strip()
    if not _is_real_date(ended):
        raise ValueError(f"{ended!r} is not a real calendar date in YYYY-MM-DD "
                         f"form — the store keeps dates one way only.")
    started = str(df.loc[mask, "date"].iloc[0])
    if ended < started:
        raise ValueError(f"Level {level_id} started on {started}; it cannot have "
                         f"ended on {ended}, before the level existed.")
    if ended > date.today().isoformat():
        raise ValueError(f"{ended} has not happened yet — a level cannot end on "
                         f"a future date.")

    df.loc[mask, "ended_date"] = ended
    df.loc[mask, "ended_reason"] = reason
    validate(df)
    _write(df, path)


def verify_broker(level_id: str, path: Path = LEVELS_PATH) -> None:
    """Re-stamp broker_verified after confirming the order at IBKR.

    The one field that may be overwritten (§5.8): it records when he last
    LOOKED, not a decision, so a new row per check would be noise. Everything
    else in FROZEN is permanent.
    """
    df = load_levels(path)
    mask = df["id"] == level_id
    if not mask.any():
        raise ValueError(f"No level with id {level_id!r} in {path}")
    if (df.loc[mask, "kind"] != "hard").any():
        raise ValueError(
            f"Level {level_id} is soft — there is no broker order to verify."
        )
    if df.loc[mask, "ended_date"].notna().any():
        raise ValueError(
            f"Level {level_id} has ended — stamping it would assert a live "
            f"order for a level that no longer applies."
        )
    df.loc[mask, "broker_verified"] = date.today().isoformat()
    validate(df)
    _write(df, path)


# Point-in-time fields: what he set, when, and why. Once written these are
# history and may never change (R1). Only broker_verified (when he last looked)
# and the ended_* pair may be filled in later.
FROZEN = ["date", "recorded", "instrument_id", "level", "currency",
          "trigger_when", "kind", "action", "instruction", "claim_id"]

# The only frozen column holding a number — see claims.same_value().
NUMERIC_FROZEN = {"level"}


def check_append_only(old: pd.DataFrame, new: pd.DataFrame) -> None:
    """Raise if `new` rewrites history rather than appending to it (§5.5).

    Only columns present in BOTH frames are compared, so a column can still be
    added later — without that, the first schema change would be permanently
    blocked by this guard.
    """
    if old.empty:
        return
    old_by_id, new_by_id = old.set_index("id"), new.set_index("id")
    dropped = sorted(set(old_by_id.index) - set(new_by_id.index))
    if dropped:
        raise ValueError(f"Rows deleted from an append-only store: {dropped}")
    comparable = [c for c in FROZEN if c in old_by_id.columns and c in new_by_id.columns]
    for lid in old_by_id.index:
        for col in comparable:
            before, after = old_by_id.at[lid, col], new_by_id.at[lid, col]
            if not same_value(before, after, col in NUMERIC_FROZEN):
                raise ValueError(
                    f"Level {lid}: {col} changed {before!r} -> {after!r}. "
                    f"A level is never edited — end it and add a new one, so "
                    f"the history of moving it survives."
                )
        # Column-guarded like `comparable` above — see claims.check_append_only.
        if "ended_date" not in old_by_id.columns or "ended_date" not in new_by_id.columns:
            continue
        if pd.notna(old_by_id.at[lid, "ended_date"]) and (
            old_by_id.at[lid, "ended_date"] != new_by_id.at[lid, "ended_date"]
        ):
            raise ValueError(f"Level {lid}: an ended level cannot be re-opened.")


# ─── Views: what am I watching, and what has it done ───

def active_levels(path: Path = LEVELS_PATH) -> pd.DataFrame:
    """Levels still applying — the ones with no ended_date."""
    df = load_levels(path)
    return df[df["ended_date"].isna()]


def _price_for(instrument_id: str, pos: pd.DataFrame,
               level_currency: str = "") -> tuple[float | None, str]:
    """One usable price for an instrument, or None and why not.

    `level_currency` is what the level is quoted in; blank means USD.

    Refuses to pick when the broker lots disagree. ASML is held as ASML_USD and
    ASML_EUR — one instrument, two lots, **prices in different currencies**
    (1847.90 and 1609.40 on 2026-08-14) and no currency column to tell them
    apart. Taking whichever row came first would compare a USD level against a
    EUR price and fire a buy trigger ~15% away from where he meant it. Found by
    review 2026-08-14.
    """
    lots = pos[pos["instrument_id"] == instrument_id]
    if lots.empty:
        return None, "not in positions.csv — nothing to price it against"
    raw = lots["last_price"]
    # 0 and negatives are not prices. IBKR reports 0 for a halted instrument or
    # one with no market data, and a 0 would mark every at_or_below stop as
    # breached at once.
    usable = raw[raw.notna() & (raw > 0)]
    if usable.empty:
        return None, "no usable last_price in positions.csv"
    # An unpriced lot must count as disagreement, not be quietly dropped. The
    # first version of this check called .dropna() BEFORE counting, so ASML with
    # a blank USD lot silently returned the EUR price — the exact wrong-currency
    # answer this function exists to refuse. Found by review 2026-08-14.
    if len(usable) < len(lots):
        return None, (f"{len(lots)} broker lots but only {len(usable)} priced — "
                      f"the unpriced one could be the lot this level refers to")
    prices = usable.unique()
    if len(prices) > 1:
        listed = " and ".join(f"{p:,.2f}" for p in sorted(prices))
        return None, (f"{len(lots)} broker lots priced {listed} — cannot tell "
                      f"which one the level refers to")

    # Compare like with like. Comparing a level against a mark quoted in
    # another currency is silently wrong in whichever direction hurts —
    # Samsung marks at 285,500 KRW against a ~$210 share, so a $190 stop could
    # never fire, and the same number written at_or_above fires immediately.
    # The disagreeing-lots check above caught ASML only because it has two
    # lots; a single foreign lot sailed through. Found by audit 2026-09-27.
    #
    # Before 2026-09-29 the only answer was "a level can only mean USD", which
    # was safe and made l0029 — a real stop at IBKR, in euros — permanently
    # uncheckable. Now the level says what it means.
    want = (level_currency or "USD").strip().upper()
    ccy = sorted({str(c).strip().upper() for c in lots.get("currency", [])
                  if str(c).strip()})
    if not ccy:
        return None, ("no currency recorded for this lot — re-run the sync so "
                      "positions.csv says what the price is quoted in")
    if ccy != [want]:
        return None, (f"level is in {want} but the lot is priced in "
                      f"{', '.join(ccy)} — one of the two is wrong")
    return float(prices[0]), ""


def _evaluate(path: Path, positions_path: Path):
    """Every active level, split into (breached, unpriced). Shared by both views."""
    from src import instruments

    df = active_levels(path)
    if df.empty:
        return [], []
    pos = _read_positions(positions_path)
    pos["last_price"] = pd.to_numeric(pos["last_price"], errors="coerce")
    warning = instruments.price_warning() or "prices are current"

    hit_rows, unpriced = [], []
    for _, r in df.iterrows():
        # pd.isna first: a missing cell stringifies to "nan", which would be
        # read as a currency named NAN and refuse every pre-2026-09-29 row.
        cur = r.get("currency", "")
        last, problem = _price_for(r["instrument_id"], pos,
                                   "" if pd.isna(cur) else str(cur))
        base = {"id": r["id"], "instrument_id": r["instrument_id"],
                "level": r["level"], "trigger_when": r["trigger_when"],
                "kind": r["kind"], "action": r["action"],
                "instruction": r["instruction"]}
        if problem:
            unpriced.append({**base, "why": problem})
            continue
        hit = (last <= r["level"] if r["trigger_when"] == "at_or_below"
               else last >= r["level"])
        if hit:
            hit_rows.append({**base, "last_price": last, "price_note": warning})
    return hit_rows, unpriced


def breached_levels(path: Path = LEVELS_PATH,
                    positions_path: Path = POSITIONS_PATH) -> pd.DataFrame:
    """Active levels the current price has reached, with the price's age.

    IBKR is the only price source, so a breach computed from a stale price must
    say so — a number that looks live but is four days old is exactly the
    silent failure P2 forbids. Read `price_note` before acting on a row.

    ⚠️ **This view alone is not "nothing is triggered".** A level that could not
    be priced is not here — it is in `unchecked_levels()`, and an empty result
    from this function while that one has rows means *unknown*, not *safe*.
    """
    cols = ["id", "instrument_id", "level", "trigger_when", "kind", "action", "instruction", "last_price", "price_note"]
    hit, _ = _evaluate(path, positions_path)
    return pd.DataFrame(hit, columns=cols)


def unchecked_levels(path: Path = LEVELS_PATH,
                     positions_path: Path = POSITIONS_PATH) -> pd.DataFrame:
    """Active levels that could NOT be evaluated, and why.

    A buy trigger on a name he does not hold lives here permanently and that is
    correct — there is no position to price. What must never happen is a *stop*
    silently vanishing from `breached_levels()` because the sync wrote a blank
    price: that would read as "nothing triggered" while a stop sat breached.
    """
    cols = ["id", "instrument_id", "level", "trigger_when", "kind", "action", "instruction", "why"]
    _, unpriced = _evaluate(path, positions_path)
    return pd.DataFrame(unpriced, columns=cols)


def stale_hard_levels(max_age_days: int = 30, path: Path = LEVELS_PATH,
                      as_of: str | None = None) -> pd.DataFrame:
    """Hard levels not confirmed at IBKR recently.

    A hard row claims an order exists at the broker. Nothing here can see the
    broker, so the only honest signal is how long it has been since he looked.
    """
    df = active_levels(path)
    cols = ["id", "instrument_id", "level", "action", "broker_verified", "age_days"]
    df = df[df["kind"] == "hard"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    today = date.fromisoformat(as_of or date.today().isoformat())
    rows = []
    for _, r in df.iterrows():
        age = (today - date.fromisoformat(r["broker_verified"])).days
        if age > max_age_days:
            rows.append({"id": r["id"], "instrument_id": r["instrument_id"],
                         "level": r["level"], "action": r["action"],
                         "broker_verified": r["broker_verified"], "age_days": age})
    return pd.DataFrame(rows, columns=cols)


def orphaned_levels(path: Path = LEVELS_PATH,
                    positions_path: Path = POSITIONS_PATH) -> pd.DataFrame:
    """Active sell/trim/add levels on instruments no longer held.

    Never auto-ended. The position may have closed, or the sync may simply not
    have run — and silently deleting a level on a guess is how the old stops
    file came to describe positions that did not exist. He ends it, with a
    reason, when he knows which it was.
    """
    df = active_levels(path)
    cols = ["id", "instrument_id", "level", "action", "instruction"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    rows = [
        {"id": r["id"], "instrument_id": r["instrument_id"], "level": r["level"],
         "action": r["action"], "instruction": r["instruction"]}
        for _, r in df.iterrows()
        if r["action"] in ACTIONS_NEEDING_POSITION
        and shares_held(r["instrument_id"], positions_path) <= 0
    ]
    return pd.DataFrame(rows, columns=cols)


def stale_refs(path: Path = LEVELS_PATH) -> pd.DataFrame:
    """Rows whose note_ref no longer resolves — usually an archived note.
    Visible, never blocking (P2): the table keeps working."""
    df = load_levels(path)
    bad = []
    for _, r in df.iterrows():
        # `note_ref` may hold several `;`-separated paths. Comparing the whole
        # cell as one filename meant a two-path row could never resolve — no
        # file is named "a.md;b.md" — so a healthy pair and a genuinely
        # missing note reported identically. claims.stale_refs() has always
        # split them; this one did not. Found by audit 2026-09-29.
        missing = [q for q in split_paths(r["note_ref"])
                   if not (PROJECT_ROOT / q).is_file()]
        if missing:
            bad.append({"id": r["id"], "instrument_id": r["instrument_id"],
                        "missing": ";".join(missing)})
    return pd.DataFrame(bad, columns=["id", "instrument_id", "missing"])
