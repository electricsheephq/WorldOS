#!/usr/bin/env python3
"""build_overlays.py — registration overlays for the camera-pin img2img strength sweep.

For each strength candidate, writes overlay_<strength>.png (the INPUT plate's structural edges, bright
magenta, composited over the candidate — same primitive as qa/evidence/1470/build_overlays.py and
qa/evidence/1556/gate.py, via qa/plate_overlays.py) so registration drift is visible, not just a number.
Also writes a side-by-side progression strip (input | 0.20 | 0.30 | 0.40).

Deterministic, offline (PIL only). Regenerate: python3 qa/evidence/camerapin-img2img/build_overlays.py
"""
import sys
from pathlib import Path

from PIL import Image

_QA = Path(__file__).resolve().parents[2]
if str(_QA) not in sys.path:
    sys.path.insert(0, str(_QA))
from plate_overlays import W, H, edge_mask, recall  # noqa: E402

_HERE = Path(__file__).resolve().parent
INPUT = _QA / "evidence" / "crypt-fresh" / "crypt_fresh_v1.png"
CANDIDATES = {
    "0.20": _HERE / "candidates" / "strength_020.png",
    "0.30": _HERE / "candidates" / "strength_030.png",
    "0.40": _HERE / "candidates" / "strength_040.png",
}
OUT = _HERE / "overlays"
SMALL = (896, 512)


def build_overlay(input_edges: Image.Image, candidate_path: Path, strength: str) -> float:
    cand = Image.open(candidate_path).convert("RGB").resize((W, H))
    cand_edges = edge_mask(cand, 24)
    r = recall(input_edges, cand_edges)

    edge_rgba = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    magenta = Image.new("RGBA", (W, H), (255, 0, 220, 255))
    edge_rgba.paste(magenta, (0, 0), input_edges)
    over = Image.alpha_composite(cand.convert("RGBA"), edge_rgba).convert("RGB")
    over.resize(SMALL).save(OUT / f"overlay_strength_{strength.replace('.', '')}.png")
    cand.resize(SMALL).save(OUT / f"plate_strength_{strength.replace('.', '')}.png")
    return r


def main() -> None:
    OUT.mkdir(exist_ok=True)
    inp = Image.open(INPUT).convert("RGB").resize((W, H))
    input_edges = edge_mask(inp, 24)
    inp.resize(SMALL).save(OUT / "plate_input.png")

    results = {}
    for strength, path in CANDIDATES.items():
        results[strength] = build_overlay(input_edges, path, strength)
        print(f"strength={strength}  recall_vs_input={results[strength]:.4f}")

    # Progression strip: input | 0.20 | 0.30 | 0.40, each panel labeled by filename only (no text
    # burned in — ADDITIONS-LOCK discipline extends to not drawing on the evidence images).
    thumbs = [Image.open(OUT / "plate_input.png")] + [
        Image.open(OUT / f"plate_strength_{s.replace('.', '')}.png") for s in CANDIDATES
    ]
    w, h = thumbs[0].size
    strip = Image.new("RGB", (w * len(thumbs), h), (0, 0, 0))
    for i, t in enumerate(thumbs):
        strip.paste(t, (i * w, 0))
    strip.save(OUT / "progression_strip.png")
    print("progression_strip.png -> input | 0.20 | 0.30 | 0.40")


if __name__ == "__main__":
    main()
