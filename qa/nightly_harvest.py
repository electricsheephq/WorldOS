#!/usr/bin/env python3
"""nightly_harvest.py — HV5 nightly batch artifact scorer (docs/roadmap/PRODUCT-ROADMAP.md §4c HV5,
epic #1327, slice 2 of the flywheel-ops sprint).

WHY THIS EXISTS
---------------
qa/nominate.py (slice 1, PR #1342) appends candidates to qa/nominations.jsonl on the duo HOT PATH —
zero extra runs, zero added wall-clock. Per the epic, artifact SCORING is explicitly DEFERRED to a
nightly batch: "Artifact scoring runs in a NIGHTLY BATCH, never inline — duo wall-clock unchanged."
This module is that batch. It:

  1. reads the accumulated qa/nominations.jsonl (every line nominate.py has ever appended),
  2. finds nominations whose artifact_id has NO scored row in the `artifacts` table (qa/scores.db) —
     "unscored" mirrors promote.py's own definition (row missing OR row.overall is None),
  3. scores each one via HV1's plain callable panel entrypoint
     (qa/artifact_score.score_artifact_panel) — the SAME seam promote.py's score_if_unscored calls,
     so there is exactly ONE place a live LLM panel call happens in the harvest loop,
  4. records the result via scores_db.add_artifact (artifact_score does this internally), and
  5. is safe to run unattended (idempotent, resumable, bounded) via an OWN processed-log distinct
     from tools/library/promote.py's ``.promoted.jsonl`` (this batch's job is SCORING; promotion is a
     separate, later, human-cadence step over the now-scored rows — see promote.py --batch).

THIS SCRIPT NEVER RUNS LIVE IN A TEST. Every test in qa/test_nightly_harvest.py monkeypatches
``score_artifact_panel`` (or ``artifact_score.score_artifact`` transitively) to a fabricated stub —
mirrors qa/test_promote_pipeline.py's ``test_score_if_unscored_is_isolated_from_promotion_path``
discipline. Actual nightly runs (a live LLM panel) are the orchestrator's job, never CI/pytest.

BOUNDED + RESUMABLE + IDEMPOTENT
---------------------------------
* ``--max-per-run`` (default 20) caps how many artifacts ONE invocation scores — an unattended cron
  must never fan out unboundedly against a spiky queue. Leftover unscored nominations simply wait for
  the next nightly tick (resumable: no work is lost, nothing is double-counted).
* A scoring failure (score.sh sentinel / RuntimeError / any exception) for one artifact is caught and
  recorded as "failed" in this module's own log — it does NOT abort the batch (one bad artifact must
  never block the rest of the night's queue), and a "failed" artifact is retried on the NEXT run (it
  is deliberately NOT added to the skip-set) rather than permanently stuck.
* Idempotent: an artifact_id that already has a scored ``artifacts`` row (overall is not None) is
  always skipped, regardless of this module's own log — the scores table itself is the source of
  truth for "already scored" (the same check promote.py's score_if_unscored path relies on).
* A nomination whose artifact_id cannot be resolved to a loadable artifact JSON (missing
  ``source_path``, file gone, JSON malformed, or a class/id mismatch) is recorded as "load-failed" and
  skipped — it never aborts the batch.

CONTRACT (matches qa/nominate.py's record shape + promote.py's reader)
-----------------------------------------------------------------------
Reads qa/nominations.jsonl (one JSON object per line; required key ``artifact_id``, optional
``source_path``). A nomination with no ``source_path`` cannot be scored here (there is nothing to
load) and is recorded as "load-failed" — the same conservative failure mode promote.py's
score_if_unscored uses for a missing source_path.

DISCIPLINE
----------
* Reads qa/nominations.jsonl + qa/scores.db (``artifacts`` table); writes ONLY new `artifacts` rows
  (via artifact_score.record_artifact_score) + this module's own JSONL progress log. NEVER touches
  ``library/`` — promotion stays promote.py's sole responsibility, run separately (and later) once
  rows here are scored.
* Additive: an empty or fully-scored queue is a no-op (exit 0, nothing written).
* OFFLINE-SAFE by construction for every test: the live-scoring call is a single seam
  (``score_artifact_panel``) that tests replace with a fabricated stub.

USAGE
-----
    python3 qa/nightly_harvest.py [--nominations qa/nominations.jsonl] [--db qa/scores.db]
                                  [--max-per-run 20] [--budget 1.50] [--panel-id ID]
                                  [--log qa/nightly_harvest_log.jsonl] [--dry-run]

``--dry-run`` reports what WOULD be scored (the resolved, capped work list) and writes nothing (no
scoring call, no db row, no log line) — the offline preview mirrors promote.py's --dry-run contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))
import scores_db  # noqa: E402

DEFAULT_NOMINATIONS = QA_DIR / "nominations.jsonl"
DEFAULT_LOG = QA_DIR / "nightly_harvest_log.jsonl"
DEFAULT_MAX_PER_RUN = 20
DEFAULT_BUDGET = "1.50"


# ---------------------------------------------------------------------------
# nomination queue reader (tolerant — mirrors nominate.py's own malformed-line handling, NOT
# promote.py's fail-loud reader: a nightly batch must never abort on one bad historical line)
# ---------------------------------------------------------------------------
def read_nominations(path: Path | str) -> list[dict]:
    """Read qa/nominations.jsonl -> a list of nomination dicts (one per non-blank, well-formed line).

    A missing file yields [] (nothing nominated yet -> nothing to score). A malformed line is
    SKIPPED (not raised) — this batch runs unattended over a queue nominate.py has appended to for
    an arbitrary stretch of time, and one corrupt historical line must never block every future
    night's scoring."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "artifact_id" in obj:
            out.append(obj)
    return out


# ---------------------------------------------------------------------------
# "already scored" check — mirrors promote.py's own definition (row missing OR overall is None)
# ---------------------------------------------------------------------------
def _scored_ids(db_path: Path | str) -> set[str]:
    """artifact_ids that already carry a scored row (``overall`` is not None) in the artifacts table."""
    return {r["artifact_id"] for r in scores_db.fetch_artifacts(db_path) if r.get("overall") is not None}


# ---------------------------------------------------------------------------
# this module's own resumable progress log (distinct from promote.py's library/.promoted.jsonl —
# that log tracks PROMOTION outcomes; this one tracks SCORING attempts made by this batch)
# ---------------------------------------------------------------------------
def _append_log(log_path: Path, artifact_id: str, verdict: str, *, overall: Optional[float] = None,
               error: Optional[str] = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rec: dict[str, Any] = {
        "artifact_id": artifact_id, "verdict": verdict,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if overall is not None:
        rec["overall"] = overall
    if error:
        rec["error"] = error[:500]
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# artifact load (mirrors artifact_score.load_artifact's shape checks + promote.py's mismatch guard)
# ---------------------------------------------------------------------------
def _load_nomination_artifact(nomination: dict) -> dict:
    """Load + validate the artifact JSON a nomination points at. Raises ValueError on any problem
    (missing source_path, unreadable file, malformed JSON, or an artifact_id mismatch) — the caller
    catches this and records "load-failed" rather than letting one bad nomination abort the batch."""
    aid = nomination["artifact_id"]
    src = nomination.get("source_path")
    if not src:
        raise ValueError(f"nomination {aid!r} carries no 'source_path' — nothing to load/score")
    import artifact_score  # local import: keeps this module import-clean when artifact_score is absent
    artifact = artifact_score.load_artifact(Path(src))
    if artifact.get("artifact_id") != aid:
        raise ValueError(
            f"source_path {src!r} loaded artifact_id {artifact.get('artifact_id')!r}, "
            f"expected {aid!r} from the nomination — refusing to score a mismatched artifact"
        )
    return artifact


# ---------------------------------------------------------------------------
# the batch driver
# ---------------------------------------------------------------------------
def harvest_batch(
    *,
    nominations_path: Path | str = DEFAULT_NOMINATIONS,
    db_path: Path | str = scores_db.DB_PATH,
    max_per_run: int = DEFAULT_MAX_PER_RUN,
    budget: str = DEFAULT_BUDGET,
    panel_id: Optional[str] = None,
    scorer_model: str = "sonnet",
    log_path: Path | str = DEFAULT_LOG,
    dry_run: bool = False,
) -> dict:
    """Score every UNSCORED nomination, up to ``max_per_run``. Returns a batch report dict.

    Pure w.r.t. everything except: (a) new `artifacts` rows via score_artifact_panel for each
    artifact actually scored, and (b) an appended line per attempt in ``log_path`` — neither happens
    under ``dry_run`` (a pure preview of the capped work list, writing nothing, scoring nothing).

    Idempotent: an artifact_id already carrying a scored row (``overall`` is not None) is always
    skipped, regardless of ``log_path``'s history — the `artifacts` table itself is truth for
    "already scored". Resumable: nominations beyond ``max_per_run`` are simply left for the next
    call (nothing here marks them as failed/skipped-forever). Non-fatal: a single artifact's
    load or scoring failure is caught, logged, and does not stop the rest of the batch.
    """
    noms = read_nominations(nominations_path)
    already_scored = _scored_ids(db_path)

    # De-dup by artifact_id within this batch's candidate list (a nominations.jsonl may carry the
    # same artifact_id more than once across separate nominate.py runs) — score it at most once.
    seen: set[str] = set()
    candidates: list[dict] = []
    for nom in noms:
        aid = nom["artifact_id"]
        if aid in already_scored or aid in seen:
            continue
        seen.add(aid)
        candidates.append(nom)

    work = candidates[:max_per_run]
    remaining = len(candidates) - len(work)

    report: dict[str, Any] = {
        "nominations_total": len(noms),
        "already_scored": len(noms) - len(candidates) if noms else 0,
        "candidates_unscored": len(candidates),
        "scored": 0,
        "load_failed": 0,
        "score_failed": 0,
        "remaining_for_next_run": max(remaining, 0),
        "dry_run": dry_run,
        "details": [],
    }

    if dry_run:
        report["would_score"] = [c["artifact_id"] for c in work]
        return report

    import artifact_score  # module-level object so tests can monkeypatch score_artifact_panel on it

    log_path = Path(log_path)
    for nom in work:
        aid = nom["artifact_id"]
        try:
            artifact = _load_nomination_artifact(nom)
        except (ValueError, OSError, json.JSONDecodeError) as e:
            report["load_failed"] += 1
            report["details"].append({"artifact_id": aid, "verdict": "load-failed", "error": str(e)})
            _append_log(log_path, aid, "load-failed", error=str(e))
            continue

        try:
            card = artifact_score.score_artifact_panel(
                artifact, budget=budget, panel_id=panel_id, scorer_model=scorer_model,
                source_path=nom.get("source_path"), db_path=db_path,
            )
        except Exception as e:  # noqa: BLE001 — one artifact's scoring failure must never abort the batch
            report["score_failed"] += 1
            report["details"].append({"artifact_id": aid, "verdict": "score-failed", "error": str(e)})
            _append_log(log_path, aid, "score-failed", error=str(e))
            continue

        overall = card.get("overall")
        report["scored"] += 1
        report["details"].append({"artifact_id": aid, "verdict": "scored", "overall": overall})
        _append_log(log_path, aid, "scored", overall=overall)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--nominations", default=str(DEFAULT_NOMINATIONS))
    p.add_argument("--db", default=str(scores_db.DB_PATH))
    p.add_argument("--max-per-run", type=int, default=DEFAULT_MAX_PER_RUN,
                   help="cap on artifacts scored by ONE invocation (default 20; the queue's "
                        "remainder waits for the next nightly tick — never fans out unboundedly)")
    p.add_argument("--budget", default=DEFAULT_BUDGET, help="per-scorer USD budget passed to score.sh")
    p.add_argument("--panel-id", default=None, help="calibration-panel id to stamp on every row scored "
                                                     "this batch (None = a lone, non-panel score per "
                                                     "artifact—never control-valid on its own)")
    p.add_argument("--scorer-model", default="sonnet")
    p.add_argument("--log", default=str(DEFAULT_LOG), help="this batch's own resumable progress log")
    p.add_argument("--dry-run", action="store_true",
                   help="report the capped work list; score nothing, write nothing")
    args = p.parse_args(argv)

    if args.max_per_run < 0:
        p.error(f"--max-per-run must be >= 0 (got {args.max_per_run})")

    report = harvest_batch(
        nominations_path=args.nominations, db_path=args.db, max_per_run=args.max_per_run,
        budget=args.budget, panel_id=args.panel_id, scorer_model=args.scorer_model,
        log_path=args.log, dry_run=args.dry_run,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0  # a nightly batch always exits 0, even with zero scored (mirrors promote.py --batch)


if __name__ == "__main__":
    raise SystemExit(main())
