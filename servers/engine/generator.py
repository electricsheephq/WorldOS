"""Author-side adventure tooling: scaffold a new adventure and validate one.

This module is PURE (no MCP, no engine state, no I/O). It works on the plain
adventure dict — the same declarative shape that content.seed_campaign() turns
into live Campaign state (see content.py and content/campaigns/cellar-rats/
adventure.json for the canonical schema).

The campaign-author skill uses these two functions to start from a correct
skeleton, fill in original/CC-only prose built on SRD primitives, and catch
authoring mistakes (bad voice ids, dangling scene references, duplicate ids)
*before* the module is saved and seeded.
"""

from __future__ import annotations

from typing import Any, Sequence

# The six logical voices wired in content/voices/voice-map.json. NPCs must use
# one of these; the map re-points them to concrete TTS voices per backend.
KNOWN_VOICE_IDS: frozenset[str] = frozenset(
    {
        "narrator-dm",
        "companion-default",
        "npc-male-1",
        "npc-female-1",
        "npc-elder",
        "npc-rogue",
    }
)


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def validate_adventure(adv: dict) -> list[str]:
    """Check an adventure dict against the ClawDnD schema.

    Returns a list of human-readable problem strings; an empty list means the
    adventure is valid. Tolerant of optional fields (descriptions, hooks,
    rewards, scene prose, etc.) but precise about the structural invariants
    seed_campaign() and the play loop rely on:

      * top-level ``title`` is present and non-empty
      * ``locations`` is a list of objects, each with a non-empty ``id`` and
        ``name``; location ids are unique
      * ``npcs`` is a list of objects, each with a non-empty ``id`` and
        ``name`` and a ``voice_id`` drawn from the six known logical voices;
        npc ids are unique
      * every scene's ``location_id`` (when present) names an existing location
    """
    problems: list[str] = []

    if not isinstance(adv, dict):
        return [f"adventure must be a JSON object, got {type(adv).__name__}"]

    # --- top-level title ---------------------------------------------------
    if not _is_nonempty_str(adv.get("title")):
        problems.append("missing or empty top-level 'title'")

    # --- locations ---------------------------------------------------------
    location_ids: set[str] = set()
    locations = adv.get("locations", [])
    if not isinstance(locations, list):
        problems.append(
            f"'locations' must be a list, got {type(locations).__name__}"
        )
    else:
        for i, loc in enumerate(locations):
            where = f"location[{i}]"
            if not isinstance(loc, dict):
                problems.append(f"{where} must be an object, got {type(loc).__name__}")
                continue
            loc_id = loc.get("id")
            if not _is_nonempty_str(loc_id):
                problems.append(f"{where} is missing a non-empty 'id'")
            else:
                if loc_id in location_ids:
                    problems.append(f"duplicate location id {loc_id!r}")
                location_ids.add(loc_id)
            if not _is_nonempty_str(loc.get("name")):
                label = loc_id if _is_nonempty_str(loc_id) else where
                problems.append(f"location {label!r} is missing a non-empty 'name'")

        # connections (when present) must reference existing location ids
        for loc in locations:
            if isinstance(loc, dict):
                for conn in loc.get("connections", []) or []:
                    if conn not in location_ids:
                        problems.append(
                            f"location {loc.get('id', '?')!r} connects to unknown location id {conn!r}"
                        )

    # --- npcs --------------------------------------------------------------
    npc_ids: set[str] = set()
    npcs = adv.get("npcs", [])
    if not isinstance(npcs, list):
        problems.append(f"'npcs' must be a list, got {type(npcs).__name__}")
    else:
        for i, npc in enumerate(npcs):
            where = f"npc[{i}]"
            if not isinstance(npc, dict):
                problems.append(f"{where} must be an object, got {type(npc).__name__}")
                continue
            npc_id = npc.get("id")
            if not _is_nonempty_str(npc_id):
                problems.append(f"{where} is missing a non-empty 'id'")
            else:
                if npc_id in npc_ids:
                    problems.append(f"duplicate npc id {npc_id!r}")
                npc_ids.add(npc_id)
            label = npc_id if _is_nonempty_str(npc_id) else where
            if not _is_nonempty_str(npc.get("name")):
                problems.append(f"npc {label!r} is missing a non-empty 'name'")
            voice_id = npc.get("voice_id")
            if not _is_nonempty_str(voice_id):
                problems.append(f"npc {label!r} is missing a 'voice_id'")
            elif voice_id not in KNOWN_VOICE_IDS:
                known = ", ".join(sorted(KNOWN_VOICE_IDS))
                problems.append(
                    f"npc {label!r} has unknown voice_id {voice_id!r} "
                    f"(must be one of: {known})"
                )

    # --- scenes -> location references ------------------------------------
    scenes = adv.get("scenes", [])
    if not isinstance(scenes, list):
        problems.append(f"'scenes' must be a list, got {type(scenes).__name__}")
    else:
        for i, scene in enumerate(scenes):
            where = f"scene[{i}]"
            if not isinstance(scene, dict):
                problems.append(f"{where} must be an object, got {type(scene).__name__}")
                continue
            loc_ref = scene.get("location_id")
            if loc_ref is not None and loc_ref not in location_ids:
                label = scene.get("id") if _is_nonempty_str(scene.get("id")) else where
                problems.append(
                    f"scene {label!r} references unknown location_id {loc_ref!r}"
                )

    return problems


def scaffold_adventure(
    title: str,
    premise: str = "",
    level_range: Sequence[int] = (1, 2),
) -> dict:
    """Return a schema-correct skeleton adventure dict.

    The result passes validate_adventure() as-is (empty location/npc/scene
    lists are valid). The DM fills in original/CC-only prose built on SRD
    primitives — locations, an NPC roster (each assigned one of the known
    logical voice ids), scenes, and encounters — then re-validates before
    saving the module under content/campaigns/<id>/.
    """
    lvl = list(level_range)

    return {
        "title": title,
        "ruleset": "SRD 5.2",
        "level_range": lvl,
        "premise": premise,
        "hook": "",
        "themes": [],
        # The six logical voices available to assign (see voice-map.json).
        # Re-point a backend by editing the map, not the adventure.
        "voices": {
            "narrator-dm": "The Dungeon Master's narration voice for read-aloud text.",
            "companion-default": "The party's AI companion adventurer.",
            "npc-male-1": "A generic male NPC voice.",
            "npc-female-1": "A generic female NPC voice.",
            "npc-elder": "An older NPC voice.",
            "npc-rogue": "A sly / roguish NPC voice.",
        },
        "locations": [],
        "npcs": [],
        "scenes": [],
        "rewards": {
            "xp": 0,
            "currency": {"gp": 0},
            "loot": [],
            "story_rewards": [],
        },
        "conclusion": "",
    }
