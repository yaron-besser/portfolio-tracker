"""The claim store — one row per bet, the decision record R1 exists for.

Design: _archive/design-record.md §5.4 (schema), §5.5 (rules). One CSV, validated at every
write; open/due/resolved are *derived* views, never stored. `date` is when
you thought it, `recorded` is when it entered the system — rows where
`recorded > date` are reconstructions and any scoring must be able to
exclude them.

Vocabularies (origin, kind, conviction) live in ops/vocab/, one value per
line, so extending one is an appended line — never a code change (§5.6).

Usage:
    from src import claims
    claims.add_claim(date="2026-08-08", ticker="NVDA", origin="mine",
                     kind="claim", claim="...", wrong_if="...",
                     check_date="2026-11-08", price=181.50, conviction="high")
    claims.due_claims()          # check date passed, no verdict yet
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLAIMS_PATH = PROJECT_ROOT / "data" / "claims.csv"

# Characters that look like nothing but survive str.strip().
INVISIBLE = "\u200b\u200c\u200d\u2060\ufeff\u00a0"
VOCAB_DIR = PROJECT_ROOT / "ops" / "vocab"

COLUMNS = [
    "id", "date", "recorded", "ticker", "instrument_id", "origin",
    # WHO said it, as opposed to which firm. `origin` has to stay coarse
    # because scoreboard() groups by it — twenty individual names would
    # shatter that cut into useless buckets. This is what makes "is Newton
    # better for me than Lee?" answerable: both are origin=fundstrat, and six
    # of his bets could not tell them apart (found 2026-09-27). Free text,
    # always optional, and NOT frozen — he may attribute a row later, and a
    # name he does not remember today must never be invented to fill it.
    "said_by",
    # The call this bet came from, when one decision produced several trades.
    # 2026-09-15: "technology is breaking out" became c0029 (QQQ), c0030 (MAGS)
    # and c0031 (XLK) with byte-identical text, because a bet names ONE
    # instrument. Yaron's call was to keep three rows — they are different
    # sizes and will have different outcomes, and the verdict belongs to the
    # instrument — so this groups them instead of merging them. A free label,
    # not a foreign key; blank means the bet stands alone. NOT frozen: the
    # three rows it exists for were written before it did.
    "decision",
    "kind",
    "claim", "wrong_if", "check_date", "price",
    # What `price` (and `atr`, `sma_200`) are quoted in. Blank means USD.
    # Added 2026-10-01: without it `/show` printed his Samsung entry of
    # ₩240,000 as "$240000.0" on a $1,530 position. Same convention as
    # levels.csv, which solved this on 2026-09-29.
    "currency",
    "conviction",
    # `evidence` used to sit here as a ;-separated list of paths. Moved to
    # data/evidence.csv + data/evidence_links.csv on 2026-08-16: one report
    # covers a dozen stocks, so a per-claim path stored the same fact a dozen
    # times, and there was nowhere to record WHICH PAGE backed which bet.
    # See src/evidence.py.
    # Which broker trade(s) actually executed this decision. Deliberately NOT
    # frozen: a plan is written before the trade exists, so this is filled in
    # afterwards, like outcome. It is the only join between what he INTENDED
    # and what he DID, and it cannot be reconstructed later — a trade from six
    # months ago cannot be matched to an intention nobody recorded.
    "trade_ids",
    # ⚠️ `atr` carries no period. His TradingView layout reads ATR(90), not the
    # conventional 14 — verified on notes/screenshots/2026-08-15-IWM-price-1d.png
    # ("ATR Display (Value & %) (90, Top Right)"). Read it off the same layout
    # every time, or the numbers stop comparing. The chart linked to the row
    # via evidence_links.csv is what settles it for any given claim.
    #
    # Market state at the moment of the decision — NOT current state. Added
    # 2026-08-15, Yaron's idea. Both are point-in-time facts that never rot:
    # "ATR was 4.64 when I set the stop" stays true forever, while "ATR is
    # 4.64" is wrong tomorrow. They are what later answers "was that stop
    # inside the noise or outside it?" — 285 against a 305 price is 4.3 ATRs
    # of room, which is a fact about the DECISION, not about today.
    #
    # Optional, always. A claim with no chart open still gets written; the
    # chart linked via evidence_links.csv carries everything else on screen.
    "atr", "sma_200",
    # How big the position was when the bet was written, as a % of NAV.
    # Computed at write time, never typed and never updated — "was I right"
    # and "did it matter" are different questions, and the second one needs
    # this. Not recoverable later for anything opened before the trade record
    # starts (2025-06-10), which is exactly the case for IWM.
    "nav_pct",
    # What this claim MEANS TO DO — open/add/trim/exit/reduce-risk/hold/watch.
    #
    # Added 2026-08-15 because owning something does not mean there is nothing
    # to act on. Yaron: "about ASML — I have the position already, it's about
    # ADDING. Maybe I want to track something but I want to add, or something I
    # want to sell if it breaks a level." A derived guess got c0001 right only
    # by accident, and would have recorded a plan he carried out as one he
    # did not.
    #
    # Not derivable: only he knows whether writing this implies doing anything.
    # `hold` and `watch` mean there is nothing to execute, so follow_through()
    # leaves `happened` blank for them rather than recording a no.
    "intent",
    # Why a planned trade did not happen. Filled in LATER, like outcome — the
    # reason only exists after the fact. Yaron, 2026-08-15: "the setup broke, I
    # changed my mind, I forgot — this is exactly the kind of stuff we want to
    # track."
    #
    # `still-waiting` is the one that earns the column: without it, patience and
    # forgetfulness are the same row. A trigger that has not hit yet is not a
    # miss, and a record that cannot tell those apart teaches the wrong lesson.
    "why_not",
    "resolved_date", "outcome", "resolution_note", "note_ref",
]

# Verdicts live in ops/vocab/outcome like every other vocabulary, so adding a
# value is an appended line rather than a code change (2026-08-15). It was a
# hardcoded three — right/wrong/unclear — which forced every bet into a binary
# Yaron does not actually trade: "I will know if I was right or wrong when I
# close the position, and I decide that."


def vocab(name: str) -> list[str]:
    """Allowed values for a field, from ops/vocab/<name> — the single source
    all enforcement points (module, tests, pre-commit hook) read."""
    path = VOCAB_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Vocabulary file missing: {path}")
    return [v.strip() for v in path.read_text().splitlines() if v.strip()]


def _vocab_check(name: str) -> pa.Check:
    """isin() against a vocab file, with an error that teaches the fix (P2)."""
    allowed = vocab(name)
    return pa.Check.isin(
        allowed,
        error=f"must be one of {allowed} — extend with: echo <value> >> ops/vocab/{name}",
    )


def explain_schema_error(err: Exception) -> str:
    """
    Render a Pandera failure as lines a person can act on (P2).

    `str(SchemaErrors)` is a nested JSON dump — the fix it is trying to teach
    is buried under about forty lines of structure. This pulls out the one
    thing that matters per failure: which column, which row, what value, and
    the check's own error message, which already carries the command to run.

    A whole-table check (one that looks at several columns at once, like "an
    update row must leave conviction blank") fails once per column, so Pandera
    reports the same sentence nine times. Those are collapsed to one line —
    nine identical paragraphs teach nothing.
    """
    cases = getattr(err, "failure_cases", None)
    if cases is None or not len(cases):
        return str(err)

    lines, seen = [], set()
    for _, c in cases.iterrows():
        check = str(c.get("check"))
        row = c.get("index")
        where = f"row {row}" if pd.notna(row) else "the table"
        # One line per (rule, row): a table-level rule names no single column,
        # so repeating it once per column is noise, not information.
        key = (check, str(row))
        if key in seen:
            continue
        seen.add(key)
        col = c.get("column")
        column_failures = cases[cases["check"] == check]["column"].nunique()
        if column_failures > 1:
            lines.append(f"  {where}: {check}")
        else:
            lines.append(
                f"  {col or 'table'} — {where}: "
                f"{c.get('failure_case')!r} failed {check}"
            )
    return "\n".join(lines)


def _is_real_date(value: str) -> bool:
    """ISO YYYY-MM-DD *and* a date that exists. A regex alone accepts
    2026-02-30, which can never be resolved correctly."""
    try:
        text = str(value)
        return date.fromisoformat(text).isoformat() == text
    except (ValueError, TypeError):
        return False


def split_paths(cell: str | float | None) -> list[str]:
    """The ;-separated paths in an evidence/note_ref cell. A blank cell reads
    back from CSV as NaN, not "", so both spellings of empty give []."""
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return []
    return [p.strip() for p in str(cell).split(";") if p.strip()]


def _normalise_decision(cell) -> str:
    """One spelling per call: lowercased, trimmed, spaces to hyphens.

    The only real failure mode for a free label — "Technology Sep 26" and
    "technology-sep-26" would be two groups and nothing would say so. The other
    half of the defence is that /note offers the labels in use rather than
    asking him to type one.
    """
    if cell is None or cell is pd.NaT:
        return ""
    # pd.isna(), not isinstance(float): pd.NA is not a float, so it fell
    # through and became the literal label "<na>" — and three untagged bets
    # then reported as one call at 100%. Reachable from any nullable-dtype
    # frame, which is how you would copy a label off an existing row.
    try:
        if pd.isna(cell):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(cell, (int, float)):
        # A bare number is not a name for a decision.
        raise ValueError(
            f"decision {cell!r} is a number — a call needs a name you will "
            f"recognise later, like 'technology-sep-26'."
        )
    text = str(cell).strip().strip(INVISIBLE).lower()
    # Whitespace was collapsed but punctuation was not, so a trailing hyphen,
    # a doubled hyphen or a macOS smart dash each made a second call of one —
    # the exact failure this function exists to prevent.
    for dash in "—–‒―":
        text = text.replace(dash, "-")
    text = "-".join(p for p in text.replace("-", " ").split() if p)
    return text


def check_refs(cell: str | float | None, field: str) -> None:
    """Paths must be repo-relative and point at a real file.

    Enforced at WRITE time only (see load_claims): re-checking history on
    every read would let one archived note brick the whole store — and the
    canonical full-close workflow archives notes routinely.
    """
    for p in split_paths(cell):
        if Path(p).is_absolute():
            raise ValueError(
                f"{field} path {p!r} is absolute — use a repo-relative path "
                f"(e.g. notes/positions/NVDA.md) so the row is valid on any machine."
            )
        if not (PROJECT_ROOT / p).is_file():
            raise ValueError(f"{field} path {p!r} does not point to a file that exists.")


def _schema() -> pa.DataFrameSchema:
    """Built per call so vocab edits apply without a reload."""
    date_check = pa.Check(
        lambda s: s.map(_is_real_date),
        error="dates must be a real calendar date in ISO YYYY-MM-DD form",
    )
    required_iso = pa.Column(str, date_check)
    optional_iso = pa.Column(str, date_check, nullable=True)
    # Path *existence* is deliberately not checked here — only at write time.
    path_col = pa.Column(str, nullable=True)
    return pa.DataFrameSchema(
        {
            "id": pa.Column(str, unique=True),
            "date": required_iso,
            "recorded": required_iso,
            # What the user called it at the time — a point-in-time fact,
            # never rewritten. `instrument_id` is the link to the reference
            # table; both are kept, because "ASML_USD" is what he actually
            # typed and that is part of the record (R1).
            "ticker": pa.Column(str, pa.Check.str_length(min_value=1)),
            "instrument_id": pa.Column(str, pa.Check.str_length(min_value=1)),
            "origin": pa.Column(str, _vocab_check("origin")),
            "said_by": pa.Column(str, nullable=True),
            "decision": pa.Column(str, nullable=True),
            "kind": pa.Column(str, _vocab_check("kind")),
            "claim": pa.Column(str, pa.Check.str_length(min_value=1)),
            # Required on kind=claim, optional on kind=watch: dataframe-level
            # checks below carry the split; columns stay nullable here.
            "wrong_if": pa.Column(str, nullable=True),
            # Nullable since kind=update: an update has no follow-up date
            # because it is not a bet. The claim/watch requirement is carried
            # by the dataframe-level checks below.
            "check_date": optional_iso,
            "price": pa.Column(
                float,
                pa.Check.gt(0, error="price must be a positive number"),
                nullable=True,
                coerce=True,
            ),
            # Not a vocab file: the set of currencies is whatever his broker
            # prices a lot in, and that comes from positions.csv rather than a
            # list somebody has to remember to edit. Same choice as levels.csv.
            "currency": pa.Column(str, nullable=True),
            "conviction": pa.Column(str, _vocab_check("conviction"), nullable=True),
            "trade_ids": pa.Column(str, nullable=True),
            "atr": pa.Column(
                float,
                pa.Check.gt(0, error="atr must be a positive number"),
                nullable=True, coerce=True,
            ),
            "sma_200": pa.Column(
                float,
                pa.Check.gt(0, error="sma_200 must be a positive number"),
                nullable=True, coerce=True,
            ),
            "nav_pct": pa.Column(
                float,
                pa.Check.in_range(0, 100, error="nav_pct is a percentage of NAV"),
                nullable=True, coerce=True,
            ),
            "intent": pa.Column(str, _vocab_check("intent"), nullable=True),
            "why_not": pa.Column(str, _vocab_check("why_not"), nullable=True),
            "resolved_date": optional_iso,
            "outcome": pa.Column(str, _vocab_check("outcome"), nullable=True),
            # Separate column so the verdict rationale never rewrites the
            # point-in-time `claim` text (1NF — two facts, two fields).
            "resolution_note": pa.Column(str, nullable=True),
            "note_ref": path_col,
        },
        strict=True,
        ordered=True,
        checks=[
            pa.Check(
                lambda df: df["check_date"].isna() | (df["check_date"] > df["date"]),
                error="check_date must be later than date",
            ),
            # His own idea has nobody else to name. Only this direction is
            # enforceable: 16 live rows are origin=fundstrat with said_by
            # blank, because the column did not exist when they were written,
            # and history is never rewritten to satisfy a new rule.
            # `unattributed()` reports those instead.
            pa.Check(
                lambda df: ~((df["origin"] == "mine")
                             & df["said_by"].fillna("").str.strip().ne("")),
                error="said_by names the person whose idea this was, so it must "
                      "be blank when origin is 'mine' — set origin to their "
                      "source instead",
            ),
            pa.Check(
                lambda df: (df["kind"] != "claim") | df["check_date"].notna(),
                error="check_date is required on every claim row — a bet with no "
                      "date to look again is not a bet",
            ),
            # ── The "two things, not one" rule (REBUILD.md Phase 2f) ──
            # An update records what happened or what someone said. It never
            # changes a bet. If a Fundstrat note changes his mind that is two
            # acts: record the update, then resolve the old claim and write a
            # new one. Blurring them into one row would make the question
            # "does reading Fundstrat actually improve my decisions?"
            # permanently unanswerable, because what he READ and what he
            # DECIDED would be the same row.
            pa.Check(
                lambda df: (df["kind"] != "update")
                | (
                    df["wrong_if"].isna()
                    & df["price"].isna()
                    & df["conviction"].isna()
                    & df["check_date"].isna()
                ),
                error="an update row must leave wrong_if, price, conviction and "
                      "check_date blank — it records what happened, it does not "
                      "change a bet. To change the bet: resolve_claim() the old "
                      "one, then add_claim() a new one",
            ),
            pa.Check(
                lambda df: df["resolved_date"].isna() == df["outcome"].isna(),
                error="outcome and resolved_date are required together or absent together",
            ),
            pa.Check(
                lambda df: (df["kind"] != "claim")
                | (df["wrong_if"].fillna("").str.len() > 0),
                error="wrong_if is required on every claim row",
            ),
            pa.Check(
                lambda df: (df["kind"] != "claim") | df["price"].notna(),
                error="price is required on every claim row",
            ),
            pa.Check(
                lambda df: (df["kind"] != "claim") | df["conviction"].notna(),
                error="conviction is required on every claim row",
            ),
        ],
    )


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Validate a claims DataFrame. Raises SchemaErrors naming every failing
    row and field (P2 — a rejection must teach the fix)."""
    return _schema().validate(df, lazy=True)


def load_claims(path: Path = CLAIMS_PATH) -> pd.DataFrame:
    """Read and validate the whole store. An empty/missing file is a valid
    empty store, not an error.

    No `comment=` argument: a '#' inside a claim ("NVDA becomes #1") would
    otherwise truncate the row on read, after a write that validated fine.
    """
    # A missing or zero-byte file is an empty store — this is also the state
    # the commit guard sees for HEAD when claims.csv is brand new.
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     na_values=[""])
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    # Loud, not coerced: errors="coerce" would silently turn "760 USD" into
    # NaN and the next rewrite would erase the original text (P2).
    for col in ("price", "atr", "sma_200"):
        if col not in df.columns:
            continue
        try:
            df[col] = pd.to_numeric(df[col])
        except (ValueError, TypeError) as e:
            raise ValueError(f"{path}: {col} column has a non-numeric value — {e}") from None
    return validate(df)


def _write(df: pd.DataFrame, path: Path) -> None:
    """Quote every text field so '#', ';' or a newline can never corrupt a row."""
    df.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def _next_id(df: pd.DataFrame) -> str:
    """The next cNNNN id: one above the highest that parses as a number."""
    nums = pd.to_numeric(df["id"].str.removeprefix("c"), errors="coerce").dropna()
    next_num = int(nums.max()) + 1 if len(nums) else 1
    return f"c{next_num:04d}"


def add_claim(path: Path = CLAIMS_PATH, **fields) -> str:
    """Validate one new row and append it. Returns the new id.

    The whole file (existing rows + the new one) is validated before a
    single byte is written, so a bad row can never land (§5.5: bad data is
    caught at write time, before it exists).
    """
    existing = load_claims(path)
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"Unknown field(s) {sorted(unknown)}. Columns are: {COLUMNS}")
    row = {c: fields.get(c) for c in COLUMNS}
    row["id"] = _next_id(existing)
    row["recorded"] = row["recorded"] or date.today().isoformat()
    for col in ("price", "atr", "sma_200", "nav_pct"):
        if row[col] is not None:
            row[col] = float(row[col])
    # Reference paths are checked here, on the new row only — never against
    # history on load (an archived note must not brick the store).
    row["decision"] = _normalise_decision(row.get("decision"))
    check_refs(row["note_ref"], "note_ref")
    check_trade_ids(row["trade_ids"], instrument_id=row["instrument_id"])
    # The ticker must be a known instrument. Imported locally because
    # instruments.py reads this module's vocab loader — the other direction.
    from src import instruments

    if not row["instrument_id"]:
        row["instrument_id"] = instruments.resolve(row["ticker"])
    instruments.check_known([row["instrument_id"]])
    # AFTER the id is resolved — computing it earlier read a blank and silently
    # recorded nothing, which is worse than refusing (found 2026-08-15).
    if row["nav_pct"] is None:
        row["nav_pct"] = position_pct_of_nav(row["instrument_id"])
    candidate = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    validate(candidate)
    _write(candidate, path)
    return row["id"]


# ─── Setups: why a claim was taken, one row per reason ───
#
# A join table, not a column, because a claim usually has several reasons at
# once — "a trendline AND the fundamentals AND 13D flagged it" (Yaron,
# 2026-08-15). Squeezing them into one cell would put a repeating group inside
# a field, and counting them would mean splitting strings.
#
# No surrogate id: the pair (claim_id, setup) IS the key. A c0001-style id
# would be a second name for something already uniquely identified, which is
# the duplication P7 forbids.
#
# Room left deliberately: if he later wants to rank the reasons — which one
# actually drove the trade — that is one added column here, not a redesign.

SETUPS_PATH = PROJECT_ROOT / "data" / "claim_setups.csv"
SETUP_COLUMNS = ["claim_id", "setup", "added"]


def _setups_schema() -> pa.DataFrameSchema:
    """Built per call so a vocab edit applies without a reload."""
    return pa.DataFrameSchema(
        {
            "claim_id": pa.Column(str, pa.Check.str_length(min_value=1)),
            "setup": pa.Column(str, _vocab_check("setup")),
            "added": pa.Column(
                str,
                pa.Check(lambda s: s.map(_is_real_date),
                         error="added must be a real calendar date, ISO YYYY-MM-DD"),
            ),
        },
        strict=True,
        ordered=True,
        checks=[
            pa.Check(
                lambda df: ~df.duplicated(subset=["claim_id", "setup"]),
                error="the same setup cannot be listed twice for one claim — "
                      "(claim_id, setup) is the key",
            ),
        ],
    )


def load_setups(path: Path = SETUPS_PATH) -> pd.DataFrame:
    """Read and validate the setup links. Missing/empty is an empty table."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=SETUP_COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     na_values=[""])
    if df.empty:
        return pd.DataFrame(columns=SETUP_COLUMNS)
    return _setups_schema().validate(df, lazy=True)


def add_setups(claim_id: str, setups, path: Path = SETUPS_PATH,
               claims_path: Path = CLAIMS_PATH) -> list[str]:
    """Record why a claim was taken. Several reasons is the normal case.

    Refuses a claim that does not exist and a setup already listed — both would
    quietly corrupt the counts `scoreboard()` produces.
    """
    names = [setups] if isinstance(setups, str) else list(setups)
    existing = load_setups(path)
    known = set(load_claims(claims_path)["id"])
    if claim_id not in known:
        raise ValueError(
            f"No claim with id {claim_id!r} — a setup must belong to a real claim."
        )
    already = set(existing[existing["claim_id"] == claim_id]["setup"])
    dupes = sorted(set(names) & already)
    if dupes:
        raise ValueError(f"{claim_id} already lists {dupes}.")
    rows = [{"claim_id": claim_id, "setup": n, "added": date.today().isoformat()}
            for n in names]
    candidate = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
    _setups_schema().validate(candidate, lazy=True)
    _write(candidate, path)
    return names


def setups_for(claim_id: str, path: Path = SETUPS_PATH) -> list[str]:
    """Every reason recorded for one claim."""
    df = load_setups(path)
    return sorted(df[df["claim_id"] == claim_id]["setup"])


def check_setup_claims(path: Path = SETUPS_PATH,
                       claims_path: Path = CLAIMS_PATH) -> None:
    """Every link must name a claim that exists — the foreign key, at commit."""
    df = load_setups(path)
    if df.empty:
        return
    known = set(load_claims(claims_path)["id"])
    missing = sorted(set(df["claim_id"]) - known)
    if missing:
        raise ValueError(
            f"claim_setups.csv names claim(s) {missing} that do not exist in "
            f"data/claims.csv."
        )


def position_pct_of_nav(instrument_id: str,
                        positions_path: Path | None = None) -> float | None:
    """This holding as a % of everything, right now. None if not held.

    Called once at write time and then frozen. It is the difference between
    "was I right" and "did it matter" — a correct call on 0.5% of the book and
    the same call on 8% are not the same event, and the record could not tell
    them apart before 2026-08-15.

    `book.nav()` (Task 3, 2026-10-06) answers a related but deliberately
    different question and must stay different. This asks "how big was this
    bet against every dollar I had" — money the repo cannot classify is still
    his money, so it stays in `total` below. `book.nav()` is the page's one
    definition of what a BASIS is worth, and excludes exactly that money,
    because it has no theme or sector to be a share of. Repointing this
    function to `book.nav()` would also mean a `nav_pct` computed today is no
    longer comparable to one frozen before this paragraph existed, which
    corrupts the comparison the column exists for. The two are expected to
    agree in every case except when `book.unclassified()` is non-empty — a
    test pins that boundary rather than leaving two silently different
    totals.
    """
    positions_path = positions_path or (PROJECT_ROOT / "data" / "positions.csv")
    if not instrument_id or not positions_path.exists():
        return None
    pos = pd.read_csv(positions_path, dtype=str, keep_default_na=False)
    # Loud, not coerced — the same rule load_claims() applies to price. A blank
    # or unreadable market_value dropped silently out of the DENOMINATOR, so
    # every other position was written up: one blank SGOV row moved IWM from
    # 16.3% to 56.4%. `nav_pct` is frozen at write time, so that number could
    # never be corrected. Refusing is the only safe answer (found by review,
    # 2026-08-15).
    mv = pd.to_numeric(pos["market_value"], errors="coerce")
    bad = pos.loc[mv.isna(), "ticker"].tolist()
    if bad:
        raise ValueError(
            f"Cannot size this claim: {bad} have an unreadable market_value in "
            f"{positions_path.name}.\n"
            f"  The size would be computed against a short denominator and "
            f"frozen wrong.\n"
            f"  Re-run the sync, or pass nav_pct=... explicitly to state it."
        )
    total = float(mv.sum())
    lots = mv[pos["instrument_id"] == instrument_id]
    if lots.empty:
        return None          # not held — no size to state, and not 0
    held = float(lots.sum())
    # Size is magnitude. A short lot carries a NEGATIVE market_value, and the
    # old `held <= 0` test returned None for it — so nav_pct went blank for
    # exactly the positions whose size matters most, while the repo carries
    # 296 rows of borrow fees proving he shorts. Direction is already recorded
    # in `intent`; repeating it as a minus sign here would only make the
    # number unusable for sorting by how big a bet was.
    if total <= 0 or held == 0:
        return None
    return round(abs(held) / total * 100, 2)


def check_trade_ids(cell, path: Path | None = None,
                    instrument_id: str | None = None) -> None:
    """Every trade id must exist, and must be a trade in the right instrument.

    A link to a trade that never happened is worse than no link: it would
    count as executed in the follow-through numbers.

    The instrument check was added 2026-08-15 after review pointed out that an
    ASML fill could be linked to an IWM claim and nothing would object. Matching
    is at INSTRUMENT level, not ticker: ASML_USD and ASML_EUR are two broker
    lots of one instrument, and a claim written against one lot is legitimately
    executed in the other. Skipped when `instrument_id` is not supplied, so the
    commit guard can still check existence for rows it reads without context.
    """
    ids = split_paths(cell)          # same ';'-separated convention as evidence
    if not ids:
        return
    path = path or (PROJECT_ROOT / "data" / "transactions.csv")
    if not path.exists():
        raise ValueError(f"Cannot check trade_ids — {path} does not exist.")
    tx = pd.read_csv(path, dtype=str, keep_default_na=False)
    known = set(tx["trade_id"])
    missing = sorted({i for i in ids if i not in known})
    if missing:
        raise ValueError(
            f"trade_ids {missing} are not in data/transactions.csv.\n"
            f"  A link to a trade that did not happen would count as executed."
        )
    if not instrument_id or "symbol" not in tx.columns:
        # No symbol column means the instrument cannot be checked at all. Say
        # nothing rather than crash — existence has already been verified, and
        # the real transactions.csv always carries symbol.
        return
    from src import instruments

    by_id = tx.set_index("trade_id")
    wrong = []
    for i in ids:
        sym = str(by_id.at[i, "symbol"])
        try:
            got = instruments.resolve(sym)
        except Exception:
            got = sym
        if got != instrument_id:
            wrong.append(f"{i} is a {sym} trade ({got}), not {instrument_id}")
    if wrong:
        raise ValueError(
            "These trades are in the wrong instrument:\n  "
            + "\n  ".join(wrong)
            + f"\n  Linking one would count a {wrong[0].split()[3]} fill as "
              f"executing a {instrument_id} plan."
        )


def link_trades(claim_id: str, trade_ids, path: Path = CLAIMS_PATH) -> None:
    """Record which broker trade(s) executed a claim. Fillable after the fact.

    This is the join that makes follow-through measurable: a claim with a
    trade was acted on, one without was not. Appends rather than replaces, so
    a position built in several fills accumulates its trades.
    """
    ids = [trade_ids] if isinstance(trade_ids, str) else list(trade_ids)
    df = load_claims(path)
    mask = df["id"] == claim_id
    if not mask.any():
        raise ValueError(f"No claim with id {claim_id!r} in {path}")
    existing = split_paths(df.loc[mask, "trade_ids"].iloc[0])
    merged = existing + [i for i in map(str, ids) if i not in existing]
    check_trade_ids(";".join(merged),
                    instrument_id=df.loc[mask, "instrument_id"].iloc[0])
    df.loc[mask, "trade_ids"] = ";".join(merged)
    _write(df, path)


def record_why_not(claim_id: str, reason: str, path: Path = CLAIMS_PATH) -> None:
    """Why a planned trade did not happen. His judgement, not an inference.

    Separate from `resolve_claim()` on purpose: whether the *thesis* was right
    and whether the *trade* happened are different questions with different
    answers. KWEB can be a good idea he never bought.

    Fillable once, then permanent — the same rule as a verdict, so a reason
    cannot be softened later.
    """
    df = load_claims(path)
    mask = df["id"] == claim_id
    if not mask.any():
        raise ValueError(f"No claim with id {claim_id!r} in {path}")
    if df.loc[mask, "why_not"].notna().any():
        raise ValueError(
            f"Claim {claim_id} already records why it did not happen "
            f"(append-only store)."
        )
    df.loc[mask, "why_not"] = reason
    validate(df)
    _write(df, path)


def resolve_claim(claim_id: str, outcome: str, note: str = "", path: Path = CLAIMS_PATH) -> None:
    """Record the verdict on a claim. The verdict text is the user's (P1).

    The note goes in its own column — the original `claim` text is a
    point-in-time record and is never rewritten (R1).
    """
    df = load_claims(path)
    mask = df["id"] == claim_id
    if not mask.any():
        raise ValueError(f"No claim with id {claim_id!r} in {path}")
    if df.loc[mask, "outcome"].notna().any():
        raise ValueError(f"Claim {claim_id} is already resolved (append-only store)")
    df.loc[mask, "outcome"] = outcome
    df.loc[mask, "resolved_date"] = date.today().isoformat()
    if note:
        df.loc[mask, "resolution_note"] = note
    validate(df)
    _write(df, path)


# Point-in-time fields: what you thought, when, at what price. Once written
# these are history and may never change (R1). Only the resolution fields may
# go from blank to filled, exactly once.
FROZEN = ["date", "recorded", "ticker", "instrument_id", "origin", "kind",
          "claim", "wrong_if", "check_date", "price", "currency", "conviction",
          "atr", "sma_200", "nav_pct", "intent"]
# `why_not` is deliberately absent above: like outcome, it can only be known
# after the fact, so it goes from blank to filled exactly once.


# Frozen columns that hold a number. Only these may be compared numerically —
# see same_value().
NUMERIC_FROZEN = {"price", "atr", "sma_200", "nav_pct"}


def same_value(before, after, numeric: bool = False) -> bool:
    """Whether two cells hold the same fact, regardless of how they were read.

    The commit guard compares the HEAD copy — read as raw text, because a file
    written under an older schema must not be held to today's — against a
    freshly typed frame. So a price of 214.60 arrives as the string '214.6' on
    one side and a float on the other, and a raw `!=` reports a rewrite that
    never happened.

    Found 2026-08-14 by review. It had never fired only because both existing
    claims were `watch` rows with a blank price; the first `claim` row, which
    *requires* a price, would have blocked every later commit touching any data
    table — and the only escape is --no-verify, which turns off every check at
    once. The same bug was live in levels.py, which reuses this function.

    `numeric` is opt-in **per column**, and deliberately narrow. Coercing every
    field would make the guard compare meaning rather than text, so a frozen
    text cell edited from '1e3' to '1000' — or a ticker from ' 700 ' to '700' —
    would read as unchanged. A guard that quietly stops guarding is worse than
    one that is occasionally noisy, so only the columns that really hold a
    number opt in (NUMERIC_FROZEN).
    """
    if pd.isna(before) and pd.isna(after):
        return True
    if before == after:
        return True
    if not numeric:
        return False
    try:
        return float(before) == float(after)
    except (TypeError, ValueError):
        return False


def check_append_only(old: pd.DataFrame, new: pd.DataFrame) -> None:
    """Raise if `new` rewrites history rather than appending to it (§5.5).

    Only columns present in BOTH frames are compared, so a column can still be
    added later. Without that, the first schema change would be permanently
    blocked by this guard — the already-committed file cannot be expected to
    carry a column that did not exist when it was written.
    """
    if old.empty:
        return
    old_by_id, new_by_id = old.set_index("id"), new.set_index("id")
    dropped = sorted(set(old_by_id.index) - set(new_by_id.index))
    if dropped:
        raise ValueError(f"Rows deleted from an append-only store: {dropped}")
    comparable = [c for c in FROZEN if c in old_by_id.columns and c in new_by_id.columns]
    for cid in old_by_id.index:
        for col in comparable:
            before, after = old_by_id.at[cid, col], new_by_id.at[cid, col]
            if not same_value(before, after, col in NUMERIC_FROZEN):
                raise ValueError(
                    f"Claim {cid}: {col} changed {before!r} -> {after!r}. "
                    f"Point-in-time facts are permanent; only outcome, "
                    f"resolved_date and resolution_note may be filled in later."
                )
        # Column-guarded like `comparable` above: without this, comparing a
        # frame written before this column existed raises KeyError, which is
        # exactly the "a column can be added later" promise in the docstring.
        if "outcome" not in old_by_id.columns or "outcome" not in new_by_id.columns:
            continue
        if pd.notna(old_by_id.at[cid, "outcome"]) and (
            old_by_id.at[cid, "outcome"] != new_by_id.at[cid, "outcome"]
        ):
            raise ValueError(f"Claim {cid}: a recorded verdict cannot be rewritten.")
        # `decision` cannot be FROZEN — the three rows it exists for were
        # written before the column did, so blank -> filled has to stay legal.
        # But scoreboard() now GROUPS on it, so erasing or moving a tag
        # silently changes the answer to "how did that call do?" after the
        # fact. Blank -> filled once, then permanent: the same shape as the
        # outcome rule above, and for the same reason.
        if "decision" not in old_by_id.columns or "decision" not in new_by_id.columns:
            continue
        was, now = old_by_id.at[cid, "decision"], new_by_id.at[cid, "decision"]
        if pd.notna(was) and str(was).strip() and (
            pd.isna(now) or str(now).strip() != str(was).strip()
        ):
            raise ValueError(
                f"Claim {cid}: decision changed {was!r} -> {now!r}. A call a bet "
                f"belongs to can be filled in once and never moved — scoreboard() "
                f"groups on it, so changing it rewrites a past answer."
            )


def stale_refs(path: Path = CLAIMS_PATH) -> pd.DataFrame:
    """Rows whose note_ref no longer resolves — usually a note that was
    archived. Visible, never blocking (P2): the store keeps working.

    Checked evidence paths too until 2026-08-16. That half moved out with the
    column: `evidence.stale_documents()` is the same check over the documents
    table, so nothing stopped being watched.
    """
    df = load_claims(path)
    bad = []
    for _, row in df.iterrows():
        refs = split_paths(row["note_ref"])
        missing = [p for p in refs if not (PROJECT_ROOT / p).is_file()]
        if missing:
            bad.append({"id": row["id"], "ticker": row["ticker"], "missing": ";".join(missing)})
    return pd.DataFrame(bad, columns=["id", "ticker", "missing"])


def open_claims(path: Path = CLAIMS_PATH) -> pd.DataFrame:
    """Unresolved rows whose check date has not arrived yet."""
    df = load_claims(path)
    return df[df["outcome"].isna() & (df["check_date"] > date.today().isoformat())]


def due_claims(as_of: str | None = None, path: Path = CLAIMS_PATH) -> pd.DataFrame:
    """Check date passed, no verdict — the follow-up list. This view is the
    mechanism that makes retrospection happen (§5.4)."""
    df = load_claims(path)
    cutoff = as_of or date.today().isoformat()
    return df[df["outcome"].isna() & (df["check_date"] <= cutoff)]


def resolved_claims(path: Path = CLAIMS_PATH) -> pd.DataFrame:
    """Rows with a verdict. `recorded > date` rows are reconstructions —
    exclude them when scoring foresight."""
    df = load_claims(path)
    return df[df["outcome"].notna()]


# `unevidenced_claims()` lived here until 2026-08-16. It now reads the link
# table rather than a column, so it lives with the tables it queries:
#     from src import evidence; evidence.unevidenced_claims()


# ─── Behaviour: what the record can tell him about himself ───

def decisions(path: Path = CLAIMS_PATH) -> pd.DataFrame:
    """Every call a bet has been tagged with, newest first.

    Exists so `/note` can offer the labels already in use as CHOICES. Letting
    him type one is how a call silently splits in two — the normaliser catches
    case and spacing, not a different word.

    **Only `claim` and `watch` rows count as bets.** An `update` row is not a
    bet by this store's own definition, and `/show` reads this number out loud
    as "three bets came from that one call" — so counting one would put a
    false sentence in his mouth. Same filter `scoreboard()` uses, so the two
    views cannot disagree about what a call contains.

    Calls of one are included: a label used once is usually a typo that split a
    call, and hiding it is how the split stays invisible.
    """
    df = load_claims(path)
    if df.empty or "decision" not in df.columns:
        return pd.DataFrame(columns=["decision", "bets", "tickers", "first", "last"])
    df = df[df["kind"].isin(["claim", "watch"])]
    tagged = df[df["decision"].fillna("").str.strip().ne("")] if not df.empty \
        else df
    if tagged.empty:
        return pd.DataFrame(columns=["decision", "bets", "tickers", "first", "last"])
    out = (tagged.groupby("decision")
           .agg(bets=("id", "size"),
                tickers=("ticker", lambda s: ", ".join(sorted(set(s)))),
                first=("date", "min"), last=("date", "max"))
           .reset_index()
           .sort_values("last", ascending=False)
           .reset_index(drop=True))
    return out


def unattributed(path: Path = CLAIMS_PATH) -> pd.DataFrame:
    """Bets that came from someone else with nobody named.

    Visible, never blocking — the same contract `instruments.gaps()` has. The
    16 rows here today predate the column, and the point of showing them is
    that "is Newton better for me than Lee?" stays unanswerable until they are
    filled in, one at a time, by him. Never guess a name to empty this list.
    """
    df = load_claims(path)
    if df.empty:
        return df
    blank = df["said_by"].fillna("").str.strip().eq("")
    return df[(df["origin"] != "mine") & blank].reset_index(drop=True)


def follow_through(path: Path = CLAIMS_PATH) -> pd.DataFrame:
    """Every written intention, and whether the trade happened.

    ⚠️ **This is an observation, not a score** (Yaron, 2026-08-15): "I don't
    think it needs to count as a success. Success is not the right word. Maybe
    stuff got broken — even though I planned to do something, maybe it declined,
    it got broken. It's like happened or not happened."

    So `happened` is a fact, never a verdict. A plan he did not carry out may
    well have been the right call: if the setup broke before he could act, not
    acting was correct, and calling that a failure would teach him the opposite
    of the truth. The column says what occurred; **why** is his to write, and
    the reading is his to make.

    It works because `trade_ids` links a decision to the trade that carried it
    out — the one join that cannot be reconstructed later.

    **Counts `watch` rows as well as `claim` rows** (2026-08-15). It used to
    count claims only, which excluded the clearest case there is: c0001 said
    "re-enter 1 sh near $1,232" in June and the trade happened in August. The
    question this view asks is "did I do what I wrote", not "was it a bet" —
    and a watch row is a written intention with a price on it. `kind` is a
    column so the two can still be read apart; nothing is hidden by the view.

    `update` rows are excluded, and that is not arbitrary: an update records
    what happened or what someone said. It states no intention, so there is
    nothing to have acted on.

    `scoreboard()` stays claim-only — that one scores right vs wrong, and a
    watch row has no verdict.

    `days_to_act` is the lag between writing and trading. Also an observation:
    a long lag and a short one are different, and neither is automatically
    better.

    **`already_held` is the row that stops this metric lying** (Yaron,
    2026-08-15): "IWM is not a trade I started yesterday. It's a trade from a
    long time ago. I recorded it yesterday because that is when we started to
    implement all the changes."

    A claim written about a position he already owns has nothing to act on, so
    counting it as "did not act" would score the work of writing the record
    down as trades he did not make. `happened` is left blank for those rather
    than False, decided by `intent` — `hold` and `watch` state no trade.

    `already_held` is derived from the TRADES that predate the claim, not from
    `nav_pct`. `nav_pct` is computed from whatever is in positions.csv at the
    moment the row is typed, so a bet written up after the event answered "do I
    own this today?" instead of "did I own it when I decided?" — and got both
    directions wrong. ANET and Samsung were `intent=open` and read as already
    held; XLRE and AXP were `intent=exit` and read as never held. Found by
    audit 2026-09-27.

    Two different things are now visible, and they were tangled before:
      recorded > date   — written later than it was thought (a reconstruction)
      already_held      — the position is older than the claim
    A claim can be either, both, or neither.
    """
    df = load_claims(path)
    df = df[df["kind"].isin(["claim", "watch"])].copy()
    if df.empty:
        return pd.DataFrame(columns=["id", "date", "ticker", "kind", "intent",
                                     "conviction", "already_held", "happened",
                                     "days_to_act", "why_not", "reconstructed",
                                     "outcome"])
    tx_path = PROJECT_ROOT / "data" / "transactions.csv"
    raw = (pd.read_csv(tx_path, dtype=str, keep_default_na=False)
           if tx_path.exists() else pd.DataFrame())
    tx = (raw.set_index("trade_id")["trade_date"].to_dict()
          if not raw.empty else {})
    fills = _fills_by_instrument(raw)

    rows = []
    for _, r in df.iterrows():
        ids = split_paths(r["trade_ids"])
        dates = sorted(tx[i] for i in ids if i in tx)
        lag = None
        if dates:
            try:
                lag = (date.fromisoformat(dates[0]) - date.fromisoformat(r["date"])).days
            except ValueError:
                lag = None
        # `hold` and `watch` state no trade, so there is nothing to have done.
        # Anything else — open, add, trim, exit, reduce-risk — is a plan, and a
        # plan with no trade against it is exactly what this view measures.
        # An unstated intent falls back to "there was a plan", so a missing
        # label can never quietly remove a row from the count.
        nothing_to_do = str(r["intent"]) in ("hold", "watch")
        rows.append({"id": r["id"], "date": r["date"], "ticker": r["ticker"],
                     "kind": r["kind"], "intent": r["intent"],
                     "conviction": r["conviction"],
                     "already_held": _held_before(
                         fills, r["instrument_id"], r["date"]),
                     "happened": None if (nothing_to_do and not ids) else bool(ids),
                     "days_to_act": lag,
                     "why_not": r["why_not"],
                     "reconstructed": r["recorded"] > r["date"],
                     "outcome": r["outcome"]})
    return pd.DataFrame(rows)


def _fills_by_instrument(tx: pd.DataFrame) -> dict[str, list[tuple[str, float]]]:
    """(trade_date, signed quantity) per instrument, for `already_held`.

    Keyed on `instrument_id` rather than the broker symbol, so a multi-lot
    holding answers as one position: ASML_USD and ASML_EUR are two symbols and
    one instrument, and owning either means owning ASML.
    """
    from src import instruments

    out: dict[str, list[tuple[str, float]]] = {}
    if tx.empty or not {"symbol", "trade_date", "quantity"} <= set(tx.columns):
        return out
    for _, t in tx.iterrows():
        try:
            qty = float(t["quantity"])
        except (TypeError, ValueError):
            continue
        iid = instruments.resolve(str(t["symbol"]))
        out.setdefault(iid, []).append((str(t["trade_date"]), qty))
    return out


def _held_before(fills: dict[str, list[tuple[str, float]]],
                 instrument_id: str, when: str,
                 positions_path: Path | None = None) -> bool | str:
    """Did he hold this instrument the day BEFORE the claim was dated?

    Three answers, not two: True, False, or `"before-the-log"`.

    Strictly before: a position opened on the same day he wrote the bet is not
    a position he already held, which is the whole distinction the column
    exists to draw. Net quantity, not "any earlier trade" — something bought
    and fully sold last year is not held now.

    ⚠️ **`"before-the-log"` is why this is not a boolean.** IBKR's Flex window
    is a rolling ~365 days, so `transactions.csv` simply does not reach back
    far enough for some positions. IWM, XLF and IMAE — $11,080 of stock —
    have no row in it at all. Answering False for those reported that he
    opened three long-held positions on the day he wrote them up, which is
    the exact lie this column was added to stop: his words about IWM were
    "It's a trade from a long time ago. I recorded it yesterday because that
    is when we started to implement all the changes."

    Held with no fills in the log can only mean acquired before it begins. We
    say that, and do not invent a date. Same wording
    `ibkr_sync.unreconciled_shares()` already uses for the same names — the
    two halves of the project used to disagree about them. His call,
    2026-10-01; found by review.
    """
    rows = fills.get(instrument_id, [])
    if not rows:
        return "before-the-log" if _is_held(instrument_id, positions_path) else False
    if not when:
        return False
    net = sum(q for d, q in rows if d and d < when)
    return abs(net) > 1e-9


def _is_held(instrument_id: str, positions_path: Path | None = None) -> bool:
    """Whether a lot of this instrument carries any market value today."""
    positions_path = positions_path or (PROJECT_ROOT / "data" / "positions.csv")
    if not instrument_id or not positions_path.exists():
        return False
    pos = pd.read_csv(positions_path, dtype=str, keep_default_na=False)
    mine = pos[pos["instrument_id"] == instrument_id]
    if mine.empty:
        return False
    mv = pd.to_numeric(mine["market_value"], errors="coerce").fillna(0.0)
    return bool(abs(mv.sum()) > 1e-9)


def scoreboard(path: Path = CLAIMS_PATH,
               setups_path: Path | None = None) -> pd.DataFrame:
    """Hit rate by conviction, by origin, and by setup — was I better when I
    was sure, are my own ideas better than the ones I read, and which kinds of
    setup actually work for me?

    Only resolved rows count. An unresolved claim is not a draw; it is a
    follow-up he has not done, and `due_claims()` is where those belong.

    **Five verdicts, three of which are judgements.** `right`, `partly-right`
    and `wrong` say how it went; `unclear` and `too-early` say he cannot tell
    yet. Only the first three reach `judged`, and `hit_rate` is a fraction of
    `judged` — never of `n`. Scoring "I cannot say" as a miss is what made the
    live store report 0.0% on 2026-09-27 against one `unclear` verdict and
    nothing ever judged wrong.

    `partly-right` gets its own column rather than half a point. Inventing a
    weight would make the number an opinion, and this table reports what he
    said, not what it is worth.

    **Which is why there are two rates, not one.** `hit_rate` counts only
    `right`; `hit_rate_with_partly` counts `right + partly`. The truth is
    between them and this table refuses to pick, because picking is the
    opinion. Reporting only the first was actively misleading: on 2026-09-29
    every line read `hit_rate 0.0` — "never right" — against 4 partly-rights
    in 8 judged bets. Neither number is the answer on its own; the pair is.

    ⚠️ Whether a partly-right bet should count as a hit, half a hit, or
    nothing is Yaron's to decide, not this function's. Until he does, both
    bounds are shown.

    **A resolved `watch` row counts.** Filtering to `kind="claim"` hid two of
    the three verdicts that existed — a watch he later judged is still a
    judgement he made.
    """
    df = load_claims(path)
    df = df[df["kind"].isin(["claim", "watch"]) & df["outcome"].notna()]
    cols = ["cut", "value", "n", "judged", "right", "partly", "wrong",
            "hit_rate", "hit_rate_with_partly"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    def _tally(cut: str, value: str, g: pd.DataFrame) -> dict:
        right = int((g["outcome"] == "right").sum())
        partly = int((g["outcome"] == "partly-right").sum())
        wrong = int((g["outcome"] == "wrong").sum())
        judged = right + partly + wrong
        return {"cut": cut, "value": value, "n": len(g), "judged": judged,
                "right": right, "partly": partly, "wrong": wrong,
                "hit_rate": round(right / judged * 100, 1) if judged else None,
                "hit_rate_with_partly": (
                    round((right + partly) / judged * 100, 1) if judged else None)}

    out = []
    # "decision" groups the bets one call produced. groupby drops NaN, but NOT
    # a whitespace-only cell — that formed a nameless call of one here while
    # decisions() filtered it out, so the two views disagreed about whether
    # that call existed. Blank it explicitly so both agree.
    if "decision" in df.columns:
        df = df.assign(decision=df["decision"].fillna("").str.strip()
                       .replace("", None))
    for cut in ("conviction", "origin", "decision"):
        for value, g in df.groupby(cut):
            out.append(_tally(cut, value, g))

    # Setups come from the join table, so a claim with three reasons counts in
    # all three rows. `n` therefore sums to more than the number of claims —
    # that is the intended reading: "when a trendline was ONE of my reasons,
    # how often was I right?"
    links = load_setups(setups_path or SETUPS_PATH)
    if not links.empty:
        joined = links.merge(df[["id", "outcome"]], left_on="claim_id",
                             right_on="id", how="inner")
        for value, g in joined.groupby("setup"):
            out.append(_tally("setup", value, g))
    return pd.DataFrame(out, columns=cols)
