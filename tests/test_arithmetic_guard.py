"""`tests/_arithmetic_guard.py`'s own test — not a report, a real test.

Fix, residual review (N2). The guard enforces D2 (the page computes nothing)
over both `src/page.py` and `ops/build_page.py`, and until this file existed
nothing in `tests/` proved it could actually catch anything: the mutation
table that demonstrated the four evasions were closed (a running `+=` total,
`.agg("sum")`, `.cumsum()`, `statistics.fmean()`) lived in a review report,
not in the suite, and each real-file caller's own "self-check" either
re-walked the AST inline (proving the file still divides somewhere, never
that `arithmetic_offenders()` itself found it) or did not exist at all. If
`arithmetic_offenders()` were edited to `return []` unconditionally, every
other test in this project would stay green.

These tests feed small synthetic sources straight to `arithmetic_offenders()`
— the same entry point both real-file tests call — so a stub, or a future
exemption widened until it swallows real division, is caught here, in one
place, independent of whatever `src/page.py` and `ops/build_page.py` happen
to contain on a given day.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._arithmetic_guard import arithmetic_offenders

# ─── what the guard MUST catch ──────────────────────────────────────────────

SRC_LOOP_ACCUMULATION = '''
def total_of(rows):
    total = 0.0
    for r in rows:
        total += float(r["x"])
    return total
'''

SRC_AGGREGATION_CALL = '''
def total_of(df):
    return df["x"].agg("sum")
'''

SRC_CUMULATIVE_SUM = '''
def running_total(df):
    return df["x"].cumsum().iloc[-1]
'''

SRC_MEAN_HELPER = '''
import statistics

def average_of(xs):
    return statistics.fmean(xs)
'''

# ─── what the guard MUST tolerate ───────────────────────────────────────────

SRC_PATH_JOIN = '''
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

def notes_dir(ticker):
    return PROJECT_ROOT / "notes" / "positions" / ticker
'''

SRC_STRING_CONCATENATION = '''
def classes_for(bar):
    classes = "bar"
    if bar.negative:
        classes += " negative"
    return "row " + classes
'''

SRC_CLAMP = '''
def width_of(scaled):
    return max(0.0, min(100.0, scaled))
'''


def _offenders(tmp_path: Path, source: str) -> list[int]:
    p = tmp_path / "synthetic.py"
    p.write_text(source, encoding="utf-8")
    return arithmetic_offenders(p, exempt_function=None)


# ─── positive: each of the four named evasions must be caught ─────────────

def test_catches_accumulation_built_in_a_loop(tmp_path):
    offenders = _offenders(tmp_path, SRC_LOOP_ACCUMULATION)
    assert offenders, (
        "a running total built one `total += x` at a time inside a loop "
        "must be caught — this is the evasion a BinOp-only guard misses "
        "entirely, since no single expression here ever divides or "
        "subtracts"
    )


def test_catches_an_aggregation_call(tmp_path):
    offenders = _offenders(tmp_path, SRC_AGGREGATION_CALL)
    assert offenders, (
        '`df["x"].agg("sum")` must be caught — pandas builds a real total '
        "here without ever writing a `+` or a `/`"
    )


def test_catches_a_cumulative_sum(tmp_path):
    offenders = _offenders(tmp_path, SRC_CUMULATIVE_SUM)
    assert offenders, (
        '`df["x"].cumsum()` must be caught — a running total expressed as '
        "one pandas call rather than a loop"
    )


def test_catches_a_mean_helper(tmp_path):
    offenders = _offenders(tmp_path, SRC_MEAN_HELPER)
    assert offenders, (
        "`statistics.fmean(xs)` must be caught — an average computed by a "
        "stdlib helper, no BinOp involved at all"
    )


# ─── negative: the three legitimate shapes the guard must NOT flag ────────

def test_does_not_flag_a_path_join(tmp_path):
    offenders = _offenders(tmp_path, SRC_PATH_JOIN)
    assert not offenders, (
        f"a `pathlib.Path` join (`PROJECT_ROOT / \"notes\" / ...`) was "
        f"flagged as arithmetic at line(s) {offenders} — `/` on a Path is "
        f"never a number"
    )


def test_does_not_flag_string_concatenation(tmp_path):
    offenders = _offenders(tmp_path, SRC_STRING_CONCATENATION)
    assert not offenders, (
        f"string concatenation (`classes += \" negative\"`, `\"row \" + "
        f"classes`) was flagged as arithmetic at line(s) {offenders} — "
        f"building a sentence or a CSS class list is not computing a number"
    )


def test_does_not_flag_a_clamp(tmp_path):
    offenders = _offenders(tmp_path, SRC_CLAMP)
    assert not offenders, (
        f"`max(0.0, min(100.0, ...))` was flagged as arithmetic at "
        f"line(s) {offenders} — clamping a bar's width into [0, 100] is a "
        f"judgement call this guard already makes, same as `len`/`round`"
    )


# ─── both real-file callers go through this same entry point ──────────────

def test_both_real_files_still_divide_outside_their_one_exemption(tmp_path):
    """The other half of what a stub would hide: not only must the guard
    catch synthetic offenders, it must still catch the REAL ones in
    `src/page.py` and `ops/build_page.py` when nothing is exempted — proof
    that `_width()`/`main()`'s exemptions are narrow, one line each, rather
    than having quietly grown to cover everything. Fix, residual review
    (N2) — this replaces the inline AST re-walk `test_page.py` used to do
    (which proved `src/page.py` still divides somewhere, never that
    `arithmetic_offenders()` itself found it) and gives `ops/build_page.py`
    the self-check it never had, through the SAME entry point both files'
    own tests call — not two re-implementations that could drift apart."""
    page_offenders = arithmetic_offenders(Path("src/page.py"), exempt_function=None)
    assert page_offenders, (
        "src/page.py has no arithmetic at all when _width() is not "
        "exempted — either _width() was deleted, or the guard itself "
        "stopped firing"
    )
    build_offenders = arithmetic_offenders(Path("ops/build_page.py"),
                                           exempt_function=None)
    assert build_offenders, (
        "ops/build_page.py has no arithmetic at all when main() is not "
        "exempted — either its one CLI-timing line was deleted, or the "
        "guard itself stopped firing"
    )
