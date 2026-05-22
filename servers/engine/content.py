"""Load a bundled adventure module into a fresh Campaign.

An adventure module is content/campaigns/<id>/adventure.json (authored, CC-BY).
seed_campaign() turns its declarative data (locations, NPCs, hook) into live
engine state: NPCs become voiced Characters, locations populate the map, and the
hook becomes the opening quest. The DM skill then reads the scenes and runs play.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from models import Campaign, Character, Location, Quest


def _content_dir() -> Path:
    raw = os.environ.get("CLAWDND_CONTENT_DIR")
    return Path(raw).expanduser() if raw else Path(__file__).resolve().parents[2] / "content"


def load_adventure_data(adventure_id: str) -> dict:
    path = _content_dir() / "campaigns" / adventure_id / "adventure.json"
    if not path.exists():
        raise ValueError(f"no adventure named {adventure_id!r} (looked at {path})")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"adventure {adventure_id!r} has malformed JSON: {exc}") from exc


def _as_list(adv: dict, key: str) -> list:
    val = adv.get(key, [])
    if not isinstance(val, list):
        raise ValueError(f"malformed adventure: '{key}' must be a list, got {type(val).__name__}")
    return val


def seed_campaign(adv: dict) -> Campaign:
    """Build a Campaign from an adventure dict. Tolerant of optional fields, but
    rejects malformed shapes and duplicate ids rather than silently dropping data."""
    if not isinstance(adv, dict):
        raise ValueError("adventure data must be a JSON object")
    c = Campaign(title=adv.get("title", "Untitled Adventure"), summary=adv.get("premise", ""))

    first_loc = None
    for loc in _as_list(adv, "locations"):
        location = Location(
            name=loc.get("name", "?"),
            description=loc.get("description", ""),
            connections=loc.get("connections", []),
        )
        if loc.get("id"):
            if loc["id"] in c.locations:
                raise ValueError(f"duplicate location id {loc['id']!r} in adventure")
            location.id = loc["id"]
        c.locations[location.id] = location
        if first_loc is None:
            first_loc = location.id
    c.current_location_id = first_loc
    if first_loc is not None:
        c.locations[first_loc].visited = True  # the party starts here

    for npc in _as_list(adv, "npcs"):
        ch = Character(
            name=npc.get("name", "NPC"),
            kind="npc",
            voice_id=npc.get("voice_id", "npc-male-1"),
            personality=npc.get("personality", ""),
            attitude=npc.get("attitude", ""),
        )
        if npc.get("id"):
            if npc["id"] in c.characters:
                raise ValueError(f"duplicate npc id {npc['id']!r} in adventure")
            ch.id = npc["id"]
        c.characters[ch.id] = ch

    if adv.get("hook"):
        quest = Quest(title=adv.get("title", "Adventure"), description=adv["hook"])
        c.quests[quest.id] = quest

    return c
