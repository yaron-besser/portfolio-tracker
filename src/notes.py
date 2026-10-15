"""Note shape — the one thing that stops 19 notes becoming 75 headings.

Why this exists (Phase 2h). Yaron's complaint, 2026-08-13: "every note looks
different." He was right — 19 position notes used 75 distinct section headings
and 57 of those appeared in exactly one note. A template already existed and
had simply drifted, because nothing checked it.

So the required headings are declared here, checked at commit, and deliberately
few. Two per note type:

    notes/positions/*.md   ## Bear case          ## Catalysts
    notes/clusters/*.md    ## Why this cluster exists   ## What would end it

Everything below them is his and unconstrained. The rigid part is small on
purpose: what can be sorted or filtered lives in `claims.csv` and the
reference tables, so the prose only has to carry what a cell cannot.

**Only staged notes are checked.** Holding history to today's shape would
block every commit until all 19 old notes were rewritten — the same trap that
would have made `check_append_only()` reject its own first migration. The rule
applies to notes as they are written, and `status()` lists the rest.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from src.claims import PROJECT_ROOT

REQUIRED: dict[str, list[str]] = {
    "notes/positions": ["Bear case", "Catalysts"],
    "notes/clusters": ["Why this cluster exists", "What would end it"],
}

# Files that are not notes about one thing.
EXEMPT = {"README.md", "_index.md"}


def _kind(path: str) -> str | None:
    """Which rule applies to this path, or None if it is not a shaped note."""
    p = str(path).replace("\\", "/")
    if Path(p).name in EXEMPT:
        return None
    for prefix in REQUIRED:
        # `_archive/` under a notes dir is history — never reshaped. So are
        # `closed/` and `watchlist/`: his call that a finished trade and a
        # name he is still considering both belong somewhere he would look,
        # rather than in `_archive/`, which had become a junk drawer. The
        # waiver is not cosmetic — these notes predate the required headings,
        # and writing the missing ones would mean inventing his thesis.
        #
        # `watchlist/` is the weaker case of the two and worth stating: a
        # name he has not bought has no position to bear-case, and demanding
        # one would put four permanent TODOs in a queue ordered by money he
        # has not spent.
        if (p.startswith(prefix + "/")
                and "/_archive/" not in p
                and "/closed/" not in p
                and "/watchlist/" not in p):
            return prefix
    return None


def headings(text: str) -> list[str]:
    """The `## ` headings in a note, in order."""
    return [h.strip() for h in re.findall(r"^##\s+(.+?)\s*$", text, re.M)]


# A line that is only structure, only a placeholder, or only the template's own
# guidance is not something Yaron wrote. Used by unwritten_sections().
#
# `_LEAD` strips everything that can sit in front of the word: bullets, numbers,
# checkboxes, block quotes, bold. All of `- TODO`, `1. TODO`, `- [ ] TODO` and
# `**TODO — write this in your own words.**` are the placeholder — the first
# three appear 17 times across live notes, and an emphasis-only prefix class
# missed every one of them.
#
# A stated decision is deliberately NOT a placeholder. GRNY's "Not written —
# this is a fund I don't do anything on" and "n/a — I hold this as pure beta"
# are answers, and the answer was no. Hence `todo`/`tbd` match anywhere in the
# line's opening, but `n/a` only when it is the entire line: a queue item he
# cannot clear is a queue item he stops reading.
_LEAD = re.compile(r"^(?:[-*+>]\s*|\d+[.)]\s*|\[[ xX]\]\s*|[*_`]+)*")
_PLACEHOLDER = re.compile(r"^(todo|tbd)\b|^n/?a$", re.I)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_RULE = re.compile(r"^([-_*=])\1{2,}$")            # ___  ---  ***  (not ~~~)
_EMPTY_BULLET = re.compile(r"^[-*+]\s*(\[[ xX]\])?\s*[….\s]*$")   # "- [ ] …"
_ONE_LINE_COMMENT = re.compile(r"^<!--.*-->$")


def _bare(line: str) -> str:
    """The line with list markers, checkboxes, quoting and emphasis stripped."""
    return _LEAD.sub("", line.strip()).strip()


def _without_noise(text: str) -> list[str]:
    """The note's lines with fenced code and HTML comments blanked out.

    Blanked rather than removed so line positions still line up — a `## Bear
    case` inside a fence or a comment is an example, not a heading, and must not
    become a section boundary.

    ``~~~`` is a fence here and NOT a horizontal rule; `_RULE` agrees. The two
    disagreed for one revision, so a pair of `~~~` separators blanked the whole
    section between them and an unwritten note read `ok`.
    """
    out, fence, comment = [], False, False
    for ln in text.splitlines():
        s = ln.strip()
        if comment:
            out.append("")
            comment = "-->" not in s
            continue
        if s.startswith("<!--"):
            # Self-contained or not, the line itself is a marker, never writing.
            # `write_fundamentals_to_notes()` appends `<!-- AUTO:FUNDAMENTALS-
            # START -->`, and a machine-written marker counting as his writing is
            # exactly the P1 failure this module exists to prevent.
            out.append("")
            comment = "-->" not in s
            continue
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
            out.append("")
            continue
        out.append("" if fence else ln)
    return out


@lru_cache(maxsize=1)
def _boilerplate() -> frozenset[str]:
    """Every line of the shipped templates, so template guidance left in place
    is not mistaken for his writing.

    Self-maintaining on purpose: edit a template and this follows. `check()`'s
    own error message tells him to start from `notes/templates/`, so a note that
    still carries those sentences is a note nobody has answered yet.
    """
    d = PROJECT_ROOT / "notes" / "templates"
    if not d.is_dir():
        return frozenset()
    return frozenset(
        s for f in d.glob("*.md")
        for s in (ln.strip() for ln in
                  _without_noise(f.read_text(encoding="utf-8", errors="replace")))
        if s and not s.startswith("#")
    )


def _is_writing(line: str, boiler: frozenset[str]) -> bool:
    """Is this line something he actually wrote?"""
    s = line.strip()
    return bool(
        s
        and s not in boiler                # the template's own prompt text
        and not s.startswith("#")          # a sub-heading is structure
        and not _ONE_LINE_COMMENT.match(s)  # <!-- AUTO:FUNDAMENTALS-START -->
        and not _PLACEHOLDER.match(_bare(s))   # TODO, - TODO, **TODO — …**, TBD
        and not _RULE.match(s)             # ___ under an empty Bear case
        and not _EMPTY_BULLET.match(s)     # "- [ ] …" straight from the template
    )


def _has_writing(body: list[str], boiler: frozenset[str]) -> bool:
    """Does this section say something of his?

    Paragraph, not line: the placeholder here wraps — "TODO — not written yet.
    This note predates the required / shape; the section is here so the gap is
    visible rather than silent." Line-by-line, the second half looks like prose,
    and eight held positions read `ok`.

    **The first paragraph that is not template boilerplate decides.** If it is a
    placeholder, the section is unwritten no matter what follows — because what
    follows is draft material, not his. `agriculture.md` opens "**TODO — write
    this in your own words.** Strong prompts to help draft:" and then four
    numbered paragraphs of drafting. Counting those as his writing is the P1
    failure this whole module exists to prevent.
    """
    for para in _paragraphs(body):
        if _is_placeholder_para(para):
            return False               # the section says of itself: not yet
        if any(_is_writing(x, boiler) for x in para):
            return True
        # pure boilerplate or structure — skip it and let the next one decide,
        # so template guidance left above real prose does not hide the prose
    return False


def _is_placeholder_para(para: list[str]) -> bool:
    """Does this paragraph announce itself as unwritten?

    Prose wraps and a list does not, so they are judged differently. "TODO — not
    written yet. This note predates the required / shape;…" is one placeholder
    across two lines. But `- TODO` followed by `- the real reason` is a list with
    one item filled in, and calling the whole thing a placeholder would hide his
    writing.
    """
    if not _PLACEHOLDER.match(_bare(para[0])):
        return False
    if _BULLET.match(para[0]):
        return all(_PLACEHOLDER.match(_bare(x)) or not _bare(x) for x in para)
    return True


def _paragraphs(body: list[str]) -> list[list[str]]:
    """Blank-line-separated blocks, blanks dropped."""
    out, cur = [], []
    for ln in [*body, ""]:
        if ln.strip():
            cur.append(ln)
        elif cur:
            out.append(cur)
            cur = []
    return out


def check(path: str, text: str) -> list[str]:
    """Problems with one note's shape. Empty list means it is fine."""
    kind = _kind(path)
    if kind is None:
        return []
    found = headings(text)
    missing = [h for h in REQUIRED[kind] if h not in found]
    if not missing:
        return []
    return [
        f"{path} is missing the heading(s) {missing}.\n"
        f"  Every note in {kind}/ must have: "
        f"{', '.join('## ' + h for h in REQUIRED[kind])}\n"
        f"  Start from notes/templates/, or write it with:  /note <NAME>\n"
        f"  Anything else you add below them is yours."
    ]


def check_paths(paths) -> None:
    """Raise if any of these notes is the wrong shape. Used by the commit guard."""
    problems = []
    for p in paths:
        f = PROJECT_ROOT / p
        if f.is_file():
            problems.extend(check(p, f.read_text(encoding="utf-8", errors="replace")))
    if problems:
        raise ValueError("\n".join(problems))


def stub(instrument_id: str, name: str = "", kind: str = "notes/positions",
         path: Path | None = None) -> Path:
    """Create an EMPTY note — headings and TODO, nothing else. Never overwrites.

    ⚠️ This is the one thing a machine may write into `notes/`, and only because
    Yaron said so on 2026-08-15 after being shown the conflict with P1: an empty
    form contains no thinking, so nothing in it can be mistaken for his words.
    P1 carries that second narrowing.

    **Anything beyond `TODO` under a heading stays forbidden.** The moment this
    writes a sentence, it is authorship, and the record stops being trustworthy —
    a year later he could not tell his reasoning from a machine's.

    It exists so a position he bought without explaining stops being invisible:
    the file appears in `status()` as `needs reshaping` instead of `missing`,
    and shows up in the writing queue.
    """
    p = path or (PROJECT_ROOT / kind / f"{instrument_id}.md")
    if p.exists():
        return p                      # never clobber something he wrote
    title = f"# {instrument_id}" + (f" — {name}" if name else "")
    body = "\n\n".join(f"## {h}\n\nTODO" for h in REQUIRED[kind])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{title}\n\n{body}\n", encoding="utf-8")
    return p


def status() -> pd.DataFrame:
    """Every holding and cluster, and whether its note is written and in shape.

    The tracker for the rewrite: one view, ordered by money, so the next thing
    to write is always obvious and nothing is quietly skipped.
    """
    from src import clusters, instruments

    inst = instruments.load().fillna("").set_index("instrument_id")
    pos = pd.read_csv(PROJECT_ROOT / "data" / "positions.csv",
                      dtype=str, keep_default_na=False)
    pos["mv"] = pd.to_numeric(pos["market_value"], errors="coerce").fillna(0.0)
    mv = pos.groupby("instrument_id")["mv"].sum()

    rows = []
    for iid, v in mv.items():
        if iid not in inst.index or inst.at[iid, "instrument_type"] in ("cash", "fx"):
            continue
        ref = str(inst.at[iid, "note_ref"]).strip()
        rows.append({"what": iid, "type": "holding",
                     "cluster": inst.at[iid, "cluster_id"],
                     "market_value": round(float(v), 2), **_state(ref)})

    cl = clusters.load().fillna("")
    parent_of = dict(zip(cl["cluster_id"], cl["parent"].astype(str).str.strip()))
    kids: dict[str, list[str]] = {}
    for cid, p in parent_of.items():
        if p:
            kids.setdefault(p, []).append(cid)

    for _, r in cl[cl["status"] == "active"].iterrows():
        cid = r["cluster_id"]
        # A group holds no instruments of its own, so its money is its
        # children's — the same rollup weights() does.
        family = [cid, *kids.get(cid, [])]
        held = [i for i in mv.index
                if i in inst.index and inst.at[i, "cluster_id"] in family]
        rows.append({"what": cid,
                     "type": "group" if r["layer"] == "group" else "cluster",
                     "cluster": cid,
                     "market_value": round(float(mv[held].sum()), 2) if held else 0.0,
                     **_state(str(r["note_ref"]).strip())})

    df = pd.DataFrame(rows)
    money = dict(zip(df["what"], df["market_value"]))

    def _key(row: pd.Series) -> tuple:
        """Group, then the clusters inside it, then the holdings inside those.

        Ordering by money alone breaks the moment groups exist: Core US is
        worth more than AI Infrastructure and would land between Technology
        and its own children. So the family comes first in the key, the group
        row ahead of its children, and a cluster ahead of its holdings —
        which is the rule this function already had, one level deeper.
        """
        cid = row["cluster"] if row["type"] == "holding" else row["what"]
        top = parent_of.get(cid, "") or cid
        # `money` holds only active rows, but `parent_of` holds every row. A
        # child of a retired or `empty` group keyed off a family worth 0.0 and
        # sank below everything — a $600 theme under a $50 one. If the group
        # is not in the queue, the child IS the top of its family.
        # `empty` is the honest status for a group the moment it is created,
        # so this was reachable by the ordinary order of building one
        # (found by review, 2026-08-16).
        if top not in money:
            top = cid
        return (-money.get(top, 0.0), top,
                0 if row["type"] == "group" else 1,
                -money.get(cid, 0.0), cid,
                1 if row["type"] == "holding" else 0,
                -row["market_value"], row["what"])

    return (df.loc[sorted(df.index, key=lambda i: _key(df.loc[i]))]
              .reset_index(drop=True))


def unwritten_sections(ref: str, text: str) -> list[str]:
    """Required headings that exist but say nothing but TODO.

    Checked PER HEADING, not across the whole file. The first version asked
    whether the *entire* note was TODO, which missed every real case: VTI has an
    opening sentence and TODO under both required headings, so it reported `ok`
    and left the writing queue with nothing written. Core US, QQQ and RSP the
    same. Five unwritten sections were reporting as finished work (found by
    review 2026-08-15).

    `check()` only asks whether the headings EXIST. This asks whether they were
    answered.

    A second review the same day found the first per-heading version still let
    13 notes through, because it only recognised a section whose body was the
    bare word `TODO`. The spelling actually in the repo is `TODO — not written
    yet. This note predates the required shape…`, which is non-blank and so
    counted as prose. ASML, MAGS, PLTR and ten others read `ok` while saying
    nothing. Hence `_is_writing()`, which asks the real question: is any line
    here *his*?
    """
    kind = _kind(ref)
    if kind is None:
        return []
    lines = _without_noise(text)
    # A heading may appear twice — notes grow by appending dated sections. Keep
    # every occurrence: a dict would drop all but the last and compute the wrong
    # end boundary for the ones it dropped.
    #
    # Same regex as headings(), so this and check() agree on what a heading is.
    # They did not for one revision — this used `startswith("## ")` on the
    # BLANKED text, check() used `^##\s+` on the raw text — so an unterminated
    # fence above the headings made check() pass, this find nothing, and a note
    # that was 100% TODO report `ok` with nothing anywhere to catch it.
    heads = [(i, m.group(1).strip()) for i, ln in enumerate(lines)
             if (m := re.match(r"^##\s+(.+?)\s*$", ln))]
    seen = {h for _, h in heads}
    boiler = _boilerplate()
    empty = []
    for h in REQUIRED[kind]:
        spans = [(i, next((j for j, _ in heads if j > i), len(lines)))
                 for i, name in heads if name == h]
        if not spans:
            # check() owns a heading that is genuinely absent. But if it is in
            # the raw text and not here, a fence or comment swallowed it — that
            # is not an answer, so say so rather than falling through to `ok`.
            if h not in seen and h in headings(text):
                empty.append(h)
            continue
        # Written if ANY occurrence says something — appending an empty second
        # copy of a heading does not un-write the first.
        if any(_has_writing(lines[a + 1:b], boiler) for a, b in spans):
            continue
        empty.append(h)
    return empty


def is_unwritten(text: str, ref: str) -> bool:
    """True when every required section of a note is still TODO.

    `ref` is required, not defaulted. It used to default to a *positions* path,
    so calling it with a cluster note returned False for a file that was 100%
    TODO — the position headings were absent, so nothing was checked and the
    answer looked confident (review, 2026-08-15).
    """
    kind = _kind(ref)
    return bool(kind) and unwritten_sections(ref, text) == REQUIRED[kind]


# ─── the in-page note pop-up (2026-10-07) ──────────────────────────────────
#
# Yaron, 2026-10-06: clicking a theme or a holding should open his thesis
# "right there", not navigate to a new page that shows raw Markdown as plain
# text. The page is one self-contained file (CLAUDE.md, D1) that can make no
# request once built, so the note has to be read and embedded at build time
# — this is the one place that happens. `ops/build_page.py` turns `.text`
# into HTML and wires up the overlay; `src/page.py` only forwards what this
# returns. Neither of those two ever opens a file of its own.

class NoteText(NamedTuple):
    """One note's content, for the in-page pop-up.

    `ok` is False exactly when `ref` is blank or does not resolve to a real
    file on disk — the same fact `instruments.stale_refs()` already lists
    as a standing report, checked again here at the moment a note is
    actually opened, so a stale ref never crashes the build or opens an
    empty box. `title`/`text` are both `""` when `ok` is False;
    `ops/build_page.py` is the one place that turns that into the
    plain-language message the overlay shows — this module only reports
    what it found.
    """
    ref: str
    ok: bool
    title: str
    text: str


_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)


def read_for_overlay(ref: str) -> NoteText:
    """One note's raw Markdown and its own title, read once.

    `src/page.py` calls this for every note_ref a rendered bar actually
    links to — derived from the hrefs the shaped context produces, never
    from a directory scan — and forwards the result unchanged; it does not
    open a file itself, and neither does `ops/build_page.py`, which only
    turns `.text` into HTML. Reading a note's bytes is this module's job;
    rendering Markdown to HTML is rendering, and belongs there instead.

    `title` is the note's own first `# ` heading, verbatim — never
    reworded, the same "read it back in his own words" rule the rest of
    this project already follows for a note's body. Falls back to `ref`
    itself only when a note genuinely has none; every note this overlay
    actually reaches has one today.
    """
    ref = str(ref or "").strip()
    if not ref:
        return NoteText(ref=ref, ok=False, title="", text="")
    f = PROJECT_ROOT / ref
    if not f.is_file():
        return NoteText(ref=ref, ok=False, title="", text="")
    text = f.read_text(encoding="utf-8", errors="replace")
    m = _TITLE_RE.search(text)
    title = m.group(1).strip() if m else ref
    return NoteText(ref=ref, ok=True, title=title, text=text)


def _state(ref: str) -> dict:
    """Whether a note exists, has its headings, and actually says anything."""
    if not ref:
        return {"note": "", "state": "missing"}
    f = PROJECT_ROOT / ref
    if not f.is_file():
        return {"note": ref, "state": "broken link"}
    text = f.read_text(encoding="utf-8", errors="replace")
    if check(ref, text):
        return {"note": ref, "state": "needs reshaping"}
    blank = unwritten_sections(ref, text)
    if blank:
        return {"note": ref, "state": f"TODO: {', '.join(blank)}"}
    return {"note": ref, "state": "ok"}
