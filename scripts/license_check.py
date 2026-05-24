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

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["LICENSE", "THIRD_PARTY_NOTICES.md", "data/srd/ATTRIBUTION.md"]
# Paths that must NEVER be committed (private / user-owned; may be copyrighted). Includes
# BOTH the _imported staging area AND every documented _private/ area (worlds AND campaigns).
FORBIDDEN_PREFIXES = (
    "content/campaigns/_imported/",
    "content/campaigns/_private/",
    "content/worlds/_private/",
)


def _check_ingested_attribution(tracked: list[str]) -> list[str]:
    """Every committed INGESTED record must carry its per-source attribution (it's wiki-derived
    CC-BY-SA, not MIT) — the docs promise it, so the gate enforces it instead of trusting memory.
    JSON records (characters/areas) need non-empty `license` + `attribution` (a wiki-derived one
    also carries `source_url`, but original ClawDnD exemplars legitimately have none); wiki lore
    .md needs a Source/license footer. Authored lore under lore/*.md is exempt — it's our prose."""
    errors: list[str] = []
    for f in tracked:
        if not f.startswith("content/worlds/"):
            continue
        is_record = f.endswith(".json") and ("/characters/" in f or "/areas/" in f)
        is_wiki = f.endswith(".md") and "/lore/wiki/" in f
        if not (is_record or is_wiki):
            continue
        p = ROOT / f
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            errors.append(f"ingested content unreadable: {f}")
            continue
        if is_record:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                errors.append(f"ingested record has malformed JSON: {f}")
                continue
            if not (isinstance(data, dict) and str(data.get("license", "")).strip()
                    and str(data.get("attribution", "")).strip()):
                errors.append(f"content record missing license/attribution: {f}")
        else:  # wiki lore page — require a source + a license token in the footer
            low = text.lower()
            if "source" not in low or not ("cc-by" in low or "ogl" in low or "license" in low):
                errors.append(f"ingested wiki page missing source/license attribution footer: {f}")
    return errors


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

    # Ingested (wiki-derived) records/pages must each carry per-source attribution.
    errors.extend(_check_ingested_attribution(tracked))

    if errors:
        print("LICENSE CHECK FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("license check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
