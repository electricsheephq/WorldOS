#!/usr/bin/env python3
"""M3 build-loop — the orchestrator that ties the steps together with the gate ENFORCED (#449-#452).

This is the unattended build-loop driver. For a game seed it runs:
    1. generate_profile  (#449/#450)  seed -> render-profile (+ stamped ai_disclosure, art scopes)
    2. gate              (#452)        REJECT unless all required objective gates pass
    3. emit_glue         (#451)        on accept, emit the per-game thin-client entry page

The contract is FROZEN: the loop validates against the schema and routes any unmappable field
(plus art-taste / story / rights) to the human-gate queue. It never edits the schema and never
ships a profile that fails the objective gate. ~70-80% of scaffolding is unattended; the
subjective + contract decisions are surfaced for humans.

Usage:
    python3 run_loop.py <seed.json> --outdir <dir> [--model M] [--date ISO] [--render-base PATH]
    # writes <outdir>/<game_id>.profile.json + <outdir>/<game_id>.index.html on accept;
    # writes <outdir>/<game_id>.human-gate.json always. Exit 0 iff accepted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from generate_profile import generate_profile, strip_unmapped  # noqa: E402
from gate import run_gate, _DEFAULT_SCHEMA  # noqa: E402
from emit_glue import emit_glue  # noqa: E402


def build_one(seed: dict, schema: dict, *, model: str = "worldos-ai-build-loop",
              date: str = "", render_base: str = "/openworlds/render") -> dict:
    """Run the full loop for one seed. Returns a result dict (no filesystem side effects)."""
    profile_with_anno = generate_profile(seed, model=model, date=date)
    report = run_gate(profile_with_anno, schema)
    clean = strip_unmapped(profile_with_anno)
    result = {
        "game_id": clean.get("game_id"),
        "accepted": report["accepted"],
        "report": report,
        "profile": clean if report["accepted"] else None,
        "glue": emit_glue(clean, render_base=render_base) if report["accepted"] else None,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the M3 gated AI build-loop for a game seed.")
    ap.add_argument("seed")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--schema", default=str(_DEFAULT_SCHEMA))
    ap.add_argument("--model", default="worldos-ai-build-loop")
    ap.add_argument("--date", default="")
    ap.add_argument("--render-base", default="/openworlds/render")
    args = ap.parse_args(argv)

    seed = json.loads(Path(args.seed).read_text())
    schema = json.loads(Path(args.schema).read_text())
    res = build_one(seed, schema, model=args.model, date=args.date, render_base=args.render_base)

    gid = res["game_id"]
    print(f"game_id={gid}  accepted={res['accepted']}")
    for name, g in res["report"]["gates"].items():
        mark = {True: "PASS", False: "FAIL", None: "SKIP"}[g["passed"]]
        print(f"  [{mark}] {name}: {g['detail']}")
    print(f"  human-gate queue: {len(res['report']['human_gate_queue'])} item(s)")

    if args.outdir:
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{gid}.human-gate.json").write_text(
            json.dumps(res["report"]["human_gate_queue"], indent=2) + "\n")
        if res["accepted"]:
            (outdir / f"{gid}.profile.json").write_text(json.dumps(res["profile"], indent=2) + "\n")
            (outdir / f"{gid}.index.html").write_text(res["glue"])
            print(f"  wrote {gid}.profile.json + {gid}.index.html + {gid}.human-gate.json -> {outdir}")
        else:
            print(f"  REJECTED — wrote only {gid}.human-gate.json -> {outdir}")
    return 0 if res["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
