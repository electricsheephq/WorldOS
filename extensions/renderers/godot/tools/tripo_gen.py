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

VERIFIED Tripo3D v3 contract (LIVE-TESTED 2026-06-28 — several details differ from BOTH the
public docs AND the prior version of this wrapper; the corrections below are what actually works):
  * Auth: Bearer  ``Authorization: Bearer <key>``  (NOT x-api-key).
  * Base: https://openapi.tripo3d.ai/v3
  * Create text-to-3D:    POST /generation/text-to-model  body {prompt, model}  (NO `type` field;
                          the old /generation/text now 404s. `model` NOT `model_version`.)
  * Create image-to-3D:   POST /generation/image-to-model body {model, file:{type:"image",file_token}}
  * Upload an image/glb:  POST /files                     (multipart) -> data.file_token
  * Model ids are DATE-STAMPED (friendly names like "tripo-p1"/"v3.1" are REJECTED):
       P1-20260311 (game/low-poly), v3.1-20260211 (default hi-precision), v3.0-20250812, v2.5-20250123.
  * Rig pre-check:        POST /animations/rig-check      body {input}  -- run FIRST; it RETURNS the
                          recommended rig_type (biped|quadruped|avian|...). DON'T hardcode biped.
  * Rig:                  POST /animations/rig            body {input, model, rig_type, spec, out_format}
                          model MUST be v2.5-20260210 (biped AND creatures; the server default
                          v2.5-20250123 is REJECTED; v1.0-20240301 rigs but its retarget fails).
                          spec=mixamo for biped, tripo for creatures. out_format glb|fbx.
  * Retarget animations:  POST /animations/retarget       body {input:<rigged_id>, animations:[preset], out_format}
                          Presets are NAMESPACED by rig_type: "preset:biped:walk", "preset:quadruped:walk"
                          (bare "preset:walk" FAILS). ONE preset per call — multi-preset batches FAIL.
                          out_format=fbx -> a Unity-ready FBX directly (no Blender needed).
  * Each create returns a TASK ID. Poll GET /tasks/{id} (PLURAL — singular /task/{id} 404s) at
    >= 3s (1 req/s limit -> 429). Output URL is at data.output.model_url and EXPIRES ~5 min ->
    download immediately on success.

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

API_BASE = "https://openapi.tripo3d.ai/v3"
CREATE_TEXT_PATH = "/generation/text-to-model"   # was /generation/text (now 404s)
CREATE_IMAGE_PATH = "/generation/image-to-model"
UPLOAD_PATH = "/files"                            # was /upload; returns data.file_token
RIG_CHECK_PATH = "/animations/rig-check"
RIG_PATH = "/animations/rig"
RETARGET_PATH = "/animations/retarget"
TASK_PATH = "/tasks/{task_id}"   # PLURAL — the singular /task/{id} 404s in v3

# Generation model ids are DATE-STAMPED (live-verified 2026-06-28; friendly names are rejected).
# Bump these when Tripo deprecates a date (a 400 "invalid model ... allowed values: ..." names the
# current set). P1 = game/low-poly, v3.1 = default high-precision.
MODEL_DEFAULT = "v3.1-20260211"
MODEL_LOWPOLY = "P1-20260311"

# The rig model. v2.5-20260210 rigs BOTH bipeds and creatures (quadruped/avian/...). The server
# default (v2.5-20250123) is REJECTED, and v1.0-20240301's retarget fails — so pin this one.
RIG_MODEL = "v2.5-20260210"

# Default retarget clips, chosen by rig_type AFTER rig-check (so a quadruped doesn't queue failing
# biped-only clips). Names live-verified per rig_type; presets namespaced at call time as
# "preset:<rig_type>:<name>". `--animations` overrides these for any rig_type. Per-clip failures
# are non-fatal (warn + skip) for the rare unsupported name.
DEFAULT_BIPED_ANIMATIONS = ["walk", "idle", "run", "slash"]
DEFAULT_CREATURE_ANIMATIONS = ["walk"]   # cross-rig-safe; the only clip verified for non-bipeds
# Back-compat alias (the biped set is what callers historically referenced).
DEFAULT_ANIMATIONS = DEFAULT_BIPED_ANIMATIONS

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
            return json.loads(resp.read().decode("utf-8"))
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


def _explain_http(code: int, detail: str, what: str) -> None:
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
    # v3 canonical: field is `model` (not `model_version`); no `type` field. Live-verified.
    body = {"prompt": prompt, "model": model}
    res = _post_json(API_BASE + CREATE_TEXT_PATH, headers, body)
    task_id = _task_id_from_create(res, "text-to-3D create")
    print("[tripo_gen] text-to-3D task submitted: %s (model=%s)" % (task_id, model))
    return task_id


def _create_image(headers: dict, file_token: str, model: str) -> str:
    # v3 canonical: {model, file:{type:"image", file_token}} (no top-level `type`/`model_version`).
    body = {
        "model": model,
        "file": {"type": "image", "file_token": file_token},
    }
    res = _post_json(API_BASE + CREATE_IMAGE_PATH, headers, body)
    task_id = _task_id_from_create(res, "image-to-3D create")
    print("[tripo_gen] image-to-3D task submitted: %s (model=%s)" % (task_id, model))
    return task_id


def _upload_image(key: str, image_path: str) -> str:
    res = _post_multipart(API_BASE + UPLOAD_PATH, key, image_path)
    data = res.get("data") or {}
    token = data.get("image_token") or data.get("file_token") or data.get("token")
    if not token:
        sys.exit("[tripo_gen] ERROR: upload returned no file token: %s" % json.dumps(res))
    print("[tripo_gen] uploaded %s -> file token acquired" % os.path.basename(image_path))
    return token


def _rig_check(headers: dict, task_id: str) -> str:
    """Pre-flight rigging check — MUST pass before /animations/rig.

    Body is just {input}; rig-check RETURNS the recommended rig_type (biped|quadruped|avian|...).
    Returns that rig_type so the caller can pick the rig spec + retarget preset namespace.
    """
    body = {"input": task_id}
    res = _post_json(API_BASE + RIG_CHECK_PATH, headers, body)
    check_task = _task_id_from_create(res, "rig-check create")
    print("[tripo_gen] rig-check submitted: %s" % check_task)
    final = _poll(headers, check_task, "rig-check", DEFAULT_TIMEOUT_SEC)
    out = final.get("output") or {}
    # Treat riggable + rig_type as REQUIRED. Defaulting them (riggable=True, rig_type=biped) would
    # turn an upstream contract break into a wrong-skeleton run (mixamo spec + preset:biped:* on a
    # creature) that burns a full rig/retarget cycle — fail fast instead.
    riggable = out.get("riggable", out.get("rig_ready"))
    if riggable is not True:
        sys.exit(
            "[tripo_gen] ERROR: rig-check did not confirm riggable=true (got %s). "
            "Rigging aborted." % json.dumps(out)
        )
    rig_type = out.get("rig_type")
    valid_types = ("biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic")
    if rig_type not in valid_types:
        sys.exit(
            "[tripo_gen] ERROR: rig-check returned no/unknown rig_type (%s); expected one of %s. "
            "Rigging aborted." % (json.dumps(out), ", ".join(valid_types))
        )
    print("[tripo_gen] rig-check OK — riggable, rig_type=%s." % rig_type)
    return rig_type


def _rig(headers: dict, task_id: str, rig_type: str, out_format: str = "glb") -> str:
    # model MUST be RIG_MODEL (v2.5-20260210); spec=mixamo gives Unity-friendly biped bone names,
    # `tripo` for non-biped creatures. rig_type comes from rig-check.
    spec = "mixamo" if rig_type == "biped" else "tripo"
    body = {
        "input": task_id, "model": RIG_MODEL, "rig_type": rig_type,
        "spec": spec, "out_format": out_format,
    }
    res = _post_json(API_BASE + RIG_PATH, headers, body)
    rig_task = _task_id_from_create(res, "rig create")
    print("[tripo_gen] rig task submitted: %s (model=%s rig_type=%s spec=%s)"
          % (rig_task, RIG_MODEL, rig_type, spec))
    return rig_task


def _retarget(headers: dict, rigged_task_id: str, rig_type: str, animation: str,
              out_format: str = "glb") -> str:
    # Presets are namespaced by rig_type and applied ONE per call (batches fail). e.g.
    # "preset:biped:walk", "preset:quadruped:walk".
    preset = "preset:%s:%s" % (rig_type, animation)
    body = {"input": rigged_task_id, "animations": [preset], "out_format": out_format}
    res = _post_json(API_BASE + RETARGET_PATH, headers, body)
    rt_task = _task_id_from_create(res, "retarget create")
    print("[tripo_gen] retarget task submitted: %s (%s)" % (rt_task, preset))
    return rt_task


def _poll(headers: dict, task_id: str, label: str, timeout_sec: int, fatal: bool = True):
    """Poll GET /task/{id} until success. >= POLL_INTERVAL_SEC between calls (rate limit).

    On failure/timeout: sys.exit when fatal (the default), else print a warning and return None
    (used for per-clip retargets, where one unsupported preset must not kill the whole run).
    """
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
        terminal_bad = (
            status in ("failed", "error", "cancelled", "canceled", "expired", "banned", "unknown")
        )
        timed_out = time.time() > deadline
        if terminal_bad or timed_out:
            why = ("FAILED: %s" % json.dumps(task)) if terminal_bad else (
                "timed out after %ds (last status=%s progress=%d%%)" % (timeout_sec, status, progress))
            if fatal:
                sys.exit("[tripo_gen] ERROR: %s task %s" % (label, why))
            print("[tripo_gen] WARN: %s task %s — skipping." % (label, why))
            return None
        time.sleep(POLL_INTERVAL_SEC)


def _model_url(task: dict) -> str:
    """Pull the downloadable GLB URL out of a succeeded task (URL expires ~5 min)."""
    out = task.get("output") or {}
    # Tripo exposes the model under several keys across endpoints/versions. `model_url` is the
    # live v3 key for generation/rig/retarget output (verified 2026-06-28).
    for k in ("model_url", "pbr_model", "model", "rigged_model", "animation_model", "base_model"):
        v = out.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            url = v.get("url") or v.get("glb")
            if url:
                return url
    result = task.get("result") or {}
    for k in ("pbr_model", "model"):
        v = result.get(k)
        if isinstance(v, dict) and v.get("url"):
            return v["url"]
        if isinstance(v, str) and v:
            return v
    sys.exit("[tripo_gen] ERROR: succeeded task has no downloadable model URL: %s" % json.dumps(out))


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
def _do_rig_pipeline(headers: dict, source_task_id: str, animations, out_dir: str,
                     timeout: int, meta: dict, out_format: str = "glb") -> None:
    """rig-check -> rig -> retarget (ONE preset per call); download rigged + per-clip animated files.

    rig-check returns the recommended rig_type (biped/quadruped/...), which drives the rig spec and
    the namespaced retarget presets. When `animations` is None, the clip set is chosen by rig_type
    (a biped set vs the cross-rig-safe creature set) so non-bipeds don't queue failing biped-only
    clips. Per-clip retargets are non-fatal so an unsupported preset just warns and skips.
    out_format glb|fbx (fbx is Unity-ready directly, no Blender).
    """
    ext = "fbx" if out_format == "fbx" else "glb"
    rig_type = _rig_check(headers, source_task_id)
    if animations is None:  # pick defaults now that rig_type is known
        animations = DEFAULT_BIPED_ANIMATIONS if rig_type == "biped" else DEFAULT_CREATURE_ANIMATIONS
    rig_task_id = _rig(headers, source_task_id, rig_type, out_format)
    rigged = _poll(headers, rig_task_id, "rig", timeout)
    rigged_path = os.path.join(out_dir, "rigged.%s" % ext)
    rsize = _download(_model_url(rigged), rigged_path)
    print("[tripo_gen] downloaded rigged.%s (%d bytes) -> %s" % (ext, rsize, rigged_path))
    meta["rig_type"] = rig_type
    meta["rig_task_id"] = rig_task_id
    meta["rigged_bytes"] = rsize

    downloaded: dict = {}
    for anim in animations:
        rt_task_id = _retarget(headers, rig_task_id, rig_type, anim, out_format)
        animated = _poll(headers, rt_task_id, "retarget:%s" % anim, timeout, fatal=False)
        if animated is None:
            downloaded[anim] = {"skipped": True}
            continue
        safe = _safe_scope(anim)
        dpath = os.path.join(out_dir, "anim_%s.%s" % (safe, ext))
        dsize = _download(_model_url(animated), dpath)
        downloaded[anim] = {"path": dpath, "bytes": dsize, "task_id": rt_task_id}
        print("[tripo_gen] downloaded anim_%s.%s (%d bytes)" % (safe, ext, dsize))
    meta["animations"] = animations
    meta["out_format"] = ext
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
        anims = args.animations or DEFAULT_BIPED_ANIMATIONS  # actual set is rig_type-chosen at run time
        est = CREDIT_EST["text"] + (
            CREDIT_EST["rig"] + CREDIT_EST["retarget_each"] * len(anims)
            if args.rig else 0
        )
        print("[tripo_gen] DRY-RUN text-to-3D")
        print("  prompt    : %s" % args.prompt)
        print("  model     : %s" % model)
        print("  rig+anim  : %s (%s)" % (args.rig, ",".join(anims) if args.rig else "-"))
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
        _do_rig_pipeline(headers, task_id, args.animations, out_dir, args.timeout, meta,
                         out_format=args.out_format)
    _write_meta(out_dir, meta)
    print("[tripo_gen] OK — generation=%s glb_bytes=%d" % (task_id, size))


def _cmd_image(args) -> None:
    model = MODEL_LOWPOLY if args.lowpoly else MODEL_DEFAULT
    out_dir = _resolve_out(args)
    if args.dry_run:
        anims = args.animations or DEFAULT_BIPED_ANIMATIONS  # actual set is rig_type-chosen at run time
        est = CREDIT_EST["image"] + (
            CREDIT_EST["rig"] + CREDIT_EST["retarget_each"] * len(anims)
            if args.rig else 0
        )
        print("[tripo_gen] DRY-RUN image-to-3D")
        print("  image     : %s" % args.image)
        print("  model     : %s" % model)
        print("  rig+anim  : %s (%s)" % (args.rig, ",".join(anims) if args.rig else "-"))
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
        _do_rig_pipeline(headers, task_id, args.animations, out_dir, args.timeout, meta,
                         out_format=args.out_format)
    _write_meta(out_dir, meta)
    print("[tripo_gen] OK — generation=%s glb_bytes=%d" % (task_id, size))


def _cmd_rig(args) -> None:
    out_dir = _resolve_out(args)
    if args.dry_run:
        anims = args.animations or DEFAULT_BIPED_ANIMATIONS  # actual set is rig_type-chosen at run time
        est = CREDIT_EST["rig"] + CREDIT_EST["retarget_each"] * len(anims)
        print("[tripo_gen] DRY-RUN rig pipeline (rig-check -> rig -> retarget, one preset/call)")
        print("  source task: %s" % args.task)
        print("  animations : %s (biped default shown; non-biped uses %s)"
              % (",".join(anims), ",".join(DEFAULT_CREATURE_ANIMATIONS)))
        print("  out format : %s" % args.out_format)
        print("  out dir    : %s" % out_dir)
        print("  est credits: ~%d (API is source of truth)" % est)
        return
    os.makedirs(out_dir, exist_ok=True)
    key = _load_api_key()
    headers = _auth_headers(key)
    meta = {"generation_task_id": args.task, "source": "tripo3d-rig-retarget"}
    _do_rig_pipeline(headers, args.task, args.animations, out_dir, args.timeout, meta,
                     out_format=args.out_format)
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
    sp.add_argument("--animations", nargs="+", default=None,
                    help="retarget clip names, namespaced per rig_type at call time. Default depends "
                         "on rig_type: biped=[%s], non-biped=[%s]."
                         % (" ".join(DEFAULT_BIPED_ANIMATIONS), " ".join(DEFAULT_CREATURE_ANIMATIONS)))
    sp.add_argument("--out-format", choices=("glb", "fbx"), default="glb", dest="out_format",
                    help="rig/retarget output format; fbx is Unity-ready directly (no Blender)")
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
