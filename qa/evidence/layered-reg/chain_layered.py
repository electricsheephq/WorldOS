#!/usr/bin/env python3
"""chain_layered.py — LAYERED-ON-REGISTERED sequential Gemini instruction-edit chain.

Applies the layered treatment ADAPTED to the additions-locked recipe on the REUSED fit2
registered base: pass1 material/brushwork -> pass2 lighting/chiaroscuro -> pass3 staging-last
micro-scatter. Each pass is a Scenario Gemini-3.1-flash instruction-edit over the PREVIOUS pass's
winner (self-edit; NO external referenceImages), carrying STRUCTURE-LOCK + ADDITIONS-LOCK verbatim.

Winner per pass = best edge-recall-vs-base among best-of-N seeds (advisory metric per #1491;
registration floor is the BASE at 0.98 vs greybox — see notes). Full recall trail written to
recall_trail.json.

  python3 chain_layered.py --base <base.png> --greybox <greybox.png> --out <dir> --n 2
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]  # qa/evidence/layered-reg -> repo root
_GODOT_TOOLS = str(_REPO / "extensions" / "renderers" / "godot" / "tools")
if _GODOT_TOOLS not in sys.path:
    sys.path.insert(0, _GODOT_TOOLS)
sys.path.insert(0, str(_REPO / "qa"))

from scenario_gen import (  # noqa: E402
    API_BASE, CONTROLNET_PATH, _load_credentials, _auth_headers, _post_json,
    _job_id_from_create, _poll_job, _download_job_assets, _upload_image,
)
from plate_overlays import registration_recall  # noqa: E402

_PLATE_W, _PLATE_H = 1344, 768
_MODEL = "model_google-gemini-3-1-flash"

PASSES = [
    ("p1_material", "pass1_material.txt"),
    ("p2_lighting", "pass2_lighting.txt"),
    ("p3_scatter",  "pass3_scatter.txt"),
]


def _downscale(path: str) -> None:
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.size != (_PLATE_W, _PLATE_H):
        im.resize((_PLATE_W, _PLATE_H), Image.LANCZOS).save(path)


def _run_pass(headers, endpoint, prompt, base_png, out_dir, tag, n, seed0):
    base_asset = _upload_image(headers, base_png)
    print(f"[chain] {tag}: base {os.path.basename(base_png)} uploaded -> {base_asset}")
    saved = []
    for i in range(n):
        seed = seed0 + i * 111
        body = {"prompt": prompt, "image": base_asset, "numSamples": 1,
                "resolution": "2K", "seed": seed}
        res = _post_json(endpoint, headers, body)
        job_id = _job_id_from_create(res, f"{tag} seed{seed}")
        print(f"[chain] {tag} seed {seed} job {job_id}")
        job = _poll_job(headers, job_id, f"{tag}_s{seed}", 300)
        got = _download_job_assets(headers, job, out_dir, f"{tag}_s{seed}")
        for a in got:
            _downscale(a["path"])
        saved.extend(got)
    return saved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--greybox", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=2)
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    key, secret = _load_credentials()
    headers = _auth_headers(key, secret)
    endpoint = API_BASE + CONTROLNET_PATH.format(model_id=_MODEL)

    base_png = args.base
    trail = {"base": base_png,
             "base_recall_vs_greybox": round(registration_recall(args.greybox, base_png), 4),
             "passes": []}
    print(f"[chain] base recall vs greybox = {trail['base_recall_vs_greybox']}")

    prev_winner = base_png
    for idx, (tag, prompt_file) in enumerate(PASSES):
        prompt = (_HERE / prompt_file).read_text(encoding="utf-8").strip()
        seed0 = 1000 + idx * 1000
        samples = _run_pass(headers, endpoint, prompt, prev_winner, args.out, tag, args.n, seed0)
        scored = []
        for s in samples:
            r_base = registration_recall(base_png, s["path"])       # vs ORIGINAL registered base
            r_prev = registration_recall(prev_winner, s["path"])    # vs previous pass (no-drift guard)
            r_grey = registration_recall(args.greybox, s["path"])   # true registration number
            scored.append({"path": s["path"], "recall_vs_base": round(r_base, 4),
                           "recall_vs_prev": round(r_prev, 4), "recall_vs_greybox": round(r_grey, 4)})
            print(f"[chain] {tag} {os.path.basename(s['path'])}: "
                  f"vs_base={r_base:.4f} vs_prev={r_prev:.4f} vs_greybox={r_grey:.4f}")
        # winner = highest recall vs the ORIGINAL registered base (keeps the whole chain anchored)
        winner = max(scored, key=lambda x: x["recall_vs_base"])
        trail["passes"].append({"tag": tag, "prompt_file": prompt_file,
                                "samples": scored, "winner": winner})
        print(f"[chain] {tag} WINNER {os.path.basename(winner['path'])} "
              f"(vs_base={winner['recall_vs_base']})")
        prev_winner = winner["path"]

    trail["final_winner"] = prev_winner
    (Path(args.out) / "recall_trail.json").write_text(json.dumps(trail, indent=2))
    print(f"[chain] DONE final winner {prev_winner}")
    print(json.dumps(trail, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
