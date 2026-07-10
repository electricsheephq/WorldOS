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
        if not (q.get("flag") and q.get("text") and q.get("applies_to") in ("all", "transition")):
            raise ValueError(f"malformed question entry: {q}")
    return qs


def questions_for_frame(questions: list, is_transition: bool) -> list:
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


def build_script(manifest: dict, plan: Optional[dict] = None) -> list:
    """Derive the journey steps from the room manifest (+ an optional plan carrying the semantic
    waypoints a manifest can't hold: start cell, parley/door/combat cells). One prop_approach step per
    impassable prop, then the configured transitions. Returns a list of Step."""
    plan = plan or {}
    grid = manifest.get("grid", {})
    cols, rows = int(grid.get("cols", 0)), int(grid.get("rows", 0))
    props = manifest.get("props", [])
    prop_cells = {(int(c), int(r)) for p in props for (c, r) in p.get("cells", [])}

    steps: list = []
    start = plan.get("start_cell")
    if start:
        steps.append(Step("start", "start", tuple(start), note="establishing frame"))
    for p in props:
        cells = [(int(c), int(r)) for (c, r) in p.get("cells", [])]
        if not cells:
            continue
        adj = _adjacent_walkable(cells, prop_cells, cols, rows)
        if adj is None:
            continue
        pid = str(p.get("id", "prop"))
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
    return steps


# ── Phase 3: VQA over frames (PURE core + injectable scorer) ─────────────────────────────────────────
# A frame record (from journey_capture.js's frames_manifest.json):
#   {"path": "...png", "step": "approach_sarcophagus", "kind": "prop_approach",
#    "side": "step"|"pre"|"post", "transition": bool}
FrameScorer = Callable[[str, list], dict]  # (image_path, questions) -> {flag: bool}


def _shell_scorer(image_path: str, questions: list, *, model: str = "sonnet",
                  timeout_s: int = 180) -> dict:
    """Default scorer: qa/vqa_frame.sh runs one sonnet `claude -p` pass over the image, returning
    {"flags": {flag: bool, ...}}. Mirrors score.sh's auth-isolation (fresh config dir + keychain
    token + GLM-neutralised env)."""
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
        return {k: bool(v) for k, v in json.loads(proc.stdout).get("flags", {}).items()}
    except (ValueError, AttributeError) as exc:
        raise RuntimeError(f"vqa_frame.sh emitted non-JSON for {image_path}: {exc}") from exc


def _sh_env(model: str, timeout_s: int) -> dict:
    import os
    return {**os.environ, "WORLDOS_VQA_MODEL": model, "WORLDOS_VQA_TIMEOUT": str(timeout_s)}


def run_vqa(frames: list, questions: list, scorer: FrameScorer) -> list:
    """Ask the applicable questions of every frame. Returns per-frame results:
    {frame, step, side, flags:{flag:bool}, defects:[flag,...]}. The scorer is injected so the
    aggregation is unit-testable with a stub (no LLM / no box)."""
    results: list = []
    for fr in frames:
        applicable = questions_for_frame(questions, bool(fr.get("transition")))
        flags = scorer(fr["path"], applicable)
        defects = sorted(k for k, v in flags.items() if v)
        results.append({"frame": fr["path"], "step": fr.get("step"), "side": fr.get("side", "step"),
                        "flags": flags, "defects": defects})
    return results


def build_verdict(vqa_results: list) -> dict:
    """ANY yes on ANY frame == journey FAIL, naming the offending frame(s) + flags. A clean journey is
    passed=True with an empty defects list."""
    offenders = [{"frame": r["frame"], "step": r["step"], "side": r["side"], "defects": r["defects"]}
                 for r in vqa_results if r["defects"]]
    return {
        "passed": not offenders,
        "frames_checked": len(vqa_results),
        "frames_with_defects": len(offenders),
        "defects": offenders,
        "per_frame": vqa_results,
    }


# ── Phase 2: box capture (thin shell over lib_native_player_boot.sh + journey_capture.js) ───────────
def capture(script_steps: list, rundir: Path, *, campaign: str, owner: str = "WorldOSPlayer") -> Path:
    """Drive the box player over the scripted path and write frames + a frames_manifest.json. Requires
    the box player env (Screen Recording + Accessibility grants + WorldOSPlayer.app), exactly as
    qa/player_smoke.sh — this is the box-only phase (run when the #1386 claim frees)."""
    rundir.mkdir(parents=True, exist_ok=True)
    script_path = rundir / "journey_script.json"
    script_path.write_text(json.dumps({"campaign": campaign,
                                       "steps": [s.as_dict() for s in script_steps]}, indent=2),
                           encoding="utf-8")
    cmd = ["node", str(_CAPTURE_JS), "--script", str(script_path), "--rundir", str(rundir),
           "--owner", owner]
    proc = subprocess.run(cmd, cwd=str(_ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"journey_capture.js failed (rc={proc.returncode}) — see {rundir}")
    manifest = rundir / "frames_manifest.json"
    if not manifest.is_file():
        raise RuntimeError(f"capture produced no frames_manifest.json in {rundir}")
    return manifest


def _load_frames_manifest(path: str | Path) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("frames", data if isinstance(data, list) else [])


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────────
def _do_vqa_and_verdict(frames_manifest: Path, out: Path, model: str, timeout_s: int) -> int:
    questions = load_questions()
    frames = _load_frames_manifest(frames_manifest)
    scorer: FrameScorer = lambda p, q: _shell_scorer(p, q, model=model, timeout_s=timeout_s)
    verdict = build_verdict(run_vqa(frames, questions, scorer))
    out.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    print(f"[journey_eval] {'PASS' if verdict['passed'] else 'FAIL'}: "
          f"{verdict['frames_checked']} frames, {verdict['frames_with_defects']} with defects -> {out}")
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
        steps = build_script(json.loads(Path(args.manifest).read_text()), _plan(args.plan))
        payload = json.dumps({"steps": [s.as_dict() for s in steps]}, indent=2)
        (Path(args.out).write_text(payload, encoding="utf-8") if args.out else print(payload))
        print(f"[journey_eval] {len(steps)} steps", file=sys.stderr)
        return 0

    if args.cmd == "capture":
        steps = build_script(json.loads(Path(args.manifest).read_text()), _plan(args.plan))
        mf = capture(steps, Path(args.rundir), campaign=args.campaign, owner=args.owner)
        print(f"[journey_eval] frames manifest -> {mf}")
        return 0

    if args.cmd == "vqa":
        return _do_vqa_and_verdict(Path(args.frames_manifest), Path(args.out), args.model, args.timeout)

    # run
    steps = build_script(json.loads(Path(args.manifest).read_text()), _plan(args.plan))
    mf = capture(steps, Path(args.rundir), campaign=args.campaign, owner=args.owner)
    return _do_vqa_and_verdict(mf, Path(args.rundir) / "journey_verdict.json", args.model, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
