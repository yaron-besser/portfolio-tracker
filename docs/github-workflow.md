# How work reaches GitHub

Agreed 2026-08-09. The goal Yaron stated: **everything backed up on GitHub,
but checked by him before it becomes final.**

A branch gives both. Pushing a branch backs the work up immediately; `main`
only changes when he clicks Merge.

Repo: <https://github.com/yaron-besser/portfolio-tracker>
(The workflow below is the one this project used: a branch, a review, a
pull request, and a merge the owner performs by hand. Nothing reaches the
default branch any other way.)

---

## The loop

1. **Work happens on a branch**, never on `main`.
   `git checkout -b phase-2b-screenshots`
   `ops/hooks/pre-commit` refuses a commit on `main`, so this is enforced,
   not remembered.
2. **Commit** — the pre-commit guard runs automatically. Commits are local
   and cheap; nothing has left the machine yet.
3. **`/review` before pushing** — plain-English summary + a read-only
   subagent defect hunt. Yaron reads it. Findings get fixed *before* the push,
   not after. **The push is the review boundary**, not the commit.
4. **Push the branch** — `git push -u origin <branch>`. Claude Code asks
   Yaron to approve the push (`ops/hooks/claude-pretooluse.sh`). Work is now
   backed up; `main` is untouched.
5. **Open a Pull Request** and give Yaron the link. He reviews the
   colour-coded diff in the browser, on any device, whenever he wants.
6. **He clicks Merge.** Only then is it official.
7. Tag the phase on `main` afterwards (`rebuild-phase-2b`) and push the tag.

**Claude never merges.** Step 6 is his, always.

## Undo, at every stage

**Read the warning column before running any of these.** `--hard` and
`clean -fd` destroy work that is not committed anywhere, including edits in
`notes/` you were part-way through.

| Stage | Undo | Warning |
|---|---|---|
| Before commit — throw away edits | `git restore --staged --worktree .` | Does **not** remove new files |
| Before commit — also remove new files | `git clean -nd` to preview, then `git clean -fd` | `-n` lists, `-f` deletes. Unrecoverable — nothing in git yet |
| Undo the last commit, keep the work | `git reset --soft HEAD~1` | Safest. Files untouched, just un-committed |
| Reset the branch to a save-point | `git reset --hard <tag>` | **Destroys every commit since that tag AND all uncommitted edits.** Commit or stash first |
| Pushed, not merged | Close the PR on GitHub, or `git push origin --delete <branch>` | `main` never saw it — nothing to undo there |
| Merged | `git revert -m 1 <merge-sha>` | The `-m 1` is required on a merge commit; plain `git revert` errors out |

**The recovery net:** `git reflog` lists every commit your repo has pointed
at for the last ~90 days, including ones a `--hard` reset "deleted". If a
reset went wrong, `git reflog` then `git reset --hard <sha-from-the-list>`
usually gets it back. It cannot recover work that was never committed.

---

## One-time setting on GitHub (Yaron does this)

Makes it *impossible* to write to `main` without a PR — including by
accident, including by Claude.

1. Open <https://github.com/yaron-besser/portfolio-tracker/settings/rules>
2. **Add branch ruleset** (or "Add rule" on older UI)
3. Branch name pattern: `main`
4. Tick **Require a pull request before merging**
5. Untick "Require approvals" — it is a solo repo; approving your own PR is
   otherwise impossible
6. Leave everything else off. Save.

Optional, worth it: on the same screen tick **Require status checks** only
once tests run in CI. There is no CI right now (the Actions workflow was
parked in Phase 2 Session 0), so skip it for now.

⚠️ After this is on, `git push origin main` will be rejected. That is the
point. Everything goes through a branch.

## When branches stack up

They have. Work has been done on a branch, left unmerged, and the next branch
started on top of it — so several branches exist and each contains the one
before. That is fine, and it is **not** several PRs.

```bash
git branch --no-merged main                  # what has not reached main
git merge-base --is-ancestor <older> <newer> && echo "already included"
```

**As of 2026-09-29** five branches are unmerged — `ai-notes`, `de-bet`,
`notes-agriculture`, `rsp-closed` and `cleanup-structure` — and the last one
**contains all four others**. One PR from `cleanup-structure` merges the lot;
the other four then close by themselves.

Rule of thumb: find the newest branch, check the others are ancestors of it,
and open **one** PR from it. Never merge the middle of a stack first — that
splits the history into a shape nobody can read afterwards.

Of the 24 local branches, most are already merged and just never deleted.
`git branch --merged main` lists those; deleting them is optional tidying and
changes nothing about the record.

## Note for later

`gh` (the GitHub command-line tool) is **not installed** on this machine —
verified again 2026-09-29 — so PRs are opened in the browser. Installing it
(`brew install gh`, then `gh auth login`) would let Claude open the PR and hand
over a link directly. Small quality-of-life win, not required.

Depending on the session, Claude may have GitHub access through a connected
integration instead, which can open the PR without `gh`. **It still never
merges** — that is always yours.
