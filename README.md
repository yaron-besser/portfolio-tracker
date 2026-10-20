# Portfolio Tracker

[![tests](https://github.com/yaron-besser/portfolio-tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/yaron-besser/portfolio-tracker/actions/workflows/tests.yml)

**[→ Open the portfolio page](https://yaron-besser.github.io/portfolio-tracker/)** -
the real thing, built by this repository from the demo book in it. No install.

A personal investment record that stores the *reasoning* behind a portfolio,
not just its contents, and then refuses to let that record go quietly wrong.

A broker already tells you what you own. Nothing tells you what you were
thinking three months ago, whether you did what you wrote down, or whether the
bets you were most confident about are the ones that worked. This project is
the database for that second question, built around one constraint: **every
number a reader sees is either computed from the data or refused.**

---

## What is real here, and what is not

This repository is a working copy of a private project. The code is the real
thing; the portfolio in it is invented.

| | |
|---|---|
| The code, schemas, guards and tests | **Real**, and they run against the data in this repository |
| Fund sector breakdowns and holdings | **Real** — each issuer's own published file, with its own `as_of` date |
| The research write-ups in `notes/picks/` | **Real** notes on real third-party reports |
| **The book** - positions, trades, account value, bets, price levels, journal | **Generated** by [`ops/seed_demo_book.py`](ops/seed_demo_book.py). There is no real portfolio behind it |

The demo book is reproducible rather than asserted. Delete every table the
generator owns, run it again, and you get the same book back byte for byte:

```bash
venv/bin/python ops/seed_demo_book.py          # rebuild it
venv/bin/python ops/seed_demo_book.py --check  # validate it the way a commit does
```

Prices follow a seeded random walk. No real instrument ever traded at them.

---

## Running it

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt

venv/bin/python ops/build_page.py       # build the portfolio page and open it
venv/bin/python ops/counts.py           # every table, and what is incomplete
venv/bin/python -m pytest tests/ -q     # the suite
```

`ops/build_page.py` writes one self-contained HTML file — no server, no CDN, no
web fonts, nothing loaded from the internet. It reads; it never writes to
`data/`, and a test enforces that. The copy at
[`docs/index.html`](docs/index.html) is the same file, which is how the live
link above works.

---

## The ideas worth a reader's time

**Four ways a table is protected, not one.** Every data file is described by a
[pandera](https://pandera.readthedocs.io/) schema, every fixed-vocabulary field
reads its allowed values from a one-value-per-line file in
[`ops/vocab/`](ops/vocab/), every foreign key has a `check_known()`, and
[`ops/hooks/pre-commit`](ops/hooks/pre-commit) runs all of it — **against the
staged copy of the file, not the working tree.** A bad row cannot be committed
even if it has already been written to disk.

**The broker tables are append-only, and the guard compares against `HEAD`.**
History cannot be rewritten to make today's answer tidier, because the broker's
own window for re-fetching it is finite and the data is not reconstructible.

**No number in prose that the code can compute.** The single most common defect
found in this project's own audit was a written-down figure that had gone stale
— ten of sixteen row counts in one document were wrong at the same time. The
rule now is to name the command instead: `ops/counts.py`.

**Intent and exposure are different questions, and nothing makes one number
answer both.** A theme says *why something was bought*. Look-through exposure
says *what you are actually exposed to* once you see inside the funds. A
technology fund bought as a core holding is both, and the page shows both.

**Every percentage says which base it is of.** Including cash, excluding cash,
and equity only are three different answers to "how big is this position", so
the page computes all three rather than choosing one.

**Point-in-time columns are written once and never updated.** What a position
was worth when a decision was made, and what the market looked like that day,
are part of the decision — not something to recompute later into a number that
makes the past look better than it was.

**The journal has a shape the commit hook enforces.** A position note must
carry `## Bear case` and `## Catalysts`; a theme note must carry
`## Why this cluster exists` and `## What would end it`. A blank section looks
identical whether it was decided or forgotten, so `TODO` is the only permitted
placeholder and the writing queue reports it.

**Gaps are reported, never blocking.** An incomplete instrument, an unanswered
bet, a price level that cannot be checked — all of them show up in
`ops/counts.py` and none of them stops a trade being recorded.

---

## How it is put together

| | |
|---|---|
| `data/*.csv` | The tables. Plain CSV on purpose: readable, diffable, and reviewable in a pull request |
| `src/` | 17 modules, ~13,700 lines. Pure functions, file IO at the edges |
| `ops/` | Scripts and the guards that run themselves |
| `tests/` | ~21,500 lines across 27 files. Every test name is a sentence about what went wrong once |
| `notes/` | The journal — see [notes/README.md](notes/README.md) |
| `.claude/skills/` | The commands that read and write the record, below |

The test suite is larger than the source it tests, and the docstrings explain
*why* a test exists rather than what it asserts. Most of them name the defect
that caused them to be written.

### The commands

An AI agent operates this record under the rules in [CLAUDE.md](CLAUDE.md).
Each command previews everything and writes only after confirmation.

| | |
|---|---|
| [`/note`](.claude/skills/note/SKILL.md) | Record a thought, a plan, a watch, or a full thesis |
| [`/show`](.claude/skills/show/SKILL.md) | Read it back. Writes nothing, ever |
| [`/evidence`](.claude/skills/evidence/SKILL.md) | File a document once, attach it to everything it bears on |
| [`/level`](.claude/skills/level/SKILL.md) | Record what happened when a price was reached |
| [`/resolve`](.claude/skills/resolve/SKILL.md) | Record a verdict when a bet reaches its check date |
| [`/review`](.claude/skills/review/SKILL.md) | Plain-English summary of what changed, before any push |
| [`/market`](.claude/skills/market/SKILL.md) | Refresh what the market says. Asks before every write |

### Reference

| | |
|---|---|
| [docs/data-model.md](docs/data-model.md) | Every table, every column, every foreign key, and how each is enforced |
| [docs/data-conventions.md](docs/data-conventions.md) | How each file merges, the sector taxonomy, ETFs, retention |
| [docs/data-model-worked-example.md](docs/data-model-worked-example.md) | One decision traced end to end, from a sentence to the rows it became |
| [docs/data-model-open-questions.md](docs/data-model-open-questions.md) | Where the shape runs out — the design debt, written down |
| [docs/journal-workflows.md](docs/journal-workflows.md) | What a trade has to leave behind |
| [docs/ibkr-flex-setup.md](docs/ibkr-flex-setup.md) | Getting the broker feed working |
| [docs/github-workflow.md](docs/github-workflow.md) | Branch, review, pull request, merge |
| [_archive/design-record.md](_archive/design-record.md) | The architectural decisions, with the reasoning kept |

Design specs, each one a numbered set of decisions with the argument for each:
[the portfolio page](docs/superpowers/specs/2026-10-01-portfolio-page-design.md),
[sub-clusters](docs/superpowers/specs/2026-08-16-sub-clusters-design.md),
[the evidence store](docs/superpowers/specs/2026-08-16-evidence-store-design.md),
[evidence targets](docs/superpowers/specs/2026-08-17-evidence-targets-design.md).

---

## Licence and scope

The **source code** in `src/`, `ops/`, `tests/` and `.claude/` is MIT licensed —
see [LICENSE](LICENSE).

Two things are not covered by it. The prose in `notes/` is not offered for
reuse. And the research write-ups under `notes/picks/` condense reports
published by third parties: copyright in that underlying material belongs to
its publishers, and no redistribution rights are claimed over it.

---

**A snapshot.** This repository is a point-in-time copy taken on 2026-10-10 and
is not updated. The page will report its prices as ageing, because they are —
that is the behaviour working, not a bug to fix.
