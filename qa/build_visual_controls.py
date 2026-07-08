#!/usr/bin/env python3
"""build_visual_controls.py — serialize the DISGUISED-real-art VISUAL control registry (the image
analogue of build_artifact_controls.py, for the promote.py visual gate — decision
docs/roadmap/VISUAL-PROMOTION-GATE-DECISION.md, item 2).

THE CONTROL LAW (image edition). A visual adoption panel is only trustworthy if a KNOWN piece of
ship-quality REAL painterly art — a shipped Pillars of Eternity II / Baldur's Gate II / Disco Elysium
plate, presented UI-free at comparable crop so the scorer cannot tell it apart from a generated plate —
lands inside its expected band and the candidate scores within the noise law of it. The visual-critic
CALIBRATION-CONTROL PROTOCOL (SKILL.md) proved the panel's ABSOLUTE 0-10 scale is untrustworthy (blind,
real shipped PoE2/BG2 plates scored 3.0-5.6 on the primed instrument; unprimed they read 6-9) — so the
reportable metric is the DELTA vs a REGISTERED control, never an absolute number. This registry is the
list of controls the visual gate will accept: a panel citing a control NOT in here does not pass.

MIRRORS the text registry's field shape (qa/artifact_controls_identity.json): each control carries
``class, world, anchor, band, file, provenance, band_ruler, band_prompt_hash`` — but on the 0-10 panel
scale (``scale_max`` 10.0), with ``reference_frame`` = the disguised real-art plate's path (the frames
the visual-critic skill + the backdrop-cadence panels already use). The a-priori anchor is UNIFORM
(mirroring build_artifact_controls.py's uniform 4.0 on the 1-5 scale) — a real per-frame calibration
panel may later re-center each anchor/band, exactly as the text controls were re-derived (#1380).

EXCLUDED: bg2ee_fortress_party_tactical_02.jpg — the backdrop-cadence-20260708/market_square panel
disclosed it as a DEFECTIVE control (baked-in UI chrome + wrong scene; 5/5 scorers flagged it, scored
2-3/10 for reasons unrelated to painterly craft). A defective plate can never be a registered control.

    python3 qa/build_visual_controls.py [--refs-dir DIR] [--identity-out qa/visual_controls_identity.json]

``--refs-dir`` defaults to the LEXAR visual-critic reference set; if it is not mounted, the registry is
still written from the committed frame manifest below (the anchors/bands/provenance are intrinsic, not
read from the pixels), only ``reference_frame_present`` is stamped false for the missing files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

from control_band import control_band  # noqa: E402 — shared with build_artifact_controls.py (1-5)

# The visual-critic reference frames live on the LEXAR spike drive (INTERNAL-only calibration set,
# never shipped). A control's identity is intrinsic (its anchor/band/provenance), so the registry is
# built from this manifest even when the drive is unmounted — only presence is probed.
DEFAULT_REFS_DIR = Path("/Volumes/LEXAR/WorldOS-Unity-spike/refs")
IDENTITY_PATH = QA_DIR / "visual_controls_identity.json"

# The 0-10 visual panel scale (vs the 1-5 text rubric) — passed to the SHARED band helper.
SCALE_MAX = 10.0
NOISE_LAW = 1.2

# A-priori anchor for REAL shipped painterly art on the current UNPRIMED 0-10 instrument. Uniform
# (like build_artifact_controls.py's ANCHOR=4.0 on 1-5), centered on the TWO measured PoE2-control
# medians on this instrument: the backdrop-cadence camp_clearing_night panel scored its disguised PoE2
# control (poe2_ruins_brazier) at median 8.0, and the market_square clean-control re-score (this PR's
# first real visual-gate run) scored its disguised PoE2 control (poe2_market_interior) at median 9.0.
# Anchor 8.0 → band [6.8, 9.2] contains both; the band is a real instrument check (it caught the
# #1416 defective bg2 control at 2.0), not a rubber stamp. A per-frame calibration panel may re-center
# each anchor (band_calibration stamp), exactly as the text controls were re-derived (#1380).
ANCHOR = 8.0

# The visual "ruler" version — a stable stamp of the registry's scoring convention (scale + noise +
# anchor). Mirrors the text registry's band_ruler (artifact_config_version()); recomputed here rather
# than imported because the visual gate scores images, not the text-artifact envelope.
_RULER_BASIS = f"visual-critic-0-10|scale_max={SCALE_MAX}|noise={NOISE_LAW}|anchor={ANCHOR}"


def _visual_ruler() -> str:
    return "vc_" + hashlib.sha1(_RULER_BASIS.encode("utf-8")).hexdigest()[:12]


def _frame_hash(filename: str) -> str:
    """Stable per-frame identity stamp (analogue of the text control's band_prompt_hash — there the
    hash is of the artifact card; here the control IS the frame, so it is the frame's identity)."""
    return "vf_" + hashlib.sha1(filename.encode("utf-8")).hexdigest()[:12]


# The registered visual controls. Each is a REAL shipped plate (game = the painterly bar it exemplifies)
# with a thematic tag for panel selection. class="room" — the visual class promote.py's gate serves
# today (a backdrop plate is a room-class artifact); the field mirrors the text registry's per-class key.
# bg2ee_fortress_party_tactical_02.jpg is intentionally ABSENT (disclosed-defective; see module docstring).
_FRAMES: list[dict] = [
    {"file": "poe2_ruins_brazier_integration_01.jpg", "game": "pillars-of-eternity-2", "tag": "ruins_warm_key"},
    {"file": "poe2_tavern_interior_combat_02.jpg", "game": "pillars-of-eternity-2", "tag": "tavern_interior"},
    {"file": "poe2_cliff_party_brushwork_03.jpg", "game": "pillars-of-eternity-2", "tag": "outdoor_cliff"},
    {"file": "poe2_market_interior_lighting_04.jpg", "game": "pillars-of-eternity-2", "tag": "market_lit"},
    {"file": "poe2_temple_interior_lighting_05.jpg", "game": "pillars-of-eternity-2", "tag": "temple_cool_key"},
    {"file": "bg2ee_forest_party_tactical_01.jpg", "game": "baldurs-gate-2-ee", "tag": "outdoor_forest"},
    {"file": "bg2ee_cavern_darkzone_lighting_03.jpg", "game": "baldurs-gate-2-ee", "tag": "dark_zone"},
    {"file": "bg2ee_temple_combat_lighting_04.jpg", "game": "baldurs-gate-2-ee", "tag": "combat_lit_interior"},
    {"file": "disco_whirling_plaza_brushwork_01.jpg", "game": "disco-elysium", "tag": "plaza_brushwork"},
    {"file": "disco_village_integration_02.jpg", "game": "disco-elysium", "tag": "outdoor_integration"},
    {"file": "disco_cafeteria_bar_interior_03.jpg", "game": "disco-elysium", "tag": "bar_interior"},
    {"file": "disco_office_interior_lighting_04.jpg", "game": "disco-elysium", "tag": "dark_pocket_lighting"},
]


def _control_id(frame: dict) -> str:
    stem = Path(frame["file"]).stem
    return f"control:visual:{frame['game']}:{stem}"


def build(refs_dir: Path) -> dict:
    """Assemble the visual control registry dict (mirrors build_artifact_controls.build's identity map,
    on the 0-10 scale). Intrinsic fields (anchor/band/provenance) do not read the pixels; only
    ``reference_frame_present`` probes the drive so a missing mount is visible, not silently wrong."""
    ruler = _visual_ruler()
    controls: dict[str, Any] = {}
    for fr in sorted(_FRAMES, key=lambda f: f["file"]):
        cid = _control_id(fr)
        ref_path = refs_dir / fr["file"]
        controls[cid] = {
            "class": "room",
            "world": "reference",
            "anchor": ANCHOR,
            "band": control_band(ANCHOR, noise=NOISE_LAW, scale_max=SCALE_MAX),
            "file": fr["file"],
            "reference_frame": str(ref_path),
            "reference_frame_present": ref_path.exists(),
            "provenance": {
                "game": fr["game"],
                "tag": fr["tag"],
                "source": "visual-critic calibration ref set (INTERNAL; qa/../refs/INDEX.md)",
                "disguised_real_art": True,
            },
            "band_ruler": ruler,
            "band_prompt_hash": _frame_hash(fr["file"]),
        }
    return {
        "anchor": ANCHOR,
        "noise_law": NOISE_LAW,
        "scale_max": SCALE_MAX,
        "band_ruler": ruler,
        "excluded": {
            "bg2ee_fortress_party_tactical_02.jpg":
                "disclosed-defective control (baked-in UI chrome + wrong scene; 5/5 scorers flagged, "
                "scored 2-3/10 for reasons unrelated to painterly craft) — backdrop-cadence-20260708 "
                "market_square panel. A defective plate can never be a registered control.",
        },
        "controls": controls,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refs-dir", default=str(DEFAULT_REFS_DIR))
    ap.add_argument("--identity-out", default=str(IDENTITY_PATH))
    args = ap.parse_args(argv)

    registry = build(Path(args.refs_dir))
    Path(args.identity_out).write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    present = sum(1 for c in registry["controls"].values() if c["reference_frame_present"])
    print(f"wrote {len(registry['controls'])} visual control(s) to {args.identity_out} "
          f"({present} reference frame(s) present on disk); excluded "
          f"{len(registry['excluded'])} defective frame(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
