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
  Goblin Boss) -> return to camp (throne_hall -> crypt -> camp) -> RETURN TO THE GIVER (camp ->
  tavern_snug): the §9 reward leg — talk to Maera again and read whether the reward actually landed.
  The route is DATA (DEFAULT_ROUTE, or --route) so a wider town graph extends it without new code.

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
from quest_progress import _select_quest  # noqa: E402  — the A-T lane's arc-quest picker (reused)

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


# The §9 G3 arc as DATA — (id, room, kind, actor). The closing `return_to_giver` stage is the
# reward leg (#1709): with the preceding return_to_camp it walks throne_hall -> crypt -> camp ->
# tavern_snug, so the party ends the walk AT the giver rather than at the campfire. A wider town
# graph is a DATA change here (or a --route override), never a change to the drive.
DEFAULT_ROUTE: tuple = (
    ("camp_start", "camp_clearing", "start", None),
    ("to_tavern", "tavern_snug", "approach", "Keeper Maera"),
    ("back_to_camp", "camp_clearing", "return", None),
    ("to_crypt", "crypt", "walk", None),
    ("to_throne", "throne_hall", "approach", "Goblin Boss"),
    ("return_to_camp", "camp_clearing", "return", None),
    ("return_to_giver", "tavern_snug", "return_to_giver", "Keeper Maera"),
)


def parse_route_spec(spec=None) -> tuple:
    """Normalise a route SPEC into the (id, room, kind, actor) tuples build_route consumes: None →
    the §9 DEFAULT_ROUTE; an inline JSON string (or `@path` to a JSON file) → its parsed list; an
    already-parsed sequence passes through. Entries are [id, room, kind, actor?] or the same fields
    as an object. PURE."""
    if spec is None:
        return DEFAULT_ROUTE
    if isinstance(spec, str):
        spec = json.loads(Path(spec[1:]).read_text(encoding="utf-8") if spec.startswith("@") else spec)
    out: list = []
    for e in spec:
        if isinstance(e, dict):
            out.append((e["id"], e["room"], e.get("kind", "walk"), e.get("actor")))
        else:
            e = list(e)
            out.append((e[0], e[1], e[2] if len(e) > 2 else "walk", e[3] if len(e) > 3 else None))
    if not out:
        raise ValueError("route override is empty — a walk with no stages proves nothing")
    return tuple(out)


def build_route(start: str = "camp_clearing", plan: tuple = DEFAULT_ROUTE) -> list:
    """The §9 walked arc as an ordered Stage list: camp -> tavern (Keeper Maera) -> back to camp ->
    crypt (walk its floor) -> throne_hall (the Goblin Boss) -> back to camp -> RETURN TO THE GIVER
    (the reward leg). Each stage carries the door chain (room_path) from the PREVIOUS stage's room,
    so the drive knows exactly which doors to cross. PURE + deterministic (unit-tested)."""
    route: list = []
    prev = start
    for sid, room, kind, actor in plan:
        hops = room_path(prev, room)
        if not hops:
            # FAIL CLOSED: an empty hop chain means the door graph does not link `room` from `prev`.
            # Building the stage anyway would let the drive record an arrival it never walked to
            # (and a --route override could then set route_complete from the wrong room).
            raise ValueError(f"route stage {sid!r}: {room!r} is not reachable from {prev!r} over the "
                             f"seeded door graph — a --route override may only name linked rooms")
        route.append(Stage(id=sid, room=room, kind=kind,
                           expected_desc=ROOM_CLASS.get(room, room.replace("_", " ")),
                           actor=actor, hops=hops))
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
        # A scorer that skipped a question must never read as clean — but a SKIPPED answer is scorer
        # infra, not evidence of the content defect itself: keep the missing flag NAMES out of
        # `defects` (else classify_stage_verdict reads them as real content defects → false RED) and
        # record them separately. Only flags the scorer actually answered True stay as content defects.
        return {"frames_checked": 1, "flags": flags,
                "defects": sorted(k for k, v in flags.items() if v) + ["vqa_incomplete"],
                "missing": sorted(missing), "passed": False}
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
    if stage_rec.get("stuck") or stage_rec.get("action_failed") or _content_defects(stage_rec):
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
    if not stages:
        return "ERROR", 2   # no stages walked = no evidence; never a vacuous GREEN
    if any(s.get("verdict") == "RED" for s in stages):
        return "RED", 1
    if any(s.get("verdict") == "ERROR" for s in stages) or report.get("harness_errors"):
        return "ERROR", 2
    return "GREEN", 0


# ── the §9 REWARD leg (read from get_quests / the A-T lane's quest_trace; PURE) ─────────────────────
QuestReader = Callable[[], dict]   # () -> {"quests": [...]} (get_quests shape) and/or {"stamps": [...]}
REWARD_SIGNALS = ("reward_received", "quest_completed")


def _reward_objective(objectives) -> Optional[str]:
    """The outstanding return/reward objective, if the quest still carries one."""
    return next((str(o) for o in objectives or []
                 if "return" in str(o).lower() or "reward" in str(o).lower()), None)


def _ended_unpaid(status) -> bool:
    """True when a quest status ENDED the arc WITHOUT paying it out — anything terminal that is not
    `completed` (failed / abandoned / cancelled). Empty or `active` is not an ending."""
    v = str(status or "").strip().lower()
    return bool(v) and v not in ("active", "completed")


def classify_reward_leg(data: dict, quest_title: str = "") -> dict:
    """Tri-state for the reward leg from a get_quests payload and/or quest_trace stamps: a
    reward_received / quest_completed signal → GREEN; a quest still readable but WITHOUT one → RED
    plus the outstanding objectives (the arc never paid out); no readable quest state at all →
    ERROR — never a silent GREEN on missing evidence, per the walk_test tri-state discipline.
    A quest that ended FAILED/abandoned is NOT a paid reward: only an independent reward_received
    signal reads GREEN there (see the status branch below)."""
    stamps = [s for s in (data.get("stamps") or []) if isinstance(s, dict)]
    quests = data.get("quests") or []
    quest = _select_quest(quests, quest_title) if quests else None
    signals = []
    # The trace is the FALLBACK source, never a second opinion: a reused sandbox run keeps its state
    # dir and the seeder rewrites the campaign WITHOUT clearing quest_trace.json (run_adventure.sh
    # warns about exactly this), so a stale quest_completed stamp must never outvote a live ACTIVE
    # quest. Stamps count only when the live read gave us nothing.
    for s in (stamps if quest is None else []):
        name = str(s.get("stage"))
        if name not in REWARD_SIGNALS:
            continue
        sig = str(s.get("signal") or "")
        # quest_progress stamps quest_completed for ANY non-active status and records WHICH in the
        # signal ("status:failed"). A failed arc is not a paid reward — skip the stamp.
        if name == "quest_completed" and sig.startswith("status:") and _ended_unpaid(sig[len("status:"):]):
            continue
        signals.append(name)
    # the trace's own quest_status is the fallback truth when the live get_quests read is down
    status, outstanding = data.get("quest_status"), []
    if quest is not None:
        status = str(quest.get("status") or "active")
        done = {str(o).strip().lower() for o in quest.get("completed_objectives") or []}
        if any("return" in o or "reward" in o for o in done):
            signals.append("reward_received")
        outstanding = [str(o) for o in quest.get("objectives") or []
                       if str(o).strip().lower() not in done]
        # A `completed` STATUS certifies the reward only when the return/reward objective is not
        # still outstanding: the DM can resolve a quest with complete_quest/set_quest_status while
        # that objective is unmet (qa/test_quest_progress.py covers that state, reward_received
        # absent), and synthesising a signal there would falsely certify the very leg this checks.
        if status == "completed" and not _reward_objective(outstanding):
            signals.append("quest_completed")
    # FAILED / abandoned ENDS the arc without paying it out (the scorecard has runs that fail after
    # the PC goes down). Drop every arc-end signal — live status or trace status — so ONLY an
    # independent reward_received can still read GREEN: a dead party never satisfies the reward leg.
    if _ended_unpaid(status):
        signals = [s for s in signals if s != "quest_completed"]
    res = {"verdict": "GREEN", "signals": sorted(set(signals)), "quest_status": status,
           "outstanding_objectives": outstanding}
    if signals:
        return res
    if quest is None and not stamps and status is None:
        return {**res, "verdict": "ERROR",
                "reason": "no readable quest state (get_quests empty / quest_trace absent)"}
    return {**res, "verdict": "RED", "signals": [],
            "reason": f"quest is {status or 'active'} with no reward signal after the giver talk; "
                      f"outstanding: {outstanding or ['(none listed)']}"}


def assert_route_returns_to_giver(plan: tuple) -> tuple:
    """A route override must close on a `return_to_giver` stage — the whole point of the §9 G3 walk.
    Without this an override of ordinary stages walks, scores GREEN/0 and only whispers its
    incompleteness through `route_complete`, which automation can miss. Partial routes stay drivable
    behind an explicit --allow-partial-route."""
    if not any(k == "return_to_giver" for _sid, _room, k, _actor in plan):
        raise ValueError("route override has no `return_to_giver` stage — the §9 G3 walk must end at "
                         "the giver (pass --allow-partial-route to drive a deliberate partial route)")
    return plan


def read_reward_leg(reader: Optional[QuestReader], quest_title: str = "") -> dict:
    """Read the quest state through `reader`, then classify. No reader wired, or a reader that RAISES
    (engine import / RPC unreachable), is HARNESS → ERROR: never a walk RED."""
    if reader is None:
        return {"verdict": "ERROR", "signals": [], "quest_status": None,
                "outstanding_objectives": [], "reason": "no quest reader wired (--state not resolved)"}
    try:
        data = reader() or {}
    except Exception as e:  # noqa: BLE001 — an unreachable quest RPC is harness, not an arc verdict
        return {"verdict": "ERROR", "signals": [], "quest_status": None,
                "outstanding_objectives": [], "reason": f"quest read failed: {e}"[:200]}
    return classify_reward_leg(data, quest_title)


def is_route_complete(report: dict) -> bool:
    """Did the walk actually FINISH the §9 arc? True ONLY when the route carried a return_to_giver
    stage, the party ARRIVED back at the giver, and its reward leg read GREEN. A walk that stopped
    at camp, never reached the giver, or could not read the quest is not complete — the G3 row must
    never read ROUTE-COMPLETE off a walk that stopped short."""
    for s in report.get("stages", []):
        if s.get("kind") == "return_to_giver":
            return bool(s.get("arrived")) and (s.get("reward_leg") or {}).get("verdict") == "GREEN"
    return False


def init_report(engine: str, qa: str, route: list) -> dict:
    """Provenance-stamped base report (mirrors walk_test._init_report's self-describing shape)."""
    return {
        "schema_version": 1,
        "repo_sha": W._repo_sha(),
        "ts": W._utc_now_iso(),
        "engine_url": engine, "qa_url": qa, "campaign": CAMPAIGN,
        "route": [s.id for s in route],
        "stages": [], "harness_errors": [], "verdict": "PENDING", "route_complete": False,
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
    # The player's QA listener answers EVERY path with HTTP 200 and a bare `{"ok": false}` for one it
    # does not serve (it serves /click,/shot,/health,/debug — no /talk), so a 200 is NOT proof the
    # verb landed: only an explicit ok:true is. Anything else records proximity only.
    try:
        resp = W._post(f"{qa}/talk", {"target": actor}) or {}
        out["talked"] = True if resp.get("ok") is True else None
        if out["talked"] is None:
            out["talk_error"] = f"channel did not accept /talk: {str(resp)[:100]}"
    except Exception as e:  # noqa: BLE001 — best-effort proximity verb; record why it failed.
        out["talked"] = None
        out["talk_error"] = str(e)[:120]
    return out


def walk_stage(qa: str, engine: str, stage: Stage, out_dir: Path, scorer: FrameScorer, *,
               settle: float, timeout: float, quest_reader: Optional[QuestReader] = None) -> dict:
    """Drive ONE stage: cross the door chain to the stage room, do its per-kind action (walk floor /
    approach actor / establish), capture a frame, score its VQA, and record stuck/dead-click accounting
    + timing + a tri-state verdict. Never raises — records everything so the report is complete."""
    t0 = time.time()
    rec = {"id": stage.id, "room": stage.room, "kind": stage.kind, "actor": stage.actor,
           "attempts": 0, "dead_clicks": 0, "arrived": False, "arrival_room": None, "stuck": False,
           "adjacent": None, "talked": None, "vqa": None, "reward_leg": None,
           "harness_errors": [], "verdict": "PENDING"}

    # 1) cross the door chain (skip the first hop — it's the room we START in). A drive-error/no-door
    # on any hop stops the chain; `stuck` records that the stage never reached its room within budget.
    # A hop-less stage is UNROUTABLE (build_route rejects these; belt-and-braces for a hand-built
    # Stage): never let it read as an arrival — record HARNESS and leave `arrived` False.
    arrival = stage.hops[0] if stage.hops else None
    if not stage.hops:
        rec["harness_errors"].append(f"unroutable stage: no door path to {stage.room}")
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
            # A majority-dead floor (clicks landed, token never arrived on BFS-reachable cells) is a
            # REAL walkability failure — one dead cell of several stays sub-verdict noise (settle
            # budget), but most-dead means the floor does not walk.
            if wf["attempts"] > 0 and wf["dead_clicks"] * 2 >= wf["attempts"] and not wf["harness_errors"]:
                rec["action_failed"] = f"walk_floor: {wf['dead_clicks']}/{wf['attempts']} sampled cells dead"
        elif stage.kind in ("approach", "return_to_giver") and stage.actor:
            ap = _approach_actor(qa, engine, stage.actor, settle, timeout)
            rec["attempts"] += ap["attempts"]; rec["dead_clicks"] += ap["dead_clicks"]
            rec["adjacent"] = ap["adjacent"]; rec["talked"] = ap["talked"]
            rec["actor_cell"] = ap.get("actor_cell")
            rec["harness_errors"].extend(ap["harness_errors"])
            # A KNOWN actor cell we cleanly failed to reach is a real approach failure (RED). An
            # UNKNOWN cell stays proximity-unknown — the surface may not expose NPCs (#1639); VQA
            # owns actor-presence there.
            if ap.get("actor_cell") and not ap["adjacent"] and not ap["harness_errors"]:
                rec["action_failed"] = f"approach {stage.actor}: never reached a cell adjacent to {ap['actor_cell']}"
        # the §9 REWARD leg: back at the giver, did the quest actually pay out? A RED leg is a real
        # arc failure (the walk finished but the reward never landed); an UNREADABLE quest is
        # harness — it must classify ERROR, never a false walk RED.
        if stage.kind == "return_to_giver":
            leg = read_reward_leg(quest_reader)
            # `talked` is the best-effort giver verb: None means the QA channel has no /talk, so the
            # parley was never driven by THIS harness — the leg still reports the true quest state,
            # but a reader must not misread its RED as "we talked and the arc refused to pay".
            leg["talk_landed"] = rec["talked"]
            rec["reward_leg"] = leg
            if leg["verdict"] == "RED":
                rec["action_failed"] = f"reward_leg: {leg.get('reason')}"
            elif leg["verdict"] == "ERROR":
                rec["harness_errors"].append(f"reward_leg: {leg.get('reason')}")

    # 3) capture a frame + score its VQA (a missing frame is a HARNESS defect, never a silent green).
    shot = W._capture_shot(qa, out_dir, stage.id)
    rec["frame"] = shot
    rec["vqa"] = score_stage_frame(shot, stage, scorer)

    rec["duration_s"] = round(time.time() - t0, 3)
    rec["verdict"] = classify_stage_verdict(rec)
    return rec


def run_walk(engine: str, qa: str, out_dir: Path, scorer: FrameScorer, *,
             settle: float, timeout: float, start: str = "camp_clearing", route_spec=None,
             quest_reader: Optional[QuestReader] = None) -> dict:
    """Drive the full §9 arc, stage by stage, into a report. The caller decides the exit code."""
    route = build_route(start, parse_route_spec(route_spec))
    report = init_report(engine, qa, route)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stage in route:
        rec = walk_stage(qa, engine, stage, out_dir, scorer, settle=settle, timeout=timeout,
                         quest_reader=quest_reader)
        report["stages"].append(rec)
        report["harness_errors"].extend(rec["harness_errors"])
    report["totals"] = {
        "stages": len(route),
        "arrived": sum(1 for s in report["stages"] if s["arrived"]),
        "dead_clicks": sum(s["dead_clicks"] for s in report["stages"]),
        "stuck_stages": sum(1 for s in report["stages"] if s["stuck"]),
        "duration_s": round(sum(s.get("duration_s", 0.0) for s in report["stages"]), 3),
    }
    report["route_complete"] = is_route_complete(report)
    report["verdict"], _ = classify_walk_verdict(report)
    return report


def _live_scorer(model: str, timeout_s: int) -> FrameScorer:
    """The live VQA scorer — journey_eval's sonnet _shell_scorer (auth-isolated `claude -p` per frame).
    Imported lazily so the pure route/report tests never need the box or an LLM."""
    from journey_eval import _shell_scorer  # noqa: PLC0415
    return lambda path, questions: _shell_scorer(path, questions, model=model, timeout_s=timeout_s)


def _live_quest_reader(state_dir: str, campaign_id: str = CAMPAIGN,
                       trace_path: Optional[str] = None) -> QuestReader:
    """The live reward-leg source: the SAME reads the A-T lane uses — quest_progress's in-process
    engine import (get_quests) against the sandbox state dir, plus the quest_trace.json stamps that
    lane writes. Read-only (engine stays the sole writer). Raises only when NEITHER source is
    readable, so read_reward_leg records that as ERROR rather than a false RED."""
    def _read() -> dict:
        out, errors = {}, []
        try:
            # qa/run_adventure.sh writes the A-T trace to qa/transcripts/<run>.quest_trace.json —
            # NOT under the state dir — so the caller passes it; the state-dir default is only
            # quest_progress.py's own bare-invocation location.
            trace = Path(trace_path) if trace_path else Path(state_dir) / "quest_trace.json"
            if trace.is_file():
                tr = json.loads(trace.read_text(encoding="utf-8"))
                # carry quest_status too: when the live get_quests read below fails, the stamps are
                # the ONLY evidence and a bare quest_completed stamp must not read as a paid reward.
                out["stamps"] = tr.get("stamps") or []
                out["quest_status"] = tr.get("quest_status")
        except Exception as e:  # noqa: BLE001
            errors.append(f"quest_trace:{e}")
        try:
            # lazy: importing the engine is a LIVE-path cost the pure tests must never pay
            from quest_progress import _import_server  # noqa: PLC0415
            out["quests"] = _import_server(str(state_dir)).get_quests(campaign_id).get("quests") or []
        except Exception as e:  # noqa: BLE001
            errors.append(f"get_quests:{e}")
        if not out:
            raise RuntimeError("; ".join(errors) or f"no quest state under {state_dir}")
        return out
    return _read


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default=DEFAULT_ENGINE, help="sandbox engine base (default :8866)")
    ap.add_argument("--qa", default=DEFAULT_QA, help="sandbox player QA channel base (default :8972)")
    ap.add_argument("--run", default=None,
                    help="qa_sandbox run name — read the live endpoints from its sandbox.json if present")
    ap.add_argument("--out", default=str(HERE / "evidence" / "adventure_walk"))
    ap.add_argument("--route", default=None,
                    help="route override: inline JSON or @path to a JSON list of [id, room, kind, "
                         "actor] (default: the §9 arc — DEFAULT_ROUTE)")
    ap.add_argument("--state", default=None,
                    help="sandbox state dir for the reward-leg quest read (default: --run's sandbox.json)")
    ap.add_argument("--allow-partial-route", action="store_true",
                    help="permit a --route override that does NOT end at the giver (a deliberate "
                         "partial/debug walk; it can never report route_complete)")
    ap.add_argument("--quest-trace", default=None,
                    help="A-T quest_trace.json for the reward-leg FALLBACK read when the live "
                         "get_quests is down (run_adventure.sh writes qa/transcripts/<run>.quest_trace.json; "
                         "default: <state>/quest_trace.json)")
    ap.add_argument("--settle", type=float, default=0.6, help="poll interval while a move resolves")
    ap.add_argument("--move-timeout", type=float, default=8.0)
    ap.add_argument("--model", default="sonnet", help="VQA scorer model")
    ap.add_argument("--vqa-timeout", type=int, default=180)
    args = ap.parse_args(argv)

    engine, qa, state = args.engine, args.qa, args.state
    if args.run:
        # Prefer the live endpoints the sandbox actually bound (custom ports don't collide with defaults).
        sb = Path("/tmp/worldos-qa-sandbox") / args.run / "sandbox.json"
        if sb.is_file():
            m = json.loads(sb.read_text())
            engine, qa = m.get("engine", engine), m.get("qa", qa)
            state = state or m.get("state")

    route_spec = parse_route_spec(args.route)
    if not args.allow_partial_route:
        assert_route_returns_to_giver(route_spec)

    out = Path(args.out)
    report = run_walk(engine, qa, out, _live_scorer(args.model, args.vqa_timeout),
                      settle=args.settle, timeout=args.move_timeout, route_spec=route_spec,
                      quest_reader=_live_quest_reader(state, trace_path=args.quest_trace)
                      if state else None)
    out.mkdir(parents=True, exist_ok=True)
    (out / "adventure_walk_report.json").write_text(json.dumps(report, indent=2) + "\n")

    verdict, exit_code = classify_walk_verdict(report)
    tot = report["totals"]
    short = "" if report["route_complete"] else " · ROUTE-INCOMPLETE (never returned to the giver)"
    print(f"\n=== ADVENTURE_WALK — {verdict}{short} ===")
    print(f"stages {tot['arrived']}/{tot['stages']} arrived · dead_clicks {tot['dead_clicks']} · "
          f"stuck {tot['stuck_stages']} · route_complete {report['route_complete']} · {tot['duration_s']}s")
    for s in report["stages"]:
        vqa = s.get("vqa") or {}
        extra = f" defects={vqa.get('defects')}" if vqa.get("defects") else ""
        print(f"  {s['verdict']:5s} {s['id']:16s} room={s['arrival_room'] or '-':13s} "
              f"attempts={s['attempts']} dead={s['dead_clicks']}{extra}")
    leg = next((s.get("reward_leg") for s in report["stages"] if s.get("kind") == "return_to_giver"), None)
    if leg:
        print(f"  reward_leg {leg['verdict']} signals={leg.get('signals')} "
              f"outstanding={leg.get('outstanding_objectives')}")
    if report["harness_errors"]:
        print(f"HARNESS ({len(report['harness_errors'])}) — NOT a walk verdict:"
              + "".join(f"\n    - {m}" for m in report["harness_errors"][:8]))
    print(f"report: {out / 'adventure_walk_report.json'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
