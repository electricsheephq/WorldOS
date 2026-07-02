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

  # OPTIONAL 3-pass LAYERED pipeline (validated 2026-07-02, 7.6 median vs 6.0 single-pass;
  # see room_recipes.json:layered_pipeline_2026_07_02). Default OFF — omitting --layered is
  # byte-identical to the plain single-pass img2img above.
  python3 generate_room.py --room crypt --base-plate <png> --layered --out <dir>

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


def _render_pass_prompt(recipe: dict, room: str, pass_spec: dict, slot_key: str) -> str:
    """Fill a layered-pipeline pass2/pass3 prompt TEMPLATE with the active room's slot values.

    Fixes the tavern-turned-crypt defect: pass2_detail_populate.prompt_template and
    pass3_staging_last.prompt_template (room_recipes.json:layered_pipeline_2026_07_02) carry
    {room_*} placeholders instead of hardcoded crypt nouns. Each room supplies its own values
    under rooms.<room>.layered.<slot_key> (pass2_slots|pass3_slots). Falls back to the legacy
    unrendered "prompt" key (pre-templatization schema) if present, for forward compatibility.
    """
    template = pass_spec.get("prompt_template")
    if template is None:
        # legacy fallback: an un-templatized recipe (shouldn't happen post-fix, but fail soft)
        return pass_spec["prompt"]
    rooms = recipe.get("rooms", {})
    rc = rooms.get(room, {})
    layered_rc = rc.get("layered", {})
    slots = layered_rc.get(slot_key)
    if not slots:
        sys.exit(
            "[generate_room] ERROR: --layered requested for room '%s' but room_recipes.json has "
            "no rooms.%s.layered.%s slot values (needed to fill the %s prompt template). "
            "Add a `layered` block for this room (see rooms.crypt.layered for the pattern)."
            % (room, room, slot_key, slot_key)
        )
    try:
        return template.format(**slots)
    except KeyError as e:
        sys.exit(
            "[generate_room] ERROR: rooms.%s.layered.%s is missing slot %s required by the "
            "%s prompt template." % (room, slot_key, e, slot_key)
        )


def _run_gemini_pass(headers: dict, pass_spec: dict, image_ref: str, out_dir: str, stem: str,
                      timeout: int, prompt: str) -> tuple:
    """Run one Gemini instruction-edit pass (detail/populate or staging) on `image_ref`.

    Reuses the same proven Scenario helpers as the img2img pass (_post_json/_poll_job/
    _download_job_assets) — the Gemini endpoint takes prompt+image+numSamples+resolution
    (no strength knob; preservation is prompt-driven, see room_recipes.json). `prompt` is the
    already-rendered (slot-filled) prompt string for the active room — see _render_pass_prompt.
    Returns (job_id, [asset metadata dicts: {asset_id, path, bytes}]).
    """
    model = pass_spec["model"]
    endpoint = API_BASE + CONTROLNET_PATH.format(model_id=model)
    body = {"prompt": prompt, "image": image_ref, "numSamples": 1, "resolution": "2K"}
    res = _post_json(endpoint, headers, body)
    job_id = _job_id_from_create(res, "%s create" % stem)
    print("[generate_room] --layered %s job submitted: %s (model=%s)" % (stem, job_id, model))
    job = _poll_job(headers, job_id, stem, timeout)
    saved = _download_job_assets(headers, job, out_dir, stem)
    return job_id, saved


def _staging_law_distance(path: str) -> tuple:
    """Score one pass-1 sample's fit to the measured PoE staging-law luma bands.

    Law (see room_recipes.json:washout_negative / gemini_polish_populate_pass_2026_07_01
    and the crypt textured-greybox campaign): a well-staged dramatic-chiaroscuro plate has
    ~66-80% of pixels near-black (L<26, deep shadow/void), ~2-4% lit (L>60, the hot key-light
    pool), and a low overall median luma (~0-15) — i.e. small pools of light in a mostly-dark
    scene, NOT an evenly-lit "museum lighting" wash. Targets below are the mid of those bands.

    Returns (distance, near_black_frac, lit_frac, median_L) so callers can print stats.
    Lower distance = closer fit to the staging law; used to pick the best of N pass-1 samples
    deterministically instead of always chaining saved[0].
    """
    from PIL import Image

    im = Image.open(path).convert("L")
    pixels = list(im.getdata())
    n = len(pixels)
    near_black = sum(1 for p in pixels if p < 26) / n
    lit = sum(1 for p in pixels if p > 60) / n
    sorted_pixels = sorted(pixels)
    median_L = sorted_pixels[n // 2]

    target_near_black, target_lit, target_median = 0.73, 0.03, 8
    distance = (
        abs(near_black - target_near_black) / target_near_black
        + abs(lit - target_lit) / target_lit
        + abs(median_L - target_median) / 30
    )
    return distance, near_black, lit, median_L


def _pick_best_pass1_sample(saved: list) -> dict:
    """Deterministically pick the pass-1 sample to chain into pass2/pass3.

    Replaces the old `saved[0]` (first-sample-wins) selection: score every downloaded
    pass-1 image by `_staging_law_distance` and chain the argmin. Prints each candidate's
    stats and which one won so the choice is auditable.
    """
    scored = []
    for asset in saved:
        distance, near_black, lit, median_L = _staging_law_distance(asset["path"])
        scored.append((distance, near_black, lit, median_L, asset))
        print(
            "[generate_room] --layered pass1 candidate %s: distance=%.3f "
            "near_black=%.1f%% lit=%.1f%% median_L=%d"
            % (asset.get("asset_id", asset.get("path")), distance, near_black * 100, lit * 100, median_L)
        )
    best = min(scored, key=lambda t: t[0])
    print(
        "[generate_room] --layered pass1 WINNER: %s (distance=%.3f)"
        % (best[4].get("asset_id", best[4].get("path")), best[0])
    )
    return best[4]


def _downscale_to_plate(path: str, width: int, height: int) -> None:
    """Downscale a pass output to the plate contract size (LANCZOS) if it came back larger.

    The Gemini endpoint is asked for resolution=2K, which can exceed the 1344x768-class
    plate contract; normalize in place so downstream renderer consumption is unaffected.
    """
    try:
        from PIL import Image
    except ImportError:
        print("[generate_room] WARNING: Pillow not installed, skipping downscale of %s" % path)
        return
    im = Image.open(path).convert("RGB")
    if im.size != (width, height):
        im.resize((width, height), Image.LANCZOS).save(path)
        print("[generate_room] --layered downscaled %s %s -> %dx%d" % (path, im.size, width, height))


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
    ap.add_argument("--layered", action="store_true",
                    help="OPTIONAL 3-pass pipeline: after the img2img layout pass, chain a Gemini "
                         "detail/populate pass then a Gemini staging-last pass (room_recipes.json:"
                         "layered_pipeline_2026_07_02). Default OFF; no flag = identical single-pass behavior.")
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
        if args.layered:
            layered = recipe.get("layered_pipeline_2026_07_02", {})
            print("[generate_room] DRY-RUN --layered: would additionally chain pass2 (detail/populate) "
                  "then pass3 (staging-last) via %s" % layered.get("pass2_detail_populate", {}).get("model", "?"))
            pass2_prompt = _render_pass_prompt(recipe, args.room, layered.get("pass2_detail_populate", {}), "pass2_slots")
            pass3_prompt = _render_pass_prompt(recipe, args.room, layered.get("pass3_staging_last", {}), "pass3_slots")
            print("  pass2 prompt (rendered for room=%s, room_recipes.json:layered_pipeline_2026_07_02.pass2_detail_populate):" % args.room)
            print("    %s" % pass2_prompt[:160] + " ...")
            print("  pass3 prompt (rendered for room=%s, room_recipes.json:layered_pipeline_2026_07_02.pass3_staging_last):" % args.room)
            print("    %s" % pass3_prompt[:160] + " ...")
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
    meta = {
        "room": args.room, "image_ref": image_ref, "strength": args.strength,
        "steps": args.steps, "guidance": args.guidance, "num_outputs": args.num_outputs,
        "model_id": recipe["base_model"], "lora": recipe["lora"], "lora_scale": recipe["lora_scale"],
        "prompt": positive, "negative_prompt": negative, "job_id": job_id,
        "assets": saved, "source": "generate_room-img2img",
    }

    if args.layered and saved:
        # OPTIONAL 3-pass pipeline (room_recipes.json:layered_pipeline_2026_07_02). ORDER LAW:
        # detail/populate MUST run before staging-last (the detail pass flattens lighting; the
        # staging pass restores it without losing the detail gains). Sequential jobs only —
        # concurrent Scenario paints collide/silently no-output.
        layered = recipe.get("layered_pipeline_2026_07_02")
        if not layered:
            sys.exit("[generate_room] ERROR: --layered requested but recipe manifest has no "
                      "layered_pipeline_2026_07_02 entry: %s" % RECIPE_PATH)
        # _download_job_assets returns metadata dicts ({asset_id, path, bytes}) — the pass-1
        # output already LIVES on Scenario, so chain its asset id directly (no re-upload).
        # Pick DETERMINISTICALLY by staging-law luma distance across all N pass-1 samples
        # (was saved[0] — first-sample-wins, which ignored the other 3 candidates).
        pass1_best = _pick_best_pass1_sample(saved)
        pass1_ref = pass1_best["asset_id"]

        # Render each pass's prompt TEMPLATE with the active room's slot values (fixes the
        # tavern-turned-crypt defect — pass2/pass3 previously ran the crypt-hardcoded prompt
        # unconditionally regardless of --room). See _render_pass_prompt + rooms.<room>.layered.
        pass2_prompt = _render_pass_prompt(recipe, args.room, layered["pass2_detail_populate"], "pass2_slots")
        pass3_prompt = _render_pass_prompt(recipe, args.room, layered["pass3_staging_last"], "pass3_slots")

        pass2_job, pass2_saved = _run_gemini_pass(
            headers, layered["pass2_detail_populate"], pass1_ref, out_dir,
            "room_%s_pass2_detail" % args.room, args.timeout, pass2_prompt)
        if not pass2_saved:
            sys.exit("[generate_room] ERROR: --layered pass2 (detail/populate) produced no assets")
        # Feed pass3 the REMOTE 2K asset (full resolution, no re-upload, no lossy round-trip);
        # only the FINAL pass output is downscaled to the plate contract below.
        pass2_ref = pass2_saved[0]["asset_id"]

        pass3_job, pass3_saved = _run_gemini_pass(
            headers, layered["pass3_staging_last"], pass2_ref, out_dir,
            "room_%s_pass3_staging" % args.room, args.timeout, pass3_prompt)
        if not pass3_saved:
            sys.exit("[generate_room] ERROR: --layered pass3 (staging-last) produced no assets")
        _downscale_to_plate(pass3_saved[0]["path"], args.width, args.height)

        meta["layered"] = {
            "pass1_selected": pass1_best,
            "pass2_job_id": pass2_job, "pass2_assets": pass2_saved, "pass2_prompt": pass2_prompt,
            "pass3_job_id": pass3_job, "pass3_assets": pass3_saved, "pass3_prompt": pass3_prompt,
            "final_plate": pass3_saved[0],
            "recipe_entry": "layered_pipeline_2026_07_02",
        }
        print("[generate_room] --layered OK — final staged plate: %s" % pass3_saved[0])

    _write_meta(out_dir, meta)
    print("[generate_room] OK — room=%s job=%s assets=%d -> %s" % (args.room, job_id, len(saved), out_dir))


if __name__ == "__main__":
    main()
