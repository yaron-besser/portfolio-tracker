#!/bin/sh
# Claude Code SessionStart hook — orients a fresh session before it does anything.
#
# Two things:
#   1. where the rebuild is, if this copy has a plan (REBUILD.md is optional)
#   2. anything traded in the last week that no claim explains
#
# (2) is Yaron's ask, 2026-08-15: "you do the synchronisation and you see
# something has been bought and there is no mention about it — then your
# responsibility is to flag me up." A session that starts without knowing DRAM
# is unexplained will not think to mention it.
#
# Moved out of an inline settings.json one-liner on 2026-08-15 so it can be
# pipe-tested. Three real bugs had previously hidden in inline hook strings
# (REBUILD.md §5.7) — the same reason claude-stop.sh is a file.
#
# It reports; it never writes and never blocks.

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$ROOT" ] || exit 0

# Appends a block, keeping the blank line between blocks but not starting
# the message with one when an earlier block was skipped.
append() {
    [ -n "$1" ] || return 0
    if [ -z "$OUT" ]; then OUT="$1"; else OUT="$OUT

$1"; fi
}

# The rebuild plan is OPTIONAL. The header and the "read it before acting"
# line used to print whether or not the file was there, so a copy of this
# project without one — the public copy has none — opened every session by
# pointing it at a file that does not exist. Saying nothing is better than
# sending a session somewhere that is not there.
OUT=""
if [ -f "$ROOT/REBUILD.md" ]; then
    PLAN="Active rebuild plan (REBUILD.md):"
    STATUS="$(grep -m1 '^\*\*Status:' "$ROOT/REBUILD.md")"
    # The first unchecked box in the FILE is not the next task — a phase above
    # the active one can say in its own text not to pick it up. Start reading
    # at the heading marked "(active".
    NEXT="$(sed -n '/^### Phase .*(active/,$p' "$ROOT/REBUILD.md" | grep -m1 '^- \[ \]')"
    [ -n "$NEXT" ] || NEXT="$(grep -m1 '^- \[ \]' "$ROOT/REBUILD.md")"
    [ -n "$STATUS" ] && PLAN="$PLAN
$STATUS"
    [ -n "$NEXT" ] && PLAN="$PLAN
Next unchecked: $NEXT"
    PLAN="$PLAN

Read REBUILD.md before acting; follow the working rules in §8."
    append "$PLAN"
fi

# Unexplained trades. Loud on failure — a silent all-clear here would defeat
# the whole point (P2).
if [ -x "$ROOT/venv/bin/python" ]; then
    GAPS="$(cd "$ROOT" && "$ROOT/venv/bin/python" - <<'EOF' 2>/dev/null
import sys
sys.path.insert(0, ".")
try:
    from src import holding
    g = holding.unexplained()
    if len(g):
        print("⚠ Traded in the last 7 days with NO claim explaining why — "
              "raise this with Yaron before other work:")
        for _, r in g.iterrows():
            held = ("now closed" if not r["shares"]
                    else f"${r['market_value']:,.0f}" if r["market_value"]
                    else "held, no price")
            print(f"    {r['instrument_id']} ({r['last_trade']}, {held}) — /note {r['instrument_id']}")
except Exception as e:
    print(f"⚠ could not check for unexplained trades — {type(e).__name__}: {e}")
EOF
)"
    append "$GAPS"
else
    append "⚠ could not check for unexplained trades — venv/bin/python is missing."
fi

printf '%s' "$OUT" | jq -Rs '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:.}}'
