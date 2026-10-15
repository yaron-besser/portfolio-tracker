# Evidence targets and research summaries — design

**Status:** built 2026-08-17. Written 2026-08-17 with Yaron.
Supersedes nothing; extends
[`2026-08-16-evidence-store-design.md`](2026-08-16-evidence-store-design.md).

## Why

Raised by Yaron 2026-08-17, mid-way through writing the Technology group note:

> "I have a research piece, I want to attach it to a sector or a cluster rather
> than a specific stock because it's common to everyone."

Three separate symptoms of one gap:

1. **A document can only attach to a claim, and a claim is always one stock.**
   `claim_evidence.csv` has a single `claim_id` column and the commit guard
   refuses anything that is not a real claim id. A broad AI report backs all
   twelve technology holdings and has nowhere to go. Today's only options are
   prose in the cluster note (not queryable) or twelve links that each assert
   the report is about one stock.
2. **A large document has no summary.** A 200-page PDF is one row carrying a
   filename. Nothing records what it said, so re-reading it is the only way to
   find out — and the page citations the link table can hold are per-claim, so
   a theme-wide finding has no home either.
3. **`notes/picks/` holds 19 research write-ups that are connected to nothing.**
   Not in `evidence.csv`, not linked to a claim, not linked to a cluster.

## Decisions taken

All Yaron's, all 2026-08-17, in the order they were made.

1. **A document can be about anything with a name** — a claim, a cluster
   (including a group), a sector, or an instrument he holds with no bet on it.
   He chose this over "cluster only" and over "cluster and sector", because
   none of the twelve technology holdings has a claim today, so a stock-level
   research piece would still have been homeless.
2. **Reading down the tree is a pointer, not a pile.** His wording: *"more of
   an option to extend the report… we just can mention 'ask for more evidence
   by the tech cluster', that way it will be shorter."* `/show ASML` prints
   ASML's own documents, then one line naming how many more sit on its cluster
   and group. **Inheritance is never stored** — nothing to go stale.
3. **A per-cluster summary is its own row with its own id.** Rejected on the
   way: one file with `## Chapter` headings and `#anchor` links (an anchor
   breaks silently when a heading is renamed, and would need a new
   does-this-heading-exist guard); and three loose files with no parent row.
4. **Three tables, not two.** Presented as an even trade and taken knowingly:
   his shape guarantees *structurally* that a summary cannot exist without its
   research piece, and leaves room for summary-only fields later (a rating, a
   re-read date). The cost — `source_id` pointing at two possible tables — is
   handled by §Row-level rules 5–7 rather than by prose.
5. **Claude writes what a document says; Yaron writes what it means.** Every
   factual line in a summary carries a page number, so any of it can be checked
   against the original in seconds. `**What it means for me:**` starts as
   `TODO` and is his. This is the P1 line for `notes/picks/`: a compression of
   someone else's document is not his reasoning; what to do about it is.
6. **Dropped research files get renamed**, the same mechanism as screenshots.

## The tables

### `data/evidence.csv` — unchanged

No new columns. A summary is **not** a row here.

### `data/evidence_summaries.csv` — new

One row per summary piece. A 13D report covering AI, crypto and gold produces
three rows, one per theme.

| Column | Rule |
|---|---|
| `id` | `sNNNN`, unique |
| `evidence_id` | **required** — the document it summarises. This is the whole reason the table exists |
| `location` | repo-relative path to the markdown, always under `notes/picks/` |
| `added` | ISO date |

Deliberately **not** here: which cluster it is for. That is the link's job —
storing it in both places is the duplication P7 forbids, and Yaron's first
sketch carried it before this was walked through.

### `data/evidence_links.csv` — renamed from `claim_evidence.csv`

| Column | Rule |
|---|---|
| `target_type` | `claim` \| `cluster` \| `sector` \| `instrument` — vocab |
| `target_id` | must exist in that type's table |
| `source_type` | `document` \| `summary` — vocab |
| `source_id` | must exist in that type's table, and its prefix must match |
| `page` | digits ≥ 1, nullable — unchanged |
| `why` | free text, nullable — unchanged |
| `added` | ISO date |

Key: `(target_type, target_id, source_type, source_id, page)`.

### Row-level rules

1. `target_type` and `source_type` must be known vocabulary values.
2. `target_id` must exist in the table `target_type` names — and **only** that
   table.
3. `source_id` must exist in the table `source_type` names.
4. A summary's `evidence_id` must be a real document.
5. **The id prefix must agree with the type.** `source_type=summary` with
   `source_id=e0007` is refused: `e` means document. This is what turns the
   classic polymorphic-pointer failure — right id, wrong table — from
   *unlikely* into *impossible*.
6. **One registry, no branching.** A single mapping is the only place that
   knows which type reads which table:

   ```python
   SOURCES = {"document": (load_evidence,  "e"),
              "summary":  (load_summaries, "s")}
   TARGETS = {"claim":      (claims.load_claims,        "id"),
              "cluster":    (clusters.load,             "cluster_id"),
              "sector":     (sectors.load,              "sector_id"),
              "instrument": (instruments.load,          "instrument_id")}
   ```

   Every guard and every read resolves through it. Adding a fifth target type
   is one entry, and nothing else changes.
7. **A test asserts the vocabularies and the registry agree.** A value in
   `ops/vocab/target_type` with no registry entry fails the suite. This is the
   guard against the failure mode nobody can remember to prevent: a new kind
   added to a vocab file, with no lookup written for it, silently unchecked.
8. Two summaries may not point at the same markdown file — the same
   one-row-per-thing promise `check_one_row_per_document()` already makes.
9. A private research filename must match `YYYY-MM-DD-source-topic.ext`, with
   `source` drawn from `ops/vocab/origin`, and must remain a bare filename —
   `notes/research/` is gitignored and a committed table must never carry a
   path to a paid PDF.

## `src/evidence.py`

```python
add_summary(evidence_id, location) -> "s0001"
link(target_type, target_id, source_type, source_id, page=None, why=None)
for_target("cluster", "Technology")   # documents + summaries, joined, with page/why
for_source("summary", "s0001")        # everything resting on one piece
inherited("instrument", "ASML")       # -> {("cluster", "AI Infrastructure"): 2,
                                      #     ("cluster", "Technology"): 1}
                                      # keyed by the PAIR: seven names are both
                                      # a cluster id and a sector id
unlinked()                            # documents AND summaries never attached
unevidenced_claims()                  # unchanged meaning: claim-type links only
```

`link()` keeps refusing a target or source that does not exist. `for_target()`
is the only join any caller performs — nothing hand-joins, so no **table
lookup** branches on a type outside the registry. Two things legitimately do
branch, because neither is a table lookup: `ancestors()`, on what a target type
inherits from, and how a `source_type` renders — a third one would otherwise read
back silently as a document.

**Inherited evidence does not count as evidence.** A claim whose only backing
is a theme-wide report still appears in `unevidenced_claims()`. A report about
technology is not research on ASML, and letting it silence the gap would make
the one report cover twelve unexamined bets.

## What changes elsewhere

- `ops/hooks/pre-commit` — validates the renamed link table and the new
  summaries table, reading both from the index as it already does.
- `.claude/skills/note/SKILL.md` — the filing flow, the rename step, and the
  preview must show summary rows and link rows.
- `.claude/skills/show/SKILL.md` — the pointer line.
- `src/holding.py` — `summary()` and `cluster_summary()` gain their documents.
- `CLAUDE.md`, `REBUILD.md` — the record table and the phase entry.
- `ops/vocab/` — two new files, `target_type` and `source_type`.
- `notes/research/_inbox/` — created, gitignored like the screenshot inbox.

### Migration

Four rows, all `kind=chart`, all already valid:

```
claim_id  evidence_id        ->   target_type target_id source_type source_id
c0003     e0001                   claim       c0003     document    e0001
c0005     e0002                   claim       c0005     document    e0002
c0007     e0003                   claim       c0007     document    e0003
c0008     e0004                   claim       c0008     document    e0004
```

(Verified against the file 2026-08-17. `c0006` is VTI's claim and carries no
document at all — it is the one claim in the store with no chart, deliberately.)

Nothing is lost and nothing is inferred. `data/claim_evidence.csv` is removed
in the same commit that adds `data/evidence_links.csv`; git keeps the old file.

### Risks

- **Two polymorphic pointers in one table** (`target_*` and `source_*`). This
  is the design's weakest joint and was argued through explicitly. Mitigated by
  rules 5–7: prefix agreement, a single registry, and a test that fails when
  the vocab outgrows the registry. Accepted knowingly rather than by default.
- **`instrument` and `claim` overlap** — see Open questions.
- **`notes/picks/` is inside `notes/`**, where P1 forbids machine-authored
  prose. Narrowed here, deliberately: Claude may write the *what it says*
  section with page citations; the *what it means for me* section is Yaron's
  and starts as `TODO`. This is a real narrowing of P1 and belongs in
  `REBUILD.md` §2 alongside the two existing ones.

## Testing

Every rule above gets a test that proves the bad case is refused, not merely
that the good case passes — the discipline that found four real holes in the
evidence store on 2026-08-16.

- each target type resolves to its own table, and only its own
- a target id that exists in a *different* table is refused
- prefix/type mismatch is refused (`source_type=summary`, `source_id=e0007`)
- a summary with a missing or unknown `evidence_id` is refused
- two summaries on one markdown file are refused
- a vocab value with no registry entry fails the suite
- a research filename with a path, or off-pattern, is refused
- the four migrated rows validate unchanged
- the commit guard refuses each of the above from the index, not the worktree

## Out of scope

- **Storing inheritance.** It is computed at read time, permanently.
- **Extra fields on summaries** — rating, re-read date, author. The table makes
  them a one-line addition the day one is wanted.
- **One summary covering several documents.** If three reports produce one
  view, that view is filed as a document in its own right (`kind=note`,
  `storage=repo`) with no parent. Keeps `evidence_summaries.evidence_id`
  single and required.
- **The research-reader skill** that reads a PDF and proposes the summary.
  That is Phase 6 and depends on this landing first.

## Settled — the three that were open

Yaron approved the spec 2026-08-17, accepting each proposal below as written.

1. **`instrument` vs `claim` when both fit.** A `claim` link means *the document
   informed that decision*. An `instrument` link means *the document is about
   the company, and no decision is attached to it*. Both may exist on one
   document without duplication, because they answer different questions — and
   `unevidenced_claims()` counts only the first, so an instrument link can
   never quietly make a bet look researched.
2. **A group's document does not merge into its clusters.**
   `for_target("cluster", "Software")` returns Software's own links only.
   Technology's documents reach it as the pointer line and nothing else — the
   same rule as decision 2, applied one level up.
3. **`why` stays on the link, and stays optional.** A per-cluster summary often
   says it already. Keeping the column costs nothing and removing it later is
   cheaper than adding it back.
