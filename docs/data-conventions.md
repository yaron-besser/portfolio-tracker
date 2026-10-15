# Data conventions — sync, sectors, ETFs, retention

Split out of `CLAUDE.md` on 2026-08-09. Read when syncing, when a new
position appears in `data/positions.csv`, or when doing periodic cleanup.

---

## What sync writes, and how

```
venv/bin/python -m src.ibkr_sync            # fetch fresh (~1 per 10-15 min)
venv/bin/python -m src.ibkr_sync --cached   # re-parse the latest XML
```

⚠️ **Never retry a failed fetch in a loop.** That rate limit is real and
IBKR enforces it with an escalating lockout:

| Code | Means | What to do |
|---|---|---|
| `1001` | *"Statement could not be generated at this time."* IBKR's side — it is rebuilding, busy, or the query was just edited | Wait **15 minutes**, try **once** |
| `1025` | *"Too many failed attempts."* You are now locked out | **Stop.** Wait an hour or more. Retrying extends the lockout |

Learned on 2026-09-29 by doing it wrong: an automated retry loop turned a
temporary `1001` into a `1025` lockout, and then kept the lockout alive. The
limit was already written on the line above and was ignored anyway — so it is
repeated here as a rule with teeth rather than a parenthesis.

**Use `--cached` while iterating.** It re-parses the newest file in `data/raw/`
and never touches the network, which is what you want for every purpose except
genuinely wanting today's prices.

| File | Merge behaviour |
|---|---|
| `positions.csv` | refreshed in place, **preserving** the per-lot `notes`. What the ticker means lives in `instruments.csv` |
| `transactions.csv` | append-only, deduped on `trade_id` — safe to re-run |
| `nav_history.csv` | append-only, keyed on `date`. The statement wins for days it still covers; older days are **carried forward** |
| `cash_transactions.csv` | dividends, taxes, interest, deposits — append + dedup |
| `borrow_fees.csv` | daily borrow-fee accruals on shorts — append + dedup |
| `corporate_actions.csv` | splits, mergers, spin-offs — append + dedup on `transaction_id`. **Added 2026-09-29**; the section had been downloaded and discarded since the importer was written, and an unrecorded XLK 2-for-1 was why the trade log did not add up |
| `option_events.csv` | contract detail for an option or future leaving the book. ⚠️ **Overlaps `transactions.csv` — never sum both.** Append + dedup on `trade_id` |

**Everything below `positions.csv` only ever gains rows**, and the commit guard
enforces it: a row that changes or disappears blocks the commit. `positions.csv`
is the exception
named in the table above — it is a snapshot, rewritten in place every sync, and
is not in `ibkr_sync.BROKER_TABLES`.

This is not theoretical. Before the guard existed, 285 dividend IDs were
silently rewritten and 60 days of NAV were deleted by the broker's rolling
365-day window.

⚠️ **`positions.csv` carries a `currency` column** — the currency `cost_basis`
and `last_price` are quoted in. `market_value` is always USD. Four lots are not
dollar-priced, and a price level compared across currencies is wrong in
whichever direction hurts, so `levels._price_for()` refuses rather than guesses.

**`Added: [...]`** → the lot is already recorded; now say what it *is*, in
`data/instruments.csv`:

```python
from src import instruments
instruments.add_instrument("ABCD", name="...", instrument_type="stock")
instruments.gaps()      # what is still incomplete
```

Only id, name and type are required — a missing sector must never stop a trade
being recorded. Until sector and cluster are filled in, the position does not
roll up into exposure. ETFs get **no** sector — add a row to
`data/market/etf_compositions.csv` instead, in the same session.

**`instruments.broker_sub_category`** is IBKR's own finer label (ADR, REIT,
NY REG SHRS, ETF, COMMON, an option's `C`), dated in `broker_sub_category_as_of`
and written only by `ops/sync_instrument_types.py`. One instrument can carry
more than one (ASML: `COMMON;NY REG SHRS`) — `;`-separated like `isin` and
`claims.trade_ids`; read with `claims.split_paths()`, never `==`.

**`Removed: [...]`** → **confirm with the user before archiving anything.**
It usually means a closed position, but two other causes exist:
- **One note covers several lots.** `ASML_USD` and `ASML_EUR` both resolve
  to `notes/positions/ASML.md`. Selling only the EUR lot prints
  `Removed: ['ASML_EUR']` while shares are still held — archiving then
  strips the live position of its thesis and its stop row.
- **A partial or failed Flex pull** can drop rows that were never sold.

Once the whole **instrument** is confirmed gone from `positions.csv` — check
every lot sharing that `instrument_id`, not just the one named in the message —
call `instruments.set_status(id, "closed")` and move the note to
**`notes/positions/closed/`**. ⚠️ **Not `_archive/`** (changed 2026-09-28): a
closed position is a trade to learn from, and `_archive/` had become a junk
drawer holding notes for nine names he still owns. `set_status()` re-checks
every lot itself and refuses if any still holds shares.

**EOD delay:** same-day trades usually do not appear until the next business
morning. `+0 new trades` after a known trade is expected — trust the archive
files until sync trues up.

**Append-only is enforced at commit, not at sync.** `check_broker_table()`
catches a value silently rewritten, a row disappearing or a key duplicated —
but it runs only at `pre-commit:382 check_broker_table`, so a bad sync writes
the file and you find out when you save. ⚠️ `positions.csv` is **not**
covered: rewritten in full every sync, it can be neither append-only nor
key-checked.

Setup, tokens, and Flex query configuration:
[ibkr-flex-setup.md](ibkr-flex-setup.md).

### Recorded, not yet read

`cash_transactions`, `borrow_fees` and `nav_history` are written by the sync
and read by nothing. Deliberate, not a defect — this is a database, and the
question they answer has not been asked yet. `nav_history`'s only ever consumer
was a `returns` module, archived in Phase 2.

⚠️ **Do not "clean up" by dropping their columns.** Several are constant today
(`borrow_fees.currency` is USD throughout, `nav_history.withdrawal` zero
throughout; `ops/counts.py` for row counts). Yaron's call, 2026-09-29: broker
tables are history, dropping a column is irreversible, and IBKR's Flex window
is a rolling ~365 days, so what is dropped may not be re-suppliable. **A column
that is empty because the feature has not happened is not a dead column.**

### What the broker sends that nothing reads

About 93% of each 2.2 MB download — 12,207 `ConversionRate` records per file,
plus unbundled commissions and two performance summaries. Run
`ibkr_import.unparsed_sections()`, or read the report the sync prints. Changing
what IBKR sends is a manual edit to the Flex query on their website and is
one-way for history.

---

## Which name to show, and which to leave alone

Three files carry a `description` column and it looks like duplication. Only
some of it is, so the rule is per-column, not per-word:

| Column | What it really is | Rule |
|---|---|---|
| `instruments.name` | today's name for the instrument | **The only name to show a person.** Reached from any table via `instrument_id` |
| `transactions.description`, `borrow_fees.description` | what IBKR called it **on the day of that trade** | Keep, never read as a name |
| `cash_transactions.description` | the text of one specific dividend — `SOXX(US4642875235) CASH DIVIDEND USD 0.483276 PER SHARE` | Keep. Not a name at all; it varies for 49 of 54 symbols |

The middle row is the one that looks wrong and isn't. `MSTR` appears in
`transactions.csv` as both `MICROSTRATEGY INC-CL A` and `STRATEGY INC` —
the company renamed itself, and each trade correctly records the name it had
at the time. Overwriting those with today's name would make the audit trail
disagree with the broker's own confirmations. `ASML`, `XLK` and `XLV` have the
same pattern for share-class and formatting reasons.

So: **the broker's `description` is history and is never edited; the name in
front of a human always comes from `data/instruments.csv`.**

```python
from src import instruments
instruments.get(instruments.resolve("MSTR"))["name"]   # the name to display
```

## Sector taxonomy (the `sector` column in `instruments.csv`)

Sector and cluster live in `data/instruments.csv`, one row per instrument —
**not** in `positions.csv`, which was the old home and held a second, drifting
copy until Phase 2d. A new instrument needs `sector` filled in, and the value
must start with a recognised prefix so look-through rolls it up correctly:

| Prefix | Parent sector |
|---|---|
| `Tech...` | Technology |
| `Financials...` | Financials |
| `Consumer Disc...` | Consumer Discretionary |
| `Consumer Staples...` | Consumer Staples |
| `Comm Services...` | Communication Services |
| `Industrials...` | Industrials |
| `Healthcare...` | Healthcare |
| `Energy...` | Energy |
| `Materials...` | Materials |
| `Utilities...` | Utilities |
| `Real Estate...` | Real Estate |
| `Crypto...` | Crypto / Digital Assets |
| `Fixed Income...` or `Cash...` | Fixed Income / Cash |

⚠️ **A prefix is not enough — the label must already be a row in
`data/sectors.csv`.** Prefix matching is gone and nothing falls into "Other"
any more: `sectors.parent()` **raises** on a label it does not have a row for,
and the commit guard then refuses the file. So `Energy - Nuclear` is not usable
until it exists:

```python
from src import sectors
sectors.add_sector("Energy - Nuclear", parent="Energy")   # then use the label
```

If you do not want to decide tonight, **leave `sector` blank.** The trade still
records, and `instruments.gaps()` will keep asking. That is always better than
inventing a label, because an invented one blocks every later commit that
touches `instruments.csv` — not just yours.

The parent list above is still the truth about what a sub-sector rolls up to,
and `exposure.parent_sector()` is what does the rolling up. It is live:
`exposure.by_sector()` is the answer to "what am I exposed to".

**Known correction:** PM (Philip Morris) is tobacco → **Consumer Staples**,
not Healthcare, despite sitting next to defensive dividend payers.
**SGOV is cash**, not equity — exclude it from equity-book denominators and
include it in dry-powder totals.

## ETFs

ETFs get no `sector` in `instruments.csv` — they are decomposed via
`data/market/etf_compositions.csv`. **If sync adds a new ETF, add its composition
row in the same session.** A held ETF with no composition row silently
under-reports sector exposure, and a position then gets sized against a
number that is quietly wrong.

Every composition row carries **`source_quality`** (filing / issuer / vendor /
issuer-by-hand / estimate) and **`as_of`** where a date is known. Blank `as_of`
means unknown — never a guessed date.

```python
from src import exposure
exposure.by_sector()      # look-through sector exposure, with pct_estimated
exposure.by_sector()      # every sector's real look-through exposure
exposure.coverage()       # which ETFs your numbers actually depend on
```

⚠️ **Quote the caveat with the number, and get the caveat from the code.**
`exposure.coverage()` names which ETFs an answer depends on, how old each
breakdown is, and which rows are estimates; `pct_estimated` on each sector row
reports the guessed share.

⚠️ This paragraph used to state those figures instead — "no sector figure rests
on an estimate", "22 ETFs have no `as_of`", "MAGS returned no usable
breakdown". All three were wrong within weeks, and the replacement figures
written on 2026-09-29 were wrong again by 2026-10-01, when a refresh re-dated
most of the file and took DRAM off the estimate list. **An instruction to quote
a caveat accurately is the last place to hardcode one** — run
`exposure.coverage()`.

Refresh one: `venv/bin/python ops/refresh_etf_compositions.py QQQ --write` —
writes into `data/market/etf_compositions.csv`.
A provider covering under 85% of the fund is **refused**, not scaled up —
Alpha Vantage returned 28.9% for MAGS, and normalising that would have
invented most of a fund while the weights still summed to 100%.

The weights for one ETF must sum to 1.0; the commit guard enforces it. A
partial breakdown silently under-reports every sector it leaves out, and the
totals still look tidy.

**Refresh everything** — every held ETF's sector breakdown, every held ETF's
company holdings, and a vendor sector for every held non-ETF, plus `SPY` so
the sector view has a benchmark to compare against:

```bash
venv/bin/python ops/refresh_market.py             # report only — fetches, writes nothing
venv/bin/python ops/refresh_market.py --write      # persists what each refresher found
venv/bin/python ops/data_gaps.py                   # what the market data is still missing
```

⚠️ **Report-only still calls every provider** — it is the write to a CSV that
`--write` gates, not the fetch. Alpha Vantage's free tier allows only a
handful of calls per minute and a capped number per day
(`src/positions_fundamentals.py`), so a full run deliberately paces itself and
**takes several minutes**; running it twice in one sitting can exhaust the
day's quota. A refusal on any one fund is reported and does not stop the rest
— he gets one complete picture, never a run that died partway through.

`SPY` is never held and never will be (`status=benchmark` in
`data/instruments.csv`, `ops/vocab/instrument_status`) — it exists only so
`exposure.by_sector()` has something to measure his sector weights against.
`tests/test_refresh_market.py` proves it can never reach an exposure figure
even once its sector breakdown is on file; `tests/test_data_gaps.py` proves
it never shows up in `orphaned_holdings()`'s "not held, his call whether to
clear" list either — the one place a benchmark's row in `etf_holdings.csv`
could otherwise invite deleting it.

---

## File retention

Three tiers. Never auto-delete; always preserve `_archive/` and CSV data.
(The constitution itself was archived 2026-08-14 and is being rewritten.)

- **🟢 PERMANENT** — `_archive/notes/portfolio_thesis.md` (archived 2026-08-14,
  **not in force**), all of `notes/positions/` including `closed/` and
  `watchlist/`, `notes/clusters/`, `_archive/notes/insights/` (the only
  performance retrospectives that exist), every `data/*.csv`,
  `data/fundamentals/*.json`, and — parked but equally permanent —
  `_archive/data/damodaran/**` and `_archive/data/models/**`.
  **`_archive/data/models/damodaran_regressions/_raw/` keeps each sheet exactly
  as received**; that untouched copy is the audit trail for every cleaned CSV
  derived from it, so it is never pruned even though it looks redundant.
- **🟡 ARCHIVE after N months** — picks (6 mo), macro views (12 mo), cleanup
  files (12 mo). Move to a dated subfolder; never delete.
  ⚠️ This tier used to name weekly market files and articles too. Neither
  `notes/market/` nor `notes/articles/` exists, and `journal-workflows.md` says
  so explicitly — two docs edited the same day contradicting each other.
- **🔴 PRUNE** — `data/raw/ibkr_flex_*.xml` (keep the last 4-5), `data/*.bak`
  (keep 1-2). Working artifacts, not history.

The monthly-review template was archived 2026-08-14 and has no replacement.

⚠️ `data/raw/` XML files contain the IBKR account ID — handle with care.
