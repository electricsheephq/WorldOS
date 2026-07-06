#!/usr/bin/env python3
"""promote.py — the HV3 eval-gated promotion pipeline (Act II §4c, #1325).

WHAT IT IS
----------
The harvest loop's PROMOTION gate. It reads a nomination queue (qa/nominations.jsonl), looks up each
nominated artifact's panel score (from the additive `artifacts` table HV1 populated, or — cleanly
separated, offline-skippable — scores it if unscored), applies the threshold gate, and writes the ones
that pass into the pack-shaped repo-root ``library/`` as reusable, tiered entries.

    nominations (qa/nominations.jsonl)
      → [score-if-unscored]  (a cleanly separated step; NO test depends on it running live)
      → threshold gate       (overall >= 4.0 AND every dim >= 3.0 AND control-valid → tier=stable)
      → library/ entries + artifacts-table rows

promote.py is the SOLE WRITER of ``library/``. It is ADDITIVE by default (an empty nomination queue
leaves the library byte-identical). It NEVER edits room_recipes.json or the asset registry — room
entries REFERENCE recipe keys + asset_ids by value only (byte-identity of both files is a hard
invariant, asserted in tests).

THE GATE (ratified epic #1325)
------------------------------
An artifact is promoted to ``tier=stable`` iff ALL of:
  * overall >= PROMOTE_OVERALL_MIN (4.0),
  * every per-dimension score >= PROMOTE_DIM_MIN (3.0), and
  * control-valid — the panel that produced the score had its disguised canon controls land in-band
    (qa/artifact_calibration_panel's ``panel_valid``; derived here from the panel's control rows).
``tier=canonical`` is HUMAN curation ONLY — promote.py never assigns it. A nomination that fails the
gate is recorded as processed (so --batch is idempotent) but NOT written to the library.

THE NOMINATION QUEUE (bootstrap ruling, epic addendum [HIGH])
-------------------------------------------------------------
Nothing upstream of HV3 produces qa/nominations.jsonl yet — HV5 (#1327, qa/closeout.py auto-nominator)
does, and it depends ON HV3. So for the FIRST batch this file is bootstrapped BY HAND, one JSON line
per artifact_id, sourced from HV2's qa/artifacts_out/<campaign>/**/*.json listings. promote.py does NOT
invent its own nomination heuristic — that logic belongs solely to HV5's closeout auto-nominator. Each
line is a JSON object; the ONLY required key is ``artifact_id``. Optional keys:
  * ``source_path`` — repo-relative/absolute path to the extracted artifact JSON (needed only for the
    score-if-unscored step and to embed the payload provenance into the library entry).
  * ``license``     — SPDX-ish license string for the entry (else --license default; else pack.json's).
  * ``curation_note`` — free text carried onto the entry.

PANEL INVOCATION (epic addendum [MED])
--------------------------------------
The score-if-unscored step calls HV1's plain function ``artifact_score.score_artifact_panel`` (added by
HV3 to artifact_score.py as the callable entrypoint the addendum requires). promote.py NEVER spawns
agent sub-tasks itself; any agent fan-out lives upstream in artifact_score.py. When the scorer is
offline (the current claude -p auth outage), run with ``--dry-run`` (no scoring, no library writes —
just the gate verdict on whatever is already scored) or ``--skip-unscored`` (promote only the
already-scored nominations; leave the unscored ones for a later live batch).

CLI CONTRACT (epic addendum [MED], owned here as HV3 implements it first)
------------------------------------------------------------------------
    promote.py --batch [--library DIR] [--nominations FILE] [--db qa/scores.db]
               [--license SPDX] [--skip-unscored] [--dry-run] [--budget 1.50]

``--batch`` reads qa/nominations.jsonl top-to-bottom, processes every UNPROCESSED line (idempotent —
re-running skips already-promoted / already-processed artifact_ids via the processed-log), exits 0 even
with zero promotions. There is no single-nomination mode in v1.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent.parent
_QA_DIR = _REPO_ROOT / "qa"
sys.path.insert(0, str(_QA_DIR))

import scores_db  # noqa: E402

# ── Gate constants (the ratified thresholds) ────────────────────────────────────────────────────
PROMOTE_OVERALL_MIN = 4.0
PROMOTE_DIM_MIN = 3.0

# ── Library layout ──────────────────────────────────────────────────────────────────────────────
DEFAULT_LIBRARY_DIR = _REPO_ROOT / "library"
DEFAULT_NOMINATIONS = _QA_DIR / "nominations.jsonl"
ENTRY_CLASSES: tuple[str, ...] = ("quest", "npc", "location", "encounter", "room")
_CLASS_TO_SUBDIR = {c: c + "s" for c in ENTRY_CLASSES}  # quest→quests, room→rooms, …
PACK_JSON = "pack.json"
PROCESSED_LOG = ".promoted.jsonl"  # append-only marker log: one line per processed nomination
DEFAULT_LICENSE = "proprietary"
PACK_VERSION = "0.1.0"


# ── Nomination queue ────────────────────────────────────────────────────────────────────────────
def read_nominations(path: Path | str) -> list[dict]:
    """Read qa/nominations.jsonl → a list of nomination dicts (one per non-blank line).

    Each line must be a JSON object with at least ``artifact_id``. A missing file yields [] (a batch
    with nothing to do exits 0). Blank lines are skipped; a malformed line raises loudly with its
    line number (a hand-authored bootstrap file should fail fast on a typo)."""
    p = Path(path)
    if not p.exists():
        return []
    noms: list[dict] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p}:{i}: not valid JSON: {e}") from e
        if not isinstance(obj, dict) or "artifact_id" not in obj:
            raise ValueError(f"{p}:{i}: nomination must be a JSON object with an 'artifact_id'")
        noms.append(obj)
    return noms


# ── Processed-log (idempotency) ─────────────────────────────────────────────────────────────────
def _processed_log_path(library_dir: Path) -> Path:
    return library_dir / PROCESSED_LOG


def read_processed(library_dir: Path) -> set[str]:
    """Set of artifact_ids already processed by a prior batch (promoted OR gate-rejected)."""
    p = _processed_log_path(library_dir)
    if not p.exists():
        return set()
    done: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            done.add(json.loads(line)["artifact_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return done


def _append_processed(library_dir: Path, artifact_id: str, verdict: str, tier: Optional[str]) -> None:
    """Append one marker line so a re-run skips this artifact_id (idempotency)."""
    p = _processed_log_path(library_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"artifact_id": artifact_id, "verdict": verdict, "tier": tier,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


# ── Score lookup + control-validity ─────────────────────────────────────────────────────────────
def _artifacts_by_id(db_path: Path | str) -> dict[str, dict]:
    """Index the `artifacts` table by artifact_id (newest row wins — fetch_artifacts is ts-desc)."""
    by_id: dict[str, dict] = {}
    for row in scores_db.fetch_artifacts(db_path):
        by_id.setdefault(row["artifact_id"], row)
    return by_id


def control_valid_for_panel(panel_id: Optional[str], rows: list[dict]) -> bool:
    """Was the panel that scored this artifact control-valid?

    A panel is control-valid iff it contained at least one disguised canon control AND every control's
    median landed inside its expected band. We reconstruct the verdict from the `artifacts` rows sharing
    ``panel_id``: each control row (is_control=1) carries its ``overall`` and its ``control_anchor``; the
    band is [anchor - NOISE, anchor + NOISE] with NOISE from the identity map. A panel with NO controls
    is NOT control-valid (we cannot vouch for the instrument on that run) — fail closed for `stable`.

    ``rows`` is the full artifacts table (list of dicts) so this is a pure function over already-fetched
    data (no second DB round-trip). A ``panel_id`` of None (a lone --no-panel single score) is never
    control-valid.
    """
    if not panel_id:
        return False
    noise = _control_noise_law()
    controls = [r for r in rows if r.get("panel_id") == panel_id and r.get("is_control")]
    if not controls:
        return False
    for r in controls:
        anchor = r.get("control_anchor")
        overall = r.get("overall")
        if anchor is None or overall is None:
            return False
        if not (anchor - noise <= overall <= anchor + noise):
            return False
    return True


def _control_noise_law() -> float:
    """The ±band half-width for a control (qa/artifact_controls_identity.json noise_law; default 1.2)."""
    ident = _QA_DIR / "artifact_controls_identity.json"
    try:
        return float(json.loads(ident.read_text(encoding="utf-8")).get("noise_law", 1.2))
    except (OSError, ValueError, json.JSONDecodeError):
        return 1.2


# ── The gate ────────────────────────────────────────────────────────────────────────────────────
class GateResult:
    __slots__ = ("passed", "tier", "reasons", "overall", "dims", "control_valid")

    def __init__(self, passed: bool, tier: Optional[str], reasons: list[str],
                 overall: Optional[float], dims: dict, control_valid: bool):
        self.passed = passed
        self.tier = tier
        self.reasons = reasons
        self.overall = overall
        self.dims = dims
        self.control_valid = control_valid

    def as_dict(self) -> dict:
        return {"passed": self.passed, "tier": self.tier, "reasons": self.reasons,
                "overall": self.overall, "dims": self.dims, "control_valid": self.control_valid}


def evaluate_gate(score_row: dict, *, control_valid: bool) -> GateResult:
    """Apply the ratified threshold gate to one scored `artifacts` row.

    stable iff overall >= 4.0 AND every dim >= 3.0 AND control_valid. canonical is NEVER assigned here
    (human curation only). Returns a GateResult with the pass/fail reasons for the batch log."""
    reasons: list[str] = []
    overall = score_row.get("overall")
    dims_raw = score_row.get("dims_json")
    dims: dict = {}
    if isinstance(dims_raw, str):
        try:
            dims = json.loads(dims_raw)
        except json.JSONDecodeError:
            dims = {}
    elif isinstance(dims_raw, dict):
        dims = dims_raw

    if overall is None:
        reasons.append("unscored (no overall)")
    elif overall < PROMOTE_OVERALL_MIN:
        reasons.append(f"overall {overall} < {PROMOTE_OVERALL_MIN}")

    low_dims = {k: v for k, v in dims.items() if isinstance(v, (int, float)) and v < PROMOTE_DIM_MIN}
    if low_dims:
        reasons.append(f"dim(s) below {PROMOTE_DIM_MIN}: {low_dims}")
    if not dims:
        reasons.append("no per-dimension scores")

    if not control_valid:
        reasons.append("panel not control-valid")

    if score_row.get("is_control"):
        reasons.append("row is a disguised canon control, not a promotable candidate")

    passed = not reasons
    return GateResult(passed, "stable" if passed else None, reasons, overall, dims, control_valid)


# ── Library entry writing ───────────────────────────────────────────────────────────────────────
def ensure_pack(library_dir: Path, *, name: str = "worldos-harvest", license: str = DEFAULT_LICENSE,
                provenance: Optional[dict] = None) -> Path:
    """Ensure library/ + pack.json + the per-class subdirs exist. Idempotent; ADDITIVE — an existing
    pack.json is left untouched (promote.py never rewrites pack metadata after creation)."""
    library_dir.mkdir(parents=True, exist_ok=True)
    for sub in _CLASS_TO_SUBDIR.values():
        (library_dir / sub).mkdir(parents=True, exist_ok=True)
    pack_path = library_dir / PACK_JSON
    if not pack_path.exists():
        pack = {"name": name, "version": PACK_VERSION, "license": license,
                "provenance": provenance or {"produced_by": "tools/library/promote.py",
                                             "roadmap": "Act II §4c HV3 (#1325)"}}
        pack_path.write_text(json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return pack_path


def _entry_filename(artifact_id: str) -> str:
    """Filesystem-safe filename 1:1 with the artifact_id (mirrors HV2's slug+hash discipline)."""
    import hashlib
    import re
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", artifact_id)
    digest = hashlib.sha1(artifact_id.encode("utf-8")).hexdigest()[:8]
    return f"{slug}__{digest}.json"


def build_entry(artifact_id: str, cls: str, score_row: dict, gate: GateResult, *,
                license: str, promoted_at: str, curation_note: Optional[str] = None,
                payload: Optional[dict] = None,
                room_ref: Optional[dict] = None) -> dict:
    """Build a library entry's metadata dict (the ratified entry shape).

    {artifact_id, class, provenance, scores{dims, overall, panel_id, ac_ruler}, tier, reuse_count,
     license, promoted_at, curation_note}. ROOM entries additionally carry a ``room_ref`` REFERENCING
     room_recipes.json recipe keys + registry asset_ids (never a copy of the recipe/registry data)."""
    provenance = {
        "run_id": score_row.get("run_id"),
        "world": score_row.get("world"),
        "sha": score_row.get("sha"),
        "source_path": score_row.get("source_path"),
    }
    entry = {
        "artifact_id": artifact_id,
        "class": cls,
        "provenance": provenance,
        "scores": {
            "dims": gate.dims,
            "overall": gate.overall,
            "panel_id": score_row.get("panel_id"),
            "ac_ruler": score_row.get("ac_ruler"),
        },
        "tier": gate.tier,
        "reuse_count": 0,
        "license": license,
        "promoted_at": promoted_at,
        "curation_note": curation_note,
    }
    if payload is not None:
        entry["payload"] = payload
    if cls == "room" and room_ref is not None:
        # A room entry REFERENCES recipe keys + asset_ids by value; promote.py never edits either source.
        entry["room_ref"] = {"recipe_key": room_ref.get("recipe_key"),
                             "asset_ids": list(room_ref.get("asset_ids", []))}
    return entry


def write_entry(library_dir: Path, entry: dict) -> Path:
    """Write ONE library entry JSON under library/<class>s/. Deterministic bytes (sorted keys)."""
    cls = entry["class"]
    sub = _CLASS_TO_SUBDIR.get(cls)
    if sub is None:
        raise ValueError(f"unknown entry class {cls!r}; expected one of {ENTRY_CLASSES}")
    cls_dir = library_dir / sub
    cls_dir.mkdir(parents=True, exist_ok=True)
    path = cls_dir / _entry_filename(entry["artifact_id"])
    path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# ── Score-if-unscored (cleanly separated; no test depends on it running live) ────────────────────
def score_if_unscored(nomination: dict, *, budget: str = "1.50",
                      db_path: Path | str = scores_db.DB_PATH) -> Optional[dict]:
    """Run HV1's panel on an unscored nomination, returning the fresh `artifacts` row (or None).

    Cleanly separated so the promotion PATH is fully testable without a live scorer: --dry-run and
    --skip-unscored both bypass this. It imports artifact_score lazily and calls its plain
    ``score_artifact_panel`` entrypoint (no agent fan-out here — that lives upstream in artifact_score).
    Requires the nomination to carry ``source_path`` (the artifact JSON to score). Fails fast if the
    loaded artifact's ``artifact_id`` does not match the nomination's (source_path drift/typo) rather
    than silently scoring the wrong artifact. Forwards ``panel_id``/``scorer_model`` from the
    nomination when present so a live-scored row can still land in a control-valid panel and pass the
    gate on a later batch (an un-panel'd row is otherwise permanently unpromotable — panel_id=None is
    never control-valid, see control_valid_for_panel)."""
    aid = nomination["artifact_id"]
    src = nomination.get("source_path")
    if not src:
        raise ValueError(f"cannot score unscored nomination {aid!r}: no 'source_path'")
    import artifact_score  # lazy: keeps the offline promotion path import-clean
    artifact = artifact_score.load_artifact(Path(src))
    if artifact.get("artifact_id") != aid:
        raise ValueError(
            f"source_path {src!r} loaded artifact_id {artifact.get('artifact_id')!r}, "
            f"expected {aid!r} from the nomination — refusing to score a mismatched artifact")
    artifact_score.score_artifact_panel(
        artifact, budget=budget, db_path=db_path,
        panel_id=nomination.get("panel_id"), scorer_model=nomination.get("scorer_model"),
    )
    return _artifacts_by_id(db_path).get(aid)


# ── The batch driver ────────────────────────────────────────────────────────────────────────────
def promote_batch(
    *,
    library_dir: Path | str = DEFAULT_LIBRARY_DIR,
    nominations_path: Path | str = DEFAULT_NOMINATIONS,
    db_path: Path | str = scores_db.DB_PATH,
    default_license: str = DEFAULT_LICENSE,
    skip_unscored: bool = False,
    dry_run: bool = False,
    budget: str = "1.50",
) -> dict:
    """Process every unprocessed nomination top-to-bottom. Returns a batch report dict.

    Idempotent: an artifact_id in the processed-log is skipped. Exits-0 semantics live in main(); this
    returns a report. On ``dry_run`` NOTHING is written (no library entries, no processed-log, no
    scoring) — it is a pure gate preview. On ``skip_unscored`` an unscored nomination is left for a
    later live batch (recorded as processed=skipped so the queue drains but not the library)."""
    library_dir = Path(library_dir)
    noms = read_nominations(nominations_path)
    processed = read_processed(library_dir) if not dry_run else set()
    rows_by_id = _artifacts_by_id(db_path)
    all_rows = scores_db.fetch_artifacts(db_path)
    promoted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    report: dict[str, Any] = {
        "nominations": len(noms), "promoted": 0, "rejected": 0, "skipped": 0,
        "already_processed": 0, "dry_run": dry_run, "entries": [], "details": [],
    }

    if not dry_run and noms:
        ensure_pack(library_dir, license=default_license)

    for nom in noms:
        aid = nom["artifact_id"]
        if aid in processed:
            report["already_processed"] += 1
            continue
        # Mark this artifact_id handled for the REST of this batch, not just future batches — a
        # nominations.jsonl with a duplicate artifact_id in the same file must not be scored/promoted
        # twice within one run (the on-disk processed-log only guards the NEXT run).
        processed.add(aid)

        row = rows_by_id.get(aid)
        if row is None or row.get("overall") is None:
            # unscored
            if skip_unscored or dry_run:
                report["skipped"] += 1
                report["details"].append({"artifact_id": aid, "verdict": "skipped-unscored"})
                if not dry_run:
                    _append_processed(library_dir, aid, "skipped-unscored", None)
                continue
            try:
                row = score_if_unscored(nom, budget=budget, db_path=db_path)
            except Exception as e:  # noqa: BLE001 — score-if-unscored must never abort the --batch run
                report["skipped"] += 1
                report["details"].append(
                    {"artifact_id": aid, "verdict": "score-failed", "error": str(e)})
                if not dry_run:
                    _append_processed(library_dir, aid, "score-failed", None)
                continue
            if row is None or row.get("overall") is None:
                report["skipped"] += 1
                report["details"].append({"artifact_id": aid, "verdict": "score-failed"})
                _append_processed(library_dir, aid, "score-failed", None)
                continue
            all_rows = scores_db.fetch_artifacts(db_path)  # refresh: new panel rows landed

        cv = control_valid_for_panel(row.get("panel_id"), all_rows)
        gate = evaluate_gate(row, control_valid=cv)
        detail = {"artifact_id": aid, "verdict": "promoted" if gate.passed else "rejected",
                  **gate.as_dict()}
        report["details"].append(detail)

        if not gate.passed:
            report["rejected"] += 1
            if not dry_run:
                _append_processed(library_dir, aid, "rejected", None)
            continue

        cls = row["class"]
        entry = build_entry(
            aid, cls, row, gate, license=nom.get("license") or default_license,
            promoted_at=promoted_at, curation_note=nom.get("curation_note"),
            payload=nom.get("payload"), room_ref=nom.get("room_ref"),
        )
        report["promoted"] += 1
        if not dry_run:
            path = write_entry(library_dir, entry)
            report["entries"].append(str(path))
            _append_processed(library_dir, aid, "promoted", gate.tier)
        else:
            report["entries"].append(f"(dry-run) {cls}/{aid}")

    return report


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", action="store_true", required=True,
                    help="process every unprocessed nomination (the only v1 mode)")
    ap.add_argument("--library", default=str(DEFAULT_LIBRARY_DIR), help="library/ pack root")
    ap.add_argument("--nominations", default=str(DEFAULT_NOMINATIONS), help="the nomination queue")
    ap.add_argument("--db", default=str(scores_db.DB_PATH), help="path to scores.db")
    ap.add_argument("--license", default=DEFAULT_LICENSE, help="default entry license (SPDX-ish)")
    ap.add_argument("--skip-unscored", action="store_true",
                    help="promote only already-scored nominations; leave unscored for a live batch")
    ap.add_argument("--dry-run", action="store_true",
                    help="gate preview only — write NOTHING (no scoring, no library, no processed-log)")
    ap.add_argument("--budget", default="1.50", help="per-scorer USD budget for score-if-unscored")
    args = ap.parse_args(argv)

    report = promote_batch(
        library_dir=Path(args.library), nominations_path=Path(args.nominations), db_path=args.db,
        default_license=args.license, skip_unscored=args.skip_unscored, dry_run=args.dry_run,
        budget=args.budget,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0  # batch always exits 0, even with zero promotions (CLI contract)


if __name__ == "__main__":
    raise SystemExit(main())
