#!/usr/bin/env python3
"""scoring_config_version — a content hash over the WorldOS *scoring ruler*.

The ruler = the exact set of files that determine what a score MEANS: the three lens rubrics +
their JSON schemas, the deterministic behavioral gate, and the RRI gate. When the rubric is
recalibrated (e.g. the "stingy" Tolkien recalibration that dropped a 4.4 run to 3.7 on identical
content) or a behavioral/RRI gate is added (a RED gate caps every lens to <=2.5), the SAME play
quality produces a DIFFERENT number. Without recording WHICH ruler produced a score, the ledger's
numbers are silently incomparable across time — the "we used to hit 4.5, now we're at 3.6" trap.

Every scored run is stamped with `scoring_config_version()` (a short content hash). Two runs are
directly comparable as a QUALITY TREND only when their `scoring_config_version` matches; across
versions the comparison is a deliberate re-baseline, never a silent trend. The hash is impossible
to forget — any edit to a rubric anchor, a schema, or a gate changes it automatically.

CLI:
  python3 qa/scoring_config_version.py            # print the current hash
  python3 qa/scoring_config_version.py --label    # print a human label + the hash
  python3 qa/scoring_config_version.py --files     # list the files that define the ruler
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_QA_DIR = Path(__file__).resolve().parent  # repo .../qa

# The exact, ORDER-STABLE file set that defines the scoring ruler. Anything that changes what a
# lens/gate number means MUST be listed here (so editing it re-versions every future score). Keep
# this list in sync if a new lens/schema/gate is added — that is the whole point: a new gate is a
# new ruler. Absent files hash as a sentinel so a rename/delete also changes the version.
SCORING_CONFIG_FILES: list[str] = [
    "rubric.md",                    # Mechanical lens
    "rubric_tolkien.md",            # Story-craft lens (BG3-anchored, stingy)
    "rubric_angry_dm.md",           # 5e rules-fidelity lens (generated from .src + SRD bench)
    "rubric_angry_dm.src.md",       # ...its source (so a bench-card change re-versions too)
    "score_schema.json",
    "score_schema_tolkien.json",
    "score_schema_angry_dm.json",
    "assert_behavioral.py",         # the deterministic gate (RED caps lenses <=2.5)
    "release_readiness.py",         # the 11-gate RRI
]


def scoring_config_version(qa_dir: Path | None = None) -> str:
    """Return a stable short hash (``sc_xxxxxxxxxxxx``) of the scoring ruler's contents."""
    root = qa_dir or _QA_DIR
    h = hashlib.sha256()
    for name in sorted(SCORING_CONFIG_FILES):
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"<absent>")
        h.update(b"\0")
    return "sc_" + h.hexdigest()[:12]


def scoring_config_label(qa_dir: Path | None = None) -> str:
    """A human-readable label + the hash, e.g. ``ruler@sc_a1b2c3d4e5f6 (9 files)``."""
    root = qa_dir or _QA_DIR
    present = sum(1 for n in SCORING_CONFIG_FILES if (root / n).is_file())
    return f"ruler@{scoring_config_version(root)} ({present}/{len(SCORING_CONFIG_FILES)} files)"


if __name__ == "__main__":
    if "--files" in sys.argv:
        for n in sorted(SCORING_CONFIG_FILES):
            print(n)
    elif "--label" in sys.argv:
        print(scoring_config_label())
    else:
        print(scoring_config_version())
