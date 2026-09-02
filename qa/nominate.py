#!/usr/bin/env python3
"""nominate.py — HV5 closeout auto-nominator (docs/roadmap/PRODUCT-ROADMAP.md §4c HV5, epic #1327).

WHY THIS EXISTS
---------------
Every scored run is simultaneously a QA datum AND a harvest candidate — "zero extra runs". After a
run closes out, its HV2-extracted artifacts (qa/artifacts_out/<campaign>/**/*.json) are scanned and
the promising ones are APPENDED to the nomination queue (qa/nominations.jsonl) that HV3's promotion
gate (tools/library/promote.py --batch, #1325) consumes. Artifact SCORING is a deferred nightly
batch (never inline) — nomination here is a cheap local reference: it reads already-extracted JSON
and the run's lens score from qa/scores.db, adds ZERO wall-clock to the duo path.

CONTRACT (matches promote.py's reader, PR #1338)
------------------------------------------------
Each nomination is one JSON object per line; the ONLY required key is ``artifact_id``. Optional keys
carried here: ``source_path`` (repo-relative path to the extracted artifact JSON) and
``curation_note`` (the heuristic that fired). promote.py invents no nomination heuristic — that logic
is SOLELY here.

DISCIPLINE
----------
* APPEND-ONLY on qa/nominations.jsonl; never rewrites/reorders existing lines.
* Additive NO-OP: nothing qualifying (or story below bar, or no artifacts) appends nothing.
* Idempotent: an artifact_id already present in the queue is not re-appended.
* NEVER a second writer of play-state or library/ — reads scores.db + artifacts_out only, writes the
  nomination queue only.

HEURISTICS (constants live in qa/closeout.py next to STORY_BAR, per epic #1327's takeover ruling)
-----------------------------------------------------------------------------------------------
* GATE  — the run's story_overall >= closeout.NOMINATION_STORY_BAR (== STORY_BAR). A run below the
  story bar nominates nothing; a run with no story score is treated as below-bar (conservative).
* quest — payload.resolution_status in {"completed", "resolved"} (the two engine quest-done enums).
* npc   — len(payload.dialogue_snippets) >= closeout.NOMINATION_TURN_MIN.
* other classes (location/encounter) are not auto-nominated in this slice.

USAGE
-----
    python3 qa/nominate.py <run-id> [--campaign <id>] [--artifacts-dir qa/artifacts_out]
                                    [--nominations qa/nominations.jsonl] [--db qa/scores.db]
                                    [--dry-run]
The run-id gates on scores.db; --campaign narrows which artifacts_out subtree to scan (default: every
campaign whose artifacts carry provenance.run_id == run-id).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))
import scores_db  # noqa: E402
import closeout  # noqa: E402

DEFAULT_ARTIFACTS_DIR = QA_DIR / "artifacts_out"
DEFAULT_NOMINATIONS = QA_DIR / "nominations.jsonl"
_REPO_ROOT = QA_DIR.parent


# ---------------------------------------------------------------------------
# per-class heuristics (the ONLY place the nomination rule lives, alongside closeout's constants)
# ---------------------------------------------------------------------------
_QUEST_DONE = ("completed", "resolved")


def _qualifies(art: dict) -> Optional[str]:
    """Return a short curation-note reason if this artifact qualifies for nomination, else None.

    Reads only the HV2 envelope's ``class`` + ``payload`` — no engine import, no scoring.
    """
    cls = art.get("class")
    payload = art.get("payload") or {}
    if cls == "quest":
        status = str(payload.get("resolution_status", "")).strip().lower()
        if status in _QUEST_DONE:
            return f"quest {status}"
    elif cls == "npc":
        snippets = payload.get("dialogue_snippets") or []
        n = len(snippets) if isinstance(snippets, list) else 0
        if n >= closeout.NOMINATION_TURN_MIN:
            return f"npc dialogue x{n} (>= {closeout.NOMINATION_TURN_MIN})"
    return None


# ---------------------------------------------------------------------------
# run gate (pure reader over scores_db)
# ---------------------------------------------------------------------------
def _run_clears_story_bar(run_id: str, db_path: Path | str) -> bool:
    """True when the run's story_overall >= NOMINATION_STORY_BAR. Missing run/score -> False.

    Returns on the FIRST row matching run_id — correct only because run_id is the runs table's
    PRIMARY KEY (scores_db.py add_run uses INSERT OR REPLACE on it), so at most one row can ever
    match. If that ever changes (e.g. a future per-lens split table), this first-match short-circuit
    would need to become an explicit "fetch by run_id" lookup instead.
    """
    for row in scores_db.fetch_rows(db_path):
        if row.get("run_id") == run_id:
            story = row.get("story_overall")
            try:
                return story is not None and float(story) >= closeout.NOMINATION_STORY_BAR
            except (TypeError, ValueError):
                return False
    return False


# ---------------------------------------------------------------------------
# artifact scan
# ---------------------------------------------------------------------------
def _iter_artifact_files(artifacts_dir: Path, campaign: Optional[str]) -> Iterable[Path]:
    """Yield extracted-artifact JSON paths under artifacts_dir, optionally scoped to one campaign."""
    root = artifacts_dir / campaign if campaign else artifacts_dir
    if not root.exists():
        return
    yield from sorted(root.rglob("*.json"))


def _rel_source_path(path: Path) -> str:
    """Repo-relative source_path when the artifact lives under the repo, else its string form."""
    try:
        return str(path.resolve().relative_to(_REPO_ROOT))
    except ValueError:
        return str(path)


def collect_nominations(
    run_id: str,
    *,
    artifacts_dir: Path | str | None = None,
    campaign: Optional[str] = None,
    db_path: Path | str = scores_db.DB_PATH,
) -> list[dict]:
    """The heuristic core: the ordered list of nomination records this run would append.

    A pure function of (scores.db, artifacts_out) — writes nothing. Returns [] when the run is below
    the story bar or nothing qualifies. Only artifacts whose provenance.run_id matches ``run_id`` are
    considered (an artifacts_out subtree can hold several runs' extractions).

    ``artifacts_dir`` defaults to the LIVE module DEFAULT_ARTIFACTS_DIR when None (resolved at call
    time, not bound at def time — so the closeout hook honors a runtime override / monkeypatch).
    """
    if not _run_clears_story_bar(run_id, db_path):
        return []
    artifacts_dir = Path(artifacts_dir if artifacts_dir is not None else DEFAULT_ARTIFACTS_DIR)
    out: list[dict] = []
    seen: set[str] = set()
    for path in _iter_artifact_files(artifacts_dir, campaign):
        try:
            art = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(art, dict):
            continue
        if (art.get("provenance") or {}).get("run_id") != run_id:
            continue
        aid = art.get("artifact_id")
        if not aid or aid in seen:
            continue
        reason = _qualifies(art)
        if reason is None:
            continue
        seen.add(aid)
        out.append({
            "artifact_id": aid,
            "source_path": _rel_source_path(path),
            "curation_note": reason,
        })
    return out


# ---------------------------------------------------------------------------
# append-only writer
# ---------------------------------------------------------------------------
def _parse_existing_ids(text: str) -> set[str]:
    """artifact_ids already in the queue, parsed from already-read text. Tolerant of malformed
    lines (shared by both the unlocked dry-run preview and the locked read-modify-write path)."""
    ids: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "artifact_id" in obj:
            ids.add(obj["artifact_id"])
    return ids


def _existing_ids(nominations_path: Path) -> set[str]:
    """artifact_ids already in the queue (for an unlocked, read-only preview e.g. --dry-run)."""
    if not nominations_path.exists():
        return set()
    return _parse_existing_ids(nominations_path.read_text(encoding="utf-8"))


def nominate(
    run_id: str,
    *,
    artifacts_dir: Path | str | None = None,
    campaign: Optional[str] = None,
    nominations_path: Path | str | None = None,
    db_path: Path | str = scores_db.DB_PATH,
    dry_run: bool = False,
) -> list[dict]:
    """Append the run's qualifying nominations to the queue (append-only, idempotent).

    Returns the records that were (or, in dry-run, would be) NEWLY appended — already-queued
    artifact_ids are filtered out. An empty result writes nothing (additive no-op).

    ``artifacts_dir`` / ``nominations_path`` default to the LIVE module constants when None (resolved
    at call time, so the closeout hook and tests can override them via the module attributes).

    The idempotency-read (``_existing_ids``) + append are done under an exclusive advisory lock
    (``fcntl.flock``) on the nominations file so two concurrent writers (two closeouts, or a
    closeout + a manual CLI run) can never both read the same "already queued" set and append the
    same fresh record twice, nor interleave partial JSON lines mid-write.
    """
    nominations_path = Path(nominations_path if nominations_path is not None else DEFAULT_NOMINATIONS)
    candidates = collect_nominations(
        run_id, artifacts_dir=artifacts_dir, campaign=campaign, db_path=db_path
    )
    if not candidates:
        return []
    if dry_run:
        already = _existing_ids(nominations_path)
        return [c for c in candidates if c["artifact_id"] not in already]

    nominations_path.parent.mkdir(parents=True, exist_ok=True)
    # Open in append mode (creates the file if absent) and hold an exclusive lock for the entire
    # read-dedup-then-append critical section — released automatically when the `with` exits.
    with nominations_path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.seek(0)
        already = _parse_existing_ids(fh.read())
        fresh = [c for c in candidates if c["artifact_id"] not in already]
        if fresh:
            fh.seek(0, 2)  # back to EOF (a+ position is unspecified after reading)
            for rec in fresh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        # lock released on context exit
    return fresh


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_id", help="the scored run-id whose artifacts to nominate")
    p.add_argument("--campaign", default=None, help="scope the scan to one campaign subtree")
    p.add_argument("--artifacts-dir", default=str(DEFAULT_ARTIFACTS_DIR))
    p.add_argument("--nominations", default=str(DEFAULT_NOMINATIONS))
    p.add_argument("--db", default=str(scores_db.DB_PATH))
    p.add_argument("--dry-run", action="store_true", help="report what would append; write nothing")
    args = p.parse_args(argv)

    fresh = nominate(
        args.run_id,
        artifacts_dir=args.artifacts_dir,
        campaign=args.campaign,
        nominations_path=args.nominations,
        db_path=args.db,
        dry_run=args.dry_run,
    )
    verb = "would nominate" if args.dry_run else "nominated"
    print(f"[nominate] {args.run_id}: {verb} {len(fresh)} artifact(s) -> {args.nominations}")
    for rec in fresh:
        print(f"  + {rec['artifact_id']}  ({rec['curation_note']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
