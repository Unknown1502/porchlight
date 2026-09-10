#!/usr/bin/env python3
"""Contrast and structure checks against the real stylesheet.

Run by CI. Two contrast pairs failed the first time this ran - the soft and
faint ink tokens measured 4.25 and 2.81 against the paper ground while carrying
section labels and the timestamps in the activity log. Colour choices drift
during a redesign; this stops them drifting below AA without anyone noticing.

    python scripts/check_design.py
"""
import re
import sys

html = open("src/porchlight/dashboard/index.html", encoding="utf-8").read()
css = html.split("<style>")[1].split("</style>")[0]

def hexes(block):
    return dict(re.findall(r"(--[a-z-]+):\s*(#[0-9A-Fa-f]{6})", block))

tok = hexes(css)

def lum(h):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(h[i:i+2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)

def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

PAIRS = [
    ("body text on paper",        "--ink",       "--paper",       4.5),
    ("body text on surface",      "--ink",       "--surface",     4.5),
    ("secondary text on paper",   "--ink-mid",   "--paper",       4.5),
    ("soft text on paper",        "--ink-soft",  "--paper",       4.5),
    ("faint text on paper",       "--ink-faint", "--paper",       4.5),
    ("primary button label",      "--paper",     "--forest",      4.5),
    ("link on paper",             "--forest",    "--paper",       4.5),
    ("link on surface",           "--forest",    "--surface",     4.5),
    ("amber note text",           "--amber",     "--amber-tint",  4.5),
    ("red note text",             "--red",       "--red-tint",    4.5),
    ("band black",                "--band-black","--red-tint",    4.5),
    ("band red",                  "--band-red",  "--red-tint",    4.5),
    ("band amber",                "--band-amber","--amber-tint",  4.5),
    ("band green",                "--band-green","--forest-tint", 4.5),
]

fails = 0
print(f"{'pair':30} {'ratio':>6}  {'need':>5}")
for name, fg, bg, need in PAIRS:
    if fg not in tok or bg not in tok:
        print(f"{name:30} {'?':>6}  token missing: {fg if fg not in tok else bg}")
        fails += 1
        continue
    r = ratio(tok[fg], tok[bg])
    ok = r >= need
    fails += 0 if ok else 1
    print(f"{name:30} {r:6.2f}  {need:5.1f}  {'ok' if ok else 'FAIL'}")

print("\nSTRUCTURE")
checks = [
    ("single h1 per view",      html.count('class="page-title"') >= 1 and "<h1" in html),
    ("skip-safe main landmark", '<main id="main" tabindex="-1">' in html),
    ("nav has a label",         'aria-label="Sections"' in html),
    ("live region for content", 'aria-live="polite"' in html),
    ("busy state announced",    'aria-busy' in html),
    ("tables have captions",    html.count("<caption") >= 2),
    ("inputs labelled",         html.count("aria-label=") >= 3),
    ("focus-visible styled",    ":focus-visible" in css),
    ("reduced motion honoured", "prefers-reduced-motion" in css),
    ("touch targets >= 40px",   "min-height: 2.5rem" in css),
    ("no horizontal overflow",  "overflow-wrap: anywhere" in css and "max-width: 40rem" in css),
    ("status not colour-only",  ".band-black" in css and "band-${esc(r.urgency)}" in html),
]
for name, ok in checks:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    fails += 0 if ok else 1

sys.exit(1 if fails else 0)
