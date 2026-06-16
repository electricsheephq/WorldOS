#!/usr/bin/env python3
"""Quantify SCORING NOISE so we know when a single duo run is trustworthy vs. when a
median-of-N is required to gate a release / auto-merge.

The three LLM lenses (story-craft / mechanical / angry-dm; see ``qa/SCORING.md``) are
stochastic graders: re-scoring *the same comparable run* yields a slightly different
``overall`` each time. This module measures that per-lens jitter from EXISTING on-disk
score artifacts and asserts it stays under a documented NOISE FLOOR. The constants here
and the "Variance & noise floor" section of ``qa/SCORING.md`` are the same numbers — if
one drifts, this test makes the other one fail.

Two on-disk sources are read (whichever exist):
  1. ``qa/scores.db`` — the canonical SQLite ledger (``qa/scores_db.py``). Each ``runs``
     row carries the three per-lens overalls (``story_overall`` / ``mech_overall`` /
     ``angrydm_overall``). Rows that share a comparability key AND are behaviorally GREEN
     form a "comparable cluster"; the spread *within* a cluster is the scoring noise.
  2. ``qa/transcripts/*.{tolkien,score,angrydm}.json`` — committed per-lens scorecards, if
     any. Each carries a single ``overall`` (see ``score_schema*.json``). Comparable cards
     (same stem prefix, same lens) form a cluster the same way.

LIVE-LLM scoring is NOT available in CI (gateway-free / null-backend; never touches Eva or
the global mcp config), so every test here reads ONLY on-disk artifacts and is fully
deterministic. A test that *would* need a live scorer is marked ``skipif`` and never runs
in CI. If too few on-disk artifacts exist to compute a spread, the test SKIPS (it never
fails for lack of data — an empty ledger is today's behavior).

Pure stdlib (sqlite3 + json + statistics) + pytest. Imports neither the engine nor the
viewer. Additive: a brand-new file; touches no existing test. Run:
    uv run --directory servers/engine python -m pytest qa/test_lens_variance.py -q -p no:xdist
or simply:
    python3 -m pytest qa/test_lens_variance.py -q
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

import scores_db  # noqa: E402  (canonical ledger reader; reuse, do not duplicate)

# DOCUMENTED NOISE FLOOR — now the single source of truth in qa/lens_noise_floor.py (shared with
# qa/detect_regression.py and mirrored in qa/SCORING.md § "Variance & noise floor"). It was DERIVED
# empirically from the comparable GREEN clusters in the committed qa/scores.db (2026-06-16 snapshot):
# observed max within-cluster population stdev story 0.15 / mech 0.25 / angry-dm 0.35 (ranges
# 0.30 / 0.50 / 0.70), rounded UP for headroom. If a future re-score blows past these, this test goes
# RED — raise the floor in qa/lens_noise_floor.py (one edit) and update SCORING.md.
from lens_noise_floor import LENS_COLUMNS, NOISE_FLOOR  # noqa: E402

# Per-lens scorecard filename suffix -> ledger lens column (for qa/transcripts/*.json).
CARD_SUFFIX_TO_LENS = {
    ".tolkien.json": "story_overall",
    ".score.json": "mech_overall",
    ".angrydm.json": "angrydm_overall",
    ".angry_dm.json": "angrydm_overall",
}

# A cluster needs at least this many comparable scores before its spread is meaningful.
_MIN_CLUSTER = 2
# We need at least this many comparable clusters (across all lenses) before we ASSERT a
# floor; otherwise the on-disk corpus is too thin and we SKIP rather than fail.
_MIN_CLUSTERS_TO_ASSERT = 1


def _green(behavioral) -> bool:
    """A score is only a real number on a GREEN run; RED runs are rubric-capped to <=2.5
    (see SCORING.md § RED-cap) and must NOT be mixed into a noise measurement."""
    return (behavioral or "").strip().upper().startswith("GREEN")


def _comparability_key(row: dict) -> tuple:
    """Two scores are comparable only if produced by the same harness against the same
    build with the same scorer under the same ruler. Fencing on these fields is what makes
    the *remaining* spread attributable to scorer stochasticity rather than to a different
    build / surface / methodology / scorer / rubric. ``lens_config_version`` /
    ``scoring_config_version`` are folded in when present so a rubric recalibration never
    silently lands in the same cluster as the old ruler."""
    return (
        row.get("build_sha"),
        row.get("surface"),
        row.get("methodology"),
        row.get("scorer_model"),
        row.get("lens_config_version"),
        row.get("scoring_config_version"),
    )


def _clusters_from_ledger(rows: list[dict], lens: str) -> dict[tuple, list[float]]:
    """Group GREEN, non-null per-lens overalls by comparability key."""
    groups: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        val = r.get(lens)
        if val is None:
            continue
        if not _green(r.get("behavioral")):
            continue
        try:
            groups[_comparability_key(r)].append(float(val))
        except (TypeError, ValueError):
            continue
    return groups


def _clusters_from_transcripts(transcripts_dir: Path) -> dict[str, dict[str, list[float]]]:
    """Read committed per-lens scorecards in qa/transcripts/. Returns
    {lens_column: {cluster_key: [overall, ...]}}. A "cluster" is the run-stem prefix that
    precedes the lens suffix (e.g. ``runA.tolkien.json`` and ``runA.r2.tolkien.json`` only
    cluster if their pre-suffix stems match exactly). Missing dir / no files -> empty."""
    out: dict[str, dict[str, list[float]]] = {lens: defaultdict(list) for lens in LENS_COLUMNS}
    if not transcripts_dir.is_dir():
        return out
    for path in sorted(transcripts_dir.rglob("*.json")):
        name = path.name
        lens = next((CARD_SUFFIX_TO_LENS[s] for s in CARD_SUFFIX_TO_LENS if name.endswith(s)), None)
        if lens is None:
            continue
        suffix = next(s for s in CARD_SUFFIX_TO_LENS if name.endswith(s))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        overall = data.get("overall")
        if overall is None:
            continue
        try:
            overall = float(overall)
        except (TypeError, ValueError):
            continue
        # Cluster key = parent dir + stem with the lens suffix stripped + any "re-score"
        # marker collapsed: we treat the leading token before the first '.' as the run id.
        stem = name[: -len(suffix)]
        run_id = stem.split(".", 1)[0]
        cluster_key = f"{path.parent}::{run_id}"
        out[lens][cluster_key].append(overall)
    return out


def _spreads_for_lens(lens: str) -> list[tuple[str, list[float], float, float]]:
    """Return [(source_tag, values, pstdev, range)] for every comparable cluster (>=2
    scores) of ``lens`` found on disk, across BOTH the ledger and committed transcripts."""
    spreads: list[tuple[str, list[float], float, float]] = []

    # 1. Ledger clusters.
    if scores_db.DB_PATH.exists():
        rows = scores_db.fetch_rows(scores_db.DB_PATH)
        for key, vals in _clusters_from_ledger(rows, lens).items():
            if len(vals) >= _MIN_CLUSTER:
                spreads.append((f"ledger:{key}", vals, statistics.pstdev(vals), max(vals) - min(vals)))

    # 2. Committed per-lens scorecards (qa/transcripts/).
    tcards = _clusters_from_transcripts(QA_DIR / "transcripts")[lens]
    for key, vals in tcards.items():
        if len(vals) >= _MIN_CLUSTER:
            spreads.append((f"transcript:{key}", vals, statistics.pstdev(vals), max(vals) - min(vals)))

    return spreads


def _total_clusters_on_disk() -> int:
    return sum(len(_spreads_for_lens(lens)) for lens in LENS_COLUMNS)


# =======================================================================================
# Tests
# =======================================================================================


def test_noise_floor_constants_are_self_consistent():
    """Cheap structural guard (always runs, needs no data): the documented floor covers all
    three lenses with positive, finite bounds and a range >= stdev. This is the contract
    the rest of the suite asserts against and that qa/SCORING.md must mirror."""
    assert set(NOISE_FLOOR) == set(LENS_COLUMNS)
    for lens, floor in NOISE_FLOOR.items():
        assert floor["max_stdev"] > 0, lens
        assert floor["max_range"] > 0, lens
        # A range across >=2 samples is always >= the population stdev, so the range bound
        # must not be tighter than the stdev bound or it could never be the binding one.
        assert floor["max_range"] >= floor["max_stdev"], lens
        assert floor["label"]


@pytest.mark.parametrize("lens", LENS_COLUMNS)
def test_per_lens_noise_within_documented_floor(lens):
    """For each lens, every comparable on-disk cluster's spread must stay under the
    documented noise floor. If NO comparable cluster exists for this lens yet, SKIP — a thin
    corpus is today's behavior and must never fail CI (additive-by-default)."""
    spreads = _spreads_for_lens(lens)
    if not spreads:
        pytest.skip(
            f"no comparable on-disk clusters (>={_MIN_CLUSTER} GREEN re-scores) for "
            f"{NOISE_FLOOR[lens]['label']} yet; cannot measure spread"
        )
    floor = NOISE_FLOOR[lens]
    worst_stdev = max(s[2] for s in spreads)
    worst_range = max(s[3] for s in spreads)
    # Report the actual measured numbers in the failure message so a breach forces a
    # conscious re-derivation of the floor (and the SCORING.md doc), not a silent bump.
    detail = "; ".join(f"{tag} vals={vals} stdev={sd:.3f} range={rg:.2f}" for tag, vals, sd, rg in spreads)
    assert worst_stdev <= floor["max_stdev"], (
        f"{floor['label']}: measured within-cluster stdev {worst_stdev:.3f} exceeds documented "
        f"noise floor {floor['max_stdev']} — re-derive the floor + update qa/SCORING.md. [{detail}]"
    )
    assert worst_range <= floor["max_range"], (
        f"{floor['label']}: measured within-cluster range {worst_range:.2f} exceeds documented "
        f"noise floor {floor['max_range']} — re-derive the floor + update qa/SCORING.md. [{detail}]"
    )


def test_at_least_one_comparable_cluster_exists_or_skip():
    """Sanity: confirm SOME comparable cluster exists on disk so the floor is actually
    being exercised. If the corpus is too thin, SKIP (don't fail) — this records that the
    measurement is currently data-starved rather than passing vacuously."""
    n = _total_clusters_on_disk()
    if n < _MIN_CLUSTERS_TO_ASSERT:
        pytest.skip(
            "on-disk score corpus too thin to measure scoring noise "
            f"({n} comparable clusters; need >= {_MIN_CLUSTERS_TO_ASSERT}). "
            "Re-run once more GREEN re-scores are committed to qa/scores.db or qa/transcripts/."
        )
    assert n >= _MIN_CLUSTERS_TO_ASSERT


def test_median_of_n_shrinks_jitter_below_single_run():
    """The whole point of the noise floor: a SINGLE duo can sit a full noise-floor away from
    truth, but the MEDIAN of N re-scores collapses that jitter. This is a deterministic
    property test (no data needed) demonstrating WHY release/auto-merge gating uses
    median-of-N while velocity work tolerates a single duo. For a worst-case symmetric
    spread at the angry-dm floor, the median of 3 lands strictly inside the single-run
    band — the documented rule's justification, in code."""
    floor = NOISE_FLOOR["angrydm_overall"]["max_range"]
    truth = 4.0
    # Worst-case single run: as far from truth as the floor allows.
    single_run_error = floor / 2.0
    # Three re-scores straddling truth (low / on / high): median is the middle one.
    samples = sorted([truth - single_run_error, truth, truth + single_run_error])
    median = samples[1]
    median_error = abs(median - truth)
    assert median_error < single_run_error, (
        "median-of-3 must beat a worst-case single run; this is the basis for the "
        "single-duo-for-velocity / median-of-N-for-release rule in qa/SCORING.md"
    )
    assert median_error == 0.0  # symmetric straddle -> exact in this idealized model


# ---------------------------------------------------------------------------------------
# Live-scorer path: only meaningful with a real LLM scorer, which CI never has. This stays
# skipped in CI (gateway-free / null-backend invariant) and is here purely to document that
# the empirical re-derivation of the floor is an explicit, opt-in, NON-CI step.
# ---------------------------------------------------------------------------------------
_RUN_LIVE = os.environ.get("CLAWDND_LIVE_SCORER") == "1"


@pytest.mark.skipif(
    not _RUN_LIVE,
    reason="needs a LIVE LLM scorer (score.sh / score_openclaw.sh) — not available in CI "
    "(QA is gateway-free / null-backend). Set CLAWDND_LIVE_SCORER=1 to re-derive the floor.",
)
def test_live_rescore_floor_rederivation_placeholder():  # pragma: no cover - never runs in CI
    raise AssertionError(
        "Live re-derivation must be driven by qa/score.sh against committed transcripts, "
        "writing fresh .tolkien/.score/.angrydm cards into qa/transcripts/ before re-running "
        "the deterministic spread assertion above. This placeholder intentionally fails if "
        "ever forced on without that harness."
    )


if __name__ == "__main__":  # pragma: no cover
    for _lens in LENS_COLUMNS:
        _s = _spreads_for_lens(_lens)
        print(f"{_lens}: clusters={len(_s)}")
        for tag, vals, sd, rg in _s:
            print(f"   {tag}: n={len(vals)} stdev={sd:.3f} range={rg:.2f} vals={vals}")
