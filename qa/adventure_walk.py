#!/usr/bin/env python3
"""adventure_walk.py — the A-series Lane G WALKED eval: drive the sandbox player through the arc.

The A-G lane (PRODUCT-ROADMAP §4d): the text-arc eval (A-T) proves the DM can NARRATE the quest loop;
this proves a player can WALK it. It drives the SANDBOX player (qa/qa_sandbox.py's second instance on
the :8972 QA channel + the :8866 engine surface) through the qa/seed_adventure_demo.py arc route and,
per STAGE, captures a frame + a FACTUAL VQA question set (is the scene the expected room class? is the
expected actor visible? any walk-through-visual anomaly?) + ui_playtest-style stuck/dead-click
accounting, then writes adventure_walk_report.json with per-stage verdicts, stage timings, and a
tri-state overall verdict.

The route (seed_adventure_demo geometry — camp_clearing is the hub):
  camp_clearing --[8,0]<->[5,0]-- tavern_snug (Keeper Maera)     ... camp -> tavern -> back to camp
  camp_clearing --[0,6]<->[7,0]-- crypt --[15,5]<->[8,11]-- throne_hall (Goblin Boss)
  walk: camp -> tavern (adjacent to Maera) -> camp -> crypt (walk floor) -> throne_hall (approach the
  Goblin Boss) -> return to camp (throne_hall -> crypt -> camp).

REUSE (do not re-invent):
  * qa/walk_test.py  — the door-graph drive machinery (_drive_and_check / _capture_shot / _token_cell /
    _location), the transport (_get/_post), and the TRI-STATE discipline (is_drive_error; a harness/
    infra defect is NEVER a room verdict — it classifies ERROR, not RED).
  * qa/journey_eval.py — the per-frame FACTUAL VQA pattern (an injectable FrameScorer, YES=defect, the
    sonnet _shell_scorer for the live path) so the aggregation is unit-testable with a stub (no box/LLM).
  * qa/ui_playtest_score.py — the stuck / dead-click accounting style (a click that LANDED but produced
    no progress is a dead click; a stage that can't reach its target within budget is `stuck`).

Engine = SOLE WRITER (VISION.md): this drives the player and reads frames/surfaces only; it never
mutates engine state. The PURE route-builder + report-shape + tri-state helpers are unit-tested in
qa/test_adventure_walk.py with a mocked transport — no live player needed.

Usage (bring the sandbox up FIRST — it provisions the cloned state, engine :8866, player :8972):
  WORLDOS_PLAYER_APP=/path/WorldOSPlayer.app qa/qa_sandbox.py up --run adventure \
      --campaign adventure_demo_v1 \
      --seed-cmd "uv run --directory servers/engine python /ABS/PATH/qa/seed_adventure_demo.py {state}"
  qa/adventure_walk.py --run adventure --out qa/evidence/adventure_walk/
  qa/qa_sandbox.py down --run adventure
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import walk_test as W  # noqa: E402  — transport + drive machinery + tri-state (reused, not re-invented)
from journey_eval import _adjacent_walkable  # noqa: E402  — the "stand next to it" cell picker

# Default sandbox endpoints (qa_sandbox.py ENGINE_PORT/QA_PORT). The owner instance is 8766/8971;
# the sandbox NEVER collides with it, so this eval always targets the disposable stack.
DEFAULT_ENGINE = "http://127.0.0.1:8866"
DEFAULT_QA = "http://127.0.0.1:8972"
CAMPAIGN = "adventure_demo_v1"

# The seed's door graph (seed_adventure_demo.ROOMS) — the adjacency the drive routes over. Kept here
# (not read from the live surface) so room_path is PURE + unit-testable; the drive still discovers each
# door CELL from the live surface at cross time, so a re-seed that moves a doorway can't desync this.
ADJACENCY: dict[str, list[str]] = {
    "camp_clearing": ["tavern_snug", "crypt"],
    "tavern_snug": ["camp_clearing", "shop"],
    "shop": ["tavern_snug"],
    "crypt": ["camp_clearing", "throne_hall"],
    "throne_hall": ["crypt"],
}


# ── the walked route (PURE) ──────────────────────────────────────────────────────────────────────
@dataclass
class Stage:
    id: str
    room: str                       # the room this stage ends in
    kind: str                       # start | approach | walk | return
    expected_desc: str              # human room-class phrasing for the VQA question
    actor: Optional[str] = None     # an NPC/monster the stage must approach + see (else None)
    hops: list = field(default_factory=list)   # room_path(prev_room, room) — the door chain to cross

    def as_dict(self) -> dict:
        return {"id": self.id, "room": self.room, "kind": self.kind, "actor": self.actor,
                "expected_desc": self.expected_desc, "hops": list(self.hops)}


# Expected room-class phrasing per room (drives the wrong_room_class VQA question; YES=defect).
ROOM_CLASS = {
    "camp_clearing": "an OUTDOOR camp clearing (tents, campfire, open ground/sky — not an interior room)",
    "tavern_snug": "a cozy TAVERN interior (wooden beams, hearth, tables/benches)",
    "crypt": "an underground CRYPT / dungeon (stone walls, tombs, gloom)",
    "throne_hall": "a grand THRONE HALL / audience chamber",
}


def room_path(src: str, dst: str) -> list:
    """BFS over the door graph → the ordered list of rooms from `src` to `dst` INCLUSIVE (so
    consecutive pairs are the doors to cross). [src] when src == dst; [] when dst is unreachable."""
    if src == dst:
        return [src]
    seen, queue = {src}, [[src]]
    while queue:
        path = queue.pop(0)
        for nxt in ADJACENCY.get(path[-1], []):
            if nxt in seen:
                continue
            if nxt == dst:
                return path + [nxt]
            seen.add(nxt)
            queue.append(path + [nxt])
    return []


def build_route(start: str = "camp_clearing") -> list:
    """The §4d walked arc as an ordered Stage list: camp -> tavern (Keeper Maera) -> back to camp ->
    crypt (walk its floor) -> throne_hall (the Goblin Boss) -> return to camp. Each stage carries the
    door chain (room_path) from the PREVIOUS stage's room, so the drive knows exactly which doors to
    cross. PURE + deterministic (unit-tested)."""
    plan = [
        ("camp_start", "camp_clearing", "start", None),
        ("to_tavern", "tavern_snug", "approach", "Keeper Maera"),
        ("back_to_camp", "camp_clearing", "return", None),
        ("to_crypt", "crypt", "walk", None),
        ("to_throne", "throne_hall", "approach", "Goblin Boss"),
        ("return_to_camp", "camp_clearing", "return", None),
    ]
    route: list = []
    prev = start
    for sid, room, kind, actor in plan:
        route.append(Stage(id=sid, room=room, kind=kind,
                           expected_desc=ROOM_CLASS.get(room, room.replace("_", " ")),
                           actor=actor, hops=room_path(prev, room)))
        prev = room
    return route


# ── FACTUAL VQA per stage (journey_eval pattern — injectable scorer, YES=defect) ────────────────────
FrameScorer = Callable[[str, list], dict]  # (image_path, questions) -> {flag: bool}


def stage_questions(stage: Stage) -> list:
    """The single-frame VQA questions for a stage (YES=defect), mirroring journey_eval's factual set.
    Every stage checks room-class + a walk-through anomaly; an actor stage adds an actor-visible check."""
    qs = [
        {"flag": "wrong_room_class",
         "text": f"Is the scene NOT {stage.expected_desc}? Answer YES only if it clearly looks like a "
                 f"different KIND of location.",
         "applies_to": "all"},
        {"flag": "walk_through_anomaly",
         "text": "Does any character visibly clip through, or stand INSIDE, a wall or a solid prop "
                 "(a wall/pillar/tomb/table)? Answer YES if so.",
         "applies_to": "all"},
    ]
    if stage.actor:
        qs.append({"flag": "actor_missing",
                   "text": f"Is {stage.actor} NOT visible anywhere in the scene? Answer YES if you "
                           f"cannot see them.",
                   "applies_to": "all"})
    return qs


def score_stage_frame(frame_path: Optional[str], stage: Stage, scorer: FrameScorer) -> dict:
    """Ask the stage's VQA questions of its captured frame → {frames_checked, defects, flags, passed}.
    A missing frame (capture failed) is NOT a silent pass: it records zero frames checked + a capture
    defect so the stage cannot read GREEN on no evidence (mirrors journey_eval.build_verdict's
    empty-capture rule)."""
    if not frame_path:
        return {"frames_checked": 0, "flags": {}, "defects": ["vqa_no_frame"], "passed": False}
    try:
        raw = scorer(frame_path, stage_questions(stage))
    except Exception as e:  # noqa: BLE001 — a scorer-infra crash is a HARNESS defect for THIS stage,
        # never a run-killer: the arc must finish and the report must land with prior stages intact.
        return {"frames_checked": 0, "flags": {}, "defects": ["vqa_scorer_error"],
                "passed": False, "error": str(e)[:200]}
    flags = {k: bool(v) for k, v in raw.items()}
    want = {q["flag"] for q in stage_questions(stage)}
    missing = want - flags.keys()
    if missing:
        # A scorer that skipped a question must never read as clean — surface it as a defect.
        return {"frames_checked": 1, "flags": flags,
                "defects": sorted(list(missing) + [k for k, v in flags.items() if v]) + ["vqa_incomplete"],
                "passed": False}
    defects = sorted(k for k, v in flags.items() if v)
    return {"frames_checked": 1, "flags": flags, "defects": defects, "passed": not defects}


# ── tri-state verdict (PURE; walk_test discipline — a harness defect is NEVER a room verdict) ───────
VQA_HARNESS_FLAGS = frozenset({"vqa_no_frame", "vqa_incomplete", "vqa_scorer_error"})


def classify_stage_verdict(stage_rec: dict) -> str:
    """A single stage's tri-state verdict. A real WALK failure (wrong/failed arrival, stuck, or a VQA
    CONTENT defect — wrong room class / actor missing / walk-through) → RED, and WINS even beside a
    harness error. Otherwise a HARNESS defect (player/engine drive-error, or a VQA capture/scorer-infra
    failure) → ERROR (never a silent GREEN on missing evidence). Fully clean → GREEN."""
    # A CLEAN walk failure (the door exists but the party never crossed, or a VQA content defect) is a
    # real arc RED and WINS over any harness noise. `stuck` is set ONLY on a clean cross failure — never
    # when the engine/player was unreachable — so it can never carry an infra defect into a RED verdict.
    if stage_rec.get("stuck") or _content_defects(stage_rec):
        return "RED"
    # Everything else that isn't a clean arrival is HARNESS: a cross that couldn't even be attempted
    # (engine/player unreachable → not arrived, not stuck), an explicit harness error, or a VQA
    # capture/scorer-infra failure. ERROR, never a silent GREEN on missing evidence.
    vqa_harness = bool(set((stage_rec.get("vqa") or {}).get("defects", [])) & VQA_HARNESS_FLAGS)
    if (not stage_rec.get("arrived")) or stage_rec.get("harness_errors") or vqa_harness:
        return "ERROR"
    return "GREEN"


def _content_defects(stage_rec: dict) -> list:
    """The VQA CONTENT defects (wrong room class / actor missing / walk-through) — a real walk RED.
    Excludes vqa_no_frame / vqa_incomplete, which are HARNESS (capture/scorer infra, not the scene)."""
    return [d for d in (stage_rec.get("vqa") or {}).get("defects", []) if d not in VQA_HARNESS_FLAGS]


def classify_walk_verdict(report: dict) -> tuple:
    """Overall (verdict, exit_code): any RED stage → RED/1 (a real walk failure wins even beside harness
    noise); else any ERROR stage or top-level harness_errors → ERROR/2; else GREEN/0."""
    stages = report.get("stages", [])
    if any(s.get("verdict") == "RED" for s in stages):
        return "RED", 1
    if any(s.get("verdict") == "ERROR" for s in stages) or report.get("harness_errors"):
        return "ERROR", 2
    return "GREEN", 0


def init_report(engine: str, qa: str, route: list) -> dict:
    """Provenance-stamped base report (mirrors walk_test._init_report's self-describing shape)."""
    return {
        "schema_version": 1,
        "repo_sha": W._repo_sha(),
        "ts": W._utc_now_iso(),
        "engine_url": engine, "qa_url": qa, "campaign": CAMPAIGN,
        "route": [s.id for s in route],
        "stages": [], "harness_errors": [], "verdict": "PENDING",
    }


# ── live drive (I/O; monkeypatched in tests via walk_test._get/_post — the test_walk_test convention) ─
def _actor_cell(surf: dict, name: str):
    """Best-effort (x,y) of a named actor from an engine surface — scan combat `tokens`, a REST
    `stage.tokens`, and `characters`, matching name/label case-insensitively. None if not located
    (the stage then records proximity-unknown rather than failing on a missing token)."""
    key = (name or "").strip().lower()
    pools = [surf.get("tokens") or [], (surf.get("stage") or {}).get("tokens") or [],
             surf.get("characters") or []]
    for pool in pools:
        for t in pool:
            label = str(t.get("name") or t.get("label") or t.get("id") or "").strip().lower()
            if key and key in label:
                if "x" in t and "y" in t:
                    return (int(t["x"]), int(t["y"]))
                cell = t.get("stage_cell") or t.get("cell")
                if cell:
                    return (int(cell[0]), int(cell[1]))
    return None


def _cross_to(qa: str, engine: str, target: str, settle: float, timeout: float) -> dict:
    """Cross the door from the CURRENT room to the adjacent `target` room: find the door whose `to` is
    `target` in the live surface, click it, poll until the engine location becomes `target`. Reuses the
    walk_test transport. Returns {ok, arrival, attempts, dead_clicks, harness}. A click/poll exception
    is a HARNESS error (not a walk RED)."""
    try:
        surf = W._get(f"{engine}/combat-surface")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "arrival": None, "attempts": 0, "dead_clicks": 0, "harness": f"surface:{e}"}
    door = next((d for d in surf.get("doors", []) if d.get("to") == target), None)
    if not door:
        return {"ok": False, "arrival": W._location(surf), "attempts": 0, "dead_clicks": 0,
                "harness": None, "no_door": target}
    c, r = int(door["cell"][0]), int(door["cell"][1])
    try:
        W._post(f"{qa}/click", {"c": c, "r": r})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "arrival": W._location(surf), "attempts": 1, "dead_clicks": 0,
                "harness": f"click:{e}"}
    deadline = time.time() + timeout
    saw_surface = False
    while time.time() < deadline:
        time.sleep(settle)
        try:
            loc = W._location(W._get(f"{engine}/combat-surface"))
        except Exception:  # noqa: BLE001
            continue
        saw_surface = True
        if loc == target:
            return {"ok": True, "arrival": target, "attempts": 1, "dead_clicks": 0, "harness": None}
    if not saw_surface:
        return {"ok": False, "arrival": None, "attempts": 1, "dead_clicks": 0,
                "harness": f"engine surface unreachable crossing to {target}"}
    # click landed but the party never crossed — a dead click (a walk failure, not harness).
    return {"ok": False, "arrival": None, "attempts": 1, "dead_clicks": 1, "harness": None}


def _walk_floor(qa: str, engine: str, room: str, settle: float, timeout: float, *, samples: int = 3) -> dict:
    """Walk a few sampled interior floor cells of the CURRENT room (proves the floor is walkable, not
    just the doorway). Reuses walk_test._drive_and_check + walkmask_from_surface. Returns
    {attempts, dead_clicks, harness_errors}."""
    out = {"attempts": 0, "dead_clicks": 0, "harness_errors": []}
    try:
        surf = W._get(f"{engine}/combat-surface")
    except Exception as e:  # noqa: BLE001
        out["harness_errors"].append(f"walk {room}: surface:{e}")
        return out
    mask = W.walkmask_from_surface(surf)
    start = W._token_cell(surf)
    reachable = W.bfs_reachable(mask, start) if start else set(mask["walkable"])
    interior = sorted((c, r) for (c, r) in reachable
                      if 0 < c < mask["cols"] - 1 and 0 < r < mask["rows"] - 1 and (c, r) != start)
    stride = max(1, len(interior) // max(1, samples))
    for (c, r) in interior[::stride][:samples]:
        out["attempts"] += 1
        ok, landed, _p = W._drive_and_check(qa, engine, c, r, settle, timeout, expect_move=True)
        if W.is_drive_error(landed):
            out["harness_errors"].append(f"walk {room} ({c},{r}): {landed}")
        elif not ok:
            out["dead_clicks"] += 1   # click landed, token didn't reach the cell — a dead click
    return out


def _approach_actor(qa: str, engine: str, actor: str, settle: float, timeout: float) -> dict:
    """Walk to a cell orthogonally adjacent to `actor`'s stage cell (proximity), then attempt a
    /talk-equivalent on the QA channel — best-effort: if the channel has no /talk the stage records
    proximity only (talked=None). Returns {attempts, dead_clicks, adjacent, talked, harness_errors}."""
    out = {"attempts": 0, "dead_clicks": 0, "adjacent": False, "talked": None, "harness_errors": []}
    try:
        surf = W._get(f"{engine}/combat-surface")
    except Exception as e:  # noqa: BLE001
        out["harness_errors"].append(f"approach {actor}: surface:{e}")
        return out
    cell = _actor_cell(surf, actor)
    if not cell:
        # no locatable token — record proximity-unknown (not a fail: the surface may not expose NPCs)
        out["actor_cell"] = None
        return out
    out["actor_cell"] = list(cell)
    mask = W.walkmask_from_surface(surf)
    blocked = set(mask["blocked"])
    adj = _adjacent_walkable([cell], blocked, mask["cols"], mask["rows"])
    if adj is None:
        out["harness_errors"].append(f"approach {actor}: no walkable neighbour of {cell}")
        return out
    out["attempts"] += 1
    ok, landed, _p = W._drive_and_check(qa, engine, adj[0], adj[1], settle, timeout, expect_move=True)
    if W.is_drive_error(landed):
        out["harness_errors"].append(f"approach {actor} ({adj[0]},{adj[1]}): {landed}")
        return out
    out["adjacent"] = bool(ok)
    if not ok:
        out["dead_clicks"] += 1
    # /talk-equivalent — best-effort; a channel without it just leaves talked=None (proximity recorded).
    try:
        W._post(f"{qa}/talk", {"target": actor})
        out["talked"] = True
    except Exception as e:  # noqa: BLE001 — best-effort proximity verb; record why it failed.
        out["talked"] = None
        out["talk_error"] = str(e)[:120]
    return out


def walk_stage(qa: str, engine: str, stage: Stage, out_dir: Path, scorer: FrameScorer, *,
               settle: float, timeout: float) -> dict:
    """Drive ONE stage: cross the door chain to the stage room, do its per-kind action (walk floor /
    approach actor / establish), capture a frame, score its VQA, and record stuck/dead-click accounting
    + timing + a tri-state verdict. Never raises — records everything so the report is complete."""
    t0 = time.time()
    rec = {"id": stage.id, "room": stage.room, "kind": stage.kind, "actor": stage.actor,
           "attempts": 0, "dead_clicks": 0, "arrived": False, "arrival_room": None, "stuck": False,
           "adjacent": None, "talked": None, "vqa": None, "harness_errors": [], "verdict": "PENDING"}

    # 1) cross the door chain (skip the first hop — it's the room we START in). A drive-error/no-door
    # on any hop stops the chain; `stuck` records that the stage never reached its room within budget.
    arrival = stage.hops[0] if stage.hops else stage.room
    for target in stage.hops[1:]:
        cross = _cross_to(qa, engine, target, settle, timeout)
        rec["attempts"] += cross["attempts"]
        rec["dead_clicks"] += cross["dead_clicks"]
        if cross.get("harness"):
            # A HARNESS cross failure (engine/player unreachable, click threw) is NOT a walk verdict —
            # record it and stop the chain WITHOUT marking `stuck`, so the stage classifies ERROR (not
            # a false RED). Only a clean "door exists but the party never crossed" sets `stuck`.
            rec["harness_errors"].append(f"cross->{target}: {cross['harness']}")
            break
        if cross["ok"]:
            arrival = target
        else:
            rec["stuck"] = True   # clean cross failure (no door, or clicked but never crossed) → arc RED
            break
    rec["arrival_room"] = arrival
    rec["arrived"] = (arrival == stage.room)

    # 2) per-kind action ONLY when we actually arrived (driving in the wrong room is meaningless).
    if rec["arrived"]:
        if stage.kind == "walk":
            wf = _walk_floor(qa, engine, stage.room, settle, timeout)
            rec["attempts"] += wf["attempts"]; rec["dead_clicks"] += wf["dead_clicks"]
            rec["harness_errors"].extend(wf["harness_errors"])
        elif stage.kind == "approach" and stage.actor:
            ap = _approach_actor(qa, engine, stage.actor, settle, timeout)
            rec["attempts"] += ap["attempts"]; rec["dead_clicks"] += ap["dead_clicks"]
            rec["adjacent"] = ap["adjacent"]; rec["talked"] = ap["talked"]
            rec["actor_cell"] = ap.get("actor_cell")
            rec["harness_errors"].extend(ap["harness_errors"])

    # 3) capture a frame + score its VQA (a missing frame is a HARNESS defect, never a silent green).
    shot = W._capture_shot(qa, out_dir, stage.id)
    rec["frame"] = shot
    rec["vqa"] = score_stage_frame(shot, stage, scorer)

    rec["duration_s"] = round(time.time() - t0, 3)
    rec["verdict"] = classify_stage_verdict(rec)
    return rec


def run_walk(engine: str, qa: str, out_dir: Path, scorer: FrameScorer, *,
             settle: float, timeout: float, start: str = "camp_clearing") -> dict:
    """Drive the full §4d arc, stage by stage, into a report. The caller decides the exit code."""
    route = build_route(start)
    report = init_report(engine, qa, route)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stage in route:
        rec = walk_stage(qa, engine, stage, out_dir, scorer, settle=settle, timeout=timeout)
        report["stages"].append(rec)
        report["harness_errors"].extend(rec["harness_errors"])
    report["totals"] = {
        "stages": len(route),
        "arrived": sum(1 for s in report["stages"] if s["arrived"]),
        "dead_clicks": sum(s["dead_clicks"] for s in report["stages"]),
        "stuck_stages": sum(1 for s in report["stages"] if s["stuck"]),
        "duration_s": round(sum(s.get("duration_s", 0.0) for s in report["stages"]), 3),
    }
    report["verdict"], _ = classify_walk_verdict(report)
    return report


def _live_scorer(model: str, timeout_s: int) -> FrameScorer:
    """The live VQA scorer — journey_eval's sonnet _shell_scorer (auth-isolated `claude -p` per frame).
    Imported lazily so the pure route/report tests never need the box or an LLM."""
    from journey_eval import _shell_scorer  # noqa: PLC0415
    return lambda path, questions: _shell_scorer(path, questions, model=model, timeout_s=timeout_s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default=DEFAULT_ENGINE, help="sandbox engine base (default :8866)")
    ap.add_argument("--qa", default=DEFAULT_QA, help="sandbox player QA channel base (default :8972)")
    ap.add_argument("--run", default=None,
                    help="qa_sandbox run name — read the live endpoints from its sandbox.json if present")
    ap.add_argument("--out", default=str(HERE / "evidence" / "adventure_walk"))
    ap.add_argument("--settle", type=float, default=0.6, help="poll interval while a move resolves")
    ap.add_argument("--move-timeout", type=float, default=8.0)
    ap.add_argument("--model", default="sonnet", help="VQA scorer model")
    ap.add_argument("--vqa-timeout", type=int, default=180)
    args = ap.parse_args(argv)

    engine, qa = args.engine, args.qa
    if args.run:
        # Prefer the live endpoints the sandbox actually bound (custom ports don't collide with defaults).
        sb = Path("/tmp/worldos-qa-sandbox") / args.run / "sandbox.json"
        if sb.is_file():
            m = json.loads(sb.read_text())
            engine, qa = m.get("engine", engine), m.get("qa", qa)

    out = Path(args.out)
    report = run_walk(engine, qa, out, _live_scorer(args.model, args.vqa_timeout),
                      settle=args.settle, timeout=args.move_timeout)
    out.mkdir(parents=True, exist_ok=True)
    (out / "adventure_walk_report.json").write_text(json.dumps(report, indent=2) + "\n")

    verdict, exit_code = classify_walk_verdict(report)
    tot = report["totals"]
    print(f"\n=== ADVENTURE_WALK — {verdict} ===")
    print(f"stages {tot['arrived']}/{tot['stages']} arrived · dead_clicks {tot['dead_clicks']} · "
          f"stuck {tot['stuck_stages']} · {tot['duration_s']}s")
    for s in report["stages"]:
        vqa = s.get("vqa") or {}
        extra = f" defects={vqa.get('defects')}" if vqa.get("defects") else ""
        print(f"  {s['verdict']:5s} {s['id']:16s} room={s['arrival_room'] or '-':13s} "
              f"attempts={s['attempts']} dead={s['dead_clicks']}{extra}")
    if report["harness_errors"]:
        print(f"HARNESS ({len(report['harness_errors'])}) — NOT a walk verdict:"
              + "".join(f"\n    - {m}" for m in report["harness_errors"][:8]))
    print(f"report: {out / 'adventure_walk_report.json'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
