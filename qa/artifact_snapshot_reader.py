#!/usr/bin/env python3
"""artifact_snapshot_reader.py — THIN, read-only extraction of scoreable artifacts from a campaign
play-state snapshot (HV1 calibration, #1323).

SCOPE (deliberately minimal): this is NOT the full harvest extractor — that is HV2's job
(export_campaign_artifacts.py, #1324). This ships only enough extraction for HV1's CALIBRATION panel:
pull the QUESTS and the (met) NPCs directly out of an existing finished campaign's snapshot.json,
serialize each into the shared data/library/artifact_schema.json envelope, and hand them to
qa/artifact_score.py. Locations/encounters for calibration come from hand-authored canon controls
(qa/artifact_controls/), so the snapshot reader only needs quests + NPCs.

READ-ONLY: imports nothing from the engine writer path, never writes play state. It reads the JSON
snapshot directly (same discipline as qa/snapshot_world_state.py) using the engine's real field names,
defensively (a missing field → today's-behavior default), so an old snapshot round-trips.

CLI:
    python3 qa/artifact_snapshot_reader.py <snapshot.json> [--world W] [--run-id R] [--out-dir DIR]
                                           [--class quest|npc] [--met-only]
Prints the artifacts as a JSON array (and writes one file per artifact to --out-dir if given).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _provenance(snapshot: dict, run_id: Optional[str], source: str) -> dict:
    return {
        "campaign_id": snapshot.get("id"),
        "run_id": run_id,
        "sha": snapshot.get("engine_sha"),
        "extracted_at": _now(),
        "source": source,
    }


def _artifact_id(cls: str, world: str, source_id: str) -> str:
    return f"{cls}:{world}:{source_id}"


def extract_quests(snapshot: dict, *, world: str, run_id: Optional[str] = None) -> list[dict]:
    """Serialize every quest in the snapshot into the shared envelope (quest class)."""
    out: list[dict] = []
    quests = snapshot.get("quests") or {}
    # quests is a dict {quest_id: quest}; tolerate a list too.
    items = quests.values() if isinstance(quests, dict) else quests
    for q in items:
        if not isinstance(q, dict):
            continue
        qid = q.get("id") or q.get("title") or "unknown-quest"
        payload = {
            "title": q.get("title"),
            "hook": q.get("description"),   # the snapshot's quest.description carries the hook/premise
            "objectives": q.get("objectives") or [],
            "giver": q.get("giver_id"),
            "status": q.get("status"),
            # `evolves_to` is the closest snapshot signal for consequence/branching.
            "consequences": q.get("evolves_to"),
        }
        out.append({
            "artifact_id": _artifact_id("quest", world, qid),
            "class": "quest",
            "world": world,
            "provenance": _provenance(snapshot, run_id, "snapshot:quests"),
            "payload": {k: v for k, v in payload.items() if v not in (None, "", [], {})},
            "scores": None,
        })
    return out


def extract_npcs(snapshot: dict, *, world: str, run_id: Optional[str] = None,
                 met_only: bool = True) -> list[dict]:
    """Serialize NPCs (kind == 'npc') from the snapshot into the shared envelope (npc class).

    met_only (default): only NPCs the party actually MET this campaign — those are the ones the
    campaign realized, the meaningful calibration set. Set met_only=False to include the full roster.
    """
    out: list[dict] = []
    chars = snapshot.get("characters") or {}
    items = chars.values() if isinstance(chars, dict) else chars
    for c in items:
        if not isinstance(c, dict):
            continue
        if c.get("kind") != "npc":
            continue
        if met_only and not c.get("met"):
            continue
        cid = c.get("id") or c.get("name") or "unknown-npc"
        payload = {
            "name": c.get("name"),
            "role": c.get("role"),
            "personality": c.get("personality"),
            "dossier": c.get("companion_dossier") or c.get("backstory"),
            "want": c.get("arc"),
            "voice_id": c.get("voice_id"),
        }
        out.append({
            "artifact_id": _artifact_id("npc", world, cid),
            "class": "npc",
            "world": world,
            "provenance": _provenance(snapshot, run_id, "snapshot:characters"),
            "payload": {k: v for k, v in payload.items() if v not in (None, "", [], {})},
            "scores": None,
        })
    return out


def extract(snapshot: dict, *, world: str, run_id: Optional[str] = None,
            classes: tuple[str, ...] = ("quest", "npc"), met_only: bool = True) -> list[dict]:
    arts: list[dict] = []
    if "quest" in classes:
        arts += extract_quests(snapshot, world=world, run_id=run_id)
    if "npc" in classes:
        arts += extract_npcs(snapshot, world=world, run_id=run_id, met_only=met_only)
    return arts


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot", help="path to a campaign snapshot.json (or a *.state.json)")
    ap.add_argument("--world", default=None, help="world id (default: snapshot.world_id)")
    ap.add_argument("--run-id", default=None, help="QA run id to stamp into provenance (FK to runs.run_id)")
    ap.add_argument("--out-dir", default=None, help="write one <artifact_id>.json per artifact here")
    ap.add_argument("--class", dest="classes", default="quest,npc",
                    help="comma-separated classes to extract (quest,npc)")
    ap.add_argument("--all-npcs", action="store_true", help="include the full roster, not just met NPCs")
    args = ap.parse_args(argv)

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    world = args.world or snapshot.get("world_id") or "unknown-world"
    classes = tuple(s.strip() for s in args.classes.split(",") if s.strip())
    arts = extract(snapshot, world=world, run_id=args.run_id, classes=classes,
                   met_only=not args.all_npcs)

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
