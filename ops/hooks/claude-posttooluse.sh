#!/bin/sh
# Claude Code PostToolUse hook — runs the suite after a Python edit and
# speaks only when the result differs from ops/test_baseline (REBUILD.md §8.2).
#
# Reports through hookSpecificOutput.additionalContext so the message enters
# Claude's context. A top-level systemMessage renders to the user but is not
# guaranteed to reach the model — and the model is who must act on it.
#
# Never exits non-zero: a broken check must not wedge the session.

FILE="$(jq -r '.tool_input.file_path // empty' 2>/dev/null)" || exit 0
[ -n "$FILE" ] || exit 0

case "$FILE" in
*/_archive/*) exit 0 ;;                      # parked code has no live gate
*/src/*.py|*/tests/*.py|src/*.py|tests/*.py) ;;   # relative paths too
*) exit 0 ;;
esac

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$ROOT" ] || exit 0

BASE="$(tr -d '[:space:]' < "$ROOT/ops/test_baseline" 2>/dev/null)"
[ -n "$BASE" ] || BASE=0

OUT="$("$ROOT/venv/bin/python" -m pytest "$ROOT/tests" -q 2>&1)"

# Read the SUMMARY line ("3 failed, 89 passed in 1.2s"), not the first match
# anywhere — a traceback can contain "1 failed" and skew the count.
SUMMARY="$(printf '%s\n' "$OUT" | grep -E '^[= ]*[0-9]+ (passed|failed|error)' | tail -1)"
[ -n "$SUMMARY" ] || SUMMARY="$(printf '%s\n' "$OUT" | tail -3 | tr '\n' ' ')"

FAILED="$(printf '%s' "$SUMMARY" | grep -oE '[0-9]+ failed' | grep -oE '^[0-9]+')"
ERRORS="$(printf '%s' "$SUMMARY" | grep -oE '[0-9]+ error' | grep -oE '^[0-9]+')"
PASSED="$(printf '%s' "$SUMMARY" | grep -oE '[0-9]+ passed' | grep -oE '^[0-9]+')"

say() { jq -n --arg m "$1" '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$m}}'; exit 0; }

# Collection/import error — tests never ran. The catastrophic case, and the
# one a bare failure-count check reads as "0 failures, all fine".
[ -n "$ERRORS" ] && [ "$ERRORS" != "0" ] &&
    say "pytest reported $ERRORS collection/import error(s) — the tests did not run. Fix before continuing: venv/bin/python -m pytest tests/ -q"

# No counts at all: pytest itself is broken. Never read that as green.
[ -z "$FAILED" ] && [ -z "$PASSED" ] &&
    say "pytest produced no pass/fail counts — it may be crashing. Run manually: venv/bin/python -m pytest tests/ -q"

[ -n "$FAILED" ] || FAILED=0
[ "$FAILED" != "$BASE" ] &&
    say "Test failures changed: $FAILED, baseline is $BASE (ops/test_baseline) — see REBUILD.md §8.2. Never weaken a test to close the gap."

exit 0
