#!/usr/bin/env python3
"""Read-only golden-state extractor for campaign snapshots (WorldOS QA Lab Phase-1).

Loads a campaign ``snapshot.json`` and produces:

  1. A CANONICAL, deterministically-ordered JSON projection of ONLY the
     engine-mutated, regression-relevant world state — flags, faction
     reputation/standing/rank/joined, NPC attitude_value + met, quest
     status/objectives, quest_outcomes, day/time_of_day, last_combat_resolution,
     party/companion roster, and world_state (tenor + facts).
  2. A stable sha256 "golden hash" of that projection.

This is a REGRESSION DETECTOR. It exists so two engine builds (or a before/after
of a change) can be compared on the state that GATES play — the values gates and
triggers read (invariant #3: gates read engine-mutated flags/reputation/
attitude_value/day/standing, NEVER fiction) — without the comparison being
swamped by prose, narration, timestamps, or RNG-seed noise that legitimately
differs run-to-run.

EXCLUDED on purpose (fiction / prose / noise — not regression-relevant):
  * narrative prose: summary, lore, scenes, personality, backstory, appearance,
    mannerisms, notes, memory, descriptions, recent_narration, read_aloud, …
  * volatile noise: created_at / updated_at timestamps, engine_sha, any RNG seed,
    active_session_id / session_ids ordering, and any UNKNOWN top-level key
    (a future fiction/telemetry field can't perturb the hash by accident).

Design notes (load-bearing invariants):
  * READ-ONLY. This module imports NOTHING from the engine writer path and never
    writes state. It only reads the snapshot file handed to it. The engine
    remains the sole writer of state (invariant #1).
  * ADDITIVE. It reads the engine's REAL field names but is defensive: a missing
    field projects to its today's-behavior default, so an OLD snapshot lacking a
    newer key round-trips to the same projection an empty-default would (invariant
    #2). It tolerates either a Campaign-shaped dict or a small partial fixture.
  * Importing the engine's pydantic models from qa/ is awkward (it would pull the
    whole server) and unnecessary — we read the JSON directly but use the engine's
    field names (per spec). The projection is the contract, not the model.

CLI:
  python qa/snapshot_world_state.py <snapshot.json>              # print projection JSON
  python qa/snapshot_world_state.py <snapshot.json> --hash-only  # print the sha256 golden hash
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


# ── helpers ──────────────────────────────────────────────────────────────────


def load_snapshot(path: str | Path) -> dict:
    """Read a snapshot.json off disk into a plain dict (READ-ONLY).

    Raises FileNotFoundError if absent and ValueError if it isn't valid JSON / not
    a JSON object. No engine import, no disk write — the only I/O is this read.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")  # FileNotFoundError propagates as-is
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{p}: not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{p}: snapshot root must be a JSON object, got {type(data).__name__}")
    return data


def _canonicalize(obj: Any) -> Any:
    """Recursively reorder every dict by key so the projection is order-independent.

    Lists keep their order (a list's order can itself be regression-relevant — e.g.
    objectives, party); the PROJECTION layer decides which lists to sort for
    canonicality. dict ordering, by contrast, is never semantically meaningful in a
    snapshot, so we always sort dict keys here. Scalars pass through unchanged.
    """
    if isinstance(obj, dict):
        return {k: _canonicalize(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_canonicalize(x) for x in obj]
    return obj


def _sorted_str_keys(d: Any) -> dict:
    """A dict with keys sorted (stringified) — for projecting id->value maps stably.
    A non-dict (a malformed/absent value) projects to an empty dict (additive default).
    """
    if not isinstance(d, dict):
        return {}
    return {str(k): d[k] for k in sorted(d.keys(), key=str)}


# ── projection ───────────────────────────────────────────────────────────────


def _project_character(cid: str, ch: Any) -> dict:
    """Regression-relevant slice of one Character.

    Keeps ONLY engine-mutated, gate-readable values: the relationship gauge
    (attitude_value), the met latch, vitals/progression that drive mechanical
    outcomes (current_hp/max_hp/xp/dead/level via classes), conditions/exhaustion.
    Drops ALL prose (name kept as a stable label only — it's an identity anchor, not
    fiction the DM narrates; if a regression renames a char that IS worth catching).
    """
    if not isinstance(ch, dict):
        return {}
    return {
        "id": ch.get("id", cid),
        "name": ch.get("name", ""),
        "kind": ch.get("kind", "player"),
        # relationship gauge a companion-flip / attitude gate reads (invariant #3)
        "attitude_value": ch.get("attitude_value", 0),
        "met": bool(ch.get("met", False)),
        # mechanical vitals/progression that change play outcomes
        "current_hp": ch.get("current_hp"),
        "max_hp": ch.get("max_hp"),
        "temp_hp": ch.get("temp_hp", 0),
        "xp": ch.get("xp", 0),
        "exhaustion": ch.get("exhaustion", 0),
        "dead": bool(ch.get("dead", False)),
        "stable": bool(ch.get("stable", False)),
        # conditions list is gate-relevant; sort for canonicality (order is noise here)
        "conditions": sorted(str(c) for c in (ch.get("conditions") or [])),
        # class levels (id+level) — a level-up regression is worth catching; prose excluded
        "classes": [
            {"name": (cl.get("name") if isinstance(cl, dict) else None),
             "level": (cl.get("level") if isinstance(cl, dict) else None)}
            for cl in (ch.get("classes") or [])
        ],
    }


def _project_quest(qid: str, q: Any) -> dict:
    """Regression-relevant slice of one Quest: stage/outcome state, never prose."""
    if not isinstance(q, dict):
        return {}
    return {
        "id": q.get("id", qid),
        "title": q.get("title", ""),
        "status": q.get("status", "active"),
        "objectives": list(q.get("objectives") or []),
        "completed_objectives": sorted(str(o) for o in (q.get("completed_objectives") or [])),
        "milestone_awarded": bool(q.get("milestone_awarded", False)),
    }


def _project_faction(fid: str, f: Any) -> dict:
    """Regression-relevant slice of one Faction: the gauges gates read, never prose."""
    if not isinstance(f, dict):
        return {}
    return {
        "id": f.get("id", fid),
        "name": f.get("name", ""),
        "reputation": f.get("reputation", 0),
        "standing": f.get("standing", 0),
        "rank": f.get("rank", 0),
        "joined": bool(f.get("joined", False)),
        "questline_arc_id": f.get("questline_arc_id", ""),
    }


def _project_world_state(ws: Any) -> dict | None:
    """The authoritative structured world-state (tenor + facts) — the canon a gate/
    trigger reads. None == today's behavior (no ending overlay). Facts dict is
    key-sorted for canonicality."""
    if not isinstance(ws, dict):
        return None
    return {
        "world_tenor": ws.get("world_tenor", "hopeful"),
        "facts": _sorted_str_keys(ws.get("facts")),
    }


def project_snapshot(snapshot: dict) -> dict:
    """Project a campaign snapshot dict to its canonical, regression-relevant state.

    The returned dict is the CONTRACT this extractor stabilizes: deterministically
    ordered (dict keys sorted), fiction-excluded, and built from defaults so an old
    snapshot projects identically to what its empty-default would. Engine field
    names are used throughout (per spec) without importing the engine model.
    """
    if not isinstance(snapshot, dict):
        raise TypeError(f"snapshot must be a dict, got {type(snapshot).__name__}")

    chars = snapshot.get("characters") or {}
    quests = snapshot.get("quests") or {}
    factions = snapshot.get("factions") or {}

    projection: dict[str, Any] = {
        # campaign-clock (engine-mutated; drives day_reached triggers)
        "day": snapshot.get("day", 1),
        "time_of_day": snapshot.get("time_of_day", "morning"),
        # the boolean world-flags gates/triggers read (invariant #3)
        "flags": _sorted_str_keys(snapshot.get("flags")),
        # replayability outcomes + the disengagement disposition the combat gate reads
        "quest_outcomes": _sorted_str_keys(snapshot.get("quest_outcomes")),
        "last_combat_resolution": snapshot.get("last_combat_resolution", ""),
        # party/companion roster (order is meaningful — marching order / turn seating)
        "party": list(snapshot.get("party") or []),
        # the authoritative structured world-state (tenor + facts)
        "world_state": _project_world_state(snapshot.get("world_state")),
        # bestiary intel tier per creature type (monotonic engine gauge)
        "bestiary_intel": _sorted_str_keys(snapshot.get("bestiary_intel")),
        # progression / pacing dials the engine mutates
        "leveling_mode": snapshot.get("leveling_mode", "xp"),
        "pacing_mode": snapshot.get("pacing_mode", "adventure"),
        # id->state maps, each key-sorted then per-entry projected (fiction-excluded)
        "characters": {
            cid: _project_character(cid, chars[cid])
            for cid in sorted(chars.keys(), key=str)
        } if isinstance(chars, dict) else {},
        "quests": {
            qid: _project_quest(qid, quests[qid])
            for qid in sorted(quests.keys(), key=str)
        } if isinstance(quests, dict) else {},
        "factions": {
            fid: _project_faction(fid, factions[fid])
            for fid in sorted(factions.keys(), key=str)
        } if isinstance(factions, dict) else {},
    }

    # Final canonical pass: sort every dict key recursively so the projection is
    # byte-stable regardless of insertion order anywhere in the source snapshot.
    return _canonicalize(projection)


def golden_hash(snapshot: dict) -> str:
    """Stable sha256 hexdigest of a snapshot's canonical regression projection.

    Deterministic: ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` over
    the already-canonical projection removes whitespace + ordering variance, so the
    same world-state always yields the same 64-char digest and a changed
    regression value always yields a different one.
    """
    projection = project_snapshot(snapshot)
    blob = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only golden-state extractor for a WorldOS campaign snapshot.json",
    )
    parser.add_argument("snapshot", help="path to a campaign snapshot.json")
    parser.add_argument(
        "--hash-only",
        action="store_true",
        help="print only the sha256 golden hash (default: print the canonical projection JSON)",
    )
    args = parser.parse_args(argv)

    snapshot = load_snapshot(args.snapshot)
    if args.hash_only:
        print(golden_hash(snapshot))
    else:
        print(json.dumps(project_snapshot(snapshot), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
