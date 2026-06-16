#!/usr/bin/env python3
"""Merge a per-run STRUCTURAL-COVERAGE block into a persona score.json (the owner's "full
circle"; pairs with the #961 structural_completeness gate).

The persona scorer (qa/ui_playtest_score.py) reads only the player's actions.ndjson — which
does NOT carry the DM's tool calls or the engine end-state — so it is BLIND to whether a run
actually recruited a companion, moved approval, camped, resolved+evolved a quest, traveled, or
left Act 1. Those structural outcomes live in the campaign SNAPSHOT (ground truth) + the DM
transcript's tool counts. The sweep KNOWS the persona's play-state store, so it calls THIS to
compute the block from that store and merge it into score.json.

Usage:
  inject_structural_coverage.py <score.json> <state-store-dir> [dm-transcript.jsonl]

  <state-store-dir>  the persona's play-state store (e.g. play-state/vm2-newbie-b). The
                     campaign snapshot is the largest non-empty <store>/campaigns/*/snapshot.json;
                     the DM transcript defaults to <store>/dm.combined.jsonl.

Writes ``score["structural_coverage"] = {...}`` into <score.json> (additive — leaves the rest
untouched; a missing/empty snapshot is a no-op) and prints the one-line summary to stdout for
the sweep's per-persona note line. Read-only over the engine/state; never fabricates a block.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from story_readout import structural_coverage_from_state
except Exception:  # pragma: no cover - defensive
    structural_coverage_from_state = None  # type: ignore[assignment]


def _largest_snapshot(store: Path) -> Path | None:
    """The largest non-empty campaigns/*/snapshot.json under a play-state store, or None.
    Mirrors the harnesses' state-pick (run_duo.sh / ui_playtest_app.sh): a lock-only orphan
    dir can write an empty snapshot, so size>1 + largest avoids grabbing it over the real save.
    """
    best: Path | None = None
    best_size = 1
    if not store.exists():
        return None
    for snap in store.glob("campaigns/*/snapshot.json"):
        try:
            size = snap.stat().st_size
        except OSError:
            continue
        if size > best_size:
            best, best_size = snap, size
    return best


def _tool_counts(transcript: Path) -> Counter:
    """{short_tool_name: count} from a claude -p stream-json DM transcript (the SAME tally the
    behavioral gate uses: tool_use blocks on assistant events). Empty Counter when absent."""
    counts: Counter = Counter()
    if not transcript.exists():
        return counts
    for line in transcript.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "assistant":
            continue
        for b in (d.get("message", {}) or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                counts[(b.get("name") or "").split("__")[-1]] += 1
    return counts


def compute(store_dir: str, transcript_path: str | None = None) -> dict | None:
    """The structural_coverage block for a persona's play-state store, or None when the
    snapshot is missing/unreadable (no fabrication)."""
    if structural_coverage_from_state is None:
        return None
    store = Path(store_dir)
    snap = _largest_snapshot(store)
    if snap is None:
        return None
    try:
        state = json.loads(snap.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(state, dict) or not state:
        return None
    transcript = Path(transcript_path) if transcript_path else (store / "dm.combined.jsonl")
    counts = _tool_counts(transcript)
    return structural_coverage_from_state(state, dict(counts) if counts else None)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: inject_structural_coverage.py <score.json> <state-store-dir> "
              "[dm-transcript.jsonl]", file=sys.stderr)
        return 2
    score_path = Path(argv[0])
    block = compute(argv[1], argv[2] if len(argv) > 2 else None)
    if block is None:
        # Honest no-op: nothing to merge (no snapshot). Print nothing → the sweep note omits it.
        return 0
    # Merge additively into score.json (leave every existing field untouched).
    if score_path.exists():
        try:
            score = json.loads(score_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            score = {}
        if not isinstance(score, dict):
            score = {}
        score["structural_coverage"] = block
        score_path.write_text(json.dumps(score, indent=2), encoding="utf-8")
    # One-line summary to stdout for the sweep's per-persona note.
    print(block.get("summary", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
