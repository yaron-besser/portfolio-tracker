# The portfolio page — design

**Status:** designed 2026-10-01 with Yaron. The page is not built. Part of Stage 0 is
done: every held ETF's breakdown was refreshed, and the refresh script's provider rule
was fixed after it degraded two funds — see *What already happened* below. This is
REBUILD.md Phase 4, and it answers the deferred decision *"what the collapsed dashboard
keeps or adds"* and the deferred *"fate of `benchmark` / `spy_loader`"* (§7).

## Why

Yaron, 2026-10-01, opening the session:

> "Now I want to create the new UI for the stocks."

And, when asked what one thing it should tell him:

> **"How my money is spread."**

Then, later, the sharper version of the same request:

> "I'm getting advice and I do my own analysis on single stocks or ETFs or
> macro, but this doesn't matter — because if I'm not constructing a good
> portfolio, it doesn't matter. I need to construct a balanced portfolio, and
> that's why I'm splitting into themes and sectors, because I want to know the
> weightings."

There is no live UI. The six Streamlit pages were archived in Phase 0 and
deleted; `ls src/` has no dashboard module. This is built fresh.

## What it is, and what it is not

**It is a read-only view over the settled data layer.** It writes nothing, ever
— not to `data/`, not to `notes/`. Every feature here was tested against the
three jobs in CLAUDE.md: *record what he decided*, *read it back*, *show the
spread*. Anything that did none of the three was not built.

**It describes construction. It never prescribes a target.** "Two of your
themes hold much the same companies" is a fact and belongs. "Technology
should be 25%" is advice, there are no caps since the constitution was
archived, and it does not belong. This line was drawn explicitly with him
and it governs every risk and construction number in this document.

**Chat remains the primary interface** (REBUILD.md R1/R2). The page is for
looking; the record is still written through the skills.

## Decisions taken

| # | Decision | Why |
|---|---|---|
| D1 | **A built page, not a running app** | One command builds a self-contained file and opens it. No server to start, nothing that goes blank when a library breaks, and it physically cannot write to `data/`. Streamlit was considered and rejected: its one real advantage is live freshness, and his numbers only change when he syncs or writes — both manual. R5 is reliability over speed. |
| D2 | **The page computes nothing** | Every number comes from a function in `src/`. If the page needs a number nobody computes, it is added to the module that owns it, never to the page. This is P7, and it is what stops the page and the chat from ever disagreeing. |
| D3 | **Four tabs** | Spread · Holdings · Record · Performance. One per build stage, so each tab arrives finished rather than half-populated. |
| D4 | **Institutional look** | White, dense, near-monochrome; bar length and type weight carry the meaning. Tabular figures so columns align. Chosen over a dark terminal style and an editorial style after seeing all three. |
| D5 | **Ranked bars, drilled three levels** | Group → theme → the actual tickers. Everything starts collapsed, so the page opens at roughly the number of themes rather than the number of lots. Chosen over a treemap (hard to compare similar sizes), donuts (too many slices), and a flat table (a spreadsheet, not a picture of the spread). |
| D6 | **One control with three settings, not two switches** | *Everything · Minus cash · Pure equity.* The three views are nested — each strips one more thing — so two independent switches would give four states and no home. A line under the header states the current setting in words and what it is a share of. |
| D7 | **P&L is present but quiet** | Grey, never coloured, never sortable. Visible when looked for; it does not turn a map of the spread into a scoreboard. |
| D8 | **Shorts show as negative bars, flagged** | A short is negative exposure and is drawn that way. The page states plainly that the instrument taxonomy for shorts, futures and options is still open (REBUILD.md §2c) rather than implying the labelling is settled. |
| D9 | **Everything in dollars** | `positions.market_value` is already USD. One currency means every bar is comparable and nothing is converted twice. A euro lot shows its local price on the stock page only, because that is the number the broker shows. |
| D10 | **Rebuilds after every IBKR sync, and on demand** | He can never be looking at a page older than his data. Not on a schedule — a background job that fails quietly belongs in Phase 5. |
| D11 | **The built page is not committed** | Machine output, regenerated in seconds, and it would change on every sync. It lands in `ops/`, per CLAUDE.md. |
| D12 | **Every number says where it came from** | Hover a figure, see the function that produced it. The value is not tidiness: when the page and the chat disagree, both he and the session know instantly which to trust. |
| D13 | **The sector view uses a vendor's classification, not his typing** | His words: *"it's not my decision, it's how the S&P does their decisions, not mine."* Today `instruments.sector` is hand-typed by him and rolled up through `sectors.csv`. The sector view will instead use a fetched, dated, vendor-named classification. His own label is kept, and the page lists every holding where the two disagree. **It goes in `instruments.provider_sector`, which already exists and already holds exactly this** — gaining `provider_sector_as_of` and `provider_sector_source` beside it. A separate `classification.csv` was proposed and rejected: a second home for a fact a table already holds is the P7 failure verbatim. |
| D14 | **Theme and sector never borrow each other's numbers** | TSLA is theme *AI Applications* (under Technology) and sector *Consumer Discretionary*. Both are correct; they answer different questions. An earlier draft of the sector table carried an "Intent" column built by matching theme names to sector names — he spotted it, it was a construct that existed nowhere in his data, and it was removed. |
| D15 | **Fetched market data lives in `data/market/`** | Physically separate from his record, so it is obvious at a glance which files hold his decisions and which hold vendor cache. `etf_compositions.csv` moves there — it is fetched by a script, and the data model currently files it under the ones he fills in, which is wrong. The data model gains a third group: **the ones a provider fills.** |
| D16 | **Daily prices: one file, strictly append-only, sorted by date** | His call, against a suggestion of monthly partitions. The concern was overstated: git delta-compresses a file that only grows at the end, so the repository does not carry a fresh copy per commit. **That only holds while the file is never re-sorted or rewritten** — a refresh appends new dates and touches nothing above them. Same discipline as every other append-only table here. |
| D17 | **A tab strip, a sticky side list, a statement look, one type scale** | His reaction to the first build, 2026-10-07: *"really hard to navigate with my eyes… I want it more user-friendly, more professional. We said we want tabs — right now there is no tabs."* Chosen from mockups: the four tabs run across the top (the three not yet built stay clickable, each naming what it will hold), the Spread panels are listed down the left and stay put while the page scrolls, ruled rows with right-aligned figures replace the dashboard look, one type scale of four sizes replaces seven near-identical ones, and the caveats collapse behind a single summary line that unfolds. |
| D18 | **Notes open in an overlay, not a new page** | His words, 2026-10-07: *"when I click to see my notes, my thesis files, I want it to be a pop-up window and not like a new page… right now it looks quite weird."* The note is embedded into the page at build time — the page makes no external request — and turned from Markdown into HTML at build time only, never in the browser. |

✅ **D13's classification choice is implemented as of 2026-10-06, and
`CLAUDE.md` says so.** `exposure.by_sector()` defaults to
`instruments.provider_sector`, built for this row's panel; his `sector`
keeps its sub-sector granularity, keeps driving `sectors.csv`, and keeps
winning every other reader. `exposure.sector_disagreements()` names the
vendor, the date and the dollar amount at stake, and lists every holding
where the two disagree. REBUILD.md §8.1's conflict note was updated in the
same commit, which is what §8.1 requires.

## The honesty contract

Not optional, never dropped for space. Every screen carries, in plain words:

- **How old the prices are**, using the oldest lot, not the newest.
- **How much of the exposure answer is estimated** — which ETF breakdowns the
  number depends on, how old each is, and which are an estimate rather than
  issuer data. A breakdown with no date reads as *age unknown*, never as recent.
- **Which money could not be classified at all**, shown as unclassified rather
  than silently redistributed.
- **A refusal to show a hit rate from too few judged bets.** It says
  *"not enough yet — N judged"*. A percentage from three bets is a lie with a
  decimal point, and `scoreboard()` already refuses to collapse its two hit-rate
  figures into one.
- **That the classification is a vendor's, not GICS.** Real GICS is licensed and
  cannot be fetched. The page names its source instead of implying authority.
- **When the page was built, and from data synced when.**

No number that `ops/counts.py` or a `src/` function can compute is ever written
into this document or into the page's prose.

## The four tabs

### 1 — Spread

The main job. In order down one column:

1. **Header** — net asset value, cash and T-bills, biggest five. Three numbers
   only; anything needing a sentence of explanation lives further down.
   The three-setting control sits here with its plain-English state line.
2. **By theme** — ranked bars, drilled three levels, collapsed by default.
   Empty themes shown greyed at the end, because stepping out of a theme is
   part of the spread. Clicking a ticker opens its page; clicking a theme opens
   the theme's note.
3. **By sector** — the look-through, every ETF opened up. Each row splits what
   he bought himself from what an ETF holds for him, with estimated slices
   marked. Beside it, the S&P weight and the difference — stated as a
   difference, never as a target.
4. **Against the S&P, company by company** — his true exposure to each company beside
   the index's weight in it, and the difference. **His addition, 2026-10-06.** Asked
   whether the plan already covered it, he said he wanted *"a comparison to the S&P
   holding"*; told the sector comparison was planned and the company one was not, he
   chose to add it — *"for me it's really important to be part of the main page."*
   Ordered by the size of the difference, because where he is most unlike the index is
   what nothing else in the record shows. Three facts that look alike and are not: a
   company the index holds and he does not (a fact about his construction, zero and not
   missing), a company he holds that the index does not (true — ASML, TSM and Samsung
   are outside the S&P 500), and a company the index's rows on file simply do not reach,
   which is **unknown** and never "absent".
5. **True exposure to one name** — direct holding plus the share held for him
   inside every ETF. This is the panel that justified itself during the design:
   NVDA is his *smallest* AI Infrastructure holding by direct value while being
   one of his largest true exposures, and nothing in the record shows that today.
6. **Themes holding the same names** — for each pair of themes, how much of
   their money sits in identical companies. Two themes can be one bet.
7. **How it's built** — effective number of bets, worst drawdown, currency
   split. Each with a sentence, because none of them is glanceable alone.

### 2 — Holdings

Search a ticker, or arrive by clicking one.

**The stock page: facts on top, story below.** A short header — what he owns,
whether the thesis is complete, open bets, levels — then everything he has ever
said, read, or done about it in date order. Chosen over fixed blocks by type
and over a pure timeline: the header answers *where do I stand* in a second,
the stream answers *how did I get here* if he keeps reading.

A theme page works the same way: the theme's own note, then what is inside it.

**Everything he has read**, as its own list, filterable by ticker and theme.

Closed positions live here too, with what he wrote at the time and how it ended.

### 3 — Record

The record read back at him. Four of these were chosen over four others:

- **Did I do what I wrote** — every plan, whether a trade followed, how many
  days later. `claims.follow_through()` already computes it and nothing
  displays it. This is the gap he diagnosed himself on 2026-07-28, and it is
  the single highest-value item in this document.
- **What I am not thinking about** — positions ranked by how long since he last
  wrote anything, weighted by money.
- **Which kinds of reasons work** — hit rate by setup, from `claim_setups.csv`.
- **Watchlist → did I ever buy it** — things watched and never bought.
- Plus: bets due a verdict, his stops and triggers with how long since each was
  confirmed, what changed since the last build, and what is missing.

### 4 — Performance

Against the S&P, which is his benchmark.

**Deposits and withdrawals must be stripped out**, or money he paid in reads
as performance. This is the one part of the project that can be *subtly* wrong
rather than obviously broken, which is why it is built last.

🚨 **`nav_history.csv` has `deposit` and `withdrawal` columns, and they are not
reliably filled.** The account-opening transfer — by far the largest inflow in
the file — is recorded in the free-text `notes` column with `deposit` reading
`0.0`. `withdrawal` is zero on every row, which `docs/data-model.md` already
says. Build the comparison by subtracting those two columns and it will look
clean and report the account opening as a one-day gain of several thousand
percent.

So: reconcile against `cash_transactions.csv` and the `notes` column before
trusting either column, and assert that the reconstructed series has no
single-day move that only an unrecorded transfer could explain.

**The NAV series also starts before the money does.** The first rows carry
`nav`, `cash` and `stock` all at zero, and the account funds weeks later. Drop
them, or the first real day is an infinite return. `nav_history.csv` is the
check; do not take a start date from this document.

SPY prices are not in the repo and will be fetched. The old `benchmark` and
`spy_loader` modules are gone — deleted, not archived — so this is written
fresh. That resolves the §7 deferred decision: they are not restored.

## How it is built

### Code

| | |
|---|---|
| `src/page.py` | pure functions shaping existing `src/` output into what the page shows. Adds no new computation of its own — anything missing goes into the module that owns it (D2). |
| `ops/build_page.py` | assembles the page and opens it |
| `tests/test_page.py` | tests the shaping functions. No test is ever weakened to go green. |

### Where the data lives

The data model gains a third group alongside *the ones you fill in* and
*the ones the broker fills*: **the ones a provider fills**, in `data/market/`.

| File | Holds | Shape |
|---|---|---|
| `data/market/etf_compositions.csv` | **moved** from `data/`. Sector weights per ETF, refreshed and dated | existing |
| `data/market/etf_holdings.csv` | **new.** `ticker` + `holding` → weight, `as_of`, `source`. Which *companies* each ETF owns | small |
| `instruments.csv` | **not new** — gains `provider_sector_as_of` and `provider_sector_source` beside the `provider_sector` column it already has. The authority for the sector view (D13). No separate classification table: that would be a second home for a fact this table already holds | existing |
| `data/market/prices.csv` | **new.** `date` + `ticker` → close. One file, append-only, sorted by date (D16) | large, grows daily |
| `data/weight_history.csv` | **new, and his** — a snapshot of theme and sector weights at each build. This is a record of his portfolio, not vendor data, so it stays in `data/` | grows per build |

Moving `etf_compositions.csv` touches `docs/data-model.md`, the pre-commit
guard, the tests, `src/exposure.py` and `ops/refresh_etf_compositions.py`.
Contained, but real; it belongs to Stage 0.

### ⚠️ Three features depend on company-level holdings data

**Against the S&P, company by company**, **true exposure to one name** and
**themes holding the same names** all need *which companies* an ETF owns — the
S&P panel on both sides, his through every fund and the index's through SPY's own
holdings file. `etf_compositions.csv` is sector-only (QQQ: a handful of rows);
`top_holding` exists only for MAGS, equal-weighted with one holding per sector.

So `etf_holdings.csv` is a precondition for all three, with a limit the page
must state: providers return a fund's **top ten** holdings, around half a
broad fund's weight. The honest number is a floor — *"your NVDA exposure is
**at least** X%"*, never precise — a name barely held directly can still be
one of his largest true exposures.

His side landed in Stage 0; SPY's own holdings file is what's still outstanding.

Any claim about true exposure made before this table exists is a guess. One was
made during the design session, from an invented mockup figure, and is withdrawn.

## Stages

Each stage ends with a page he can open.

| | | |
|---|---|---|
| **0** | **The data** | His instruction: *"before we do anything, you need to get the breakdowns of the ETF."* Create `data/market/` and move `etf_compositions.csv` into it, with the data model, guard, tests and callers updated. Refresh every held ETF so no row is of unknown age. Build `etf_holdings.csv` and `classification.csv`. Fetch SPY's breakdown. **Everything else is blocked on this** — the sector table cannot be trusted and three panels cannot exist until it is done. |
| **1** | **Spread** | The tab above, end to end — including the company-by-company comparison against the S&P he added on 2026-10-06. |
| **2** | **Holdings** | Search, the stock page, theme pages, everything he has read, closed positions. |
| **3** | **Record** | Follow-through, staleness, setups, watchlist conversion, due bets, levels, what changed, what is missing. |
| **4** | **Performance** | Deposit-adjusted return vs the S&P. Account volatility and beta. |
| **5** | **Price history** | A new dated price table for the holdings that have one, then correlation **between themes** (not a grid of every pair — unreadable, and themes are how he thinks), risk contribution versus weight, and how much of the account's movement comes from a single factor. Every figure shown with the period it was measured over, because the history is short and many positions are newer than it. |

## Known limits, stated once

- **`yfinance` is unofficial and breaks periodically.** It is already a
  dependency but nothing load-bearing uses it. Stages 0, 4 and 5 change that.
  Accepted deliberately; the alternative is no classification data at all.
- **The price history is short** — the funded part of the NAV series is under
  eighteen months and many positions are newer. Beta and correlation from it carry wide error bars and
  are shown with their measurement window.
- **A refused ETF breakdown stays refused.** When a provider covers too little
  of a fund, the script stops rather than scaling a partial answer to 100%.
  Those funds keep their old row and are named on the page.
- **IBKR's resting stop orders may not be reachable.** If they are, hard stops
  verify themselves and a real hole closes — alerting has been off since
  2026-08-08. This is a cheap thing to check and it is not promised until checked.

## What already happened, 2026-10-01

Part of Stage 0 ran during the design session, on his instruction. Every held
ETF's breakdown was refreshed and is now dated except four — SGOV, IBIT, SLV
and MAGS — which no provider reports sector weights for; their existing rows
are correct, they only lack a date. `exposure.coverage()` is the live picture.

The refresh also **degraded IWM and VTI** before anyone noticed, by preferring
an issuer feed that omitted Real Estate and then scaled the rest to 100%. The
rule is now best-coverage-wins, and a refresh that would delete a whole sector
says so out loud; both funds were re-run and read correctly. The full account
lives in `fetch()`'s docstring and `tests/test_refresh_etf_compositions.py`, not here.

One thing was not reversed: **DRAM lost a small cash sleeve.** The old sleeve
was hand-typed and Morningstar reports the fund as entirely Technology, which
is the better source.

## Still his to decide

- **The stale instruction on the ASML level (`l0006`)** — whether it should be
  corrected: it says *"levels.csv has no currency column"*, but the column was
  added in the structural cleanup, so the level may now be checkable.
- The catalysts pass (REBUILD.md) is still open and unaffected by this work.

**Decided during the session, recorded here so they are not re-opened by
accident:** refused ETF breakdowns are hand-entered from the issuer's own page,
with the issuer's as-of date and a note saying it was entered by hand — MAGS
alone is enough money to justify it. `partly-right` hit rates keep the existing
refusal to collapse into one figure. The look-through classification is a
vendor's and the page says so; GICS is licensed and is not an option.
