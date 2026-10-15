# One decision, end to end

Split out of [data-model.md](data-model.md), which had grown past the 300-line
limit `tests/test_repo_shape.py` enforces. The map belongs there; this is the
worked example.

One decision from the demo book, with its actual ids, so you can see where each
part of what was said landed. Every figure below is in `data/` and can be read
back — nothing here is typed from memory.

| When | What happened | What it wrote |
|---|---|---|
| 11 Feb 2025 | the view, attributed | `claims.csv` → **c0005**, `kind=claim`, `origin=fundstrat`, `said_by` names the analyst, `conviction=high`, `intent=open` |
| 11 Feb 2025 | the market, as it was that day | the same row: `price=122.57`, `atr=2.57`, `sma_200=126.91`, `nav_pct=4.82` — computed at write time and **never updated** |
| 11 Feb 2025 | why, in a form you can sort by | `claim_setups.csv` → two rows, `research-call` and `breakout`. One claim, two reasons |
| 11 Feb 2025 | bought it | `transactions.csv` → fill **1000000006**, BUY 29 at `122.57`, `net_cash=-3555.77` |
| 11 Feb 2025 | linked the two | `c0005.trade_ids=1000000006` — the only join between what was planned and what was done |
| 11 Feb 2025 | a real stop at the broker | `levels.csv` → **l0002**, `95.60` `at_or_below`, `kind=hard`, `action=sell_all`, `broker_verified=2026-10-06`, pointing back at `c0005` |
| 13 Aug 2025 | the verdict | `c0005.outcome=right`, `resolved_date`, and a resolution note in the owner's own words |

Three things that row cannot do afterwards. `check_date` cannot move, so the
bet cannot be quietly rescheduled out of the overdue list. `price`, `atr` and
`sma_200` cannot be recomputed, so the past cannot be made to look better than
it was. And `outcome` cannot be rewritten once set.

**What would have refused each step:** the claim, before `NVDA` existed in
`instruments.csv`; the level, before `c0005` existed; and the trade link,
against a `trade_id` the broker never sent — or against a fill for a different
instrument, because the guard checks that the symbol matches the claim.

## The part worth having — where the record disagrees with itself

A level is never edited. When one stops applying it is **ended, with a
reason**, and a replacement is a new row. That turns out to be where the
useful findings are.

`l0012` was a soft level on `CAT` meaning *trim half*. Its `ended_reason`
reads **"Reached and sold in full instead of trimmed."** The level was
reached and the written instruction was not followed — and because the row
records both, that gap is visible a year later instead of being tidied away
into a sale that looks deliberate.

`l0001` was the first level set on `IWM`. It ended on 2026-09-26 as
*"Superseded; a new level has not been set."* That is also a finding: the
position is still held and nothing now watches it. `orphaned_levels()` and
`unchecked_levels()` exist to keep saying so.

## And what the record admits it does not know

Two documents in `evidence.csv` are filed and linked to nothing — they are
reported by `evidence.unlinked()` rather than hidden. Five claims are past
their check date and still unresolved; they stay in `claims.due_claims()`
until a verdict is recorded, because the only way to clear one is to answer
it.

Run `venv/bin/python ops/counts.py` for the current figures. This document
names the command rather than quoting the numbers, for the reason
[data-model.md](data-model.md) gives.
