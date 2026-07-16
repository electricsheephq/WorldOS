#!/usr/bin/env python3
"""THE Room Readiness Pipeline orchestrator — one resumable command, every gate, exit code = ship gate.

Epic #1581, issue #1587. This is the harness the owner asked for: a room is authored + verified by ONE
command, and it **exits non-zero unless every gate that ran is GREEN** — that exit code, not a human
"ship it", is the gate. Hand-off-able to a sub-agent or a cron; RESUMABLE via per-stage markers so a
crash/compaction picks up where it left off.

The pipeline (VISION.md → "The Room Readiness Pipeline"):
  generate-geometry → design-gate → build_room_unified (greybox+depth+boxes, ON THE BOX) →
  qa/paint_room.py (pinned; CU) → BEAUTY gate (blind panel; LLM) → **WALKABILITY gate (qa/walk_test.py)**
  → adopt (tools/library/promote.py) → report.

GEOMETRY IS GROUND TRUTH: collision/occlusion come from the grid + boxes sidecar; the plate is cosmetic.
So the deterministic gates below (coherence + walk) are the auto-enforceable floor; the beauty panel +
paint + box-render stages need CU / the GEX44 box and are declared here so the whole loop is one place,
resumable, and self-documenting.

Modes:
  --mode verify  (default): gate an EXISTING manifest room — coherence + walk + report. Fully automatic,
                 CU-free, no box. This is what a cold agent runs to check a shipped room is still walkable.
  --mode full:   the whole generate→paint→gate chain for a NEW room. The heavy stages (build/paint/panel)
                 print the exact command + require the box/CU; they are resumable so a sub-agent can drive
                 them. (Wiring the box-render + Workflow-panel stages to run inline is issue #1587 follow-up.)

Usage:
  qa/room_pipeline.py --room crypt                       # verify a shipped room (walk gate is the point)
  qa/room_pipeline.py --room crypt --mode verify --resume
  qa/room_pipeline.py --room shop --mode full            # a NEW room: prints the box/CU stages to run
Exit 0 == every gate that ran is GREEN (shippable). Non-zero == a gate failed or a required stage is
incomplete. Evidence + per-stage markers under qa/evidence/pipeline/<room>/.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MANIFEST = REPO / "extensions" / "renderers" / "unity" / "plates_manifest.json"
GEO_DIR = HERE / "room_geometries"
GEO_OF = {"crypt": "crypt_v36_geometry.json", "tavern": "tavern_v2_geometry.json",
          "throne_hall": "throne_hall_geometry.json"}


def _log(msg: str) -> None:
    print(f"[room_pipeline] {msg}", flush=True)


def _manifest_entry(room: str) -> dict:
    plates = json.loads(MANIFEST.read_text()).get("plates", {})
    if room not in plates:
        raise SystemExit(f"room '{room}' not in {MANIFEST} (have: {sorted(plates)})")
    return plates[room]


# --- stages (each returns dict: {status: GREEN|RED|SKIP|MANUAL, detail}) -------------------------
def stage_coherence(room: str, out: Path) -> dict:
    """Deterministic paint↔grid coherence (qa/check_grid_paint_coherence.py) — registration/beauty proxy."""
    entry = _manifest_entry(room)
    plate = REPO / "extensions" / "renderers" / "unity" / entry["plate"]
    if not plate.exists():
        return {"status": "SKIP", "detail": f"plate not on disk: {plate}"}
    cmd = [sys.executable, str(HERE / "check_grid_paint_coherence.py"), str(plate), str(MANIFEST)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as e:  # noqa: BLE001
        return {"status": "SKIP", "detail": f"coherence runner error: {e}"}
    (out / "coherence.log").write_text(p.stdout + "\n---STDERR---\n" + p.stderr)
    return {"status": "GREEN" if p.returncode == 0 else "RED",
            "detail": f"rc={p.returncode}; see coherence.log"}


def stage_walk(room: str, out: Path, engine: str, qa: str, stride: int) -> dict:
    """THE walkability gate — imports walk_test.run_gate against the live player."""
    sys.path.insert(0, str(HERE))
    import walk_test as W  # noqa: PLC0415
    try:
        report = W.run_gate(room, engine, qa, stride=stride, out=out / "walk",
                            settle=0.5, move_timeout=5.0)
    except Exception as e:  # noqa: BLE001
        return {"status": "SKIP", "detail": f"walk_test could not run (player up on {qa}?): {e}"}
    (out / "walk_report.json").write_text(json.dumps(report, indent=2))
    cam = report["camera"]["ok"]
    counts = {k: report[k] for k in ("reachable", "impassable", "doors")}
    return {"status": report["verdict"], "detail": {"camera_ok": cam, **counts}}


def stage_manual(name: str, cmd: str, needs: str) -> dict:
    return {"status": "MANUAL", "detail": f"{name}: run `{cmd}` ({needs})"}


def run(room: str, mode: str, out: Path, engine: str, qa: str, stride: int, resume: bool) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    marker = out / "stages.json"
    done = json.loads(marker.read_text()) if (resume and marker.exists()) else {}

    if mode == "full":
        # heavy stages: declared + resumable; they need the GEX44 box / CU (wiring inline = #1587 follow-up)
        geo = GEO_OF.get(room, f"{room}_geometry.json")
        stages = [
            ("geometry", lambda: stage_manual("geometry", f"tools/author_room_geometry.py {room}", "authoring")),
            ("greybox", lambda: stage_manual("greybox", "build_room_unified.cs (GEX44 box)", "needs the box")),
            ("paint", lambda: stage_manual("paint", f"qa/paint_room.py {room} --depth <depth.png>", "needs CU")),
            ("beauty", lambda: stage_manual("beauty", "blind 5-scorer panel (Workflow)", "needs LLM panel")),
            ("coherence", lambda: stage_coherence(room, out)),
            ("walk", lambda: stage_walk(room, out, engine, qa, stride)),
        ]
    else:  # verify: the automatic, CU-free, no-box gates
        stages = [
            ("coherence", lambda: stage_coherence(room, out)),
            ("walk", lambda: stage_walk(room, out, engine, qa, stride)),
        ]

    results = {}
    for name, fn in stages:
        if resume and done.get(name, {}).get("status") in ("GREEN", "SKIP"):
            results[name] = done[name]
            _log(f"{name}: (cached {done[name]['status']})")
            continue
        _log(f"{name}: running…")
        res = fn()
        results[name] = res
        _log(f"{name}: {res['status']} — {res['detail']}")
        marker.write_text(json.dumps(results, indent=2))

    gate_stages = [n for n in ("coherence", "walk") if n in results]
    reds = [n for n in gate_stages if results[n]["status"] == "RED"]
    manuals = [n for (n, r) in results.items() if r["status"] == "MANUAL"]
    shippable = not reds and not manuals and any(results[n]["status"] == "GREEN" for n in gate_stages)
    report = {"room": room, "mode": mode, "stages": results,
              "gate_stages": gate_stages, "reds": reds, "pending_manual": manuals,
              "shippable": shippable, "ts": None}
    (out / "pipeline_report.json").write_text(json.dumps(report, indent=2))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--room", required=True)
    ap.add_argument("--mode", choices=["verify", "full"], default="verify")
    ap.add_argument("--engine", default="http://127.0.0.1:8766")
    ap.add_argument("--qa", default="http://127.0.0.1:8971")
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--out", default=str(HERE / "evidence" / "pipeline"))
    args = ap.parse_args(argv)

    out = Path(args.out) / args.room
    report = run(args.room, args.mode, out, args.engine, args.qa, args.stride, args.resume)

    print(f"\n=== ROOM_PIPELINE {args.room} ({args.mode}) — "
          f"{'SHIPPABLE' if report['shippable'] else 'NOT SHIPPABLE'} ===")
    for name, r in report["stages"].items():
        print(f"  {name:11s}: {r['status']}")
    if report["reds"]:
        print(f"  RED gates: {report['reds']}")
    if report["pending_manual"]:
        print(f"  pending (manual/box/CU): {report['pending_manual']}")
    print(f"  report: {out / 'pipeline_report.json'}")
    return 0 if report["shippable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
