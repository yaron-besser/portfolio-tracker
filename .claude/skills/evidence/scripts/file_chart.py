#!/usr/bin/env python
"""Rename, downscale and file one chart screenshot into notes/screenshots/.

Three things the commit guard enforces on every filed chart, all of them easy
to get wrong by hand: the filename shape, the subject vocabulary, and the
500 KB ceiling. A TradingView export is routinely 2800px and 600 KB, so the
downscale is the normal path rather than the exception.

Downscaling is lossy and deliberate — the chart is evidence of what was on
screen, not an archival image. 1600px keeps every price label readable.

Usage:
    venv/bin/python .claude/skills/evidence/scripts/file_chart.py \\
        "notes/screenshots/_inbox/ASML_2026-08-18_15-52-25.png" \\
        --date 2026-08-18 --ticker ASML --subject price --timeframe 1d

Prints the filed path, which is what add_evidence(location=...) wants.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCREENSHOTS = PROJECT_ROOT / "notes" / "screenshots"
VOCAB = PROJECT_ROOT / "ops" / "vocab" / "chart_subjects"
MAX_BYTES = 500 * 1024
TIMEFRAMES = ("1d", "1w", "1m")


def subjects() -> list[str]:
    """The allowed subjects, read from the vocab file rather than hardcoded.

    Hardcoding would let this script and the commit guard drift apart, and the
    guard is the one that actually blocks.
    """
    return [s.strip() for s in VOCAB.read_text().split() if s.strip()]


def shrink_to_fit(src: Path, dst: Path, start_width: int = 1600) -> tuple[int, int]:
    """Save `src` at `dst` under the size ceiling. Returns (width, bytes).

    Steps down in width rather than lowering PNG quality: a chart is thin
    lines and small type, and resampling degrades it far more gracefully than
    palette reduction would.
    """
    im = Image.open(src).convert("RGB")
    width = min(start_width, im.width)
    while True:
        out = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        out.save(dst, optimize=True)
        if dst.stat().st_size <= MAX_BYTES or width <= 700:
            return width, dst.stat().st_size
        width -= 200


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", help="the file in notes/screenshots/_inbox/")
    p.add_argument("--date", required=True, help="YYYY-MM-DD, the day the chart was taken")
    p.add_argument("--ticker", required=True, help="instrument id, e.g. ASML")
    p.add_argument("--subject", required=True, help=f"one of: {', '.join(subjects())}")
    p.add_argument("--timeframe", choices=TIMEFRAMES, default=None,
                   help="optional: 1d, 1w or 1m")
    p.add_argument("--keep-source", action="store_true",
                   help="leave the original in _inbox instead of removing it")
    a = p.parse_args()

    allowed = subjects()
    if a.subject not in allowed:
        print(f"subject {a.subject!r} is not in ops/vocab/chart_subjects.\n"
              f"  allowed: {', '.join(allowed)}\n"
              f"  If this is a genuinely new kind of chart, append it to that "
              f"file first — the commit guard reads the same list.", file=sys.stderr)
        return 1

    src = Path(a.source)
    if not src.is_file():
        print(f"no such file: {src}", file=sys.stderr)
        return 1

    stem = f"{a.date}-{a.ticker}-{a.subject}"
    if a.timeframe:
        stem += f"-{a.timeframe}"
    dst = SCREENSHOTS / f"{stem}.png"
    if dst.exists():
        print(f"{dst.relative_to(PROJECT_ROOT)} already exists — filing this one "
              f"would overwrite a chart already on record. Rename or check first.",
              file=sys.stderr)
        return 1

    before_px, before_kb = Image.open(src).width, src.stat().st_size / 1024
    width, size = shrink_to_fit(src, dst)

    if size > MAX_BYTES:
        dst.unlink()
        print(f"could not get under 500 KB (smallest try was {size/1024:,.0f} KB). "
              f"Crop the screenshot and try again.", file=sys.stderr)
        return 1

    if not a.keep_source:
        src.unlink()

    rel = dst.relative_to(PROJECT_ROOT)
    print(f"{before_px}px {before_kb:,.0f} KB  ->  {width}px {size/1024:,.0f} KB")
    print(rel)
    print(f"\nfile it with:\n"
          f'  evidence.add_evidence(kind="chart", title="{stem}", source="mine",\n'
          f'                        published="{a.date}", storage="repo",\n'
          f'                        location="{rel}")')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
