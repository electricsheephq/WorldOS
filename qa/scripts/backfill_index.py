#!/usr/bin/env python3
"""Rebuild qa/INDEX.jsonl from scratch by walking all QA artifact dirs.

Idempotent: re-running produces the same INDEX.jsonl modulo `indexed_at`
timestamps. Writes to <index>.new, then atomic-renames over.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

# Local import works when run as `python3 qa/scripts/backfill_index.py`
# or `python3 -m qa.scripts.backfill_index`.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import indexer  # noqa: E402


def _walk_runs(repo_root: Path, verbose: bool) -> list[dict]:
    """Walk qa/ui_playtest_runs/* and produce one entry per subdir."""
    base = repo_root / "qa" / "ui_playtest_runs"
    out: list[dict] = []
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        entry = indexer.extract_run(d, repo_root)
        if entry is None:
            if verbose:
                print(f"  skip (no entry): {d.name}", file=sys.stderr)
            continue
        entry["source"] = "backfill"
        out.append(entry)
    return out


def _walk_play_states(repo_root: Path, verbose: bool) -> list[dict]:
    base = repo_root / "play-state"
    out: list[dict] = []
    if not base.exists():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        entry = indexer.extract_play_state(d, repo_root)
        if entry is None:
            if verbose:
                print(f"  skip (no entry): {d.name}", file=sys.stderr)
            continue
        entry["source"] = "backfill"
        out.append(entry)
    return out


def _walk_transcripts(repo_root: Path, verbose: bool) -> list[dict]:
    base = repo_root / "qa" / "transcripts"
    out: list[dict] = []
    if not base.exists():
        return out
    for f in sorted(base.glob("*.jsonl")):
        if not f.is_file():
            continue
        entry = indexer.extract_transcript(f, repo_root)
        if entry is None:
            if verbose:
                print(f"  skip (no entry): {f.name}", file=sys.stderr)
            continue
        entry["source"] = "backfill"
        out.append(entry)
    return out


def backfill(repo_root: Path, index_path: Path, kinds: set[str], verbose: bool) -> dict:
    """Rebuild INDEX.jsonl. Returns counts per kind."""
    t0 = time.monotonic()
    entries: list[dict] = []
    counts: dict[str, int] = {}

    if "run" in kinds:
        runs = _walk_runs(repo_root, verbose)
        entries.extend(runs)
        counts["run"] = len(runs)

    if "play-state" in kinds:
        ps = _walk_play_states(repo_root, verbose)
        entries.extend(ps)
        counts["play-state"] = len(ps)

    if "transcript" in kinds:
        tr = _walk_transcripts(repo_root, verbose)
        entries.extend(tr)
        counts["transcript"] = len(tr)

    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = index_path.with_suffix(index_path.suffix + ".new")
    with tmp_path.open("w") as out:
        for e in entries:
            out.write(json.dumps(e, ensure_ascii=False))
            out.write("\n")
    import os
    os.replace(tmp_path, index_path)

    counts["total"] = len(entries)
    counts["elapsed_sec"] = round(time.monotonic() - t0, 2)
    return counts


def opaque_summary(repo_root: Path, kinds: set[str]) -> list[str]:
    """Return ids of entries we could only minimally parse (no sha, no canonical)."""
    opaque: list[str] = []
    if "run" in kinds:
        for d in (repo_root / "qa" / "ui_playtest_runs").iterdir() if (repo_root / "qa" / "ui_playtest_runs").exists() else []:
            if not d.is_dir():
                continue
            parsed = indexer.parse_canonical_name(d.name)
            has_meta = (d / "run.json").exists() or (d / "meta.json").exists()
            if not has_meta and not parsed.get("sha"):
                opaque.append(f"run/{d.name}")
    return opaque


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild qa/INDEX.jsonl.")
    parser.add_argument("--root", help="Repo root (default: $PWD)")
    parser.add_argument("--index", help="Path to INDEX.jsonl (default: <root>/qa/INDEX.jsonl)")
    parser.add_argument("--kinds", default="run,play-state,transcript",
                        help="Comma-separated kinds to index (default: all)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    index_path = Path(args.index).resolve() if args.index else repo_root / "qa" / "INDEX.jsonl"
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}

    if not (repo_root / "qa").exists():
        print(f"error: no qa/ dir under {repo_root}", file=sys.stderr)
        return 2

    counts = backfill(repo_root, index_path, kinds, args.verbose)
    opaque = opaque_summary(repo_root, kinds)

    print(f"Backfill complete: {counts['total']} entries → {index_path}")
    for k in ("run", "play-state", "transcript"):
        if k in counts:
            print(f"  {k:<12} {counts[k]:>6}")
    print(f"  elapsed:     {counts['elapsed_sec']}s")
    if opaque:
        print(f"  opaque (no metadata, no sha in name): {len(opaque)}")
        for o in opaque[:10]:
            print(f"    {o}")
        if len(opaque) > 10:
            print(f"    ... and {len(opaque) - 10} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
