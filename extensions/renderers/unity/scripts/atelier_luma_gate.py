#!/usr/bin/env python3
# Staging-law luma gate on the beauty pass. Gate: 60-85% of pixels L<26, 2-5% L>60 (Rec.709 luma).
import sys
import numpy as np
from PIL import Image
p = sys.argv[1] if len(sys.argv) > 1 else "atelier_beauty_v4.png"
im = Image.open(p).convert("RGB")
arr = np.asarray(im, dtype=np.float64)
n = arr.shape[0] * arr.shape[1]
if n == 0:
    print(f"{p}: empty image, cannot compute luma gate")
    sys.exit(1)
L = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
dark = int(np.count_nonzero(L < 26))
lit = int(np.count_nonzero(L > 60))
tot = float(L.sum())
pd = 100.0 * dark / n
pl = 100.0 * lit / n
mean = tot / n
dark_ok = 60.0 <= pd <= 85.0
lit_ok = 2.0 <= pl <= 5.0
print(f"{p}: pixels={n} mean_L={mean:.2f}")
print(f"  DARK L<26 : {pd:.2f}%  (gate 60-85%)  {'OK' if dark_ok else 'FAIL'}")
print(f"  LIT  L>60 : {pl:.2f}%  (gate 2-5%)    {'OK' if lit_ok else 'FAIL'}")
print(f"  VERDICT: {'PASS' if dark_ok and lit_ok else 'FAIL'}")
