#!/usr/bin/env python3
"""meshy_gen.py — generate a single textured 3D character via the Meshy text-to-3d API (#1062).

Stage 1 of the WorldOS GT2 final-art pipeline:

    meshy_gen.py  -> a textured .glb        (this tool; Meshy AI)
    bake_sprites.py -> 8-facing frame PNGs  (Blender headless, dimetric 2:1)
    pack_sheet.py  -> the renderer sheet.png + sheet.json manifest

This tool is WorldOS-ORIGINAL code; the ART it downloads (model.glb) is NOT committed —
it lives under content/worlds/_private/ (gitignored). See ISO-PROJECTION.md for the locked
projection the downstream bake targets.

Flow (per the verified Meshy v2 text-to-3d contract):
  1. POST /openapi/v2/text-to-3d  mode=preview  -> task id
  2. poll GET /openapi/v2/text-to-3d/<id> until SUCCEEDED (untextured geometry)
  3. POST /openapi/v2/text-to-3d  mode=refine preview_task_id=<id> enable_pbr -> task id
  4. poll until SUCCEEDED -> model_urls.glb is the TEXTURED model
  5. download that glb to <out>/model.glb (+ thumbnail.png if available)

The API key is read from ~/.worldos/meshy.key or $MESHY_API_KEY. It is NEVER printed and
NEVER written into any repo file.

Usage:
    python3 meshy_gen.py --prompt "a stylized fantasy human ranger ..." --out <dir>
    python3 meshy_gen.py --prompt "..." --out <dir> --no-refine   # geometry only (no texture)
    python3 meshy_gen.py --prompt "..." --out <dir> --force        # re-generate even if model.glb exists
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://api.meshy.ai"
CREATE_PATH = "/openapi/v2/text-to-3d"
POLL_TEMPLATE = "/openapi/v2/text-to-3d/{task_id}"

# Polling cadence + ceilings.
POLL_INTERVAL_SEC = 8
DEFAULT_TIMEOUT_SEC = 600


# ---------------------------------------------------------------------------
# Key handling. NEVER print/log the key; NEVER write it to a repo file.
# ---------------------------------------------------------------------------
def _load_api_key() -> str:
    key = os.environ.get("MESHY_API_KEY", "").strip()
    if key:
        return key
    key_path = os.path.expanduser("~/.worldos/meshy.key")
    if os.path.isfile(key_path):
        with open(key_path, "r") as f:
            key = f.read().strip()
        if key:
            return key
    sys.exit(
        "[meshy_gen] ERROR: no API key. Set $MESHY_API_KEY or put it in ~/.worldos/meshy.key"
    )


def _auth_headers(key: str) -> dict:
    return {
        "Authorization": "Bearer %s" % key,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# HTTP (urllib only — no `requests` dependency required).
# ---------------------------------------------------------------------------
def _post_json(url: str, headers: dict, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = _read_error(e)
        _explain_http(e.code, detail, "POST " + url)
    except urllib.error.URLError as e:
        sys.exit("[meshy_gen] ERROR: network failure on POST %s: %s" % (url, e.reason))


def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = _read_error(e)
        _explain_http(e.code, detail, "GET " + url)
    except urllib.error.URLError as e:
        sys.exit("[meshy_gen] ERROR: network failure on GET %s: %s" % (url, e.reason))


def _read_error(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8")
    except Exception:
        return "<no body>"


def _explain_http(code: int, detail: str, what: str) -> None:
    if code == 402:
        sys.exit(
            "[meshy_gen] ERROR 402 insufficient Meshy credits on %s. Top up the account; "
            "art was NOT generated. Detail: %s" % (what, detail)
        )
    if code == 429:
        sys.exit(
            "[meshy_gen] ERROR 429 rate-limited on %s. Wait and retry. Detail: %s"
            % (what, detail)
        )
    sys.exit("[meshy_gen] ERROR HTTP %d on %s. Detail: %s" % (code, what, detail))


def _download(url: str, dest: str) -> int:
    """Stream a binary asset (glb / png) to dest. Returns bytes written."""
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
        sys.exit("[meshy_gen] ERROR downloading %s -> %s: %s" % (url, dest, e))


# ---------------------------------------------------------------------------
# Task lifecycle.
# ---------------------------------------------------------------------------
def _create_preview(headers: dict, prompt: str) -> str:
    body = {
        "mode": "preview",
        "prompt": prompt,
        "ai_model": "meshy-5",
        "model_type": "standard",
        "pose_mode": "t-pose",
        "target_formats": ["glb"],
        "should_remesh": True,
        "topology": "triangle",
        "target_polycount": 30000,
        "auto_size": True,
        "origin_at": "bottom",
    }
    res = _post_json(API_BASE + CREATE_PATH, headers, body)
    task_id = res.get("result")
    if not task_id:
        sys.exit("[meshy_gen] ERROR: preview create returned no task id: %s" % json.dumps(res))
    print("[meshy_gen] preview task submitted: %s" % task_id)
    return task_id


def _create_refine(headers: dict, preview_task_id: str) -> str:
    body = {
        "mode": "refine",
        "preview_task_id": preview_task_id,
        "enable_pbr": True,
        "target_formats": ["glb"],
    }
    res = _post_json(API_BASE + CREATE_PATH, headers, body)
    task_id = res.get("result")
    if not task_id:
        sys.exit("[meshy_gen] ERROR: refine create returned no task id: %s" % json.dumps(res))
    print("[meshy_gen] refine task submitted: %s" % task_id)
    return task_id


def _poll(headers: dict, task_id: str, label: str, timeout_sec: int) -> dict:
    """Poll a task until SUCCEEDED. Exits on FAILED / timeout. Returns the final task dict."""
    url = API_BASE + POLL_TEMPLATE.format(task_id=task_id)
    deadline = time.time() + timeout_sec
    last_progress = -1
    while True:
        task = _get_json(url, headers)
        status = task.get("status", "UNKNOWN")
        progress = int(task.get("progress", 0) or 0)
        if progress != last_progress or status not in ("PENDING", "IN_PROGRESS"):
            print("[meshy_gen] %s status=%s progress=%d%%" % (label, status, progress))
            last_progress = progress
        if status == "SUCCEEDED":
            return task
        if status == "FAILED":
            err = task.get("task_error") or task.get("error") or {}
            sys.exit("[meshy_gen] ERROR: %s task FAILED: %s" % (label, json.dumps(err)))
        if status in ("EXPIRED", "CANCELED"):
            sys.exit("[meshy_gen] ERROR: %s task %s" % (label, status))
        if time.time() > deadline:
            sys.exit(
                "[meshy_gen] ERROR: %s task timed out after %ds (last status=%s progress=%d%%). "
                "Art was NOT completed." % (label, timeout_sec, status, progress)
            )
        time.sleep(POLL_INTERVAL_SEC)


def _glb_url(task: dict) -> str:
    urls = task.get("model_urls", {}) or {}
    glb = urls.get("glb")
    if not glb:
        sys.exit("[meshy_gen] ERROR: task has no model_urls.glb: %s" % json.dumps(urls))
    return glb


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a textured .glb via Meshy text-to-3d.")
    ap.add_argument("--prompt", required=True, help="text-to-3d prompt")
    ap.add_argument("--out", required=True, help="output dir for model.glb (+ thumbnail.png)")
    ap.add_argument("--no-refine", action="store_true",
                    help="stop after preview (untextured geometry only)")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if <out>/model.glb already exists")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                    help="per-stage poll timeout in seconds (default %d)" % DEFAULT_TIMEOUT_SEC)
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    model_path = os.path.join(out_dir, "model.glb")
    thumb_path = os.path.join(out_dir, "thumbnail.png")
    meta_path = os.path.join(out_dir, "meshy_meta.json")

    if os.path.exists(model_path) and not args.force:
        print("[meshy_gen] %s already exists; skipping (use --force to regenerate)." % model_path)
        return

    key = _load_api_key()
    headers = _auth_headers(key)

    # ---- preview ----
    preview_id = _create_preview(headers, args.prompt)
    preview_task = _poll(headers, preview_id, "preview", args.timeout)

    final_task = preview_task
    refine_id = None
    if args.no_refine:
        print("[meshy_gen] --no-refine: keeping the untextured preview model.")
    else:
        # ---- refine (adds PBR texture) ----
        refine_id = _create_refine(headers, preview_id)
        final_task = _poll(headers, refine_id, "refine", args.timeout)

    # ---- download ----
    glb_url = _glb_url(final_task)
    size = _download(glb_url, model_path)
    print("[meshy_gen] downloaded model.glb (%d bytes) -> %s" % (size, model_path))

    thumb_url = final_task.get("thumbnail_url")
    if thumb_url:
        try:
            tsize = _download(thumb_url, thumb_path)
            print("[meshy_gen] downloaded thumbnail.png (%d bytes)" % tsize)
        except SystemExit:
            print("[meshy_gen] (thumbnail download failed; continuing — non-fatal)")

    # consumed-credits: Meshy reports per-task usage on some plans.
    consumed = (
        final_task.get("consumed_credits")
        or final_task.get("credits")
        or preview_task.get("consumed_credits")
    )

    meta = {
        "prompt": args.prompt,
        "preview_task_id": preview_id,
        "refine_task_id": refine_id,
        "refined": not args.no_refine,
        "glb_bytes": size,
        "consumed_credits": consumed,
        "ai_model": "meshy-5",
        "source": "meshy-text-to-3d-v2",
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")

    print("[meshy_gen] OK — preview=%s refine=%s glb_bytes=%d consumed_credits=%s" % (
        preview_id, refine_id, size, consumed))
    print("[meshy_gen] meta -> %s" % meta_path)


if __name__ == "__main__":
    main()
