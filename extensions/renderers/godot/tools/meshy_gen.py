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

Rig + animate (HUMANOID only — Meshy cannot rig creatures; use tripo_gen.py for those):
  6. POST /openapi/v1/rigging {input_task_id, height_meters} -> rigged fbx+glb + FREE walk/run clips
  7. POST /openapi/v1/animations {rig_task_id, action_id}    -> one library clip (3 cr each)
  Import the rigged FBX into Unity as animationType=GENERIC (not Humanoid — the Meshy bone names
  don't auto-map to Unity's Humanoid avatar, which silently drops the clips). Verified 2026-06-28.

Usage:
    python3 meshy_gen.py --prompt "a stylized fantasy human ranger ..." --out <dir>
    python3 meshy_gen.py --prompt "..." --out <dir> --no-refine   # geometry only (no texture)
    python3 meshy_gen.py --prompt "..." --out <dir> --force        # re-generate even if model.glb exists
    python3 meshy_gen.py --prompt "..." --out <dir> --rig          # gen -> rig (+ free walk/run)
    python3 meshy_gen.py --prompt "..." --out <dir> --rig --animate 0 4   # + Idle(0) + Attack(4)
    python3 meshy_gen.py --prompt "..." --out <dir> --moveset             # FULL WorldOS combat moveset, one command
    python3 meshy_gen.py --rig-from-task <meshy_model_task_id> --out <dir> --moveset  # moveset onto an existing model
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
# Rigging + animation live on /openapi/v1 (the version split is a Meshy footgun).
RIGGING_PATH = "/openapi/v1/rigging"
RIGGING_POLL = "/openapi/v1/rigging/{task_id}"
ANIM_PATH = "/openapi/v1/animations"

# ★ The canonical WorldOS combat moveset → Meshy animation-library action_ids (ALL live-verified
# 2026-06-28: each id generated a real clip on a rigged elf). walk + run come FREE with the rig
# (basic_animations), so `--moveset` only spends 3 cr/clip on these 7 library actions.
# Source of ids: Meshy's animation library (no REST list endpoint — cache from the docs).
WORLDOS_MOVESET = {
    "idle": 0, "attack": 4, "cast": 125, "block": 138, "dodge": 156, "hit": 178, "death": 8,
}
ANIM_POLL = "/openapi/v1/animations/{task_id}"

# Polling cadence + ceilings.
POLL_INTERVAL_SEC = 8
DEFAULT_TIMEOUT_SEC = 600


# ---------------------------------------------------------------------------
# Key handling. NEVER print/log the key; NEVER write it to a repo file.
# ---------------------------------------------------------------------------
def _load_api_key() -> str:
    # Precedence (mirrors the sibling wrappers): WORLDOS_MESHY_API_KEY (CI/env-canonical),
    # then the legacy MESHY_API_KEY, then the mode-600 file outside the repo.
    key = os.environ.get("WORLDOS_MESHY_API_KEY", "").strip()
    if key:
        return key
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
        "[meshy_gen] ERROR: no API key. Set $WORLDOS_MESHY_API_KEY or $MESHY_API_KEY, "
        "or put it in ~/.worldos/meshy.key"
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


def _poll(headers: dict, task_id: str, label: str, timeout_sec: int,
          poll_template: str = POLL_TEMPLATE) -> dict:
    """Poll a task until SUCCEEDED. Exits on FAILED / timeout. Returns the final task dict.

    poll_template selects the endpoint family: v2 text-to-3d (default), or the v1 rigging /
    animation templates. All share the same SUCCEEDED/FAILED/progress status model.
    """
    url = API_BASE + poll_template.format(task_id=task_id)
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
# Rigging + animation (HUMANOID only). Mirrors tripo_gen.py's rig pipeline shape.
# ---------------------------------------------------------------------------
def _create_rigging(headers: dict, model_task_id: str, height_meters: float) -> str:
    # input_task_id = a prior SUCCEEDED Meshy text-to-3d/image-to-3d task. Humanoid only (else 422).
    body = {"input_task_id": model_task_id, "height_meters": height_meters}
    res = _post_json(API_BASE + RIGGING_PATH, headers, body)
    task_id = res.get("result")
    if not task_id:
        sys.exit("[meshy_gen] ERROR: rigging create returned no task id: %s" % json.dumps(res))
    print("[meshy_gen] rigging task submitted: %s" % task_id)
    return task_id


def _create_animation(headers: dict, rig_task_id: str, action_id: int) -> str:
    # action_id is an integer from the Meshy animation library (0=Idle, 4=Attack, 14=Run_02, ...).
    body = {"rig_task_id": rig_task_id, "action_id": int(action_id)}
    res = _post_json(API_BASE + ANIM_PATH, headers, body)
    task_id = res.get("result")
    if not task_id:
        sys.exit("[meshy_gen] ERROR: animation create returned no task id: %s" % json.dumps(res))
    print("[meshy_gen] animation task submitted: %s (action_id=%d)" % (task_id, int(action_id)))
    return task_id


def _download_pairs(result: dict, out_dir: str, pairs: list) -> dict:
    """Download a set of (result-key, dest-filename) URLs that are present; return {name: bytes}.

    Prefers FBX for Unity (Mecanim). Missing keys are skipped (e.g. armature-only variants).
    """
    saved: dict = {}
    for key, fname in pairs:
        url = result.get(key)
        if isinstance(url, str) and url:
            size = _download(url, os.path.join(out_dir, fname))
            saved[fname] = size
            print("[meshy_gen] downloaded %s (%d bytes)" % (fname, size))
    return saved


def _run_rig_and_animate(headers: dict, model_task_id: str, out_dir: str, height: float,
                         action_ids: list, timeout: int, meta: dict, clip_names: dict = None) -> None:
    """rig the model (humanoid) -> download rigged FBX/GLB + free walk/run -> optional library clips.

    clip_names maps action_id -> friendly name (e.g. {4: "attack"}); when present, clips are saved as
    anim_<name>.fbx (not anim_action_<id>.fbx) so the moveset lands as named, agent-readable files.
    """
    names = clip_names or {}
    rig_id = _create_rigging(headers, model_task_id, height)
    rig_task = _poll(headers, rig_id, "rigging", timeout, poll_template=RIGGING_POLL)
    result = rig_task.get("result", {}) or {}
    rig_files = _download_pairs(result, out_dir, [
        ("rigged_character_fbx_url", "rigged.fbx"),
        ("rigged_character_glb_url", "rigged.glb"),
    ])
    basic = result.get("basic_animations", {}) or {}
    basic_files = _download_pairs(basic, out_dir, [
        ("walking_fbx_url", "anim_walk.fbx"),    # the rig's FREE locomotion clips, named for the moveset
        ("running_fbx_url", "anim_run.fbx"),
    ])
    meta["rig_task_id"] = rig_id
    meta["rig_files"] = rig_files
    meta["basic_animation_files"] = basic_files

    anim_files: dict = {}
    for aid in action_ids or []:
        label = names.get(int(aid), "action_%d" % int(aid))
        an_id = _create_animation(headers, rig_id, aid)
        an_task = _poll(headers, an_id, "animation:%s" % label, timeout, poll_template=ANIM_POLL)
        an_result = an_task.get("result", {}) or {}
        files = _download_pairs(an_result, out_dir, [
            ("animation_fbx_url", "anim_%s.fbx" % label),
            ("animation_glb_url", "anim_%s.glb" % label),
        ])
        anim_files[label] = dict(action_id=int(aid), task_id=an_id, **files)
    if action_ids:
        meta["animation_files"] = anim_files


# ---------------------------------------------------------------------------
# --test-key (cheap auth probe) and --dry-run (no API call).
# ---------------------------------------------------------------------------
# Rough per-stage credit estimates (for --dry-run only; the API is the source of truth).
CREDIT_EST = {"preview": "~5-20", "refine": "~10"}


def _cmd_test_key() -> None:
    """Cheap auth smoke-test. GET a dummy task id:
       401/403 => bad key; 404 => key is valid (task just doesn't exist); 200 => valid.
       Never generates anything, never spends credits, never prints the key."""
    key = _load_api_key()
    headers = _auth_headers(key)
    url = API_BASE + POLL_TEMPLATE.format(task_id="00000000-0000-0000-0000-000000000000")
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        print("Meshy Auth OK")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            sys.exit("[meshy_gen] AUTH FAILED: HTTP %d — bad/expired Meshy API key." % e.code)
        # 404 (and any other non-auth code) means the request was authenticated/accepted.
        print("Meshy Auth OK")
    except urllib.error.URLError as e:
        sys.exit("[meshy_gen] ERROR: network failure on --test-key: %s" % e.reason)


def _cmd_dry_run(args) -> None:
    """Print the generation plan + estimated credits and exit — NO API call, NO key needed."""
    out_dir = os.path.abspath(args.out)
    print("[meshy_gen] DRY-RUN (NO API call)")
    print("  out dir    : %s" % out_dir)
    if args.rig_from_task:
        print("  plan       : rig existing model task %s (no generation)" % args.rig_from_task)
    elif args.no_refine:
        print("  prompt     : %s" % args.prompt)
        print("  plan       : preview only (untextured geometry; --no-refine)")
        print("  est credits: preview %s" % CREDIT_EST["preview"])
    else:
        print("  prompt     : %s" % args.prompt)
        print("  plan       : preview -> refine (PBR-textured)")
        print("  est credits: preview %s + refine %s (API is source of truth)" % (
            CREDIT_EST["preview"], CREDIT_EST["refine"]))
    if args.rig or args.rig_from_task or args.action_ids:
        print("  rig        : YES (HUMANOID only) height=%.2fm -> rigged fbx/glb + free walk/run (~5 cr)"
              % args.rig_height)
        if args.moveset:
            print("  moveset    : WorldOS combat set %s (~%d cr) -> named anim_<name>.fbx"
                  % (",".join(WORLDOS_MOVESET), 3 * len(WORLDOS_MOVESET)))
        elif args.action_ids:
            print("  animate    : action_ids %s (~3 cr each)" % " ".join(str(a) for a in args.action_ids))


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a textured .glb via Meshy text-to-3d.")
    # --prompt/--out are NOT required for --test-key (and we validate them for the real run below).
    ap.add_argument("--prompt", help="text-to-3d prompt (required unless --test-key)")
    ap.add_argument("--out", help="output dir for model.glb (+ thumbnail.png) (required unless --test-key)")
    ap.add_argument("--test-key", action="store_true",
                    help="cheap auth smoke-test (no generation, no credits) -> 'Meshy Auth OK'")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan + est credits and exit; makes NO API call")
    ap.add_argument("--no-refine", action="store_true",
                    help="stop after preview (untextured geometry only)")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if <out>/model.glb already exists")
    ap.add_argument("--rig", action="store_true",
                    help="after generation, auto-rig the model (HUMANOID only) + download free walk/run")
    ap.add_argument("--rig-from-task", dest="rig_from_task",
                    help="skip generation; rig an EXISTING Meshy model task id (implies --rig)")
    ap.add_argument("--rig-height", dest="rig_height", type=float, default=1.7,
                    help="character height in meters for rigging (default 1.7)")
    ap.add_argument("--animate", dest="action_ids", nargs="+", type=int, metavar="ACTION_ID",
                    help="library action_id(s) to apply after rigging (e.g. 0=Idle 4=Attack); implies --rig")
    ap.add_argument("--moveset", action="store_true",
                    help="apply the full WorldOS combat moveset (rig + walk/run free + %s); "
                         "clips land as named anim_<name>.fbx" % "/".join(WORLDOS_MOVESET))
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                    help="per-stage poll timeout in seconds (default %d)" % DEFAULT_TIMEOUT_SEC)
    args = ap.parse_args()

    if args.test_key:
        _cmd_test_key()
        return

    # --moveset = the canonical combat set, saved under friendly names.
    clip_names = None
    if args.moveset:
        if args.action_ids:
            ap.error("--moveset and --animate are mutually exclusive.")
        args.action_ids = list(WORLDOS_MOVESET.values())
        clip_names = {v: k for k, v in WORLDOS_MOVESET.items()}

    do_rig = bool(args.rig or args.rig_from_task or args.action_ids)

    # --rig-from-task operates on an existing model; otherwise --prompt is required to generate.
    if not args.out:
        ap.error("--out is required (unless --test-key).")
    if not args.rig_from_task and not args.prompt:
        ap.error("--prompt is required (unless --test-key or --rig-from-task).")
    if args.action_ids:
        # Reject duplicate ids: each clip writes anim_action_<id>.* + meta["animation_files"][id],
        # so a repeat would bill twice and silently clobber the earlier output.
        deduped = list(dict.fromkeys(args.action_ids))
        if len(deduped) != len(args.action_ids):
            ap.error("--animate action IDs must be unique.")

    if args.dry_run:
        _cmd_dry_run(args)
        return

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    model_path = os.path.join(out_dir, "model.glb")
    thumb_path = os.path.join(out_dir, "thumbnail.png")
    meta_path = os.path.join(out_dir, "meshy_meta.json")

    # Only the no-rig generation path short-circuits on an existing model.glb; rig paths must proceed
    # (they need a live task id, which --rig-from-task supplies and a fresh gen produces).
    if os.path.exists(model_path) and not args.force and not do_rig:
        print("[meshy_gen] %s already exists; skipping (use --force to regenerate)." % model_path)
        return

    key = _load_api_key()
    headers = _auth_headers(key)
    # Provenance is set per-branch — don't stamp text-to-3d defaults onto a reused task.
    meta: dict = {}

    if args.rig_from_task:
        # Rig an EXISTING Meshy model task — skip generation entirely. Its origin (which model /
        # whether image-to-3d) is unknown here, so record only the rigging-reuse provenance.
        model_task_id = args.rig_from_task
        meta["model_task_id"] = model_task_id
        meta["source"] = "meshy-rigging-existing-task"
        print("[meshy_gen] rigging existing model task %s (generation skipped)." % model_task_id)
    else:
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

        # ---- download base model ----
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

        consumed = (
            final_task.get("consumed_credits")
            or final_task.get("credits")
            or preview_task.get("consumed_credits")
        )
        # Rigging consumes the textured model (refine) when present, else the preview geometry.
        model_task_id = refine_id or preview_id
        meta.update({
            "ai_model": "meshy-5",
            "source": "meshy-text-to-3d-v2",
            "prompt": args.prompt,
            "preview_task_id": preview_id,
            "refine_task_id": refine_id,
            "model_task_id": model_task_id,
            "refined": not args.no_refine,
            "glb_bytes": size,
            "consumed_credits": consumed,
        })

    # ---- rig + animate (HUMANOID only) ----
    if do_rig:
        _run_rig_and_animate(headers, model_task_id, out_dir, args.rig_height,
                             args.action_ids, args.timeout, meta, clip_names=clip_names)

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")
    summary = {k: meta[k] for k in
               ("preview_task_id", "refine_task_id", "model_task_id", "rig_task_id")
               if meta.get(k)}
    print("[meshy_gen] OK — %s" % json.dumps(summary))
    print("[meshy_gen] meta -> %s" % meta_path)


if __name__ == "__main__":
    main()
