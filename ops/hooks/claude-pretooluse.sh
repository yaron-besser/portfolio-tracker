#!/bin/sh
# Claude Code PreToolUse hook — binds THE AGENT (_archive/design-record.md §5.7).
# Reads the tool call as JSON on stdin, decides whether it needs Yaron.
#
# Two gates, both agreed 2026-08-09:
#   git push          -> ASK Yaron (he reviews before anything becomes final)
#   git commit on main-> DENY (work belongs on a branch; docs/github-workflow.md)
#
# "ask" is used rather than a bare message because a PreToolUse hook that only
# emits additionalContext is ADVISORY — the push runs anyway and the reminder
# lands next to its output, too late to matter (verified against the hooks docs
# 2026-08-09). Advisory text cannot enforce "checked before it becomes final".
#
# Never exits non-zero: a broken gate must not wedge the session.

CMD="$(jq -r '.tool_input.command // empty' 2>/dev/null)" || exit 0
[ -n "$CMD" ] || exit 0

# Find the subcommand that follows the word `git`, skipping its flags. This
# catches `git -C dir push` and `git --no-pager commit`, which a test for the
# literal string "git push" would miss.
#
# ⚠️ It does NOT tell position from content: `echo git push` also matches,
# because the awk scans every field of every line for the word `git` rather
# than only the command position. Verified 2026-09-29 — an earlier version of
# this comment claimed the opposite, and a comment that vouches for a property
# the code lacks is worse than no comment.
#
# Deliberately left as is. The failure mode is one extra approval prompt on a
# line that merely mentions git; the opposite error would be a push reaching
# GitHub without Yaron seeing it. Over-asking is the safe direction, and
# parsing shell properly here would mean writing a shell parser.
SUB="$(printf '%s' "$CMD" | tr '\n;|&' '\012\012\012\012' |
       awk '{for(i=1;i<=NF;i++) if($i=="git"){
               for(j=i+1;j<=NF;j++){
                 if($j ~ /^-/){ if($j=="-C"||$j=="--git-dir"||$j=="--work-tree") j++; continue }
                 print $j; break } } }')"

case " $SUB " in
*" push "*)   ACTION=push ;;
*" commit "*) ACTION=commit ;;
*)            exit 0 ;;
esac

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
# No git repo -> nothing to gate. Stay silent rather than reporting a clean
# state we cannot actually verify (P2: dead and quiet must not look alike).
[ -n "$ROOT" ] || exit 0

BRANCH="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)"

case "$ACTION" in
push)
    # Count what the push would actually send, not what happens to be dirty.
    AHEAD="$(git -C "$ROOT" rev-list --count "origin/${BRANCH}..HEAD" 2>/dev/null)"
    [ -n "$AHEAD" ] || AHEAD="$(git -C "$ROOT" rev-list --count "origin/main..HEAD" 2>/dev/null)"
    [ -n "$AHEAD" ] || AHEAD="?"
    jq -n --arg a "$AHEAD" --arg b "$BRANCH" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "ask",
        permissionDecisionReason: ("Pushing \($a) commit(s) from branch \($b). Yaron asked (2026-08-09) to see changes before they become final: run /review and show him the summary first."),
        additionalContext: "Run the /review skill and show Yaron the plain-English summary before pushing. Never merge to main — he does that on GitHub."
      }
    }'
    ;;
commit)
    if [ "$BRANCH" = "main" ]; then
        jq -n '{
          hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: "Committing directly to main. Work goes on a branch, then a PR Yaron merges (docs/github-workflow.md). Create one first: git checkout -b <name>"
          }
        }'
    fi
    ;;
esac
exit 0
