#!/usr/bin/env python3
"""mixamo_gen.py — headless Mixamo animation downloader for the WorldOS asset pipeline.

A sibling of meshy_gen.py / tripo_gen.py. Mixamo has NO official public API and NO Linux/headless
MCP (the `unity-mcp-mixamo` project ships a Windows-only .exe + a GUI-bound Unity package). But the
Mixamo WEBSITE drives an internal REST API with a user OAuth token — so this wrapper replicates that
exact flow (search -> product details -> export -> poll monitor -> download FBX) with urllib only,
runs anywhere (Linux/macOS), and drops named FBX clips into the pipeline. NO browser, NO Unity, NO .exe.

The API surface was reverse-engineered from the working `HaD0Yun/unity-mcp-mixamo` Python client
(Server/src/mixamo_mcp/client.py) + gnuton/mixamo_anims_downloader. It is UNOFFICIAL and Adobe has
signaled a Mixamo sunset — run `--test-key` first to confirm it's still live before a batch.

WHY Mixamo at all: it's the largest free HUMAN mocap library, richer than Meshy's preset set. Meshy
(`meshy_gen.py --moveset`) stays the zero-touch DEFAULT; Mixamo is the quality/variety upgrade. Clips
come on Mixamo's skeleton, which matches our Meshy/Tripo `spec:"mixamo"` rigs, so they retarget onto
our generated actors. Import the FBX in Unity as animationType=Generic (NOT Humanoid — Humanoid
silently drops clips on non-standard bone names; see the asset-gen skill).

AUTH (Mixamo is browser-login only): log in at mixamo.com, open DevTools console, run
`copy(localStorage.access_token)`, and save it to `~/.worldos/mixamo.token` (mode 600) or
`$WORLDOS_MIXAMO_TOKEN`. The token EXPIRES (~hours) — re-extract when `--test-key` says unauthorized.

Usage:
    python3 mixamo_gen.py --test-key                       # validate the token (GET /characters)
    python3 mixamo_gen.py search "sword slash"             # list matching animations
    python3 mixamo_gen.py download "Sword And Shield Slash" --out /tmp/clips
    python3 mixamo_gen.py moveset --out /tmp/clips          # the full WorldOS combat moveset, named FBX
    python3 mixamo_gen.py moveset --out /tmp/clips --skin   # include the mesh (first base download)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://www.mixamo.com/api/v1"

# The canonical WorldOS combat moveset -> a Mixamo SEARCH QUERY per clip (we take the top result).
# Names are the OUTPUT filenames (anim_<name>.fbx), matching meshy_gen.py's --moveset naming so both
# feeders land identically. Mixamo's library is huge; these queries hit a strong canonical clip.
WORLDOS_MOVESET = {
    "idle": "idle",
    "walk": "walking",
    "run": "running",
    "attack": "sword and shield slash",
    "cast": "standing 2h magic attack 01",
    "block": "sword and shield block",
    "dodge": "sword and shield dodge",
    "hit": "standing react large from right",
    "death": "standing death backward 01",
}

POLL_INTERVAL_SEC = 2
DEFAULT_TIMEOUT_SEC = 120


# --------------------------------------------------------------------------- #
# Token (NEVER print/log; NEVER write to a repo file).
# --------------------------------------------------------------------------- #
def _load_token() -> str:
    tok = os.environ.get("WORLDOS_MIXAMO_TOKEN", "").strip()
    if tok:
        return tok
    path = os.path.expanduser("~/.worldos/mixamo.token")
    if os.path.isfile(path):
        with open(path) as f:
            tok = f.read().strip()
        if tok:
            return tok
    sys.exit(
        "[mixamo_gen] ERROR: no Mixamo token. Log in at mixamo.com, run "
        "`copy(localStorage.access_token)` in the DevTools console, and save it to "
        "~/.worldos/mixamo.token (chmod 600) or $WORLDOS_MIXAMO_TOKEN."
    )


def _headers(token: str) -> dict:
    # X-Api-Key 'mixamo2' is the website's public key; required alongside the user Bearer token.
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Api-Key": "mixamo2",
        "Authorization": "Bearer %s" % token,
    }


# --------------------------------------------------------------------------- #
# HTTP (urllib only).
# --------------------------------------------------------------------------- #
def _get(url: str, headers: dict, params: dict = None) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _explain(e.code, _read_err(e), "GET " + url)
    except urllib.error.URLError as e:
        sys.exit("[mixamo_gen] ERROR: network failure on GET %s: %s" % (url, e.reason))


def _post(url: str, headers: dict, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _explain(e.code, _read_err(e), "POST " + url)
    except urllib.error.URLError as e:
        sys.exit("[mixamo_gen] ERROR: network failure on POST %s: %s" % (url, e.reason))


def _read_err(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8")[:500]
    except Exception:
        return "<no body>"


def _explain(code: int, detail: str, what: str) -> None:
    if code in (401, 403):
        sys.exit(
            "[mixamo_gen] ERROR %d on %s — the Mixamo token is missing/expired. Re-extract "
            "localStorage.access_token from a logged-in mixamo.com tab. Detail: %s" % (code, what, detail)
        )
    if code == 404:
        sys.exit("[mixamo_gen] ERROR 404 on %s — Mixamo may have sunset this endpoint. Detail: %s"
                 % (what, detail))
    sys.exit("[mixamo_gen] ERROR HTTP %d on %s. Detail: %s" % (code, what, detail))


def _download(url: str, dest: str) -> int:
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
        sys.exit("[mixamo_gen] ERROR downloading %s -> %s: %s" % (url, dest, e))


# --------------------------------------------------------------------------- #
# Mixamo API flow (search -> details -> export -> monitor -> download).
# --------------------------------------------------------------------------- #
def _primary_character_id(headers: dict) -> str:
    """Mixamo retargets onto a character; export needs the account's character_id."""
    data = _get(BASE_URL + "/characters", headers)
    cid = data.get("primary_character_id")
    if cid:
        return cid
    results = data.get("results") or []
    if results:
        cid = results[0].get("character_id") or results[0].get("id")
        if cid:
            return cid
    sys.exit("[mixamo_gen] ERROR: no character in the Mixamo account; upload one at mixamo.com first.")


def _search(headers: dict, query: str, limit: int = 12) -> list:
    data = _get(BASE_URL + "/products", headers, params={
        "page": 1, "limit": min(limit, 24), "order": "", "query": query, "type": "Motion,MotionPack",
    })
    return data.get("results", []) or []


def _resolve_animation(headers: dict, name_or_id: str) -> dict:
    """Find the animation product by exact id or by best search match on the name."""
    hits = _search(headers, name_or_id, limit=24)
    for h in hits:
        if h.get("id") == name_or_id or (h.get("name", "").lower() == name_or_id.lower()):
            return h
    if hits:
        return hits[0]   # best match
    sys.exit("[mixamo_gen] ERROR: no Mixamo animation matches %r." % name_or_id)


def _gms_hash(headers: dict, animation_id: str, character_id: str) -> dict:
    """Fetch product details and build the export gms_hash (params array -> comma string)."""
    details = _get(BASE_URL + "/products/%s" % animation_id, headers,
                   params={"similar": 0, "character_id": character_id})
    gms = (details.get("details") or {}).get("gms_hash") or {}
    if not gms:
        sys.exit("[mixamo_gen] ERROR: animation %s has no gms_hash (not exportable)." % animation_id)
    out = dict(gms)
    params = gms.get("params", [])
    if isinstance(params, list):
        out["params"] = ",".join(str(p[1]) for p in params if isinstance(p, (list, tuple)) and len(p) > 1) or "0"
    else:
        out["params"] = str(params) if params else "0"
    return out, details.get("description") or details.get("name") or animation_id


def _export_and_download(headers: dict, character_id: str, gms: dict, product_name: str,
                         dest: str, skin: bool, timeout: int) -> int:
    body = {
        "character_id": character_id,
        "gms_hash": [gms],
        "preferences": {"format": "fbx7_2019", "skin": "true" if skin else "false",
                        "fps": "30", "reducekf": "0"},
        "product_name": product_name,
        "type": "Motion",
    }
    _post(BASE_URL + "/animations/export", headers, body)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SEC)
        mon = _get(BASE_URL + "/characters/%s/monitor" % character_id, headers)
        status = str(mon.get("status", "")).lower()
        if status in ("completed", "succeeded"):
            url = mon.get("job_result")
            if not url:
                sys.exit("[mixamo_gen] ERROR: export completed but no job_result URL: %s" % json.dumps(mon))
            return _download(url, dest)
        if status in ("failed", "error"):
            sys.exit("[mixamo_gen] ERROR: Mixamo export failed: %s" % mon.get("message", json.dumps(mon)))
    sys.exit("[mixamo_gen] ERROR: export timed out after %ds." % timeout)


def _fetch_clip(headers: dict, character_id: str, query: str, out_path: str,
                skin: bool, timeout: int) -> int:
    anim = _resolve_animation(headers, query)
    gms, product_name = _gms_hash(headers, anim["id"], character_id)
    print("[mixamo_gen] export %r (%s) -> %s" % (anim.get("name", query), anim["id"], out_path))
    return _export_and_download(headers, character_id, gms, product_name, out_path, skin, timeout)


# --------------------------------------------------------------------------- #
# Commands.
# --------------------------------------------------------------------------- #
def _cmd_test_key() -> None:
    """Validate the token + confirm the (unofficial, sunset-risk) API is still live."""
    headers = _headers(_load_token())
    data = _get(BASE_URL + "/characters", headers)
    cid = data.get("primary_character_id") or (data.get("results") or [{}])[0].get("character_id")
    print("Mixamo Auth OK" + (" (character %s)" % cid if cid else " (no character uploaded yet)"))


def _cmd_search(args) -> None:
    headers = _headers(_load_token())
    for h in _search(headers, args.query, limit=args.limit):
        print("  %-40s  id=%s" % (h.get("name", "")[:40], h.get("id")))


def _cmd_download(args) -> None:
    headers = _headers(_load_token())
    cid = _primary_character_id(headers)
    os.makedirs(args.out, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in args.name)[:64]
    size = _fetch_clip(headers, cid, args.name, os.path.join(args.out, "anim_%s.fbx" % safe),
                       args.skin, args.timeout)
    print("[mixamo_gen] downloaded anim_%s.fbx (%d bytes)" % (safe, size))


def _cmd_moveset(args) -> None:
    headers = _headers(_load_token())
    cid = _primary_character_id(headers)
    os.makedirs(args.out, exist_ok=True)
    done = {}
    for i, (name, query) in enumerate(WORLDOS_MOVESET.items()):
        # first clip carries the skin (a base mesh) if --skin; the rest are clip-only for retargeting.
        skin = args.skin and i == 0
        path = os.path.join(args.out, "anim_%s.fbx" % name)
        try:
            done[name] = _fetch_clip(headers, cid, query, path, skin, args.timeout)
        except SystemExit as e:
            print("[mixamo_gen] WARN: %s (%r) skipped — %s" % (name, query, e))
    print("[mixamo_gen] moveset OK — %d/%d clips: %s"
          % (len(done), len(WORLDOS_MOVESET), ", ".join(done)))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Headless Mixamo animation downloader for WorldOS.")
    ap.add_argument("--test-key", action="store_true", help="validate the Mixamo token + API liveness")
    sub = ap.add_subparsers(dest="command")

    sp_s = sub.add_parser("search", help="list animations matching a query")
    sp_s.add_argument("query")
    sp_s.add_argument("--limit", type=int, default=12)

    sp_d = sub.add_parser("download", help="download one animation by name/id")
    sp_d.add_argument("name")
    sp_d.add_argument("--out", required=True)
    sp_d.add_argument("--skin", action="store_true", help="include the character mesh (default: clip only)")
    sp_d.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)

    sp_m = sub.add_parser("moveset", help="download the full WorldOS combat moveset as named FBX")
    sp_m.add_argument("--out", required=True)
    sp_m.add_argument("--skin", action="store_true", help="include the mesh on the FIRST clip (a base)")
    sp_m.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)

    args = ap.parse_args(argv)
    if args.test_key:
        _cmd_test_key()
        return
    if args.command == "search":
        _cmd_search(args)
    elif args.command == "download":
        _cmd_download(args)
    elif args.command == "moveset":
        _cmd_moveset(args)
    else:
        ap.print_help()
        sys.exit("\n[mixamo_gen] ERROR: a subcommand (search|download|moveset) or --test-key is required.")


if __name__ == "__main__":
    main()
