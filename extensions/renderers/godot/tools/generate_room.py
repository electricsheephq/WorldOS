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

  # OPTIONAL --controlnet (W6.3b, #1470): condition the base/layout pass on the greybox as a
  # ControlNet depth|canny control image (Pipeline A, scenario_gen._cmd_controlnet, proven
  # 2026-06-22) instead of unconditioned img2img — locks the paint to the authored geometry so
  # paint-vs-grid drift goes to ~zero. Default OFF — absent flag = byte-identical img2img. Composes
  # with --layered (conditioning applies to the base pass; the Gemini detail/staging passes ride on
  # top unchanged, restoring paint quality). The greybox = the --base-plate (or --refine-from asset).
  python3 generate_room.py --room camp_clearing_night --base-plate <greybox.png> \
      --controlnet depth --layered --out <dir>

Design notes:
- The lever from L6 6.5 -> 8 is LIGHTING DRAMA (single warm key, deep blue-violet
  shadows, hard cast shadows), NOT more texture. Keep `strength` low (~0.30-0.45) so
  the camera-pinned composition (the contract camera's dimetric layout) does NOT drift.
- Reuses scenario_gen.py's proven helpers (auth/upload/poll/download) — same account,
  same endpoints. img2img rides the txt2img endpoint with an `image` + `strength` body.
- Engine = sole writer; this is a generation/view-layer tool only. It never writes
  engine state; it produces a PNG the renderer/registry later consume by slot.
"""
from __future__ import annotations

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


def _render_pass_prompt(recipe: dict, room: str, pass_spec: dict, slot_key: str,
                          slot_block: str = "layered") -> str:
    """Fill a layered-pipeline pass2/pass3 prompt TEMPLATE with the active room's slot values.

    Fixes the tavern-turned-crypt defect: pass2_detail_populate.prompt_template and
    pass3_staging_last.prompt_template (room_recipes.json:layered_pipeline_2026_07_02) carry
    {room_*} placeholders instead of hardcoded crypt nouns. Each room supplies its own values
    under rooms.<room>.layered.<slot_key> (pass2_slots|pass3_slots). Falls back to the legacy
    unrendered "prompt" key (pre-templatization schema) if present, for forward compatibility.

    `slot_block` selects which per-room block to read slots from: "layered" (night, default)
    or "layered_day" (#1291 G5a day-state variant — only pass3_slots exists there; pass2 is
    shared with the night "layered" block since detail/populate craft is lighting-agnostic).
    """
    template = pass_spec.get("prompt_template")
    if template is None:
        # legacy fallback: an un-templatized recipe (shouldn't happen post-fix, but fail soft)
        return pass_spec["prompt"]
    rooms = recipe.get("rooms", {})
    rc = rooms.get(room, {})
    block_rc = rc.get(slot_block, {})
    slots = block_rc.get(slot_key)
    if not slots:
        sys.exit(
            f"[generate_room] ERROR: --layered requested for room '{room}' but room_recipes.json has "
            f"no rooms.{room}.{slot_block}.{slot_key} slot values (needed to fill the {slot_key} prompt template). "
            f"Add a `{slot_block}` block for this room (see rooms.crypt.layered for the pattern)."
        )
    try:
        return template.format(**slots)
    except KeyError as e:
        sys.exit(
            f"[generate_room] ERROR: rooms.{room}.{slot_block}.{slot_key} is missing slot {e} required by the "
            f"{slot_key} prompt template."
        )
    except (IndexError, ValueError) as e:
        sys.exit(
            f"[generate_room] ERROR: rooms.{room}.{slot_block}.{slot_key} has a malformed slot value for the "
            f"{slot_key} prompt template ({e})."
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
    # daylight_outdoor (backdrop-cadence restart, HV5) is a THIRD sibling for OPEN-AIR daylight rooms
    # (market squares, roads, camps-by-day) — the existing "daylight" template is interior-specific
    # ("tall windows", "stained-glass shaft"), which is wrong for an exterior plate; this keeps that
    # interior template untouched (church/tavern's adopted day variants are unaffected) and adds a
    # sibling with open-sky/sun wording instead. Falls back to the interior daylight template if the
    # manifest hasn't been given the outdoor template yet (forward/backward compatible).
    if lighting == "daylight_outdoor":
        template = recipe.get("daylight_outdoor_positive_template",
                              recipe.get("daylight_positive_template", recipe["firelit_positive_template"]))
        negative = recipe.get("daylight_negative", recipe["washout_negative"])
    elif lighting == "daylight":
        template = recipe.get("daylight_positive_template", recipe["firelit_positive_template"])
        negative = recipe.get("daylight_negative", recipe["washout_negative"])
    elif lighting == "firelit_outdoor":
        # night EXTERIOR sibling of the (interior-worded) firelit default — same "single warm key +
        # deep blue-violet shadow" chiaroscuro law, minus the "interior"/"walls" phrasing that's wrong
        # for an open-air room (backdrop-cadence restart, HV5; e.g. camp_clearing_night).
        template = recipe.get("firelit_outdoor_positive_template", recipe["firelit_positive_template"])
        negative = recipe["washout_negative"]
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


# Default ControlNet base model + strength for the --controlnet base/layout pass (W6.3b, #1470).
# These mirror the PROVEN Pipeline A recipe (scenario_gen._cmd_controlnet, validated 2026-06-22:
# model_bfl-flux-1-dev with canny/depth modality at controlStrength 0.7 — the only controlnet-proven
# model on the account; our default img2img base_model is model_z-image, whose controlnet support is
# unproven). Overridable per-manifest via a top-level `controlnet` block ({"model", "control_strength",
# optional "loras"/"lora_scales"}) or per-invocation via --control-model / --control-strength.
_CONTROLNET_DEFAULT_MODEL = "model_bfl-flux-1-dev"
_CONTROLNET_DEFAULT_STRENGTH = 0.7


def _resolve_controlnet(recipe: dict, args) -> dict | None:
    """Resolve {model, modality, strength, loras, lora_scales} for the --controlnet base pass, or
    None when it's off.

    Returns None whenever args.controlnet is unset so callers keep the byte-identical unconditioned
    img2img path. Precedence for model/strength: CLI flag > recipe `controlnet` block > proven
    Pipeline A default.

    LoRA handling INTENTIONALLY differs from the img2img path: the img2img painterly LoRA is trained
    on the z-image base model and the default ControlNet model (flux.1-dev) REJECTS it with HTTP 400
    ("Allowed model types: flux.1-lora, flux.1-composition"). So the ControlNet pass does NOT inherit
    recipe.lora — it applies ONLY LoRAs explicitly declared model-compatible in the recipe
    `controlnet` block. Default: none (the base pass locks geometry; --layered's Gemini passes
    restore paint).
    """
    if not getattr(args, "controlnet", None):
        return None
    cn_block = recipe.get("controlnet", {})
    model = args.control_model or cn_block.get("model") or _CONTROLNET_DEFAULT_MODEL
    if args.control_strength is not None:
        strength = args.control_strength
    elif "control_strength" in cn_block:
        strength = cn_block["control_strength"]
    else:
        strength = _CONTROLNET_DEFAULT_STRENGTH
    loras = list(cn_block.get("loras") or [])
    lora_scales = list(cn_block.get("lora_scales") or [])
    return {"model": model, "modality": args.controlnet, "strength": float(strength),
            "loras": loras, "lora_scales": lora_scales}


def _build_base_pass_request(recipe: dict, args, positive: str, negative: str,
                              image_ref, cn: dict | None) -> tuple:
    """Build (endpoint, body) for the base/layout pass — two shapes on the SAME custom endpoint.

    cn is None (DEFAULT): unconditioned img2img — `image` + `strength` seed, BYTE-IDENTICAL to the
      pre-W6.3b request (the LoRA rides `loras`/`lorasScale`; the base model is in the PATH).
    cn set (--controlnet): ControlNet conditioning (Pipeline A) — the greybox rides as `controlImage`
      with a depth|canny `controlModality` + `controlStrength`, locking the paint to the authored
      geometry. Same prompt/negative/steps/guidance/size knobs; the image-conditioning fields, the
      PATH model, and the LoRA set differ (see _resolve_controlnet on why the z-image LoRA is dropped
      here). --layered's Gemini passes ride on top of this pass unchanged.
    `image_ref` is the uploaded greybox/base asset id (or None for a dry-run preview).
    """
    if cn is None:
        endpoint = API_BASE + CONTROLNET_PATH.format(model_id=recipe["base_model"])
        body = {
            "prompt": positive,
            "negativePrompt": negative,
            "image": image_ref,
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
        return endpoint, body
    endpoint = API_BASE + CONTROLNET_PATH.format(model_id=cn["model"])
    body = {
        "prompt": positive,
        "negativePrompt": negative,
        "controlImage": image_ref,
        "controlModality": cn["modality"],
        "controlStrength": cn["strength"],
        "numInferenceSteps": args.steps,
        "guidance": args.guidance,
        "numSamples": args.num_outputs,
        "width": args.width,
        "height": args.height,
    }
    if cn["loras"]:
        body["loras"] = list(cn["loras"])
        if cn["lora_scales"]:
            body["lorasScale"] = list(cn["lora_scales"])
    if args.seed is not None:
        body["seed"] = args.seed
    return endpoint, body


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
    ap.add_argument("--lighting", choices=["firelit", "daylight", "daylight_outdoor", "firelit_outdoor"],
                    default="firelit",
                    help="day/night dimension: firelit (night interior, default), daylight (cool sunlit "
                         "interior, stained-glass shaft), daylight_outdoor (open-sky sun for exterior "
                         "rooms), or firelit_outdoor (night exterior, e.g. a campfire clearing)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--layered", action="store_true",
                    help="OPTIONAL 3-pass pipeline: after the img2img layout pass, chain a Gemini "
                         "detail/populate pass then a Gemini staging-last pass (room_recipes.json:"
                         "layered_pipeline_2026_07_02). Default OFF; no flag = identical single-pass behavior.")
    ap.add_argument("--day", action="store_true",
                    help="DAY-STATE variant of --layered (#1291 G5a). Implies --lighting daylight for pass1; "
                         "pass2 (detail/populate) is unchanged/shared with the night recipe; pass3 (staging) "
                         "uses the DAY staging law (room_recipes.json:layered_pipeline_day_2026_07_03 / "
                         "rooms.<room>.layered_day.pass3_slots) instead of the night chiaroscuro staging law. "
                         "No effect without --layered (day/night only differs in the layered pipeline's "
                         "staging pass + pass1 lighting).")
    ap.add_argument("--controlnet", choices=["depth", "canny"], default=None,
                    help="W6.3b (#1470): condition the base/layout pass on the greybox as a ControlNet "
                         "depth|canny control image (Pipeline A, scenario_gen._cmd_controlnet, proven "
                         "2026-06-22) instead of unconditioned img2img — locks paint to the authored "
                         "geometry (paint-vs-grid drift -> ~0). The greybox = the --base-plate (or "
                         "--refine-from asset). Default OFF; absent flag = byte-identical img2img. "
                         "Composes with --layered (conditioning applies to the base pass only; the "
                         "Gemini detail/staging passes ride on top unchanged).")
    ap.add_argument("--control-strength", type=float, default=None,
                    help="ControlNet conditioning strength 0.0-1.0 for --controlnet (default: recipe "
                         "controlnet.control_strength, else 0.7). No effect without --controlnet.")
    ap.add_argument("--control-model", default=None,
                    help="Scenario model id for the --controlnet base pass (default: recipe "
                         "controlnet.model, else the proven model_bfl-flux-1-dev). No effect without "
                         "--controlnet.")
    ap.add_argument("--dry-run", action="store_true", help="print the resolved request without calling the API")
    args = ap.parse_args(argv)

    if args.day and args.lighting != "daylight_outdoor":
        # Don't clobber an explicit outdoor choice (backdrop-cadence restart) — --day's whole-manifest
        # default remains the INTERIOR daylight template (byte-identical for existing --day callers:
        # church/tavern/bosshall), but --day --lighting daylight_outdoor lets an exterior room (e.g.
        # market_square) keep its open-sky pass1 template while still routing pass2/pass3 through the
        # day-state layered pipeline (layered_pipeline_day_2026_07_03).
        args.lighting = "daylight"

    # Require EXACTLY ONE image source up front so an ambiguous/missing combo fails fast (incl. on --dry-run),
    # not silently as "<upload:None>" or only at submit time.
    src_count = (1 if args.base_plate else 0) + (1 if args.refine_from else 0)
    if src_count != 1:
        ap.error("provide EXACTLY ONE of --base-plate <png> or --refine-from <asset_id>")

    # Fail fast on a missing --day recipe entry BEFORE the (expensive, billed) pass1 img2img job
    # runs — --layered --day chains pass1 -> pass2 -> pass3, and pass3's day branch is the one
    # that actually needs layered_pipeline_day_2026_07_03; checking only at that point would
    # burn a pass1 job on a room that was never wired for the day variant.
    if args.layered and args.day and not recipe.get("layered_pipeline_day_2026_07_03"):
        sys.exit(
            f"[generate_room] ERROR: --day requested but recipe manifest has no "
            f"layered_pipeline_day_2026_07_03 entry: {RECIPE_PATH}"
        )

    positive, negative = _build_prompt(recipe, args.room, args.lighting)
    # W6.3b (#1470): --controlnet swaps the unconditioned img2img seed for greybox ControlNet
    # conditioning on the base/layout pass. cn is None (default) -> byte-identical img2img below.
    # Standalone base models (model_z-image) run img2img via POST /generate/custom/{modelId}
    # with `image` + `strength` (the txt2img endpoint rejects standalone models). The base model
    # is in the PATH; the painterly LoRA rides `loras`/`lorasScale` (string ids, per the proven job).
    cn = _resolve_controlnet(recipe, args)
    endpoint, body = _build_base_pass_request(recipe, args, positive, negative, None, cn)

    if args.dry_run:
        preview = dict(body)
        img_src = args.refine_from or ("<upload:%s>" % args.base_plate)
        if cn is None:
            preview["image"] = img_src
            print("[generate_room] DRY-RUN img2img (%s)" % args.room)
        else:
            preview["controlImage"] = img_src
            print("[generate_room] DRY-RUN --controlnet %s base pass (%s, model=%s, strength=%.2f)"
                  % (cn["modality"], args.room, cn["model"], cn["strength"]))
        print(json.dumps(preview, indent=2))
        print("  endpoint  : %s" % endpoint)
        print("  recipe    : %s" % RECIPE_PATH)
        if args.layered:
            layered = recipe.get("layered_pipeline_2026_07_02", {})
            if args.day:
                layered_day = recipe.get("layered_pipeline_day_2026_07_03", {})
                pass2_spec = layered_day.get("pass2_detail_populate_day", {})
                pass3_spec = layered_day.get("pass3_staging_last_day", {})
                print("[generate_room] DRY-RUN --layered --day: would additionally chain pass2 (detail/populate, "
                      "day variant) then pass3 (staging-last, DAY law) via %s" % pass2_spec.get("model", "?"))
                pass2_prompt = _render_pass_prompt(recipe, args.room, pass2_spec, "pass2_slots")
                pass3_prompt = _render_pass_prompt(recipe, args.room, pass3_spec, "pass3_slots", slot_block="layered_day")
                print("  pass2 prompt [DAY] (rendered for room=%s, room_recipes.json:layered_pipeline_day_2026_07_03.pass2_detail_populate_day):" % args.room)
                print("    %s" % pass2_prompt[:160] + " ...")
                print("  pass3 prompt [DAY] (rendered for room=%s, room_recipes.json:layered_pipeline_day_2026_07_03.pass3_staging_last_day):" % args.room)
                print("    %s" % pass3_prompt[:160] + " ...")
            else:
                pass2_spec = layered.get("pass2_detail_populate", {})
                pass3_spec = layered.get("pass3_staging_last", {})
                print("[generate_room] DRY-RUN --layered: would additionally chain pass2 (detail/populate) "
                      "then pass3 (staging-last) via %s" % pass2_spec.get("model", "?"))
                pass2_prompt = _render_pass_prompt(recipe, args.room, pass2_spec, "pass2_slots")
                pass3_prompt = _render_pass_prompt(recipe, args.room, pass3_spec, "pass3_slots")
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
    # Rebuild with the resolved greybox/base asset id filled into the image-conditioning field
    # (image for img2img, controlImage for --controlnet).
    endpoint, body = _build_base_pass_request(recipe, args, positive, negative, image_ref, cn)

    res = _post_json(endpoint, headers, body)
    if cn is None:
        job_id = _job_id_from_create(res, "img2img create")
        print("[generate_room] %s img2img job submitted: %s (strength=%s)" % (args.room, job_id, args.strength))
    else:
        job_id = _job_id_from_create(res, "controlnet create")
        print("[generate_room] %s --controlnet %s job submitted: %s (model=%s, controlStrength=%.2f)"
              % (args.room, cn["modality"], job_id, cn["model"], cn["strength"]))
    job = _poll_job(headers, job_id, "controlnet" if cn else "img2img", args.timeout)
    saved = _download_job_assets(headers, job, out_dir, "room_%s" % args.room)
    meta = {
        "room": args.room, "image_ref": image_ref, "strength": args.strength,
        "steps": args.steps, "guidance": args.guidance, "num_outputs": args.num_outputs,
        "model_id": cn["model"] if cn else recipe["base_model"],
        "lora": recipe["lora"], "lora_scale": recipe["lora_scale"],
        "prompt": positive, "negative_prompt": negative, "job_id": job_id,
        "assets": saved,
        "source": "generate_room-controlnet" if cn else "generate_room-img2img",
    }
    if cn:
        # W6.3b (#1470): record the ControlNet conditioning so the plate's greybox-lock is auditable.
        meta["controlnet"] = {
            "modality": cn["modality"], "control_strength": cn["strength"],
            "control_model": cn["model"], "control_image_ref": image_ref,
            "loras": cn["loras"],
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
        # pass2 SLOT VALUES are shared between day and night (#1291 G5a) — the craft/de-clone/
        # populate TARGETS are lighting-agnostic — but the pass2 PROMPT TEMPLATE branches on
        # --day (the night template explicitly protects dark chiaroscuro, which fights a
        # daylit pass1 base); pass3 (staging) also fully branches on --day.
        if args.day:
            layered_day = recipe.get("layered_pipeline_day_2026_07_03")
            if not layered_day:
                sys.exit("[generate_room] ERROR: --day requested but recipe manifest has no "
                          "layered_pipeline_day_2026_07_03 entry: %s" % RECIPE_PATH)
            pass2_spec = layered_day["pass2_detail_populate_day"]
            pass2_prompt = _render_pass_prompt(recipe, args.room, pass2_spec, "pass2_slots")
            pass3_spec = layered_day["pass3_staging_last_day"]
            pass3_prompt = _render_pass_prompt(recipe, args.room, pass3_spec, "pass3_slots", slot_block="layered_day")
            pass3_recipe_entry = "layered_pipeline_day_2026_07_03"
        else:
            pass2_spec = layered["pass2_detail_populate"]
            pass2_prompt = _render_pass_prompt(recipe, args.room, pass2_spec, "pass2_slots")
            pass3_spec = layered["pass3_staging_last"]
            pass3_prompt = _render_pass_prompt(recipe, args.room, pass3_spec, "pass3_slots")
            pass3_recipe_entry = "layered_pipeline_2026_07_02"

        pass2_stem = "room_%s_pass2_detail_day" % args.room if args.day else "room_%s_pass2_detail" % args.room
        pass2_job, pass2_saved = _run_gemini_pass(
            headers, pass2_spec, pass1_ref, out_dir,
            pass2_stem, args.timeout, pass2_prompt)
        if not pass2_saved:
            sys.exit("[generate_room] ERROR: --layered pass2 (detail/populate%s) produced no assets"
                      % (" day" if args.day else ""))
        # Feed pass3 the REMOTE 2K asset (full resolution, no re-upload, no lossy round-trip);
        # only the FINAL pass output is downscaled to the plate contract below.
        pass2_ref = pass2_saved[0]["asset_id"]

        pass3_stem = "room_%s_pass3_staging_day" % args.room if args.day else "room_%s_pass3_staging" % args.room
        pass3_job, pass3_saved = _run_gemini_pass(
            headers, pass3_spec, pass2_ref, out_dir,
            pass3_stem, args.timeout, pass3_prompt)
        if not pass3_saved:
            sys.exit("[generate_room] ERROR: --layered pass3 (staging-last%s) produced no assets"
                      % (" day" if args.day else ""))
        _downscale_to_plate(pass3_saved[0]["path"], args.width, args.height)

        meta["layered"] = {
            "day": args.day,
            "pass1_selected": pass1_best,
            "pass2_job_id": pass2_job, "pass2_assets": pass2_saved, "pass2_prompt": pass2_prompt,
            "pass3_job_id": pass3_job, "pass3_assets": pass3_saved, "pass3_prompt": pass3_prompt,
            "final_plate": pass3_saved[0],
            "recipe_entry": pass3_recipe_entry,
        }
        print("[generate_room] --layered%s OK — final staged plate: %s"
              % (" --day" if args.day else "", pass3_saved[0]))

    _write_meta(out_dir, meta)
    print("[generate_room] OK — room=%s job=%s assets=%d -> %s" % (args.room, job_id, len(saved), out_dir))


if __name__ == "__main__":
    main()
