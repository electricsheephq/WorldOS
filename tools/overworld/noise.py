"""Hand-rolled deterministic value-noise fBm — stdlib + numpy ONLY.

Why value noise and not a library (Perlin/simplex via `noise`/`opensimplex`)?
The W1 spike must add ZERO new pip dependencies (the engine venv ships numpy +
PIL and nothing else terrain-related). Value noise interpolated with the Perlin
quintic fade curve is visually indistinguishable from gradient noise at fBm
scale for a *schematic* heightfield — we only need plausible continents,
mountains and valleys, not shader-grade detail. It is also trivially
vectorisable in numpy, so a 1024x1024 field with 6 octaves builds in well under
a second.

Determinism contract: every field is a pure function of (seed, resolution,
octave params). We derive a distinct child RNG per octave from the base seed so
the same seed always reproduces the same lattice, and therefore the same world.
This is what makes the whole overworld reproducible (the W1 acceptance test).

Design attribution: the two-resolution terrain idea (a coarse global field for
continents/hydrology/settlements, fine bakes only under actual scenes) is drawn
as a *design reference* from OpenMMO's doc/TERRAIN_GENERATION.md — ideas only,
no code (their licence is noncommercial).
"""

from __future__ import annotations

import numpy as np


def _fade(t: np.ndarray) -> np.ndarray:
    """Perlin's quintic smoothstep 6t^5 - 15t^4 + 10t^3.

    Gives C2 continuity at lattice boundaries so interpolated value noise has no
    visible grid creases (a plain linear/`smoothstep` fade leaves faint seams
    that read as artificial ridges on a hillshade)."""
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _value_noise(h: int, w: int, freq: int, rng: np.random.Generator) -> np.ndarray:
    """One octave of value noise at integer lattice frequency `freq`.

    A random value is placed on each node of a (freq+2) square lattice; the
    output is that lattice bilinearly interpolated up to (h, w) with a quintic
    fade on the fractional coordinates. Fully vectorised (no python pixel loop).
    """
    freq = max(1, int(freq))
    lattice = rng.random((freq + 2, freq + 2))

    # Continuous sample coordinates in lattice space, one per output pixel.
    gy = np.linspace(0.0, freq, h, endpoint=False)
    gx = np.linspace(0.0, freq, w, endpoint=False)
    y0 = np.floor(gy).astype(np.intp)
    x0 = np.floor(gx).astype(np.intp)
    fy = _fade(gy - y0)  # faded fractional offset within the cell
    fx = _fade(gx - x0)

    # Gather the four lattice corners for every output pixel via broadcasting.
    Y0 = y0[:, None]
    X0 = x0[None, :]
    v00 = lattice[Y0, X0]
    v01 = lattice[Y0, X0 + 1]
    v10 = lattice[Y0 + 1, X0]
    v11 = lattice[Y0 + 1, X0 + 1]

    FX = fx[None, :]
    FY = fy[:, None]
    top = v00 * (1.0 - FX) + v01 * FX
    bot = v10 * (1.0 - FX) + v11 * FX
    return top * (1.0 - FY) + bot * FY


def fbm(
    h: int,
    w: int,
    seed: int,
    octaves: int = 6,
    base_freq: float = 3.0,
    lacunarity: float = 2.0,
    gain: float = 0.5,
) -> np.ndarray:
    """Fractional Brownian motion: a normalised (0..1) sum of value-noise octaves.

    Each octave doubles the frequency (`lacunarity`) and halves the amplitude
    (`gain`), the classic 1/f spectrum that reads as natural terrain. Each octave
    draws from its own seeded child RNG (`seed + o*7919`, 7919 prime to decorrelate
    octaves) so the whole stack is a deterministic function of `seed`.
    """
    total = np.zeros((h, w), dtype=np.float64)
    amp = 1.0
    freq = float(base_freq)
    norm = 0.0
    for o in range(octaves):
        rng = np.random.default_rng(seed + o * 7919)
        total += amp * _value_noise(h, w, freq, rng)
        norm += amp
        amp *= gain
        freq *= lacunarity
    total /= norm
    # Renormalise to full 0..1 (the finite-octave sum rarely spans the range).
    lo, hi = float(total.min()), float(total.max())
    if hi - lo > 1e-9:
        total = (total - lo) / (hi - lo)
    return total
