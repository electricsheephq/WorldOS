#!/usr/bin/env python3
"""drive_gfx_combat.py — drive a demo combat ROUND on camp_gfxdemo01 + capture each turn (gfx P2 step 7).

POST /move is the ONLY turn-advance lane (there is no GET-poll turn tick); this Mac-side driver
posts player actions + re-renders the box frame after each, so a move/attack visibly updates the
3D-on-2D combat frame. Demonstrates the full action-feedback loop: a move routes around the painted
wall (engine lastPath), an attack fires the impact VFX + floating damage number over the struck foe.

Pre-reqs: the seeded campaign (qa/seed_gfx_combat.py), the viewer running with WORLDOS_PLAYER_MOVES
set (so /move is live), and the GEX44 box reachable via the ControlMaster + the 8765->viewer tunnel.

  python3 qa/drive_gfx_combat.py [viewer_url] [campaign]
"""
import json
import subprocess
import sys
import urllib.request

VIEWER = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8770"
CID = sys.argv[2] if len(sys.argv) > 2 else "camp_gfxdemo01"
CM = "/tmp/gex44-cm.sock"
BOX = "root@46.4.26.123"
RENDER_SCRIPT = "/Users/lume/WorldOS/extensions/renderers/unity/scripts/paint_combat_v1.cs"
BOX_CAP = "/home/unity/worldos-unity/Captures-Durable/m1_combat_v1.png"
OUT_DIR = "/Users/lume/worldos-session-notes/renders"


def surface() -> dict:
    with urllib.request.urlopen(f"{VIEWER}/combat-surface?campaign={CID}", timeout=8) as r:
        return json.load(r)


def post_move(move: dict) -> dict:
    req = urllib.request.Request(f"{VIEWER}/move", data=json.dumps(move).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.load(r)


def render(tag: str) -> None:
    subprocess.run(["/Users/lume/.local/bin/unity-mcp", "code", "execute", "--no-safety-checks",
                    "-f", RENDER_SCRIPT], capture_output=True, timeout=160)
    subprocess.run(["scp", "-o", f"ControlPath={CM}", f"{BOX}:{BOX_CAP}",
                    f"{OUT_DIR}/m1_combat_{tag}.png"], capture_output=True, timeout=30)
    print(f"  rendered -> {OUT_DIR}/m1_combat_{tag}.png")


def main() -> None:
    s = surface()
    hero = next(t for t in s["tokens"] if t["team"] == "ally")
    gob = next(t for t in s["tokens"] if t["team"] == "foe")
    print(f"START hero {hero['name']}@({hero['x']},{hero['y']}) goblin {gob['name']}@({gob['x']},{gob['y']}) turn={s['turnToken']}")
    render("00_start")

    # 1) move the hero adjacent to the goblin (one cell to its left) — engine routes around obstacles.
    tx, ty = gob["x"] - 1, gob["y"]
    r = post_move({"kind": "move_to_cell", "x": tx, "y": ty, "turn_token": s["turnToken"], "campaign": CID})
    print(f"MOVE -> ({tx},{ty}): ok={r.get('ok')} reason={r.get('reason')} path={(r.get('combat') or {}).get('lastPath')}")
    render("01_moved")

    # 2) attack the goblin (turn stays open after a bare move; re-read the live token).
    s2 = surface()
    r2 = post_move({"kind": "attack", "target_id": gob["id"], "turn_token": s2["turnToken"], "campaign": CID})
    bl = (r2.get("combat") or {}).get("battleLog") or []
    tail = [(e.get("text") if isinstance(e, dict) else e) for e in bl[-3:]]
    print(f"ATTACK -> {gob['name']}: ok={r2.get('ok')} reason={r2.get('reason')} log={tail}")
    render("02_attack")


if __name__ == "__main__":
    main()
