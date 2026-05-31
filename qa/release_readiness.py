#!/usr/bin/env python3
"""WorldOS Release Readiness Index (RRI 0-10) — roll up existing QA artifacts into ONE
release number with a HARD-GATE FLOOR (a failed gate caps the score; it is NOT a soft
average that hides a zero — mirrors the SCORECARD RED-cap discipline).

Pure reader of on-disk artifacts (never the live HTTP channel, which can corrupt):
  - <run>/run.json            (ui_playtest_app.sh)  -> part_a native #356 gate
  - <run>/score.json          (ui_playtest_score.py) -> intro flow, satisfaction, bugs, image_404s
  - <run>/network.ndjson or <run>/player/network.ndjson
                               (palette/playwright)  -> image-render rate (img 200 vs 404)
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
      [--build-sha SHA] [--expected-personas newbie,veteran,...]
      [--out qa/RRI.json] [--scorecard-row]

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


REQUIRED_RELEASE_PERSONAS = ["newbie", "veteran", "adversarial", "narrative", "optimizer"]
GATE_SPLIT_CONTRACT = {
    "deterministic_built_app_smoke": {
        "scope": "fast built-app wiring proof with deterministic provider",
        "release_verdict": False,
    },
    "short_real_provider_playtest": {
        "scope": "short built-app proof with a real provider and provider trace evidence",
        "release_verdict": False,
    },
    "full_five_persona_rri": {
        "scope": "non-partial five-persona release readiness verdict",
        "release_verdict": True,
    },
}


def looks_like_path(value: str) -> bool:
    return Path(value).is_absolute() or value.startswith(("./", "../", "~")) or "/" in value or "\\" in value


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


def image_render_rate(run: Path, score: dict) -> tuple[float, int, int, str]:
    """Fraction of image requests that returned bytes (not 404). Prefers network.ndjson
    (200 vs 404 image responses); falls back to score.json's image_404s with an unknown
    denominator (then rate is reported as 1.0 only if 0 404s, else conservative)."""
    network_paths = [run / "network.ndjson", run / "player" / "network.ndjson"]
    net_path = next((p for p in network_paths if p.exists()), network_paths[0])
    net = read_ndjson(net_path)
    img = [n for n in net if "/image" in str(n.get("url", ""))]
    if img:
        ok = sum(1 for n in img if int(n.get("status", 0) or 0) and int(n.get("status")) < 400)
        total = len(img)
        return (ok / total if total else 1.0), ok, total, str(net_path)
    # fallback: score.json carries image_404s but not the success count
    f404 = int(score.get("image_404s", 0) or 0)
    if f404 == 0:
        return 1.0, 0, 0, str(run / "score.json")
    # unknown denominator → report the 404 count, conservative rate
    return 0.0, 0, f404, str(run / "score.json")


def split_csv(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def infer_persona(run_dir: Path) -> str:
    name = run_dir.name
    for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer"):
        if name.endswith(f"-{persona}") or f"-{persona}-" in name:
            return persona
    return name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, help="comma-separated persona run dirs")
    ap.add_argument("--story", default="")
    ap.add_argument("--mech", default="")
    ap.add_argument("--behavioral", default="", choices=["", "GREEN", "RED"])
    ap.add_argument("--ui-audit", dest="ui_audit", default="", choices=["", "PASS", "FAIL"])
    ap.add_argument("--palette-live", dest="palette_live", default="", choices=["", "true", "false"])
    ap.add_argument("--expected-personas", default="", help="comma-separated persona names expected in this sweep")
    ap.add_argument("--behavioral-path", default="", help="path to the behavioral evidence source")
    ap.add_argument("--ui-audit-log", default="", help="path to the UI audit evidence source")
    ap.add_argument("--palette-source", default="", help="path or label for the palette-live evidence source")
    ap.add_argument("--build-sha", dest="build_sha", default="")
    ap.add_argument("--out", default="qa/RRI.json")
    ap.add_argument("--scorecard-row", action="store_true")
    args = ap.parse_args()

    run_dirs = [Path(p) for p in split_csv(args.runs)]
    expected_personas = split_csv(args.expected_personas)
    persona_scores = []
    harness_failures = []
    completed_personas: list[str] = []
    for idx, rd in enumerate(run_dirs):
        expected_for_run = expected_personas[idx] if idx < len(expected_personas) else ""
        rj = read_json(rd / "run.json")
        sc = read_json(rd / "score.json")
        if not sc:
            part_b = (rj.get("part_b") or {}).get("persona_loop")
            harness_failures.append({
                "run": rd.name,
                "persona": expected_for_run or infer_persona(rd),
                "missing": "score.json",
                "part_b": part_b or "n/a",
            })
            continue
        rate, ok, total, image_source = image_render_rate(rd, sc)
        persona = sc.get("persona") or expected_for_run or infer_persona(rd)
        if persona:
            completed_personas.append(str(persona))
        persona_scores.append({
            "run": sc.get("run") or rd.name,
            "persona": persona,
            "completed_intro_flow": bool(sc.get("completed_intro_flow")),
            "satisfaction": sc.get("persona_satisfaction"),
            "gave_up": bool(sc.get("gave_up")),
            "critical": int(sc.get("bug_reports_critical", 0) or 0),
            "console_errors": int(sc.get("console_errors", 0) or 0),
            "image_rate": rate,
            "image_ok": ok,
            "image_total": total,
            "image_source": image_source,
            "run_build_sha": rj.get("build_sha") or "",
            "part_b_result": (rj.get("part_b") or {}).get("persona_loop") or "n/a",
            "part_b_score_pass": bool((rj.get("part_b") or {}).get("score_pass")),
        })

    if not expected_personas:
        expected_personas = [p["persona"] for p in persona_scores if p.get("persona")]
        expected_personas.extend(h["persona"] for h in harness_failures if h.get("persona"))
    completed_set = set(completed_personas)
    missing_personas = [p for p in expected_personas if p not in completed_set]
    expected_complete = not missing_personas

    sats = [p["satisfaction"] for p in persona_scores if isinstance(p["satisfaction"], (int, float))]
    avg_sat = sum(sats) / len(sats) if sats else 0.0
    any_gave_up = any(p["gave_up"] for p in persona_scores)
    any_completed = any(p["completed_intro_flow"] for p in persona_scores)
    total_critical = sum(p["critical"] for p in persona_scores)
    total_console_errors = sum(p["console_errors"] for p in persona_scores)
    # weighted image rate across personas that recorded image traffic. A release
    # verdict needs a denominator for every scored persona; zero image requests is
    # an evidence gap, not a 100% pass.
    img_runs = [p for p in persona_scores if p["image_total"] > 0]
    image_missing_personas = [str(p["persona"]) for p in persona_scores if p["image_total"] <= 0]
    image_evidence_complete = bool(persona_scores) and not image_missing_personas
    img_rate = (sum(p["image_ok"] for p in img_runs) / sum(p["image_total"] for p in img_runs)) if img_runs else 0.0

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

    evidence_gaps = []
    build_shas = sorted({str(p["run_build_sha"]) for p in persona_scores if p.get("run_build_sha")})
    missing_build_sha = [p for p in persona_scores if not p.get("run_build_sha")]
    if not args.build_sha:
        evidence_gaps.append({
            "gate": "native_gate",
            "missing": "--build-sha",
            "detail": "release verdict requires the measured build SHA",
        })
    if missing_build_sha:
        evidence_gaps.append({
            "gate": "native_gate",
            "missing": "per-run build_sha",
            "detail": "missing run build_sha for: " + ", ".join(str(p.get("persona") or p.get("run")) for p in missing_build_sha),
        })
    if args.build_sha:
        mismatched = [p for p in persona_scores if p.get("run_build_sha") and p.get("run_build_sha") != args.build_sha]
        if mismatched:
            evidence_gaps.append({
                "gate": "native_gate",
                "missing": "same-build persona evidence",
                "detail": "run build_sha mismatch: " + ", ".join(f"{p['persona']}={p['run_build_sha']}" for p in mismatched),
            })
    if len(build_shas) > 1:
        evidence_gaps.append({
            "gate": "native_gate",
            "missing": "single build_sha",
            "detail": "mixed persona build_sha values: " + ", ".join(build_shas),
        })
    missing_release_personas = [p for p in REQUIRED_RELEASE_PERSONAS if p not in completed_set]
    if missing_release_personas:
        missing_detail = f"missing release persona(s): {', '.join(missing_release_personas)}"
        for gate in ("cross_persona_sat", "no_give_up", "zero_critical", "image_render"):
            evidence_gaps.append({
                "gate": gate,
                "missing": "canonical five-persona release set",
                "detail": missing_detail,
            })
    for h in harness_failures:
        evidence_gaps.append({
            "gate": "cross_persona_sat",
            "missing": f"{h['run']}/score.json",
            "detail": f"persona={h.get('persona') or 'unknown'} part_b={h.get('part_b') or 'n/a'}",
        })
    failed_part_b = [p for p in persona_scores if p.get("part_b_result") != "PASS"]
    for p in failed_part_b:
        evidence_gaps.append({
            "gate": "arc_completed",
            "missing": f"{p['run']}/run.json part_b PASS",
            "detail": f"persona={p.get('persona') or 'unknown'} part_b={p.get('part_b_result')} score_pass={p.get('part_b_score_pass')}",
        })
    if not native:
        evidence_gaps.append({
            "gate": "native_gate",
            "missing": "run.json part_a.result",
            "detail": "no persona run recorded native built-app transition evidence",
        })
    if not args.story:
        evidence_gaps.append({"gate": "story_craft", "missing": "--story", "detail": "story lens path not supplied"})
    elif "overall" not in story:
        evidence_gaps.append({"gate": "story_craft", "missing": args.story, "detail": "story lens JSON missing overall"})
    if not args.mech:
        evidence_gaps.append({"gate": "mechanical", "missing": "--mech", "detail": "mechanical lens path not supplied"})
    elif "overall" not in mech:
        evidence_gaps.append({"gate": "mechanical", "missing": args.mech, "detail": "mechanical lens JSON missing overall"})
    if not args.behavioral:
        evidence_gaps.append({"gate": "behavioral", "missing": "--behavioral", "detail": "behavioral result not supplied"})
    elif not args.behavioral_path:
        evidence_gaps.append({"gate": "behavioral", "missing": "--behavioral-path", "detail": "behavioral evidence path not supplied"})
    elif not Path(args.behavioral_path).exists():
        evidence_gaps.append({"gate": "behavioral", "missing": args.behavioral_path, "detail": "behavioral evidence path missing"})
    if not args.ui_audit:
        evidence_gaps.append({"gate": "ui_audit", "missing": "--ui-audit", "detail": "UI audit result not supplied"})
    elif not args.ui_audit_log:
        evidence_gaps.append({"gate": "ui_audit", "missing": "--ui-audit-log", "detail": "UI audit log path not supplied"})
    elif not Path(args.ui_audit_log).exists():
        evidence_gaps.append({"gate": "ui_audit", "missing": args.ui_audit_log, "detail": "UI audit log path missing"})
    if image_missing_personas:
        evidence_gaps.append({
            "gate": "image_render",
            "missing": "network.ndjson image denominator",
            "detail": f"no /image requests recorded for: {', '.join(image_missing_personas)}",
        })
    if not args.palette_live:
        evidence_gaps.append({"gate": "palette_live", "missing": "--palette-live", "detail": "palette-live result not supplied"})
    elif not args.palette_source:
        evidence_gaps.append({"gate": "palette_live", "missing": "--palette-source", "detail": "palette-live evidence source not supplied"})
    elif looks_like_path(args.palette_source) and not Path(args.palette_source).exists():
        evidence_gaps.append({"gate": "palette_live", "missing": args.palette_source, "detail": "palette-live evidence source missing"})
    evidence_gap_gates = {gap["gate"] for gap in evidence_gaps}

    # ---- the 11 gates (each contributes to RRI; all must hold for 10/10) ----
    gates = {
        "native_gate":        (native == "PASS" and "native_gate" not in evidence_gap_gates,
                               f"part_a={native or 'n/a'}"),
        "arc_completed":      (any_completed and "arc_completed" not in evidence_gap_gates,
                               f"completed_intro_flow on >=1 persona"),
        "cross_persona_sat":  (not missing_release_personas and expected_complete and avg_sat >= 7.0,
                               f"avg={avg_sat:.1f}/10 over {len(sats)}; missing={missing_personas or 'none'}; release_missing={missing_release_personas or 'none'}"),
        "no_give_up":         (not any_gave_up and "no_give_up" not in evidence_gap_gates,
                               f"any_gave_up={any_gave_up}"),
        "zero_critical":      (total_critical == 0 and total_console_errors == 0 and "zero_critical" not in evidence_gap_gates,
                               f"critical={total_critical}; console_errors={total_console_errors}"),
        "story_craft":        (story_overall >= 4.3 and "story_craft" not in evidence_gap_gates,
                               f"story={story_overall or 'n/a'}"),
        "mechanical":         (mech_overall >= 4.5 and "mechanical" not in evidence_gap_gates,
                               f"mech={mech_overall or 'n/a'}"),
        "behavioral":         (args.behavioral == "GREEN" and "behavioral" not in evidence_gap_gates,
                               f"behavioral={args.behavioral or 'n/a'}"),
        "ui_audit":           (args.ui_audit == "PASS" and "ui_audit" not in evidence_gap_gates,
                               f"ui_audit={args.ui_audit or 'n/a'}"),
        "image_render":       (image_evidence_complete and img_rate >= 0.95 and "image_render" not in evidence_gap_gates,
                               f"rate={img_rate:.2%}; denominator={sum(p['image_total'] for p in img_runs)}"),
        "palette_live":       (args.palette_live == "true" and "palette_live" not in evidence_gap_gates,
                               f"palette_live={args.palette_live or 'n/a'}"),
    }
    passed = sum(1 for ok, _ in gates.values() if ok)
    total_gates = len(gates)

    # RRI: each gate worth 10/total; HARD FLOOR — a missed gate can't be hidden by others.
    # (Equal weight keeps it honest: "10/10" literally means every gate held.)
    rri = round(10.0 * passed / total_gates, 1)
    failed = [name for name, (ok, _) in gates.items() if not ok]
    if missing_release_personas and "missing_release_personas" not in failed:
        failed.insert(0, "missing_release_personas")
    if missing_personas and "missing_personas" not in failed:
        failed.insert(0, "missing_personas")
    release_ready = passed == total_gates and not evidence_gaps and not missing_personas and not harness_failures

    result = {
        "rri": rri,
        "release_ready": release_ready,
        "release_verdict_gate": "full_five_persona_rri",
        "gate_split_contract": GATE_SPLIT_CONTRACT,
        "partial": bool(missing_personas or evidence_gaps),
        "harness_contaminated": bool(missing_personas or harness_failures or evidence_gaps),
        "expected_personas": expected_personas,
        "required_release_personas": REQUIRED_RELEASE_PERSONAS,
        "completed_personas": completed_personas,
        "missing_personas": missing_personas,
        "missing_release_personas": missing_release_personas,
        "harness_failures": harness_failures,
        "evidence_gaps": evidence_gaps,
        "gates_passed": passed,
        "gates_total": total_gates,
        "failed_gates": failed,
        "build_sha": args.build_sha,
        "artifact_sources": {
            "behavioral": args.behavioral_path or "argument",
            "ui_audit": args.ui_audit_log or "argument",
            "palette_live": args.palette_source or "argument",
            "story": args.story or "",
            "mechanical": args.mech or "",
            "runs": [str(p) for p in run_dirs],
            "images": sorted({p["image_source"] for p in persona_scores if p.get("image_source")}),
        },
        "signals": {
            "native_gate": native,
            "arc_completed": any_completed,
            "cross_persona_satisfaction": round(avg_sat, 1),
            "any_gave_up": any_gave_up,
            "total_critical_bugs": total_critical,
            "total_console_errors": total_console_errors,
            "story_overall": story_overall,
            "mech_overall": mech_overall,
            "behavioral": args.behavioral,
            "ui_audit": args.ui_audit,
            "image_render_rate": round(img_rate, 4),
            "image_request_denominator": sum(p["image_total"] for p in img_runs),
            "image_missing_personas": image_missing_personas,
            "palette_live": args.palette_live,
            "run_build_shas": build_shas,
        },
        "gate_detail": {name: detail for name, (ok, detail) in gates.items()},
        "personas": persona_scores,
    }

    out = Path(args.out)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # human line
    print(f"RRI {rri}/10  ({passed}/{total_gates} gates)  release_ready={release_ready}")
    if failed:
        details = []
        for f in failed:
            if f == "missing_personas":
                details.append(f"{f} [{missing_personas}]")
            elif f == "missing_release_personas":
                details.append(f"{f} [{missing_release_personas}]")
            else:
                details.append(f"{f} [{gates[f][1]}]")
        print("  FAILED: " + ", ".join(details))
    if evidence_gaps:
        print("  EVIDENCE GAPS: " + "; ".join(f"{g['gate']} missing {g['missing']}" for g in evidence_gaps))

    if args.scorecard_row:
        sha = (args.build_sha or "?")[:7]
        verdict = "PARTIAL/HARNESS" if (missing_personas or evidence_gaps or harness_failures) else ("**GREEN**" if release_ready else "RED")
        row = (f"| RRI-{sha} | (date) | baldurs-gate | {len(expected_personas) or len(persona_scores)}-persona | sonnet | gate | "
               f"{verdict} | {story_overall or '—'} | "
               f"{mech_overall or '—'} | — | **{rri}** | "
               f"RRI {passed}/{total_gates}; failed: {', '.join(failed) or 'none'} |")
        print(row)

    return 0 if release_ready else 1


if __name__ == "__main__":
    sys.exit(main())
