#!/bin/sh
# Claude Code SessionStart hook — orients a fresh session before it does anything.
#
# Two things:
#   1. where the rebuild is, and the first unfinished item (REBUILD.md)
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

OUT="Active rebuild plan (REBUILD.md):"
if [ -f "$ROOT/REBUILD.md" ]; then
    STATUS="$(grep -m1 '^\*\*Status:' "$ROOT/REBUILD.md")"
    # The first unchecked box in the FILE is not the next task — Phase 2b's
    # "write the constitution" sits above the active phase and says in its own
    # text not to pick it up. Start reading at the heading marked "(active".
    NEXT="$(sed -n '/^### Phase .*(active/,$p' "$ROOT/REBUILD.md" | grep -m1 '^- \[ \]')"
    [ -n "$NEXT" ] || NEXT="$(grep -m1 '^- \[ \]' "$ROOT/REBUILD.md")"
    [ -n "$STATUS" ] && OUT="$OUT
$STATUS"
    [ -n "$NEXT" ] && OUT="$OUT
Next unchecked: $NEXT"
fi
OUT="$OUT

Read REBUILD.md before acting; follow the working rules in §8."

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
    [ -n "$GAPS" ] && OUT="$OUT

$GAPS"
else
    OUT="$OUT

⚠ could not check for unexplained trades — venv/bin/python is missing."
fi

printf '%s' "$OUT" | jq -Rs '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:.}}'
