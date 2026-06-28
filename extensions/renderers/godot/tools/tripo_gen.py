#!/usr/bin/env python3
"""tripo_gen.py — generate / rig / animate a 3D character via the Tripo3D API.

A sibling of meshy_gen.py in the WorldOS GT2 final-art pipeline. Tripo3D adds a
RIGGING + ANIMATION pipeline on top of text/image-to-3D, so the GLB it produces can be
mixamo-rigged and pre-loaded with locomotion/combat clips before the bake.

    tripo_gen.py    -> a (optionally rigged + animated) .glb     (THIS tool; Tripo3D AI)
    bake_sprites.py -> 8-facing frame PNGs (Blender headless, dimetric 2:1, ISO-PROJECTION.md)
    pack_sheet.py   -> the renderer sheet.png + sheet.json manifest (v1)

Downstream contract (the manifest this output ultimately feeds):
  bake_sprites.py renders 8 facings [S,SE,E,NE,N,NW,W,SW] at the locked dimetric 2:1
  projection (see extensions/renderers/godot/ISO-PROJECTION.md), then pack_sheet.py emits a sprite-sheet
  manifest v1: rows=8 facings, cols=24 (idle4 / walk8 / attack6 / cast6), 128px,
  foot-anchored. tripo_gen does NOT bake — it just produces a clean rigged/animated GLB.
  A rigged GLB whose retarget presets are [walk, idle, run, attack, cast] lines up with
  the manifest's idle/walk/attack/cast columns.

This tool is WorldOS-ORIGINAL code; the ART it downloads (model.glb + animated GLBs) is
NOT committed — it lives under content/worlds/_private/ (gitignored, owner-licensed).

VERIFIED Tripo3D contract (v2 OpenAPI — confirmed live + against the official tripo3d
Python SDK v0.4.1, 2026-06-28). The old openapi.tripo3d.ai/v3 paths are DEAD (404
"No endpoint found"). ALL tasks are created via a single POST /task with a "type"
discriminator in the JSON body — there are NO per-operation paths.
  * Auth: Bearer  ``Authorization: Bearer <key>``  (NOT x-api-key).
  * Base: https://api.tripo3d.ai/v2/openapi
  * Create ANY task:      POST /task   (body.type selects the operation; returns data.task_id)
      - text-to-3D:       {"type":"text_to_model","prompt":...,"model_version":...}
      - image-to-3D:      {"type":"image_to_model","file":{...},"model_version":...}
      - rig pre-check:    {"type":"animate_prerigcheck","original_model_task_id":...}   -- run FIRST
      - rig:             {"type":"animate_rig","original_model_task_id":...,"rig_type":"biped","spec":"mixamo","out_format":"glb"}
      - retarget anims:  {"type":"animate_retarget","original_model_task_id":<rig_task>,"animation"|"animations":[preset:*],"out_format":"glb"}
  * Upload an image:      POST /upload  (multipart "file"; legacy fallback) — returns data.image_token,
                          referenced as file={"type":"jpg","file_token":<token>}. (SDK prefers STS S3,
                          but the multipart /upload path still works for a single image.)
  * Poll task status:     GET /task/{id}   ->  data.{status,progress,output}. Poll >= 2s (rate limit).
      status values: queued|running|success|failed|cancelled|banned|expired|unknown
  * Model URL lives under data.output.{model | pbr_model | base_model} (rig/retarget reuse the
    SAME output.* fields — there is NO rigged_model / animation_model key on the wire).
  * The model download URL EXPIRES ~5 min -> download immediately on success.
  * Preset animation names (Animation enum): idle, walk, run, dive, climb, jump, slash, shoot,
    hurt, fall, turn (+ multi-leg variants). NOTE: there is NO "attack"/"cast" preset — the
    closest melee/ranged presets are "slash"/"shoot"; CLI aliases map attack->slash, cast->shoot.

The API key is read from ~/.worldos/tripo3d.key or $WORLDOS_TRIPO_API_KEY. It is NEVER
printed/logged and NEVER written into any repo file.

Usage:
    # auth smoke-test (a cheap GET — no asset generation, no credit spend)
    python3 tripo_gen.py --test-key

    # text-to-3D (default model H3.1 = v3.1; --lowpoly switches to P1)
    python3 tripo_gen.py text --prompt "a stylized fantasy human ranger ..." --world baldurs-gate --scope ranger
    python3 tripo_gen.py text --prompt "..." --lowpoly --out <dir>

    # image-to-3D (uploads the image first, then generates)
    python3 tripo_gen.py image --image hero.png --world baldurs-gate --scope hero

    # rig + animate an existing generation task (rig-check -> rig spec=mixamo -> retarget)
    python3 tripo_gen.py rig --task <generation_task_id> --out <dir>
    python3 tripo_gen.py rig --task <id> --animations walk idle run attack cast --out <dir>

    # show the plan + estimated credits WITHOUT calling the API
    python3 tripo_gen.py text --prompt "..." --world w --scope s --dry-run
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

API_BASE = "https://api.tripo3d.ai/v2/openapi"
# v2 OpenAPI: every operation is a POST to the single /task endpoint; the body's
# "type" field selects the operation. There are NO per-operation create paths anymore.
TASK_CREATE_PATH = "/task"
UPLOAD_PATH = "/upload"
TASK_PATH = "/task/{task_id}"

# Models (dated model_version ids per the v2 schema): v3.1 (default, high quality) vs P1 (low-poly / game-ready).
MODEL_DEFAULT = "v3.1-20260211"
MODEL_LOWPOLY = "P1-20260311"

# The retarget presets that align with the bake manifest's idle/walk/attack/cast columns.
# NOTE: Tripo has no "attack"/"cast" preset; ANIM_ALIAS maps the bake-manifest names to the
# real Animation enum values (attack->slash, cast->shoot) so the CLI surface stays stable.
DEFAULT_ANIMATIONS = ["walk", "idle", "run", "attack", "cast"]

# Map friendly/bake-manifest animation names -> Tripo preset names (Animation enum, sans "preset:").
ANIM_ALIAS = {"attack": "slash", "cast": "shoot"}
# Canonical Tripo preset animation names (used to validate / pass through unknown names).
TRIPO_PRESETS = {
    "idle", "walk", "run", "dive", "climb", "jump", "slash", "shoot",
    "hurt", "fall", "turn",
}

# Polling cadence + ceilings. >= 2s honors the documented 1 req/s rate limit (else 429).
POLL_INTERVAL_SEC = 3
DEFAULT_TIMEOUT_SEC = 600

# Rough per-stage credit estimates (for --dry-run only; the API is the source of truth).
CREDIT_EST = {"text": 20, "image": 20, "rig": 10, "retarget_each": 5}


# --------------------------------------------------------------------------- #
# Key handling. NEVER print/log the key; NEVER write it to a repo file.
# --------------------------------------------------------------------------- #
def _load_api_key() -> str:
    key = os.environ.get("WORLDOS_TRIPO_API_KEY", "").strip()
    if key:
        return key
    key_path = os.path.expanduser("~/.worldos/tripo3d.key")
    if os.path.isfile(key_path):
        with open(key_path, "r") as f:
            key = f.read().strip()
        if key:
            return key
    sys.exit(
        "[tripo_gen] ERROR: no API key. Set $WORLDOS_TRIPO_API_KEY or put it in "
        "~/.worldos/tripo3d.key"
    )


def _auth_headers(key: str) -> dict:
    return {
        "Authorization": "Bearer %s" % key,
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
            res = json.loads(resp.read().decode("utf-8"))
            _check_api_code(res, "POST " + url)
            return res
    except urllib.error.HTTPError as e:
        _explain_http(e.code, _read_error(e), "POST " + url)
    except urllib.error.URLError as e:
        sys.exit("[tripo_gen] ERROR: network failure on POST %s: %s" % (url, e.reason))


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _explain_http(e.code, _read_error(e), "GET " + url)
    except urllib.error.URLError as e:
        sys.exit("[tripo_gen] ERROR: network failure on GET %s: %s" % (url, e.reason))


def _post_multipart(url: str, key: str, file_path: str) -> dict:
    """Upload a binary file (image/glb) as multipart/form-data via urllib only."""
    with open(file_path, "rb") as f:
        file_data = f.read()
    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    boundary = "----worldos%s" % uuid.uuid4().hex
    pre = (
        "--%s\r\n"
        'Content-Disposition: form-data; name="file"; filename="%s"\r\n'
        "Content-Type: %s\r\n\r\n" % (boundary, filename, content_type)
    ).encode("utf-8")
    post = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    payload = pre + file_data + post
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": "Bearer %s" % key,
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _explain_http(e.code, _read_error(e), "POST(multipart) " + url)
    except urllib.error.URLError as e:
        sys.exit("[tripo_gen] ERROR: network failure on upload %s: %s" % (url, e.reason))


def _read_error(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8")
    except Exception:
        return "<no body>"


def _check_api_code(res: dict, what: str) -> None:
    """The v2 API returns a non-zero `code` (with a human `message`) for business errors
    even on HTTP 200 (e.g. code 2010 = insufficient credit). Surface those clearly."""
    code = res.get("code")
    if code in (None, 0):
        return
    msg = res.get("message") or ""
    if code == 2010 or "credit" in msg.lower():
        sys.exit(
            "[tripo_gen] ERROR: insufficient Tripo credits on %s (code=%s). Top up the "
            "account at https://platform.tripo3d.ai/ ; art was NOT generated. Detail: %s"
            % (what, code, json.dumps(res))
        )
    sys.exit("[tripo_gen] ERROR: Tripo API error code=%s on %s. Detail: %s"
             % (code, what, json.dumps(res)))


def _explain_http(code: int, detail: str, what: str) -> None:
    # code 2010 (insufficient credit) is returned with HTTP 403 in some paths.
    if "2010" in detail or '"credit"' in detail.lower() or "enough credit" in detail.lower():
        sys.exit(
            "[tripo_gen] ERROR: insufficient Tripo credits on %s. Top up the account at "
            "https://platform.tripo3d.ai/ ; art was NOT generated. Detail: %s" % (what, detail)
        )
    if code == 401:
        sys.exit(
            "[tripo_gen] ERROR 401 unauthorized on %s — bad/expired API key. Detail: %s"
            % (what, detail)
        )
    if code == 402:
        sys.exit(
            "[tripo_gen] ERROR 402 insufficient Tripo credits on %s. Top up the account; "
            "art was NOT generated. Detail: %s" % (what, detail)
        )
    if code == 429:
        sys.exit(
            "[tripo_gen] ERROR 429 rate-limited on %s (1 req/s limit). Slow polling. Detail: %s"
            % (what, detail)
        )
    sys.exit("[tripo_gen] ERROR HTTP %d on %s. Detail: %s" % (code, what, detail))


def _download(url: str, dest: str) -> int:
    """Stream a binary asset (glb) to dest. Returns bytes written.

    The Tripo download URL expires ~5 min after success, so callers must invoke this
    immediately once the task succeeds.
    """
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
        sys.exit(
            "[tripo_gen] ERROR downloading %s -> %s: %s "
            "(the download URL expires ~5 min — re-run if it lapsed)" % (url, dest, e)
        )


# --------------------------------------------------------------------------- #
# Task lifecycle.
# --------------------------------------------------------------------------- #
def _task_id_from_create(res: dict, what: str) -> str:
    """Tripo wraps create responses as {"code":0,"data":{"task_id": ...}}."""
    data = res.get("data") or {}
    task_id = data.get("task_id") or res.get("task_id") or data.get("result")
    if not task_id:
        sys.exit("[tripo_gen] ERROR: %s returned no task id: %s" % (what, json.dumps(res)))
    return task_id


def _create_text(headers: dict, prompt: str, model: str) -> str:
    body = {"type": "text_to_model", "prompt": prompt, "model_version": model}
    res = _post_json(API_BASE + TASK_CREATE_PATH, headers, body)
    task_id = _task_id_from_create(res, "text-to-3D create")
    print("[tripo_gen] text-to-3D task submitted: %s (model=%s)" % (task_id, model))
    return task_id


def _create_image(headers: dict, file_token: str, model: str) -> str:
    body = {
        "type": "image_to_model",
        # v2 file content block: a single image by upload token. "type" is the file ext.
        "file": {"type": "jpg", "file_token": file_token},
        "model_version": model,
    }
    res = _post_json(API_BASE + TASK_CREATE_PATH, headers, body)
    task_id = _task_id_from_create(res, "image-to-3D create")
    print("[tripo_gen] image-to-3D task submitted: %s (model=%s)" % (task_id, model))
    return task_id


def _upload_image(key: str, image_path: str) -> str:
    # v2 legacy multipart upload -> data.image_token (referenced as file.file_token).
    res = _post_multipart(API_BASE + UPLOAD_PATH, key, image_path)
    data = res.get("data") or {}
    token = data.get("image_token") or data.get("file_token") or data.get("token")
    if not token:
        sys.exit("[tripo_gen] ERROR: upload returned no file token: %s" % json.dumps(res))
    print("[tripo_gen] uploaded %s -> file token acquired" % os.path.basename(image_path))
    return token


def _rig_check(headers: dict, task_id: str) -> dict:
    """Pre-flight rigging check (animate_prerigcheck) — MUST pass before animate_rig."""
    body = {"type": "animate_prerigcheck", "original_model_task_id": task_id}
    res = _post_json(API_BASE + TASK_CREATE_PATH, headers, body)
    check_task = _task_id_from_create(res, "rig-check create")
    print("[tripo_gen] rig-check submitted: %s" % check_task)
    final = _poll(headers, check_task, "rig-check", DEFAULT_TIMEOUT_SEC)
    out = final.get("output") or {}
    riggable = out.get("riggable", out.get("rig_ready", True))
    if riggable is False:
        sys.exit(
            "[tripo_gen] ERROR: model is NOT riggable per rig-check (%s). "
            "Rigging aborted." % json.dumps(out)
        )
    print("[tripo_gen] rig-check OK — model is riggable.")
    return final


def _rig(headers: dict, task_id: str) -> str:
    body = {
        "type": "animate_rig",
        "original_model_task_id": task_id,
        "rig_type": "biped",
        "spec": "mixamo",
        "out_format": "glb",
    }
    res = _post_json(API_BASE + TASK_CREATE_PATH, headers, body)
    rig_task = _task_id_from_create(res, "rig create")
    print("[tripo_gen] rig task submitted: %s (spec=mixamo)" % rig_task)
    return rig_task


def _preset_for(name: str) -> str:
    """Map a friendly/bake-manifest animation name to a Tripo 'preset:<name>' value."""
    base = ANIM_ALIAS.get(name, name)
    return "preset:%s" % base


def _retarget(headers: dict, rigged_task_id: str, animations: list) -> str:
    presets = [_preset_for(a) for a in animations]
    # v2 takes a single "animation" for one clip, or "animations" for a list.
    body = {
        "type": "animate_retarget",
        "original_model_task_id": rigged_task_id,
        "out_format": "glb",
    }
    if len(presets) == 1:
        body["animation"] = presets[0]
    else:
        body["animations"] = presets
    res = _post_json(API_BASE + TASK_CREATE_PATH, headers, body)
    rt_task = _task_id_from_create(res, "retarget create")
    print("[tripo_gen] retarget task submitted: %s (animations=%s)" % (
        rt_task, ",".join("%s->%s" % (a, ANIM_ALIAS.get(a, a)) for a in animations)))
    return rt_task


def _poll(headers: dict, task_id: str, label: str, timeout_sec: int) -> dict:
    """Poll GET /task/{id} until success. >= POLL_INTERVAL_SEC between calls (rate limit)."""
    url = API_BASE + TASK_PATH.format(task_id=task_id)
    deadline = time.time() + timeout_sec
    last_progress = -1
    while True:
        res = _get_json(url, headers)
        task = res.get("data") or res
        status = str(task.get("status", "unknown")).lower()
        progress = int(task.get("progress", 0) or 0)
        if progress != last_progress or status not in ("queued", "running", "pending"):
            print("[tripo_gen] %s status=%s progress=%d%%" % (label, status, progress))
            last_progress = progress
        if status in ("success", "succeeded", "completed"):
            return task
        if status in ("failed", "error"):
            sys.exit("[tripo_gen] ERROR: %s task FAILED: %s" % (label, json.dumps(task)))
        if status in ("cancelled", "canceled", "expired", "banned", "unknown"):
            sys.exit("[tripo_gen] ERROR: %s task %s: %s" % (label, status, json.dumps(task)))
        if time.time() > deadline:
            sys.exit(
                "[tripo_gen] ERROR: %s task timed out after %ds (last status=%s progress=%d%%). "
                "Art was NOT completed." % (label, timeout_sec, status, progress)
            )
        time.sleep(POLL_INTERVAL_SEC)


def _model_url(task: dict) -> str:
    """Pull the downloadable GLB URL out of a succeeded task (URL expires ~5 min).

    v2 OpenAPI puts the model URL under output.{pbr_model,model,base_model}; rig and
    retarget tasks reuse these SAME keys (there is no rigged_model/animation_model on the
    wire). pbr_model is preferred (textured), then model, then base_model.
    """
    out = task.get("output") or {}
    for k in ("pbr_model", "model", "base_model"):
        v = out.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            url = v.get("url") or v.get("glb")
            if url:
                return url
    sys.exit("[tripo_gen] ERROR: succeeded task has no downloadable model URL: %s" % json.dumps(out))


def _animation_urls(task: dict) -> dict:
    """Return {anim_name: url} for retarget output, best-effort across response shapes."""
    out = task.get("output") or {}
    anims = out.get("animations") or out.get("animation_models") or {}
    result: dict = {}
    if isinstance(anims, dict):
        for name, v in anims.items():
            url = v.get("url") if isinstance(v, dict) else v
            if url:
                result[name] = url
    elif isinstance(anims, list):
        for i, v in enumerate(anims):
            if isinstance(v, dict):
                name = v.get("name") or v.get("type") or "anim_%d" % i
                url = v.get("url") or v.get("model")
                if url:
                    result[name] = url
    return result


# --------------------------------------------------------------------------- #
# Output dir resolution.
# --------------------------------------------------------------------------- #
def _safe_scope(scope: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(scope))[:128]


def _resolve_out(args) -> str:
    """Default output to the gitignored content/worlds/_private/<world>/images/<scope>/."""
    if getattr(args, "out", None):
        return os.path.abspath(args.out)
    world = _safe_scope(getattr(args, "world", "") or "")
    scope = _safe_scope(getattr(args, "scope", "") or "")
    if not world or not scope:
        sys.exit(
            "[tripo_gen] ERROR: provide --out, or both --world and --scope "
            "(output goes to content/worlds/_private/<world>/images/<scope>/)."
        )
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(repo_root, "content", "worlds", "_private", world, "images", scope)


# --------------------------------------------------------------------------- #
# Sub-command bodies.
# --------------------------------------------------------------------------- #
def _do_rig_pipeline(headers: dict, source_task_id: str, animations: list,
                     out_dir: str, timeout: int, meta: dict) -> None:
    """rig-check -> rig (spec=mixamo) -> retarget [presets]; download rigged + animated GLBs."""
    _rig_check(headers, source_task_id)
    rig_task_id = _rig(headers, source_task_id)
    rigged = _poll(headers, rig_task_id, "rig", timeout)
    rigged_path = os.path.join(out_dir, "rigged.glb")
    rsize = _download(_model_url(rigged), rigged_path)
    print("[tripo_gen] downloaded rigged.glb (%d bytes) -> %s" % (rsize, rigged_path))
    meta["rig_task_id"] = rig_task_id
    meta["rigged_glb_bytes"] = rsize

    rt_task_id = _retarget(headers, rig_task_id, animations)
    animated = _poll(headers, rt_task_id, "retarget", timeout)
    anim_urls = _animation_urls(animated)
    downloaded = {}
    if anim_urls:
        for name, url in anim_urls.items():
            safe = _safe_scope(name)
            dpath = os.path.join(out_dir, "anim_%s.glb" % safe)
            dsize = _download(url, dpath)
            downloaded[name] = {"path": dpath, "bytes": dsize}
            print("[tripo_gen] downloaded anim_%s.glb (%d bytes)" % (safe, dsize))
    else:
        # Some plans return one combined animated GLB instead of per-clip files.
        dpath = os.path.join(out_dir, "animated.glb")
        dsize = _download(_model_url(animated), dpath)
        downloaded["combined"] = {"path": dpath, "bytes": dsize}
        print("[tripo_gen] downloaded animated.glb (%d bytes, combined)" % dsize)
    meta["retarget_task_id"] = rt_task_id
    meta["animations"] = animations
    meta["animation_files"] = downloaded


def _write_meta(out_dir: str, meta: dict) -> None:
    meta_path = os.path.join(out_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    print("[tripo_gen] meta -> %s" % meta_path)


def _cmd_test_key() -> None:
    """Cheap auth smoke-test: GET a dummy task id. 404 'not found' == auth OK."""
    key = _load_api_key()
    headers = _auth_headers(key)
    url = API_BASE + TASK_PATH.format(task_id="00000000-0000-0000-0000-000000000000")
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        print("Tripo Auth OK")  # 200 would also mean the key is valid
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("Tripo Auth OK")  # 404 'task not found' with a valid key == authenticated
        elif e.code in (401, 403):
            sys.exit("[tripo_gen] AUTH FAILED: HTTP %d — bad/expired API key." % e.code)
        else:
            # Any non-auth HTTP error still proves the request was accepted/authenticated.
            print("Tripo Auth OK")
    except urllib.error.URLError as e:
        sys.exit("[tripo_gen] ERROR: network failure on --test-key: %s" % e.reason)


def _cmd_text(args) -> None:
    model = MODEL_LOWPOLY if args.lowpoly else MODEL_DEFAULT
    out_dir = _resolve_out(args)
    if args.dry_run:
        est = CREDIT_EST["text"] + (
            CREDIT_EST["rig"] + CREDIT_EST["retarget_each"] * len(args.animations)
            if args.rig else 0
        )
        print("[tripo_gen] DRY-RUN text-to-3D")
        print("  prompt    : %s" % args.prompt)
        print("  model     : %s" % model)
        print("  rig+anim  : %s (%s)" % (args.rig, ",".join(args.animations) if args.rig else "-"))
        print("  out dir   : %s" % out_dir)
        print("  est credits: ~%d (API is source of truth)" % est)
        return
    os.makedirs(out_dir, exist_ok=True)
    model_path = os.path.join(out_dir, "model.glb")
    if os.path.exists(model_path) and not args.force:
        print("[tripo_gen] %s exists; skipping (use --force)." % model_path)
        return
    key = _load_api_key()
    headers = _auth_headers(key)
    task_id = _create_text(headers, args.prompt, model)
    task = _poll(headers, task_id, "text-to-3D", args.timeout)
    size = _download(_model_url(task), model_path)
    print("[tripo_gen] downloaded model.glb (%d bytes) -> %s" % (size, model_path))
    meta = {
        "prompt": args.prompt, "model_version": model, "generation_task_id": task_id,
        "glb_bytes": size, "source": "tripo3d-text-to-model",
    }
    if args.rig:
        _do_rig_pipeline(headers, task_id, args.animations, out_dir, args.timeout, meta)
    _write_meta(out_dir, meta)
    print("[tripo_gen] OK — generation=%s glb_bytes=%d" % (task_id, size))


def _cmd_image(args) -> None:
    model = MODEL_LOWPOLY if args.lowpoly else MODEL_DEFAULT
    out_dir = _resolve_out(args)
    if args.dry_run:
        est = CREDIT_EST["image"] + (
            CREDIT_EST["rig"] + CREDIT_EST["retarget_each"] * len(args.animations)
            if args.rig else 0
        )
        print("[tripo_gen] DRY-RUN image-to-3D")
        print("  image     : %s" % args.image)
        print("  model     : %s" % model)
        print("  rig+anim  : %s (%s)" % (args.rig, ",".join(args.animations) if args.rig else "-"))
        print("  out dir   : %s" % out_dir)
        print("  est credits: ~%d (API is source of truth)" % est)
        return
    if not os.path.isfile(args.image):
        sys.exit("[tripo_gen] ERROR: image not found: %s" % args.image)
    os.makedirs(out_dir, exist_ok=True)
    model_path = os.path.join(out_dir, "model.glb")
    if os.path.exists(model_path) and not args.force:
        print("[tripo_gen] %s exists; skipping (use --force)." % model_path)
        return
    key = _load_api_key()
    headers = _auth_headers(key)
    token = _upload_image(key, args.image)
    task_id = _create_image(headers, token, model)
    task = _poll(headers, task_id, "image-to-3D", args.timeout)
    size = _download(_model_url(task), model_path)
    print("[tripo_gen] downloaded model.glb (%d bytes) -> %s" % (size, model_path))
    meta = {
        "image": os.path.basename(args.image), "model_version": model,
        "generation_task_id": task_id, "glb_bytes": size, "source": "tripo3d-image-to-model",
    }
    if args.rig:
        _do_rig_pipeline(headers, task_id, args.animations, out_dir, args.timeout, meta)
    _write_meta(out_dir, meta)
    print("[tripo_gen] OK — generation=%s glb_bytes=%d" % (task_id, size))


def _cmd_rig(args) -> None:
    out_dir = _resolve_out(args)
    if args.dry_run:
        est = CREDIT_EST["rig"] + CREDIT_EST["retarget_each"] * len(args.animations)
        print("[tripo_gen] DRY-RUN rig pipeline (rig-check -> rig spec=mixamo -> retarget)")
        print("  source task: %s" % args.task)
        print("  animations : %s" % ",".join(args.animations))
        print("  out dir    : %s" % out_dir)
        print("  est credits: ~%d (API is source of truth)" % est)
        return
    os.makedirs(out_dir, exist_ok=True)
    key = _load_api_key()
    headers = _auth_headers(key)
    meta = {"generation_task_id": args.task, "source": "tripo3d-rig-retarget"}
    _do_rig_pipeline(headers, args.task, args.animations, out_dir, args.timeout, meta)
    _write_meta(out_dir, meta)
    print("[tripo_gen] OK — rigged+animated from %s" % args.task)


# --------------------------------------------------------------------------- #
# Argparse / main.
# --------------------------------------------------------------------------- #
def _add_common(sp) -> None:
    sp.add_argument("--out", help="explicit output dir (overrides --world/--scope)")
    sp.add_argument("--world", help="world id (default out: content/worlds/_private/<world>/images/<scope>/)")
    sp.add_argument("--scope", help="scope/entity key for the output dir")
    sp.add_argument("--lowpoly", action="store_true",
                    help="use the P1 low-poly / game-ready model instead of v3.1")
    sp.add_argument("--rig", action="store_true",
                    help="after generation, run rig-check -> rig(spec=mixamo) -> retarget")
    sp.add_argument("--animations", nargs="+", default=list(DEFAULT_ANIMATIONS),
                    help="retarget preset animations (default: %s). "
                         "Tripo presets: %s. Aliases: attack->slash, cast->shoot."
                         % (" ".join(DEFAULT_ANIMATIONS), " ".join(sorted(TRIPO_PRESETS))))
    sp.add_argument("--force", action="store_true", help="regenerate even if model.glb exists")
    sp.add_argument("--dry-run", action="store_true", help="print plan + est credits, make NO API calls")
    sp.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                    help="per-stage poll timeout seconds (default %d)" % DEFAULT_TIMEOUT_SEC)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Generate / rig / animate a 3D model via Tripo3D.")
    ap.add_argument("--test-key", action="store_true",
                    help="cheap auth smoke-test (GET a dummy task; no generation) -> 'Tripo Auth OK'")
    sub = ap.add_subparsers(dest="command")

    sp_text = sub.add_parser("text", help="text-to-3D")
    sp_text.add_argument("--prompt", required=True, help="text-to-3D prompt")
    _add_common(sp_text)

    sp_img = sub.add_parser("image", help="image-to-3D")
    sp_img.add_argument("--image", required=True, help="path to the source image")
    _add_common(sp_img)

    sp_rig = sub.add_parser("rig", help="rig + animate an existing generation task")
    sp_rig.add_argument("--task", required=True, help="the generation task id to rig")
    _add_common(sp_rig)

    args = ap.parse_args(argv)

    if args.test_key:
        _cmd_test_key()
        return
    if args.command == "text":
        _cmd_text(args)
    elif args.command == "image":
        _cmd_image(args)
    elif args.command == "rig":
        _cmd_rig(args)
    else:
        ap.print_help()
        sys.exit("\n[tripo_gen] ERROR: a subcommand (text|image|rig) or --test-key is required.")


if __name__ == "__main__":
    main()
