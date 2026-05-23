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

    # --- arcs (optional multi-act campaign structure) ---------------------
    arcs = adv.get("arcs", [])
    if not isinstance(arcs, list):
        problems.append(f"'arcs' must be a list, got {type(arcs).__name__}")
    else:
        arc_ids: set[str] = set()
        for i, arc in enumerate(arcs):
            where = f"arc[{i}]"
            if not isinstance(arc, dict):
                problems.append(f"{where} must be an object, got {type(arc).__name__}")
                continue
            arc_id = arc.get("id")
            if not _is_nonempty_str(arc_id):
                problems.append(f"{where} is missing a non-empty 'id'")
            elif arc_id in arc_ids:
                problems.append(f"duplicate arc id {arc_id!r}")
            else:
                arc_ids.add(arc_id)
            label = arc_id if _is_nonempty_str(arc_id) else where
            if not _is_nonempty_str(arc.get("title")):
                problems.append(f"arc {label!r} is missing a non-empty 'title'")
            beats = arc.get("beats", [])
            if not isinstance(beats, list):
                problems.append(f"arc {label!r} 'beats' must be a list")
            else:
                for j, beat in enumerate(beats):
                    if not isinstance(beat, dict):
                        problems.append(f"arc {label!r} beat[{j}] must be an object")
                        continue
                    if not _is_nonempty_str(beat.get("title")):
                        problems.append(f"arc {label!r} beat[{j}] is missing a non-empty 'title'")
                    loc_ref = beat.get("location_id")
                    if loc_ref is not None and loc_ref not in location_ids:
                        problems.append(
                            f"arc {label!r} beat references unknown location_id {loc_ref!r}"
                        )

    # --- antagonist (optional hidden villain) -----------------------------
    ant = adv.get("antagonist")
    if ant is not None:
        if not isinstance(ant, dict):
            problems.append(f"'antagonist' must be an object, got {type(ant).__name__}")
        else:
            if not _is_nonempty_str(ant.get("name")):
                problems.append("antagonist is missing a non-empty 'name'")
            voice_id = ant.get("voice_id")
            if _is_nonempty_str(voice_id) and voice_id not in KNOWN_VOICE_IDS:
                problems.append(f"antagonist has unknown voice_id {voice_id!r}")

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


def _level_bounds(level_range: Sequence[int]) -> tuple[int, int]:
    lvls = list(level_range) or [1, 5]
    lo = max(1, min(int(lvls[0]), 20))
    hi = max(lo, min(int(lvls[-1]), 20))
    return lo, hi


def _level_bands(lo: int, hi: int, acts: int) -> list[list[int]]:
    """Split the level range into `acts` contiguous, escalating bands."""
    span = hi - lo
    bands = []
    for i in range(acts):
        a = lo + (span * i) // acts
        b = lo + (span * (i + 1)) // acts
        bands.append([a, max(a, b)])
    return bands


def generate_campaign(
    title: str,
    premise: str = "",
    num_acts: int = 3,
    level_range: Sequence[int] = (1, 5),
) -> dict:
    """Generate a schema-correct MULTI-ACT campaign skeleton (a real generator,
    not the empty scaffold). Produces a hidden antagonist, `num_acts` arcs each
    with a hook/challenge/climax beat trio across escalating level bands, and a
    home-base hub connected to one site per act. Passes validate_adventure() as-is;
    the campaign-author fills in original/CC-only prose, the NPC roster, the
    companion, and CR-balanced encounters per act, then re-validates before saving.

    The shape mirrors a classic hub-and-spokes campaign (home base -> escalating
    sites), the structure proven by published level-1->tier campaigns."""
    acts = max(1, min(int(num_acts), 10))
    lo, hi = _level_bounds(level_range)
    base = scaffold_adventure(title, premise, [lo, hi])
    base["antagonist"] = {
        "id": "antagonist",
        "name": "(unnamed villain — name me)",
        "goal": "(the escalating scheme that ties the acts together — fill in)",
        "hidden": True,
        "voice_id": "npc-elder",
    }
    site_ids = [f"loc-act{i + 1}-site" for i in range(acts)]
    base["locations"].append(
        {
            "id": "loc-hub",
            "name": "Home Base",
            "description": "(the safe hub the party returns to between acts — a town, keep, or camp)",
            "connections": list(site_ids),
        }
    )
    bands = _level_bands(lo, hi, acts)
    arcs = []
    for i in range(acts):
        site_id = site_ids[i]
        base["locations"].append(
            {
                "id": site_id,
                "name": f"Act {i + 1} Site",
                "description": f"(the principal location of act {i + 1} — fill in)",
                "connections": ["loc-hub"],
            }
        )
        arcs.append(
            {
                "id": f"arc-{i + 1}",
                "title": f"Act {i + 1}",
                "level_range": bands[i],
                "beats": [
                    {"id": f"act{i+1}-hook", "title": "Hook", "location_id": "loc-hub",
                     "summary": "(what draws the party into this act)"},
                    {"id": f"act{i+1}-challenge", "title": "Challenge", "location_id": site_id,
                     "summary": "(the central obstacle / dungeon of the act)"},
                    {"id": f"act{i+1}-climax", "title": "Climax", "location_id": site_id,
                     "summary": "(the act's payoff; the antagonist's hand shows more each act)"},
                ],
            }
        )
    base["arcs"] = arcs
    return base
