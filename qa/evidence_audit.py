#!/usr/bin/env python3
"""evidence_audit.py — answer "is this a real blocker, or do I just need more
runs/budget?" for a WorldOS release rollup.

PURE READER. Given an existing RRI.json (`--rri`) or a run dir that contains one
(`--run <dir>`), it reports, per gate, REQUIRED vs PRESENT evidence and classifies
each FAILED gate as either:

  EVIDENCE_GAP  — recoverable. The gate failed for lack of evidence (a missing
                  persona, a missing image denominator, an un-supplied artifact, a
                  mixed/missing build SHA, a missing Mac handoff, a harness failure).
                  The suggested action is a re-run / more personas / more budget /
                  supply-the-artifact — NOT a product fix.

  REAL_BLOCKER  — a genuine failing signal. The gate is in RRI.json.failed_gates and
                  has NO matching entry in RRI.json.evidence_gaps, which means the
                  evidence was PRESENT and the measured value missed the gate
                  threshold (e.g. story 3.0 < 4.3, a persona gave_up, a critical bug,
                  satisfaction < 7). The suggested action is to FIX THE PRODUCT.

Special case: when the rollup ABORTED on quota (RRI.json.aborted, abort_reason
quota_session_limit / HTTP 429) the RRI number is not a measurement at all — every
gap is reclassified ABORTED_RECOVERABLE and the action is "re-run after the quota
resets" (the reset hint is in RRI.json.abort_detail).

This tool NEVER writes state, never re-runs a sweep, never touches the committed
qa/RRI.json or qa/scores* artifacts. It only reads the RRI.json you point it at and
the qa/verdict_requirements.json declaration that lives beside it.

Usage:
  evidence_audit.py --rri <RRI.json> [--verdict rri_release] [--json]
  evidence_audit.py --run <dir>      [--verdict rri_release] [--json]

Exit codes (so a caller can branch without parsing):
  0  RELEASE_READY (no gaps, no blockers)
  0  ABORTED_RECOVERABLE (infra abort — not a product failure)
  3  EVIDENCE_GAP (only recoverable gaps; get more runs/budget/artifacts)
  4  REAL_BLOCKER (>=1 genuine failing signal; fix the product)
  2  usage / NO_RRI (no RRI.json to read)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
REQUIREMENTS_PATH = HERE / "verdict_requirements.json"
DEFAULT_VERDICT = "rri_release"

# The canonical 11 RRI gates (mirrors release_readiness.py — kept here so the audit
# can report REQUIRED items even when a gate did not appear in the rollup output).
RRI_GATE_NAMES = [
    "native_gate",
    "arc_completed",
    "cross_persona_sat",
    "no_give_up",
    "zero_critical",
    "story_craft",
    "mechanical",
    "behavioral",
    "ui_audit",
    "image_render",
    "palette_live",
]

# Non-gate sentinels release_readiness.py prepends to failed_gates for missing
# personas. They are always recoverable (more personas) and are represented by their
# own evidence_gaps entries, so we never treat them as REAL_BLOCKER candidates.
NON_GATE_FAILURES = {"missing_personas", "missing_release_personas"}

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_EVIDENCE_GAP = 3
EXIT_REAL_BLOCKER = 4

VERDICT_RELEASE_READY = "RELEASE_READY"
VERDICT_EVIDENCE_GAP = "EVIDENCE_GAP"
VERDICT_REAL_BLOCKER = "REAL_BLOCKER"
VERDICT_ABORTED = "ABORTED_RECOVERABLE"
VERDICT_NO_RRI = "NO_RRI"


def load_requirements(path: Path | None = None) -> dict:
    """Read the verdict_requirements.json declaration (pure read)."""
    p = path or REQUIREMENTS_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - config error
        raise SystemExit(f"could not read {p}: {exc}")


def _read_rri(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _match_rule(rules: list[dict], gate: str, missing: str, detail: str) -> dict | None:
    """Pick the recovery rule whose patterns match this evidence_gaps entry.

    Matching is conservative and ordered: a rule scoped to specific gates
    (`match_gate`) only applies to those gates; `match_missing_equals` is an exact
    match on the `missing` field; `match_any` is a case-insensitive substring search
    over both `missing` and `detail`. The first matching rule (in declaration order)
    wins, so put the most specific rules first in the JSON.
    """
    hay = f"{missing} {detail}".lower()
    for rule in rules:
        gates = rule.get("match_gate")
        if gates and gate not in gates:
            continue
        eqs = rule.get("match_missing_equals") or []
        if any(missing == e for e in eqs):
            return rule
        needles = rule.get("match_any") or []
        if any(str(n).lower() in hay for n in needles):
            return rule
    return None


def audit_rri(rri_path: Path | str, verdict_type: str = DEFAULT_VERDICT,
              requirements: dict | None = None) -> dict:
    """Classify an RRI.json. Returns a structured report (dict). Pure reader."""
    rri_path = Path(rri_path)
    if not rri_path.exists():
        return {
            "verdict": VERDICT_NO_RRI,
            "source": str(rri_path),
            "release_ready": False,
            "aborted": False,
            "items": [],
            "evidence_gaps": [],
            "real_blockers": [],
            "summary": f"no RRI.json at {rri_path}",
        }
    rri = _read_rri(rri_path)
    report = classify(rri, verdict_type=verdict_type, requirements=requirements)
    report["source"] = str(rri_path)
    return report


def audit_run(run_dir: Path | str, verdict_type: str = DEFAULT_VERDICT,
              requirements: dict | None = None) -> dict:
    """Read <run_dir>/RRI.json and classify it. Pure reader."""
    run_dir = Path(run_dir)
    rri_path = run_dir / "RRI.json"
    return audit_rri(rri_path, verdict_type=verdict_type, requirements=requirements)


def classify(rri: dict, verdict_type: str = DEFAULT_VERDICT,
             requirements: dict | None = None) -> dict:
    """Core classifier over an in-memory RRI payload. Pure function."""
    reqs = requirements or load_requirements()
    rules = reqs.get("gap_recovery_rules", [])
    verdict_decl = (reqs.get("verdicts") or {}).get(verdict_type, {})
    real_blocker_decl = verdict_decl.get("real_blocker_gates", {})
    abort_decl = reqs.get("abort_is_not_a_blocker", {})

    aborted = bool(rri.get("aborted"))
    abort_detail = str(rri.get("abort_detail") or "")
    release_ready = bool(rri.get("release_ready"))
    failed_gates = list(rri.get("failed_gates") or [])
    evidence_gaps_in = list(rri.get("evidence_gaps") or [])
    gate_detail = rri.get("gate_detail") or {}

    # Set of gates that the rollup itself flagged as evidence gaps. A gate that fails
    # AND appears here lacked evidence; a gate that fails WITHOUT appearing here had
    # evidence present and missed its threshold -> a real signal.
    gap_gates = {g.get("gate") for g in evidence_gaps_in}

    evidence_gaps_out: list[dict] = []
    real_blockers: list[dict] = []

    # ---- classify each declared evidence gap from the rollup ----
    for gap in evidence_gaps_in:
        gate = gap.get("gate", "")
        missing = str(gap.get("missing") or "")
        detail = str(gap.get("detail") or "")
        if aborted:
            classification = VERDICT_ABORTED
            recoverable = True
            action = abort_decl.get("action") or "Infra abort — re-run after the quota resets."
            if abort_detail and abort_detail not in action:
                action = f"{action} (reset hint: {abort_detail})"
            rule_id = "abort"
        else:
            rule = _match_rule(rules, gate, missing, detail)
            classification = VERDICT_EVIDENCE_GAP
            recoverable = True
            rule_id = rule.get("id") if rule else "unmatched_gap"
            action = rule.get("action") if rule else (
                "Recoverable evidence gap (no specific rule matched). Supply the "
                "missing evidence and re-roll RRI."
            )
        evidence_gaps_out.append({
            "gate": gate,
            "missing": missing,
            "detail": detail,
            "classification": classification,
            "recoverable": recoverable,
            "rule_id": rule_id,
            "action": action,
        })

    # ---- classify each failed gate that is NOT covered by an evidence gap ----
    if not aborted:
        for gate in failed_gates:
            if gate in NON_GATE_FAILURES:
                continue  # represented by its own evidence_gaps entries
            if gate in gap_gates:
                continue  # already a recoverable evidence gap
            decl = real_blocker_decl.get(gate, {})
            real_blockers.append({
                "gate": gate,
                "classification": VERDICT_REAL_BLOCKER,
                "recoverable": False,
                "measured": str(gate_detail.get(gate, "")),
                "threshold": decl.get("threshold", ""),
                "action": decl.get("action") or (
                    f"Real failing signal on gate '{gate}' with evidence present — "
                    "this is a product/quality defect, not a re-run. Fix it."
                ),
            })

    # ---- build the REQUIRED-vs-PRESENT item table ----
    required_gates = verdict_decl.get("required_gates") or RRI_GATE_NAMES
    blocker_gates = {b["gate"] for b in real_blockers}
    items: list[dict] = []
    for gate in required_gates:
        is_blocker = gate in blocker_gates
        is_gap = gate in gap_gates
        present = not is_gap  # evidence is "present" unless the rollup gapped it
        if is_blocker:
            status = "REAL_BLOCKER"
        elif is_gap:
            status = "EVIDENCE_GAP"
        else:
            status = "PASS"
        items.append({
            "gate": gate,
            "required": True,
            "present": present,
            "status": status,
            "measured": str(gate_detail.get(gate, "")),
        })

    # ---- top-level verdict ----
    if aborted:
        verdict = VERDICT_ABORTED
    elif real_blockers:
        verdict = VERDICT_REAL_BLOCKER
    elif evidence_gaps_out:
        verdict = VERDICT_EVIDENCE_GAP
    elif release_ready:
        verdict = VERDICT_RELEASE_READY
    else:
        # Not release-ready, no gaps, no blockers we could attribute — surface it
        # honestly rather than silently calling it ready.
        verdict = VERDICT_EVIDENCE_GAP if failed_gates else VERDICT_RELEASE_READY

    summary = _summarize(verdict, evidence_gaps_out, real_blockers, abort_detail)

    return {
        "verdict": verdict,
        "verdict_type": verdict_type,
        "release_ready": release_ready,
        "aborted": aborted,
        "abort_detail": abort_detail,
        "rri": rri.get("rri"),
        "status": rri.get("status"),
        "gates_passed": rri.get("gates_passed"),
        "gates_total": rri.get("gates_total"),
        "items": items,
        "evidence_gaps": evidence_gaps_out,
        "real_blockers": real_blockers,
        "summary": summary,
    }


def _summarize(verdict: str, gaps: list[dict], blockers: list[dict], abort_detail: str) -> str:
    if verdict == VERDICT_RELEASE_READY:
        return "All required evidence present and every gate held — release-ready."
    if verdict == VERDICT_ABORTED:
        return ("Infra abort (account session limit / HTTP 429), not a product verdict. "
                f"Re-run after the quota resets. {abort_detail}".strip())
    if verdict == VERDICT_REAL_BLOCKER:
        names = ", ".join(b["gate"] for b in blockers)
        gap_note = f" ({len(gaps)} recoverable gap(s) also present)" if gaps else ""
        return (f"{len(blockers)} REAL BLOCKER(s) — fix the product: {names}.{gap_note} "
                "These gates have evidence present and missed their threshold; more runs will not help.")
    if verdict == VERDICT_EVIDENCE_GAP:
        names = ", ".join(sorted({g["gate"] for g in gaps}))
        return (f"No real blockers. {len(gaps)} recoverable EVIDENCE GAP(s): {names}. "
                "Get more runs / budget / personas / artifacts, then re-roll RRI.")
    return verdict


def exit_code_for(verdict: str) -> int:
    return {
        VERDICT_RELEASE_READY: EXIT_OK,
        VERDICT_ABORTED: EXIT_OK,
        VERDICT_EVIDENCE_GAP: EXIT_EVIDENCE_GAP,
        VERDICT_REAL_BLOCKER: EXIT_REAL_BLOCKER,
        VERDICT_NO_RRI: EXIT_USAGE,
    }.get(verdict, EXIT_USAGE)


def _print_human(report: dict) -> None:
    print(f"VERDICT: {report['verdict']}  (rri={report.get('rri')}, status={report.get('status')})")
    print(f"  {report['summary']}")
    blockers = report.get("real_blockers") or []
    gaps = report.get("evidence_gaps") or []
    print(f"\n  REAL_BLOCKER: {len(blockers)}    EVIDENCE_GAP: {len(gaps)}")
    if blockers:
        print("\n  REAL BLOCKERS (fix the product):")
        for b in blockers:
            measured = f" [measured: {b['measured']}]" if b.get("measured") else ""
            print(f"    - {b['gate']}: {b['action']}{measured}")
    if gaps:
        print("\n  EVIDENCE GAPS (recoverable — more runs/budget/artifacts):")
        for g in gaps:
            print(f"    - {g['gate']} (missing {g['missing']}; rule={g['rule_id']}): {g['action']}")
    if not blockers and not gaps:
        print("\n  (no gaps, no blockers)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Classify RRI evidence gaps vs real blockers (pure reader).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--rri", help="path to an RRI.json")
    src.add_argument("--run", help="path to a run dir containing RRI.json")
    ap.add_argument("--verdict", default=DEFAULT_VERDICT,
                    help="verdict type declared in verdict_requirements.json (default: rri_release)")
    ap.add_argument("--requirements", default="",
                    help="override path to verdict_requirements.json (default: beside this script)")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON on stdout")
    args = ap.parse_args(argv)

    requirements = load_requirements(Path(args.requirements)) if args.requirements else None

    if args.rri:
        report = audit_rri(args.rri, verdict_type=args.verdict, requirements=requirements)
    else:
        report = audit_run(args.run, verdict_type=args.verdict, requirements=requirements)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)

    return exit_code_for(report["verdict"])


if __name__ == "__main__":
    sys.exit(main())
