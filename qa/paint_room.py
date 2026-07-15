#!/usr/bin/env python3
"""THE dedicated room painter — one command, pinned API shape, recipe-driven prompts.

Born from the 2026-07-15 slot-swap postmortem (qa/evidence/gemini-restyle/FLUX_ROOTCAUSE.md): a
context-compacted agent freehanded Scenario calls, silently put the depth map in the img2img
`image` slot instead of the ControlNet `controlImage` slot, and burned hours misdiagnosing the
resulting clay output as a provider regression. THE RULE: nobody freehands a paint call. This
script is the only sanctioned path; prompts and params come from qa/unified_paint_recipes.json,
the API payload shape is pinned here, and every job's ACTUAL submitted body is logged beside its
output so a repro claim can always be diffed against service-recorded inputs.

Chain (the canonical registered pipeline):
  depth PNG (build_room_unified.cs) → flux depth-CN, one job per recipe seed (seed[0] byte-
  reproduces the canonical crypt base) → edge-recall SELECTION vs the depth (qa/select_best_draw
  semantics) → Gemini structure-lock style pass → exact 1344x768 resize → report.json.

Usage:
  python3 qa/paint_room.py crypt --depth /tmp/cryptv36/depth_v36b.png --out-dir /tmp/paint/crypt
  python3 qa/paint_room.py tavern --depth <path-or-asset_id> [--skip-gemini] [--seeds 12345]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

QA = Path(__file__).resolve().parent
sys.path.insert(0, str(QA))
sys.path.insert(0, str(QA.parent / "extensions" / "renderers" / "godot" / "tools"))

from scenario_gen import (  # noqa: E402  (the PROVEN helpers — auth/upload/poll/download)
    API_BASE, CONTROLNET_PATH, _load_credentials, _auth_headers, _post_json,
    _job_id_from_create, _poll_job, _download_job_assets, _upload_image,
)
from plate_overlays import registration_recall  # noqa: E402

RECIPES = json.loads((QA / "unified_paint_recipes.json").read_text())


def _flux_draw(headers: dict, cls: dict, flux: dict, control_asset: str, seed: int,
               out_dir: Path, stem: str) -> dict:
    endpoint = API_BASE + CONTROLNET_PATH.format(model_id=flux["model_id"])
    # ★ THE PINNED SHAPE: depth goes in controlImage. Never `image` (that is img2img). ★
    body = {
        "prompt": cls["base_prompt"],
        "controlImage": control_asset,
        "controlModality": flux["control_modality"],
        "controlStrength": flux["control_strength"],
        "width": flux["width"],
        "height": flux["height"],
        "numSamples": 1,
        "seed": seed,
    }
    res = _post_json(endpoint, headers, body)
    job_id = _job_id_from_create(res, f"{stem} create")
    job = _poll_job(headers, job_id, stem, 300)
    saved = _download_job_assets(headers, job, str(out_dir), stem)
    (out_dir / f"{stem}.submitted.json").write_text(json.dumps(body, indent=1))
    return {"job_id": job_id, "seed": seed, "saved": saved, "submitted": body}


def _gemini_pass(headers: dict, cls: dict, gem: dict, image_asset: str,
                 out_dir: Path, stem: str) -> dict:
    endpoint = API_BASE + CONTROLNET_PATH.format(model_id=gem["model_id"])
    prompt = cls["gemini_grounding"] + "\n\n" + gem["structure_lock"]
    body = {"prompt": prompt, "image": image_asset, "numSamples": 1,
            "resolution": gem["resolution"]}
    res = _post_json(endpoint, headers, body)
    job_id = _job_id_from_create(res, f"{stem} create")
    job = _poll_job(headers, job_id, stem, 300)
    saved = _download_job_assets(headers, job, str(out_dir), stem)
    (out_dir / f"{stem}.submitted.json").write_text(json.dumps(body, indent=1))
    return {"job_id": job_id, "saved": saved}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("room_class", choices=sorted(RECIPES["classes"]))
    ap.add_argument("--depth", required=True, help="depth PNG path OR an existing asset_id")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--seeds", default=None,
                    help="comma-separated seed override (default: recipe seeds = 3-draw selection)")
    ap.add_argument("--skip-gemini", action="store_true", help="stop after the selected flux base")
    args = ap.parse_args()

    cls = RECIPES["classes"][args.room_class]
    flux, gem = RECIPES["flux"], RECIPES["gemini"]
    seeds = ([int(s) for s in args.seeds.split(",")] if args.seeds else flux["seeds"])
    out_dir = Path(args.out_dir or f"/tmp/paint/{args.room_class}")
    out_dir.mkdir(parents=True, exist_ok=True)

    key, secret = _load_credentials()
    headers = _auth_headers(key, secret)

    depth_local = None
    if args.depth.startswith("asset_"):
        control_asset = args.depth
    else:
        depth_local = Path(args.depth)
        if not depth_local.is_file():
            ap.error(f"depth not found: {depth_local}")
        print(f"[paint_room] uploading depth: {depth_local}")
        control_asset = _upload_image(headers, str(depth_local))

    draws = []
    for seed in seeds:
        stem = f"{args.room_class}_flux_s{seed}"
        print(f"[paint_room] flux draw seed={seed} (controlImage={control_asset})")
        draws.append(_flux_draw(headers, cls, flux, control_asset, seed, out_dir, stem))

    # SELECTION: edge-recall vs the local depth (ranking contract). With an asset-id depth and no
    # local file, selection degrades to seed[0] with a loud note.
    winner = draws[0]
    table = []
    if depth_local is not None:
        for d in draws:
            path = d["saved"][0]["path"]
            r = registration_recall(str(depth_local), path)
            table.append({"seed": d["seed"], "recall": round(r, 4), "path": path})
        table.sort(key=lambda t: t["recall"], reverse=True)
        for t in table:
            print(f"[paint_room] recall {t['recall']:.4f} seed={t['seed']}")
        winner = next(d for d in draws if d["seed"] == table[0]["seed"])
    else:
        print("[paint_room] WARNING: depth given as asset_id, no local file — selection skipped (seed[0]).")

    base_asset = winner["saved"][0]["asset_id"]
    base_path = winner["saved"][0]["path"]
    print(f"[paint_room] BASE = seed {winner['seed']} ({base_asset})")

    result = {"class": args.room_class, "control_asset": control_asset,
              "draws": [{"seed": d["seed"], "job": d["job_id"],
                         "asset": d["saved"][0]["asset_id"]} for d in draws],
              "selection": table, "base": {"seed": winner["seed"], "asset": base_asset,
                                           "path": base_path}}

    if not args.skip_gemini:
        print("[paint_room] gemini structure-lock pass…")
        g = _gemini_pass(headers, cls, gem, base_asset, out_dir, f"{args.room_class}_styled")
        styled_path = g["saved"][0]["path"]
        final_path = out_dir / f"{args.room_class}_final_1344.png"
        subprocess.run(["sips", "-z", "768", "1344", styled_path, "--out", str(final_path)],
                       check=True, capture_output=True)
        result["styled"] = {"job": g["job_id"], "asset": g["saved"][0]["asset_id"],
                            "path": str(final_path)}
        if depth_local is not None:
            result["styled"]["recall_vs_depth"] = round(
                registration_recall(str(depth_local), str(final_path)), 4)
        print(f"[paint_room] FINAL: {final_path}")

    (out_dir / "report.json").write_text(json.dumps(result, indent=1))
    print(f"[paint_room] report: {out_dir/'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
