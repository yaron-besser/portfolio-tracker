"""`ops/build_page.py` — one file, opened, and only ever that one write.

`ops/` has no `__init__.py` (the repo's convention — see `tests/
test_repo_shape.py`'s own `_guard()` and `tests/test_refresh_etf_compositions.
py`), so the module under test is loaded by path, not by `import`.

Only `test_the_page_loads_no_external_resource` and
`test_the_built_file_is_not_committable` run against the real
`page.context()` (the brief gave these two full bodies, verbatim) — the real
call costs about 22 seconds, and paying it twice in one file would be the
exact mistake `tests/test_page.py::real_context`'s own docstring warns
against. Every other test here builds a small synthetic context, shaped
exactly like `page.context()`'s real output but engineered to exercise one
trap each: a negative bar, a blank benchmark, a distinct state line per
basis. `page.Figure`/`page.Bar` are used directly rather than hand-rolled
dicts, so a shape drift in either dataclass fails these tests too.
"""

from __future__ import annotations

import html
import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

from src import notes as notes_mod
from src import page

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def build_page():
    """`ops/build_page.py`, loaded once for this file."""
    spec = importlib.util.spec_from_file_location(
        "build_page", PROJECT_ROOT / "ops" / "build_page.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── a small, deliberately engineered context ──────────────────────────────
#
# Every dollar/percentage figure below carries non-".00" cents on purpose —
# page.money()/page.pct() both use ":,.2f", so a round figure like 100.00
# would put the literal substring "0.00" into the page for a reason that has
# nothing to do with the bug any of these tests is hunting for. Picking
# values that round cleanly keeps a failure meaningful.

def _bar(label, value, pct, depth=0, negative=False, muted=False, href="",
        note="", source="fake"):
    fig = page.Figure(value=value, text=page.money(value), source=source)
    return page.Bar(label=label, figure=fig, pct=pct, width=73.42,
                    depth=depth, negative=negative, muted=muted, href=href,
                    note=note)


def _fake_context() -> dict:
    bases = {}
    for basis in page.BASES_ORDER:
        tag = basis.upper()
        bases[basis] = {
            "header": {
                "nav": page.Figure(148123.45, page.money(148123.45), "book.nav()"),
                "cash": page.Figure(17890.37, page.money(17890.37),
                                    "exposure.by_sector()"),
                "biggest": [
                    _bar(f"BIGGEST-{tag}", 12345.67, 8.33, source="book.biggest()"),
                ],
                "state_line": f"STATE-LINE-{tag} — showing a share of the basis.",
            },
            "theme_total": page.Figure(148123.45, page.money(148123.45),
                                       "book.nav()"),
            # Fix, pre-push review 2026-10-07 (finding 3): the Total row's
            # percentage is now a derived `page.Figure`
            # (`clusters.tree_coverage_pct()`), never a hard-coded 100 —
            # this fixture's tree is engineered complete, so 100.0 is the
            # honest answer for IT, not an assumption the renderer makes.
            "theme_coverage_pct": page.Figure(
                100.0, page.pct(100.0), "clusters.tree_coverage_pct()"),
            "honesty": [
                f"Honesty sentence one for {tag}.",
                f"Honesty sentence two for {tag}, naming the basis.",
            ],
            "theme_bars": [
                _bar("GROUP-ONE", 50123.91, 33.84, depth=0,
                    source="clusters.tree()"),
                _bar("THEME-ONE", 30123.45, 20.34, depth=1,
                    source="clusters.tree()"),
                _bar("TICK-ONE", 10123.45, 6.83, depth=2,
                    href="notes/positions/TICKONE.md", source="clusters.tree()"),
                _bar("THEME-EMPTY", 0.37, 0.01, depth=0, muted=True,
                    source="clusters.tree()"),
                _bar("POSITIVE-ONE", 4321.67, 2.91, depth=0,
                    source="clusters.tree()"),
                _bar("SHORT-ONE", -2345.67, -1.58, depth=0, negative=True,
                    source="clusters.tree()"),
            ],
            "sector_rows": [
                {
                    "sector": f"Technology-{tag}",
                    "value": page.Figure(42311.23, page.money(42311.23),
                                         "exposure.by_sector()"),
                    "pct": page.Figure(28.57, page.pct(28.57),
                                       "exposure.by_sector()"),
                    "direct_value": page.Figure(9012.34, page.money(9012.34),
                                                "exposure.by_sector()"),
                    "estimated_pct": page.Figure(11.23, page.pct(11.23),
                                                 "exposure.by_sector()"),
                    "benchmark": page.Figure(31.47, page.pct(31.47),
                                             "exposure.benchmark_sector_comparison()"),
                    "difference": page.Figure(-2.9, page.pct(-2.9),
                                              "exposure.benchmark_sector_comparison()"),
                    "state": "both",
                    "note": "",
                },
                {
                    "sector": f"UNKNOWNSECTOR-{tag}",
                    "value": page.Figure(777.77, page.money(777.77),
                                         "exposure.by_sector()"),
                    "pct": page.Figure(0.52, page.pct(0.52),
                                       "exposure.by_sector()"),
                    "direct_value": "",
                    "estimated_pct": "",
                    "benchmark": "",
                    "difference": "",
                    "state": "",
                    "note": "",
                },
            ],
            "benchmark_company_rows": {
                "rows": [
                    {
                        "key": "mine-co", "label": f"MINECO-{tag}",
                        "issuer_group": "MINECO",
                        "mine": page.Figure(3.21, page.pct(3.21),
                                            "exposure.benchmark_companies()"),
                        "benchmark": page.Figure(2.98, page.pct(2.98),
                                                 "exposure.benchmark_companies()"),
                        "benchmark_at_most": "",
                        "difference": page.Figure(0.23, page.pct(0.23),
                                                  "exposure.benchmark_companies()"),
                        "difference_floor": "",
                        "state": "both", "is_floor": False, "note": "",
                    },
                    {
                        "key": "unknownco", "label": "UNKNOWNCO",
                        "issuer_group": "UNKNOWNCO",
                        "mine": page.Figure(1.11, page.pct(1.11),
                                            "exposure.benchmark_companies()"),
                        "benchmark": "",
                        "benchmark_at_most": "",
                        "difference": "",
                        "difference_floor": "",
                        "state": "unknown", "is_floor": False,
                        "note": "Unknown — not knowable from today's data.",
                    },
                ],
                "note": f"Comparison made under the {tag} basis.",
            },
            "company_bars": [
                _bar("TICK-TWO", 6543.21, 4.42, depth=0,
                    href="notes/positions/TICKTWO.md",
                    note="at least — only part of this fund's own holdings "
                         "file names companies",
                    source="exposure.by_company()"),
                _bar("TICK-MISSING", 222.22, 0.15, depth=0,
                    href="notes/positions/TICKMISSING.md",
                    source="exposure.by_company()"),
            ],
            "overlap_rows": [
                {
                    "cluster_a": f"CLUSTERA-{tag}", "cluster_b": f"CLUSTERB-{tag}",
                    "shared": page.Figure(5432.19, page.money(5432.19),
                                          "clusters.overlap()"),
                    "pct_of_a": page.Figure(41.23, page.pct(41.23),
                                            "clusters.overlap()"),
                    "pct_of_b": "",
                    "top_names": "TICK-ONE, TICK-TWO",
                    "is_floor": False, "note": "",
                },
            ],
            "construction": [
                {
                    "metric": "Effective number of bets",
                    "value": page.Figure(4.73, "4.73",
                                         "exposure.effective_bets()"),
                    "why": f"Why sentence for {tag} effective bets.",
                },
                {
                    "metric": "Currency: USD",
                    "value": page.Figure(91234.56, page.money(91234.56),
                                         "book.by_currency()"),
                    "why": "91.23% of this basis is quoted in USD.",
                },
                {
                    "metric": "Worst drawdown",
                    "value": "",
                    "why": "Deferred to the Performance tab (Stage 4).",
                },
            ],
            # Not basis-dependent (page.pnl_panel()'s own docstring: a
            # realised gain has no basis to be a share of) — the real
            # context() hands every basis the SAME dict; this fixture does
            # the same rather than inventing three different P&L panels
            # that `page.py` never actually produces.
            "pnl": {
                "realized": page.Figure(10652.72, page.money(10652.72),
                                        "book.realized_pnl()"),
                "unrealized": page.Figure(-321.54, page.money(-321.54),
                                          "book.unrealized_pnl()"),
                "total": page.Figure(10331.18, page.money(10331.18),
                                     "book.total_pnl()"),
                "rows": [
                    {
                        "ticker": "TICK-ONE", "name": "TICKONE",
                        "market_value": page.Figure(10123.45, page.money(10123.45),
                                                    "book.lot_cost_usd()"),
                        "cost": page.Figure(10987.65, page.money(10987.65),
                                           "book.lot_cost_usd()"),
                        "unrealized": page.Figure(-864.20, page.money(-864.20),
                                                  "book.lot_cost_usd()"),
                        "flagged": False,
                    },
                    {
                        "ticker": "TICK-FX", "name": "TICKFX",
                        "market_value": page.Figure(555.55, page.money(555.55),
                                                    "book.lot_cost_usd()"),
                        "cost": page.Figure(500.00, page.money(500.00),
                                           "book.lot_cost_usd()"),
                        "unrealized": page.Figure(55.55, page.money(55.55),
                                                  "book.lot_cost_usd()"),
                        "flagged": True,
                    },
                ],
                "uncosted": [
                    {"ticker": "TICK-NOCOST", "name": "TICKNOCOST",
                     "reason": "TICKNOCOST: IBKR reports no cost basis for this lot"},
                ],
            },
        }
    return {
        "bases": bases,
        "built_at": "2026-10-07T12:00:00",
        "price_age_days": 2,
        # 2026-10-06 brief: the note pop-up. One real note with a table and
        # a link to another embedded note (TICKONE -> TICKTWO, the same
        # shape ASML -> AI Infrastructure takes in the real book); one more
        # embedded note it links to; one ref that is embedded (a bar links
        # to it — TICK-MISSING, above) but does not resolve to a real file.
        "notes": {
            "notes/positions/TICKONE.md": notes_mod.NoteText(
                ref="notes/positions/TICKONE.md", ok=True,
                title="TICKONE — a real note",
                text=(
                    "# TICKONE — a real note\n\n"
                    "The thesis text for TICKONE, with a "
                    "[link to TICKTWO](TICKTWO.md) inside it.\n\n"
                    "## A table\n\n"
                    "| Col A | Col B |\n|---|---|\n| 1 | 2 |\n"
                ),
            ),
            "notes/positions/TICKTWO.md": notes_mod.NoteText(
                ref="notes/positions/TICKTWO.md", ok=True,
                title="TICKTWO",
                text="# TICKTWO\n\nNothing special about TICKTWO.\n",
            ),
            "notes/positions/TICKMISSING.md": notes_mod.NoteText(
                ref="notes/positions/TICKMISSING.md", ok=False,
                title="", text="",
            ),
        },
    }


def _all_figures(ctx):
    """Every `page.Figure` reachable anywhere in `ctx`, walked generically —
    a dict, a list, or a `page.Bar`'s own `.figure`."""
    found: list[page.Figure] = []

    def walk(x):
        if isinstance(x, page.Figure):
            found.append(x)
        elif isinstance(x, page.Bar):
            walk(x.figure)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(ctx)
    return found


# ─── D1 and R5: no external reference of any kind ──────────────────────

#: Every pattern below is something the BROWSER would load on its own, with
#: no click from him, the moment the page opens — a CDN script, a remote
#: stylesheet, a background image fetched via CSS, an `@import`, a fetch/XHR
#: call a script makes behind his back. Any one of those can 404 or be
#: blocked and leave him staring at a broken or blank page (D1/R5) — that is
#: what this list exists to catch.
#:
#: What it must NEVER catch: a URL quoted inside his own note, rendered as a
#: link he might click. That is content, not a load — clicking it is his
#: choice, it happens after the page has already rendered in full, and it
#: cannot make the page blank. An earlier version of this check banned the
#: bare substring `http://`/`https://` anywhere in the document, which this
#: list deliberately does not do — that version forced the renderer to
#: HTML-escape a real URL he quoted in GRNY.md just to dodge this check,
#: which misrepresented what the document contains. See
#: `test_a_quoted_link_in_a_note_is_allowed` and
#: `test_an_external_stylesheet_or_script_is_not_allowed` below, which pin
#: that exact distinction.
_EXTERNAL_RESOURCE_LOADS = (
    "<script src=", '<link rel="stylesheet"', "@import",
    "url(http", "url('http", 'url("http',
    '<img src="http', "<img src='http",
    "<iframe", "fetch(", "XMLHttpRequest",
)


def _external_resource_loads(html_text: str) -> list[str]:
    """Which of `_EXTERNAL_RESOURCE_LOADS`' patterns appear in `html_text`,
    if any — shared by the real page-wide test below and the two tests
    that pin what this does and does not forbid."""
    return [p for p in _EXTERNAL_RESOURCE_LOADS if p in html_text]


def test_the_page_loads_no_external_resource(build_page):
    """D1 and R5: reliability over speed. A CDN script, a web font, a
    remote stylesheet or image, an `@import`, a `fetch`/`XMLHttpRequest`
    call — anything the page would load ON ITS OWN, with no click from
    him — must not be able to 404 or be blocked and leave him staring at
    a broken page.

    This is deliberately narrower than an earlier version of this test,
    which banned the bare substring `http://`/`https://` anywhere in the
    document — broad enough to also forbid a URL quoted inside his own
    note and rendered as a link he might click. That is content, not a
    load: he clicks it after the page has already rendered, and it
    cannot empty the page. Narrowed here to what D1/R5 actually means —
    see `_EXTERNAL_RESOURCE_LOADS`'s own comment."""
    html_text = build_page.render(page.context())
    found = _external_resource_loads(html_text)
    assert not found, f"page loads an external resource on its own: {found}"


def test_a_quoted_link_in_a_note_is_allowed(build_page):
    """Pins one half of the distinction `_EXTERNAL_RESOURCE_LOADS` draws: a
    URL he quoted in his own words, inside a note, is content and must
    reach the document untouched — scheme and all, never escaped, still
    a real, clickable link — and must never be flagged as an external
    resource load."""
    ctx = _fake_context()
    ctx["notes"]["notes/positions/TICKONE.md"] = notes_mod.NoteText(
        ref="notes/positions/TICKONE.md", ok=True, title="TICKONE",
        text=(
            "# TICKONE\n\n"
            "Read the strategy here: <https://example.com/report>.\n"
        ),
    )

    html_text = build_page.render(ctx)

    assert '<a href="https://example.com/report">' in html_text, (
        "his own quoted link must reach the document exactly as written, "
        "not escaped or mangled"
    )
    assert not _external_resource_loads(html_text)


def test_an_external_stylesheet_or_script_is_not_allowed():
    """The other half: a resource the PAGE loads on its own — here, an
    external stylesheet tag, the exact shape a 404able CDN reference would
    take — must be caught. Verified by mutation (see the note-overlay
    report) that removing a pattern from `_EXTERNAL_RESOURCE_LOADS` turns
    this red; this test only pins that the list, as written, catches it."""
    poisoned = '<link rel="stylesheet" href="https://evil.example/x.css">'
    found = _external_resource_loads(poisoned)
    assert found == ['<link rel="stylesheet"']


# ─── D2, extended here: this file computes nothing either ───────────────

def test_building_computes_no_arithmetic_of_its_own():
    """The D2 guard, same rule as `tests/test_page.py::
    test_shaping_computes_no_arithmetic_of_its_own`, now pointed at THIS
    file too.

    🚨 **Fix, 2026-10-07 final review (I9).** `src/page.py` has had an AST
    arithmetic guard since fix round 1; `ops/build_page.py` never did — only
    a prose promise in its own module docstring ("Nothing here is fetched
    and nothing here is computed"). The review named this file as exactly
    where a future session would reach for a number nobody computes, since
    the write guard (`test_nothing_in_the_page_layer_opens_a_file_for_
    writing`'s own sibling checks) had already been extended here while the
    arithmetic guard had not.

    `max(0.0, min(100.0, bar.width))` and `min(bar.depth, 3)` are clamps,
    not arithmetic the guard flags, the same judgement call `max`/`min`/
    `len`/`round` already get in the shared module's own docstring.

    `exempt_function="main"`: `main()`'s one `elapsed = time.monotonic() -
    started` is CLI build-timing for the console message printed after a
    build (and the `venv/bin/python ops/build_page.py` wall-time this
    project's own `CLAUDE.md` asks for) — never a number that reaches the
    rendered page, so it is not the thing D2 exists to stop. Nothing else
    in `main()` computes anything; this is the one line the exemption
    actually covers.

    🚨 **Fix, residual review (N2).** This test had no self-check at all —
    if `arithmetic_offenders()` were edited to always return `[]`, the
    assertion below would stay green forever. `tests/test_arithmetic_guard.
    py` now carries that self-check (through this exact entry point, with
    `exempt_function=None`, confirming `ops/build_page.py` still divides
    outside `main()`), alongside synthetic-source tests proving the guard
    catches a loop accumulation, an aggregation call, a cumulative sum and
    a mean helper, and tolerates a path join, string concatenation and a
    clamp.
    """
    from pathlib import Path

    from tests._arithmetic_guard import arithmetic_offenders

    offenders = arithmetic_offenders(Path("ops/build_page.py"),
                                    exempt_function="main")
    assert not offenders, (
        f"ops/build_page.py computes at line(s) {offenders} — a total, "
        f"difference or average invented in the renderer is a second "
        f"definition of a number a src/ module already owns (D2); put it "
        f"there instead. Nothing in this file is allowed to compute "
        f"anything."
    )


# ─── I6: the sector table's "Est. share" and ambiguous zero ────────────

def test_the_estimated_share_column_says_what_it_measures(build_page):
    """Fix, 2026-10-07 final review (I6). The column header used to read
    'Est. share', which reads as 'how much of this is estimated' — it
    actually means the narrower 'sourced from a composition row whose
    source_quality is literally estimate', and no held fund carries that
    value today, so the column was 0.00% on every real row. Renamed to
    what it means; the old header must be gone."""
    rows = [{
        "sector": "Materials",
        "value": page.Figure(13527.83, page.money(13527.83), "exposure.by_sector()"),
        "pct": page.Figure(9.0, page.pct(9.0), "exposure.by_sector()"),
        "direct_value": page.Figure(0.0, page.money(0.0), "exposure.by_sector()"),
        "estimated_pct": page.Figure(0.0, page.pct(0.0), "exposure.by_sector()"),
        "benchmark": "", "difference": "", "state": "", "note": "",
    }]
    html_text = build_page._sector_table(rows)
    assert "Est. share" not in html_text
    assert "estimated breakdown" in html_text.lower()


def test_a_zero_direct_value_beside_a_real_total_says_its_all_look_through(
        build_page):
    """Fix I6, second half. `Direct $0.00` next to a five-figure
    look-through total (Materials, live on the real book) used to read
    identically to a sector nobody has any exposure to at all — a
    non-developer reading the panel cold would conclude the split was
    unmeasured rather than entirely indirect. The caption only applies
    when the sector's own total is a real, nonzero value; a sector he has
    NO exposure to at all (I3's new rows) must not get it tacked onto a
    correctly blank zero."""
    rows = [
        {
            "sector": "Materials",
            "value": page.Figure(13527.83, page.money(13527.83), "exposure.by_sector()"),
            "pct": page.Figure(9.0, page.pct(9.0), "exposure.by_sector()"),
            "direct_value": page.Figure(0.0, page.money(0.0), "exposure.by_sector()"),
            "estimated_pct": page.Figure(0.0, page.pct(0.0), "exposure.by_sector()"),
            "benchmark": "", "difference": "", "state": "", "note": "",
        },
        {
            "sector": "Financials",
            "value": page.Figure(0.0, page.money(0.0), "exposure.by_sector()"),
            "pct": page.Figure(0.0, page.pct(0.0), "exposure.by_sector()"),
            "direct_value": page.Figure(0.0, page.money(0.0), "exposure.by_sector()"),
            "estimated_pct": page.Figure(0.0, page.pct(0.0), "exposure.by_sector()"),
            "benchmark": page.Figure(1.7, page.pct(1.7),
                                    "exposure.benchmark_sector_comparison()"),
            "difference": "", "state": "only-index", "note": "",
        },
    ]
    html_text = build_page._sector_table(rows)
    materials_row = html_text[html_text.index(">Materials<"):html_text.index(">Financials<")]
    financials_row = html_text[html_text.index(">Financials<"):]

    assert "(all look-through)" in materials_row
    assert "(all look-through)" not in financials_row


def test_the_sector_notes_column_carries_only_mines_caption(build_page):
    """Fix I8. `page.sector_rows()`'s own `note` — blank except on an
    `only-mine` row — must reach the markup, not be silently dropped by
    the renderer the way the whole `only-index` row itself used to be
    (I3)."""
    rows = [{
        "sector": "Crypto / Digital Assets",
        "value": page.Figure(500.0, page.money(500.0), "exposure.by_sector()"),
        "pct": page.Figure(0.33, page.pct(0.33), "exposure.by_sector()"),
        "direct_value": page.Figure(500.0, page.money(500.0), "exposure.by_sector()"),
        "estimated_pct": page.Figure(0.0, page.pct(0.0), "exposure.by_sector()"),
        "benchmark": "", "difference": "", "state": "only-mine",
        "note": "the index cannot hold this sector at all — SPY is an "
                "all-equity fund — so zero here is certain, not a gap",
    }]
    html_text = build_page._sector_table(rows)
    assert "the index cannot hold this sector at all" in html_text


# ─── D6: three settings, one file, no server to ask ─────────────────────

def test_all_three_bases_are_in_the_one_file(build_page):
    """The control switches a view. There is no server to ask."""
    ctx = _fake_context()
    html_text = build_page.render(ctx)

    assert html_text.count('class="basis-block"') == 3
    for basis in page.BASES_ORDER:
        assert f'data-basis="{basis}"' in html_text
        marker = f"STATE-LINE-{basis.upper()}"
        assert marker in html_text, (
            f"the {basis} view's own state line never reached the page — "
            f"a renderer that reused one basis's block for all three would "
            f"still look plausible without this check"
        )


# ─── D12, rendered: every figure's source is on hover ───────────────────

def test_every_figure_has_its_source_on_hover(build_page):
    """D12, rendered. A source that reaches page.py and not the HTML is a
    promise the screen does not keep."""
    ctx = _fake_context()
    html_text = build_page.render(ctx)
    figures = _all_figures(ctx)
    assert figures, "fixture produced no figures to check — proves nothing"

    missing = [
        f for f in figures
        if f'title="{html.escape(f.source, quote=True)}"' not in html_text
    ]
    assert not missing, (
        "figures with no title attribute naming their source reached the "
        f"page: {[(f.text, f.source) for f in missing]}"
    )


# ─── D8: a negative bar draws on the negative side ──────────────────────

def _own_row(html_text: str, label: str) -> str:
    """The HTML of just the one leaf row naming `label` — bounded by its
    own `<div class="leafrow...">...</div>`, not a fixed character window
    that could bleed into an adjacent row's markup (two bars sit right next
    to each other in the fixture on purpose, to make that bleed-over the
    first thing this would catch)."""
    idx = html_text.index(label)
    start = html_text.rindex('<div class="leafrow', 0, idx)
    end = html_text.index("</div>", idx) + len("</div>")
    return html_text[start:end]


def test_a_negative_bar_renders_on_the_negative_side(build_page):
    ctx = _fake_context()
    html_text = build_page.render(ctx)

    neg_row = _own_row(html_text, "SHORT-ONE")
    assert "fill neg" in neg_row, (
        "SHORT-ONE is a negative bar and did not render with the "
        "negative-side fill class (D8)"
    )

    pos_row = _own_row(html_text, "POSITIVE-ONE")
    assert "fill neg" not in pos_row, (
        "POSITIVE-ONE is not negative and rendered with the negative-fill "
        "class — the one accent is reserved for the negative side only"
    )

    assert "taxonomy" in html_text.lower() and "open question" in html_text.lower(), (
        "the page must say the instrument taxonomy for shorts, futures and "
        "options is still open, rather than imply the labelling is settled"
    )


# ─── trap 10: a disclosure arrow only where there is something to open ─────

def _own_summary(html_text: str, label: str) -> str:
    """The HTML of just the one `<summary>...</summary>` naming `label` —
    the branch-row equivalent of `_own_row()` above, since a branch's
    content lives inside `<summary>`, never inside a `leafrow` `<div>`."""
    idx = html_text.index(label)
    start = html_text.rindex('<summary>', 0, idx)
    end = html_text.index('</summary>', idx) + len('</summary>')
    return html_text[start:end]


def test_disclosure_arrow_only_on_rows_that_actually_expand(build_page):
    """He looked at the built page and said he wanted "those little arrows
    ... which shows me that I can extend a theme". `.warnline` already had
    one; the theme tree had none. Every row — branch or leaf — reserves the
    same disclosure slot so a leaf's label still lines up with an
    expandable sibling, but only a row that genuinely nests children (GROUP
    -ONE, which THEME-ONE nests under in the fixture) may carry the glyph
    class; a true leaf (TICK-ONE) must reserve the slot and stay empty."""
    html_text = build_page.render(_fake_context())

    branch_row = _own_summary(html_text, "GROUP-ONE")
    assert 'class="disclosure has-children"' in branch_row, (
        "GROUP-ONE has a child (THEME-ONE nests under it) and must show "
        "the disclosure glyph — otherwise nothing on screen says it can "
        "be opened"
    )

    leaf_row = _own_row(html_text, "TICK-ONE")
    assert 'class="disclosure has-children"' not in leaf_row, (
        "TICK-ONE is a leaf with no children and must not carry the glyph "
        "class — that would promise an expansion that never happens, the "
        "same class of defect as rendering a blank figure as a zero"
    )
    assert 'class="disclosure"' in leaf_row, (
        "TICK-ONE must still reserve the same disclosure slot its "
        "expandable siblings use, or the ruled columns go ragged"
    )


# ─── trap 3: a blank benchmark is a sentence, never a zero ──────────────

def test_a_missing_benchmark_renders_the_sentence_and_not_a_zero(build_page):
    ctx = _fake_context()
    html_text = build_page.render(ctx)

    idx = html_text.index("UNKNOWNCO")
    window = html_text[idx:idx + 700]

    assert "Unknown" in window and "not knowable from today" in window, (
        "the module's own explanation for a missing benchmark did not "
        "reach the page next to the row it explains"
    )
    assert "0.00%" not in window, (
        "a blank benchmark figure rendered as a literal 0.00%% — exactly "
        "the false measurement trap 3 forbids"
    )
    assert "&mdash;" in window, (
        "no dash marker found where the blank benchmark figure should be"
    )


# ─── D11: machine output, never committed ───────────────────────────────

def test_the_built_file_is_not_committable():
    """D11: machine output, regenerated in seconds, and it would change on
    every sync. ops/ per CLAUDE.md, and ignored."""
    out = subprocess.run(["git", "check-ignore", "-q", "ops/portfolio.html"],
                         capture_output=True)
    assert out.returncode == 0, "ops/portfolio.html is not git-ignored"


# ─── the other half of the write guard: nowhere but the built file ──────

def test_building_writes_only_the_built_file(tmp_path, monkeypatch, build_page):
    """`conftest.py` already refuses a test that writes under data/. This is
    the other half: the builder must not write anywhere else either —
    checked dynamically here, not just by reading the source (that half
    lives in `tests/test_page.py`)."""
    import pathlib

    fake_ctx = _fake_context()
    monkeypatch.setattr(page, "context", lambda **kwargs: fake_ctx)
    out_path = tmp_path / "portfolio.html"
    monkeypatch.setattr(build_page, "OUTPUT_PATH", out_path)
    monkeypatch.setattr(
        build_page.webbrowser, "open",
        lambda *a, **k: pytest.fail("--no-open must never open a browser"),
    )

    written: list[pathlib.Path] = []
    real_write_text = pathlib.Path.write_text

    def tracking_write_text(self, *a, **kw):
        written.append(self)
        return real_write_text(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", tracking_write_text)

    rc = build_page.main(["--no-open"])

    assert rc == 0
    assert written == [out_path], (
        f"expected exactly one write, to {out_path}; got {written}"
    )
    assert out_path.read_text(encoding="utf-8")


# ─── 2026-10-07 redesign: D3's four tabs, three of them unbuilt ────────────

def test_all_four_tabs_present_and_three_carry_their_note(build_page):
    """D3 names four tabs. Only Spread is built; the other three must still
    appear, be clickable, and say what they will hold and which stage
    builds them — `_COMING_TABS`'s own words, never invented here."""
    html_text = build_page.render(_fake_context())

    for tab in build_page._TABS:
        assert f'value="{tab}"' in html_text, f"tab {tab!r} has no control in the page"
        assert f'>{build_page._TAB_LABELS[tab]}</label>' in html_text

    assert html_text.count('data-tab="spread"') == 1
    for tab in ("holdings", "record", "performance"):
        heading, note, stage = build_page._COMING_TABS[tab]
        assert f'data-tab="{tab}"' in html_text
        assert heading in html_text, f"{tab}'s own heading missing"
        assert html.escape(note, quote=True) in html_text, f"{tab}'s own note missing"
        assert stage in html_text, f"{tab}'s own stage label missing"


# ─── trap 7: the side list is derived from what the context provides ───────

def test_side_nav_has_an_entry_for_every_panel_the_context_provides(build_page):
    """The sticky side list must walk what `page.context()` actually
    produced, not a fixed list of strings typed into the renderer.
    Proved by mutation: one panel key is deleted from the fixture below,
    and both its content section AND its side-nav link must vanish
    together — a hard-coded list of labels would still print the
    link for a panel that no longer has any content behind it."""
    ctx = _fake_context()
    basis = page.BASES_ORDER[0]
    del ctx["bases"][basis]["overlap_rows"]

    html_text = build_page.render(ctx)

    assert f'id="{basis}-overlap_rows"' not in html_text, (
        "the removed panel's own section is still in the main column"
    )
    assert f'href="#{basis}-overlap_rows"' not in html_text, (
        "the removed panel still has a side-nav link pointing at nothing — "
        "proof the nav was not actually derived from what data supplied"
    )

    for b in page.BASES_ORDER:
        data = ctx["bases"][b]
        for key, _, _ in build_page._PANELS:
            if key not in data:
                continue
            assert f'id="{b}-{key}"' in html_text, (
                f"{b}/{key} has content but no anchor for the side nav to reach"
            )
            assert f'href="#{b}-{key}"' in html_text, (
                f"{b}/{key} has content but no side-nav link to it"
            )
    assert html_text.count('class="side"') == len(page.BASES_ORDER), (
        "expected exactly one sticky side list per basis block"
    )


# ─── trap 4: one grey warning line per panel, never an invented number ─────

def test_warning_line_above_each_panel_and_the_full_caveat_list_is_present(build_page):
    """Every panel, for every basis, gets its own collapsed warning
    line, and the number in it is nothing but `len(data["honesty"])`
    — never hand-counted, and the panel count itself is `len(build_page.
    _PANELS)`, never retyped as a literal either. The Trust section
    holds the same sentences again, in full, already open."""
    ctx = _fake_context()
    html_text = build_page.render(ctx)

    n_panels = len(build_page._PANELS)
    n_bases = len(page.BASES_ORDER)
    assert html_text.count('class="warnline"') == n_panels * n_bases, (
        "expected exactly one warning line above every panel, for every basis"
    )

    # Every basis's own warning line must show nothing but len(honesty) for
    # THAT basis — tallied by count value, since the fixture happens to
    # give every basis the same length today; this still catches a wrong
    # or stale number for any basis without assuming the counts differ.
    from collections import Counter
    tally = Counter(len(ctx["bases"][b]["honesty"]) for b in page.BASES_ORDER)
    for count, n_bases_with_count in tally.items():
        noun = "thing" if count == 1 else "things"
        expected = f"{count} {noun} worth knowing about these numbers"
        assert html_text.count(expected) == n_panels * n_bases_with_count, (
            f"expected {count} (taken from len(honesty)) to appear in the "
            f"warning line above all {n_panels} panels for each of the "
            f"{n_bases_with_count} basis/bases with that many honesty "
            f"sentences — found a different count, meaning a hand count "
            f"or a stale one"
        )

    for basis in page.BASES_ORDER:
        for line in ctx["bases"][basis]["honesty"]:
            assert line in html_text, (
                f"honesty sentence {line!r} for {basis} never reached the "
                "page — the Trust section must carry the full list verbatim"
            )
        assert f'id="{basis}-trust"' in html_text


# ─── trap 9: exactly one deliberate type scale ──────────────────────────────

def test_stylesheet_declares_one_small_named_type_scale(build_page):
    """The old page had seven font sizes between 9px and 13px — nothing to
    anchor on. The approved redesign replaces that with exactly four named
    sizes (NAV / panel heading / row / small label). This checks the
    stylesheet text directly: the only literal pixel sizes may be the four
    `--fs-*` custom properties in `:root`, and every actual `font-size`
    declaration anywhere in the sheet must reference one of those four
    variables — never a raw number that could quietly become a fifth
    size."""
    css = build_page._CSS

    names = set(re.findall(r'(--fs-[\w-]+)\s*:\s*[0-9.]+px', css))
    assert names == {"--fs-hero", "--fs-heading", "--fs-row", "--fs-label"}, (
        f"expected exactly the four named sizes the approved design chose, "
        f"got {sorted(names)}"
    )

    uses = re.findall(r'font-size\s*:\s*([^;}]+)[;}]', css)
    assert uses, "fixture found no font-size declarations at all — proves nothing"
    bad = [v.strip() for v in uses if not re.fullmatch(r'var\(--fs-[\w-]+\)', v.strip())]
    assert not bad, (
        f"found font-size declared as something other than one of the four "
        f"named scale variables: {bad} — this is exactly how the page grew "
        f"seven near-identical sizes the first time"
    )


# ─── 2026-10-06 brief: the note pop-up, in-page, not a new page ────────────

def test_note_content_present_for_a_bar_with_one_absent_for_a_bar_without(build_page):
    """TICK-ONE carries an href into a real embedded note; POSITIVE-ONE
    carries none at all. The note's own text must reach the document for
    the bar that has one, and no note-link machinery may attach to the bar
    that has none — proof the overlay embeds exactly what a bar actually
    links to, never more, never a blank box for a bar with nothing to
    show."""
    html_text = build_page.render(_fake_context())

    assert "The thesis text for TICKONE" in html_text
    assert 'data-ref="notes/positions/TICKONE.md"' in html_text
    tick_one_row = _own_row(html_text, "TICK-ONE")
    assert 'data-note-ref="notes/positions/TICKONE.md"' in tick_one_row

    positive_one_row = _own_row(html_text, "POSITIVE-ONE")
    assert "data-note-ref" not in positive_one_row
    assert "notelink" not in positive_one_row


def test_a_table_inside_a_note_renders_as_a_table(build_page):
    """His notes carry 436 table rows across 21 files — `markdown-it-py`
    must actually be configured with tables enabled, not merely imported."""
    html_text = build_page.render(_fake_context())
    idx = html_text.index('data-ref="notes/positions/TICKONE.md"')
    fragment = html_text[idx:idx + 2000]

    assert "<table" in fragment
    assert "<th>Col A</th>" in fragment and "<th>Col B</th>" in fragment
    assert "<td>1</td>" in fragment and "<td>2</td>" in fragment


def test_overlay_markup_has_the_three_closing_routes_and_focus_handling(build_page):
    """Close button, Escape key, backdrop click — all three — plus focus
    moving into the overlay on open and back to the opening bar on close.
    His own words: a lost focus point is exactly the complaint this
    redesign is fixing."""
    html_text = build_page.render(_fake_context())

    assert 'id="note-close"' in html_text and 'aria-label="Close"' in html_text
    assert 'id="note-backdrop"' in html_text
    assert 'id="note-dialog"' in html_text and 'tabindex="-1"' in html_text
    assert 'role="dialog"' in html_text and 'aria-modal="true"' in html_text

    js = build_page._JS
    assert "closeBtn.addEventListener('click', closeNote)" in js, (
        "the close button must close the overlay"
    )
    assert "backdrop.addEventListener('click', closeNote)" in js, (
        "clicking the backdrop must close the overlay"
    )
    assert "e.key === 'Escape'" in js, "Escape must close the overlay"
    assert "closeBtn.focus()" in js, (
        "opening the overlay must move focus into it"
    )
    assert "lastOpener.focus()" in js, (
        "closing the overlay must return focus to the bar that opened it"
    )


def test_link_between_two_embedded_notes_opens_the_overlay_not_a_navigation(build_page):
    """TICKONE's own text links to TICKTWO, which this page also embeds —
    the SAME shape a real position note takes linking to its cluster note
    (ASML -> AI Infrastructure). That link must resolve to the overlay
    (`data-note-ref`), never a plain navigable `href` pointing at the raw
    file."""
    html_text = build_page.render(_fake_context())
    idx = html_text.index('data-ref="notes/positions/TICKONE.md"')
    fragment = html_text[idx:idx + 2000]

    assert 'data-note-ref="notes/positions/TICKTWO.md"' in fragment
    assert 'href="TICKTWO.md"' not in fragment
    assert 'href="../notes/positions/TICKTWO.md"' not in fragment
    assert 'class="notelink"' in fragment


def test_a_referenced_but_missing_note_shows_the_plain_language_message(build_page):
    """TICK-MISSING links to a ref `page.embedded_notes()` could not read —
    `src/notes.py` reports `ok=False` for it, exactly what a stale
    `note_ref` (`instruments.stale_refs()`'s own report) looks like. The
    overlay must say so, by path, in plain words — never crash the build,
    never open on an empty box."""
    html_text = build_page.render(_fake_context())

    missing_row = _own_row(html_text, "TICK-MISSING")
    assert 'data-note-ref="notes/positions/TICKMISSING.md"' in missing_row

    idx = html_text.index('data-ref="notes/positions/TICKMISSING.md"')
    fragment = html_text[idx:idx + 400]
    assert "notes/positions/TICKMISSING.md" in fragment, (
        "the missing note's own path must be named, in his language"
    )
    assert 'class="notemissing"' in fragment
    assert '</div><div class="note-source"' not in fragment[:200] and (
        '<p class="notemissing">' in fragment
    ), "a missing note must say something — never an empty box"


# ─── 2026-10-07 addition: the By-theme panel's own total ───────────────────

def test_theme_panel_has_one_underlined_total_row_reading_100_percent(build_page):
    """Spec: a total row, underlined in the statement style already
    established, reading 100% of the selected basis — because this
    fixture's own tree is engineered complete (`theme_coverage_pct` is
    given as 100.0), never because the renderer assumes it (fix, pre-push
    review 2026-10-07, finding 3 — see the mutation test right below, which
    proves a derived figure actually drives this text)."""
    html_text = build_page.render(_fake_context())

    assert html_text.count('class="themetotal"') == len(page.BASES_ORDER), (
        "expected exactly one total row per basis, in the By-theme panel "
        "alone"
    )
    assert "border-top" in build_page._CSS.split(".themetotal{")[1][:200], (
        "the total row must be underlined in the statement style already "
        "used for a panel heading's own rule"
    )
    assert html_text.count(page.pct(100.0)) >= len(page.BASES_ORDER)


def test_theme_total_pct_is_derived_never_a_hard_coded_100(build_page):
    """Fix, pre-push review 2026-10-07 (finding 3). `ops/build_page.py`
    used to hard-code `page.pct(100.0)` for the Total row's percentage —
    the one row that could have revealed a cluster-tree defect letting real
    money fall out of every row, structurally incapable of doing so.
    Proved by mutation: this fixture's `theme_coverage_pct` is set to a
    value that is NOT 100 — a hard-coded renderer would still print 100%
    regardless; the fixed renderer must print the given figure instead."""
    ctx = _fake_context()
    basis = page.BASES_ORDER[0]
    given = page.Figure(64.29, page.pct(64.29), "clusters.tree_coverage_pct()")
    ctx["bases"][basis]["theme_coverage_pct"] = given

    html_text = build_page.render(ctx)

    assert page.pct(64.29) in html_text, (
        "the derived coverage percentage never reached the page"
    )
    # Only THIS basis was mutated — the others still legitimately read 100%,
    # so this only pins that the renderer did not just print a constant for
    # every basis regardless of what it was given.
    assert html_text.count(page.pct(100.0)) >= len(page.BASES_ORDER) - 1


def test_theme_total_renders_the_given_figure_never_a_sum_of_the_bars(build_page):
    """The renderer may not sum anything (CLAUDE.md) — proved by mutation:
    the fixture's own `theme_bars` do NOT sum to its `theme_total` (they
    never should; a group's row already carries its children's money, so
    adding every row double-counts). The rendered total must show the
    GIVEN figure and nothing else — never a recomputed sum of the rows
    above it, which would silently disagree with `book.nav()` the moment a
    renderer tried it."""
    ctx = _fake_context()
    basis = page.BASES_ORDER[0]
    bars = ctx["bases"][basis]["theme_bars"]
    bars_sum = sum(b.figure.value for b in bars)
    given_value = 999_111.47
    assert page.money(given_value) != page.money(bars_sum), (
        "fixture sanity check — the given total must differ from the "
        "bars' own sum, or this test proves nothing"
    )
    ctx["bases"][basis]["theme_total"] = page.Figure(
        given_value, page.money(given_value), "book.nav()")

    html_text = build_page.render(ctx)

    assert page.money(given_value) in html_text
    assert page.money(bars_sum) not in html_text


def test_no_total_row_on_any_other_panel(build_page):
    """Spec: no total on the other panels — sector differences do not
    meaningfully total, the company panel's total would restate the
    basis, overlapping themes are not a partition, and realised/unrealised
    P&L already carry their own two totals in the panel's own header, never
    a `.themetotal` row. Only the By-theme panel may ever render it."""
    html_text = build_page.render(_fake_context())
    assert html_text.count('class="themetotal"') == len(page.BASES_ORDER)


# ─── D7: P&L is present but quiet — grey, never coloured, never sortable ──

def test_pnl_panel_is_present_for_every_basis(build_page):
    """The P&L panel walks `_PANELS` exactly like every other panel — one
    section and one side-nav link per basis, even though its own content
    (`page.pnl_panel()`'s docstring: no basis to be a share of) never
    differs between them."""
    html_text = build_page.render(_fake_context())
    for basis in page.BASES_ORDER:
        assert f'id="{basis}-pnl"' in html_text
        assert f'href="#{basis}-pnl"' in html_text
    assert html_text.count('Realised + unrealised') == len(page.BASES_ORDER)


def test_pnl_figures_render_in_the_same_ink_regardless_of_sign(build_page):
    """D7/D9: grey, never coloured. The fixture's own `unrealized` total is
    negative and TICK-ONE's own row is negative too — proving a negative
    figure gets no colour class is only meaningful because this fixture
    actually contains one. `--neg` is the one CSS colour this page uses for
    a signed figure (a short's bar fill, D8); P&L must never reach for it."""
    html_text = build_page.render(_fake_context())
    idx = html_text.index('id="everything-pnl"')
    section = html_text[idx:html_text.index('</section>', idx)]

    assert page.money(-321.54) in section  # the negative unrealised total
    assert page.money(-864.20) in section  # TICK-ONE's own negative row
    assert "var(--neg)" not in section
    classes = " ".join(re.findall(r'class="([^"]*)"', section))
    assert "neg" not in classes.split(), (
        "a P&L cell must never carry the short's negative-fill class"
    )


def test_pnl_table_carries_no_sort_affordance(build_page):
    """D7: never sortable. This file wires sorting nowhere at all — proved
    here on the one table a feature like that would most tempt someone to
    add to next."""
    html_text = build_page.render(_fake_context())
    idx = html_text.index('id="everything-pnl"')
    section = html_text[idx:html_text.index('</section>', idx)]
    assert "<script" not in section
    assert "sort" not in section.lower()
    assert "onclick" not in section.lower()


def test_pnl_flagged_lot_gets_a_marker_not_a_sentence_of_its_own(build_page):
    """TICK-FX is `flagged=True` in the fixture; the full explanation lives
    in the honesty block (one sentence per flagged lot, see `page.honesty()`
    and `test_the_fx_fallback_sentence...` in tests/test_page.py) — this
    table only needs to point at it."""
    html_text = build_page.render(_fake_context())
    idx = html_text.index('id="everything-pnl"')
    section = html_text[idx:html_text.index('</section>', idx)]
    assert "TICKFX" in section
    assert "&deg;" in section


def test_pnl_uncosted_lot_is_named_with_no_figure_at_all(build_page):
    """CLAUDE.md: a lot whose cost cannot be established at all gets no
    figure and is named. TICKNOCOST appears in the uncosted note; it never
    appears as a row in the priced table (no fabricated $0.00 cost)."""
    html_text = build_page.render(_fake_context())
    idx = html_text.index('id="everything-pnl"')
    section = html_text[idx:html_text.index('</section>', idx)]
    assert "TICKNOCOST" in section
    assert '<table class="datatable pnl">' in section
    table = section[section.index('<table class="datatable pnl">'):
                    section.index('</table>', section.index('<table class="datatable pnl">'))]
    assert "TICKNOCOST" not in table


def test_pnl_panel_renders_nothing_when_every_lot_is_uncosted(build_page):
    """An empty `rows` list (every lot uncosted, or no lot at all) must
    print the same plain-language empty state every other panel on this
    page uses — never an empty `<table>`."""
    ctx = _fake_context()
    for basis in page.BASES_ORDER:
        ctx["bases"][basis]["pnl"] = {
            "realized": page.Figure(0.0, page.money(0.0), "book.realized_pnl()"),
            "unrealized": page.Figure(0.0, page.money(0.0), "book.unrealized_pnl()"),
            "total": page.Figure(0.0, page.money(0.0), "book.total_pnl()"),
            "rows": [],
            "uncosted": [],
        }
    html_text = build_page.render(ctx)
    assert html_text.count('class="empty"') >= len(page.BASES_ORDER)
