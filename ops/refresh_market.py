"""The one command that refreshes the whole market data layer.

Yaron's ask: *"I want to have all the ETF compositions built in advance, to
see if we got some holes, and an update mechanism."* Three refreshers already
exist — sector breakdowns, company-by-company holdings, a vendor sector per
stock — plus a report naming what is still missing. This is the mechanism:
one command, run in order, that drives all four rather than asking him to
remember three scripts and their arguments.

    venv/bin/python ops/refresh_market.py             # report only
    venv/bin/python ops/refresh_market.py --write      # persist what came back

**Report-only is the default on purpose.** A later skill (`/market`) wraps
this, and Yaron was explicit: *"I don't want you to automatically update it
without asking — it's something you do monthly or quarterly."* This script
never schedules itself and is never called from a hook; it only runs when he
types it. `--write` passes through to all three refreshers — report-only
still fetches from every provider, exactly as each refresher already does
standalone, it just does not touch a CSV.

**A refusal anywhere is reported and never stops the rest.** He should get
one run and one complete picture, not a half-finished one that died on fund
three because Alpha Vantage's daily quota ran out partway through. Each step
is driven through its own `main()` — this file does not reimplement any
fetch, scoring or write logic, only the order to run them in.

`ops/` is deliberately not a package (ruling, Task 6), so each sibling script
is loaded by file path, the same way `tests/test_refresh_etf_compositions.py`
and `ops/data_gaps.py` already load one.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# SPY is not held and never will be — it is the benchmark the sector view is
# measured against, so its breakdown has to be fetched like any other fund's.
# `data/instruments.csv` carries a row for it with `status=benchmark`
# (`ops/vocab/instrument_status`), never `active`: a test proves it can never
# reach an exposure or holdings figure (tests/test_refresh_market.py).
BENCHMARK_TICKERS = ["SPY"]


def _load(name: str):
    """Import one sibling `ops/` script by path, not by package import."""
    spec = importlib.util.spec_from_file_location(
        name, PROJECT_ROOT / "ops" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tickers_to_refresh(held_etfs: list[str]) -> list[str]:
    """Every held ETF plus the benchmark, each named once.

    SPY is never held in practice, so this just appends it today — but a
    provider must never be asked for the same breakdown twice in one run,
    which a held instrument that later becomes a benchmark (or vice versa)
    would otherwise cause.
    """
    return list(dict.fromkeys(list(held_etfs) + BENCHMARK_TICKERS))


def tickers_for_compositions(tickers: list[str], no_equity_inside,
                             compositions_path: Path | None = None) -> list[str]:
    """Held ETFs actually worth asking a sector-weighting provider about.

    A fund with nothing equity inside it (`refresh_etf_holdings`'s
    `NO_EQUITY_INSIDE` — reused rather than retyped, so the two lists cannot
    drift) can never get a usable answer from an equity-sector provider:
    confirmed live, 2026-10-04, when SGOV, IBIT and SLV each returned 0.0%
    coverage from both Alpha Vantage and yfinance. Asking anyway spends Alpha
    Vantage's daily quota — shared with every other held ETF — on a question
    that has no answer.

    🚨 **The skip is refused, not taken, when the fund is already wearing an
    equity breakdown it cannot have.** Skipping is right while the row on
    file says what the fund really is. On 2026-10-01 a refresh replaced DBA's
    single `Materials - Agriculture` row with eleven Morningstar equity
    sectors led by Healthcare — for a fund holding agricultural futures and
    T-bill collateral — and the skip then made that answer permanent: no
    later run could reach the fund to put it right, and it carried a fresh
    date while being wrong. A frozen wrong answer is worse than a stale one,
    because nothing about it looks stale. So this stops the run and names the
    fix instead.

    Otherwise a plain filter, nothing SPY-specific: the benchmark passes
    through because it is not a real member of `NO_EQUITY_INSIDE`
    (`test_the_benchmark_is_not_in_no_equity_inside` pins that fact), not
    because this function special-cases it.
    """
    from src import exposure

    comp = exposure.load(compositions_path or exposure.COMPOSITIONS_PATH)
    for ticker in tickers:
        if ticker not in no_equity_inside:
            continue
        rows = comp.loc[comp["ticker"] == ticker]
        if len(rows) > 1:
            raise SystemExit(
                f"{ticker} holds {no_equity_inside[ticker]}, so it cannot "
                f"have an equity sector breakdown — but "
                f"data/market/etf_compositions.csv gives it "
                f"{len(rows)}: {', '.join(sorted(rows['sector']))}.\n"
                f"  Refusing to skip past it: skipping is what made the same "
                f"wrong answer permanent for DBA on 2026-10-01, since no "
                f"refresh can reach a fund it never asks about.\n"
                f"  Fix the rows first — one row naming what the fund really "
                f"holds, source_quality=issuer — then re-run. If {ticker} "
                f"really does hold companies, take it out of "
                f"NO_EQUITY_INSIDE in ops/refresh_etf_holdings.py instead."
            )
    return [t for t in tickers if t not in no_equity_inside]


class _Tee(io.TextIOBase):
    """Write-only stream that copies everything to several real streams.

    Used to let a step's output reach the terminal exactly as it always has
    while a second copy is captured for `run_steps()` to scan for warnings —
    a warning printed mid-run must not become invisible just because it was
    also captured.
    """

    def __init__(self, *streams) -> None:
        self._streams = streams

    def write(self, s: str) -> int:
        for stream in self._streams:
            stream.write(s)
        return len(s)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def run_steps(
    steps: list[tuple[str, Callable[[], None]]],
) -> tuple[list[str], list[str]]:
    """Run each step in order; one failing never costs the other two.

    A refresher can refuse on purpose (`raise SystemExit`, e.g. an unmapped
    provider sector) or fail outright (a provider being down). Either way the
    run continues — Yaron should see everything that COULD be refreshed, not
    lose every fund after the one that broke.

    Returns `(failed, warnings)`. `failed` is the labels that did not
    complete cleanly. `warnings` is every line a step printed containing
    "⚠" — the convention all three refreshers already use for a real but
    non-fatal problem (an ETF losing a whole sector on refresh, say). A
    warning like that can land eighty lines up in a combined run and still
    be true; collecting it here is what lets `main()` repeat it in the
    closing summary instead of trusting he scrolled back up to find it.
    """
    failed: list[str] = []
    warnings: list[str] = []
    for label, fn in steps:
        print(f"\n=== {label} ===")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(_Tee(sys.stdout, buf)):
                fn()
        except SystemExit as e:
            print(f"  refused: {e}")
            failed.append(label)
        except Exception as e:  # noqa: BLE001 — one refresher's failure is not fatal to the rest
            print(f"  failed: {type(e).__name__}: {e}")
            failed.append(label)
        warnings.extend(
            line.strip() for line in buf.getvalue().splitlines() if "⚠" in line
        )
    return failed, warnings


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv

    # This command never takes tickers — it always refreshes every held ETF
    # plus the benchmark, and every held non-ETF. All three sibling scripts
    # DO take tickers, so `refresh_market.py XLF --write` is a plausible
    # mistake to type expecting a one-fund scope — and it would silently
    # refresh and write every fund, since only `"--write" in argv` was ever
    # checked (found by review). Refused rather than silently ignored.
    extra = [a for a in argv if not a.startswith("--")]
    if extra:
        raise SystemExit(
            f"refresh_market.py takes no tickers (got {extra!r}) — it always "
            f"refreshes every held ETF plus the benchmark, and every held "
            f"non-ETF. To refresh just one fund, call the underlying script "
            f"directly, e.g.:\n"
            f"  venv/bin/python ops/refresh_etf_compositions.py "
            f"{' '.join(extra)} --write"
        )

    write = "--write" in argv
    write_flag = ["--write"] if write else []

    comp_mod = _load("refresh_etf_compositions")
    hold_mod = _load("refresh_etf_holdings")
    classify_mod = _load("refresh_classification")
    gaps_mod = _load("data_gaps")

    tickers = tickers_to_refresh(hold_mod.held_etfs())
    composition_tickers = tickers_for_compositions(tickers, hold_mod.NO_EQUITY_INSIDE)

    def run_compositions() -> None:
        # refresh_etf_compositions.main() takes no arguments of its own and
        # reads sys.argv directly — driven rather than reimplemented, so its
        # argument-reading stays exactly as it is for a standalone run.
        old_argv = sys.argv
        sys.argv = ["refresh_etf_compositions.py", *composition_tickers, *write_flag]
        try:
            comp_mod.main()
        finally:
            sys.argv = old_argv

    def run_holdings() -> None:
        hold_mod.main(argv=[*tickers, *write_flag])

    def run_classification() -> None:
        # refresh_classification.main() takes no tickers at all — it scopes
        # itself to every held non-ETF instrument already, which is exactly
        # what this command is meant to run it over.
        old_argv = sys.argv
        sys.argv = ["refresh_classification.py", *write_flag]
        try:
            classify_mod.main()
        finally:
            sys.argv = old_argv

    steps = [
        (f"ETF sector breakdowns — every held ETF plus the benchmark, minus "
         f"the ones with nothing equity inside to classify "
         f"({', '.join(composition_tickers)})", run_compositions),
        (f"ETF company holdings — every held ETF plus the benchmark "
         f"({', '.join(tickers)})", run_holdings),
        ("Vendor sector per held non-ETF instrument", run_classification),
    ]
    failed, warnings = run_steps(steps)

    print("\n=== What the market data is still missing (ops/data_gaps.py) ===")
    try:
        gaps_mod.main()
    except Exception as e:  # noqa: BLE001 — a crash here must not hide what already ran
        print(f"  failed: {type(e).__name__}: {e}")
        failed.append("market data gaps report")

    print()
    if failed:
        print(f"{len(failed)} of {len(steps)} refresher(s) did not complete "
              f"cleanly: {', '.join(failed)}. See their output above for why.")
    else:
        print(f"All {len(steps)} refreshers ran.")
    # A warning is not a refusal — the step it came from still succeeded —
    # but it can land dozens of lines up in a combined run and still be true.
    # Repeated here so "scrolled to the end, saw no refusals, typed --write"
    # cannot skip past it (the XLF-loses-a-sector case, found by review).
    if warnings:
        print(f"{len(warnings)} warning(s) from the refreshers above — read "
              f"before running --write:")
        for w in warnings:
            print(f"  {w}")
    if write:
        print("--write was set: each refresher above persisted what it could.")
    else:
        print("Report only — nothing was written. Re-run with --write to persist.")


if __name__ == "__main__":
    main()
