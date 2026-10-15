#!/usr/bin/env python
"""Find the pages of a research PDF that talk about a theme, and show them.

A Fundstrat monthly runs 300+ pages and says something about a dozen themes.
Reading it front to back to write one themed summary is not workable, and
guessing at page numbers defeats the point of citing them — the whole reason
a machine may write "What it says" is that every line can be checked against
the original in seconds.

So: search, then read only what matched.

Usage:
    venv/bin/python .claude/skills/evidence/scripts/find_pages.py \\
        notes/research/2026-07-16-fundstrat-market-update.pdf memory dram hbm

    # more or less surrounding text per hit
    ... --chars 2000 --max-pages 12

Slide decks repeat their section dividers, so the same wording often appears
on many consecutive pages. Cite the FIRST page of a run — that is the one a
reader flipping through will find.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    print("pdfplumber is not installed in this venv.\n"
          "  venv/bin/pip install pdfplumber", file=sys.stderr)
    raise SystemExit(1)


def runs(pages: list[int]) -> list[tuple[int, int]]:
    """Collapse [3,4,5,9] into [(3,5),(9,9)].

    Decks repeat a divider slide; reporting 3-4-5-7-13-21 as six separate hits
    invites citing whichever one happened to be listed first.
    """
    out: list[tuple[int, int]] = []
    for p in pages:
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("terms", nargs="+", help="words to look for, case-insensitive")
    ap.add_argument("--chars", type=int, default=1200,
                    help="characters of text to print per page (default 1200)")
    ap.add_argument("--max-pages", type=int, default=8,
                    help="how many matching pages to print in full (default 8)")
    a = ap.parse_args()

    path = Path(a.pdf)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 1

    terms = [t.lower() for t in a.terms]
    hits: dict[int, list[str]] = {}

    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for n, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").lower()
            found = [t for t in terms if t in text]
            if found:
                hits[n] = found

        print(f"{path.name} — {total} pages, {len(hits)} mention {', '.join(terms)}\n")

        if not hits:
            print("Nothing matched. Try broader terms, or the theme may genuinely "
                  "not be in this document — which is itself worth saying out loud.")
            return 0

        print("pages:")
        for start, end in runs(sorted(hits)):
            span = f"p.{start}" if start == end else f"p.{start}-{end}"
            words = ", ".join(sorted(set(hits[start])))
            note = "  (repeated divider — cite the first)" if end > start else ""
            print(f"   {span:<12} {words}{note}")

        # Print the first page of each run: in a deck that is the slide itself,
        # and in prose it is where the section starts.
        firsts = [s for s, _ in runs(sorted(hits))][: a.max_pages]
        print(f"\ntext of the first page of each run ({len(firsts)} shown):")
        for n in firsts:
            print("=" * 68)
            print(f"--- p.{n} ---")
            print((pdf.pages[n - 1].extract_text() or "")[: a.chars])

    print("\nQuote with the page number attached. Anything you cannot point at a "
          "page for does not belong under 'What it says'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
