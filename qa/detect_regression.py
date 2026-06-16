#!/usr/bin/env python3
"""Is this candidate run BETTER or WORSE than the comparable baseline? — the QA harness's
machine-readable regression signal for the implementing agent.

The agent asks one question after a scored run: *did I regress quality vs the last known-good build,
or is this within scorer noise?* This tool answers it WITHOUT the agent spelunking the ledger by hand:

  1. Build the candidate's comparability key (surface, dm_model, methodology, lens_config_version).
  2. Find the single canonical GREEN baseline for THAT EXACT key (``scores_db.get_canonical_baseline``).
     The key includes ``lens_config_version`` so the comparison is ruler-fenced — a candidate scored
     under a different lens ruler has NO comparable baseline (we refuse rather than emit a false
     REGRESSED; cross-ruler deltas are not apples-to-apples, per qa/REGRESSION-FORENSICS.md).
  3. Per lens (story/mech/angry), classify ``candidate - baseline`` against the published per-lens
     NOISE FLOOR (qa/lens_noise_floor.py): beyond +floor = IMPROVED, beyond -floor = REGRESSED, else
     WITHIN_NOISE. A behavioral GREEN→RED flip is a regression regardless of lens deltas.

Verdict (overall): REGRESSED if any lens regressed OR behavioral flipped GREEN→RED; else IMPROVED if any
lens improved and none regressed; else WITHIN_NOISE; NO_BASELINE / NO_DATA when there is nothing to compare.

Usage:
    python qa/detect_regression.py --candidate <run_id> [--db qa/scores.db] [--json]
    python qa/detect_regression.py --candidate-json '<json>'|@file.json [--db ...] [--json]

Exit codes (so CI / the agent can gate): 0 = IMPROVED/WITHIN_NOISE, 2 = REGRESSED, 3 = NO_BASELINE/NO_DATA.
This is a pure reader — it never writes state, scores a transcript, or runs a game.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import scores_db  # noqa: E402
from lens_noise_floor import LENS_COLUMNS, NOISE_FLOOR, classify_delta, delta_floor  # noqa: E402

# Verdict -> process exit code. NO_BASELINE/NO_DATA are advisory (no signal), distinct from a regression.
_EXIT = {"IMPROVED": 0, "WITHIN_NOISE": 0, "REGRESSED": 2, "NO_BASELINE": 3, "NO_DATA": 3}


def _key_of(candidate: dict) -> dict:
    """The comparability key (surface, dm_model, methodology, lens_config_version) from a candidate."""
    return {c: candidate.get(c) for c in scores_db._BASELINE_KEY}


def detect_regression(candidate: dict, db_path: Path | str = scores_db.DB_PATH) -> dict:
    """Compare ``candidate`` (a run dict: comparability key + lens overalls + behavioral) against the
    canonical GREEN baseline for its key. Returns a machine-readable verdict dict."""
    key = _key_of(candidate)
    baseline = scores_db.get_canonical_baseline(db_path=db_path, **key)

    result: dict = {
        "candidate_run": candidate.get("run_id"),
        "baseline_run": baseline.get("run_id") if baseline else None,
        "comparability_key": key,
        "per_lens": [],
        "behavioral": {
            "candidate": candidate.get("behavioral"),
            "baseline": baseline.get("behavioral") if baseline else None,
            "regressed": False,
        },
    }

    if baseline is None:
        result["verdict"] = "NO_BASELINE"
        result["message"] = (
            "No canonical GREEN baseline for this comparability key "
            f"({key}). Set one with: scores_db.set_canonical_baseline(<run_id>) "
            "after a trusted GREEN run, then re-run. Cross-ruler comparison is intentionally refused."
        )
        return result

    # Per-lens deltas vs the noise floor.
    classes: list[str] = []
    for col in LENS_COLUMNS:
        cand_v = candidate.get(col)
        base_v = baseline.get(col)
        if cand_v is None or base_v is None:
            result["per_lens"].append(
                {"lens": col, "label": NOISE_FLOOR[col]["label"], "candidate": cand_v,
                 "baseline": base_v, "delta": None, "floor": delta_floor(col), "classification": "NO_DATA"}
            )
            continue
        delta = round(float(cand_v) - float(base_v), 4)
        cls = classify_delta(col, delta)
        classes.append(cls)
        result["per_lens"].append(
            {"lens": col, "label": NOISE_FLOOR[col]["label"], "candidate": cand_v, "baseline": base_v,
             "delta": delta, "floor": delta_floor(col), "classification": cls}
        )

    # A behavioral GREEN -> RED flip is a regression regardless of lens deltas (the deterministic gate
    # caps quality anyway; surface it explicitly so the agent sees the real cause).
    behavioral_regressed = (
        str(candidate.get("behavioral") or "").upper() == "RED"
        and str(baseline.get("behavioral") or "").upper() == "GREEN"
    )
    result["behavioral"]["regressed"] = behavioral_regressed

    if behavioral_regressed or "REGRESSED" in classes:
        verdict = "REGRESSED"
    elif "IMPROVED" in classes:
        verdict = "IMPROVED"
    elif classes:
        verdict = "WITHIN_NOISE"
    else:
        verdict = "NO_DATA"
    result["verdict"] = verdict
    result["message"] = _summary(result)
    return result


def _summary(result: dict) -> str:
    lines = [f"{result['verdict']}: candidate {result.get('candidate_run')} vs baseline {result['baseline_run']}"]
    for p in result["per_lens"]:
        if p["delta"] is None:
            lines.append(f"  {p['label']:24s} no-data (cand={p['candidate']} base={p['baseline']})")
        else:
            sign = "+" if p["delta"] >= 0 else ""
            lines.append(
                f"  {p['label']:24s} {p['baseline']} -> {p['candidate']} "
                f"({sign}{p['delta']}, floor ±{p['floor']}) {p['classification']}"
            )
    if result["behavioral"]["regressed"]:
        lines.append("  behavioral: GREEN -> RED  (regression)")
    return "\n".join(lines)


def _load_candidate(args) -> dict:
    if args.candidate_json:
        raw = args.candidate_json
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text()
        return json.loads(raw)
    # --candidate <run_id>: look the row up in the ledger.
    rows = {r["run_id"]: r for r in scores_db.fetch_rows(db_path=args.db)}
    row = rows.get(args.candidate)
    if row is None:
        raise SystemExit(f"detect_regression: no run '{args.candidate}' in {args.db}")
    return row


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--candidate", help="run_id of the candidate run (looked up in --db)")
    src.add_argument("--candidate-json", help="candidate run as JSON, or @path to a JSON file")
    p.add_argument("--db", default=str(scores_db.DB_PATH), help="path to scores.db")
    p.add_argument("--json", action="store_true", help="emit the machine-readable verdict as JSON")
    args = p.parse_args(argv)

    candidate = _load_candidate(args)
    result = detect_regression(candidate, db_path=args.db)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result.get("message", result["verdict"]))
    return _EXIT.get(result["verdict"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
