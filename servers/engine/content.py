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

from models import Campaign, Character, Faction, Location, Quest


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

    # Persist the authored scenes verbatim so the DM can read them at play time via
    # get_scene (read_aloud prose, dm_notes staging beats, check DCs). Without this
    # the rich per-scene authoring is dropped at seed and the DM plays blind.
    scenes = adv.get("scenes", [])
    if isinstance(scenes, list):
        c.scenes = [s for s in scenes if isinstance(s, dict)]

    first_loc = None
    for loc in _as_list(adv, "locations"):
        location = Location(
            name=loc.get("name", "?"),
            description=loc.get("description", ""),
            connections=loc.get("connections", []),
            hex=loc.get("hex"),  # optional axial coords (presentation only)
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
    # Render as a hex map if the adventure declares it or any location has coords.
    c.map_kind = adv.get("map_kind") or (
        "hex" if any(l.hex for l in c.locations.values()) else "none"
    )

    for npc in _as_list(adv, "npcs"):
        data = {
            "name": npc.get("name", "NPC"),
            "kind": "npc",
            "voice_id": npc.get("voice_id", "npc-male-1"),
            "personality": npc.get("personality", ""),
            "attitude": npc.get("attitude", ""),
        }
        # Optional combat stats: a fightable NPC (a villain, a guard) is seeded
        # battle-ready so the DM uses THIS record in combat rather than spawning a
        # duplicate monster — which is what left two records of the same character.
        for k in (
            "max_hp", "armor_class", "hit_dice", "proficiency_bonus", "abilities",
            "damage_resistances", "damage_immunities", "damage_vulnerabilities",
            "condition_immunities",
        ):
            if k in npc:
                data[k] = npc[k]
        ch = Character(**data)
        if "max_hp" in npc:
            ch.current_hp = ch.max_hp  # a stat-blocked NPC starts at full health
        if npc.get("id"):
            if npc["id"] in c.characters:
                raise ValueError(f"duplicate npc id {npc['id']!r} in adventure")
            ch.id = npc["id"]
        c.characters[ch.id] = ch

    # Companions are full party members (their own sheet + voice), seeded into
    # the party so the player starts the adventure WITH a companion at their side.
    # The companion dict mirrors Character's fields (Pydantic coerces the nested
    # abilities / classes / spell_slots); unknown keys are rejected.
    for comp in _as_list(adv, "companions"):
        data = dict(comp)
        comp_id = data.pop("id", None)
        data["kind"] = "companion"
        data.setdefault("voice_id", "companion-default")
        ch = Character(**data)
        if comp_id:
            if comp_id in c.characters:
                raise ValueError(f"duplicate character id {comp_id!r} in adventure")
            ch.id = comp_id
        ch.current_hp = ch.max_hp  # a fresh companion joins at full health
        if not ch.hit_dice_remaining:
            ch.hit_dice_remaining = ch.total_level
        c.characters[ch.id] = ch
        c.party.append(ch.id)

    for fac in _as_list(adv, "factions"):
        faction = Faction(
            name=fac.get("name", "Faction"),
            description=fac.get("description", ""),
            reputation=int(fac.get("reputation", 0)),
        )
        if fac.get("id"):
            faction.id = fac["id"]
        c.factions[faction.id] = faction

    if adv.get("hook"):
        quest = Quest(title=adv.get("title", "Adventure"), description=adv["hook"])
        c.quests[quest.id] = quest

    return c


def load_world_data(world_id: str) -> dict:
    """Load a world-seed bible: content/worlds/<id>/world.json, falling back to the
    gitignored content/worlds/_private/<id>/ for personal/internal seeds (e.g. a
    Forgotten-Realms/post-BG3 world the owner uses privately). Same loader either way."""
    base = _content_dir() / "worlds"
    path = base / world_id / "world.json"
    if not path.exists():
        private = base / "_private" / world_id / "world.json"
        if not private.exists():
            raise ValueError(f"no world named {world_id!r} (looked at {path} and {private})")
        path = private
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"world {world_id!r} has malformed JSON: {exc}") from exc


def seed_world(world: dict, start_at: str = "") -> Campaign:
    """Seed a Campaign from a WORLD bible (a persistent setting the DM generates
    *within*, not a fixed plot). Unlike an adventure, a world ships its regions,
    factions, a roster of pullable NPCs, and its history/standing-threads as `lore`
    — which the ledger indexes so `recall` keeps the generated story consistent. The
    DM then drops the party at a starting region and generates + persists the actual
    adventure as the player explores."""
    if not isinstance(world, dict):
        raise ValueError("world data must be a JSON object")
    c = Campaign(title=world.get("name", "Untitled World"), summary=world.get("premise", ""))

    first_loc = None
    for reg in _as_list(world, "regions"):
        location = Location(
            name=reg.get("name", "?"),
            description=reg.get("description", ""),
            connections=reg.get("connections", []),
            notes=" ".join(reg.get("tags", [])),
            hex=reg.get("hex"),
        )
        if reg.get("id"):
            if reg["id"] in c.locations:
                raise ValueError(f"duplicate region id {reg['id']!r} in world")
            location.id = reg["id"]
        c.locations[location.id] = location
        if first_loc is None:
            first_loc = location.id

    # Drop the party at the requested start, else the world's first starting_option,
    # else the first region.
    starts = [s.get("location_id") for s in _as_list(world, "starting_options") if s.get("location_id")]
    start_id = start_at or (starts[0] if starts else first_loc)
    if start_id and start_id not in c.locations:
        raise ValueError(f"start location {start_id!r} is not a region of this world")
    c.current_location_id = start_id
    if start_id:
        c.locations[start_id].visited = True
    c.map_kind = world.get("map_kind") or ("hex" if any(l.hex for l in c.locations.values()) else "none")

    for fac in _as_list(world, "factions"):
        faction = Faction(
            name=fac.get("name", "Faction"),
            description=fac.get("description", ""),
            reputation=int(fac.get("reputation", 0)),
        )
        if fac.get("id"):
            faction.id = fac["id"]
        c.factions[faction.id] = faction

    # Roster NPCs exist in state (recallable, voiced) but are not party members — the
    # DM pulls them in or invents freely. Each NPC's hook is stored as a memory fact.
    for npc in _as_list(world, "npc_roster"):
        ch = Character(
            name=npc.get("name", "NPC"),
            kind="npc",
            voice_id=npc.get("voice_id", "npc-male-1"),
            personality=npc.get("personality", ""),
            attitude=npc.get("role", ""),
        )
        if npc.get("id"):
            if npc["id"] in c.characters:
                raise ValueError(f"duplicate npc id {npc['id']!r} in world")
            ch.id = npc["id"]
        if npc.get("hook"):
            ch.memory.append(npc["hook"])
        c.characters[ch.id] = ch

    # World facts the DM recalls to stay consistent (indexed into the ledger as lore).
    c.lore = [str(x) for x in (_as_list(world, "history") + _as_list(world, "standing_threads")) if str(x).strip()]

    return c
