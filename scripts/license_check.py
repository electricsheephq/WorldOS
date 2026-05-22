#!/usr/bin/env python3
"""ClawDnD license / content gate.

Fails the build if:
  - any file under content/campaigns/_imported/ is tracked in git — privately
    imported, user-owned adventures may be copyrighted and must NEVER be
    committed or redistributed; or
  - a required licensing/attribution file is missing.

This is intentionally conservative and fast; it runs in CI on every push.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["LICENSE", "THIRD_PARTY_NOTICES.md", "data/srd/ATTRIBUTION.md"]
FORBIDDEN_PREFIX = "content/campaigns/_imported/"


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def main() -> int:
    errors: list[str] = []

    for req in REQUIRED:
        if not (ROOT / req).exists():
            errors.append(f"missing required licensing file: {req}")

    for f in tracked_files():
        if f.startswith(FORBIDDEN_PREFIX):
            errors.append(
                f"private/user-owned content must not be committed: {f}"
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
