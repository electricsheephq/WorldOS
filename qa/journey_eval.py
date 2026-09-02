#!/usr/bin/env python3
"""journey_eval.py — journey capture + FACTUAL VQA: catch player-visible defects the panels miss.

The eval-blindness the owner called out: aesthetic panels measure beauty-vs-bar, so a T-posing actor,
a wrong-plate bundle, a character standing inside a painted prop, and a failed door-cross plate swap all
reached an owner build with high scores AROUND them. This harness walks the actual playable loop and
asks FACTUAL yes/no questions of every frame — facts a beauty score never registers.

Three phases (split so the expensive box drive and the LLM VQA are independently runnable + testable):
  1. build-script  — from a room manifest (+ an optional journey plan) derive the scripted path: a step
                     adjacent to EVERY impassable prop, plus configured parley / door-cross / combat-entry
                     waypoints. Transitions capture BOTH sides. PURE, deterministic, unit-tested.
  2. capture       — boot the box player (the same lib_native_player_boot.sh + #1466 QA click channel
                     qa/player_smoke.sh uses) and run qa/journey_capture.js over the script, writing a
                     frame per step (+ both sides of each transition) and a frames_manifest.json.
  3. vqa + verdict — ask qa/journey_vqa_questions.md (YES=defect) of every frame via qa/vqa_frame.sh
                     (the score.sh auth-isolation pattern, sonnet), then aggregate: ANY yes on ANY frame
                     == journey FAIL, naming the offending frame(s). Writes journey_verdict.json.

`run` does capture->vqa->verdict on the box. `vqa`/`verdict` run over an already-captured frames dir
anywhere claude is authed (the VQA core takes an injectable scorer, so the aggregation is unit-tested
with a stub — no box, no LLM). Invocation is documented in qa/UI_PLAYTEST.md.

Engine = SOLE WRITER: this drives the player and reads frames only; it never mutates engine state.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_QA_DIR = Path(__file__).resolve().parent
_ROOT = _QA_DIR.parent
_QUESTIONS_MD = _QA_DIR / "journey_vqa_questions.md"
_VQA_FRAME_SH = _QA_DIR / "vqa_frame.sh"
_CAPTURE_JS = _QA_DIR / "journey_capture.js"


# ── VQA question set (parsed from the versioned, human-reviewable markdown) ─────────────────────────
def load_questions(path: str | Path = _QUESTIONS_MD) -> list:
    """The fenced ```json block from qa/journey_vqa_questions.md — the SINGLE source of the question set
    (so the wording is reviewable/extensible in one versioned place). Every question is YES=defect."""
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        raise ValueError(f"no ```json question block in {path}")
    qs = json.loads(m.group(1)).get("questions", [])
    for q in qs:
        if not (q.get("flag") and q.get("text")
                and q.get("applies_to") in ("all", "transition", "transition_pair")):
            raise ValueError(f"malformed question entry: {q}")
    return qs


def questions_for_frame(questions: list, is_transition: bool) -> list:
    """The questions a SINGLE-FRAME LLM scorer can answer. `transition_pair` questions are excluded —
    they need both sides of a transition and are computed deterministically (see _transition_pair_flags),
    never guessed from one image."""
    return [q for q in questions if q["applies_to"] == "all"
            or (q["applies_to"] == "transition" and is_transition)]


# ── Phase 1: the scripted path (PURE) ───────────────────────────────────────────────────────────────
@dataclass
class Step:
    id: str
    kind: str                 # prop_approach | parley | door_cross | combat_entry | start
    cell: tuple               # (c, r) the click target
    transition: bool = False  # capture BOTH sides (pre + post) — a plate swap moment
    note: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "cell": list(self.cell),
                "transition": self.transition, "note": self.note}


def _adjacent_walkable(cells: list, prop_cells: set, cols: int, rows: int) -> Optional[tuple]:
    """A walkable cell orthogonally adjacent to a prop's footprint: in-bounds and occupied by no prop.
    This is the cell a player stands on to LOOK AT the prop — where an off-grid prop reads as the
    character overlapping it. Deterministic scan order (N,S,W,E per cell) so the script is reproducible."""
    for (c, r) in cells:
        for (dc, dr) in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and 0 <= nr < rows and (nc, nr) not in prop_cells:
                return (nc, nr)
    return None


@dataclass
class JourneyScript:
    steps: list                                       # approachable Step objects (drive capture)
    unreachable: list = field(default_factory=list)   # [{id, cells, reason}] — no walkable neighbour

    def as_dict(self) -> dict:
        return {"steps": [s.as_dict() for s in self.steps], "unreachable": self.unreachable}


def build_script(manifest: dict, plan: Optional[dict] = None) -> JourneyScript:
    """Derive the journey from the room manifest (+ an optional plan carrying the semantic waypoints a
    manifest can't hold: start cell, parley/door/combat cells). One prop_approach step per impassable
    prop that HAS a walkable neighbour, then the configured transitions. Props with NO walkable adjacent
    cell (e.g. scenery pinned in a wall corner) can't be stood next to — they are RECORDED as
    `unreachable` (surfaced in the verdict), never silently dropped. Approaches key off the FOOTPRINT
    (the floor cells the coherence gate checks), not `cells`, so the two agree if they ever diverge."""
    plan = plan or {}
    grid = manifest.get("grid", {})
    cols, rows = int(grid.get("cols", 0)), int(grid.get("rows", 0))
    props = manifest.get("props", [])
    prop_cells = {(int(c), int(r)) for p in props
                  for (c, r) in (p.get("footprint") or p.get("cells", []))}

    steps: list = []
    unreachable: list = []
    start = plan.get("start_cell")
    if start:
        steps.append(Step("start", "start", tuple(start), note="establishing frame"))
    for p in props:
        cells = [(int(c), int(r)) for (c, r) in (p.get("footprint") or p.get("cells", []))]
        pid = str(p.get("id", "prop"))
        if not cells:
            unreachable.append({"id": pid, "cells": [], "reason": "no footprint cells"})
            continue
        adj = _adjacent_walkable(cells, prop_cells, cols, rows)
        if adj is None:
            unreachable.append({"id": pid, "cells": [list(c) for c in cells],
                                "reason": "no walkable orthogonal neighbour"})
            continue
        steps.append(Step(f"approach_{pid}", "prop_approach", adj,
                          note=f"stand adjacent to {pid} {cells}"))
    # Configured transitions / interactions (all optional — a plan without them just walks the props).
    if plan.get("parley_cell"):
        steps.append(Step("parley", "parley", tuple(plan["parley_cell"]),
                          note="walk adjacent to the NPC + open parley"))
    if plan.get("door_cell"):
        steps.append(Step("door_cross", "door_cross", tuple(plan["door_cell"]), transition=True,
                          note="cross the doorway — plate swap; capture both sides"))
    if plan.get("combat_cell"):
        steps.append(Step("combat_entry", "combat_entry", tuple(plan["combat_cell"]), transition=True,
                          note="enter combat — surface swap; capture both sides"))
    return JourneyScript(steps=steps, unreachable=unreachable)


# ── Phase 3: VQA over frames (PURE core + injectable scorer) ─────────────────────────────────────────
# A frame record (from journey_capture.js's frames_manifest.json):
#   {"path": "...png", "step": "approach_sarcophagus", "kind": "prop_approach",
#    "side": "step"|"pre"|"post", "transition": bool}
FrameScorer = Callable[[str, list], dict]  # (image_path, questions) -> {flag: bool}
ImageDiffer = Callable[[str, str], float]  # (path_a, path_b) -> normalised difference 0..1

# A door-cross / combat-entry SHOULD swap the backdrop; a pre/post pair below this normalised luma
# difference means the room did NOT change (a failed plate swap) -> transition_backdrop_unchanged.
SWAP_MIN_DIFF = 0.04

# Flags NOT asked of an ESTABLISHING shot (kind=="start"): an establishing/cutaway frame may legitimately
# show only scenery, so "all characters missing" there is not a defect (it would false-red a clean
# journey). Every gameplay frame (prop_approach / parley / transition) still gets the full set.
_SCENERY_TOLERANT_FLAGS = {"missing_or_cloned"}


def _questions_for_record(questions: list, fr: dict) -> list:
    """The single-frame LLM questions for THIS frame — transition-aware, minus the scenery-tolerant
    flags on an establishing 'start' shot."""
    qs = questions_for_frame(questions, bool(fr.get("transition")))
    if fr.get("kind") == "start":
        qs = [q for q in qs if q["flag"] not in _SCENERY_TOLERANT_FLAGS]
    return qs


def _shell_scorer(image_path: str, questions: list, *, model: str = "sonnet",
                  timeout_s: int = 180) -> dict:
    """Default scorer: qa/vqa_frame.sh runs one sonnet `claude -p` pass over the image, returning
    {"flags": {flag: bool, ...}}. Mirrors score.sh's auth-isolation. VALIDATES that the scorer answered
    EXACTLY the requested flags — a missing flag is an error (never silently treated as clean), and the
    booleans are already normalised by vqa_frame.sh (YES/NO/true/false coercion happens there)."""
    payload = json.dumps({"questions": questions})
    proc = subprocess.run(
        [str(_VQA_FRAME_SH), image_path],
        input=payload, capture_output=True, text=True, timeout=timeout_s + 30,
        env={**_sh_env(model, timeout_s)},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"vqa_frame.sh failed on {image_path} (rc={proc.returncode}): "
                           f"{proc.stderr.strip()[-400:]}")
    try:
        flags = {k: bool(v) for k, v in json.loads(proc.stdout).get("flags", {}).items()}
    except (ValueError, AttributeError) as exc:
        raise RuntimeError(f"vqa_frame.sh emitted non-JSON for {image_path}: {exc}") from exc
    want = {q["flag"] for q in questions}
    missing = want - flags.keys()
    if missing:
        raise RuntimeError(f"vqa_frame.sh did not answer {sorted(missing)} for {image_path} "
                           f"(got {sorted(flags)}) — a missing flag must never read as clean")
    return {k: flags[k] for k in want}


def _sh_env(model: str, timeout_s: int) -> dict:
    import os
    return {**os.environ, "WORLDOS_VQA_MODEL": model, "WORLDOS_VQA_TIMEOUT": str(timeout_s)}


def _default_image_differ(a: str, b: str) -> float:
    """Normalised (0..1) mean-absolute luma difference of two frames at 64x64 — a deterministic backdrop-
    change detector for transition pairs. Lazy PIL import so the pure aggregation stays dependency-free."""
    from PIL import Image  # noqa: PLC0415
    ia = Image.open(a).convert("L").resize((64, 64))
    ib = Image.open(b).convert("L").resize((64, 64))
    da, db = list(ia.getdata()), list(ib.getdata())
    return sum(abs(x - y) for x, y in zip(da, db)) / (len(da) * 255.0)


def _transition_pair_flags(frames: list, results_by_frame: dict, questions: list,
                           image_differ: ImageDiffer) -> None:
    """Compute the `transition_pair` questions deterministically from BOTH sides of each transition (a
    single-frame LLM scorer can't compare to the other side). Today: transition_backdrop_unchanged — a
    pre/post pair that barely differs means the plate swap failed. Mutates the POST frame's result."""
    pair_flags = [q["flag"] for q in questions if q["applies_to"] == "transition_pair"]
    if not pair_flags:
        return
    by_step: dict = {}
    for fr in frames:
        if fr.get("transition"):
            by_step.setdefault(fr.get("step"), {})[fr.get("side")] = fr["path"]
    for step, sides in by_step.items():
        pre, post = sides.get("pre"), sides.get("post")
        if not (pre and post):
            continue
        res = results_by_frame.get(post)
        if res is None:
            continue
        diff = image_differ(pre, post)
        if "transition_backdrop_unchanged" in pair_flags:
            res["flags"]["transition_backdrop_unchanged"] = diff < SWAP_MIN_DIFF
        res["defects"] = sorted(k for k, v in res["flags"].items() if v)


def run_vqa(frames: list, questions: list, scorer: FrameScorer, *,
            image_differ: Optional[ImageDiffer] = None) -> list:
    """Ask the single-frame LLM questions of every frame, then fill in the `transition_pair` questions
    deterministically from paired frames. Returns per-frame results {frame, step, side, flags, defects}.
    The scorer AND the differ are injected so the aggregation is unit-testable with stubs (no LLM/box)."""
    results: list = []
    by_frame: dict = {}
    for fr in frames:
        applicable = _questions_for_record(questions, fr)
        flags = scorer(fr["path"], applicable)
        rec = {"frame": fr["path"], "step": fr.get("step"), "side": fr.get("side", "step"),
               "flags": flags, "defects": sorted(k for k, v in flags.items() if v)}
        results.append(rec)
        by_frame[fr["path"]] = rec
    _transition_pair_flags(frames, by_frame, questions, image_differ or _default_image_differ)
    return results


def build_verdict(vqa_results: list, unreachable: Optional[list] = None) -> dict:
    """ANY yes on ANY frame == journey FAIL, naming the offending frame(s) + flags. Also FAILs when NO
    frames were checked (an empty/malformed capture is not evidence the loop was inspected). Props the
    journey could not approach are surfaced (informational — a wall-pinned scenery prop is legitimately
    unreachable, not a defect, but it must never silently disappear)."""
    offenders = [{"frame": r["frame"], "step": r["step"], "side": r["side"], "defects": r["defects"]}
                 for r in vqa_results if r["defects"]]
    reasons = []
    if not vqa_results:
        reasons.append("no frames checked — capture produced nothing to inspect")
    return {
        "passed": bool(vqa_results) and not offenders,
        "frames_checked": len(vqa_results),
        "frames_with_defects": len(offenders),
        "defects": offenders,
        "unreachable_props": unreachable or [],
        "reasons": reasons,
        "per_frame": vqa_results,
    }


# ── Phase 2: box capture (thin shell over lib_native_player_boot.sh + journey_capture.js) ───────────
def capture(script: JourneyScript, rundir: Path, *, campaign: str, owner: str = "WorldOSPlayer") -> Path:
    """Drive the box player over the scripted path and write frames + a frames_manifest.json. The player
    must ALREADY be booted with the #1466 QA click channel (WORLDOS_QA_INPUT=1 + WORLDOS_QA_INPUT_PORT) —
    boot it exactly as qa/player_smoke.sh does (lib_native_player_boot.sh + Screen Recording/Accessibility
    grants + WorldOSPlayer.app). journey_capture.js FAILS LOUD if the QA channel is unhealthy or no click
    lands, so it can never VQA a stack of stale/unchanged frames. Box-only phase (#1386 claim)."""
    rundir.mkdir(parents=True, exist_ok=True)
    script_path = rundir / "journey_script.json"
    script_path.write_text(json.dumps({"campaign": campaign, **script.as_dict()}, indent=2),
                           encoding="utf-8")
    cmd = ["node", str(_CAPTURE_JS), "--script", str(script_path), "--rundir", str(rundir),
           "--owner", owner]
    proc = subprocess.run(cmd, cwd=str(_ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"journey_capture.js failed (rc={proc.returncode}) — the QA click channel was "
                           f"unhealthy or no click landed (boot the player with WORLDOS_QA_INPUT=1 first); "
                           f"see {rundir}")
    manifest = rundir / "frames_manifest.json"
    if not manifest.is_file():
        raise RuntimeError(f"capture produced no frames_manifest.json in {rundir}")
    return manifest


def _load_frames_manifest(path: str | Path) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("frames", data if isinstance(data, list) else [])


def _load_unreachable(frames_manifest: Path) -> list:
    """The un-approachable props recorded in the sibling journey_script.json (surfaced in the verdict)."""
    script = frames_manifest.parent / "journey_script.json"
    if script.is_file():
        try:
            return json.loads(script.read_text(encoding="utf-8")).get("unreachable", [])
        except (OSError, ValueError):
            return []
    return []


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────────
def _do_vqa_and_verdict(frames_manifest: Path, out: Path, model: str, timeout_s: int) -> int:
    questions = load_questions()
    frames = _load_frames_manifest(frames_manifest)
    scorer: FrameScorer = lambda p, q: _shell_scorer(p, q, model=model, timeout_s=timeout_s)
    verdict = build_verdict(run_vqa(frames, questions, scorer), _load_unreachable(frames_manifest))
    out.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    print(f"[journey_eval] {'PASS' if verdict['passed'] else 'FAIL'}: "
          f"{verdict['frames_checked']} frames, {verdict['frames_with_defects']} with defects, "
          f"{len(verdict['unreachable_props'])} unreachable props -> {out}")
    for off in verdict["defects"]:
        print(f"  DEFECT {off['step']}/{off['side']} {off['defects']} :: {off['frame']}")
    return 0 if verdict["passed"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    bs = sub.add_parser("build-script", help="derive the scripted path from a manifest (+ optional plan)")
    bs.add_argument("manifest")
    bs.add_argument("--plan", default=None, help="journey plan JSON (start/parley/door/combat cells)")
    bs.add_argument("-o", "--out", default=None)

    cap = sub.add_parser("capture", help="[box] boot the player + drive the script, writing frames")
    cap.add_argument("manifest")
    cap.add_argument("--plan", default=None)
    cap.add_argument("--campaign", required=True)
    cap.add_argument("--rundir", required=True)
    cap.add_argument("--owner", default="WorldOSPlayer")

    vq = sub.add_parser("vqa", help="run factual VQA over an already-captured frames_manifest.json")
    vq.add_argument("frames_manifest")
    vq.add_argument("-o", "--out", default="journey_verdict.json")
    vq.add_argument("--model", default="sonnet")
    vq.add_argument("--timeout", type=int, default=180)

    rn = sub.add_parser("run", help="[box] capture -> vqa -> verdict end to end")
    rn.add_argument("manifest")
    rn.add_argument("--plan", default=None)
    rn.add_argument("--campaign", required=True)
    rn.add_argument("--rundir", required=True)
    rn.add_argument("--owner", default="WorldOSPlayer")
    rn.add_argument("--model", default="sonnet")
    rn.add_argument("--timeout", type=int, default=180)

    args = ap.parse_args(argv)

    def _plan(p):
        return json.loads(Path(p).read_text(encoding="utf-8")) if p else None

    if args.cmd == "build-script":
        script = build_script(json.loads(Path(args.manifest).read_text()), _plan(args.plan))
        payload = json.dumps(script.as_dict(), indent=2)
        (Path(args.out).write_text(payload, encoding="utf-8") if args.out else print(payload))
        print(f"[journey_eval] {len(script.steps)} steps, {len(script.unreachable)} unreachable props",
              file=sys.stderr)
        return 0

    if args.cmd == "capture":
        script = build_script(json.loads(Path(args.manifest).read_text()), _plan(args.plan))
        mf = capture(script, Path(args.rundir), campaign=args.campaign, owner=args.owner)
        print(f"[journey_eval] frames manifest -> {mf}")
        return 0

    if args.cmd == "vqa":
        return _do_vqa_and_verdict(Path(args.frames_manifest), Path(args.out), args.model, args.timeout)

    # run
    script = build_script(json.loads(Path(args.manifest).read_text()), _plan(args.plan))
    mf = capture(script, Path(args.rundir), campaign=args.campaign, owner=args.owner)
    return _do_vqa_and_verdict(mf, Path(args.rundir) / "journey_verdict.json", args.model, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
