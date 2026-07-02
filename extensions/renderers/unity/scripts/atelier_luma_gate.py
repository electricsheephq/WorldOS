#!/usr/bin/env python3
# Staging-law luma gate on the beauty pass. Gate: 60-85% of pixels L<26, 2-5% L>60 (Rec.709 luma).
import sys
from PIL import Image
p = sys.argv[1] if len(sys.argv) > 1 else "atelier_beauty.png"
im = Image.open(p).convert("RGB")
px = im.getdata()
n = len(px)
dark = lit = 0
tot = 0
for r, g, b in px:
    L = 0.2126 * r + 0.7152 * g + 0.0722 * b
    tot += L
    if L < 26:
        dark += 1
    if L > 60:
        lit += 1
pd = 100.0 * dark / n
pl = 100.0 * lit / n
mean = tot / n
dark_ok = 60.0 <= pd <= 85.0
lit_ok = 2.0 <= pl <= 5.0
print(f"{p}: pixels={n} mean_L={mean:.2f}")
print(f"  DARK L<26 : {pd:.2f}%  (gate 60-85%)  {'OK' if dark_ok else 'FAIL'}")
print(f"  LIT  L>60 : {pl:.2f}%  (gate 2-5%)    {'OK' if lit_ok else 'FAIL'}")
print(f"  VERDICT: {'PASS' if dark_ok and lit_ok else 'FAIL'}")
