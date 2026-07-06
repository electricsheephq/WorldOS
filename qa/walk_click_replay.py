#!/usr/bin/env python3
"""walk_click_replay.py — the W2-UI (#1350) lane EVAL: drive rest-mode click-to-move + the
door-cell walk-through through the REAL viewer, exactly as the browser does, and assert the
whole UI ↔ /move ↔ engine loop is honest.

Deferred from #1341 by the architect ruling (the ENGINE half shipped there; this exercises the
UI half). It is the LIGHT replay path from qa/UI_PLAYTEST.md: NO DM / NO LLM / NO Playwright —
`walk_to_cell` is engine-resolved IN-PROCESS by the viewer (viewer/server.py:_resolve_walk_to),
so a plain HTTP client hitting the viewer's real `/move` sink + `/combat-surface` + `/events`
reads exercises the identical click path the browser fires (screen-combat.jsx postRestWalk /
onRestDoorWalk). We boot the viewer as its own process against a seeded 2-room rest campaign and
POST the same payloads the JSX posts.

ASSERTS (the acceptance bundle for #1350):
  A1  PATH LEGALITY — a walk POST returns ok:True with an engine-CONFIRMED path (a contiguous
      cell list from the start cell to the target); an off-grid / into-a-wall walk is REJECTED
      (ok:False) — the viewer never invents a route (VISION: renderer = pure consumer).
  A2  REST_WALK BEAT — the walk lands a `rest_walk` beat in /events, projected as the
      Action-Replay envelope verb "walk" / anim_hint "glide" carrying the engine path under
      result.path (the #1303 renderer's glide input).
  A3  RENDERED CELL == ENGINE stage_cell — after the walk the /combat-surface rest token for the
      mover renders AT the cell the engine wrote (stage.tokens[mover].x/y == target), so a
      surface reload glides the token to the confirmed destination (no client prediction).
  A4  DOOR WALK-THROUGH — clicking a door cell (walk_to_cell onto the doorway, then cross_door)
      ends in the LINKED room: /combat-surface reports the new current location + its own grid.

Engine = SOLE WRITER: this script only SEEDS (via server.* under the engine lock) and then drives
the viewer over HTTP; it never writes campaign state directly during the replay.

  uv run --directory servers/engine python "$PWD/qa/walk_click_replay.py"
  (or plain `python3 qa/walk_click_replay.py` if the engine deps are importable)

Exit 0 = all asserts green; non-zero = the first failed assert (printed).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "servers" / "engine"
VIEWER = ROOT / "viewer" / "server.py"
CID = "camp_w2walkreplay01"
GRID_W, GRID_H = 8, 6
DOOR = (4, 0)  # back-wall-center doorway, shared link between the two rest units


# ── seed: a 2-room REST campaign (no combat) with scene grids + a shared doorway ───────────────


def _author_room(loc_id: str, scene_id: str):
    """A GRID_W×GRID_H rest scene: perimeter walls with a walkable DOOR gap at (4,0), one blocking
    prop, floor elsewhere. Mirrors qa/seed_gfx_crypt_2room._author_room, trimmed for a rest replay."""
    from scene_grid import (  # noqa: PLC0415
        SceneGrid, SceneGridSpec, SceneCell, SceneCellDefault, SceneProp,
    )

    cells: list = []
    for c in range(GRID_W):
        if (c, 0) == DOOR:
            cells.append(SceneCell(c=c, r=0, type="door", walkable=True))  # the doorway opening
        else:
            cells.append(SceneCell(c=c, r=0, type="wall", walkable=False))
        cells.append(SceneCell(c=c, r=GRID_H - 1, type="wall", walkable=False))
    for r in range(1, GRID_H - 1):
        cells.append(SceneCell(c=0, r=r, type="wall", walkable=False))
        cells.append(SceneCell(c=GRID_W - 1, r=r, type="wall", walkable=False))
    # one interior blocking prop the walk must route around.
    prop = SceneProp(id="urn", kind="urn", cells=[(3, 3)], anchor_cell=(3, 3))
    cells.append(SceneCell(c=3, r=3, type="prop", walkable=False, prop_ref="urn"))
    return SceneGrid(
        scene_id=scene_id, location_id=loc_id, kind="dungeon",
        grid=SceneGridSpec(cols=GRID_W, rows=GRID_H, cell_size_ft=5),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=[prop],
        door_cells=[DOOR],
    )


def seed(state_dir: str) -> dict:
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="W2 Walk-Replay",
                                  summary="Two linked rest rooms for the click-to-move replay."))
    room_a = server.add_location(campaign_id=CID, name="Antechamber", make_current=True,
                                 description="A small stone antechamber; a doorway leads north.")
    room_b = server.add_location(campaign_id=CID, name="Inner Hall",
                                 description="The inner hall beyond the doorway.",
                                 connections=[room_a["id"]])
    c = server._require(CID)
    c.locations[room_a["id"]].scene_grid = _author_room(room_a["id"], f"{CID}:a")
    c.locations[room_b["id"]].scene_grid = _author_room(room_b["id"], f"{CID}:b")
    server.save_campaign(c)
    server.start_session(CID, title="W2 Walk-Replay")

    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player", race="human", class_name="fighter", level=3,
        apply_srd_defaults=True, add_to_party=True,
    )
    # place the hero on a known start cell (no combat — a pure rest scene).
    c = server._require(CID)
    c.characters[hero["id"]].stage_cell = (1, 4)
    server.save_campaign(c)
    return {"room_a": room_a["id"], "room_b": room_b["id"], "hero_id": hero["id"]}


# ── HTTP helpers against the REAL viewer ───────────────────────────────────────────────────────


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_move(base: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{base}/move", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def _wait_ready(base: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            _get(base, f"/combat-surface?campaign={CID}")
            return
        except (urllib.error.URLError, ConnectionError, OSError) as exc:  # noqa: PERF203
            last = str(exc)
            time.sleep(0.25)
    raise RuntimeError(f"viewer did not come up within {timeout}s: {last}")


def _rest_token(surface: dict, cid: str) -> dict | None:
    for t in ((surface.get("stage") or {}).get("tokens") or []):
        if t.get("id") == cid:
            return t
    return None


# ── the assertions ─────────────────────────────────────────────────────────────────────────────


class ReplayFail(AssertionError):
    pass


def _check(cond: bool, label: str, detail: str = "") -> None:
    if not cond:
        raise ReplayFail(f"{label}{(': ' + detail) if detail else ''}")
    print(f"  PASS  {label}")


def run(base: str, ids: dict) -> None:
    hero = ids["hero_id"]
    room_b = ids["room_b"]

    # sanity: the rest surface renders the hero at his seeded stage_cell before any walk.
    surf0 = _get(base, f"/combat-surface?campaign={CID}")
    tok0 = _rest_token(surf0, hero)
    _check(tok0 is not None and (tok0["x"], tok0["y"]) == (1, 4),
           "rest surface seats the hero at his stage_cell",
           detail=f"got {tok0 and (tok0.get('x'), tok0.get('y'))}")

    # A1 (legal): walk to an open cell across the room — engine confirms a contiguous path.
    target = (6, 4)
    ev_before = len(_get(base, f"/events?campaign={CID}&since=0").get("entries", []))
    out = _post_move(base, {"kind": "walk_to_cell", "character_id": hero, "x": target[0], "y": target[1]})
    _check(out.get("ok") is True and out.get("walked") is True,
           "A1 legal walk is accepted (ok:True)", detail=json.dumps(out))
    path = out.get("path") or []
    _check(len(path) >= 2 and tuple(path[0]) == (1, 4) and tuple(path[-1]) == target,
           "A1 engine-confirmed path runs start→target",
           detail=f"path={path}")
    # contiguity: each step is a single grid move (chebyshev-1) — the engine routed it, not the client.
    steps_ok = all(
        max(abs(path[i + 1][0] - path[i][0]), abs(path[i + 1][1] - path[i][1])) == 1
        for i in range(len(path) - 1)
    )
    _check(steps_ok, "A1 path is contiguous (engine-routed, no teleport)", detail=f"path={path}")

    # A1 (illegal): a walk into a wall / off-grid is REJECTED — the viewer never invents a route.
    bad = _post_move(base, {"kind": "walk_to_cell", "character_id": hero, "x": 0, "y": 0})
    _check(bad.get("ok") is False, "A1 into-a-wall walk is rejected (ok:False)", detail=json.dumps(bad))
    off = _post_move(base, {"kind": "walk_to_cell", "character_id": hero, "x": 99, "y": 99})
    _check(off.get("ok") is False, "A1 off-grid walk is rejected (ok:False)", detail=json.dumps(off))

    # A2: the legal walk landed a rest_walk beat → envelope verb "walk" / anim_hint "glide" / path.
    evs = _get(base, f"/events?campaign={CID}&since=0").get("entries", [])
    _check(len(evs) > ev_before, "A2 a new event beat landed", detail=f"{ev_before}→{len(evs)}")
    walk_beats = [e for e in evs if e.get("verb") == "walk"]
    _check(bool(walk_beats), "A2 a rest_walk beat is projected as verb 'walk'")
    beat = walk_beats[-1]
    _check(beat.get("anim_hint") == "glide", "A2 the walk beat carries anim_hint 'glide'",
           detail=str(beat.get("anim_hint")))
    beat_path = (beat.get("result") or {}).get("path") or []
    _check(len(beat_path) >= 2 and tuple(beat_path[-1]) == target,
           "A2 the beat carries the engine path under result.path", detail=f"{beat_path}")

    # A3: the rendered rest token now sits AT the engine stage_cell (no client-side drift).
    surf1 = _get(base, f"/combat-surface?campaign={CID}")
    tok1 = _rest_token(surf1, hero)
    _check(tok1 is not None and (tok1["x"], tok1["y"]) == target,
           "A3 rendered rest token == engine stage_cell after the walk",
           detail=f"got {tok1 and (tok1.get('x'), tok1.get('y'))}, want {target}")

    # A4: DOOR WALK-THROUGH — walk onto the doorway, then cross into the linked room.
    door_walk = _post_move(base, {"kind": "walk_to_cell", "character_id": hero, "x": DOOR[0], "y": DOOR[1]})
    _check(door_walk.get("ok") is True, "A4 walk onto the doorway cell is accepted",
           detail=json.dumps(door_walk))
    crossed = _post_move(base, {"kind": "cross_door", "x": DOOR[0], "y": DOOR[1]})
    _check(crossed.get("ok") is True, "A4 cross_door from the doorway is accepted",
           detail=json.dumps(crossed))
    surf2 = _get(base, f"/combat-surface?campaign={CID}")
    _check((surf2.get("location") or {}).get("id") == room_b,
           "A4 the party arrives in the LINKED room after the door walk-through",
           detail=f"now at {(surf2.get('location') or {}).get('id')}, want {room_b}")
    _check(bool((surf2.get("grid") or {}).get("cols")),
           "A4 the linked room renders its own scene grid")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="w2-walk-replay-") as td:
        state_dir = str(Path(td) / "state")
        moves = str(Path(td) / "moves.ndjson")
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        ids = seed(state_dir)

        env = dict(os.environ)
        env["WORLDOS_STATE_DIR"] = state_dir
        env["WORLDOS_PLAYER_MOVES"] = moves  # enables the /move write path (the live-game gate)
        # Boot with output CAPTURED (not DEVNULL — a silent import/bind/seed failure would
        # otherwise hide behind a generic _wait_ready timeout) and ONE fresh-port retry:
        # _free_port() is inherently TOCTOU (close->launch window), so a lost bind race gets a
        # second freshly-probed port instead of a 20s stall.
        boot_log = Path(td) / "viewer-boot.log"
        proc = None
        base = ""
        for attempt in (1, 2):
            port = _free_port()
            base = f"http://127.0.0.1:{port}"
            with open(boot_log, "wb") as lf:
                proc = subprocess.Popen(
                    [sys.executable, str(VIEWER), CID, str(port)],
                    env=env, stdout=lf, stderr=subprocess.STDOUT,
                )
            try:
                _wait_ready(base)
                break
            except ReplayFail:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                tail = boot_log.read_bytes()[-2000:].decode(errors="replace")
                if attempt == 2:
                    print(f"\nviewer never came up; boot log tail:\n{tail}", file=sys.stderr)
                    return 1
                print(f"boot attempt {attempt} failed (bind race or slow boot) — retrying on a "
                      f"fresh port; log tail:\n{tail}", file=sys.stderr)
        try:
            print(f"viewer up on {base} (campaign {CID})")
            run(base, ids)
        except ReplayFail as exc:
            print(f"\nREPLAY FAILED: {exc}", file=sys.stderr)
            return 1
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    print("\nwalk_click_replay: ALL ASSERTS GREEN ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
