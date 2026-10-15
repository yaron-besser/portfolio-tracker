"""Every hole in the market data, biggest money first.

Built to answer what Yaron actually asked for: *"I want to see if we got some
holes."* The market data layer (`src/market.py`, `src/exposure.py`,
`instruments.provider_sector`) gives each instrument a vendor sector and each
held ETF a company-level and a sector-level breakdown. This is the one command
that says which of those are still missing — with the biggest money named
first, because a missing breakdown on a fund holding a lot of money matters
and the same hole on a few hundred dollars does not, and an alphabetical list
makes the two look identical. Ranked on the SIZE of the money at stake, not
its sign — a short position the wrong way is still money at risk, so a
`-$9,000` line ranks above a `$300` one, printed with its sign intact.

Scoped to what `positions.csv` says is held **today**, never
`instruments.status` — the two can disagree, and the position file is the
truth (CLAUDE.md). One deliberate exception: the benchmark (`status=benchmark`
in `data/instruments.csv`) is never held and never will be, yet the page's
comparison columns need its two tables anyway — `benchmark_gaps()` is the one
part of this report that is not scoped to `positions.csv`, and it is kept as
its own function for exactly that reason.

The kinds of hole:

    no instrument row          positions.csv holds a lot instruments.csv has
                               never heard of — every other check below is
                               blind to it until this is fixed first
    no sector breakdown        etf_compositions.csv has no row for the ETF
    undated sector breakdown   it has a row, but no `as_of` — age unknown
                               (unless nothing inside the fund could ever
                               make it stale, see `_is_definitional()`)
    no company holdings        etf_holdings.csv has no row for the ETF
    holdings thinner than      etf_holdings.csv knows fewer companies for the
    the breakdown              fund than etf_compositions.top_holding already
                               names — two tables describing one fund and
                               disagreeing, and the thin one is what
                               look-through company exposure reads
    issuer spelling misses     instruments.issuer is free text AND the key
    its downloader             into ISSUER_DOWNLOADERS, so a typo demotes the
                               fund from an exact issuer file to a thin Yahoo
                               floor in total silence
    no dated vendor sector      a non-ETF, non-cash, non-option/future
                               instrument with no `provider_sector`, or one
                               with no `provider_sector_as_of`
    no sector of his own       the same kind of instrument, but it is his own
                               `sector` that has never been typed — a blank
                               label is a different problem from one
                               data/sectors.csv rejects, so it is not folded
                               into "unmappable sector" below
    vendor sector left behind  its `provider_sector_as_of` stopped advancing
                               while the rest of the table moved on — the
                               refresher leaves a row untouched on a vendor
                               disagreement or a failed fetch, and the frozen
                               date was the only trace
    unmappable sector          his `sector` or the vendor's `provider_sector`
                               is not in data/sectors.csv, so neither can be
                               rolled up to compare
    sector disagreement        his own `sector` and the vendor's
                               `provider_sector` roll up to different SPY
                               parents (a fact to surface — his label still
                               wins, nothing here corrects it) — unless he
                               has already settled this one
                               (`instruments.sector_settled`)
    unresolvable ticker        market.unresolvable() suspects the id is not
                               what a price provider expects
    benchmark sector           the benchmark (SPY) has no row in
    breakdown missing          etf_compositions.csv — the only one of these
                               kinds that is not about something he holds
    benchmark holdings         the benchmark has no row in etf_holdings.csv —
    missing                    see `benchmark_gaps()`

Two things that are true and worth saying but are NOT gaps, printed in their
own lines: a fund like SGOV holds no companies at all, so no row for it in
`etf_holdings.csv` is correct, not missing (`NO_EQUITY_INSIDE`,
`ops/refresh_etf_holdings.py`); and a fund he no longer holds can still carry
old holdings rows nobody can refresh, because the fetcher's own scope is what
he holds today, not what he once held.

Usage:
    venv/bin/python ops/data_gaps.py
"""

from __future__ import annotations

import difflib
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import exposure, instruments, market, sectors  # noqa: E402
from src.claims import _is_real_date  # noqa: E402

POSITIONS_PATH = PROJECT_ROOT / "data" / "positions.csv"

GAP_COLUMNS = ["kind", "subject", "market_value", "detail"]

# When a breakdown stops being worth trusting. Matches `ops/hooks/
# claude-stop.sh`, which nags at the same age — a second number here would
# let the hook and the skill disagree about whether the data is old.
STALE_AFTER_DAYS = 90

NEEDS_COLUMNS = ["ticker", "market_value", "why"]

# Instrument kinds no sector-data provider has ever assigned a sector to —
# nobody classifies a VIX call or a crude-oil future into a SPY sector. This
# is NOT the same predicate as `market.NON_MARKET_KINDS` (cash/fx), which
# answers "does a price provider quote this at all": `market.provider_ticker()`
# reads that set, and an option DOES have a price, so widening it to cover
# this would conflate the two questions. Narrow on purpose — without it, a
# held option or future's "no dated vendor sector" gap could never clear, the
# same unclearable-queue failure `instruments.gaps()` was fixed for on
# 2026-09-29.
#
# Read off `exposure.sector_disagreements()`, not retyped (Task 7): that
# function is the one place that decides which instrument kinds are even
# eligible for a vendor-sector comparison now, and a second literal copy here
# could drift from the one it actually uses.
NO_VENDOR_SECTOR_KINDS = exposure.NO_VENDOR_SECTOR_KINDS


def _load_sibling(name: str):
    """Import another `ops/` script by file path, not by package import.

    `ops/` is a directory of standalone scripts, not a package — adding
    `ops/__init__.py` would change how every script in it resolves its own
    imports (ruling, Task 6 brief). This is the same mechanism
    `tests/test_refresh_etf_compositions.py` already uses to load a script
    under test, used here so `NO_EQUITY_INSIDE` is written once
    (`ops/refresh_etf_holdings.py`) and never copied into a second list that
    could drift from the first.
    """
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Funds with nothing inside them to decompose into companies — SGOV (treasury
# bills), SLV (silver bullion), IBIT (bitcoin), DBA (agricultural futures).
# Reused, not retyped: `ops/refresh_etf_holdings.py` is where this list is a
# statement about what a fund holds (an `etf_holdings.csv` fact), and a
# second copy here could silently drift from it.
#
# `_is_definitional()` below needs this set as HALF of its answer, never the
# whole of it. On its own this set is not enough: a fund in it carrying a
# multi-sector equity breakdown has a breakdown that is WRONG — that is
# exactly what happened to DBA on 2026-10-01 — and suppressing its date
# would hide the one signal that something put it there, so shape still has
# to fail it. And the row shape alone is not enough either: an equity fund
# filed as a single 100% row (KIE, PBW, XLRE are real examples in
# etf_compositions.csv today, none currently held) still holds companies
# that move between sub-sectors even though the row itself never changes —
# only membership in THIS set says there is nothing inside the fund that
# could ever do that.
NO_EQUITY_INSIDE: dict[str, str] = _load_sibling("refresh_etf_holdings").NO_EQUITY_INSIDE

# Who the holdings fetcher can actually download from. `instruments.issuer`
# is free text and is used as the KEY into this dict, so a typo is not a
# typo — it is a silent demotion from an exact issuer file to a thin Yahoo
# floor, with nothing printed and nothing to notice afterwards. Reused from
# the fetcher for the same reason NO_EQUITY_INSIDE is: a second copy here
# would drift the moment a downloader is added.
ISSUER_DOWNLOADERS: dict = _load_sibling("refresh_etf_holdings").ISSUER_DOWNLOADERS


_ISSUER_NEAR = 0.7


def _issuer_near_miss(issuer: str) -> str | None:
    """A downloader key this issuer string was probably meant to be, or None.

    `issuer_of()` strips the cell and then looks it up in `ISSUER_DOWNLOADERS`
    as an exact, case-sensitive dict hit, so "Spdr" and "SDPR" miss and fall
    through to the Yahoo floor without a word printed. iShares and Roundhill
    miss too, but those are honest misses — no downloader is written for them
    — so only a string CLOSE to a real key is worth reporting.

    Compared case-folded, which is what separates the two kinds of miss
    cleanly: against "spdr", every typo shape scores 0.75 or better and every
    real other issuer on his book scores 0.43 or worse.
    """
    clean = str(issuer).strip()
    if not clean or clean in ISSUER_DOWNLOADERS:
        return None
    by_fold = {k.casefold(): k for k in ISSUER_DOWNLOADERS}
    close = difflib.get_close_matches(clean.casefold(), list(by_fold),
                                      n=1, cutoff=_ISSUER_NEAR)
    return by_fold[close[0]] if close else None


def _held_market_values(positions_path: Path) -> pd.Series:
    """instrument_id -> total market_value, summed across every broker lot.

    `positions.csv` is what decides "held" everywhere in this report, never
    `instruments.status`: a position he has exited can sit `active` for a
    while, and one he still holds can be marked wrong by hand. The lot is the
    fact (CLAUDE.md).
    """
    pos = pd.read_csv(positions_path, dtype=str, keep_default_na=False)
    pos["mv"] = pd.to_numeric(pos["market_value"], errors="coerce").fillna(0.0)
    return pos.groupby("instrument_id")["mv"].sum()


def _is_definitional(comp_rows: pd.DataFrame, ticker: str) -> bool:
    """True when a fund's undated sector breakdown needs no date at all —
    because there is nothing inside the fund that could ever make it stale.

    This is the CONJUNCTION of two facts, and neither one alone is enough
    (the last two rounds of review each tried one and found the counter-
    example the other provides):

        one issuer row at ~100% of the fund   AND   NO_EQUITY_INSIDE

    Shape alone is not enough: `KIE`, `PBW` and `XLRE` are real rows in
    `etf_compositions.csv` today (none currently held) that are each a
    single 100% issuer row and are ordinary EQUITY funds — the companies
    inside them drift between sub-sectors constantly even though the row
    itself never changes, so shape alone would make that drift invisible
    forever the day one of them is bought back.

    `NO_EQUITY_INSIDE` alone is not enough either, and DBA is the member
    that proves it: on 2026-10-01 a refresh gave it eleven Morningstar
    equity sectors led by Healthcare, for a fund that holds agricultural
    futures and T-bill collateral and no companies at all. A fund in this
    set wearing a multi-sector equity breakdown does not have a definitional
    row — it has a WRONG one, and suppressing its date would hide the only
    signal that something put it there.

    Together: SGOV, IBIT, SLV and DBA (one row + in the set) are suppressed;
    a set member that somehow has several rows, and KIE/PBW/XLRE (one row,
    not in the set), are both still reported, each for a different reason. A
    bullion or crypto fund bought later that is not yet in
    `NO_EQUITY_INSIDE` is correctly reported as undated — adding it to that
    set is a real, clearable action, never a silent suppression.
    """
    if ticker not in NO_EQUITY_INSIDE:
        return False
    if len(comp_rows) != 1:
        return False
    row = comp_rows.iloc[0]
    return float(row["weight"]) >= 0.999 and row["source_quality"] == "issuer"


def _has_named_holdings(comp_rows: pd.DataFrame) -> bool:
    """True when every sector row IS one company (`top_holding` filled).

    Only MAGS today (`src/exposure.py`): a fund small enough that each of its
    seven sector slices carries the single ticker behind it. Re-running the
    composition refresher on a fund like this would destroy that data, so a
    gap on one needs a different instruction than "re-run the refresher".
    """
    if comp_rows.empty:
        return False
    return comp_rows["top_holding"].astype(str).str.strip().ne("").all()


def _named_in_composition(comp_rows: pd.DataFrame | None) -> int:
    """How many distinct companies the sector breakdown already names.

    `top_holding` is filled only where a sector slice IS one company, so this
    is 0 for a broad fund and seven for MAGS. It is a floor under the floor:
    companies known to be in the fund, from the issuer, already on file.
    `_has_named_holdings()` asks whether EVERY row carries one; this asks how
    many there are, which is the question a thin holdings table fails.
    """
    if comp_rows is None or comp_rows.empty:
        return 0
    named = comp_rows["top_holding"].astype(str).str.strip()
    return int(named[named != ""].nunique())


def _hand_check_note(comp_rows: pd.DataFrame, issuer: str) -> str:
    """The right action for an undated breakdown the refresher would wreck.

    `apply()` (`ops/refresh_etf_compositions.py`) always writes `top_holding`
    blank, so running it on a fund like MAGS would replace the only
    `top_holding` data in the table with coarse parent sectors — a gap line
    that led there would lead nowhere good. The actual fix is to look the
    fund's current holdings up by hand and date the rows that are already
    there.

    The row stays issuer data, because that is what it is — the figures come
    off the issuer's own page. `source_quality=issuer-by-hand` is the value
    that says so AND records that nobody fetched them, which is the half the
    plain `issuer` value cannot carry. `estimate` would be the wrong answer
    twice over: these are not estimates, and `exposure.by_sector()` counts
    an `estimate` row's whole position as guessed, so one label would put
    the fund's entire market value into `pct_estimated` for good.
    """
    who = issuer or "the issuer"
    return (f" Running the composition refresher would NOT fix this — it "
            f"would replace these top_holding-bearing rows with coarse "
            f"parent sectors and erase the only top_holding data in the "
            f"table. Check {who}'s current {len(comp_rows)} holdings by "
            f"hand, date the rows with the issuer's own as-of date, and set "
            f"source_quality=issuer-by-hand so the record says they were "
            f"typed, not fetched.")


def benchmark_gaps(
    compositions_path: Path = exposure.COMPOSITIONS_PATH,
    holdings_path: Path = market.HOLDINGS_PATH,
    instruments_path: Path = instruments.INSTRUMENTS_PATH,
) -> pd.DataFrame:
    """Data the BENCHMARK is missing, which nothing else in this report sees.

    Every other check here scopes itself to what `positions.csv` says he holds,
    and `status=benchmark` means never held. That is right for the money
    questions and wrong for this one: the page's by-sector panel compares his
    look-through against the S&P's own sector weights, and — his instruction,
    2026-10-06 — against the S&P's holdings company by company. Neither
    comparison can exist without SPY's two tables, and until this function
    existed `/market` reported "nothing is missing" while the page waited on a
    fetch nothing would ever offer.

    `market_value` is 0.0 and not a fabricated figure: he does not own it. Every
    other row in this report is ordered by money, so zero sorts the benchmark
    last, which is where a thing he does not own belongs.

    ⚠️ **Not the mirror of `orphaned_holdings()`, which excludes the benchmark
    on purpose and must keep doing so.** That function asks "are there rows
    nobody can refresh?" and listing SPY there would put it permanently on the
    last line of every run, inviting the one action that destroys this data.
    This function asks "is data the page needs absent?". Opposite directions,
    both correct.
    """
    inst = instruments.load(instruments_path).fillna("")
    benchmarks = sorted(inst.loc[inst["status"] == "benchmark", "instrument_id"])
    comp = set(exposure.load(compositions_path)["ticker"])
    hold = set(market.load_holdings(holdings_path)["ticker"])
    rows = []
    for ticker in benchmarks:
        if ticker not in comp:
            rows.append({
                "kind": "benchmark sector breakdown missing",
                "subject": ticker, "market_value": 0.0,
                "detail": (f"{ticker} is the benchmark the sector panel is "
                           f"measured against and it has no sector breakdown, "
                           f"so the comparison column cannot be drawn. "
                           f"/market fetches it — nothing to set up, it "
                           f"already knows to ask for the benchmark."),
            })
        if ticker not in hold:
            rows.append({
                "kind": "benchmark holdings missing",
                "subject": ticker, "market_value": 0.0,
                "detail": (f"{ticker} has no company holdings on file, so the "
                           f"company-by-company comparison against the index "
                           f"cannot be drawn. /market fetches it; {ticker} is a "
                           f"SPDR fund and SPDR is one of the two issuers the "
                           f"fetcher can download by itself."),
            })
    return pd.DataFrame(rows, columns=GAP_COLUMNS)


def gaps(
    compositions_path: Path = exposure.COMPOSITIONS_PATH,
    holdings_path: Path = market.HOLDINGS_PATH,
    positions_path: Path = POSITIONS_PATH,
    instruments_path: Path | None = None,
) -> pd.DataFrame:
    """Every hole in the market data, for what `positions.csv` says is held
    today. Ordered by the SIZE of `market_value` descending — the ordering is
    the point, and it is computed on magnitude so a short position cannot
    rank below a tiny long one; the printed figure keeps its sign.

    Capable of being empty: a book where every lot has an instrument row,
    every ETF has a dated breakdown and its company holdings on file, and
    every other held instrument's vendor sector agrees with his own (or he
    has already settled that it does not need to), reports nothing. A report
    that always finds something is noise.
    """
    mv = _held_market_values(positions_path)
    # Every other input to this report is injectable; this one was not, so
    # the one scenario worth rehearsing — he buys a fund the repo has never
    # seen — could not be tested without editing his real book.
    instruments_path = instruments_path or instruments.INSTRUMENTS_PATH
    inst_raw = instruments.load(instruments_path).fillna("")
    inst = inst_raw.set_index("instrument_id")
    held_ids = [i for i in mv.index if i in inst.index]
    held_etf_ids = [i for i in held_ids if inst.at[i, "instrument_type"] == "etf"]

    rows: list[dict] = []

    # --- a lot with no instrument row at all. Every other check below needs
    # instruments.csv to even run, so a fully unknown id would otherwise
    # vanish from the report completely rather than show up as a gap — and
    # `src/ibkr_sync.py` writes exactly that row, with no instruments.csv
    # entry, for every newly bought ticker ("AUTO-ADDED by ibkr_sync — add
    # it to instruments.csv"). The moment right after a sync, when this
    # report is most likely to be run, is exactly when this fires.
    # The SAME instruments file the rest of this report reads. Loaded
    # separately, the report can hold two views of what exists and report a
    # fund as unknown in one breath and describe its holdings in the next.
    for _, r in exposure.unknown_holdings(positions_path,
                                          instruments_path).iterrows():
        rows.append({
            "kind": "no instrument row", "subject": r["instrument_id"],
            "market_value": float(r["mv"]),
            "detail": "not in data/instruments.csv — every other check in "
                      "this report is blind to it until it has a row. Add "
                      "one with instruments.add_instrument(...)",
        })

    # --- ETFs: the sector breakdown (etf_compositions.csv) and the
    # company-by-company holdings (etf_holdings.csv) are two different
    # tables answering two different questions. A hole in one says nothing
    # about the other, so they are checked separately.
    # `exposure.coverage()` returns an empty frame with the right columns for
    # a book holding zero ETFs, so no special case is needed here any more
    # (fixed at the source, Task 7 review).
    cov = exposure.coverage(compositions_path, positions_path).set_index("ticker")
    comp_by_ticker = {t: g for t, g in exposure.load(compositions_path).groupby("ticker")}
    hold_df = market.load_holdings(holdings_path)
    held_holdings_tickers = set(hold_df["ticker"])

    for iid in held_etf_ids:
        value = float(mv[iid])
        if iid not in comp_by_ticker:
            # A fund bought since the last refresh. Asked of the
            # compositions file DIRECTLY, never of `exposure.coverage()`:
            # coverage is derived, and a fund can be absent from it for
            # reasons that have nothing to do with having a breakdown — it
            # loads instruments.csv by its own default path, so an injected
            # book is invisible to it. Gating on coverage both missed the
            # real case (found 2026-10-06 by rehearsing a new purchase) and
            # then reported a complete fund as empty.
            rows.append({
                "kind": "no sector breakdown", "subject": iid,
                "market_value": value,
                "detail": "no row in etf_compositions.csv — none of its "
                          "sector exposure is counted in any total. A fund "
                          "bought since the last refresh looks exactly like "
                          "this; run /market",
            })
        elif iid in cov.index:
            quality = cov.at[iid, "source_quality"]
            if quality == "MISSING":
                rows.append({
                    "kind": "no sector breakdown", "subject": iid,
                    "market_value": value,
                    "detail": "no row in etf_compositions.csv — none of its "
                              "sector exposure is counted in any total",
                })
            elif not str(cov.at[iid, "as_of"]).strip():
                comp_rows = comp_by_ticker.get(iid)
                if comp_rows is None or not _is_definitional(comp_rows, iid):
                    detail = ("has a sector breakdown, but no as_of date — "
                              "age unknown")
                    if comp_rows is not None and _has_named_holdings(comp_rows):
                        detail += _hand_check_note(comp_rows, inst.at[iid, "issuer"])
                    rows.append({
                        "kind": "undated sector breakdown", "subject": iid,
                        "market_value": value, "detail": detail,
                    })
        if iid not in held_holdings_tickers and iid not in NO_EQUITY_INSIDE:
            rows.append({
                "kind": "no company holdings", "subject": iid,
                "market_value": value,
                "detail": "no row in etf_holdings.csv — none of what it "
                          "owns company-by-company is counted",
            })
        # Skipped for a fund with nothing inside it: the fetcher never calls
        # a downloader for one of those at all, so a misspelt issuer there
        # costs nothing and the sentence below would not be true of it.
        near = (None if iid in NO_EQUITY_INSIDE
                else _issuer_near_miss(inst.at[iid, "issuer"]))
        if near is not None:
            rows.append({
                "kind": "issuer spelling misses its downloader", "subject": iid,
                "market_value": value,
                "detail": f"instruments.issuer is "
                          f"{str(inst.at[iid, 'issuer']).strip()!r}, which is "
                          f"not a key in ISSUER_DOWNLOADERS but is one "
                          f"character or two from {near!r}. The lookup is an "
                          f"exact dict hit, so this fund silently stops being "
                          f"fetched from its issuer and falls to a thin Yahoo "
                          f"floor instead — no error, no warning, just a "
                          f"worse answer. Fix the spelling with "
                          f"instruments.set_issuer({iid!r}, {near!r})",
            })
        if iid in held_holdings_tickers:
            named = _named_in_composition(comp_by_ticker.get(iid))
            stored = len(set(hold_df.loc[hold_df["ticker"] == iid, "holding"]))
            if named and stored < named:
                rows.append({
                    "kind": "holdings thinner than the breakdown",
                    "subject": iid, "market_value": value,
                    "detail": f"etf_holdings.csv has {stored} compan"
                              f"{'y' if stored == 1 else 'ies'} for it, but "
                              f"etf_compositions.top_holding already names "
                              f"{named}. Two tables in data/market/ describe "
                              f"this fund and disagree about what is in it, "
                              f"and the thinner one is what look-through "
                              f"company exposure reads. A plain re-run may "
                              f"not fix it — a floor thinner than this is "
                              f"now refused rather than stored. Drop the "
                              f"issuer's own holdings file into "
                              f"data/market/holdings_inbox/, then run "
                              f"ops/refresh_etf_holdings.py {iid} --write",
                })

    # --- everything else held: does it have a dated vendor sector, does he
    # have one of his own at all, and — unless he has already looked and
    # kept his own label — does that vendor sector agree with his once both
    # roll up to a parent? The comparison itself moved to
    # `exposure.sector_disagreements()` on 2026-10-06 (Task 7, spec D13),
    # built for the portfolio page's eventual by-sector panel — the same
    # function this report calls, so the fact that panel will show and the
    # fact this report prints have to be the same one (P7). This block only
    # turns its five states into the gap kinds and plain-English detail this
    # report has always printed; `kept-mine` and an agreement (no row at
    # all) are facts to keep quiet about, not gaps to print.
    disagreements = exposure.sector_disagreements(
        positions_path=positions_path, instruments_path=instruments_path)
    for _, r in disagreements.iterrows():
        iid, value = r["instrument_id"], float(r["market_value"])
        if r["state"] == "no-vendor-sector":
            rows.append({
                "kind": "no dated vendor sector", "subject": iid,
                "market_value": value,
                "detail": "no dated provider_sector on file",
            })
        elif r["state"] == "no-own-sector":
            rows.append({
                "kind": "no sector of his own", "subject": iid,
                "market_value": value,
                "detail": f"instruments.sector has never been typed for "
                          f"this holding — the vendor dates "
                          f"{r['vendor']!r} as of {r['vendor_as_of']}, but "
                          f"there is nothing of his own to compare it "
                          f"against",
            })
        elif r["state"] == "unmappable":
            bad = []
            for label in (r["mine"], r["vendor"]):
                if not label:
                    continue
                try:
                    sectors.parent(label)
                except KeyError:
                    bad.append(label)
            rows.append({
                "kind": "unmappable sector", "subject": iid,
                "market_value": value,
                "detail": f"{', '.join(repr(b) for b in bad)} not in "
                          f"data/sectors.csv — his sector and the vendor's "
                          f"cannot be rolled up to compare",
            })
        elif r["state"] == "disagree":
            his_parent = sectors.parent(r["mine"])
            vendor_parent = sectors.parent(r["vendor"])
            rows.append({
                "kind": "sector disagreement", "subject": iid,
                "market_value": value,
                "detail": f"his sector {r['mine']!r} rolls up to "
                          f"{his_parent!r}; the vendor's {r['vendor']!r} "
                          f"rolls up to {vendor_parent!r} — his label "
                          f"still wins, this is not a correction. "
                          f"instruments.set_sector_settled({iid!r}, ...) "
                          f"silences this for good once he has looked.",
            })

    # --- a vendor sector whose date stopped advancing while the rest of the
    # table moved on. `ops/refresh_classification.py` leaves BOTH the sector
    # and its date untouched when the vendor disagrees with what is on file —
    # deliberately, because a vendor changing its mind is for him to see, not
    # a correction to apply unseen. But it says so only in that run's output,
    # and once that scrolls away the single remaining trace is a date that
    # stopped moving, which nothing reported. A failed fetch leaves the same
    # trace, so the line names both rather than guessing which.
    newest = inst_raw["provider_sector_as_of"].astype(str).str.strip()
    newest = newest[newest != ""].max() if (newest != "").any() else ""
    for iid in held_ids:
        itype = inst.at[iid, "instrument_type"]
        if itype == "etf" or itype in market.NON_MARKET_KINDS \
                or itype in NO_VENDOR_SECTOR_KINDS:
            continue
        on = str(inst.at[iid, "provider_sector_as_of"]).strip()
        if on and newest and on < newest:
            rows.append({
                "kind": "vendor sector left behind", "subject": iid,
                "market_value": float(mv[iid]),
                "detail": f"its provider_sector is dated {on} while the "
                          f"newest in the table is {newest} — the last "
                          f"refresh did not write this row. "
                          f"ops/refresh_classification.py leaves a row "
                          f"untouched when the vendor's answer disagrees "
                          f"with what is on file, and also when the fetch "
                          f"fails; either way the frozen date is the only "
                          f"trace. Re-run it to see which, and why",
            })

    for _, r in market.unresolvable(inst_raw).iterrows():
        iid = r["instrument_id"]
        if iid not in mv.index:
            continue
        rows.append({
            "kind": "unresolvable ticker", "subject": iid,
            "market_value": float(mv[iid]), "detail": r["why"],
        })

    # --- the benchmark's own data (Task 2). Every check above scopes itself
    # to what positions.csv says he holds, and the benchmark is never held —
    # so this is appended rather than folded into any loop above, on purpose,
    # as its own small block a later edit to this function cannot drop by
    # accident. See benchmark_gaps()'s own docstring for why this exists.
    rows.extend(benchmark_gaps(compositions_path, holdings_path,
                               instruments_path).to_dict("records"))

    out = pd.DataFrame(rows, columns=GAP_COLUMNS)
    # Sorted on the SIZE of the exposure, not its sign — a short position the
    # wrong way is still money at risk, and `-$9,000` must not rank below
    # `$300`. `kind="stable"` so two gaps of equal size keep the order they
    # were discovered in, run to run, rather than swapping at random.
    order = out["market_value"].abs().sort_values(ascending=False, kind="stable").index
    return out.loc[order].reset_index(drop=True)


def no_equity_etfs_held(positions_path: Path = POSITIONS_PATH) -> pd.DataFrame:
    """Held ETFs with nothing inside them to decompose into companies.

    Not a gap — `gaps()` already excludes these from "no company holdings" —
    but the money is real and worth naming on its own line rather than
    disappearing silently. Every held ETF in `NO_EQUITY_INSIDE` is reported
    regardless of what `etf_holdings.csv` contains for it, so there is no
    `holdings_path` to pass here.
    """
    mv = _held_market_values(positions_path)
    rows = [{"ticker": iid, "market_value": float(mv[iid]), "why": why}
            for iid, why in NO_EQUITY_INSIDE.items() if iid in mv.index]
    df = pd.DataFrame(rows, columns=["ticker", "market_value", "why"])
    return df.sort_values("market_value", ascending=False,
                          kind="stable").reset_index(drop=True)


def orphaned_holdings(
    holdings_path: Path = market.HOLDINGS_PATH,
    positions_path: Path = POSITIONS_PATH,
    instruments_path: Path = instruments.INSTRUMENTS_PATH,
) -> pd.DataFrame:
    """Tickers in etf_holdings.csv for a fund he no longer holds.

    They affect no number — `gaps()` only ever looks at what `positions.csv`
    says is held today — but the rows can never be refreshed, because the
    fetcher's own scope is what he holds today, not what he once held
    (ruling, Task 6 brief). Named here so clearing them stays his decision.

    A `status=benchmark` ticker (SPY) is excluded too, and for a sharper
    reason than "not held": it is NEVER going to be held, so unlike IMAE it
    would sit in this list forever, on the exact line `ops/refresh_market.py`
    prints last on every run — inviting the one action (clearing it) that
    destroys the benchmark data this task exists to build. The same
    permanently-unclearable-row failure already fixed once in
    `instruments.gaps()` (Task 7 review).

    ⚠️ **Not the mirror of `benchmark_gaps()`, which asks the opposite
    question.** This function asks "are there rows nobody can refresh?" and
    excludes SPY because it would sit here forever; `benchmark_gaps()` asks
    "is data the page needs absent?" and exists specifically to report SPY's
    two tables when they are missing. Opposite directions, both correct — a
    session reading only one of them could mistake the exclusion here for a
    bug and "fix" it into reporting the benchmark as an orphan.
    """
    held = set(_held_market_values(positions_path).index)
    inst = instruments.load(instruments_path).fillna("")
    benchmarks = set(inst.loc[inst["status"] == "benchmark", "instrument_id"])
    tickers = set(market.load_holdings(holdings_path)["ticker"])
    return pd.DataFrame({"ticker": sorted(tickers - held - benchmarks)})


def unnamed_money(positions_path: Path = POSITIONS_PATH,
                  holdings_path: Path | None = None) -> pd.DataFrame:
    """Per fund: money inside it whose COMPANY the table cannot name.

    There are two defensible answers to "how much is unknown", and on
    2026-10-06 a session quoted the flattering one at him four times:

    * `1 - coverage` — how much of the fund its SOURCE failed to account
      for. Across his book that is $30.
    * `1 - the stored weights` — how much is not written down company by
      company, because rows under `MIN_HOLDING_WEIGHT` are dropped AFTER
      coverage is computed. That is $1,553, almost all of it IWM's and VTI's
      long tails.

    The second is the one that answers "how much NVIDIA do I really own",
    so it is the one this returns. Both are kept in the frame: a figure with
    no sibling invites the next reader to recompute it their own way, which
    is how one question came to have two answers in the first place.
    """
    held = _held_market_values(positions_path)
    rows = []
    holdings = market.load_holdings(holdings_path or market.HOLDINGS_PATH)
    for ticker, g in holdings.groupby("ticker"):
        value = float(held.get(ticker, 0.0))
        if value <= 0:
            continue
        cov = float(g["coverage"].max())
        named = min(float(g["weight"].sum()), cov)
        rows.append({"ticker": ticker, "market_value": value,
                     "by_coverage": value * (1 - cov),
                     "unnamed": value * (1 - named)})
    frame = pd.DataFrame(rows, columns=["ticker", "market_value",
                                        "by_coverage", "unnamed"])
    return frame.sort_values("unnamed", ascending=False, kind="stable")


def needs_refresh(
    compositions_path: Path = exposure.COMPOSITIONS_PATH,
    holdings_path: Path = market.HOLDINGS_PATH,
    positions_path: Path = POSITIONS_PATH,
    instruments_path: Path | None = None,
    today: date | None = None,
) -> pd.DataFrame:
    """Funds worth refreshing today — missing or aged — biggest money first.

    His instruction, 2026-10-06: *"most of the time it's unnecessary"* to
    refresh everything, and a full refresh should be deliberately hard. This
    is what `/market` asks him to choose from, so that the default run
    touches only what is actually missing and a fund with good data on file
    is never re-fetched into something worse.

    Two reasons a fund appears: the report above already calls it a gap, or
    one of its two dates has passed STALE_AFTER_DAYS. Nothing else — a fund
    that is complete and recent is not an option, because offering it is how
    a full refresh happens by accident.
    """
    today = today or date.today()
    g = gaps(compositions_path, holdings_path, positions_path,
             instruments_path)
    why: dict[str, list[str]] = {}
    value: dict[str, float] = {}
    for _, r in g.iterrows():
        why.setdefault(r["subject"], []).append(r["kind"])
        value[r["subject"]] = float(r["market_value"])

    held = _held_market_values(positions_path)
    for label, frame, col in (
            ("sector breakdown", exposure.load(compositions_path), "ticker"),
            ("holdings", market.load_holdings(holdings_path), "ticker")):
        for ticker, rows in frame.groupby(col):
            if ticker not in held.index or float(held[ticker]) <= 0:
                continue
            dates = [d for d in rows["as_of"].astype(str) if _is_real_date(d)]
            if not dates:
                continue
            age = (today - date.fromisoformat(max(dates))).days
            if age > STALE_AFTER_DAYS:
                why.setdefault(ticker, []).append(f"{label} {age} days old")
                value[ticker] = float(held[ticker])

    rows = [{"ticker": t, "market_value": value[t], "why": ", ".join(w)}
            for t, w in why.items()]
    out = pd.DataFrame(rows, columns=NEEDS_COLUMNS)
    return out.sort_values("market_value", key=abs, ascending=False,
                           kind="stable").reset_index(drop=True)


def main() -> None:
    g = gaps()
    print("Market data gaps — biggest money first\n")
    if g.empty:
        print("None. Every lot has an instrument row, every held ETF has a "
              "dated sector breakdown and its company holdings on file, and "
              "every other held instrument's vendor sector agrees with his "
              "own once both roll up (or he has already settled that it "
              "does not need to).")
    else:
        # Kind headings ordered by each kind's own largest gap (by size, not
        # sign), so the kind carrying the most money leads — the same
        # principle as the rows.
        order = (g["market_value"].abs().groupby(g["kind"]).max()
                 .sort_values(ascending=False, kind="stable").index)
        for kind in order:
            sub = g[g["kind"] == kind]
            print(f"{kind} ({len(sub)})")
            for _, r in sub.iterrows():
                print(f"  {r['subject']:<12} ${r['market_value']:>10,.2f}  "
                      f"{r['detail']}")
            print()

    no_equity = no_equity_etfs_held()
    if not no_equity.empty:
        print("Not a gap — nothing inside them to decompose into companies:")
        for _, r in no_equity.iterrows():
            print(f"  {r['ticker']:<12} ${r['market_value']:>10,.2f}  {r['why']}")
        print()

    unnamed = unnamed_money()
    material = unnamed[unnamed["unnamed"] >= 1]
    if not material.empty:
        print("Not a gap — money inside a fund whose companies are not "
              "written down one by one:")
        for _, r in material.iterrows():
            print(f"  {r['ticker']:<12} ${r['unnamed']:>10,.2f}  of "
                  f"${r['market_value']:,.0f}, below the 0.05% floor he set")
        print(f"  {'TOTAL':<12} ${material['unnamed'].sum():>10,.2f}  "
              f"— the honest answer to \"how much is unknown\"; the "
              f"coverage-only figure is ${unnamed['by_coverage'].sum():,.2f} "
              f"and flatters it\n")

    orphaned = orphaned_holdings()
    if not orphaned.empty:
        print("Not held today, but etf_holdings.csv still carries their rows "
              "(affects no number — his call whether to clear them):")
        print("  " + ", ".join(orphaned["ticker"]))


if __name__ == "__main__":
    main()
