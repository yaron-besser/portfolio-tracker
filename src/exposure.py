"""Look-through exposure — what you actually own, not what the labels say.

Why this exists. A cluster answers *why you bought something*; it cannot also
answer *what you are exposed to*. Yaron hit this asking where QQQ belongs: it
sits in the Core US cluster because he bought it for US beta, and it is also
50% technology. Both are true, and one label cannot carry both.

So the two questions are kept apart:

    instruments.cluster_id      intent  — why I own it
    exposure.by_sector()        fact    — what I am exposed to
The gap is not academic. On 2026-10-10: technology is 35.2% of NAV
look-through against 21.8% from direct holdings, and materials is 5.3% with
no direct holding at all — more than a third of the tech exposure sits in funds.
no direct holding at all — more than half the tech exposure sits inside ETFs.

⚠️ Those numbers are an illustration of the SHAPE, and they are already a
little out of date as you read them. Run `by_sector()`. The previous version
of this paragraph quoted 2026-08-10 figures for seven weeks, by which point
every one of them was wrong.

**Provenance travels with the number.** Each composition row carries a
`source_quality` (filing / issuer / vendor / issuer-by-hand / estimate) and an
`as_of` where one is known. Every function here can report how much of its answer rests on a
guess — `coverage()` is that report — and an exposure number that hides it is
worse than no number.
"""

from __future__ import annotations

import csv
import functools
import re
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from src.claims import PROJECT_ROOT, _is_real_date, _vocab_check

COMPOSITIONS_PATH = PROJECT_ROOT / "data" / "market" / "etf_compositions.csv"

COLUMNS = ["ticker", "sector", "weight", "top_holding", "source",
           "source_quality", "as_of"]

def parent_sector(sector: str) -> str:
    """Roll a sub-sector up to one of the 11 SPY sectors (plus cash and crypto).

    This used to match on the label's prefix, which meant the taxonomy lived in
    code Yaron could not see, and anything unrecognised silently became 'Other'
    — money that stopped counting toward any sector while the totals still
    looked tidy. The mapping is `data/sectors.csv` now, and an unknown label
    raises rather than disappearing.
    """
    from src import sectors

    return sectors.parent(sector)


def _schema() -> pa.DataFrameSchema:
    return pa.DataFrameSchema(
        {
            "ticker": pa.Column(str),
            "sector": pa.Column(str, pa.Check.str_length(min_value=1)),
            "weight": pa.Column(
                float,
                pa.Check.in_range(0, 1, error="weight is a fraction between 0 and 1"),
                coerce=True,
            ),
            # The single name behind this sector slice, when the ETF is
            # small enough for that to be meaningful — only MAGS today, where
            # each of the seven rows IS one company. Named `sub_sector` until
            # 2026-09-29, which is what it was never used for: every value in
            # it has always been a ticker.
            "top_holding": pa.Column(str, nullable=True),
            "source": pa.Column(str, pa.Check.str_length(min_value=1)),
            "source_quality": pa.Column(str, _vocab_check("composition_source")),
            # Blank is honest: most issuer pages carry no date. A guessed date
            # would be worse than an admitted gap.
            "as_of": pa.Column(
                str,
                pa.Check(lambda s: s.map(lambda v: v == "" or _is_real_date(v)),
                         error="as_of must be blank or a real ISO date"),
                nullable=True,
            ),
        },
        strict=True,
        ordered=True,
        checks=[
            pa.Check(
                lambda df: df.groupby("ticker")["weight"].transform("sum").between(0.98, 1.02),
                error="each ETF's weights must sum to 1.0 (±0.02) — a partial "
                      "breakdown silently under-reports every sector it omits",
            ),
        ],
    )


def load(path: Path = COMPOSITIONS_PATH) -> pd.DataFrame:
    """Read and validate the composition table."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype={c: str for c in COLUMNS if c != "weight"},
                     keep_default_na=False)
    df["weight"] = pd.to_numeric(df["weight"])
    return _schema().validate(df, lazy=True)


def _holdings(positions_path: Path | None = None,
              instruments_path: Path | None = None,
              clusters_path: Path | None = None,
              basis: str = "everything") -> pd.DataFrame:
    """Held instruments with market value, type, sector and cluster, one row
    each.

    Delegates to `book.holdings()`, which is the one definition of what the
    book contains and what it is worth (P7). This used to read positions.csv
    and sum its own total; so did `clusters.weights()`; the page would have
    been a third. The column names are kept as they were — `mv` and `type` —
    because renaming them is a change to `by_sector()` and `coverage()` and
    not to where the number comes from.

    `basis` threads through to `book.holdings()` so `by_sector()`'s three
    settings (spec D6) work; `coverage()` still calls this with the default,
    since a composition's source quality has nothing to do with which basis
    is in view. Carries `provider_sector`/`provider_sector_as_of` as well as
    his own `sector` — spec D13 needs both to compare them.

    🚨 **Fix, 2026-10-07 final review (I5).** `instruments_path`/
    `clusters_path` used to stop here — `book.holdings()` accepts both, but
    this function never forwarded them, so a caller that injected a fixture
    `clusters_path` (to test `in_equity_base` under `pure-equity`/
    `minus-cash`, say) had it silently dropped and `book.holdings()` fell
    back to the real `data/clusters.csv` instead. Harmless on the real page
    (no overrides, both read the same files on disk) but it meant a
    fixture-based test of `by_sector()` under a restricted basis could read
    live data without anyone asking it to. Threaded straight through now,
    same as `positions_path` always was.
    """
    from src import book

    book.check_basis(basis)
    held = book.holdings(basis, positions_path=positions_path,
                         instruments_path=instruments_path,
                         clusters_path=clusters_path)
    if held.empty:
        return pd.DataFrame(columns=["instrument_id", "mv", "type", "sector",
                                     "provider_sector", "provider_sector_as_of",
                                     "cluster_id"])
    return held.rename(columns={"market_value": "mv",
                                "instrument_type": "type"})[
        ["instrument_id", "mv", "type", "sector", "provider_sector",
         "provider_sector_as_of", "cluster_id"]]


def unknown_holdings(positions_path: Path | None = None,
                     instruments_path: Path | None = None) -> pd.DataFrame:
    """Money held under an instrument_id that instruments.csv does not know.

    Every exposure percentage silently excludes these, top and bottom. An
    unknown id is not an exotic failure: `ibkr_sync` writes a brand-new lot
    with "AUTO-ADDED by ibkr_sync — add it to instruments.csv", so this is
    exactly how a ticker bought this week arrives.

    Empty is the normal answer — `ops/hooks/pre-commit` enforces the foreign
    key, so anything here got in without passing a commit. Visible, never
    blocking; the fix is to write the instruments.csv row.

    Delegates to `book.unclassified()`, which is the one implementation of
    "money under an instrument_id instruments.csv does not know" (P7) — this
    used to read positions.csv and compute the same set independently, the
    second of two definitions `ops/data_gaps.py`'s single call site could
    never tell apart. Column names kept as they were — `mv`, not
    `market_value` — because this module's own callers and tests read `mv`.
    """
    from src import book

    out = book.unclassified(positions_path=positions_path,
                            instruments_path=instruments_path)
    if out.empty:
        return pd.DataFrame(columns=["instrument_id", "mv"])
    return out.rename(columns={"market_value": "mv"})[["instrument_id", "mv"]]


def _label_for(holding, classification: str) -> str:
    """The sector label this classification gives a non-fund holding.

    An ETF never reaches here — its sectors come from the compositions table,
    which is a vendor's answer already, so D13 changes nothing for funds. What
    it changes is the hand-typed label on a stock.
    """
    if classification == "mine":
        return str(holding.get("sector") or "").strip()
    if classification != "provider":
        raise ValueError(
            f"classification must be 'provider' or 'mine', not "
            f"{classification!r}.\n"
            f"  'provider' is the vendor's dated label (spec D13, the page's "
            f"sector view); 'mine' is instruments.sector, which wins "
            f"everywhere else."
        )
    if not str(holding.get("provider_sector_as_of") or "").strip():
        return ""           # undated: the raw vendor word, not a sectors.csv label
    return str(holding.get("provider_sector") or "").strip()


#: `by_sector()`'s fallback bucket for money it could not classify — an ETF
#: with no composition row on file, or a stock whose own label (`_label_for()`
#: above) comes back blank. Named once here, pre-push review 2026-10-07
#: (finding 1), so nothing downstream has to match on the literal string
#: "Other" to tell "we don't know this holding's sector" apart from "the
#: index genuinely cannot hold this sector" (Fixed Income / Cash, Crypto /
#: Digital Assets) — both used to land on the identical `only-mine` state in
#: `benchmark_sector_comparison()`, which then let `page.sector_rows()` print
#: its certain-zero caption ("SPY is an all-equity fund, so zero here is
#: certain") on money whose sector is simply unknown. `benchmark_sector_
#: comparison()` reads this constant to give the bucket its own `state`
#: instead.
UNCLASSIFIED_SECTOR = "Other"


def by_sector(
    path: Path = COMPOSITIONS_PATH, positions_path: Path | None = None,
    basis: str = "everything", classification: str = "provider",
    instruments_path: Path | None = None, clusters_path: Path | None = None,
) -> pd.DataFrame:
    """Look-through exposure per parent sector.

    An ETF is decomposed into its sectors; a stock counts as its own. Cash and
    FX count as Fixed Income / Cash. `direct_value` is the part you could see
    without this function — the gap between it and `market_value` is the
    exposure that was invisible.

    `estimated_value` is how much of the number comes from a composition row
    whose source is a bare estimate, so the confidence travels with the answer.

    **Which classification (spec D13).** `classification="provider"` — the
    default — reads `instruments.provider_sector`, a fetched, dated,
    vendor-named label, because he said the sector split is not his call:
    *"it's not my decision, it's how the S&P does their decisions, not mine"*
    (2026-10-01). It is **not GICS**; GICS is licensed and cannot be fetched,
    so the page names the vendor instead of implying an authority it has not
    got. `classification="mine"` reads `instruments.sector`, which keeps its
    sub-sector granularity, keeps driving `sectors.csv` and keeps winning
    everywhere else in the repository.

    ⚠️ **Only a DATED `provider_sector` is used.** That column holds two
    vocabularies — a dated row holds a `sectors.csv` label, an undated one the
    raw vendor word from before the mapping existed — and
    `instruments.dated_provider_sectors()` exists because the date is what says
    which. An undated row counts as having no vendor label at all: its money
    goes to `Other` and `sector_disagreements()` names it. Falling back to his
    own label would make this function claim a vendor said something nobody
    said.

    `instruments_path`/`clusters_path` (fix, 2026-10-07 final review, I5):
    forwarded straight through to `_holdings()` — see its own docstring.
    Before this, the two were silently unreachable from here at all, which
    meant `basis="pure-equity"`/`"minus-cash"` could only ever test
    `in_equity_base` against the real `data/clusters.csv`.
    """
    from src import book

    book.check_basis(basis)
    comp = load(path)
    held = _holdings(positions_path, instruments_path=instruments_path,
                     clusters_path=clusters_path, basis=basis)
    if held.empty:
        return pd.DataFrame(columns=["sector", "market_value", "direct_value",
                                     "estimated_value", "pct_of_nav"])

    by_ticker = {t: g for t, g in comp.groupby("ticker")}
    agg: dict[str, dict[str, float]] = {}

    def add(sector, value, *, direct, estimated):
        d = agg.setdefault(sector, {"market_value": 0.0, "direct_value": 0.0,
                                    "estimated_value": 0.0})
        d["market_value"] += value
        if direct:
            d["direct_value"] += value
        if estimated:
            d["estimated_value"] += value

    uncovered = []
    for _, h in held.iterrows():
        if h["type"] in ("cash", "fx"):
            add("Fixed Income / Cash", h["mv"], direct=True, estimated=False)
        elif h["type"] == "etf":
            rows = by_ticker.get(h["instrument_id"])
            if rows is None:
                uncovered.append(h["instrument_id"])
                add(UNCLASSIFIED_SECTOR, h["mv"], direct=False, estimated=False)
                continue
            est = (rows["source_quality"] == "estimate").any()
            for _, r in rows.iterrows():
                add(parent_sector(r["sector"]), h["mv"] * r["weight"],
                    direct=False, estimated=est)
        else:
            label = _label_for(h, classification)
            add(parent_sector(label) if label else UNCLASSIFIED_SECTOR,
                h["mv"], direct=True, estimated=False)

    nav = held["mv"].sum()
    out = pd.DataFrame([
        {"sector": s, **v,
         "pct_of_nav": round(v["market_value"] / nav * 100, 2) if nav else 0.0,
         "pct_estimated": round(v["estimated_value"] / v["market_value"] * 100, 1)
                          if v["market_value"] else 0.0}
        for s, v in agg.items()
    ])
    out.attrs["uncovered_etfs"] = uncovered
    return out.sort_values("market_value", ascending=False).reset_index(drop=True)


#: Instrument kinds no sector-data provider has ever assigned a sector to —
#: nobody classifies a VIX call or a crude-oil future into a SPY sector. Moved
#: from `ops/data_gaps.py` (Task 7): `sector_disagreements()` is the one place
#: that decides which instrument kinds are even eligible for a vendor-sector
#: comparison now, so a second copy of this set there could drift from the one
#: this function actually uses. Narrow on purpose — widening it to cover cash
#: (which has its own exclusion, `book.CASH_KINDS`) or a stock would let a
#: held option or future's "no dated vendor sector" state sit forever with no
#: way to clear, the same unclearable-queue failure `instruments.gaps()` was
#: fixed for on 2026-09-29.
NO_VENDOR_SECTOR_KINDS = {"option", "future"}


def _rollup_or_none(sector: str) -> str | None:
    """`sectors.parent()`, or None for a label `data/sectors.csv` does not know.

    A label neither table can roll up is not something `sector_disagreements()`
    can safely judge — raising here would stop every other instrument's row
    from being reported over one bad label, so the caller turns this into its
    own `unmappable` state instead of staying silent about it. Moved from
    `ops/data_gaps.py`'s `_parent_or_none()` (Task 7) — the comparison lives
    here now, so the helper it needs does too.
    """
    from src import sectors

    try:
        return sectors.parent(sector)
    except KeyError:
        return None


DISAGREEMENT_COLUMNS = ["instrument_id", "mine", "vendor", "vendor_source",
                        "vendor_as_of", "market_value", "dollar_effect",
                        "state"]


def sector_disagreements(basis: str = "everything", **paths) -> pd.DataFrame:
    """Held holdings where his label and the vendor's roll up differently.

    Spec D13: built for the portfolio page's by-sector panel, which is to
    "list every holding where the two disagree". A fact to surface, never a
    correction — his label still wins everywhere this function is not that
    panel, and nothing here rewrites either side.

    `vendor_source`/`vendor_as_of` (`instruments.provider_sector_source`/
    `provider_sector_as_of`) travel with every row — naming the vendor and
    the date is part of the promise, not just the label. `dollar_effect` is
    the money that moves between `mine`'s parent sector and `vendor`'s if the
    classification changes: for a single stock (an ETF never reaches this
    function — see the type filter below) that is the WHOLE position,
    because nothing here is split across sectors the way a fund's own
    composition is, so it is computed once here rather than re-derived by
    whatever reads this frame next.

    Five states, because they need five different sentences:
      disagree           both roll up, to different parents
      kept-mine          he looked and decided his own label stands anyway
                         (`instruments.sector_settled`, BMNR 2026-10-04) —
                         they still roll up to different parents, so this is
                         NOT agreement. Shown rather than hidden: collapsing
                         it into silence would read as the two agreeing,
                         which is the opposite of what he decided
      unmappable         one of the two is not in `sectors.csv`, so neither can
                         be rolled up to compare
      no-vendor-sector   nothing dated to compare against
      no-own-sector      `instruments.sector` has never been typed for this
                         holding at all — a blank label is a different
                         problem from one `sectors.csv` rejects, and reusing
                         `unmappable` for it used to render a sentence naming
                         nothing

    A holding whose two labels roll up to the SAME parent produces no row at
    all, `sector_settled` or not — agreement is agreement, and a settlement
    the vendor has since caught up to is not a disagreement any more.

    `ops/data_gaps.py` computed this itself until 2026-10-06. Two comparisons
    of the same two columns would drift, and the one the page shows and the
    one `ops/data_gaps.py`'s own `gaps()` reads have to be the same fact (P7)
    — not `/market` itself, which never calls this function directly; it
    only calls `exposure.coverage()` and runs `ops/data_gaps.py` as one of
    its own steps (fixed 2026-10-07, the same false attribution four other
    documents carried until this one).
    """
    from src import book
    from src import instruments as instruments_mod

    book.check_basis(basis)
    held = book.holdings(basis, **{k: v for k, v in paths.items()
                                   if k != "holdings_path"})
    if held.empty:
        return pd.DataFrame(columns=DISAGREEMENT_COLUMNS)

    inst = instruments_mod.load(
        paths.get("instruments_path") or instruments_mod.INSTRUMENTS_PATH
    ).fillna("").set_index("instrument_id")
    missing = [c for c in ("sector_settled", "provider_sector_source")
              if c not in inst.columns]
    if missing:
        raise ValueError(
            f"the instruments store has no {' / '.join(missing)} column(s), "
            f"so sector_disagreements() cannot tell a settled disagreement "
            f"from an open one, or name the vendor it is reading.\n"
            f"  Pass a frame (or instruments_path) that carries "
            f"{' and '.join(missing)}."
        )

    rows = []
    for _, h in held.iterrows():
        itype = str(h["instrument_type"]).strip().lower()
        if itype == "etf" or itype in book.CASH_KINDS \
                or itype in NO_VENDOR_SECTOR_KINDS:
            continue
        iid = h["instrument_id"]
        mine = str(h["sector"] or "").strip()
        vendor_as_of = str(h["provider_sector_as_of"] or "").strip()
        vendor = str(h["provider_sector"] or "").strip() if vendor_as_of else ""
        vendor_source = (str(inst.at[iid, "provider_sector_source"]).strip()
                         if iid in inst.index else "")
        mv = float(h["market_value"])

        def row(state: str) -> dict:
            return {"instrument_id": iid, "mine": mine, "vendor": vendor,
                   "vendor_source": vendor_source, "vendor_as_of": vendor_as_of,
                   "market_value": mv, "dollar_effect": mv, "state": state}

        if not vendor_as_of or not vendor:
            rows.append(row("no-vendor-sector"))
            continue
        if not mine:
            rows.append(row("no-own-sector"))
            continue

        his_parent = _rollup_or_none(mine)
        vendor_parent = _rollup_or_none(vendor)
        if his_parent is None or vendor_parent is None:
            rows.append(row("unmappable"))
            continue
        if his_parent == vendor_parent:
            continue   # agreement — nothing to report, settled or not (F6)

        settled = iid in inst.index and bool(
            str(inst.at[iid, "sector_settled"]).strip())
        rows.append(row("kept-mine" if settled else "disagree"))

    return pd.DataFrame(rows, columns=DISAGREEMENT_COLUMNS)


# `cap_breaches()` was deleted 2026-08-15, on Yaron's instruction: "all the
# rules of the twenty five percent cap and all of those stuff are irrelevant…
# we are building a new system, we're just using what we have as inspiration".
# It measured a 40% sector line taken from portfolio_thesis.md §3, archived the
# day before. A cap check with no cap behind it is theatre, and a number that
# looks like policy is worse than no number.
#
# `by_sector()` above still reports every sector's real look-through exposure —
# that is fact, not rule. When the new constitution defines sizing, build the
# check fresh against it. Old version: git history before 2026-08-15.


COVERAGE_COLUMNS = ["ticker", "market_value", "source_quality", "as_of", "why"]


def coverage(
    path: Path = COMPOSITIONS_PATH, positions_path: Path | None = None
) -> pd.DataFrame:
    """Held ETFs and how trustworthy each one's breakdown is.

    Visible always, blocking never. A missing row is reported rather than
    quietly leaving that money out of every sector total.

    Capable of being empty: a book holding zero ETFs reports nothing, rather
    than raising. `pd.DataFrame([])` has no `market_value` column to sort
    by, so the empty case used to crash `sort_values()` outright —
    `ops/data_gaps.py` carried a call-site workaround for exactly this until
    the fix landed here (Task 7, 2026-10-03 review).
    """
    comp = load(path)
    held = _holdings(positions_path)
    # `held` itself can be empty (no "type" column at all) as well as merely
    # containing no ETF row — guard on `.empty` first so neither shape raises
    # on the column lookup below.
    etfs = held[held["type"] == "etf"] if not held.empty else held
    first = comp.drop_duplicates("ticker").set_index("ticker")
    rows = []
    for _, h in etfs.iterrows():
        t = h["instrument_id"]
        if t not in first.index:
            rows.append({"ticker": t, "market_value": h["mv"],
                         "source_quality": "MISSING", "as_of": "",
                         "why": "no composition row — its money is in no sector total"})
            continue
        q, on = first.at[t, "source_quality"], first.at[t, "as_of"]
        why = []
        if q == "estimate":
            why.append("sector split is an estimate, not issuer data")
        # NOT counted as estimated in `by_sector()`, and deliberately: the
        # numbers came off the issuer's own page. What is worth saying is
        # that nothing refetches them, so they age until someone looks again.
        if q == "issuer-by-hand":
            why.append("issuer's own figures, typed in by hand — no refresh "
                       "will update them")
        if not str(on).strip():
            why.append("no as_of date — age unknown")
        rows.append({"ticker": t, "market_value": h["mv"], "source_quality": q,
                     "as_of": on, "why": "; ".join(why)})
    df = pd.DataFrame(rows, columns=COVERAGE_COLUMNS)
    return df.sort_values("market_value", ascending=False).reset_index(drop=True)


# ─── Task 5: look-through exposure to a COMPANY, as a floor ───────────────
#
# `by_sector()` above answers "what sector am I exposed to"; this answers the
# question that actually justified the panel in the design session — "how
# much NVDA do I really own, counting what my ETFs hold for me". The design
# session's own illustration: his smallest AI Infrastructure holding by direct
# value turned out to be one of his largest true exposures, and nothing in the
# record showed that. Panel 5, and the input panel 6 (theme overlap) and
# panel 7 (vs. the S&P) both build on.

COMPANY_COLUMNS = ["key", "label", "instrument_id", "direct_value",
                   "via_funds_value", "market_value", "pct_of_basis",
                   "is_floor", "kind", "funds"]

#: The label for the part of a fund its stored holdings do not reach — used
#: for both bucket `kind`s (`unaccounted` and `no-companies`); `kind` is what
#: tells them apart, not the text.
UNACCOUNTED = "(not named by the fund's holdings)"

#: `by_company()`'s `kind` column. A `company` row is a real company — direct
#: or through a fund, it makes no difference once it is named. `cash` is a
#: cash or FX balance (`instrument_type` in `book.CASH_KINDS`) — real money
#: in the book, but not a name, and a panel headed "true exposure to one
#: name" must not open with the dollar as his biggest single-name bet (review
#: round 2, 2026-10-06: `USD Cash` and `EUR Cash` were the top two `company`
#: rows). The other two are synthetic buckets: `unaccounted` is the part of a
#: fund its stored holdings do not reach (a real gap, `is_floor=True`);
#: `no-companies` is a fund with nothing to look through to at all — Treasury
#: bills, bitcoin, bullion, futures — which is CORRECT, not missing, so it
#: carries `is_floor=False`.
#:
#: **A `stock` lot is always `company`, and so, for now, is a `short`,
#: `future` or `option` lot — deliberately, not by oversight.** What kind of
#: row a derivative or a short position should be is an open question this
#: project has not settled (REBUILD.md §2c, the instrument taxonomy); this
#: function is not the place to invent an answer it does not have. Treating
#: them as `company` here is the current, honest default — a decision to
#: revisit when that question is actually decided, not a gap this docstring
#: should let a future reader mistake for a bug.
#:
#: Added 2026-10-06 (review round 1): before `kind` existed, a bucket and a
#: company he holds only through funds were both `direct_value == 0`, and a
#: bucket and a company he does not hold at all both had `instrument_id ==
#: ""` — a consumer could only tell them apart by matching on the
#: `UNACCOUNTED` label text.
KIND_COMPANY = "company"
KIND_CASH = "cash"
KIND_UNACCOUNTED = "unaccounted"
KIND_NO_COMPANIES = "no-companies"

#: Above this, an "accounted" fraction is close enough to the whole thing
#: that what is left out is the ordinary small-weight tail, not a real hole.
#: One constant, two different things it is compared against — never a
#: second number that happens to equal this one by coincidence (P7 slip
#: found 2026-10-07: it used to be a bare `0.995` literal, independently, in
#: both places below):
#:
#:   `_company_rows()` / `company_coverage()` compare it against a HELD
#:   fund's own STORED company weights summed — never its `coverage` column.
#:   Measured 2026-10-06: eight of twenty-six held funds have a stored sum
#:   below this threshold while their `coverage` is at or above it, so
#:   reading `coverage` there would call all eight complete. Exposure to a
#:   NAMED company can only come from a stored row — the same reasoning
#:   `ops/data_gaps.py`'s `unnamed_money()` already settled for the
#:   aggregate gap figure — so HIS dollar exposure through a fund is never
#:   judged by what the fund's source merely claims.
#:
#:   `benchmark_companies()` compares it against the INDEX's own `coverage`
#:   — what its SOURCE claimed to have captured, before the identical
#:   small-weight floor trimmed the stored rows. That is a different
#:   question (is a company genuinely outside the index, not how many of
#:   his own dollars a fund can name), and the index's own stored sum is
#:   NOT the right test for it: SPY's SPDR file claims the whole fund
#:   (`coverage` 1.0) and is still a stored sum of about 0.937, the ordinary
#:   shape of a 500-constituent file after the same floor — treating that
#:   as "incomplete" would read every company below the floor as merely
#:   unseen, which is false for the ~217 of them that are seen and simply
#:   too small to store, and worse than false for ASML, TSM and Samsung,
#:   which the source never saw at all because they are not constituents.
ACCOUNTED_COMPLETE_AT = 0.995


def company_key(symbol: str, instruments: pd.DataFrame) -> str:
    """One company, one key — using HIS spelling wherever his book has one.

    `market.holding_instrument()` is the join, and it already handles the four
    spellings that actually occur: `BRK.B` against his `BRKB`, Bloomberg's
    `005930 KS`, iShares' bare `005930`, and an exact id. Its guarantee is
    that it only ever returns an id ALREADY in his book, so a guessed exchange
    code can fail to join but can never invent one.

    For a company he does not hold, there is no id to return, and no safe way
    to invent one: letting the match see letters would marry XLP's `EL`
    (Estée Lauder) to an `EL.PA` (EssilorLuxottica). So the raw symbol is the
    key, two funds spelling it differently stay two rows, and
    `symbol_collisions()` reports the size of that rather than guessing.
    """
    from src import market

    resolved = market.holding_instrument(symbol, instruments)
    return resolved or str(symbol).strip().upper()


def _company_rows(basis, paths):
    """Every (cluster_id, company key, label, value, is_floor) in the basis.

    Shared by `by_company()` and `company_by_cluster()` — the decomposition of
    a fund into its companies is one piece of logic and a second copy of it
    would drift. Before this existed, the walk over `book.holdings()` — cash
    kept apart from a company, a fund's stored rows turned into company rows,
    the unaccounted remainder named per fund — was written out once inside
    `by_company()` alone; `company_by_cluster()` would have been a second,
    silently divergeable copy of exactly that walk.

    Each row also carries `kind` (`KIND_COMPANY` / `KIND_CASH` /
    `KIND_UNACCOUNTED` / `KIND_NO_COMPANIES` — see `by_company()`'s docstring
    for what each means) and `fund` (blank for a direct holding, the ETF's own
    ticker for anything reached through one). `by_company()` needs both to
    split `direct_value` from `via_funds_value` and to build its `funds`
    column; `company_by_cluster()` needs `kind` to keep cash and the two
    bucket kinds out of a frame that exists only to answer "same company,
    different theme".

    `paths` is the same `positions_path`/`instruments_path`/`clusters_path`/
    `holdings_path` quartet both callers accept, passed through as a dict
    rather than four repeated parameters — this function is private, and its
    only callers already hold that dict.
    """
    from src import book
    from src import instruments as instruments_mod
    from src import market

    book.check_basis(basis)
    held = book.holdings(basis, **{k: v for k, v in paths.items()
                                   if k != "holdings_path"})
    inst = instruments_mod.load(
        paths.get("instruments_path") or instruments_mod.INSTRUMENTS_PATH
    ).fillna("")
    hold = market.load_holdings(paths.get("holdings_path")
                                or market.HOLDINGS_PATH)
    by_fund = {t: g for t, g in hold.groupby("ticker")} if not hold.empty else {}
    names = dict(zip(inst["instrument_id"], inst["name"]))
    no_equity = _no_equity_inside()

    for _, h in held.iterrows():
        iid, mv = h["instrument_id"], float(h["market_value"])
        cid = str(h["cluster_id"]).strip()
        itype = str(h["instrument_type"]).lower()
        if itype != "etf":
            # A stock, and also cash, FX, a future, an option, a short: each
            # is its own exposure and none of them has a holdings table.
            # `book.CASH_KINDS` (not retyped — the same set `in_basis()`
            # reads) is cash and FX specifically: real money in the book,
            # but not a name, so it is never `company` — a panel headed
            # "true exposure to one name" must not open with the dollar as
            # his biggest single bet (review round 2, 2026-10-06). A stock,
            # and for now a short/future/option too (REBUILD.md §2c is still
            # open on what those should be), stays `company`.
            yield {"cluster_id": cid, "key": iid, "label": names.get(iid, iid),
                  "value": mv, "is_floor": False, "fund": "",
                  "kind": KIND_CASH if itype in book.CASH_KINDS else KIND_COMPANY}
            continue
        rows = by_fund.get(iid)
        if rows is None or rows.empty:
            # Two different reasons a fund has no holdings rows, and `kind`
            # is what tells a reader which: `no-companies` (SGOV, IBIT, SLV,
            # DBA) is CORRECT — nothing is absent, the money just is not in
            # companies, so `is_floor` is False. Anything else with no rows
            # is a real hole in the data, `unaccounted`, `is_floor=True`.
            # Either way the money is shown under the fund rather than
            # vanishing — `company_coverage()` is the function with the
            # `missing` vs `no-companies` prose for *why*.
            bucket_key, bucket_label = f"{iid} {UNACCOUNTED}", f"{iid} — {UNACCOUNTED}"
            if iid in no_equity:
                yield {"cluster_id": cid, "key": bucket_key, "label": bucket_label,
                      "value": mv, "is_floor": False, "fund": iid,
                      "kind": KIND_NO_COMPANIES}
            else:
                yield {"cluster_id": cid, "key": bucket_key, "label": bucket_label,
                      "value": mv, "is_floor": True, "fund": iid,
                      "kind": KIND_UNACCOUNTED}
            continue
        accounted = float(rows["weight"].sum())
        floor = accounted < ACCOUNTED_COMPLETE_AT
        for _, r in rows.iterrows():
            key = company_key(r["holding"], inst)
            yield {"cluster_id": cid, "key": key,
                  "label": names.get(key) or str(r["holding_name"]) or key,
                  "value": mv * float(r["weight"]), "is_floor": floor,
                  "fund": iid, "kind": KIND_COMPANY}
        # A fund clearing the ACCOUNTED_COMPLETE_AT "exact" threshold is not
        # THE SAME as summing to exactly 1.0 — QQQ's own stored weights are
        # 0.9975, not 1.0, and sixteen other "exact" funds measured
        # 2026-10-06 are the same. `floor` alone used to gate this row, so
        # each of those sixteen silently dropped up to half a percent of
        # itself: real money this function's own docstring promises never to
        # lose. `1e-6` is here only to swallow float noise around a fund that
        # is genuinely complete (BUG sums to exactly 1.0); it is not the
        # ACCOUNTED_COMPLETE_AT threshold, which is still what `is_floor` and
        # `company_coverage()`'s state read. A fund over-covered by a
        # hand-typed sheet (MAGS, 1.0007) has
        # a negative remainder here and gets no row — inventing negative
        # exposure would be its own kind of lie.
        remainder = 1.0 - accounted
        if remainder > 1e-6:
            yield {"cluster_id": cid, "key": f"{iid} {UNACCOUNTED}",
                  "label": f"{iid} — {UNACCOUNTED}", "value": mv * remainder,
                  "is_floor": floor, "fund": iid, "kind": KIND_UNACCOUNTED}


def by_company(basis: str = "everything", positions_path: Path | None = None,
               instruments_path: Path | None = None,
               clusters_path: Path | None = None,
               holdings_path: Path | None = None) -> pd.DataFrame:
    """What he is really exposed to, company by company. **A floor.**

    Direct holding plus the share each fund holds for him. Spec panel 5, and
    the design session's own illustration of why it earns its place: a name he
    barely holds directly can be one of his largest true exposures, and nothing
    in the record showed that.

    ⚠️ **`is_floor` is the whole honesty of this function.** A fund's stored
    holdings account for some fraction of it — nearly all, for an issuer's own
    file; a top-ten slice for a Yahoo fallback — and a total summed across both
    kinds is *at least* X. `company_coverage()` says which funds each answer
    rests on. Any row touching a partial fund is flagged, because the flag
    cannot be recomputed from the total afterwards.

    The part of a fund its holdings do not reach gets its own row, labelled
    `UNACCOUNTED`, per fund. Spreading it pro-rata would invent exposure he
    does not have; dropping it would make the column add to less than the
    basis with nothing saying so.

    **`kind` says what a row actually is, so a reader never has to match on
    the label text.** `company` is a real company, direct or through a fund.
    `cash` is a cash or FX balance — real money, never a name, so it must
    never be the answer to "what is my biggest single-name exposure"
    (`USD Cash` and `EUR Cash` were the top two `company` rows until review
    round 2 caught it). `unaccounted` is one fund's own bucket above — a real
    gap, `is_floor` always True. `no-companies` is a *different* bucket: a
    fund that owns nothing to look through to at all (T-bills, bitcoin,
    bullion, futures — `NO_EQUITY_INSIDE` in `ops/refresh_etf_holdings.py` is
    the authority). Before `kind` existed, both bucket rows carried
    `is_floor=True`, which lied about the `no-companies` ones — nothing is
    missing from SGOV, so it is `is_floor=False`. A `company` row he reaches
    only through funds and a bucket row both have `direct_value == 0`; an
    unheld company and a bucket both have a blank `instrument_id`. `kind` is
    the one column built to never be ambiguous the way those two are.

    **Only funds he actually holds are ever opened.** The loop walks
    `book.holdings()`, not every ticker in `etf_holdings.csv` — so SPY, which
    is in that table now for the benchmark panel but carries
    `status=benchmark` and is never a position, never gets a row here and
    none of its 283 constituent companies are ever added to his numbers.

    Built on `_company_rows()` now, the generator shared with
    `company_by_cluster()` — this function's own output did not change: it is
    still a group-by over the same per-holding decomposition, just no longer
    the only place that decomposition is written.
    """
    from src import book
    from src import instruments as instruments_mod

    book.check_basis(basis)
    paths = {"positions_path": positions_path,
             "instruments_path": instruments_path,
             "clusters_path": clusters_path, "holdings_path": holdings_path}
    held = book.holdings(basis, positions_path=positions_path,
                         instruments_path=instruments_path,
                         clusters_path=clusters_path)
    total = float(held["market_value"].sum()) if not held.empty else 0.0
    # `.fillna("")` matters here (M2, fix round 1): a blank `instrument_id`
    # read back as NaN would otherwise match nothing it's compared against
    # directly — except once, if a key constructed elsewhere ever reads back
    # as the literal string "nan", which is exactly the trap this repo has
    # been bitten by before. Cheap enough to keep as a standing guard.
    inst_ids = set(instruments_mod.load(
        instruments_path or instruments_mod.INSTRUMENTS_PATH
    ).fillna("")["instrument_id"])

    agg: dict[str, dict] = {}
    for r in _company_rows(basis, paths):
        d = agg.setdefault(r["key"], {"label": r["label"], "direct_value": 0.0,
                                      "via_funds_value": 0.0, "is_floor": False,
                                      "kind": r["kind"], "funds": set()})
        if r["fund"]:
            d["via_funds_value"] += r["value"]
            d["funds"].add(r["fund"])
        else:
            d["direct_value"] += r["value"]
        d["is_floor"] = d["is_floor"] or r["is_floor"]
        if r["label"] and not d["label"]:
            d["label"] = r["label"]

    out = pd.DataFrame([
        {"key": k, "label": v["label"],
         "instrument_id": k if k in inst_ids else "",
         "direct_value": round(v["direct_value"], 2),
         "via_funds_value": round(v["via_funds_value"], 2),
         "market_value": round(v["direct_value"] + v["via_funds_value"], 2),
         "pct_of_basis": round((v["direct_value"] + v["via_funds_value"])
                               / total * 100, 2) if total else 0.0,
         "is_floor": v["is_floor"],
         "kind": v["kind"],
         "funds": ", ".join(sorted(v["funds"]))}
        for k, v in agg.items()
    ], columns=COMPANY_COLUMNS)
    if out.empty:
        return out
    return out.reindex(
        out["market_value"].abs().sort_values(ascending=False).index
    ).reset_index(drop=True)


COMPANY_BY_CLUSTER_COLUMNS = ["cluster_id", "key", "label", "market_value",
                             "is_floor"]


def company_by_cluster(basis: str = "everything", **paths) -> pd.DataFrame:
    """Company exposure, split by the theme the money was bought under.

    `cluster_id` is INTENT and the company is FACT (D14). This frame holds both
    on purpose, and it is the only place they meet: it exists to answer "do two
    of my themes own the same companies", which needs the intent to name the
    pair and the fact to find the overlap. Nothing here makes one answer the
    other's question.

    🚨 **Restricted to `kind == "company"` rows.** `_company_rows()` also
    yields a cash/FX balance and the two bucket kinds a fund's own holdings
    cannot reach — none of those is a company, and this frame's only reason to
    exist is `clusters.overlap()`'s pairing of real companies. Keeping cash in
    would make two themes "share" a dollar of cash; keeping either bucket in
    would make two themes "share" a fund's unexplained remainder, which is not
    a company either theme owns. That is the single most important rule this
    function enforces, not a detail — an overlap figure inflated by cash would
    be a false statement about how his money is actually built.

    A retired cluster never appears here, same reason `clusters.tree()` leaves
    one out — `clusters.set_status()` refuses to retire one that still holds
    money, but a hand-edit or a re-bought instrument can still name it, and
    pairing a closed theme against a live one would read as an overlap that
    is not really there. A holding naming no live cluster at all (blank, or a
    name `data/clusters.csv` does not have) is left out for the same reason:
    it is not a theme, so a theme-pairing frame has nothing to do with it.
    """
    from src import book
    from src import clusters as clusters_mod

    book.check_basis(basis)
    live = clusters_mod.load(paths.get("clusters_path")
                             or clusters_mod.CLUSTERS_PATH)
    live_ids = set(live.loc[live["status"] != "retired", "cluster_id"])

    agg: dict[tuple[str, str], dict] = {}
    for r in _company_rows(basis, paths):
        if r["kind"] != KIND_COMPANY or r["cluster_id"] not in live_ids:
            continue
        k = (r["cluster_id"], r["key"])
        d = agg.setdefault(k, {"label": r["label"], "market_value": 0.0,
                               "is_floor": False})
        d["market_value"] += r["value"]
        d["is_floor"] = d["is_floor"] or r["is_floor"]
        if r["label"] and not d["label"]:
            d["label"] = r["label"]

    return pd.DataFrame([
        {"cluster_id": cid, "key": key, "label": v["label"],
         "market_value": round(v["market_value"], 2), "is_floor": v["is_floor"]}
        for (cid, key), v in agg.items()
    ], columns=COMPANY_BY_CLUSTER_COLUMNS)


COMPANY_COVERAGE_COLUMNS = ["ticker", "market_value", "state", "accounted_pct",
                            "source_coverage_pct", "as_of", "as_of_kind", "why"]


def company_coverage(basis: str = "everything", **paths) -> pd.DataFrame:
    """How much of each held fund's companies are actually on file.

    The honesty contract's "how much of this is estimated", for the company
    panels rather than the sector one. `coverage()` is its sibling and answers
    the same question about SECTOR breakdowns; the two are separate because a
    fund can have a dated sector split and no holdings at all.

    ⚠️ **Two percentages, never collapsed into one.** `accounted_pct` is how
    much of the fund is written down company by company — the stored weights,
    summed — and it is what decides `state` below: exposure to a NAMED
    company can only ever come from a stored row, so the honest floor test
    reads the rows, not the source. `source_coverage_pct` is the separate
    question of how much of the fund its SOURCE claimed to account for,
    before `ops/refresh_etf_holdings.py` dropped everything under his
    small-weight floor. The two differ — almost always `accounted_pct` is the
    lower of the two — because that dropped tail is real exposure no company
    total can see. `ops/data_gaps.py`'s `unnamed_money()` is where this exact
    question was settled for the aggregate gap report, by the same reasoning;
    read its docstring before changing either column here. A session folded
    the two into one number on 2026-10-06 and quoted the flattering one four
    times — that is the bug this function exists not to repeat.

    Four states, and the distinction between the last two is the one that
    matters: `no-companies` is a fund with nothing to hold — T-bills, bitcoin,
    bullion, futures — and is CORRECT, while `missing` is a real hole. Folding
    them together would either make the floor look worse than it is or hide
    real money.

    ⚠️ **A fund whose own file adds to more than 100% is never clamped.**
    MAGS's seven companies, entered by hand to four decimal places from the
    issuer's own sheet, sum to 1.0007. `accounted_pct` reports that number
    honestly (over 100, not capped at it) and `why` says so in plain words —
    clamping it to look complete would hide the one fund whose data is known
    to be off, which is worse than a number over 100. `ops/data_gaps.py`
    clamps this same arithmetic with `min(weight_sum, coverage)`, which is
    why the two reports disagree on MAGS specifically; that disagreement is
    recorded, not a bug to reconcile here.
    """
    from src import book
    from src import market

    book.check_basis(basis)
    held = book.holdings(basis, **{k: v for k, v in paths.items()
                                   if k != "holdings_path"})
    etfs = held[held["instrument_type"] == "etf"] if not held.empty else held
    hold = market.load_holdings(paths.get("holdings_path")
                                or market.HOLDINGS_PATH)
    by_fund = {t: g for t, g in hold.groupby("ticker")} if not hold.empty else {}
    no_companies = _no_equity_inside()

    rows = []
    for _, h in etfs.iterrows():
        t = h["instrument_id"]
        g = by_fund.get(t)
        if g is None or g.empty:
            if t in no_companies:
                rows.append({"ticker": t, "market_value": h["market_value"],
                             "state": "no-companies", "accounted_pct": 0.0,
                             "source_coverage_pct": 0.0,
                             "as_of": "", "as_of_kind": "",
                             "why": f"holds {no_companies[t]} — it owns no "
                                    f"companies, so no row for it is correct"})
            else:
                rows.append({"ticker": t, "market_value": h["market_value"],
                             "state": "missing", "accounted_pct": 0.0,
                             "source_coverage_pct": 0.0,
                             "as_of": "", "as_of_kind": "",
                             "why": "no holdings on file — none of its money "
                                    "reaches a company on this page"})
            continue
        accounted = float(g["weight"].sum())
        source_cov = float(g["coverage"].max())
        exact = accounted >= ACCOUNTED_COMPLETE_AT
        if accounted > 1.0 + 1e-6:
            # MAGS, found reviewing round 0 (2026-10-06): seven companies
            # entered by hand to four decimal places from the issuer's own
            # file, rounded independently, summing to 1.0007 rather than
            # 1.0. `ops/data_gaps.py` clamps this with `min(weight_sum,
            # coverage)` before reporting it — which is why the two now
            # disagree, and why clamping was rejected here: it would make
            # the one fund with a known-bad file read as perfect. State
            # stays `exact` (it really did clear ACCOUNTED_COMPLETE_AT) but
            # `why` is never blank for an overstated fund — a non-developer
            # reader needs the plain-English flag, not a clamp that hides it.
            #
            # Round 1 wrote this with a percentage at 0 decimal places —
            # "adds to 100% ... over 100%" — which self-contradicts the
            # moment the real figure (100.07%) rounds back to the very
            # number it is claiming to exceed (review round 2, 2026-10-06).
            # Dollars instead: he thinks in dollars, the amount is small, and
            # a dollar figure cannot collapse into the threshold it is
            # describing the way a rounded percentage can.
            overstated_by = h["market_value"] * (accounted - 1.0)
            why = (f"its own holdings file was typed in by hand from the "
                   f"issuer's sheet and adds up to slightly more than the "
                   f"whole fund — about ${overstated_by:,.2f} more than it "
                   f"should, so its company figures here are overstated by "
                   f"about that much")
        elif not exact:
            # "exact" reads the STORED weight sum, never `coverage`
            # (ACCOUNTED_COMPLETE_AT's own docstring) — a fund whose source
            # claims the whole thing can still be a floor here if its rows
            # were trimmed hard enough after that claim.
            why = (
                f"its holdings on file name {accounted:.0%} of the fund company "
                f"by company — its source accounted for {source_cov:.0%} before "
                f"rows under his small-weight floor were dropped — so every "
                f"company total touching it is a floor")
        else:
            why = ""
        if str(g["as_of_kind"].iloc[0]) == "read":
            why = ("; ".join(x for x in [why, "nobody published a date — "
                   "as_of is the day we looked, not how old the holdings are"]
                   if x))
        rows.append({"ticker": t, "market_value": h["market_value"],
                     "state": "exact" if exact else "floor",
                     "accounted_pct": round(accounted * 100, 1),
                     "source_coverage_pct": round(source_cov * 100, 1),
                     "as_of": g["as_of"].iloc[0],
                     "as_of_kind": g["as_of_kind"].iloc[0], "why": why})
    df = pd.DataFrame(rows, columns=COMPANY_COVERAGE_COLUMNS)
    return df.sort_values("market_value", ascending=False).reset_index(drop=True)


@functools.lru_cache(maxsize=1)
def _load_refresh_etf_holdings():
    """Load `ops/refresh_etf_holdings.py` by path, exactly once per process.

    `ops/` is not a package, so this is the same `importlib` pattern
    `ops/data_gaps.py` and `ops/refresh_market.py` already use to load a
    sibling. Cached (review round 1, I-1): both `by_company()` and
    `company_coverage()` call `_no_equity_inside()` below, and the page calls
    both on every render. `exec_module()` re-runs the whole ~1,000-line file
    — a fresh `import requests` included — and its own `sys.path.insert`
    left one duplicate entry per call. `lru_cache` makes the cost one-time,
    the same as an ordinary top-of-file import would be.
    """
    import importlib.util

    path = PROJECT_ROOT / "ops" / "refresh_etf_holdings.py"
    spec = importlib.util.spec_from_file_location("refresh_etf_holdings", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _no_equity_inside() -> dict[str, str]:
    """`ops/refresh_etf_holdings.py`'s NO_EQUITY_INSIDE, read not retyped.

    One definition (P7): the list of funds that own no companies is already
    maintained beside the fetcher that must not ask a provider about them, and
    a second copy here would drift the day a fund is added to one of them.
    A fresh `dict()` copy on every call, even though the module itself is
    cached — callers are free to treat the result as theirs to hold.
    """
    return dict(_load_refresh_etf_holdings().NO_EQUITY_INSIDE)


def symbol_collisions(positions_path: Path | None = None,
                      instruments_path: Path | None = None,
                      clusters_path: Path | None = None,
                      holdings_path: Path | None = None) -> pd.DataFrame:
    """Unheld holdings that look like one company under two keys.

    ⚠️ **An UPPER BOUND, never a count.** The match is a two-word prefix of
    the holding's name — the only thing two differently-spelled, unheld
    symbols share — and two different companies can share one. The 2026-10-06
    review found this exact failure live in the book: two distinct Abu Dhabi
    banks, `ADCB` and `ADIB`, both begin "ABU DHABI" and were counted as one
    collision though they are not the same institution. So read what this
    returns as "at most N apparent collisions," never "N" — panel 7 must say
    "at most," not quote the row count as fact.

    The dangerous direction, and the reason this is a report and not a fix. A
    company he holds joins on his own spelling; one he does not has no id to
    join to, so two funds writing it differently stay two rows. That
    understates theme overlap and **overstates** how many distinct bets he has
    — the only bias on this page that flatters him.

    Nothing here merges anything. Matching on the name is exactly what
    `holding_instrument()` refuses to do, and for a good reason: two companies
    can share a name prefix, which is also why the result above is a ceiling
    and not a fact. This counts the cases so panel 7 can say how big the
    effect could be, instead of implying there is none.

    **Restricted to funds he actually holds** (review round 1, I-4). Before
    this, `etf_holdings.csv` was read whole — SPY, filed there now for the
    benchmark panel, and any fund he has since sold both padded the number
    with collisions that have nothing to do with his own book.
    """
    from src import book
    from src import instruments as instruments_mod
    from src import market

    hold = market.load_holdings(holdings_path or market.HOLDINGS_PATH)
    if hold.empty:
        return pd.DataFrame(columns=["name", "keys", "funds"])
    held = book.holdings("everything", positions_path=positions_path,
                         instruments_path=instruments_path,
                         clusters_path=clusters_path)
    held_tickers = set(held["instrument_id"]) if not held.empty else set()
    hold = hold[hold["ticker"].isin(held_tickers)]
    if hold.empty:
        return pd.DataFrame(columns=["name", "keys", "funds"])
    inst = instruments_mod.load(
        instruments_path or instruments_mod.INSTRUMENTS_PATH).fillna("")
    df = hold.assign(key=[company_key(s, inst) for s in hold["holding"]])
    df = df[~df["key"].isin(set(inst["instrument_id"]))]
    df["name"] = (df["holding_name"].astype(str).str.upper()
                  .str.replace(r"[^A-Z0-9 ]", " ", regex=True)
                  .str.split().str[:2].str.join(" "))
    rows = []
    for name, g in df.groupby("name"):
        keys = sorted(set(g["key"]))
        if len(keys) > 1:
            rows.append({"name": name, "keys": ", ".join(keys),
                         "funds": ", ".join(sorted(set(g["ticker"])))})
    return pd.DataFrame(rows, columns=["name", "keys", "funds"])


# ─── Does a fund's own stored weights sum to the whole fund it claims? ─────
#
# Found reviewing Task 7 (2026-10-06): MAGS's seven companies, typed in by
# hand to four decimal places from the issuer's own sheet, sum to 1.0007 in
# BOTH `etf_compositions.csv` and `etf_holdings.csv` — the roundings do not
# cancel. The dollar effect today is a few dollars on a six-figure book; the
# mechanism is not immaterial, because the same silence would hide thousands
# on a future hand-entered fund off by a few per cent. The honesty contract
# ("an exposure answer says how much of itself is a guess") has nothing today
# that says so.

#: Below this a residue is noise — not float noise (these sums are only a
#: handful of 4-6 decimal numbers; that would be ~1e-15), but the ordinary
#: rounding every stored weight already carries at its own precision. Real
#: data measured 2026-10-06: every ticker but MAGS sums to within 2e-6 of its
#: target; MAGS is 7e-4, 350x that. 1e-4 sits cleanly between the two.
_RESIDUE_EPSILON = 1e-4

#: `etf_holdings.csv` rows whose SMALLEST stored weight clears this bar are
#: immune to `ops/refresh_etf_holdings.py`'s `MIN_HOLDING_WEIGHT` (0.0005) —
#: nothing in the fund was small enough to be anywhere near that cutoff, so an
#: under-shoot cannot be the floor's doing. Real data measured 2026-10-06: the
#: smallest stored weight for every OTHER held fund is ≤0.019; MAGS's is
#: 0.1399, seven times this bar. Without this gate, IWM alone (coverage 0.9994,
#: stored weights summing to 0.7927 — its long tail of sub-floor positions)
#: would print as an "under" anomaly — restating money `ops/data_gaps.py`'s
#: `unnamed_money()` already names honestly as a floor. Run that command for
#: the real figure; it moves with his book, so it is never quoted here.
_FLOOR_IMMUNE_MIN_WEIGHT = 0.05

WEIGHT_RESIDUE_COLUMNS = ["ticker", "table", "weight_sum", "target",
                         "direction", "market_value", "dollar_effect", "why"]


def _residue_row(ticker: str, table: str, weight_sum: float, target: float,
                 market_value: float) -> dict:
    """One row of `weight_residue()` — shared so both tables read identically.

    `target` is what "the whole fund" means for this row: always 1.0 for
    `etf_compositions.csv`; the row's own `coverage` for `etf_holdings.csv`,
    because that is what the table's own docstring says `coverage` IS — how
    much of the fund the source claims to span. Comparing against a hardcoded
    1.0 there would also miss a fund whose rows sum to MORE than its own
    source ever claimed while still reading under 100% — a real anomaly
    `company_coverage()`'s `accounted > 1.0` check cannot see.
    """
    residue = weight_sum - target
    direction = "over" if residue > 0 else "under"
    dollar_effect = round(market_value * residue, 2)
    return {
        "ticker": ticker, "table": table,
        "weight_sum": round(weight_sum, 6), "target": round(target, 6),
        "direction": direction,
        "market_value": round(market_value, 2), "dollar_effect": dollar_effect,
        "why": (f"{ticker}'s {table} rows sum to {weight_sum:.4f} against "
               f"{target:.4f} it claims to cover — {abs(residue) * 100:.2f}% "
               f"{direction}, about ${abs(dollar_effect):,.2f} on his book at "
               f"today's market value. Not normalised, clamped or corrected: "
               f"the stored weights are reported exactly as filed."),
    }


def weight_residue(
    path: Path = COMPOSITIONS_PATH, holdings_path: Path | None = None,
    positions_path: Path | None = None, basis: str = "everything",
) -> pd.DataFrame:
    """Held funds whose stored weights do not sum to the whole fund they claim.

    **Not a third opinion.** `ops/data_gaps.py`'s `unnamed_money()` clamps a
    fund's company weights with `min(weight_sum, coverage)` before turning the
    remainder into a dollar figure — the right call there, because that
    function is answering "how much of this fund's money has no company name
    at all", and a weight sum that overshoots its own coverage would otherwise
    invent negative unnamed money. `company_coverage()` deliberately does NOT
    clamp, and already prints MAGS's one known case in English
    (`accounted_pct` over 100%, `why` says so) — but only for
    `etf_holdings.csv`, only in the over-1.0 direction, and with no number a
    test or a later reader can check directly. This function is the narrower,
    numeric sibling of that one sentence: it does not re-answer "how much of
    the fund is accounted for" (`coverage()`, `company_coverage()` and
    `unnamed_money()` already do, and disagree with each other on purpose, for
    reasons their own docstrings give, and it changes no number any of them
    returns) — it answers one question only, for BOTH market tables: do a
    fund's own stored weights sum to what it claims to cover, in EITHER
    direction, by more than ordinary rounding.

    `etf_compositions.csv` (sector weights): every ticker's rows are already
    required to sum to 1.0 within ±0.02 just to load (`_schema()`'s check) —
    so a real residue inside that allowed band, on a fund worth real money,
    has always been able to hide in total silence. Checked for every held
    ticker, both directions, no gate: there is no floor concept for this
    table — a sector breakdown is always meant to be the whole fund, so
    `target` is always 1.0.

    `etf_holdings.csv` (company weights): most held funds are a DELIBERATE
    floor (`MIN_HOLDING_WEIGHT` drops a small-weight tail after fetching) and
    summing to well under `coverage` is correct, not a residue — IWM's stored
    weights are 21 points under its own 99.94% coverage, which is
    `unnamed_money()`'s territory, not this function's. So the UNDER direction
    here is gated on `_FLOOR_IMMUNE_MIN_WEIGHT`: only a fund whose smallest
    stored row is nowhere near the floor is even a candidate for "sums to less
    than it should" being new information rather than the floor restated. The
    OVER direction carries no such gate — the floor only ever REMOVES weight,
    so stored weights exceeding their own fund's `coverage` can never be a
    floor artifact, at any size.

    Reports the raw residue, never adjusts, clamps or normalises a stored
    weight. Those figures match the issuer's own published numbers, the row
    already records that it was hand-entered, and silently correcting a
    figure he verified against the issuer would be worse than reporting the
    residue plainly.
    """
    from src import book
    from src import market

    book.check_basis(basis)
    held = book.holdings(basis, positions_path=positions_path)
    mv_by_ticker = (dict(zip(held["instrument_id"], held["market_value"]))
                   if not held.empty else {})

    rows = []

    comp = load(path)
    if not comp.empty:
        for ticker, g in comp.groupby("ticker"):
            if ticker not in mv_by_ticker:
                continue
            weight_sum = float(g["weight"].sum())
            if abs(weight_sum - 1.0) <= _RESIDUE_EPSILON:
                continue
            rows.append(_residue_row(ticker, "etf_compositions.csv",
                                     weight_sum, 1.0, mv_by_ticker[ticker]))

    hold = market.load_holdings(holdings_path or market.HOLDINGS_PATH)
    if not hold.empty:
        for ticker, g in hold.groupby("ticker"):
            if ticker not in mv_by_ticker:
                continue
            target = float(g["coverage"].max())
            weight_sum = float(g["weight"].sum())
            residue = weight_sum - target
            if abs(residue) <= _RESIDUE_EPSILON:
                continue
            if residue < 0 and float(g["weight"].min()) < _FLOOR_IMMUNE_MIN_WEIGHT:
                continue   # under-coverage a small-weight floor explains
            rows.append(_residue_row(ticker, "etf_holdings.csv",
                                     weight_sum, target, mv_by_ticker[ticker]))

    return pd.DataFrame(rows, columns=WEIGHT_RESIDUE_COLUMNS)


# ─── Task 8: against the S&P — by sector, and company by company ─────────
#
# Two comparisons, one join, because both are built from the same two tables
# Task 2 brought in: SPY's own sector weights (`etf_compositions.csv`) and
# SPY's own company holdings (`etf_holdings.csv`), both carrying
# `ticker="SPY"`. `instruments.csv` carries SPY with `status=benchmark`,
# never `active`, so it can never reach a holding, a basis or an exposure
# total — the functions below are the first ones that read SPY's rows ON
# PURPOSE, by name, rather than skipping them the way `by_sector()` and
# `by_company()` do.

BENCHMARK_TICKER = "SPY"

BENCHMARK_WEIGHTS_COLUMNS = ["sector", "weight_pct", "as_of", "source_quality"]


def benchmark_weights(ticker: str = BENCHMARK_TICKER,
                      path: Path = COMPOSITIONS_PATH) -> pd.DataFrame:
    """The benchmark's own sector weights, rolled up the way his book is.

    Spec panel 3 shows this beside his look-through, and the gap between them
    **as a difference**. Not as a target and never as a cap: the caps were
    deleted with the constitution on 2026-08-15 and nothing has replaced them.

    SPY is in `instruments.csv` with `status=benchmark`, never `active`, so
    it can never reach a holding, a basis or an exposure total.
    `ops/refresh_market.py`'s `BENCHMARK_TICKERS` is what fetches it, through
    `/market`, with his two yeses.

    **Empty is a real answer and carries its reason** in `attrs["why"]`. A zero
    in the difference column would say the S&P holds none of that sector,
    which is a false statement about the benchmark rather than a missing
    number; a blank would say there is nothing to say.

    ⚠️ **This is the benchmark's OWN weights only — not a comparison.**
    `benchmark_sector_comparison()` is the safe way to put this beside his
    own `by_sector()`: it joins the two itself, names a `state`, and carries
    a basis caveat (`attrs["compare_note"]`). Joining this frame against his
    own by hand and filling blanks with `fillna(0.0)` is exactly the move
    that read 152 real S&P 500 constituents as absent before fix round 1 —
    the same hazard, for sectors, that `benchmark_sector_comparison()` was
    added in fix round 1 (I-B) to remove.
    """
    comp = load(path)
    rows = comp[comp["ticker"] == ticker] if not comp.empty else comp
    if rows.empty:
        out = pd.DataFrame(columns=BENCHMARK_WEIGHTS_COLUMNS)
        out.attrs["why"] = (
            f"{ticker}'s own sector breakdown has not been fetched yet, so "
            f"there is nothing to compare against. Run /market — it asks "
            f"before it checks and again before it saves."
        )
        return out
    agg: dict[str, float] = {}
    for _, r in rows.iterrows():
        parent = parent_sector(r["sector"])
        agg[parent] = agg.get(parent, 0.0) + float(r["weight"]) * 100
    out = pd.DataFrame(
        [{"sector": s, "weight_pct": round(w, 2),
          "as_of": rows["as_of"].iloc[0],
          "source_quality": rows["source_quality"].iloc[0]}
         for s, w in agg.items()],
        columns=BENCHMARK_WEIGHTS_COLUMNS,
    )
    out.attrs["why"] = ""
    return out.sort_values("weight_pct", ascending=False).reset_index(drop=True)


BENCHMARK_COMPANY_COLUMNS = ["key", "label", "issuer_group", "mine_pct",
                             "benchmark_pct", "benchmark_pct_at_most",
                             "difference", "difference_floor", "state",
                             "is_floor"]

#: The exact sentence a `below-floor` row's bound means, named once so a
#: future caption never re-derives it or retypes it differently than the
#: code that computes it.
#:
#: Fix round 1 (2026-10-07), C-A — the bug this function existed to avoid,
#: shipped anyway. The first version read absence from SPY's STORED rows as
#: absence from the index itself, full stop: "ASML, TSM and Samsung really
#: are outside the S&P 500" was true of those three and FALSE of the other
#: 152 measured against the union of his seven held Select Sector SPDRs
#: (which hold only S&P 500 members by construction) — Akamai, American
#: Water Works, Trimble, Xylem, Pentair, Bunge and the rest all read
#: `only-mine` with `benchmark_pct=0.00`: 152 false statements that the
#: index holds none of a company it does hold, on the panel he called the
#: most important one. The cause: SPY's stored rows stop at a 0.05% floor,
#: and "absent from the stored rows" is not "absent from the index" — it is
#: "at most the floor, possibly nothing", which is true of ASML and of
#: Akamai alike, and is the only thing this function can honestly say about
#: either without a source it does not have.
BELOW_FLOOR_WORDING = (
    "the index holds at most the stored floor of this, or none — his "
    "holding is absent from the index's STORED rows, which stop at a "
    "small-weight floor rather than listing every constituent, so this is "
    "a bound, never a confirmed absence"
)

#: `unknown`'s own sentence, kept distinct from `BELOW_FLOOR_WORDING` — the
#: two are not the same uncertainty. A `below-floor` row still carries a
#: real bound (`benchmark_pct_at_most`) because the index's SOURCE claimed
#: the whole fund; an `unknown` row has no such bound because the SOURCE
#: itself never claimed to have seen the whole fund, so even "at most the
#: floor" would be a guess dressed as a number.
UNKNOWN_WORDING = (
    "the index's own source did not claim to cover the whole fund, so "
    "there is not even a reliable bound here — treat this the way an "
    "un-fetched figure is treated, not as evidence of absence or presence"
)

#: `only-index`'s own caveat when his side could be hiding a sliver — fix,
#: 2026-10-07 final review (I2), the mirror of `BELOW_FLOOR_WORDING` for HIS
#: side rather than the index's. Only used when `benchmark_companies()`
#: finds at least one held fund that is itself a floor (`any_mine_floor`);
#: `STATE_WORDING["only-index"]` stays `""` otherwise — see where it is
#: built, below.
ONLY_INDEX_FLOOR_WORDING = (
    "he holds none of this that any fund names, company by company — but "
    "at least one of his held funds is itself a floor (its own stored "
    "companies do not add up to the whole fund), so a sliver could be "
    "sitting below that floor, unseen; this is a bound, never a confirmed "
    "absence"
)

#: `both`'s own caveat when HIS side is the floor — fix, pre-push review
#: 2026-10-07 (finding 2). Unlike `ONLY_INDEX_FLOOR_WORDING`, this cannot be
#: swapped into `STATE_WORDING` for the whole call: `is_floor` on a `both`
#: row is a fact about THAT company (which fund, if any, reached it, and
#: whether that fund's own stored rows are complete), not about his held
#: funds as a whole the way `any_mine_floor` is. `page.
#: benchmark_company_rows()` reads `is_floor` off each row itself and
#: substitutes this text there, the one state this dict cannot answer alone.
BOTH_FLOOR_WORDING = (
    "both sides show this company, but his own figure comes through a fund "
    "whose stored holdings stop short of the whole fund — his true share "
    "could be larger than shown, so the exact difference is withheld in "
    "favor of a lower bound (at least this much)"
)

#: One lookup, keyed on every value `state` can hold, so a reader of the
#: `state` column can always find its caveat (or confirm there is none)
#: through this single dict rather than a fifth copy of the prose.
#: `attrs["state_wording"]` carries this unchanged, EXCEPT `only-index`
#: (fix round 1, C-D: a hedge that reaches no consumer, and is never
#: tested, is not a deliverable) — `benchmark_companies()` swaps in
#: `ONLY_INDEX_FLOOR_WORDING` for that one key when `any_mine_floor` is
#: True for the call that built the frame (I2): the caveat is real only
#: when at least one of HIS held funds is itself a floor, so it cannot be
#: a fixed value in this dict the way `below-floor`'s and `unknown`'s are.
#: `both` stays `""` here for the identical reason `only-index` used to —
#: its own floor caveat (`BOTH_FLOOR_WORDING`) is per-row, not per-call, so
#: `page.benchmark_company_rows()` applies it directly rather than through
#: this dict.
STATE_WORDING = {
    "both": "",
    "only-index": "",
    "below-floor": BELOW_FLOOR_WORDING,
    "unknown": UNKNOWN_WORDING,
}

#: A share-class marker exactly as the index's own `holding_name` spells it
#: — " CL A", " CL. B", " CLASS A", " CLASS A SHARES". Reused rather than
#: guessed, per instruction: the index's `holding_name` is the one place a
#: class marker is ever written down in this table; a row with no index
#: match (see `_issuer_group()`) keeps its plain label instead of a
#: stripped guess at one.
_CLASS_SUFFIX = re.compile(
    r"\s+(?:CL\.?\s*[A-Z]|CLASS\s*[A-Z])(?:\s+SHARES)?\s*$", re.IGNORECASE)


def _issuer_group(holding_name: str, fallback: str) -> str:
    """One issuer, however many of its share classes the index lists.

    Added fix round 1 (2026-10-07) — the Alphabet problem. GOOG and GOOGL
    are correctly two rows (different securities, different weights), but
    their combined difference would rank third on the real book today and
    never appeared anywhere, because nothing grouped them. This never
    merges the two ROWS — `company_key()` still keeps them apart, correctly
    — it only labels which issuer they share, so a renderer can offer a
    grouped view without this function inventing one unasked.
    """
    name = str(holding_name or "").strip().upper()
    if not name:
        return fallback
    stripped = _CLASS_SUFFIX.sub("", name).strip()
    return stripped or fallback


def non_equity_in_basis(basis: str = "everything",
                        mine: pd.DataFrame | None = None, **paths) -> float:
    """Dollars, inside `basis`, held in a fund that owns no equities at all.

    Added fix round 1 (2026-10-07), C-C. A `pure-equity` compare-note used to
    call itself the honest, equity-to-equity comparison. `src/book.py`'s own
    module docstring already says otherwise: *"'pure-equity' is his phrase
    for the third one and it is not literally only equities — crypto,
    bullion and the commodity funds stay in"* — `in_equity_base` is a
    CLUSTER flag, not an instrument-type filter, and his bitcoin, bullion
    and commodity-futures clusters are flagged `True`.

    `by_company()`'s `KIND_NO_COMPANIES` is already the one place that names
    exactly these funds (T-bills, bitcoin, bullion, futures —
    `NO_EQUITY_INSIDE` is its own authority); this sums their market value
    within `basis` rather than retyping that list, or the figure it adds up
    to, a second time. Measured 2026-10-07: pure-equity ≈ 9.8% of its NAV
    (DBA + IBIT + SLV); minus-cash ≈ 19.0% (the same three plus the T-bills
    `pure-equity` excludes) — called by the compare-note text below, never
    typed there.

    `mine`, when given, is an already-computed `by_company(basis, **paths)`
    frame (fix round 2, MIN-A): `benchmark_companies()` holds one by the
    time it builds its compare-note, and recomputing it here roughly
    doubled that call's own `by_company()` cost — the page calls this.
    Passed through rather than retyped; computed fresh only when nobody
    already has one (the empty-index path, and `benchmark_sector_comparison()`,
    neither of which ever holds a `by_company()` frame to begin with).
    """
    from src import book

    book.check_basis(basis)
    rows = mine if mine is not None else by_company(basis, **paths)
    if rows.empty:
        return 0.0
    return float(
        rows.loc[rows["kind"] == KIND_NO_COMPANIES, "market_value"].sum()
    )


def _compare_note(basis: str, mine: pd.DataFrame | None = None,
                  **paths) -> str:
    """The sentence `benchmark_companies()` attaches as `attrs["compare_note"]`.

    Fix round 1 (2026-10-07), C-C: the `pure-equity` version of this sentence
    called itself "the honest comparison" and claimed both sides were shares
    of an equity book. No basis is called "honest" any more.

    Fix round 2 (2026-10-07): two more holes at the same spot. `minus-cash`
    named the non-equity slice without sizing it, even though it is the
    BIGGEST of the three (≈19% of that basis vs ≈10% of `pure-equity`'s,
    because `minus-cash` still keeps the T-bills `pure-equity` excludes) —
    now sized the same way `pure-equity`'s already was. `pure-equity`'s own
    figure moved from a dollar amount to a SHARE of the basis: the sentence
    never stated the base, so a bare dollar gave him nothing to judge it
    against, and cent precision on a rounding-level number implied an
    accuracy neither this function nor its data has.

    `mine`, when given, is passed straight through to `non_equity_in_basis()`
    rather than letting it fetch a second `by_company()` frame (MIN-A) —
    only meaningful for `pure-equity`/`minus-cash`, where a number is
    actually computed; harmless to pass for `everything`, which ignores it.
    """
    from src import book

    # `holdings_path` is `by_company()`'s own kwarg, not `book.nav()`'s —
    # `lots()`, underneath `nav()`, does not accept it. Filtered here
    # rather than upstream, so every OTHER caller of this module can keep
    # passing the same four-path kwarg set everywhere else.
    nav_paths = {k: v for k, v in paths.items() if k != "holdings_path"}

    if basis in ("pure-equity", "minus-cash"):
        non_equity = non_equity_in_basis(basis, mine=mine, **paths)
        nav = book.nav(basis, **nav_paths)
        pct = round(non_equity / nav * 100, 1) if nav else 0.0
        if basis == "pure-equity":
            return (
                "This uses the 'pure-equity' basis — cash, FX and T-bills "
                "are left out on his side, and it is the closest "
                "comparison available, not a fully equity-to-equity one: "
                "'pure-equity' is his own phrase, and about "
                f"{pct:.1f}% of this basis is still held in a bitcoin, "
                f"bullion or commodity-futures fund that owns no equities "
                f"at all. That slice dilutes every one of his percentages "
                f"against an index that is entirely equities — the same "
                f"arithmetic effect cash has under the other two bases, "
                f"just smaller."
            )
        return (  # "minus-cash"
            "This uses the 'minus-cash' basis — his percentage excludes "
            "cash and FX but still counts everything else, including "
            "funds that hold no equities at all (bullion, crypto, "
            f"commodity futures, T-bills) — about {pct:.1f}% of this "
            f"basis, the biggest such slice of the three bases, because "
            f"'minus-cash' is the only one that still keeps T-bills. The "
            f"index's percentage is of an all-equity fund, so a "
            f"difference here can still read low for a reason that is "
            f"arithmetic rather than construction. 'pure-equity' is the "
            f"closest comparison available, though it is not a fully "
            f"equity-to-equity one either — see its own note."
        )
    return (  # "everything"
        "This uses the 'everything' basis — his percentage is a share of "
        "everything he holds, cash included. The index's percentage is "
        "always a share of an all-equity fund, so his cash sits in his "
        "denominator and not in the index's: every difference reads low "
        "for a reason that is arithmetic, not construction. 'pure-equity' "
        "is the closest comparison available, though it is not a fully "
        "equity-to-equity one either — see its own note."
    )


def benchmark_companies(basis: str = "everything",
                        ticker: str = BENCHMARK_TICKER,
                        limit: int | None = None,
                        mine: pd.DataFrame | None = None, **paths) -> pd.DataFrame:
    """His true exposure to each company, beside the index's, and the gap.

    `mine`, when given, is an already-computed `by_company(basis, **paths)`
    frame — the same pass-down `_compare_note()`/`non_equity_in_basis()`
    already accept, extended here (fix round 1, Task 10, 2026-10-07) because
    a page assembling all three bases otherwise recomputes this exact walk
    once per caller that needs it (this function, `effective_bets()`,
    and the caller's own direct use) — four-plus times per basis for no
    reason, since none of them sees different data. Computed fresh
    (`by_company(basis, **paths)`) only when nobody already holds one.

    **His addition, 2026-10-06**, not in the original spec: asked if the plan
    already covered it, he wanted *"a comparison to the S&P holding"*; told the
    company view was not planned, he chose to add it — *"for me it's really
    important to be part of the main page."* The sector comparison answers how
    his shape differs from the index's; this answers it name by name, which is
    where a single position being three times the index's weight actually
    shows up.

    Both sides join through `company_key()` — the same function `by_company()`
    uses — so `BRK.B` in the index file and `BRKB` in his book are one company
    and the two panels cannot disagree about what a company is. Two index
    rows that resolve to the same key (two share classes of one company, or
    two spellings of one security) ADD rather than overwrite — each is real
    weight.

    🚨 **Fix round 1 (2026-10-07) — the bug this function existed to avoid,
    and shipped anyway.** The first version read absence from SPY's STORED
    rows as absence from the index itself, gated only on whether the index's
    SOURCE claimed the whole fund. That gate is still right — `coverage`
    genuinely is the raw source sum taken before the small-weight floor
    (`ops/refresh_etf_holdings.py`'s `assemble()`) — but it was used to answer
    a wider question than it actually settles: it says the SOURCE saw the
    whole fund, not that every one of its STORED rows survived into the
    file. SPY's stored rows stop at a 0.05% floor, and measured against the
    union of his seven held Select Sector SPDRs (which hold only S&P 500
    members by construction) 152 real constituents sit below it — Akamai,
    American Water Works, Trimble, Xylem, Pentair, Bunge among them. All 152
    read `only-mine` with `benchmark_pct=0.00` in the first version.

    `state`, and why the last two are not the same answer:

      both         he holds it and the index's stored rows show it too —
                   `benchmark_pct` is exact
      only-index   the index's stored rows show it; no STORED row of his
                   names it either. `mine_pct` reads 0.0, but — fix,
                   2026-10-07 final review (I2) — that is only a confirmed
                   zero when NONE of his held funds is itself a floor.
                   `by_company()` already documents a held fund's own
                   company list as a floor (its stored rows can stop short
                   of the whole fund); a company he reaches only through
                   such a fund, below ITS floor, reads identically to one
                   he truly holds none of. `is_floor` carries which case
                   this is (`any_mine_floor`, computed once per call); when
                   it is True, `difference` is withheld and only
                   `difference_floor` (still `mine_pct - benchmark_pct`,
                   still a safe LOWER bound — a hidden sliver can only add
                   to `mine_pct`, never subtract) is kept, the same bound/
                   exact split `below-floor` already makes for the index's
                   own side
      below-floor  he holds it; the index's stored rows do not show it; the
                   index's own SOURCE claimed the whole fund anyway
                   (`coverage`, never the stored sum — `ACCOUNTED_COMPLETE_AT`
                   says why). The true answer is NOT "the index holds none
                   of this": it is "the index holds AT MOST the stored
                   floor of this, or none". `benchmark_pct` stays genuinely
                   blank (`NaN`); `benchmark_pct_at_most` carries the bound
                   (`floor_pct`, the smallest weight among the index's
                   stored rows); `difference_floor` (`mine_pct` minus the
                   bound) is a LOWER bound on the real difference, in the
                   direction of *at least this much* — **but only when
                   `mine_pct >= floor_pct`.** When his own share is itself
                   smaller than the floor, the interval `[0, floor_pct)` the
                   true `benchmark_pct` sits in straddles `mine_pct`, so the
                   sign of the real difference is not determined — a
                   `below-floor` row's `difference_floor` is genuinely
                   blank (`NaN`) in that case, not merely rounded to zero,
                   and the overwhelming majority of real `below-floor` rows
                   (a tiny stake reached through a broad fund) are exactly
                   this case. A company confirmed
                   outside the S&P 500 (ASML, TSM, Samsung) and one
                   confirmed inside it below the floor (Akamai) read
                   IDENTICALLY here — `BELOW_FLOOR_WORDING` is true of both,
                   which is the honest answer this function can give;
                   telling the two apart needs a source this function does
                   not have
      unknown      he holds it; the index's rows do not show it; the index's
                   own SOURCE never claimed the whole fund either, so there
                   is not even a reliable bound (`UNKNOWN_WORDING`).
                   **The one way this panel could lie outright:** from a
                   ten-name floor, every company outside the top ten would
                   otherwise read as confirmed absent. Not reachable from
                   SPY's real file today (its source claims the whole fund);
                   kept for a future refetch that falls back to a source
                   this thin

    `attrs["state_wording"]` carries `STATE_WORDING` — every state's caveat,
    blank for the two certain ones — so a reader of the `state` column can
    always find its caveat without a sixth copy of this prose (fix round 1,
    C-D: a hedge that reaches no consumer is not a deliverable).

    ⚠️ **The two denominators are not the same kind of thing, and no basis is
    "honest".** `mine_pct` is of the selected basis; `benchmark_pct` is of an
    all-equity fund. Under `everything` his cash is in his denominator and not
    in the index's; even under `pure-equity`, his side still contains funds
    that own no equities at all (`non_equity_in_basis()`) — both dilute every
    one of his percentages for a reason that is arithmetic, not construction.
    `attrs["basis"]` carries which setting was used and `attrs["compare_note"]`
    is the sentence naming what dilutes it, never claiming any basis is clean.
    Not refused under `everything` — he chose the setting and hiding a view is
    worse than annotating it.

    ⚠️ **A floor on his side stays a floor in the difference.** `is_floor`
    travels from `by_company()`: a difference between a floor and an exact
    figure is itself a floor, in the direction of *at least this much* — a
    different floor from `benchmark_pct_at_most`'s, which is the INDEX's own,
    and the two can both apply to the same row. **Fix, pre-push review
    2026-10-07 (finding 2): this was true of the docstring and of the
    `only-index` branch below, and false of `both` — the state 272 of 279
    live rows are actually in.** `difference` there was published exact
    regardless of `is_floor`, which both understates an overweight (a
    floor's hidden sliver can only add to `mine_pct`) and left `both`'s own
    entry in `STATE_WORDING` blank even on a floor row, the one state this
    whole panel exists to get right. `BOTH_FLOOR_WORDING` is the caveat;
    unlike `only-index`'s `any_mine_floor` (one fact for the whole call),
    `is_floor` on a `both` row is per-company, so `page.
    benchmark_company_rows()` reads it off each row directly rather than a
    single call-wide swap in `attrs["state_wording"]`.

    **`attrs["index_accounted_pct"]` and `attrs["index_source_coverage_pct"]`
    are never collapsed into one number** — the same rule `company_coverage()`
    already follows for a held fund, applied here to the benchmark. The first
    is what the index's STORED rows actually sum to (≈93.7% for SPY today —
    the number `floor_pct`'s hazard comes from); the second is what the
    index's SOURCE claimed before the floor trimmed it (1.0 for SPY's own
    SPDR file). `floor_pct` is the smallest weight among the index's stored
    rows — the bound every `below-floor` row's `benchmark_pct_at_most`
    carries.

    **`attrs["as_of"]`** carries this file's own date (`etf_holdings.csv`,
    blank when nothing is fetched) — added, residual review fix (N1), because
    `honesty()` used to state `benchmark_weights()`'s date (`etf_compositions.
    csv`, a different file, refreshed by a different path) as if it covered
    this comparison too. The two dates already differ on real data; each
    frame now carries its own so a reader of either is never told the
    other's age.

    🚨 **Restricted to `KIND_COMPANY` rows on his side.** `by_company()`'s
    `KIND_CASH`, `KIND_UNACCOUNTED` and `KIND_NO_COMPANIES` rows have no
    index counterpart — a cash balance, a fund's unexplained remainder, and a
    fund that owns no companies at all are not companies, and comparing them
    would be a false statement about his construction. They do not vanish
    from the page: `attrs["excluded_by_kind"]` carries each kind's dollar
    total SEPARATELY (fix round 1, I-A — a single lumped number described
    three different things under one caption), so a later renderer can
    caption each correctly rather than let the comparison read as if it
    covered his whole book.

    `issuer_group` (fix round 1, the Alphabet problem) groups share classes
    of one issuer using the INDEX's own `holding_name`, which is the one
    place a class marker (`CL A`, `CLASS A`, …) is written down — never
    guessed from his own label. GOOG and GOOGL stay two rows (different
    securities, different weights, correctly) but share one `issuer_group`,
    so a renderer can offer the combined view without this function
    inventing one unasked.

    Ordered by the size of the difference — `difference` where it is exact,
    `difference_floor` where only a bound is known, `unknown` rows last —
    because where he is most unlike the index is the thing nothing else in
    the record shows. The sort is stable with `key` as an explicit tiebreak
    (fix round 1, M-A: well over a thousand rows tie at a zero or blank
    difference, and sorting a frame built by walking a `set`'s iteration
    order reordered the page between runs with no data change — Python
    randomises string hashes per process, so even a "stable" sort was only
    ever stable around whatever order that run's set happened to produce).
    `limit` applies after that sort; `None` is every row.

    Empty, with `attrs["why"]` naming `/market`, when the index has no
    holdings on file — the same shape `benchmark_weights()` uses, never a
    zero that would claim the index holds nothing.
    """
    from src import book
    from src import instruments as instruments_mod
    from src import market

    book.check_basis(basis)
    index = market.load_holdings(paths.get("holdings_path")
                                 or market.HOLDINGS_PATH)
    index = index[index["ticker"] == ticker] if not index.empty else index
    if index.empty:
        out = pd.DataFrame(columns=BENCHMARK_COMPANY_COLUMNS)
        out.attrs["why"] = (
            f"{ticker}'s own company holdings have not been fetched yet, so "
            f"there is nothing to compare against. Run /market — it asks "
            f"before it checks and again before it saves."
        )
        out.attrs["index_accounted_pct"] = 0.0
        out.attrs["index_source_coverage_pct"] = 0.0
        out.attrs["floor_pct"] = 0.0
        out.attrs["basis"] = basis
        out.attrs["compare_note"] = _compare_note(basis, **paths)
        out.attrs["excluded_by_kind"] = {KIND_CASH: 0.0, KIND_UNACCOUNTED: 0.0,
                                         KIND_NO_COMPANIES: 0.0}
        out.attrs["state_wording"] = dict(STATE_WORDING)
        out.attrs["as_of"] = ""
        return out

    inst = instruments_mod.load(
        paths.get("instruments_path") or instruments_mod.INSTRUMENTS_PATH
    ).fillna("")

    # Two different numbers, never collapsed (C-B — the same rule
    # `company_coverage()` already follows for a held fund, applied here to
    # the benchmark). `index_accounted` is what the index's STORED rows
    # actually sum to; `index_source_coverage` is what the index's SOURCE
    # claimed before the small-weight floor trimmed them. `complete` — can
    # absence from the stored rows carry a bound at all — reads the
    # SOURCE's claim; `floor_pct` reads the STORED rows, because the bound
    # on a missing company's weight can only ever be "smaller than the
    # smallest one actually kept".
    index_accounted = float(index["weight"].sum())
    index_source_coverage = float(index["coverage"].max())
    complete = index_source_coverage >= ACCOUNTED_COMPLETE_AT
    floor_pct = round(float(index["weight"].min()) * 100, 4)
    # This file's own date (fix, residual review N1) — never
    # `benchmark_weights()`'s, which dates a different file
    # (`etf_compositions.csv`) refreshed by a different path. `holdings_
    # schema()` requires `as_of` on every row, so `.iloc[0]` is the same
    # read `benchmark_weights()` already does for its own file.
    holdings_as_of = str(index["as_of"].iloc[0]).strip()

    idx_agg: dict[str, dict] = {}
    for _, r in index.iterrows():
        key = company_key(r["holding"], inst)
        d = idx_agg.setdefault(key, {"label": "", "benchmark_pct": 0.0,
                                     "issuer_group": ""})
        # Two share classes of one company (or two spellings that resolve
        # to the same key) must ADD, never overwrite — each is real weight.
        d["benchmark_pct"] += float(r["weight"]) * 100
        if not d["label"]:
            d["label"] = str(r["holding_name"]).strip() or key
        if not d["issuer_group"]:
            d["issuer_group"] = _issuer_group(r["holding_name"], key)

    mine_all = mine if mine is not None else by_company(basis, **paths)
    if not mine_all.empty:
        excluded_by_kind = {
            kind: round(float(
                mine_all.loc[mine_all["kind"] == kind, "market_value"].sum()
            ), 2)
            for kind in (KIND_CASH, KIND_UNACCOUNTED, KIND_NO_COMPANIES)
        }
        mine = mine_all[mine_all["kind"] == KIND_COMPANY]
    else:
        excluded_by_kind = {KIND_CASH: 0.0, KIND_UNACCOUNTED: 0.0,
                            KIND_NO_COMPANIES: 0.0}
        mine = mine_all
    mine_by_key = ({r["key"]: r for _, r in mine.iterrows()}
                  if not mine.empty else {})
    # Fix, 2026-10-07 final review (I2). `True` when ANY of his held funds
    # is itself a floor (`by_company()`'s own `is_floor`, which `mine_all`
    # already carries per row) — a real hole in a fund's own stored company
    # list, never the correct `no-companies` kind. A company with NO row
    # anywhere in `mine_by_key` is then not a confirmed zero: it could be
    # sitting below one of those funds' own small-weight floors, unseen.
    # Global rather than per-company on purpose — a company absent from
    # `mine` has no stored row to check a floor against, so the only honest
    # question this function can answer is "could ANY held fund be hiding
    # it", not "which one".
    any_mine_floor = bool(mine_all["is_floor"].any()) if not mine_all.empty else False

    rows = []
    # `sorted()`, not a bare `set` union (fix round 1, M-A): a set's own
    # iteration order is not deterministic across process runs (Python
    # randomises string hashing by default), so walking it straight into
    # `rows` made even a "stable" sort unstable from one run to the next.
    for key in sorted(set(mine_by_key) | set(idx_agg)):
        m, i = mine_by_key.get(key), idx_agg.get(key)
        mine_pct = round(float(m["pct_of_basis"]), 2) if m is not None else 0.0
        is_floor = bool(m["is_floor"]) if m is not None else False
        label = (str(m["label"]).strip() if m is not None else "") or (
            i["label"] if i is not None else key)
        # `key`, never `label` (fix round 2, NEW-1): `label` is free text —
        # "First Bancorp" (North Carolina) and "First BanCorp" (Puerto
        # Rico) would share a group under it, two different companies
        # merged by a name collision. `key` is unique by construction
        # (`company_key()`'s whole job), so an unmatched row can only ever
        # fall back to something that is already its own, unshared group.
        issuer_group = i["issuer_group"] if i is not None else key

        if m is not None and i is not None:
            state = "both"
            benchmark_pct = round(i["benchmark_pct"], 2)
            benchmark_pct_at_most = benchmark_pct
            # Fix, pre-push review 2026-10-07 (finding 2). `is_floor` was
            # computed two lines up and then ignored here — every `both`
            # row published an exact `difference` regardless, the docstring's
            # own rule above ("a floor on his side stays a floor in the
            # difference") implemented for `only-index` below but not here,
            # where 272 of 279 live rows actually land. `difference_floor`
            # stays the safe bound (`mine_pct - benchmark_pct`, true only in
            # the "at least this much" direction — for an overweight this
            # understates his concentration, since a floor's hidden sliver
            # can only ADD to `mine_pct`); `difference` itself, the claim
            # that this bound IS the exact gap, is withheld exactly when his
            # side is a floor — same shape as `only-index` just below.
            difference_floor = round(mine_pct - benchmark_pct, 2)
            difference = float("nan") if is_floor else difference_floor
        elif i is not None:
            state = "only-index"
            benchmark_pct = round(i["benchmark_pct"], 2)
            benchmark_pct_at_most = benchmark_pct
            # Fix, 2026-10-07 final review (I2) — the mirror of C-A, on his
            # own side. `mine_pct` is 0.0 here because no fund's STORED rows
            # name this company, never because his construction is known to
            # hold none of it: `by_company()` already documents a held
            # fund's own company list as a floor, and two live S&P members
            # (DHI, IRM) sit below VTI's. `any_mine_floor` is True whenever
            # ANY held fund is such a floor — the observed 0.0 is then only
            # a LOWER bound on his true share (a hidden sliver can only add
            # to it, never subtract), so `difference_floor` stays the safe
            # bound `mine_pct - benchmark_pct` while `difference` itself —
            # the claim that this IS the exact gap — is withheld, same
            # shape as `below-floor`'s own bound/exact split above.
            is_floor = any_mine_floor
            difference_floor = round(mine_pct - benchmark_pct, 2)
            difference = float("nan") if is_floor else difference_floor
        elif complete:
            # He holds it; the index's stored rows do not; its SOURCE still
            # claimed the whole fund — so absence from the rows is a BOUND,
            # never a confirmed absence (C-A). The real benchmark_pct sits
            # somewhere in [0, floor_pct) — unknown, but bounded.
            state = "below-floor"
            benchmark_pct = float("nan")
            benchmark_pct_at_most = floor_pct
            difference = float("nan")
            # Fix round 2, NEW-2: the sign of mine_pct - benchmark_pct is
            # only determined when mine_pct >= floor_pct — then EVERY value
            # in [0, floor_pct) makes the true difference strictly positive,
            # so "at least mine_pct - floor_pct" is a safe claim. When
            # mine_pct < floor_pct, the true benchmark_pct could be either
            # side of mine_pct (the interval straddles zero) — the
            # OVERWHELMING majority of below-floor rows on the real book
            # are this case (a tiny stake reached through a broad fund),
            # and stating `mine_pct - floor_pct` there asserted "at least
            # this underweight" when the truth could just as well be
            # overweight. Left genuinely absent instead — `na_position`
            # in the sort below already keeps an absent bound from
            # outranking a row whose difference is actually measured.
            if mine_pct >= floor_pct:
                difference_floor = round(mine_pct - floor_pct, 2)
            else:
                difference_floor = float("nan")
        else:
            # The index's own SOURCE never claimed the whole fund either —
            # not even "at most the floor" is a safe thing to say.
            state = "unknown"
            benchmark_pct = float("nan")
            benchmark_pct_at_most = float("nan")
            difference = float("nan")
            difference_floor = float("nan")

        rows.append({
            "key": key, "label": label, "issuer_group": issuer_group,
            "mine_pct": mine_pct, "benchmark_pct": benchmark_pct,
            "benchmark_pct_at_most": benchmark_pct_at_most,
            "difference": difference, "difference_floor": difference_floor,
            "state": state, "is_floor": is_floor,
        })

    out = pd.DataFrame(rows, columns=BENCHMARK_COMPANY_COLUMNS)
    if not out.empty:
        out = out.assign(_rank=out["difference_floor"].abs())
        out = out.sort_values(
            ["_rank", "key"], ascending=[False, True],
            kind="stable", na_position="last",
        ).drop(columns="_rank").reset_index(drop=True)
        if limit is not None:
            out = out.head(limit).reset_index(drop=True)
    out.attrs["why"] = ""
    out.attrs["index_accounted_pct"] = round(index_accounted * 100, 2)
    out.attrs["index_source_coverage_pct"] = round(index_source_coverage * 100, 2)
    out.attrs["floor_pct"] = floor_pct
    out.attrs["as_of"] = holdings_as_of
    out.attrs["basis"] = basis
    # `mine=mine_all` (fix round 2, MIN-A): this frame is already in hand —
    # recomputing it inside `_compare_note()` roughly doubled the cost of
    # every call to this function under `pure-equity`/`minus-cash`.
    out.attrs["compare_note"] = _compare_note(basis, mine=mine_all, **paths)
    out.attrs["excluded_by_kind"] = excluded_by_kind
    state_wording = dict(STATE_WORDING)
    if any_mine_floor:
        # Fix, 2026-10-07 final review (I2): every `only-index` row in THIS
        # frame shares the same `any_mine_floor` (it is a fact about his
        # held funds as a whole, not about any one company), so one swap
        # here covers every such row rather than a per-row caveat.
        state_wording["only-index"] = ONLY_INDEX_FLOOR_WORDING
    out.attrs["state_wording"] = state_wording
    return out


BENCHMARK_SECTOR_COLUMNS = ["sector", "mine_pct", "benchmark_pct",
                           "difference", "state"]


def benchmark_sector_comparison(basis: str = "everything",
                                ticker: str = BENCHMARK_TICKER,
                                **paths) -> pd.DataFrame:
    """`benchmark_weights()` and his own `by_sector()`, joined safely.

    Added fix round 1 (2026-10-07), I-B. Before this existed, the only way to
    compare the two was an outer join written out at the call site — this
    module's own Step 6 verification script did exactly that —
    `b.set_index('sector')[['weight_pct']].join(mine, how='outer').fillna(0.0)`.
    `fillna(0.0)` is the move that turns "nobody built this row" into "the
    index holds none of this sector" — indistinguishable, to anyone reading
    the result, from a company reading `only-mine` with `benchmark_pct=0.00`
    before C-A's fix, just for sectors instead of companies.

    Unlike a company, a sector really is a near-certain zero when one side's
    figure is missing and the other is not — `etf_compositions.csv` is
    schema-checked to sum to ~1.0 per ticker, so there is no small-weight
    floor hiding a sector the way there is hiding a company. But "confident
    zero" and "never fetched at all" still look identical after a bare
    `fillna(0.0)`, which is the risk this function removes: it does the join
    itself and names a `state`, never writing a blank as a number.

    `state`:

      both          both sides report this sector
      only-mine     he has exposure to a sector the index cannot hold any of
                    by construction (Crypto / Digital Assets, Fixed Income /
                    Cash — SPY is an all-equity fund). Unlike a company, this
                    really is as certain as absence gets — there is no
                    small-weight floor hiding a sector the way there is hiding
                    a company — but `benchmark_pct` still comes back blank
                    (`NaN`), never `0.0`, for the reason below: the NUMBER
                    does not get to carry that certainty, only `state` does
      unclassified  fix, pre-push review 2026-10-07 (finding 1). `sector ==
                    UNCLASSIFIED_SECTOR` ("Other") — money `by_sector()`
                    could not place at all (an ETF with no composition row,
                    or a stock whose vendor sector carries no date), never a
                    sector the index structurally cannot hold. Before this
                    state existed, "Other" fell into `only-mine` identically
                    to Crypto/Cash, and `page.sector_rows()`'s fixed caption
                    for that state ("SPY is an all-equity fund, so zero here
                    is certain") was printed on money that is simply
                    unclassified — the opposite of certain. Reproduced live:
                    a newly bought real-estate ETF with no breakdown fetched
                    yet read this way, while the real `Real Estate` row
                    (below) read his side as a confirmed `$0.00` beside it
      only-index    the index reports a sector he has no exposure to at all —
                    rare, since the 11 GICS sectors plus his own two
                    non-GICS ones cover both sides almost everywhere. 🚨
                    **Not a confirmed zero the moment money sits in
                    `UNCLASSIFIED_SECTOR`** (fix, pre-push review 2026-10-07,
                    finding 1): unclassified money could belong to ANY
                    sector he is not otherwise showing exposure to, this one
                    included, so "by_sector() has no row for it" stops being
                    the same fact as "he holds none of it" the instant a
                    sibling row in THIS SAME frame reads `state=="
                    unclassified"` with real money in it. This function does
                    not change its own output for that case — `state` stays
                    `only-index`, still literally true that his side has no
                    row — the caveat belongs to the page that reads both
                    rows together; see `page.sector_rows()`

    🚨 **Fix, 2026-10-07 final review (I8).** This docstring used to call
    `only-mine`'s missing `benchmark_pct` "a genuine, confident zero" — a
    claim about the NUMBER that the code does not make: the code emits
    `NaN`, drawn as a dash, same as every other absent figure. The
    certainty is real (see above) but it lives in `state`, never in a bare
    `0.0` — so `mine_pct`/`benchmark_pct` are blank (`NaN`) on the side with
    no row for a sector, for both `only-mine` AND `only-index` alike, even
    though the missing side is usually knowable here. Writing it as a bare
    number would be indistinguishable from the one case where it is NOT
    knowable: `benchmark_weights()` itself coming back empty, which this
    function propagates through `attrs["why"]` rather than silently zeroing
    every sector. A reader (or a page) that wants to say "this is a
    structural zero, not a gap" has to read `state`, not the number —
    `page.sector_rows()`'s own `note` column is where that distinction
    reaches the screen.

    🚨 **`attrs["compare_note"]` (fix round 2, NEW-3)** — this frame's own
    `**paths` carries `path`/`classification`/`positions_path` for
    `by_sector()`'s sake, which is NOT the kwarg set `_compare_note()` (via
    `non_equity_in_basis()`, via `by_company()`) accepts — forwarding it
    blindly would raise on `path` and `classification` alone, the
    implementer's own observation when this function was first built. Only
    the keys `by_company()` actually takes are passed through, computed
    fresh (`mine=None`) since this function never holds a `by_company()`
    frame of its own to pass. Without this, a reader of sector differences
    under a basis that still contains non-equity funds would have no way to
    know — exactly C-C's failure, one panel over.
    """
    from src import book

    book.check_basis(basis)
    comp_path = paths.get("path", COMPOSITIONS_PATH)
    # `by_company()`'s own kwarg set — never `path`/`classification`, which
    # are this function's, not its sibling's.
    company_paths = {k: v for k, v in paths.items()
                     if k in ("positions_path", "instruments_path",
                              "clusters_path", "holdings_path")}
    b = benchmark_weights(ticker=ticker, path=comp_path)
    if b.empty:
        out = pd.DataFrame(columns=BENCHMARK_SECTOR_COLUMNS)
        out.attrs["why"] = b.attrs.get("why", "")
        out.attrs["basis"] = basis
        out.attrs["compare_note"] = _compare_note(basis, **company_paths)
        return out

    mine = by_sector(comp_path, paths.get("positions_path"), basis=basis,
                     classification=paths.get("classification", "provider"),
                     instruments_path=paths.get("instruments_path"),
                     clusters_path=paths.get("clusters_path"))
    mine_by_sector = (dict(zip(mine["sector"], mine["pct_of_nav"]))
                      if not mine.empty else {})
    bench_by_sector = dict(zip(b["sector"], b["weight_pct"]))

    rows = []
    for sector in sorted(set(mine_by_sector) | set(bench_by_sector)):
        m_pct = mine_by_sector.get(sector)
        b_pct = bench_by_sector.get(sector)
        if m_pct is not None and b_pct is not None:
            state, difference = "both", round(m_pct - b_pct, 2)
        elif m_pct is not None:
            # Fix, pre-push review 2026-10-07 (finding 1). The benchmark
            # genuinely cannot hold `UNCLASSIFIED_SECTOR` ("Other") — but
            # not because it is a structural non-equity sector the way
            # Crypto/Cash are; it is because `by_sector()` could not
            # classify this money at all. The two used to share one state
            # (`only-mine`) and one caption downstream; derived apart here
            # so `page.sector_rows()` never has to match on the sector
            # NAME to tell them apart.
            state = ("unclassified" if sector == UNCLASSIFIED_SECTOR
                     else "only-mine")
            difference = float("nan")
        else:
            state, difference = "only-index", float("nan")
        rows.append({"sector": sector, "mine_pct": m_pct,
                     "benchmark_pct": b_pct, "difference": difference,
                     "state": state})

    out = pd.DataFrame(rows, columns=BENCHMARK_SECTOR_COLUMNS)
    out.attrs["why"] = ""
    out.attrs["basis"] = basis
    out.attrs["compare_note"] = _compare_note(basis, **company_paths)
    return out


# ─── Task 9: how many bets this really is ──────────────────────────────────
#
# Panel 7: "Each with a sentence, because none of them is glanceable alone."
# `book.by_currency()` already answers the currency half; this is the other
# half — how many distinct bets the book really holds, as opposed to how many
# rows a holdings list happens to print.

EFFECTIVE_BETS_KEYS = ("count", "largest_pct", "is_upper_bound",
                       "split_symbols", "why")


def effective_bets(basis: str = "everything",
                   mine: pd.DataFrame | None = None, **paths) -> dict:
    """How many distinct bets the book really holds — `1 / Σw²`.

    `mine`, when given, is an already-computed `by_company(basis, **paths)`
    frame — same pass-down as `benchmark_companies()` now accepts, and for
    the identical reason: a page holding all three bases should not pay for
    this walk twice over the same data just because two different panels
    both need it.

    Spec panel 7. The figure exists because a company held directly and the
    same company inside three funds is one bet and reads as four on a
    holdings list — `by_company()` is already the function that joins those
    four rows into one, so this is `1 / Σw²` over ITS rows, never a second
    walk over `book.holdings()` or `_company_rows()` that could draw the line
    differently.

    **The universe is every row `by_company()` returns**, each kind counted
    the way its own docstring says to: a `company` row is one bet; a cash or
    FX balance (`KIND_CASH`) is one block, because it is real money even
    though it is not a name; a fund's `unaccounted` remainder and a
    `no-companies` fund (bullion, bitcoin, T-bills, futures) are each ONE
    unknown block, not spread across however many companies might be in
    there and not dropped either — `by_company()` already groups by `key`,
    and a bucket's key is per-FUND (`f"{iid} {UNACCOUNTED}"`), so two funds'
    remainders can never merge into one block and one fund's remainder can
    never fragment into many. That is inherited for free from `by_company()`
    rather than re-decided here, which is the only way this function and
    that one can agree about what a "bet" is.

    ⚠️ **Magnitudes, never signed weights — the short test.** A short's
    `market_value` is negative. `by_company()`'s own `total` (what
    `pct_of_basis` divides by) is the SIGNED sum — `book.nav(basis)` in
    another name — and a short large enough relative to the book can shrink
    that sum toward zero or flip its sign, which would make every weight
    built on it enormous, negative, or undefined: the short would not just
    misstate its own weight, it would corrupt everyone else's. Dividing by
    `Σ|market_value|` instead — the sum of MAGNITUDES, never signed — means a
    short counts as a bet its own size and can never cancel another position
    out of the denominator. `largest_pct` is the same weight's own share,
    in percent, for the same reason: the question is how big the biggest bet
    is, not which way it points — `intent` already records direction, and
    `claims.position_pct_of_nav()`/`book.holdings()`'s own `size` column made
    the identical split for the identical reason.

    🚨 **An upper bound, and the one bias on this page that flatters him.** A
    company he does not hold directly has no `instrument_id` to join two
    funds' spellings of it to (`company_key()`'s own limit) — so two funds
    spelling it two ways read as two bets, `Σw²` falls, and `1/Σw²` RISES:
    the book looks more diversified than it is. `symbol_collisions()` is how
    many such apparent splits exist across his held funds today, reported as
    an upper bound itself (a name match, never a merge); its row count is
    `split_symbols`, and `is_upper_bound` is true exactly when that count is
    non-zero — the flag and the number travel together so the sentence
    beside the figure can say "at most this many," never present a ceiling
    as a measurement.

    Returns a dict, not a frame — there is one figure, not one row per
    anything:

      count           `round(1 / Σw², 1)`. 0.0 on an empty or all-zero basis.
      largest_pct     the single largest weight, in percent of the magnitude
                       total — the position (or bucket) this basis is most
                       exposed to, by size, regardless of direction.
      is_upper_bound  `split_symbols > 0` — whether a KNOWN symbol split is
                       inflating `count` today. The method can only ever bias
                       the figure upward, never down, so this is never a
                       claim that `count` is otherwise exact.
      split_symbols   `len(symbol_collisions(**paths))` — travels with the
                       flag so the sentence can name the size of the effect,
                       not just gesture at it.
      why             the plain-English sentence for the page.
    """
    from src import book

    def result(**kwargs) -> dict:
        """The one place the return dict is built — asserts it matches
        `EFFECTIVE_BETS_KEYS` exactly, so a key added or renamed at one of
        the three return points below cannot silently drift from the
        constant a caller would otherwise trust unchecked (fix round 1,
        2026-10-07, C: the constant existed but nothing ever read it)."""
        assert set(kwargs) == set(EFFECTIVE_BETS_KEYS), (
            f"effective_bets() built {sorted(kwargs)}, not "
            f"{sorted(EFFECTIVE_BETS_KEYS)} — EFFECTIVE_BETS_KEYS and the "
            f"dict this function returns have drifted apart."
        )
        return kwargs

    book.check_basis(basis)
    empty = {"count": 0.0, "largest_pct": 0.0, "is_upper_bound": False,
             "split_symbols": 0}
    rows = mine if mine is not None else by_company(basis, **paths)
    if rows.empty:
        return result(**empty,
                      why=f"Nothing is held in the {basis!r} basis, so "
                          f"there is nothing to count.")

    magnitude = rows["market_value"].abs()
    total = float(magnitude.sum())
    if total <= 0:
        return result(**empty,
                      why=f"Every holding in the {basis!r} basis sums to "
                          f"zero by magnitude, so there is nothing to "
                          f"divide by.")

    w = magnitude / total
    sum_sq = float((w ** 2).sum())
    count = round(1.0 / sum_sq, 1) if sum_sq else 0.0
    largest_pct = round(float(w.max()) * 100, 2)

    collisions = symbol_collisions(**paths)
    split_symbols = 0 if collisions.empty else int(len(collisions))
    is_upper_bound = split_symbols > 0

    method = (
        "This is 1 divided by the sum of every bet's squared share of the "
        "book — a company held directly and the same company reached "
        "through three funds counts once, not four, and a fund's own "
        "unaccounted remainder counts as one unknown block rather than "
        "nothing or a thousand tiny ones. Sized by magnitude, never by "
        "direction, so a short counts as a bet its own size instead of "
        "cancelling one out."
    )
    if is_upper_bound:
        plural = "y" if split_symbols == 1 else "ies"
        bias = (
            f"This number is an upper bound: at most {split_symbols} unheld "
            f"compan{plural} appear{'s' if split_symbols == 1 else ''} "
            f"spelled two different ways across his funds "
            f"(exposure.symbol_collisions()), each counted as two bets "
            f"instead of one. The real number of distinct bets is this "
            f"many or fewer, never more."
        )
    else:
        bias = (
            "exposure.symbol_collisions() finds no unheld company spelled "
            "two ways across his held funds today, so this count is not "
            "known to be inflated by that — though the method can only "
            "ever read as more spread out than the book really is, never "
            "less."
        )
    why = f"{method} {bias}"

    return result(count=count, largest_pct=largest_pct,
                  is_upper_bound=is_upper_bound, split_symbols=split_symbols,
                  why=why)
