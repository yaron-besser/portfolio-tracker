#!/bin/sh
# Claude Code Stop hook — runs when Claude finishes a turn.
#
# Four things Yaron would otherwise have to remember:
#   1. claims whose check date has arrived and have no verdict
#   2. levels the price has reached
#   3. market data (ETF breakdowns) going stale
#   4. uncommitted work
#
# Why here. Every number the record can ever produce — hit rate, which setups
# work, whether Fundstrat helps — comes from RESOLVED claims. Resolving used to
# depend on him running a command on the right day, which is the same failure as
# the four stops sitting unwatched at IBKR: a record nobody reads. This fires on
# its own, every turn, and says nothing when there is nothing to say.
#
# It reports; it never blocks and never writes. The verdict is always his (P1).
#
# ⚠️ Two things this must never do, both found by review 2026-08-15:
#   - run from the wrong directory and then say nothing. A silent all-clear is
#     the exact failure it exists to prevent, so it cd's to $ROOT and says
#     "could not check" out loud rather than swallowing an error.
#   - lose the uncommitted-file warning when the venv is missing. That warning
#     needs only git, so it is computed before python is ever consulted.

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$ROOT" ] || exit 0

# git-only, so it survives a missing or broken interpreter
N="$(git -C "$ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"

MSG=""
if [ -x "$ROOT/venv/bin/python" ]; then
    MSG="$(cd "$ROOT" && "$ROOT/venv/bin/python" - <<'EOF'
import sys
sys.path.insert(0, ".")
lines = []

try:
    from src import claims
    for _, r in claims.due_claims().iterrows():
        lines.append(f"  • {r['ticker']} ({r['id']}) was due {r['check_date']} — "
                     f"no verdict yet. Run /resolve.")
except Exception as e:
    lines.append(f"  • could not check due claims — {type(e).__name__}: {e}")

try:
    from src import levels
    for _, r in levels.breached_levels().iterrows():
        lines.append(f"  • {r['instrument_id']} reached {r['level']} "
                     f"({r['trigger_when']}) → {r['action']}: {r['instruction']}")
    # Name them. A count told him something was wrong and nothing else, and
    # for seven weeks one of the two was l0029 — a HARD stop, live at IBKR,
    # on 40 shares of IMAE. A hard level that cannot be evaluated is the one
    # case that must never be summarised.
    for _, r in levels.unchecked_levels().iterrows():
        flag = "⚠️ HARD — real order at IBKR" if r["kind"] == "hard" else "soft"
        lines.append(f"  • {r['instrument_id']} {r['level']} ({flag}) "
                     f"cannot be checked: {r['why']}")
except Exception as e:
    lines.append(f"  • could not check levels — {type(e).__name__}: {e}")

# Market data (data/market/) only ever changes when he runs /market and says
# yes — nothing here fetches anything. This just reads the date already on
# the oldest held ETF's sector breakdown and says so out loud once it is
# old, so he notices on his own schedule rather than this nagging on one.
# 90 is a threshold, not a date — the hook never hardcodes a calendar date,
# only compares today's real date to the real one already on file.
# ⚠️ Only watches funds that already carry a date. A fund with none at all
# (SGOV/IBIT/SLV because nothing inside them can go stale, MAGS because the
# breakdown has never been dated) never fires this line, no matter how old
# it gets — the report that /market prints is where those show up instead.
try:
    from src import exposure
    from datetime import date
    cov = exposure.coverage()
    dated = cov.loc[cov["as_of"].astype(str).str.strip() != "", "as_of"]
    if not dated.empty:
        age = (date.today() - date.fromisoformat(min(dated))).days
        if age > 90:
            lines.append(f"  • Market data is {age} days old — run /market "
                         f"when convenient.")
except Exception as e:
    lines.append(f"  • could not check market data age — {type(e).__name__}: {e}")

print("\n".join(lines))
EOF
)"
else
    MSG="  • could not check claims or levels — venv/bin/python is missing"
fi

OUT=""
[ -n "$MSG" ] && OUT="Needs your judgement:
$MSG"
if [ "$N" != "0" ]; then
    WARN="⚠ $N uncommitted file(s) — commit before closing (REBUILD.md P4)."
    if [ -n "$OUT" ]; then OUT="$OUT
$WARN"; else OUT="$WARN"; fi
fi

[ -n "$OUT" ] || exit 0
printf '%s' "$OUT" | jq -Rs '{systemMessage: .}'
