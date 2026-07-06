#!/usr/bin/env python3
"""HV4 (Act II §4c, #1326) — the library-reuse A/B harness.

Runs LIBRARY-FIRST vs PURE-GEN on the SAME seed/world and diffs three things the epic's EVAL gate
cares about:

  (a) HOOKS PRODUCED — how many quest_hooks each arm seeds, and how many are library-sourced.
  (b) ENGAGEMENT — whether library content is ENGAGED (not decorative), via
      qa/feature_engagement.engagement_coverage (the `library_reuse` SystemSpec HV4 added).
  (c) COST — latency + token deltas between the arms.

TWO LAYERS, one contract:

  * ``seed_diff`` (DETERMINISTIC, offline, $0) — seeds the world twice (with and without
    ``library_packs``) and diffs the generated hook graph. This is the fast inner-loop signal and
    the part the tests exercise. It needs NO LLM.

  * ``duo_ab`` (the SCORED arm — NOT run here) — the scaffold that shells out to ``qa/run_duo.sh``
    for each arm with a shared (world, model, ruler, seed) and reads back the per-lens scores +
    token/latency, then applies the EVAL gate:

        PASS = each lens's library-first score is within the documented noise floor
               (qa/lens_noise_floor.py) of pure-gen, AND token OR latency drops by >= the target %
               — else iterate on candidate selection before merging.

    Per the dispatch packet, this module WIRES that verdict but DOES NOT execute a scored duo — the
    orchestrator runs the measurement pass. ``duo_ab(..., execute=False)`` (the default) returns the
    fully-formed plan (both arms' commands + the gate spec) WITHOUT spawning any claude -p session.

USAGE
    # deterministic seed-level diff (safe, offline):
    uv run --directory servers/engine python ../../qa/library_reuse_ab.py \
        --world baldurs-gate --pack worldos-harvest --seed rri-a1

    # print the scored-duo PLAN (does NOT run it):
    uv run --directory servers/engine python ../../qa/library_reuse_ab.py --world baldurs-gate \
        --pack worldos-harvest --plan-duo
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import time
from pathlib import Path
from typing import Optional

# The engine modules live in servers/engine; add it so this qa-side harness can seed a world the
# same way the server does (mirrors the sys.path.insert idiom the sibling qa tests use).
_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent
_ENGINE_DIR = _REPO_ROOT / "servers" / "engine"
sys.path.insert(0, str(_ENGINE_DIR))
sys.path.insert(0, str(_QA_DIR))

# Import the noise-floor + engagement scorer up front (they are pure; no LLM). The engine imports
# are deferred into seed_diff so `--plan-duo` works even in an environment without the engine deps.
import lens_noise_floor  # noqa: E402

# EVAL gate: the minimum token OR latency reduction library-first must show to PASS (epic addendum
# [HIGH]). A placeholder default the orchestrator overrides at measurement time; documented, not
# guessed-in-code as final.
DEFAULT_COST_REDUCTION_TARGET_PCT = 10.0


# ── (a)+(b) the deterministic seed-level diff (offline, $0) ──────────────────────────────────────

def _hook_summary(hooks: list) -> dict:
    """Bucket a seeded hook list by provenance — the (a) HOOKS PRODUCED signal."""
    total = len(hooks)
    library = [h for h in hooks if getattr(h, "source", "") == "library"]
    return {
        "total": total,
        "library_sourced": len(library),
        "native": total - len(library),
        "library_titles": [getattr(h, "grievance", "") or getattr(h, "title", "") for h in library],
        "library_tiers": sorted({getattr(h, "tier", "") for h in library if getattr(h, "tier", "")}),
    }


def _campaign_state(c) -> dict:
    """The engine snapshot dict the engagement scorer reads (a full model_dump)."""
    return json.loads(c.model_dump_json())


def seed_diff(world_id: str, pack: str, *, seed: str = "ab", session_beats: int = 12,
              library_root: Optional[str] = None) -> dict:
    """Seed ``world_id`` twice — PURE-GEN (no library_packs) and LIBRARY-FIRST (opted into ``pack``)
    — off the SAME rng seed, and diff the hook graph + engagement. Fully deterministic + offline.

    Returns a report dict with per-arm hook summaries, the engagement-coverage block for each arm,
    and the hook/engagement DELTA. ``session_beats`` is the (simulated) run length fed to the
    engagement scorer so its beats-keyed preconditions are OWED (a seed-only diff carries no
    transcript beats). ``library_root`` overrides the on-disk library dir (tests point at a tmp
    pack; production reads the repo-root library/)."""
    import content  # deferred: keeps --plan-duo import-clean without engine deps
    import questgen

    base_world = content.load_world_data(world_id)

    def _arm(opt_in: bool):
        w = copy.deepcopy(base_world)
        if opt_in:
            w["library_packs"] = [pack]
            if library_root is not None:
                w["_library_root"] = library_root
        else:
            w.pop("library_packs", None)  # PURE-GEN: dormant reuse surface
        c = content.seed_world(w)
        # Re-run questgen off a FIXED seed so the two arms differ ONLY by the library source, not by
        # rng stream (seed_world already ran questgen off the campaign id; this pins comparability).
        questgen.generate(c, w, random.Random(seed))
        state = _campaign_state(c)
        return c, state

    _pure_c, pure_state = _arm(False)
    _lib_c, lib_state = _arm(True)

    import feature_engagement as fe  # pure; deferred only to share the engine sys.path setup
    pure_cov = fe.engagement_coverage(pure_state, tool_counts={}, session_beats=session_beats)
    lib_cov = fe.engagement_coverage(lib_state, tool_counts={}, session_beats=session_beats)

    pure_hooks = _hook_summary(_pure_c.quest_hooks)
    lib_hooks = _hook_summary(_lib_c.quest_hooks)
    return {
        "world_id": world_id,
        "pack": pack,
        "seed": seed,
        "arms": {
            "pure_gen": {"hooks": pure_hooks, "engagement": pure_cov},
            "library_first": {"hooks": lib_hooks, "engagement": lib_cov},
        },
        "delta": {
            "library_sourced_hooks": lib_hooks["library_sourced"],
            "total_hooks": lib_hooks["total"] - pure_hooks["total"],
            # library content is ENGAGED (not decorative) when the library_reuse system is engaged
            # in the library-first arm (and, correctly, N/A in the pure-gen arm).
            "library_engaged": "library_reuse" in lib_cov["engaged"],
            "library_dormant_in_pure_gen": "library_reuse" in lib_cov["engaged"]
            and "library_reuse" not in pure_cov["engaged"],
        },
    }


# ── (c) the scored-duo PLAN (NOT executed here — the orchestrator's measurement pass) ────────────

def _arm_cmd(run_id: str, world_id: str, persona: str, beats: int, *, library_first: bool) -> dict:
    """One arm's run_duo.sh invocation + the env that turns library reuse on/off. Library-first sets
    WORLDOS_LIBRARY_PACKS so the DM's world opts in; pure-gen leaves it empty (dormant)."""
    return {
        "arm": "library_first" if library_first else "pure_gen",
        "cmd": ["qa/run_duo.sh", run_id, world_id, persona, str(beats)],
        "env": {"WORLDOS_LIBRARY_PACKS": "worldos-harvest" if library_first else ""},
    }


def duo_ab(world_id: str, *, persona: str = "skeptic", beats: int = 8, pack: str = "worldos-harvest",
           cost_target_pct: float = DEFAULT_COST_REDUCTION_TARGET_PCT,
           execute: bool = False) -> dict:
    """Build the SCORED A/B plan (both arms + the EVAL gate). With ``execute=False`` (the default)
    this NEVER spawns a run — it returns the plan for the orchestrator to run and score. ``execute``
    is a deliberate future hook; it raises here so a stray call can't silently burn a scored duo in
    this build (the measurement pass is the orchestrator's, per the dispatch packet)."""
    if execute:
        raise NotImplementedError(
            "duo_ab(execute=True) is intentionally not wired in this PR — the scored A/B "
            "measurement pass is the orchestrator's (dispatch packet). Run the two arms via the "
            "returned plan and score with qa/run_duo.sh + the lens DB.")
    seed = f"ab-{world_id}"
    return {
        "world_id": world_id,
        "pack": pack,
        "arms": [
            _arm_cmd(f"{seed}-pure", world_id, persona, beats, library_first=False),
            _arm_cmd(f"{seed}-lib", world_id, persona, beats, library_first=True),
        ],
        "gate": {
            "lens_parity": {
                "rule": "each lens's library-first score within the noise floor of pure-gen",
                "noise_floor": {c: lens_noise_floor.delta_floor(c)
                                for c in lens_noise_floor.LENS_COLUMNS},
            },
            "cost_reduction": {
                "rule": "token OR latency drops by >= target_pct vs pure-gen",
                "target_pct": cost_target_pct,
            },
            "engagement": {
                "rule": "qa/feature_engagement library_reuse is ENGAGED in the library-first arm",
            },
            "verdict": "PASS iff lens_parity AND cost_reduction AND engagement all hold; "
                       "else ITERATE on candidate selection before merging.",
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default="baldurs-gate", help="world id to seed both arms from")
    ap.add_argument("--pack", default="worldos-harvest", help="library pack the library-first arm opts into")
    ap.add_argument("--seed", default="ab", help="rng seed pinned across both arms (comparability)")
    ap.add_argument("--beats", type=int, default=12, help="simulated run length for the engagement scorer")
    ap.add_argument("--library-root", default=None, help="override the on-disk library dir (tests)")
    ap.add_argument("--plan-duo", action="store_true",
                    help="print the scored-duo PLAN (does NOT run it) and exit")
    args = ap.parse_args(argv)

    if args.plan_duo:
        print(json.dumps(duo_ab(args.world, pack=args.pack), indent=2, ensure_ascii=False))
        return 0

    t0 = time.time()
    report = seed_diff(args.world, args.pack, seed=args.seed, session_beats=args.beats,
                       library_root=args.library_root)
    report["seed_diff_wall_s"] = round(time.time() - t0, 4)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
