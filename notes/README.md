# The journal

Everything in this folder is **demo text**. It was written to exercise this
project's note-shape rules against a generated book — see the repository
README for what is real here and what is not.

## The layout

| | |
|---|---|
| `positions/<TICKER>.md` | Why a holding is held, and what would make that wrong |
| `positions/closed/` | A position that has been exited, kept rather than deleted |
| `positions/watchlist/` | A name being considered, written before any money moves |
| `clusters/<theme>.md` | Why a whole theme exists, and what would end it |
| `picks/` | Write-ups of third-party research |
| `templates/` | The starting shapes |
| `macro/` | A template for a view on the whole market |

## The two rules a commit will not get past

**Every note in `positions/` must have `## Bear case` and `## Catalysts`.**
**Every note in `clusters/` must have `## Why this cluster exists` and
`## What would end it`.**

`closed/` and `watchlist/` are deliberately exempt. A finished trade and a
name not yet bought both belong somewhere findable, and neither has a live
position to bear-case — demanding the headings there would mean inventing
reasoning to satisfy a checker.

Anything below the required headings is free. That part is meant to differ
from one note to the next.

## What does not go in a note

Any number the code can compute. Position size, market value, weight and
multiples all rot the moment they are typed; `clusters.weights()` and
`/show` print them live instead. A dated fact — what was paid, on what day —
is permanent and belongs here.
