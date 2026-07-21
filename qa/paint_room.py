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
  python3 qa/paint_room.py dwing_room_1 --depth <path> --geometry qa/room_geometries/dwing_room_1_geometry.json
      # ^ #1619: compose flavor + GENERATED structural block from the geometry JSON.
      #   Without --geometry the static recipe is used verbatim (today's behavior).
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


def resolve_recipe(cls: dict, geometry_path: str | None) -> dict:
    """#1619 (additive): WITH a geometry JSON, return a copy of the recipe class whose
    base_prompt/gemini_grounding are composed as per-class flavor + the DETERMINISTIC
    structural block generated from the geometry (render_recipe) — nobody freehands what a
    program can pin. WITHOUT --geometry, return the class dict UNCHANGED (byte-identical to
    today; VISION additive invariant). Never mutates the loaded recipes.
    """
    if geometry_path is None:
        return cls
    from render_recipe import render_recipe  # local import: the default path stays untouched
    geometry = json.loads(Path(geometry_path).read_text())
    flavor, gemini_flavor = cls.get("flavor"), cls.get("gemini_flavor")
    if not flavor or not gemini_flavor:
        raise SystemExit(
            "--geometry requires 'flavor' + 'gemini_flavor' keys on the recipe class (the only "
            "hand-authored prose left — the structural block is generated). This class lacks "
            "them; add the flavor sentence or paint without --geometry.")
    rendered = render_recipe(geometry, flavor=flavor, gemini_flavor=gemini_flavor)
    return {**cls, "base_prompt": rendered["base_prompt"],
            "gemini_grounding": rendered["gemini_grounding"]}


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
    ap.add_argument("--geometry", default=None,
                    help="room geometry JSON (qa/room_geometries/*.json) — composes the recipe "
                         "as per-class flavor + the DETERMINISTIC structural block generated "
                         "from the geometry (render_recipe, #1619): feature counts, room "
                         "shape, door walls, focal placement, negatives. Absent: the static "
                         "recipe is used verbatim (today's behavior).")
    ap.add_argument("--boxes", default=None,
                    help="room_boxes.json sidecar for the depth room — enables the HARD registration "
                         "gate: a styled recall < 0.60 triggers the brazier-beacon corrective warp "
                         "(qa/reregister_plate.py) and, if still < 0.60 after correction, FAILS the "
                         "run non-zero. Absent: warn-only (backward-compatible).")
    args = ap.parse_args()

    cls = RECIPES["classes"][args.room_class]
    if args.geometry:
        cls = resolve_recipe(cls, args.geometry)
        print(f"[paint_room] --geometry: structural recipe block generated from {args.geometry} (#1619)")
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

    base_beacon_advisory = None
    # ★ BASE-STAGE BEACON GATE (with --boxes): the styled pass can only KEEP fire it inherits —
    # a base that drew the braziers UNLIT leaves the beacons undetectable, and Gemini then INVENTS
    # the fire with count/position liberties (measured: b1 grew a 4th brazier; b2 bases had no
    # detectable fire at all). The base is depth-registered by construction, so when fire IS lit
    # the solve must be stable and tight; if not, redraw with extra seeds (flux is the cheap stage).
    if args.boxes:
        from overlay_boxes import blob_solve as _bs  # noqa: E402
        from reregister_plate import _max_err_cells as _mec, fit_similarity as _fs  # noqa: E402
        _bx = json.loads(Path(args.boxes).read_text())
        def _base_beacons_ok(path: str) -> bool:
            sv = _bs(_bx, Path(path))
            if "error" in sv or "matched_pairs" not in sv:
                return False
            return (not _fs(sv["matched_pairs"]).get("unstable")) and _mec(sv) <= 0.5
        if not _base_beacons_ok(winner["saved"][0]["path"]):
            others = sorted((d for d in draws if d is not winner),
                            key=lambda d: -next((t["recall"] for t in table if t["seed"] == d["seed"]), 0))
            for alt in others:
                if _base_beacons_ok(alt["saved"][0]["path"]):
                    print(f"[paint_room] base-beacon gate: seed {winner['seed']} unlit/undetectable -> "
                          f"switching to seed {alt['seed']} (lit beacons)")
                    winner = alt
                    break
            else:
                for extra_seed in (12348, 12349, 12350):
                    stem = f"{args.room_class}_flux_s{extra_seed}"
                    print(f"[paint_room] base-beacon gate: redrawing seed={extra_seed}")
                    nd = _flux_draw(headers, cls, flux, control_asset, extra_seed, out_dir, stem)
                    draws.append(nd)
                    if depth_local is not None:
                        r = registration_recall(str(depth_local), nd["saved"][0]["path"])
                        table.append({"seed": extra_seed, "recall": round(r, 4),
                                      "path": nd["saved"][0]["path"]})
                    if _base_beacons_ok(nd["saved"][0]["path"]):
                        winner = nd
                        break
                else:
                    # ADVISORY ONLY (calibration 2026-07-22): room_1's KNOWN-GOOD base — the chain
                    # that passed err_cells, panel, blind adjudication AND the walk gate — ALSO
                    # fails this detector (err 1.13, 2 blobs): flux fire is too dim for a blob
                    # detector tuned on styled brightness. A hard fail here blocks good chains.
                    # The styled-stage err_cells gate remains the authoritative adjudicator
                    # (it passed the good chain and failed every invented one).
                    print("[paint_room] ⚠ base beacons not detector-visible after redraws "
                          "(advisory — flux fire is often too dim for the detector; the styled-"
                          "stage err_cells gate adjudicates)")
                    base_beacon_advisory = "advisory-undetectable"

    base_asset = winner["saved"][0]["asset_id"]
    base_path = winner["saved"][0]["path"]
    print(f"[paint_room] BASE = seed {winner['seed']} ({base_asset})")

    result = {"class": args.room_class, "control_asset": control_asset,
              "draws": [{"seed": d["seed"], "job": d["job_id"],
                         "asset": d["saved"][0]["asset_id"]} for d in draws],
              "selection": table, "base": {"seed": winner["seed"], "asset": base_asset,
                                           "path": base_path}}
    if base_beacon_advisory:
        result["base_beacon_gate"] = base_beacon_advisory

    registration_failed = False
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
            styled_recall = round(registration_recall(str(depth_local), str(final_path)), 4)
            result["styled"]["recall_vs_depth"] = styled_recall
            # The Gemini pass can silently RECOMPOSE structure (measured 3/3 on the dwing cycle:
            # pillar/doorway multiplication took base 0.96 -> styled 0.63). A big base->styled drop
            # is that signature — warn LOUD so the operator eyeballs the final before any panel.
            # winner recall from the SELECTION table (sorted desc; [0] = the adopted base).
            # (codex #1614 catch: the old read used a nonexistent "selected" key, so base_recall
            # was ALWAYS None and the drop guard never fired — every report showed "base None".)
            base_recall = (result["selection"][0]["recall"] if result.get("selection") else None)
            drop = (base_recall - styled_recall) if isinstance(base_recall, (int, float)) else None
            if (drop is not None and drop > 0.15) or styled_recall < 0.60:
                result["styled"]["registration_warning"] = (
                    f"styled recall {styled_recall} (base {base_recall}) — structure-lock likely "
                    "violated (invented/multiplied features); eyeball before panel")
                print(f"[paint_room] ⚠ REGISTRATION WARNING: {result['styled']['registration_warning']}")

            # HARD GATE (#1618 upgrade): recall CANNOT gate registration — measured 2026-07-16: a
            # draw at recall 0.6377 (above the old 0.60 floor) sat 1.45 CELLS off. With --boxes the
            # gate is SOLVE-BASED: brazier-beacon err_cells must be <= 0.35 at the stamped ortho.
            # Over the bar => attempt the SIMILARITY warp (rotation term — Gemini measurably rotates
            # plates 1-4.75°), re-solve, adopt if it clears; still over => REGISTRATION FAILED
            # (non-zero). Recall stays RECORDED (drift telemetry) but no longer gates. Without
            # --boxes: warn-only (backward compatible).
            if args.boxes:
                from PIL import Image  # noqa: E402
                from overlay_boxes import blob_solve  # noqa: E402
                from reregister_plate import _max_err_cells, apply_similarity, fit_similarity  # noqa: E402
                boxes_sc = json.loads(Path(args.boxes).read_text())
                solve0 = blob_solve(boxes_sc, final_path)
                corr = {"gate": "err_cells<=0.35 (solve-based, #1618)"}
                if "error" in solve0:
                    corr.update(attempted=False, error=solve0["error"])
                    registration_failed = True  # unsolvable = ungateable = not shippable
                else:
                    err0 = _max_err_cells(solve0)
                    corr["before_err_cells"] = err0
                    if err0 > 0.35:
                        # ITERATIVE similarity refine (proven: room_1 1.43 -> 0.31 in 2 passes).
                        # An UNSTABLE fit here is usually STRUCTURAL (an invented/relocated fire
                        # beacon contorts the 3-point fit — measured: an invented 4th brazier read
                        # as rot 12-21deg @ scale 0.65) — the retry lever is a REPAINT, not a warp.
                        from reregister_plate import reregister_iterative  # noqa: E402
                        corrected_path = out_dir / f"{args.room_class}_final_1344_reregistered.png"
                        res = reregister_iterative(boxes_sc, final_path, corrected_path, max_iters=3)
                        corr.update(attempted=True, **{k: v for k, v in res.items() if k != "passed"})
                        corr["path"] = str(corrected_path)
                        if res.get("passed"):
                            subprocess.run(["cp", str(corrected_path), str(final_path)], check=True)
                            corr["adopted"] = True
                            print(f"[paint_room] iterative warp adopted: err_cells {err0} -> {res['err_cells']}")
                        else:
                            registration_failed = True
                result["styled"]["corrected"] = corr
            else:
                result["styled"]["corrected"] = {"attempted": False,
                                                 "note": "no --boxes: warn-only (no hard gate)"}
        print(f"[paint_room] FINAL: {final_path}")

    (out_dir / "report.json").write_text(json.dumps(result, indent=1))
    print(f"[paint_room] report: {out_dir/'report.json'}")
    if registration_failed:
        print("[paint_room] ✖ REGISTRATION FAILED: beacon err_cells > 0.35 after similarity warp "
              f"(see {out_dir/'report.json'})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
