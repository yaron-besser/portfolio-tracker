"""Shaping the record into what the Spread tab shows. Computes nothing.

Spec D2: every number comes from a function in `src/`. If the page needs a
number nobody computes, it is added to the module that owns it and never
here. That is what stops the page and the chat from ever disagreeing — and
when they do, D12 means every figure on screen already names the call behind
it.

What this module is allowed to do: order rows, scale a bar against the widest
bar at its own depth, format money and percentages, route a caller's own
path overrides to whichever keyword name each `src/` function happens to use
for them, and assemble the honesty lines out of reports that already exist.
What it may not do: add, divide, or decide — the one exception being `_width`,
the single place a bar's length is computed, and the only function `tests/
test_page.py::test_shaping_computes_no_arithmetic_of_its_own` allows to
contain a division operator anywhere in this file.

Four traps this module was built to avoid, each one already caught a mistake
while this plan was being written (see `.superpowers/sdd/2026-10-06-spread-tab/
progress.md`):

  1. Depth comes from `parent`, not from `level` — `clusters.tree()`'s own
     docstring found this the hard way. `_tree_depths()` below walks `parent`
     for exactly that reason.
  2. A company row and a fund's unexplained remainder are different things —
     `company_bars()` filters to `exposure.KIND_COMPANY`; the rest reach the
     screen through `honesty()`'s own sentence, from
     `exposure.benchmark_companies().attrs["excluded_by_kind"]`, never summed
     here.
  3. An absent benchmark figure is not zero — `benchmark_company_rows()` and
     `sector_rows()` both print `""`, never `pct(0.0)`, for a blank
     `benchmark_pct`.
  4. A theme holding nothing under the selected basis is not an empty theme —
     `theme_bars()` reads `is_empty` straight off `clusters.tree()` rather
     than re-deriving it from `market_value == 0`.

Every public function here accepts `**paths`, in ONE vocabulary regardless of
which `src/` function it eventually calls: `positions_path`, `instruments_
path`, `clusters_path`, `compositions_path`, `holdings_path`. The private
`_*_paths()` helpers below translate that into whatever keyword name the
callee actually uses — `clusters.tree()` calls its own cluster store `path`,
`exposure.by_sector()` calls the ETF-composition store `path` too, and
`exposure.weight_residue()`/`symbol_collisions()` take a fixed, short
parameter list rather than `**kwargs` — so forwarding one dict unfiltered
would either collide two different meanings of `path` or raise on an
unexpected keyword. Routing a dict of path overrides is not arithmetic; it
never touches a dollar or a percentage.

⚠️ **Fix round 1 (2026-10-07) — three defects a reviewer found in the first
version, kept here so they are never repeated.**

1. **Critical.** `benchmark_company_rows()` used to relabel `exposure.
   benchmark_companies()`'s `below-floor` state to `only-mine`. That
   collided with a state `sector_rows()` genuinely emits under that exact
   name from a DIFFERENT module (`exposure.benchmark_sector_comparison()`),
   where `only-mine` is about as certain an absence as this module ever
   gets — a sector weighting has no small-weight floor to hide behind, so
   absence really is absence — but the NUMBER itself still comes back
   blank (`NaN`, drawn as a dash), same as every other absent figure; the
   certainty lives only in `state` (fix, 2026-10-07 final review, I8: this
   sentence used to call it "a confident zero", a claim about the number
   the code does not make). A company row below the index's STORED floor
   is a bound, never a confirmed absence. One token, two incompatible
   meanings, in one
   `context()`. Fixed by passing every state through UNCHANGED and never
   renaming one module's vocabulary into another's — see `context()`'s own
   note on why the two panels must never share a legend.
2. **Critical.** `sector_rows()` used to invent `state="only-mine"` for
   EVERY sector when the benchmark had simply never been fetched — the
   exact `fillna(0.0)`-shaped move `exposure.benchmark_sector_comparison()`
   exists to refuse, done one layer up instead. Fixed: when the comparison
   frame is empty, no state is invented at all (`""`), and the reason lives
   in `honesty()`, sourced from the frame's own `attrs["why"]`.
3. **Performance.** The expensive `exposure.by_company()` walk (resolving
   every holding's company key) was being computed independently by
   `benchmark_company_rows()`, `company_bars()`, `construction()` and
   `honesty()` — four times per basis, twelve times per `context()`, for
   data that never differs within one call. Fixed by computing it ONCE per
   basis in `context()` and passing it down as `mine=` — a parameter
   `exposure.benchmark_companies()` and `exposure.effective_bets()` both
   gained for exactly this (mirroring the pass-down `_compare_note()`/
   `non_equity_in_basis()` already used), so the cache lives in the module
   that owns the number, not in this one. No module-level cache was added
   here: the test suite monkeypatches loaders without changing any path,
   so a path-keyed cache outliving one call would hand the next test a
   stale frame.

⚠️ **Fix round 2 (2026-10-07) — a sentence that stated the opposite of his
own decision, found by reading the honesty block the way he will.**

1. **Critical.** The sector-disagreement sentence (`honesty()`, BMNR's real
   row) said the money was "counted under his own label" — unconditionally.
   Measured against the panel's own default: `exposure.by_sector()`
   defaults `classification` to `"provider"`, so a settled disagreement is
   counted under the VENDOR's label on screen, not his — exactly backwards
   from what the sentence told him. Fixed: the sentence now DERIVES which
   label wins from `classification` actually in force (`paths.get(
   "classification") or "provider"`, the same default `by_sector()` itself
   uses — never a second, independently guessed default), and
   `_by_sector_paths()`/`_sector_compare_paths()` now forward an explicit
   `classification` override so `sector_rows()` and this sentence can never
   describe two different panels.
2. **Important.** `instruments.price_warning()`'s own text ends with a
   shell command and no trailing punctuation; the next sentence followed it
   with only a space, so the line read as if the command took an argument
   named "Using" — precisely the line he would copy. Fixed with a period
   between the two sentences.
3. **Minor.** "Every dollar in positions.csv is classified..." named a file
   he has never opened. Said in his terms instead — the effect, not the
   mechanism (CLAUDE.md) — with no filename in either branch of that
   sentence.
4. **Minor.** The S&P-exclusion sentence used to print all three excluded
   kinds even when one was an exact, structural zero (`pure-equity`'s cash
   exclusion is $0.00 by construction, measured live). A zero is not
   information: each zero component is omitted, and the whole sentence is
   omitted if every component is zero.

⚠️ **Fix round 3 (2026-10-07) — the last round; two findings, and a ruling
that is deliberately NOT a fix.**

1. **Minor.** The not-GICS sentence named the database column
   (`instruments.provider_sector`) in parentheses — a mechanism name he
   never sees anywhere else. Dropped; the sentence says everything it
   needs to without it.
2. **Ruling, not a fix.** The weight-residue sentences name
   `etf_compositions.csv`/`etf_holdings.csv` by filename, and a re-reviewer
   flagged that as the same kind of mechanism leak as (1). It is not: those
   two filenames are already part of his vocabulary — `ops/data_gaps.py`
   prints them to him by name on every gap report, and `/market` names
   them in its own output — while a database column is not. Left in,
   on purpose, so a later reader does not "tidy" this into a third
   mechanism-name removal.

This round also moved three of this file's own tests off a per-test
`page.context()` rebuild onto one module-scoped fixture (`tests/
test_page.py::real_context`) — nothing in `src/page.py` itself changed as
a result, and `page.context()`'s own assembly time is unaffected; only how
many times the test suite paid for it changed.

⚠️ **2026-10-07 addition — the note pop-up, and the By-theme total.** Two
more things this module hands the renderer, both following the same D2
rule as everything above:

1. `embedded_notes()` reads, once each, every note a rendered bar actually
   links to (`_bar_hrefs()`'s own set, derived from the hrefs `header()`/
   `theme_bars()`/`company_bars()` already produced — never a directory
   scan) and ships the raw text under `context()["notes"]`. Turning that
   text into HTML is rendering, and happens in `ops/build_page.py`, never
   here; reading it is `notes.read_for_overlay()`'s job, never opened by
   this file directly.
2. `theme_total()` is `book.nav(basis)` a second time, under its own name,
   for the By-theme panel alone — never a sum of `theme_bars()`'s own rows.
   See its own docstring for why this one panel, and no other, gets one.
   `theme_coverage_pct()` (pre-push review 2026-10-07, finding 3) sits
   beside it: what `theme_bars()`'s own rows actually add up to, as a % of
   that same total — `clusters.tree_coverage_pct()`'s number, never a sum
   taken here, so the one row meant to reveal a shortfall stays capable of
   revealing one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

import pandas as pd

from src import book

BASES_ORDER = book.BASES

#: The label `exposure.by_sector()` rolls cash, FX and T-bill funds (SGOV)
#: up to. Named once so `header()`'s prose and its one lookup can never
#: drift apart — the only two places this string appears in this file.
_CASH_AND_TBILLS_SECTOR = "Fixed Income / Cash"


@dataclass(frozen=True)
class Figure:
    """One number on the page, and the call that produced it (D12)."""
    value: float
    text: str
    source: str


@dataclass(frozen=True)
class Bar:
    """One ranked bar. `width` is 0-100 of the widest bar at its own depth."""
    label: str
    figure: Figure
    pct: float
    width: float
    depth: int
    negative: bool
    muted: bool
    href: str
    note: str


class BenchmarkCompanyRows(NamedTuple):
    """The shape `benchmark_company_rows(with_note=True)` returns.

    Not a bare `(rows, note)` tuple — found by review, 2026-10-07: that
    shape reads at a glance like `(rows, a row count)` rather than `(rows,
    the basis sentence)`. Still unpacks exactly like a plain tuple
    (`rows, note = ...`), so every existing caller is unaffected; the name
    is the only thing this adds.
    """
    rows: list[dict]
    note: str


def _maybe_figure(value: float | None, text_fn, source: str):
    """A `Figure` when `value` is a real number, `""` when it is `None` or
    NaN.

    An absent figure is not a number (trap 3) — rendering it as a `Figure`
    with a made-up value would give a renderer something to compare
    against the chat that was never really computed. `text_fn` is `money`
    or `pct`, whichever this value is.
    """
    if value is None or value != value:
        return ""
    v = float(value)
    return Figure(value=v, text=text_fn(v), source=source)


def money(value: float) -> str:
    """Dollars, tabular, with a minus sign and never a colour (D7, D9).

    Same decimal places and digit grouping on both sides of zero — only the
    leading `-` differs — so a column of these lines up, and nothing here
    ever returns a colour code or markup for the renderer to key a colour
    off of. P&L stays grey because this function never says otherwise.
    """
    v = float(value)
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def pct(value: float) -> str:
    """A percentage, already computed elsewhere — this only prints it.

    Never called on a blank/NaN figure: an absent benchmark weight prints as
    `""` at the call site (trap 3), never as `pct(0.0)`, which would say the
    index holds none of something nobody even looked at.
    """
    v = float(value)
    sign = "-" if v < 0 else ""
    return f"{sign}{abs(v):,.2f}%"


def _width(size: float, widest: float) -> float:
    """A bar against the widest bar AT ITS OWN DEPTH.

    Scaling a holding against the largest theme makes every holding a
    sliver, and the panel's whole job is comparing similar sizes — which is
    why a treemap was rejected (D5). `size` is always a magnitude (never
    signed) by the time it reaches here; direction is `Bar.negative`'s job,
    not this one's — repeating it in the bar's length would make the length
    unusable for ranking, the same split `book.holdings()`'s `size` column
    and `exposure.effective_bets()` both make for the same reason.
    """
    if not widest:
        return 0.0
    return round(size / widest * 100, 2)


def _href(note_ref: str) -> str:
    """A note_ref, relative to the built file's directory (`ops/`).

    Every `note_ref` on file is stored relative to the repository root
    (e.g. a path two directories deep under notes). `ops` sits one directory
    below the root, so the built page's own link is that same path with one
    `..` step in front of it. Blank in, blank out — an empty note_ref means
    nothing is written yet, and a bar he cannot click is itself information
    (no underline), never a link to a page that does not exist.

    Stage-2 stand-in, named so a later session does not read it as finished:
    until the Holdings tab exists, a ticker's bar links straight to its own
    note file, never to a stock page.
    """
    ref = str(note_ref or "").strip()
    return f"../{ref}" if ref else ""


# ─── routing: one path vocabulary in, each callee's own kwargs out ─────────
#
# Every function below selects and renames; none of it is arithmetic, and
# none of it decides what a number means. A caller never has to know that
# `clusters.tree()` calls its cluster store `path` while `exposure.
# by_company()` calls the same kind of override `clusters_path`.

def _pick(paths: dict, *keys: str) -> dict:
    return {k: paths[k] for k in keys if paths.get(k) is not None}


def _book_paths(paths: dict) -> dict:
    """What `book.lots()/holdings()/nav()/biggest()/by_currency()` accept."""
    return _pick(paths, "positions_path", "instruments_path", "clusters_path")


def _unclassified_paths(paths: dict) -> dict:
    """`book.unclassified()` alone has no `clusters_path` parameter."""
    return _pick(paths, "positions_path", "instruments_path")


def _pnl_paths(paths: dict) -> dict:
    """`book.lot_cost_usd()`/`uncosted_lots()`/`fx_fallback_lots()`/
    `unrealized_pnl()`/`total_pnl()` all take exactly this quartet — no
    `clusters_path` (P&L has no basis to be a share of, so it never reads
    the cluster store at all)."""
    return _pick(paths, "positions_path", "instruments_path",
                 "transactions_path", "corporate_actions_path")


def _realized_paths(paths: dict) -> dict:
    """`book.realized_pnl()` takes only `transactions_path` — a bare
    positional there, so this names it rather than forwarding the dict."""
    return _pick(paths, "transactions_path")


def _cash_flow_paths(paths: dict) -> dict:
    """`book.excluded_cash_flows()` takes `cash_transactions_path` and
    `borrow_fees_path` — neither shared with any other function this
    module routes for."""
    return _pick(paths, "cash_transactions_path", "borrow_fees_path")


def _fund_paths(paths: dict) -> dict:
    """What `exposure.by_company()`, `benchmark_companies()`,
    `sector_disagreements()`, `company_coverage()`, `effective_bets()`,
    `symbol_collisions()` and `clusters.overlap()` all accept directly —
    the one place in this module all four path kinds share one name with
    their callees, so no renaming is needed."""
    return _pick(paths, "positions_path", "instruments_path",
                 "clusters_path", "holdings_path")


def _tree_paths(paths: dict) -> dict:
    """`clusters.tree()` names its own cluster store `path`, not
    `clusters_path` — translated here once rather than by every caller."""
    out = _pick(paths, "positions_path", "instruments_path")
    if paths.get("clusters_path") is not None:
        out["path"] = paths["clusters_path"]
    return out


def _by_sector_paths(paths: dict) -> dict:
    """`exposure.by_sector()`'s parameter list — `path` for the
    ETF-composition store, `positions_path`, `classification`
    (`"provider"`/`"mine"`), and now `instruments_path`/`clusters_path`.
    D13's default is the vendor's, which `by_sector()` itself already
    defaults to — `classification` is only forwarded as an EXPLICIT
    override, never invented, so `honesty()` and `sector_rows()` can be
    asked to agree about which classification is in force (fix round 2,
    Critical A: a sentence that hard-coded "the vendor's" was backwards the
    moment it was checked against the panel's own default).

    🚨 **Fix, 2026-10-07 final review (I5).** `instruments_path`/
    `clusters_path` used to stop here with a comment saying `by_sector()`
    had no such parameters — true until this same review, and false the
    moment it gained them. Left unfixed, `by_sector()` would have accepted
    the two but this router would still have swallowed them before they
    ever arrived — the identical bug, one call frame higher. No
    `holdings_path` — `by_sector()` still has no such parameter."""
    out = _pick(paths, "positions_path", "classification",
               "instruments_path", "clusters_path")
    if paths.get("compositions_path") is not None:
        out["path"] = paths["compositions_path"]
    return out


def _coverage_paths(paths: dict) -> dict:
    """`exposure.coverage()` takes exactly `path` + `positions_path` — NOT
    `classification` (sector-breakdown QUALITY is classification-agnostic,
    and `coverage()` has no such parameter to forward it to)."""
    out = _pick(paths, "positions_path")
    if paths.get("compositions_path") is not None:
        out["path"] = paths["compositions_path"]
    return out


def _sector_compare_paths(paths: dict) -> dict:
    """`exposure.benchmark_sector_comparison()` reads BOTH the compositions
    store (as `path`) and the fund-paths quartet (for its own internal
    `by_company()` call, which sizes the non-equity slice in its basis
    note) — so this is the union of the two routers above, `classification`
    included so this call agrees with `sector_rows()`'s own."""
    out = _fund_paths(paths)
    if paths.get("compositions_path") is not None:
        out["path"] = paths["compositions_path"]
    if paths.get("classification") is not None:
        out["classification"] = paths["classification"]
    return out


def _residue_paths(paths: dict) -> dict:
    """`exposure.weight_residue()` takes exactly `path`, `holdings_path`,
    `positions_path` and `basis` — no `instruments_path`, no `**kwargs`."""
    out = _pick(paths, "positions_path", "holdings_path")
    if paths.get("compositions_path") is not None:
        out["path"] = paths["compositions_path"]
    return out


def _benchmark_weights_paths(paths: dict) -> dict:
    """`exposure.benchmark_weights()` takes only `ticker` and `path` — no
    position of his own enters it at all, since it is the INDEX's own
    weights, not a comparison. Added, fix I4 (2026-10-07 final review), so
    `honesty()` can read the same `as_of` the sector/company comparisons
    are themselves built from."""
    out = {}
    if paths.get("compositions_path") is not None:
        out["path"] = paths["compositions_path"]
    return out


def _price_paths(paths: dict) -> dict:
    """`instruments.price_age_days()`/`price_warning()` call the
    positions store `path`, not `positions_path`."""
    out = {}
    if paths.get("positions_path") is not None:
        out["path"] = paths["positions_path"]
    return out


def _instrument_lookup(paths: dict) -> tuple[dict, dict]:
    """instrument_id -> (name, note_ref), read once. A lookup, not a figure —
    the project's own routing rule for links: "a holding bar's href is its
    instrument's note_ref" (Task 10 brief)."""
    from src import instruments as instruments_mod

    inst = instruments_mod.load(
        paths.get("instruments_path") or instruments_mod.INSTRUMENTS_PATH
    ).fillna("")
    names = dict(zip(inst["instrument_id"], inst["name"]))
    refs = dict(zip(inst["instrument_id"], inst["note_ref"]))
    return names, refs


# ─── bars ────────────────────────────────────────────────────────────────

def _build_bars(items: list[dict], source: str) -> list[Bar]:
    """Turn plain dicts into ranked `Bar`s, width scaled per `depth` (D5).

    Each item carries `label`, `value` (signed), `pct` (already computed by
    whichever `src/` function produced it), and optionally `depth` (default
    0 — a flat list, like the top five or the single-name panel), `href`,
    `note`, `muted`. The widest-at-this-depth lookup, and therefore every
    call to `_width()`, happens here and only here, so a theme's own bar
    never becomes the denominator for a holding under some OTHER theme at
    the same nominal depth either — see `_width()`'s own docstring.
    """
    if not items:
        return []
    widest_by_depth: dict[int, float] = {}
    for it in items:
        d = it.get("depth", 0)
        widest_by_depth[d] = max(widest_by_depth.get(d, 0.0), abs(it["value"]))
    bars = []
    for it in items:
        v = float(it["value"])
        d = it.get("depth", 0)
        bars.append(Bar(
            label=it["label"],
            figure=Figure(value=v, text=money(v), source=source),
            pct=float(it["pct"]),
            width=_width(abs(v), widest_by_depth[d]),
            depth=d,
            negative=v < 0,
            muted=bool(it.get("muted", False)),
            href=it.get("href", "") or "",
            note=it.get("note", "") or "",
        ))
    return bars


def header(basis: str = "everything", **paths) -> dict:
    """NAV, cash and T-bills, and the biggest five. Spec panel 1.

    `cash` reads `exposure.by_sector()`'s own `_CASH_AND_TBILLS_SECTOR` row —
    cash and FX balances plus any fund (SGOV, a T-bill ETF) whose vendor
    sector rolls up the same way — never a second definition of "what counts
    as cash" typed out here. Under `pure-equity`, where cash and T-bills are
    stripped from the basis entirely, that row genuinely does not exist and
    the true answer is zero, not a gap.
    """
    from src import exposure

    book.check_basis(basis)
    book_paths = _book_paths(paths)
    nav_value = book.nav(basis, **book_paths)
    nav_figure = Figure(value=nav_value, text=money(nav_value),
                        source="book.nav()")

    sector = exposure.by_sector(basis=basis, **_by_sector_paths(paths))
    cash_value = 0.0
    if not sector.empty:
        row = sector[sector["sector"] == _CASH_AND_TBILLS_SECTOR]
        if not row.empty:
            cash_value = float(row["market_value"].iloc[0])
    cash_figure = Figure(value=cash_value, text=money(cash_value),
                         source="exposure.by_sector()")

    names, refs = _instrument_lookup(paths)
    big = book.biggest(5, basis, **book_paths)
    items = [{
        "label": names.get(r["instrument_id"], r["instrument_id"]) or r["instrument_id"],
        "value": float(r["market_value"]),
        "pct": float(r["pct_of_basis"]),
        "href": _href(refs.get(r["instrument_id"], "")),
    } for _, r in big.iterrows()]
    biggest_bars = _build_bars(items, source="book.biggest()")

    return {
        "nav": nav_figure,
        "cash": cash_figure,
        "biggest": biggest_bars,
        "state_line": book.words(basis),
    }


def _tree_depths(rows: list[dict]) -> list[int]:
    """Nesting depth per `clusters.tree()` row, walking `parent` (trap 1).

    Not a figure `clusters.tree()` returns, and not re-derivable from
    `level` — a top-level theme and a theme nested inside a group are BOTH
    `level="cluster"`, and only `parent` (blank for the first, the group's
    id for the second) tells them apart. This is purely a layout count for
    indentation and bar grouping, built the exact way the brief names: "a
    renderer indents by walking `parent` up until it is blank."
    """
    depth_by_cluster: dict[str, int] = {}
    depths = []
    for r in rows:
        if r["level"] == "holding":
            d = depth_by_cluster.get(r["cluster_id"], 0) + 1
        else:
            parent = r["parent"]
            d = 0 if not parent else depth_by_cluster.get(parent, 0) + 1
            depth_by_cluster[r["cluster_id"]] = d
        depths.append(d)
    return depths


def theme_bars(basis: str = "everything", **paths) -> list[Bar]:
    """The spread by theme, three levels deep, ranked. Spec panel 2.

    Row order is `clusters.tree()`'s own drawing order — group, then its
    children, then their holdings, every family by its own size — and this
    function never re-sorts it (its own docstring: "page.py shapes and never
    re-sorts"). `is_empty` becomes `Bar.muted` directly (trap 4): a theme
    holding nothing under THIS basis is not the same as one holding nothing
    anywhere, and `tree()` already tells the two apart.
    """
    from src import clusters

    book.check_basis(basis)
    df = clusters.tree(basis=basis, **_tree_paths(paths))
    if df.empty:
        return []
    rows = df.to_dict("records")
    depths = _tree_depths(rows)
    items = [{
        "label": r["label"],
        "value": float(r["market_value"]),
        "pct": float(r["pct_of_basis"]),
        "depth": d,
        "muted": bool(r["is_empty"]),
        "href": _href(r["note_ref"]),
    } for r, d in zip(rows, depths)]
    return _build_bars(items, source="clusters.tree()")


def theme_total(basis: str = "everything", **paths) -> Figure:
    """The By-theme panel's own total row. Spec panel 2, 2026-10-07 addition.

    `book.nav(basis)` — the exact same call `header()` already makes for
    `"nav"` — never a sum of `theme_bars()`'s own rows: this module may not
    add (D2), and that rule is the whole reason this figure is trustworthy.
    Recomputed here rather than threaded down from `header()`'s result so
    every function in this file stays independently callable with its own
    `**paths`, the same contract every other function here already keeps.

    Why it earns a place at all: his theme rows are SUPPOSED to add to
    exactly this number, and that property broke twice while this very
    panel was being built — once when money belonging to no live theme
    vanished from the totals (before `clusters.tree()`'s `UNFILED_LABEL`
    bucket existed), once from a fund's hand-entered rounding overstating
    its own weight. A visible total turns a tested property into one he
    can see on every build, not only when a test happens to catch it.

    Deliberately NOT added anywhere else — `sector_rows()`'s rows compare
    against the S&P and do not meaningfully total to anything; `benchmark_
    company_rows()`'s total would only restate this same basis figure a
    second time; `company_bars()` is filtered to real companies only
    (trap 2) and does not cover the basis; `overlap_rows()`'s pairs are not
    a partition, so summing them would double-count shared money on
    purpose. One basis, one total, in one panel.
    """
    book.check_basis(basis)
    value = book.nav(basis, **_book_paths(paths))
    return Figure(value=value, text=money(value), source="book.nav()")


def theme_coverage_pct(basis: str = "everything", **paths) -> Figure:
    """What `theme_bars()`'s own rows actually add up to. Spec panel 2,
    added pre-push review 2026-10-07 (finding 3), beside `theme_total()`.

    `clusters.tree_coverage_pct()` — never a sum taken here, which D2
    forbids and which is exactly the bug this figure exists to catch:
    `ops/build_page.py` used to hard-code this percentage as 100, the one
    row on the page that could have revealed a defect letting real money
    fall out of every row `theme_bars()` draws, and a constant cannot
    reveal anything. Reads 100.0 when the tree is complete; anything else
    is the fact this figure exists to make visible rather than assume.
    """
    from src import clusters

    book.check_basis(basis)
    value = clusters.tree_coverage_pct(basis=basis, **_tree_paths(paths))
    return Figure(value=value, text=pct(value),
                 source="clusters.tree_coverage_pct()")


def _bar_hrefs(bases: dict) -> set[str]:
    """Every distinct note ref a rendered bar actually links to, across
    every basis — the set `embedded_notes()` reads, and the ONLY set it
    reads (never a directory scan, per the 2026-10-07 brief).

    `Bar.href` is already `_href()`'s own `"../" + ref` (or `""`); this
    only strips that prefix back off. The three places a `Bar` carries an
    `href` today are named here directly rather than walked generically —
    `header()`'s biggest five, `theme_bars()`, and `company_bars()` — so a
    future bar-shaped panel that does NOT carry a clickable note silently
    stays out of this set rather than being guessed at.
    """
    refs: set[str] = set()
    for data in bases.values():
        for bars in (data["header"]["biggest"], data["theme_bars"],
                    data["company_bars"]):
            for bar in bars:
                if bar.href:
                    ref = bar.href[3:] if bar.href.startswith("../") else bar.href
                    refs.add(ref)
    return refs


def embedded_notes(bases: dict) -> dict:
    """Every note a rendered bar links to, read once, keyed by its own ref.

    The page is one self-contained file that can make no request once
    built (D1), so a note's text has to be embedded at build time — this
    is the one place that decides WHICH notes that is, from `_bar_hrefs()`
    alone, never from a directory scan. Reading each one is `notes.
    read_for_overlay()`'s job; turning `.text` into HTML is rendering and
    happens in `ops/build_page.py`, never here — this function only reads
    and relays, the same role `_instrument_lookup()` already plays for a
    name and a note_ref.
    """
    from src import notes as notes_mod

    return {ref: notes_mod.read_for_overlay(ref) for ref in sorted(_bar_hrefs(bases))}


def sector_rows(basis: str = "everything", **paths) -> list[dict]:
    """Look-through sector exposure beside the S&P's own weight. Spec panel 3.

    `exposure.by_sector()` for his own shape (already basis-aware, already
    vendor-classified per D13); `exposure.benchmark_sector_comparison()` for
    the index's side of the same join — never a hand-rolled outer join and
    `fillna(0.0)`, which is exactly the move that once read 152 real S&P
    constituents as a confident zero (trap 3, one panel over).

    ⚠️ **This panel's `state` vocabulary is NOT `benchmark_company_rows()`'s
    (fix round 1, A).** It comes straight from `exposure.
    benchmark_sector_comparison()` — `both` / `only-mine` / `unclassified` /
    `only-index` — and `only-mine` HERE is about as certain an absence as
    this module ever gets: a sector weighting is schema-checked to sum to
    the whole fund, so there is no small-weight floor to hide a sector
    behind the way there is a company. **That certainty used to be written
    here as "a confident zero" — fixed, 2026-10-07 final review (I8): the
    code never claimed that.** `benchmark`/`difference` still come back
    blank (`""`) for an `only-mine` row, the same shape as any other absent
    figure — a bare `0.0` would be pixel-identical to the benchmark never
    having been fetched at all (every row reads `state=""` then, never
    `only-mine`). `note` is where the certainty actually reaches the
    screen: a short caption, present only on an `only-mine` row, blank
    everywhere else — including the `""` rows it has to stay
    distinguishable from. A renderer must read this column (and `note`)
    against THIS panel's own caption, never the company panel's — see
    `context()`'s own note.

    🚨 **`unclassified` is not `only-mine` — pre-push review 2026-10-07,
    finding 1.** `exposure.UNCLASSIFIED_SECTOR` ("Other") is money
    `by_sector()` could not place at all — an ETF with no composition row
    on file, or a stock whose vendor sector carries no date — never a
    sector the index structurally cannot hold. The two used to share one
    state and one caption: an "Other" row printed the same "SPY is an
    all-equity fund, so zero here is certain" sentence as Crypto/Cash,
    which is backwards — this money's sector is UNKNOWN, the opposite of
    certain. `note` says so instead, for this state alone. The mirror
    case — a real sector the index holds that `by_sector()` shows no row
    for at all (`only-index`, built back in further down) — also stops
    claiming a confirmed zero the moment this panel's own `unclassified`
    row carries real money: that money could be sitting in exactly this
    sector, unseen.

    ⚠️ **Fix round 1, B.** When the comparison frame is empty — the
    benchmark was never fetched at all — NO state is invented for any row.
    The first version of this function fell back to `"only-mine"` for every
    sector in that case, which is exactly the `fillna(0.0)`-shaped move
    `benchmark_sector_comparison()` exists to refuse, committed one layer
    up instead. `"benchmark"`/`"difference"` stay `""`, and `"state"` is
    `""` too — the reason the comparison could not be made at all lives in
    `honesty()`, sourced from the frame's own `attrs["why"]`, never guessed
    here.

    ⚠️ **Fix, 2026-10-07 final review (I3).** `cmp` can carry a sector the
    index reports and he does not (`state="only-index"`) that `mine.
    iterrows()` never visits — `by_sector()` produces no row at all for a
    sector he has zero exposure to, so the loop over HIS rows alone simply
    never reaches it. That was the one case this panel exists to show —
    "the index has this much and you have nothing" — and it was the one
    row silently dropped. Added back in a second pass over `cmp`, with his
    side stated as a true `0.0`, never left blank: unlike a company, his
    own absence from a sector IS a confirmed zero (no small-weight floor to
    hide behind), the one case this module may state as a plain fact
    rather than call a function for — `by_sector()`'s own COMPLETE
    classification is what makes "no row" mean "nothing", not a guess made
    here.

    🚨 **Fix, residual review (N3).** The four zeros these rows carry used
    to be stamped `source="exposure.by_sector()"` — the same label every
    OTHER row in this function earns by actually reading a value out of
    that frame. These four never do: `by_sector()` returned no row at all
    for this sector, which is the entire premise above. Hovering one of
    these cells therefore named a function that produced nothing, and the
    row rendered with a blank `note`, so nothing on screen explained the
    zero either. Both corrected below: the source names THIS function and
    the reasoning above, never a module that was not actually called for
    it, and the note states the same thing a reader would otherwise have
    to reconstruct from the column headers alone.
    """
    from src import exposure

    book.check_basis(basis)
    mine = exposure.by_sector(basis=basis, **_by_sector_paths(paths))
    if mine.empty:
        return []
    cmp = exposure.benchmark_sector_comparison(basis=basis,
                                               **_sector_compare_paths(paths))
    cmp_by_sector = ({} if cmp.empty else
                     {r["sector"]: r for _, r in cmp.iterrows()})

    sector_source = "exposure.by_sector()"
    compare_source = "exposure.benchmark_sector_comparison()"
    rows = []
    seen_sectors = set()
    for _, r in mine.iterrows():
        sector = r["sector"]
        seen_sectors.add(sector)
        c = cmp_by_sector.get(sector)
        if c is not None:
            bench_pct, diff, state = c["benchmark_pct"], c["difference"], c["state"]
        else:
            # Either the comparison was never built at all (`cmp.empty`) or
            # — defensively — this sector has no counterpart row in it.
            # Either way: no state is invented. Absent is not "only-mine".
            bench_pct, diff, state = None, None, ""
        value = float(r["market_value"])
        if state == "only-mine":
            note = ("the index cannot hold this sector at all — SPY is an "
                    "all-equity fund — so zero here is certain, not a gap")
        elif state == "unclassified":
            # Fix, pre-push review 2026-10-07 (finding 1). This is
            # `exposure.UNCLASSIFIED_SECTOR` ("Other") — never the
            # structural-zero case above. The money is real; its SECTOR is
            # what is unknown (an ETF with no composition row on file, or a
            # stock whose vendor sector carries no date — `exposure.
            # coverage()` and the sector-disagreement lines in `honesty()`
            # name which).
            note = ("this money's sector could not be classified yet — an "
                    "ETF with no breakdown on file, or a stock whose "
                    "vendor sector carries no date — so its sector is "
                    "unknown, not a confirmed zero for any sector")
        else:
            note = ""
        rows.append({
            "sector": sector,
            "value": Figure(value=value, text=money(value), source=sector_source),
            "pct": _maybe_figure(float(r["pct_of_nav"]), pct, sector_source),
            "direct_value": _maybe_figure(float(r["direct_value"]), money, sector_source),
            "estimated_pct": _maybe_figure(float(r["pct_estimated"]), pct, sector_source),
            "benchmark": _maybe_figure(bench_pct, pct, compare_source),
            "difference": _maybe_figure(diff, pct, compare_source),
            "state": state,
            "note": note,
        })

    # Fix, 2026-10-07 final review (I3) — see the docstring above.
    # Fix, residual review (N3): `source` here used to read `sector_source`
    # ("exposure.by_sector()") — the label every other row earns by reading
    # a value that function actually returned. These four never do:
    # by_sector() returned no row at all, which is the whole premise of
    # this block. Attributed instead to this reasoning, never to a module
    # that was not actually consulted for the number.
    confirmed_zero_source = (
        "no row in exposure.by_sector() for this sector — confirmed a "
        "true zero, not a gap, because exposure.benchmark_sector_"
        "comparison()'s own sector classification is complete"
    )
    # Fix, pre-push review 2026-10-07 (finding 1). Read straight off the
    # `unclassified` row this SAME frame may carry (built above, from
    # `exposure.UNCLASSIFIED_SECTOR`) — never a second string match on
    # "Other" out here. `0.0`/`None` both mean "no unclassified money",
    # which is also what a `cmp.empty` or classification-free `mine`
    # produces, so one `or 0.0` covers every shape.
    unclassified_pct = next(
        (c["mine_pct"] for c in cmp_by_sector.values()
         if c["state"] == "unclassified"), 0.0) or 0.0
    if not cmp.empty:
        for sector, c in cmp_by_sector.items():
            if sector in seen_sectors or c["state"] != "only-index":
                continue
            if unclassified_pct:
                # A sector the index holds and `by_sector()` shows no row
                # for is no longer a confirmed zero the moment real money
                # sits unclassified: it could belong here, unseen — the
                # exact contradiction the review reproduced (a new
                # real-estate ETF's money read `Other`, captioned certain,
                # while `Real Estate` itself read a confirmed `$0.00` one
                # row down).
                zero_source = (
                    "no row in exposure.by_sector() for this sector, but "
                    "exposure.benchmark_sector_comparison() also carries "
                    "unclassified money this call — not confirmed"
                )
                note = (
                    f"the index holds this sector; none of his classified "
                    f"money is counted under it, but {pct(unclassified_pct)} "
                    f"of his basis has not been classified into any sector "
                    f"at all — some of that could belong here, so this is "
                    f"not a confirmed zero"
                )
            else:
                zero_source = confirmed_zero_source
                note = ("the index holds this sector and he holds none "
                        "of it at all — exposure.by_sector() has no row "
                        "for it, confirmed a true zero rather than a gap "
                        "by exposure.benchmark_sector_comparison()'s own "
                        "classification")
            rows.append({
                "sector": sector,
                "value": Figure(value=0.0, text=money(0.0), source=zero_source),
                "pct": Figure(value=0.0, text=pct(0.0), source=zero_source),
                "direct_value": Figure(value=0.0, text=money(0.0), source=zero_source),
                "estimated_pct": Figure(value=0.0, text=pct(0.0), source=zero_source),
                "benchmark": _maybe_figure(float(c["benchmark_pct"]), pct, compare_source),
                "difference": _maybe_figure(c["difference"], pct, compare_source),
                "state": c["state"],
                "note": note,
            })
    return rows


def benchmark_company_rows(basis: str = "everything", limit: int = 30,
                           with_note: bool = False,
                           mine: pd.DataFrame | None = None, **paths):
    """His true exposure to each company beside the index's. Spec panel 4.

    ⚠️ **`state` is `exposure.benchmark_companies()`'s OWN vocabulary,
    passed through UNCHANGED — `both` / `only-index` / `below-floor` /
    `unknown` (fix round 1, Critical A).** The first version of this
    function relabelled `below-floor` to `only-mine`, which collided with a
    state `sector_rows()` genuinely emits under that same name from a
    DIFFERENT module — there, `only-mine` means a sector the index cannot
    hold at all by construction (no small-weight floor to hide a sector
    behind, unlike a company); here, a company below the index's STORED
    floor is a bound, never a confirmed absence. One token with two
    incompatible meanings in one `context()` is worse than two panels that
    merely look similar — never rename a module's vocabulary again; see
    `context()`'s own note.

    Three facts that look alike and are not (trap 3, named by the brief for
    this exact panel): `benchmark=""` with `state="below-floor"` means the
    index's SOURCE claims the whole fund and his holding is simply below the
    small-weight floor it stores down to — a bound, never a confirmed
    absence, and `note` carries that bound in words. `state="unknown"` means
    the index's own source never claimed completeness either, so there is
    not even a bound — `note` says `"Unknown — "` plus the reason, so a
    reader of the note column, not just the state column, can tell the two
    apart. `state="only-index"` **used to be documented here as "a real,
    confirmed zero: a fact about his construction, not a missing number" —
    wrong, fixed 2026-10-07 final review (I2).** His side is `by_company()`,
    which is itself a floor for most held funds; a company reached only
    through a fund below ITS OWN floor reads identically to one he truly
    holds none of. `is_floor` on an `only-index` row says which case this
    is; `note` carries `exposure.ONLY_INDEX_FLOOR_WORDING` whenever it is
    True, the same way `below-floor`'s own bound reaches `note` — read the
    column, never assume blank cells mean confirmed zero.

    🚨 **`state="both"` can ALSO be a floor — fix, pre-push review
    2026-10-07 (finding 2).** `exposure.benchmark_companies()` now withholds
    `difference` (never `benchmark_pct`, which is exact on a `both` row
    either way) whenever his own side came through a fund whose stored
    holdings stop short of the whole thing — `difference_floor` carries the
    safe bound instead, same shape as `below-floor`'s own exact/bound split.
    `is_floor` here is per-COMPANY (unlike `only-index`'s call-wide
    `any_mine_floor`), so it is read off each row directly rather than
    through `wording`/`STATE_WORDING`, which stays `""` for `both` — the
    caveat text is `exposure.BOTH_FLOOR_WORDING`.

    `mine`, when given, is an already-computed `by_company(basis, **paths)`
    frame, passed straight through to `exposure.benchmark_companies()`
    (fix round 1, I) — `context()` computes it once per basis rather than
    letting every panel that needs it recompute the same walk.
    """
    from src import exposure

    book.check_basis(basis)
    out = exposure.benchmark_companies(basis=basis, limit=limit, mine=mine,
                                       **_fund_paths(paths))
    wording = out.attrs.get("state_wording", {})
    source = "exposure.benchmark_companies()"

    rows = []
    for _, r in out.iterrows():
        state = r["state"]
        is_floor = bool(r["is_floor"])
        caveat = wording.get(state, "")
        if state == "unknown":
            note = f"Unknown — {caveat}" if caveat else "Unknown — not knowable from today's data."
        elif state == "both" and is_floor:
            # Fix, pre-push review 2026-10-07 (finding 2). `is_floor` on a
            # `both` row is per-company, never a single fact for the whole
            # call the way `only-index`'s `any_mine_floor` is — so this is
            # read off THIS row, not off `wording` (which stays "" for
            # `both`, see `exposure.STATE_WORDING`'s own note).
            note = exposure.BOTH_FLOOR_WORDING
        else:
            note = caveat
        rows.append({
            "key": r["key"], "label": r["label"],
            "issuer_group": r["issuer_group"],
            "mine": _maybe_figure(float(r["mine_pct"]), pct, source),
            "benchmark": _maybe_figure(r["benchmark_pct"], pct, source),
            "benchmark_at_most": _maybe_figure(r["benchmark_pct_at_most"], pct, source),
            "difference": _maybe_figure(r["difference"], pct, source),
            "difference_floor": _maybe_figure(r["difference_floor"], pct, source),
            "state": state,
            "is_floor": bool(r["is_floor"]),
            "note": note,
        })

    note_text = out.attrs.get("compare_note", "")
    if with_note:
        return BenchmarkCompanyRows(rows, note_text)
    return rows


def company_bars(basis: str = "everything", limit: int = 20,
                 mine: pd.DataFrame | None = None, **paths) -> list[Bar]:
    """True exposure to one name — direct plus every fund's share. Panel 5.

    Filtered to `exposure.KIND_COMPANY` (trap 2): a cash balance and a
    fund's own unexplained remainder are real money but not a name, and a
    panel headed "true exposure to one name" must never open with the
    dollar as his biggest single bet. The money this filter leaves out
    still reaches the screen, in `honesty()`, captioned per kind, from
    `exposure.benchmark_companies().attrs["excluded_by_kind"]` — never
    re-summed here.

    `mine`, when given, is an already-computed `by_company(basis, **paths)`
    frame (fix round 1, I) — see `context()`, which computes it once per
    basis rather than paying for this walk again here.
    """
    from src import exposure

    book.check_basis(basis)
    rows = mine if mine is not None else exposure.by_company(
        basis=basis, **_fund_paths(paths))
    if rows.empty:
        return []
    companies = rows[rows["kind"] == exposure.KIND_COMPANY].head(limit)
    if companies.empty:
        return []
    _, refs = _instrument_lookup(paths)
    items = [{
        "label": str(r["label"]),
        "value": float(r["market_value"]),
        "pct": float(r["pct_of_basis"]),
        "href": _href(refs.get(r["instrument_id"], "")) if r["instrument_id"] else "",
        "note": ("at least — only part of this fund's own holdings file "
                "names companies" if bool(r["is_floor"]) else ""),
    } for _, r in companies.iterrows()]
    return _build_bars(items, source="exposure.by_company()")


def overlap_rows(basis: str = "everything", **paths) -> list[dict]:
    """Themes holding the same companies. Spec panel 6.

    Every number carries its source (D12, fix round 1, E) — the first
    version of this function shipped `shared`/`pct_of_a`/`pct_of_b` as bare
    formatted strings, the one panel (besides the company comparison) a
    reviewer found doing that.
    """
    from src import clusters

    book.check_basis(basis)
    df = clusters.overlap(basis=basis, **_fund_paths(paths))
    if df.empty:
        return []
    source = "clusters.overlap()"
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "cluster_a": r["cluster_a"], "cluster_b": r["cluster_b"],
            "shared": _maybe_figure(float(r["shared_value"]), money, source),
            "pct_of_a": _maybe_figure(float(r["pct_of_a"]), pct, source),
            "pct_of_b": _maybe_figure(float(r["pct_of_b"]), pct, source),
            "top_names": r["top_names"],
            "is_floor": bool(r["is_floor"]),
            "note": ("at least — reached partly through a fund whose own "
                    "holdings file is partial" if r["is_floor"] else ""),
        })
    return rows


def construction(basis: str = "everything",
                 mine: pd.DataFrame | None = None, **paths) -> list[dict]:
    """How it's built: effective bets, currency split. Spec panel 7.

    Worst drawdown is deliberately NOT here. A test in `tests/test_exposure.py`
    is a tripwire written ahead of this module specifically so that
    temptation goes red the day it is acted on: the account-opening transfer
    sits in the raw NAV series' free-text notes column with the deposit
    figure itself reading zero, so a drawdown taken straight off that series
    would report his own money paying in as a market move. His call,
    2026-10-06 — it is built properly, reconciled against the cash-transfer
    record, in the Performance tab (Stage 4), never here.

    `mine`, when given, is an already-computed `by_company(basis, **paths)`
    frame, passed to `exposure.effective_bets()` (fix round 1, I) — see
    `context()`.
    """
    from src import exposure

    book.check_basis(basis)
    eb = exposure.effective_bets(basis=basis, mine=mine, **_fund_paths(paths))
    bets_source = "exposure.effective_bets()"
    rows = [{
        "metric": "Effective number of bets",
        "value": Figure(
            value=float(eb["count"]),
            text=f"{eb['count']:.1f}" + (" or fewer" if eb["is_upper_bound"] else ""),
            source=bets_source,
        ),
        "why": eb["why"],
    }]
    cur = book.by_currency(basis=basis, **_book_paths(paths))
    currency_source = "book.by_currency()"
    for _, r in cur.iterrows():
        mv = float(r["market_value"])
        rows.append({
            "metric": f"Currency: {r['currency']}",
            "value": Figure(value=mv, text=money(mv), source=currency_source),
            "why": f"{pct(float(r['pct_of_basis']))} of this basis is quoted in {r['currency']}.",
        })
    rows.append({
        "metric": "Worst drawdown",
        "value": "",
        "why": ("Deferred to the Performance tab (Stage 4) — a drawdown off "
               "the raw NAV series would record his own deposits as a "
               "market move. Not computed here on purpose."),
    })
    return rows


def pnl_panel(**paths) -> dict:
    """Profit and loss (CLAUDE.md, 2026-10-08; spec D7: present but quiet —
    grey, never coloured, never sortable).

    Not a function of `basis` — alone among this module's panels, which is
    why this is the one function here that takes no `basis` argument at
    all. A realised gain belongs to a trade, not a share of a current
    basis, and his own sizing decision for the unrealised side
    (`book.lot_cost_usd()`'s own docstring) does not change if he happens
    to be looking at "minus cash" at the time — so `context()` computes
    this once and the SAME dict is handed to every basis block, never
    recomputed three times for an answer that cannot differ.

    `rows` covers only the lots `book.lot_cost_usd()` could price at all
    (`established`), ordered by the size of the position — the same
    size-ranks-everything convention `book.holdings()` already uses, so a
    reader scanning top to bottom sees his biggest holdings first here too.
    `uncosted` names every lot that did not make that cut (CLAUDE.md: "a
    lot whose cost cannot be established at all gets no figure and is
    named") — empty on his real book today, same as `fx_fallback_lots()`.
    """
    realized_value = book.realized_pnl(**_realized_paths(paths))
    unrealized_value = book.unrealized_pnl(**_pnl_paths(paths))
    total_value = book.total_pnl(**_pnl_paths(paths))
    pnl_source = "book.lot_cost_usd()"

    names, _ = _instrument_lookup(paths)

    def _label(ticker: str, instrument_id: str) -> str:
        """One instrument, many broker lots (CLAUDE.md) — ASML_USD and
        ASML_EUR share one `instrument_id` and therefore one NAME, but this
        is the one panel on the page that lists a lot's OWN cost, so the
        two must still read as two different rows. The ticker is appended
        only when it differs from the instrument_id it resolves to — the
        ordinary case (one lot, one ticker, same as its instrument_id)
        never gains a parenthetical nobody asked for."""
        name = names.get(instrument_id, instrument_id) or instrument_id
        return name if ticker == instrument_id else f"{name} ({ticker})"

    lots = book.lot_cost_usd(**_pnl_paths(paths))
    rows = []
    if not lots.empty:
        established = lots[lots["established"]].to_dict("records")
        established.sort(key=lambda r: abs(r["market_value"]), reverse=True)
        for r in established:
            mv = float(r["market_value"])
            cost = float(r["cost_usd"])
            unreal = float(r["unrealized"])
            rows.append({
                "ticker": r["ticker"],
                "name": _label(r["ticker"], r["instrument_id"]),
                "market_value": Figure(value=mv, text=money(mv), source=pnl_source),
                "cost": Figure(value=cost, text=money(cost), source=pnl_source),
                "unrealized": Figure(value=unreal, text=money(unreal),
                                     source=pnl_source),
                "flagged": bool(r["flagged"]),
            })

    uncosted = book.uncosted_lots(**_pnl_paths(paths))
    uncosted_rows = [{
        "ticker": r["ticker"],
        "name": _label(r["ticker"], r["instrument_id"]),
        "reason": r["reason"],
    } for _, r in uncosted.iterrows()]

    return {
        "realized": Figure(value=realized_value, text=money(realized_value),
                           source="book.realized_pnl()"),
        # 🚨 Review, 2026-10-08 (I3). `book.unrealized_pnl()`/`total_pnl()`
        # both return `None` when nothing could be costed at all — a
        # `Figure(value=0.0, ...)` built straight from that `None` would
        # have raised (trap: `money(None)` has no `float()` to call), and
        # the ORIGINAL bug was worse than a crash: both used to default to
        # 0.0 upstream, so this line printed a perfectly plausible $0.00
        # while every lot silently went uncosted. `_maybe_figure()` is the
        # one place in this file allowed to tell an absent figure from a
        # real zero (trap 3) — used here for exactly the reason it exists.
        "unrealized": _maybe_figure(unrealized_value, money,
                                    "book.unrealized_pnl()"),
        "total": _maybe_figure(total_value, money, "book.total_pnl()"),
        "rows": rows,
        "uncosted": uncosted_rows,
    }


def honesty(basis: str = "everything",
           mine: pd.DataFrame | None = None, **paths) -> list[str]:
    """The honesty contract, in plain words. Not optional, not droppable.

    One sentence per kind of doubt, assembled from reports that already
    exist — nothing here is a number this module computed:

      * price age        instruments.price_age_days()/price_warning() —
                          stated unconditionally, not only when stale,
                          because the honesty contract asks "how old", not
                          "is it too old". The staleness THRESHOLD is
                          `price_warning()`'s own default, read ONCE from
                          its own return (empty = fresh) rather than
                          retyped as a second literal here (fix round 1,
                          minor) — this module does not get to have its own
                          opinion about what counts as stale.
      * estimation        exposure.coverage() — EVERY row with a non-blank
                          `why`, not just the ones flagged `estimate` (fix
                          round 1, D: the first version dropped every other
                          caveat that function carries — issuer-by-hand,
                          age-unknown — which is most of what it has to say)
                          — and exposure.company_coverage() for company
                          holdings, same idea.
      * unclassified money book.unclassified() — listed per lot, never
                          summed into one total here (that sum would be the
                          one this module is forbidden to compute).
      * vendor, not GICS   fixed sentence — D13's own framing.
      * benchmark age      TWO dates, never one — `exposure.
                          benchmark_weights()`'s own `as_of` (sector
                          breakdown, `etf_compositions.csv`) and `exposure.
                          benchmark_companies().attrs["as_of"]` (company
                          breakdown, `etf_holdings.csv`), each stated under
                          its own name. Fix, I4 (2026-10-07 final review),
                          corrected again at the residual review (N1): the
                          first version read ONE date and reported it as
                          covering both files, which is false the moment
                          they are refreshed on different days — exactly
                          the case on real data today. The two are only
                          ever reported as one sentence when they are
                          equal, and that is derived by comparing them,
                          never assumed. Stated only when at least one side
                          has been fetched; the never-fetched case is the
                          "sector fetch gap"/"company fetch gap" lines
                          below, not this one.
      * sector fetch gap   exposure.benchmark_sector_comparison().attrs["why"]
      * company fetch gap  exposure.benchmark_companies().attrs["why"], and
                          its basis note, attrs["compare_note"]
      * excluded money     exposure.benchmark_companies().attrs["excluded_by_kind"]
                          — the cash/unaccounted/no-companies money the S&P
                          company comparison cannot cover at all (trap 2)
      * sector disagreement exposure.sector_disagreements() — names the
                          vendor, the date and the dollar amount for every
                          holding where his label and the vendor's differ,
                          AND which label the sector panel actually counts
                          the money under — derived from the `classification`
                          in force (fix round 2, Critical A), never
                          hard-coded, because the panel's own classification
                          is a parameter with a default (D13: the vendor's),
                          not a constant this module gets to assume.
                          🚨 Fix, pre-push review 2026-10-07 (finding 1): this
                          loop used to stop at `disagree`/`kept-mine` and
                          silently drop `no-vendor-sector`, `no-own-sector`
                          and `unmappable` — the undated-stock case reached
                          NO sentence anywhere on the page, the "what could
                          not be classified" half of the honesty contract.
                          All four states say something now
      * weight residue     exposure.weight_residue() — currently uncalled
                          anywhere else, so without this line a hand-entered
                          fund's rounding keeps overstating its companies
                          with nothing on screen saying so

    `mine`, when given, is an already-computed `by_company(basis, **paths)`
    frame, passed to `exposure.benchmark_companies()` (fix round 1, I) —
    see `context()`, which assembles this PER BASIS now (fix round 1,
    Important C: the first version computed it once for `"everything"` and
    shipped it top-level, so the `pure-equity` view displayed a basis note
    about `everything`).

    `classification`, when given in `**paths`, is forwarded to
    `exposure.by_sector()`/`benchmark_sector_comparison()` (via
    `_by_sector_paths()`/`_sector_compare_paths()`) exactly as `sector_rows()`
    does, so the sector-disagreement sentence below describes the SAME
    panel the reader is actually looking at — never a second, independently
    guessed answer to "which label wins here".
    """
    from src import exposure, instruments

    book.check_basis(basis)
    fund_paths = _fund_paths(paths)
    price_paths = _price_paths(paths)
    lines: list[str] = []

    age = instruments.price_age_days(**price_paths)
    warn = instruments.price_warning(**price_paths)
    if age is None:
        lines.append("Prices carry no date, so how old they are is not known.")
    elif not warn:
        when = "today" if age == 0 else f"{age} day{'s' if age != 1 else ''} ago"
        lines.append(
            f"Prices are current as of {when}, using the OLDEST lot on "
            f"file — one lot left behind makes the whole book read stale."
        )
    else:
        # Fix round 2, Important B: `warn` ends with a shell command
        # ("...src.ibkr_sync") and nothing separated it from the sentence
        # that followed, so the command read as if it took an argument
        # called "Using" — exactly the line he would have copied. A period
        # closes the command's own sentence before the next one starts.
        lines.append(
            f"{warn.rstrip('.')}. Using the OLDEST lot on file — one lot "
            f"left behind makes the whole book read stale."
        )

    cov = exposure.coverage(**_coverage_paths(paths))
    for _, r in cov.iterrows():
        why = str(r["why"]).strip()
        if why:
            lines.append(f"{r['ticker']}'s sector breakdown: {why}.")

    cc = exposure.company_coverage(basis=basis, **fund_paths)
    lines.append(
        "Exposure reached through a fund is stated as AT LEAST that much, "
        "never a precise figure — a fund's own holdings file usually names "
        "only part of it, after a small-weight floor trims the rest."
    )
    # Fix, 2026-10-07 final review (I7). This used to name the floor funds
    # and stop there ("Today that is a floor for: IWM, VEGI, VTI, ..."),
    # which is a list he cannot act on — with almost every fund a floor,
    # knowing WHICH funds carries almost no information; knowing HOW MUCH of
    # each is unstored does. `company_coverage()`'s own `why` already says
    # that in his language (and, for a fund with no `as_of` date, that the
    # date on file is the day it was looked at, not how old the holdings
    # are) — read here the same way `cov`'s sector `why` already is above,
    # never re-summarised.
    for _, r in cc.iterrows():
        why = str(r["why"]).strip()
        if why:
            lines.append(f"{r['ticker']}: {why}.")

    unclassified = book.unclassified(**_unclassified_paths(paths))
    if unclassified.empty:
        # Fix round 2, Minor C: the effect, not the mechanism (CLAUDE.md) —
        # he is not a developer, and "positions.csv" names a file he has
        # never opened.
        lines.append(
            "Every dollar in the book is accounted for under a holding "
            "the record knows."
        )
    else:
        parts = [f"{r['instrument_id']} ({money(float(r['market_value']))})"
                for _, r in unclassified.iterrows()]
        lines.append(
            "Held under a ticker the record does not recognise yet, so it "
            "counts toward no theme or sector: " + ", ".join(parts) + "."
        )

    # Fix round 3, Minor B: the parenthetical named the database column
    # (`instruments.provider_sector`) — a mechanism name he never sees
    # anywhere else, unlike the two CSV filenames in the weight-residue
    # sentences below, which ARE already part of his vocabulary (named by
    # `ops/data_gaps.py` and `/market` on every run) and stay. The sentence
    # says everything it needs to without naming a column.
    lines.append(
        "The sector split uses a named vendor's classification, not "
        "GICS — real GICS is licensed and cannot be fetched."
    )

    # Fix, 2026-10-07 final review (I4) — a ruling of mine corrected. I had
    # parked `ops/data_gaps.py`'s presence-only benchmark check on the
    # stated ground that this module already showed the benchmark's own
    # `as_of` somewhere on the page; it did not, anywhere, so the S&P
    # comparison could age indefinitely with no date on screen and no nag.
    #
    # Fix, residual review (N1) — the version above shipped a false
    # sentence in the DIRECTION that hides ageing, which is the one
    # direction this block cannot afford: it read `benchmark_weights()`'s
    # date (`etf_compositions.csv`, the sector weights) and reported it as
    # if it also dated the company comparison. The company comparison is
    # built from `etf_holdings.csv`, fetched by a separate path
    # (`benchmark_companies().attrs["as_of"]`), and the two already differ
    # by a day on real data. Each date is read from the frame that owns
    # it; the two are stated as one sentence only when they are equal, and
    # that equality is derived by comparing the strings, never assumed.
    bc = exposure.benchmark_companies(basis=basis, mine=mine, **fund_paths)
    bw = exposure.benchmark_weights(**_benchmark_weights_paths(paths))
    sector_as_of = str(bw["as_of"].iloc[0]).strip() if not bw.empty else ""
    company_as_of = (str(bc.attrs.get("as_of") or "").strip()
                     if not bc.empty else "")
    if not bw.empty and not bc.empty and sector_as_of and sector_as_of == company_as_of:
        lines.append(
            f"The S&P's own sector breakdown and its own company "
            f"breakdown are both dated {sector_as_of} — every number on "
            f"this page compared against the S&P is only as current as "
            f"that date, and nothing here re-fetches it; run /market to "
            f"check for a newer one."
        )
    else:
        if not bw.empty:
            lines.append(
                f"The S&P's own sector breakdown is dated {sector_as_of} "
                f"— every sector number compared against the S&P is only "
                f"as current as that date, and nothing here re-fetches "
                f"it; run /market to check for a newer one."
                if sector_as_of else
                "The S&P's own sector breakdown carries no date on file, "
                "so how current the sector comparison is is not known."
            )
        if not bc.empty:
            lines.append(
                f"The S&P's own company breakdown is dated {company_as_of} "
                f"— every company number compared against the S&P is only "
                f"as current as that date, and nothing here re-fetches "
                f"it; run /market to check for a newer one."
                if company_as_of else
                "The S&P's own company breakdown carries no date on "
                "file, so how current the company comparison is is not "
                "known."
            )

    sector_cmp = exposure.benchmark_sector_comparison(
        basis=basis, **_sector_compare_paths(paths))
    if sector_cmp.attrs.get("why"):
        lines.append(sector_cmp.attrs["why"])

    # `bc` already computed above, for the two dates — not recomputed here.
    if bc.attrs.get("why"):
        lines.append(bc.attrs["why"])
    if bc.attrs.get("compare_note"):
        lines.append(bc.attrs["compare_note"])
    if not bc.empty:
        # Fix round 2, Minor D. `excluded_by_kind` is read from `bc`, which
        # was just computed under THIS call's own `basis` (never a figure
        # borrowed from a different basis) — but under `pure-equity` (and
        # often `minus-cash`), cash is already excluded from his side
        # before `by_company()` ever runs, so the cash component here is a
        # genuine, exact zero, not a rounding artefact. A zero is not
        # information; omit the component, and omit the whole sentence if
        # every component is zero, rather than reporting "nothing" three
        # ways.
        excluded = bc.attrs.get("excluded_by_kind", {})
        excluded_parts = []
        if excluded.get(exposure.KIND_CASH, 0.0):
            excluded_parts.append(
                f"{money(excluded[exposure.KIND_CASH])} in cash/FX balances")
        if excluded.get(exposure.KIND_UNACCOUNTED, 0.0):
            excluded_parts.append(
                f"{money(excluded[exposure.KIND_UNACCOUNTED])} not named by "
                f"a fund's own holdings file (unaccounted)")
        if excluded.get(exposure.KIND_NO_COMPANIES, 0.0):
            excluded_parts.append(
                f"{money(excluded[exposure.KIND_NO_COMPANIES])} in funds "
                f"that own no companies at all")
        if excluded_parts:
            if len(excluded_parts) == 1:
                listed = excluded_parts[0]
            elif len(excluded_parts) == 2:
                listed = f"{excluded_parts[0]} and {excluded_parts[1]}"
            else:
                listed = (f"{excluded_parts[0]}, {excluded_parts[1]}, and "
                         f"{excluded_parts[2]}")
            lines.append(
                f"The S&P company comparison only covers real companies: "
                f"it leaves out {listed} — none of it has an index "
                f"counterpart."
            )

    # Fix round 2, Critical A. The sector panel's `classification` is a
    # parameter with a default (D13: the vendor's), never a constant — the
    # first version of this sentence hard-coded "counted under his own
    # label", which was backwards the moment the panel's own default was
    # checked: under "provider" (the default), BMNR's $2,630.80 is counted
    # under the VENDOR's "Financials", not his own "Crypto - Miners /
    # Treasury" — measured live, Financials reads $9,815.40 under the
    # vendor's view against $7,184.20 under his own, a difference of
    # exactly that figure. Derived here from whichever classification is
    # actually in force for THIS call, so the sentence flips correctly if
    # `classification="mine"` is ever passed instead.
    sector_classification = paths.get("classification") or "provider"
    panel_uses_vendor = sector_classification == "provider"
    sd = exposure.sector_disagreements(basis=basis, **fund_paths)
    for _, r in sd.iterrows():
        state = r["state"]
        if state in ("disagree", "kept-mine"):
            panel_label = r["vendor"] if panel_uses_vendor else r["mine"]
            panel_whose = "the vendor's" if panel_uses_vendor else "his own"
            decided = (f"he recorded keeping his own label, {r['mine']!r}, anyway"
                      if state == "kept-mine"
                      else f"filed as {r['mine']!r} by him, unresolved")
            lines.append(
                f"{r['instrument_id']}: {decided}; {r['vendor_source']} calls "
                f"it {r['vendor']!r} as of {r['vendor_as_of']}. The sector "
                f"panel counts this {money(float(r['market_value']))} under "
                f"{panel_label!r} ({panel_whose} classification) — the two "
                f"disagree on screen."
            )
        # 🚨 Fix, pre-push review 2026-10-07 (finding 1, third part).
        # `honesty()` used to drop every OTHER state `sector_disagreements()`
        # can produce — "the undated-stock case" the review reproduced
        # (`no-vendor-sector`) reached NO sentence anywhere on the page,
        # which is the "what could not be classified" half of the honesty
        # contract, dropped. All three remaining states now say something,
        # built only from fields this same row already carries — never a
        # guess at content `sector_disagreements()` itself does not know.
        elif state == "no-vendor-sector":
            if panel_uses_vendor:
                lines.append(
                    f"{r['instrument_id']}: the vendor has not dated a "
                    f"sector for this holding, so its "
                    f"{money(float(r['market_value']))} counts under "
                    f"'Other' on the sector panel — its sector is unknown, "
                    f"not a confirmed zero for any sector."
                )
            else:
                lines.append(
                    f"{r['instrument_id']}: the vendor has not dated a "
                    f"sector for this holding; the sector panel is showing "
                    f"his own label instead, so this does not change what "
                    f"is on screen today, but no vendor comparison is "
                    f"possible for it."
                )
        elif state == "no-own-sector":
            if panel_uses_vendor:
                lines.append(
                    f"{r['instrument_id']}: he has never typed his own "
                    f"sector for this holding; the sector panel is using "
                    f"{r['vendor_source']}'s {r['vendor']!r} (dated "
                    f"{r['vendor_as_of']}) instead, so this does not change "
                    f"what is on screen today."
                )
            else:
                lines.append(
                    f"{r['instrument_id']}: he has never typed his own "
                    f"sector for this holding, so its "
                    f"{money(float(r['market_value']))} counts under "
                    f"'Other' on the sector panel — its sector is unknown, "
                    f"not a confirmed zero for any sector."
                )
        elif state == "unmappable":
            lines.append(
                f"{r['instrument_id']}: his label ({r['mine']!r}) or the "
                f"vendor's ({r['vendor']!r}) is not in data/sectors.csv, so "
                f"neither can be rolled up to compare — add it with "
                f"sectors.add_sector()."
            )

    # `r["why"]` names `etf_compositions.csv`/`etf_holdings.csv` by filename
    # — left in on purpose (fix round 3, ruling, not an oversight a later
    # session should "clean up"). Unlike `instruments.provider_sector`
    # above, these two names are already part of his vocabulary:
    # `ops/data_gaps.py` prints them to him by name on every gap report,
    # and `/market` names them in its own output. A sentence about a
    # fund's stored weights being over 100% is clearer saying WHICH table,
    # in the same words the rest of the system already uses with him — a
    # column name he never sees anywhere else is a different kind of
    # mechanism from a filename he already reads elsewhere in this project.
    wr = exposure.weight_residue(basis=basis, **_residue_paths(paths))
    for _, r in wr.iterrows():
        lines.append(r["why"])

    # 🚨 Review, 2026-10-08 (I2): the honesty line that was missing for the
    # NORMAL case, not just the flagged one. A foreign lot priced by rule A
    # (decision A: each purchase's own exchange rate) always reads a
    # little differently from what IBKR's own report-date conversion would
    # show — that is the whole point of the choice, and it is the one case
    # his own FX worry was actually about: he would open this page, see
    # one number, compare it with his broker's, and find nothing here
    # explaining why they disagree. Fires for every lot rule A actually
    # priced; `book.fx_choice_effect()` derives the amount — never typed
    # here — and its sign picks the one honest word.
    pnl_lots = book.lot_cost_usd(**_pnl_paths(paths))
    reconstructed = (pnl_lots[pnl_lots["method"] == "trade_reconstruction"]
                     if not pnl_lots.empty else pnl_lots)
    if not reconstructed.empty:
        names = ", ".join(sorted(reconstructed["ticker"]))
        effect = book.fx_choice_effect(**_pnl_paths(paths))
        direction = ("lower" if effect > 0 else
                    "higher" if effect < 0 else "no different from")
        lines.append(
            f"{names}: priced at the exchange rate on each day he actually "
            f"bought, as he chose, so currency movement since then counts "
            f"as part of the gain — the unrealised figure here reads "
            f"{money(abs(effect))} {direction} than IBKR's own number for "
            f"these same holdings. That gap is the choice working as "
            f"intended, not a mistake."
        )

    # CLAUDE.md, 2026-10-08: the P&L honesty requirement — a sentence
    # whenever a lot falls back to today's exchange rate instead of each
    # purchase's own, naming which lot and what the figure then means.
    # `book.fx_fallback_lots()`'s own `reason` already says both; nothing
    # at all is appended when it is empty, which is every real lot today.
    for _, r in book.fx_fallback_lots(**_pnl_paths(paths)).iterrows():
        lines.append(r["reason"])

    # 🚨 Review, 2026-10-08 (I2, "while you are there"): dividends,
    # withholding tax, interest and short-borrow fees are real money, on
    # file, that neither total above counts at all — roughly a tenth of
    # `total_pnl()` on his real book, silently, until this line existed.
    # `book.excluded_cash_flows()` derives the amount; nothing here is
    # typed. Silent (no line at all) only in the structural-zero case —
    # no cash activity on file whatsoever — the same "a zero is not
    # information, omit it" rule the S&P-exclusion sentence above already
    # follows.
    excluded = book.excluded_cash_flows(**_cash_flow_paths(paths))
    if abs(excluded) > 0.005:
        word = "income" if excluded > 0 else "cost"
        lines.append(
            f"Dividends, withholding tax, interest and borrow fees are not "
            f"part of either figure above — a net {word} of "
            f"{money(abs(excluded))} sits in his cash ledger instead."
        )

    return lines


def context(**paths) -> dict:
    """Everything the renderer needs, for all three bases.

    All three are computed at build time and shipped in one file, so the
    control switches a view rather than asking anything to recompute — the
    page is a static file with no server behind it (D1).

    ⚠️ **Two panels, two incompatible state vocabularies, on purpose (fix
    round 1, Critical A) — never translate one into the other again.**
    `bases[basis]["sector_rows"]`'s `state` comes from `exposure.
    benchmark_sector_comparison()`: `both` / `only-mine` / `unclassified` /
    `only-index` / `""` (not fetched). There, `only-mine` is about as
    certain an absence as this module ever gets — a sector weighting is
    schema-checked to sum to the whole fund, so there is no small-weight
    floor hiding a sector the way one hides a company — but the code still
    emits an absent value (`NaN`, drawn as a dash) for the NUMBER itself,
    never a bare `0.0`; the certainty is real but it lives in `state`, not
    in the figure (fix, 2026-10-07 final review, I8: this sentence used to
    call it "a CONFIDENT zero", which the code deliberately does not
    claim). `unclassified` (fix, pre-push review 2026-10-07, finding 1) is
    NOT the same certainty — it is money `by_sector()` could not place at
    all, never a sector the index structurally cannot hold, and the two
    used to collapse into one `only-mine` state and one caption.
    `bases[basis]["benchmark_company_rows"]["rows"]`'s `state` comes from
    `exposure.benchmark_companies()`: `both` / `only-index` / `below-floor`
    / `unknown`. There is no `only-mine` here at all, and `below-floor` is
    a BOUND, never a confirmed absence, because the index's STORED company
    rows stop at a small-weight floor a sector table never has. A
    renderer must read each panel's `state` against THAT panel's own
    caption — a shared legend across the two would print confident absence
    over what are, for roughly 2,000 real rows today, only bounds.

    `price_age_days` is `instruments.price_age_days()`'s own return value —
    not a calendar date (this key was named `priced_at` in round 0; fixed,
    Important H, because that name means an ISO date everywhere else in
    this project, including this module's own fixtures, and an integer
    day-count under that name lies about its own shape). No `src/` function
    returns the raw date of the oldest stamped lot, only its age in days —
    reconstructing a date from `date.today()` minus that age would
    re-derive, outside `src/`, a fact `instruments.py` already computed and
    chose not to expose, which is the second-definition failure this
    module exists to avoid. If a later task needs the literal date, that
    getter belongs in `instruments.py`, not invented here.

    The honesty block is assembled PER BASIS now (fix round 1, Important C
    — the first version computed it once for `"everything"` and shipped it
    top-level, so the `pure-equity` view displayed a basis note that said
    `"everything"`), under `bases[basis]["honesty"]`.

    `exposure.by_company(basis, **paths)` is computed exactly ONCE per
    basis, here, and threaded as `mine=` into every panel that would
    otherwise recompute the identical walk (fix round 1, Important I) —
    `benchmark_company_rows()`, `company_bars()`, `construction()` and
    `honesty()` all accept it. This is a per-call pass-down, not a cache:
    nothing here persists across calls to `context()`, so a test that
    monkeypatches a loader between calls is never handed a stale frame.

    `pnl_panel()` is computed exactly ONCE here too, for the opposite
    reason `by_company()` is computed per basis — its own docstring
    explains why P&L has no basis to vary by at all — and the SAME dict is
    stored under `bases[basis]["pnl"]` for all three, so `ops/build_page.py`
    can walk `_PANELS` (the one list naming every Spread panel) without a
    special case for the one panel that happens not to change.
    """
    from src import exposure, instruments

    fund_paths = _fund_paths(paths)
    pnl = pnl_panel(**paths)
    bases = {}
    for basis in BASES_ORDER:
        mine_all = exposure.by_company(basis=basis, **fund_paths)
        result = benchmark_company_rows(basis, with_note=True, mine=mine_all,
                                        **paths)
        bases[basis] = {
            "header": header(basis, **paths),
            "theme_bars": theme_bars(basis, **paths),
            "theme_total": theme_total(basis, **paths),
            "theme_coverage_pct": theme_coverage_pct(basis, **paths),
            "sector_rows": sector_rows(basis, **paths),
            "benchmark_company_rows": {"rows": result.rows, "note": result.note},
            "company_bars": company_bars(basis, mine=mine_all, **paths),
            "overlap_rows": overlap_rows(basis, **paths),
            "construction": construction(basis, mine=mine_all, **paths),
            "pnl": pnl,
            "honesty": honesty(basis, mine=mine_all, **paths),
        }

    return {
        "bases": bases,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "price_age_days": instruments.price_age_days(**_price_paths(paths)),
        "notes": embedded_notes(bases),
    }
