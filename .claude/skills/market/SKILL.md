---
name: market
description: Refresh what the market says about his ETFs and stocks — sector breakdowns, company-by-company holdings, and a vendor sector per stock. Reports everything first, asks before it checks the internet, asks again before it saves anything, and afterwards names what changed, including what got worse. Use when he types /market, says "update the market data", "check for new ETF holdings", "refresh the sector breakdowns", "is the market data stale", "are my ETF holdings up to date", or when the end-of-turn hook says the market data is ageing.
---

# /market — the only thing that refreshes market data, and it never does so without being asked

**His words, 2026-10-04:** *"I don't want you to automatically update it
without asking — it's something you do monthly or quarterly."* Quarterly is
how often he chose to run this, not a deadline anything enforces. Nothing in
this repo fetches market data except this skill.

**Keep two things apart.** The portfolio page redraws itself after every IBKR
sync — that only re-draws numbers already sitting in `data/market/`, reaches
nothing, and changes nothing here. This skill is the only thing that reaches
the internet. Different things, different rules; this file governs only this
one. What this skill may touch is listed exactly in Hard rules, below.

## 🚨 The rules that outrank everything below

**Two separate yeses by default, never silently merged into one.** Checking
the internet is its own question; saving what came back is a second, later
question. Step 2 offers a third, explicit option — check and save in one trip
— but only as something he chooses knowingly, never as the default and never
without saying what that choice costs (see Step 2).

**`ops/refresh_market.py` only ever runs with `--write` right after he has
said yes to saving, in this same conversation.** Never pass `--write` because
a run looks clean, or because he said yes to checking — that yes was only for
the look, unless he explicitly chose the combined option.

**Every warning gets read out loud.** `ops/refresh_market.py` already collects
every `⚠` line the refreshers print and repeats it in its own closing summary
— a fund losing a sector it used to carry, say. Quote that whole block to him
whenever it has anything in it. Scrolling past it because the rest of the run
looked fine is the exact mistake this skill exists to stop: on 2026-10-01 a
refresh silently replaced two complete breakdowns with incomplete ones, and
the only reason anyone noticed was him knowing his own book.

**Never invent a fund's download link.** `ops/refresh_etf_holdings.py`'s
`ISSUER_DOWNLOADERS` is the list, and it is short — **SPDR and Global X**
(2026-10-06). Every other issuer's funds fall to the inbox or the floor. All
nine issuers he holds were tested on 2026-10-05: iShares answers a CSV
request with a web page labelled `text/csv`, Invesco and WisdomTree refuse
outright, and Vanguard, Amplify, VanEck, Roundhill and Fundstrat build their
holdings tables in the browser, so no file exists at an address to fetch.
Re-testing them is work already done — read the registry comment first. Point him at whichever issuer's own site
the fund actually belongs to and tell him to search the ticker there — never
a guessed deep link that might 404 and waste his time.

## 🚦 Scope — settle this BEFORE Step 1, and default to the small one

His instruction, 2026-10-06: *"most of the time it's unnecessary… if I'm
doing a full refresh it's a big session, something that happens once in a
while, so the system has to have a guard against it."*

**`/market` means: refresh only what is missing or aged.** This is the whole
scope of a normal run. It reads files already on disk and touches no network:

```
venv/bin/python - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("dg", "ops/data_gaps.py")
dg = importlib.util.module_from_spec(spec); spec.loader.exec_module(dg)
n = dg.needs_refresh()
if n.empty:
    print("Nothing is missing and nothing has aged past "
          f"{dg.STALE_AFTER_DAYS} days. There is no work to do.")
else:
    print(f"{len(n)} fund(s) worth refreshing, biggest money first:\n")
    for _, r in n.iterrows():
        print(f"  {r['ticker']:<8} ${r['market_value']:>10,.0f}   {r['why']}")
PY
```

**If it prints "nothing", say exactly that and stop.** Do not offer a full
refresh as a consolation, do not continue to Step 1, do not fetch "just to
check". A complete fund has nothing to gain from a re-fetch and something to
lose: `data/market/holdings_inbox/` is git-ignored, so the file behind a
fund may no longer be on disk, and the fetcher would fall through to Yahoo's
ten names. (`fetch_holdings()` refuses a floor thinner than what is already
stored, so this is guarded twice — but the first guard is not offering the
work at all.)

**The benchmark can appear in this list, and it is not a held fund.** SPY has no
money beside it because he does not own it — it is the thing his sector and
company panels are compared against, and `ops/data_gaps.py` names its two
tables when they are absent. Treat it like any other row he picks: scope the run
to it, take both yeses, touch nothing else.

**If it prints funds**, show that list and ask which he wants. Then run
Steps 1-6 **over only the tickers he picked**, passed as arguments —
`ops/refresh_etf_holdings.py IWM QQQ --write`. Never widen his choice,
never add "and while we were there". A fund he did not pick is not touched.

### A full refresh is a different thing, and he types the words himself

Every held fund, every stock, every vendor sector: a long session, many
network calls, and the one operation that can make the record worse.

**Only run it when he has typed `full refresh` in his own message.** Not
"refresh everything", not "do them all", not a yes to a question you asked —
those words, from him. If he says something that sounds like it, show the
cost and ask him to type it:

> A full refresh covers every held fund and every stock — far more than the
> N that need it. It can also make things worse: the files behind most of
> your funds are not in git, so any that are missing fall back to a ten-name
> floor. Type **full refresh** if that is what you want.

Then, before the first network call, print what it will touch (Step 2's
script with no ticker filter) and take the second yes exactly as Step 2
describes. Both yeses still apply — the typed words replace neither.

---

## Step 1 — report what is true today. No network call, nothing to approve.

This is read-only and needs no yes from him — it only reads files already on
disk. **If he stops here, that is the whole skill.**

```
venv/bin/python - <<'PY'
import pandas as pd
from datetime import date
from src import exposure, market, instruments
import importlib.util

today = date.today()

print("=== ETF sector-breakdown ages (held funds) ===")
cov = exposure.coverage()
dated, undated = [], []
for _, r in cov.iterrows():
    on = str(r["as_of"]).strip()
    if on:
        age = (today - date.fromisoformat(on)).days
        dated.append((age, r["ticker"], on, r["source_quality"], r["why"]))
    else:
        undated.append((r["ticker"], r["source_quality"], r["why"]))
print("  Dated, oldest first:")
for age, ticker, on, quality, why in sorted(dated, reverse=True):
    note = f"  ({why})" if why else ""
    print(f"    {ticker:6} {age:4} days old (as of {on}, {quality}){note}")
print("  No date on file at all (age unknown — not the same as 'oldest'):")
for ticker, quality, why in undated:
    note = f" — {why}" if why else ""
    print(f"    {ticker:6} ({quality}){note}")

print("\n=== Company holdings: exact vs floor (held funds) ===")
spec = importlib.util.spec_from_file_location(
    "refresh_etf_holdings", "ops/refresh_etf_holdings.py")
hold_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(hold_mod)
held = hold_mod.held_etfs()
h = market.load_holdings()
by_ticker = {t: g for t, g in h.groupby("ticker")}
for t in held:
    if t in hold_mod.NO_EQUITY_INSIDE:
        print(f"  {t:6} n/a — {hold_mod.NO_EQUITY_INSIDE[t]}")
        continue
    g = by_ticker.get(t)
    if g is None:
        print(f"  {t:6} MISSING — nothing on file")
        continue
    floor = g["source"].str.startswith("Yahoo top holdings").all()
    # Both numbers and both dates, exactly as the refresher prints them.
    # FLOOR on its own reads the same for a fund half described and one
    # described down to 3%, and "as of <date>" reads the same whether the
    # source published that day or nobody published one and that is merely
    # the day we looked. IMAE is the case that proves it: its rows name
    # companies that stopped existing in 2021-22 under a date from this week.
    stored, cov_pct = g["weight"].sum(), float(g["coverage"].iloc[0])
    print(f"  {t:6} {'FLOOR' if floor else 'exact':6} "
          f"{min(stored, cov_pct):6.1%} of the fund stored, out of the "
          f"{cov_pct:6.1%} its source accounted for  "
          f"as of {g['as_of'].iloc[0]} ({g['as_of_kind'].iloc[0]})")

print("\n=== His own file(s) sitting in the inbox ===")
inbox = market.MARKET_DIR / "holdings_inbox"
files = sorted(p.name for p in inbox.glob("*") if p.is_file()) if inbox.is_dir() else []
print(files if files else "(empty)")

print("\n=== Sector disagreements he has already settled ===")
inst = instruments.load().fillna("")
settled = inst[inst["sector_settled"].str.strip() != ""]
cols = ["instrument_id", "sector", "provider_sector", "sector_settled"]
print(settled[cols].to_string(index=False) if not settled.empty else "(none)")
PY
```

Then run the gap report, which covers everything the block above does not —
no instrument row, no sector breakdown at all, no dated vendor sector, an
unmappable or disagreeing sector (skipping anything just printed above as
already settled), and an unresolvable ticker:

```
venv/bin/python ops/data_gaps.py
```

Read both back to him in plain sentences — which funds are oldest (and which
have no date at all, which is a different, separate thing from "oldest" and
should be said as its own line), which are still a floor **and how thin each
of those floors is**, which dates are only `read` (nobody published one, so
the date says when we looked and not how old the holdings are — say this as
its own line too, because a `read` date sorts as the freshest in the table
while the holdings behind it can be years old), what is sitting unused in the
inbox, which disagreements he has already closed out (so he can revisit one if
he wants to, not because anything is wrong), and what the gap report still
names as missing.

## Step 2 — name what would be fetched, then ask

The scope is knowable with no network call at all — it is just every fund and
stock `positions.csv` says he holds today, read fresh, never a list typed
anywhere:

```
venv/bin/python - <<'PY'
import importlib.util

def load(name):
    spec = importlib.util.spec_from_file_location(name, f"ops/{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

hold_mod = load("refresh_etf_holdings")
market_mod = load("refresh_market")
classify_mod = load("refresh_classification")

from src import instruments as inst, market

held_etfs = hold_mod.held_etfs()
tickers = market_mod.tickers_to_refresh(held_etfs)
composition_tickers = market_mod.tickers_for_compositions(tickers, hold_mod.NO_EQUITY_INSIDE)
holdings_tickers = [t for t in tickers if t not in hold_mod.NO_EQUITY_INSIDE]

held_non_etf = classify_mod._held_instrument_ids(inst.PROJECT_ROOT / "data" / "positions.csv")
instruments_df = inst.load()
non_etf_scope = sorted(
    iid for iid in instruments_df.loc[
        instruments_df["instrument_id"].isin(held_non_etf)
        & (instruments_df["instrument_type"] != "etf"), "instrument_id"]
    if market.provider_ticker(iid, instruments_df) is not None
)

print(f"Sector breakdowns would be checked and overwritten for "
      f"({len(composition_tickers)}): {', '.join(composition_tickers)}")
print(f"Company holdings would be checked and overwritten for "
      f"({len(holdings_tickers)}): {', '.join(holdings_tickers)}")
print(f"Vendor sector would be checked and overwritten for "
      f"({len(non_etf_scope)}): {', '.join(non_etf_scope)}")
PY
```

Funds that hold nothing to look through to (SGOV, IBIT, SLV, DBA today) are
never fetched for company holdings, and cash balances are never fetched for a
vendor sector — both are already excluded above, which is why the two counts
can differ. **"Overwritten" needs two honest exceptions, said to him up
front:** a fetch that fails leaves yesterday's rows alone — a fund's old
breakdown is only replaced if the refresh actually comes back with an answer
— and a vendor sector that *disagrees* with the one already on file is
reported and left untouched, never silently applied over his existing cell
(`ops/refresh_classification.py`). Otherwise it is literal: a refreshed fund's
rows are replaced wholesale, not merged, so a company it sold yesterday does
not survive its own removal.

**This step also reaches a real, shared budget, and that shapes the choice
you offer him next.** Every fund's sector breakdown is checked against Alpha
Vantage, which this project is capped at using 25 times a day, account-wide
(`docs/journal-workflows.md`) — and that is asked for *every* fund above, not
just a few, so a single sweep across a normal-sized book can spend most or
all of a day's budget on its own. A run also takes several minutes, mostly
spent deliberately pacing those requests (13 seconds apart) so the free tier
is not abused — that is not a hang.

Ask him plainly, as a real choice, not a yes/no:

- **"No, not now"** → stop. Nothing else runs.
- **"Just check, I'll decide after seeing it"** → the default. Continue to
  the check below; Step 4 asks a second, separate question before anything is
  saved.
- **"Check and save in one go"** → skip straight to Step 4's `--write`
  command after this one yes. Offer this honestly: it means only one trip to
  Alpha Vantage's shared daily budget instead of two, so if he already knows
  he wants to save, this is the choice that protects the *quality* of what
  gets saved — checking first and saving later can spend the good half of the
  budget on the check and leave the save to fall back to a lower-quality
  provider for whatever's left. Only take this path if he picks it himself.

If "just check":

```
venv/bin/python ops/refresh_market.py
```

This reaches the internet — Alpha Vantage, yfinance, SPDR's own file, Yahoo's
floor — for everything named above. **It does not write anything**, no matter
what it finds; `--write` is not on this command. It is also the first of up
to two trips this session may end up making, for the budget reasons above.

Tell him, per fund, in plain words: did its date move forward, did it stay
exactly where it was, did a fund that was a floor become exact, did anything
refuse outright and why. **Then quote the warnings block from its closing
summary whenever it has anything in it.** `ops/refresh_market.py` only prints
that block at all when there is something to say, so there is no empty block
to quote — if nothing printed, just say "no warnings" in one sentence rather
than hunting for a section that isn't there.

If "check and save", skip ahead to Step 4's command directly — there is no
separate check-only run in this path.

## Step 3 — if a fund has no automated downloader, offer the inbox

This reads `data/market/etf_holdings.csv` exactly as it stands on disk right
now. A "just check" run changes nothing here — this list is identical to
Step 1's. Only an actual save (`--write`, Step 4) can move a fund off it; if
you just saved, re-run this and a fund you expected to see may already be
gone, which is correct, not a bug.

```
venv/bin/python - <<'PY'
import importlib.util
from src import instruments, market

spec = importlib.util.spec_from_file_location(
    "refresh_etf_holdings", "ops/refresh_etf_holdings.py")
hold_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(hold_mod)

inst = instruments.load().fillna("").set_index("instrument_id")
h = market.load_holdings()
by_ticker = {t: g for t, g in h.groupby("ticker")}

for t in hold_mod.held_etfs():
    if t in hold_mod.NO_EQUITY_INSIDE or t not in inst.index:
        continue
    g = by_ticker.get(t)
    floor = g is None or g["source"].str.startswith("Yahoo top holdings").all()
    issuer = hold_mod.issuer_of(t)
    if floor and issuer not in hold_mod.ISSUER_DOWNLOADERS:
        print(f"  {t:6} {issuer or '(issuer unknown)':12} {inst.at[t, 'name']}")
PY
```

SPDR and Global X are the issuers this repo can download from automatically
(`ops/refresh_etf_holdings.py`'s `ISSUER_DOWNLOADERS`). Every fund the script
above prints — whatever its issuer — stays on Yahoo's floor until he drops a
file in himself. **Always optional** — a fund he never gets to stays a floor,
nothing breaks, nothing is blocking.

For each one, say the ticker, its issuer, and its full name, and tell him:
search the ticker on that issuer's own website (`ishares.com` for iShares,
`invesco.com` for Invesco, or whichever site belongs to the issuer named
above for anyone else) and download its holdings file — usually under a
"Holdings" tab.

Either way, the file goes in `data/market/holdings_inbox/` and its name must
**start with the ticker** — `QQQ.csv`, `QQQ-holdings.xlsx`, anything after
the ticker is fine. A spreadsheet is converted to CSV on the way in, so one
parser reads every file; he asked for that consistency on 2026-10-06. No deadline, no queue: a fund with no file just keeps
reading from Yahoo's floor. **A file dropped in before Step 4 runs will be
picked up by that same `--write` run** — there is nothing further to trigger;
one dropped in after it has to wait for the next `/market`.

## Step 4 — save

Reached two ways: as a second, separate yes after "just check" showed him
something worth keeping, or directly, because he chose "check and save" back
in Step 2.

**If this follows a "just check" run, say plainly before asking: this is a
SECOND trip to every provider, not a save of what the check already found.**
Alpha Vantage's daily budget is shared across both trips — if the check
above already used most of it, this run may come back lower-quality for
whatever is left (falling back to yfinance's vendor-level data instead of the
issuer's own), and Step 5 below will name that by ticker if it happens.

- **No** → stop. Nothing is written. Say so plainly: everything the check
  found is thrown away, and he can run `/market` again whenever he likes.
- **Yes** →

```
venv/bin/python ops/refresh_market.py --write
```

## Step 5 — show what changed, including what got worse

Quote the warnings block from this run too, whenever it has anything in it —
same rule as Step 2: no block printed means say "no warnings" yourself, never
imply one was hidden.

Then run Step 1's two commands again and set the new numbers beside the old
ones. For every held fund, say plainly:

- did its sector breakdown get a newer date, or did it not move
- did its quality change — issuer data, vendor data, an estimate — in
  **either** direction (this is where a same-day double-fetch's quota damage,
  if it happened, shows up — call it out by name if it did)
- did it cross from exact to floor, or floor to exact
- is there a gap in `ops/data_gaps.py`'s output now that was not there before

**Name anything that got worse, by ticker, even if nothing else asked you
to.** If nothing got worse, say that in one plain sentence — "nothing got
worse" is a real answer, not a thing to leave unsaid.

## Step 6 — offer the rollback, correctly

Two different files changed, and they are undone two different ways. Say
both, every time, whether or not anything looks wrong.

**`data/market/` — the sector breakdowns and company holdings:**

```
git checkout -- data/market/
```

This alone undoes everything written to those two tables and puts the old
numbers back.

**`data/instruments.csv` also changed, and that command does not touch it.**
`--write` fills in `provider_sector` / `provider_sector_as_of` /
`provider_sector_source` for every held stock it could get an answer for, and
`issuer` for any fund that did not already have one on file. That same file
also carries his own hand-typed `sector`, `cluster_id`, and everything else
about every instrument he owns — so it must never be blanket-reverted; that
would throw his own edits away to undo ours. Instead:

```
git diff -- data/instruments.csv
```

Show him exactly which cells changed. If he wants any of them undone, clear
just those cells for him — `instruments.set_provider_sector(<id>, "", "", "")`
or `instruments.set_issuer(<id>, "")` — never `git checkout` that file.

## Things that will come up

**"Why is X still a floor after I ran this?"** Its issuer has no automated
download — today that is every issuer except SPDR — and no file is sitting
in the inbox for it. Not a failure — offer Step 3.

**"Can I just update one fund?"** No — this always covers every fund he
holds, read fresh from `positions.csv` each time, plus the benchmark. A new
fund bought last week must never be silently left out because a narrower
command only knew the old list. If he really wants one fund by itself, that
is `ops/refresh_etf_compositions.py <TICKER> --write` or
`ops/refresh_etf_holdings.py <TICKER> --write` run by hand — outside this
skill entirely, something only he runs, and he should know he is choosing to
skip both the full picture and the two yeses.

**"It's only been a few weeks, why are you asking?"** It isn't — nothing in
this skill nags on a calendar. The only thing that ever mentions this on its
own is the end-of-turn line that appears once the oldest *dated* breakdown
passes ninety days — and that line can only watch funds that already carry a
date. A fund with none at all (today: MAGS, SGOV, IBIT, SLV) never triggers
it, however old it gets; Step 1 above is where those show up instead.

## Hard rules

- **Never fetch, and never write, without his yes.** By default that is two
  separate yeses; "check and save" (Step 2) is the one case where a single,
  explicit yes covers both, and it is only ever his choice to make, never the
  default offered first.
- **Never pass `--write` to `ops/refresh_market.py`** except right after a
  yes that was actually about saving, in this same conversation.
- **Never bury a warning.** Quote the block in full whenever it runs with
  anything in it; say "no warnings" plainly when it has nothing.
- **Never guess a fund's download page.** Point at the issuer's own site and
  the ticker to search for. `ISSUER_DOWNLOADERS` is the list of issuers with
  code to automate this — SPDR and Global X; everyone else goes through
  Step 3. Read the registry rather than naming issuers here, so this line
  cannot go stale the way it did on 2026-10-06.
- **Never touch a claim, a note, or anything he decided.** This skill writes
  only `data/market/etf_compositions.csv`, `data/market/etf_holdings.csv`,
  and the vendor-sector/`issuer` columns on `data/instruments.csv` — never a
  claim, a note, his own `sector` or `cluster_id`, or anything else.
- Stop after Step 6. He runs [`/review`](../review/SKILL.md), then a branch
  and a PR he merges himself.
