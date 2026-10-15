---
name: evidence
description: File something he has read — a research PDF, a chart screenshot, a web article — into the three evidence tables, and attach it to the decisions and themes it actually bears on. Asks the questions in a fixed order, previews everything, and writes only after he confirms. Use when he types /evidence, says "I dropped a PDF", "I dropped a chart", "file this report", "I read something interesting", "here is the Fundstrat report", "add this screenshot", "what did I read about X", or whenever a new file appears in notes/screenshots/_inbox/, notes/research/_inbox/ or notes/research/. Also use when a claim is missing its evidence and he wants to fix that.
---

# /evidence — file what he reads, once, and attach it to everything it bears on

**Why this exists, in his words:** *"the evidence is what lets me manage all
the knowledge from all the stuff I'm reading and, of course, forgetting. For
me, consistency is the most important thing."*

So the value here is **not** speed. It is that the same questions get asked in
the same order every time, so nothing is filed half-way and he never has to
remember what a complete filing needs. That is your job, not his.

`/evidence` files documents. [`/note`](../note/SKILL.md) writes claims and
notes. [`/show`](../show/SKILL.md) reads things back.

## 🚨 The three rules that outrank everything here

**One document, one row — forever.** A 325-page report covering twelve stocks
is filed **once** and attached many times. Never file it twice; the store and
the commit guard both refuse it. Run `evidence.load_evidence()` **before you
ask him anything** — it may already be there, and the right answer is then
"REUSED e0005", not a new row.

**You may write "What it says". You may never write "What it means for me".**
A page-cited compression of someone else's document is not his reasoning — that
is the only reason a machine may write prose into `notes/` at all. "What it
means for me" stays `TODO` until he fills it in himself. No code enforces this.
It rests on you, and it is the same rule as never inventing a bear case.

**Never guess `source` or what it is about.** If he says "13D said something
about bitcoin", ask which. A document attached to the wrong thing is worse than
one not attached at all, because it will be found later and believed.

## Step 1 — look before you ask

Never open with a question. Find out what is actually there, then report it:

```
venv/bin/python - <<'PY'
import pandas as pd
from pathlib import Path
from src import evidence
pd.set_option("display.width", 200); pd.set_option("display.max_colwidth", 50)

# ⚠️ Both _inbox folders come FIRST — a file sitting in one is something he
# has read and not yet filed, and it is the whole reason the drop zones exist.
# `notes/research/_inbox` was added 2026-10-11 and was missing from this list
# for its first hour, which made the inbox invisible to the only step that
# looks. `is_file()` skips the nested _inbox when listing its parent, so the
# parent never hides it.
for d in ("notes/screenshots/_inbox", "notes/research/_inbox", "notes/research"):
    files = sorted(p for p in Path(d).glob("*") if p.is_file())
    print(f"{d}/")
    for p in files:
        print(f"   {p.name}   {p.stat().st_size/1024:,.0f} KB")
    if not files:
        print("   (empty)")

print("\nalready filed:")
print(evidence.load_evidence()[["id","kind","title","source","published","location"]].to_string(index=False))
print("\nfiled and never attached to anything:")
u = evidence.unlinked()
print(u.to_string(index=False) if len(u) else "(none)")
print("\nclaims with no evidence of their own:")
c = evidence.unevidenced_claims()
print(c.to_string(index=False) if len(c) else "(none)")
PY
```

Report in one line each: what is sitting unfiled, whether it looks like
something already in the table, and which claims are currently unevidenced.
`unevidenced_claims()` is never blocking — it is a list of opportunities, not
errors.

**If all three folders are empty**, say so and ask him to drop the file in —
a PDF goes in `notes/research/_inbox/` under any name, a chart in
`notes/screenshots/_inbox/`. A URL needs no file: that is `storage="link"`.

## Step 2 — the questions, in this order

The order is not arbitrary. Each answer narrows the next, and the last two
cannot be answered until the earlier ones are.

**1. What kind of thing is it?** `chart` · `research` · `article` · `note`
(from `ops/vocab/evidence_kind` — print all four; he cannot be expected to
recall a fixed vocabulary, and `AskUserQuestion` truncates at four options.)

**2. Who is it from?** `mine` · `fundstrat` · `13d` · `wsj` · `x` · `friend` ·
`ft` (from `ops/vocab/origin`.) His own chart is `mine`. Never inferred
from the filename.

**3. When was it published?** The date on the document, not today.

**3a. For `article` only — who wrote it, and what does it say?**
`author` is the byline; `gist` is **one line** on what the piece argues. Both
live on the document, so an article linked to four things carries one gist.
Added 2026-10-11 at his request: he wanted a reading list he could pull out,
and `evidence.articles()` is it — id, date, provider, writer, headline, gist.

- **The gist may be drafted for him**, unlike a thesis — it describes what the
  article says, not what he decided, so there is no real money in it. Draft it
  from the piece, show it, and let him change it. He chose that on 2026-10-11
  over writing it himself.
- **Blank is fine** on both. The 50 documents filed before this existed have
  neither, and `articles()` prints an empty cell rather than `NaN`.
- Skip this for a chart. A chart has no byline and its gist is the picture.

**4. What is it about?** — the heart of it. Ask in his language: *"what does
this bear on?"* Three altitudes, and they mean different things:

| Attach to | Means | Effect |
|---|---|---|
| `claim` | *this is why I decided* | Closes that claim's evidence gap. Reaches nothing else |
| `cluster` / `sector` | *this is about the whole theme* | Reaches every holding underneath as a **count**. Fills nobody's gap |
| `instrument` | *this is about the company* | Background, no decision attached |
| `macro` | *this is about the whole economy* | Reaches no holding. The one target with no table behind it |

⚠️ **`macro` takes the id `macro` and nothing else** — `check_target()` refuses
`"Macro"`, `"macro rates"` or anything else, so the subject cannot split into
synonyms. Added 2026-10-11, after an S&P chart had to be filed unattached for
want of it. **The same idea is called `market` in `commentary.csv`**, and the
two were deliberately left unmatched — he was shown the split and chose it. So
*"everything about macro"* means reading **both words**.

Offer real targets, not the abstract choice — his open claims by id and ticker,
the cluster the holding sits in, the instrument itself. **One document often
belongs at two altitudes at once**, and that is correct, not duplication.

**5. For a long document — which themes need a write-up now?**
A 300-page report gets **one file per theme**, never one file summarising the
whole thing. Recommend writing only the themes he is acting on today; the rest
get written when the holding they bear on gets written. That keeps the write-up
attached to a decision instead of becoming homework.
⚠️ **If a write-up of that theme already exists, link it — do not write a
second one.** Double-*linking* is the design; double-*writing* is the thing it
exists to prevent.

**6. The page and the one-line why, for each link.** These live on the
**link**, never on the document — the same report backs one claim at p.12 and
another at p.31. The `why` is one line in his words: *"the memory bottleneck
and DRAM's YTD lead"*, not "research".

**A page may be a range** — `page="175-176"`, forwards only. A theme rarely
sits on one page: Fundstrat's IBM selloff runs 15-17 and 13D's Optimus
paragraph runs 55-56, and citing only the first sends a reader somewhere that
does not carry the whole claim. Use two links instead when the pages say
genuinely different things and each deserves its own `why`.

**7. And last, the open one: "anything else in this I should know about?"**

Ask it every time, right before the preview. The five questions above are
fixed so that filing is consistent — but a fixed list can only ask what was
anticipated, and **he is the one who read the document.** He may have noticed
that it names a holding you did not know he owns, or contradicts a level he
set last month, or says something about a theme nobody asked about.

Make it easy to answer by offering what you already noticed while reading —
a ticker he holds that appears in it, a number that cuts against an existing
claim, a second theme you saw and did not raise. Then let him add anything
else in his own words.

What his answer becomes, depending on what it is:

| He says | Where it goes |
|---|---|
| another thing this bears on | one more `evidence_links.csv` row |
| a second theme worth capturing | another write-up in `notes/picks/` |
| something that changed his mind | **not here** — that is a comment in `commentary.csv`, or `resolve_claim()` then a new claim, via [`/note`](../note/SKILL.md). Say so and offer it. ⚠️ Not a `kind=update` row; retired 2026-09-27 |
| context about the document itself | a line in the write-up under "What it says", only if you can cite a page |
| nothing | write nothing. Silence is a complete answer |

🚨 **His answer here is his.** It arrives as loose speech at the end of a long
session, which is exactly when a plausible tidy-up is most tempting and most
damaging. File what he said; if it needs a sentence he did not say, ask rather
than supply it.

## Step 3 — file the physical file first

Nothing can be recorded before the thing it points at exists.
`add_evidence(storage="repo")` refuses a chart that is not filed yet, and
`add_summary()` refuses a write-up that is not on disk.

| What it is | `storage` | `location` |
|---|---|---|
| a chart, or a `picks/` write-up you committed | `repo` | the repo-relative path |
| a web page | `link` | the URL |
| a paid research PDF | `private` | **the filename only** — it lives in gitignored `notes/research/`, and a path would leak it into a committed table |

**Charts** live in `notes/screenshots/` as
`YYYY-MM-DD-TICKER-subject[-1d|-1w|-1m].png`, subject from
`ops/vocab/chart_subjects`, **≤500 KB** — the commit guard enforces all three.
Use the bundled helper; it renames, downscales and verifies in one step:

```
venv/bin/python .claude/skills/evidence/scripts/file_chart.py \
    "notes/screenshots/_inbox/ASML_2026-08-18.png" \
    --date 2026-08-18 --ticker ASML --subject price --timeframe 1d
```

**Read the chart before filing it.** It carries the market state — ATR, the
200-day, the Fib levels, the trend line — and CLAUDE.md's rule is that the
chart carries everything that was on screen. Tell him what it shows; if it
confirms or contradicts a level he set, that is worth a sentence.

**PDFs have a drop zone too: `notes/research/_inbox/`** (added 2026-10-11).
He drops a file under whatever name the newspaper gave it; **you** rename it
to `YYYY-MM-DD-source-topic.pdf` and move it up into `notes/research/`. Check
the inbox at the start of any evidence session — a file sitting there is a
piece he has read and not yet filed.

```bash
ls notes/research/_inbox/          # anything waiting?
```

⚠️ **Never ask him to name a file himself.** He asked for this inbox on
2026-10-11 precisely because naming came before filing; a rule he has to obey
before he can hand you something is a rule that stops him handing it over.
⚠️ Both the inbox and `research/` are gitignored, so **a fresh clone has
neither the PDFs nor the folder** — `mkdir -p notes/research/_inbox` restores
the folder; the documents themselves exist only on his Mac.

**Research PDFs** go to `notes/research/` as
`YYYY-MM-DD-source-topic.pdf`. To find the pages worth citing without reading
300 slides:

```
venv/bin/python .claude/skills/evidence/scripts/find_pages.py \
    notes/research/2026-07-16-fundstrat-market-update.pdf memory dram hbm
```

It prints every page matching those words, then the text of the top hits, so
you can quote with a page number rather than a vague gesture at the report.

**Write-ups** go in `notes/picks/` from
`notes/templates/research_summary.md`. Every line under "What it says" carries
a page number. "What it means for me" is `TODO`.

## Step 4 — preview everything, then wait

**His rule, and it is not negotiable:** nothing is written until he has seen
the complete thing and said yes. Hold every answer in memory; write nothing
during the interview.

```
notes/screenshots/2026-08-18-ASML-price-1d.png  FILED   1600px, 457 KB
                                                        (was 2798px, 581 KB)
data/evidence.csv        NEW    e0006  chart · "2026-08-18-ASML-price-1d"
                                       · mine · 2026-08-18 · repo
data/evidence.csv        REUSED e0005  research · Fundstrat, 16 July 2026
notes/picks/2026-07-16-fundstrat-memory-bottleneck.md   NEW
                                       full text below; "What it says" is mine,
                                       every line page-cited. "What it means
                                       for me" stays TODO — yours
data/evidence_summaries.csv NEW s0001  e0005 -> that file
data/evidence_links.csv  NEW  claim   c0010            <- e0006
                              why "the trend line and the €1,492.4 Fib"
data/evidence_links.csv  NEW  cluster AI Infrastructure <- e0005  p.3
                              why "AI bottlenecks exist in Memory, Chips, Power"

confirm?
```

**Print any write-up in full**, and say which words are yours. It is prose that
will sit under his name, so it needs the same yes a note does. If he changes
one thing, **reprint the whole preview, not the diff** — he is confirming the
artefact.

## Step 5 — write, in this order

The order is forced by what each function checks. Get it wrong and it raises.

```python
from src import evidence

# 1. the file exists on disk already (step 3)

# 2. the document — check first, it may already be there
eid = evidence.add_evidence(kind="research", title="Fundstrat Market Update, 16 July 2026",
                            source="fundstrat", published="2026-07-16",
                            storage="private",              # the FILENAME only
                            location="2026-07-16-fundstrat-market-update.pdf")

# 3. the write-up file, then its row
sid = evidence.add_summary(eid, "notes/picks/2026-07-16-fundstrat-memory-bottleneck.md")

# 4. the links — BOTH ENDS MUST ALREADY EXIST
evidence.link("claim", "c0012", "summary", sid, why="the memory bottleneck")
evidence.link("cluster", "AI Infrastructure", "document", eid, page="3",
              why="AI bottlenecks exist in Memory, Chips, Power")
```

⚠️ **A link needs a claim that exists.** If the evidence backs a decision he has
not recorded yet, the claim comes first — that is [`/note`](../note/SKILL.md)'s
job, not yours. Say so and offer to run it, rather than filing the document
with nothing to attach it to.

**The write is not atomic.** If something fails partway, say exactly what
landed and what did not. A silent partial write is worse than a loud failure.

## Step 6 — read it back, then stop

Show him it worked, from his side rather than the table's:

```
venv/bin/python -c "
from src import evidence, holding
import pandas as pd; pd.set_option('display.max_colwidth', 60)
print(evidence.for_target('claim', 'c0012').to_string(index=False))
print(holding.inherited_line(holding.summary('DRAM')['inherited']))
"
```

Then **stop. Do not commit.** He runs [`/review`](../review/SKILL.md), then a
branch and a PR he merges himself.

## Things that will come up

**"Isn't attaching the same report twice a duplicate?"** No — and he asked
exactly this. The link table exists so one page can bear on two things for two
different reasons. One PDF, one write-up, many links: fix a page number and you
fix it once. What the design forbids is writing the same five bullets into two
files.

**"Can I get to the write-up through the cluster?"** Only if a link says so.
A cluster linked to the *document* gives a page number; linked to the
*summary* it gives something readable. If he wants to reach his own reading
from the theme, that is one extra link row — no new file.

**A cluster link never closes a claim's gap.** "There is a report about AI
somewhere" is not "this is why I bought." Say this plainly if he expects
`unevidenced_claims()` to go quiet after a theme-level filing. It won't, and
that is the point.

**He does not want a chart on something.** Fine. `no chart filed against any
claim` will keep appearing in that holding's gaps because it is derived, not a
to-do list. Tell him it is informational and nothing acts on it.

**Nothing is ever blocking.** A claim with no document is *reported* by
`evidence.unevidenced_claims()`, never refused. `evidence.unlinked()` is the
other side — research he filed and never acted on, which is the more expensive
mistake of the two.
