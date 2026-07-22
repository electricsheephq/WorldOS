#!/usr/bin/env python3
"""check_coherence_freshness.py — CI freshness lint for qa/evidence/paint-coherence/ reports (#1651 CI half).

Ground truth checked before writing this (2026-07-22): the per-room reports paint_coherence.py writes
(qa/evidence/paint-coherence/<room>_coherence_report.json) carry NO plate-identity field at all — no
sha, no filename, nothing to diff against a "recorded" value. So "recorded plate sha" cannot be a JSON
field lookup without inventing one, which the charter forbids. The honest, zero-new-fields substitute:
git ALREADY records exact file content at every commit. "The report's recorded plate sha" = the plate's
git blob sha AT THE COMMIT the report file was last touched; "the shipped plate file sha" = the plate's
git blob sha at HEAD (checked against the working tree too, so an uncommitted local edit is also caught).
If they differ, the plate changed after the report was last generated: the checked-in evidence no longer
speaks for the shipped plate. This mirrors the existing plate_sha256 drift convention in
qa/room_pipeline.py's certifications (item #2 of this charter) without inventing a parallel field name.

Scope (no silent skips — the audit's flagged failure mode was a drift gate that quietly reported 8 of 9
rooms 'no-plate: skipped'): EVERY room in extensions/renderers/unity/plates_manifest.json that ships a
`plate` entry must have a paint-coherence report, full stop. A room paint_coherence.py doesn't yet cover
(only the 5 _OWNER_ROOMS are wired today) still gets checked here — a missing report is a FAILURE, not a
scope exclusion. Rooms that fail today are legitimate roadmap gaps, not a "not applicable" skip.

Exit codes (tri-state, matching the sibling qa gates): 0 clean, 1 findings (missing/stale reports),
2 harness error (manifest/report unreadable, git unavailable).

Usage: python3 qa/check_coherence_freshness.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MANIFEST = REPO / "extensions" / "renderers" / "unity" / "plates_manifest.json"
PLATES_DIR = REPO / "extensions" / "renderers" / "unity" / "plates"
EVIDENCE_DIR = HERE / "evidence" / "paint-coherence"


def _git(*args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


def _blob_sha_at(commit: str, rel_path: str) -> str | None:
    """The git blob sha of `rel_path` as it existed at `commit`, or None if it didn't exist there."""
    rc, out = _git("rev-parse", f"{commit}:{rel_path}")
    return out if rc == 0 and out else None


def _last_commit_touching(rel_path: str) -> str | None:
    rc, out = _git("log", "-1", "--format=%H", "--", rel_path)
    return out if rc == 0 and out else None


def _working_tree_sha(path: Path) -> str | None:
    """git's blob sha of the file AS IT SITS ON DISK NOW (catches an uncommitted local plate edit that
    HEAD-only comparison would miss)."""
    rc, out = _git("hash-object", str(path))
    return out if rc == 0 and out else None


def check_room(reg_key: str, plate_rel_from_unity: str) -> list[str]:
    """Failure strings for one manifest-shipped room; empty == fresh."""
    plate_path = PLATES_DIR / Path(plate_rel_from_unity).name
    report_path = EVIDENCE_DIR / f"{reg_key}_coherence_report.json"
    fails: list[str] = []

    if not plate_path.is_file():
        return [f"{reg_key}: shipped plate {plate_path.relative_to(REPO)} is missing on disk "
                "(manifest points at a file that doesn't exist) — harness cannot even check freshness"]

    if not report_path.is_file():
        return [f"{reg_key}: NO paint-coherence report at "
                f"{report_path.relative_to(REPO)} for a manifest-shipped room "
                "(run `qa/paint_coherence.py gate-rooms` and commit the report — a missing report is a "
                "FAILURE here, never a silent skip)"]

    plate_rel = str(plate_path.relative_to(REPO))
    report_rel = str(report_path.relative_to(REPO))

    report_commit = _last_commit_touching(report_rel)
    if report_commit is None:
        return [f"{reg_key}: {report_path.name} is not tracked by git — cannot establish when it was "
                "last generated, so freshness can't be verified (commit it or regenerate + commit)"]

    recorded_sha = _blob_sha_at(report_commit, plate_rel)
    current_sha = _blob_sha_at("HEAD", plate_rel)
    working_sha = _working_tree_sha(plate_path)

    if recorded_sha is None or current_sha is None:
        return [f"{reg_key}: could not resolve a git blob sha for {plate_path.name} "
                f"(report_commit={report_commit}) — harness error, not a verdict"]

    if recorded_sha != current_sha:
        fails.append(f"{reg_key}: plate drifted since {report_path.name} was last generated in "
                     f"{report_commit[:12]} (plate blob {recorded_sha[:12]} then vs {current_sha[:12]} "
                     "at HEAD now) — re-run qa/paint_coherence.py gate-rooms and commit the refreshed "
                     "report")
    elif working_sha and working_sha != current_sha:
        fails.append(f"{reg_key}: plate has UNCOMMITTED local changes since {report_path.name} was "
                     f"generated (working tree blob {working_sha[:12]} vs committed {current_sha[:12]}) "
                     "— re-run qa/paint_coherence.py gate-rooms before committing")
    return fails


def main() -> int:
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[check_coherence_freshness] ERROR: cannot read {MANIFEST}: {exc}", file=sys.stderr)
        return 2
    rc, _ = _git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        print("[check_coherence_freshness] ERROR: not inside a git work tree — cannot verify freshness",
              file=sys.stderr)
        return 2

    plates = manifest.get("plates", {})
    all_fails: list[str] = []
    checked = 0
    for reg_key, entry in sorted(plates.items()):
        plate_rel = entry.get("plate")
        if not plate_rel:
            continue   # not a plate-shipping manifest entry — nothing for this lint to check
        checked += 1
        all_fails.extend(check_room(reg_key, plate_rel))

    if not all_fails:
        print(f"[check_coherence_freshness] {checked} manifest-shipped room(s) all have a fresh "
              "paint-coherence report")
        return 0

    print(f"[check_coherence_freshness] {len(all_fails)} finding(s) across {checked} manifest-shipped "
          "room(s):")
    for f in all_fails:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
