#!/usr/bin/env python3
"""artifact_snapshot_reader.py — THIN read-only adapter that hands HV1's calibration the artifacts it
needs, by CONSUMING HV2's canonical extractor (qa/export_campaign_artifacts.py, #1329/#1324).

RECONCILED (post-#1329 merge): HV2 now owns the real extractor and the canonical envelope
(data/library/artifact_schema.json). This module no longer duplicates extraction logic — it DELEGATES
to HV2's pure `build_artifacts(campaign_dict, ...)` and simply selects the class(es) HV1's calibration
panel wants (quests + NPCs from an existing finished campaign). Locations/encounters for calibration
come from the hand-authored canon controls (qa/artifact_controls/), so this reader only needs quest+npc.

Why keep a thin HV1-side reader at all (vs calling export_campaign_artifacts directly)? Convenience +
isolation for the calibration flow: it fills in the caller-supplied `extracted_at` (deterministic),
tolerates a snapshot with no transcript (quests/NPCs don't need one), and returns an in-memory list the
panel runner consumes — without HV1 having to reproduce HV2's CLI contract. If HV2 is unavailable it
falls back to nothing (raises), rather than forking a second extractor.

READ-ONLY: never writes play state. Writing to --out-dir (QA output) is optional.

CLI:
    python3 qa/artifact_snapshot_reader.py <snapshot.json> [--world W] [--run-id R] [--out-dir DIR]
                                           [--class quest,npc] [--extracted-at STR]
Prints the artifacts as a JSON array (and writes one file per artifact to --out-dir if given).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QA_DIR))

# HV2's canonical extractor (the schema-owning lane). We consume its pure per-class functions.
import export_campaign_artifacts as hv2  # noqa: E402

# Classes HV1's calibration reads from a live snapshot. HV2's extract_quests/extract_npcs are pure and
# do NOT require the transcript (NPC dialogue_snippets simply come back empty with no text blocks) —
# exactly what a thin calibration read needs.
_SNAPSHOT_CLASSES = ("quest", "npc")


def _default_extracted_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract(
    snapshot: dict,
    *,
    world: Optional[str] = None,
    run_id: Optional[str] = None,
    extracted_at: Optional[str] = None,
    classes: tuple[str, ...] = _SNAPSHOT_CLASSES,
) -> list[dict]:
    """Return the requested classes' canonical artifacts from a campaign snapshot dict, via HV2.

    Delegates to HV2's pure per-class extractors so every artifact is byte-for-byte the canonical
    envelope (non-null provenance.campaign_id, canonical payload shape). `extracted_at` is
    caller-supplied (deterministic re-runs) — defaults to now() only when omitted.
    """
    campaign_id = snapshot["id"]
    world = world or snapshot.get("world_id", "")
    sha = snapshot.get("engine_sha") or None
    ea = extracted_at or _default_extracted_at()

    def provenance_base():
        return hv2._make_provenance(campaign_id, run_id, sha, ea)

    out: list[dict] = []
    if "quest" in classes:
        out += hv2.extract_quests(snapshot, provenance_base, world)
    if "npc" in classes:
        # No transcript in a thin calibration read → empty text blocks (dialogue_snippets come back []).
        out += hv2.extract_npcs(snapshot, provenance_base, world, [])
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot", help="path to a campaign snapshot.json (or a *.state.json)")
    ap.add_argument("--world", default=None, help="world id (default: snapshot.world_id)")
    ap.add_argument("--run-id", default=None, help="QA run id to stamp into provenance (FK to runs.run_id)")
    ap.add_argument("--extracted-at", default=None,
                    help="caller-supplied extraction timestamp (deterministic; default now())")
    ap.add_argument("--out-dir", default=None, help="write one <artifact_id>.json per artifact here")
    ap.add_argument("--class", dest="classes", default="quest,npc",
                    help="comma-separated classes to extract (quest,npc)")
    args = ap.parse_args(argv)

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    classes = tuple(s.strip() for s in args.classes.split(",") if s.strip())
    arts = extract(snapshot, world=args.world, run_id=args.run_id,
                   extracted_at=args.extracted_at, classes=classes)

    if args.out_dir:
        d = Path(args.out_dir)
        d.mkdir(parents=True, exist_ok=True)
        for a in arts:
            safe = a["artifact_id"].replace(":", "__").replace("/", "_")
            (d / f"{safe}.json").write_text(json.dumps(a, indent=2, ensure_ascii=False) + "\n",
                                            encoding="utf-8")
        print(f"wrote {len(arts)} artifact(s) to {d}", file=sys.stderr)
    print(json.dumps(arts, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
