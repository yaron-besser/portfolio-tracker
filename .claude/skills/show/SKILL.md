---
name: show
description: Read back everything the record holds about one holding — what he owns, what he said and when, what he is watching, what he actually did, and what is missing. Use when he types /show TICKER, asks "what do I know about X", "what did I say about X", "show me X", "have I written anything on X", or before writing something new so he can see whether he already said it.
---

# /show — read the record back

**This skill writes nothing. Ever.** It is the read half of the pair; `/note`
is the write half. If he wants to add something, say so and stop — do not
start capturing here.

`/show` takes **a ticker or a cluster name** — `holding.what_is()` routes.
Replace `TICKER` and run this; it prints everything:

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
    # A group's money is the sum of its children, so showing the total
    # without the parts hides where it came from. A child names its group,
    # because "why do I own tech at all" is answered one level up.
    if c["children"]:
        # "clusters", not "groups" — these ARE the clusters inside the group,
        # and groups are one level deep, so none of them is one.
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
    # holding.said_lines() is shared with /note; never re-inline it.
    print("said :")
    for line in holding.said_lines(c["said"]):
        print("   ", line)
    # Research filed against the theme itself, not against one stock in it.
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
        # Comments filed against THIS bet, as opposed to against the holding.
        # Until 2026-10-01 these were writable and unreadable — /note points
        # here for correcting a bet that can no longer be edited, so without
        # this the instruction led nowhere.
        on_bet = s["said_on_claims"]
        mine_c = on_bet[on_bet["target_id"] == c["id"]] if len(on_bet) else on_bet
        if len(mine_c):                      # silent when there is nothing —
            for line in holding.said_lines(mine_c):   # otherwise every bet
                print("     ↳", line)                 # grows a "nothing said"
    if len(s["levels"]):
        print("\nwatching:")
        print(s["levels"][["id","level","trigger_when","kind","action","ended_date"]]
              .to_string(index=False))
    if len(s["trades"]):
        print("\ntrades:")
        print(s["trades"][["trade_date","buy_sell","quantity","price","currency"]]
              .to_string(index=False))
    if len(s["evidence"]):
        # Filed on the company, with no bet attached — read on ASML is not the
        # same thing as a reason he bought it.
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
    # ⚠️ TWO WORDS, deliberately: commentary calls this `market`, evidence
    # calls it `macro` (evidence.MACRO_TARGET), and Yaron chose to keep them
    # apart on 2026-10-11. Reading only one leaves the other invisible — the
    # FT leverage piece was filed under `macro` and this branch could not see
    # it for an hour. Both must be printed here or neither is findable.
    print("research:", holding.sources_line(
        evidence.for_target(evidence.MACRO_TARGET, evidence.MACRO_TARGET)))
else:
    print(f"{NAME} is not a known ticker, cluster, sector or 'market'.")
PY
```

Everything comes from `holding.summary()` / `holding.cluster_summary()`. Do not
assemble it by hand from the CSVs — the whole reason those exist is so `/show`
and `/note` can never disagree about what the record says. The two lines that
need formatting come from `holding.sources_line()` and
`holding.inherited_line()`, for the same reason: **call them, never re-inline
them here.** They were copied into both skills once and nothing could tell when
one copy changed.

## What to print, in this order

**1. What he owns.** Shares, market value, % of NAV, cost vs price, P&L in
money and percent, the cluster. Say the price's age out loud —
`summary()["price_warning"]` is there for exactly that, and a stale price
presented as current is the failure P2 exists to stop.

**2. What he thought, newest first** — `summary()["said"]` /
`cluster_summary()["said"]`. Thoughts and commentary are **not** bets: no
conviction, no check date, nothing to score. Print them apart from the claims
below, or the two run together and a passing remark reads as a position.

What `holding.said_lines()` actually prints — one line per row, ending with the
source in brackets and the scope in parentheses:

```
2026-09-27  cm0004  thought    me             "Metals feel early to me."  (cluster: Metals)
2026-09-26  cm0006  commentary Mark Newton    "Metals are early in the cycle."  [fundstrat]  (cluster: Metals)
2026-09-20  cm0007  commentary (nobody named) "Gold leads here."  [13d]  (cluster: Metals)
2026-09-18  cm0002  thought    me             "..."  (market: the whole market)  -> became c0031
```

Paste that shape, not a prettier one — a sample that disagrees with the code is
how the next session "fixes" the formatter to match the doc.

**Always print `said_by`.** Mark Newton and Tom Lee are both
`origin=fundstrat`, and the name is the only thing separating them. A blank one
is honest — he may not remember — so print `(nobody named)` rather than
guessing.

**Say when a thought became a bet.** `promoted_to` is the link; without it the
same idea reads twice, once as a remark and once as a claim.

**A thought about the whole market is not on any holding.** It lives under
`/show market`, which is the only place to find it — and that branch must read
**both words**: `commentary.on_market()` for what he thinks (`market`) and
`evidence.for_target("macro", "macro")` for what he read (`macro`). The two
names were kept apart by his own choice on 2026-10-11, so reading one and
reporting "nothing on file" is a lie the record cannot correct.

**3. What he said as a BET, oldest first.** Every claim as a dated line:

```
2026-08-15  c0003  claim · hold · fundstrat · high conviction · $214.60 · 4.20% of NAV
            "Falling inflation, which leads to lower interest rates."
            wrong if: weekly close below $198 — soft, re-evaluate
            because: macro, research-call, trendline
            next look: 2026-09-25   evidence: 2026-08-15-IWM-price-1d
```

**Evidence is a document title, not a path, and it may carry a page.** A
Fundstrat report backing two claims at two pages prints as
`evidence: Fundstrat Core Large Cap, May p.12` on one and `p.31` on the other —
same document, filed once. `evidence.for_target()` reads the join; the page and
the one-line `why` live on the link, never on the document. A **summary** — a
page-cited write-up of one theme in a long report, in `notes/picks/` — prints
the report's own title, marked `(summary)`.

**A document can also be about the company or the theme rather than a bet, and
those print separately.** A holding shows what is filed on the company itself,
then **one line counting what sits above it** — its cluster, that cluster's
group, its sector and that sector's parent sector: *"2 more on the AI
Infrastructure cluster — ask to see them"*. Say which kind each one is; a
cluster and a sector can share a name, and so can a sector and its parent.
A cluster prints its own research the same way, under `research:`, and a cluster
inside a group gets the same pointer line for the group's — so a report filed on
Technology is findable from `/show Software`.

⚠️ **That count is a pointer, never a merge, and it never fills a gap.** A claim
whose only backing is a theme-wide report still reads as unevidenced, because a
report about technology is not research on ASML — treating it as one would let a
single document cover twelve bets nobody examined. Print the documents
themselves only if he asks.

An `update` row is one line — it carries no bet, and dressing it up as one
would blur the distinction the store enforces. A resolved claim shows its
verdict and the resolution note. An unfulfilled plan shows its `why_not`, and
**say plainly that `still-waiting` is not a miss** — it is patience, and the
column exists precisely so the two are not confused.

**The call, when several bets share one.** `holding.claim_lines()` prints it on
each bet. Add the roll-up beside it, from `claims.decisions()`:

```
technology-sep-26   3 bets · MAGS, QQQ, XLK · 15 Sep to 22 Sep
```

Say it plainly: *"three bets came from that one call, and each gets its own
verdict."* Never imply they were one trade — he chose three rows precisely
because QQQ, MAGS and XLK are not the same bet.

**4. What he is watching.** Active levels first, then ended ones with the
reason they ended. For each: how far away it is now, and in ATRs if the claim
recorded one — "$198 is 16.60 below, about 4 ATRs" tells him more than the
number alone. ⚠️ His charts read **ATR(90)**, not the usual 14.

**5. What he actually did.** Every trade, with the date, side, quantity, price
and currency. If a claim has `trade_ids`, say which trade executed which plan
and how many days later. If a plan has no trade, say so without a verdict —
`happened` is an observation, never a score.

### Deleting a comment

A comment is disposable — that is the whole reason it does not live in
`claims.csv`, which physically refuses to lose a row. If he asks to remove one:

```
venv/bin/python -c "
from src import commentary
print(commentary.about('Metals')[['id','date','kind','said_by','body']].to_string(index=False))"
```

**Preview the exact row and get a yes before deleting it**, the same rule every
other write follows:

> *"Remove this? cm0007 · 20 Sep · about Metals · 13D · 'Gold leads here.'
> It stops appearing anywhere. Git keeps the old copy, so it can be recovered,
> but nothing in the app brings it back."*

Then `commentary.forget("cm0007")`. ⚠️ **Never delete a bet this way** — there
is no equivalent for `claims.csv` and there must not be.

⚠️ **An id can be reissued after a deletion.** `cm0007` is the highest number
plus one, so deleting the newest row frees its number for the next comment.
Nothing in the database points at a comment, so nothing dangles — but an old
printout quoting `cm0007` may name a different thought. Say the date and the
first words, not just the id, whenever you quote one back to him.

**6. What is missing.** `summary()["gaps"]` — print it last and print it
plainly. A gap he cannot see is a gap he will not fill. If there are none,
say the record is complete for that holding rather than staying silent.

## How to write it

He is not a developer. Short sentences, plain words, effect not mechanism.
Never print a raw DataFrame — turn it into lines a person reads.

Dates as they are stored (ISO), money with a `$` and thousands separators,
percentages to two places. Never round a price he might act on.

## Two things not to do

- **Do not interpret.** The record says what it says. "You were right about
  this" is his judgement, not yours — resolving a claim is `resolve_claim()`
  and it is his call (P1).
- **Do not offer to fix anything.** If the record has gaps, name them and stop.
  He decides whether to run `/note`.
