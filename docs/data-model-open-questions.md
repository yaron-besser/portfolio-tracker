# Open questions about the shape of the data

Limits in the database as it stands today. **Stated, not answered** — each one
is yours to decide. Split out of [data-model.md](data-model.md) on 2026-09-27,
which describes the shape as it is; this describes where it runs out.

⚠️ **Closed items are kept, struck through, with the reasoning.** Do not count
the entries to learn how many are open — read the headings. This file said
"nine limits" while two were already marked CLOSED, and CLAUDE.md repeated the
nine. **Every claim here was checked by running the code against the live
tables** — but that was true on the day each was written, and #4 and #6 had
both answered themselves without anyone noticing.

**1. ~~A bet about a theme has no home.~~ CLOSED 2026-09-28 — and the question
was wrong.** Kept here because the reasoning is worth more than the answer.

The problem was real: on 15 and 22 September one decision — *"I bought
technology for a technical reason"* — became **three rows with byte-identical
472-character text**, `c0029` (QQQ), `c0030` (MAGS), `c0031` (XLK), and
`scoreboard()` would have counted one idea three times.

Two wrong answers were proposed first, both mine. **Letting a claim name a
theme** — Yaron refused it: a bet refers to an instrument, so a column that is
sometimes a ticker and sometimes a theme destroys the meaning of `price`,
`nav_pct` and `trade_ids`. Then **a parent `decisions` table**, on the evidence
that the decision-level columns were identical across the three rows. That
evidence was bad: `outcome`, `resolved_date` and `resolution_note` were among
the "identical" ones, and all three are **blank**. They matched because they
are empty.

**The answer, and it is the right one.** Those are three genuinely different
bets — ASML at 4.78% of NAV, MSFT at 5.60%, IGV at 1.58% bought a week later,
and `date` differs too. They will have three different outcomes; MSFT can
easily diverge from IGV. **The verdict belongs to the instrument**, so a parent
table would force one verdict, which is the opposite of what is wanted. The
reasoning: three funds at three altitudes of one theme are not the same bet,
and a theme going well is a different observation from each holding inside it
going well. Both are worth being able to see separately.

So `claims.decision` is a **label**, not a parent: three rows, three verdicts,
one call, and `scoreboard()` reports both — the call rolled up beside each bet
on its own.

⚠️ **The 472-character text still repeats, deliberately.** That is a
normalisation trade taken with his eyes open: each row is the record of why he
bought *that* thing, and he said the duplication does not bother him. If it
ever does, the label can become a pointer to a thought without losing anything.

**2. ~~`levels.csv` has no currency column.~~ CLOSED 2026-09-29.** The column
exists; blank means USD, so all 30 existing rows kept their meaning. `l0029` —
a `hard` stop live at IBKR on 40 shares of IMAE at €102 — could never be
evaluated before it, which made the one kind of level that must never be silent
the one that could never fire.

⚠️ `l0006` (ASML, €1,490) is **still** unevaluable, for a different reason:
ASML is two broker lots in two currencies and the level does not say which one
it means. That remaining half is a decision, not code. Samsung at ₩285,500
would now work.

**3. A sector's parent is checked against a text file, not against the table.**
This one had already gone wrong and was **fixed on 2026-09-27** (commit `f3f45f9`):
`Communication Services`, `Crypto / Digital Assets` and `Fixed Income / Cash`
were used as parents with no row of their own, so eleven instruments — every
cash and FX line, plus BMNR — pointed up at a theme that
`evidence.link("sector", …)` refused. Three rows were added, following the
pattern the other ten tops already use, and
`test_every_parent_is_itself_a_sector_row` now fails if it recurs. No exposure
number moved. **The design is still the open question**: `clusters.parent` must
name a real cluster row, but `sectors.parent` need only appear in a text file.

**4. ~~Clusters hold no position and still call themselves `active`.~~ CLOSED
2026-09-29 — it was never a defect.** Real Estate holding nothing is **your
decision, made twice**: you do a look-through by sector, some ETFs you hold
carry real-estate exposure, and you want every sector to have a cluster. Your
words are in `commentary.csv` as `cm0014`. Materials, Consumer Discretionary
and Communication Services were then created as `sector-breadth` slots for the
same reason. **Do not propose retiring them** — this file was the ammunition
two sessions used to, and you had to say no twice.

⚠️ **Half fixed.** `clusters.set_status()` now exists, so the two can be
retired whenever you decide that is what they are. Nothing does it
*automatically* when the last position leaves, and nothing should — leaving a
theme is a decision, not a side effect of a trade.

**5. The five broker tables have no column validation.** They are protected
against rows changing or disappearing, and their keys must be unique, but
nothing checks that a price is a number or a date is a date. The eleven tables you
fill in by hand all have full schemas. The trust is placed in the machine that
produced the data, which is defensible — but `positions.csv` is in that group
and it is the table every money number comes from.

**6. ~~The table has a shape for real broker orders and it is empty.~~ CLOSED
2026-09-29 — and it had closed itself the day before.** Six `hard` rows carry
`broker_verified=2026-09-29`: BNY, XLF, XBI, MRK, IMAE and COPX. The question
above, and the four documents that repeated it, all still said the record was
empty.

⚠️ **The lesson is worth more than the answer.** This file opens by promising
every claim was checked against the live tables. It was — on the day it was
written. Nothing re-checks it, nothing surfaces it to you, and so a question
that had answered itself sat here asserting the opposite. The parked mirror it
complained about is now deleted.

**7. `nav_history.csv` has a `notes` column you can never use.** It is the one
column in the broker tables that is yours rather than the broker's, but the
append-only check compares every shared column, so writing a note against a past
day is refused as *"This is the broker's record of what happened; it is
permanent."* The column exists and is unusable.

**8. ✅ CLOSED 2026-09-28.** `add_instrument()` set the status once and no
function wrote it again. `clusters.set_status()` and `instruments.set_cluster()`
landed first; `instruments.set_status()` landed with the RSP close and finishes
it. It refuses to mark something `closed` while any broker lot still holds
shares — summed across lots, because `ASML_EUR` going to zero is not `ASML`
closing — and refuses outright when `positions.csv` cannot be read, rather than
reading silence as "nothing held".

⚠️ **Still open underneath it:** ANET and Samsung are held and marked
`incomplete`, so they sit on the gap list with nothing left to fix. That is now
a data question, not a missing-writer one — `instruments.set_status("ANET",
"active")` would clear it, and whether it should is his call.

**9. ~~The broker reports corporate actions and there is no table for them.~~
CLOSED 2026-09-29.** `data/corporate_actions.csv` exists, and the three splits
that were sitting unread in every raw file are in it: NFLX 10-for-1 on
2025-11-17, XLK 2-for-1 and XLU 2-for-1 on 2025-12-05.

All three now reconcile exactly:

```
XLK    +35 traded, -17 sold, +10 split  = 28 held
XLU    +12 traded,            +12 split = 24 held
NFLX    +1 traded, -10 sold,   +9 split =  0 held
```

NFLX had been reporting **-9 shares**, which is not a quantity a portfolio can
have. `ibkr_sync.unreconciled_shares()` reports what trades still cannot
explain, and separates "held before the log" (expected — bought before
`transactions.csv` begins) from "unexplained".

⚠️ **One unexplained row remains, and it is yours to answer: GRNY.** 50 shares
bought 2025-08-01, 250 held, and no trade or corporate action accounts for the
other 200. IBKR's Flex window is a rolling ~365 days and has already passed
whatever happened, so syncing cannot recover it.

⚠️ This entry and `REBUILD.md` once described the same bug with **different
numbers** — "bought 15 and sold 5, then sold 12" here against something else
there — and neither cited the other. Two accounts of one problem that disagree
are worse than one.

**10. The spread-vs-S&P panel cannot tell "outside the index" from "below its
stored floor" for any one company, and his own sector funds cannot settle it
either.** `exposure.benchmark_companies()` reads a company absent from SPY's
stored rows as `below-floor` — "the index holds at most the stored floor of
this, or none" — the same sentence whether the company is ASML (genuinely
not a constituent) or Akamai (a real constituent SPY's own file simply
trims). Fix round 1 found 152 companies reading a confirmed, false absence
before this; fix round 2 made the state uniform rather than guessing at
either one.

Breaking that uniformity using his seven held Select Sector SPDRs — which
hold only S&P 500 members by construction — was proposed and refused, for
three reasons. He holds sector funds for only seven of the eleven GICS
sectors, so Materials — his single largest sector divergence against the
index — has no membership evidence from them at all. The sector funds' own
stored floors are themselves coarser than the benchmark's — measured
2026-10-07, their smallest stored weights run from about 1.3× to 17× SPY's
0.05%, unevenly across the seven — so even inside a sector he does hold,
a company's absence from a sector fund's rows proves nothing about its
membership either. And a flag present on some rows and silent on the rest
reads, to anyone scanning the panel, as absence exactly where it is silent —
the same failure fix round 1 corrected, reappearing by design instead of by
accident.

What would actually settle it is a **constituent list** — names, not
weights, which answers every member evenly rather than only the ones a fund
happens to hold enough of to store. That reaches the internet, which makes
fetching one `/market`'s job, with his two yeses, not something inferred
quietly from data already on file. **Until he decides to fetch one: nothing
is inferred from his sector funds about S&P 500 membership, and
`below-floor` stays uniform.**

---


## One company, several ticker spellings — SETTLED 2026-10-06

Issuers and Yahoo write foreign listings differently. Samsung arrives three
ways: `005930.KS` in his own book, `005930 KS` from Roundhill and every
Bloomberg-style issuer file, and `005930.KQ` from Yahoo — which is also
simply wrong, putting it on KOSDAQ rather than KOSPI.

Unjoined, this is not cosmetic. His Samsung exposure read **$1,580** when the
real figure is **$2,780**: what he bought on purpose, with what DRAM bought
for him counted as an unrelated company.

**His ruling (2026-10-06): be consistent, and the choice was left here.** The
decision taken:

**His book's spelling wins, and nothing stored is rewritten.** A holdings row
records what its source said — that principle already governs the table.
`market.holding_instrument()` translates on the way out instead, and only
ever returns an id that ALREADY EXISTS in `instruments.csv`, so a wrong guess
about an exchange code cannot invent a join; the worst it can do is fail to
make one. `_EXCHANGE_SUFFIX` maps the issuer codes worth the risk.

Rejected: re-spelling the stored rows. It would mean re-spelling them again
every time a fund's source changes, and discarding what the issuer actually
wrote — and `instruments.csv` is the one place that says what a ticker means.

⚠️ **Not handled, deliberately: a root that matches under two different
exchange suffixes.** EEM's `005930.KQ` is Samsung and does not join. It is
$48 of his book. Merging on the root alone would wrongly fuse genuinely
separate dual listings, which is a worse failure than an unjoined $48 — so
this stays a known miss rather than a clever rule.

---
