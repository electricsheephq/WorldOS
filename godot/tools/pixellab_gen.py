#!/usr/bin/env python3
"""pixellab_gen.py — STUB wrapper for PixelLab (RESERVED for the FUTURE GT1 tile/pixel game-type).

  *** GT1 FUTURE — NOT WIRED. This is intentionally a stub. ***

PixelLab (https://www.pixellab.ai) is a pixel-art generation service: top-down/side-view
PIXEL SPRITES, animated character sprites (walk/idle/attack frames), and TILESETS for 2D
tile maps. WorldOS reserves it for a future GT1 (game-type 1) TILE/PIXEL engine — the
small-tile, retro-pixel mode — which does NOT exist yet. The current GT2 final-art
pipeline uses Meshy/Tripo (3D -> bake) and Scenario (2D painterly art); none of that
touches PixelLab.

Downstream linkage (future): when GT1 lands, this tool would emit pixel sprites/tilesets
that feed a GT1-specific packer (analogous to how meshy_gen/tripo_gen -> bake_sprites.py
-> pack_sheet.py serves GT2). It would NOT use bake_sprites.py (that renders 3D at the
dimetric 2:1 projection in godot/ISO-PROJECTION.md; pixel art is authored, not baked).

VERIFIED PixelLab contract (confirmed live):
  * Transport: MCP (Model Context Protocol) — JSON-RPC 2.0 over HTTP POST.
  * Endpoint: https://api.pixellab.ai/mcp
  * Auth: Bearer  ``Authorization: Bearer <key>``
  * The MCP responds with text/event-stream (SSE): lines ``event: message`` then
    ``data: {<json-rpc result>}``. A plain ``tools/list`` call returns the available
    tools (e.g. create_character, ...). NOTE: the REST ``/balance`` endpoint is 404 — the
    service is MCP-first, which is why this stub speaks JSON-RPC, not REST.

The API key is read from ~/.worldos/pixellab.key or $WORLDOS_PIXELLAB_API_KEY. It is
NEVER printed/logged and NEVER written into any repo file.

Usage:
    # auth smoke-test — POSTs JSON-RPC tools/list to the MCP, prints the tool count
    python3 pixellab_gen.py --test-key
    # -> "PixelLab Auth OK" + the number of MCP tools available

Full generation (pixel sprites / tilesets / animation) is deliberately NOT implemented
here — it is GT1 future work. When GT1 is scoped, wire the relevant MCP tools
(create_character, animate, tileset, ...) following the JSON-RPC pattern in _mcp_call().
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

MCP_ENDPOINT = "https://api.pixellab.ai/mcp"


# --------------------------------------------------------------------------- #
# Key handling. NEVER print/log the key; NEVER write it to a repo file.
# --------------------------------------------------------------------------- #
def _load_api_key() -> str:
    key = os.environ.get("WORLDOS_PIXELLAB_API_KEY", "").strip()
    if key:
        return key
    key_path = os.path.expanduser("~/.worldos/pixellab.key")
    if os.path.isfile(key_path):
        with open(key_path, "r") as f:
            key = f.read().strip()
        if key:
            return key
    sys.exit(
        "[pixellab_gen] ERROR: no API key. Set $WORLDOS_PIXELLAB_API_KEY or put it in "
        "~/.worldos/pixellab.key"
    )


def _mcp_call(key: str, method: str, params: dict = None) -> dict:
    """POST a JSON-RPC 2.0 request to the PixelLab MCP and parse its SSE response.

    The MCP returns text/event-stream: one or more `event: <type>` / `data: <json>` line
    pairs. We collect the JSON from `data:` lines and return the LAST one carrying a
    JSON-RPC `result`/`error` (the response to our request).
    """
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        MCP_ENDPOINT,
        data=data,
        headers={
            "Authorization": "Bearer %s" % key,
            "Content-Type": "application/json",
            # The MCP streams SSE; advertise that we accept it.
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            sys.exit("[pixellab_gen] AUTH FAILED: HTTP %d — bad/expired API key." % e.code)
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            detail = "<no body>"
        sys.exit("[pixellab_gen] ERROR HTTP %d on MCP %s. Detail: %s" % (e.code, method, detail))
    except urllib.error.URLError as e:
        sys.exit("[pixellab_gen] ERROR: network failure on MCP %s: %s" % (method, e.reason))

    return _parse_mcp_response(raw)


def _parse_mcp_response(raw: str) -> dict:
    """Parse an MCP response that may be plain JSON or SSE (event:/data: lines)."""
    raw = raw.strip()
    if not raw:
        sys.exit("[pixellab_gen] ERROR: empty MCP response.")
    # Plain JSON?
    if raw[0] == "{":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    # SSE: scan `data:` lines, keep the last that has a JSON-RPC result/error.
    last = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if "result" in obj or "error" in obj:
            last = obj
    if last is None:
        sys.exit("[pixellab_gen] ERROR: could not parse MCP SSE response: %s" % raw[:300])
    return last


# --------------------------------------------------------------------------- #
# Sub-command bodies.
# --------------------------------------------------------------------------- #
def _cmd_test_key() -> None:
    """Auth smoke-test: JSON-RPC tools/list against the MCP; print the tool count."""
    key = _load_api_key()
    resp = _mcp_call(key, "tools/list")
    if "error" in resp:
        sys.exit("[pixellab_gen] ERROR: MCP tools/list returned an error: %s" % json.dumps(resp["error"]))
    tools = (resp.get("result") or {}).get("tools") or []
    print("PixelLab Auth OK")
    print("[pixellab_gen] MCP tools available: %d" % len(tools))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="STUB wrapper for PixelLab pixel-art MCP (GT1 future — not wired)."
    )
    ap.add_argument("--test-key", action="store_true",
                    help="auth smoke-test: POST JSON-RPC tools/list to the MCP -> 'PixelLab Auth OK' + tool count")
    args = ap.parse_args(argv)

    if args.test_key:
        _cmd_test_key()
        return
    ap.print_help()
    print(
        "\n[pixellab_gen] NOTE: this is a STUB reserved for the FUTURE GT1 tile/pixel "
        "game-type. Only --test-key is implemented; full pixel-sprite/tileset generation "
        "is GT1 future work and is NOT wired."
    )


if __name__ == "__main__":
    main()
