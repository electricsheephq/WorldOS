#!/usr/bin/env python3
"""Resolve the auto-tag-on-milestone-close GATE — Versioning Phase-2.

WHY THIS EXISTS
---------------
"Complete a milestone → tag/release" is the owner's standing automation. The GitHub Actions
workflow ``.github/workflows/release-on-milestone-close.yml`` fires on ``milestone: closed``
(or a manual ``workflow_dispatch``) and must decide, deterministically and conservatively,
**whether this milestone is allowed to be tagged/released**. This module is that decision —
extracted from the YAML so it can be UNIT-TESTED (a shell ``if`` in a workflow cannot).

THE GATE (all must hold for a GO)
---------------------------------
1. **The opt-in marker.** GitHub milestones do NOT support labels, so the "ready-for-release"
   opt-in lives in the milestone's TITLE or DESCRIPTION as an explicit ``[release-ready]`` marker.
   Absent ⇒ NO-GO (a development milestone closing must NOT auto-tag). This is the human's
   deliberate "yes, cut it" — closing a milestone alone is NOT consent to tag.
2. **A clean version tag from the title.** The milestone title must be a clean ``vX.Y.Z`` (an
   optional ``-rcN`` / ``-<suffix>`` pre-release is allowed). Anything else ⇒ NO-GO.
3. **The tag must not already exist.** If ``vX.Y.Z`` is already a tag ⇒ NO-GO (never clobber /
   re-cut a release).
4. **Version consistency.** The milestone's base ``X.Y.Z`` must equal the repo-root ``VERSION``
   file AND ``servers/engine/__version__.py`` (the single source of truth). A mismatch ⇒ NO-GO
   (the milestone is for a version the code hasn't been bumped to).
5. **STATUS: RELEASE.** ``generate_release_notes.py`` / the per-gate verdict must report all 11
   RRI gates PASSED (STATUS: RELEASE). If the status is DEVELOPMENT (any gate
   SKIPPED/FAILED/MISSING/UNKNOWN) ⇒ NO-GO. A pre-release (``-rc``) is allowed to ship as a
   GitHub *pre-release* even on DEVELOPMENT status IF (and only if) ``--allow-prerelease-dev`` is
   passed — but a clean GA (no ``-rc``) ALWAYS requires STATUS: RELEASE.

READ-ONLY: this never mutates qa/scores.db, never tags, never calls ``gh release``. It only reads
the ledger/verdict + git tags + the VERSION files and returns a structured verdict. The workflow
(and only in real, non-dry-run mode) performs the actual tag/release.

USAGE
-----
    python3 qa/release_gate_check.py \
        --milestone-title "v1.0.5" \
        --milestone-description "...[release-ready]..." \
        [--verdict-json release_readiness_verdict.json | --rri-json qa/RRI.json] \
        [--db qa/scores.db] [--allow-prerelease-dev] [--out gate_verdict.json]

Exit code 0 = GO (safe to tag in real mode); 1 = NO-GO; 2 = a usage / IO error. The structured
JSON verdict (printed to stdout, and to ``--out`` if given) carries ``decision`` (``go`` /
``no_go``), the resolved ``tag`` / ``version`` / ``prerelease`` booleans, and a ``reasons`` list
so the workflow log explains EXACTLY why it would or would not tag.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

QA_DIR = Path(__file__).resolve().parent
REPO_ROOT = QA_DIR.parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402  (READ-ONLY ledger + verdict reader)
import generate_release_notes as grn  # noqa: E402  (reuse the DEVELOPMENT/RELEASE logic)

# The explicit opt-in marker. Case-insensitive; brackets are literal so it can't be tripped by a
# stray "release ready" in prose. This is the documented convention (docs/roadmap/release-automation.md).
RELEASE_READY_MARKER = "[release-ready]"

# A clean version tag: vMAJOR.MINOR.PATCH with an OPTIONAL pre-release suffix (-rc1, -beta, ...).
# Anchored so "v1.0" (no patch) and "v1.0.5 (final)" (trailing prose) are REFUSED.
_VERSION_TAG_RE = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)(?P<pre>-[0-9A-Za-z.\-]+)?$")


def has_release_marker(*texts: Optional[str]) -> bool:
    """True iff the literal ``[release-ready]`` marker appears in ANY of the given texts."""
    marker = RELEASE_READY_MARKER.lower()
    return any(marker in (t or "").lower() for t in texts)


def parse_version_tag(title: Optional[str]) -> Optional[dict]:
    """Parse a milestone title into a tag dict, or None if it isn't a clean vX.Y.Z[-pre].

    Returns ``{"tag": "v1.0.5-rc4", "base": "1.0.5", "prerelease": True}`` — ``prerelease`` is
    True when a ``-suffix`` is present (an rc / beta / etc. ships as a GitHub pre-release)."""
    if not title:
        return None
    m = _VERSION_TAG_RE.match(title.strip())
    if not m:
        return None
    return {
        "tag": m.group(0).strip(),
        "base": m.group("base"),
        "prerelease": bool(m.group("pre")),
    }


def tag_exists(tag: str, *, repo_root: Path = REPO_ROOT) -> bool:
    """True iff ``tag`` already exists as a git tag in ``repo_root`` (never clobber)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # If git is unavailable we cannot prove the tag is free → be conservative, treat as "exists".
        return True
    return proc.returncode == 0


def read_repo_version(*, repo_root: Path = REPO_ROOT) -> tuple[Optional[str], Optional[str]]:
    """Return (VERSION-file, engine __version__) — the two halves of the source of truth.

    Either may be None if unreadable. The consistency guard compares the milestone BASE against
    both; a None side is reported as a mismatch (we never tag against an unreadable source)."""
    version_file = None
    try:
        version_file = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    engine_version = None
    vmod = repo_root / "servers" / "engine" / "__version__.py"
    try:
        text = vmod.read_text(encoding="utf-8")
        m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if m:
            engine_version = m.group(1).strip()
    except OSError:
        pass
    return version_file, engine_version


def _resolve_status(
    *, verdict_json: Optional[str], rri_json: Optional[str], db_path, build_sha: Optional[str]
) -> tuple[str, str, list[str]]:
    """Resolve the DEVELOPMENT/RELEASE status by reusing generate_release_notes' gate logic.

    Returns (status, source, not_passed_gates). ``status`` is "RELEASE" / "DEVELOPMENT". When NO
    per-gate artifact is supplied the status is DEVELOPMENT (the inferred path can never certify
    RELEASE — the honesty guard), and the source records why."""
    gate_statuses, source = grn._gate_statuses_from_artifact(
        rri_json=rri_json, verdict_json=verdict_json, db_path=db_path, build_sha=build_sha,
    )
    if gate_statuses is None:
        rows = scores_db.fetch_rows_readonly(db_path)
        # Match the latest RRI-bearing row (best-effort, for the few provable gates).
        row = grn.latest_rri_row(rows)
        gate_statuses, source = grn._gate_statuses_inferred(row)
    status, not_passed = grn.development_or_release(gate_statuses)
    return status, source, not_passed


def evaluate_gate(args: argparse.Namespace) -> dict:
    """The pure decision. Returns a structured verdict dict (no side effects beyond reads)."""
    reasons: list[str] = []
    decision = "go"

    def block(reason: str) -> None:
        nonlocal decision
        decision = "no_go"
        reasons.append(reason)

    # 1) The opt-in marker (title OR description).
    marker_present = has_release_marker(args.milestone_title, args.milestone_description)
    if marker_present:
        reasons.append(f"marker `{RELEASE_READY_MARKER}` present (opt-in granted)")
    else:
        block(f"marker `{RELEASE_READY_MARKER}` absent in milestone title/description "
              f"(a development milestone closing must NOT auto-tag)")

    # 2) Clean version tag from the title.
    parsed = parse_version_tag(args.milestone_title)
    tag = parsed["tag"] if parsed else None
    base = parsed["base"] if parsed else None
    prerelease = bool(parsed["prerelease"]) if parsed else False
    if parsed:
        reasons.append(f"milestone title `{args.milestone_title}` → tag `{tag}` "
                       f"({'pre-release' if prerelease else 'GA'})")
    else:
        block(f"milestone title `{args.milestone_title}` is not a clean vX.Y.Z[-pre] "
              f"(refusing to derive a tag)")

    # 3) The tag must not already exist (only meaningful once we have a tag).
    if tag is not None:
        if tag_exists(tag, repo_root=Path(args.repo_root)):
            block(f"tag `{tag}` already exists (never clobber an existing release)")
        else:
            reasons.append(f"tag `{tag}` does not yet exist (free to create)")

    # 4) Version consistency: base must match VERSION and engine __version__.
    if base is not None:
        vfile, eng = read_repo_version(repo_root=Path(args.repo_root))
        if vfile == base and eng == base:
            reasons.append(f"version consistency OK (VERSION={vfile}, __version__={eng} == {base})")
        else:
            block(f"version mismatch: milestone base {base} vs VERSION={vfile!r} / "
                  f"__version__={eng!r} (bump the source of truth first)")

    # 5) STATUS: RELEASE (all 11 RRI gates PASSED) — the quality gate.
    status, status_source, not_passed = _resolve_status(
        verdict_json=args.verdict_json, rri_json=args.rri_json,
        db_path=args.db, build_sha=args.build_sha,
    )
    if status == "RELEASE":
        reasons.append(f"RRI status RELEASE — all 11 gates PASSED (source: {status_source})")
    else:
        np = ", ".join(not_passed) if not_passed else "—"
        # A pre-release MAY ship on DEVELOPMENT status only with the explicit opt-out; a GA never can.
        if prerelease and args.allow_prerelease_dev:
            reasons.append(f"RRI status DEVELOPMENT (not-passed: {np}; source: {status_source}) — "
                           f"ALLOWED as a pre-release via --allow-prerelease-dev")
        else:
            block(f"RRI status DEVELOPMENT (not-passed: {np}; source: {status_source}) — "
                  f"only an all-11-PASS RELEASE may auto-tag"
                  + ("" if prerelease else " (a GA always requires STATUS: RELEASE)"))

    return {
        "schema": "worldos.release-gate-check.v1",
        "decision": decision,  # "go" | "no_go"
        "tag": tag,
        "version": base,
        "prerelease": prerelease,
        "marker_present": marker_present,
        "rri_status": status,
        "rri_status_source": status_source,
        "gates_not_passed": not_passed,
        "reasons": reasons,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--milestone-title", required=True,
                   help="the closed milestone's title (the tag is derived from this, e.g. v1.0.5)")
    p.add_argument("--milestone-description", default="",
                   help="the milestone's description (searched for the [release-ready] marker too)")
    p.add_argument("--verdict-json", default=None,
                   help="a release_readiness_verdict.json (scores_db) for the 11 per-gate statuses")
    p.add_argument("--rri-json", default=None,
                   help="a release_readiness.py RRI.json (converted to the 11 per-gate statuses)")
    p.add_argument("--build-sha", default=None,
                   help="build SHA to attribute (defaults to the latest RRI row's build_sha)")
    p.add_argument("--db", default=str(scores_db.DB_PATH), help="path to scores.db (READ-ONLY)")
    p.add_argument("--repo-root", default=str(REPO_ROOT),
                   help="repo root for the tag-exists + VERSION consistency checks")
    p.add_argument("--allow-prerelease-dev", action="store_true",
                   help="allow a -rc/-pre tag to proceed on DEVELOPMENT status (GA always needs RELEASE)")
    p.add_argument("--out", default=None, help="also write the JSON verdict here")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        verdict = evaluate_gate(args)
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        sys.stderr.write(f"release_gate_check: error: {exc}\n")
        return 2
    blob = json.dumps(verdict, indent=2)
    sys.stdout.write(blob + "\n")
    if args.out:
        Path(args.out).write_text(blob + "\n", encoding="utf-8")
    return 0 if verdict["decision"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())
