#!/usr/bin/env python3
"""Persist one two-pass Agent G4 playtest row in the scores ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))
import scores_db  # noqa: E402

def _count(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("counts must be non-negative")
    return number

def _frames_dir(value: str) -> str:
    if not (path := Path(value).expanduser()).is_dir(): raise argparse.ArgumentTypeError("--frames-dir must be an existing directory")
    return str(path)

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("p1", "p2", "p3"):
        p.add_argument(f"--{name}", required=True, type=_count)
    p.add_argument("--build", required=True)
    p.add_argument("--route-complete", required=True, type=int, choices=(0, 1))
    p.add_argument("--pass1", required=True, choices=("PASS", "FAIL"))
    p.add_argument("--pass2", required=True, choices=("PASS", "FAIL"))
    p.add_argument("--frames-dir", required=True, type=_frames_dir)
    p.add_argument("--persist", action="store_true")
    p.add_argument("--run-id", required=True)
    p.add_argument("--db", default=str(scores_db.DB_PATH), help=argparse.SUPPRESS)
    p.add_argument("--lenses", type=_count, default=0)
    p.add_argument("--reproductions", type=_count, default=0)
    p.add_argument("--legibility-median", type=float)
    p.add_argument("--actor-luminance-floor", type=float)
    p.add_argument("--frames-per-room", help="JSON map of room to frame count")
    return p

def row_from_args(args: argparse.Namespace) -> dict:
    row = {
        "surface": "agent_g4", "build_sha": args.build,
        "methodology": (f"agent-g4 pass=both build={args.build} lenses={args.lenses} "
                         f"reproductions={args.reproductions}"),
        "p1_count": args.p1, "p2_count": args.p2, "p3_count": args.p3,
        "route_completion": args.route_complete, "pass1_verdict": args.pass1,
        "pass2_verdict": args.pass2, "pass": int(args.p1 == 0 and args.pass1 == args.pass2 == "PASS"),
        "scorer_model": "derived", "source_path": str(Path(args.frames_dir).expanduser()),
        "notes": (f"Agent G4 two-pass playtest; P1={args.p1} P2={args.p2} P3={args.p3} "
                   f"route={args.route_complete} pass1={args.pass1} pass2={args.pass2}."),
    }
    for key in ("legibility_median", "actor_luminance_floor"):
        if getattr(args, key) is not None:
            row[key] = getattr(args, key)
    if args.frames_per_room:
        try:
            row["frames_per_room"] = json.loads(args.frames_per_room)
        except json.JSONDecodeError as exc:
            raise ValueError("--frames-per-room must be a JSON object") from exc
        if not isinstance(row["frames_per_room"], dict) or not all(isinstance(room, str) and room and isinstance(count, int) and not isinstance(count, bool) and count >= 0 for room, count in row["frames_per_room"].items()): raise ValueError("--frames-per-room must map rooms to non-negative integer frame counts")
    return row

def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    row = row_from_args(args)
    if args.persist:
        scores_db.add_run(args.run_id, db_path=args.db, **row)
    print(json.dumps({"run_id": args.run_id, "persisted": args.persist, "row": row}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
