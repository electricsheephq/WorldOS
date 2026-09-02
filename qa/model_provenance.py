#!/usr/bin/env python3
"""Resolve the CONCRETE model ids a run actually used, from its ``claude -p`` transcripts.

WHY (the 2026-09-02 mis-attribution): ``qa/run_adventure.sh`` launches the DM and the player with
CLI *aliases* (``opus`` / ``sonnet``), and an alias DRIFTS — the same ``opus`` string served
``claude-opus-4-8`` in July and ``claude-opus-5`` on 2026-09-02. A row that records only the alias
therefore cannot answer "which model produced this score?", which is exactly the question the G2
regression forensics had to answer. The ``claude -p`` transcripts record what the API actually
served (``"model":"claude-…"``), so a resolved id is READABLE FROM THE RUN rather than asserted.

The resolved id is ADDITIVE provenance: it never replaces the recorded alias (``dm_model`` /
``actor_model`` keep their existing meaning), and an unresolvable run simply carries ``None``.

Transcript layout (``qa/transcripts/<run>.*``, written by run_adventure.sh):
  ``<run>.dm.<ns>.jsonl``      one stream-json file per DM beat        -> the DM model
  ``<run>.jsonl``              the concatenation of those DM streams   -> DM fallback
  ``<run>.player.<ns>.jsonl``  one ``--output-format json`` result per player turn -> player model

CLI:
  python3 qa/model_provenance.py qa/transcripts/adv_ctl_o48   # prints the resolved-ids JSON
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

# The model id as the CLI records it, in stream-json events (``"model":"claude-opus-4-8"``) and in
# the ``--output-format json`` result envelope. Matched textually so BOTH pretty-printed and
# single-line payloads resolve without a JSON-shape assumption that would silently break on a CLI
# output-format change (a silent None is a worse failure than a slightly loose regex here).
_MODEL_RE = re.compile(r'"model"\s*:\s*"(claude-[A-Za-z0-9._:-]+)"')

# Bounded scan: transcripts are megabytes and the model id repeats on every event, so a few hundred
# matches is already decisive. Keeps provenance resolution O(1)-ish per run.
_MAX_MATCHES = 200


def _scan_files(paths: list[Path]) -> Optional[str]:
    """The most frequent ``claude-…`` model id across ``paths`` (ties -> first seen), or None."""
    counts: Counter[str] = Counter()
    order: list[str] = []
    for p in paths:
        try:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    for m in _MODEL_RE.findall(line):
                        if m not in counts:
                            order.append(m)
                        counts[m] += 1
                    if sum(counts.values()) >= _MAX_MATCHES:
                        break
        except OSError:
            continue
        if sum(counts.values()) >= _MAX_MATCHES:
            break
    if not counts:
        return None
    return max(order, key=lambda m: counts[m])


def _dm_transcripts(prefix: str) -> list[Path]:
    p = Path(prefix)
    files = sorted(p.parent.glob(f"{p.name}.dm.*.jsonl"))
    combined = Path(f"{prefix}.jsonl")  # the DM streams concatenated (run_adventure.sh)
    if combined.is_file():
        files.append(combined)
    return files


def _player_transcripts(prefix: str) -> list[Path]:
    p = Path(prefix)
    return sorted(p.parent.glob(f"{p.name}.player.*.jsonl"))


def resolve_models(prefix: str) -> dict:
    """``{"dm_model_resolved": <id|None>, "player_model_resolved": <id|None>}`` for one run prefix.

    A missing/unreadable transcript resolves to None — never a guess, and never the alias (an alias
    dressed up as a resolved id is the exact false-provenance this module exists to prevent)."""
    return {
        "dm_model_resolved": _scan_files(_dm_transcripts(prefix)),
        "player_model_resolved": _scan_files(_player_transcripts(prefix)),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: model_provenance.py <run-path-prefix>")
    print(json.dumps(resolve_models(sys.argv[1]), indent=2))
