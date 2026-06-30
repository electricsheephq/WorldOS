#!/usr/bin/env python3
"""generate_room.py — the REPEATABLE painterly-backdrop workflow (gfx M-A).

ONE command turns a camera-pinned base/greybox plate into a firelit PoE2-caliber
painterly room plate, via Scenario img2img on model_z-image + our painterly LoRA.
The recipe (model, LoRA, strength, steps, guidance, per-room prompts) lives in the
committed manifest `extensions/renderers/shared/room_recipes.json` so any room is
regeneratable on demand and the workflow is auditable.

  # relight the crypt base plate (4 variants) into firelit chiaroscuro:
  python3 generate_room.py --room crypt \
      --base-plate ~/worldos-session-notes/scenario-assets/crypt_plate_v2_s50.png \
      --out ~/worldos-session-notes/scenario-assets/crypt_gen

  # refine an already-generated Scenario asset (low strength, protect composition):
  python3 generate_room.py --room crypt --refine-from asset_XXXX --strength 0.30 --out <dir>

  # inspect the resolved request without spending credits:
  python3 generate_room.py --room crypt --base-plate <png> --dry-run

Design notes:
- The lever from L6 6.5 -> 8 is LIGHTING DRAMA (single warm key, deep blue-violet
  shadows, hard cast shadows), NOT more texture. Keep `strength` low (~0.30-0.45) so
  the camera-pinned composition (the contract camera's dimetric layout) does NOT drift.
- Reuses scenario_gen.py's proven helpers (auth/upload/poll/download) — same account,
  same endpoints. img2img rides the txt2img endpoint with an `image` + `strength` body.
- Engine = sole writer; this is a generation/view-layer tool only. It never writes
  engine state; it produces a PNG the renderer/registry later consume by slot.
"""
import argparse
import json
import os
import sys

# Reuse the proven Scenario plumbing (auth, upload, post, poll, download, meta).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from scenario_gen import (  # noqa: E402
    API_BASE,
    CONTROLNET_PATH,
    _load_credentials,
    _auth_headers,
    _post_json,
    _job_id_from_create,
    _poll_job,
    _download_job_assets,
    _upload_image,
    _write_meta,
)

RECIPE_PATH = os.path.normpath(
    os.path.join(_HERE, "..", "..", "shared", "room_recipes.json")
)


def _load_recipe() -> dict:
    if not os.path.isfile(RECIPE_PATH):
        sys.exit("[generate_room] ERROR: recipe manifest not found: %s" % RECIPE_PATH)
    with open(RECIPE_PATH, "r") as f:
        return json.load(f)


def _build_prompt(recipe: dict, room: str, lighting: str = "firelit") -> tuple:
    rooms = recipe.get("rooms", {})
    if room not in rooms:
        sys.exit(
            "[generate_room] ERROR: unknown room '%s'. Known: %s"
            % (room, ", ".join(sorted(rooms)))
        )
    rc = rooms[room]
    # day/night dimension: same camera-pinned greybox, swapped lighting prompt (zero-drift regen). The
    # firelit (night) template is the default; daylight floods the room cool with a stained-glass shaft.
    if lighting == "daylight":
        template = recipe.get("daylight_positive_template", recipe["firelit_positive_template"])
        negative = recipe.get("daylight_negative", recipe["washout_negative"])
    else:
        template = recipe["firelit_positive_template"]
        negative = recipe["washout_negative"]
    # Sprint 4: suppress img2img-invented stray props (the owner's "lantern on top of a pillar").
    stray = recipe.get("stray_item_negative", "")
    if stray:
        negative = f"{negative}, {stray}"
    positive = template.format(
        room=room,
        key_light=rc["key_light"],
        shadow_casters=rc["shadow_casters"],
        room_detail_tokens=rc["room_detail_tokens"],
    )
    return positive, negative


def main(argv=None) -> None:
    recipe = _load_recipe()
    d = recipe["defaults"]
    ap = argparse.ArgumentParser(description="Repeatable painterly-backdrop workflow (gfx M-A)")
    ap.add_argument("--room", required=True, help="room type key in room_recipes.json (crypt|tavern|church|...)")
    ap.add_argument("--base-plate", default=None, help="local PNG path of the camera-pinned base/greybox plate to img2img from")
    ap.add_argument("--refine-from", default=None, help="an existing Scenario asset_id to refine (skips upload)")
    ap.add_argument("--out", default=None, help="output dir for the generated plates")
    ap.add_argument("--strength", type=float, default=d["strength"], help="img2img denoise strength (low=protect composition)")
    ap.add_argument("--steps", type=int, default=d["num_inference_steps"])
    ap.add_argument("--guidance", type=float, default=d["guidance"])
    ap.add_argument("--num-outputs", type=int, default=d["num_outputs"])
    ap.add_argument("--width", type=int, default=d["width"])
    ap.add_argument("--height", type=int, default=d["height"])
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--lighting", choices=["firelit", "daylight"], default="firelit",
                    help="day/night dimension: firelit (night, default) or daylight (cool sunlit, stained-glass shaft)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true", help="print the resolved request without calling the API")
    args = ap.parse_args(argv)

    # Require EXACTLY ONE image source up front so an ambiguous/missing combo fails fast (incl. on --dry-run),
    # not silently as "<upload:None>" or only at submit time.
    src_count = (1 if args.base_plate else 0) + (1 if args.refine_from else 0)
    if src_count != 1:
        ap.error("provide EXACTLY ONE of --base-plate <png> or --refine-from <asset_id>")

    positive, negative = _build_prompt(recipe, args.room, args.lighting)
    # Standalone base models (model_z-image) run img2img via POST /generate/custom/{modelId}
    # with `image` + `strength` (the txt2img endpoint rejects standalone models). The base model
    # is in the PATH; the painterly LoRA rides `loras`/`lorasScale` (string ids, per the proven job).
    endpoint = API_BASE + CONTROLNET_PATH.format(model_id=recipe["base_model"])
    body = {
        "prompt": positive,
        "negativePrompt": negative,
        "image": None,  # filled below (asset id)
        "strength": args.strength,
        "numInferenceSteps": args.steps,
        "guidance": args.guidance,
        "numSamples": args.num_outputs,
        "width": args.width,
        "height": args.height,
        "loras": [recipe["lora"]],
        "lorasScale": [recipe["lora_scale"]],
    }
    if args.seed is not None:
        body["seed"] = args.seed

    if args.dry_run:
        preview = dict(body)
        preview["image"] = args.refine_from or ("<upload:%s>" % args.base_plate)
        print("[generate_room] DRY-RUN img2img (%s)" % args.room)
        print(json.dumps(preview, indent=2))
        print("  endpoint  : %s" % endpoint)
        print("  recipe    : %s" % RECIPE_PATH)
        return

    out_dir = args.out or os.path.join(os.getcwd(), "room_gen_%s" % args.room)
    os.makedirs(out_dir, exist_ok=True)
    key, secret = _load_credentials()
    headers = _auth_headers(key, secret)

    if args.refine_from:
        image_ref = args.refine_from
    elif args.base_plate:
        image_ref = _upload_image(headers, os.path.expanduser(args.base_plate))
    else:
        sys.exit("[generate_room] ERROR: need --base-plate <png> or --refine-from <asset_id>.")
    body["image"] = image_ref

    res = _post_json(endpoint, headers, body)
    job_id = _job_id_from_create(res, "img2img create")
    print("[generate_room] %s img2img job submitted: %s (strength=%s)" % (args.room, job_id, args.strength))
    job = _poll_job(headers, job_id, "img2img", args.timeout)
    saved = _download_job_assets(headers, job, out_dir, "room_%s" % args.room)
    _write_meta(out_dir, {
        "room": args.room, "image_ref": image_ref, "strength": args.strength,
        "steps": args.steps, "guidance": args.guidance, "num_outputs": args.num_outputs,
        "model_id": recipe["base_model"], "lora": recipe["lora"], "lora_scale": recipe["lora_scale"],
        "prompt": positive, "negative_prompt": negative, "job_id": job_id,
        "assets": saved, "source": "generate_room-img2img",
    })
    print("[generate_room] OK — room=%s job=%s assets=%d -> %s" % (args.room, job_id, len(saved), out_dir))


if __name__ == "__main__":
    main()
