#!/usr/bin/env python3
"""Generate Markdown release notes for a WorldOS milestone — Versioning Phase-1.

WHY THIS EXISTS
---------------
"Complete a milestone → tag/release" needs a repeatable, evidence-linked notes generator that
reads the SAME canonical sources the rest of QA reasons over — the scores ledger (qa/scores.db
via qa/scores_db.py) for the latest RRI verdict + story/mech/sat + ruler versions, and an
optional per-gate RRI artifact (a release_readiness.py RRI.json or the
release_readiness_verdict.json scores_db emits) for the 11 individual gate statuses.

The load-bearing output is the **DEVELOPMENT-vs-RELEASE flag**, computed from the 11 canonical
RRI gates (qa/release_readiness.py: the 7 LLM/persona gates + the 4 base deterministic gates):

    * if ANY of the 11 gates is SKIPPED / FAILED / MISSING (i.e. not PASSED)
        → ``**STATUS: DEVELOPMENT** (gates skipped: …)``
    * only when ALL 11 are PASSED
        → ``**STATUS: RELEASE**``

This catches ad-hoc rc tagging that skips the formal RRI: a tag cut from a partial / deterministic-
only / never-run sweep can never masquerade as a clean RELEASE. When NO per-gate artifact is
supplied, the generator falls back to the ledger row's coarse signals (RRI value + behavioral +
story/mech thresholds) and clearly says the per-gate evidence was inferred, never PASSED-by-default.

READ-ONLY: never mutates qa/scores.db (it only calls scores_db.fetch_rows / the verdict reader).

USAGE
-----
    python3 qa/generate_release_notes.py [--tag vX.Y.Z] [--milestone "v1.0.5"] \
        [--rri-json qa/RRI.json | --verdict-json release_readiness_verdict.json] \
        [--db qa/scores.db] [--repo electricsheephq/WorldOS] [--out RELEASE_NOTES.md] \
        [--no-issues]

With a ``--milestone``, the closed-issue list is pulled via ``gh issue list --milestone`` (skipped
with ``--no-issues`` or when ``gh`` is unavailable — the notes still generate, just without that
section). ``--out`` writes the Markdown; otherwise it prints to stdout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402  (the canonical ledger reader; READ-ONLY use here)

# The 11 canonical RRI gates — re-exported from scores_db so there is ONE definition.
RRI_CANONICAL_GATES = scores_db.RRI_CANONICAL_GATES


def _read_version() -> str:
    """Best-effort read of the repo-root VERSION file (the product version mirror)."""
    vf = QA_DIR.parent / "VERSION"
    try:
        return vf.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def latest_rri_row(rows: list[dict], rc_label: Optional[str] = None) -> Optional[dict]:
    """The most-recent ledger row that carries an RRI value (optionally scoped to an rc_label).

    ``rows`` is scores_db.fetch_rows() order (newest-first), so the first match is the latest."""
    for r in rows:
        if r.get("rri") is None:
            continue
        if rc_label and (r.get("rc_label") or "") != rc_label:
            continue
        return r
    return None


def _gate_statuses_from_artifact(
    *, rri_json: Optional[str], verdict_json: Optional[str], db_path, build_sha: Optional[str]
) -> tuple[Optional[dict], str]:
    """Return ({gate: status}, source-label) from a per-gate artifact, or (None, "") if none.

    Accepts EITHER a release_readiness.py RRI.json (converted via
    scores_db.release_readiness_verdict) OR an already-emitted release_readiness_verdict.json.
    A verdict JSON wins if both are given (it is the more specific, canonical artifact)."""
    if verdict_json:
        try:
            payload = json.loads(Path(verdict_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not read verdict JSON {verdict_json}: {exc}") from exc
        gates = payload.get("gates") if isinstance(payload.get("gates"), dict) else {}
        return ({name: (gates.get(name) or {}).get("status", "MISSING")
                 for name in RRI_CANONICAL_GATES}, f"verdict-json={verdict_json}")
    if rri_json:
        verdict = scores_db.release_readiness_verdict(rri_json, db_path=db_path, build_sha=build_sha)
        gates = verdict.get("gates") or {}
        return ({name: (gates.get(name) or {}).get("status", "MISSING")
                 for name in RRI_CANONICAL_GATES}, f"rri-json={rri_json}")
    return None, ""


def _gate_statuses_inferred(row: Optional[dict]) -> tuple[dict, str]:
    """Coarse per-gate inference from a ledger row when NO per-gate artifact exists.

    The ledger row carries only rolled-up signals (rri / story / mech / behavioral), NOT the 11
    individual gate booleans. So we can prove a HANDFUL of gates from it and must mark the rest
    UNKNOWN — which is deliberately NOT "PASSED", so an inferred verdict can never claim RELEASE.
    This keeps the discipline honest: only a real RRI artifact can certify RELEASE."""
    statuses = {name: "UNKNOWN" for name in RRI_CANONICAL_GATES}
    if row is None:
        return statuses, "no-evidence"
    story = row.get("story_overall")
    mech = row.get("mech_overall")
    behav = row.get("behavioral")
    if isinstance(story, (int, float)):
        statuses["story_craft"] = "PASSED" if story >= 4.3 else "FAILED"
    if isinstance(mech, (int, float)):
        statuses["mechanical"] = "PASSED" if mech >= 4.5 else "FAILED"
    if behav in ("GREEN", "RED"):
        statuses["behavioral"] = "PASSED" if behav == "GREEN" else "FAILED"
    return statuses, "ledger-inferred"


def development_or_release(gate_statuses: dict) -> tuple[str, list[str]]:
    """The load-bearing flag: RELEASE only if ALL 11 gates PASSED; else DEVELOPMENT.

    Returns (status, not_passed_gate_names). Any status other than the literal "PASSED" (FAILED /
    SKIPPED / MISSING / UNKNOWN) blocks a RELEASE verdict — the catch for ad-hoc rc tagging."""
    not_passed = [name for name in RRI_CANONICAL_GATES
                  if gate_statuses.get(name) != "PASSED"]
    return ("RELEASE" if not not_passed else "DEVELOPMENT"), not_passed


def closed_issues(milestone: str, repo: Optional[str]) -> tuple[list[dict], str]:
    """Closed issues for a milestone via `gh issue list`. Returns (issues, note).

    Tolerant: a missing `gh`, an auth error, or any nonzero exit yields ([], <reason>) so the
    notes still generate. ``repo`` is passed to `--repo` when given (else gh's default repo)."""
    cmd = ["gh", "issue", "list", "--milestone", milestone, "--state", "closed",
           "--limit", "200", "--json", "number,title,url"]
    if repo:
        cmd += ["--repo", repo]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return [], "gh CLI not available"
    except subprocess.TimeoutExpired:
        return [], "gh issue list timed out"
    if proc.returncode != 0:
        return [], f"gh issue list failed: {proc.stderr.strip()[:200] or 'nonzero exit'}"
    try:
        issues = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return [], "gh issue list returned unparseable JSON"
    if not isinstance(issues, list):
        return [], "gh issue list returned non-list JSON"
    return issues, ""


def _fmt(v, dash: str = "—") -> str:
    if v is None or v == "":
        return dash
    if isinstance(v, float):
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s or "0"
    return str(v)


def render_notes(
    *,
    tag: Optional[str],
    milestone: Optional[str],
    row: Optional[dict],
    gate_statuses: dict,
    gate_source: str,
    issues: list[dict],
    issues_note: str,
    version: str,
) -> str:
    status, not_passed = development_or_release(gate_statuses)
    title = tag or milestone or version or "WorldOS release"
    out: list[str] = []
    out.append(f"# WorldOS Release Notes — {title}")
    out.append("")

    # The load-bearing DEVELOPMENT-vs-RELEASE flag, front and center.
    if status == "RELEASE":
        out.append("**STATUS: RELEASE** — all 11 RRI gates PASSED.")
    else:
        # Group the not-passed gates by why (skipped / failed / missing / unknown) for the detail.
        by_reason: dict[str, list[str]] = {}
        for name in not_passed:
            by_reason.setdefault(gate_statuses.get(name, "MISSING"), []).append(name)
        parts = []
        for reason in ("SKIPPED", "FAILED", "MISSING", "UNKNOWN"):
            if by_reason.get(reason):
                parts.append(f"{reason.lower()}: {', '.join(sorted(by_reason[reason]))}")
        out.append(f"**STATUS: DEVELOPMENT** (gates {' · '.join(parts)})")
    out.append("")
    out.append(f"> Per-gate evidence source: `{gate_source or 'none'}`. "
               f"A tag is RELEASE-grade only when ALL 11 RRI gates "
               f"(`{', '.join(RRI_CANONICAL_GATES)}`) PASSED in ONE formal sweep.")
    out.append("")

    # Milestone summary (the RRI verdict + lens numbers + ruler provenance from the ledger).
    out.append("## Milestone summary")
    out.append("")
    if row is not None:
        out.append(f"- **Version:** {version or '—'}")
        out.append(f"- **RRI:** {_fmt(row.get('rri'))}/10  "
                   f"(run `{_fmt(row.get('run_id'))}`, surface `{_fmt(row.get('surface'))}`)")
        out.append(f"- **Story-craft:** {_fmt(row.get('story_overall'))}/5  ·  "
                   f"**Mechanical:** {_fmt(row.get('mech_overall'))}/5  ·  "
                   f"**Angry-DM:** {_fmt(row.get('angrydm_overall'))}/5")
        out.append(f"- **Behavioral gate:** {_fmt(row.get('behavioral'))}  ·  "
                   f"**Cross-persona satisfaction:** {_fmt(row.get('cross_persona_sat'))}/10  ·  "
                   f"**Critical bugs:** {_fmt(row.get('critical_bugs'))}")
        out.append(f"- **Build SHA:** `{_fmt(row.get('build_sha'))}`  "
                   f"(date {_fmt(row.get('build_date'))})")
        out.append(f"- **DM model:** {_fmt(row.get('dm_model'))}  ·  "
                   f"**Scorer:** {_fmt(row.get('scorer_model'))}")
    else:
        out.append("- _No RRI-bearing row found in the scores ledger._ "
                   "The release verdict below is inferred from available evidence only.")
    out.append("")

    # Ruler versions (the comparability provenance — sc_ / lc_).
    out.append("## Ruler versions")
    out.append("")
    if row is not None:
        out.append(f"- **Full scoring ruler:** `{_fmt(row.get('scoring_config_version'))}` "
                   f"(rubrics + schemas + gates incl. RRI)")
        out.append(f"- **Lens ruler:** `{_fmt(row.get('lens_config_version'))}` "
                   f"(the 8 files that produce the story/mech/angry numbers)")
        out.append("")
        out.append("> Rows under a DIFFERENT ruler are not a directly-comparable quality trend; "
                   "the ruler change (a rubric recalibration or a new gate) moves the number "
                   "with no change in play quality.")
    else:
        out.append("- _ruler versions unavailable (no ledger row)._")
    out.append("")

    # The 11-gate table.
    out.append("## RRI gate results (11 canonical gates)")
    out.append("")
    out.append("| Gate | Status |")
    out.append("|---|---|")
    for name in RRI_CANONICAL_GATES:
        out.append(f"| `{name}` | {gate_statuses.get(name, 'MISSING')} |")
    out.append("")

    # Closed-issue list.
    if milestone:
        out.append(f"## Closed issues — milestone `{milestone}`")
        out.append("")
        if issues:
            for it in sorted(issues, key=lambda i: i.get("number", 0)):
                num = it.get("number")
                ttl = str(it.get("title") or "").strip()
                url = it.get("url") or ""
                out.append(f"- #{num} {ttl}" + (f" ({url})" if url else ""))
            out.append("")
            out.append(f"> {len(issues)} closed issue(s) in this milestone.")
        else:
            out.append(f"- _No closed issues listed_"
                       + (f" — {issues_note}." if issues_note else "."))
        out.append("")

    out.append("---")
    out.append(f"> Generated by `qa/generate_release_notes.py` "
               f"(READ-ONLY over `qa/scores.db`).")
    out.append("")
    return "\n".join(out)


def build_notes(args: argparse.Namespace) -> str:
    # READ-ONLY over the committed qa/scores.db: fetch_rows_readonly opens mode=ro and never
    # runs the schema-ensure that would rewrite the binary (the additive, read-only invariant).
    rows = scores_db.fetch_rows_readonly(args.db)
    row = latest_rri_row(rows, rc_label=args.rc_label)
    build_sha = args.build_sha or (row.get("build_sha") if row else None)

    gate_statuses, gate_source = _gate_statuses_from_artifact(
        rri_json=args.rri_json, verdict_json=args.verdict_json,
        db_path=args.db, build_sha=build_sha,
    )
    if gate_statuses is None:
        gate_statuses, gate_source = _gate_statuses_inferred(row)

    issues: list[dict] = []
    issues_note = ""
    if args.milestone and not args.no_issues:
        issues, issues_note = closed_issues(args.milestone, args.repo)

    return render_notes(
        tag=args.tag, milestone=args.milestone, row=row,
        gate_statuses=gate_statuses, gate_source=gate_source,
        issues=issues, issues_note=issues_note, version=_read_version(),
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tag", default=None, help="git tag this release links to (e.g. v1.0.5)")
    p.add_argument("--milestone", default=None,
                   help='GitHub milestone for the closed-issue list (e.g. "v1.0.5")')
    p.add_argument("--rc-label", default=None,
                   help="restrict the latest-RRI lookup to this rc_label (e.g. v1.0.5-rc1)")
    p.add_argument("--rri-json", default=None,
                   help="a release_readiness.py RRI.json for the 11 per-gate statuses")
    p.add_argument("--verdict-json", default=None,
                   help="a release_readiness_verdict.json (scores_db) for the 11 per-gate statuses "
                        "(wins over --rri-json)")
    p.add_argument("--build-sha", default=None,
                   help="build SHA to attribute (defaults to the latest RRI row's build_sha)")
    p.add_argument("--db", default=str(scores_db.DB_PATH), help="path to scores.db (READ-ONLY)")
    p.add_argument("--repo", default=None,
                   help="owner/repo for `gh issue list` (default: gh's current repo)")
    p.add_argument("--no-issues", action="store_true",
                   help="skip the `gh issue list` call (offline / no milestone section)")
    p.add_argument("--out", default=None, help="write Markdown here (default: stdout)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    notes = build_notes(args)
    if args.out:
        Path(args.out).write_text(notes, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
