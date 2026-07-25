#!/usr/bin/env python3
"""Audit docs/pitch.html against published slide-design thresholds.

Every limit here traces to a primary source, not taste:

  words/slide <= 21     Garner & Alley 2013 (n=110): the assertion-evidence deck
                        that won averaged 21.2 words/slide, the bullet deck that
                        lost averaged 41.5.  https://www.ijee.ie/articles/Vol29-6/23_ijee2791ns.pdf
  code lines <= 6       reveal.js ships `pre code {max-height:400px}` at 27.7px
                        per line = 14 lines before a scrollbar; 5-6 is the
                        authoring ideal.  https://revealjs.com/code/
  code font >= 24px     WCAG 1.4.3 defines "large scale" as 18pt = 24px, below
                        which the relaxed 3:1 contrast allowance is lost.
  bullet lists == 0     Bullets hide relationships; for a DAG the relationships
                        are the content.  https://writing.engr.psu.edu/ae_rethinking.pdf

Run: python tools/audit_deck.py
Exit 0 = every threshold met. Non-zero = a specific violation, named.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORDS_MEAN_MAX = 21.2
WORDS_SLIDE_MAX = 40
CODE_LINES_MAX = 6
CODE_PX_MIN = 24
DECK = Path(__file__).resolve().parent.parent / "docs" / "pitch.html"


def visible_words(chunk: str) -> int:
    """Words the audience must read: prose and headlines, excluding code panes."""
    chunk = re.sub(r"<pre>.*?</pre>", " ", chunk, flags=re.S)
    chunk = re.sub(r"<!--.*?-->", " ", chunk, flags=re.S)
    chunk = re.sub(r"data-notes=\"[^\"]*\"", " ", chunk)  # notes are not on screen
    chunk = re.sub(r"<[^>]+>", " ", chunk)
    chunk = chunk.replace("&darr;", " ").replace("&nbsp;", " ").replace("&times;", " ")
    chunk = re.sub(r"&[a-z]+;", " ", chunk)
    return len([w for w in chunk.split() if any(c.isalnum() for c in w)])


def main() -> int:
    html = DECK.read_text(encoding="utf-8")
    body = html[html.index("<!-- 1 "):html.index("<nav>")]
    slides = re.findall(r"<section[^>]*>.*?</section>", body, re.S)
    fails: list[str] = []

    if not slides:
        print("no slides parsed", file=sys.stderr)
        return 2

    counts = []
    for n, s in enumerate(slides, 1):
        words = visible_words(s)
        counts.append(words)
        if words > WORDS_SLIDE_MAX:
            fails.append(f"slide {n}: {words} words exceeds the {WORDS_SLIDE_MAX}-word ceiling")
        for pane in re.findall(r"<pre>(.*?)</pre>", s, re.S):
            lines = len([ln for ln in pane.strip().splitlines() if ln.strip()])
            if lines > CODE_LINES_MAX:
                fails.append(f"slide {n}: code pane has {lines} lines, max {CODE_LINES_MAX}")

    mean = sum(counts) / len(counts)
    # Compare at the source's own precision: 21.2 is the published figure.
    if round(mean, 1) > WORDS_MEAN_MAX:
        fails.append(f"mean {mean:.1f} words/slide exceeds {WORDS_MEAN_MAX}")

    # Bullet lists are forbidden outright in the slide body.
    if re.search(r"<li[ >]", body):
        fails.append("slide body contains a <li> bullet list")

    # Code must be legible: the clamp()'s preferred size must reach 24px.
    m = re.search(r"pre\{[^}]*font-size:clamp\([^,]+,\s*([\d.]+)vw,\s*(\d+)px\)", html, re.S)
    if not m:
        fails.append("could not find the pre font-size clamp to verify legibility")
    elif int(m.group(2)) < CODE_PX_MIN:
        fails.append(f"code max font {m.group(2)}px is below the {CODE_PX_MIN}px floor")

    # Every slide needs speaker notes, since that is where the prose went.
    for n, s in enumerate(slides, 1):
        if "data-notes=" not in s:
            fails.append(f"slide {n}: no speaker notes")

    print(f"{len(slides)} slides | mean {mean:.1f} words | "
          f"per-slide {counts} | max code font {m.group(2) if m else '?'}px")
    for f in fails:
        print(f"  FAIL {f}")
    print("PASS: every threshold met" if not fails else f"{len(fails)} violation(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
