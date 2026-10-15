"""Tests for the commit guard itself.

Run: venv/bin/python -m pytest tests/test_pre_commit.py -q

The guard is the only thing that binds the REPOSITORY rather than the chat
session, so it is the last line between a bad artefact and permanent history.
Until 2026-09-27 exactly one test touched it (`test_repo_shape.py` checks the
trigger regex names every data table) and not one of its RULES was covered.

Each test builds a throwaway git repo in `tmp_path`, symlinks the real hook
into it, stages one bad artefact and asserts the commit is refused. The real
repo is never touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "ops" / "hooks" / "pre-commit"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=repo, env=env,
                          capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A scratch repo wired to the real hook.

    `venv/`, `src/` and `ops/` are symlinked rather than copied so the hook
    runs the same code the real repo runs — the point is to test THIS guard,
    not a copy of it.
    """
    r = tmp_path / "scratch"
    r.mkdir()
    _git(r, "init", "-q", "-b", "work")
    # `src` and `ops` are COPIED, not symlinked: PROJECT_ROOT is
    # `Path(__file__).resolve().parents[1]`, so a symlinked src/ would resolve
    # back to the real repo and every data check would validate the real
    # tables instead of the scratch ones. `venv` is symlinked — it is large,
    # read-only here, and carries no paths of ours.
    (r / "venv").symlink_to(ROOT / "venv")
    for name in ("src", "ops"):
        shutil.copytree(ROOT / name, r / name)
    (r / "data").mkdir()
    (r / "notes" / "screenshots").mkdir(parents=True)
    # A first commit, so HEAD exists for the append-only comparisons.
    (r / "README.md").write_text("scratch\n", encoding="utf-8")
    _git(r, "add", "README.md")
    _git(r, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "init")
    hooks = r / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").symlink_to(r / "ops" / "hooks" / "pre-commit")
    return r


def _commit(repo: Path) -> subprocess.CompletedProcess:
    return _git(repo, "commit", "-q", "-m", "attempt")


def _png(path: Path, size: int = 1024) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * size)


# ─── Rule 0: never commit to main ───

def test_a_commit_to_main_is_refused(repo: Path):
    _git(repo, "checkout", "-q", "-b", "main")
    (repo / "x.txt").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "x.txt")
    out = _commit(repo)
    assert out.returncode != 0
    assert "Refusing to commit directly to main" in out.stdout + out.stderr


def test_the_same_commit_passes_on_a_branch(repo: Path):
    (repo / "x.txt").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "x.txt")
    assert _commit(repo).returncode == 0


# ─── Rule 3: screenshots ───

def test_a_misnamed_screenshot_is_refused(repo: Path):
    _png(repo / "notes" / "screenshots" / "nvda.png")
    _git(repo, "add", "notes/screenshots/nvda.png")
    out = _commit(repo)
    assert out.returncode != 0
    assert "Bad screenshot name" in out.stdout + out.stderr


def test_an_unknown_chart_subject_is_refused(repo: Path):
    _png(repo / "notes" / "screenshots" / "2026-09-26-NVDA-sentiment.png")
    _git(repo, "add", "notes/screenshots/2026-09-26-NVDA-sentiment.png")
    out = _commit(repo)
    assert out.returncode != 0
    assert "Unknown chart subject" in out.stdout + out.stderr


def test_a_screenshot_over_500kb_is_refused(repo: Path):
    _png(repo / "notes" / "screenshots" / "2026-09-26-NVDA-price-1d.png",
         size=520_000)
    _git(repo, "add", "notes/screenshots/2026-09-26-NVDA-price-1d.png")
    out = _commit(repo)
    assert out.returncode != 0
    assert "over 500 KB" in out.stdout + out.stderr


def test_a_well_formed_screenshot_is_accepted(repo: Path):
    _png(repo / "notes" / "screenshots" / "2026-09-26-NVDA-price-1d.png")
    _git(repo, "add", "notes/screenshots/2026-09-26-NVDA-price-1d.png")
    assert _commit(repo).returncode == 0


def test_a_ticker_with_a_dot_is_accepted(repo: Path):
    """Samsung is `005930.KS` and the euro pair is `EUR.USD`.

    The pattern excluded `.`, so the one holding he could never file a chart
    for was the one he had just bought (2026-09-09, claim c0027).
    """
    _png(repo / "notes" / "screenshots" / "2026-09-26-005930.KS-price-1d.png")
    _git(repo, "add", "notes/screenshots/2026-09-26-005930.KS-price-1d.png")
    out = _commit(repo)
    assert out.returncode == 0, out.stdout + out.stderr


def test_the_inbox_is_not_policed(repo: Path):
    """Raw drops land in `_inbox/` under any name; filing is what renames them."""
    _png(repo / "notes" / "screenshots" / "_inbox" / "Screenshot 2026.png")
    _git(repo, "add", "notes/screenshots/_inbox/Screenshot 2026.png")
    assert _commit(repo).returncode == 0


# ─── Rule 1: foreign keys must be checked on the STAGED copy ───

def _seed_tables(repo: Path) -> None:
    """Real clusters.csv and sectors.csv, so only the instrument row varies."""
    for name in ("clusters.csv", "sectors.csv"):
        shutil.copy(ROOT / "data" / name, repo / "data" / name)
    (repo / "data" / "instruments.csv").write_text(
        '"instrument_id","name","instrument_type","broker_sub_category",'
        '"broker_sub_category_as_of","issuer",'
        '"sector","cluster_id",'
        '"provider_sector","provider_sector_as_of","provider_sector_source",'
        '"sector_settled","isin","av_ticker","price_ticker","note_ref",'
        '"status","added"\n'
        '"IWM","iShares Russell 2000 ETF","etf","ETF","2026-10-04","iShares","","Core US","","","","","","","","",'
        '"active","2026-08-10"\n', encoding="utf-8")


def _typo_the_cluster(repo: Path) -> None:
    p = repo / "data" / "instruments.csv"
    p.write_text(p.read_text().replace('"Core US"', '"Watre"'), encoding="utf-8")


def test_a_bad_cluster_is_refused(repo: Path):
    _seed_tables(repo)
    _typo_the_cluster(repo)
    _git(repo, "add", "data")
    out = _commit(repo)
    assert out.returncode != 0
    assert "Watre" in out.stdout + out.stderr


def test_a_typo_fixed_but_not_restaged_is_still_refused(repo: Path):
    """The index is what gets committed, so the index is what must validate.

    Half the guard's foreign keys read the working tree. Stage a typo, fix the
    file in the editor, commit without re-adding — and the guard read the fixed
    copy from disk, passed, and committed the typo. The hook's own contract
    says the STAGED version must validate (`pre-commit:27-29`), and its comment
    at :76-80 describes this exact bug being fixed once already, for one check.
    Found by audit 2026-09-27.
    """
    _seed_tables(repo)
    _typo_the_cluster(repo)
    _git(repo, "add", "data")            # the typo is now in the index
    _seed_tables(repo)                   # fixed on disk only, NOT re-staged

    out = _commit(repo)
    assert out.returncode != 0, (
        "the typo is still what would be committed:\n" + out.stdout + out.stderr)
    assert "Watre" in out.stdout + out.stderr


def test_a_bad_dated_provider_sector_is_refused(repo: Path):
    """`provider_sector` is a sector label too, and spec D13 makes it the
    page's sector authority — but only his own `sector` was keyed, so until
    now a vendor label could be any words at all."""
    _seed_tables(repo)
    p = repo / "data" / "instruments.csv"
    p.write_text(p.read_text().replace(
        '"","","",""', '"Blockchain Widgets","2026-10-04","Yahoo",""'),
        encoding="utf-8")
    _git(repo, "add", "data")
    out = _commit(repo)
    assert out.returncode != 0
    assert "Blockchain Widgets" in out.stdout + out.stderr


def test_an_undated_provider_sector_is_left_alone(repo: Path):
    """The same column holds the raw vendor word on every older row —
    "FINANCIAL SERVICES" is not and never will be a sectors.csv label.
    Keying the whole column would refuse his real table."""
    _seed_tables(repo)
    p = repo / "data" / "instruments.csv"
    p.write_text(p.read_text().replace(
        '"","","",""', '"FINANCIAL SERVICES","","",""'), encoding="utf-8")
    _git(repo, "add", "data")
    assert _commit(repo).returncode == 0


# ─── The guard must not leave copies of his data behind ───

def test_a_successful_commit_leaves_no_copies_of_the_data_in_git(repo: Path):
    """Every temp file the guard makes must be gone when it returns.

    To read the STAGED copy of a table the guard writes it to `.git/_*.csv`
    and registers the path on a `scratch` list its `finally` clears. The
    evidence section opened a second list under the same name at :211,
    shadowing the first — so the outer `finally` at :324 cleaned the evidence
    section's list twice and the seven outer files never at all. Snapshots of
    positions.csv and transactions.csv accumulated in `.git/` indefinitely,
    which is the account history the repo is careful never to commit.
    Found by audit 2026-09-27; the inner list is now `ev_scratch`.
    """
    _seed_tables(repo)
    # One lot, naming the one instrument _seed_tables() defines — enough to
    # make the guard stage instruments, clusters, sectors AND positions.
    (repo / "data" / "positions.csv").write_text(
        "ticker,instrument_id,shares,cost_basis,last_price,currency,"
        "market_value,priced_at,notes\n"
        "IWM,IWM,100,290.00,196.40,USD,19640.00,2026-09-25,\n",
        encoding="utf-8")
    _git(repo, "add", "data")
    assert _commit(repo).returncode == 0
    left = sorted(p.name for p in (repo / ".git").glob("_*.csv"))
    assert left == [], f"the guard left {left} behind in .git/"


def test_the_evidence_section_also_cleans_up_after_itself(repo: Path):
    """Cover the inner list too — the outer test never enters this branch.

    `ev_scratch` (pre-commit:211-278) stages its own seven copies and is
    reached only when an evidence table is staged. Nothing in this file did
    that, so the fix for the shadowed list was covered by inspection on one
    side and by test on the other. This stages a document so both `finally`
    blocks run in the same commit.
    """
    _seed_tables(repo)
    (repo / "data" / "evidence.csv").write_text(
        '"id","kind","title","source","published","storage","location","added",'
        '"author","gist"\n'
        '"e0001","article","A thing he read","wsj","2026-09-01","link",'
        '"https://example.com/a","2026-09-01","",""\n', encoding="utf-8")
    _git(repo, "add", "data")
    out = _commit(repo)
    assert out.returncode == 0, out.stdout + out.stderr
    left = sorted(p.name for p in (repo / ".git").glob("_*.csv"))
    assert left == [], f"the guard left {left} behind in .git/"


def test_a_bad_evidence_row_is_refused(repo: Path):
    """Proof that the evidence branch is actually entered.

    The cleanup test above would pass even if the branch never ran, because
    the shadowing bug rebound the list unconditionally. This one cannot: the
    URL rule lives at `evidence.check_location()`, reached only from
    `check_locations()` at pre-commit:217, inside the branch.
    """
    _seed_tables(repo)
    (repo / "data" / "evidence.csv").write_text(
        '"id","kind","title","source","published","storage","location","added",'
        '"author","gist"\n'
        '"e0001","article","A thing he read","wsj","2026-09-01","link",'
        '"not-a-url","2026-09-01","",""\n', encoding="utf-8")
    _git(repo, "add", "data")
    out = _commit(repo)
    assert out.returncode != 0
    assert "not a URL" in out.stdout + out.stderr


# ─── Thoughts and commentary ───

def _commentary(repo: Path, body: str) -> None:
    (repo / "data" / "commentary.csv").write_text(
        '"id","date","recorded","kind","target_type","target_id","origin",'
        '"said_by","body","promoted_to","note_ref"\n' + body, encoding="utf-8")


def test_a_good_commentary_row_commits(repo: Path):
    """Also the only test that ENTERS the commentary branch at all.

    The guard referenced `commentary.` seven times while the import edit had
    silently not matched, and every test still passed — because nothing staged
    the file. Found 2026-09-27.
    """
    _seed_tables(repo)
    _commentary(repo, '"cm0001","2026-09-27","2026-09-27","thought","cluster",'
                      '"Core US","mine","","Small caps still look heavy.","",""\n')
    _git(repo, "add", "data")
    out = _commit(repo)
    assert out.returncode == 0, out.stdout + out.stderr


def test_a_thought_attributed_to_someone_else_is_refused(repo: Path):
    """The cross-check that keeps scoreboard()'s origin cut honest."""
    _seed_tables(repo)
    _commentary(repo, '"cm0001","2026-09-27","2026-09-27","thought","cluster",'
                      '"Core US","fundstrat","","Newton likes small caps.","",""\n')
    _git(repo, "add", "data")
    out = _commit(repo)
    assert out.returncode != 0
    assert "thought" in out.stdout + out.stderr


def test_commentary_about_a_cluster_that_does_not_exist_is_refused(repo: Path):
    _seed_tables(repo)
    _commentary(repo, '"cm0001","2026-09-27","2026-09-27","thought","cluster",'
                      '"Watre","mine","","A theme that is not there.","",""\n')
    _git(repo, "add", "data")
    out = _commit(repo)
    assert out.returncode != 0
    assert "Watre" in out.stdout + out.stderr


def test_a_thought_promoted_to_a_bet_that_does_not_exist_is_refused(repo: Path):
    _seed_tables(repo)
    _commentary(repo, '"cm0001","2026-09-27","2026-09-27","thought","market",'
                      '"","mine","","Breadth is improving.","c9999",""\n')
    _git(repo, "add", "data")
    out = _commit(repo)
    assert out.returncode != 0
    assert "c9999" in out.stdout + out.stderr


def test_a_comment_may_be_deleted_unlike_a_bet(repo: Path):
    """The whole reason this is not claims.csv. A row may LEAVE this store."""
    _seed_tables(repo)
    _commentary(repo, '"cm0001","2026-09-27","2026-09-27","thought","market",'
                      '"","mine","","Breadth is improving.","",""\n'
                      '"cm0002","2026-09-27","2026-09-27","thought","market",'
                      '"","mine","","Something he later regrets writing.","",""\n')
    _git(repo, "add", "data")
    assert _commit(repo).returncode == 0
    _commentary(repo, '"cm0001","2026-09-27","2026-09-27","thought","market",'
                      '"","mine","","Breadth is improving.","",""\n')
    _git(repo, "add", "data")
    out = _commit(repo)
    assert out.returncode == 0, "a comment must be deletable: " + out.stdout + out.stderr
