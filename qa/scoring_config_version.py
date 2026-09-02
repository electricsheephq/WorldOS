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

There are TWO rulers (issue #725):
  * the FULL scoring ruler (``scoring_config_version`` → ``sc_…``) — everything above INCLUDING
    ``release_readiness.py`` (the 11-gate RRI). Fences the RRI trend.
  * the LENS ruler (``lens_config_version`` → ``lc_…``) — only the 8 files that produce the
    story/mech/angry LENS numbers (rubrics + schemas + the behavioral gate). Fences the
    engine-duo quality trend. Without this split, an RRI-gate-only edit (like #723/#728)
    re-versions the full ruler and FALSELY fences the lens trend even though the rubrics that
    produced those numbers never changed.

CLI:
  python3 qa/scoring_config_version.py            # print the current FULL ruler hash
  python3 qa/scoring_config_version.py --lens     # print the current LENS ruler hash
  python3 qa/scoring_config_version.py --label    # print a human label + the hash
  python3 qa/scoring_config_version.py --files     # list the files that define the full ruler
  python3 qa/scoring_config_version.py --artifact  # print the ARTIFACT ruler hash (ac_…, HV1 #1323)
  python3 qa/scoring_config_version.py --artifact-files  # list the artifact-ruler files
  python3 qa/scoring_config_version.py --adventure  # print the ADVENTURE ruler hash (av_…, A-series A-T)
  python3 qa/scoring_config_version.py --adventure-files  # list the adventure-ruler files
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

# The LENS ruler (#725): exactly the files that produce the story/mech/angry LENS numbers —
# SCORING_CONFIG_FILES minus release_readiness.py. The RRI gate does not touch what a lens
# number MEANS, so an RRI-only edit must not re-fence the engine-duo quality trend.
LENS_CONFIG_FILES: list[str] = [n for n in SCORING_CONFIG_FILES if n != "release_readiness.py"]

# The ARTIFACT ruler (HV1, #1323): a SEPARATE hash family (``ac_…``) for the per-artifact eval
# instrument — the quest / npc / location / encounter rubrics + their plain-number schemas. This is a
# DELIBERATELY DISTINCT list: it MUST NOT be folded into SCORING_CONFIG_FILES. Appending an artifact
# rubric there would silently re-version the ``sc_``/``lc_`` engine-duo rulers, breaking the ledger's
# comparability of every existing story/mech/angry number (the exact trap this whole module exists to
# prevent). The artifact ruler fences the harvest loop's per-artifact scores (scores.db
# ``artifacts.ac_ruler``) on their OWN axis: edit an artifact rubric/schema and every FUTURE artifact
# score re-versions, while the engine-duo ``sc_``/``lc_`` trend stays byte-identical. Absent files
# hash as a sentinel (same as the other families) so a rename/delete also re-versions.
ARTIFACT_CONFIG_FILES: list[str] = [
    "rubric_artifact_quest.md",
    "rubric_artifact_npc.md",
    "rubric_artifact_location.md",
    "rubric_artifact_encounter.md",
    "score_schema_artifact_quest.json",
    "score_schema_artifact_npc.json",
    "score_schema_artifact_location.json",
    "score_schema_artifact_encounter.json",
]

# The ADVENTURE ruler (A-series A-T, adventure_eval.py): a SEPARATE hash family (``av_…``) for the
# N-run adventure aggregator. Same discipline as ARTIFACT_CONFIG_FILES — a DELIBERATELY DISTINCT list
# that MUST NOT be folded into SCORING_CONFIG_FILES (appending there would silently re-version the
# ``sc_``/``lc_`` engine-duo rulers, breaking the ledger's comparability of every existing
# story/mech/angry number — the exact trap this module exists to prevent). The adventure ruler fences
# the aggregator's WEAKEST-LINK verdict + per-dimension thresholds on their OWN axis: edit
# ``adventure_eval_config.json`` and every FUTURE adventure aggregate re-versions, while the engine-duo
# ``sc_``/``lc_`` and artifact ``ac_`` trends stay byte-identical. Absent files hash as a sentinel
# (same as the other families) so a rename/delete also re-versions. NOTE: the adventure row's per-lens
# story/mech/angry numbers still carry the engine-duo ``lc_`` stamp (they are the SAME lens rubrics);
# ``av_`` fences only the aggregation/verdict config that is unique to this eval.
#
# ``adventure_eval.py`` itself is INCLUDED so the ruler fences the aggregation FORMULAS too, not just
# the JSON thresholds: a change to how a dimension is computed (completion honesty, the pass/behavioral
# derivation, the weakest-link pick) re-versions the adventure trend the same way a threshold edit does.
# This OVER-versions on a pure comment edit — an accepted, honest-direction tradeoff (a stale hash that
# claimed comparability across a formula change would be the worse failure).
ADVENTURE_CONFIG_FILES: list[str] = [
    "adventure_eval_config.json",
    "adventure_eval.py",
    "quest_progress.py",
]


def _content_hash(files: list[str], prefix: str, qa_dir: Path | None = None) -> str:
    """Order-stable sha256 over (name, content) pairs; absent files hash as a sentinel."""
    root = qa_dir or _QA_DIR
    h = hashlib.sha256()
    for name in sorted(files):
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"<absent>")
        h.update(b"\0")
    return prefix + h.hexdigest()[:12]


def scoring_config_version(qa_dir: Path | None = None) -> str:
    """Return a stable short hash (``sc_xxxxxxxxxxxx``) of the FULL scoring ruler's contents."""
    return _content_hash(SCORING_CONFIG_FILES, "sc_", qa_dir)


def lens_config_version(qa_dir: Path | None = None) -> str:
    """Return a stable short hash (``lc_xxxxxxxxxxxx``) of the LENS ruler's contents (#725).

    Distinct ``lc_`` prefix so a lens hash can never be mistaken for (or checked against) a
    full-ruler ``sc_`` hash — the two namespaces are guarded separately in scores_db.add_run.
    """
    return _content_hash(LENS_CONFIG_FILES, "lc_", qa_dir)


def artifact_config_version(qa_dir: Path | None = None) -> str:
    """Return a stable short hash (``ac_xxxxxxxxxxxx``) of the ARTIFACT ruler's contents (HV1, #1323).

    Distinct ``ac_`` prefix so an artifact-ruler hash can never be mistaken for (or checked against) a
    full-ruler ``sc_`` or lens ``lc_`` hash. Fences the harvest loop's per-artifact scores on their
    own axis, entirely independent of the engine-duo rulers.
    """
    return _content_hash(ARTIFACT_CONFIG_FILES, "ac_", qa_dir)


def adventure_config_version(qa_dir: Path | None = None) -> str:
    """Return a stable short hash (``av_xxxxxxxxxxxx``) of the ADVENTURE ruler's contents (A-T).

    Distinct ``av_`` prefix so an adventure-ruler hash can never be mistaken for (or checked against)
    a full-ruler ``sc_``, lens ``lc_``, or artifact ``ac_`` hash. Fences the N-run adventure
    aggregator's weakest-link verdict + per-dimension thresholds on their own axis, independent of the
    engine-duo and artifact rulers.
    """
    return _content_hash(ADVENTURE_CONFIG_FILES, "av_", qa_dir)


def scoring_config_label(qa_dir: Path | None = None) -> str:
    """A human-readable label + the hash, e.g. ``ruler@sc_a1b2c3d4e5f6 (9 files)``."""
    root = qa_dir or _QA_DIR
    present = sum(1 for n in SCORING_CONFIG_FILES if (root / n).is_file())
    return f"ruler@{scoring_config_version(root)} ({present}/{len(SCORING_CONFIG_FILES)} files)"


if __name__ == "__main__":
    if "--files" in sys.argv:
        for n in sorted(SCORING_CONFIG_FILES):
            print(n)
    elif "--artifact-files" in sys.argv:
        for n in sorted(ARTIFACT_CONFIG_FILES):
            print(n)
    elif "--adventure-files" in sys.argv:
        for n in sorted(ADVENTURE_CONFIG_FILES):
            print(n)
    elif "--adventure" in sys.argv:
        print(adventure_config_version())
    elif "--label" in sys.argv:
        print(scoring_config_label())
    elif "--lens" in sys.argv:
        print(lens_config_version())
    elif "--artifact" in sys.argv:
        print(artifact_config_version())
    else:
        print(scoring_config_version())
