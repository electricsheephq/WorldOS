#!/usr/bin/env python3
"""scenario_gen.py — generate 2D art (portraits / scenes / textures) via the Scenario API.

A sibling of meshy_gen.py / tripo_gen.py. Scenario is a NON-Eva, NON-OpenClaw image
backend: it has its own API key + secret and never touches the gateway. Use it for the
GT-style 2D art the engine and viewer consume (portraits, scenes, maps, item icons), and
as paint-over reference for the 3D bake pipeline.

This tool is WorldOS-ORIGINAL code; the ART it downloads (PNGs) is NOT committed — it
lives under content/worlds/_private/ (gitignored, owner-licensed).

VERIFIED Scenario contract (confirmed LIVE — these differ from the first research pass):
  * Auth: HTTP BASIC  — username = API key, password = SECRET. (NOT Bearer.)
  * Base: https://api.cloud.scenario.com/v1   (NOT api.scenario.com)
  * List models:           GET  /v1/models
  * Text-to-image:         POST /v1/generate/txt2img  body {"modelId","prompt",...} -> a JOB
  * Upscale:               POST /v1/generate/upscale  body {"image",...}            -> a JOB
  * Poll a job:            GET  /v1/jobs/{jobId}       until status=success, asset ids land
  * Fetch an asset (PNG):  GET  /v1/assets/{assetId}   -> {"asset":{"url": <download>}}
  * Custom model: same txt2img endpoint, just pass the custom modelId.
  Confirmed live by probing: POST /v1/generate/txt2img with no modelId -> 400
  "\"modelId\" must be a string"; POST /v1/generate/upscale empty -> 400 "image parameter
  is required"; GET /v1/jobs/{bad} -> 404 "Job ... not found"; GET /v1/models -> 200.

The key+secret are read from ~/.worldos/scenario.key + ~/.worldos/scenario.secret, or
$WORLDOS_SCENARIO_API_KEY + $WORLDOS_SCENARIO_SECRET. They are NEVER printed/logged and
NEVER written into any repo file.

Usage:
    # auth smoke-test (GET /v1/models — no generation, no credit spend)
    python3 scenario_gen.py --test-key

    # list the models available on the account (and their ids)
    python3 scenario_gen.py list-models

    # text-to-image (modelId required — pick one from list-models)
    python3 scenario_gen.py generate --model-id <id> --prompt "a grizzled dwarf cleric ..." \
        --world baldurs-gate --scope dwarf-cleric

    # upscale an existing asset (by Scenario asset id) or an image URL
    python3 scenario_gen.py upscale --asset-id <id> --world baldurs-gate --scope dwarf-cleric

    # plan + est credits WITHOUT calling the API
    python3 scenario_gen.py generate --model-id <id> --prompt "..." --world w --scope s --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.cloud.scenario.com/v1"
MODELS_PATH = "/models"
TXT2IMG_PATH = "/generate/txt2img"
UPSCALE_PATH = "/generate/upscale"
CONTROLNET_PATH = "/generate/custom/{model_id}"
ASSETS_PATH = "/assets"
JOB_PATH = "/jobs/{job_id}"
ASSET_PATH = "/assets/{asset_id}"

POLL_INTERVAL_SEC = 4
DEFAULT_TIMEOUT_SEC = 600

# Rough per-op credit estimates (for --dry-run only; the API is the source of truth).
CREDIT_EST = {"generate_per_sample": 1, "upscale": 2}


# --------------------------------------------------------------------------- #
# Key handling. NEVER print/log the key/secret; NEVER write them to a repo file.
# --------------------------------------------------------------------------- #
def _load_credentials() -> tuple:
    key = os.environ.get("WORLDOS_SCENARIO_API_KEY", "").strip()
    secret = os.environ.get("WORLDOS_SCENARIO_SECRET", "").strip()
    if not key:
        key_path = os.path.expanduser("~/.worldos/scenario.key")
        if os.path.isfile(key_path):
            with open(key_path, "r") as f:
                key = f.read().strip()
    if not secret:
        sec_path = os.path.expanduser("~/.worldos/scenario.secret")
        if os.path.isfile(sec_path):
            with open(sec_path, "r") as f:
                secret = f.read().strip()
    if not key or not secret:
        sys.exit(
            "[scenario_gen] ERROR: missing credentials. Set $WORLDOS_SCENARIO_API_KEY + "
            "$WORLDOS_SCENARIO_SECRET, or put them in ~/.worldos/scenario.key + "
            "~/.worldos/scenario.secret."
        )
    return key, secret


def _auth_headers(key: str, secret: str) -> dict:
    token = base64.b64encode(("%s:%s" % (key, secret)).encode("utf-8")).decode("ascii")
    return {
        "Authorization": "Basic %s" % token,
        "Content-Type": "application/json",
    }


# --------------------------------------------------------------------------- #
# HTTP (urllib only — no `requests` dependency).
# --------------------------------------------------------------------------- #
def _post_json(url: str, headers: dict, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _explain_http(e.code, _read_error(e), "POST " + url)
    except urllib.error.URLError as e:
        sys.exit("[scenario_gen] ERROR: network failure on POST %s: %s" % (url, e.reason))


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _explain_http(e.code, _read_error(e), "GET " + url)
    except urllib.error.URLError as e:
        sys.exit("[scenario_gen] ERROR: network failure on GET %s: %s" % (url, e.reason))


def _read_error(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8")
    except Exception:
        return "<no body>"


def _explain_http(code: int, detail: str, what: str) -> None:
    if code == 401:
        sys.exit(
            "[scenario_gen] ERROR 401 unauthorized on %s — bad key/secret (Basic auth). "
            "Detail: %s" % (what, detail)
        )
    if code in (402, 403):
        sys.exit(
            "[scenario_gen] ERROR %d on %s — insufficient credits or forbidden. Art was NOT "
            "generated. Detail: %s" % (code, what, detail)
        )
    if code == 429:
        sys.exit(
            "[scenario_gen] ERROR 429 rate-limited on %s. Wait and retry. Detail: %s"
            % (what, detail)
        )
    sys.exit("[scenario_gen] ERROR HTTP %d on %s. Detail: %s" % (code, what, detail))


def _download(url: str, dest: str) -> int:
    """Stream a binary asset (png) to dest. Returns bytes written. Asset URLs have a finite TTL."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as out:
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                total += len(chunk)
            return total
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        sys.exit("[scenario_gen] ERROR downloading %s -> %s: %s" % (url, dest, e))


# --------------------------------------------------------------------------- #
# Job lifecycle.
# --------------------------------------------------------------------------- #
def _job_id_from_create(res: dict, what: str) -> str:
    job = res.get("job") or {}
    job_id = job.get("jobId") or job.get("id") or res.get("jobId")
    if not job_id:
        sys.exit("[scenario_gen] ERROR: %s returned no job id: %s" % (what, json.dumps(res)))
    return job_id


def _poll_job(headers: dict, job_id: str, label: str, timeout_sec: int) -> dict:
    """Poll GET /jobs/{id} until success. Returns the final job dict (carries asset ids)."""
    url = API_BASE + JOB_PATH.format(job_id=job_id)
    deadline = time.time() + timeout_sec
    last = None
    while True:
        res = _get_json(url, headers)
        job = res.get("job") or res
        status = str(job.get("status", "unknown")).lower()
        progress = job.get("progress")
        if status != last:
            tail = (" progress=%s" % progress) if progress is not None else ""
            print("[scenario_gen] %s status=%s%s" % (label, status, tail))
            last = status
        if status in ("success", "succeeded", "completed", "done"):
            return job
        if status in ("failure", "failed", "error"):
            sys.exit("[scenario_gen] ERROR: %s job FAILED: %s" % (label, json.dumps(job)))
        if status in ("canceled", "cancelled"):
            sys.exit("[scenario_gen] ERROR: %s job %s" % (label, status))
        if time.time() > deadline:
            sys.exit(
                "[scenario_gen] ERROR: %s job timed out after %ds (last status=%s). "
                "Art was NOT completed." % (label, timeout_sec, status)
            )
        time.sleep(POLL_INTERVAL_SEC)


def _job_asset_ids(job: dict) -> list:
    """Extract output asset ids from a succeeded job, best-effort across response shapes."""
    md = job.get("metadata") or {}
    ids = md.get("assetIds") or job.get("assetIds") or md.get("assets") or job.get("assets")
    out = []
    if isinstance(ids, list):
        for a in ids:
            if isinstance(a, str):
                out.append(a)
            elif isinstance(a, dict) and (a.get("id") or a.get("assetId")):
                out.append(a.get("id") or a.get("assetId"))
    return out


def _asset_url(headers: dict, asset_id: str) -> str:
    """Resolve a downloadable URL for an asset id via GET /assets/{id}."""
    res = _get_json(API_BASE + ASSET_PATH.format(asset_id=asset_id), headers)
    asset = res.get("asset") or res
    url = asset.get("url") or asset.get("downloadUrl") or asset.get("signedUrl")
    if not url:
        sys.exit("[scenario_gen] ERROR: asset %s has no download url: %s" % (asset_id, json.dumps(asset)))
    return url


# --------------------------------------------------------------------------- #
# Output dir resolution.
# --------------------------------------------------------------------------- #
def _safe_scope(scope: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(scope))[:128]


def _resolve_out(args) -> str:
    if getattr(args, "out", None):
        return os.path.abspath(args.out)
    world = _safe_scope(getattr(args, "world", "") or "")
    scope = _safe_scope(getattr(args, "scope", "") or "")
    if not world or not scope:
        sys.exit(
            "[scenario_gen] ERROR: provide --out, or both --world and --scope "
            "(output goes to content/worlds/_private/<world>/images/<scope>/)."
        )
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(repo_root, "content", "worlds", "_private", world, "images", scope)


def _download_job_assets(headers: dict, job: dict, out_dir: str, stem: str) -> list:
    """Download every output asset of a succeeded job to <out>/<stem>[_N].png. Returns metadata."""
    asset_ids = _job_asset_ids(job)
    if not asset_ids:
        sys.exit("[scenario_gen] ERROR: succeeded job has no output assets: %s" % json.dumps(job))
    saved = []
    for i, aid in enumerate(asset_ids):
        url = _asset_url(headers, aid)
        name = "%s.png" % stem if len(asset_ids) == 1 else "%s_%d.png" % (stem, i)
        dest = os.path.join(out_dir, name)
        size = _download(url, dest)
        print("[scenario_gen] downloaded %s (%d bytes)" % (name, size))
        saved.append({"asset_id": aid, "path": dest, "bytes": size})
    return saved


# --------------------------------------------------------------------------- #
# Sub-command bodies.
# --------------------------------------------------------------------------- #
def _cmd_test_key() -> None:
    """Cheap auth smoke-test: GET /v1/models. 200 == authenticated."""
    key, secret = _load_credentials()
    headers = _auth_headers(key, secret)
    req = urllib.request.Request(API_BASE + MODELS_PATH + "?pageSize=1", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        print("Scenario Auth OK")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            sys.exit("[scenario_gen] AUTH FAILED: HTTP %d — bad key/secret." % e.code)
        # Any other HTTP code still means the request was authenticated/accepted.
        print("Scenario Auth OK")
    except urllib.error.URLError as e:
        sys.exit("[scenario_gen] ERROR: network failure on --test-key: %s" % e.reason)


def _cmd_list_models(args) -> None:
    key, secret = _load_credentials()
    headers = _auth_headers(key, secret)
    res = _get_json(API_BASE + MODELS_PATH + "?pageSize=100", headers)
    models = res.get("models") or []
    if not models:
        print("[scenario_gen] (no models on this account — train/import one in the Scenario UI, "
              "or use a public modelId.)")
        return
    for m in models:
        mid = m.get("id") or m.get("modelId") or "?"
        name = m.get("name") or m.get("displayName") or ""
        status = m.get("status") or ""
        print("  %s\t%s\t%s" % (mid, status, name))
    print("[scenario_gen] %d model(s)." % len(models))


def _validate_model_id(headers: dict, model_id: str) -> None:
    """Confirm the model exists and is ready BEFORE generating, so we fail fast with an
    actionable error instead of submitting a job that silently never produces an asset."""
    res = _get_json(API_BASE + MODELS_PATH + "?pageSize=100", headers)
    models = res.get("models") or []
    match = None
    for m in models:
        if (m.get("id") or m.get("modelId")) == model_id:
            match = m
            break
    if match is None:
        sys.exit(
            "[scenario_gen] ERROR: model %s not found or not ready — run "
            "`scenario_gen.py list-models`, or train/import a model in the Scenario UI." % model_id
        )
    status = str(match.get("status") or "").lower()
    # Treat empty/unknown status as ready (some accounts omit it); only block on a clearly-not-ready state.
    ready_states = ("", "ready", "trained", "active", "available", "succeeded", "success", "completed")
    if status not in ready_states:
        sys.exit(
            "[scenario_gen] ERROR: model %s not found or not ready (status=%s) — run "
            "`scenario_gen.py list-models`, or train/import a model in the Scenario UI."
            % (model_id, status)
        )


def _cmd_generate(args) -> None:
    out_dir = _resolve_out(args)
    if args.dry_run:
        print("[scenario_gen] DRY-RUN text-to-image")
        print("  model-id  : %s" % args.model_id)
        print("  prompt    : %s" % args.prompt)
        print("  samples   : %d" % args.num_samples)
        print("  size      : %dx%d" % (args.width, args.height))
        print("  out dir   : %s" % out_dir)
        print("  est credits: ~%d (API is source of truth)" % (CREDIT_EST["generate_per_sample"] * args.num_samples))
        return
    os.makedirs(out_dir, exist_ok=True)
    key, secret = _load_credentials()
    headers = _auth_headers(key, secret)
    # Fail fast on a missing/not-ready model instead of wasting a long poll on a job that never lands.
    _validate_model_id(headers, args.model_id)
    body = {
        "modelId": args.model_id,
        "prompt": args.prompt,
        "numSamples": args.num_samples,
        "width": args.width,
        "height": args.height,
    }
    if args.negative_prompt:
        body["negativePrompt"] = args.negative_prompt
    if args.seed is not None:
        body["seed"] = args.seed
    res = _post_json(API_BASE + TXT2IMG_PATH, headers, body)
    job_id = _job_id_from_create(res, "txt2img create")
    print("[scenario_gen] txt2img job submitted: %s (model=%s)" % (job_id, args.model_id))
    job = _poll_job(headers, job_id, "txt2img", args.timeout)
    stem = _safe_scope(getattr(args, "scope", "") or "scenario") or "scenario"
    saved = _download_job_assets(headers, job, out_dir, stem)
    _write_meta(out_dir, {
        "prompt": args.prompt, "model_id": args.model_id, "num_samples": args.num_samples,
        "width": args.width, "height": args.height, "job_id": job_id,
        "assets": saved, "source": "scenario-txt2img",
    })
    print("[scenario_gen] OK — job=%s assets=%d" % (job_id, len(saved)))


def _cmd_upscale(args) -> None:
    out_dir = _resolve_out(args)
    image_ref = args.asset_id or args.image_url
    if not image_ref:
        sys.exit("[scenario_gen] ERROR: upscale needs --asset-id or --image-url.")
    if args.dry_run:
        print("[scenario_gen] DRY-RUN upscale")
        print("  image     : %s" % image_ref)
        print("  scale     : %sx" % args.scale)
        print("  out dir   : %s" % out_dir)
        print("  est credits: ~%d (API is source of truth)" % CREDIT_EST["upscale"])
        return
    os.makedirs(out_dir, exist_ok=True)
    key, secret = _load_credentials()
    headers = _auth_headers(key, secret)
    body = {"image": image_ref, "scalingFactor": args.scale}
    res = _post_json(API_BASE + UPSCALE_PATH, headers, body)
    job_id = _job_id_from_create(res, "upscale create")
    print("[scenario_gen] upscale job submitted: %s" % job_id)
    job = _poll_job(headers, job_id, "upscale", args.timeout)
    stem = (_safe_scope(getattr(args, "scope", "") or "scenario") or "scenario") + "_upscaled"
    saved = _download_job_assets(headers, job, out_dir, stem)
    _write_meta(out_dir, {
        "image": image_ref, "scale": args.scale, "job_id": job_id,
        "assets": saved, "source": "scenario-upscale",
    })
    print("[scenario_gen] OK — job=%s assets=%d" % (job_id, len(saved)))


def _upload_image(headers: dict, local_path: str) -> str:
    """Upload a local image file to Scenario as a private asset and return its asset id.

    The Scenario asset-create endpoint accepts a JSON body with:
      { "image": "data:<mime>;base64,<b64>", "name": "<filename>" }
    and returns { "asset": { "id": "<asset_id>", ... } }.

    This is the proven approach (confirmed live 2026-06-22 on POST /v1/assets):
    no presigned-URL multipart flow is needed — a single JSON POST suffices.
    """
    if not os.path.isfile(local_path):
        sys.exit("[scenario_gen] ERROR: control image not found: %s" % local_path)
    ext = os.path.splitext(local_path)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif"}
    mime = mime_map.get(ext, "image/png")
    with open(local_path, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode("ascii")
    data_url = "data:%s;base64,%s" % (mime, b64)
    name = os.path.basename(local_path)
    body = {"image": data_url, "name": name}
    res = _post_json(API_BASE + ASSETS_PATH, headers, body)
    asset = res.get("asset") or res
    asset_id = asset.get("id") or asset.get("assetId")
    if not asset_id:
        sys.exit(
            "[scenario_gen] ERROR: upload returned no asset id: %s" % json.dumps(res)
        )
    print("[scenario_gen] uploaded %s -> asset_id=%s" % (name, asset_id))
    return asset_id


# Known Scenario model-family incompatibility (confirmed live 2026-07-10, ARM C / PLATE SPRINT): a
# LoRA trained on the z-image base model is REJECTED by flux.1-dev-family ControlNet models with HTTP
# 400 ("Allowed model types: flux.1-lora, flux.1-composition"). The WorldOS painterly LoRA is z-image-
# trained (room_recipes.json top-level `lora`). This guard rejects the known-bad combo LOUDLY before
# any credits are spent, instead of letting it 400 deep inside _post_json or (worse) silently drop the
# LoRA the way the malformed loras:[{"assetId":...}] payload shape used to (see _cmd_controlnet below).
_FLUX_MODEL_PREFIXES = ("model_bfl-flux",)
_Z_IMAGE_ONLY_LORA_IDS = {"model_MB22WaRCBLtfhi5R2CRpHoEL"}  # the WorldOS painterly LoRA (z-image-trained)


def guard_flux_lora_compat(model_id: str, lora_ids) -> None:
    """Reject loudly (sys.exit) if a known z-image-only LoRA is being applied to a flux model.

    Both scenario_gen.py's --controlnet command and generate_room.py's --controlnet base pass share
    this guard (generate_room imports it) so the incompatible combo is caught the same way regardless
    of entry point.
    """
    if not lora_ids or not model_id:
        return
    if any(model_id.startswith(p) for p in _FLUX_MODEL_PREFIXES):
        bad = [lid for lid in lora_ids if lid in _Z_IMAGE_ONLY_LORA_IDS]
        if bad:
            sys.exit(
                "[scenario_gen] ERROR: LoRA(s) %s are trained on model_z-image and are REJECTED by "
                "flux model '%s' (HTTP 400: \"Allowed model types: flux.1-lora, flux.1-composition\"). "
                "Use a flux-compatible LoRA or drop --loras for the ControlNet/flux pass." % (bad, model_id)
            )


def _cmd_controlnet(args) -> None:
    """Pipeline A: painterly-on-grid ControlNet generation (FLUX.1-dev canny/depth).

    Proven MCP recipe (2026-06-22):
      model:   model_bfl-flux-1-dev
      params:  { controlImage:<scenario_asset_id>, controlModality:'canny',
                 controlStrength:0.7, width:1024, height:1024,
                 numSamples:N, seed:S, prompt:... }
    The output painterly floor preserves the grid + obstacle cells with ~zero drift
    when the control image is a 2:1 dimetric structure plate.

    REST contract (confirmed live 2026-06-22):
      * Upload local image  : POST /v1/assets  {"image":"data:...", "name":"..."}
                              -> {"asset":{"id":"<id>"}}
      * Submit ControlNet   : POST /v1/generate/custom/<modelId>
                              {"prompt", "controlImage", "controlModality",
                               "controlStrength", "width", "height", "numSamples", "seed",
                               "loras", "lorasScale"}
                              -> job (same shape as txt2img)
      * Poll + download     : same _poll_job / _job_asset_ids / _asset_url / _download

    LoRA payload shape (fixed 2026-07-10, PLATE SPRINT Phase 3, ARM C): the Scenario custom-model
    endpoint expects `loras` as a list of bare model-id STRINGS (["<model_id>", ...]) plus a parallel
    `lorasScale` list of floats — NOT a list of {"assetId": id} objects. The old dict-shaped payload
    was SILENTLY DROPPED by the API (confirmed live) so --loras had zero effect.
    """
    out_dir = _resolve_out(args)

    # Resolve control asset id
    control_asset_id = getattr(args, "control_asset_id", None)
    control_image_path = getattr(args, "control_image", None)

    if args.dry_run:
        print("[scenario_gen] DRY-RUN controlnet (Pipeline A)")
        print("  model-id          : %s" % args.model_id)
        print("  prompt            : %s" % args.prompt)
        print("  control-image     : %s" % (control_image_path or "(none)"))
        print("  control-asset-id  : %s" % (control_asset_id or "(will upload)"))
        print("  control-modality  : %s" % args.control_modality)
        print("  control-strength  : %s" % args.control_strength)
        print("  samples           : %d" % args.num_samples)
        print("  size              : %dx%d" % (args.width, args.height))
        print("  out dir           : %s" % out_dir)
        print("  est credits       : ~%d (API is source of truth)" % (CREDIT_EST["generate_per_sample"] * args.num_samples))
        return

    if not control_asset_id and not control_image_path:
        sys.exit(
            "[scenario_gen] ERROR: controlnet requires --control-image <path> "
            "OR --control-asset-id <id>."
        )

    os.makedirs(out_dir, exist_ok=True)
    key, secret = _load_credentials()
    headers = _auth_headers(key, secret)

    # Upload local image if no asset id given
    if not control_asset_id:
        print("[scenario_gen] uploading control image: %s" % control_image_path)
        control_asset_id = _upload_image(headers, control_image_path)

    body = {
        "prompt": args.prompt,
        "controlImage": control_asset_id,
        "controlModality": args.control_modality,
        "controlStrength": float(args.control_strength),
        "numSamples": args.num_samples,
        "width": args.width,
        "height": args.height,
    }
    # Optional img2img INIT image (composes with the depth ControlNet on flux.1-dev, which advertises
    # txt2img+img2img+controlnet together). Used by the TILED-SPACE edge-continuation arm: seed tile 2
    # with tile 1's finished overlap strip so the paint continues across the seam. `strength` is the
    # img2img denoise (lower = adhere more closely to the init; flux recommends higher values).
    init_image = getattr(args, "init_image", None)
    if init_image:
        init_asset_id = getattr(args, "init_asset_id", None)
        if not init_asset_id:
            print("[scenario_gen] uploading init image: %s" % init_image)
            init_asset_id = _upload_image(headers, init_image)
        body["image"] = init_asset_id
        if getattr(args, "strength", None) is not None:
            body["strength"] = float(args.strength)
    if args.seed is not None:
        body["seed"] = args.seed
    if getattr(args, "loras", None):
        lora_list = [s.strip() for s in args.loras.split(",") if s.strip()]
        if lora_list:
            guard_flux_lora_compat(args.model_id, lora_list)
            body["loras"] = lora_list
            if getattr(args, "loras_scale", None):
                scale_list = [float(s.strip()) for s in args.loras_scale.split(",") if s.strip()]
                if len(scale_list) != len(lora_list):
                    sys.exit(
                        "[scenario_gen] ERROR: --loras (%d) and --loras-scale (%d) must have the same "
                        "number of entries." % (len(lora_list), len(scale_list))
                    )
                body["lorasScale"] = scale_list

    url = API_BASE + CONTROLNET_PATH.format(model_id=args.model_id)
    print("[scenario_gen] submitting controlnet job (model=%s, modality=%s, strength=%.2f)"
          % (args.model_id, args.control_modality, float(args.control_strength)))
    res = _post_json(url, headers, body)
    job_id = _job_id_from_create(res, "controlnet create")
    print("[scenario_gen] controlnet job submitted: %s" % job_id)
    job = _poll_job(headers, job_id, "controlnet", args.timeout)
    stem = _safe_scope(getattr(args, "scope", "") or "controlnet") or "controlnet"
    saved = _download_job_assets(headers, job, out_dir, stem)
    _write_meta(out_dir, {
        "prompt": args.prompt,
        "model_id": args.model_id,
        "control_asset_id": control_asset_id,
        "control_image_path": control_image_path,
        "control_modality": args.control_modality,
        "control_strength": float(args.control_strength),
        "num_samples": args.num_samples,
        "width": args.width,
        "height": args.height,
        "job_id": job_id,
        "assets": saved,
        "source": "scenario-controlnet",
    })
    print("[scenario_gen] OK — job=%s assets=%d" % (job_id, len(saved)))


def _write_meta(out_dir: str, meta: dict) -> None:
    meta_path = os.path.join(out_dir, "scenario_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print("[scenario_gen] meta -> %s" % meta_path)


# --------------------------------------------------------------------------- #
# Argparse / main.
# --------------------------------------------------------------------------- #
def _add_out(sp) -> None:
    sp.add_argument("--out", help="explicit output dir (overrides --world/--scope)")
    sp.add_argument("--world", help="world id (default out: content/worlds/_private/<world>/images/<scope>/)")
    sp.add_argument("--scope", help="scope/entity key for the output dir + filename stem")
    sp.add_argument("--dry-run", action="store_true", help="print plan + est credits, make NO API calls")
    sp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                    help="job poll timeout seconds (default %d)" % DEFAULT_TIMEOUT_SEC)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Generate 2D art via the Scenario API (non-Eva).")
    ap.add_argument("--test-key", action="store_true",
                    help="cheap auth smoke-test (GET /v1/models; no generation) -> 'Scenario Auth OK'")
    sub = ap.add_subparsers(dest="command")

    sub.add_parser("list-models", help="list models on the account (and their ids)")

    sp_gen = sub.add_parser("generate", help="text-to-image (modelId required)")
    sp_gen.add_argument("--model-id", required=True, help="Scenario modelId (see list-models)")
    sp_gen.add_argument("--prompt", required=True, help="text-to-image prompt")
    sp_gen.add_argument("--negative-prompt", help="negative prompt")
    sp_gen.add_argument("--num-samples", type=int, default=1, help="images to generate (default 1)")
    sp_gen.add_argument("--width", type=int, default=1024, help="image width (default 1024)")
    sp_gen.add_argument("--height", type=int, default=1024, help="image height (default 1024)")
    sp_gen.add_argument("--seed", type=int, help="deterministic seed (optional)")
    _add_out(sp_gen)

    sp_up = sub.add_parser("upscale", help="upscale an asset id or image url")
    sp_up.add_argument("--asset-id", help="Scenario asset id to upscale")
    sp_up.add_argument("--image-url", help="image URL to upscale (alternative to --asset-id)")
    sp_up.add_argument("--scale", type=int, default=2, help="scaling factor (default 2)")
    _add_out(sp_up)

    sp_cn = sub.add_parser(
        "controlnet",
        help="Pipeline A: painterly-on-grid ControlNet generation (FLUX.1-dev canny/depth). "
             "Upload a 2:1 dimetric structure plate as the control image; outputs a painterly "
             "scene whose floor/obstacle layout is grid-aligned BY CONSTRUCTION.",
    )
    sp_cn.add_argument(
        "--control-image",
        metavar="PATH",
        help="local path to the control image (structure plate PNG). Uploaded automatically. "
             "Mutually exclusive with --control-asset-id.",
    )
    sp_cn.add_argument(
        "--control-asset-id",
        metavar="ASSET_ID",
        help="Scenario asset id of an already-uploaded control image (skips upload step). "
             "Mutually exclusive with --control-image.",
    )
    sp_cn.add_argument("--prompt", required=True, help="painterly scene prompt")
    sp_cn.add_argument(
        "--control-modality",
        default="canny",
        choices=["canny", "depth", "mlsd", "pose", "scribble", "seg", "normal", "softedge"],
        help="ControlNet modality (default: canny)",
    )
    sp_cn.add_argument(
        "--control-strength",
        type=float,
        default=0.7,
        help="ControlNet conditioning strength 0.0–1.0 (default: 0.7)",
    )
    sp_cn.add_argument(
        "--model-id",
        default="model_bfl-flux-1-dev",
        help="Scenario model id (default: model_bfl-flux-1-dev)",
    )
    sp_cn.add_argument(
        "--init-image", metavar="PATH",
        help="optional img2img INIT image (uploaded automatically). Composes with the depth ControlNet "
             "on flux.1-dev — used by the tiled-space edge-continuation arm to seed tile 2 with tile 1's "
             "finished overlap strip. Requires --strength to tune adherence.",
    )
    sp_cn.add_argument(
        "--strength", type=float,
        help="img2img denoise strength 0.01–1.0 (lower = adhere more closely to --init-image). No "
             "effect without --init-image.",
    )
    sp_cn.add_argument("--num-samples", type=int, default=2,
                       help="number of output images (default: 2)")
    sp_cn.add_argument("--width", type=int, default=1024, help="output width px (default: 1024)")
    sp_cn.add_argument("--height", type=int, default=1024, help="output height px (default: 1024)")
    sp_cn.add_argument("--seed", type=int, help="deterministic seed (optional)")
    sp_cn.add_argument(
        "--loras",
        help="comma-separated Scenario LoRA model/asset ids to apply (optional). Rejected loudly if a "
             "known z-image-only LoRA is combined with a flux model (see guard_flux_lora_compat).",
    )
    sp_cn.add_argument(
        "--loras-scale",
        help="comma-separated LoRA scales (floats), same length + order as --loras (optional; omit to "
             "let the API use its default scale per LoRA). No effect without --loras.",
    )
    _add_out(sp_cn)

    args = ap.parse_args(argv)

    if args.test_key:
        _cmd_test_key()
        return
    if args.command == "list-models":
        _cmd_list_models(args)
    elif args.command == "generate":
        _cmd_generate(args)
    elif args.command == "upscale":
        _cmd_upscale(args)
    elif args.command == "controlnet":
        _cmd_controlnet(args)
    else:
        ap.print_help()
        sys.exit("\n[scenario_gen] ERROR: a subcommand (list-models|generate|upscale|controlnet) or --test-key is required.")


if __name__ == "__main__":
    main()
