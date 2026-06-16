#!/usr/bin/env python3
"""Root-cause analyzer — turn a behavioral-gate RED into ACTIONABLE locations.

The QA harness flips a run RED when a structural invariant fails: qa/assert_behavioral.py emits
each FATAL/WARN check via chk("CHECK_NAME", condition, ...) and, on a RED, prints per-check
``  [FAIL] <name> — <detail>`` lines plus a trailing ``RED: N behavioral assertion(s) FAILED.``;
run_duo.sh tees that into ``qa/transcripts/<run>.gate.txt`` and echoes ``behavioral=GREEN|RED``.

A RED tells the implementing agent THAT something broke, not WHERE. This tool closes that gap:
given a RED (a gate file, a comma list of check names, or a run id whose on-disk gate it reads),
it maps each FAILED check through qa/BEHAVIORAL_GATE_TAXONOMY.json to a root-cause category
(ENGINE_INVARIANT | DM_ADHERENCE | HARNESS_WIRING), candidate code locations, and an exact
retest command — plus a one-line summary. An UNKNOWN check (taxonomy drift) degrades gracefully
to category UNKNOWN with a generic hint; it never crashes.

PURE READER. It never runs a game, scores a transcript, writes a snapshot, or touches
qa/scores.db / qa/scores_ledger.md / qa/RRI.json or any committed artifact. It only reads the
gate text the harness already wrote and the static taxonomy.

Usage:
    python qa/root_cause_analyzer.py --gate qa/transcripts/<run>.gate.txt [--json]
    python qa/root_cause_analyzer.py --checks xp_not_orphaned,dice_used [--json]
    python qa/root_cause_analyzer.py --run <run_id> [--transcripts <dir>] [--json]
    # add --include-warnings to also surface [WARN] rows (not just [FAIL]).

Exit codes: 0 = ran fine (the RED/GREEN verdict is IN the report, not the exit code — this is a
reporter, not a gate), 2 = usage error. A pure reporter does not gate CI; consumers read the
JSON `verdict`/`reports`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

_QA_DIR = Path(__file__).resolve().parent
_TAXONOMY_PATH = _QA_DIR / "BEHAVIORAL_GATE_TAXONOMY.json"
# Default transcripts dir (where run_duo.sh / run_combat_sprint.sh tee <run>.gate.txt). Resolved
# relative to qa/ so the tool works from any cwd; overridable for tests / non-default lanes.
_DEFAULT_TRANSCRIPTS = _QA_DIR / "transcripts"

_VALID_CATEGORIES = ("ENGINE_INVARIANT", "DM_ADHERENCE", "HARNESS_WIRING")

# Fallback for a check the taxonomy doesn't know (drift): never crash, give the agent a starting
# point and a clear signal that the taxonomy is stale.
_UNKNOWN_CATEGORY = "UNKNOWN"
_UNKNOWN_RETEST = "bash qa/run_duo.sh duo-retest"
_UNKNOWN_HINT = (
    "This check is not in qa/BEHAVIORAL_GATE_TAXONOMY.json (taxonomy drift — a chk() was added or "
    "renamed in qa/assert_behavioral.py without updating the taxonomy). Grep assert_behavioral.py "
    "for the check name to read its assertion + detail string, then add it to the taxonomy."
)
_UNKNOWN_LOCATIONS = ["qa/assert_behavioral.py", "qa/BEHAVIORAL_GATE_TAXONOMY.json"]


def _load_taxonomy(path: Path = _TAXONOMY_PATH) -> dict[str, Any]:
    """Load the check taxonomy. Tolerant: a missing/garbled file yields an empty checks map so
    every check degrades to UNKNOWN rather than crashing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"checks": {}}
    if not isinstance(data, dict) or not isinstance(data.get("checks"), dict):
        return {"checks": {}}
    return data


# Parse a gate row like ``  [FAIL] xp_not_orphaned — detail`` / ``  [WARN] world_peopled``.
_ROW_RE = re.compile(r"^\s*\[(PASS|FAIL|WARN)\]\s+([a-z0-9_]+)")


def failed_checks_from_gate(path: Path, include_warnings: bool = False) -> list[str]:
    """The FAILED check names from a <run>.gate.txt, in file order (de-duped, order preserved).

    Reads only the per-check ``[FAIL]`` rows assert_behavioral.py prints (the WARN/PASS rows are
    not RED causes). With ``include_warnings`` the ``[WARN]`` rows are appended too. A missing /
    unreadable / empty gate yields [] — never raises."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    wanted = {"FAIL"} | ({"WARN"} if include_warnings else set())
    seen: list[str] = []
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        mark, name = m.group(1), m.group(2)
        if mark in wanted and name not in seen:
            seen.append(name)
    return seen


def gate_verdict(path: Path) -> Optional[str]:
    """The gate's own verdict: 'RED' if any [FAIL] row, else 'GREEN', else None (no gate).

    Mirrors qa/collect_findings.parse_gate's derive-from-markers logic but normalizes to the bare
    RED / GREEN token the analyzer reports."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    rows = [m for line in text.splitlines() if (m := _ROW_RE.match(line))]
    if not rows and not text.strip():
        return None
    return "RED" if any(m.group(1) == "FAIL" for m in rows) else "GREEN"


def resolve_run_gate(run_id: str, transcripts: Path = _DEFAULT_TRANSCRIPTS) -> Optional[Path]:
    """Map a run id to its on-disk gate file (``<transcripts>/<run_id>.gate.txt``). None if absent.

    This is the run_duo.sh / run_combat_sprint.sh convention: each run tees its gate text to
    ``qa/transcripts/<RUN>.gate.txt``. Read-only; never creates the dir or file."""
    candidate = Path(transcripts) / f"{run_id}.gate.txt"
    return candidate if candidate.exists() else None


def analyze_checks(check_names: list[str], taxonomy: Optional[dict] = None) -> list[dict[str, Any]]:
    """One structured report per check, in input order. Known checks map through the taxonomy;
    an unknown check degrades to category UNKNOWN with a generic hint + safe retest (never crash).

    Each report: {check, category, likely_code_locations, retest, hint, known}."""
    taxo = taxonomy if taxonomy is not None else _load_taxonomy()
    checks = taxo.get("checks", {})
    out: list[dict[str, Any]] = []
    for name in check_names:
        entry = checks.get(name)
        if isinstance(entry, dict) and entry.get("category") in _VALID_CATEGORIES:
            out.append({
                "check": name,
                "category": entry["category"],
                "likely_code_locations": list(entry.get("likely_code_locations") or []),
                "retest": str(entry.get("retest") or _UNKNOWN_RETEST),
                "hint": str(entry.get("hint") or _UNKNOWN_HINT),
                "known": True,
            })
        else:
            out.append({
                "check": name,
                "category": _UNKNOWN_CATEGORY,
                "likely_code_locations": list(_UNKNOWN_LOCATIONS),
                "retest": _UNKNOWN_RETEST,
                "hint": _UNKNOWN_HINT,
                "known": False,
            })
    return out


def _summarize(reports: list[dict[str, Any]], verdict: str) -> str:
    """A one-line summary: the verdict, the failed-check count, and a category tally."""
    if not reports:
        return f"{verdict}: 0 actionable check(s)."
    tally: dict[str, int] = {}
    for r in reports:
        tally[r["category"]] = tally.get(r["category"], 0) + 1
    cats = ", ".join(f"{c}={n}" for c, n in sorted(tally.items()))
    names = ", ".join(r["check"] for r in reports)
    return f"{verdict}: {len(reports)} actionable check(s) [{cats}] — {names}"


def analyze_gate(path: Path, include_warnings: bool = False) -> dict[str, Any]:
    """End-to-end on a gate file: parse the FAILED checks, map each, roll up the verdict + summary."""
    failed = failed_checks_from_gate(path, include_warnings=include_warnings)
    verdict = gate_verdict(path) or "UNKNOWN"
    reports = analyze_checks(failed)
    return {
        "source": str(path),
        "verdict": verdict,
        "failed_count": len(reports),
        "summary": _summarize(reports, verdict),
        "reports": reports,
    }


def _render_human(result: dict[str, Any]) -> str:
    """A compact, copy-paste-friendly human report for the agent's terminal."""
    lines = [
        f"=== root-cause analysis ({result.get('source', 'checks')}) ===",
        result["summary"],
        "",
    ]
    if not result["reports"]:
        lines.append("No failed checks to analyze (GREEN, or only warnings).")
        return "\n".join(lines)
    for r in result["reports"]:
        flag = "" if r.get("known", True) else "  [!! UNKNOWN check — taxonomy may be stale]"
        lines.append(f"• {r['check']}  [{r['category']}]" + flag)
        lines.append(f"    locations : {', '.join(r['likely_code_locations'])}")
        lines.append(f"    retest    : {r['retest']}")
        lines.append(f"    hint      : {r['hint']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="root_cause_analyzer.py",
        description="Turn a behavioral-gate RED into actionable code locations + retest commands.",
    )
    src = parser.add_mutually_exclusive_group(required=False)
    src.add_argument("--gate", help="path to a <run>.gate.txt to analyze")
    src.add_argument("--checks", help="comma-separated check names to analyze directly")
    src.add_argument("--run", help="a run id; reads <transcripts>/<run>.gate.txt")
    parser.add_argument("--transcripts", default=str(_DEFAULT_TRANSCRIPTS),
                        help="transcripts dir for --run (default: qa/transcripts)")
    parser.add_argument("--include-warnings", action="store_true",
                        help="also surface [WARN] rows, not just [FAIL]")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a human report")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.checks is not None:
        names = [n.strip() for n in args.checks.split(",") if n.strip()]
        reports = analyze_checks(names)
        result = {
            "source": "--checks",
            "verdict": "RED" if reports else "GREEN",
            "failed_count": len(reports),
            "summary": _summarize(reports, "RED" if reports else "GREEN"),
            "reports": reports,
        }
    elif args.gate is not None:
        result = analyze_gate(Path(args.gate), include_warnings=args.include_warnings)
    elif args.run is not None:
        gate_path = resolve_run_gate(args.run, transcripts=Path(args.transcripts))
        if gate_path is None:
            print(f"error: no gate file for run {args.run!r} under {args.transcripts}", file=sys.stderr)
            return 2
        result = analyze_gate(gate_path, include_warnings=args.include_warnings)
    else:
        parser.print_usage(sys.stderr)
        print("error: one of --gate / --checks / --run is required", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2) if args.json else _render_human(result), end="" if args.json else "")
    if args.json:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
