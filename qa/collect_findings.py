#!/usr/bin/env python3
"""On-demand QA findings collector — ONE queryable place for score trends + verdicts.

The QA harness (run_duo.sh / run_combat_sprint.sh / score.sh) drops per-run score artifacts
into qa/transcripts/ — but each run's verdict is scattered across several JSON files plus a gate
.txt, and the dir is a heap of every run ever played. This script is a READ-ONLY collector: it
scans qa/transcripts/ for every scored run, distills each into one compact row, and maintains an
append-only qa/findings.jsonl so the score-loop debugging culture has a single grep-able ledger
("did multiattack regress?", "which runs went RED?") WITHOUT touching the harness scripts.

It NEVER runs a game, scores a transcript, or mutates any harness artifact — it only reads the
scorecards the harness already wrote and refreshes findings.jsonl.

Per-run artifacts it reads (filenames per run_duo.sh / run_combat_sprint.sh):
    <run>.angrydm.json   Angry-DM 5e rules-fidelity scorecard (score_schema_angry_dm.json)
                         — every scored run has one (sprint + duo).
    <run>.tolkien.json   story / Tolkien story-craft scorecard (duo runs)  -> scores.story
    <run>.score.json     mechanical scorecard (duo runs)                   -> scores.mechanical
    <run>.gate.txt       behavioral-gate output; its final line is the GREEN/RED verdict.

Each findings.jsonl row:
    {run, mtime, gate, scores:{story, mechanical, angry_dm}, angry_overall, top_defects:[...]}

Idempotent: re-running refreshes the rows for runs currently on disk and dedupes by run; rows
for runs whose artifacts have since been cleaned are preserved (append-only ledger).

Usage:  python qa/collect_findings.py            # from the repo root
        python qa/collect_findings.py --transcripts <dir> --out <findings.jsonl>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

# Per-run artifact suffixes. The angry-DM card anchors discovery (every scored run has one), but
# we also anchor on the other score artifacts so a run that somehow lacks an angry-DM card is
# still surfaced rather than silently dropped.
SUFFIX_ANGRYDM = ".angrydm.json"
SUFFIX_TOLKIEN = ".tolkien.json"   # story / Tolkien story-craft
SUFFIX_MECH = ".score.json"        # mechanical
SUFFIX_GATE = ".gate.txt"

# Worst-first ordering so "top defects" surfaces the most severe seams.
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# How many defect summaries to keep per run.
TOP_DEFECTS = 3


def _load_json(path: Path) -> Optional[dict]:
    """Read a JSON object, or None if absent / unreadable / not an object. Tolerant by design:
    a half-written or non-JSON scorecard must not crash the whole collection."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _overall(card: Optional[dict]) -> Optional[float]:
    """The `overall` score off a scorecard, as a float, or None if absent/unparseable.

    Note: on a behavioral-gate RED run the harness CAPS `overall` to 2.5 and records the
    pre-cap value in `overall_before_cap`; we report the (capped) `overall` — the honest,
    displayed score — on purpose."""
    if not card:
        return None
    try:
        return float(card["overall"])
    except (KeyError, TypeError, ValueError):
        return None


def _defect_summary(defect: dict) -> str:
    """One compact line for a single Angry-DM defect: '[severity/kind] area — rule: evidence'.

    Every field is optional in practice; we degrade gracefully and truncate the (often long)
    evidence so a row stays grep-able."""
    sev = str(defect.get("severity", "?"))
    kind = defect.get("kind")
    area = str(defect.get("area", "?"))
    rule = defect.get("rule")
    evidence = str(defect.get("evidence", "")).strip().replace("\n", " ")
    if len(evidence) > 200:
        evidence = evidence[:197].rstrip() + "..."
    head = f"[{sev}/{kind}]" if kind else f"[{sev}]"
    rule_part = f" — {rule}" if rule else ""
    ev_part = f": {evidence}" if evidence else ""
    return f"{head} {area}{rule_part}{ev_part}"


def parse_angrydm(card: Optional[dict]) -> tuple[Optional[float], list[str]]:
    """Pull (overall, top_defects) out of an Angry-DM scorecard dict.

    `top_defects` is up to TOP_DEFECTS one-line summaries, worst severity first (then the card's
    own order). Pure + tolerant so it can be unit-tested against a real <run>.angrydm.json: a
    missing/empty/malformed card yields (None, []); a missing `defects` list yields []."""
    overall = _overall(card)
    defects_raw = (card or {}).get("defects")
    if not isinstance(defects_raw, list):
        return overall, []
    defects = [d for d in defects_raw if isinstance(d, dict)]
    # Stable sort by severity rank (unknown severities sort last); preserves file order within a rank.
    ordered = sorted(defects, key=lambda d: _SEVERITY_RANK.get(str(d.get("severity", "")).lower(), 99))
    return overall, [_defect_summary(d) for d in ordered[:TOP_DEFECTS]]


def parse_gate(path: Path) -> Optional[str]:
    """The behavioral-gate verdict from <run>.gate.txt. None if the file is absent/empty.

    assert_behavioral.py USUALLY prints a trailing summary line ('GREEN', 'GREEN (1 warning(s))',
    'RED ...'); we use it verbatim when present. But some gate files end at the last assertion
    line with no summary footer — so we DON'T just take the last line (that would mistake a
    trailing '[PASS] world_peopled' for the verdict). When no summary line is present we DERIVE
    the verdict from the assertion markers themselves: any [FAIL] -> RED, else any [WARN] ->
    GREEN (n warning(s)), else GREEN. Self-contained and authoritative on the gate's own output."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    # 1. An explicit trailing verdict line (a line that STARTS with GREEN/RED and is not an
    #    assertion row) wins — it's what the harness prints and may carry a warning count.
    for ln in reversed(lines):
        up = ln.upper()
        if up.startswith("GREEN") or up.startswith("RED"):
            return ln

    # 2. No summary footer — derive from the assertion markers.
    fails = sum(1 for ln in lines if "[FAIL]" in ln)
    warns = sum(1 for ln in lines if "[WARN]" in ln)
    if fails:
        return "RED"
    if warns:
        return f"GREEN ({warns} warning(s))"
    return "GREEN"


def _run_mtime(paths: list[Path]) -> float:
    """The NEWEST mtime across a run's present artifacts — i.e. when this run was last scored."""
    mtimes = [p.stat().st_mtime for p in paths if p.exists()]
    return max(mtimes) if mtimes else 0.0


def collect(transcripts: Path) -> list[dict[str, Any]]:
    """Scan a transcripts dir and return one findings row per scored run, sorted by run id.

    A 'scored run' is any stem that carries at least one score artifact (angry-DM, Tolkien, or
    mechanical). A run missing some artifacts is fine — the absent score is reported as null."""
    if not transcripts.is_dir():
        return []

    # Discover run ids from the union of score-artifact stems (anchor on angry-DM but don't miss
    # a run that only has another lens).
    runs: set[str] = set()
    for suffix in (SUFFIX_ANGRYDM, SUFFIX_TOLKIEN, SUFFIX_MECH):
        for p in transcripts.glob(f"*{suffix}"):
            runs.add(p.name[: -len(suffix)])

    rows: list[dict[str, Any]] = []
    for run in sorted(runs):
        angrydm_path = transcripts / f"{run}{SUFFIX_ANGRYDM}"
        tolkien_path = transcripts / f"{run}{SUFFIX_TOLKIEN}"
        mech_path = transcripts / f"{run}{SUFFIX_MECH}"
        gate_path = transcripts / f"{run}{SUFFIX_GATE}"

        angrydm_card = _load_json(angrydm_path)
        angry_overall, top_defects = parse_angrydm(angrydm_card)

        rows.append({
            "run": run,
            "mtime": round(_run_mtime([angrydm_path, tolkien_path, mech_path, gate_path]), 3),
            "gate": parse_gate(gate_path),
            "scores": {
                "story": _overall(_load_json(tolkien_path)),
                "mechanical": _overall(_load_json(mech_path)),
                "angry_dm": angry_overall,
            },
            "angry_overall": angry_overall,
            "top_defects": top_defects,
        })
    return rows


def merge_rows(existing: list[dict], fresh: list[dict]) -> list[dict]:
    """Merge freshly-scanned rows into existing ones, deduped by run (fresh wins). Rows for runs
    no longer on disk are PRESERVED — findings.jsonl is an append-only ledger across invocations.
    Result is sorted by run id for a stable, diff-friendly file."""
    by_run: dict[str, dict] = {}
    for row in existing:
        if isinstance(row, dict) and "run" in row:
            by_run[row["run"]] = row
    for row in fresh:
        by_run[row["run"]] = row  # refresh / dedupe by run
    return [by_run[r] for r in sorted(by_run)]


def _read_existing(out_path: Path) -> list[dict]:
    """Load prior findings.jsonl rows (skipping any blank/corrupt line), or [] if absent."""
    if not out_path.exists():
        return []
    rows: list[dict] = []
    for line in out_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def write_jsonl(out_path: Path, rows: list[dict]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _fmt(score: Optional[float]) -> str:
    return f"{score:.1f}" if isinstance(score, (int, float)) else "  -"


def print_table(rows: list[dict]) -> None:
    """A compact stdout table: run, the three lens scores, gate verdict, and defect count."""
    if not rows:
        print("(no scored runs found in transcripts dir)")
        return
    name_w = max(3, max(len(str(r.get("run", ""))) for r in rows))
    header = f"{'run':<{name_w}}  story  mech  angry  gate"
    print(header)
    print("-" * len(header))
    for r in rows:
        scores = r.get("scores") or {}
        # The gate verdict can be long; keep just the leading token (GREEN/RED) for the table.
        gate = r.get("gate")
        gate_tok = gate.split()[0] if isinstance(gate, str) and gate else "-"
        n_def = len(r.get("top_defects") or [])
        defs = f"  ({n_def} top defect{'s' if n_def != 1 else ''})" if n_def else ""
        print(
            f"{str(r.get('run','')):<{name_w}}  "
            f"{_fmt(scores.get('story')):>5}  "
            f"{_fmt(scores.get('mechanical')):>4}  "
            f"{_fmt(scores.get('angry_dm')):>5}  "
            f"{gate_tok:<5}{defs}"
        )
    print(f"\n{len(rows)} run(s).")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect QA score findings into findings.jsonl.")
    parser.add_argument(
        "--transcripts", default="qa/transcripts",
        help="directory of per-run score artifacts (default: qa/transcripts)",
    )
    parser.add_argument(
        "--out", default="qa/findings.jsonl",
        help="append-only findings ledger to write/refresh (default: qa/findings.jsonl)",
    )
    args = parser.parse_args(argv)

    transcripts = Path(args.transcripts)
    out_path = Path(args.out)

    fresh = collect(transcripts)
    merged = merge_rows(_read_existing(out_path), fresh)
    write_jsonl(out_path, merged)

    print_table(merged)
    print(f"\nwrote {out_path} ({len(fresh)} run(s) scanned from {transcripts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
