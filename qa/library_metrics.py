#!/usr/bin/env python3
"""library_metrics.py — HV5 flywheel's own eval: a snapshot of the harvest loop's own health
(docs/roadmap/PRODUCT-ROADMAP.md §4c HV5, epic #1327, slice 2 of the flywheel-ops sprint).

WHY THIS EXISTS
---------------
The epic names this explicitly: "THE FLYWHEEL'S OWN EVAL: the %library-sourced vs lens-scores
trend — 'less AI dependence' as a measured claim." Everything else in the HV-series scores PLAY
QUALITY (runs) or CONTENT QUALITY (artifacts); this module scores the LOOP ITSELF — how big the
library has grown, how much of it is actually being reused, how often a nomination clears the
promotion gate, and (once HV4 wires per-run attribution) what fraction of a run's beats came from
the library instead of fresh AI generation.

WHAT IT DOES
------------
Reads (never writes) the promoted `library/` pack + its processed-log, and appends ONE snapshot row
to the additive `library_metrics` table in qa/scores.db via qa/scores_db.add_library_metrics — its
SOLE writer (mirrors "promote.py is the sole writer of runs/artifacts" for its own tables). Reads:

  * library/**/*.json (every class subdir: quests/npcs/locations/encounters/rooms) for
    {size_total, size_by_class, size_by_tier, Σreuse_count} — a pure directory scan, no schema
    coupling beyond the `class`/`tier`/`reuse_count` keys tools/library/promote.py's build_entry
    already writes onto every entry.
  * library/.promoted.jsonl (promote.py's PROCESSED_LOG — one line per {artifact_id, verdict, tier,
    at}) for {promoted_total, rejected_total, promotion_pass_rate}. Absent/empty log -> pass_rate
    is None (no batch has run yet; NOT zero — zero would falsely claim "every batch failed").

pct_library_sourced is intentionally left None/omittable here: attributing a RUN's beats to
"library-sourced vs fresh-gen" is HV4 wiring (questgen._derive_hooks' library candidate source,
roadmap §4c "needs HV3") that does not exist yet in this repo state. snapshot_library() accepts an
explicit override so a future HV4-aware caller can pass a measured value without this module
guessing at one — leaving it unset is the correct, honest "not measured this snapshot" state (the
same discipline scores_db already uses for every additive, not-yet-wired column).

DISCIPLINE
----------
* READ-ONLY over library/ — never a second writer of promote.py's pack (no entry is edited/moved).
* Additive: scores.db writes go ONLY through qa/scores_db.py's add_library_metrics (never hand-
  rolled SQL here or anywhere else).
* Idempotent scan (a pure directory read); NOT idempotent as a WRITE — each invocation intentionally
  appends a NEW snapshot row (a time-series of "library health over time", the same shape as
  scores_db.trends_json gives for runs). Running this twice in a row with an unchanged library/
  simply records two identical readings a few seconds apart — that is the correct trend behavior,
  not a bug to dedup away.
* Offline-safe: pure filesystem + sqlite reads/writes, no LLM, no subprocess, no network.

USAGE
-----
    python3 qa/library_metrics.py [--library library/] [--db qa/scores.db] [--sha <short-sha>]
                                  [--pct-library-sourced 0.0-1.0] [--notes TEXT] [--render]
                                  [--dry-run]

``--render`` also regenerates qa/library_metrics_ledger.md after the snapshot (mirrors --render on
scores_db.py's own CLI). ``--dry-run`` computes + prints the snapshot payload but appends nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = QA_DIR.parent
sys.path.insert(0, str(QA_DIR))
import scores_db  # noqa: E402

DEFAULT_LIBRARY_DIR = _REPO_ROOT / "library"
PROCESSED_LOG_NAME = ".promoted.jsonl"  # matches tools/library/promote.py's PROCESSED_LOG constant

# Mirrors tools/library/promote.py's ENTRY_CLASSES / _CLASS_TO_SUBDIR (kept as a local literal
# rather than importing promote.py, so this reader never depends on the writer's import surface —
# a pure filesystem scan needs only the subdir names, which are part of the on-disk CONTRACT, not
# an implementation detail that could drift silently).
_CLASS_SUBDIRS: tuple[str, ...] = ("quests", "npcs", "locations", "encounters", "rooms")
_VALID_TIERS: tuple[str, ...] = ("experimental", "stable", "canonical")


# ---------------------------------------------------------------------------
# library/ directory scan (read-only)
# ---------------------------------------------------------------------------
def scan_library(library_dir: Path | str) -> dict:
    """Read every entry under library/<class>s/*.json and roll up size + tier + reuse counters.

    Returns {size_total, size_by_class: {cls: n}, size_by_tier: {tier: n}, reuse_count_sum}. A
    missing library_dir, or one with no class subdirs yet, yields all-zero counters (an empty/not-
    yet-promoted library is a valid, measurable state — not an error). A malformed entry JSON is
    counted toward size_total (it undeniably occupies a library slot) but contributes 0 to
    reuse_count_sum and is NOT counted under any tier (an unreadable tier is unknown, not
    "experimental" by default — that would misrepresent the tier distribution)."""
    library_dir = Path(library_dir)
    size_by_class: dict[str, int] = {}
    size_by_tier: dict[str, int] = {t: 0 for t in _VALID_TIERS}
    reuse_sum = 0
    total = 0

    if not library_dir.exists():
        return {
            "size_total": 0,
            "size_by_class": {},
            "size_by_tier": dict(size_by_tier),
            "reuse_count_sum": 0,
        }

    for subdir in _CLASS_SUBDIRS:
        d = library_dir / subdir
        if not d.is_dir():
            continue
        cls_name = subdir[:-1]  # "quests" -> "quest", "rooms" -> "room", etc.
        for entry_path in sorted(d.glob("*.json")):
            total += 1
            size_by_class[cls_name] = size_by_class.get(cls_name, 0) + 1
            try:
                entry = json.loads(entry_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # counted in size_total above; unreadable beyond that
            if not isinstance(entry, dict):
                continue
            tier = entry.get("tier")
            if tier in size_by_tier:
                size_by_tier[tier] += 1
            rc = entry.get("reuse_count")
            if isinstance(rc, (int, float)):
                reuse_sum += rc

    return {
        "size_total": total,
        "size_by_class": size_by_class,
        "size_by_tier": size_by_tier,
        "reuse_count_sum": int(reuse_sum),
    }


# ---------------------------------------------------------------------------
# promotion pass-rate (library/.promoted.jsonl — promote.py's own processed-log)
# ---------------------------------------------------------------------------
def scan_promotion_log(library_dir: Path | str) -> dict:
    """Read library/.promoted.jsonl and roll up {promoted_total, rejected_total, pass_rate}.

    Tolerant reader: a malformed line is skipped (never raises — a metrics snapshot must not fail
    because one historical batch line got corrupted). Verdicts other than "promoted"/"rejected"
    (e.g. "skipped-unscored" / "score-failed", if a future writer ever appends those here) are
    counted in neither bucket and do not affect the pass-rate denominator — the rate is
    specifically "of the artifacts a batch actually GATED, how many passed", not "of everything
    ever attempted". An absent or empty log yields pass_rate=None (no batch has run yet) rather
    than 0.0 (which would falsely read as "every batch failed")."""
    p = Path(library_dir) / PROCESSED_LOG_NAME
    promoted = 0
    rejected = 0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            verdict = rec.get("verdict")
            if verdict == "promoted":
                promoted += 1
            elif verdict == "rejected":
                rejected += 1

    gated = promoted + rejected
    pass_rate = (promoted / gated) if gated > 0 else None
    return {"promoted_total": promoted, "rejected_total": rejected, "promotion_pass_rate": pass_rate}


# ---------------------------------------------------------------------------
# the snapshot writer
# ---------------------------------------------------------------------------
def snapshot_library(
    *,
    library_dir: Path | str = DEFAULT_LIBRARY_DIR,
    db_path: Path | str = scores_db.DB_PATH,
    library_sha: Optional[str] = None,
    pct_library_sourced: Optional[float] = None,
    notes: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Compute one library-health reading and append it via scores_db.add_library_metrics.

    Returns the computed payload dict (the exact fields passed to add_library_metrics, plus
    ``row_id`` when written). Under ``dry_run`` nothing is written and ``row_id`` is omitted — a
    pure preview of what the snapshot WOULD record (mirrors nightly_harvest.py's / promote.py's own
    --dry-run contract: compute, report, write nothing).
    """
    library_dir = Path(library_dir)
    counts = scan_library(library_dir)
    promo = scan_promotion_log(library_dir)

    # Record a repo-relative source_path when library_dir lives under this checkout — an absolute
    # path (e.g. /Users/<you>/WorldOS/library) is developer-local and not reproducible for anyone
    # else (or CI) re-running this snapshot from a different checkout root.
    try:
        source_path = str(library_dir.resolve().relative_to(_REPO_ROOT))
    except ValueError:
        source_path = str(library_dir)

    payload: dict[str, Any] = {
        "library_sha": library_sha,
        "size_total": counts["size_total"],
        "size_by_class_json": counts["size_by_class"],
        "size_by_tier_json": counts["size_by_tier"],
        "reuse_count_sum": counts["reuse_count_sum"],
        "promotion_pass_rate": promo["promotion_pass_rate"],
        "promoted_total": promo["promoted_total"],
        "rejected_total": promo["rejected_total"],
        "pct_library_sourced": pct_library_sourced,
        "source_path": source_path,
        "notes": notes,
    }

    if dry_run:
        return payload

    row_id = scores_db.add_library_metrics(db_path=db_path, **payload)
    payload["row_id"] = row_id
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--library", default=str(DEFAULT_LIBRARY_DIR), help="library/ pack root to snapshot")
    p.add_argument("--db", default=str(scores_db.DB_PATH), help="path to scores.db")
    p.add_argument("--sha", default=None, help="short git SHA of the repo state this snapshot reads")
    p.add_argument("--pct-library-sourced", type=float, default=None,
                   help="0.0-1.0 %% of a run's beats sourced from library/ (HV4 wiring; omit if unmeasured)")
    p.add_argument("--notes", default=None)
    p.add_argument("--render", action="store_true",
                   help="also regenerate qa/library_metrics_ledger.md after the snapshot")
    p.add_argument("--dry-run", action="store_true",
                   help="compute + print the snapshot payload; append nothing")
    args = p.parse_args(argv)

    payload = snapshot_library(
        library_dir=args.library, db_path=args.db, library_sha=args.sha,
        pct_library_sourced=args.pct_library_sourced, notes=args.notes, dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.render and not args.dry_run:
        scores_db.render_library_metrics_markdown(args.db)
        print(f"rendered {scores_db.LIBRARY_METRICS_MD_PATH}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
