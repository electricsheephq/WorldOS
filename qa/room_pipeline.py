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
    cmd = [sys.executable, str(HERE / "check_grid_paint_coherence.py"), "check", str(plate), str(MANIFEST)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception as e:  # noqa: BLE001
        return {"status": "SKIP", "detail": f"coherence runner error: {e}"}
    (out / "coherence.log").write_text(p.stdout + "\n---STDERR---\n" + p.stderr)
    # rc 0 = coherent, 1 = ran + failed (RED), anything else (2 usage / missing greybox input) = the
    # gate could not RUN for this shipped room -> SKIP (best-effort), never a false RED that blocks ship.
    status = {0: "GREEN", 1: "RED"}.get(p.returncode, "SKIP")
    return {"status": status, "detail": f"rc={p.returncode}; see coherence.log"}


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
    # ERROR verdict = a harness/infrastructure defect (player/engine/debug/shot unreachable), NOT a
    # walkability verdict. Map it to SKIP (which already blocks shippable) so a broken harness never
    # reads as a RED room defect AND never certifies the room — it must be retried/investigated.
    if report["verdict"] == "ERROR":
        errs = report.get("harness_errors", [])
        first = "; ".join(errs[:3]) if errs else "(unspecified)"
        return {"status": "SKIP",
                "detail": f"harness error — retry/investigate (never a verdict): {first}"}
    cam = report["camera"]["ok"]
    counts = {k: report[k] for k in ("reachable", "impassable", "doors")}
    return {"status": report["verdict"], "detail": {"camera_ok": cam, **counts}}


def stage_manual(name: str, cmd: str, needs: str) -> dict:
    return {"status": "MANUAL", "detail": f"{name}: run `{cmd}` ({needs})"}


# --- room certification (sidecar round-3 adoption): "is this room walk-certified?" answerable cold --
CERT_DIR = HERE / "certifications"


def _sha256(path: Path) -> str:
    import hashlib  # noqa: PLC0415
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_certification(room: str, results: dict, out: Path) -> Path:
    """On a SHIPPABLE verdict, pin the exact artifacts the gates certified: shas of the plate, the
    boxes sidecar and the geometry, the manifest entry itself, and a pointer to the walk report.
    A later consumer (promote.py, CI, a cold agent) calls verify_certification — any sha drift means
    the certification is STALE and the room must re-gate. Compaction-proof by construction."""
    import walk_static as WS  # noqa: PLC0415
    sys.path.insert(0, str(HERE))
    entry = _manifest_entry(room)
    cert = {"room": room, "verdicts": {k: v["status"] for k, v in results.items()},
            "manifest_entry": entry, "artifacts": {}, "walk_report": str(out / "walk_report.json")}
    plate = REPO / "extensions" / "renderers" / "unity" / entry.get("plate", "")
    if plate.is_file():
        cert["artifacts"]["plate_sha256"] = _sha256(plate)
    boxes = entry.get("boxes")
    if boxes and (REPO / "extensions" / "renderers" / "unity" / boxes).is_file():
        cert["artifacts"]["boxes_sha256"] = _sha256(REPO / "extensions" / "renderers" / "unity" / boxes)
    geof = WS.GEOMETRY_OF.get(room)
    if geof and (GEO_DIR / geof).is_file():
        cert["artifacts"]["geometry_sha256"] = _sha256(GEO_DIR / geof)
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    path = CERT_DIR / f"{room}.json"
    path.write_text(json.dumps(cert, indent=2) + "\n")
    return path


def verify_certification(room: str) -> list:
    """Return failure strings; EMPTY == the room's certification exists and every pinned sha still
    matches the artifacts on disk (nothing changed since the gates ran)."""
    import walk_static as WS  # noqa: PLC0415
    path = CERT_DIR / f"{room}.json"
    if not path.exists():
        return [f"{room}: NOT CERTIFIED (no {path.name}) — run room_pipeline to walk-certify"]
    cert = json.loads(path.read_text())
    fails = []
    entry = _manifest_entry(room)
    if entry != cert.get("manifest_entry"):
        fails.append(f"{room}: manifest entry changed since certification — re-gate")
    unity = REPO / "extensions" / "renderers" / "unity"
    checks = [("plate_sha256", unity / entry.get("plate", ""))]
    if entry.get("boxes"):
        checks.append(("boxes_sha256", unity / entry["boxes"]))
    geof = WS.GEOMETRY_OF.get(room)
    if geof:
        checks.append(("geometry_sha256", GEO_DIR / geof))
    for key, p in checks:
        pinned = cert.get("artifacts", {}).get(key)
        if pinned and p.is_file() and _sha256(p) != pinned:
            fails.append(f"{room}: {key} drifted since certification ({p.name} changed) — re-gate")
    return fails


def _is_shippable(results: dict, gate_stages: list) -> bool:
    """Pure ship gate. A room ships ONLY when: no RED gate, no pending MANUAL stage, at least one
    GREEN gate, AND — whenever a walk gate ran — its verdict is GREEN. A walk=SKIP (harness ERROR /
    player down) or coherence-alone GREEN must NEVER certify: a coherent paint says nothing about
    whether the room is walkable, which is the entire point of the walk gate."""
    reds = [n for n in gate_stages if results[n]["status"] == "RED"]
    manuals = [n for (n, r) in results.items() if r["status"] == "MANUAL"]
    walk_ok = ("walk" not in gate_stages) or (results.get("walk", {}).get("status") == "GREEN")
    return bool(not reds and not manuals and walk_ok
                and any(results[n]["status"] == "GREEN" for n in gate_stages))


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
    else:  # verify: the automatic, CU-free, no-box WALKABILITY gate (the point of verify mode)
        # coherence is an AUTHORING-time gate — it regenerates a greybox from a room GEOMETRY manifest;
        # the runtime plate registry doesn't carry that geometry, so coherence belongs in `full` mode
        # (where the room manifest exists), not in verify. Verify gates purely on walkability.
        stages = [
            ("walk", lambda: stage_walk(room, out, engine, qa, stride)),
        ]

    results = {}
    for name, fn in stages:
        # Only a cached GREEN is terminal on --resume. A cached SKIP must RE-RUN: an ERROR-mapped walk
        # SKIP is a harness error to retry, and re-running a deliberate MANUAL/SKIP stage is cheap and
        # safe. (Reusing SKIP as terminal could carry a harness outage forward as if it were settled.)
        if resume and done.get(name, {}).get("status") == "GREEN":
            results[name] = done[name]
            _log(f"{name}: (cached GREEN)")
            continue
        _log(f"{name}: running…")
        res = fn()
        results[name] = res
        _log(f"{name}: {res['status']} — {res['detail']}")
        marker.write_text(json.dumps(results, indent=2))

    gate_stages = [n for n in ("coherence", "walk") if n in results]
    reds = [n for n in gate_stages if results[n]["status"] == "RED"]
    manuals = [n for (n, r) in results.items() if r["status"] == "MANUAL"]
    shippable = _is_shippable(results, gate_stages)
    report = {"room": room, "mode": mode, "stages": results,
              "gate_stages": gate_stages, "reds": reds, "pending_manual": manuals,
              "shippable": shippable, "ts": None}
    if shippable:
        try:
            report["certification"] = str(write_certification(room, results, out))
            _log(f"certified -> {report['certification']}")
        except Exception as e:  # noqa: BLE001
            _log(f"certification write failed (non-fatal): {e}")
    # Surface the walk verdict in the scores ledger (latest-per-room; honest on RED too). Only a
    # DECIDED walk stage stamps — a MANUAL/pending stage records nothing.
    walk_status = results.get("walk", {}).get("status")
    if walk_status in ("GREEN", "RED"):
        try:
            import scores_db  # noqa: PLC0415
            sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, timeout=10).stdout.strip() or None
            scores_db.record_room_walk(
                room, walk_status, sha=sha,
                walk_report_path=str(out / "walk_report.json"),
                source_path=report.get("certification"),
                notes=f"room_pipeline --mode {mode}")
            _log(f"walk ledger: room:{room} = {walk_status}")
        except Exception as e:  # noqa: BLE001
            _log(f"walk ledger stamp failed (non-fatal): {e}")
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
    ap.add_argument("--check-cert", action="store_true",
                    help="only verify the room's certification (sha freshness); exit non-zero on stale/missing")
    ap.add_argument("--out", default=str(HERE / "evidence" / "pipeline"))
    args = ap.parse_args(argv)

    if args.check_cert:
        fails = verify_certification(args.room)
        print(f"[room_pipeline] certification {args.room}: "
              + ("FRESH" if not fails else "STALE/MISSING"))
        for f in fails:
            print(f"  - {f}")
        return 0 if not fails else 1

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
