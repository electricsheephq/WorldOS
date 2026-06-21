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
    else:
        ap.print_help()
        sys.exit("\n[scenario_gen] ERROR: a subcommand (list-models|generate|upscale) or --test-key is required.")


if __name__ == "__main__":
    main()
