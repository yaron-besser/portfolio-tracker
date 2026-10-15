# Sub-clusters — design

**Decided with Yaron, 2026-08-16.** Adds one column to `data/clusters.csv` so a
cluster can sit inside a bigger group, and creates two groups: **Technology**
and **Commodities**.

## Why

The thesis rewrite reached AI Infrastructure ($16,948) and the first question a
cluster note has to answer — *why does this theme get its own slot rather than
being folded into Technology* — turned out to have the answer "it doesn't":

> "AI can be part of tech for sure."
>
> "Technology, but we should also do sub-clusters as we got separation inside,
> so it means we're going to plan another change on the schema before we
> continue."

AI Infrastructure, Software and Technology (broad) are three rows describing
one bet at three altitudes — **$29,061, 23.4% of NAV**, the largest thing in the
book after cash. Nothing in the table said they were related, so no note could
answer "why do I own technology at all", and the narrower question was about to
be answered three times in three separate notes.

The same shape exists in hard assets: Metals, Agriculture and Critical Minerals
— **$7,557, 6.1% of NAV** — which his own 2026-05-27 commitment already treats
as one tilt while the table treats them as three unrelated themes.

A flat merge was considered and rejected by Yaron in the same conversation: it
would have deleted the separation he actually wants to keep. Chips, software
and broad beta fail for different reasons and he wants to be able to say so.

## Decisions taken

| Question | Decision | Reasoning |
|---|---|---|
| Where does the grouping live? | **A `parent` column on `data/clusters.csv`** | Considered and rejected: a `sub_cluster` column on `instruments.csv` — Yaron's own first instinct, and the more common design. It writes the tree once per *instrument*: "Technology" 12 times instead of 3, and 22 instrument rows edited in total. Two rows can then disagree about where a group sits with nothing to catch it (P7), and renaming a group is 22 edits instead of 1 |
| Precedent | **`data/sectors.csv`** | Already does exactly this — `Tech - Semiconductors` has parent `Technology`. Same repo, proven pattern, one nullable text column |
| How deep? | **One level, enforced** | A group cannot sit inside a group. Two levels answer every question asked so far; arbitrary depth makes both the weight rollup and `notes.status()` ordering unbounded for a case nobody has named. YAGNI |
| Do group rows hold positions directly? | **No** | A holding always points at a leaf cluster. Otherwise a group's money is part rolled-up and part direct, and no weight can be read without first knowing which |
| Does AI Infrastructure survive? | **Yes — as a child of Technology** | The merge Yaron asked for is achieved by grouping, not deleting. `instruments.csv` is untouched, and every note, claim and level already written stays attached to the cluster it was written about |
| What `layer` is a group? | **New vocab value `group`** in `ops/vocab/cluster_layer` | Technology holds one `thematic` child and one `sector-breadth` child, so it is neither. Reusing `thematic` would make `layer` mean two different things |
| Do both levels get a note? | **Yes** — Yaron's call | The group note answers "why do I own technology at all / what would end the whole thing". Each child answers the narrower question. The writing queue goes from 6 notes to 8, and each one gets easier because it is no longer trying to answer both |
| Are weights stored? | **No — unchanged** | `clusters.weights()` still derives everything from `positions.csv`. A group's money is the sum of its children, computed on read |
| Which clusters get grouped now? | **Only these six** | The other twelve clusters are looked at when the rewrite reaches them, with the holdings in front of him. The capability is general; the application is deliberate |

## The column

`data/clusters.csv` — `parent` is inserted second, so the tree reads down the
left of the file:

```
cluster_id, parent, layer, in_equity_base, note_ref, status, added
```

| column | rule |
|---|---|
| `parent` | text, nullable. Blank means top-level |

### Row-level rules — 1–5 enforced in `clusters.validate()`

The guard already calls `clusters.load()` on every commit, so validation is the
enforcement point for every rule that can be checked inside one file. Rule 6
needs two files and lives in the guard.

1. **`parent` must name a cluster that exists** in the same file. Same foreign
   key discipline as every other link in the system.
2. **No self-parenting** — a row may not name itself.
3. **One level only** — if a row has a `parent`, that parent's own `parent`
   must be blank. This also makes cycles impossible.
4. **A group and its children agree on `in_equity_base`.** Otherwise "X% of the
   equity book" has two different denominators depending on which row you read.

And two rules about `layer`:

5. **A row with children must have `layer=group`.** A theme that has become a
   container stops being a theme, and the label should say so.

   The mirror rule — *a group must have children* — was written into the first
   draft and removed: it makes the table unusable. A group is necessarily empty
   for the moment between being created and having anything put inside it, so
   the rule would refuse the only order in which a group can be built. An empty
   group is reported by `clusters.drift()`, which already exists to name
   clusters that hold nothing. Visible, never blocking — the same call the
   repo makes for every other gap.
6. **A `layer=group` row holds no positions directly** — this needs
   `instruments.csv` as well, so it is enforced in the commit guard where both
   tables are already loaded, not in `clusters.validate()`, which reads one file.

## The data change

Two new rows, six rows given a parent, **nothing in `instruments.csv`**:

```
cluster_id            parent        layer            in_equity_base
──────────────────────────────────────────────────────────────────
Technology            (blank)       group            True     ← NEW
Commodities           (blank)       group            True     ← NEW
AI Infrastructure     Technology    thematic         True
Software              Technology    thematic         True
Technology (broad)    Technology    sector-breadth   True
Metals                Commodities   thematic         True
Agriculture           Commodities   thematic         True
Critical Minerals     Commodities   thematic         True
```

## What changes in the code

| File | Change |
|---|---|
| `src/clusters.py` | `parent` added to `COLUMNS` and the schema; rules 1–5 in `validate()`; `weights()` gains `parent` and `is_group` columns and rolls children up into their group row; new `children(cluster_id)` helper; `add_cluster()` accepts `parent` |
| `src/notes.py` | `status()` lists group rows and orders **group → the clusters inside it → the holdings inside those**, extending today's cluster-before-its-holdings rule by one level |
| `src/holding.py` | `cluster_summary()` reports a group's children and its rolled-up weight; for a child, names its parent. `what_is()` needs no change — a group is a row in `clusters.csv` |
| `ops/hooks/pre-commit` | Rule 6 — a group row holds no positions — added where `instruments` and `clusters` are already both loaded |
| `ops/vocab/cluster_layer` | `group` appended |
| `notes/templates/cluster_thesis.md` | A line saying what a *group* note answers that a child note does not |
| `.claude/skills/note/SKILL.md`, `show/SKILL.md` | `/note <group>` writes the wider note; `/show` reads a group back with its children |

### The one arithmetic trap

`weights()` returns one row per cluster today, and callers are free to sum the
`market_value` column. Once group rows exist and carry their children's money,
that sum **over-counts by the whole of every grouped cluster** — $166,648
against a real $124,199 on today's book, and 2× only in the limit where every
cluster sits in a group. So every group row is marked `is_group=True`, the
docstring states that leaves alone sum to NAV, and a test asserts it.

Ordering also changes: `weights()` currently sorts by money alone. It will sort
so each group is immediately followed by its own children, groups ranked by
their rolled-up total and ungrouped clusters interleaved by their own money —
the same shape `notes.status()` already produces.

## What this does not change

- **Sector exposure.** `exposure.by_sector()` reads `sector`, a different axis
  entirely. QQQ is Core US by intent and 50% technology by fact; that stays true
  and unrelated.
- `positions.csv`, `instruments.csv`, `claims.csv`, `levels.csv` — untouched.
- **No caps, no targets, no limits.** There are none, and grouping does not
  create one. A 21.7% Technology group is a measurement, not a breach.

## Testing

The suite is 364 today and must stay green. New tests:

- one per rule 1–6, each proving the bad shape is **refused**, not repaired
- rollup arithmetic: a group's `market_value` equals the sum of its children,
  and the leaf rows alone still sum to NAV
- `weights()` ordering: group immediately followed by its children
- `notes.status()` ordering: group → child cluster → holdings
- a cluster with no parent behaves exactly as it does today (the twelve
  ungrouped clusters are the regression surface)
