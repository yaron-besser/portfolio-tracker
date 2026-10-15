"""Thoughts and commentary — a dated line about anything, deletable.

Why this is not a column on `claims.csv` (2026-09-27, Yaron's call and his
reasoning):

1. **A comment is disposable; a bet is history.** `claims.csv` is append-only
   and physically refuses to lose a row — `Rows deleted from an append-only
   store: ['c0004']`. He wants to be able to throw away something Mark Newton
   said once it stops mattering. The two lifespans cannot share a file.
2. **Every claims row needs one instrument.** `add_claim(ticker="Metals")`
   raises `Unknown instrument(s) ['Metals']`, so a view about a theme, a
   sector or the whole market had no row shape at all.
3. **Everything computed from claims.csv assumes a row can be scored.**
   `scoreboard()` already reported a 0% hit rate once because of a counting
   mistake over row kinds. Rows that can never be right or wrong would mean
   every number needs a filter, and a missed filter lies about his hit rate.

So `claims.csv` holds bets only, and this holds everything else he thinks,
hears or notices. A bet is an *upgrade* from a thought, never a rival door:
`promote()` records that a thought grew into one.

Two columns keep each other honest, because the alternative was asking him to
introspect — and "is this a bet or a thought?" is a question about his state of
mind, which his own record shows is not a stable line. "Whose words are these?"
is a fact:

    kind=thought     ⇒ origin=mine, and said_by blank
    kind=commentary  ⇒ origin is someone else; said_by names the person

`said_by` is free text on purpose. `origin` stays coarse because
`scoreboard()` groups by it — twenty individual names would shatter that cut
into useless buckets. `said_by` is what finally makes "is Newton better for me
than Lee?" answerable; both are `origin=fundstrat`.

Usage:
    from src import commentary
    commentary.add(date="2026-09-27", kind="thought", target_type="cluster",
                   target_id="Metals", origin="mine",
                   body="Metals feel early to me.")
    commentary.about("Metals")          # everything ever said about it
    commentary.since("2026-08-01")      # what he thought in August
    commentary.forget("cm0007")         # the one store a row may leave
"""

from __future__ import annotations

import csv
from datetime import date as _date
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from src import evidence
from src.claims import (
    _is_real_date,
    _vocab_check,
    check_refs,
    explain_schema_error,  # noqa: F401  — re-exported for the commit guard
    vocab,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMENTARY_PATH = PROJECT_ROOT / "data" / "commentary.csv"

COLUMNS = [
    "id", "date", "recorded", "kind", "target_type", "target_id",
    "origin", "said_by", "body", "promoted_to", "note_ref",
]

# The one scope that names nothing. There is no row anywhere for "the market",
# and inventing a pseudo-cluster for it would land in clusters.weights() and
# corrupt every NAV percentage.
UNTARGETED = "market"

# Characters that look like nothing but survive str.strip().
INVISIBLE = "\u200b\u200c\u200d\u2060\ufeff\u00a0"


def _blank(value) -> str:
    """A cell's text, with every spelling of empty flattened to "".

    `load()` passes `na_values=[""]`, so a blank cell comes back as NaN — and
    `nan or ""` is NaN, because NaN is truthy. `str()` on that gives the string
    "nan", which then reads as a real value. That bug cost two tests here; it
    is the same class as the `N/A` round-trip found in the 2026-09-27 audit.
    """
    if value is None or value is pd.NaT:
        return ""
    # pd.isna() covers float NaN, pd.NA and NaT alike; the earlier
    # isinstance(float) test let pd.NA through as the literal text "<NA>".
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):       # arrays and the like are never blank
        pass
    # U+200B and friends are NOT whitespace to str.strip(), so a zero-width
    # space passed as a name and as a body — defeating the two rules that are
    # meant never to be defeatable by whitespace.
    return str(value).strip().strip(INVISIBLE)


def _schema() -> pa.DataFrameSchema:
    """Built per call so a vocab edit applies without a reload."""
    return pa.DataFrameSchema(
        {
            "id": pa.Column(str, unique=True),
            "date": pa.Column(
                str,
                pa.Check(lambda s: s.map(_is_real_date),
                         error="date must be a real calendar date, ISO YYYY-MM-DD"),
            ),
            "recorded": pa.Column(
                str,
                pa.Check(lambda s: s.map(_is_real_date),
                         error="recorded must be a real calendar date, ISO YYYY-MM-DD"),
            ),
            "kind": pa.Column(str, _vocab_check("commentary_kind")),
            "target_type": pa.Column(str, _vocab_check("target_scope")),
            # Blank is legal here and checked against target_type by the
            # dataframe-level rule below: only `market` may leave it empty.
            "target_id": pa.Column(str, nullable=True),
            "origin": pa.Column(str, _vocab_check("origin")),
            "said_by": pa.Column(str, nullable=True),
            "body": pa.Column(
                str,
                pa.Check(lambda s: s.map(lambda v: bool(str(v).strip())),
                         error="body is the words themselves — it may not be blank"),
            ),
            "promoted_to": pa.Column(str, nullable=True),
            "note_ref": pa.Column(str, nullable=True),
        },
        checks=[
            pa.Check(
                lambda df: ~((df["kind"] == "thought") & (df["origin"] != "mine")),
                error="a thought is HIS view, so origin must be 'mine' — file "
                      "someone else's words as kind=commentary, or the origin "
                      "cut in scoreboard() stops meaning anything",
                element_wise=False,
            ),
            pa.Check(
                lambda df: ~((df["kind"] == "thought")
                             & df["said_by"].fillna("").str.strip().ne("")),
                error="a thought may not name someone else in said_by — if "
                      "someone said it, kind is commentary",
                element_wise=False,
            ),
            pa.Check(
                lambda df: ~((df["kind"] == "commentary") & (df["origin"] == "mine")),
                error="commentary is someone ELSE's words, so origin may not be "
                      "'mine' — extend ops/vocab/origin if the source is new",
                element_wise=False,
            ),
            pa.Check(
                lambda df: ~((df["target_type"] != UNTARGETED)
                             & df["target_id"].fillna("").str.strip().eq("")),
                error=f"target_id is blank — only target_type={UNTARGETED!r} "
                      f"names nothing; everything else must say what it is about",
                element_wise=False,
            ),
            pa.Check(
                lambda df: ~((df["target_type"] == UNTARGETED)
                             & df["target_id"].fillna("").str.strip().ne("")),
                error=f"target_type={UNTARGETED!r} is the whole market and takes "
                      f"no target_id — file it against a cluster or a sector if "
                      f"it is really about one",
                element_wise=False,
            ),
        ],
        strict=True,
        ordered=True,
    )


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Raises SchemaErrors naming every failing row and field (P2)."""
    return _schema().validate(df, lazy=True)


def load(path: Path = COMMENTARY_PATH) -> pd.DataFrame:
    """Read and validate the whole store. Missing or empty is a valid empty
    store, not an error — that is also what the commit guard sees for HEAD
    when the file is brand new.

    No `comment=` argument, for the same reason as claims.csv: a '#' inside
    his words ("NVDA becomes #1") would truncate the row on read, after a
    write that validated fine.
    """
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    # The header is checked even with no rows in it. Returning the canonical
    # empty frame first meant `"banana","split"` was a valid empty store and
    # committed cleanly — and because this table is deliberately exempt from
    # the append-only rule, nothing else would have caught a truncating or
    # column-mangling write.
    if list(df.columns) != COLUMNS:
        raise ValueError(
            f"{path}: columns are {list(df.columns)}\nexpected exactly "
            f"{COLUMNS}\nA row may be deleted from this store, but its shape "
            f"may not change."
        )
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    return validate(df)


def _write(df: pd.DataFrame, path: Path) -> None:
    """Quote every text field so '#', ';' or a newline cannot corrupt a row."""
    df.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def _next_id(df: pd.DataFrame) -> str:
    """The next cmNNNN id. `cm` not `c`: a comment must never be mistaken for
    a bet, and `c0001`/`cm0001` are different strings at a glance."""
    nums = pd.to_numeric(df["id"].str.removeprefix("cm"), errors="coerce").dropna()
    return f"cm{(int(nums.max()) + 1 if len(nums) else 1):04d}"


def check_target(target_type: str, target_id: str,
                 paths: dict[str, Path] | None = None) -> None:
    """The thing it is about must exist — except `market`, which names nothing.

    Delegates to `evidence.check_target()` for the four named scopes rather
    than reimplementing them: that function already resolves a claim, a
    cluster, a sector or an instrument, and already takes the `paths` override
    the commit guard needs to check STAGED copies.
    """
    if target_type == UNTARGETED:
        return
    evidence.check_target(target_type, target_id, paths)


def add(path: Path = COMMENTARY_PATH, **fields) -> str:
    """Validate one new row and append it. Returns the new id.

    The whole file is validated before the write, so a bad row can never land.
    """
    existing = load(path)
    row = {c: _blank(fields.get(c, "")) for c in COLUMNS}
    row["id"] = _next_id(existing)
    row["recorded"] = fields.get("recorded") or _date.today().isoformat()
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"Unknown field(s) {sorted(unknown)}. Known: {COLUMNS}.")

    check_refs(row["note_ref"] or None, "note_ref")
    # The cross-checks run in the schema so one bad hand-edit is caught on
    # every read too, not only on the path through this function.
    candidate = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    validate(candidate)
    # The foreign key is checked AFTER the shape: "target_type 'portfolio' is
    # not a known kind" is a better first message than a missing-row hunt.
    check_target(row["target_type"], row["target_id"])
    check_promoted_to(row["promoted_to"])
    _write(candidate, path)
    return row["id"]


def check_promoted_to(claim_id: str, claims_path: Path | None = None) -> None:
    """A thought may only be promoted to a bet that exists.

    Checked here AND at commit. The guard alone would mean a bad link sits in
    the working tree until he tries to save, and `promoted_to` is what stops
    one idea reading twice — once as a remark and once as the claim it became.
    """
    from src import claims as claims_mod

    text = str(claim_id or "").strip()
    if not text:
        return
    known = set(claims_mod.load_claims(
        claims_path or claims_mod.CLAIMS_PATH)["id"].astype(str))
    if text not in known:
        raise ValueError(
            f"No claim with id {text!r} — a thought may only be promoted to a "
            f"bet that exists. Write the claim first, then promote."
        )


def promote(commentary_id: str, claim_id: str,
            path: Path = COMMENTARY_PATH,
            claims_path: Path | None = None) -> None:
    """Record that a thought grew into a bet.

    Written once and never rewritten: two bets from one thought means the
    second was a different thought, and overwriting would lose the first link.
    """
    df = load(path)
    if commentary_id not in set(df["id"]):
        raise ValueError(f"No row with id {commentary_id!r}.")
    i = df.index[df["id"] == commentary_id][0]
    current = _blank(df.at[i, "promoted_to"])
    if current:
        raise ValueError(
            f"{commentary_id} was already promoted to {current!r}. A thought "
            f"becomes one bet; record the second as its own thought."
        )
    target = _blank(claim_id)
    if not target:
        raise ValueError(
            "promote() needs the id of the bet the thought became — a blank "
            "would leave the row promotable and say nothing."
        )
    check_promoted_to(target, claims_path)
    # _blank() and not the raw string: "  c0001  " validated, committed and
    # printed correctly while matching no claim id in a join.
    df.at[i, "promoted_to"] = target
    validate(df)
    _write(df, path)


def forget(commentary_id: str, path: Path = COMMENTARY_PATH) -> dict:
    """Remove one row. Returns what was removed, so a caller can show it.

    ⚠️ This is the ONLY store in the repo a row may leave, and that is the
    whole reason it is not part of claims.csv. Git keeps the previous version,
    so a deletion by mistake is recoverable — but nothing in the app brings it
    back, and it stops appearing everywhere at once.

    The skill must preview the row and get a yes before calling this.
    """
    # Read RAW, not through load(). A single bad hand-edited row makes load()
    # raise, which used to shut the repair path: /show and /note both broke,
    # and the only store designed to lose a row could not lose the row that was
    # breaking it. The only exit was editing the CSV by hand.
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError(f"No row with id {commentary_id!r} — the store is empty.")
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    if list(df.columns) != COLUMNS:
        raise ValueError(f"{path}: columns are {list(df.columns)}, expected {COLUMNS}.")
    if commentary_id not in set(df["id"]):
        raise ValueError(f"No row with id {commentary_id!r} — nothing to forget.")
    gone = {k: _blank(v) for k, v in
            df[df["id"] == commentary_id].iloc[0].to_dict().items()}
    kept = df[df["id"] != commentary_id].reset_index(drop=True)
    # What is left must still be a valid store — repairing one row may not be a
    # way to write a broken file. If more than one row is bad, the message
    # names the rest so he knows what else to forget.
    if not kept.empty:
        validate(kept)
    _write(kept, path)
    return gone


def about(name: str, scope: str | None = None,
          path: Path = COMMENTARY_PATH) -> pd.DataFrame:
    """Everything ever said about one thing, newest first.

    Matches on `target_id` whatever its scope, so `about("Metals")` finds the
    cluster rows and `about("NVDA")` the instrument ones without the caller
    having to know which it is.

    ⚠️ **Seven names are both a cluster and a sector** — Technology, Healthcare,
    Financials, Energy, Utilities, Consumer Staples and Real Estate. CLAUDE.md
    is emphatic that those are two different questions: the cluster is why he
    bought it, the sector is what he is exposed to. So this returns BOTH and
    leaves `target_type` on every row rather than merging them behind one
    heading — and `is_ambiguous()` tells a caller to print them apart. Pass
    `scope="cluster"` to ask only one of the two.
    """
    df = load(path)
    wanted = _blank(name)
    if df.empty or not wanted:
        # about("") used to return every market row — the one scope whose
        # target_id is legitimately blank. "Everything about nothing" must be
        # nothing, not the market.
        return df.iloc[0:0]
    hit = df["target_id"].map(_blank) == wanted
    if scope is not None:
        hit &= df["target_type"] == scope
    return df[hit].sort_values("date", ascending=False).reset_index(drop=True)


def is_ambiguous(name: str, data_dir: Path | None = None) -> bool:
    """True when this name is both one of his themes and a sector label.

    Seven are today. A caller showing `about(name)` must say which rows are
    which, or it silently answers a question he did not ask.
    """
    from src import clusters, sectors

    d = data_dir or (PROJECT_ROOT / "data")
    in_cluster = str(name) in set(clusters.load(d / "clusters.csv")["cluster_id"])
    in_sector = str(name) in set(sectors.load(d / "sectors.csv")["sector_id"])
    return in_cluster and in_sector


def on_market(path: Path = COMMENTARY_PATH) -> pd.DataFrame:
    """Everything said about the market as a whole, newest first."""
    df = load(path)
    if df.empty:
        return df
    return (df[df["target_type"] == UNTARGETED]
            .sort_values("date", ascending=False).reset_index(drop=True))


def since(start: str, path: Path = COMMENTARY_PATH) -> pd.DataFrame:
    """What he thought or heard on or after a date, newest first."""
    if not _is_real_date(start):
        raise ValueError(f"{start!r} is not a real date — use ISO YYYY-MM-DD.")
    df = load(path)
    if df.empty:
        return df
    return (df[df["date"] >= start]
            .sort_values("date", ascending=False).reset_index(drop=True))


def unattributed(path: Path = COMMENTARY_PATH) -> pd.DataFrame:
    """Commentary with nobody named. Visible, never blocking.

    He may genuinely not remember who said it, and refusing the row would
    lose the thought to protect a column. Reported the way
    `instruments.gaps()` reports an incomplete row.
    """
    df = load(path)
    if df.empty:
        return df
    blank = df["said_by"].fillna("").str.strip().eq("")
    return df[(df["kind"] == "commentary") & blank].reset_index(drop=True)
