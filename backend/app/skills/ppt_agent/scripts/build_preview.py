#!/usr/bin/env python3
"""Build a contact-sheet preview.html from the SVG slides in a deck folder.

Usage:
    build_preview.py <deck-dir>

Reads <deck-dir>/slides/*.svg (sorted) and writes <deck-dir>/preview.html — a
single self-contained page showing every slide in order. Standard library only.
Run in the LearnGraph offline sandbox via sandbox_exec / skill.sandbox-run;
host fallback also works (Windows: PYTHONUTF8=1).
"""
import sys
import os
import glob
import html

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CSS = """
 body{margin:0;background:#f4f5f7;font:14px/1.5 system-ui,'Segoe UI','Microsoft YaHei',sans-serif;color:#333}
 header{padding:18px 28px;background:#fff;border-bottom:1px solid #e5e7eb;position:sticky;top:0;z-index:1}
 h1{margin:0;font-size:18px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(480px,1fr));gap:24px;padding:28px}
 figure{margin:0;background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}
 figcaption{padding:10px 14px;font-size:12px;color:#6b7280;border-bottom:1px solid #f0f0f0}
 .slide{aspect-ratio:16/9;background:#fff}
 .slide svg{width:100%;height:100%;display:block}
"""


def main():
    if len(sys.argv) != 2:
        print("Usage: build_preview.py <deck-dir>")
        sys.exit(1)

    deck = sys.argv[1]
    slides_dir = os.path.join(deck, "slides")
    svgs = sorted(glob.glob(os.path.join(slides_dir, "*.svg")))
    if not svgs:
        print("No SVG slides found in " + slides_dir)
        sys.exit(1)

    cards = []
    for i, path in enumerate(svgs, 1):
        with open(path, encoding="utf-8") as f:
            svg = f.read()
        cap = "%02d · %s" % (i, html.escape(os.path.basename(path)))
        cards.append('<figure><figcaption>' + cap + '</figcaption>'
                     '<div class="slide">' + svg + '</div></figure>')

    title = html.escape(os.path.basename(os.path.abspath(deck)) or "deck")
    doc = (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<title>PPT 预览 · ' + title + '</title><style>' + CSS + '</style></head>'
        '<body><header><h1>PPT 预览 — 共 ' + str(len(svgs)) + ' 页</h1></header>'
        '<div class="grid">' + "".join(cards) + '</div></body></html>'
    )

    out = os.path.join(deck, "preview.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print("Wrote %s (%d slides)" % (out, len(svgs)))


if __name__ == "__main__":
    main()
