"""Build the portfolio page and open it.

    venv/bin/python ops/build_page.py            # build and open
    venv/bin/python ops/build_page.py --no-open  # build only

Spec D1: a built file, not a running app. One command, a self-contained file,
no server to start, nothing that goes blank when a library breaks — and it
physically cannot write to `data/`. Streamlit was considered and rejected: its
one real advantage is live freshness, and his numbers only change when he
syncs or writes, both of which he does by hand. R5 is reliability over speed.

**Nothing here is fetched and nothing here is computed.** Every number
arrives from `src/page.py`, which got it from the module that owns it (D2).
The file references no external URL at all — no CDN, no web font — because a
404 must not be able to make his portfolio page go blank.

D11: the output is machine output. It lands in `ops/`, it is git-ignored, and
it is regenerated in seconds.

2026-10-07 redesign: same data, same functions, new presentation only — a
tab strip (D3's four tabs; three wait their turn), a sticky side list of the
Spread panels, one deliberate type scale, and the fourteen caveats
folded behind a one-line warning above each panel instead of stacked ahead
of the first number. 2026-10-07 corrections 1: a disclosure arrow on every
row that actually expands, and one `--gutter` variable instead of three
hand-typed margins. Nothing in `src/page.py` changed for any of this; every
trap below is about not letting the markup *say* something the data does
not.

Eleven traps this renderer was built to respect, each one a way a correct
`page.context()` could still be shown dishonestly:

  1. **Depth, never level.** `theme_bars()`/`company_bars()` carry `Bar.
     depth`, already walked from `parent` by `src/page.py`. The nested
     `<details>` tree below is built purely from the SEQUENCE of depths (a
     bar has children iff the next bar's depth is greater) — `level` is
     never read here because a `Bar` does not even carry one.
  2. **Two panels, two vocabularies, never one legend.** The sector table
     renders `state` as the module's own raw token; the company table
     renders `note`, the module's own prose, verbatim. Neither is
     translated into the other's wording, and no shared caption explains
     both at once.
  3. **A blank figure is a dash, never `pct(0.0)`.** `_fig()` is the one
     place a `Figure | ""` reaches markup, and it is the only function that
     may look at which of the two it got.
  4. **The warning line above every panel invents no number.** `_warn_line()`
     reads exactly two values that already exist — `ctx["price_age_days"]`
     and `len(data["honesty"])` — and nothing else. The full sentence list
     it unfolds to is `data["honesty"]` rendered verbatim, never re-worded.
  5. **`muted` is rendered, never re-derived.** A theme's own `is_empty`
     already travelled onto `Bar.muted`; this file only reads the flag.
  6. **Every value that reaches the document is escaped.** `_esc()` wraps
     every label, note, source and sentence — a fund literally named
     `SPDR S&P 500 ETF Trust` is in `instruments.csv` today.
  7. **One list, walked twice, never two.** `_PANELS` is the only place every
     Spread panel is named. The sticky side nav and the main column
     both walk it, both skip a key `data` does not supply — a panel that
     stopped being produced would disappear from both at once, because
     neither is typed out a second time anywhere else.
  8. **The three unbuilt tabs say only what the spec already promises.**
     `_COMING_TABS`' three sentences are a paraphrase of `docs/superpowers/
     specs/2026-10-01-portfolio-page-design.md` ("The four tabs" / "Stages"),
     not a new commitment invented for this file.
  9. **One type scale, enforced structurally.** Every `font-size` in `_CSS`
     is a `var(--fs-*)` reference; the four literal pixel values live once,
     in the `:root` block, under the four names the approved design chose
     (NAV / heading / row / label). A fifth size snuck in anywhere else is
     exactly the seven-near-identical-sizes regression this file must not
     let back in — `tests/test_build_page.py` checks the stylesheet text
     for it directly.
  10. **A disclosure arrow only where there is something to disclose.**
      `_bar_row()` takes `has_children` as a parameter, computed once in
      `_bars_tree()` from the SAME depth-sequence test that decides branch
      vs. leaf (trap 1) — never re-derived from `bar.depth` on its own,
      which could not tell a leaf at depth 1 from a branch at depth 1.
      Every row, leaf included, gets the same `.disclosure` slot so the
      columns stay aligned; only `has-children` rows get the glyph, and the
      glyph itself (`▸`/`▾`) is pure CSS keyed off the browser's own
      `details[open]`, the same mechanism `.warnline` already used.
  11. **One gutter, not three numbers that happen to agree today.**
      `--gutter` is declared once, in `:root`; `.topbar`, `.tabs` and
      `.tab-content` all reference it. Changing the page's left/right
      margin is changing one line, not finding three.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
import webbrowser
from pathlib import Path

from markdown_it import MarkdownIt

#: Run directly (`venv/bin/python ops/build_page.py`), `src` is not on
#: `sys.path` unless put there by hand — the same bootstrap `ops/counts.py`
#: and every other script in this directory already uses.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import page  # noqa: E402 — must follow the sys.path bootstrap above

#: The note pop-up's own Markdown renderer (2026-10-07 addition). `html:
#: False` is passed EXPLICITLY rather than trusted to any preset's own
#: idea of "default" — `MarkdownIt()` with no config at all actually
#: resolves to the `"commonmark"` preset, which sets `html: True` (real
#: CommonMark allows raw HTML passthrough); the preset actually named
#: `"default"` is the one that turns it off. Spelling it out here means
#: this file's own behaviour never depends on which of those two a later
#: markdown-it-py release decides to call "the" default. `table` is
#: enabled because his notes carry 436 real table rows across 21 files;
#: nothing else is enabled — no `linkify` (bare URLs are not auto-linked;
#: the one real external link on file, GRNY's, already uses CommonMark's
#: own `<https://...>` autolink syntax and needs no help), no `breaks`.
_MD = MarkdownIt("commonmark", {"html": False}).enable("table")

#: Machine output (D11): regenerated in seconds, never committed. Living
#: beside this script rather than under a computed PROJECT_ROOT so the path
#: is right regardless of the caller's own working directory.
OUTPUT_PATH = Path(__file__).resolve().parent / "portfolio.html"

#: D6's own wording, verbatim ("Everything · Minus cash · Pure equity") —
#: never the dict key, which is a programming token and not a sentence he
#: would recognise.
_BASIS_LABELS = {
    "everything": "Everything",
    "minus-cash": "Minus cash",
    "pure-equity": "Pure equity",
}

#: D3: four tabs, one per build stage. Order is the tab-strip order.
_TABS = ["spread", "holdings", "record", "performance"]
_TAB_LABELS = {
    "spread": "Spread",
    "holdings": "Holdings",
    "record": "Record",
    "performance": "Performance",
}

#: The three unbuilt tabs' own words — a paraphrase of `docs/superpowers/
#: specs/2026-10-01-portfolio-page-design.md`, sections "The four tabs" and
#: "Stages", and nothing else. (heading, one/two-sentence note, stage label)
#: Holdings: spec §"2 — Holdings" + Stages row 2 ("Search, the stock page,
#: theme pages, everything he has read, closed positions").
#: Record: spec §"3 — Record" + Stages row 3 ("Follow-through, staleness,
#: setups, watchlist conversion, due bets, levels, what changed, what is
#: missing").
#: Performance: spec §"4 — Performance" + Stages row 4 ("Deposit-adjusted
#: return vs the S&P. Account volatility and beta").
_COMING_TABS = {
    "holdings": (
        "Holdings",
        "Search a ticker or a theme to see what you own, what you've said "
        "about it, and everything you've read — closed positions "
        "included.",
        "Stage 2",
    ),
    "record": (
        "Record",
        "Reads your own record back to you: whether your trades followed "
        "your plans, which positions have gone quiet, which kinds of "
        "reasons tend to work, and what's overdue for a verdict.",
        "Stage 3",
    ),
    "performance": (
        "Performance",
        "Your return against the S&P, with deposits and withdrawals "
        "stripped out first so money you paid in never reads as a gain.",
        "Stage 4",
    ),
}

#: Trap 7 — the one place every Spread panel is named: the dict key
#: `page.context()` stores the panel under, the short label the sticky side
#: nav shows, and the fuller heading the panel itself shows. Both the side
#: nav and the main column walk THIS list and skip whatever key `data` does
#: not supply; neither ever types them out a second time.
_PANELS: list[tuple[str, str, str]] = [
    ("header", "Overview", "Overview"),
    ("theme_bars", "By theme", "By theme"),
    ("sector_rows", "By sector", "By sector, against the S&amp;P"),
    ("benchmark_company_rows", "vs S&amp;P — companies",
     "Against the S&amp;P, company by company"),
    ("company_bars", "One name", "True exposure to one name"),
    ("overlap_rows", "Overlaps", "Themes holding the same names"),
    ("construction", "How it's built", "How it's built"),
    ("pnl", "P&amp;L", "Profit &amp; loss"),
]


def _esc(value) -> str:
    """Every value that reaches the document goes through here (trap 6)."""
    return html.escape(str(value), quote=True)


def _fig(fig, prefix: str = "") -> str:
    """One number, with its source as hover text (D12/provenance).

    `fig` is either a `page.Figure` or `""` — the shape `page.py` already
    enforces for every optional number (trap 3). `""` renders as a dash,
    never as `pct(0.0)` or `money(0.0)`: an absent figure is not a zero, and
    this is the one place in the file that gets to tell the two apart.

    `prefix` labels a BOUND as a bound (`≤` for `benchmark_at_most`,
    `≥` for `difference_floor`) without touching the number itself —
    presentation, not arithmetic; the value and its formatting both still
    come straight from `page.py`.
    """
    if isinstance(fig, page.Figure):
        text = f"{prefix}{fig.text}" if prefix else fig.text
        return f'<span class="fig" title="{_esc(fig.source)}">{_esc(text)}</span>'
    return '<span class="fig-blank">&mdash;</span>'


def _age_html(age) -> str:
    """`price_age_days` as the page shows it — in the top meta line and in
    every panel's own warning line. One function so the two can never say
    something different about the same number."""
    if age is None:
        return '<span class="fig-blank">age unknown</span>'
    return (f'<span class="fig" title="instruments.price_age_days()">'
            f'{_esc(age)} day(s) old</span>')


# ─── bars: header.biggest, theme_bars, company_bars all share this shape ──

def _bar_label(bar) -> str:
    """A plain label, or — his main request, 2026-10-06 — a link that opens
    the note in the pop-up rather than navigating to a new page. `bar.href`
    is already `_href()`'s own `"../" + ref`; `_note_ref_of()` strips that
    back off to the key `ctx["notes"]` (and `.note-source[data-ref]` in the
    built markup) is actually keyed on. `href="#"` plus `data-note-ref`
    only, never the old file path — `_JS` is the only thing that acts on
    the click, the same dependency the tab strip and basis control already
    have on it."""
    text = _esc(bar.label)
    if bar.href:
        ref = _note_ref_of(bar.href)
        return (f'<a href="#" class="notelink" '
                f'data-note-ref="{_esc(ref)}">{text}</a>')
    return text


def _bar_track(bar) -> str:
    """A zero-axis track: positive bars fill right of the axis, negative
    bars fill left of it (D8) — the only place the one accent colour
    (`.fill.neg`) is ever used."""
    width = max(0.0, min(100.0, bar.width))
    if bar.negative:
        fill = f'<i class="fill neg" style="width:{width:.2f}%"></i>'
        return (f'<span class="track"><span class="half neg">{fill}</span>'
                f'<span class="half pos"></span></span>')
    fill = f'<i class="fill" style="width:{width:.2f}%"></i>'
    return (f'<span class="track"><span class="half neg"></span>'
            f'<span class="half pos">{fill}</span></span>')


def _bar_row(bar, has_children: bool) -> str:
    """`has_children` decides only whether the disclosure slot carries a
    glyph (trap 10) — it never changes the row's other markup, and it is
    passed in by the caller, which already knows it from the SEQUENCE of
    depths (the same test `_bars_tree()` uses to decide branch vs. leaf),
    never re-derived from `bar.depth` here."""
    classes = "barrow"
    if bar.muted:
        classes += " muted"
    if bar.negative:
        classes += " negrow"
    disclosure_cls = "disclosure has-children" if has_children else "disclosure"
    return (
        f'<span class="{classes}">'
        f'<span class="{disclosure_cls}"></span>'
        f'<span class="lbl">{_bar_label(bar)}</span>'
        f'{_bar_track(bar)}'
        f'<span class="val">{_fig(bar.figure)}</span>'
        f'<span class="pct">{_esc(page.pct(bar.pct))}</span>'
        f'</span>'
    )


def _bars_tree(bars: list) -> str:
    """Ranked bars, drilled however deep `Bar.depth` goes (D5).

    Built from the SEQUENCE of depths alone (trap 1): a bar has children
    iff the very next bar in the list is one level deeper, and that is the
    only test used to decide whether it becomes a `<details>` (a branch,
    collapsible, closed by default) or a plain row (a leaf). Indentation
    is never computed from `depth` directly — nesting the real `<details>`
    tags and letting each level's own `padding-left` compound is what keeps
    a leaf and a sibling branch at the same depth aligned without any
    arithmetic on `depth` at all.

    Trap 10, found by him: a branch row used to give no visual sign it
    could be opened — `.warnline` got its `▸`/`▾` and the theme tree did
    not. Every row here, leaf or branch, now carries a `.disclosure` slot
    of the SAME reserved width (`_CSS`'s `.disclosure`), so a leaf's label
    still lines up with an expandable sibling at the same depth; only a
    branch's slot gets the `has-children` class that actually paints the
    glyph (`_CSS`'s `.disclosure.has-children::before`, flipped by the
    browser's own `details[open]`). `has_children` is computed once, from
    the depth sequence, and handed to both the row and the `<details>`
    wrapper — never re-derived from `bar.depth` a second time, which is
    exactly the kind of drift that could leave a leaf showing an arrow for
    something that does not happen.
    """
    if not bars:
        return '<p class="empty">Nothing here under this setting.</p>'
    out: list[str] = []
    open_depths: list[int] = []
    n = len(bars)
    for i, bar in enumerate(bars):
        while open_depths and open_depths[-1] >= bar.depth:
            out.append("</details>")
            open_depths.pop()
        depth_cls = f"d{min(bar.depth, 3)}"
        has_children = i + 1 < n and bars[i + 1].depth > bar.depth
        row = _bar_row(bar, has_children)
        note_html = (f'<div class="barnote">{_esc(bar.note)}</div>'
                    if bar.note else "")
        if has_children:
            out.append(f'<details class="branch {depth_cls}">'
                      f'<summary>{row}</summary>{note_html}')
            open_depths.append(bar.depth)
        else:
            out.append(f'<div class="leafrow {depth_cls}">{row}{note_html}</div>')
    while open_depths:
        out.append("</details>")
        open_depths.pop()
    return "".join(out)


# ─── the note pop-up (2026-10-06 brief) ────────────────────────────────────
#
# His words: "I want it to be a pop-up window and not like a new page... and
# I want it to look better." `ctx["notes"]` (`page.embedded_notes()`) already
# holds, read once each, every note a bar on this page actually links to —
# this section turns `.text` into HTML and builds the markup and script the
# pop-up itself needs. `src/notes.py` reads bytes; this file is the one
# place that is allowed to call it "rendering."
#
# Two traps specific to this feature:
#
#   12. **A note's own first `# ` heading is not repeated inside the
#       pop-up's body.** It is already the pop-up's title (`.note-source`'s
#       own `data-title`, read straight off `NoteText.title`); re-rendering
#       it as an `<h1>` inside the body too would print his thesis's name
#       twice for no reason. `_strip_leading_title()` removes only the
#       FIRST such line, on the raw Markdown, before `_MD.render()` ever
#       sees it — never a second, independent guess at what the title was.
#   13. **A URL he quoted in his own note is content, not a load, and must
#       reach the document exactly as he wrote it.** One note (GRNY's)
#       quotes a real URL via plain CommonMark autolink syntax
#       (`<https://...>`), which `_MD.render()` turns into a real
#       `<a href="https://...">` — a link he may click, after the page
#       has already rendered, which cannot make the page blank. An
#       earlier version of this function HTML-escaped the slashes in it
#       (`https:&#47;&#47;...`) purely to dodge a substring check that
#       was never about this — that check (D1/R5) is about a LOADED
#       resource, a CDN script or a remote stylesheet that can 404 before
#       he ever gets to click anything. Escaping his own quoted link to
#       satisfy a check meant for something else is a test dodge, not a
#       fix — it misrepresents what the document contains, and the next
#       note he writes with a URL in it would be silently mangled the
#       same way. Fixed properly in two places: nothing here escapes a
#       URL any more, and `tests/test_build_page.py::
#       test_the_page_loads_no_external_resource` now checks for an
#       actual resource LOAD (`<script src=`, `<link rel="stylesheet"`,
#       `@import`, `url(http`, `<img src="http`, `<iframe`, `fetch(`,
#       `XMLHttpRequest`) instead of banning the substring `http(s)://`
#       outright.

def _note_ref_of(href: str) -> str:
    """`_href()`'s own `"../" + ref` (or a bare `ref`, which some test
    fixtures use directly), with the prefix stripped back off — the one
    place that reversal happens, so a bar's link and a note's own
    `.note-source[data-ref]` can never disagree about the key."""
    return href[3:] if href.startswith("../") else href


_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _resolve_note_link(note_ref: str, href: str) -> str | None:
    """Where a relative Markdown link inside the note at `note_ref` actually
    points, as a path relative to the repo root — or `None` when `href` is
    not a repo-relative file path at all (an absolute URL, a bare
    `#fragment`, `mailto:`, a leading `/`), so this never tries to resolve
    something that was never a file path to begin with.

    Needed because a note's own links are written relative to ITS OWN
    directory (`notes/positions/ASML.md`'s `../clusters/ai-infrastructure.
    md` means `notes/clusters/ai-infrastructure.md`) — resolving against
    the wrong base (the built file's own directory, `ops/`, the way a
    bar's `href` already is) would silently point at the wrong file.
    """
    if not href:
        return None
    head = href.split("#", 1)[0].split("?", 1)[0]
    if not head or _SCHEME_RE.match(head) or head.startswith("/"):
        return None
    try:
        resolved = (PROJECT_ROOT / Path(note_ref).parent / head).resolve()
        rel = resolved.relative_to(PROJECT_ROOT)
    except (ValueError, OSError):
        return None
    return str(rel).replace("\\", "/")


_ANCHOR_OPEN_RE = re.compile(r"<a\s+([^>]*)>")
_HREF_ATTR_RE = re.compile(r'href="([^"]*)"')


def _rewrite_note_links(fragment: str, note_ref: str, embedded: set[str]) -> str:
    """Every `<a href="...">` `_MD.render()` wrote for this note, corrected.

    A link that resolves (via `_resolve_note_link()`) to another note THIS
    PAGE also embedded becomes an overlay link — `href="#"` plus
    `data-note-ref`, exactly the shape `_bar_label()` already produces for
    a bar, so one JS handler drives both. His notes contain 141 links
    across 39 files; two of his position notes linking to the same cluster
    note (ASML and NVDA both cite AI Infrastructure, for instance) is
    exactly this case. A link that resolves to a REAL file that merely
    is not embedded keeps its normal behaviour — corrected to be relative
    to `ops/` (the built file's own directory) rather than left relative to
    the note's, which would silently point at the wrong file. Anything
    that does not resolve to a repo file at all (an external URL, a
    `mailto:`) is left exactly as `_MD.render()` wrote it — never disabled.
    """
    def repl(m: re.Match) -> str:
        attrs = m.group(1)
        href_m = _HREF_ATTR_RE.search(attrs)
        if not href_m:
            return m.group(0)
        raw_href = html.unescape(href_m.group(1))
        resolved = _resolve_note_link(note_ref, raw_href)
        if resolved is None:
            return m.group(0)
        if resolved in embedded:
            new_attrs = _HREF_ATTR_RE.sub('href="#"', attrs, count=1)
            return (f'<a {new_attrs} class="notelink" '
                    f'data-note-ref="{_esc(resolved)}">')
        corrected = "../" + resolved
        new_attrs = _HREF_ATTR_RE.sub(f'href="{_esc(corrected)}"', attrs, count=1)
        return f'<a {new_attrs}>'

    return _ANCHOR_OPEN_RE.sub(repl, fragment)


_LEADING_H1_RE = re.compile(r"^#[ \t]+.*\n+")


def _strip_leading_title(text: str) -> str:
    """See trap 12 above. Removes only the FIRST `# ` line and the blank
    line(s) after it; every other heading in the note is untouched."""
    return _LEADING_H1_RE.sub("", text, count=1)


def _render_note_html(note, embedded: set[str]) -> str:
    """One note's content, as the pop-up's body — or, if `src/notes.py`
    could not find the file on disk, the plain-language message rather
    than empty markup (CLAUDE.md: a missing note must not crash the build
    or open an empty box). Never raises on a stale `note_ref` — the exact
    kind `instruments.stale_refs()` already reports as a standing list;
    this is the same fact, surfaced in the moment the note is opened.
    """
    if not note.ok:
        return (
            '<p class="notemissing">There is no file here. The record '
            f'points at <code>{_esc(note.ref)}</code>, but nothing is '
            'there — it was never written, or it moved.</p>'
        )
    rendered = _MD.render(_strip_leading_title(note.text))
    return _rewrite_note_links(rendered, note.ref, embedded)


def _notes_overlay_block(ctx: dict) -> str:
    """The pop-up itself: one hidden overlay shell, shared by every basis
    and every tab, plus one hidden `.note-source` per note `ctx["notes"]`
    holds (`page.embedded_notes()` — read from the hrefs the context
    actually produced, never a directory scan). `_JS` below reads these;
    nothing here is basis-specific, so this is built once, not per basis.
    """
    notes_map = ctx.get("notes", {})
    embedded = set(notes_map.keys())
    sources = []
    for ref, note in notes_map.items():
        title = note.title if note.ok else f"Note missing — {ref}"
        body = _render_note_html(note, embedded)
        sources.append(
            f'<div class="note-source" data-ref="{_esc(ref)}" '
            f'data-title="{_esc(title)}" hidden>{body}</div>'
        )
    return (
        '<div class="note-overlay" id="note-overlay" hidden aria-hidden="true">'
        '<div class="note-backdrop" id="note-backdrop"></div>'
        '<div class="note-dialog" id="note-dialog" role="dialog" '
        'aria-modal="true" aria-labelledby="note-overlay-title" tabindex="-1">'
        '<button type="button" class="note-close" id="note-close" '
        'aria-label="Close">&times;</button>'
        '<h2 id="note-overlay-title"></h2>'
        '<div id="note-overlay-body"></div>'
        '</div></div>'
        + "".join(sources)
    )


# ─── header panel: NAV is the one number he anchors on ────────────────────

def _header_panel(data: dict) -> str:
    """Spec panel 1. NAV renders at `--fs-hero`; cash is real and visible
    but deliberately smaller — the approved design names NAV, singular, as
    the large number at the top. `state_line` is rendered beside the basis
    pills instead (see `_basis_control()`), not inside this panel, so it
    reads as one unit with the control that produced it."""
    stats = (
        '<div class="stat-row">'
        '<div class="stat stat-hero"><div class="lb">Net asset value</div>'
        f'<div class="hero">{_fig(data["nav"])}</div></div>'
        '<div class="stat stat-sub"><div class="lb">Cash &amp; T-bills</div>'
        f'<div class="subfig">{_fig(data["cash"])}</div></div>'
        '</div>'
    )
    biggest = (
        '<div class="panel-sub"><div class="sublabel">Biggest five</div>'
        f'{_bars_tree(data["biggest"])}</div>'
    )
    return stats + biggest


# ─── by sector ──────────────────────────────────────────────────────────

def _sector_table(rows: list[dict]) -> str:
    """Spec panel 3. `state` is rendered as-is — `exposure.
    benchmark_sector_comparison()`'s own vocabulary, never translated into
    the company panel's (trap 2).

    🚨 **Fix, 2026-10-07 final review (I6).** The column used to read "Est.
    share" — which reads as "how much of this is estimated" — for
    `pct_estimated`, a narrower thing: the share sourced from a composition
    row whose `source_quality` is literally `estimate` (worked out here,
    never from the issuer). No held fund carries that value today, so the
    column was 0.00% on every row while a sector could sit right beside it
    at `Direct $0.00` next to a five-figure look-through total — reading, to
    a non-developer, as "measured, not inferred". Renamed to say what it
    means; the honest, fuller answer (`exposure.coverage()`) is still only
    in the honesty block, never duplicated here.

    `note` (fix I8, same review) is `page.sector_rows()`'s own caption —
    blank except on an `only-mine` row (the index cannot hold this sector
    at all), an `unclassified` row (money with no sector on file at all —
    pre-push review 2026-10-07, finding 1), or an `only-index` row (his
    side reads a confirmed zero, or — when unclassified money exists this
    call — says it cannot be confirmed). Never built here; this file only
    renders it."""
    if not rows:
        return '<p class="empty">No sector breakdown under this setting.</p>'
    head = (
        '<tr><th class="l">Sector</th><th>Value</th><th>% of basis</th>'
        '<th>Direct</th><th>From an estimated breakdown</th><th>S&amp;P</th>'
        '<th>Diff</th><th class="l">State</th><th class="l">Note</th></tr>'
    )
    body = []
    for r in rows:
        state = _esc(r["state"]) if r["state"] else "&mdash;"
        direct_html = _fig(r["direct_value"])
        # Fix I6, second half: `$0.00` alone does not say whether nothing
        # was found or everything was found through a fund — true only
        # when `value` (the sector's real total) is itself not zero, so a
        # sector he holds nothing of at all (I3's new rows, `value` also
        # 0.0) gets no misleading "(all look-through)" tacked onto a
        # correctly blank zero.
        if (isinstance(r["direct_value"], page.Figure)
                and r["direct_value"].value == 0.0
                and isinstance(r["value"], page.Figure)
                and r["value"].value > 0.0):
            direct_html += ' <span class="fig-note">(all look-through)</span>'
        body.append(
            '<tr>'
            f'<td class="l">{_esc(r["sector"])}</td>'
            f'<td>{_fig(r["value"])}</td>'
            f'<td>{_fig(r["pct"])}</td>'
            f'<td>{direct_html}</td>'
            f'<td>{_fig(r["estimated_pct"])}</td>'
            f'<td>{_fig(r["benchmark"])}</td>'
            f'<td>{_fig(r["difference"])}</td>'
            f'<td class="l state">{state}</td>'
            f'<td class="l note">{_esc(r.get("note", ""))}</td>'
            '</tr>'
        )
    return f'<table class="datatable">{head}{"".join(body)}</table>'


# ─── against the S&P, company by company ───────────────────────────────

def _company_table(data: dict) -> str:
    """Spec panel 4. `note` is `page.benchmark_company_rows()`'s own prose,
    rendered verbatim (trap 2) — this file invents no wording of its own
    for `below-floor` or `unknown`. A blank `benchmark` falls back to the
    stored bound (`benchmark_at_most`, marked `≤`) rather than a dash
    only when a bound actually exists; a blank `difference` falls back to
    `difference_floor` (marked `≥`) the same way. Neither fallback
    invents a number — it only chooses which already-computed figure to
    show (trap 3)."""
    rows, note = data["rows"], data["note"]
    parts = []
    if note:
        parts.append(f'<p class="panelnote">{_esc(note)}</p>')
    if not rows:
        parts.append('<p class="empty">No comparison available under this setting.</p>')
        return "".join(parts)
    head = (
        '<tr><th class="l">Company</th><th>Mine</th><th>S&amp;P</th>'
        '<th>Diff</th><th class="l">State</th><th class="l">Note</th></tr>'
    )
    body = []
    for r in rows:
        if isinstance(r["benchmark"], page.Figure):
            bench_html = _fig(r["benchmark"])
        elif isinstance(r["benchmark_at_most"], page.Figure):
            bench_html = _fig(r["benchmark_at_most"], prefix="≤")
        else:
            bench_html = _fig("")
        if isinstance(r["difference"], page.Figure):
            diff_html = _fig(r["difference"])
        elif isinstance(r["difference_floor"], page.Figure):
            diff_html = _fig(r["difference_floor"], prefix="≥")
        else:
            diff_html = _fig("")
        body.append(
            '<tr>'
            f'<td class="l">{_esc(r["label"])}</td>'
            f'<td>{_fig(r["mine"])}</td>'
            f'<td>{bench_html}</td>'
            f'<td>{diff_html}</td>'
            f'<td class="l state">{_esc(r["state"])}</td>'
            f'<td class="l note">{_esc(r["note"]) if r["note"] else ""}</td>'
            '</tr>'
        )
    parts.append(f'<table class="datatable">{head}{"".join(body)}</table>')
    return "".join(parts)


# ─── themes holding the same names ──────────────────────────────────────

def _overlap_table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="empty">No overlap under this setting.</p>'
    head = (
        '<tr><th class="l">Theme A</th><th class="l">Theme B</th>'
        '<th>Shared</th><th>% of A</th><th>% of B</th>'
        '<th class="l">Top names</th><th class="l">Note</th></tr>'
    )
    body = []
    for r in rows:
        body.append(
            '<tr>'
            f'<td class="l">{_esc(r["cluster_a"])}</td>'
            f'<td class="l">{_esc(r["cluster_b"])}</td>'
            f'<td>{_fig(r["shared"])}</td>'
            f'<td>{_fig(r["pct_of_a"])}</td>'
            f'<td>{_fig(r["pct_of_b"])}</td>'
            f'<td class="l">{_esc(r["top_names"])}</td>'
            f'<td class="l note">{_esc(r["note"]) if r["note"] else ""}</td>'
            '</tr>'
        )
    return f'<table class="datatable">{head}{"".join(body)}</table>'


# ─── how it's built ──────────────────────────────────────────────────────

def _construction_table(rows: list[dict]) -> str:
    """Spec panel 7. `value` is blank for the worst-drawdown row, by the
    investor's own call, 2026-10-06 — that figure is deferred to the
    Performance tab, and `why` carries the deferral sentence in place of a
    number this file is forbidden from computing. This file reads no raw
    NAV series and contains no drawdown idiom — a tripwire in
    tests/test_exposure.py checks both, by name, for this exact file."""
    body = []
    for r in rows:
        body.append(
            '<tr>'
            f'<td class="l">{_esc(r["metric"])}</td>'
            f'<td>{_fig(r["value"])}</td>'
            f'<td class="l why">{_esc(r["why"])}</td>'
            '</tr>'
        )
    return f'<table class="datatable constr">{"".join(body)}</table>'


# ─── profit and loss ─────────────────────────────────────────────────────

def _pnl_table(data: dict) -> str:
    """D7: P&L is present but quiet — grey, never coloured, never sortable.

    No class here is ever conditioned on a figure's SIGN — `.fill.neg`
    (the brick-red a short's bar fills with, D8) is deliberately never
    reused anywhere in this function. A negative `unrealized` renders in
    exactly the same ink as a positive one; only `page.money()`'s own
    leading `-` tells the two apart, the same restraint D9 already asks of
    every dollar figure on this page.

    The two totals sit in the same `stat-row`/`stat-sub` shape
    `_header_panel()` already uses for NAV/cash, so a reader who knows
    that panel already knows how to read this one. `flagged` rows (today,
    none — both real foreign lots on file reconstruct cleanly) get a small
    marker rather than a colour; `page.honesty()` is where the actual
    sentence explaining the marker lives, read the same way every other
    caveat on this page already is, through the warning line above."""
    stats = (
        '<div class="stat-row">'
        '<div class="stat stat-sub"><div class="lb">Realised</div>'
        f'<div class="subfig">{_fig(data["realized"])}</div></div>'
        '<div class="stat stat-sub"><div class="lb">Unrealised</div>'
        f'<div class="subfig">{_fig(data["unrealized"])}</div></div>'
        '<div class="stat stat-sub"><div class="lb">Realised + unrealised</div>'
        f'<div class="subfig">{_fig(data["total"])}</div></div>'
        '</div>'
    )
    rows = data["rows"]
    if not rows:
        body = '<p class="empty">No lot carries an established cost.</p>'
    else:
        head = (
            '<tr><th class="l">Holding</th><th>Market value</th>'
            '<th>Cost</th><th>Unrealised</th></tr>'
        )
        trs = []
        for r in rows:
            mark = ' <span class="fig-note">&deg;</span>' if r["flagged"] else ""
            trs.append(
                '<tr>'
                f'<td class="l">{_esc(r["name"])}{mark}</td>'
                f'<td>{_fig(r["market_value"])}</td>'
                f'<td>{_fig(r["cost"])}</td>'
                f'<td>{_fig(r["unrealized"])}</td>'
                '</tr>'
            )
        body = (f'<table class="datatable pnl">{head}{"".join(trs)}</table>')
    note = ""
    uncosted = data.get("uncosted") or []
    if uncosted:
        names = ", ".join(_esc(u["name"]) for u in uncosted)
        note = (f'<p class="panelnote">No established cost for: {names} '
               f'— see Trust for why.</p>')
    return stats + body + note


def _theme_total_row(total, coverage_pct=None) -> str:
    """The By-theme panel's OWN total row — `page.theme_total()`'s `book.
    nav()` figure, underlined in the statement style the rest of the page
    already uses for a rule (`.panel h2`'s own `border-bottom`). `total`
    is a `page.Figure` or `None`; `None` only if a caller's own context
    never supplied `"theme_total"` at all (every real `page.context()`
    does), in which case this renders nothing rather than a blank row.

    Reuses the bare `.disclosure`/`.track`/`.val`/`.pct` classes `_bar_row()`
    already defined at those exact widths, so this row's dollar figure and
    percentage line up in the SAME columns as every bar above it, with no
    new CSS size of its own.

    🚨 **`coverage_pct` — fix, pre-push review 2026-10-07 (finding 3).**
    This used to hard-code `page.pct(100.0)` on the premise that
    `theme_bars()`'s own rows always add up to the whole basis — the one
    row that could have revealed the day that stopped being true, made
    structurally incapable of doing so. `coverage_pct` is `page.
    theme_coverage_pct()`'s own `Figure` (`clusters.tree_coverage_pct()`'s
    number) — rendered through `_fig()` exactly like `total`, so a blank
    context (no such key, or `None`) still falls back to the `&mdash;`
    `_fig()` already draws for anything that is not a `page.Figure`, never
    back to an assumed 100.

    **No other panel gets one — this is the only call site, on purpose.**
    `sector_rows()` compares against the S&P and does not meaningfully
    total; `benchmark_company_rows()`'s total would only restate this same
    basis figure a second time; `company_bars()` is filtered to real
    companies only and does not cover the basis; `overlap_rows()`'s pairs
    are not a partition, so summing them would double-count shared money.
    Adding a second call to this function for any of those panels is
    exactly the mistake this comment exists to head off — see `page.
    theme_total()`'s own docstring for the fuller version of this
    argument.
    """
    if total is None:
        return ""
    return (
        '<div class="themetotal">'
        '<span class="disclosure"></span>'
        '<span class="lbl">Total</span>'
        '<span class="track"></span>'
        f'<span class="val">{_fig(total)}</span>'
        f'<span class="pct">{_fig(coverage_pct)}</span>'
        '</div>'
    )


# ─── one panel body, by its dict key ────────────────────────────────────

def _panel_body(key: str, data: dict) -> str:
    """Dispatch to the one renderer each panel key already had — `data` is
    the WHOLE per-basis dict, not just `data[key]`, so the one panel that
    needs a second key alongside its own (`theme_bars`, which also wants
    `theme_total`) does not need a second dispatch path of its own."""
    value = data[key]
    if key == "header":
        return _header_panel(value)
    if key == "theme_bars":
        return _bars_tree(value) + _theme_total_row(
            data.get("theme_total"), data.get("theme_coverage_pct"))
    if key == "company_bars":
        return _bars_tree(value)
    if key == "sector_rows":
        return _sector_table(value)
    if key == "benchmark_company_rows":
        return _company_table(value)
    if key == "overlap_rows":
        return _overlap_table(value)
    if key == "construction":
        return _construction_table(value)
    if key == "pnl":
        return _pnl_table(value)
    raise KeyError(f"no renderer for panel key {key!r}")


# ─── the one warning line above every panel (trap 4) ───────────────────

def _warn_line(data: dict, age) -> str:
    """One collapsed grey line above every panel. Unfolds, on click, to the
    exact sentences `honesty()` wrote for this basis — never a re-worded
    summary. The two numbers in the collapsed line are `age` (`ctx[
    "price_age_days"]`, already computed by `instruments.price_age_days()`)
    and `len(data["honesty"])` (how many sentences are already sitting in
    the list); nothing here counts, measures or re-derives anything (trap
    4) — this is the one place CLAUDE.md's "no number in prose that the
    code can compute" rule is checked by a test, not just a rule."""
    lines = data["honesty"]
    count = len(lines)
    noun = "thing" if count == 1 else "things"
    items = "".join(f"<li>{_esc(line)}</li>" for line in lines)
    summary = (
        f'<span class="warnsummary">Prices {_age_html(age)} '
        f'&middot; {count} {noun} worth knowing about these numbers</span>'
    )
    body = f'<ul>{items}</ul>' if items else ""
    return f'<details class="warnline"><summary>{summary}</summary>{body}</details>'


def _trust_section(basis: str, data: dict) -> str:
    """The side nav's "Trust" entry lands here: the SAME `honesty()`
    sentences as every panel's warning line, laid out in full and already
    open — nothing to click to see the rest of it."""
    lines = data["honesty"]
    if lines:
        items = "".join(f"<li>{_esc(line)}</li>" for line in lines)
    else:
        items = '<li class="empty">Nothing flagged for this basis.</li>'
    return (
        f'<section class="panel trust" id="{_esc(basis)}-trust">'
        f'<h2>Trust</h2><ul class="trustlist">{items}</ul></section>'
    )


# ─── the sticky side list and the main column (trap 7: one list, walked twice) ──

def _side_nav(basis: str, data: dict) -> str:
    """Every Spread panel becomes a menu entry that stays in view while
    the page scrolls. Walks `_PANELS`, skipping any key `data` does not
    actually supply — never a separate, hand-typed list of link labels
    that could say more than the page actually renders."""
    links = [
        f'<a href="#{_esc(basis)}-{_esc(key)}">{nav_label}</a>'
        for key, nav_label, _ in _PANELS if key in data
    ]
    count = len(data.get("honesty", []))
    trust_label = f"{count} caveat{'' if count == 1 else 's'}"
    return (
        '<nav class="side">'
        '<div class="navcap">On this tab</div>'
        + "".join(links)
        + '<div class="navcap trustcap">Trust</div>'
        f'<a href="#{_esc(basis)}-trust">{_esc(trust_label)}</a>'
        '</nav>'
    )


def _main_column(basis: str, data: dict, age) -> str:
    """The same `_PANELS` walk as `_side_nav()`, in the same order, so a
    panel can never appear in one and not the other (trap 7)."""
    sections = []
    for key, _, full_heading in _PANELS:
        if key not in data:
            continue
        sections.append(
            f'<section class="panel" id="{_esc(basis)}-{_esc(key)}">'
            + _warn_line(data, age)
            + f'<h2>{full_heading}</h2>'
            + _panel_body(key, data)
            + '</section>'
        )
    sections.append(_trust_section(basis, data))
    return '<div class="main">' + "".join(sections) + '</div>'


def _basis_block(basis: str, data: dict, visible: bool, age) -> str:
    hidden = "" if visible else " hidden"
    layout = (
        '<div class="layout">'
        + _side_nav(basis, data)
        + _main_column(basis, data, age)
        + '</div>'
    )
    return (f'<div class="basis-block" data-basis="{_esc(basis)}"{hidden}>'
            + layout + '</div>')


# ─── the basis control: pills, and the plain-English state beside them ──

def _basis_control(bases: dict, order: list[str]) -> str:
    """D6's three settings as a row of pills, with each one's own
    plain-English `state_line` sitting beside them — not inside the
    Overview panel any more, so it reads as one control-and-caption unit.
    All three state lines are pre-rendered; only one is ever visible,
    toggled by the same radio group that shows and hides the basis-blocks
    (`_JS` targets both by their shared `[data-basis]` attribute)."""
    radios = []
    states = []
    for i, basis in enumerate(order):
        visible = i == 0
        label = _BASIS_LABELS.get(basis, basis)
        checked = " checked" if visible else ""
        radios.append(
            f'<input type="radio" id="basis-{_esc(basis)}" name="basis" '
            f'value="{_esc(basis)}"{checked}>'
            f'<label for="basis-{_esc(basis)}">{_esc(label)}</label>'
        )
        hidden = "" if visible else " hidden"
        state_text = bases[basis]["header"]["state_line"]
        states.append(
            f'<span class="state-line" data-basis="{_esc(basis)}"{hidden}>'
            f'{_esc(state_text)}</span>'
        )
    return (
        '<div class="basis-control">'
        + "".join(radios)
        + '<span class="state-sep">&mdash;</span>'
        + "".join(states)
        + '</div>'
    )


# ─── the tab strip: D3's four tabs ───────────────────────────────────────

def _tab_strip() -> str:
    parts = ['<div class="tabs">']
    for i, tab in enumerate(_TABS):
        checked = " checked" if i == 0 else ""
        parts.append(
            f'<input type="radio" id="tab-{tab}" name="tab" value="{tab}"'
            f'{checked}><label for="tab-{tab}">{_TAB_LABELS[tab]}</label>'
        )
    parts.append('</div>')
    return "".join(parts)


def _coming_panel(heading: str, note: str, stage: str) -> str:
    """An unbuilt tab: clickable, and honest about being unbuilt (trap 8).
    `note`/`stage` always come from `_COMING_TABS`, never typed here."""
    return (
        '<div class="comingnote">'
        f'<h2>{_esc(heading)}</h2>'
        f'<p>{_esc(note)}</p>'
        f'<p class="stagetag">{_esc(stage)} &mdash; not built yet.</p>'
        '</div>'
    )


# ─── style and behaviour: inline, so the file needs nothing else ───────
#
# Trap 9: every `font-size` below is `var(--fs-*)`. The four literal pixel
# values live once, in :root, under the names the approved design chose —
# NAV/hero, panel heading, row, small label. Nothing outside :root may
# declare a raw size; that is what stops the old seven-near-identical-sizes
# problem from growing back one rule at a time.
#
# Trap 11, found by him: the gutter (`.topbar`/`.tabs`/`.tab-content`'s
# shared left/right padding) is one `--gutter` variable, not three
# hand-typed `24px`s — three numbers that happened to match today could
# drift apart tomorrow one edit at a time, which is the same failure
# `--fs-*` already exists to prevent for font sizes.

_CSS = """
*{box-sizing:border-box}
body{margin:0}
:root{
  --fs-hero:25px;
  --fs-heading:11px;
  --fs-row:12.5px;
  --fs-label:9.5px;
  --gutter:10px;
  --ink:#14171a;
  --rule:#d9dce1;
  --rule-soft:#eceef1;
  --muted:#6b7280;
  --faint:#9aa3ae;
  --neg:#8a3b2e;
}
.page{background:#fff;color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,
  "Segoe UI",Helvetica,Arial,sans-serif;line-height:1.5;
  font-variant-numeric:tabular-nums;max-width:1080px;margin:0 auto;
  padding:0 0 48px}
.topbar{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;
  padding:16px var(--gutter) 10px}
.brand{font-size:var(--fs-label);letter-spacing:.09em;text-transform:uppercase;
  color:var(--faint);font-weight:700}
.meta{font-size:var(--fs-label);color:var(--muted)}

.tabs{display:flex;border-bottom:1px solid var(--rule);background:#fafbfc;
  padding:0 var(--gutter)}
.tabs input{display:none}
.tabs label{display:inline-block;padding:10px 18px;font-size:var(--fs-row);
  color:var(--faint);border-bottom:2px solid transparent;cursor:pointer;
  font-weight:500}
.tabs input:checked+label{color:var(--ink);font-weight:650;
  border-bottom-color:var(--ink)}
.tab-content[hidden]{display:none}
.tab-content{padding:0 var(--gutter)}

.basis-control{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  padding:12px 0 6px}
.basis-control input{display:none}
.basis-control label{font-size:var(--fs-row);border:1px solid var(--rule);
  border-radius:11px;padding:3px 11px;cursor:pointer;color:#3b4149}
.basis-control input:checked+label{background:var(--ink);color:#fff;
  border-color:var(--ink)}
.state-sep{color:var(--faint)}
.state-line{font-size:var(--fs-label);color:var(--muted)}
.state-line[hidden]{display:none}

.taxonomy-note{font-size:var(--fs-label);color:var(--muted);
  border-left:3px solid #c9ced6;padding:6px 12px;margin:4px 0 20px;max-width:720px}

.basis-block[hidden]{display:none}
.layout{display:flex;gap:28px;align-items:flex-start}

.side{width:160px;flex:0 0 160px;position:sticky;top:12px;
  padding:4px 0 24px;align-self:flex-start}
.navcap{font-size:var(--fs-label);letter-spacing:.08em;text-transform:uppercase;
  color:var(--faint);font-weight:650;margin:14px 0 5px}
.navcap:first-child{margin-top:0}
.side a{display:block;font-size:var(--fs-row);color:#3b4149;
  text-decoration:none;padding:4px 6px;border-radius:3px;margin:0 -6px}
.side a:hover{background:#f2f3f5}

.main{flex:1;min-width:0}
.panel{margin:0 0 30px}
.panel h2{font-size:var(--fs-heading);letter-spacing:.09em;text-transform:uppercase;
  color:#5a616b;font-weight:700;margin:10px 0 10px;border-bottom:1px solid var(--ink);
  padding-bottom:5px}

.warnline{margin:0 0 9px}
.warnline summary{display:block;cursor:pointer;font-size:var(--fs-label);
  color:var(--muted);background:#f6f7f8;border-left:3px solid var(--rule);
  padding:5px 10px;list-style:none}
.warnline summary::-webkit-details-marker{display:none}
.warnline summary::before{content:"▸ "}
.warnline[open] summary::before{content:"▾ "}
.warnline ul{font-size:var(--fs-row);color:#2c3139;margin:4px 0 0;
  padding:8px 14px 2px 26px;background:#f6f7f8;border-left:3px solid var(--rule)}
.warnline li{margin:2px 0}

.trust h2{color:var(--ink)}
.trustlist{font-size:var(--fs-row);color:#2c3139;padding-left:18px;margin:0}
.trustlist li{margin:4px 0}
.trustlist li.empty{color:var(--faint);list-style:none;padding-left:0;margin-left:-18px}

.stat-row{display:flex;gap:40px;margin-bottom:6px;align-items:flex-end}
.stat .lb{font-size:var(--fs-label);letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);margin-bottom:4px;font-weight:650}
.stat-hero .hero{font-size:var(--fs-hero);font-weight:650;letter-spacing:-.4px}
.stat-sub .subfig{font-size:var(--fs-heading);font-weight:650;color:#2c3139}
.panel-sub{margin-top:10px}
.sublabel{font-size:var(--fs-label);letter-spacing:.07em;text-transform:uppercase;
  color:var(--faint);font-weight:650;margin-bottom:4px}

.fig{cursor:help}
.fig-blank{color:#b0b5bd}
.fig-note{color:var(--muted);font-size:var(--fs-label)}
details.branch{padding-left:16px;border:0;margin:0}
details.branch>summary{cursor:pointer;margin-left:-16px;padding:3px 0;list-style:none}
details.branch>summary::-webkit-details-marker{display:none}
.leafrow{padding:3px 0}
.barnote{font-size:var(--fs-label);color:var(--faint);padding:0 0 3px 2px}
.barrow{display:flex;align-items:center;gap:9px;font-size:var(--fs-row)}
.disclosure{flex:0 0 14px;width:14px;text-align:center;color:var(--muted);
  font-size:var(--fs-label)}
.disclosure.has-children::before{content:"▸"}
details.branch[open]>summary .disclosure.has-children::before{content:"▾"}
.barrow .lbl{flex:0 0 190px;font-weight:600;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.barrow.d1 .lbl{font-weight:550}
.barrow.d2 .lbl,.barrow.d3 .lbl{font-weight:450;color:#2c3139}
.barrow.muted{color:#b0b5bd}
.barrow.muted .lbl{color:#b0b5bd;font-weight:500}
.barrow a{color:inherit;text-decoration:underline}
.track{flex:1;display:flex;height:11px}
.track .half{height:11px;background:#f0f1f3}
.track .half.neg{flex:0 0 26%;display:flex;justify-content:flex-end;
  border-right:1px solid #c9ced6}
.track .half.pos{flex:1}
.fill{display:block;height:100%;background:var(--ink)}
.barrow.muted .fill{background:#d7dade}
.fill.neg{background:var(--neg)}
.val{width:72px;text-align:right;font-weight:600}
.pct{width:56px;text-align:right;color:var(--muted)}
table.datatable{width:100%;border-collapse:collapse;font-size:var(--fs-row)}
table.datatable th{font-size:var(--fs-label);letter-spacing:.06em;
  text-transform:uppercase;color:var(--faint);font-weight:650;text-align:right;
  padding:0 8px 6px 0;border-bottom:1px solid var(--rule)}
table.datatable th.l{text-align:left}
table.datatable td{padding:5px 8px 5px 0;border-bottom:1px solid var(--rule-soft);
  text-align:right;vertical-align:top}
table.datatable td.l{text-align:left}
table.datatable td.note,table.datatable td.why{color:var(--muted);
  font-size:var(--fs-label);max-width:320px}
table.datatable td.state{color:var(--muted);font-size:var(--fs-label)}
.panelnote{font-size:var(--fs-label);color:var(--muted);margin:0 0 10px;max-width:760px}
.empty{color:var(--faint);font-size:var(--fs-row);font-style:italic}

.comingnote{max-width:560px;padding:28px 0}
.comingnote h2{font-size:var(--fs-heading);letter-spacing:.09em;
  text-transform:uppercase;color:#5a616b;font-weight:700;margin:0 0 10px;
  border-bottom:1px solid var(--ink);padding-bottom:5px}
.comingnote p{font-size:var(--fs-row);color:#2c3139;margin:0 0 8px}
.stagetag{font-size:var(--fs-label);color:var(--faint);text-transform:uppercase;
  letter-spacing:.06em}

/* The By-theme panel's own total row (page.theme_total()) — reuses the
   bare .disclosure/.track/.val/.pct widths .barrow already defines above,
   so its figure lines up in the same columns as every bar above it. No
   other panel may render this row — see _theme_total_row()'s own note. */
.themetotal{display:flex;align-items:center;gap:9px;font-size:var(--fs-row);
  font-weight:650;border-top:1px solid var(--ink);margin-top:3px;padding-top:7px}
.themetotal .lbl{flex:0 0 190px}

/* The note pop-up (2026-10-06 brief: "a pop-up window... and I want it to
   look better"). Hidden by default — same explicit [hidden] rule the tab
   strip and basis control already use (.tab-content[hidden]/.basis-
   block[hidden] above), rather than trusting the browser's own UA default
   for the attribute. */
.note-overlay[hidden]{display:none}
.note-source[hidden]{display:none}
.note-overlay{position:fixed;inset:0;z-index:50}
.note-backdrop{position:absolute;inset:0;background:rgba(20,23,26,.5)}
.note-dialog{position:relative;max-width:660px;max-height:82vh;margin:6vh auto 0;
  background:#fff;border-radius:6px;box-shadow:0 14px 44px rgba(0,0,0,.28);
  padding:28px 32px 32px;overflow-y:auto}
.note-dialog:focus{outline:none}
.note-close{position:absolute;top:12px;right:14px;width:30px;height:30px;
  border:0;background:transparent;font-size:var(--fs-heading);line-height:1;
  color:var(--muted);cursor:pointer;border-radius:4px}
.note-close:hover{background:#f0f1f3;color:var(--ink)}
#note-overlay-title{font-size:var(--fs-hero);font-weight:650;letter-spacing:-.3px;
  margin:0 0 16px;padding-right:30px}
#note-overlay-body{font-size:var(--fs-row);color:#1d2024;line-height:1.6}
#note-overlay-body h2{font-size:var(--fs-heading);letter-spacing:.07em;
  text-transform:uppercase;color:#5a616b;font-weight:700;margin:20px 0 8px;
  border-bottom:1px solid var(--rule);padding-bottom:4px}
#note-overlay-body h2:first-child{margin-top:0}
#note-overlay-body p{margin:0 0 10px}
#note-overlay-body ul,#note-overlay-body ol{margin:0 0 10px;padding-left:22px}
#note-overlay-body li{margin:2px 0}
#note-overlay-body code{background:#f0f1f3;padding:1px 4px;border-radius:3px}
#note-overlay-body blockquote{margin:0 0 10px;padding:1px 12px;
  border-left:3px solid var(--rule);color:var(--muted)}
#note-overlay-body hr{border:0;border-top:1px solid var(--rule);margin:14px 0}
#note-overlay-body a{color:inherit;text-decoration:underline;cursor:pointer}
#note-overlay-body table{width:100%;border-collapse:collapse;margin:0 0 12px}
#note-overlay-body th{text-align:left;font-size:var(--fs-label);color:var(--faint);
  text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--rule);
  padding:4px 8px 4px 0}
#note-overlay-body td{border-bottom:1px solid var(--rule-soft);
  padding:4px 8px 4px 0;vertical-align:top}
.notemissing{color:var(--muted);font-style:italic}
"""

_JS = """
document.querySelectorAll('input[name="tab"]').forEach(function (radio) {
  radio.addEventListener('change', function () {
    document.querySelectorAll('.tab-content').forEach(function (el) {
      el.hidden = el.dataset.tab !== radio.value;
    });
  });
});
document.querySelectorAll('input[name="basis"]').forEach(function (radio) {
  radio.addEventListener('change', function () {
    document.querySelectorAll('[data-basis]').forEach(function (el) {
      el.hidden = el.dataset.basis !== radio.value;
    });
  });
});

(function () {
  // The note pop-up. Three closing routes (close button, Escape, backdrop
  // click) and the focus handling his main request (2026-10-06) asked for
  // by name: focus moves INTO the overlay on open, and back to the bar
  // that opened it on close — never left stranded.
  var overlay = document.getElementById('note-overlay');
  var dialog = document.getElementById('note-dialog');
  var titleEl = document.getElementById('note-overlay-title');
  var bodyEl = document.getElementById('note-overlay-body');
  var closeBtn = document.getElementById('note-close');
  var backdrop = document.getElementById('note-backdrop');
  if (!overlay) { return; }
  var lastOpener = null;

  function wireInnerLinks() {
    // A link INSIDE a note, to another note this page also embedded,
    // already carries data-note-ref (ops/build_page.py's own
    // _rewrite_note_links()) — wired fresh every time the body's
    // innerHTML is replaced, since those are brand-new DOM nodes with no
    // listener of their own yet.
    bodyEl.querySelectorAll('a[data-note-ref]').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        renderInto(a.getAttribute('data-note-ref'));
        closeBtn.focus();
      });
    });
  }

  function renderInto(ref) {
    var src = document.querySelector('.note-source[data-ref="' + ref + '"]');
    if (src) {
      titleEl.textContent = src.getAttribute('data-title');
      bodyEl.innerHTML = src.innerHTML;
    } else {
      // Defensive only — every ref a link can point at was embedded by
      // ops/build_page.py's own _notes_overlay_block(); a missing FILE
      // already renders its plain-language message inside a real
      // .note-source, so this branch is never reached by ordinary use.
      titleEl.textContent = 'Not found';
      bodyEl.innerHTML = '<p class="notemissing">There is no note on file for this.</p>';
    }
    wireInnerLinks();
  }

  function openNote(ref, opener) {
    lastOpener = opener || null;
    renderInto(ref);
    overlay.hidden = false;
    overlay.setAttribute('aria-hidden', 'false');
    closeBtn.focus();
  }

  function closeNote() {
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');
    bodyEl.innerHTML = '';
    if (lastOpener) { lastOpener.focus(); }
    lastOpener = null;
  }

  document.querySelectorAll('a[data-note-ref]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      e.preventDefault();
      openNote(a.getAttribute('data-note-ref'), a);
    });
  });

  closeBtn.addEventListener('click', closeNote);
  backdrop.addEventListener('click', closeNote);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !overlay.hidden) { closeNote(); }
  });
})();
"""


def render(ctx: dict) -> str:
    """`page.context()` -> one self-contained HTML document.

    All three bases are rendered into this one file (D1/D6) — nothing here
    recomputes anything on a click; the control only toggles which
    pre-rendered block is visible. Every value that reaches the markup
    passes through `_esc()` or `_fig()`, both of which escape (trap 6).
    """
    bases = ctx["bases"]
    order = [b for b in page.BASES_ORDER if b in bases]
    age = ctx.get("price_age_days")

    blocks = [_basis_block(basis, bases[basis], i == 0, age)
              for i, basis in enumerate(order)]

    spread_tab = (
        _basis_control(bases, order)
        + '<p class="taxonomy-note">The instrument taxonomy for shorts, futures and '
          'options is still an open question in this project (REBUILD.md, Phase '
          '2c) &mdash; a negative bar here means negative market value, nothing '
          'more settled than that.</p>'
        + "".join(blocks)
    )

    tab_contents = [f'<div class="tab-content" data-tab="spread">{spread_tab}</div>']
    for tab in _TABS[1:]:
        heading, note, stage = _COMING_TABS[tab]
        tab_contents.append(
            f'<div class="tab-content" data-tab="{tab}" hidden>'
            + _coming_panel(heading, note, stage)
            + '</div>'
        )

    built_at = _esc(ctx.get("built_at", ""))
    age_html = _age_html(age)
    notes_overlay = _notes_overlay_block(ctx)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Portfolio Spread</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page">
  <div class="topbar">
    <span class="brand">Portfolio &middot; The Spread</span>
    <span class="meta">Built {built_at} &middot; prices {age_html}
      &middot; hover any figure to see the call that produced it.</span>
  </div>
  {_tab_strip()}
  {"".join(tab_contents)}
</div>
{notes_overlay}
<script>{_JS}</script>
</body>
</html>"""


def main(argv: list[str] | None = None) -> int:
    """Build `OUTPUT_PATH` and open it, unless told not to.

    The only write in this file is the one line below, to `OUTPUT_PATH` —
    `tests/test_page.py::
    test_nothing_in_the_page_layer_opens_a_file_for_writing` checks for
    exactly that and nothing more.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-open", action="store_true",
        help="Build ops/portfolio.html without opening it (for tests and "
             "headless runs).",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    ctx = page.context()
    html_text = render(ctx)
    OUTPUT_PATH.write_text(html_text, encoding="utf-8")
    elapsed = time.monotonic() - started

    size = OUTPUT_PATH.stat().st_size
    print(f"Built {OUTPUT_PATH} ({size:,} bytes) in {elapsed:.1f}s.")

    if not args.no_open:
        webbrowser.open(OUTPUT_PATH.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
