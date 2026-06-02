#!/usr/bin/env python3
"""M3 build-loop — STEP 3: emit the Phaser thin-client glue for a generated game (#451).

The WorldOS renderers (renderer-tilemap.js / renderer-backdrop.js) are GENERIC and entirely
profile-driven — they own no per-game logic. So "generate the thin-client glue for a new game"
is NOT codegen of a bespoke renderer (which would fork + rot); it is emitting a tiny
self-contained entry page that:
  - injects the generated render-profile via window.WORLDOS_PROFILE (so no extra fetch / MIME /
    path coupling — the page is portable),
  - loads the VENDORED Phaser + surface-client + the correct generic renderer for the profile's
    scene_kind (tilemap -> renderer-tilemap.js, backdrop -> renderer-backdrop.js), by ABSOLUTE
    served path so the page works wherever it is served from,
  - carries the surfaces + the frozen /move intents unchanged (the renderer already does this).

This keeps the loop's output minimal + honest: one HTML file per game, zero renderer forks, and
the moment the renderer improves, every generated game inherits it. The engine stays sole writer;
this emits presentation only.

Usage:
    python3 emit_glue.py <profile.json> [--out index.html] [--render-base /openworlds/render]
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

_RENDERER_FOR = {"tilemap": "renderer-tilemap.js", "backdrop": "renderer-backdrop.js"}


def emit_glue(profile: dict, *, render_base: str = "/openworlds/render") -> str:
    # scene_kind picks the renderer (allowlisted, never interpolated raw into HTML).
    scene_kind = profile.get("core", {}).get("scene_kind", "tilemap")
    renderer = _RENDERER_FOR.get(scene_kind, _RENDERER_FOR["tilemap"])
    # ALL generated/seed-derived strings are untrusted -> HTML-escape every interpolation into an
    # HTML text node or attribute. The embedded profile (a <script> body) is JSON-encoded AND has
    # its only script-breakout sequence neutralized.
    title = html.escape(str(profile.get("title", "WorldOS Game")))
    game_id_raw = str(profile.get("game_id", "untitled"))
    game_id_attr = html.escape(game_id_raw, quote=True)
    scene_kind_esc = html.escape(str(scene_kind))
    rb = render_base.rstrip("/")
    # Phaser is vendored ONE LEVEL UP from the renderers: renderers live at /openworlds/render/,
    # the vendor dir at /openworlds/vendor/ (mirrors tilemap.html's "../vendor/"). Derive it from
    # render_base so a non-default base stays correct.
    vendor_base = (rb.rsplit("/", 1)[0] if "/" in rb else rb) + "/vendor"
    # Embed the profile so the page is portable (no separate fetch). json.dumps + neutralize the
    # only sequences that can break out of / comment-inject a <script> element.
    embedded = json.dumps(profile).replace("</", "<\\/").replace("<!--", "<\\!--")
    disclosure = profile.get("core", {}).get("ai_disclosure", {}) or {}
    disc_line = ""
    if disclosure.get("generated_by"):
        gb = html.escape(str(disclosure.get("generated_by")))
        md = html.escape(str(disclosure.get("model", "?")))
        dt = html.escape(str(disclosure.get("date") or "?"))
        disc_line = f"AI-generated profile (generated_by={gb}, model={md}, date={dt})."
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WorldOS — {title}</title>
  <meta name="worldos:game-id" content="{game_id_attr}" />
  <meta name="worldos:scene-kind" content="{scene_kind_esc}" />
  <!-- AI build-loop output (#451). {disc_line} -->
  <!-- Phaser 3 (MIT) VENDORED, no runtime CDN. -->
  <script src="{vendor_base}/phaser-3.80.1.min.js"></script>
  <style>
    body {{ margin: 0; background: #07090b; color: #aeb8c2; font-family: -apple-system, system-ui, sans-serif; }}
    #wrap {{ max-width: 1040px; margin: 1em auto; padding: 0 1em; }}
    #game {{ box-shadow: 0 0 0 1px rgba(90,110,140,.4); width: max-content; }}
    .meta {{ color: #6a7682; font-size: .82em; line-height: 1.5; }}
  </style>
</head>
<body>
  <div id="wrap">
    <h1 style="font-size:1.05em">{title}</h1>
    <p class="meta">
      AI-built WorldOS game ({scene_kind_esc}). Thin client over the engine surfaces; the engine stays
      sole writer. {disc_line}
      Add <code>?campaign=&lt;id&gt;</code> (optional <code>?base=…</code>) for a live game.
    </p>
    <div id="game"></div>
  </div>
  <script>
    // STEP 3 glue: inject the generated profile (no extra fetch) before the renderer boots.
    window.WORLDOS_PROFILE = {embedded};
  </script>
  <script src="{rb}/surface-client.js"></script>
  <script src="{rb}/{renderer}"></script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Emit Phaser thin-client glue for a generated game.")
    ap.add_argument("profile", help="path to a generated render-profile JSON")
    ap.add_argument("--out", default="", help="write HTML here (default: stdout)")
    ap.add_argument("--render-base", default="/openworlds/render",
                    help="served base path for the vendored Phaser + generic renderers")
    args = ap.parse_args(argv)

    profile = json.loads(Path(args.profile).read_text())
    html = emit_glue(profile, render_base=args.render_base)
    if args.out:
        Path(args.out).write_text(html)
        print(f"wrote {args.out}")
    else:
        print(html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
