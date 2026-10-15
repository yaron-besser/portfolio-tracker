# Research write-ups

One file per report read. Each condenses what a third party published — their
argument, their figures, their price levels — with page citations back to the
original.

> **These are summaries of other people's work.** Copyright in the underlying
> reports belongs to their publishers. No redistribution rights are claimed
> over that material, and the reports themselves are not in this repository.

## What is NOT here

Anything that read the research against a particular portfolio. Those sections
existed in the originals — *"my portfolio relevance"*, *"what it means for
me"*, *"direct holdings on this list"*, *"what this means for current
positioning"* — and they were the owner's rather than the publisher's. They
have been removed, along with the files that turned out to be substantially
about a book rather than about a report.

What is left is the record of what was read.

## How these join the rest of the record

A write-up is a row in `evidence_summaries.csv`, which points at the document
it came from in `evidence.csv`. `evidence_links.csv` then says what it bears
on — a bet, a theme, a sector, or the market as a whole. A report about
technology is not research about one company inside it, so inheritance is
computed on demand and never stored.

See [docs/data-model.md](../../docs/data-model.md) for the three tables, and
[/evidence](../../.claude/skills/evidence/SKILL.md) for the command that files
one.
