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

Beyond the plate-blob check, three more real, cheap freshness signals (adversarial-review adds,
2026-07-22 — validated against ground truth first, each is either a real existing field or the same
git-blob technique, never a new invented field):
  - the report file must actually PARSE and carry the expected top-level keys — a present-but-empty or
    wrong-room report must not silently count as evidence just because a file exists at that path;
  - `method.ortho` (a field paint_coherence.py already writes) must match the manifest's current
    cameraPin.ortho — a room resize/re-fit can change ortho without touching the plate's bytes, and the
    per-cell projection is keyed on ortho;
  - for the 5 _OWNER_ROOMS, the room's geometry JSON (qa/room_geometries/) gets the SAME git-blob
    freshness check as the plate — classify_cells() projects the plate through that geometry, so a
    walls/props/doors/spawns edit stales the report exactly like a plate edit does.
Known residual limitation (not fixed — would need paint_coherence.py to stamp real provenance, which the
charter's "do not invent fields" rules out): freshness keys off the LAST COMMIT TOUCHING the report file,
which is a proxy for "the classifier was re-run," not a guarantee — a commit that edits the report file
without regenerating it (reformatting, a bulk edit) would advance that proxy without truly re-running
the classifier. Full closure needs the report to record its own generation provenance.

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
GEO_DIR = HERE / "room_geometries"

sys.path.insert(0, str(HERE))
from paint_coherence import _OWNER_ROOMS  # noqa: E402  (registry_key -> geometry stem; the SAME mapping
                                          # gate_owner_rooms() uses — reused, not re-derived, so the two
                                          # can't silently drift apart)


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
    HEAD-only comparison would miss). --no-filters is load-bearing: the plates/*.png paths match a
    `filter=lfs` rule in .gitattributes, and on a runner with git-lfs installed, a filtered
    `git hash-object` re-cleans the (already-real, non-pointer) bytes into a FRESH LFS pointer and hashes
    THAT — a spurious mismatch against the real blob sha that has nothing to do with plate drift (caught
    on the actual GitHub Actions runner, which has git-lfs; this repo's committed plate blobs are real
    bytes, not pointers, so no filter should touch them at all)."""
    rc, out = _git("hash-object", "--no-filters", str(path))
    return out if rc == 0 and out else None


def _drift_check(reg_key: str, label: str, file_path: Path, report_commit: str) -> list[str]:
    """Shared plate/geometry drift check: does `file_path`'s content at `report_commit` (when the report
    was last touched) still match its content now (HEAD + working tree)? Empty == fresh."""
    if not file_path.is_file():
        return [f"{reg_key}: {label} {file_path} is missing on disk — harness cannot check its freshness"]
    rel = str(file_path.relative_to(REPO))
    recorded_sha = _blob_sha_at(report_commit, rel)
    current_sha = _blob_sha_at("HEAD", rel)
    working_sha = _working_tree_sha(file_path)
    if recorded_sha is None or current_sha is None:
        return [f"{reg_key}: could not resolve a git blob sha for {label} {file_path.name} "
                f"(report_commit={report_commit}) — harness error, not a verdict"]
    if recorded_sha != current_sha:
        return [f"{reg_key}: {label} drifted since the coherence report was last generated in "
                f"{report_commit[:12]} ({label} blob {recorded_sha[:12]} then vs {current_sha[:12]} at "
                "HEAD now) — re-run qa/paint_coherence.py gate-rooms and commit the refreshed report"]
    if working_sha and working_sha != current_sha:
        return [f"{reg_key}: {label} has UNCOMMITTED local changes since the coherence report was "
                f"generated (working tree blob {working_sha[:12]} vs committed {current_sha[:12]}) — "
                "re-run qa/paint_coherence.py gate-rooms before committing"]
    return []


def _load_report(reg_key: str, report_path: Path) -> tuple[dict | None, list[str]]:
    """Parse + sanity-check the report JSON. A report file existing is not enough evidence on its own —
    an empty/malformed file, or a report copy-pasted for the wrong room, must not silently pass."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"{reg_key}: {report_path.name} exists but is not valid JSON ({exc}) — a report "
                      "that can't be parsed is a FAILURE, never a silent pass"]
    missing_keys = {"room", "passed", "cells", "violations", "method"} - set(report)
    if missing_keys:
        return None, [f"{reg_key}: {report_path.name} is missing expected key(s) {sorted(missing_keys)} "
                      "— not a real paint_coherence.py report (or from an incompatible tool version)"]
    return report, []


def check_room(reg_key: str, plate_rel_from_unity: str, entry: dict) -> list[str]:
    """Failure strings for one manifest-shipped room; empty == fresh."""
    plate_path = PLATES_DIR / Path(plate_rel_from_unity).name
    report_path = EVIDENCE_DIR / f"{reg_key}_coherence_report.json"

    if not plate_path.is_file():
        return [f"{reg_key}: shipped plate {plate_path.relative_to(REPO)} is missing on disk "
                "(manifest points at a file that doesn't exist) — harness cannot even check freshness"]

    if not report_path.is_file():
        return [f"{reg_key}: NO paint-coherence report at "
                f"{report_path.relative_to(REPO)} for a manifest-shipped room "
                "(run `qa/paint_coherence.py gate-rooms` and commit the report — a missing report is a "
                "FAILURE here, never a silent skip)"]

    report, fails = _load_report(reg_key, report_path)
    if fails:
        return fails

    report_rel = str(report_path.relative_to(REPO))
    report_commit = _last_commit_touching(report_rel)
    if report_commit is None:
        return [f"{reg_key}: {report_path.name} is not tracked by git — cannot establish when it was "
                "last generated, so freshness can't be verified (commit it or regenerate + commit)"]

    fails = _drift_check(reg_key, "plate", plate_path, report_commit)
    if fails:
        return fails

    # ortho freshness: a REAL, already-existing report field (method.ortho — no invention needed here)
    # vs the manifest's current cameraPin.ortho. A room resize/re-fit changes ortho without necessarily
    # touching the plate PNG's bytes, and the per-cell projection paint_coherence.py classified against
    # is keyed on ortho — so an ortho drift alone stales the report just as much as a plate-byte drift.
    manifest_ortho = (entry.get("cameraPin") or {}).get("ortho")
    report_ortho = (report.get("method") or {}).get("ortho")
    if manifest_ortho is not None and report_ortho is not None:
        if round(float(manifest_ortho), 4) != round(float(report_ortho), 4):
            return [f"{reg_key}: manifest cameraPin.ortho ({manifest_ortho}) no longer matches the "
                    f"ortho the coherence report was classified against ({report_ortho}) — re-run "
                    "qa/paint_coherence.py gate-rooms and commit the refreshed report"]

    # geometry freshness: only the 5 _OWNER_ROOMS have a known geometry file (paint_coherence.py's own
    # registry — reused, not re-derived). classify_cells() projects the plate through THIS geometry, so
    # a geometry edit (walls/props/doors/spawns) stales the report exactly like a plate edit does.
    geo_stem = _OWNER_ROOMS.get(reg_key)
    if geo_stem:
        geo_path = GEO_DIR / f"{geo_stem}_geometry.json"
        fails = _drift_check(reg_key, "geometry", geo_path, report_commit)
        if fails:
            return fails
    return []


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
        all_fails.extend(check_room(reg_key, plate_rel, entry))

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
