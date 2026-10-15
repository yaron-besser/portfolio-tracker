# Evidence store — design

**Decided with Yaron, 2026-08-16.** Replaces the `;`-separated
`claims.evidence` column with two tables and a new module.

## Why

One Fundstrat report covers a dozen stocks. Today its path is pasted into
every claim it backs, so the same fact is stored many times — and correcting
the filename means correcting it everywhere. Yaron's words:

> "Multiple stocks could refer to the same research… then we're gonna have a
> duplication of a lot of almost identical stuff… Maybe do a table which is
> called evidence, which only stores the evidence, and then another table
> which connects the stock with the evidence."

`evidence` is the only multi-valued fact in the schema not modelled as a side
table. `claim_setups.csv` is the shape to copy.

Two things the column cannot hold and the tables can:

- **The kind of each item** — a chart is not a research report is not an article.
- **A page citation.** The Phase 6 research-reader must return claims *with page
  citations* (REBUILD §6, "page citations are mandatory"). There is nowhere to
  put them today.

## Decisions taken

| Question | Decision | Reasoning |
|---|---|---|
| What does evidence link to? | **Rows in `claims.csv`** | That table holds `claim`, `watch` *and* `update` rows, so it already covers bets, things being watched, and thoughts on stocks he does not own. Linking to instruments instead would lose which evidence actually drove a decision, polluting "do Fundstrat calls work for me?" with names he merely read about |
| What can an item be? | **Committed file, URL, or private file** | A paid PDF cannot be committed (`notes/research/` is gitignored, `*.pdf` blocked). Recording it as private — filename only — is honest; refusing it breaks the trail from note back to report |
| Where do `source` and `published` live? | **On the document, not the link** | They are properties of the report. On the link they would repeat once per connection — the duplication this change exists to remove |
| Where do `page` and `why` live? | **On the link** | The same report backs GRNY on p.12 and RSP on p.31. Different bets, different pages, same document |
| Two dates? | **Yes, both** | `published` (on the document) is when the report is dated; `added` (on the link) is when he attached it. The gap between reading and acting is a fact about him — it is the same insight as `follow_through()` |
| Old `evidence` column | **Removed, four rows migrated** | P7: one source of truth per fact. Keeping both guarantees drift |
| Where does the code live? | **New `src/evidence.py`** | `claims.py` is already 894 lines, the largest module. A document is its own thing that happens to link to claims |
| SQLite? | **No** | Already rejected 2026-08-08 (REBUILD §7). CSV + pandera is working |

## The tables

### `data/evidence.csv` — one row per document

| column | type | rule |
|---|---|---|
| `id` | text | unique, shaped `e0001` |
| `kind` | text | vocab `ops/vocab/evidence_kind`: chart · research · article · note |
| `title` | text | required, min length 1 |
| `source` | text | vocab `ops/vocab/origin` — **reused, not duplicated** |
| `published` | text | real ISO date, nullable, **not in the future** |
| `storage` | text | vocab `ops/vocab/evidence_storage`: repo · link · private |
| `location` | text | required; validated per `storage` (below) |
| `added` | text | real ISO date, required |

### `data/claim_evidence.csv` — one row per connection

| column | type | rule |
|---|---|---|
| `claim_id` | text | required, must exist in `claims.csv` |
| `evidence_id` | text | required, must exist in `evidence.csv` |
| `page` | whole number | stored as text, checked to be digits only and `>= 1`, then coerced to `int` for use. Nullable. `"12a"`, `"-3"`, `"0"`, `"1.5"` all refused |
| `why` | text | nullable, one line |
| `added` | text | real ISO date, required |

### Row-level rules

- **Key is `(claim_id, evidence_id, page)`** — one report may back one claim at
  two different pages, without returning to a delimited string.
- `storage=repo` → the file must exist at write time (today's `check_refs`).
- `storage=link` → must start `http://` or `https://`.
- `storage=private` → **a bare filename, no `/`.** Private files live in
  `notes/research/`, which is gitignored, and the module supplies that prefix —
  the row stores only the name. **No existence check**: the file may sit on
  another machine, and the row must stay valid there.
  *(An earlier draft said "refused if it resolves inside the repo". That was
  wrong and self-contradictory: `notes/research/` IS inside the repo — being
  gitignored is exactly what makes it the safe home. Storing a bare filename
  achieves the real goal, which is that no committed path can ever name a paid
  PDF.)*
- `published` must not be later than `added`.

## `src/evidence.py`

```python
add_evidence(kind=, title=, source=, published=, storage=, location=) -> str
link(claim_id=, evidence_id=, page=, why=) -> None
for_claim(claim_id) -> DataFrame      # documents behind one row, with page and why
claims_for(evidence_id) -> DataFrame  # every row resting on one document
unlinked() -> DataFrame               # filed but never used
load_evidence() / load_links()        # read + validate
check_links() -> None                 # both foreign keys, run by the commit guard
```

`unevidenced_claims()` **moves here from `claims.py`** and is rewritten against
the link table. It is the only function whose behaviour changes rather than its
location.

**No delete function.** Nothing in this system deletes; an unused document is
answered by `unlinked()`, not by removal (P4).

Two questions that become answerable and are not today:

- `claims_for()` — "this report drove four of my bets."
- `unlinked()` — research filed and never acted on. A real measure of how
  research is used, invisible today.

## What changes elsewhere

| File | Change |
|---|---|
| `src/claims.py` | drop `evidence` from `COLUMNS` and `_schema()`; delete `unevidenced_claims()`. **Keep `split_paths`** — still used by `note_ref` |
| `src/holding.py` | `summary()` reads the link table, so `/show` prints page and why rather than a bare path |
| `ops/hooks/pre-commit` | validate both tables; run `check_links()` |
| `.claude/skills/note`, `show`, `review` | teach the new shape |
| `ops/vocab/evidence_kind`, `evidence_storage` | new |
| `tests/test_evidence.py` | new |

### Migration

Four claims carry evidence, all charts, all `storage=repo`:

```
c0003 IWM  -> e0001 -> link c0003
c0005 QQQ  -> e0002 -> link c0005
c0007 RSP  -> e0003 -> link c0007
c0008 GRNY -> e0004 -> link c0008
```

A one-off script, **deleted after it runs** — not left as a module nobody calls.

### Risks

1. **The commit guard can fail silently.** If it does not really check the new
   tables, a broken link reaches git and nothing complains. P2: silence is a
   failure state. Verified by deliberately committing a link to a claim that
   does not exist and confirming the block.
2. **Removing a column changes the file's shape**, and `check_append_only()`
   compares the committed copy against the new one. It already tolerates older
   schemas on read, but no column has ever been *removed* before. Tested, not
   assumed.

## Testing

Test-first, per repo conventions. Suite is green at 322 and must stay green;
**no test is weakened to pass.**

Every constraint gets a test that proves bad data is *refused*:

- vocab violations on `kind`, `source`, `storage`
- `page` of `"12a"`, `"-3"`, `"0"`
- `2026-02-30`; a future `published`; `published` later than `added`
- the `(claim_id, evidence_id, page)` key, twice over
- both foreign keys
- `storage=repo` pointing at a missing file
- `storage=link` that is not a URL
- `storage=private` containing `/` (must be a bare filename)
- `unlinked()`, `for_claim()`, `claims_for()` in both directions

Three that matter most:

1. **The hook itself blocks a broken link** — not merely that the check
   function returns an error.
2. **The migration is lossless** — all four charts still reachable from their
   claims, verified against the old column before it is dropped.
3. **`check_append_only()` survives the column removal.**

## Out of scope

- Linking evidence to instruments as well as claims. Considered and dropped
  (YAGNI): `/show TICKER` already reaches evidence through that ticker's rows.
- A research-reader that extracts claims and page citations from a PDF —
  REBUILD Phase 6. This design gives it somewhere to write.
