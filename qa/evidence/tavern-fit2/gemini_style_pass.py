#!/usr/bin/env python3
"""gemini_style_pass.py — the TAVERN-REGEN (M-ALIGN) Gemini instruction-edit style pass.

Runbook step 6: a DIRECT Scenario Gemini instruction-edit job over the flux depth-CN registered
base, carrying the STRUCTURE-LOCK + ADDITIONS-LOCK clauses verbatim, NO external referenceImages
(the base being edited is the only image passed — the sanctioned self-edit case). Best-of-N seeds.
Not via generate_room.py --layered (its pass2 POPULATE would invent furniture, violating
ADDITIONS-LOCK — the exact defect this regen exists to kill).

  python3 gemini_style_pass.py <base.png> <prompt.txt> <out_dir> [--n 3] [--model model_google-gemini-3-1-flash]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_GODOT_TOOLS = str(Path(__file__).resolve().parents[3] / "extensions" / "renderers" / "godot" / "tools")
if _GODOT_TOOLS not in sys.path:
    sys.path.insert(0, _GODOT_TOOLS)

from scenario_gen import (  # noqa: E402
    API_BASE, CONTROLNET_PATH,
    _load_credentials, _auth_headers, _post_json, _job_id_from_create,
    _poll_job, _download_job_assets, _upload_image,
)

_PLATE_W, _PLATE_H = 1344, 768


def _downscale(path: str) -> None:
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.size != (_PLATE_W, _PLATE_H):
        im.resize((_PLATE_W, _PLATE_H), Image.LANCZOS).save(path)
        print(f"[gemini_style_pass] downscaled {os.path.basename(path)} {im.size} -> {_PLATE_W}x{_PLATE_H}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("base")
    ap.add_argument("prompt")
    ap.add_argument("out_dir")
    ap.add_argument("--n", type=int, default=3, help="best-of-N seeds")
    ap.add_argument("--model", default="model_google-gemini-3-1-flash")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(argv)

    prompt = Path(args.prompt).read_text(encoding="utf-8").strip()
    os.makedirs(args.out_dir, exist_ok=True)
    key, secret = _load_credentials()
    headers = _auth_headers(key, secret)

    base_asset = _upload_image(headers, args.base)
    print(f"[gemini_style_pass] base uploaded -> {base_asset}")
    endpoint = API_BASE + CONTROLNET_PATH.format(model_id=args.model)

    saved_all = []
    for i in range(args.n):
        seed = 1000 + i * 111
        # Gemini instruction-edit: prompt + single image (the base) + NO referenceImages beyond it.
        body = {"prompt": prompt, "image": base_asset, "numSamples": 1,
                "resolution": "2K", "seed": seed}
        res = _post_json(endpoint, headers, body)
        job_id = _job_id_from_create(res, f"style seed{seed}")
        print(f"[gemini_style_pass] seed {seed} job {job_id} (model={args.model})")
        job = _poll_job(headers, job_id, f"style_s{seed}", args.timeout)
        saved = _download_job_assets(headers, job, args.out_dir, f"candidate_s{seed}")
        for a in saved:
            _downscale(a["path"])
            print(f"[gemini_style_pass] seed {seed} -> {a['path']}")
        saved_all.extend(saved)

    print(f"[gemini_style_pass] DONE — {len(saved_all)} candidates in {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
