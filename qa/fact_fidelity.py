#!/usr/bin/env python3
"""Differential fact-fidelity — the SENSITIVE, deterministic content-loss measure.

The three 1–5 LLM lenses (``qa/score.sh``) grade prose/plot-gist and are demonstrably
BLIND to content degradation: deleting a transcript's entire climax+resolution moved the
lens ``overall`` by ~0.0 (within the 0.40 noise floor), and the mechanical lens even scored
a gutted transcript HIGHER than the intact one (fewer beats ⇒ fewer SRD errors to find).
A grep-verified 45-fact differential on the SAME variants read 100% / 53% / 27% — i.e. the
real, massive content loss the lens could not see.

This module is that grep-verified differential, as a reusable instrument:

  - A **fact inventory** is a committed list of discrete, grep-able facts extracted from a
    REFERENCE transcript (each: an id, a human description, one-or-more match patterns, a
    severity). Authored once per reference; the CHECK is fully deterministic (no LLM).
  - ``score_fidelity(facts, candidate_text)`` reports what fraction of the reference's facts
    survive in a candidate transcript. A truncated / compressed / degraded candidate drops
    monotonically; a dropped CRITICAL fact (an antagonist reveal, the central MacGuffin, an
    end-session mechanic) is flagged regardless of the headline percentage.

Used for regression detection (compression A/B, candidate-vs-baseline), NOT as a prose-quality
grader — it answers "did this candidate preserve the reference's facts?", which the lens cannot.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Fact:
    """One discrete, grep-able fact from a reference transcript.

    ``patterns`` are case-insensitive regexes; the fact is PRESENT in a candidate when ANY
    pattern matches (so a fact can be spelled several equivalent ways).
    """

    id: str
    desc: str
    patterns: list[str] = field(default_factory=list)
    severity: str = "normal"  # "critical" | "high" | "normal"


# A dropped CRITICAL fact (an antagonist reveal, the central MacGuffin, an end-session
# mechanic) must dominate the headline number, so severity weights the fidelity.
SEVERITY_WEIGHT: dict[str, float] = {"critical": 3.0, "high": 2.0, "normal": 1.0}


@dataclass
class FidelityReport:
    fidelity: float          # unweighted fraction of facts preserved (0.0–1.0)
    weighted: float          # severity-weighted fraction preserved (0.0–1.0)
    present: list[str]       # ids of preserved facts
    missing: list[str]       # ids of dropped facts
    critical_loss: bool      # True iff any severity="critical" fact was dropped


def load_inventory(path: str | Path) -> list[Fact]:
    """Parse a committed fact-inventory JSON file into ``Fact`` objects.

    Schema: ``{"reference": <name>, "facts": [{"id","desc","patterns":[...],"severity"?}, …]}``.
    ``severity`` defaults to "normal" when omitted.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        Fact(
            id=f["id"],
            desc=f.get("desc", ""),
            patterns=list(f.get("patterns", [])),
            severity=f.get("severity", "normal"),
        )
        for f in data.get("facts", [])
    ]


def _fact_present(fact: Fact, text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in fact.patterns)


def _weight(fact: Fact) -> float:
    return SEVERITY_WEIGHT.get(fact.severity, 1.0)


def score_fidelity(facts: list[Fact], candidate_text: str) -> FidelityReport:
    present: list[str] = []
    missing: list[str] = []
    present_weight = 0.0
    total_weight = 0.0
    critical_loss = False
    for f in facts:
        total_weight += _weight(f)
        if _fact_present(f, candidate_text):
            present.append(f.id)
            present_weight += _weight(f)
        else:
            missing.append(f.id)
            if f.severity == "critical":
                critical_loss = True
    fidelity = len(present) / len(facts) if facts else 1.0
    weighted = present_weight / total_weight if total_weight else 1.0
    return FidelityReport(
        fidelity=fidelity,
        weighted=weighted,
        present=present,
        missing=missing,
        critical_loss=critical_loss,
    )


def passed(report: FidelityReport, min_fidelity: float = 0.95, fail_on_critical: bool = True) -> bool:
    """Gate verdict: a candidate PASSES when its fidelity clears ``min_fidelity`` and (unless
    disabled) no CRITICAL fact was dropped. A dropped critical fact fails regardless of the
    headline percentage — that is the whole point (the 1–5 lens missed exactly that)."""
    if fail_on_critical and report.critical_loss:
        return False
    return report.fidelity >= min_fidelity


def report_dict(report: FidelityReport, facts: list[Fact] | None = None) -> dict:
    """JSON-serializable view of a report. When ``facts`` is supplied, missing facts carry their
    severity + description so a CLI/regression log names WHAT was lost, not just an id."""
    by_id = {f.id: f for f in (facts or [])}
    return {
        "fidelity": round(report.fidelity, 4),
        "weighted": round(report.weighted, 4),
        "critical_loss": report.critical_loss,
        "present": list(report.present),
        "missing": [
            {
                "id": mid,
                "severity": by_id[mid].severity if mid in by_id else None,
                "desc": by_id[mid].desc if mid in by_id else None,
            }
            for mid in report.missing
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: score a candidate transcript against a committed fact inventory.

    Usage: fact_fidelity.py <inventory.json> <candidate.md> [--min-fidelity F] [--json]
    Exit 0 if the candidate clears the floor with no critical loss, else 1.
    """
    ap = argparse.ArgumentParser(description="Differential fact-fidelity check for QA transcripts.")
    ap.add_argument("inventory", help="path to the fact-inventory JSON")
    ap.add_argument("candidate", help="path to the candidate transcript (.md)")
    ap.add_argument("--min-fidelity", type=float, default=0.95,
                    help="minimum fraction of facts that must survive (default 0.95)")
    ap.add_argument("--no-fail-on-critical", action="store_true",
                    help="do not fail solely because a critical fact was dropped")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = ap.parse_args(argv)

    facts = load_inventory(args.inventory)
    text = Path(args.candidate).read_text(encoding="utf-8")
    report = score_fidelity(facts, text)
    ok = passed(report, min_fidelity=args.min_fidelity,
                fail_on_critical=not args.no_fail_on_critical)

    if args.json:
        out = report_dict(report, facts)
        out["passed"] = ok
        out["min_fidelity"] = args.min_fidelity
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        verdict = "PASS" if ok else "FAIL"
        print(f"[fact-fidelity] {verdict}  fidelity={report.fidelity*100:.1f}%  "
              f"weighted={report.weighted*100:.1f}%  critical_loss={report.critical_loss}")
        if report.missing:
            by_id = {f.id: f for f in facts}
            print(f"  dropped {len(report.missing)} fact(s):")
            for mid in report.missing:
                sev = by_id[mid].severity if mid in by_id else "?"
                desc = by_id[mid].desc if mid in by_id else ""
                print(f"    - [{sev}] {mid}: {desc}")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
