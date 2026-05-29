#!/usr/bin/env python3
"""Aggregate N WorldOS AI-playtest runs into a release-readiness summary (issue #324 v2).

The v1 scorer (qa/ui_playtest_score.py) scores ONE persona run. This aggregator combines
several persona runs — the v2 panel (newbie / veteran / adversarial / narrative / optimizer)
— into a single release-readiness picture:

  * per-persona PASS/FAIL + satisfaction + did-they-play, side by side;
  * every bug clustered ACROSS personas and ranked by HOW MANY PERSONAS HIT IT.
    The natural prioritization from the persona strategy: a defect that EVERY persona
    trips over is a P0; one that only a single persona notices is a P3. Severity (the
    persona's own critical/major/minor/trivial) breaks ties within a priority band.
  * the by-design missing-image 404 noise collapsed into a single advisory line.

Pure reader: never imports the engine, never touches campaign state. Reads only each run's
score.json + bugs.ndjson + meta.json.

Usage:
  ui_playtest_aggregate.py <sweep-dir>
      Treats every immediate subdirectory of <sweep-dir> that has a score.json as a run.
      Writes <sweep-dir>/RELEASE_SUMMARY.md.

  ui_playtest_aggregate.py --runs <run-dir> [<run-dir> ...] --out <sweep-dir>
      Aggregate an explicit list of run dirs; write RELEASE_SUMMARY.md under --out.

Output: <sweep>/RELEASE_SUMMARY.md (+ <sweep>/release_summary.json with the raw aggregate).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "trivial": 3}
# Priority band names by how many personas hit a clustered bug. Index by (n_personas - 1),
# clamped to the last band. P0 = all five personas; falls off to P3 for a lone finding.
PRIORITY_BY_HITS = ["P3", "P2", "P1", "P0", "P0"]


def read_ndjson(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
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


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# A bug's cross-persona cluster KEY. Different personas describe the same defect in different
# words, so we fingerprint on (screen, category) + a small bag of salient content keywords.
# This is deliberately coarse — better to merge two near-identical reports than to split one
# defect across personas (which would understate its priority). The raw per-persona text is
# always preserved under the cluster for the human reader.
_STOP = {
    "the", "a", "an", "to", "of", "and", "or", "is", "are", "was", "were", "be", "been", "it",
    "this", "that", "with", "for", "on", "in", "at", "as", "by", "from", "i", "you", "my", "me",
    "but", "not", "no", "do", "did", "does", "can", "cannot", "could", "would", "should", "when",
    "what", "which", "screen", "button", "click", "clicked", "page", "see", "saw", "show", "shows",
    "showed", "expected", "actual", "actually", "nothing", "happened", "happens", "there", "here",
    "have", "has", "had", "get", "got", "one", "any", "all", "they", "their", "its", "into", "out",
    "up", "so", "if", "than", "then", "just", "like", "only", "also", "after", "before", "still",
}


# A few synonym families collapse the words different personas reach for when they mean the
# same thing — so "dead button" and "control did nothing" cluster. Each family maps to a canon.
_SYNONYM = {
    "dead": "dead", "unresponsive": "dead", "nothing": "dead", "inert": "dead", "lying": "dead",
    "button": "control", "tab": "control", "toggle": "control", "radio": "control",
    "chip": "control", "control": "control", "slider": "control", "link": "control",
    "proficiency": "proficiency", "proficient": "proficiency", "expertise": "proficiency",
    "marker": "marker", "markers": "marker", "diamond": "marker", "diamonds": "marker",
    "dot": "marker", "dots": "marker",
    "missing": "missing", "absent": "missing", "empty": "missing", "blank": "missing",
    "inspector": "inspector", "tooltip": "inspector", "hover": "inspector",
    "compare": "compare", "comparison": "compare", "delta": "compare",
    "duplicate": "duplicate", "duplicated": "duplicate", "double": "duplicate", "doubled": "duplicate",
    "crash": "crash", "crashed": "crash", "froze": "crash", "frozen": "crash", "hang": "crash",
}


def _tokens(text: str) -> set[str]:
    """Salient, synonym-normalized tokens for a bug's text — order-independent."""
    words = re.findall(r"[a-z][a-z0-9\-]{2,}", (text or "").lower())
    out: set[str] = set()
    for w in words:
        if w in _STOP:
            continue
        out.add(_SYNONYM.get(w, w))
    return out


def bug_tokens(bug: dict) -> set[str]:
    return _tokens(f"{bug.get('title','')} {bug.get('expected','')} {bug.get('actual','')}")


def _overlap(a: set[str], b: set[str]) -> float:
    """Jaccard-ish overlap, normalized by the SMALLER set so a terse title still matches a
    verbose one when its few tokens are all present in the other."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b))

# Two findings on the same (screen, category) merge when their normalized token sets overlap
# at least this much. 0.5 = at least half the smaller report's salient tokens are shared —
# coarse on purpose (under-splitting a defect is better than over-splitting it across personas).
_MERGE_THRESHOLD = 0.5


def is_image_404_bug(bug: dict) -> bool:
    """The by-design missing-art noise: auto-captured network 404s on the image endpoint,
    or any finding explicitly about a missing portrait/scene placeholder."""
    if bug.get("source") == "auto" and bug.get("category") == "network":
        ev = bug.get("evidence") or {}
        url = str(ev.get("url") or "")
        if ev.get("status") == 404 and "/image?scope=" in url:
            return True
        if "/image?scope=" in str(bug.get("actual") or ""):
            return True
    blob = f"{bug.get('title','')} {bug.get('expected','')} {bug.get('actual','')}".lower()
    if "/image?scope=" in blob:
        return True
    return False


def discover_runs(sweep: Path) -> list[Path]:
    runs = []
    for child in sorted(sweep.iterdir()):
        if child.is_dir() and (child / "score.json").exists():
            runs.append(child)
    return runs


def merge_cluster_text(cluster: dict) -> None:
    """Pick the most informative representative title/expected/actual for a cluster:
    the longest non-empty string across member reports (more detail = better digest)."""
    def longest(field: str) -> str:
        vals = [str(b.get(field) or "").strip() for b in cluster["bugs"]]
        vals = [v for v in vals if v]
        return max(vals, key=len) if vals else ""

    cluster["title"] = longest("title") or "(untitled)"
    cluster["expected"] = longest("expected")
    cluster["actual"] = longest("actual")


def main() -> int:
    ap = argparse.ArgumentParser(description="Aggregate WorldOS AI-playtest persona runs.")
    ap.add_argument("sweep", nargs="?", help="Sweep dir whose subdirs are runs.")
    ap.add_argument("--runs", nargs="+", help="Explicit run dirs to aggregate.")
    ap.add_argument("--out", help="Output sweep dir (required with --runs).")
    args = ap.parse_args()

    if args.runs:
        if not args.out:
            print("error: --out <sweep-dir> is required with --runs", file=sys.stderr)
            return 2
        run_dirs = [Path(r) for r in args.runs]
        sweep = Path(args.out)
        sweep.mkdir(parents=True, exist_ok=True)
    elif args.sweep:
        sweep = Path(args.sweep)
        if not sweep.is_dir():
            print(f"error: sweep dir not found: {sweep}", file=sys.stderr)
            return 2
        run_dirs = discover_runs(sweep)
    else:
        ap.print_usage(sys.stderr)
        return 2

    run_dirs = [r for r in run_dirs if (r / "score.json").exists()]
    if not run_dirs:
        print("error: no runs with a score.json found", file=sys.stderr)
        return 2

    personas: list[dict] = []
    # Greedy overlap clustering bucketed by (screen, category). Each bucket holds a list of
    # clusters; a new bug joins the best-overlapping cluster above threshold, else starts one.
    buckets: dict[tuple, list[dict]] = {}
    image_404_runs: Counter = Counter()  # persona -> count of collapsed image-404 findings

    def add_to_clusters(bug: dict, persona: str) -> None:
        screen = (bug.get("screen") or "?").strip().lower()
        cat = (bug.get("category") or "?").strip().lower()
        toks = bug_tokens(bug)
        bucket = buckets.setdefault((screen, cat), [])
        best, best_ov = None, 0.0
        for cl in bucket:
            ov = _overlap(toks, cl["tokens"])
            if ov > best_ov:
                best, best_ov = cl, ov
        if best is not None and best_ov >= _MERGE_THRESHOLD:
            best["bugs"].append(bug)
            best["personas"].add(persona)
            best["tokens"] |= toks
            if SEV_RANK.get(bug.get("severity", "trivial"), 3) < SEV_RANK.get(best["worst_sev"], 3):
                best["worst_sev"] = bug.get("severity", "trivial")
        else:
            bucket.append({
                "screen": bug.get("screen", "?"), "category": bug.get("category", "?"),
                "bugs": [bug], "personas": {persona}, "tokens": set(toks),
                "worst_sev": bug.get("severity", "trivial"),
            })

    for rd in run_dirs:
        score = read_json(rd / "score.json")
        meta = read_json(rd / "meta.json")
        bugs = read_ndjson(rd / "bugs.ndjson")
        persona = score.get("persona") or meta.get("persona") or rd.name
        personas.append(
            {
                "persona": persona,
                "run": score.get("run") or meta.get("run") or rd.name,
                "run_dir": str(rd),
                "world": score.get("world") or meta.get("world"),
                "pass": bool(score.get("pass")),
                "satisfaction": score.get("persona_satisfaction"),
                "satisfaction_source": score.get("satisfaction_source"),
                "completed_intro_flow": bool(score.get("completed_intro_flow")),
                "reached_play_screen": bool(score.get("reached_play_screen")),
                "in_story_turns": score.get("in_story_turns"),
                "gave_up": bool(score.get("gave_up")),
                "dead_clicks": score.get("dead_clicks", 0),
                "console_errors": score.get("console_errors", 0),
                "network_failures": score.get("network_failures", 0),
                "image_404s": score.get("image_404s", 0),
                "bug_total": score.get("bug_reports_total", 0),
                "critical": score.get("bug_reports_critical", 0),
                "major": score.get("bug_reports_major", 0),
                "cost_usd": score.get("player_cost_usd"),
            }
        )

        for b in bugs:
            if is_image_404_bug(b):
                image_404_runs[persona] += 1
                continue
            add_to_clusters(b, persona)

    # Finalize clusters: priority by #personas, representative text, sort.
    cluster_list = [cl for bucket in buckets.values() for cl in bucket]
    for cl in cluster_list:
        n = len(cl["personas"])
        cl["n_personas"] = n
        cl["priority"] = PRIORITY_BY_HITS[min(n, len(PRIORITY_BY_HITS)) - 1]
        merge_cluster_text(cl)

    def cluster_sort(cl: dict):
        # Most personas first, then worst severity, then most reports.
        return (-cl["n_personas"], SEV_RANK.get(cl["worst_sev"], 3), -len(cl["bugs"]))

    cluster_list.sort(key=cluster_sort)

    n_personas_total = len(personas)
    n_pass = sum(1 for p in personas if p["pass"])
    any_critical = any(p["critical"] for p in personas)
    p0_count = sum(1 for c in cluster_list if c["priority"] == "P0")
    p1_count = sum(1 for c in cluster_list if c["priority"] == "P1")

    # Release verdict: ship-ready only if every persona passed AND no P0/P1 cross-persona defect.
    release_ready = (n_pass == n_personas_total) and (p0_count == 0) and (p1_count == 0)

    aggregate = {
        "sweep": str(sweep),
        "n_personas": n_personas_total,
        "n_pass": n_pass,
        "any_critical": any_critical,
        "release_ready": release_ready,
        "p0": p0_count,
        "p1": p1_count,
        "p2": sum(1 for c in cluster_list if c["priority"] == "P2"),
        "p3": sum(1 for c in cluster_list if c["priority"] == "P3"),
        "image_404_collapsed": dict(image_404_runs),
        "image_404_total": sum(image_404_runs.values()),
        "personas": personas,
        "clusters": [
            {
                "priority": c["priority"],
                "n_personas": c["n_personas"],
                "personas": sorted(c["personas"]),
                "screen": c["screen"],
                "category": c["category"],
                "worst_severity": c["worst_sev"],
                "title": c["title"],
                "expected": c["expected"],
                "actual": c["actual"],
                "report_count": len(c["bugs"]),
            }
            for c in cluster_list
        ],
    }
    (sweep / "release_summary.json").write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
    (sweep / "RELEASE_SUMMARY.md").write_text(build_md(aggregate), encoding="utf-8")
    print(f"[aggregate] wrote {sweep/'RELEASE_SUMMARY.md'} "
          f"({n_pass}/{n_personas_total} personas passed, {p0_count} P0, {p1_count} P1, "
          f"release_ready={release_ready})")
    return 0


def build_md(agg: dict) -> str:
    L: list[str] = []
    verdict = "READY" if agg["release_ready"] else "NOT READY"
    L.append("# WorldOS AI Playtest — Release Readiness Summary")
    L.append("")
    L.append(f"**Verdict: {verdict}**  ·  {agg['n_pass']}/{agg['n_personas']} personas passed  ·  "
             f"P0 **{agg['p0']}** · P1 **{agg['p1']}** · P2 **{agg['p2']}** · P3 **{agg['p3']}** "
             f"cross-persona findings")
    L.append("")
    L.append("Release gate: every persona passes its own run AND there is no P0/P1 defect "
             "(a defect hit by 3+ personas). Priority = **how many personas hit a defect** "
             "(all = P0, one = P3); the persona's own severity breaks ties.")
    L.append("")

    # --- per-persona table ---------------------------------------------------
    L.append("## Per-persona scorecard")
    L.append("")
    L.append("| Persona | Pass | Satisfaction | Played? | Turns | Dead clicks | Console err | "
             "Critical bugs | Total bugs | Cost |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for p in agg["personas"]:
        played = "yes" if p["completed_intro_flow"] else ("reached play" if p["reached_play_screen"] else "no")
        if p["gave_up"]:
            played += " (gave up)"
        sat = p.get("satisfaction")
        sat_s = f"{sat}/10" if sat is not None else "—"
        cost = p.get("cost_usd")
        cost_s = f"${cost:.2f}" if isinstance(cost, (int, float)) else "—"
        L.append(
            f"| **{p['persona']}** | {'PASS' if p['pass'] else 'FAIL'} | {sat_s} | {played} | "
            f"{p.get('in_story_turns') or 0} | {p.get('dead_clicks') or 0} | "
            f"{p.get('console_errors') or 0} | {p.get('critical') or 0} | {p.get('bug_total') or 0} | "
            f"{cost_s} |"
        )
    L.append("")

    # --- cross-persona ranked findings --------------------------------------
    L.append("## Cross-persona findings (ranked by how many personas hit them)")
    L.append("")
    if not agg["clusters"]:
        L.append("_No persona-reported findings (after collapsing by-design image-404 noise)._")
        L.append("")
    else:
        L.append("| Priority | Hit by | Personas | Screen | Worst sev | Finding |")
        L.append("|---|---|---|---|---|---|")
        for c in agg["clusters"]:
            who = ", ".join(c["personas"])
            title = c["title"].replace("|", "\\|")
            L.append(
                f"| **{c['priority']}** | {c['n_personas']}/{agg['n_personas']} | {who} | "
                f"{c['screen']} | {c['worst_severity']} | {title} |"
            )
        L.append("")

        # Detail blocks for the top tier (P0/P1) so the reader sees expected/actual.
        top = [c for c in agg["clusters"] if c["priority"] in ("P0", "P1")]
        if top:
            L.append("### Top-priority detail (P0/P1)")
            L.append("")
            for c in top:
                L.append(f"- **[{c['priority']}]** ({c['screen']}, {c['worst_severity']}, "
                         f"hit by {c['n_personas']}/{agg['n_personas']}: {', '.join(c['personas'])}) "
                         f"{c['title']}")
                if c["expected"]:
                    L.append(f"    - expected: {c['expected']}")
                if c["actual"]:
                    L.append(f"    - actual: {c['actual']}")
            L.append("")

    # --- collapsed noise -----------------------------------------------------
    if agg["image_404_total"]:
        by = ", ".join(f"{k} ({v})" for k, v in sorted(agg["image_404_collapsed"].items()))
        L.append("## Collapsed by-design noise")
        L.append("")
        L.append(f"- **Missing-image 404s** (graceful degradation: silhouettes/placeholders for "
                 f"un-ingested portrait/scene art) — **{agg['image_404_total']}** findings collapsed "
                 f"into this one advisory line; not prioritized. Per persona: {by}.")
        L.append("")

    L.append("---")
    L.append("_Generated by qa/ui_playtest_aggregate.py. Per-persona runs: "
             + ", ".join(p["run_dir"] for p in agg["personas"]) + "._")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
