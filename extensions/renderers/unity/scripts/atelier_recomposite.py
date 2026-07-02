#!/usr/bin/env python3
"""Albedo-swap recomposite: transfer the 3D render's light transport onto a painted albedo.

    final = painted_albedo * (beauty / albedo)        (all in LINEAR space)

`beauty / albedo` isolates the scene's light transport (direct + shadows + falloff) from the
render; multiplying the PAINTED albedo by it re-lights the painterly surface with the real
3D lighting — the PoE recipe's compositing step. Epsilon-guarded where albedo ~ 0.

Usage: recomposite.py <beauty.png> <albedo.png> <painted_albedo.png> <out.png> [gain]
The painted albedo is resized to the beauty's dims if needed.
"""
import sys
import numpy as np
from PIL import Image

def srgb_to_lin(x):
    x = x / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)

def lin_to_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * (x ** (1 / 2.4)) - 0.055) * 255.0

def load_lin(path, size=None):
    im = Image.open(path).convert('RGB')
    if size and im.size != size:
        im = im.resize(size, Image.LANCZOS)
    return srgb_to_lin(np.asarray(im, dtype=np.float64)), im.size

def main():
    beauty_p, albedo_p, painted_p, out_p = sys.argv[1:5]
    gain = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
    beauty, size = load_lin(beauty_p)
    albedo, _ = load_lin(albedo_p, size)
    painted, _ = load_lin(painted_p, size)
    eps = 0.004
    transport = beauty / np.maximum(albedo, eps)
    # clamp transport to sane range (specular/emissive can exceed 1; runaway where albedo~0)
    transport = np.clip(transport, 0.0, 6.0)
    out = np.clip(painted * transport * gain, 0.0, 1.0)
    Image.fromarray(lin_to_srgb(out).astype(np.uint8)).save(out_p)
    # value-structure stats of the result (the staging-law gate)
    L = np.asarray(Image.fromarray(lin_to_srgb(out).astype(np.uint8)).convert('L'), dtype=np.float64)
    print(f"recomposite -> {out_p}  {size[0]}x{size[1]}")
    print(f"stats: near-black(L<26)={np.mean(L<26)*100:.0f}%  lit(L>60)={np.mean(L>60)*100:.1f}%  "
          f"high(L>120)={np.mean(L>120)*100:.1f}%  median={np.median(L):.0f}  [PoE: 66-80% / 2-4% / ~0-0.5% / 0-15]")

if __name__ == '__main__':
    main()
