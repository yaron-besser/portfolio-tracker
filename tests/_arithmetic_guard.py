"""Shared AST guard for the page-computes-nothing rule (spec D2).

`src/page.py` and `ops/build_page.py` may order, scale, format and assemble
sentences — they may never add, divide or decide. Before this module
existed, `tests/test_page.py` carried one AST pass over `src/page.py` alone,
and `ops/build_page.py` had no such guard at all — only a prose promise
("Nothing here is fetched and nothing here is computed"). The 2026-10-06
final review proved the one guard that did exist was also narrower than it
looked: a BinOp-only check never catches a running total built one `+=` at
a time, a pandas `.agg("sum")`/`.cumsum()`, or `statistics.fmean()` — none
of those is a `Div`/`FloorDiv`/`Mult`/`Sub` BinOp, so all four walked past
it in a scratch mutation test. Fix, 2026-10-07 final review (I9): one
check, shared by both files, extended to catch all four.

**What counts as computing a number, here:**

  * `Div`/`FloorDiv`/`Mult`/`Sub` BinOps — a total, a percentage, or a
    difference built by one ordinary expression.
  * `sum`/`mean`/`fmean`/`agg`/`cumsum`, as a bare call or a method
    (`.sum()`, `df.agg("sum")`, `df["x"].cumsum()`, `statistics.fmean(xs)`)
    — the ways pandas (and the stdlib) build the same totals/averages
    without ever writing a `/` or a `-`.
  * `AugAssign` (`total += x`, `n -= 1`, `w *= 2`, ...) for `Add`/`Sub`/
    `Mult`/`Div`/`FloorDiv` — a running total built one step at a time,
    which a BinOp-only guard never sees because no single expression ever
    divides or subtracts. The ONE exemption is `+=` on a bare string
    constant (`classes += " muted"`, both real lines in
    `ops/build_page.py` today) — string concatenation, not arithmetic, and
    the same exemption plain `+` already gets as a BinOp.

`Add` as a BinOp stays unflagged, same as before this module existed:
string concatenation (`"a" + "b"`) is legitimate throughout both files'
own sentence-building, and flagging it would either break every honesty
sentence or force a false exemption into the guard. `len()`, `max()`,
`min()`, `round()` and comparisons are unflagged too — a judgement call
about where the line sits, not an oversight (`max`/`min` clamp a bar's
width in `ops/build_page.py`'s own layout code and are not in dispute).

**One more exemption this file's own code forced into the open:**
`pathlib.Path` overloads `/` for joining — `Path(__file__).resolve().parent
/ "portfolio.html"` and `PROJECT_ROOT / Path(note_ref).parent / head` are
both real lines in `ops/build_page.py`, and both parse as `ast.BinOp(op=
ast.Div)`, indistinguishable at the AST level from a genuine division
unless the operands are inspected. `_looks_like_path_join()` recognises a
`Div` as a path join (never flagged) when either side is a string literal,
a `Path(...)` call (or an attribute chain off one, like `.parent` or
`.resolve()`), or an ALL-CAPS name containing `PATH`/`ROOT` (this
codebase's own naming convention — `PROJECT_ROOT`, `OUTPUT_PATH`,
`COMPOSITIONS_PATH`, ...). `src/page.py` never joins a path this way, so
the exemption never applied there before this module existed, and a
genuine numeric division (`value / total`, two plain names) matches none
of the three signals and is still caught.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Bare-function or method names that build a total or an average without
#: ever writing a `Div`/`Mult`/`Sub` BinOp.
FLAGGED_CALLS = ("sum", "mean", "fmean", "agg", "cumsum")

#: BinOp node types that are never page-layer arithmetic exemptions the way
#: string `+` is.
FLAGGED_BINOPS = (ast.Div, ast.FloorDiv, ast.Mult, ast.Sub)

#: AugAssign ops that count as arithmetic UNLESS the one string-literal
#: exemption below applies.
_FLAGGED_AUGASSIGN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv)


def _is_string_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _looks_like_path_join(node: ast.AST) -> bool:
    """True when `node` is (or is built from) a `pathlib.Path`, never a
    number — see the module docstring's own worked example. Recurses
    through `.attr` access and nested `Div` BinOps so a long chain
    (`A / B / C / D`) is recognised from any link, not just its two
    immediate operands."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Name):
        return node.id.isupper() and ("PATH" in node.id or "ROOT" in node.id)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "Path":
            return True
        if isinstance(node.func, ast.Attribute):
            return _looks_like_path_join(node.func.value)
        return False
    if isinstance(node, ast.Attribute):
        return _looks_like_path_join(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _looks_like_path_join(node.left) or _looks_like_path_join(node.right)
    return False


def arithmetic_offenders(path: Path, exempt_function: str | None = None,
                         ) -> list[int]:
    """Line numbers in `path` where page-layer code computes a number.

    `exempt_function`, when given, is the name of the one function allowed
    to do arithmetic in that file — every line inside its body, start to
    end, is excluded. This project exempts exactly two: `_width()` in
    `src/page.py` (scaling a bar against the widest one at its own depth)
    and `main()` in `ops/build_page.py` (one CLI build-timing line,
    `elapsed = time.monotonic() - started`, printed to the console after a
    build and never reaching the rendered page —
    `tests/test_build_page.py::test_building_computes_no_arithmetic_of_its_own`
    passes `exempt_function="main"`, never `None`, for exactly this line).
    Nothing else in either file may compute anything at all.

    Fix, residual review (N2): this docstring used to claim `ops/
    build_page.py` passes `None` and that "the self-check in both callers
    of this function asserts [nothing else computes] is still true" —
    neither half was accurate (the real caller passes `"main"`, and at the
    time neither caller had a self-check capable of proving anything about
    THIS function — one re-walked the AST inline, the other had no
    self-check at all). The real self-check — that this function still
    catches real arithmetic in both files when nothing is exempted, plus
    synthetic-source tests proving it catches a loop accumulation, an
    aggregation call, a cumulative sum and a mean helper, and tolerates a
    path join, string concatenation and a clamp — lives in
    `tests/test_arithmetic_guard.py`, through this same entry point, not
    re-implemented inline by each file's own test.
    """
    text = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    exempt_ranges: list[tuple[int, int]] = []
    if exempt_function:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == exempt_function:
                exempt_ranges.append((node.lineno, node.end_lineno))

    def exempt(lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in exempt_ranges)

    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, FLAGGED_BINOPS):
            if isinstance(node.op, ast.Div) and _looks_like_path_join(node):
                continue
            if not exempt(node.lineno):
                offenders.append(node.lineno)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.op, ast.Add) and _is_string_literal(node.value):
                continue  # the one exemption — see module docstring
            if isinstance(node.op, _FLAGGED_AUGASSIGN_OPS) and not exempt(node.lineno):
                offenders.append(node.lineno)
        elif isinstance(node, ast.Call):
            if _call_name(node) in FLAGGED_CALLS and not exempt(node.lineno):
                offenders.append(node.lineno)

    return sorted(set(offenders))
