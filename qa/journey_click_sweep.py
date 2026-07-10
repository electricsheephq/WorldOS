#!/usr/bin/env python3
"""journey_click_sweep.py — journey_eval v2 (#1523): the ADVERSARIAL prop-click sweep the legal-path
run structurally cannot do (see qa/evidence/journey-eval-first-run/RECALL.md — the honest 0/4 recall
table: journey_eval v1 only ever walks cells a manifest/plan already picked for OTHER reasons, so it
never deliberately visits a candidate painted-solid cell to test whether the engine wrongly accepts it).

A sibling to qa/journey_eval.py (kept separate: a different phase shape — deterministic engine-state
assertions, not VQA — would only clutter journey_eval's FrameScorer/ImageDiffer contract). In every
visited room it enumerates adversarial click targets from the ROOM'S OWN LIVE DATA (its scene_grid,
read fresh off the booted engine's /combat-surface — never a possibly-stale static snapshot):

  * every PROP FOOTPRINT cell          -> expect REJECTED (a walk onto it must fail)
  * one Chebyshev-1 RING cell per prop -> expect ACCEPTED, landing adjacent, never ON the footprint
  * every DOOR cell                    -> expect a cross to the door's OWN mapped connection
                                           (door_cells[i] -> connections[i], PR #1532's positional fix)
  * 3-5 RANDOM walkable cells          -> expect ACCEPTED (a sanity floor: the sweep isn't all-reject)
  * the room's ENTRY/spawn cell        -> expect NOT ON any footprint (the woodpile-spawn class — PR
                                          #1532's own orchestrator note: "frame 2 hero spawn cell reads
                                          as standing on the woodpile")

DESIGN DEVIATION FROM THE ISSUE TEXT (flagged, not hidden): #1523 asks to drive targets through the
#1466 QA `/click` channel (the Unity player's localhost HttpListener + journey_capture.js's
qaClickCell). Live-checked before writing this: the OWNER'S OWN WorldOSPlayer.app was already running
against the live viewer on port 8766 (`ps aux` showed PID 81564 + the viewer at PID 99659/99657). A
second native player instance cannot be safely driven alongside it — macOS window lookup by owner name
(`CGWindowList` / native_palette_core.js's `findWindow(helperCmd, "WorldOSPlayer")`) cannot disambiguate
two windows sharing that name, so a scratch capture risks grabbing the OWNER'S live window instead of
this run's. Every click a real player makes ultimately POSTs one of exactly the three /move intents this
driver posts directly (CombatSurfaceClient.HandleCell -> PostWalk/PostCrossDoor ->
`walk_to_cell`/`cross_door`, viewer/server.py:_resolve_walk_to / _resolve_cross_door) — the client-side
impassable pre-check is a UX-only FlashReject shortcut ("the engine stays authoritative and
independently rejects illegal moves" per the CombatSurfaceClient.cs comment above HandleCell). So this
driver hits `{base_url}/move` directly (qa/walk_click_replay.py's proven pattern) against a SCRATCH
viewer instance on a non-8766 port: it exercises the IDENTICAL engine-validation surface the
missing-footprint bug class lives in, with zero risk to the owner's live session, and sweeps ~100+
targets across 3 rooms in seconds instead of minutes of screenshot settles.

Two phases, pure core + injectable IO (mirrors journey_eval.py's FrameScorer/ImageDiffer split):
  1. manifest_from_surface() + build_adversarial_targets() — PURE, unit-tested: derive every target
     from a manifest dict ({grid, props, walkable, doors}) built fresh off a live /combat-surface JSON.
  2. dfs_sweep(...) — the per-room + door-graph traversal; callables injected (get_surface /
     run_room_checks / cross_door) so the traversal logic is unit-tested with a stub 3-room graph, no
     engine/HTTP involved.

CLI `run` is the only impure entry point: seeds qa/seed_gfx_walkslice.py into a state dir, boots a
SCRATCH viewer/server.py on a non-8766 port, drives the sweep over every room, writes a PASS/FAIL table
+ findings.json (RECALL.md-comparable shape), then kills the ONE viewer subprocess it started, by PID.
Engine = SOLE WRITER: this only POSTs the same rest-mode /move intents a real click does; it never
writes campaign state directly.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_QA_DIR = Path(__file__).resolve().parent
_ROOT = _QA_DIR.parent
_SEED_SCRIPT = _QA_DIR / "seed_gfx_walkslice.py"
_VIEWER = _ROOT / "viewer" / "server.py"

# Chebyshev-1 ring: the 8 neighbours of a cell (orthogonal + diagonal).
_RING_OFFSETS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))

OWNER_LIVE_PORT = 8766  # never touch — see the module docstring's deviation note.


# ── pure: live surface -> the {grid, props, walkable, doors} manifest contract ─────────────────────
def manifest_from_surface(surface: dict) -> dict:
    """{grid, props, walkable, doors} straight off a live /combat-surface JSON — the room's OWN data,
    zero drift risk vs a static qa/room_manifests/*.cells.json snapshot. `grid.cells` carries every
    explicitly-authored SceneCell; an omitted cell falls back to `cellDefault` (walkable=True unless
    stated otherwise) — mirrors tools/derive_room_manifest.py's derive_walkable exactly, just sourced
    from the LIVE scene_grid instead of an offline geometry.json. `doors` is the surface's own
    `_combat_doors` projection (cell/to/toName), which already mirrors engine.cross_door's positional
    door_cells[i]->connections[i] resolution (PR #1532) — reused verbatim, never re-derived."""
    grid = surface.get("grid") or {}
    cols, rows = int(grid.get("cols", 0)), int(grid.get("rows", 0))
    cells = grid.get("cells") or []
    default_walkable = bool((grid.get("cellDefault") or {}).get("walkable", True))
    blocked: set = set()
    explicit_walkable: set = set()
    footprints: dict = {}
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        c, r = cell.get("c"), cell.get("r")
        if not (isinstance(c, int) and isinstance(r, int)):
            continue
        if cell.get("walkable") is False:
            blocked.add((c, r))
        elif cell.get("walkable") is True:
            explicit_walkable.add((c, r))
        pref = cell.get("prop_ref")
        if pref:
            footprints.setdefault(str(pref), []).append([c, r])
    props = [{"id": pid, "footprint": sorted(fp)} for pid, fp in sorted(footprints.items())]
    if default_walkable:
        walkable = [[c, r] for r in range(rows) for c in range(cols) if (c, r) not in blocked]
    else:
        # No current seed uses cell_default_walkable=False, but handled rather than silently
        # emitting an empty room if one ever does.
        walkable = sorted([list(c) for c in explicit_walkable])
    doors = [{"cell": [int(d["cell"][0]), int(d["cell"][1])], "to": d.get("to"), "toName": d.get("toName")}
             for d in (surface.get("doors") or []) if d.get("cell")]
    return {"grid": {"cols": cols, "rows": rows}, "props": props, "walkable": walkable, "doors": doors}


# ── pure: the adversarial target plan ────────────────────────────────────────────────────────────────
@dataclass
class AdversarialTarget:
    id: str
    kind: str            # footprint_reject | ring_accept | door_cross | random_accept
    cell: tuple
    expect: str           # reject | accept_adjacent | accept | door_cross
    prop_id: str = ""
    forbidden_cells: tuple = ()   # ring_accept: the prop's own footprint — landing must avoid these
    expected_room: str = ""       # door_cross: the door's mapped destination location id
    note: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "cell": list(self.cell), "expect": self.expect,
                "prop_id": self.prop_id, "forbidden_cells": [list(c) for c in self.forbidden_cells],
                "expected_room": self.expected_room, "note": self.note}


@dataclass
class AdversarialSweepPlan:
    targets: list
    unreachable: list = field(default_factory=list)   # props with no walkable Chebyshev-1 ring cell

    def as_dict(self) -> dict:
        return {"targets": [t.as_dict() for t in self.targets], "unreachable": self.unreachable}


def build_adversarial_targets(manifest: dict, *, random_count: int = 4,
                              rng_seed: int = 1523, include_doors: bool = True) -> AdversarialSweepPlan:
    """Derive the FULL adversarial target set from a room manifest — PURE, deterministic (a fixed
    rng_seed + sorted candidate pools), unit-tested with no engine/HTTP involved. Mirrors journey_eval's
    build_script: never silently drops a prop — one with no walkable ring cell is RECORDED unreachable."""
    grid = manifest.get("grid", {})
    cols, rows = int(grid.get("cols", 0)), int(grid.get("rows", 0))
    props = manifest.get("props", [])
    walkable = {(int(c), int(r)) for (c, r) in manifest.get("walkable", [])}
    all_footprint = {(int(c), int(r)) for p in props for (c, r) in p.get("footprint", [])}

    targets: list = []
    unreachable: list = []

    for p in props:
        pid = str(p.get("id", "prop"))
        fp = sorted((int(c), int(r)) for (c, r) in p.get("footprint", []))
        for (c, r) in fp:
            targets.append(AdversarialTarget(
                id=f"footprint_{pid}_{c}_{r}", kind="footprint_reject", cell=(c, r), expect="reject",
                prop_id=pid, note=f"prop {pid} footprint cell — must be blocked"))
        ring_candidates = sorted({(c + dc, r + dr) for (c, r) in fp for (dc, dr) in _RING_OFFSETS})
        ring_cell = next(
            (cell for cell in ring_candidates
             if 0 <= cell[0] < cols and 0 <= cell[1] < rows
             and cell in walkable and cell not in all_footprint),
            None,
        )
        if ring_cell is None:
            unreachable.append({"id": pid, "cells": [list(c) for c in fp],
                               "reason": "no walkable Chebyshev-1 ring cell"})
            continue
        targets.append(AdversarialTarget(
            id=f"ring_{pid}", kind="ring_accept", cell=ring_cell, expect="accept_adjacent",
            prop_id=pid, forbidden_cells=tuple(fp),
            note=f"Chebyshev-1 ring cell of {pid} — must land adjacent, never on the footprint"))

    door_cells = set()
    if include_doors:
        for d in manifest.get("doors", []):
            cell = d.get("cell")
            if not cell:
                continue
            cc, cr = int(cell[0]), int(cell[1])
            door_cells.add((cc, cr))
            targets.append(AdversarialTarget(
                id=f"door_{cc}_{cr}", kind="door_cross", cell=(cc, cr), expect="door_cross",
                expected_room=str(d.get("to") or ""),
                note=f"door cell -> must cross to {d.get('toName') or d.get('to')}"))

    already = all_footprint | door_cells | {t.cell for t in targets if t.kind == "ring_accept"}
    pool = sorted(walkable - already)
    rng = random.Random(rng_seed)
    rng.shuffle(pool)
    for cell in pool[:max(0, random_count)]:
        targets.append(AdversarialTarget(
            id=f"random_{cell[0]}_{cell[1]}", kind="random_accept", cell=cell, expect="accept",
            note="random walkable sanity cell — must be accepted"))

    return AdversarialSweepPlan(targets=targets, unreachable=unreachable)


# ── pure-ish: the per-room + door-graph traversal (callables injected — unit-tested with stubs) ─────
def dfs_sweep(get_surface: Callable[[], dict], run_room_checks: Callable[[str, dict], dict],
             cross_door: Callable[[dict], dict], *, visited: Optional[set] = None) -> list:
    """DFS over the live door graph starting at wherever `get_surface()` currently reports. Every room
    is checked exactly once (`run_room_checks`); every door cell in a room's `doors` list is crossed
    exactly once (`cross_door`) REGARDLESS of whether its destination is already visited — a door is
    itself one of the adversarial targets (#1523: "every door cell ... should cross to the door's own
    mapped connection"), and testing it doubles as the traversal's own backtrack when the destination
    was the room we just came from. INVARIANT (by induction): `dfs_sweep` always leaves the current
    position back at the room it was called on — after an unvisited-destination recursion returns
    (which leaves position==that destination, its own invariant), an explicit fix-up crosses back
    before the next door / before this call returns. Requires reciprocal doors (every current seed's
    door graph is a tree/star — camp and tavern each have exactly one door, straight back to the hub —
    so this always terminates and returns cleanly)."""
    visited = visited if visited is not None else set()
    surface = get_surface()
    loc_id = str((surface.get("location") or {}).get("id") or "")
    if not loc_id or loc_id in visited:
        return []
    visited.add(loc_id)
    room_result = run_room_checks(loc_id, surface)
    results = [room_result]
    for door in surface.get("doors") or []:
        dest = str(door.get("to") or "")
        was_visited = dest in visited
        outcome = cross_door(door)
        room_result.setdefault("door_checks", []).append({
            "cell": door.get("cell"), "expected_room": dest, **outcome,
        })
        if not outcome.get("ok"):
            continue
        if dest and not was_visited:
            results += dfs_sweep(get_surface, run_room_checks, cross_door, visited=visited)
        cur = str((get_surface().get("location") or {}).get("id") or "")
        if cur != loc_id:
            back = next((d for d in (get_surface().get("doors") or []) if d.get("to") == loc_id), None)
            if back is not None:
                cross_door(back)
    return results


def build_findings(room_results: list) -> dict:
    """Aggregate dfs_sweep's per-room results into a flat, RECALL.md-comparable verdict: an overall
    passed bool, EVERY check flattened (room/kind/cell/pass/defect), a per-room PASS/FAIL table, and
    unreachable props surfaced (never silently dropped, mirroring journey_eval.build_verdict)."""
    all_checks: list = []
    unreachable: list = []
    per_room_table: list = []
    for room in room_results:
        room_id = room.get("room", "")
        checks_here: list = []
        for t in room.get("targets", []):
            rec = {"room": room_id, **t}
            all_checks.append(rec)
            checks_here.append(rec)
        if room.get("spawn_check"):
            rec = {"room": room_id, **room["spawn_check"]}
            all_checks.append(rec)
            checks_here.append(rec)
        for d in room.get("door_checks", []):
            rec = {"room": room_id, "kind": "door_cross", **d}
            all_checks.append(rec)
            checks_here.append(rec)
        for u in room.get("unreachable_props", []):
            unreachable.append({"room": room_id, **u})
        n = len(checks_here)
        n_pass = sum(1 for c in checks_here if c.get("pass"))
        per_room_table.append({"room": room_id, "checked": n, "passed": n_pass, "failed": n - n_pass})
    offenders = [c for c in all_checks if not c.get("pass")]
    reasons = [] if all_checks else ["no targets checked — sweep produced nothing to inspect"]
    return {
        "passed": bool(all_checks) and not offenders,
        "targets_checked": len(all_checks),
        "targets_with_defects": len(offenders),
        "defects": offenders,
        "unreachable_props": unreachable,
        "per_room": per_room_table,
        "reasons": reasons,
        "per_target": all_checks,
    }


# ── impure: HTTP against the booted viewer (mirrors qa/walk_click_replay.py's proven pattern) ───────
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


def _rest_token(surface: dict, character_id: str) -> Optional[dict]:
    for t in ((surface.get("stage") or {}).get("tokens") or []):
        if t.get("id") == character_id:
            return t
    return None


def _drive_target(base: str, hero_id: str, t: AdversarialTarget) -> dict:
    tc, tr = t.cell
    out = _post_move(base, {"kind": "walk_to_cell", "character_id": hero_id, "x": tc, "y": tr})
    ok = bool(out.get("ok"))
    to = out.get("to")
    landed = (int(to[0]), int(to[1])) if (ok and isinstance(to, (list, tuple)) and len(to) == 2) else None
    rec = t.as_dict()
    rec.update({"engine_ok": ok, "landed": list(landed) if landed else None, "reason": out.get("reason")})
    if t.expect == "reject":
        rec["pass"] = not ok
        if ok:
            rec["defect"] = (f"engine ACCEPTED a walk onto {t.prop_id} footprint cell {[tc, tr]} — "
                             f"missing-footprint class")
    elif t.expect == "accept_adjacent":
        on_prop = landed is None or landed in {tuple(fc) for fc in t.forbidden_cells}
        rec["pass"] = ok and landed == (tc, tr) and not on_prop
        if not rec["pass"]:
            rec["defect"] = (f"ring cell of {t.prop_id} not accepted/landed correctly "
                             f"(ok={ok}, landed={landed}, target={[tc, tr]})")
    elif t.expect == "accept":
        rec["pass"] = ok and landed == (tc, tr)
        if not rec["pass"]:
            rec["defect"] = f"random walkable cell {[tc, tr]} rejected (reason={out.get('reason')})"
    else:
        rec["pass"] = False
        rec["defect"] = f"unhandled expect {t.expect!r} for a non-door target"
    return rec


def _cross_door(base: str, hero_id: str, door: dict) -> dict:
    """Mirrors CombatSurfaceClient.PostCrossDoor exactly: walk-to-the-door-cell (best-effort — its
    outcome is ignored, matching the Unity coroutine), then POST cross_door unconditionally."""
    cell = door.get("cell") or [0, 0]
    c, r = int(cell[0]), int(cell[1])
    _post_move(base, {"kind": "walk_to_cell", "character_id": hero_id, "x": c, "y": r})
    out = _post_move(base, {"kind": "cross_door", "x": c, "y": r})
    ok = bool(out.get("ok"))
    landed_room = None
    if ok:
        surf = _get(base, "/combat-surface?campaign=" + door.get("_cid", ""))
        landed_room = str((surf.get("location") or {}).get("id") or "")
    expected = str(door.get("to") or "")
    passed = ok and landed_room == expected
    return {"ok": ok, "landed_room": landed_room, "pass": passed, "reason": out.get("reason"),
           "defect": (None if passed else
                     f"door {cell} expected->{expected!r} got->{landed_room!r} ok={ok}")}


def _run_room_checks(base: str, hero_id: str, random_count: int) -> Callable[[str, dict], dict]:
    def _inner(loc_id: str, surface: dict) -> dict:
        manifest = manifest_from_surface(surface)
        plan = build_adversarial_targets(manifest, random_count=random_count)
        target_results = [_drive_target(base, hero_id, t) for t in plan.targets
                          if t.kind != "door_cross"]  # doors are exercised by dfs_sweep's own traversal
        all_fp = {(int(c), int(r)) for p in manifest["props"] for (c, r) in p["footprint"]}
        tok = _rest_token(surface, hero_id)
        spawn_cell = [tok["x"], tok["y"]] if tok else None
        spawn_ok = spawn_cell is not None and tuple(spawn_cell) not in all_fp
        spawn_check = {
            "id": "spawn_position", "kind": "spawn_position", "cell": spawn_cell, "pass": spawn_ok,
            "defect": (None if spawn_ok else
                      f"party token at {spawn_cell} sits ON a prop footprint cell — the woodpile-spawn class"),
        }
        return {"room": loc_id, "targets": target_results, "spawn_check": spawn_check,
               "unreachable_props": plan.unreachable}
    return _inner


# ── impure: boot + seed + teardown ───────────────────────────────────────────────────────────────────
def _free_port(start: int = 8767, end: int = 8799) -> int:
    for p in range(start, end + 1):
        if p == OWNER_LIVE_PORT:
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
            continue
    raise RuntimeError(f"no free port in {start}-{end} (excluding the owner's live {OWNER_LIVE_PORT})")


def _wait_ready(base: str, cid: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            _get(base, f"/combat-surface?campaign={cid}")
            return
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last = str(exc)
            time.sleep(0.25)
    raise RuntimeError(f"viewer never came up within {timeout}s: {last}")


def run_live(state_dir: Path, rundir: Path, *, port: Optional[int] = None, random_count: int = 4,
            seed_script: Optional[Path] = None) -> dict:
    """Seed the walkslice 3-room world into `state_dir`, boot a SCRATCH viewer on a non-8766 port,
    drive the adversarial sweep across every room, write findings.json into `rundir`, then tear the
    viewer down. Kills EXACTLY the one subprocess this function starts, by PID — never a pre-existing
    viewer/player, never the owner's live 8766 session (see the module docstring's deviation note)."""
    rundir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    seed_script = seed_script or _SEED_SCRIPT

    seed_env = {**os.environ, "WORLDOS_STATE_DIR": str(state_dir)}
    seed_proc = subprocess.run(
        ["uv", "run", "--directory", str(_ROOT / "servers" / "engine"), "python",
         str(seed_script), str(state_dir)],
        cwd=str(_ROOT), env=seed_env, capture_output=True, text=True,
    )
    (rundir / "seed.log").write_text((seed_proc.stdout or "") + (seed_proc.stderr or ""), encoding="utf-8")
    if seed_proc.returncode != 0:
        raise RuntimeError(f"seed failed (rc={seed_proc.returncode}); see {rundir}/seed.log")
    seed_lines = [ln for ln in seed_proc.stdout.strip().splitlines() if ln.strip()]
    if not seed_lines:
        raise RuntimeError(f"seed produced no output; see {rundir}/seed.log")
    seed_out = json.loads(seed_lines[-1])
    cid = seed_out["campaign_id"]
    hero_id = seed_out["hero_id"]

    port = port or _free_port()
    if port == OWNER_LIVE_PORT:
        raise RuntimeError(f"refusing port {OWNER_LIVE_PORT} — the owner's live server")
    base = f"http://127.0.0.1:{port}"

    viewer_env = {**os.environ, "WORLDOS_STATE_DIR": str(state_dir),
                 "WORLDOS_PLAYER_MOVES": str(state_dir / "player_moves.jsonl")}
    viewer_log = open(rundir / "viewer.log", "wb")
    viewer = subprocess.Popen([sys.executable, str(_VIEWER), cid, str(port)],
                             cwd=str(_ROOT), env=viewer_env, stdout=viewer_log, stderr=subprocess.STDOUT)
    try:
        _wait_ready(base, cid)

        def _get_surface() -> dict:
            surf = _get(base, f"/combat-surface?campaign={cid}")
            return surf

        def _cross(door: dict) -> dict:
            return _cross_door(base, hero_id, {**door, "_cid": cid})

        results = dfs_sweep(
            get_surface=_get_surface,
            run_room_checks=_run_room_checks(base, hero_id, random_count),
            cross_door=_cross,
        )
    finally:
        viewer.terminate()
        try:
            viewer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            viewer.kill()
            viewer.wait(timeout=5)
        viewer_log.close()

    findings = build_findings(results)
    (rundir / "findings.json").write_text(json.dumps(findings, indent=2), encoding="utf-8")
    return findings


def _print_table(findings: dict) -> None:
    print(f"[journey_click_sweep] {'PASS' if findings['passed'] else 'FAIL'}: "
         f"{findings['targets_checked']} targets, {findings['targets_with_defects']} with defects")
    for room in findings["per_room"]:
        print(f"  ROOM {room['room']}: {room['passed']}/{room['checked']} passed")
    for u in findings["unreachable_props"]:
        print(f"  UNREACHABLE [{u['room']}] {u['id']}: {u['reason']}")
    for d in findings["defects"]:
        print(f"  DEFECT [{d.get('room')}/{d.get('kind')}] cell={d.get('cell')} :: {d.get('defect')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    rn = sub.add_parser("run", help="[scratch] seed + boot a scratch viewer + sweep + teardown")
    rn.add_argument("--state-dir", required=True)
    rn.add_argument("--rundir", required=True)
    rn.add_argument("--port", type=int, default=None)
    rn.add_argument("--random-count", type=int, default=4)
    rn.add_argument("--seed-script", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "run":
        findings = run_live(
            Path(args.state_dir), Path(args.rundir), port=args.port, random_count=args.random_count,
            seed_script=Path(args.seed_script) if args.seed_script else None,
        )
        _print_table(findings)
        return 0 if findings["passed"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
