#!/usr/bin/env python3
"""build_panel.py — CRYPT CORRECTED-ANCHOR RE-PANEL (#1546 non-spend half).

Re-panels the #1528 crypt candidate (iter3_a2) under CORRECTED NEUTRAL ANCHORING
(the #1560/#1561 recipe): neutral anchors = disguised PoE2 real-art control + a
DISCLOSED CAMP house-ref (non-competing room); the crypt incumbent is a DISCLOSED
comparison slot, NOT house_best. Pure re-panel of EXISTING images (0 CU).

All blind targets normalized to identical size + format so identity can't leak via
resolution/crop/format. Mapping is recorded OUTSIDE panel/ (scorers never see it).
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json

ROOT = Path(__file__).resolve().parents[3]  # repo root
HERE = Path(__file__).resolve().parent
PANEL = HERE / "panel"
PANEL.mkdir(parents=True, exist_ok=True)

NORM = (1344, 768)  # normalize every plate to identical dims (kills size/crop tells)

# Sources (repo-relative)
SRC = {
    "candidate_iter3_a2": "qa/evidence/crypt-replicate/gen/iter3_a2.jpg",
    "distractor_iter3_a1": "qa/evidence/crypt-replicate/gen/iter3_a1.jpg",
    "distractor_iter3_flux_s40": "qa/evidence/crypt-replicate/gen/iter3_flux_s40.jpg",
    "control_poe2": "qa/evidence/crypt-replicate/loop/panel/image_2.jpg",
    "house_ref_camp": "qa/evidence/plate-audit/camp_clearing_night_truegrey_v1.png",
    "incumbent_crypt_armb_iter3": "qa/evidence/crypt-replicate/refs/incumbent_crypt_armb_iter3.jpg",
}

# Blind slot shuffle (identity hidden). Recorded in panel_mapping.json (outside panel/).
BLIND = {
    "A": "distractor_iter3_flux_s40",
    "B": "candidate_iter3_a2",        # PRIMARY candidate
    "C": "control_poe2",              # disguised real-art parity control
    "D": "distractor_iter3_a1",
}
DISCLOSED = {
    "HOUSE_REF_camp": "house_ref_camp",
    "INCUMBENT_crypt": "incumbent_crypt_armb_iter3",
}


def norm_save(src_rel, out_path):
    im = Image.open(ROOT / src_rel).convert("RGB").resize(NORM, Image.LANCZOS)
    im.save(out_path)
    return out_path


def main():
    written = {}
    for label, key in BLIND.items():
        out = norm_save(SRC[key], PANEL / f"{label}.png")
        written[label] = str(out)
    for label, key in DISCLOSED.items():
        out = norm_save(SRC[key], PANEL / f"{label}.png")
        written[label] = str(out)

    mapping = {
        "protocol": "CORRECTED-ANCHORING (issue #1560/#1561) 5-scorer blind panel. "
                    "Neutral anchors = disguised PoE2 real-art control (blind slot) + "
                    "DISCLOSED CAMP house-ref (non-competing room). Crypt incumbent "
                    "crypt_armb_iter3 = DISCLOSED comparison slot, NOT house_best.",
        "blind_slots": {k: SRC[v] for k, v in BLIND.items()},
        "blind_slot_roles": {
            "A": "distractor (recipe-b FLUX candidate iter3_flux_s40)",
            "B": "PRIMARY CANDIDATE (#1528 recipe-a Gemini two-stage iter3_a2)",
            "C": "disguised PoE2 real-art parity control",
            "D": "distractor (recipe-a variant iter3_a1)",
        },
        "disclosed": {k: SRC[v] for k, v in DISCLOSED.items()},
        "norm_dims": list(NORM),
        "note": "candidate B and incumbent are BOTH scored in the same panel per packet.",
    }
    (HERE / "panel_mapping.json").write_text(json.dumps(mapping, indent=2))

    # PR comparison strip (candidate | incumbent | camp-ref | control), labeled.
    def _font(sz):
        for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                  "/System/Library/Fonts/Helvetica.ttc"):
            try:
                return ImageFont.truetype(p, sz)
            except Exception:
                pass
        return ImageFont.load_default()

    strip_items = [
        (SRC["candidate_iter3_a2"], "CANDIDATE iter3_a2"),
        (SRC["incumbent_crypt_armb_iter3"], "INCUMBENT crypt_armb_iter3"),
        (SRC["house_ref_camp"], "HOUSE-REF camp (non-competing)"),
        (SRC["control_poe2"], "CONTROL PoE2 (real art)"),
    ]
    TW = 520
    tiles = []
    for rel, lab in strip_items:
        im = Image.open(ROOT / rel).convert("RGB")
        h = int(im.height * TW / im.width)
        im = im.resize((TW, h), Image.LANCZOS)
        canvas = Image.new("RGB", (TW, h + 40), (18, 18, 22))
        canvas.paste(im, (0, 40))
        ImageDraw.Draw(canvas).text((8, 10), lab, fill=(238, 238, 238), font=_font(18))
        tiles.append(canvas)
    th = max(t.height for t in tiles)
    out_im = Image.new("RGB", (TW * len(tiles), th), (18, 18, 22))
    for i, t in enumerate(tiles):
        out_im.paste(t, (i * TW, 0))
    out_im.save(HERE / "panel_strip.png")
    print("wrote panel/", list(written.keys()))
    print("wrote panel_mapping.json + panel_strip.png", out_im.size)


if __name__ == "__main__":
    main()
