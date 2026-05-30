#!/usr/bin/env python3
"""WorldOS Release Readiness Index (RRI 0-10) — roll up existing QA artifacts into ONE
release number with a HARD-GATE FLOOR (a failed gate caps the score; it is NOT a soft
average that hides a zero — mirrors the SCORECARD RED-cap discipline).

Pure reader of on-disk artifacts (never the live HTTP channel, which can corrupt):
  - <run>/run.json            (ui_playtest_app.sh)  -> part_a native #356 gate
  - <run>/score.json          (ui_playtest_score.py) -> intro flow, satisfaction, bugs, image_404s
  - <run>/network.ndjson      (palette/playwright)  -> image-render rate (img 200 vs 404)
  - <story.json>/<mech.json>  (score.sh + rubrics)  -> story-craft / mechanical 1-5
  - --behavioral GREEN|RED    (assert_behavioral.py exit)
  - --ui-audit PASS|FAIL      (ui_audit_health.sh exit)
  - --palette-live true|false (a clean /session-surface read done by the CALLER, not here)

The two NEW signals the plan calls for — image-render-rate and palette-live — are computed
here (image rate from score.json/network.ndjson) and passed in (palette-live), so this stays
a pure disk reader.

Usage:
  release_readiness.py --runs <run-dir>[,<run-dir>...] \
      [--story story.json] [--mech mech.json] \
      [--behavioral GREEN|RED] [--ui-audit PASS|FAIL] [--palette-live true|false] \
      [--build-sha SHA] [--out qa/RRI.json] [--scorecard-row]

Targets for 10/10 (each dimension is a gate; all must hold on ONE build):
  native gate PASS · arc completed · cross-persona satisfaction >=7 & no give-up ·
  0 critical bugs · story >=4.3 · mech >=4.5 · behavioral GREEN · ui-audit PASS ·
  image-render >=95% · palette-live true
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def read_json(path: Path) -> dict:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def read_ndjson(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path or not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def image_render_rate(run: Path, score: dict) -> tuple[float, int, int]:
    """Fraction of image requests that returned bytes (not 404). Prefers network.ndjson
    (200 vs 404 image responses); falls back to score.json's image_404s with an unknown
    denominator (then rate is reported as 1.0 only if 0 404s, else conservative)."""
    net = read_ndjson(run / "network.ndjson")
    img = [n for n in net if "/image" in str(n.get("url", ""))]
    if img:
        ok = sum(1 for n in img if int(n.get("status", 0) or 0) and int(n.get("status")) < 400)
        total = len(img)
        return (ok / total if total else 1.0), ok, total
    # fallback: score.json carries image_404s but not the success count
    f404 = int(score.get("image_404s", 0) or 0)
    if f404 == 0:
        return 1.0, 0, 0
    # unknown denominator → report the 404 count, conservative rate
    return 0.0, 0, f404


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, help="comma-separated persona run dirs")
    ap.add_argument("--story", default="")
    ap.add_argument("--mech", default="")
    ap.add_argument("--behavioral", default="", choices=["", "GREEN", "RED"])
    ap.add_argument("--ui-audit", dest="ui_audit", default="", choices=["", "PASS", "FAIL"])
    ap.add_argument("--palette-live", dest="palette_live", default="", choices=["", "true", "false"])
    ap.add_argument("--build-sha", dest="build_sha", default="")
    ap.add_argument("--out", default="qa/RRI.json")
    ap.add_argument("--scorecard-row", action="store_true")
    args = ap.parse_args()

    run_dirs = [Path(p.strip()) for p in args.runs.split(",") if p.strip()]
    persona_scores = []
    for rd in run_dirs:
        sc = read_json(rd / "score.json")
        if not sc:
            continue
        rate, ok, total = image_render_rate(rd, sc)
        persona_scores.append({
            "run": sc.get("run") or rd.name,
            "persona": sc.get("persona"),
            "completed_intro_flow": bool(sc.get("completed_intro_flow")),
            "satisfaction": sc.get("persona_satisfaction"),
            "gave_up": bool(sc.get("gave_up")),
            "critical": int(sc.get("bug_reports_critical", 0) or 0),
            "image_rate": rate,
            "image_ok": ok,
            "image_total": total,
        })

    n = len(persona_scores) or 1
    sats = [p["satisfaction"] for p in persona_scores if isinstance(p["satisfaction"], (int, float))]
    avg_sat = sum(sats) / len(sats) if sats else 0.0
    any_gave_up = any(p["gave_up"] for p in persona_scores)
    any_completed = any(p["completed_intro_flow"] for p in persona_scores)
    total_critical = sum(p["critical"] for p in persona_scores)
    # weighted image rate across personas that recorded image traffic
    img_runs = [p for p in persona_scores if p["image_total"] > 0]
    img_rate = (sum(p["image_ok"] for p in img_runs) / sum(p["image_total"] for p in img_runs)) if img_runs else (
        1.0 if persona_scores and all(p["image_rate"] >= 0.95 for p in persona_scores) else 0.0)

    story = read_json(Path(args.story)) if args.story else {}
    mech = read_json(Path(args.mech)) if args.mech else {}
    story_overall = float(story.get("overall", 0) or 0)
    mech_overall = float(mech.get("overall", 0) or 0)

    # native gate: read part_a from any run.json present
    native = ""
    for rd in run_dirs:
        rj = read_json(rd / "run.json")
        pa = (rj.get("part_a") or {}).get("result")
        if pa:
            native = pa
            break

    # ---- the 11 gates (each contributes to RRI; all must hold for 10/10) ----
    gates = {
        "native_gate":        (native == "PASS",            f"part_a={native or 'n/a'}"),
        "arc_completed":      (any_completed,               f"completed_intro_flow on >=1 persona"),
        "cross_persona_sat":  (avg_sat >= 7.0,              f"avg={avg_sat:.1f}/10 over {len(sats)}"),
        "no_give_up":         (not any_gave_up,             f"any_gave_up={any_gave_up}"),
        "zero_critical":      (total_critical == 0,         f"critical={total_critical}"),
        "story_craft":        (story_overall >= 4.3,        f"story={story_overall or 'n/a'}"),
        "mechanical":         (mech_overall >= 4.5,         f"mech={mech_overall or 'n/a'}"),
        "behavioral":         (args.behavioral == "GREEN",  f"behavioral={args.behavioral or 'n/a'}"),
        "ui_audit":           (args.ui_audit == "PASS",     f"ui_audit={args.ui_audit or 'n/a'}"),
        "image_render":       (img_rate >= 0.95,            f"rate={img_rate:.2%}"),
        "palette_live":       (args.palette_live == "true", f"palette_live={args.palette_live or 'n/a'}"),
    }
    passed = sum(1 for ok, _ in gates.values() if ok)
    total_gates = len(gates)

    # RRI: each gate worth 10/total; HARD FLOOR — a missed gate can't be hidden by others.
    # (Equal weight keeps it honest: "10/10" literally means every gate held.)
    rri = round(10.0 * passed / total_gates, 1)
    release_ready = passed == total_gates

    failed = [name for name, (ok, _) in gates.items() if not ok]

    result = {
        "rri": rri,
        "release_ready": release_ready,
        "gates_passed": passed,
        "gates_total": total_gates,
        "failed_gates": failed,
        "build_sha": args.build_sha,
        "signals": {
            "native_gate": native,
            "arc_completed": any_completed,
            "cross_persona_satisfaction": round(avg_sat, 1),
            "any_gave_up": any_gave_up,
            "total_critical_bugs": total_critical,
            "story_overall": story_overall,
            "mech_overall": mech_overall,
            "behavioral": args.behavioral,
            "ui_audit": args.ui_audit,
            "image_render_rate": round(img_rate, 4),
            "palette_live": args.palette_live,
        },
        "gate_detail": {name: detail for name, (ok, detail) in gates.items()},
        "personas": persona_scores,
    }

    out = Path(args.out)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # human line
    print(f"RRI {rri}/10  ({passed}/{total_gates} gates)  release_ready={release_ready}")
    if failed:
        print("  FAILED: " + ", ".join(f"{f} [{gates[f][1]}]" for f in failed))

    if args.scorecard_row:
        sha = (args.build_sha or "?")[:7]
        row = (f"| RRI-{sha} | (date) | baldurs-gate | 5-persona | sonnet | gate | "
               f"{'**GREEN**' if release_ready else 'RED'} | {story_overall or '—'} | "
               f"{mech_overall or '—'} | — | **{rri}** | "
               f"RRI {passed}/{total_gates}; failed: {', '.join(failed) or 'none'} |")
        print(row)

    return 0 if release_ready else 1


if __name__ == "__main__":
    sys.exit(main())
