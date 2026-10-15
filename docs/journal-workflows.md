# Journal workflows — trades, position notes, research

Detail split out of `CLAUDE.md` on 2026-08-10 (P5: CLAUDE.md routes, it does
not explain). Read this when logging a trade or filing research.

---

## Every trade leaves a written artifact

Since 2026-08-18 the commands do this work.

⚠️ The weekly market file, the Sunday pre-commit, the cleanup tracker and the
current-vs-target table are **gone**, along with the caps and targets they
carried — archived with the constitution on 2026-08-15. Do not reinstate them;
ask him.

| What happened | How it gets recorded |
|---|---|
| **A buy, an add, a trim** | [`/note TICKER`](../.claude/skills/note/SKILL.md) — his reason, and a claim row if there is a real bet behind it |
| **A price he set was reached** | [`/level`](../.claude/skills/level/SKILL.md) — what happened, and what it does to the bet |
| **A bet reached its check date** | [`/resolve`](../.claude/skills/resolve/SKILL.md) — his verdict, in his words |
| **A full close** | `/level` or `/resolve`, then `instruments.set_status(TICKER, "closed")` and move the note to `notes/positions/closed/`. **Not `_archive/`** — his call 2026-09-28: a closed position is a trade to learn from, not dead machinery. `set_status()` refuses while any broker lot still holds shares. ⚠️ Confirm the whole instrument is gone first — one note can cover several lots, and `Removed:` does not reliably mean closed |
| **A fill that matched something he already wrote** | `claims.link_trades()` — the only join between what he planned and what he did. Nothing else prompts it |

**The position note holds the reasoning; `claims.csv` holds the checkable bet.**
A note is not required before an order — never let missing paperwork stop a
trade being recorded.

**On the sync delay:** a same-day trade usually does not appear until the next
business morning, so `+0 new trades` after a known trade is expected. Say so
and re-sync in the morning; `positions.csv` stays the truth meanwhile.

---

## Writing a position note

`/note TICKER` is the tool — it asks only what the shape needs and writes
nothing until he confirms the whole record at once. The rules it enforces, in
case you are ever writing one by hand:

1. **Never invent the thesis, or fundamentals.** P/E, market cap, growth —
   only from him or a citable research file. Otherwise `TODO`.
2. **Current-state numbers are never typed.** Shares, market value, weight and
   multiples rot silently; `clusters.weights()` and `/show` print them live.
   Point-in-time facts ("bought 100 sh at $40 on 2025-03-11") are permanent
   and belong in the note.
3. **`## Bear case` and `## Catalysts` are required**, ETF or not. `src/notes.py`
   enforces it and the commit hook refuses without them. For an ETF keep the
   bear case about what breaks the *exposure*, not a company story.
4. **Templates** live in `notes/templates/` — `position.md`, `etf_position.md`,
   `cluster_thesis.md`, `research_summary.md`.

⚠️ **The `AUTO:FUNDAMENTALS` writer was deleted on 2026-09-29.** It could only
crash — it read a `positions.csv` column removed in Phase 2d and needed the
archived `damodaran` module besides. Two closed notes still carry its markers;
leave them, they are harmless.

If it is ever rebuilt, two decisions are worth keeping from the version that
was deleted: never hand-edit inside the markers, and the writer must **refuse**
to create a note that does not exist — the thesis is his to write first.

---

## Alpha Vantage fundamentals — quota discipline

`data/fundamentals/<TICKER>.json` is the cache. `pf.get_fundamentals(ticker)`
fetches and caches; a second call within **7 days** costs nothing.

The free tier allows **25 calls/day, 5/minute**. Space ad-hoc fetches ~13
seconds apart. Blowing the quota means no fundamentals until tomorrow.

**Check `av_ticker` in `data/instruments.csv` before fetching.** That column
decides which company gets cached — broker `NOV` resolves to `NVO` for Novo
Nordisk — and blank means no fundamentals exist for that row (cash, ETF, FX).
Fetching on the broker symbol is how the wrong company ends up cached under
the right name.

---

## Filing research

[`/evidence`](../.claude/skills/evidence/SKILL.md) owns this. The shape, so you
know what it is doing:

- **An article** → `kind="article"`, plus `author` and a one-line `gist`.
  `storage="link"` with the URL for a free page; **`storage="private"` with
  the bare filename for a paywalled PDF** (WSJ, FT), which is the usual case.
  Then `evidence.link(...)` onto what it bears on — a holding, a theme, or
  `macro` for the whole economy. There is no `notes/articles/` folder.
- **A research PDF** → he drops it in `notes/research/_inbox/` under any name;
  **you** rename it `YYYY-MM-DD-source-topic.pdf` (lowercase) into
  `notes/research/` (gitignored, never commit). Then **three steps, in this
  order**:

  1. the document row — `add_evidence(kind="research", …, storage="private",
     location="<the filename only>")`. Filed **once**, however many stocks it
     covers.
  2. one write-up per theme in `notes/picks/`, from
     `notes/templates/research_summary.md`, then `add_summary(eid, path)` — so
     a summary can never float free of its source.
  3. `evidence.link("cluster", "<the theme>", "summary", sid, page=…, why=…)`
     — or `sector`, `instrument` or `claim`, whichever it is actually about.

⚠️ Skip step 2 and the write-up is invisible to `/show` — which is how the
older files in `notes/picks/` became unfindable. Skip step 3 and
`evidence.unlinked()` names it: research filed and never acted on is exactly
what that function is for.

🚨 **"What it says" may be written for him, every line page-cited. "What it
means for me" is his and stays `TODO`.** No code enforces this one.
