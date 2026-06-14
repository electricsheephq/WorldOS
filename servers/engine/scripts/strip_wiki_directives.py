#!/usr/bin/env python3
"""F10-7 — one-shot content scrubber: strip MediaWiki magic-word directives from canon
character JSON.

The ingested canon backstories carry leading wiki magic words — ``__notoc__`` and the
uppercase ``__NOTOC__`` (and a stray ``__TOC__``) — which is editor markup, not lore. The
engine reads several prose fields VERBATIM (the picker snippet, the DM-voiced backstory,
the portrait prompt), so the directive rode straight into the player-facing surface. This
script removes it from EVERY string field of every characters/*.json under each shipped
world, case-INsensitively and anywhere in the string (~1/3 of the dirty files use the
uppercase form, so a case-sensitive scrub would miss them — #758 enrichment).

Idempotent: a clean corpus is a no-op. Run from the repo root (or anywhere):

    uv run --directory servers/engine python scripts/strip_wiki_directives.py        # apply
    uv run --directory servers/engine python scripts/strip_wiki_directives.py --check # report only

``--check`` exits non-zero if any file would change (the CI invariant lives in the engine
test suite, ``test_content_hygiene.py``; this flag is for a quick local pre-commit pass).

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F10-7).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Import the engine's canonical stripper so the script and the load-time belt share ONE
# definition of "what a directive is" (no drift between the scrub and the CI invariant).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import content  # noqa: E402


def _scrub(value):
    """Recursively strip directives from every string in a JSON-ish value. Returns
    (new_value, changed)."""
    if isinstance(value, str):
        cleaned = content.strip_wiki_directives(value)
        return cleaned, cleaned != value
    if isinstance(value, list):
        out, changed = [], False
        for item in value:
            new_item, ch = _scrub(item)
            out.append(new_item)
            changed = changed or ch
        return out, changed
    if isinstance(value, dict):
        out, changed = {}, False
        for k, v in value.items():
            new_v, ch = _scrub(v)
            out[k] = new_v
            changed = changed or ch
        return out, changed
    return value, False


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    changed_files: list[Path] = []
    scanned = 0

    for w in content.list_worlds():
        for cdir in content._characters_dirs(w["id"]):
            if not cdir.is_dir():
                continue
            for p in sorted(cdir.glob("*.json")):
                scanned += 1
                try:
                    rec = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    print(f"[strip] SKIP {p} — {exc}", file=sys.stderr)
                    continue
                cleaned, changed = _scrub(rec)
                if not changed:
                    continue
                changed_files.append(p)
                if not check_only:
                    # Preserve the on-disk style: 2-space indent, non-ASCII kept, trailing newline.
                    p.write_text(
                        json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

    verb = "would change" if check_only else "stripped"
    print(f"[strip] scanned {scanned} canon files; {verb} {len(changed_files)}.")
    if check_only and changed_files:
        for p in changed_files[:10]:
            print(f"[strip]   dirty: {p}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
