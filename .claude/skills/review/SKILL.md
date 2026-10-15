---
name: review
description: Show Yaron a plain-English summary of what changed, so he can check it matches what he asked before anything is pushed. Use when he types /review, asks "what changed?", "what did you do?", "show me the changes", or before any commit or push.
---

# /review — what actually changed

Yaron runs this to see his own repo's changes in language he can judge.
He is a first-year business-analytics student, not a developer. **He cannot
read a diff.** Your job is to translate.

## Step 1 — find the changes

```
git status --short                      # includes untracked (??) files
git add -N .                            # make untracked files visible to diff
git log --oneline origin/main..HEAD     # committed but not on main yet
git diff --stat origin/main             # every line that differs from main
```

**Use `origin/main`, never `@{u}`.** The workflow creates a fresh branch with
no upstream, so `@{u}` fails — and its `||` fallback silently reports
already-pushed commits as new. Verify the baseline exists first
(`git rev-parse --verify origin/main`); if it doesn't, fall back to the
newest `rebuild-phase-*` tag and say which baseline you used.

`git add -N .` is what makes **new** files appear in the diff. Without it
they show only as `??` and get reviewed by filename — the exact thing Step 2
forbids. It stages nothing's content, only the file's existence.

Scope, in order of preference:
1. If arguments were given, use them as the scope (a tag, a commit, a path)
   and say so in the report heading.
2. Otherwise: everything not yet on `origin/main` — uncommitted changes,
   untracked files, **and** commits on this branch. A branch that is already
   pushed but not merged still differs from `origin/main`, so it stays in
   scope until he clicks Merge. That is the point.
3. Only if the branch matches `origin/main` **and** the tree is clean: say so
   in one line and stop.

**Check for an odd repo state first, and say so before anything else** —
these silently corrupt the report:

```
git rev-parse --abbrev-ref HEAD          # "HEAD" means detached
git rev-parse --verify -q HEAD           # empty means no commits yet
ls .git/MERGE_HEAD .git/rebase-merge .git/rebase-apply .git/CHERRY_PICK_HEAD 2>/dev/null
```

If a merge, rebase or cherry-pick is in progress, **stop and tell him** — the
diff contains conflict markers and any summary would be fiction. If HEAD is
detached, say so plainly ("you are not on a branch") before reporting.

## Step 2 — read the actual changes

Read the real diff, not just the file names. For each changed file, know
what changed and why. Never guess from the filename — including for new
files, which need reading in full.

Group by **what it does for him**, not by directory:

- His money data (`data/*.csv`) — positions, trades, claims, stops
- His writing (`notes/`)
- The machinery (`src/`, `tests/`, `ops/`, `.claude/`)
- The plan and docs (`*.md`)

## Step 3 — check it against what he asked

Look back at what he actually requested in this conversation. For each
change, decide honestly:

- **Asked for** — he requested this
- **Needed for it** — not requested, but the requested thing doesn't work
  without it. Say why in one clause.
- **Not asked for** — flag it clearly. This is the most valuable line in
  the whole report. Never bury it.
- **Can't tell** — the request is not in this conversation (a resumed
  session, a compacted context, someone else's commit). **Say this rather
  than guessing.** Never print the all-clear you cannot support.

This judgment rests on conversation memory, which P3 says not to rely on.
So anchor it in files where you can: `REBUILD.md` phases, the plan file, and
recent commit messages are written evidence of what was asked. If the change
traces to none of them and you don't remember the request, it is
**Can't tell**, not **Asked for**.

## Step 4 — write the report

Rules: short, simple words, no jargon. Effect, not mechanism. Say "the file
that lists what you own", not "positions.csv schema". If he asked for two
things, do not write ten bullets.

```
## What changed since your last push

**Your data**
- <plain sentence>. <asked for / needed for X / NOT ASKED FOR>

**Your notes**
- ...

**The machinery**
- ...

## Anything that doesn't match what you asked
- <specific>, or "Nothing — all of it traces back to what you asked for."
- <anything you could not trace, stated as "I can't tell whether you asked
  for X" — never silently omitted>

## Risk if this is wrong
- <one line: what breaks, and is it recoverable>

## To undo
- <the exact command for THIS state — see the undo table in
  docs/github-workflow.md; never paste `git reset --hard` without saying
  what else it destroys>
```

**Undo advice must be state-specific.** `git reset --hard <tag>` also
deletes every uncommitted edit in the tree, including notes he was part-way
through. If uncommitted work exists, say so and prefer
`git reset --soft HEAD~1`. Always mention `git reflog` as the recovery net.

## Step 5 — correctness review

Launch subagents to hunt for real defects before he pushes. Run this for
**any** change, not just code: `CLAUDE.md` and `docs/` are this project's
operating instructions, and a wrong line there drives real-money workflow
decisions with no compiler to catch it.

Scale the lenses to what changed:

| Changed | Lenses |
|---|---|
| `.py`, hooks, settings | correctness · spec-conformance vs REBUILD.md · the guard itself |
| `CLAUDE.md`, `docs/`, skills | what information was LOST vs the old version · does it contradict REBUILD.md or the code · would a fresh session be misled |
| `notes/`, `data/` | does it match `positions.csv` / `transactions.csv` reality |

**Use the `Explore` agent type** — it has no write tools, so REBUILD.md P1
("a subagent may read freely and must never write") is enforced by the
harness rather than by asking politely. **Also state the constraint inside
each subagent's prompt** ("READ-ONLY: do not edit any file; report findings
only"). Belt and braces: if the agent type is ever changed, the prompt still
carries the rule.

Require a concrete failure scenario with specific inputs, then have a
skeptic try to refute each finding. Report only what survives. For "lost
information" claims the skeptic must grep the whole repo before agreeing.

If nothing survives: say "reviewed, nothing found" — never invent findings
to look thorough.

## Hard rules

- **Never push.** This command ends with him deciding.
- Report what is really there. If a test fails, say so with the output. If
  you skipped something, say that.
- If a change turns out not to match what he asked, say it plainly and
  offer the undo command. Do not defend the work.
