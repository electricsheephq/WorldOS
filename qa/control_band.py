#!/usr/bin/env python3
"""control_band.py — the ONE scale-parametrized control-band helper (shared code, separate data).

Both control registries — the TEXT artifact controls (qa/build_artifact_controls.py, 1-5 rubric) and
the VISUAL controls (qa/build_visual_controls.py, 0-10 panel scale) — derive a control's a-priori
valid band as ``[anchor - noise, min(scale_max, anchor + noise)]``. That formula used to live inline
in build_artifact_controls.py hardwired to the 1-5 ceiling (``min(5.0, anchor + 1.2)``). Extracting it
here — parametrized by ``scale_max`` — is exactly the "two registries drift by discipline" mitigation
the promotion-gate decision calls for: the DATA files genuinely differ (text canon vs image frames),
but the band CODE is unified, so a change to the band law changes both at once.

The 1-5 caller passes ``scale_max=5.0`` (byte-identical to the old inline expression); the 0-10 caller
passes ``scale_max=10.0``. ``noise`` defaults to the shared ±1.2 noise law (measured cross-panel drift,
memory feedback_visual_panel_scoring_variance — the same constant both registries carry as ``noise_law``).
"""
from __future__ import annotations


def control_band(anchor: float, *, noise: float = 1.2, scale_max: float = 5.0) -> list[float]:
    """A control's a-priori valid band ``[anchor - noise, min(scale_max, anchor + noise)]``, each end
    rounded to 1 decimal (matching the committed identity-map bands).

    Byte-identity note: ``control_band(anchor, scale_max=5.0)`` reproduces the exact values
    build_artifact_controls.py wrote inline before extraction — ``[round(anchor - 1.2, 1),
    round(min(5.0, anchor + 1.2), 1)]`` — so the text-control fixtures/identity map do not churn."""
    return [round(anchor - noise, 1), round(min(scale_max, anchor + noise), 1)]
