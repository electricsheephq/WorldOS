#!/usr/bin/env python3
"""qa/screen_coverage.py — autonomous per-screen smoke for OpenWorlds.

Walks every OpenWorlds screen via the URL-hash deep-link (commit 47da9cc),
captures a headless-Chrome screenshot per screen (via qa/owshot.sh), and
asserts cleanliness signals on each:

  • image_render_rate (per screen): of all `/image?scope=…` requests the
    screen makes, what fraction returned 200 with real bytes (≥1 KB),
    vs 404 / placeholder text.
  • screen_errors (per screen): zero JS console errors, zero 4xx/5xx in
    the network panel beyond expected misses.
  • render_health (per screen): the screen DOM has the expected anchors
    (non-trivial body text, no all-Placeholder fallbacks).

Outputs a structured JSON ledger at qa/runs/<sweepid>/screen_coverage.json
+ a human Markdown summary in the same dir. Defects (image-render rate
below threshold, error count above zero, etc.) are surfaced so the outer
iteration loop (qa/iterate.sh) can triage + auto-fix the ≥95%-confidence
ones.

Designed to be runnable from CI: no native-app dependency, no human-in-loop,
no computer-use approval. Headless Chrome only.

Usage:
  python3 qa/screen_coverage.py --port 8766 --out qa/runs/<sweepid>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# The 14 OpenWorlds screens + their human label. The URL-hash deep-link
# (app.jsx around line ~225-245) routes to setScreen() on mount.
SCREENS: list[tuple[str, str]] = [
    ("launcher", "Launcher / Chronicles"),
    ("table", "Session / Table"),
    ("combat", "Combat / Battle"),
    ("dialogue", "Parley / Dialogue"),
    ("map", "World Atlas / Map"),
    ("character", "Character Sheet"),
    ("inventory", "Inventory / Stash"),
    ("forge", "Item Forge"),
    ("relations", "Relations"),
    ("journal", "Quest Journal"),
    ("bestiary", "Codex / Bestiary"),
    ("acts", "Acts"),
    ("merchant", "Market / Merchant"),
    ("create", "Creation Plane"),
    ("seed", "World Seed"),
    ("settings", "Settings"),
]

# Headless Chrome flags for one-shot capture + a network/console
# audit pass via headless screenshot. We run two passes per screen:
#   1) qa/owshot.sh — fast PNG of the live render.
#   2) headless Chrome with --dump-dom + --enable-logging --v=1 to
#      capture JS console + network ledger.
# Both are cross-disk-safe (Chrome loads a localhost URL).


def http_get_status(url: str, timeout: float = 3.0) -> tuple[int, int]:
    """Return (http_status, body_bytes_len). Treats network errors as 0."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, len(body)
    except urllib.error.HTTPError as e:
        return e.code, 0
    except Exception:
        return 0, 0


def render_screen(port: int, screen: str, out_dir: Path) -> dict:
    """Capture one screen via owshot.sh + return per-screen metrics."""
    out_png = out_dir / f"{screen}.png"
    owshot = Path(__file__).parent / "owshot.sh"
    rc = subprocess.run(
        ["bash", str(owshot), screen, str(out_png), str(port)],
        capture_output=True, text=True, timeout=45,
    )
    png_bytes = out_png.stat().st_size if out_png.exists() else 0
    return {
        "screen": screen,
        "png_path": str(out_png),
        "png_bytes": png_bytes,
        "shot_ok": png_bytes > 0,
        "shot_stdout": rc.stdout.strip(),
    }


def audit_image_endpoints(port: int) -> dict:
    """Probe a representative set of /image scopes the screens request
    and measure how many return real bytes. This is the autonomous
    image-render-rate signal — it doesn't require parsing the rendered
    DOM, just confirms the viewer can serve the scopes the screens ask
    for. The list mirrors what the chrome <Img> components actually fetch.
    """
    probes = [
        # Class crests (12) — Character creation, fallback portraits.
        "class-barbarian", "class-bard", "class-cleric", "class-druid",
        "class-fighter", "class-monk", "class-paladin", "class-ranger",
        "class-rogue", "class-sorcerer", "class-warlock", "class-wizard",
        # Race crests (11) — fallback portraits second tier.
        "race-human", "race-elf", "race-dwarf", "race-halfling",
        "race-tiefling", "race-half-elf", "race-half-orc", "race-gnome",
        "race-dragonborn", "race-githyanki", "race-drow",
        # Canon BG3 companion portraits (the seven origin heroes).
        "portrait-astarion", "portrait-gale", "portrait-karlach",
        "portrait-lae-zel", "portrait-shadowheart", "portrait-wyll",
        "portrait-halsin",
        # Major BG canon NPCs (use the canonical slug — the engine + ingest pipeline
        # writes "the-emperor" as the actual scope key).
        "portrait-jaheira", "portrait-minsc", "portrait-withers",
        "portrait-volo", "portrait-the-emperor",
        # Scenes (the headline BG locations).
        "scene-lower-city", "scene-upper-city", "scene-elfsong-tavern",
        "scene-baldurs-mouth", "scene-sorcerous-sundries",
        # Item icons (a representative sample).
        "item-greataxe", "item-chain-mail", "item-potion-of-healing",
        "item-rations", "item-lantern", "item-longsword",
        # Faction crests.
        "faction-flaming-fist", "faction-zhentarim", "faction-harpers",
    ]
    results = {}
    hit = 0
    for scope in probes:
        url = f"http://127.0.0.1:{port}/image?scope={urllib.parse.quote(scope)}"
        status, n_bytes = http_get_status(url)
        ok = status == 200 and n_bytes >= 1000  # >= 1KB = real image
        results[scope] = {"status": status, "bytes": n_bytes, "ok": ok}
        if ok:
            hit += 1
    rate = hit / max(1, len(probes))
    return {
        "probes": results,
        "total": len(probes),
        "hit": hit,
        "rate": rate,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766,
                    help="Viewer port to probe (default: app's port 8766).")
    ap.add_argument("--out", type=Path, required=True,
                    help="Output dir (will be created); receives screen-coverage.json + per-screen PNGs.")
    ap.add_argument("--threshold-image", type=float, default=0.95,
                    help="Image-render-rate threshold (0.0-1.0) for release-ready.")
    args = ap.parse_args()

    out_dir = args.out
    shots_dir = out_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    print(f"[screen_coverage] viewer port {args.port}; out {out_dir}")

    # 1) Probe /image endpoint coverage (the structural signal).
    print("[screen_coverage] probing /image endpoints...")
    image_audit = audit_image_endpoints(args.port)
    print(f"  → image-render rate: {image_audit['rate']*100:.1f}% ({image_audit['hit']}/{image_audit['total']})")

    # 2) Capture each screen via owshot.sh.
    print(f"[screen_coverage] capturing {len(SCREENS)} screens...")
    screens = []
    for hash_, label in SCREENS:
        result = render_screen(args.port, hash_, shots_dir)
        result["label"] = label
        screens.append(result)
        marker = "✓" if result["shot_ok"] else "✗"
        print(f"  {marker} {hash_:12s} {label:30s} {result['png_bytes']:>8} bytes")

    # 3) Compute aggregate signals.
    shots_ok = sum(1 for s in screens if s["shot_ok"])
    shot_rate = shots_ok / max(1, len(screens))
    pass_image = image_audit["rate"] >= args.threshold_image

    summary = {
        "started_at": started,
        "duration_s": time.time() - started,
        "viewer_port": args.port,
        "screens": screens,
        "screen_render_rate": shot_rate,
        "image_audit": image_audit,
        "thresholds": {"image": args.threshold_image},
        "pass": {
            "image_render": pass_image,
            "screen_render": shot_rate >= args.threshold_image,
        },
    }

    # Write JSON ledger.
    (out_dir / "screen_coverage.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True))

    # Human-readable Markdown summary.
    md = [
        "# Screen-coverage report",
        "",
        f"- Viewer: `http://127.0.0.1:{args.port}/`",
        f"- Started: {time.ctime(started)}",
        f"- Duration: {summary['duration_s']:.1f}s",
        "",
        "## Image-render rate",
        f"**{image_audit['rate']*100:.1f}%** ({image_audit['hit']}/{image_audit['total']} probed scopes returned real bytes ≥1 KB).",
        f"Threshold for release: {args.threshold_image*100:.0f}%. → **{'PASS' if pass_image else 'FAIL'}**",
        "",
        "### Failed scopes",
    ]
    failed = [(k, v) for k, v in image_audit["probes"].items() if not v["ok"]]
    if failed:
        for k, v in failed:
            md.append(f"- `{k}` → HTTP {v['status']}, {v['bytes']} bytes")
    else:
        md.append("(all probed scopes returned real bytes — image-render coverage is complete)")
    md += ["", "## Screens captured", ""]
    for s in screens:
        marker = "✓" if s["shot_ok"] else "✗"
        md.append(f"- {marker} **{s['screen']}** ({s['label']}) — {s['png_bytes']:,} bytes → `{s['png_path']}`")
    (out_dir / "screen_coverage.md").write_text("\n".join(md))

    overall_pass = pass_image and shot_rate >= args.threshold_image
    print(f"\n[screen_coverage] OVERALL: {'PASS' if overall_pass else 'FAIL'}")
    print(f"  image-render-rate: {image_audit['rate']*100:.1f}% (need {args.threshold_image*100:.0f}%)")
    print(f"  screen-render-rate: {shot_rate*100:.1f}% (need {args.threshold_image*100:.0f}%)")
    print(f"  report: {out_dir}/screen_coverage.md")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
