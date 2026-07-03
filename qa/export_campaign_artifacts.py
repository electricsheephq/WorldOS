#!/usr/bin/env python3
"""export_campaign_artifacts.py — export a campaign's quests/npcs/locations/encounters as
structured artifact JSONs (HV2, Act II harvest loop — docs/roadmap/PRODUCT-ROADMAP.md §4c).

Sibling of qa/export_scene_grid.py: the SAME read-only-snapshot extractor pattern (import
servers/engine/models.py pydantic types read-only, never mutate, never save). Reuses
qa/distill.py's transcript reader (`distill.distill`) to pull NPC dialogue snippets and
combat start/end pairs out of a played campaign's stream-json transcript.

  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python qa/export_campaign_artifacts.py \\
      <campaign_id> [--out-dir qa/artifacts_out] [--transcript qa/transcripts/<run>.jsonl] \\
      [--run-id <run_id>] [--extracted-at <iso8601>]

Output layout: <out-dir>/<campaign_id>/{quests,npcs,locations,encounters}/*.json — one file per
artifact, each matching the common envelope in data/library/artifact_schema.json:
    {artifact_id, class, world, provenance{campaign_id, run_id, sha, extracted_at}, payload, scores: null}

Engine = SOLE WRITER: this script is read-only on engine state (snapshot.json is loaded via
server._require / store.load_campaign, never saved) and read-only on transcripts. It never
writes under play-state/qa/state — only under --out-dir (default qa/artifacts_out, gitignored).

`--extracted-at` is a caller-supplied string, NEVER derived from wall-clock time inside this
script, so re-running with the same argument is byte-for-byte reproducible (test determinism).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_MAX_DIALOGUE_SNIPPETS = 5


# ── engine + distill imports (read-only) ─────────────────────────────────────────────────────
def _import_engine():
    sys.path.insert(0, str(_ROOT / "servers" / "engine"))
    import server  # noqa: PLC0415
    import scene_grid as sg  # noqa: PLC0415

    return server, sg


def _import_distill():
    sys.path.insert(0, str(_ROOT / "qa"))
    import distill  # noqa: PLC0415

    return distill


# ── provenance / ids ──────────────────────────────────────────────────────────────────────────
def _derive_run_id(campaign_id: str, explicit_run_id: Optional[str], state_dir: Optional[str]) -> Optional[str]:
    """The qa harness convention (qa/run_duo.sh): STATE_DIR="qa/state/$RUN", so the state
    directory's basename IS the run_id. Best-effort: an explicit --run-id always wins; else,
    when WORLDOS_STATE_DIR points inside qa/state/<run_id>/..., recover <run_id> from the path.
    Returns None when neither is available (e.g. a hand-run campaign outside the harness) —
    never guessed from the campaign_id itself."""
    if explicit_run_id:
        return explicit_run_id
    if not state_dir:
        return None
    parts = Path(state_dir).resolve().parts
    for i, p in enumerate(parts):
        if p == "state" and i > 0 and parts[i - 1] == "qa" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def _make_provenance(campaign_id: str, run_id: Optional[str], sha: Optional[str], extracted_at: str) -> dict:
    return {"campaign_id": campaign_id, "run_id": run_id, "sha": sha or None, "extracted_at": extracted_at}


def _envelope(artifact_id: str, cls: str, world: str, provenance: dict, payload: dict) -> dict:
    return {
        "artifact_id": artifact_id,
        "class": cls,
        "world": world,
        "provenance": provenance,
        "payload": payload,
        "scores": None,
    }


# ── transcript helpers ───────────────────────────────────────────────────────────────────────
def _load_transcript_lines(transcript_path: Optional[Path]) -> list[str]:
    if transcript_path is None or not transcript_path.exists():
        return []
    return [ln for ln in transcript_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _dm_player_text_blocks(lines: list[str]) -> list[str]:
    """Every assistant text block (DM/player narration) in the transcript, in order. Reuses
    distill's own event-shape tolerance (a line that fails to parse is skipped, not raised)."""
    distill = _import_distill()
    out: list[str] = []
    for raw in lines:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        for b in distill._content_blocks(ev.get("message", {})):
            if b.get("type") == "text":
                txt = b.get("text", "").strip()
                if txt:
                    out.append(txt)
    return out


def _npc_dialogue_snippets(name: str, text_blocks: list[str], limit: int = _MAX_DIALOGUE_SNIPPETS) -> list[str]:
    """Up to `limit` narration snippets that mention this NPC by name — a cheap, transcript-
    reading proxy for "moments this NPC appeared in the story" (no NLP, no engine dependency;
    a plain case-insensitive whole-word-ish substring match). First name only (e.g. "Jaheira"
    out of "Jaheira" or "Minsc and Boo" -> "Minsc") so a two-word canon name still matches."""
    if not name:
        return []
    first = name.split()[0]
    pat = re.compile(re.escape(first), re.IGNORECASE)
    out: list[str] = []
    for blk in text_blocks:
        if pat.search(blk):
            out.append(blk if len(blk) <= 400 else blk[:399] + "…")
            if len(out) >= limit:
                break
    return out


def _combat_encounters_from_transcript(lines: list[str], characters: dict) -> list[dict]:
    """Pairs each start_combat tool call with its matching end_combat (composition from
    start_combat's combatant_ids resolved to character names; outcome from end_combat's
    resolution text, which is the same string the engine stamps onto
    Campaign.last_combat_resolution). A start_combat with no later end_combat in this
    transcript yields outcome="" (combat still open / transcript truncated mid-fight) rather
    than being dropped — every started encounter is accounted for."""
    encounters: list[dict] = []
    pending: Optional[list[str]] = None
    idx = 0
    for raw in lines:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name = b.get("name", "")
            args = b.get("input", {}) or {}
            if name.endswith("start_combat"):
                ids = args.get("combatant_ids") or []
                pending = [characters.get(i, {}).get("name", i) for i in ids]
            elif name.endswith("end_combat") and pending is not None:
                idx += 1
                encounters.append(
                    {
                        "id": f"encounter-{idx}",
                        "composition": [{"name": n} for n in pending],
                        "outcome": args.get("resolution", ""),
                    }
                )
                pending = None
    if pending is not None:
        idx += 1
        encounters.append({"id": f"encounter-{idx}", "composition": [{"name": n} for n in pending], "outcome": ""})
    return encounters


# ── per-class extraction ─────────────────────────────────────────────────────────────────────
def _quest_consequences(quest_id: str, quest_title: str, consequences: list) -> list[dict]:
    """Best-effort linkage: the engine's Consequence has no direct quest_id FK (it is keyed by
    world-sim thread_id, not per-quest — see Campaign.consequences / thread model), so a
    consequence is attributed to a quest only when its free-text note/text mentions the quest's
    id or title. Conservative by design: no false-positive FK is invented; an unmatched
    consequence simply isn't attached to any quest."""
    out = []
    needle_id = quest_id.lower()
    needle_title = (quest_title or "").strip().lower()
    for c in consequences:
        text = f"{c.get('note', '')} {c.get('text', '')}".lower()
        if (needle_id and needle_id in text) or (needle_title and needle_title in text):
            out.append({"id": c.get("id", ""), "trigger_day": c.get("trigger_day"), "text": c.get("text", "")})
    return out


def extract_quests(campaign: dict, provenance_base, world: str) -> list[dict]:
    artifacts = []
    quests = campaign.get("quests") or {}
    consequences = campaign.get("consequences") or []
    for qid, q in quests.items():
        payload = {
            "id": q.get("id", qid),
            "name": q.get("title", ""),
            "objectives": q.get("objectives", []),
            "completed_objectives": q.get("completed_objectives", []),
            "resolution_status": q.get("status", ""),
            "evolves_to": q.get("evolves_to", ""),
            "consequences": _quest_consequences(qid, q.get("title", ""), consequences),
        }
        artifacts.append(
            _envelope(f"quest:{campaign['id']}:{qid}", "quest", world, provenance_base(), payload)
        )
    return artifacts


def extract_npcs(campaign: dict, provenance_base, world: str, text_blocks: list[str]) -> list[dict]:
    artifacts = []
    characters = campaign.get("characters") or {}
    for cid, c in characters.items():
        if c.get("kind") != "npc":
            continue
        log = c.get("approval_log") or []
        if log:
            first = log[0]
            start_val = first.get("new_value", 0) - first.get("delta", 0)
        else:
            start_val = c.get("attitude_value", 0)
        end_val = c.get("attitude_value", 0)
        personality = {
            k: c.get(k, "")
            for k in ("personality", "appearance", "mannerisms", "backstory")
            if c.get(k)
        }
        payload = {
            "id": c.get("id", cid),
            "name": c.get("name", ""),
            "voice_id": c.get("voice_id", ""),
            "personality": personality,
            "attitude_arc": {"start": start_val, "end": end_val},
            "final_status": "dead" if c.get("dead") else ("stable" if c.get("stable") else "active"),
            "dialogue_snippets": _npc_dialogue_snippets(c.get("name", ""), text_blocks),
        }
        artifacts.append(_envelope(f"npc:{campaign['id']}:{cid}", "npc", world, provenance_base(), payload))
    return artifacts


def extract_locations(campaign: dict, provenance_base, world: str, sg_module, campaign_id: str) -> list[dict]:
    artifacts = []
    locations = campaign.get("locations") or {}
    for lid, loc in locations.items():
        grid_payload = None
        grid = loc.get("scene_grid")
        if grid is not None:
            grid_payload = _scene_grid_payload(grid, loc)
        payload = {
            "id": loc.get("id", lid),
            "name": loc.get("name", ""),
            "description": loc.get("description", ""),
            "scene_grid": grid_payload,
            "visited": loc.get("visited", False),
        }
        artifacts.append(
            _envelope(f"location:{campaign['id']}:{lid}", "location", world, provenance_base(), payload)
        )
    return artifacts


def _scene_grid_payload(grid: dict, loc: dict) -> dict:
    """Reuses export_scene_grid's field mapping directly on the raw dict snapshot (NOT a
    fork/re-derivation — same keys, same walls/props/impassable projection) so a location's
    scene_grid artifact is exactly what qa/export_scene_grid.py would emit for it."""
    cols = grid["grid"]["cols"]
    rows = grid["grid"]["rows"]
    walls = [
        [cell["c"], cell["r"]]
        for cell in grid.get("cells", [])
        if cell.get("type") == "wall" or not cell.get("walkable", True)
    ]
    props = [{"kind": p.get("kind", "prop"), "cells": [list(pair) for pair in p.get("cells", [])]} for p in grid.get("props", [])]
    return {
        "cols": cols,
        "rows": rows,
        "cell_default_walkable": bool(grid.get("cell_default", {}).get("walkable", True)),
        "walls": walls,
        "props": props,
        "door_cells": [list(pair) for pair in (grid.get("door_cells") or [])],
        "protected_lane_cells": [list(pair) for pair in (grid.get("protected_lane_cells") or [])],
    }


def extract_encounters(campaign: dict, provenance_base, world: str, transcript_lines: list[str]) -> list[dict]:
    characters = campaign.get("characters") or {}
    raw_encounters = _combat_encounters_from_transcript(transcript_lines, characters)
    artifacts = []
    for enc in raw_encounters:
        artifacts.append(
            _envelope(f"encounter:{campaign['id']}:{enc['id']}", "encounter", world, provenance_base(), enc)
        )
    return artifacts


# ── orchestration ────────────────────────────────────────────────────────────────────────────
def build_artifacts(
    campaign_dict: dict,
    *,
    run_id: Optional[str],
    extracted_at: str,
    transcript_lines: list[str],
    sg_module,
) -> dict[str, list[dict]]:
    """Pure function: campaign snapshot (as a dict) + transcript lines -> {class: [artifact, ...]}.
    No I/O — the caller loads the snapshot / transcript and writes the output."""
    campaign_id = campaign_dict["id"]
    world = campaign_dict.get("world_id", "")
    sha = campaign_dict.get("engine_sha") or None

    def provenance_base():
        return _make_provenance(campaign_id, run_id, sha, extracted_at)

    text_blocks = _dm_player_text_blocks(transcript_lines)
    return {
        "quests": extract_quests(campaign_dict, provenance_base, world),
        "npcs": extract_npcs(campaign_dict, provenance_base, world, text_blocks),
        "locations": extract_locations(campaign_dict, provenance_base, world, sg_module, campaign_id),
        "encounters": extract_encounters(campaign_dict, provenance_base, world, transcript_lines),
    }


def write_artifacts(out_dir: Path, campaign_id: str, artifacts_by_class: dict[str, list[dict]]) -> dict[str, int]:
    counts = {}
    campaign_out = out_dir / campaign_id
    for cls, artifacts in artifacts_by_class.items():
        cls_dir = campaign_out / cls
        cls_dir.mkdir(parents=True, exist_ok=True)
        for art in artifacts:
            # a stable, filesystem-safe filename derived from the artifact_id
            fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", art["artifact_id"]) + ".json"
            (cls_dir / fname).write_text(json.dumps(art, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        counts[cls] = len(artifacts)
    return counts


def _resolve_transcript_path(campaign_id: str, run_id: Optional[str], explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        return Path(explicit)
    if run_id:
        candidate = _ROOT / "qa" / "transcripts" / f"{run_id}.jsonl"
        if candidate.exists():
            return candidate
    return None


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("campaign_id")
    ap.add_argument("--out-dir", default=str(_ROOT / "qa" / "artifacts_out"))
    ap.add_argument("--transcript", default=None, help="explicit transcript .jsonl path (default: qa/transcripts/<run_id>.jsonl)")
    ap.add_argument("--run-id", default=None, help="override run_id derivation (default: inferred from WORLDOS_STATE_DIR)")
    ap.add_argument("--extracted-at", required=True, help="caller-supplied timestamp string; never wall-clock-derived here")
    args = ap.parse_args(argv)

    server, sg = _import_engine()
    campaign_obj = server._require(args.campaign_id)  # read-only: store.load_campaign, no save
    campaign_dict = json.loads(campaign_obj.model_dump_json())

    state_dir = os.environ.get("WORLDOS_STATE_DIR")
    run_id = _derive_run_id(args.campaign_id, args.run_id, state_dir)
    transcript_path = _resolve_transcript_path(args.campaign_id, run_id, args.transcript)
    transcript_lines = _load_transcript_lines(transcript_path)

    artifacts_by_class = build_artifacts(
        campaign_dict, run_id=run_id, extracted_at=args.extracted_at, transcript_lines=transcript_lines, sg_module=sg
    )
    counts = write_artifacts(Path(args.out_dir), args.campaign_id, artifacts_by_class)

    total = sum(counts.values())
    print(
        f"[export_campaign_artifacts] {args.campaign_id} (run_id={run_id!r}, transcript={transcript_path}): "
        f"{counts} ({total} artifacts) -> {args.out_dir}/{args.campaign_id}/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
