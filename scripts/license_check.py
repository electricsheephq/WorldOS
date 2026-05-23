#!/usr/bin/env python3
"""ClawDnD license / content gate.

Fails the build if:
  - any file under a FORBIDDEN prefix is tracked in git — privately imported,
    user-owned, or unpublished content that must NEVER be committed; or
  - a required top-level licensing/attribution file is missing; or
  - a committed world seed (content/worlds/<id>/world.json) lacks its LICENSE.md —
    world seeds based on existing settings ship as FREE, unofficial Fan Content and
    must carry the required Fan-Content / OGL notice beside them.

This is intentionally conservative and fast; it runs in CI on every push.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["LICENSE", "THIRD_PARTY_NOTICES.md", "data/srd/ATTRIBUTION.md"]
# Paths that must NEVER be committed (private / user-owned; may be copyrighted).
FORBIDDEN_PREFIXES = ("content/campaigns/_imported/", "content/worlds/_private/")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def main() -> int:
    errors: list[str] = []
    tracked = tracked_files()

    for req in REQUIRED:
        if not (ROOT / req).exists():
            errors.append(f"missing required licensing file: {req}")

    for f in tracked:
        if any(f.startswith(p) for p in FORBIDDEN_PREFIXES):
            errors.append(f"private/user-owned content must not be committed: {f}")

    # Every committed world seed must carry its licensing/attribution notice.
    for f in tracked:
        if f.startswith("content/worlds/") and f.endswith("/world.json"):
            license_md = f.rsplit("/", 1)[0] + "/LICENSE.md"
            if license_md not in tracked:
                errors.append(
                    f"world seed {f} is missing its required {license_md} "
                    f"(FREE/unofficial Fan-Content + OGL/CC-BY notice)"
                )

    if errors:
        print("LICENSE CHECK FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("license check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
