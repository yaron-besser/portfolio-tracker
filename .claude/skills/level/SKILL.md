---
name: level
description: Record what happened when a price you set was reached — a stop that fired, or a line that made you look. Use when he types /level, says "my stop hit", "IWM touched 285", "a level was reached", "what levels are breached", or when the session-end hook names a breached level.
---

# /level — a price he set was reached

**Record what happened. Never tell him what to do.**

A level is a number he wrote down with an instruction attached. When price
reaches it, exactly one of three things is true: a real order filled, he looked
and decided something, or he looked and decided nothing. All three are
recordable. Only the third one used to have nowhere to go.

## 🚨 The rules that outrank everything here

**Nothing here is advice.** This skill asks what happened and files it. It
never says whether to sell, hold or buy — that is his call and the record's
only job is to remember it accurately ([`CLAUDE.md`](../../../CLAUDE.md)).

**Everything this writes is permanent.** `claims.csv` and `levels.csv` are
append-only and the commit guard enforces it. A duplicate row cannot be
deleted — only explained by another row. So **check for a half-finished run
before writing anything** (Step 0), and **write the verdict last** (Step 4).

**Preview, then write.** He sees every row in full before anything lands, and
if he changes one thing, reprint the whole thing.

**His words, never yours.** `ended_reason`, `resolution_note` and any new
`claim` are his. Options you offer are built from his own text and his own
data, and you say where each came from.

## Step 0 — has a run already started today?

Cheap, and it is the only step that prevents something unfixable.

```
venv/bin/python -c "
from datetime import date
from src import claims, levels
t = date.today().isoformat()
lv, cl = levels.load_levels(), claims.load_claims()
hits = {'levels added': lv[lv.recorded == t].id.tolist(),
        'levels ended': lv[lv.ended_date == t].id.tolist(),
        'claims added': cl[cl.recorded == t].id.tolist(),
        'claims judged': cl[cl.resolved_date == t].id.tolist()}
print({k: v for k, v in hits.items() if v} or 'nothing written today — fresh run')"
```

If anything comes back, **say so and ask before continuing.** A run that
stopped between adding a replacement level and ending the old one leaves both
live; resume at the end, do not start again. Re-running a whole flow writes a
second claim with identical text and a second level at the same price, and
nothing in the system objects.

## Step 1 — what was actually reached

⚠️ **`breached_levels()` is not the whole answer.** A hard stop that already
filled leaves the position, so it cannot be priced and never appears there —
it turns up in `orphaned_levels()`. Read all four, and say the price's age out
loud, because a level judged against a stale price is judged against nothing.

```
venv/bin/python -c "
from src import levels, instruments
print('price age:', instruments.price_warning() or 'current')
for name, f in (('reached', levels.breached_levels),
                ('could not be priced', levels.unchecked_levels),
                ('on something no longer held', levels.orphaned_levels),
                ('hard, unconfirmed 30+ days', levels.stale_hard_levels)):
    df = f()
    print(f'\n{name}:'); print(df.to_string(index=False) if len(df) else '  none')"
```

Show him the list in one line each and let him pick one. **One level per run.**

## Step 2 — the dossier, before any question

For the level he picked, put on screen: the level itself and its instruction;
the bet behind it (`claim_id`) with its own `wrong_if`, conviction and
`check_date`; how many shares he still holds; and any trade in that instrument
since the level was set. `holding.summary()` has all of it.

Three things to check and say out loud:

- **Is the bet already judged?** If `outcome` is filled, do not ask for a
  verdict. The thing to fix is a live level on a dead bet.
- **Does the level's action contradict the bet's intent?** `l0004` is a
  `trim_half` level on `c0007`, whose intent is `hold` — a bet that planned no
  trade. Say so plainly; it changes what can be recorded (see Step 3).
- **Is the position already gone?** `levels.shares_held()` across every lot.
  `ASML_EUR` closing is not `ASML` closing.

## Step 3 — ask, in choices

Five questions at most. Everything else is derived — the fill price, the dates,
the size, which trade matched. Never ask what the record can work out.

| Ask | Fills |
|---|---|
| Did the order fill, or did you look and decide? | routes everything below |
| Does this level still apply — end it, end it and set a new one, or leave it? | `ended_date`, `ended_reason` |
| **Why did it stop applying?** — his words | `ended_reason` |
| When did it actually stop applying? | `end_level(on=…)` |
| What happens to the bet behind it? | `outcome`, or a new claim, or nothing |

**The exit date is the day it really stopped applying** — the day the stop
filled, not the day you are writing it up. `end_level(..., on="YYYY-MM-DD")`
takes it. Getting this wrong makes the record claim a live order existed for
days after it was gone.

**`why_not` only when the bet planned a trade.** It is for a plan that did not
happen. A `hold` or `watch` bet stated no trade, so there is nothing that
failed to happen — recording one would invent a miss. If the level said trim
and the bet said hold, that mismatch is the finding; record it in
`ended_reason`, not as a `why_not`.

**If he looked and did nothing, that is an answer.** Record it in
`data/commentary.csv` — `commentary.add(kind="thought",
target_type="instrument", target_id=..., body=...)` — dated, in his words,
carrying no bet. Never leave a breach unrecorded because "nothing happened".

⚠️ **Not a `kind="update"` claim row.** That vocabulary was retired on
2026-09-27 and CLAUDE.md forbids a third one. The write would still succeed —
`update` is still in `ops/vocab/kind` so the two historical rows load — and
`claims.csv` is append-only, so a wrong row there could never be removed, only
explained by another row. It would also be invisible to `commentary.about()`,
so `/show TICKER` would print it as a bet-shaped row with no bet.

## Step 4 — preview, then write in this order

Show every row in full, mark which words are his and which are yours, and wait
for a yes. Then write **in this order, and the order matters**:

1. **Link the fill** to the bet it executed — facts before verdicts
2. **The new level, if there is one** — *before* the old one is ended, so the
   position is never left unwatched for even one row
3. **End the old level** — `on=` the real date, his reason
4. **The verdict, last** — `resolve_claim()` can never be amended, so it goes
   in only once everything reversible has already landed

If the position closed completely, end every other active level on that
instrument too, and ask before moving the note — one note can cover several
lots, and `Removed:` does not reliably mean closed. When he confirms, the note
goes to **`notes/positions/closed/`** (not `_archive/`) and the instrument to
`instruments.set_status(id, "closed")`, which refuses while any lot still
holds shares.

## Finish

Say what landed — the ids, the rows as filed — and which queues this cleared.
Then **stop. Do not commit.** He runs [`/review`](../review/SKILL.md) first.
