# TICKER — Company Name

<!--
The note holds only what cannot be a cell.

Most of what used to live here is now a row in data/claims.csv, where it can
be sorted, filtered and checked:

    the thesis, in ≤3 sentences   -> the `claim` column
    what would prove me wrong     -> the `wrong_if` column
    when I'll look again          -> the `check_date` column
    how sure I am                 -> the `conviction` column
    the sources                   -> data/evidence.csv, one row per document,
                                     joined to the claim by data/evidence_links.csv
                                     (which carries the page and the why)
    every later observation       -> a `kind=update` row, dated and sourced

The test for where something goes: if you can sort or filter by it, it is a
column. "Every bet I got wrong" and "everything 13D said about TSLA" are
sortable. "Why is the smartest person on the other side selling?" is not —
that is what this file is for.

Write the note with:  /note TICKER
It asks the questions, files the answers, and opens the claim row.
Position size, cost and cluster are NOT written here — they are already in
positions.csv and instruments.csv, and a copy would go stale.
-->

## Bear case

The smartest person on the other side is short, or refusing to buy, because:

___

If you cannot articulate this, you do not yet understand the position. It is
the one section with no shortcut — no data file can derive it for you.

## Catalysts

Specific things you are betting will happen. Without them this is "I like it",
not a trade. Dates where you have them.

- [ ] …
- [ ] …

<!--
Anything below here is yours and unconstrained — this is the part that is
deliberately NOT the same for every holding. Add whatever that holding
actually needs, e.g.:

  ## Why this ETF and not another wrapper
  ## How I'll manage it tactically
  ## What I got wrong last time I owned this

Only `## Bear case` and `## Catalysts` are required.
-->
