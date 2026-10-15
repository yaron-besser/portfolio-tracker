"""The cluster reference table — one row per thematic or structural bucket.

Why this exists (Phase 2f). The cluster *name* already sat on the instrument,
which was right. But everything that *defines* a cluster — its type, its target
weight, its cap, its thesis — lived in prose in `notes/portfolio_thesis.md` and
`notes/clusters/*.md`. Prose cannot be checked against holdings, so it drifted:
four clusters were named but held nothing, ten named holdings had been closed,
fifteen held instruments appeared in no list at all, and `crypto.md` still
claimed the cluster was $9,240 when it was $4,100.

So a cluster becomes a row, and `instruments.cluster` becomes a foreign key
into it. A mistyped or invented cluster is then blocked at commit instead of
quietly becoming an eighteenth cluster.

**`current_pct` is deliberately not a column.** Today's weight is derived from
`positions.csv` by `weights()`. Writing a moving number down is exactly what
made the cluster notes wrong.

Usage:
    from src import clusters
    clusters.weights()                       # what each cluster is worth TODAY
    clusters.add_cluster("Water", layer="thematic")
    clusters.drift()                         # clusters you hold nothing in
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from src.claims import PROJECT_ROOT, _is_real_date, _vocab_check, split_paths, vocab

CLUSTERS_PATH = PROJECT_ROOT / "data" / "clusters.csv"

# `cluster_id` IS the readable name — "AI Infrastructure", not a slug. Same
# decision as instrument_id (ASML, not an ISIN): the key a person reads in a
# CSV should be the thing they call it. A separate `name` column would then
# hold the same fact twice, which is the duplication P7 forbids.
COLUMNS = [
    "cluster_id", "parent", "layer", "in_equity_base",
    "note_ref", "status", "added",
]


def _schema() -> pa.DataFrameSchema:
    """Built per call so vocabulary edits apply without a reload."""
    return pa.DataFrameSchema(
        {
            "cluster_id": pa.Column(str, unique=True),
            # Which group this cluster sits inside; blank means top-level.
            # The tree rules live in _check_tree(), not here: pandera checks
            # one column at a time, and every rule there is about how two rows
            # relate. Same shape as sectors.csv:parent.
            "parent": pa.Column(str, nullable=True),
            "layer": pa.Column(str, _vocab_check("cluster_layer")),
            # Does this cluster count toward the equity denominator? Cash is
            # not equity, so "Dry Powder is 58% of the equity book" is a
            # sentence with no meaning. That is a fact about the cluster, not
            # a rule, which is why it survived the 2026-08-14 deletion below.
            "in_equity_base": pa.Column(bool, coerce=True),
            "note_ref": pa.Column(str, nullable=True),
            "status": pa.Column(str, _vocab_check("cluster_status")),
            "added": pa.Column(
                str,
                pa.Check(lambda s: s.map(_is_real_date),
                         error="added must be a real calendar date, ISO YYYY-MM-DD"),
            ),
        },
        strict=True,
        ordered=True,
    )


def _parents(df: pd.DataFrame) -> pd.Series:
    """cluster_id -> its parent, blank for top-level. Blank, never NaN."""
    return (df.set_index("cluster_id")["parent"]
              .fillna("").astype(str).str.strip())


def _check_tree(df: pd.DataFrame) -> None:
    """The five rules that keep `parent` a one-level tree instead of a tangle.

    Not in the pandera schema because every one of them is about how two rows
    relate, and pandera checks a column at a time. Raised loudly and named
    with the fix (P2) — a silently broken tree would make every number in
    `weights()` wrong in a way nothing on screen would reveal.
    """
    if df.empty:
        return
    ids = set(df["cluster_id"])
    parent = _parents(df)
    layer = df.set_index("cluster_id")["layer"]
    base = df.set_index("cluster_id")["in_equity_base"]

    for cid, p in parent.items():
        if not p:
            continue
        if p == cid:                                              # rule 2
            raise ValueError(
                f"Cluster {cid!r} names itself as its parent. A cluster "
                f"cannot be inside itself."
            )
        if p not in ids:                                          # rule 1
            raise ValueError(
                f"Cluster {cid!r} has parent {p!r}, which is not a cluster "
                f"in data/clusters.csv.\n"
                f"  Add it with:  clusters.add_cluster({p!r}, layer='group')"
            )
        # Rule 3, both directions. Checking only the parent's parent let a
        # CHILDLESS group be nested one level down and load clean — the table
        # promised "a group cannot sit inside a group" and nothing checked the
        # child's own layer (found by review, 2026-08-16).
        if parent[p]:
            raise ValueError(
                f"Cluster {cid!r} sits inside {p!r}, which is itself inside "
                f"{parent[p]!r}. Groups are one level deep — put {cid!r} "
                f"directly under {parent[p]!r}, or take {p!r} out of it."
            )
        if layer[cid] == "group":
            raise ValueError(
                f"Cluster {cid!r} is a group and cannot sit inside {p!r}. "
                f"Groups are one level deep — take {cid!r} out of {p!r}, or "
                f"move its children up into {p!r} directly."
            )
        if bool(base[cid]) != bool(base[p]):                      # rule 4
            raise ValueError(
                f"Cluster {cid!r} and its group {p!r} disagree about "
                f"in_equity_base. A group and its children must agree, or "
                f"'% of the equity book' means two different things "
                f"depending on which row you read."
            )

    for cid in sorted(set(parent[parent != ""])):                 # rule 5
        if layer[cid] != "group":
            raise ValueError(
                f"Cluster {cid!r} has clusters inside it, so its layer must "
                f"be 'group', not {layer[cid]!r}.\n"
                f"  Fix it in data/clusters.csv, or take its children out."
            )


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Validate the cluster table. Raises naming every failing row (P2)."""
    out = _schema().validate(df, lazy=True)
    _check_tree(out)
    return out


def load(path: Path = CLUSTERS_PATH) -> pd.DataFrame:
    """Read and validate. A missing/empty file is an empty table, not an error."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)
    # `in_equity_base` is deliberately NOT forced to str. Read as text it
    # arrives as "False", and bool("False") is True — the flag would be
    # silently on for every row and "cash is a share of equity" would look fine.
    text_cols = [c for c in COLUMNS if c != "in_equity_base"]
    df = pd.read_csv(path, dtype={c: str for c in text_cols},
                     keep_default_na=False, na_values=[""])
    if "in_equity_base" in df.columns and df["in_equity_base"].dtype == object:
        df["in_equity_base"] = (
            df["in_equity_base"].astype(str).str.strip().str.lower().map(
                {"true": True, "false": False, "1": True, "0": False,
                 "yes": True, "no": False}
            )
        )
        if df["in_equity_base"].isna().any():
            bad = df.loc[df["in_equity_base"].isna(), "cluster_id"].tolist()
            raise ValueError(
                f"in_equity_base must be true or false; unreadable for {bad}."
            )
    return validate(df) if not df.empty else pd.DataFrame(columns=COLUMNS)


def get(cluster_id: str, path: Path = CLUSTERS_PATH) -> dict:
    """One cluster's row as a dict. Raises with the command to add it (P2)."""
    df = load(path)
    row = df[df["cluster_id"] == cluster_id]
    if row.empty:
        raise KeyError(
            f"Unknown cluster {cluster_id!r}.\n"
            f"  Add it with:  clusters.add_cluster({cluster_id!r}, "
            f"layer='thematic')\n"
            f"  Allowed layers: {vocab('cluster_layer')}"
        )
    return row.iloc[0].where(row.iloc[0].notna(), "").to_dict()


def add_cluster(
    cluster_id: str, layer: str, path: Path = CLUSTERS_PATH, **optional
) -> str:
    """Add a cluster with the minimum needed to use it tonight.

    Only the name and its layer are required — target and cap decided later.
    Same escape hatch as `instruments.add_instrument()`, for the same reason:
    a missing target must never stop a holding being classified.
    """
    df = load(path)
    if (df["cluster_id"] == cluster_id).any():
        raise ValueError(f"Cluster {cluster_id!r} already exists.")
    unknown = set(optional) - set(COLUMNS)
    if unknown:
        raise ValueError(f"Unknown field(s) {sorted(unknown)}. Columns are: {COLUMNS}")

    # Numeric columns default to None, not "": an empty string cannot be
    # coerced to a float, so a cluster added without a target would make the
    # whole table unreadable on the next load.
    row = {
        c: optional.get(c, "") for c in COLUMNS
    }
    row.update(
        cluster_id=cluster_id,
        layer=layer,
        in_equity_base=optional.get("in_equity_base", True),
        status=optional.get("status", "active"),
        added=optional.get("added", date.today().isoformat()),
    )
    _check_note_ref(row["note_ref"])

    candidate = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    validate(candidate)
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return cluster_id


def check_known(ids, path: Path = CLUSTERS_PATH) -> None:
    """Raise unless every cluster id exists — the foreign key instruments uses."""
    known = set(load(path)["cluster_id"])
    missing = sorted({str(i) for i in ids if str(i).strip() and str(i) not in known})
    if missing:
        raise ValueError(
            f"Unknown cluster(s) {missing} — not in data/clusters.csv.\n"
            f"  Add one with:  clusters.add_cluster('{missing[0]}', "
            f"layer='thematic')"
        )


def children(cluster_id: str, path: Path = CLUSTERS_PATH) -> list[str]:
    """The clusters sitting inside this one — empty for a leaf."""
    df = load(path)
    if df.empty:
        return []
    return df.loc[_parents(df).values == cluster_id, "cluster_id"].tolist()


def set_parent(cluster_id: str, parent: str, path: Path = CLUSTERS_PATH) -> None:
    """Move a cluster into a group, or out of one with `parent=""`.

    Separate from `add_cluster()` because grouping happens *after* both rows
    exist: a group is necessarily empty the moment it is created, so the only
    workable order is create-then-assign. Validates the whole table before
    writing, so a refused move leaves the file exactly as it was.
    """
    df = load(path)
    if cluster_id not in set(df["cluster_id"]):
        raise KeyError(
            f"Unknown cluster {cluster_id!r}.\n"
            f"  Add it with:  clusters.add_cluster({cluster_id!r}, "
            f"layer='thematic')"
        )
    candidate = df.copy()
    candidate.loc[candidate["cluster_id"] == cluster_id, "parent"] = parent
    validate(candidate)                    # raises before anything is written
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def _check_note_ref(cell) -> None:
    """A note_ref must point at a file that exists.

    Shared by add_cluster() and set_note_ref() so the two can never disagree
    about what a valid link is.
    """
    for p in split_paths(cell):
        if not (PROJECT_ROOT / p).is_file():
            raise ValueError(
                f"note_ref path {p!r} does not point to a file that exists."
            )


def _held_ids(positions_path: Path | None = None) -> set[str]:
    """instrument_ids with a live position, for the retire check."""
    p = positions_path or (PROJECT_ROOT / "data" / "positions.csv")
    if not Path(p).exists():
        return set()
    pos = pd.read_csv(p, dtype=str, keep_default_na=False)
    return {str(i).strip() for i in pos.get("instrument_id", []) if str(i).strip()}


def set_status(cluster_id: str, status: str,
               path: Path = CLUSTERS_PATH,
               positions_path: Path | None = None) -> None:
    """Retire a theme, or bring one back.

    Nothing could write this column after `add_cluster()` set it, which is why
    `clusters.status` had drifted: Real Estate and Fixed Income still said
    `active` holding nothing, because he exited XLRE and TLT and no function
    could say so (open question #4 in docs/data-model-open-questions.md).

    Vocabulary lives in `ops/vocab/cluster_status`, so adding a value is an
    appended line rather than a code change.
    """
    from src import instruments as instruments_mod

    df = load(path)
    if cluster_id not in set(df["cluster_id"]):
        raise KeyError(
            f"Unknown cluster {cluster_id!r}.\n"
            f"  Add it with:  clusters.add_cluster({cluster_id!r}, "
            f"layer='thematic')"
        )
    text = "" if status is None else str(status).strip()

    if text == "retired":
        # Retiring something that still holds money leaves a slot everyone
        # reads as closed while weights() still shows the money in it — and
        # notes.status() drops the row, so its note requirement disappears
        # too. Found by review 2026-09-28.
        try:
            inst = instruments_mod.load()
            members = set(inst.loc[inst["cluster_id"] == cluster_id,
                                   "instrument_id"])
        except Exception:                  # a caller with its own tiny store
            members = set()
        if members:
            held = _held_ids(positions_path)
            still = sorted(members & held)
            if still:
                raise ValueError(
                    f"{cluster_id!r} still holds {still}. Move them first — a "
                    f"retired cluster that shows money is a slot the record "
                    f"calls closed and the numbers call open."
                )
        kids = df.loc[df["parent"].fillna("") == cluster_id]
        live = sorted(kids.loc[kids["status"] != "retired", "cluster_id"])
        if live:
            raise ValueError(
                f"{cluster_id!r} is a group and {live} are still active inside "
                f"it. Retire or move the children first."
            )

    candidate = df.copy()
    candidate.loc[candidate["cluster_id"] == cluster_id, "status"] = text
    validate(candidate)                    # vocabulary + the tree rules
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def set_note_ref(cluster_id: str, note_ref: str,
                 path: Path = CLUSTERS_PATH) -> None:
    """Point a cluster at its note.

    Nothing in `src/` wrote this column, so the only route was hand-editing a
    file the commit guard watches — which is what both cluster notes written on
    2026-09-27 needed. `cluster_summary()` reads note state from THIS column,
    not from the file being on disk, so a note written without it reports as
    "missing" for ever.

    Validates the whole table before writing, so a refused link leaves the file
    exactly as it was. Modelled on set_parent().
    """
    df = load(path)
    if cluster_id not in set(df["cluster_id"]):
        raise KeyError(
            f"Unknown cluster {cluster_id!r}.\n"
            f"  Add it with:  clusters.add_cluster({cluster_id!r}, "
            f"layer='thematic')"
        )
    _check_note_ref(note_ref)
    candidate = df.copy()
    candidate.loc[candidate["cluster_id"] == cluster_id, "note_ref"] = note_ref
    validate(candidate)                    # raises before anything is written
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


def weights(
    path: Path = CLUSTERS_PATH, positions_path: Path | None = None,
    instruments_path: Path | None = None,
) -> pd.DataFrame:
    """What each cluster is worth **today**, derived from positions.

    Never stored. `notes/clusters/crypto.md` recorded "8.1% ($9,240)" once and
    it was $4,100 by the time anyone read it again; a number that moves belongs
    in a function, not a column.

    Two denominators, because one would lie. `pct_of_nav` is share of
    everything. `pct_of_equity` is share of the equity book only — the basis
    that makes a weight comparable between clusters — and is left
    blank for a cluster that is not part of that base, because "cash is 58% of
    equity" is a sentence with no meaning.

    Both denominators come from `book.nav()`, which is the one definition of
    what the book is worth. They were computed here until 2026-10-06, and
    `exposure.by_sector()` computed a third of its own.

    ⚠️ **Unclassified money is excluded from both denominators now, and that
    is a real change, not a refactor.** The old code grouped positions with
    `dropna=False`, which kept a lot whose `instrument_id` instruments.csv
    does not know inside both `nav` and `equity` — counted, but filed under
    no cluster. `book.lots()` drops that lot instead, because money the repo
    cannot place has no theme to carry a percentage of. The honest number is
    smaller: every `pct_of_nav` and `pct_of_equity` below moves the moment
    `book.unclassified()` is non-empty, which is the normal state right after
    `ibkr_sync` auto-adds a freshly bought ticker — check that frame before
    reading a weight as settled.

    ⚠️ This split once existed to check a 25% cluster cap from
    `portfolio_thesis.md` §3, archived 2026-08-14 — the sentence naming that
    cap was deleted with the column, leaving a warning about a number the
    reader could no longer see. **No cap is in force** until Yaron writes the
    replacement. The two-denominator split stays because it is still the
    honest way to read a weight, not
    because the cap survives.

    ⚠️ **Summing `market_value` over every row over-counts.** A group row
    carries its children's money as well as its own name, so every grouped
    cluster is counted twice — $166,648 against a real $124,199 on today's
    book, and 2× only in the limit where every cluster sits in a group. The
    rows that add up to NAV are the ones where `is_group` is False. That is
    what the flag is for.
    """
    from src import book

    lots = book.lots(positions_path=positions_path, clusters_path=path,
                     instruments_path=instruments_path)
    by = lots.groupby("cluster_id")["market_value"].sum()

    df = load(path).set_index("cluster_id")
    parent = df["parent"].fillna("").astype(str).str.strip()

    # Positions only ever name a leaf — a holding filed straight into a group
    # is refused by the commit guard — so `by` never contains a group, and the
    # two denominators below cannot double-count.
    direct = {cid: float(by.get(cid, 0.0)) for cid in df.index}
    total = dict(direct)
    for cid, p in parent.items():
        if p:
            total[p] = total.get(p, 0.0) + direct[cid]

    nav = book.nav("everything", positions_path=positions_path, clusters_path=path,
                   instruments_path=instruments_path)
    equity = book.nav("pure-equity", positions_path=positions_path,
                      clusters_path=path, instruments_path=instruments_path)
    out = []
    for cid, row in df.iterrows():
        mv = total[cid]
        out.append({
            "cluster_id": cid,
            "parent": parent[cid],
            "layer": row["layer"],
            "is_group": row["layer"] == "group",
            "market_value": round(mv, 2),
            "pct_of_nav": round(mv / nav * 100, 2) if nav else 0.0,
            "pct_of_equity": (round(mv / equity * 100, 2)
                              if equity and row["in_equity_base"] else float("nan")),
        })

    # Sort so a group is followed by its own children rather than by whatever
    # else happens to be worth a similar amount. Key, outermost first: which
    # family (by the family's total), the group row ahead of its children,
    # then each child by its own money.
    def _key(r: dict) -> tuple:
        cid = r["cluster_id"]
        top = r["parent"] or cid
        return (-total[top], top, 0 if r["is_group"] else 1, -total[cid], cid)

    return pd.DataFrame(sorted(out, key=_key)).reset_index(drop=True)


TREE_COLUMNS = ["level", "cluster_id", "instrument_id", "label", "parent",
                "market_value", "size", "pct_of_basis", "is_empty", "note_ref",
                "layer"]

#: Sentinel `cluster_id`/`label` for `tree()`'s synthetic bucket — basis money
#: that names no live theme at all (see `tree()`'s docstring for the three
#: ways a lot lands here). Never written to `data/clusters.csv` and never
#: returned by `load()`, so a renderer can recognise it with one equality
#: check instead of guessing from shape.
UNFILED_LABEL = "Not in a live theme"


def tree(basis: str = "everything", path: Path = CLUSTERS_PATH,
         positions_path: Path | None = None,
         instruments_path: Path | None = None) -> pd.DataFrame:
    """The spread by theme, three levels deep, in the order it is drawn.

    Spec D5: group -> theme -> the actual tickers, ranked, everything
    collapsed. `weights()` answers the first two levels and this answers all
    three, under any one of the three bases (D6).

    **Row order is the drawing order.** `page.py` shapes and never re-sorts, so
    the ranking lives here: which family (by the family's total), the group row
    ahead of its children, each child by its own money, each holding under its
    cluster by its own money. Same key as `weights()`, extended one level.

    **`level` is not depth — `parent` is.** A top-level family's own row and a
    group's child are both `level="cluster"`; nothing in `level` tells them
    apart, only `parent` does (blank for the first, the group's name for the
    second). A renderer indents by walking `parent` up until it is blank —
    found by review the hard way, reading a top-level theme as though it sat
    inside the group printed above it. `parent` means one thing at every
    level, the row one level up: a group's parent is always blank; a
    cluster's parent is its group, or blank if it has none; a holding's
    parent is the cluster that holds it — never that cluster's own group,
    which is one level too far up.

    ⚠️ **Summing every row over-counts** — a group row carries its children's
    money as well as its own name. The rows that add up to the basis are the
    `holding` rows, **the unfiled bucket below included** — nothing else in
    this frame should be summed.

    Three states that look alike on a bar chart and are not:

      * a live theme holding nothing — shown, greyed, LOCALLY last: after its
        own group's other children if it sits in one, after every other
        family if it does not. Not globally last. Stepping out of a theme is
        part of the spread (spec, panel 2), but a group is still its
        children's context, and everything starts collapsed — expanding
        Technology must show all four of its themes together, not three with
        the fourth stranded under an unrelated family further down the page.
      * a theme he RETIRED — not shown at all. `drift()` already draws this
        line: retiring Metals and Water otherwise added two permanent rows
        saying exactly what `retired` already said.
      * a theme the BASIS emptied — Dry Powder holds real money and holds
        nothing under `pure-equity`. Not greyed and not called empty, because
        the basis is on screen and "you hold nothing in it" would be false.

    **A fourth state this function must never produce: money with no row at
    all.** A lot inside the basis lands outside every live theme three ways —
    `instruments.add_instrument()` deliberately allows a blank `cluster_id` so
    a missing theme never blocks recording a trade; a row can name a cluster
    `data/clusters.csv` does not have; or it can still name one he has since
    retired, which is already filtered out of the tree above. All three used
    to simply vanish — dropped by the dict comprehensions below that key only
    on `df.index` — which is the gap `instruments.set_cluster()`'s own
    docstring named on 2026-09-28 and left unfixed, and it is the normal state
    of every freshly bought ticker until he classifies it. The honesty
    contract (spec) is explicit: "which money could not be classified at all,
    shown as unclassified rather than silently redistributed." So every such
    lot is collected instead of dropped, into one synthetic trailing `cluster`
    row labelled `UNFILED_LABEL`, with ordinary `holding` rows under it exactly
    like a real cluster. `is_empty` is False on it — it holds real money, which
    is the whole point — and it is appended after everything else, including
    the greyed, wholly-empty families above. Omitted entirely when there is
    nothing to put in it.

    🚨 **A fifth way money with no row happened anyway — fix, pre-push
    review 2026-10-07 (finding 3).** A live cluster whose own PARENT was
    retired (the child's own status untouched) is none of the three cases
    above: its `cluster_id` IS known (so the unfiled bucket's foreign-key
    check does not catch it), and its `parent` is non-blank (so it used to
    fail the old top-level test too) — it is simply no longer anyone's
    declared child, because its parent's row was filtered out of `df`
    before the parent map was built. Fixed by promoting such a cluster to
    its own top-level row (see `tops`, below) rather than merging it into
    `UNFILED_LABEL` — it is a real, named, live theme, not unclassified
    money. `tree_coverage_pct()` is the companion fix: a defect shaped like
    this one should never again be invisible just because the By-theme
    panel's own Total row assumed its rows always add up.
    """
    from src import book
    from src import instruments as instruments_mod

    book.check_basis(basis)
    lots = book.lots(positions_path=positions_path, clusters_path=path,
                     instruments_path=instruments_path)
    kept = lots[lots.apply(lambda r: book.in_basis(r, basis), axis=1)] \
        if not lots.empty else lots
    total = float(kept["market_value"].sum()) if not kept.empty else 0.0

    inst = instruments_mod.load(
        instruments_path or instruments_mod.INSTRUMENTS_PATH
    ).fillna("").set_index("instrument_id")

    df = load(path)
    df = df[df["status"] != "retired"].set_index("cluster_id")
    parent = df["parent"].fillna("").astype(str).str.strip()
    # `load()` reads a blank note_ref as the float NaN (same `na_values=[""]`
    # that makes a blank `parent` need the `.fillna("")` just above) — left
    # unfilled, `row()` below does `str(df.at[cid, "note_ref"])` and gets the
    # literal four-character string "nan", which `page._href()` then treats
    # as a real, non-blank ref and turns into a clickable link to "../nan".
    # A blank note_ref must mean no link at all — a bar he cannot click is
    # itself information (nothing is written there yet) — so this is filled
    # here the same way `inst`, two lines up, already is.
    df["note_ref"] = df["note_ref"].fillna("")
    known_cids = set(df.index)

    # Money per cluster, in this basis, and — separately — in the whole book.
    # Both are needed: the first is what is drawn, the second is what says
    # whether a theme is EMPTY or merely outside this basis.
    if kept.empty:
        per_cluster = {}
    else:
        per_cluster = kept.groupby("cluster_id")["market_value"].sum().to_dict()
    everywhere = (lots.groupby("cluster_id")["market_value"].sum().to_dict()
                  if not lots.empty else {})

    direct = {cid: float(per_cluster.get(cid, 0.0)) for cid in df.index}
    held_anywhere = {cid: float(everywhere.get(cid, 0.0)) for cid in df.index}
    family = dict(direct)
    family_anywhere = dict(held_anywhere)
    for cid, p in parent.items():
        if p and p in family:
            family[p] = family.get(p, 0.0) + direct[cid]
            family_anywhere[p] = family_anywhere.get(p, 0.0) + held_anywhere[cid]

    def pct(value: float) -> float:
        return round(value / total * 100, 2) if total else 0.0

    def row(level, cid, iid, label, mv, note_ref, layer, empty,
           force_top=False):
        # A holding's parent is the cluster that holds it — `cid` here IS
        # that cluster, never the cluster's own group, which is one level
        # too far up. A group's or cluster's parent instead comes from the
        # tree in data/clusters.csv, where a group's is always blank.
        #
        # `force_top` (fix, pre-push review 2026-10-07, finding 3): every
        # row built for a `tops` entry passes this, because `tops` now
        # includes a live cluster promoted up from a RETIRED parent — its
        # raw `parent[cid]` still reads that parent's id (e.g.
        # "Technology"), which is no longer a row anywhere in this tree.
        # `page._tree_depths()` walks `parent` to indent a bar one level
        # per step up (trap 1); left un-blanked, a promoted orphan would
        # read as a child of a group that never appears, and indent one
        # level under nothing. Every genuinely top-level row already has
        # `parent.get(cid, "") == ""`, so this only ever changes the
        # orphan's own row, never a real child's.
        if force_top:
            parent_value = ""
        else:
            parent_value = cid if level == "holding" else parent.get(cid, "")
        return {"level": level, "cluster_id": cid, "instrument_id": iid,
                "label": label, "parent": parent_value,
                "market_value": round(mv, 2), "size": round(abs(mv), 2),
                "pct_of_basis": pct(mv), "is_empty": empty,
                "note_ref": note_ref, "layer": layer}

    def holding_rows(mine, cid, layer):
        """`holding` rows for one pre-filtered slice of `kept`, by magnitude.

        Shared by a real cluster's own holdings and the unfiled bucket's, so
        the two can never rank differently or drift apart in shape.
        """
        if mine.empty:
            return []
        per = (mine.groupby("instrument_id")["market_value"].sum()
                   .sort_values(key=lambda s: s.abs(), ascending=False))
        return [row("holding", cid, iid, str(inst.at[iid, "name"]) or iid,
                    float(v), str(inst.at[iid, "note_ref"]), layer, False)
                for iid, v in per.items()]

    def holdings_in(cid):
        if kept.empty:
            return []
        return holding_rows(kept[kept["cluster_id"] == cid], cid,
                            str(df.at[cid, "layer"]))

    # A family is a top-level cluster and everything under it. Order by the
    # family's own total so a group's children stay with it, not with whatever
    # else happens to be worth a similar amount.
    #
    # 🚨 Fix, pre-push review 2026-10-07 (finding 3) — the fourth state this
    # function's own docstring says must never happen. `df` just filtered out
    # every RETIRED row before this point; a live cluster whose PARENT was
    # retired (the child itself untouched) still names that parent in
    # `parent[cid]` — non-blank, so the old `not parent[cid]` test skipped
    # it — but the parent's own row is gone from `df.index`, so it is never
    # anyone's declared "kid" below either (that loop only ever visits a
    # `top`'s children). Its own id is still known (`known_cids`, built from
    # this same `df.index`), so the unfiled bucket's foreign-key check never
    # catches it. It landed in neither place: reproduced on the real book,
    # 2026-10-07, the Technology group retired with its four children left
    # active — $40,300.50, about 35% of the basis, with no row anywhere.
    # `validate()`'s own `_check_tree()` guarantees `parent[cid]`, when
    # non-blank, names SOME row in the full (unfiltered) table — see
    # `_check_tree()` rule 1 — so "non-blank but missing from this filtered
    # `df.index`" can only mean "retired", never "never existed". Promoted
    # to its own top-level row rather than merged into the unfiled bucket:
    # it is a real, named, live theme, not unclassified money.
    tops = [cid for cid in df.index
            if not parent[cid] or parent[cid] not in df.index]
    tops.sort(key=lambda cid: (-abs(family[cid]), cid))

    out, trailing = [], []
    for top in tops:
        is_group = str(df.at[top, "layer"]) == "group"
        # Empty means he holds nothing in it ANYWHERE, not merely nothing in
        # this basis.
        empty = family_anywhere[top] == 0
        rows = [row("group" if is_group else "cluster", top, "", top,
                    family[top], str(df.at[top, "note_ref"]),
                    str(df.at[top, "layer"]), empty, force_top=True)]
        if is_group:
            # Empty-anywhere children sort LOCALLY last, after their live
            # siblings — not globally last, which would strand one under an
            # unrelated family further down the page instead of leaving a
            # collapsed group's children together. Found by review: an empty
            # AI Applications, nested in Technology, landed ahead of a live
            # Precious Metals family because the old key (money magnitude
            # alone) said nothing about emptiness — it only happened to agree
            # with it when every zero-money child really was empty.
            kids = [c for c in df.index if parent[c] == top]
            kids.sort(key=lambda c: (held_anywhere[c] == 0, -abs(direct[c]), c))
            for kid in kids:
                rows.append(row("cluster", kid, "", kid, direct[kid],
                                str(df.at[kid, "note_ref"]),
                                str(df.at[kid, "layer"]),
                                held_anywhere[kid] == 0))
                rows.extend(holdings_in(kid))
        else:
            rows.extend(holdings_in(top))
        (trailing if empty else out).extend(rows)

    # The fourth state: basis money that names no live cluster at all — blank,
    # a cluster data/clusters.csv does not have, or one he has since retired
    # (already outside `known_cids`, filtered above). Collected rather than
    # dropped, appended after every greyed family, because it is real money
    # and the honesty contract (spec) requires it stay on screen.
    unfiled = []
    if not kept.empty:
        stray = kept[~kept["cluster_id"].isin(known_cids)]
        if not stray.empty:
            bucket_total = float(stray["market_value"].sum())
            unfiled = [row("cluster", UNFILED_LABEL, "", UNFILED_LABEL,
                          bucket_total, "", "", False)]
            unfiled.extend(holding_rows(stray, UNFILED_LABEL, ""))

    return pd.DataFrame(out + trailing + unfiled,
                        columns=TREE_COLUMNS).reset_index(drop=True)


def tree_coverage_pct(basis: str = "everything", path: Path = CLUSTERS_PATH,
                      positions_path: Path | None = None,
                      instruments_path: Path | None = None) -> float:
    """What `tree()`'s own rows actually add up to, as a % of the basis.

    Added, pre-push review 2026-10-07 (finding 3), alongside the `tree()`
    fix for the orphan-parent bug: a live cluster whose parent was retired
    fell through every branch of `tree()` and left $40,300.50 — about 35%
    of the real basis — with no row anywhere, while the By-theme panel's
    Total row kept reading 100% because `ops/build_page.py` hard-coded it.
    That row is the one place on the page that could have revealed the
    shortfall, and a hard-coded constant is structurally incapable of
    revealing anything. This is the derived number it needed: even after
    THIS orphan bug is fixed, a different defect shaped like it should
    never again be invisible just because nothing checked.

    Walks `tree()`'s own TOP-LEVEL rows only — `level != "holding"` and
    `parent == ""` — the identical partition `weights()`'s own docstring
    already establishes to avoid double-counting ("the rows that add up to
    NAV are the ones where `is_group` is False" there; here, a group's row
    already carries its children's money, so only a family's own top row,
    never its kids too, may be summed). `book.nav(basis)` is the
    denominator — the same call `theme_total()` already reports as the
    panel's own dollar total, so the percentage and the dollar figure next
    to it are guaranteed to describe the same basis.

    Reads 100.0 when `tree()` is complete, by construction (every dollar in
    `basis` belongs to exactly one top-level row, or the basis is empty).
    Anything else means money is missing from every row the By-theme panel
    draws — the fact this function exists to make visible rather than
    assumed away.
    """
    from src import book

    book.check_basis(basis)
    nav = book.nav(basis, positions_path=positions_path,
                   clusters_path=path, instruments_path=instruments_path)
    if not nav:
        return 0.0
    rows = tree(basis=basis, path=path, positions_path=positions_path,
               instruments_path=instruments_path)
    if rows.empty:
        return 0.0
    top_level = rows[(rows["level"] != "holding") & (rows["parent"] == "")]
    covered = float(top_level["market_value"].sum())
    return round(covered / nav * 100, 2)


def drift(path: Path = CLUSTERS_PATH, positions_path: Path | None = None) -> pd.DataFrame:
    """Clusters you name but hold nothing in.

    That is now the only thing this reports. The cap and target flags were
    **deleted on 2026-08-15** at Yaron's instruction — "all the rules of the
    twenty five percent cap and all of those stuff are irrelevant… we are
    building a new system, we're just using what we have as inspiration". The
    columns went with them.

    Nothing here is a rule. When the new constitution exists, whatever sizing
    it defines gets built fresh against it rather than inherited from a
    document nobody re-adopted. Old values: git history, `data/clusters.csv`
    before 2026-08-15.
    """
    w = weights(path, positions_path)
    # A RETIRED cluster holds nothing by definition, so reporting it here is
    # noise in the one report that exists to find forgotten themes. Retiring
    # Metals and Water on 2026-09-28 added two permanent rows saying exactly
    # what `retired` already says (found by review the same day).
    live = load(path)
    retired = set(live.loc[live["status"] == "retired", "cluster_id"])
    w = w[~w["cluster_id"].isin(retired)]
    # Two different empties. A cluster you hold nothing in is the original
    # case. A group with no clusters inside it is the one the design traded
    # the blocking rule for — "an empty group is reported by drift()" — and
    # the first version reported neither it nor anything through it, because
    # a childless group has no children to be reported by (review 2026-08-16).
    kids = {c: children(c, path) for c in w.loc[w["is_group"], "cluster_id"]}
    rows = []
    for _, r in w.iterrows():
        cid = r["cluster_id"]
        if r["is_group"]:
            if not kids[cid]:
                rows.append({"cluster_id": cid, "pct_of_equity": r["pct_of_equity"],
                             "why": "a group with no clusters inside it"})
            # A group that HAS children needs no row of its own: if they are
            # all empty, each is already named below on its own account.
            continue
        if r["market_value"] == 0:
            rows.append({"cluster_id": cid, "pct_of_equity": r["pct_of_equity"],
                         "why": "named but you hold nothing in it"})
    return pd.DataFrame(rows)


def set_layer(cluster_id: str, layer: str,
              path: Path = CLUSTERS_PATH) -> None:
    """Change what KIND of slot a cluster is.

    `layer` is the difference between "a theme I have a view on" and "one of
    the eleven S&P sectors I hold so I am never at zero". Nothing could write
    it after `add_cluster()`, so when Healthcare turned out on 2026-09-29 to be
    the second kind and not the first, the only route was hand-editing a file
    the commit guard watches.

    Vocabulary lives in `ops/vocab/cluster_layer`, and `validate()` enforces
    both it and the tree rules — a group cannot stop being a group while
    children still point at it. Modelled on set_note_ref().
    """
    df = load(path)
    if cluster_id not in set(df["cluster_id"]):
        raise KeyError(
            f"Unknown cluster {cluster_id!r}.\n"
            f"  Add it with:  clusters.add_cluster({cluster_id!r}, "
            f"layer='thematic')"
        )
    text = "" if layer is None else str(layer).strip()

    if text == "group":
        # validate() only knows the parent tree; nothing in it can see
        # instruments.cluster_id. Without this, promoting a cluster that holds
        # positions succeeds, weights() drops its money out of the rows that
        # sum to NAV, and ops/hooks/pre-commit then refuses EVERY later commit
        # with "filed directly into a group" — recoverable only by hand-editing
        # the file this function exists to stop him hand-editing.
        # instruments.set_cluster() already refuses the mirror image.
        from src import instruments as instruments_mod
        try:
            inst = instruments_mod.load()
            members = sorted(inst.loc[inst["cluster_id"] == cluster_id,
                                      "instrument_id"])
        except Exception:                  # a caller with its own tiny store
            members = []
        if members:
            raise ValueError(
                f"{cluster_id!r} holds {members}, and a group holds no "
                f"positions of its own. Move them to another cluster first."
            )

    candidate = df.copy()
    candidate.loc[candidate["cluster_id"] == cluster_id, "layer"] = text
    validate(candidate)                    # raises before anything is written
    candidate.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)


# ─── Task 6: two themes can be one bet ─────────────────────────────────────
#
# A cluster records WHY he bought something. Two different whys can buy the
# same company — AI Infrastructure (a thematic bet) and Technology (a breadth
# slot, one of the SPY sectors he holds so he is never at zero) are the design
# session's own example, and nothing before this existed said how much of one
# is really the other. Spec panel 6.

OVERLAP_COLUMNS = ["cluster_a", "cluster_b", "shared_value", "pct_of_a",
                   "pct_of_b", "top_names", "is_floor"]


def overlap(basis: str = "everything", **paths) -> pd.DataFrame:
    """For each pair of themes, how much of their money is in the same companies.

    Spec panel 6: **two themes can be one bet.** A cluster says why he bought
    something, and two different whys can buy the same company — AI
    Infrastructure and Technology (broad) are a theme and a breadth slot, and
    nothing in the record says how much of each is the other.

    The shared value of a company is the **smaller** of the two themes' shares
    of it. Summing them would double count the same dollar; averaging would be
    a number with no meaning.

    One row per pair, not two. Pairs with nothing in common are left out — a
    row of zero for every pair makes a panel nobody reads, and sharing nothing
    is the normal case.

    `pct_of_a` and `pct_of_b` divide by each theme's **whole market value in
    `basis`** — not its company-only money. Fix round 1 (reviewer, 2026-10-07)
    reversed the first version of this choice. The company-only total looks
    like the more careful base at first (only count the part that is even
    comparable), but that reasoning runs the wrong way: cash, bullion and a
    futures fund genuinely are NOT the same bet as another theme, which is
    exactly why they belong in the denominator, as part of what is NOT
    shared. The decisive argument is comparability, not purity — company
    money is close to 100% of a technology theme's own money but closer to a
    third of Agriculture's or Crypto's, so a company-only base makes two
    numbers in this same column percentages of two different things, and a
    column that exists to be read against itself cannot do that. `pct_of_a`
    is therefore computed from `book.holdings(basis, ...)` grouped by
    `cluster_id`, independent of `company_by_cluster()`'s own total — the
    `kind == "company"` restriction below stays exactly where it is, on the
    NUMERATOR (which companies can be shared at all), and the two concerns
    are separable on purpose.

    ⚠️ **A floor, inherited.** Any pair whose shared names come through a fund
    whose holdings reach only part of it is marked, for the reason
    `exposure.by_company()` is: the overlap can only be larger than this, never
    smaller. And a company he does not hold directly, spelled two ways by two
    funds, is invisible here — `exposure.symbol_collisions()` is how big that is.

    🚨 **Computed over `kind == "company"` rows only.** `exposure.by_company()`
    also carries a `cash` kind and two bucket kinds (`unaccounted`,
    `no-companies`) for money a fund's own holdings cannot name — none of
    those is a company, and `exposure.company_by_cluster()`, which this
    function pivots, already excludes all three before this ever sees a row.
    Letting any of them in would be nonsense dressed as a number: two themes
    would "share" a company because both hold cash, or because two unrelated
    funds each have their own unexplained remainder — neither a company either
    theme actually owns. An overlap figure inflated by cash would be a false
    statement about his construction, on a panel whose whole claim is "these
    two themes are one bet" — this is the single most important rule behind
    this function, not an incidental detail of how it is built.

    **A long and a short in the same name are two opposing bets, not one.**
    The smaller-of-the-two-shares rule above assumes both themes are on the
    SAME side — true for every lot today, since `book.CASH_KINDS` aside,
    everything in this book is a long. If one theme is ever long a name and
    another is short it, `min()` on the raw signed values would pick the MORE
    negative number — the larger short — which is exactly backwards, so the
    comparison is by MAGNITUDE when both sides share a sign (keeping that
    sign — two shorts overlap too, and the smaller stake is still the
    honest shared figure), and ZERO when the signs differ. This is arithmetic
    hygiene, not a classification: whether a short, a future or an option
    should even be a `company` row at all is still open (REBUILD.md §2c,
    `exposure.by_company()`'s own docstring) and this function does not
    answer it — it only refuses to compute nonsense once that question is
    settled and a short does show up here.

    A retired cluster never appears as `cluster_a` or `cluster_b`. This is
    NOT the same handling as `clusters.tree()`'s — `tree()` keeps a lot with
    no live cluster on screen, re-filed into a visible "not in a live theme"
    row, because the spread must still show every dollar. This function
    drops it instead, and that is correct here for a different reason: a lot
    naming no live theme cannot be half of a PAIR of themes, so there is
    nothing honest to re-file it as. `set_status()` refuses to retire a
    theme that still holds money, but a hand-edit or a re-bought instrument
    could still name one, and pairing a closed theme against a live one
    would read as an overlap that is not really there.
    """
    from src import book
    from src import exposure

    book.check_basis(basis)
    rows = exposure.company_by_cluster(basis, **paths)
    if rows.empty:
        return pd.DataFrame(columns=OVERLAP_COLUMNS)

    value = rows.pivot_table(index="key", columns="cluster_id",
                             values="market_value", aggfunc="sum",
                             fill_value=0.0)
    is_floor = rows.pivot_table(index="key", columns="cluster_id",
                                values="is_floor", aggfunc="max",
                                fill_value=False).astype(bool)
    # A company can be reached by more than one fund inside the SAME cluster
    # (two of his funds both hold NVDA); `rows` is already one line per
    # (cluster, company) with that summed, so the label is unambiguous too.
    labels = rows.drop_duplicates("key").set_index("key")["label"]

    # D1 (fix round 1): each theme's WHOLE market value in this basis, read
    # straight from book.holdings() — deliberately NOT `value.sum(axis=0)`
    # (the company-only total `company_by_cluster()` produces), because a
    # column meant to be read against itself needs one consistent meaning of
    # "100%" and cash/bullion/futures are part of a theme's money even though
    # they can never be part of what it shares with another theme.
    lot_paths = {k: v for k, v in paths.items() if k != "holdings_path"}
    held = book.holdings(basis, **lot_paths)
    cluster_totals = (held.groupby("cluster_id")["market_value"].sum()
                      if not held.empty else pd.Series(dtype=float))

    cluster_ids = list(value.columns)
    out_rows = []
    for i, a in enumerate(cluster_ids):
        for b in cluster_ids[i + 1:]:
            a_vals, b_vals = value[a], value[b]
            # The smaller of the two themes' shares of each company — summing
            # would double count the same dollar, averaging would be a number
            # with no meaning (spec, this function's own docstring). "Smaller"
            # means smaller MAGNITUDE, compared only when both sides carry the
            # same sign (M3, fix round 1): two longs, or — once this book
            # holds one — two shorts, share a trade; a long and a short in
            # the same name do not, so that pair contributes zero regardless
            # of either side's size.
            sign_a = a_vals.gt(0).astype(int) - a_vals.lt(0).astype(int)
            sign_b = b_vals.gt(0).astype(int) - b_vals.lt(0).astype(int)
            same_sign = (sign_a == sign_b) & (sign_a != 0)
            magnitude = pd.concat([a_vals.abs(), b_vals.abs()], axis=1).min(axis=1)
            shared = (magnitude * sign_a).where(same_sign, 0.0)
            shared = shared[shared != 0]
            if shared.empty:
                continue       # sharing nothing is the normal case
            shared_value = float(shared.sum())
            pair_is_floor = bool(
                (is_floor[a] | is_floor[b]).loc[shared.index].any())
            # "Largest" means largest MAGNITUDE — a dominant shared short
            # should name itself here just as a dominant shared long would.
            top_keys = shared.abs().sort_values(ascending=False).index[:3]
            top_names = ", ".join(str(labels.get(k, k)) for k in top_keys)
            total_a = float(cluster_totals.get(a, 0.0))
            total_b = float(cluster_totals.get(b, 0.0))
            out_rows.append({
                "cluster_a": a, "cluster_b": b,
                "shared_value": round(shared_value, 2),
                "pct_of_a": round(shared_value / total_a * 100, 2) if total_a else 0.0,
                "pct_of_b": round(shared_value / total_b * 100, 2) if total_b else 0.0,
                "top_names": top_names,
                "is_floor": pair_is_floor,
            })

    out = pd.DataFrame(out_rows, columns=OVERLAP_COLUMNS)
    if out.empty:
        return out
    return out.sort_values("shared_value", ascending=False).reset_index(drop=True)
