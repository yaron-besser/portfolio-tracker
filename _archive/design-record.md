# Design record — the reasoning behind each table

Moved out of `REBUILD.md` §5 on 2026-08-14, at Yaron's request: the file was
954 lines on `main` and every session read all of it to find the thirty that mattered
today. This is the part that explains *why* each table has the shape it has —
still true, still worth reading, just not on every turn.

Live rules are enforced by code, not by this document: the pandera schemas in
`src/`, the vocabulary files in `ops/vocab/`, and `ops/hooks/pre-commit`. Where
this file and the code disagree, **the code is what runs** — but a disagreement
is a bug in one of them, so say so rather than picking a side quietly.

---

## 5. Information architecture

Agreed 2026-08-08. The diagrams were drawn outside this repo and are not
included in it.

**Data flow after Phase 2c** (2026-08-10) — sources, tables, guards, and what
is parked. That diagram also lived outside this repo.

### 5.1 Flow A — context (what Claude reads, in what order)

| Layer | File | Loaded | Contains |
|---|---|---|---|
| 0 | `CLAUDE.md` | every turn | What the project is, where things live, which skill handles which task. **Routing only, never explanation.** ≤150 lines |
| 1 | a skill | when the task matches | The procedure for that task |
| 2 | target files | when the skill names them | The note or CSV being worked on |
| 3 | reference docs | only when explicitly needed | Sector taxonomy, Damodaran conventions, retention policy |

Each layer names the next. No fact appears in two layers.

### 5.2 Flow B — information lifecycle

`origin` distinguishes material that came from outside from the user's own
thinking. Both are tracked; they are not the same kind of asset.

| Input | Origin | Arrives as | Rests in | Expires |
|---|---|---|---|---|
| External research | external | raw file, gitignored | `notes/picks/`, `notes/articles/` | archive 6 mo |
| Macro view | yours | written monthly | `notes/macro/` | archive 12 mo |
| **A decision** | yours | chat | `notes/positions/` | **never** |
| Position closed | yours | chat | `notes/positions/_archive/` | **never** |
| Lesson or pattern | yours | chat | `notes/insights/` | **never** |
| Broker data | external | scheduled Flex pull | `data/*.csv` | never |
| Alert + heartbeat | machine | monitor job | `ops/` | 30 days |
| Weekly summary | machine | scheduled job | email only | on send |

Two rules:
1. **Raw stays raw; interpretation gets committed.** `notes/research/` holds
   only raw files you could re-acquire. Your reading of them is committed to
   `picks/`. Nothing you thought is ever gitignored.
2. **Research is an input, never a conclusion.** A summary names the
   positions it touches; the decision is written in the position note.

### 5.3 Normalization

- **1NF — one fact in one place; no file mixes altitudes.** A file is rules,
  or a position, or an execution log — never two.
- **2NF — a fact belongs to the entity it describes.** Cluster files hold
  only what is true of the cluster as a whole.
- **3NF — nothing *derived* is stored by hand.** This splits in two, and the
  distinction matters more than the rule:
  - **Point-in-time facts** ("bought 100 shares at $40 on 2025-03-11") are
    historical record. They never go stale. They stay in the note,
    permanently — they are the substance of R1.
  - **Current state** (current shares, market value, portfolio weight, P/E)
    is derived, rots silently, and is never typed by hand.

**Controlled duplication (P7).** A copy is permitted only if all three hold:
1. Exactly one source of truth exists and is named explicitly.
2. The copy is written by a program, inside visible markers.
3. Hand-edits inside those markers are expected to be destroyed on next run.

Every generated block must carry a visible `generated YYYY-MM-DD` stamp, so
a dead generator is legible as staleness rather than passing as current (P2).
A "trigger" must be a real mechanism — a scheduled generator, a test that
fails on divergence, or a check inside the weekly job. A habit is not a
trigger.

### 5.4 Decision and claim storage

Requirement: look back three months, see what was thought about a stock,
and judge whether the decision was right *given what was known at the time*.

**Split by shape, not by topic.** The test is whether you can sort or filter
by it.

| Content | Store | Why |
|---|---|---|
| Claims, check dates, outcomes — same fields every time | `data/claims.csv` | Sortable, filterable, pandas-native |
| Thesis, bear case, reasoning — an argument | `notes/positions/<TICKER>.md` | No columns exist for it; git diffs show how thinking changed |

One file per position stays correct. It gets much shorter once claims and
current-state numbers move out. Rejected: SQLite (binary — git cannot diff
it, which destroys the audit trail that makes R1 valuable), and parsing
claims out of prose (silent parse failures; structured data inside a prose
file violates 1NF).

**Schema — `data/claims.csv`** (amended 2026-08-08: `chart_tf` removed,
`price` + `conviction` added — see below):

```
id, date, recorded, ticker, origin, kind, claim, wrong_if,
check_date, price, conviction, evidence, resolved_date, outcome, note_ref
```

**Two dates, and this is not optional.** `date` is when you thought it;
`recorded` is when it entered the system. A claim written on the day and
one backfilled months later must be permanently distinguishable — otherwise
backfill reads as foresight and every retrospective conclusion is
untrustworthy. `recorded > date` marks a row as reconstructed, and any
scoring must be able to exclude those.

This is the same two-date problem as stale fundamentals (§5.6): when was it
true, versus when did we learn it. One rule covers both.

- `origin` — `mine | fundstrat | 13d | wsj | x | …` (fixed vocabulary)
- `kind` — `claim` (a bet) or `watch` (tracking, no position yet)
- `price` — the close, or your fill, on `date`. Required on a `claim` row
  (added 2026-08-08): without it a resolved claim says only *whether* you
  were right, never *how much* the move was worth. Optional on `watch`
- `conviction` — `low | med | high`. Required on a `claim` row (added
  2026-08-08): this is what lets the ledger score *you* — do
  high-conviction calls actually beat low-conviction ones? Optional on `watch`
- `outcome` — `right | wrong | unclear`, blank until resolved
- `note_ref` — path to the markdown holding the reasoning

No `status` column: open / due / resolved is **derived** from `check_date`
and `resolved_date`. Storing it would violate 3NF.

**Visual evidence is screenshots, never generated charts** (revised
2026-08-08, replacing the earlier generated-chart design). The user's charts
come from TradingView — the indicators, drawings, and panels *are* the
evidence, and a rendered price line does not reproduce them. One decision
also routinely rests on several views at once (1D + 1W + a PE chart), which
no single mandatory `chart_tf` column could carry — and the screenshot
filenames already record the view, so a column would be a hand-maintained
duplicate (P7). `chart_tf` is removed; the price-history cache and chart
generation are dropped entirely, not deferred. Accepted cost: no
"what happened after" view — a screenshot only shows the day it was taken.

- **`evidence` is multi-valued and never blocking** — semicolon-separated
  screenshot paths, or blank. The ledger reports claims with no image so the
  gap is *visible*. A hard requirement would mean that on a night you cannot
  screenshot, the claim goes unrecorded — losing the decision to protect the
  evidence.

**Screenshots capture what data cannot** — your indicators, your drawings,
the PE panel you were reading. Unlike research PDFs, a screenshot of a
moment cannot be re-acquired, which is why these are **committed** where
PDFs are not.

| Path | Tracked | Rule |
|---|---|---|
| `notes/screenshots/_inbox/` | gitignored | Drop zone. Any filename. Never referenced by a claim |
| `notes/screenshots/` | committed | `YYYY-MM-DD-TICKER-slug.png` only. Permanent |

You never rename anything: drop the file in `_inbox/`, describe the
decision in chat, and the file is renamed, filed, and written into
`evidence`. The filename *is* the index — date, ticker, and subject are
greppable, so no separate catalog exists to go stale (P7).

Volume: git history is 2.8 MB today. At ~300 KB per downscaled screenshot
and ~200 a year, that is ~60 MB/year — comfortable for over a decade. Git
never forgets a binary even after deletion, which is why the size cap in
§5.5 is not optional.

**Two entry tiers.** Observations are free-form prose in the note, never
parsed, no required fields. Claims are rows in the table with every field
required. A watchlist item is a claim with no position — same format,
different `kind`.

**The bet ledger is a generated view, not a second file to maintain.** It
reads `claims.csv` and produces three views — open, due, resolved. Written
once, read many ways (P7). `due` (check date passed, no resolution) is
surfaced by the weekly job — this is the mechanism that makes retrospection
happen rather than being intended.

Resolution is written by the user (P1), prompted by the machine.

### 5.5 Constraints

A rule written in a document is a habit. A constraint is code that refuses
invalid data. Everything in §5.4 is only as reliable as this section.

**Evidence this is needed:** `data/positions.csv` has 31 of 52 rows with a
blank `direct_sector`. `CLAUDE.md` instructs that it be filled in. Nothing
checks, and nothing distinguishes a legitimately blank ETF row from a
forgotten one.

**Implementation: Pandera** (decided 2026-08-08). It is the pandas
equivalent of `CREATE TABLE` constraints, declared once and readable as a
single source of truth.

| SQL | Pandera |
|---|---|
| `NOT NULL` | `nullable=False` (default) |
| `UNIQUE` / `PRIMARY KEY` | `unique=True` |
| `CHECK (x IN (…))` | `Check.isin([…])` |
| `CHECK (check_date > date)` | dataframe-level `Check` |
| `FOREIGN KEY` | custom check — `note_ref` resolves to a real file |
| rejects unknown columns | `strict=True` |

Adds one dependency (`pandera`) to `requirements.txt`. Rejected: hand-rolled
validation (~50 lines, no dependency) — the declarative schema is easier to
audit and its errors name every failing row and field, which P2 requires.

**One schema definition, three enforcement points:**

| Where | Catches |
|---|---|
| `src/claims.py` — validator called by `add_claim()` | Bad data at write time, before it exists |
| `tests/test_claims.py` | Drift in the whole file, and hand-edits |
| git pre-commit hook | Anything invalid reaching history |

The rules themselves live in exactly one place and all three call it (P7).

**Rules** (amended 2026-08-08 to match the §5.4 schema change):
- Required on every `claim` row: `date`, `ticker`, `origin`, `kind`,
  `claim`, `wrong_if`, `check_date`, `price`, `conviction`
- `check_date` must be later than `date`
- Dates ISO `YYYY-MM-DD`; `price` a positive number
- `origin`, `kind`, and `conviction` must each appear in their vocabulary
  file under `ops/vocab/` (one value per line — the single source all
  enforcement points read; adding a value is a one-line append, §5.6)
- `outcome` and `resolved_date` are required together or absent together
- `note_ref` must point to a file that exists
- `evidence` may be blank; when present, every semicolon-separated path
  must point to a file that exists
- `id` unique; the file is append-only

**Screenshot rules, enforced by the pre-commit hook:**
- Any file in `notes/screenshots/` must match
  `YYYY-MM-DD-TICKER-subject.png`, where `subject` is listed in
  `ops/vocab/chart_subjects` (starting set: `price`, `pe`, `ev-sales`,
  `ev-ebitda`, `rsi`, `volume`, `macro`, `other`; an optional `-1d`/`-1w`/
  `-1m` suffix may follow). A raw `Screenshot 2026-06-11 at 22.14.03.png`
  physically cannot be committed — the gate remembers so the user does not
  have to. A rejection names the allowed values and how to extend them.
- Reject images over 500 KB. Git never forgets a binary, so a stray retina
  capture is permanent weight even after deletion. The filing pipeline
  downscales automatically before committing; the hook is the backstop.

**All-or-nothing, and this is the point.** If a claim cannot be completed,
it is not recorded as a claim — it is written as an observation. Laziness
routes a thought down to the lower tier; it never produces a half-filled
bet. The constraint blocks *mislabelling*, never writing.

**No silent failures (P2).** A validator that rejects a row says which
field and why. A generated ledger reports rows it could not process rather
than omitting them quietly.

### 5.6 Vocabularies, extension, and data provenance

Every reference table needs a defined answer to *"what happens when
something arrives that has no slot?"* Today nothing does, so the failure
mode is a silent wrong number rather than an error.

**Extension protocol.** Fixed vocabularies for `kind`, `direct_sector`,
and `origin`. An unknown value makes the tool **refuse to compute and name
what is missing** — never fall back to "Other", never skip the row (P2).
Adding a value is a deliberate edit to the vocabulary plus a row fix, not
a blank that nobody notices.

**The dangerous case is a new ETF.** If a held ETF has no row in
`etf_compositions.csv`, look-through silently under-reports sector
exposure, and a position gets sized against a number that is quietly
wrong. Look-through must fail loudly on an uncovered ETF.

**Current state, measured 2026-08-08:** 21 direct + 31 ETF positions; all
31 ETFs have compositions; all 21 direct rows have `direct_sector`. The
discipline is holding today. Nothing guarantees it keeps holding — that is
the argument for the constraint, not any existing breakage.

**`etf_compositions.csv` has no freshness signal.** Columns are `ticker,
sector, weight, source, sub_sector`. Compositions drift and nothing marks
a row as stale. Add `as_of` and a refresh cadence, as Damodaran data
already has.

**Fundamentals provenance.** `data/fundamentals/<TICKER>.json` carries no
fetch timestamp; the 7-day freshness check relies on file mtime, which a
git checkout or file copy silently resets. Alpha Vantage already returns
`LatestQuarter` (e.g. `2026-04-30`) — the data's own as-of — and it is
never stored or displayed. Fix both:

- Store `fetched_at` (when we asked) and surface `LatestQuarter` (what
  period the number describes). Same two-date rule as §5.4.
- Distinguish **not applicable** from **missing**. 2 of 42 cached tickers
  have no usable PE (BMNR, MSTR — no meaningful earnings). That is a real
  economic fact, not absent data, and must never be read as zero.

### 5.7 Enforcement layers

Three separate mechanisms enforce this design. They are easy to confuse, and
confusing them is dangerous, so the distinction is recorded here.

| Layer | Binds | Fires when | Catches |
|---|---|---|---|
| Pandera schema (`src/claims.py`) | data written through `add_claim()` | at write time | malformed rows before they exist |
| git pre-commit hook | the repository | at `git commit` | anything invalid reaching history, **from any tool** |
| Claude Code hooks (`.claude/settings.json`) | this chat session | on session and tool events | drift that would otherwise go unnoticed mid-work |

**Claude Code hooks are never the data gate.** They only run during a Claude
session — edit `claims.csv` in Excel and none of them fire. Pandera plus the
git hook are the real enforcement; the Claude hooks are a third layer that
binds *the agent* rather than the data. Treating them as data validation
would create false confidence, which is worse than no check.

**Configured 2026-08-08, extended 2026-08-09** in committed
`.claude/settings.json` so they survive a machine change and are visible in
history (P3, P4). **Four** events. Each was pipe-tested on every branch
before being written.

| Event | Behaviour | Blocks? |
|---|---|---|
| `SessionStart` | Injects the `REBUILD.md` status line and first unchecked item into context, so any new chat is self-orienting. P3 enforced by the harness instead of by convention | no |
| `Stop` | Counts uncommitted files and warns if any. Silent on a clean tree. Aimed at the two-months-unbacked failure this project actually had. Fires each turn, not once per session | no |
| `PostToolUse` on `Write\|Edit` → `ops/hooks/claude-posttooluse.sh` | On a `.py` edit under `src/` or `tests/` (skipping `_archive/`), runs the suite (~2.4s) and speaks only if the result differs from `ops/test_baseline` | no |
| `PreToolUse` on `Bash` → `ops/hooks/claude-pretooluse.sh` | **Added 2026-08-09.** Asks the user to approve `git push`; denies `git commit` on `main` | **yes** |

**Both hook bodies are committed shell scripts under `ops/hooks/`, not
inline strings in `settings.json`** (moved 2026-08-09). Inline one-liners
could not be tested without retyping them, and three real bugs had already
hidden in them: the test check read the first `N failed` anywhere in the
output (a traceback could skew it), it cried wolf on every edit once the
baseline was non-zero, and the push guard matched the substring `git push`
so `git -C dir push` slipped through while `echo "git push"` fired. All
paths of both scripts are now pipe-tested; the scripts also report through
`additionalContext` rather than `systemMessage`, since the model is who must
act on them.

**The `PreToolUse` gate is the one exception to "none of them block", and
deliberately so.** A hook that emits only `additionalContext` is advisory:
the docs confirm it is surfaced next to the *tool result*, so the push has
already happened by the time the reminder lands. "Checked before it becomes
final" cannot be built on advisory text, so this one returns
`permissionDecision: "ask"`. It still binds only the agent — the repository
layer (`ops/hooks/pre-commit`, which also refuses commits on `main`) and the
GitHub ruleset on `origin/main` are the real gates.

### 5.8 Price levels — `data/levels.csv`

**Designed 2026-08-14. Not built.** Agreed with Yaron while writing the IWM
thesis: he asked for a downside alarm that carries an instruction, covering
stop losses *and* buy triggers on names he does not hold, and asked whether it
belonged in its own table or as columns on `claims.csv`.

**Its own table.** Three things had no home: a stop level *moves*, and a claim
row can never change; a level needs an instruction, because "$198" does not say
whether to sell, trim, or just look again; and a level can exist for something
he does not own, which has no position to hang off. Today the only price levels
in the system are sentences inside `claim` text — c0001 reads *"Re-enter 1 sh
near $1,232 (Ichimoku Kijun, USD chart)"*. Nothing can check a sentence.

**Schema:** `id`, `date`, `recorded`, `instrument_id`, `level`, `trigger_when`
(`at_or_below` / `at_or_above`), `kind` (`hard` = a real order at IBKR /
`soft` = his own trigger), `action` (`sell_all` / `trim_half` / `re_evaluate` /
`add` / `buy`), `instruction`, `claim_id`, `broker_verified`, `ended_date`,
`ended_reason`, `note_ref`.

Required on every row: everything up to and including `instruction`.
`broker_verified` is **required when `kind=hard`** and **forbidden when
`kind=soft`** — a hard row asserts an order exists at the broker, a soft row
has no order to have verified. `ended_date` and `ended_reason` are required
together or absent together. `instruction` is required even though `action`
already says what to do: the action is countable, the sentence is why, and
dropping it would repeat the mistake the claim store exists to avoid.

**A sell/trim/add level needs a position to exist** (added 2026-08-14, Yaron's
own catch during planning — not in the original design). `sell_all`,
`trim_half` and `add` all assume something is held; `add_level()` refuses them
unless `positions.csv` shows shares for that `instrument_id`, summed across
broker lots. `buy` and `re_evaluate` stay free, so a buy trigger or a "look
again at $X" still works on a watchlist name.

⚠️ **Write time only, never on load or at commit.** If it ran on load, the day
a position is sold the whole file would stop being readable — exactly the
reason `note_ref` existence is a write-time check. A level whose position later
disappears is surfaced by `orphaned_levels()` and **never auto-ended**: the
position may have closed, or the sync may simply not have run, and silently
deleting a level on a guess is how `active_stops.csv` came to describe
positions that no longer existed.

**Append-only, like claims.** A level is never edited — moving a stop is two
acts: end the old row, add a new one. Raising a stop *is* a decision and gets
recorded as one. Active is derived (`ended_date` blank), never stored.
`broker_verified` is the one field re-stamped in place, because it records when
he last *looked* rather than a decision; the exemption is documented rather than
left as a silent inconsistency. **Nothing ends a level automatically** — a
level whose instrument is no longer held is surfaced loudly by
`active_levels()`, never tidied away, because silent cleanup is how
`active_stops.csv` came to describe positions that no longer existed.

| Rejected | Because |
|---|---|
| Columns on `claims.csv` | A claim row can never change but a stop moves; VTI has no claim and could still have a level; one position can carry two levels (a soft trigger and a hard stop) |
| Prose in the position note | Nothing countable, and the Phase 5 alert job could never read it |
| Reviving `_archive/data/active_stops.csv` | It overwrote in place, so the history of his own stop-moving vanished and stale rows went unnoticed — PWR had stopped out, DGX was not held, IBIT's quantity was wrong |
| Deriving direction from the position | Correct until buy triggers exist. A name he does not hold has no position to derive from, and "buy the dip at $X" and "buy the breakout at $X" are opposite instructions at the same price. **This was my own earlier draft, corrected by his requirement** |
| Making `wrong_if` blank-able when a level is linked | Would loosen the rule forcing every claim to state its own invalidation — a guard on the file that protects real money — to buy tidiness, and would make a claim row unreadable on its own |
| An upside target field | Dropped entirely at his call, 2026-08-14. A target is not a level with an instruction; it is a wish |

**`wrong_if` and a level both stay — they are different facts.** `wrong_if`
records what he believed *on the day*, and never changes; a level records what
is operationally true *now*, and moves. The same number may appear in both on
day one. That is not the duplication P7 forbids: a claim is never edited, so
the two cannot drift out of sync — the level simply moves on without it.
Accepted cost: a year-old claim shows the level as it was, not as it is, and
`claim_id` is the link to the live one.

**Built 2026-08-14.** `src/levels.py` (pandera schema, `add_level()`,
`end_level()`, `verify_broker()`, and the views `active_levels()`,
`breached_levels()`, `stale_hard_levels()`, `orphaned_levels()`,
`stale_refs()`), `tests/test_levels.py`, vocabulary files
`level_kind` / `level_action` / `trigger_when`, and `ops/hooks/pre-commit`
extended — the same three enforcement points as claims (§5.7).

⚠️ **Nothing fires.** The alert job was archived 2026-08-10 and does not return
until Phase 5; this builds the record Phase 5 reads. A `hard` row is a **dated
photograph of a broker order, not the order** — truth lives at IBKR, and
`broker_verified` makes the age visible without making the copy correct.
Automatic reconciliation needs open orders from the Flex API, which is Phase 5.
Four real stops are live at IBKR today and nothing is watching them; this
design does not change that.

---

