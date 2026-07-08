#!/usr/bin/env python3
"""library_lint.py — the HV3 library invariant checker (Act II §4c, #1325).

Validates the promoted ``library/`` pack against the ratified invariants. Exit 0 = clean, 2 = failures.
Called as ``library-lint`` in the acceptance bundle. Read-only over the library (never writes).

CHECKS (fail loud, list every violation):
  * pack.json exists and carries {name, version, license, provenance}.
  * every entry carries the required metadata: artifact_id, class, provenance, scores, tier,
    reuse_count, license, promoted_at.
  * NO unscored gate-promoted entry — a ``tier in {"stable", "canonical-candidate"}`` entry MUST have
    scores.overall not null AND scores.dims non-empty (a gate-promoted entry with no score is the exact
    thing the gate exists to prevent).
  * provenance AND license are present + non-empty on EVERY entry (any tier).
  * tier is one of {experimental, stable, canonical-candidate, canonical} (promote.py's text gate writes
    stable; its visual gate writes stable OR canonical-candidate — delta at parity with real art;
    canonical is human-curated; experimental is a manual/lower-bar lane, incl. a retained but
    gate-rejected incumbent).
  * an entry's class matches its subdirectory (quests/ holds class=quest, …).
  * room entries REFERENCE a recipe key + asset_ids (room_ref present) — they never inline recipe data.

CLI:
    python3 tools/library/library_lint.py [--library DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent.parent
DEFAULT_LIBRARY_DIR = _REPO_ROOT / "library"

VALID_TIERS = {"experimental", "stable", "canonical-candidate", "canonical"}
# Gate-promoted tiers that MUST carry a score (the visual gate's parity tier joins stable here).
_SCORED_TIERS = {"stable", "canonical-candidate"}
_REQUIRED_ENTRY_KEYS = ("artifact_id", "class", "provenance", "scores", "tier",
                        "reuse_count", "license", "promoted_at")
_REQUIRED_PACK_KEYS = ("name", "version", "license", "provenance")
_CLASS_FOR_SUBDIR = {"quests": "quest", "npcs": "npc", "locations": "location",
                     "encounters": "encounter", "rooms": "room"}


def _is_nonempty(v) -> bool:
    return v not in (None, "", [], {})


def lint_library(library_dir: Path | str) -> list[str]:
    """Return a list of violation strings (empty == clean)."""
    library_dir = Path(library_dir)
    problems: list[str] = []

    if not library_dir.exists():
        return [f"library dir {library_dir} does not exist"]

    # pack.json
    pack_path = library_dir / "pack.json"
    if not pack_path.exists():
        problems.append("pack.json is missing")
    else:
        try:
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"pack.json is not valid JSON: {e}")
            pack = {}
        for k in _REQUIRED_PACK_KEYS:
            if not _is_nonempty(pack.get(k)):
                problems.append(f"pack.json missing/empty required key {k!r}")

    # entries
    for subdir, expected_class in _CLASS_FOR_SUBDIR.items():
        d = library_dir / subdir
        if not d.is_dir():
            continue
        for entry_path in sorted(d.glob("*.json")):
            rel = f"{subdir}/{entry_path.name}"
            try:
                entry = json.loads(entry_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                problems.append(f"{rel}: not valid JSON: {e}")
                continue

            for k in _REQUIRED_ENTRY_KEYS:
                if k not in entry:
                    problems.append(f"{rel}: missing required key {k!r}")

            if entry.get("class") != expected_class:
                problems.append(
                    f"{rel}: class {entry.get('class')!r} does not match subdir (expected "
                    f"{expected_class!r})")

            tier = entry.get("tier")
            if tier not in VALID_TIERS:
                problems.append(f"{rel}: tier {tier!r} not in {sorted(VALID_TIERS)}")

            # provenance + license required + non-empty on EVERY entry.
            if not _is_nonempty(entry.get("provenance")):
                problems.append(f"{rel}: provenance missing/empty (required on every entry)")
            if not _is_nonempty(entry.get("license")):
                problems.append(f"{rel}: license missing/empty (required on every entry)")

            # NO unscored gate-promoted entry (stable OR canonical-candidate).
            scores = entry.get("scores") or {}
            if tier in _SCORED_TIERS:
                if scores.get("overall") is None:
                    problems.append(f"{rel}: {tier.upper()} entry has no scores.overall (unscored {tier})")
                if not _is_nonempty(scores.get("dims")):
                    problems.append(f"{rel}: {tier.upper()} entry has empty scores.dims (unscored {tier})")

            # room entries reference a recipe + asset_ids (never inline recipe data).
            if expected_class == "room":
                ref = entry.get("room_ref") or {}
                if not _is_nonempty(ref.get("recipe_key")):
                    problems.append(f"{rel}: room entry missing room_ref.recipe_key")
                if not _is_nonempty(ref.get("asset_ids")):
                    problems.append(f"{rel}: room entry missing room_ref.asset_ids")

    return problems


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--library", default=str(DEFAULT_LIBRARY_DIR), help="library/ pack root")
    args = ap.parse_args(argv)

    problems = lint_library(Path(args.library))
    if problems:
        print(f"library-lint: {len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    print("library-lint: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
