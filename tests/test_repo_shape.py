"""The repo must stay small enough to be read.

Run: venv/bin/python -m pytest tests/test_repo_shape.py -q

Yaron's ask, 2026-08-14: a fresh session should be able to understand the whole
workflow, and that only stays true if something refuses the sprawl. His own
principle (_archive/design-record.md §5.5) is the reason this is a test and not a paragraph:
"A rule written in a document is a habit. A constraint is code that refuses."

What it refuses:
  - dead links      — a doc pointing at a moved file actively misleads
  - orphan markdown — a file nothing links to will never be read
  - oversized docs  — a reference doc past 300 lines is two documents
  - a stale citation — a `file.py:LINE symbol` that no longer resolves reads
    as a fact and is not one
  - an unguarded data table — one the commit guard's trigger regex does not
    name is skipped in total silence (Task 7, 2026-08-17: the guard kept
    grepping for a table two renames ago and nothing noticed)
  - a test that can reach a real file under `data/`

`_archive/` is exempt from the doc checks: it is parked by definition, and
holding retired files to today's structure would make archiving anything
impossible.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The documents a session enters through. Nothing links *to* them — CLAUDE.md is
# loaded by the harness, README.md is the front page — so they cannot be orphans.
ENTRY_POINTS = {"CLAUDE.md", "README.md", "REBUILD.md"}

# notes/ is exempt from the orphan rule: it is Yaron's thinking, and a note
# nobody links to is still his. Dead links inside it are still checked.
ORPHAN_EXEMPT_DIRS = ("notes/",)

DOCS_MAX_LINES = 300

# [text](target) — but not ![image](...) and not a reference-style definition.
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def _tracked_markdown() -> list[Path]:
    """Every tracked .md outside _archive/, as repo-relative paths."""
    out = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "*.md"],
        capture_output=True, text=True, check=True,
    )
    # Files must exist on disk, not merely in the index. git ls-files reports a
    # renamed file at its OLD path until the rename is staged, so reading the
    # index alone made this test crash mid-reorganisation — exactly when a
    # repo-shape check is most wanted.
    return [
        Path(line) for line in out.stdout.splitlines()
        if line and not line.startswith("_archive/")
        and (PROJECT_ROOT / line).is_file()
    ]


def _tracked_source_files() -> list[Path]:
    """Every tracked .py and .md file this repo actively maintains, plus the
    commit guard itself — everywhere a stale reference could mislead.

    Code alone missed the actual failure mode (Task 1 round 1 review): every
    stale path a human found by hand lived in prose — a position note, not a
    module. Markdown is included for the same reason `_tracked_markdown()`
    exists at all.

    Three carve-outs, each because the text there is a record of the past,
    not a live description of today's repo, and this repo does not reshape
    history (CLAUDE.md): `_archive/` (via `_tracked_markdown()`),
    `notes/positions/closed/` (a closed position's note — exempt from the
    note-shape guard for the same reason), and `docs/superpowers/plans/` (an
    SDD plan quoting code as it stood when the plan was written, not as it
    stands now).
    """
    out = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "*.py"],
        capture_output=True, text=True, check=True,
    )
    files = [
        Path(line) for line in out.stdout.splitlines()
        if line and not line.startswith("_archive/")
        and (PROJECT_ROOT / line).is_file()
    ]
    files += [
        rel for rel in _tracked_markdown()
        if not str(rel).startswith("notes/positions/closed/")
        and not str(rel).startswith("docs/superpowers/plans/")
    ]
    pre_commit = Path("ops/hooks/pre-commit")
    if (PROJECT_ROOT / pre_commit).is_file():
        files.append(pre_commit)
    return files


def _links_in(rel: Path) -> list[str]:
    """Relative link targets in one file, fragments and URLs stripped out."""
    targets = []
    for raw in LINK.findall((PROJECT_ROOT / rel).read_text(encoding="utf-8")):
        target = raw.split()[0].strip()          # drop an optional "title"
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#")[0]            # notes/X.md#L12 -> notes/X.md
        if target:
            targets.append(target)
    return targets


def test_no_dead_links():
    """Every relative link resolves. A doc pointing at an archived file is
    worse than no doc: it sends the next session somewhere that isn't there."""
    dead = []
    for rel in _tracked_markdown():
        for target in _links_in(rel):
            resolved = (PROJECT_ROOT / rel).parent / target
            if not resolved.exists():
                dead.append(f"{rel} -> {target}")
    assert not dead, (
        "Links pointing at files that do not exist:\n  "
        + "\n  ".join(sorted(dead))
        + "\n\nFix the link, or restore the file. If the target moved to "
          "_archive/, point at its new path."
    )


def test_no_orphan_markdown():
    """Every doc outside notes/ is reachable from another file.

    This is the check that stops the sprawl returning: a file nothing points
    at will not be read by Yaron or by a session, so it is pure weight.
    """
    files = _tracked_markdown()
    linked = set()
    for rel in files:
        for target in _links_in(rel):
            resolved = ((PROJECT_ROOT / rel).parent / target).resolve()
            try:
                linked.add(str(resolved.relative_to(PROJECT_ROOT)))
            except ValueError:          # points outside the repo; the dead-link
                continue                # test owns that failure, not this one

    orphans = [
        str(rel) for rel in files
        if str(rel) not in linked
        and rel.name not in ENTRY_POINTS
        and not str(rel).startswith(ORPHAN_EXEMPT_DIRS)
    ]
    assert not orphans, (
        "Markdown nothing links to:\n  " + "\n  ".join(sorted(orphans))
        + "\n\nLink it from a document that is read, or move it to _archive/."
    )


def test_no_oversized_docs():
    """A reference doc past 300 lines has become two documents.

    docs/ only — REBUILD.md and the journal are long for reasons of their own.

    `docs/superpowers/plans/` is exempt for the same reason. A plan is not a
    reference document: it is a build script for one piece of work, it carries
    the code to write and the commands to run, nobody reads it twice, and it
    moves to `_archive/superpowers/plans/` when the work lands. Splitting one
    would separate a task from the task it depends on, which is the opposite
    of what this test is protecting. Specs are NOT exempt — those are read
    repeatedly and the 300-line pressure on them is wanted.
    """
    too_long = []
    for rel in _tracked_markdown():
        if not str(rel).startswith("docs/"):
            continue
        if str(rel).startswith("docs/superpowers/plans/"):
            continue
        lines = len((PROJECT_ROOT / rel).read_text(encoding="utf-8").splitlines())
        if lines > DOCS_MAX_LINES:
            too_long.append(f"{rel} ({lines} lines)")
    assert not too_long, (
        f"docs/ files over {DOCS_MAX_LINES} lines:\n  " + "\n  ".join(sorted(too_long))
        + "\n\nSplit it, or move the part that is really a design record."
    )


# ── 4. every data/*.csv table is covered by the commit guard, on purpose ──
#
# `ops/hooks/pre-commit` gates its whole data-check section behind one grep
# that names tables literally (line ~31). That is a hardcoded whitelist, and
# a hardcoded whitelist goes stale the same way every time: a table is
# renamed or added, nobody updates the grep, and every commit touching it
# skips the guard with no message at all — not a warning, just silence. Task
# 7 (2026-08-17) hit exactly this: the guard kept grepping for
# `claim_evidence.csv` for two commits after it was renamed to
# `evidence_links.csv`.
#
# Named here rather than duplicated: this table's ids and shape live in a
# table someone has to update by hand the moment they touch the hook, or
# rename a data file, and forgetting is indistinguishable from deciding —
# exactly the gap this test exists to close.

PRE_COMMIT_HOOK = PROJECT_ROOT / "ops" / "hooks" / "pre-commit"

# Tables deliberately left OUTSIDE the trigger regex — the guard skips them on
# purpose, never by accident. Empty today: every tracked data/*.csv fires the
# guard. Adding a table here is a real decision — name it and say why, right
# in this comment, or the next person reading this file cannot tell "decided"
# from "the rename that forgot to update the hook".
UNGUARDED_DATA_TABLES: set[str] = set()


def _delivery_dir() -> str:
    """The directory holding files that are NOT tables of his.

    Read out of `ops/counts.py` rather than spelled again here, for the same
    reason this file reads the commit hook's own regex instead of copying
    it: a second spelling is a second place to go stale.
    """
    text = (PROJECT_ROOT / "ops" / "counts.py").read_text(encoding="utf-8")
    match = re.search(r'INBOX_DIR_NAME = "([^"]+)"', text)
    assert match, "ops/counts.py no longer names the delivery directory"
    return match.group(1)


def _pre_commit_trigger_pattern() -> re.Pattern[str]:
    """The `grep -qE` pattern that gates the commit guard's data-check block.

    Read out of the hook file rather than copied by hand into this test —
    copying it would just move the staleness risk from the hook to the test,
    and the two could drift from each other the same way the hook and
    reality already have once.
    """
    text = PRE_COMMIT_HOOK.read_text(encoding="utf-8")
    match = re.search(r"grep -qE '(\^data/[^']+)'", text)
    assert match, (
        "could not find the data-table trigger regex in "
        f"{PRE_COMMIT_HOOK} — has section 1 moved or been reworded?"
    )
    return re.compile(match.group(1))


def test_every_data_table_is_guarded_or_allowlisted():
    """Every tracked data/*.csv either fires the commit guard, or is named as
    deliberately exempt in UNGUARDED_DATA_TABLES above.

    The failure mode this closes is silent: a table the trigger regex does
    not name is not refused and not warned about, it is simply never checked
    — the same class of gap Task 7 fixed for `claim_evidence.csv` ->
    `evidence_links.csv`/`evidence_summaries.csv`, patched for those two
    names rather than at the mechanism. This test is the mechanism.
    """
    pattern = _pre_commit_trigger_pattern()
    out = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "data/*.csv"],
        capture_output=True, text=True, check=True,
    )
    # Committed since 2026-10-06 so a dated snapshot cannot be lost, but an
    # issuer's download is not a table of his — it has no schema of ours and
    # nothing validates it. The rows PARSED out of it into
    # etf_holdings.csv are the guarded thing.
    delivery = f"/{_delivery_dir()}/"
    tables = [line for line in out.stdout.splitlines()
              if line and delivery not in line]
    allowlisted = {f"data/{name}.csv" for name in UNGUARDED_DATA_TABLES}
    unguarded = [
        t for t in tables if not pattern.match(t) and t not in allowlisted
    ]
    assert not unguarded, (
        "data/*.csv table(s) not covered by the commit guard's trigger regex "
        f"({PRE_COMMIT_HOOK}) and not named in UNGUARDED_DATA_TABLES here:\n  "
        + "\n  ".join(sorted(unguarded))
        + "\n\nEither add the table to the regex's alternation so the guard "
          "checks it, or add its name to UNGUARDED_DATA_TABLES with a "
          "comment saying why it is deliberately exempt."
    )


# ─── Citations in docs/ must point at real code ───

_CITED_FILES = {
    "pre-commit": "ops/hooks/pre-commit",
    "refresh_etf_compositions.py": "ops/refresh_etf_compositions.py",
    "ops/refresh_etf_compositions.py": "ops/refresh_etf_compositions.py",
}


def _resolve_cited(name: str):
    """A cited filename -> its path, or None if we cannot place it."""
    if name in _CITED_FILES:
        return PROJECT_ROOT / _CITED_FILES[name]
    if name.endswith(".py"):
        for base in ("src", "ops", "tests"):
            p = PROJECT_ROOT / base / name
            if p.is_file():
                return p
    p = PROJECT_ROOT / name
    return p if p.is_file() else None


def test_every_file_line_citation_in_docs_points_at_real_code():
    """`claims.py:398` must name a line that still does what the doc says.

    This cannot prove the line is the RIGHT one, but it catches the failure that
    has now happened twice: a commit inserts lines above a cited one and every
    citation below it silently slides. Both times the stale numbers landed on
    blanks and comments, and both times they sat in the file whose stated
    purpose is "so a session can verify a claim rather than trust it".

    Adding a line to src/claims.py will turn this red. That is the point: fix
    the citation in the same commit, or the map starts lying.
    """
    # Two patterns, deliberately. The second catches a citation written in the
    # OLD bare form — tightening the first to require a symbol left bare ones
    # matching nothing at all, so they would have got zero checking rather than
    # the weak checking they had before (found by review 2026-09-28).
    pattern = re.compile(
        r"`([A-Za-z0-9_/.\-]+\.py|pre-commit):(\d+)(?:-\d+)? ([A-Za-z_][A-Za-z0-9_]*)`")
    bare = re.compile(r"`([A-Za-z0-9_/.\-]+\.py|pre-commit):(\d+)(?:-\d+)?`")
    problems = []
    for doc in sorted((PROJECT_ROOT / "docs").glob("*.md")):
        for m in pattern.finditer(doc.read_text(encoding="utf-8")):
            name, lineno, symbol = m.group(1), int(m.group(2)), m.group(3)
            path = _resolve_cited(name)
            if path is None:
                problems.append(f"{doc.name}: cites {name}, which is not a file")
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if lineno > len(lines):
                problems.append(
                    f"{doc.name}: {name}:{lineno} is past the end of the file "
                    f"({len(lines)} lines)")
                continue
            text = lines[lineno - 1].strip()
            if not text or text.startswith("#"):
                problems.append(
                    f"{doc.name}: {name}:{lineno} is a blank line or a comment "
                    f"({text[:50]!r}) — the code it pointed at has moved")
            elif symbol not in text:
                # The real check. Checking only for non-blank was too weak: when
                # `said_by` and then `decision` shifted claims.py, every citation
                # below slid onto a DIFFERENT line of real code and the test
                # stayed green. Naming the symbol is what makes the number
                # verifiable — this is the fourth time they drifted.
                problems.append(
                    f"{doc.name}: {name}:{lineno} should name {symbol!r} but that "
                    f"line is {text[:60]!r} — the code has moved")
    for doc in sorted((PROJECT_ROOT / "docs").glob("*.md")):
        text = doc.read_text(encoding="utf-8")
        for m in bare.finditer(text):
            problems.append(
                f"{doc.name}: {m.group(0)} has no symbol — write it as "
                f"`{m.group(1)}:{m.group(2)} the_function_name` so the number "
                f"can be checked against what is actually on that line")

    assert not problems, (
        "stale file:line citations in docs/:\n  " + "\n  ".join(problems)
        + "\n\nRe-derive them: grep for the function or check the line actually "
          "does what the doc claims."
    )


def test_no_code_points_at_the_old_compositions_path():
    """A reader left on data/etf_compositions.csv fails silently.

    exposure.load() returns an empty frame for a missing compositions file
    rather than raising, so a stale path does not crash — it reports every
    sector at zero and looks entirely healthy. Grep is the only guard.
    """
    # This file itself names the pre-move string, twice, on purpose — that is
    # what makes it a guard. Scanning it would flag itself forever, which is
    # not a stale reference; it is the test.
    here = Path(__file__).resolve().relative_to(PROJECT_ROOT)
    # notes/positions/watchlist/PEJ.md:91 names the old path inside a dated
    # "2026-05-29 — Pre-trade thesis drafted" log entry — a record of a
    # checklist item from a trade that was never opened (PEJ is still
    # watchlist today), not a live instruction. Judged as history, like
    # notes/positions/closed/, so it is named here rather than updated.
    pej_watchlist = Path("notes/positions/watchlist/PEJ.md")
    hits = []
    for rel in _tracked_source_files():
        if rel == here or rel == pej_watchlist:
            continue
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if 'data/etf_compositions.csv' in line or '"data" / "etf_compositions.csv"' in line:
                hits.append(f"{rel}:{n}")
    assert not hits, (
        "these still point at the pre-move compositions path:\n  " + "\n  ".join(hits)
        + "\n\nThe table lives in data/market/ now."
    )


def test_nothing_still_points_at_a_note_that_moved_into_closed():
    """A note moved into `notes/positions/closed/` must leave no dangling
    pointer behind it — including from inside `_archive/`.

    ⚠️ `test_no_dead_links` structurally cannot see this. `_tracked_markdown()`
    skips every path starting `_archive/`, and when RSP, TLT, AXP, XLRE and
    KWEB moved on 2026-09-28 the two links that broke were both archived
    weeklies: `_archive/notes/market/2026-W22.md` -> `../positions/XLRE.md`
    and `2026-W21.md` -> `../positions/KWEB.md`. Six other dead
    `../positions/` links in `_archive/` predate that move and are not this
    test's business, so this checks only the names that actually live in
    `closed/`.
    """
    closed_dir = PROJECT_ROOT / "notes" / "positions" / "closed"
    if not closed_dir.is_dir():
        return
    moved = {p.name for p in closed_dir.glob("*.md")}
    dangling = []
    for rel in PROJECT_ROOT.rglob("*.md"):
        if ".git" in rel.parts:
            continue
        try:
            targets = _links_in(rel.relative_to(PROJECT_ROOT))
        except (OSError, UnicodeDecodeError):
            continue
        for target in targets:
            if Path(target).name not in moved:
                continue
            if not (rel.parent / target).exists():
                dangling.append(f"{rel.relative_to(PROJECT_ROOT)} -> {target}")
    assert not dangling, (
        "These point at a position note that has moved into "
        "notes/positions/closed/:\n  "
        + "\n  ".join(sorted(dangling))
        + "\n\nRepoint them. A closed position is kept in view precisely so "
          "it can still be read."
    )


# ─── 5. the guard that stops a test writing to a real data file ───
#
# `tests/conftest.py` installs it. A guard nothing exercises is a guard that
# can be broken by an unrelated refactor and stay broken silently — which is
# the whole failure mode it was written to stop.

# ⚠️ EVERY destructive call below aims at a path under `data/` that does not
# exist, inside a directory that does not exist either. That is the rule, and
# it is not a style preference: on 2026-10-04 a test here aimed a real
# `rmtree` at the real `data/` directory to prove the guard would stop it, the
# guard did not, and the directory went. A test for a guard must never need
# the guard to work in order to be safe. Written this way, a guard that stops
# firing makes these tests fail with FileNotFoundError and harms nothing.
#
# Where the real tables are involved at all — the symlink case needs a link to
# something — the assertion is made against the predicate, which touches no
# file system.

#: A path the guard must refuse, whose parent does not exist, so a refusal
#: that fails to happen cannot create or truncate anything.
GUARD_PROBE = PROJECT_ROOT / "data" / "_guard_probe_dir" / "instruments.csv"


def _guard():
    """`tests/conftest.py` as a module, so its predicate can be called directly.

    Imported by path rather than by name: pytest loads conftest under a
    private module name that is not importable as `conftest`.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_conftest_under_test", PROJECT_ROOT / "tests" / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_test_cannot_write_to_a_real_data_file():
    """It fired for real on 2026-10-04 and nothing caught it but a human."""
    import pytest

    assert not GUARD_PROBE.parent.exists()
    with pytest.raises(AssertionError, match="real data file"):
        GUARD_PROBE.write_text("destroyed")
    with pytest.raises(AssertionError, match="real data file"):
        open(GUARD_PROBE, "w").close()
    assert not GUARD_PROBE.parent.exists(), "the guard let the write through"


def test_the_refusal_says_what_to_do_instead():
    """A rejection that does not teach the fix just gets worked around."""
    import pytest

    probe = PROJECT_ROOT / "data" / "_guard_probe_dir" / "claims.csv"
    with pytest.raises(AssertionError, match=r"path=tmp_path"):
        probe.write_text("x")


def test_reading_a_real_data_file_is_still_allowed():
    """The convention is that tests read the real reference tables."""
    assert (PROJECT_ROOT / "data" / "sectors.csv").read_text().strip()


def test_a_symlink_cannot_be_used_to_reach_a_real_data_file(tmp_path):
    """The hole that let a path under `data/` be reached by another name.

    The first version of the guard normalised without following symlinks, so
    a link inside `tmp_path` wrote straight through to a real table. A guard
    with a known hole is worse than no guard, because it is the one that gets
    trusted.

    The link here dangles, pointing at a path under `data/` that does not
    exist. That exercises the identical code path — the link's own name is
    not under `data/` and only resolving it says otherwise — while a
    regression costs a FileNotFoundError instead of a live table.
    """
    import pytest

    link = tmp_path / "innocent.csv"
    link.symlink_to(GUARD_PROBE)
    with pytest.raises(AssertionError, match="real data file"):
        link.write_text("destroyed")
    assert not GUARD_PROBE.parent.exists()


def test_a_symlink_to_a_table_that_really_exists_is_refused_too(tmp_path):
    """The dangling link above proves the resolution; this proves the target.

    Asserted against the predicate, so a regression cannot reach the file
    this test is named after.
    """
    import pytest

    link = tmp_path / "innocent.csv"
    link.symlink_to(PROJECT_ROOT / "data" / "instruments.csv")
    with pytest.raises(AssertionError, match="real data file"):
        _guard()._refuse_if_protected(link, "write to")


def test_a_real_data_file_cannot_be_deleted_either(tmp_path):
    """Destroying a table is as bad as corrupting one, and was unguarded.

    ⚠️ Every destructive call below is aimed at a path under `data/` that
    DOES NOT EXIST. That is deliberate and must stay that way. The first
    version of this test aimed a real `shutil.rmtree` at the real `data/`
    directory to prove the guard would stop it — the guard did not, because
    it only matched paths *under* `data/` and not the directory itself, and
    the test deleted the thing it was written to protect. A test for a guard
    must never need the guard to work in order to be safe.
    """
    import os
    import shutil

    import pytest

    absent = PROJECT_ROOT / "data" / "_guard_probe_that_does_not_exist.csv"
    absent_dir = PROJECT_ROOT / "data" / "_guard_probe_dir_that_does_not_exist"
    assert not absent.exists() and not absent_dir.exists()
    for attempt in (
        lambda: absent.unlink(),
        lambda: os.remove(absent),
        lambda: os.unlink(absent),
        lambda: os.truncate(absent, 0),
        lambda: shutil.rmtree(absent_dir),
        lambda: absent_dir.rmdir(),
        lambda: os.rmdir(absent_dir),
        lambda: os.rename(absent, tmp_path / "stolen.csv"),
    ):
        # If the guard ever stops firing this raises FileNotFoundError, which
        # fails the test and harms nothing.
        with pytest.raises(AssertionError, match="real data file"):
            attempt()


def test_the_data_directory_itself_is_protected_not_only_what_is_in_it():
    """The hole that let the guard's own test delete `data/`.

    Asserted against the predicate rather than by calling `rmtree`, because a
    test that proves a guard works must not depend on the guard working.
    """
    import pytest

    with pytest.raises(AssertionError, match="real data file"):
        _guard()._refuse_if_protected(PROJECT_ROOT / "data", "delete")
