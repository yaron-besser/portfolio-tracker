"""The evidence store — one row per document, one row per link.

Design: docs/superpowers/specs/2026-08-16-evidence-store-design.md

A Fundstrat report covering a dozen stocks is ONE row here, linked to every
claim it backs. The `;`-separated `claims.evidence` column it replaces stored
the same path once per claim, so correcting a filename meant correcting it
everywhere — the duplication P7 forbids.

`source` and `published` describe the document and live here. `page` and `why`
describe one use of it and live on the link: the same report backs GRNY on
p.12 and RSP on p.31.

Usage:
    from src import evidence
    eid = evidence.add_evidence(kind="research", title="Fundstrat Core, May",
                                source="fundstrat", published="2026-05-21",
                                storage="private", location="core-may.pdf")
    evidence.link("claim", "c0008", "document", eid, page="12", why="the case")
    evidence.for_target("claim", "c0008")   # sources behind one thing
    evidence.for_source("document", eid)    # things resting on one document
    evidence.unlinked()                     # filed but never used
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from src.claims import (
    PROJECT_ROOT,
    _is_real_date,
    _vocab_check,
    _write,
)

EVIDENCE_PATH = PROJECT_ROOT / "data" / "evidence.csv"

EVIDENCE_COLUMNS = [
    "id", "kind", "title",
    # The vocabulary is `origin`, REUSED not copied: "who said this" is the
    # same question for a document as for a claim, and two lists would drift.
    "source",
    # When the report is dated — a property of the document, so it is stored
    # once here rather than once per link.
    "published",
    "storage", "location", "added",
    # Who wrote it, and one line on what it says. Both are properties of the
    # DOCUMENT, like `published` above, so they sit here rather than on the
    # link — `evidence_links.why` answers a different question ("why does this
    # bear on THAT holding"), and one article linked to four things would
    # otherwise carry four copies of the same gist. Blank throughout the 50
    # documents filed before 2026-10-11, which is why both are optional.
    "author", "gist",
]

# Macro is the one target that names nothing in any table — there is no row
# for "the whole economy". It takes this fixed id so that "Macro", "macro
# rates" and "MACRO" cannot become three subjects nobody can read back
# together. ⚠️ `commentary.py` calls the same idea `market` (its UNTARGETED);
# the two names were kept apart deliberately on 2026-10-11, so a search for
# everything macro must read BOTH words.
MACRO_TARGET = "macro"

# Private files live here. The row stores only the filename: notes/research/
# is gitignored, so the path must never appear in a committed table.
RESEARCH_DIR = "notes/research"

# YYYY-MM-DD-source-topic.ext — the same discipline the screenshot guard
# enforces, by a different door.
RESEARCH_NAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"-(?P<source>[a-z0-9]+)"
    r"-(?P<topic>[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"\.(?P<ext>[a-z0-9]+)$"
)


SUMMARIES_PATH = PROJECT_ROOT / "data" / "evidence_summaries.csv"

SUMMARY_COLUMNS = ["id", "evidence_id", "location", "added"]

# Summaries live here and nowhere else. notes/picks/ already holds 19 research
# write-ups; this is what finally connects them to the rest of the store.
PICKS_DIR = "notes/picks"


def _refuse_traversal(location: str) -> None:
    """`..` defeats every prefix rule in this module, so no location may carry it.

    `notes/picks/../research/x.pdf` is relative, starts with `notes/picks/`, and
    lands in gitignored `notes/research/` where the paid PDFs are — so both rules
    that keep a paid path out of a committed table were one `..` away from
    useless, at write time and at the gate. Refused by shape, before anything
    resolves the path: whether the file exists is a different question, and the
    gate deliberately does not ask it.
    """
    text = str(location or "").strip()
    if ".." in text.replace("\\", "/").split("/"):
        raise ValueError(
            f"location {text!r} climbs out of the directory it names with "
            f"'..'. Write the path plainly — every prefix rule here is one "
            f"'..' away from reaching {RESEARCH_DIR}/, where the paid PDFs live."
        )


def _summaries_schema() -> pa.DataFrameSchema:
    """Built per call, like every other schema here.

    `evidence_id` is NOT nullable, and that is the entire reason this table
    exists rather than a `summarises` column on `evidence.csv`: a summary that
    has lost its research piece is unreachable, and here it is structurally
    impossible rather than forbidden by a rule someone has to remember.
    """
    return pa.DataFrameSchema(
        {
            "id": pa.Column(str, pa.Check.str_matches(r"^s\d{4}$"), unique=True),
            "evidence_id": pa.Column(str, pa.Check.str_length(min_value=1)),
            "location": pa.Column(str, pa.Check.str_length(min_value=1)),
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
                lambda df: ~df["location"].duplicated(),
                error="one summary per file — two rows pointing at one markdown "
                      "file split every question about it in half",
            ),
        ],
    )


def load_summaries(path: Path = SUMMARIES_PATH) -> pd.DataFrame:
    """Read and validate the summaries. Missing/empty is an empty store."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     na_values=[""])
    if df.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    return _summaries_schema().validate(df, lazy=True)


def _next_summary_id(df: pd.DataFrame) -> str:
    """The next sNNNN id: one above the highest that parses as a number."""
    nums = pd.to_numeric(df["id"].str.removeprefix("s"), errors="coerce").dropna()
    next_num = int(nums.max()) + 1 if len(nums) else 1
    return f"s{next_num:04d}"


def check_summary_location(location: str) -> None:
    """Where a summary may live — shape rules only, never file existence.

    Split out of add_summary() so the commit gate can apply the same rule to a
    row nobody wrote through add_summary(). It had none: check_summaries()
    checked the foreign key and stopped, so a hand-edited location pointing into
    gitignored notes/research/ committed cleanly — precisely the harm the
    storage=private bare-filename rule exists to prevent, through the one table
    for which nobody had written a location rule.
    """
    text = str(location or "").strip()
    if Path(text).is_absolute() or not text.startswith(f"{PICKS_DIR}/"):
        raise ValueError(
            f"location {text!r} must be a repo-relative path under "
            f"{PICKS_DIR}/ — that is where research write-ups live."
        )
    _refuse_traversal(text)


def add_summary(evidence_id: str, location: str,
                path: Path = SUMMARIES_PATH,
                evidence_path: Path = EVIDENCE_PATH,
                project_root: Path = PROJECT_ROOT) -> str:
    """File one summary of one document. Returns the new id.

    Every rule is pre-checked here rather than left to the schema, for the same
    reason link() pre-checks its duplicate: a pandera SchemaErrors is not a
    ValueError, and the message the writer needs has to name the file.

    `project_root` is a parameter only so tests can point the file-existence
    check at a tmp tree; nothing in the app should pass it.
    """
    text = str(location or "").strip()
    known_docs = set(load_evidence(evidence_path)["id"])
    if evidence_id not in known_docs:
        raise ValueError(
            f"No document with id {evidence_id!r} — file the research piece "
            f"with add_evidence() first, then summarise it."
        )
    check_summary_location(text)
    if not (project_root / text).is_file():
        raise ValueError(
            f"location {text!r} does not point to a file that exists. "
            f"Write the summary first, then record it."
        )
    existing = load_summaries(path)
    if not existing.empty and text in set(existing["location"]):
        raise ValueError(
            f"one summary per file — {text!r} is already summarised by "
            f"{existing.loc[existing['location'] == text, 'id'].iloc[0]}."
        )
    row = {
        "id": _next_summary_id(existing),
        "evidence_id": evidence_id,
        "location": text,
        "added": date.today().isoformat(),
    }
    candidate = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    _summaries_schema().validate(candidate, lazy=True)
    _write(candidate, path)
    return row["id"]


def check_summaries(path: Path = SUMMARIES_PATH,
                    evidence_path: Path = EVIDENCE_PATH) -> None:
    """The foreign key, for the commit gate.

    File existence is deliberately NOT checked — the same asymmetry
    check_locations() already makes: a summary deleted months later must not
    block every future commit.
    """
    df = load_summaries(path)
    if df.empty:
        return
    known = set(load_evidence(evidence_path)["id"])
    missing = sorted(set(df["evidence_id"]) - known)
    if missing:
        raise ValueError(
            f"data/evidence_summaries.csv names document(s) {missing} that do "
            f"not exist in data/evidence.csv."
        )


def check_summary_locations(path: Path = SUMMARIES_PATH) -> None:
    """Every row's location, for the commit gate.

    The counterpart of check_locations() for the summaries table, and it exists
    for the same reason: a rule enforced only by the writing function is a rule a
    hand-edited CSV walks straight past. The failing row is named, because a
    message without an id makes the reader diff the whole table to find it.
    """
    for _, r in load_summaries(path).iterrows():
        try:
            check_summary_location(r["location"])
        except ValueError as exc:
            raise ValueError(
                f"data/evidence_summaries.csv row {r['id']}: {exc}"
            ) from exc


# ─── The registry: the ONE place that knows which type reads which table ───
#
# `target_type`/`target_id` and `source_type`/`source_id` are polymorphic
# pointers — the destination changes row by row, so no schema can enforce them.
# Three things make that safe, and all three live here:
#
#   1. the id prefix must agree with the type, so a real id sitting in the
#      wrong table is impossible rather than merely unlikely;
#   2. every guard and every read resolves which TABLE to load through this
#      mapping, so no table lookup branches on a type anywhere else;
#   3. a test asserts the vocabulary and this mapping agree, so a kind can
#      never be added to ops/vocab/ without a lookup being written for it.
#
# What the registry does NOT cover, because a type -> table map cannot: a fifth
# target type also needs a decision in `ancestors()` about what, if anything, it
# inherits from — a tree's shape is not a table lookup — and a third source type
# needs a branch in `_source_index()` and in `holding.sources_line()`, which
# would otherwise render it silently as a document.


def target_registry() -> dict[str, tuple]:
    """What a document can be about: type -> (loader, id column, id prefix).

    Built per call so a vocab edit applies without a reload, like every schema
    here. The imports are deferred purely to keep this module cheap to import —
    **there is no cycle.** Nothing in src/ imports evidence except holding.py,
    which evidence never imports, and claims is already imported at the top of
    this file. Hoisting these three to module level was tried and works; the
    reason to leave them here is import cost, and nothing else. (The docstring
    used to claim a cycle. A phantom workaround a future maintainer preserves
    because a comment vouched for it is worse than no comment.)

    A prefix of None means the ids are names Yaron chose rather than a counter:
    "Technology" is a cluster id and has no shape to check.
    """
    from src import claims as claims_mod
    from src import clusters, instruments, sectors

    return {
        "claim":      (claims_mod.load_claims, "id",            "c"),
        "cluster":    (clusters.load,          "cluster_id",    None),
        "sector":     (sectors.load,           "sector_id",     None),
        "instrument": (instruments.load,       "instrument_id", None),
    }


def source_registry() -> dict[str, tuple]:
    """What a link can point at: type -> (loader, id column, id prefix)."""
    return {
        "document": (load_evidence,  "id", "e"),
        "summary":  (load_summaries, "id", "s"),
    }


def _check_id(kind: str, ident: str, known: set[str],
              prefix: str | None, label: str) -> None:
    """The rule itself, with the table already loaded.

    Split out from check_target/check_source so check_links() can load each
    table once for a whole file instead of once per row.

    The prefix is checked BEFORE membership on purpose: "e0007 is not a summary"
    is the true diagnosis, and "no summary with id e0007" would send the reader
    hunting for a missing row that was never supposed to exist.
    """
    text = str(ident or "").strip()
    if not text:
        raise ValueError(
            f"{label}_id is blank — a link must name what it points at."
        )
    if prefix and not text.startswith(prefix):
        raise ValueError(
            f"{label}_id {text!r} does not belong to {label}_type {kind!r} — "
            f"{kind} ids start with {prefix!r}. The id may be real, but it "
            f"lives in another table."
        )
    if text not in known:
        raise ValueError(
            f"No {kind} with id {text!r} — a link must point at something "
            f"that exists."
        )


def _load_known(kind: str, registry: dict, label: str,
                paths: dict[str, Path] | None = None) -> tuple[set[str], str | None]:
    """Resolve one type to (its ids, its prefix), honouring a path override.

    `paths` exists for the commit guard, which must check links against the
    STAGED copies of claims.csv and evidence.csv rather than the working tree.
    """
    if kind not in registry:
        raise ValueError(
            f"{label}_type {kind!r} is not a known kind. Known: {sorted(registry)}."
        )
    loader, id_column, prefix = registry[kind]
    override = (paths or {}).get(kind)
    df = loader(override) if override is not None else loader()
    return set(df[id_column].astype(str)), prefix


def check_target(target_type: str, target_id: str,
                 paths: dict[str, Path] | None = None) -> None:
    """Refuse a target that is unknown, or that lives in another table."""
    if target_type == MACRO_TARGET:
        # Nothing to look up — there is no macro table. The id is still
        # checked, because the whole point of a fixed id is that it cannot
        # drift into synonyms.
        if str(target_id).strip() != MACRO_TARGET:
            raise ValueError(
                f"target_type {MACRO_TARGET!r} takes the id {MACRO_TARGET!r} "
                f"and nothing else — got {target_id!r}. File it against a "
                f"sector, a cluster or an instrument if it is really about one."
            )
        return
    known, prefix = _load_known(target_type, target_registry(), "target", paths)
    _check_id(target_type, target_id, known, prefix, "target")


def check_source(source_type: str, source_id: str,
                 paths: dict[str, Path] | None = None) -> None:
    """Refuse a source that is unknown, or that lives in another table."""
    known, prefix = _load_known(source_type, source_registry(), "source", paths)
    _check_id(source_type, source_id, known, prefix, "source")


def _evidence_schema() -> pa.DataFrameSchema:
    """Built per call so a vocab edit applies without a reload."""
    date_check = pa.Check(
        lambda s: s.map(_is_real_date),
        error="dates must be a real calendar date in ISO YYYY-MM-DD form",
    )
    return pa.DataFrameSchema(
        {
            "id": pa.Column(str, unique=True),
            "kind": pa.Column(str, _vocab_check("evidence_kind")),
            "title": pa.Column(str, pa.Check.str_length(min_value=1)),
            "source": pa.Column(str, _vocab_check("origin")),
            "published": pa.Column(str, date_check, nullable=True),
            "storage": pa.Column(str, _vocab_check("evidence_storage")),
            "location": pa.Column(str, pa.Check.str_length(min_value=1)),
            "added": pa.Column(str, date_check),
            "author": pa.Column(str, nullable=True),
            "gist": pa.Column(str, nullable=True),
        },
        strict=True,
        ordered=True,
    )


def check_one_row_per_document(df: pd.DataFrame) -> None:
    """The table's whole promise: a document is filed once, linked many times.

    `id` being unique is not that promise — two ids can point at the same file,
    and then `for_source()` returns half the links resting on it while
    `unlinked()` calls the other half research that was never acted on. The
    identity of a document is where it lives, so (storage, location) is what
    must be unique. Checked here rather than in the schema so the message names
    the file, which is what the reader needs to fix it.
    """
    if df.empty:
        return
    key = df["storage"].astype(str).str.strip() + " | " + df["location"].astype(str).str.strip()
    dupes = df[key.duplicated(keep=False)]
    if not dupes.empty:
        listed = "\n".join(
            f"  {r['id']}  {r['storage']}  {r['location']}"
            for _, r in dupes.iterrows()
        )
        raise ValueError(
            "the same document is filed more than once — one row per document "
            "is what this table exists for:\n"
            f"{listed}\n"
            "Keep the first id and link it again; a second row splits every "
            "question about that document in half."
        )


def load_evidence(path: Path = EVIDENCE_PATH) -> pd.DataFrame:
    """Read and validate the documents. Missing/empty is an empty store."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=EVIDENCE_COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     na_values=[""])
    if df.empty:
        return pd.DataFrame(columns=EVIDENCE_COLUMNS)
    df = _evidence_schema().validate(df, lazy=True)
    check_one_row_per_document(df)
    return df


def check_location(storage: str, location: str, *, require_file: bool = True) -> None:
    """The location rule depends on how the document is stored.

    Three different promises, so three different checks. A committed file must
    exist; a link must be one; a private file must be a bare name, because
    notes/research/ is gitignored and a committed row must never carry a path
    to a paid PDF.

    `require_file=False` keeps every shape rule but drops the does-the-file-exist
    one. That is the split the commit guard needs: a paid-PDF path must never
    reach a commit, but a chart deleted months later must not brick every future
    commit — that case is `stale_documents()`'s job, not the gate's.
    """
    text = str(location or "").strip()
    if storage == "repo":
        if Path(text).is_absolute():
            raise ValueError(
                f"location {text!r} is absolute — use a repo-relative path "
                f"(e.g. notes/screenshots/2026-08-15-IWM-price-1d.png) so the "
                f"row is valid on any machine."
            )
        _refuse_traversal(text)
        if require_file and not (PROJECT_ROOT / text).is_file():
            raise ValueError(
                f"location {text!r} does not point to a file that exists. "
                f"File the chart first, then record it."
            )
    elif storage == "link":
        if not text.startswith(("http://", "https://")):
            raise ValueError(
                f"location {text!r} is storage=link but is not a URL — "
                f"it must start with http:// or https://"
            )
    elif storage == "private":
        if "/" in text or "\\" in text:
            raise ValueError(
                f"location {text!r} must be a bare filename for storage=private "
                f"(e.g. 2026-08-17-13d-ai-capex.pdf). Private files live in "
                f"{RESEARCH_DIR}/, which is gitignored — the row stores only the "
                f"name so no committed table ever carries the path."
            )
        match = RESEARCH_NAME.match(text)
        if not match:
            raise ValueError(
                f"location {text!r} is not named YYYY-MM-DD-source-topic.ext "
                f"(e.g. 2026-08-17-13d-ai-capex.pdf). A meaningful name is the "
                f"only handle you have on a file no table can point at."
            )
        if not _is_real_date(match["date"]):
            raise ValueError(
                f"location {text!r} starts with {match['date']!r}, which is not "
                f"a real calendar date."
            )
        # Local so a caller checking a repo path never pays for reading a vocab
        # file. Not a cycle — src.claims is already imported at the top of this
        # module; this only binds the module object.
        from src import claims as claims_mod
        if match["source"] not in claims_mod.vocab("origin"):
            raise ValueError(
                f"location {text!r} names source {match['source']!r}, which is "
                f"not in ops/vocab/origin. Add it there first if it is real."
            )
    else:
        # Adding a *kind* is a line in a vocab file. Adding a *storage* is not:
        # it needs a rule here, and silently accepting one without a rule is how
        # an unchecked absolute path to a paid PDF would enter a committed table.
        raise ValueError(
            f"storage {storage!r} has no location rule in check_location(). "
            f"Adding a value to ops/vocab/evidence_storage is not enough — "
            f"decide what its location must look like and check it here."
        )


def check_locations(path: Path = EVIDENCE_PATH, *, require_file: bool = False) -> None:
    """Every row's location, shape rules only by default. For the commit gate."""
    for _, r in load_evidence(path).iterrows():
        check_location(r["storage"], r["location"], require_file=require_file)


def _next_id(df: pd.DataFrame) -> str:
    """The next eNNNN id: one above the highest that parses as a number."""
    nums = pd.to_numeric(df["id"].str.removeprefix("e"), errors="coerce").dropna()
    next_num = int(nums.max()) + 1 if len(nums) else 1
    return f"e{next_num:04d}"


def add_evidence(path: Path = EVIDENCE_PATH, **fields) -> str:
    """Validate one new document and append it. Returns the new id.

    The whole file is validated before a byte is written, so a bad row can
    never land.
    """
    existing = load_evidence(path)
    unknown = set(fields) - set(EVIDENCE_COLUMNS)
    if unknown:
        raise ValueError(
            f"Unknown field(s) {sorted(unknown)}. Columns are: {EVIDENCE_COLUMNS}"
        )
    row = {c: fields.get(c) for c in EVIDENCE_COLUMNS}
    row["id"] = _next_id(existing)
    row["added"] = row["added"] or date.today().isoformat()
    # Trailing whitespace survives a paste and is invisible in the CSV. Strip
    # before anything is checked, or check_location() validates one string and
    # the row stores another — stale_documents() would then report a file that
    # is really there, on every run, forever.
    for field in ("title", "storage", "location"):
        if row[field] is not None:
            row[field] = str(row[field]).strip()

    candidate = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    # Schema first, so the vocabularies stay the one gate on "is this a known
    # value". check_location() below then answers a different question — this
    # value is known, but does its location obey that storage's rule.
    _evidence_schema().validate(candidate, lazy=True)
    check_one_row_per_document(candidate)
    check_location(row["storage"], row["location"])
    # Order matters: published is compared against added, so added must be set.
    # A report cannot be filed before it was published, and a published date in
    # the future is a typo — both are caught here rather than by the schema,
    # because they are facts about the world, not about the column's shape.
    published = row["published"]
    today = date.today().isoformat()
    if row["added"] > today:
        raise ValueError(
            f"added {row['added']!r} is in the future — a document cannot be "
            f"filed before today ({today})."
        )
    if published and _is_real_date(published) and published > row["added"]:
        raise ValueError(
            f"published {published!r} is later than added {row['added']!r} — "
            f"a report cannot be filed before it was published."
        )
    _write(candidate, path)
    return row["id"]


# ─── Links: which source backs which thing ───
#
# A join table, not a column, because one document backs many things and one
# thing rests on many documents. Two polymorphic pairs: `target_*` says what the
# link is about, `source_*` says what it is. `page` is part of the key so one
# report can back one target twice at two different pages.

LINKS_PATH = PROJECT_ROOT / "data" / "evidence_links.csv"

LINK_COLUMNS = ["target_type", "target_id", "source_type", "source_id",
                "page", "why", "added"]

_LINK_KEY = ["target_type", "target_id", "source_type", "source_id", "page"]


def _is_page_number(text: str) -> bool:
    """One page number, in the one spelling it is allowed to have.

    `page` is part of the link key, so one page value needs exactly one
    spelling or the file ends up holding rows a reader cannot tell apart.
    Three ways that happens, all closed here:

      "05"   same page as "5", different key
      "١٢"   str.isdigit() is True for Arabic-Indic and fullwidth digits, so
             this would validate, store, and print as p.١٢ while never
             matching "12"
      "¹²"   isdigit() is True but int() raises — a traceback where the
             written error message belongs

    isascii() runs before int(), so int() can never be reached by something
    that would raise.
    """
    return (text.isascii() and text.isdigit()
            and int(text) >= 1 and text == str(int(text)))


def _is_page(value: str) -> bool:
    """A page is "12", or a forward range "15-17".

    Ranges exist because a theme rarely sits on one page — Fundstrat's IBM
    selloff runs 15-17 and 13D's Optimus paragraph runs 55-56. Citing only the
    first page sends a reader somewhere that does not carry the whole claim.

    Kept as text and parsed rather than coerced: `int("12a")` raising is a
    refusal, but `int(12.9)` silently truncating is a wrong page number.

    Whitespace is NOT tolerated here, only in `link()`, which trims before it
    stores.

    ⚠️ What this does NOT prevent: two rows whose pages overlap. "57" and
    "57-58" are different key values, so both can be filed against the same
    target and nothing sees that they cover the same page. The dedup is exact
    string equality, and ranges gave one page more than one legal value to
    appear in. Left open deliberately — refusing overlaps would also refuse
    citing p.55 for one reason and pp.55-56 for another — and recorded in
    REBUILD.md rather than fixed quietly.
    """
    text = str(value)
    if _is_page_number(text):
        return True
    start, sep, end = text.partition("-")
    # A single "-" only, both ends real page numbers, and the range runs
    # forwards. "175-175" is not a range; it is page 175, written that way.
    if sep and _is_page_number(start) and _is_page_number(end):
        return int(start) < int(end)
    return False


def _links_schema() -> pa.DataFrameSchema:
    """Built per call, like every other schema here."""
    return pa.DataFrameSchema(
        {
            "target_type": pa.Column(str, _vocab_check("target_type")),
            "target_id": pa.Column(str, pa.Check.str_length(min_value=1)),
            "source_type": pa.Column(str, _vocab_check("source_type")),
            "source_id": pa.Column(str, pa.Check.str_length(min_value=1)),
            # Text, not an integer — a CSV has no integer type, and coercing
            # would turn "12a" into 12 rather than refusing it. See _is_page:
            # a single page or a forward range.
            "page": pa.Column(
                str,
                pa.Check(
                    lambda s: s.map(_is_page),
                    error="page must be a whole number of 1 or more, or a "
                          "forward range like 15-17",
                ),
                nullable=True,
            ),
            "why": pa.Column(str, nullable=True),
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
                lambda df: ~df.duplicated(subset=_LINK_KEY),
                error="the same page of the same source cannot back one target "
                      "twice — (target_type, target_id, source_type, source_id, "
                      "page) is the key",
            ),
        ],
    )


def load_links(path: Path = LINKS_PATH) -> pd.DataFrame:
    """Read and validate the links. Missing/empty is an empty table."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=LINK_COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     na_values=[""])
    if df.empty:
        return pd.DataFrame(columns=LINK_COLUMNS)
    return _links_schema().validate(df, lazy=True)


def link(target_type: str, target_id: str, source_type: str, source_id: str,
         page: str | int | None = None, why: str | None = None,
         path: Path = LINKS_PATH,
         paths: dict[str, Path] | None = None) -> None:
    """Attach a document or a summary to a claim, cluster, sector, instrument
    or `macro` (the whole economy; id fixed to "macro", added 2026-10-11).

    Refuses either end if it does not exist — a dangling link would make
    unevidenced_claims() disagree with what is on file, and silently.
    """
    check_target(target_type, target_id, paths)
    check_source(source_type, source_id, paths)

    existing = load_links(path)
    # Trimmed on the way in, so the stored form is the canonical one and the
    # key cannot hold " 175" and "175" as two indistinguishable rows.
    page_text = None if page is None or str(page).strip() == "" else str(page).strip()
    if not existing.empty:
        clash = existing[
            (existing["target_type"] == target_type)
            & (existing["target_id"] == target_id)
            & (existing["source_type"] == source_type)
            & (existing["source_id"] == source_id)
            & (existing["page"].fillna("") == (page_text or ""))
        ]
        if len(clash):
            where = f"page {page_text}" if page_text else "with no page"
            raise ValueError(
                f"{target_type} {target_id} already links {source_id} {where}."
            )
    row = {
        "target_type": target_type,
        "target_id": target_id,
        "source_type": source_type,
        "source_id": source_id,
        "page": page_text,
        "why": why,
        "added": date.today().isoformat(),
    }
    candidate = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    _links_schema().validate(candidate, lazy=True)
    _write(candidate, path)


def check_links(path: Path = LINKS_PATH,
                paths: dict[str, Path] | None = None) -> None:
    """Both polymorphic pairs, for the commit gate.

    Enforced at commit as well as at write, because a hand-edited CSV bypasses
    link() entirely and a dangling link is invisible until something counts it.

    Grouped by type so each table is read once for the whole file rather than
    once per row.

    🚨 **This does NOT call `check_target()`** — it reimplements the lookup to
    get that one-read-per-type grouping, which is exactly how the `macro`
    exemption was missed on 2026-10-11: added to `check_target()` alone, it
    left this gate raising on a link `link()` had just written, and
    `pre-commit:302` runs this over the STAGED copy, so one macro row blocked
    **every future commit**. Any future target that `check_target()` special-
    cases must be special-cased here too, or the two will disagree again.
    """
    df = load_links(path)
    if df.empty:
        return
    for type_col, id_col, registry, label in (
        ("target_type", "target_id", target_registry(), "target"),
        ("source_type", "source_id", source_registry(), "source"),
    ):
        for kind, group in df.groupby(type_col):
            if type_col == "target_type" and kind == MACRO_TARGET:
                # No table to read, but the fixed id is still enforced — a
                # hand-edited CSV must not be able to smuggle in macro/rates.
                for ident in group[id_col].unique():
                    check_target(MACRO_TARGET, ident, paths)
                continue
            known, prefix = _load_known(kind, registry, label, paths)
            for ident in group[id_col].unique():
                _check_id(kind, ident, known, prefix, label)


# ─── The views: what the three tables can now answer ───

_SOURCE_INDEX_COLUMNS = ["source_type", "source_id", "document_id", "title",
                         "kind", "source", "published", "storage", "location"]

_UNLINKED_COLUMNS = ["source_type", "id", "title", "location"]


def _source_index(evidence_path: Path = EVIDENCE_PATH,
                  summaries_path: Path = SUMMARIES_PATH) -> pd.DataFrame:
    """One frame keyed by (source_type, source_id), carrying what a reader needs.

    A summary has no title of its own, deliberately — it borrows its document's,
    so a renamed report renames its summaries for free (P7). It keeps its OWN
    `location` and its own `storage`, always `repo`. `storage` is not
    decoration: holding.py finds a claim's screenshots by filtering on it.
    """
    docs = load_evidence(evidence_path)
    summaries = load_summaries(summaries_path)
    by_id = {r["id"]: r for _, r in docs.iterrows()}

    rows = [
        {"source_type": "document", "source_id": r["id"], "document_id": r["id"],
         "title": r["title"], "kind": r["kind"], "source": r["source"],
         "published": r["published"], "storage": r["storage"],
         "location": r["location"]}
        for _, r in docs.iterrows()
    ] + [
        {"source_type": "summary", "source_id": r["id"],
         "document_id": r["evidence_id"],
         "title": by_id.get(r["evidence_id"], {}).get("title", ""),
         "kind": "note",
         "source": by_id.get(r["evidence_id"], {}).get("source", ""),
         "published": by_id.get(r["evidence_id"], {}).get("published", ""),
         "storage": "repo",
         "location": r["location"]}
        for _, r in summaries.iterrows()
    ]
    return pd.DataFrame(rows, columns=_SOURCE_INDEX_COLUMNS)


def articles(path: Path = EVIDENCE_PATH) -> pd.DataFrame:
    """Everything he has read from a newspaper, newest first.

    The six columns he asked for on 2026-10-11: the id first, then date,
    provider, writer, headline and the one-line gist. `kind == "article"` is the
    filter — a chart of the S&P is not a piece of journalism, and mixing them
    makes the table unreadable as a reading list.

    Sorted by `published` rather than `added`, because the question this
    answers is "what have I been reading lately", not "what did I file when".
    """
    df = load_evidence(path)
    cols = ["id", "published", "source", "author", "title", "gist"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    out = df[df["kind"] == "article"]
    # A missing writer is shown as blank: he reads this table, and NaN is a
    # pandas word rather than an English one.
    return (out.sort_values("published", ascending=False)
               .reset_index(drop=True)[cols].fillna(""))


def for_target(target_type: str, target_id: str, path: Path = LINKS_PATH,
               paths: dict[str, Path] | None = None) -> pd.DataFrame:
    """Everything attached to one thing — documents and summaries alike.

    The ONLY join any caller performs. Nothing hand-joins, so no caller has to
    know which type reads which table — that stays in the registry. Which is not
    the same as nothing branching on a type: `ancestors()` branches on
    cluster/instrument and `holding.sources_line()` on summary, because a tree's
    shape and how a row reads back are not table lookups.
    """
    overrides = paths or {}
    columns = LINK_COLUMNS + [c for c in _SOURCE_INDEX_COLUMNS
                              if c not in ("source_type", "source_id")]
    links = load_links(path)
    if links.empty:
        return pd.DataFrame(columns=columns)
    mine = links[(links["target_type"] == target_type)
                 & (links["target_id"] == target_id)]
    if mine.empty:
        return pd.DataFrame(columns=columns)
    index = _source_index(
        overrides.get("document", EVIDENCE_PATH),
        overrides.get("summary", SUMMARIES_PATH),
    )
    return mine.merge(index, on=["source_type", "source_id"], how="left")


def for_source(source_type: str, source_id: str,
               path: Path = LINKS_PATH) -> pd.DataFrame:
    """Everything that rests on one document or one summary."""
    links = load_links(path)
    if links.empty:
        return links
    return links[(links["source_type"] == source_type)
                 & (links["source_id"] == source_id)]


def unlinked(path: Path = LINKS_PATH,
             evidence_path: Path = EVIDENCE_PATH,
             summaries_path: Path = SUMMARIES_PATH) -> pd.DataFrame:
    """Documents and summaries filed but never attached to anything.

    Research read and not acted on. Summaries count too: one written and never
    linked is a theme nobody connected to a holding.
    """
    index = _source_index(evidence_path, summaries_path)
    if index.empty:
        return pd.DataFrame(columns=_UNLINKED_COLUMNS)
    links = load_links(path)
    used = set(zip(links["source_type"], links["source_id"]))
    keep = [(r["source_type"], r["source_id"]) not in used
            for _, r in index.iterrows()]
    return index[keep].rename(columns={"source_id": "id"})[_UNLINKED_COLUMNS]


def unevidenced_claims(path: Path = LINKS_PATH,
                       claims_path: Path | None = None) -> pd.DataFrame:
    """Claims with no document attached — never blocking, always visible.

    Only `target_type == "claim"` counts. Inherited evidence deliberately does
    NOT silence this: a report about technology is not research on ASML, and
    letting one theme-wide document cover twelve unexamined bets is exactly the
    reassurance this function exists to withhold.
    """
    # Local for symmetry with the other deferred imports here, not for a cycle:
    # src.claims is imported at the top of this module already.
    from src import claims as claims_mod

    df = claims_mod.load_claims(claims_path or claims_mod.CLAIMS_PATH)
    if df.empty:
        return df
    links = load_links(path)
    used = set(links[links["target_type"] == "claim"]["target_id"])
    return df[(df["kind"] == "claim") & (~df["id"].isin(used))]


def ancestors(target_type: str, target_id: str,
              paths: dict[str, Path] | None = None) -> list[tuple[str, str]]:
    """The things whose research also bears on this one, nearest first.

    A holding sits in a cluster (which may sit in a group) and carries a sector
    label (which sits under one of the 11 SPY sectors). Nothing else inherits: a
    claim's research is its own, and a group is the top.

    Nearest first means one level at a time — the holding's own cluster and
    sector, then what each of those sits inside.

    **A sector walks up exactly as a cluster does** (Yaron, settled 2026-08-17),
    and it has to: data/sectors.csv is two levels deep and 21 of the 24
    instruments carrying a sector carry a LEAF label, so while the walk stopped
    at the leaf, filing a document on "sector Technology" — a legal target, and
    the wording CLAUDE.md itself uses — reached nothing at all.

    `paths` overrides which reference tables are read, keyed by type exactly as
    everywhere else in this module. Without it these lookups were the only reads
    here that could not be pointed at a test tree, and holding.summary() is
    data_dir-parameterised: a caller working in a fixture tree would have
    had the real portfolio's cluster tree answered back, and
    instruments.get() raises KeyError for a fixture instrument the real
    data/instruments.csv has never heard of.
    """
    from src import clusters, instruments, sectors

    overrides = paths or {}
    out: list[tuple[str, str]] = []
    cluster_id = ""
    sector = ""
    if target_type == "instrument":
        row = instruments.get(
            target_id,
            overrides.get("instrument", instruments.INSTRUMENTS_PATH))
        cluster_id = str(row.get("cluster_id") or "")
        sector = str(row.get("sector") or "")
        if cluster_id:
            out.append(("cluster", cluster_id))
        if sector:
            out.append(("sector", sector))
    elif target_type == "cluster":
        cluster_id = target_id

    if cluster_id:
        parent = str(clusters.get(
            cluster_id,
            overrides.get("cluster", clusters.CLUSTERS_PATH)).get("parent") or "")
        if parent:
            out.append(("cluster", parent))
    if sector:
        # parents_map() rather than parent(): a label this table has never heard
        # of simply has no parent above it. ancestors() reports what is there and
        # raising here would take /show down over a reference-table gap that
        # instruments.gaps() already surfaces — and every fixture tree that
        # writes no sectors.csv would crash instead of answering.
        # The 11 top rows are self-parented, so the guard is what stops each of
        # them being listed as its own ancestor.
        above = str(sectors.parents_map(
            overrides.get("sector", sectors.SECTORS_PATH)).get(sector) or "")
        if above and above != sector:
            out.append(("sector", above))
    return out


def inherited(target_type: str, target_id: str, path: Path = LINKS_PATH,
              paths: dict[str, Path] | None = None) -> dict[tuple[str, str], int]:
    """How many sources sit on the things above this one, keyed by (type, id).

    The pointer, not the pile (Yaron, 2026-08-17): `/show ASML` prints its own
    documents in full and then one line built from this — "3 more from AI
    Infrastructure and Technology — ask to see them". Nothing is stored, so
    nothing can go stale.

    Keyed by the PAIR, not by the name (controller ruling, 2026-08-17). Seven
    strings are both a cluster id and a sector id — Healthcare, Technology and
    five more — so a name-keyed dict silently overwrote one ancestor's count
    with the other's: MRK sits in cluster Healthcare AND in sector Healthcare,
    and its cluster's documents vanished from the pointer line. Summing them
    was the alternative and is wrong for the same reason a sector is its own
    ancestor at all: a sector is a different question from a cluster, and an
    ancestor's evidence is pointed at, never merged in.

    Insertion order follows ancestors(), so a caller reads them nearest first.
    `path` is the link table; `paths` is the reference tables ancestors() walks.
    """
    links = load_links(path)
    if links.empty:
        return {}
    counts: dict[tuple[str, str], int] = {}
    for kind, ident in ancestors(target_type, target_id, paths):
        n = len(links[(links["target_type"] == kind)
                      & (links["target_id"] == ident)])
        if n:
            counts[(kind, ident)] = n
    return counts


def stale_documents(path: Path = EVIDENCE_PATH) -> pd.DataFrame:
    """Committed documents whose file no longer resolves. Visible, never
    blocking (P2), exactly like claims.stale_refs() — which is where this
    check lived until the evidence column moved out on 2026-08-16.

    Only `storage=repo` is checked. A link has no local file, and a private
    PDF legitimately sits on one machine only, so neither can be called stale
    without crying wolf on every clone.
    """
    docs = load_evidence(path)
    if docs.empty:
        return docs
    in_repo = docs[docs["storage"] == "repo"]
    missing = in_repo["location"].map(lambda p: not (PROJECT_ROOT / p).is_file())
    return in_repo[missing]
