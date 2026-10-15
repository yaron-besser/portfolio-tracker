---
name: resolve
description: Record his verdict on a bet whose check date has arrived — was he right, wrong, or is it still too early. Use when he types /resolve, says "let's judge X", "was I right about X", "what's due", "close out that claim", or when the session-end hook names a claim as due.
---

# /resolve — a bet reached its check date

**The verdict is always his.** Never infer one, never soften one, never score a
bet he has not judged.

This is the step everything else feeds. `scoreboard()` can only answer "am I
better when I'm sure?" and "are my own ideas better than the ones I read?" from
rows that have been judged. Until a bet is resolved the record cannot tell him
anything about himself.

⚠️ **Run `claims.due_claims()`; never state a backlog from memory.** This line
used to say "two claims have been overdue for weeks", so the skill opened by
describing a problem that had been cleared.

## 🚨 The rules that outrank everything here

**A verdict is written once and can never be amended.** `outcome`,
`resolved_date` and `resolution_note` go in together and `resolve_claim()`
refuses a row that already has them. So it is the **last** thing written in any
run.

**Five values, and only three are judgements.** `right` · `partly-right` ·
`wrong` · `unclear` · `too-early`. The last two mean "I cannot say", and the
scoreboard leaves them out of the denominator rather than scoring them as
misses. **Print all five in the message** — the picker only shows four.

**Changing his mind is not a verdict.** If the old bet is no longer what he
thinks, that is two acts: judge the old one on its own terms, then write a new
one. Never edit the old text; history is immutable.

**Preview, then write.** Every row in full before anything lands.

## Step 1 — what is due

```
venv/bin/python -c "
from src import claims
d = claims.due_claims()
print(d[['id','date','ticker','kind','intent','conviction','check_date']].to_string(index=False) if len(d) else 'nothing due')"
```

`/resolve` with no argument works the due list, oldest first. `/resolve TICKER`
finds that holding's unresolved bets. **One bet per run.**

Before writing anything, check whether a run already started today — claims
with `resolved_date` or `recorded` of today. A verdict written twice cannot be
taken back.

## Step 2 — the brief

Two things stop the run immediately rather than becoming a warning:

- **Already judged.** `outcome` is filled. Say what it says and stop.
- **A `kind="update"` row.** It carries no bet, so there is nothing to judge.

Then put on screen: his own words, his `wrong_if`, the conviction and price he
recorded, what he holds now, the move since, any trade linked, and any level
pointing at this bet.

**Say whether his own `wrong_if` actually happened**, in his terms — and say
plainly when the record cannot tell. `c0003` reads *"weekly close below $198"*;
`positions.csv` carries a last price, not weekly closes, so the honest line is
"IWM is $196.40, which is below $198 — but only you can say whether a weekly
close did it."

## Step 3 — ask, in choices

| Ask | Fills |
|---|---|
| Can you call this one yet? | routes to which three values, or which two |
| Which of the five? | `outcome` |
| **In your own words — what happened, and how do you read it?** | `resolution_note` |
| What happens now — same bet with a new date, a different bet, or nothing? | a successor claim, or none |
| What happens to the level pointing at this? | `ended_date`, or left alone |

Only if the bet planned a trade that never happened, and no fill is linked, ask
`why_not` — all eight values printed: `still-waiting` · `setup-broke` ·
`changed-mind` · `price-ran-away` · `no-capital` · `better-opportunity` ·
`forgot` · `not-sure`. A `hold` or `watch` bet planned no trade, so the
question does not apply and asking it would invent a miss.

**Offer the trade link unprompted.** If a fill exists in that instrument since
the bet was written and `trade_ids` is blank, say so — nothing else prompts it,
and `follow_through()` is empty without it.

## Step 4 — preview, then write in this order

Show every row in full, mark his words from yours, wait for a yes. Then:

1. **Link any fill** the bet should have carried
2. **`record_why_not()`**, if it applies
3. **The successor bet**, if there is one — the call with the most ways to
   refuse, run while nothing irreversible has landed
4. **Its setups**, then **its level** — the new level *before* the old one is
   ended, so the holding is never unwatched
5. **End the old level**, `on=` the day it really stopped applying
6. **The verdict — last.** `resolve_claim(id, outcome, note)`

A successor on a holding he no longer owns is a `watch`, not a `claim` — there
is no live price to record and no position to size against.

## Finish

Show what landed, and say which of the two queues this cleared — due claims and
breached levels are usually the same names. Then **stop. Do not commit.**
