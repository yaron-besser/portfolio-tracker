---
name: note
description: Capture anything Yaron wants to record — a thought, something someone told him, something he is watching, a plan to buy, a plan to sell or trim, or a full thesis. It can be about a holding, one of his themes, a sector, or the whole market. Asks only the questions that shape needs, previews everything, and writes only after he confirms. Use when he types /note, says "I think...", "I noticed...", "Newton said...", "Tom Lee thinks...", "a friend told me...", "my view on metals is", "something about the market", "I want to add an update on X", "let's write up X", "why do I own X", "I'm watching X", "I plan to buy X", "I want to sell X if", or when a position has no record.
---

# /note — one way in

**You ask; he answers; you file it.** He cannot be expected to remember what a
complete record needs — that is your job, not his.

`/note TICKER` writes. [`/show TICKER`](../show/SKILL.md) reads. If he only
wants to see what is there, use that instead.

## 🚨 The rules that outrank everything else here

**Never invent anything.** Not the bet, not the bear case, not a catalyst, not
a conviction, not a price target, not a date. This is real money and a
plausible sentence he did not say is worse than a blank. If he cannot answer,
write `TODO` and move on. **`TODO` is the only acceptable fallback.**

You may state **facts from his own files** — size, cost, dates, which levels
are set, what he said before. You may not state opinions, and you may not turn
a short answer into three polished sentences he did not write. File his words.

**Do not quote caps, targets or sizing rules at him. There are none.**
`portfolio_thesis.md` was archived 2026-08-14 and the last inherited numbers
were deleted 2026-08-15. If a sizing question comes up, **ask him** — his
answer is raw material for the constitution he has not written yet.

## Step 1 — show him where things stand, then route

Always open with the current state. He answers better with his own numbers
visible, and he may find he already said the thing he was about to say.

**First, what is next.** If he did not name anything, run this and say what
the queue says — clusters sort before the holdings inside them, deliberately:

```
venv/bin/python -c "from src import notes; print(notes.status().head(12).to_string(index=False))"
```

**`/note` takes a ticker OR a cluster name.** `holding.what_is()` routes.
Replace `TICKER` and run this — it prints everything:

```
venv/bin/python - <<'PY'
import pandas as pd
from src import commentary, evidence, holding
NAME = "TICKER"                      # a ticker OR a cluster name

kind = holding.what_is(NAME)
if kind == "cluster":
    c = holding.cluster_summary(NAME)
    print(f"{NAME} — cluster ({c['cluster']['layer']})")
    print(c["weights"].to_string(index=False))
    # Same block as /show, deliberately. CLAUDE.md promises the two commands
    # can never disagree about what the record says, and a group whose
    # children were not printed would read as `holds: (nothing)`.
    if c["children"]:
        print("clusters:", ", ".join(c["children"]))
        for kid in c["children"]:
            k = holding.cluster_summary(kid)
            w = k["weights"].iloc[0]
            print(f"   {kid:22} ${w['market_value']:>10,.2f}  "
                  f"{w['pct_of_nav']:>5}% of NAV  -> {k['note_state']}")
    if c["parent"]:
        print("inside :", c["parent"])
    print("holds:", ", ".join(c["members"]) or "(nothing)")
    # What he has SAID about the theme — not bets, so printed apart from them.
    # holding.said_lines() is shared with /show; never re-inline it.
    print("said :")
    for line in holding.said_lines(c["said"]):
        print("   ", line)
    # Research filed against the theme itself — read it before asking him why
    # the theme exists; he may have said it in a summary already.
    print("research:", holding.sources_line(c["evidence"]))
    if c["inherited"]:
        # A group's research reaches the clusters inside it as this line and
        # nothing else — never folded into `research:` above.
        print("theme research:", holding.inherited_line(c["inherited"]))
    print("note :", c["note_ref"] or "(none)", "->", c["note_state"])
    print("gaps :", c["gaps"] or "(none)")
elif kind == "instrument":
    s = holding.summary(NAME)
    i = s["instrument"]
    print(f"{NAME} — {i['name']}  |  cluster: {s['cluster']}")
    print(s["lots"][["ticker","shares","cost_basis","last_price","market_value","priced_at"]]
          .to_string(index=False))
    print(s["price_warning"] or "prices are current")
    # Thoughts and commentary first: they carry no conviction and no check
    # date, so running them together with the bets below makes a passing
    # remark read as a position.
    print("said :")
    for line in holding.said_lines(s["said"]):
        print("   ", line)
    for c in s["claims"]:
        # holding.claim_lines() guards every optional cell: a watch row has no
        # conviction or price and an update row no wrong_if, and this used to
        # print them as "nan" and "$nan". Shared with /note; never re-inline.
        print()
        for line in holding.claim_lines(c):
            print(line)
    if len(s["levels"]):
        print("\nwatching:")
        print(s["levels"][["id","level","trigger_when","kind","action","ended_date"]]
              .to_string(index=False))
    if len(s["trades"]):
        print("\ntrades:")
        print(s["trades"][["trade_date","buy_sell","quantity","price","currency"]]
              .to_string(index=False))
    if len(s["evidence"]):
        # Filed on the company, with no bet attached.
        print("\nabout the company:", holding.sources_line(s["evidence"]))
    if s["inherited"]:
        # The pointer, not the pile: a count of what sits above it, never the
        # documents. holding.inherited_line() says why it is keyed the way it is.
        print("\ntheme research:", holding.inherited_line(s["inherited"]))
    print("\nnote :", s["note_ref"] or "(none)", "->", s["note_state"])
    print("gaps :", s["gaps"] or "(none — the record is complete)")
elif kind == "sector":
    # A thought can be filed on a sector, so it must be readable back from one.
    print(f"{NAME} — sector")
    print("said :")
    for line in holding.said_lines(commentary.about(NAME, scope="sector")):
        print("   ", line)
    print("research:", holding.sources_line(evidence.for_target("sector", NAME)))
elif kind == "market":
    # The whole market belongs to no holding, so this is the ONLY place it
    # can be read back.
    print("the market as a whole")
    print("said :")
    for line in holding.said_lines(commentary.on_market()):
        print("   ", line)
else:
    print(f"{NAME} is not a known ticker, cluster, sector or 'market'.")
PY
```

The block above is the same one [`/show`](../show/SKILL.md) runs, and the
formatting inside it comes from `holding.sources_line()` and
`holding.inherited_line()` — **call them, never re-inline them here**, or the two
commands start disagreeing about what the record says.

Report in one line each: what he holds and its size, the last thing he said
about it, any active level, **any trade since his last claim**, and the price's
age (`price_warning`) — a claim's `price` is permanent, so a stale one is
permanent too. Then say what is missing.

**If he names a holding whose cluster has no note yet, say so and offer the
cluster first.** Do not refuse — it is his call — but do not let him write
"why I own GDX" three times when it is really "why I own metals".

**Almost nothing needs asking.** Held or not, the price, the size, open plans,
active levels and recent trades are all derived. What is missing is only what
he *means*.

### One door in, and a bet is an upgrade

🚨 **Never ask him "is this a bet, or a thought?"** That asks him to introspect,
and his own record shows the line is not stable — on 2026-09-27 he said plainly
he did not understand the old `claim`/`watch`/`update` split. The old table
here made it worse: it offered *"current thinking on a holding"*, which filed
as a **bet**, and a bet demands a check date, so a plain thought was refused
with `check_date is required on every claim row`.

**Everything he says is recorded first.** Then one optional question turns it
into a bet as well. There is no wrong door because there is only one.

**Q1 — whose words are these?** A fact, not a judgement.

| He says | `kind` | then |
|---|---|---|
| something he thinks | `thought` | `origin=mine`, `said_by` stays blank |
| something someone said | `commentary` | **who?** → `said_by` free text, and `origin` is their firm |

Mark Newton and Tom Lee are both `origin=fundstrat` — `said_by` is the only
thing that tells them apart, so **always ask the name**. If he cannot remember,
leave it blank and move on; `commentary.unattributed()` reports it and nothing
blocks.

**Q2 — what is it about?** 🚨 **Show him names, never field values.** He is
picking a thing he recognises, not a column. Fill the names in from his own
data — `holding.what_is()` for what he typed, `instruments.get()` for its
cluster and sector, `claims` for a recent bet:

> *"What's this about?"*
> → **GDX itself**
> → **Metals** — the whole theme it sits in
> → **Materials - Gold Miners** — its sector
> → **the whole market**
> → **something you already said** — c0023, 28 Aug, "DBA on the Hormuz closure"

What those become, which he never sees: the holding → `instrument`, the theme →
`cluster`, the sector → `sector`, the market → `market` **with a blank
`target_id`**, an earlier bet → `claim` with its id.

⚠️ **Seven names are both a theme and a sector** — Technology, Healthcare,
Financials, Energy, Utilities, Consumer Staples, Real Estate.
`commentary.is_ambiguous(name)` says so. When one comes up, offer **both lines**
and let him pick, worded as *"Technology, the theme you chose"* and
*"Technology, the 36% of the book you're exposed to"*. Never pick for him.

**Q3 — the only bet question. Ask it ONLY when Q2 was the holding itself.**

🚨 **A bet still needs one instrument.** `instruments.check_known(["Metals"])`
raises `Unknown instrument(s) ['Metals']`, and so do a sector and `market`. So
for a theme, a sector, the market or a correction, **do not offer a date** —
the write would raise *after* the comment landed, and after promising him
something the record cannot hold. Say so plainly instead:

> *"Recorded. I can't put a check date on a theme yet — if you want one, name
> the holding you'd actually act on and I'll write the bet against that."*

On a holding, ask it last and only once:

> *"Want me to ask you about this later?"*
> → **No, it's just what I think** — done. One row, nothing else.
> → **Yes, ask me on ⟨date⟩** — then, and only then, ask `wrong_if`, `price`,
>   `conviction` **and `intent`**, write the claim, and `commentary.promote()`.

**Then one more, only when a bet is being written:**

> *"Is this part of a call you already made?"*
> → **the technology-sep-26 call** — QQQ, MAGS, XLK
> → **its own call** — leave it blank
> → **a new call** — name it, and say which other bets will share it

🚨 **Offer the labels already in use; never ask him to type one.** Get them from
`claims.decisions()`, which lists each call with its bets. A typed label that
differs by a word splits the call in two and nothing says so — the store
normalises case and spacing, not vocabulary.

**Why this exists.** On 15 September one decision — *technology is breaking
out* — became `c0029` (QQQ), `c0030` (MAGS) and `c0031` (XLK) with identical
text. His call was to keep three rows: they are different sizes and will have
different outcomes, and **the verdict belongs to the instrument.** The label is
what makes *"how did the technology call do?"* answerable without pretending
they were one trade. `scoreboard()` shows both — the call rolled up, and each
bet on its own.

**A bet that stands alone gets no label.** Most do. Do not invent a call for one
trade.

⚠️ **`intent` is not optional here.** It is nullable in the schema, so the row
writes — and a blank `intent` is not `hold`, so `follow_through()` reports the
bet as **a plan he never executed** (`claims.py`, `nothing_to_do`). A view on
something he already owns is `intent="hold"`; anything else, ask which action
it implies.

**If he says "I did it"** — link the trade; nothing new is written.

**If what he said contains both a fact and a plan, that is two records, not
one.** "Fundstrat said X so I'll add" = a `commentary` row for what Newton said
**and** a claim for what *he* decided. Merging them is what would make
*"does reading Fundstrat improve my decisions?"* permanently unanswerable.

⚠️ **`kind=update` is retired.** The vocabulary keeps the value so his two
existing rows still load, but never write another: a correction is a thought
about an earlier bet, so it is `target_type=claim`.

**Offer the trade link unprompted.** If the ticker has an open plan and a fill
since it was written, say so: *"you planned to add on 9 June and there is a
fill on 11 August — link them?"* Nothing else in the system prompts this, and
`follow_through()` is empty without it.

### For a CLUSTER, the questions are different

Two, and only two: **why does this theme exist as its own slot**, and **what
would make him close the whole thing** rather than trim one holding. Use
`notes/templates/cluster_thesis.md`. Required headings, enforced by
`src/notes.py` and the commit hook: `## Why this cluster exists` and
`## What would end it`.

⚠️ **Never write a weight into a cluster note.** `crypto.md` recorded
"8.1% ($9,240)" once and it was $4,100 by the time anyone read it again.
Show him `cluster_summary()["weights"]` on screen; never file it.

A cluster gets a note, and now also a thought — no claim row, no level. A
cluster is not an instrument and `claims.csv` has no row shape for it, which is
why `data/commentary.csv` exists: `commentary.about("Metals")` is where a view
about the theme lives.

**A group is a cluster too, and it is written first.** `cluster_summary()`
returns `children` for a group and `parent` for a cluster inside one, and
`notes.status()` lists the group above them. Ask a group the wide version —
why he owns technology at all, what would make him leave the whole thing — and
leave the narrow version to the clusters inside it. **Never ask the same
question twice at two altitudes**; sparing him that is what the group is for.
A group's money is the sum of its children, so it holds no instruments of its
own — that is correct, not a gap.

## Step 2 — ask in choices, not essays

**Ask questions he can answer by picking, not by writing.** Use
`AskUserQuestion` with real options. An open "why do you own this?" is how the
work stalls.

The *shape* is yours to offer; the *substance* is his. Never write a reason he
did not pick or type.

**Where options come from, in order:**

1. **His own existing text** — offer his sentences back verbatim: keep / change.
2. **His own data** — the level he already set, the cost basis, a support level
   in his chart note. Say where each came from. ⚠️ Those are all *prices*; for
   `wrong_if`, offer a macro or fundamental condition alongside them.
3. **His own past decisions** — what he said about a sibling holding, the
   reason he gave for the cluster. ⚠️ **Not the archived constitution.**
4. **A neutral taxonomy**, only when 1–3 give nothing. The category is not the
   thesis.

**Always categorical, always choices:** `conviction`, `check_date`, `origin`,
`intent`, `setup` (several is normal), `why_not`, `trigger_when`, level
`kind` and `action`. `origin` and `kind` are **never guessed** — if he says
"13D said something about bitcoin", ask which.

⚠️ **A level `action` is not an `intent`.** They overlap in the one place it
matters: `trim` is a legal *intent* and is **not** a legal action. The five
actions are **`sell_all` · `trim_half` · `re_evaluate` · `add` · `buy`** —
trimming half is `trim_half`. Passing `trim` raises at the last step of the
write, after the claim and every evidence row have already landed.

⚠️ **A `hard` level needs one more question.** `hard` asserts a real order
exists at IBKR, so the store refuses the row unless it also says when that was
last true: ask **when did you last see that order at IBKR?** and pass it as
`broker_verified="YYYY-MM-DD"`. Never guess it and never stamp today — the
field exists to record when he actually looked. On a `soft` row it must stay
blank.

**Only ever free text:** the bear case, `wrong_if`, and the wording of the bet.

### A `wrong_if` must be judgeable, not numeric

His correction, 2026-08-15:

> "Sometimes it's about price and sometimes it's about fundamentals or macro
> stuff as the main reason for failure. Yes I am trading according to price
> action, but not the price action itself must be the catalyst for the
> decision. Sometimes it could be a pure price-action technical decision and
> sometimes the price action is just a framework for something bigger."

All three are good answers — a price, a macro condition, a fundamental one.
The test is: *in six months, can he say whether this happened?* "It goes down"
fails. "The Fed stops cutting" passes. **Do not push him toward a price.** If
the invalidation *is* a price, it is also a `levels.csv` row — with an action
attached, because a level is something to do, not a verdict.

### For an ETF holding

Use `notes/templates/etf_position.md`. Ask what exposure it gives him, why now,
what would kill *the exposure*, and why this wrapper over a peer. Keep the bear
case short — a bear case on 2,000 names is filler.

⚠️ **`## Bear case` and `## Catalysts` are still required**, ETF or not.
`src/notes.py` enforces them on every file in `notes/positions/` and the commit
hook refuses without them. (`docs/journal-workflows.md` used to say an index
needs no bear case; that was wrong and is corrected.)

### What each shape needs

- **a thought or commentary** — `date`, `kind`, `target_type`, `target_id`,
  `origin`, `body`. Nothing else is required. It cannot carry `wrong_if`,
  `price`, `conviction` or `check_date`, because it is not a bet.
- **`watch`** — the text and usually a level. No price or conviction required.
- **`claim`** — `wrong_if`, `check_date`, `price`, `conviction`, `intent`, all
  required. Optionally `atr` and `sma_200` off his chart, and the setups.

**If he says "I don't know" to the bet or to what would change his mind, stop
and say so plainly:** a position with no bet and no invalidation is not a
thesis, and writing one anyway would be inventing it. Offer to record what he
*does* know as **a thought** instead, and leave the claim for when he has one.
That is not a failure — it is the two-tier design working, and it is now the
normal path rather than a consolation prize.

**If he does not state an `intent`, it counts as a plan** — the safe direction.
A forgotten label must never quietly improve his follow-through numbers.

**Do not write position size, cost or cluster into the note.** They are in
`positions.csv` and `instruments.csv`; a copy goes stale, which is exactly how
`crypto.md` came to claim $9,240 against a real $4,100.

## Step 3 — nothing is written until he confirms the whole thing

**His rule, 2026-08-15:**

> "During the interview, asking me everything — do you have screenshots, what
> is the conviction level and so on. Before you're writing everything, you're
> summarising everything and ask me: do you confirm? And only if I'm
> confirming, you add everything in one shot."

**Hold every answer in memory. Write nothing during the interview.** When IWM
was written on 2026-08-15 its claim, level, setups, ATR and note went in as
five separate steps — a "no" halfway would have left a half-built record with
nothing marking it as partial.

At the end, print **everything that will be created**, in full:

```
data/commentary.csv         NEW  cm0003   your own thought, about Metals
                                          "Metals feel early to me."
data/claims.csv             NEW  c0005    every field, labelled
data/claim_setups.csv       NEW  ×2       c0005 -> trendline, macro
data/levels.csv             NEW  l0002    IWM $198 at_or_below soft re_evaluate -> c0005
notes/positions/X.md        NEW           the complete text, every line
notes/screenshots/          FILED         2026-08-15-X-price-1d.png  (from _inbox)
data/evidence.csv           NEW  e0005    chart · "X daily, 15 Aug" · mine · repo
                                          -> notes/screenshots/2026-08-15-X-price-1d.png
data/evidence.csv           REUSED e0002  research · "Fundstrat AI, Aug" · private
notes/picks/2026-08-17-fundstrat-ai.md    NEW  the complete text, every line
                                          "What it says" is mine, every line page-cited
                                          "What it means for me" stays TODO — yours
data/evidence_summaries.csv NEW  s0001    e0002 -> that file
data/evidence_links.csv     NEW           claim   c0005            <- e0005  p.12
                                          "the trendline"
data/evidence_links.csv     NEW           cluster AI Infrastructure <- s0001  p.4
                                          "the theme, not the stock"
data/commentary.csv         LINKED   what you said on 27 Sep is now also a bet
                                          you'll be asked about on 2026-12-27
                                          (cm0003 -> c0005)
data/claims.csv             LINKED        c0001 <- trade 1000000001

confirm?
```

**The evidence rows belong in the preview like everything else.** The document
row, the summary, its `page` and its `why`, and **what each link is attached to**
are all things he is agreeing to; a preview that skips them is asking him to
confirm a record he has not seen. If the document already exists, show it as
`REUSED e0002` rather than `NEW` — filing one report twice is refused by the
store and by the commit guard.

**Print a summary's text in full, and say which words are yours.** It is prose
that will sit in `notes/picks/` under his name, so it needs the same yes as a
note does — and the `TODO` under "What it means for me" is deliberate, not an
omission he should ask you to fill.

**Mark which words are his and which are yours.** He is confirming the
artefact and cannot confirm what he cannot tell apart. **Do not write on
"sounds good" alone if any part of the preview was your paraphrase — say which
part is yours and get that part confirmed specifically.** If he changes one
thing, **reprint the whole summary** — not the diff.

If this replaces an existing note, show **what will be removed** and say that
git keeps the old version.

Then, and only then, write — **all of it, in one go.**

⚠️ **The write is not atomic and must not pretend to be.** Validate everything
first, then write in this order. **The order is not a preference — get it wrong
and it raises.** `add_claim()` calls `check_refs()`, which refuses a `note_ref`
that does not yet exist, and `add_evidence(storage="repo")` refuses a chart
that is not filed yet:

```
ValueError: note_ref path 'notes/positions/IWM.md' does not point to a file
            that exists.
```

**1. the note · 2. the instrument row, if the ticker is new · 3. the screenshot
 file and the summary write-up · 4. the thought or commentary · 5. the claim, if
 he asked for a date · 6. `promote()` · 7. the setups · 8. the documents, the
 summary rows and the links · 9. the level**

The comment goes in **before** the claim, so that if he says yes to the bet and
something later raises, what he *said* is already on the record — that is job
one, and it must not be lost defending a bet that failed to write.

`promote()` goes straight after the claim, **not at the end.** It needs only the
two ids, and the level at step 9 is the step most likely to raise — a comment
left with a blank `promoted_to` makes `/show` print the same idea twice, once as
a remark and once as the bet it became.

The note must be on disk before the claim can point at it, the chart before
`add_evidence()` records it, and the write-up before `add_summary()` records it —
each of those refuses a file that is not there yet. The setups, the links and the
level all need the claim's id, so they come after it. If a write fails partway,
say exactly what landed and what did not — a silent partial write is worse than
a loud failure.

⚠️ **The instrument row is step 2, not an afterthought.** `add_claim()`,
`add_level()` and an `instrument` evidence link all call
`instruments.check_known()` and refuse an id that does not exist. Written last,
a brand-new ticker dies at the claim — with the note file already created and
the screenshot already renamed out of `_inbox/`. That is the exact case `/note`
exists for: a first buy, a plan to buy something not held, anything
`holding.unexplained()` names.

```python
from src import claims, commentary, evidence, instruments, levels, notes

# 1. the note exists FIRST — every writer below refuses a note_ref that is not
#    yet a file (notes.stub() only if he is starting from nothing)

# 2. the instrument row, if the ticker is new. BEFORE the claim, not after:
#    add_claim(), add_level() and an instrument link all refuse an unknown id.
#    Passing note_ref here also does the back-link in the same call.
instruments.add_instrument("<TICKER>", name=…, instrument_type=…,
                           cluster_id=…, note_ref="notes/positions/<TICKER>.md")

# 3. the chart and any summary write-up are on disk before the rows name them

# 4. WHAT HE SAID — always, and before the claim. If step 5 raises, his words
#    are already on the record; job one must not be lost defending a bet that
#    failed to write. Keyword arguments only: the first positional is `path`.
cm = commentary.add(date="2026-09-27",
                    kind="thought",             # or "commentary"
                    target_type="cluster",      # instrument|cluster|sector|
                                                # market|claim
                    target_id="Metals",         # "" ONLY when market
                    origin="mine",              # someone else's -> their source
                    said_by="",                 # the person, if not his own
                    body="his words, verbatim")

# 5. the bet — ONLY if he said yes to a check date, and ONLY on a holding.
#    intent is not optional: blank is scored as a plan he never executed.
cid = claims.add_claim(date=…, ticker=…, origin=…, kind=…, intent=…,
                       claim=…, wrong_if=…, check_date=…, price=…,
                       conviction=…, atr=…, sma_200=…,
                       said_by=…,              # blank when origin is "mine"
                       decision=…,             # the call, if he picked one in Q4.
                                               # add_claim() is the ONLY thing that
                                               # normalises the label — a hand-edit
                                               # skips it and splits the call.
                       note_ref="notes/positions/<TICKER>.md")
claims.add_setups(cid, ["trendline", "macro"])

# 6. link the thought to the bet, straight after the claim — NOT at the very
#    end. The level below is the step most likely to raise, and a comment left
#    with a blank promoted_to makes /show print the same idea twice.
commentary.promote(cm, cid)

# 7. the document, then any summary of it, then the links. The document is
#    filed ONCE and may already exist — check first, or one report becomes two
#    rows. link() takes four positional arguments: what it is about, then what
#    it is.
eid = evidence.add_evidence(kind="chart", title="QQQ daily, 16 Aug",
                            source="mine", published="2026-08-16",
                            storage="repo", location="notes/screenshots/….png")
evidence.link("claim", cid, "document", eid, page=None, why="the trendline")

rid = evidence.add_evidence(kind="research", title="Fundstrat AI, Aug",
                            source="fundstrat", published="2026-08-01",
                            storage="private",           # the filename ONLY
                            location="2026-08-01-fundstrat-ai.pdf")
sid = evidence.add_summary(rid, "notes/picks/2026-08-17-fundstrat-ai.md")
evidence.link("cluster", "AI Infrastructure", "summary", sid, page="4",
              why="the theme, not the stock")

# 8. the level. `date` is REQUIRED and never defaults — it is the day he
#    decided, which is not always today. `recorded` fills itself.
levels.add_level(date=…, instrument_id=…, level=…, trigger_when=…, kind="soft",
                 action=…, instruction=…, claim_id=cid, note_ref=…)

# A hard level asserts a real order at IBKR, so it ALSO needs the day he last
# saw it there. Without broker_verified the row is refused.
levels.add_level(date=…, instrument_id=…, level=…, trigger_when=…, kind="hard",
                 action="sell_all", instruction=…, claim_id=cid,
                 broker_verified="2026-09-27")

claims.link_trades("c0001", "1000000001")        # if a fill matched a plan
```

**Then set the link the other way**, or the note is invisible:

A NEW ticker got its `note_ref` in step 2 above. For one that already exists
with a blank `note_ref` — and for every cluster note — set it explicitly:

```python
instruments.set_note_ref("<TICKER>", "notes/positions/<TICKER>.md")
clusters.set_note_ref("Metals", "notes/clusters/metals.md")
```

`notes.status()` and `holding.cluster_summary()` both read note state from THAT
column, so a note written without it reports as "missing" for ever.

`nav_pct` is computed from `positions.csv` — never typed. A new instrument needs
only `instruments.add_instrument(id, name=…, instrument_type=…)`; never let
missing metadata stop a record landing.

## Evidence — charts, research, articles

**One row per document, attached to everything it bears on.** A Fundstrat report
covering a dozen stocks is filed **once** and attached many times; never file it
again per claim. `evidence.load_evidence()` first — the document may already
be there.

**Attach it to what it is actually about**, and ask him if it is not obvious. A
chart of one stock behind one decision is a `claim`. A broad AI report is a
`cluster` (or a `sector`, or an `instrument` if it is about the company with no
decision attached) — that is what stops twelve links each claiming the report is
about one stock. ⚠️ **Only a `claim` link counts as evidence for that claim.** A
report filed on the cluster does not fill the claim's gap, and never say it does:
`evidence.unevidenced_claims()` still names the claim, and `/show` shows the
report as a count of what sits above the holding.

**A long report gets one summary per theme, never one summary of the report.**
He drops the PDF into `notes/research/_inbox/` under any name and **you**
rename it into `notes/research/` as `YYYY-MM-DD-source-topic.pdf` (gitignored,
lowercase) — never ask him to name it first; you file the document row, write one write-up per
theme in `notes/picks/` from `notes/templates/research_summary.md`, record each
with `add_summary()`, and attach each to the theme it is about.

🚨 **You may write "What it says". You may never write "What it means for me".**
Every line under "What it says" carries a page number, so he can check any of it
against the original in seconds — that is the only reason a machine is allowed to
write prose into `notes/` at all (P1, narrowed 2026-08-17). "What it means for me"
stays `TODO` until he fills it in himself. **No code enforces this. It rests on
you**, and it is the same rule as never inventing a bear case.

Filed charts live in `notes/screenshots/` as
`YYYY-MM-DD-TICKER-subject[-1d|-1w|-1m].png`, subject from
`ops/vocab/chart_subjects`, ≤500 KB — the commit guard enforces all three. He
drops any file into `notes/screenshots/_inbox/`; you rename, downscale if
needed, file it, then record it and attach it.

| What it is | `storage` | `location` |
|---|---|---|
| a chart or a `picks/` write-up you committed | `repo` | the repo-relative path |
| a web page | `link` | the URL |
| a paid research PDF | `private` | **the filename only** — it lives in the gitignored `notes/research/`, and a path would put it in a committed table |

`page` and `why` go on the **link**, not the document: the same report backs
one claim on p.12 and another on p.31. A summary keeps no title of its own — it
borrows the document's, so renaming the report renames its summaries too.

It is never blocking — a claim with no document is reported by
`evidence.unevidenced_claims()`, not refused. `evidence.unlinked()` is the
other side: research filed and never acted on.

## Updating something already written

**Never edit a claim.** History is immutable; only `outcome`, `resolved_date`,
`resolution_note` and `why_not` are filled in later.

- Something happened, or a source said something → **a row in
  `data/commentary.csv`**: `kind=commentary` when someone said it (and **ask
  who** — `said_by`), `kind=thought` when it is his own.
- He changed his mind → **two acts**: `resolve_claim()` the old one with his
  verdict in his words, then `add_claim()` the new bet.
- A plan did not happen → `record_why_not()`. Once, then permanent.
- A correction to an old row's *text* → a thought filed against that bet:
  `kind=thought, target_type="claim", target_id="c0001"`. c0004 corrected
  c0001's currency the old way, as a `kind=update` row; those two rows stay.
  ⚠️ **Nothing reads claim-scoped rows back yet** — say so when writing one.

## Positions with no record

`holding.unexplained()` lists anything traded in the last 7 days that no claim
explains — the sync and every session start now report it. When he runs `/note`
on one of those, `notes.stub()` may create the empty note if none exists:
title, the two required headings, `TODO` under each, **and nothing else**
(P1, narrowed 2026-08-15). Anything beyond `TODO` is authorship and forbidden.

## Finish

Show what landed: the ids, the paths, the rows as filed. Then **stop.**

**Do not commit.** He runs `/review` first, then a branch and a PR he merges.
