"""ClawDnD game-engine MCP server.

Authoritative D&D 5e game state — dice, character sheets, and campaign
persistence — exposed as MCP tools. Every tool reads the campaign from disk,
mutates it, and writes it back atomically (single-writer), so state survives
restarts and context compaction and is never held only in the conversation.

Combat, encounters, leveling, and spellcasting tools build on these foundations
in later epics; this server already owns dice, characters, and persistence.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

import bestiary
import combat
import companion
import consequences as consequences_mod
import content as content_mod
import dice as dice_mod
import encounter
import generator
import imagegen
import inventory
import ledger as ledger_mod
import lorebook
import npc as npc_mod
import recap
import rests
import spells
import srd_tables
import travel
import worldsim
from models import (
    SKILL_ABILITIES,
    Ability,
    AbilityScores,
    Campaign,
    Character,
    ClassLevel,
    Combat,
    Combatant,
    Condition,
    Decision,
    Faction,
    HouseRules,
    Location,
    Quest,
    SessionLogEntry,
    SpellSlotLevel,
)

_AB3_TO_FULL = {
    "str": "strength",
    "dex": "dexterity",
    "con": "constitution",
    "int": "intelligence",
    "wis": "wisdom",
    "cha": "charisma",
}
from store import append_log, campaign_lock, campaigns_for_world
from store import list_campaigns as _list_campaigns
from store import load_campaign, save_campaign

mcp = FastMCP("clawdnd-engine")


def _require(campaign_id: str) -> Campaign:
    c = load_campaign(campaign_id)
    if c is None:
        raise ValueError(f"no campaign with id {campaign_id!r}")
    return c


def _char(c: Campaign, character_id: str) -> Character:
    ch = c.characters.get(character_id)
    if ch is None:
        raise ValueError(f"no character {character_id!r} in campaign")
    return ch


def _combat_view(c: Campaign) -> dict:
    order = []
    for cb in c.combat.order:
        ch = c.characters.get(cb.character_id)
        order.append(
            {
                "character_id": cb.character_id,
                "name": ch.name if ch else "?",
                "initiative": cb.initiative,
            }
        )
    return {
        "active": c.combat.active,
        "round": c.combat.round,
        "turn_index": c.combat.turn_index,
        "current": c.combat.current_combatant_id,
        "order": order,
    }


def _deep_update(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def _safe_caster_type(name: str) -> str:
    try:
        return srd_tables.caster_type(name)
    except ValueError:
        return "none"


def _meets_prereq(ch: Character, class_name: str) -> bool:
    for option in srd_tables.multiclass_prereq(class_name):
        if all(getattr(ch.abilities, _AB3_TO_FULL[ab]) >= minv for ab, minv in option.items()):
            return True
    return False


def _recompute_spellcasting(ch: Character) -> None:
    """Recompute spell-slot maximums from class levels, preserving used slots.
    Single-class Warlock uses Pact Magic; multiclass Warlock merging is deferred."""
    class_levels = [(cl.name, cl.level) for cl in ch.classes]
    casters = [(n, l) for (n, l) in class_levels if _safe_caster_type(n) in ("full", "half", "third")]
    new_slots: dict[int, SpellSlotLevel] = {}
    if casters:
        for lvl, maximum in srd_tables.multiclass_slots(casters).items():
            prev = ch.spell_slots.get(lvl)
            used = min(prev.used, maximum) if prev else 0
            new_slots[lvl] = SpellSlotLevel(maximum=maximum, used=used)
    warlocks = [(n, l) for (n, l) in class_levels if _safe_caster_type(n) == "pact"]
    if warlocks and len(class_levels) == 1:
        pact = srd_tables.warlock_pact_slots(warlocks[0][1])
        if pact:
            new_slots[pact["level"]] = SpellSlotLevel(maximum=pact["slots"], used=0)
    ch.spell_slots = new_slots


def _casting_mod(ch: Character) -> int:
    """Casting-ability modifier from the character's first caster class. A
    character with classes but none that cast has no spellcasting (raises); a
    truly unclassed caster (NPC/monster) falls back to its best mental stat."""
    for cl in ch.classes:
        ability = srd_tables.casting_ability(cl.name)
        if ability:
            return ch.ability_modifier(Ability(ability))
    if ch.classes:
        raise ValueError(f"{ch.name} has no spellcasting class")
    return max(
        ch.ability_modifier(Ability.INT),
        ch.ability_modifier(Ability.WIS),
        ch.ability_modifier(Ability.CHA),
    )


@mcp.tool()
def ping() -> str:
    """Health check. Returns ok if the ClawDnD engine server is reachable."""
    return "clawdnd-engine: ok (v0.0.1)"


@mcp.tool()
def roll(
    expression: str,
    advantage: bool = False,
    disadvantage: bool = False,
    reason: str = "",
) -> dict:
    """Roll dice using D&D notation, e.g. '1d20+5', '2d6', '4d6kh3'.

    Use this for EVERY die roll — never narrate a number you did not roll here.
    Supports advantage/disadvantage on a single d20 (they cancel if both set) and
    keep-highest/lowest (khN / klN). Returns the total, the individual dice, a
    human-readable breakdown, and natural-20/natural-1 crit/fumble flags.
    """
    r = dice_mod.roll(expression, advantage=advantage, disadvantage=disadvantage)
    return {
        "expression": r.expression,
        "total": r.total,
        "rolls": r.rolls,
        "dropped": r.dropped,
        "modifier": r.modifier,
        "detail": r.detail,
        "natural": r.natural,
        "crit": r.crit,
        "fumble": r.fumble,
        "reason": reason,
    }


@mcp.tool()
def create_campaign(title: str, summary: str = "") -> dict:
    """Create a new campaign and persist it. Returns the new campaign id."""
    c = Campaign(title=title, summary=summary)
    save_campaign(c)
    return {"id": c.id, "title": c.title}


@mcp.tool()
def list_campaigns() -> list[dict]:
    """List all saved campaigns (id, title, last-updated time)."""
    return _list_campaigns()


@mcp.tool()
def start_adventure(adventure_id: str) -> dict:
    """Seed a NEW campaign from a bundled adventure module
    (content/campaigns/<adventure_id>/adventure.json): world summary, locations,
    NPCs as voiced Characters, and the opening quest. Returns the campaign id and
    a summary. The DM then reads the scenes (adventure.md) and runs play, creating
    the player + companion with create_character."""
    adv = content_mod.load_adventure_data(adventure_id)
    c = content_mod.seed_campaign(adv)
    save_campaign(c)
    loc = c.locations.get(c.current_location_id) if c.current_location_id else None
    return {
        "campaign_id": c.id,
        "title": c.title,
        "summary": c.summary,
        "level_range": adv.get("level_range"),
        "current_location": loc.name if loc else None,
        "npcs": [
            {"id": ch.id, "name": ch.name, "voice_id": ch.voice_id}
            for ch in c.characters.values()
            if ch.kind == "npc"
        ],
        "scene_count": len(adv.get("scenes", [])),
    }


@mcp.tool()
def list_worlds() -> dict:
    """List the available WORLD seeds you can drop into with `start_world` — each a
    persistent setting the DM generates *within* (returns id, name, premise, era, tone,
    lore_pages). Use for `/world-list` and to let the player pick a world to play."""
    return {"worlds": content_mod.list_worlds()}


@mcp.tool()
def start_world(world_id: str, start_at: str = "", resume: str = "") -> dict:
    """Seed a NEW campaign from a persistent WORLD bible
    (content/worlds/<world_id>/world.json) — a living setting you GENERATE WITHIN,
    not a fixed plot.

    Seeds the world's regions as a navigable map, its factions, a roster of pullable
    NPCs, and its history + standing-threads as `lore` (indexed into recall, so a
    generated story stays consistent with the canon and never forgets it). Drops the
    party at `start_at` (a region id) or the world's first starting option. Then YOU
    run a LIVING SANDBOX: generate the specific scene on arrival and PERSIST it
    (add_location / create_character / remember / add_quest) so the world grows and is
    carried across sessions.

    Pass `resume`=<campaign_id> to CONTINUE an existing campaign in this world rather
    than starting fresh — re-running start_world otherwise mints a NEW campaign and
    orphans the old living world. If campaigns already exist for this world, the result
    lists them under `existing_campaigns` with a `resume_hint`. Returns the world's
    premise, tone, DM guidance, threads, seeds, and the seeded regions/factions/roster."""
    world = content_mod.load_world_data(world_id)

    # Resume an existing campaign in this world instead of abandoning it.
    if resume:
        prior = load_campaign(resume)
        if prior is not None and prior.world_id == world_id:
            ploc = prior.locations.get(prior.current_location_id) if prior.current_location_id else None
            return {
                "campaign_id": prior.id,
                "world": prior.title,
                "resumed": True,
                "premise": prior.summary,
                "era": prior.era,
                "day": prior.day,
                "time_of_day": prior.time_of_day,
                "dm_guidance": world.get("dm_guidance", ""),
                "lore_corpus_pages": lorebook.page_count(prior.world_id),
                "current_location": {"id": ploc.id, "name": ploc.name} if ploc else None,
                "regions": [{"id": l.id, "name": l.name} for l in prior.locations.values()],
                "note": "Resumed an existing campaign. Call session_recap / get_state to re-ground, then start_session.",
            }
        # invalid/mismatched resume id -> fall through to a fresh start

    c = content_mod.seed_world(world, start_at=start_at)
    save_campaign(c)
    loc = c.locations.get(c.current_location_id) if c.current_location_id else None
    result = {
        "campaign_id": c.id,
        "world": c.title,
        "premise": c.summary,
        "era": c.era,
        "tone": world.get("tone", ""),
        "dm_guidance": world.get("dm_guidance", ""),
        "lore_corpus_pages": lorebook.page_count(c.world_id),
        "standing_threads": world.get("standing_threads", []),
        "story_seeds": world.get("story_seeds", []),
        "starting_at": {"id": loc.id, "name": loc.name} if loc else None,
        "starting_options": world.get("starting_options", []),
        "regions": [{"id": l.id, "name": l.name} for l in c.locations.values()],
        "factions": [{"id": f.id, "name": f.name} for f in c.factions.values()],
        "npc_roster": [
            {"id": ch.id, "name": ch.name, "role": ch.attitude, "voice_id": ch.voice_id}
            for ch in c.characters.values()
            if ch.kind == "npc"
        ],
        "lore_count": len(c.lore),
        "map_kind": c.map_kind,
    }
    others = [x for x in campaigns_for_world(world_id) if x["id"] != c.id]
    if others:
        result["existing_campaigns"] = others
        result["resume_hint"] = (
            "Other campaigns already exist in this world — to CONTINUE one instead of "
            "this fresh start, call start_world(world_id, resume=<campaign_id>)."
        )
    return result


@mcp.tool()
def get_state(campaign_id: str) -> dict:
    """Read current campaign state — call at the start of a beat to re-ground
    after any gap or compaction. Returns a summary (scene, party vitals, active
    quests, combat status). Use get_character for a full sheet.
    """
    c = _require(campaign_id)
    loc = c.locations.get(c.current_location_id) if c.current_location_id else None
    party = []
    for cid in c.party:
        ch = c.characters.get(cid)
        if ch is None:
            continue
        party.append(
            {
                "id": ch.id,
                "name": ch.name,
                "kind": ch.kind,
                "hp": f"{ch.current_hp}/{ch.max_hp}",
                "ac": ch.armor_class,
                "conditions": [x.value for x in ch.conditions],
                "voice_id": ch.voice_id,
            }
        )
    return {
        "id": c.id,
        "title": c.title,
        "ruleset": c.ruleset,
        "day": c.day,
        "time_of_day": c.time_of_day,
        "location": {"id": loc.id, "name": loc.name} if loc else None,
        "party": party,
        "active_quests": [
            {"id": q.id, "title": q.title}
            for q in c.quests.values()
            if q.status == "active"
        ],
        "in_combat": c.combat.active,
        "current_turn": c.combat.current_combatant_id,
        "npc_count": sum(1 for x in c.characters.values() if x.kind == "npc"),
    }


@mcp.tool()
def look_around(campaign_id: str) -> dict:
    """Describe the party's current location and the exits they can take.

    Read-only. Returns the current location (name, description, notes, visited)
    plus each reachable exit with whether it's been visited, and the in-world
    day / time-of-day. Use this to ground exploration before narrating or
    prompting the player to move.
    """
    c = _require(campaign_id)
    return travel.look_around(c)


@mcp.tool()
def get_scene(campaign_id: str, location_id: str = "") -> dict:
    """Read the AUTHORED scene guidance for a location — your beat sheet for running
    the adventure as written instead of improvising blind.

    Call this on ARRIVAL at a new location/beat. Returns each authored scene whose
    `location_id` matches (defaults to the party's current location): its
    `read_aloud` boxed text to set the scene, `dm_notes` (what to STAGE — which NPCs
    to put on screen, the emotional turn, the villain beat, the menace to show),
    any `checks` with their DCs, and the scene `name`/`summary`. The DM should voice
    the read_aloud in its own words and play the dm_notes beats — the heartbreak
    line, the antagonist's warmth, the felt threat — rather than leaving them buried.
    Returns an empty list if the adventure shipped no scene for this location (then
    improvise from get_state / look_around). Read-only.
    """
    c = _require(campaign_id)
    loc_id = location_id or c.current_location_id or ""
    scenes = [s for s in c.scenes if s.get("location_id") == loc_id] if loc_id else []
    return {
        "location_id": loc_id,
        "count": len(scenes),
        "scenes": scenes,
        "all_scene_location_ids": sorted({s.get("location_id", "") for s in c.scenes if s.get("location_id")}),
    }


@mcp.tool()
def lookup_lore(campaign_id: str, query: str, limit: int = 5) -> dict:
    """Look up established WORLD LORE on demand — the DM's wiki for a universe seed.

    When the party reaches a city/region or meets a figure tied to the setting, call
    this to pull the canon, then GROUND your generation in it (and invent the specifics
    on top — a city's lord can be noble one game and fallen the next, but the city is
    the city). Searches the world seed's lore corpus (content/worlds/<id>/lore/) and
    returns the most relevant pages as {title, excerpt, source}, ranked by relevance.

    Also returns `era` — the in-world date/chronology. RESPECT IT: don't bring on
    figures who are long dead or events that haven't happened yet. Returns empty hits
    if this campaign isn't from a world seed, or the world ships no lore corpus — then
    generate freely and use `recall` for the facts you've established this game."""
    c = _require(campaign_id)
    hits = lorebook.lookup_lore(c.world_id, query, max(1, limit)) if c.world_id else []
    return {
        "world_id": c.world_id,
        "era": c.era,
        "query": query,
        "hits": hits,
        "corpus_pages": lorebook.page_count(c.world_id) if c.world_id else 0,
    }


@mcp.tool()
def add_location(
    campaign_id: str,
    name: str,
    description: str = "",
    connections: Optional[list] = None,
    location_id: str = "",
    hex: Optional[list] = None,
    region: str = "",
    travel_times: Optional[dict] = None,
) -> dict:
    """Create — or update — a location in the world DURING live play. The key
    world-building primitive for generated / sandbox campaigns.

    It puts the place into ENGINE STATE so it can be traveled to (`travel_to`),
    re-grounded (`look_around`), recalled, and carried into future sessions — instead
    of living only in your narration (where it's lost and `look_around` returns null).
    `connections` are existing location ids, wired BIDIRECTIONALLY so the party can
    walk both ways. Pass `location_id` to fill in a generator placeholder or update an
    existing place; omit it to mint a new one. If the campaign had no current location,
    this becomes it. `hex` is optional axial [q, r] map coords (presentation only).
    `region` is the parent zone this nests in ("Lower City"); `travel_times` is an
    optional `{connected_location_id: walk_minutes}` map that `look_around` surfaces as
    fables-style walk-times to nearby places.
    """
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        conns = [str(x) for x in (connections or [])]
        coords = tuple(hex) if hex and len(hex) == 2 else None
        tt = {str(k): int(v) for k, v in (travel_times or {}).items()}
        warnings: list[str] = []
        existing = c.locations.get(location_id) if location_id else None
        if existing is not None:  # update / fill-in a placeholder
            if name:
                existing.name = name
            if description:
                existing.description = description
            if coords is not None:
                existing.hex = coords
            if region:
                existing.region = region
            if tt:
                existing.travel_times.update(tt)
            loc = existing
        else:
            # Advisory: a same-named location usually means the DM meant the existing place.
            dup = next((l for l in c.locations.values() if l.name.strip().lower() == name.strip().lower()), None)
            if dup is not None:
                warnings.append(
                    f"a location named {name!r} already exists ({dup.id!r}) — pass "
                    f"location_id={dup.id!r} to update it instead of creating a duplicate"
                )
            loc = Location(name=name, description=description, hex=coords, region=region, travel_times=tt)
            if location_id:
                loc.id = location_id  # honor a caller-chosen id (e.g. a generated skeleton's)
            c.locations[loc.id] = loc
        unresolved: list[str] = []
        for other_id in conns:  # wire bidirectional edges to existing locations only
            if other_id == loc.id:
                continue
            other = c.locations.get(other_id)
            if other is None:
                unresolved.append(other_id)
                continue
            if other_id not in loc.connections:
                loc.connections.append(other_id)
            if loc.id not in other.connections:
                other.connections.append(loc.id)
        if unresolved:
            warnings.append(f"unknown connection ids skipped (no such location): {unresolved}")
        if c.current_location_id is None:
            c.current_location_id = loc.id
        # Orphan guard: a non-current location with no edges can never be reached.
        if loc.id != c.current_location_id and not loc.connections:
            warnings.append(
                f"location {loc.id!r} has NO connections — it is unreachable; call add_location "
                f"again with connections=[an existing location id] to wire it into the map"
            )
        save_campaign(c)
    return {
        "id": loc.id,
        "name": loc.name,
        "connections": loc.connections,
        "is_current": c.current_location_id == loc.id,
        "location_count": len(c.locations),
        "warnings": warnings,
    }


@mcp.tool()
def travel_to(campaign_id: str, destination_id: str, advance_time: bool = False) -> dict:
    """Move the party to a connected location along the map graph.

    The destination must be reachable from the current location (listed in its
    connections); travel to an unconnected or unknown location is rejected with
    the reachable exits. Marks the destination visited. The clock advances a
    time-of-day phase only when advance_time=True — leave it False for short
    moves within a site (room to room) so a quick crawl doesn't burn a day; pass
    True for a long or overland journey. Returns ``{from, to, to_name,
    first_visit, day, time_of_day, reachable}`` so the DM knows whether to read
    first-visit boxed text.
    """
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        result = travel.travel_to(c, destination_id, advance_time=advance_time)
        if advance_time:  # time passed → ONE standing thread may stir (one discrete beat, not a list)
            beats = worldsim.tick(c, max_beats=1)
            if beats:
                result["world_beats"] = [b.text for b in beats]
        save_campaign(c)
        return result


def _apply_srd_class_defaults(ch, class_name: str, level: int, set_base_ac: bool) -> None:
    """Fill SRD class defaults onto a character in place: saving-throw proficiencies,
    hit dice, level-1 HP (max die + CON), proficiency bonus, class base AC (when
    requested), and class features through `level`. No-op on an unknown class. Shared
    by create_character and recruit_companion so a live-made hero gets a real sheet
    instead of forcing the DM to invent modifiers."""
    try:
        cname = class_name.lower()
        ch.saving_throw_proficiencies = [Ability(s) for s in srd_tables.class_saves(cname)]
        die = srd_tables.hit_die(cname)
        ch.hit_dice = f"{level}d{die}"
        ch.hit_dice_remaining = level
        if ch.max_hp <= 1:  # HP not explicitly provided -> compute for the FULL level
            # SRD fixed-HP: max die + CON at L1, then average (die//2+1) + CON per level.
            con = ch.abilities.modifier(Ability.CON)
            per_level_after_first = (die // 2 + 1) + con
            ch.max_hp = max(1, die + con + (level - 1) * per_level_after_first)
            ch.current_hp = ch.max_hp
        ch.proficiency_bonus = srd_tables.proficiency_bonus(level)
        if set_base_ac:
            ch.armor_class = srd_tables.class_base_ac(cname)
        for f in srd_tables.features_through(cname, level):
            if f["name"] not in ch.features:
                ch.features.append(f["name"])
            if "extra_attacks" in f:
                ch.extra_attacks = max(ch.extra_attacks, int(f["extra_attacks"]))
            if f.get("sneak_attack_dice"):
                ch.sneak_attack_dice = f["sneak_attack_dice"]
        # Grant the class's default skill proficiencies if none were chosen, so skill
        # checks (incl. social_check) include the proficiency bonus instead of the DM
        # inventing a modifier on an empty sheet. The caller can pass an explicit
        # `skills` list to choose; this only fills an otherwise-empty list.
        if not ch.skill_proficiencies:
            sk = srd_tables.class_skills(cname)
            ch.skill_proficiencies = list(sk.get("from", []))[: int(sk.get("count", 0))]
        _recompute_spellcasting(ch)
    except ValueError:
        pass  # unknown class -> keep the explicit values


@mcp.tool()
def create_character(
    campaign_id: str,
    name: str,
    kind: str = "player",
    race: str = "",
    class_name: str = "",
    level: int = 1,
    max_hp: int = 1,
    armor_class: int = 10,
    voice_id: str = "narrator-dm",
    abilities: Optional[dict] = None,
    background: str = "",
    subclass: Optional[str] = None,
    apply_srd_defaults: bool = False,
    skills: Optional[list] = None,
    location_id: str = "",
    add_to_party: bool = True,
) -> dict:
    """Create a character (player, companion, npc, or monster) and persist it.

    `abilities` is an optional dict like {"strength": 15, "dexterity": 14, ...}.
    If `apply_srd_defaults=True` and `class_name` is a known SRD class, the engine
    sets saving-throw proficiencies, proficiency bonus, hit dice, level-1 HP
    (max hit die + CON), spell slots, and a class-appropriate AC (only when
    armor_class is left at the unarmored default of 10) from that class; otherwise
    the explicit max_hp/armor_class are used as-is. Returns the new character id.
    """
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if kind == "companion":
            # Adventures seed their companion at start_adventure; a second
            # create_character with the same name produces a duplicate party
            # member (blank personality, wrong id). Block it — the DM should
            # get_state to find the seeded companion, not recreate it.
            dup = next(
                (e for e in c.characters.values()
                 if e.kind == "companion" and e.name.strip().lower() == name.strip().lower()),
                None,
            )
            if dup is not None:
                raise ValueError(
                    f"Companion {name!r} already exists as {dup.id!r}. Adventures seed "
                    f"their companion at start_adventure — call get_state to find it and "
                    f"reference that id; do not recreate it."
                )
        scores = AbilityScores(**(abilities or {}))
        ch = Character(
            name=name,
            kind=kind,  # type: ignore[arg-type]
            race=race,
            background=background,
            voice_id=voice_id,
            classes=[ClassLevel(name=class_name.capitalize(), level=level, subclass=subclass)]
            if class_name
            else [],
            abilities=scores,
            max_hp=max_hp,
            current_hp=max_hp,
            armor_class=armor_class,
            initiative_bonus=scores.modifier(Ability.DEX),
        )
        if skills:  # explicit skill choices win over the class default-fill
            ch.skill_proficiencies = [s.lower() for s in skills if s.lower() in SKILL_ABILITIES]
        if apply_srd_defaults and class_name:
            _apply_srd_class_defaults(ch, class_name, level, set_base_ac=(armor_class == 10))
        # Anchor NPCs/monsters to where they're introduced so "who's in the scene" is
        # the current location's cast — not the whole seeded world roster. Explicit
        # location_id wins; otherwise default to the party's current location.
        if kind in ("npc", "monster"):
            ch.location_id = location_id or c.current_location_id
        c.characters[ch.id] = ch
        if add_to_party and kind in ("player", "companion"):
            c.party.append(ch.id)
        save_campaign(c)
    return {"id": ch.id, "name": ch.name, "kind": ch.kind}


@mcp.tool()
def recruit_companion(
    campaign_id: str,
    npc_id: str,
    class_name: str = "",
    level: int = 1,
    abilities: Optional[dict] = None,
    subclass: Optional[str] = None,
    max_hp: int = 0,
    armor_class: int = 0,
    apply_srd_defaults: bool = True,
    skills: Optional[list] = None,
) -> dict:
    """Promote an EXISTING roster NPC into the party's companion — the clean way to
    bring a world-seed candidate (e.g. "Minsc is ready", "Bram is ready") into the
    party. Use this INSTEAD of create_character for someone who already exists in the
    world: it flips the record npc->companion, adds it to the party (once), and fills
    a real sheet so you never invent modifiers. Pass `class_name`+`level`+`abilities`
    for the companion's build; `apply_srd_defaults` sets saves/HP/AC/features (HP is
    auto-set only at level 1 — pass `max_hp` for a higher-level companion). Idempotent
    if already a companion. This prevents the duplicate-stub bug (a roster NPC plus a
    second hand-built companion of the same name)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, npc_id)  # raises if the id isn't in the campaign
        if ch.kind not in ("npc", "companion"):
            raise ValueError(
                f"{npc_id!r} is a {ch.kind!r}; only an NPC (a roster figure) can be recruited "
                f"as a companion. To make a brand-new companion, use create_character."
            )
        ch.kind = "companion"  # type: ignore[assignment]
        if abilities:
            ch.abilities = AbilityScores(**abilities)
            ch.initiative_bonus = ch.abilities.modifier(Ability.DEX)
        if class_name:
            ch.classes = [ClassLevel(name=class_name.capitalize(), level=level, subclass=subclass)]
        if max_hp and max_hp > 0:
            ch.max_hp = max_hp
            ch.current_hp = max_hp
        if armor_class and armor_class > 0:
            ch.armor_class = armor_class
        if skills:  # explicit skill choices win over the class default-fill
            ch.skill_proficiencies = [s.lower() for s in skills if s.lower() in SKILL_ABILITIES]
        if apply_srd_defaults and class_name:
            _apply_srd_class_defaults(ch, class_name, level, set_base_ac=(armor_class <= 0))
        if ch.id not in c.party:
            c.party.append(ch.id)
        save_campaign(c)
        return {"id": ch.id, "name": ch.name, "kind": ch.kind, "party": list(c.party)}


@mcp.tool()
def generate_image(kind: str, prompt: str, seed: Optional[int] = None, scope: Optional[str] = None) -> dict:
    """Generate (or recall from cache) an image for the campaign. `kind` is 'map'
    (region/dungeon), 'portrait' (NPC/PC), or 'scene' (illustration); `prompt` is the
    visual brief. The active provider is chosen by CLAWDND_IMAGE_PROVIDER (default
    'null' → a deterministic placeholder, no network); pass `scope` (a world or
    campaign id) to partition the derived image cache. Repeat requests hit the
    content-hash cache for free. The cache is a derived, rebuildable artifact — it
    never touches campaign state (engine stays sole writer)."""
    return imagegen.generate(kind, prompt, seed=seed, scope=scope)


_SHORT_TO_FULL_AB = {
    "str": "strength", "dex": "dexterity", "con": "constitution",
    "int": "intelligence", "wis": "wisdom", "cha": "charisma",
}


@mcp.tool()
def spawn_monster(campaign_id: str, name: str, count: int = 1) -> dict:
    """Spawn combat-ready monster(s) from the bundled SRD bestiary by name.

    Looks the creature up (case-insensitive) in the ~330-creature SRD data and
    creates Character(kind="monster") records with HP, AC, abilities, proficiency
    and initiative bonuses, and damage resistances/immunities/vulnerabilities all
    pre-filled — so you never hand-transcribe a stat block (and never leave a
    duplicate NPC record). The creature's actions/attacks are stored on the
    monster's `notes` (with the to-hit/damage text) for you to drive `attack`.
    count>1 spawns numbered copies. Unknown name -> {"error", "suggestions"} from a
    fuzzy search (try e.g. 'Goblin Warrior', 'Wolf'). Returns the spawned ids + a
    stat summary incl. xp_each (the encounter reward); pass the ids to start_combat."""
    canonical = bestiary.resolve(name)
    sb = bestiary.stat_block(canonical) if canonical else None
    if sb is None:
        return {"error": f"no creature named {name!r} in the bestiary", "suggestions": bestiary.find(name)}
    n = max(1, min(int(count), 20))
    scores = AbilityScores(**{_SHORT_TO_FULL_AB[k]: v for k, v in sb["abilities"].items()})
    actions_note = " | ".join(f"{a['name']}: {a['desc']}" for a in sb["actions"][:10])
    summary = f"CR {sb['cr']}, {sb['xp']} XP. {sb['size']} {sb['type']}. Actions: {actions_note}"
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        spawned = []
        for i in range(n):
            label = f"{sb['name']} {i + 1}" if n > 1 else sb["name"]
            ch = Character(
                name=label,
                kind="monster",
                abilities=scores,
                max_hp=sb["hp"],
                current_hp=sb["hp"],
                armor_class=sb["ac"],
                hit_dice=sb["hit_dice"],
                proficiency_bonus=sb["proficiency_bonus"],
                initiative_bonus=sb["initiative_bonus"] or scores.modifier(Ability.DEX),
                damage_resistances=sb["damage_resistances"],
                damage_immunities=sb["damage_immunities"],
                damage_vulnerabilities=sb["damage_vulnerabilities"],
                condition_immunities=sb["condition_immunities"],
                notes=summary,
                xp_value=sb["xp"],
            )
            c.characters[ch.id] = ch
            spawned.append({"id": ch.id, "name": ch.name})
        save_campaign(c)
    return {
        "spawned": spawned,
        "name": sb["name"],
        "ac": sb["ac"],
        "hp": sb["hp"],
        "cr": sb["cr"],
        "xp_each": sb["xp"],
        "actions": sb["actions"],
    }


@mcp.tool()
def get_character(campaign_id: str, character_id: str) -> dict:
    """Return a character's full sheet."""
    c = _require(campaign_id)
    ch = c.characters.get(character_id)
    if ch is None:
        raise ValueError(f"no character {character_id!r} in campaign")
    return ch.model_dump(mode="json")


@mcp.tool()
def update_character(campaign_id: str, character_id: str, patch: dict) -> dict:
    """Apply a partial update to a character and persist it.

    `patch` is a dict of fields to change (deep-merged for nested objects), e.g.
    {"current_hp": 12, "armor_class": 15}. Unknown field names are REJECTED.

    WARNING: list fields (conditions, inventory, spells_known, classes) are
    REPLACED wholesale by the patch, not merged. To change a single condition
    use add_condition / remove_condition; for HP use set_hp. Vitals are clamped
    to valid ranges (current_hp to 0..max_hp, exhaustion to 0..6).
    """
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        data = ch.model_dump(mode="json")
        _deep_update(data, patch)
        c.characters[character_id] = Character.model_validate(data)
        save_campaign(c)
        return c.characters[character_id].model_dump(mode="json")


@mcp.tool()
def add_condition(campaign_id: str, character_id: str, condition: str) -> dict:
    """Add a 5e condition to a character (idempotent). Prefer this over patching
    the whole conditions list. Valid values: blinded, charmed, deafened,
    frightened, grappled, incapacitated, invisible, paralyzed, petrified,
    poisoned, prone, restrained, stunned, unconscious."""
    cond = Condition(condition.lower())
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        if cond not in ch.conditions:
            ch.conditions.append(cond)
        if cond in combat.INCAPACITATING:
            ch.concentration = None  # SRD: incapacitation breaks concentration
        save_campaign(c)
        return ch.model_dump(mode="json")


@mcp.tool()
def remove_condition(campaign_id: str, character_id: str, condition: str) -> dict:
    """Remove a 5e condition from a character (no-op if not present)."""
    cond = Condition(condition.lower())
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.conditions = [x for x in ch.conditions if x != cond]
        save_campaign(c)
        return ch.model_dump(mode="json")


@mcp.tool()
def set_hp(
    campaign_id: str, character_id: str, current_hp: int, temp_hp: Optional[int] = None
) -> dict:
    """Set a character's current HP (and optionally temporary HP). Values are
    clamped to valid ranges by the engine (current_hp to 0..max_hp, temp_hp >= 0)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.current_hp = current_hp
        if temp_hp is not None:
            ch.temp_hp = temp_hp
        # Re-validate so the clamp invariants apply.
        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        save_campaign(c)
        return c.characters[character_id].model_dump(mode="json")


@mcp.tool()
def start_combat(campaign_id: str, combatant_ids: list[str]) -> dict:
    """Begin combat: roll initiative (1d20 + initiative_bonus) for each combatant
    and build the turn order (desc, ties broken by DEX modifier then input order).
    Pass the character ids of everyone in the fight."""
    if not combatant_ids:
        raise ValueError("combatant_ids must be non-empty")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if c.combat.active:
            raise ValueError("combat already active; call end_combat first")
        rolled = []
        for cid in combatant_ids:
            ch = _char(c, cid)
            r = dice_mod.roll(f"1d20+{ch.initiative_bonus}")
            rolled.append((cid, r.total, ch.ability_modifier(Ability.DEX)))
        indexed = sorted(enumerate(rolled), key=lambda t: (-t[1][1], -t[1][2], t[0]))
        c.combat = Combat(
            active=True,
            round=1,
            turn_index=0,
            order=[Combatant(character_id=o[0], initiative=o[1]) for _, o in indexed],
        )
        save_campaign(c)
        return _combat_view(c)


@mcp.tool()
def next_turn(campaign_id: str) -> dict:
    """Advance to the next LIVING combatant's turn (round increments on wrap;
    dead or removed combatants are skipped). Returns whose turn it is and whether
    they owe a death save (downed and unstable)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        order = c.combat.order
        if not c.combat.active or not order:
            raise ValueError("no active combat")
        n = len(order)
        cur = None
        for _ in range(n):  # at most one full lap; skip dead/removed combatants
            # Keep turn_index NORMALIZED to [0, n) — it's a position, not a running
            # tally. (A monotonic counter desynced remove_combatant's index math,
            # skipping the current turn after a few rounds.) A wrap back to the top
            # of the initiative order starts a new round.
            c.combat.turn_index = (c.combat.turn_index + 1) % n
            if c.combat.turn_index == 0:
                c.combat.round += 1
            candidate = c.characters.get(c.combat.current_combatant_id)
            if candidate is not None and not candidate.dead:
                cur = candidate
                break
        # Fresh action economy for the new turn; the current combatant's reaction
        # recharges at the start of their turn.
        c.combat.action_used = False
        c.combat.bonus_action_used = False
        if cur is not None:
            for cb in order:
                if cb.character_id == cur.id:
                    cb.reaction_used = False
                    break
        save_campaign(c)
        view = _combat_view(c)
        view["current_name"] = cur.name if cur else None
        view["death_save_due"] = bool(cur and cur.current_hp == 0 and not cur.dead and not cur.stable)
        return view


@mcp.tool()
def use_action(campaign_id: str, character_id: str, kind: str = "action") -> dict:
    """Track a combatant's action economy. kind: action | bonus | reaction | free.
    `action`/`bonus` are legal only on the creature's OWN turn and only once each
    per turn; `reaction` is legal any time but once per round (it refreshes at the
    start of the creature's turn via next_turn); `free`/movement isn't rate-limited.
    Returns {ok, reason, action_available, bonus_available, reaction_available} so
    you can flag an illegal double-action. NOTE: multiattack (Extra Attack) is ONE
    action — declare a single `action`, then make several attack() calls under it."""
    kind = kind.lower()
    if kind not in ("action", "bonus", "reaction", "free", "movement"):
        raise ValueError("kind must be action | bonus | reaction | free")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if not c.combat.active:
            raise ValueError("no active combat")
        ch = _char(c, character_id)
        combatant = next(
            (cb for cb in c.combat.order if cb.character_id == character_id), None
        )
        if combatant is None:
            raise ValueError(f"{ch.name} is not in the initiative order")
        is_current = c.combat.current_combatant_id == character_id
        ok, reason = True, ""
        if kind in ("action", "bonus"):
            if not is_current:
                ok, reason = False, f"it is not {ch.name}'s turn (only a reaction acts off-turn)"
            elif kind == "action" and c.combat.action_used:
                ok, reason = False, "action already used this turn"
            elif kind == "bonus" and c.combat.bonus_action_used:
                ok, reason = False, "bonus action already used this turn"
            elif kind == "action":
                c.combat.action_used = True
            else:
                c.combat.bonus_action_used = True
        elif kind == "reaction":
            if combatant.reaction_used:
                ok, reason = False, f"{ch.name} has already used a reaction this round"
            else:
                combatant.reaction_used = True
        save_campaign(c)
        return {
            "ok": ok,
            "kind": kind,
            "reason": reason,
            "action_available": not c.combat.action_used,
            "bonus_available": not c.combat.bonus_action_used,
            "reaction_available": not combatant.reaction_used,
        }


@mcp.tool()
def remove_combatant(campaign_id: str, character_id: str) -> dict:
    """Remove a combatant from the initiative order (a slain monster, or one that
    fled). Adjusts the turn pointer so the order stays consistent; ends combat if it
    was the last combatant OR if only allies (players/companions) remain — no
    hostiles left means the fight is over."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        order = c.combat.order
        idx = next((i for i, cb in enumerate(order) if cb.character_id == character_id), None)
        if idx is None:
            raise ValueError(f"{character_id!r} is not in the combat order")
        order.pop(idx)
        remaining_kinds = {
            c.characters[cb.character_id].kind for cb in order if cb.character_id in c.characters
        }
        if not order or (remaining_kinds and remaining_kinds <= {"player", "companion"}):
            c.combat = Combat()  # last combatant gone, or no hostiles left -> end the fight
        else:
            if idx < c.combat.turn_index:
                c.combat.turn_index -= 1
            c.combat.turn_index %= len(order)
        save_campaign(c)
        return _combat_view(c)


@mcp.tool()
def attack(
    campaign_id: str,
    attacker_id: str,
    target_id: str,
    attack_bonus: int,
    damage_dice: str,
    damage_type: str = "",
    advantage: bool = False,
    disadvantage: bool = False,
    is_ranged: bool = False,
) -> dict:
    """Resolve an attack. The DM supplies attack_bonus and damage_dice (e.g.
    '1d8+3'); the engine rolls 1d20+bonus vs the target's AC, auto-hits on a
    natural 20 and auto-misses on a natural 1, doubles damage dice on a crit, and
    applies the damage. Condition-based advantage/disadvantage is detected (set
    is_ranged=True so a prone target gives disadvantage rather than advantage) and
    combined with the explicit flags (they cancel if both apply)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        attacker = _char(c, attacker_id)
        target = _char(c, target_id)
        cadv, cdis = combat.attack_modifiers(attacker, target, is_ranged=is_ranged)
        adv = advantage or cadv
        dis = disadvantage or cdis
        atk = dice_mod.roll(f"1d20+{attack_bonus}", advantage=adv, disadvantage=dis)
        hit = atk.crit or (not atk.fumble and atk.total >= target.armor_class)
        # SRD: a melee hit against an unconscious/paralyzed creature auto-crits.
        is_crit = atk.crit or (hit and combat.melee_auto_crit(target, is_ranged))
        result = {
            "attacker": attacker.name,
            "target": target.name,
            "attack_roll": {"total": atk.total, "natural": atk.natural, "detail": atk.detail},
            "advantage": adv,
            "disadvantage": dis,
            "crit": is_crit,
            "hit": hit,
            "target_ac": target.armor_class,
            "damage": None,
        }
        # Non-breaking turn-order signal: off-turn attacks are legal (reactions),
        # but an unintended one desyncs the tracker — surface it so the DM can tell.
        if c.combat.active and c.combat.current_combatant_id not in (None, attacker_id):
            cur = c.characters.get(c.combat.current_combatant_id)
            result["off_turn_warning"] = (
                f"{attacker.name} is acting, but it is "
                f"{cur.name if cur else c.combat.current_combatant_id}'s turn — "
                f"a reaction? Otherwise advance with next_turn so the order stays in sync."
            )
        if hit:
            expr = combat.double_dice(damage_dice) if is_crit else damage_dice
            dmg = dice_mod.roll(expr)
            outcome = combat.apply_damage(target, max(0, dmg.total), crit=is_crit, damage_type=damage_type)
            save_campaign(c)
            result["damage"] = {"total": max(0, dmg.total), "type": damage_type, "expr": expr, "detail": dmg.detail}
            result["target_state"] = outcome
        return result


@mcp.tool()
def apply_damage(
    campaign_id: str, target_id: str, amount: int, damage_type: str = "", crit: bool = False, half: bool = False
) -> dict:
    """Apply damage to a character. Temp HP is absorbed first; HP floors at 0;
    massive damage causes instant death; dropping to 0 makes the target unconscious
    and dying; a hit while already down adds a death-save failure (two on a crit).
    Set half=True for a successful save vs a 'half on save' spell (halves the amount).
    `damage_type` (e.g. 'fire', 'slashing') applies the target's resistance (half),
    immunity (none), or vulnerability (double). Returns the new state, including
    any concentration_dc to roll."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        out = combat.apply_damage(_char(c, target_id), amount, crit=crit, half=half, damage_type=damage_type)
        save_campaign(c)
        return out


@mcp.tool()
def apply_healing(campaign_id: str, target_id: str, amount: int) -> dict:
    """Heal a character (up to max HP). Healing above 0 HP ends the dying state
    and resets death saves. Cannot revive the dead."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        out = combat.apply_healing(_char(c, target_id), amount)
        save_campaign(c)
        return out


@mcp.tool()
def set_temp_hp(campaign_id: str, target_id: str, amount: int) -> dict:
    """Grant temporary HP. Temp HP does NOT stack — keeps the higher of current
    and new (SRD rule)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, target_id)
        ch.temp_hp = max(ch.temp_hp, max(0, amount))
        save_campaign(c)
        return {"temp_hp": ch.temp_hp, "hp": f"{ch.current_hp}/{ch.max_hp}"}


@mcp.tool()
def concentration_save(campaign_id: str, character_id: str, dc: int) -> dict:
    """Roll a concentration saving throw (CON save) at the given DC (usually
    max(10, damage//2) from apply_damage). On failure, concentration is lost."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        r = dice_mod.roll(f"1d20+{ch.saving_throw_bonus(Ability.CON)}")
        maintained = r.total >= dc
        if not maintained:
            ch.concentration = None
        save_campaign(c)
        return {
            "roll": r.total,
            "natural": r.natural,
            "dc": dc,
            "maintained": maintained,
            "concentration": ch.concentration,
        }


@mcp.tool()
def roll_death_save(campaign_id: str, character_id: str) -> dict:
    """Roll a death saving throw for a downed character (must be at 0 HP, not dead
    or stable). 10+ success, <10 failure; nat 20 -> regain 1 HP; nat 1 -> two
    failures; 3 successes stabilize; 3 failures die."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        if ch.current_hp != 0 or ch.dead or ch.stable:
            raise ValueError("death saves apply only to a downed (0 HP), unstable, living character")
        out = combat.resolve_death_save(ch, dice_mod.roll("1d20"))
        save_campaign(c)
        return out


@mcp.tool()
def stabilize(campaign_id: str, actor_id: str, target_id: str, dc: int = 10) -> dict:
    """An actor stabilizes a DOWNED ally with a DC 10 Wisdom (Medicine) check — the
    5e action for saving a dying ally when you have NO healing spell in hand. This
    closes the companion's `aid_downed` loop: when companion_suggest_action returns
    aid_downed with spell=null (no slot), call this instead of hand-waving it. On
    success the target becomes stable (dying stops, death saves reset, holds at 0 HP);
    on failure nothing changes and they keep rolling death saves."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        actor = _char(c, actor_id)
        target = _char(c, target_id)
        if target.current_hp != 0 or target.dead or target.stable:
            raise ValueError("can only stabilize a downed (0 HP), unstable, living creature")
        r = dice_mod.roll(f"1d20+{actor.skill_bonus('medicine')}")
        success = r.total >= dc
        if success:
            target.stable = True
            target.death_saves.successes = 0
            target.death_saves.failures = 0
        save_campaign(c)
        return {
            "actor": actor.name,
            "target": target.name,
            "skill": "medicine",
            "roll": r.total,
            "natural": r.natural,
            "dc": dc,
            "success": success,
            "stable": target.stable,
        }


@mcp.tool()
def end_combat(campaign_id: str) -> dict:
    """End combat (clears initiative, round, and turn order). Character HP and
    conditions persist past the encounter.

    If the campaign is in "xp" `leveling_mode` (the default), the XP of monsters
    defeated THIS encounter is auto-awarded to the party, split evenly — so
    progression isn't a manual chore. Returns `xp_awarded` + per-character `grants`
    (with `can_level_up`) when any was granted. "milestone" mode leaves leveling to
    the DM (no auto-XP)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        result: dict = {"active": False}
        if c.leveling_mode == "xp":
            combat_ids = {cb.character_id for cb in c.combat.order}
            total = sum(
                ch.xp_value for ch in c.characters.values()
                if ch.id in combat_ids and ch.kind == "monster" and ch.dead and ch.xp_value > 0
            )
            recipients = [
                c.characters[i] for i in c.party
                if i in c.characters and not c.characters[i].dead
            ]
            if total > 0 and recipients:
                each, rem = divmod(total, len(recipients))
                grants = []
                for idx, ch in enumerate(recipients):
                    amt = each + (rem if idx == 0 else 0)
                    ch.xp = max(0, ch.xp + amt)
                    available = srd_tables.level_for_xp(ch.xp)
                    grants.append({
                        "id": ch.id, "name": ch.name, "xp_gained": amt, "xp": ch.xp,
                        "level_available": available, "can_level_up": available > ch.total_level,
                    })
                result["xp_awarded"] = total
                result["grants"] = grants
        c.combat = Combat()
        save_campaign(c)
        return result


@mcp.tool()
def generate_ability_scores(
    method: str = "standard_array", point_buy: Optional[dict] = None, seed: Optional[int] = None
) -> dict:
    """Generate ability scores. method:
    - 'standard_array' -> returns [15,14,13,12,10,8] to assign;
    - 'point_buy' -> validate a {ability: score} dict against the 27-point SRD
      budget (scores 8-15), returning points spent/remaining;
    - 'roll' -> six 4d6-drop-lowest rolls.
    Pure helper — does not write campaign state."""
    m = method.lower()
    if m == "standard_array":
        return {"method": "standard_array", "array": srd_tables.standard_array()}
    if m == "point_buy":
        if not point_buy:
            raise ValueError("point_buy requires a {ability: score} mapping")
        cost = srd_tables.point_buy_cost()
        total = 0
        for ability, score in point_buy.items():
            if str(score) not in cost:
                raise ValueError(f"score {score} for {ability} is out of point-buy range 8-15")
            total += cost[str(score)]
        if total > 27:
            raise ValueError(f"point-buy total {total} exceeds the 27-point budget")
        return {
            "method": "point_buy",
            "scores": point_buy,
            "points_spent": total,
            "points_remaining": 27 - total,
        }
    if m == "roll":
        rolls = []
        for i in range(6):
            r = dice_mod.roll("4d6kh3", seed=(seed + i) if seed is not None else None)
            rolls.append({"total": r.total, "kept": r.rolls, "dropped": r.dropped})
        return {"method": "roll", "rolls": rolls, "totals": [x["total"] for x in rolls]}
    raise ValueError(f"unknown method {method!r} (use standard_array | point_buy | roll)")


@mcp.tool()
def award_xp(campaign_id: str, character_id: str, amount: int, reason: str = "") -> dict:
    """Award (or deduct) XP. Reports whether a new level is available — leveling
    is a deliberate choice via level_up, never automatic."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.xp = max(0, ch.xp + amount)
        save_campaign(c)
        available = srd_tables.level_for_xp(ch.xp)
        return {
            "xp": ch.xp,
            "current_level": ch.total_level,
            "level_available": available,
            "can_level_up": available > ch.total_level,
            "reason": reason,
        }


@mcp.tool()
def award_party_xp(
    campaign_id: str, amount: int, reason: str = "", include_companions: bool = True
) -> dict:
    """Award one encounter's XP to the whole party, split evenly. Divides `amount`
    across the player characters (and companions, unless include_companions=False),
    giving any remainder to the first recipient. Returns the per-character grants
    and whether anyone can now level up — use this instead of computing the split
    by hand and calling award_xp repeatedly."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        kinds = {"player", "companion"} if include_companions else {"player"}
        recipients = [
            cid
            for cid in c.party
            if (m := c.characters.get(cid)) and m.kind in kinds and not m.dead
        ]
        if not recipients:
            raise ValueError("no eligible party members to award XP to")
        each, extra = divmod(max(0, amount), len(recipients))
        grants = []
        for i, cid in enumerate(recipients):
            ch = c.characters[cid]
            share = each + (extra if i == 0 else 0)
            ch.xp = max(0, ch.xp + share)
            available = srd_tables.level_for_xp(ch.xp)
            grants.append(
                {
                    "id": ch.id,
                    "name": ch.name,
                    "granted": share,
                    "xp": ch.xp,
                    "current_level": ch.total_level,
                    "can_level_up": available > ch.total_level,
                }
            )
        save_campaign(c)
        return {
            "total": amount,
            "split_between": len(recipients),
            "grants": grants,
            "reason": reason,
        }


@mcp.tool()
def level_up(
    campaign_id: str,
    character_id: str,
    class_name: str,
    hp_method: str = "average",
    hp_roll: Optional[int] = None,
    subclass: Optional[str] = None,
    asi: Optional[dict] = None,
    feat: Optional[str] = None,
    seed: Optional[int] = None,
) -> dict:
    """Level a character up in a class (multiclass if new — SRD prerequisites are
    enforced). Adds HP (average, or rolled with hp_method='roll'), applies an ASI
    or feat at ASI levels, and recomputes proficiency bonus, initiative, and spell
    slots. `asi` is e.g. {"strength": 2} or {"strength": 1, "dexterity": 1}."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        cname = class_name.lower()
        srd_tables.class_data(cname)  # validate the class exists
        existing = next((cl for cl in ch.classes if cl.name.lower() == cname), None)
        if existing is None and ch.classes and not _meets_prereq(ch, cname):
            raise ValueError(f"does not meet the multiclass prerequisite for {class_name}")

        die = srd_tables.hit_die(cname)
        con = ch.ability_modifier(Ability.CON)
        if hp_method == "roll":
            base = hp_roll if hp_roll is not None else dice_mod.roll(f"1d{die}", seed=seed).total
        else:
            base = srd_tables.average_hp(die)
        gain = max(1, base + con)

        if existing:
            existing.level += 1
            if subclass:
                existing.subclass = subclass
            new_class_level = existing.level
        else:
            ch.classes.append(ClassLevel(name=class_name.capitalize(), level=1, subclass=subclass))
            new_class_level = 1

        ch.max_hp += gain
        ch.current_hp += gain
        ch.hit_dice_remaining += 1
        # keep the hit_dice string in sync (single-class; was left stale after level_up)
        if len({cl.name.lower() for cl in ch.classes}) == 1:
            ch.hit_dice = f"{sum(cl.level for cl in ch.classes)}d{die}"

        applied = None
        if srd_tables.is_asi_level(cname, new_class_level):
            if asi:
                for ability, inc in asi.items():
                    if ability not in _AB3_TO_FULL.values():
                        raise ValueError(f"unknown ability {ability!r} in asi")
                    setattr(ch.abilities, ability, min(20, getattr(ch.abilities, ability) + inc))
                applied = {"asi": asi}
            elif feat:
                applied = {"feat": feat}
                ch.notes = (ch.notes + f" | feat: {feat}").strip(" |")

        ch.proficiency_bonus = srd_tables.proficiency_bonus(ch.total_level)
        ch.initiative_bonus = ch.ability_modifier(Ability.DEX)
        _recompute_spellcasting(ch)

        # Class/subclass features gained at this new class level — leveling now
        # grants real features (and the mechanical hints the engine references),
        # not just HP and slots.
        gained = srd_tables.features_at(cname, new_class_level)
        for f in gained:
            if f["name"] not in ch.features:
                ch.features.append(f["name"])
            if "extra_attacks" in f:
                ch.extra_attacks = max(ch.extra_attacks, int(f["extra_attacks"]))
            if f.get("sneak_attack_dice"):
                ch.sneak_attack_dice = f["sneak_attack_dice"]

        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        save_campaign(c)
        sheet = c.characters[character_id].model_dump(mode="json")
        sheet["_hp_gained"] = gain
        sheet["_asi_applied"] = applied
        sheet["_features_gained"] = gained
        return sheet


@mcp.tool()
def spell_save_dc(campaign_id: str, character_id: str) -> dict:
    """Return a caster's spell save DC (8 + proficiency + casting modifier) and
    spell attack bonus (proficiency + casting modifier)."""
    c = _require(campaign_id)
    ch = _char(c, character_id)
    mod = _casting_mod(ch)
    return {"spell_save_dc": 8 + ch.proficiency_bonus + mod, "spell_attack_bonus": ch.proficiency_bonus + mod}


@mcp.tool()
def cast_spell(
    campaign_id: str, character_id: str, spell_name: str, slot_level: Optional[int] = None
) -> dict:
    """Cast a spell — works for ANY of the ~339 SRD spells. Consumes a spell slot
    (cantrips use none); upcasts when slot_level exceeds the spell's level; sets
    concentration if the spell concentrates (breaking any prior). If spells_known/
    prepared are set, the spell must be among them (skipped leniently when empty).

    For the hand-authored spells the engine fully resolves the effect (returns
    `automated:true` + `effect` with upcast/cantrip-scaled damage/heal). For every
    other SRD spell it DEGRADES GRACEFULLY (returns `automated:false`): the slot is
    spent and concentration set, and it hands you the structured values to resolve
    by hand — `save_ability`, `attack_roll`, `base_damage`, `upcast`, plus the
    `spell_save_dc`/`spell_attack_bonus`. It never errors on an un-modeled spell.
    Resolve: attack-roll spells via attack(); save spells via saving_throw + then
    apply_damage(half=<save succeeded>); heals via apply_healing."""
    curated = None
    try:
        curated = spells.spell_data(spell_name)
    except ValueError:
        curated = None
    srd = spells.srd_spell(spell_name)
    if curated is None and srd is None:
        raise ValueError(f"unknown spell {spell_name!r}")
    canonical = (curated or srd).get("name", spell_name)
    spell_level = int((curated.get("level", 0) if curated else srd.get("level", 0)) or 0)
    concentrates = bool(curated.get("concentration") if curated else srd.get("concentration"))
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        known = set(ch.spells_known) | set(ch.spells_prepared)
        if known and canonical not in known:
            raise ValueError(f"{ch.name} doesn't know or have {canonical!r} prepared")
        slot_used = None
        if spell_level > 0:
            lvl = spell_level if slot_level is None else slot_level
            if lvl < spell_level:
                raise ValueError(f"cannot cast a level-{spell_level} spell with a level-{lvl} slot")
            slot = ch.spell_slots.get(lvl)
            if slot is None or slot.used >= slot.maximum:
                raise ValueError(f"no level-{lvl} spell slot available")
            slot.used += 1
            slot_used = lvl
        if concentrates:
            ch.concentration = canonical  # replaces (breaks) any prior concentration
        mod = _casting_mod(ch)
        prof = ch.proficiency_bonus
        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        save_campaign(c)
        updated = c.characters[character_id]
        result = {
            "spell": canonical,
            "level": spell_level,
            "slot_used": slot_used,
            "concentration": updated.concentration,
            "spell_save_dc": 8 + prof + mod,
            "spell_attack_bonus": prof + mod,
            "slots_remaining": {
                str(lv): s.maximum - s.used for lv, s in updated.spell_slots.items()
            },
        }
        if curated is not None:
            result["automated"] = True
            result["effect"] = spells.resolve_effect(
                curated, slot_used or spell_level, ch.total_level, mod
            )
        else:
            result["automated"] = False
            result["school"] = srd.get("school")
            result["save_ability"] = srd.get("saving_throw_ability") or None
            result["attack_roll"] = bool(srd.get("attack_roll"))
            result["base_damage"] = srd.get("damage_roll") or None
            result["damage_types"] = srd.get("damage_types") or None
            result["upcast"] = srd.get("higher_level") or None
            result["casting_time"] = srd.get("casting_time")
            result["range"] = srd.get("range_text")
            result["note"] = (
                "Slot spent + concentration set. Effect not auto-rolled — resolve with "
                "the values above: attack-roll spells via attack(attack_bonus="
                "spell_attack_bonus); save spells via saving_throw(save_ability vs "
                "spell_save_dc) then apply_damage(base_damage, damage_types, "
                "half=<save succeeded>); healing via apply_healing."
            )
        return result


@mcp.tool()
def saving_throw(campaign_id: str, character_id: str, ability: str, dc: int) -> dict:
    """Roll a saving throw for a character against a DC. ability is one of
    str/dex/con/int/wis/cha. Returns the roll and whether it succeeded."""
    c = _require(campaign_id)
    ch = _char(c, character_id)
    ab = Ability(ability.lower())
    r = dice_mod.roll(f"1d20+{ch.saving_throw_bonus(ab)}")
    return {"ability": ab.value, "roll": r.total, "natural": r.natural, "dc": dc, "success": r.total >= dc}


@mcp.tool()
def add_item(
    campaign_id: str, character_id: str, name: str, quantity: int = 1, weight: float = 0.0,
    requires_attunement: bool = False, description: str = "",
) -> dict:
    """Add an item to a character's inventory (stacks with an identical unequipped,
    non-attuned item)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        inventory.add_item(ch, name, quantity, weight, requires_attunement, description)
        save_campaign(c)
        return {"inventory": [i.model_dump() for i in ch.inventory]}


@mcp.tool()
def remove_item(campaign_id: str, character_id: str, name: str, quantity: int = 1) -> dict:
    """Remove a quantity of an item (removes the whole stack if quantity >= held)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        inventory.remove_item(ch, name, quantity)
        save_campaign(c)
        return {"inventory": [i.model_dump() for i in ch.inventory]}


@mcp.tool()
def equip_item(campaign_id: str, character_id: str, name: str, equipped: bool = True) -> dict:
    """Equip an item (or unequip with equipped=False)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        it = inventory.set_equipped(ch, name, equipped)
        save_campaign(c)
        return it.model_dump()


@mcp.tool()
def attune_item(campaign_id: str, character_id: str, name: str, attuned: bool = True) -> dict:
    """Attune to a magic item (or end attunement with attuned=False). Max 3 attuned."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        it = inventory.set_attuned(ch, name, attuned)
        save_campaign(c)
        return it.model_dump()


@mcp.tool()
def adjust_currency(
    campaign_id: str, character_id: str, cp: int = 0, sp: int = 0, ep: int = 0, gp: int = 0, pp: int = 0
) -> dict:
    """Add or subtract specific coin denominations. Raises if any would go negative."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        cur = inventory.adjust_currency(ch, cp, sp, ep, gp, pp)
        save_campaign(c)
        return cur.model_dump()


@mcp.tool()
def buy_item(
    campaign_id: str, character_id: str, name: str, cost_gp: float, quantity: int = 1,
    weight: float = 0.0, requires_attunement: bool = False, description: str = "",
) -> dict:
    """Buy an item: pay cost_gp (making change from the purse) and add it to inventory.
    Raises if the character can't afford it."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        inventory.pay(ch, cost_gp)
        inventory.add_item(ch, name, quantity, weight, requires_attunement, description)
        save_campaign(c)
        return {"currency": ch.currency.model_dump(), "inventory": [i.model_dump() for i in ch.inventory]}


@mcp.tool()
def sell_item(campaign_id: str, character_id: str, name: str, price_gp: float, quantity: int = 1) -> dict:
    """Sell an item: remove it and add price_gp to the purse."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        inventory.remove_item(ch, name, quantity)
        inventory.gain(ch, price_gp)
        save_campaign(c)
        return {"currency": ch.currency.model_dump(), "inventory": [i.model_dump() for i in ch.inventory]}


@mcp.tool()
def encumbrance_status(campaign_id: str, character_id: str) -> dict:
    """Carried weight vs capacity and encumbrance status (SRD variant thresholds:
    STR x5 encumbered, x10 heavily encumbered, x15 max)."""
    c = _require(campaign_id)
    return inventory.encumbrance(_char(c, character_id))


@mcp.tool()
def short_rest(campaign_id: str, character_id: str, hit_dice_to_spend: int = 0) -> dict:
    """Take a short rest: optionally spend Hit Dice to heal (1d{hit die} + CON
    each); a single-class Warlock recovers all (pact) spell slots."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if c.combat.active:
            raise ValueError("cannot rest during active combat — call end_combat first")
        ch = _char(c, character_id)
        out = rests.short_rest(ch, hit_dice_to_spend, dice_mod.roll)
        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        save_campaign(c)
        return out


@mcp.tool()
def long_rest(campaign_id: str, character_id: str) -> dict:
    """Take a long rest: restore all HP, recover half total Hit Dice (min 1), reset
    all spell slots, reduce exhaustion by 1, and end the dying state. The DM should
    call this for each party member. Cannot rest while dead."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if c.combat.active:
            raise ValueError("cannot rest during active combat — call end_combat first")
        ch = _char(c, character_id)
        out = rests.long_rest(ch)
        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        save_campaign(c)
        return out


@mcp.tool()
def learn_spells(campaign_id: str, character_id: str, spells_list: list) -> dict:
    """Set a character's known spells (replaces the list)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.spells_known = list(spells_list)
        save_campaign(c)
        return {"spells_known": ch.spells_known}


@mcp.tool()
def prepare_spells(campaign_id: str, character_id: str, spells_list: list) -> dict:
    """Set a character's prepared spells (replaces the list)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.spells_prepared = list(spells_list)
        save_campaign(c)
        return {"spells_prepared": ch.spells_prepared}


@mcp.tool()
def set_attitude(campaign_id: str, character_id: str, attitude: str) -> dict:
    """Set an NPC's attitude (free text, e.g. 'guarded', or a track value:
    hostile / wary / indifferent / friendly / helpful)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.attitude = attitude
        save_campaign(c)
        return {"id": ch.id, "name": ch.name, "attitude": ch.attitude}


@mcp.tool()
def remember(campaign_id: str, character_id: str, fact: str) -> dict:
    """Append a fact to a character's (usually an NPC's) persistent memory, so it
    is recalled in later sessions."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        if fact not in ch.memory:  # de-dupe identical facts
            ch.memory.append(fact)
        save_campaign(c)
        return {"id": ch.id, "name": ch.name, "memory": ch.memory}


@mcp.tool()
def forget(campaign_id: str, character_id: str, fact: str) -> dict:
    """Remove a remembered fact (exact match) from a character's memory."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        match = next((m for m in ch.memory if m.lower() == fact.lower()), None)
        if match is None:
            raise ValueError(f"{ch.name} does not remember that")
        ch.memory.remove(match)
        save_campaign(c)
        return {"id": ch.id, "name": ch.name, "memory": ch.memory}


# Skills that PERCEIVE an NPC rather than INFLUENCE them. A read (insight,
# perception, investigation) tells the actor something; it must NOT change how the
# NPC feels about the actor — observer clarity is not persuasion, and a misread is
# the actor's problem, not a relationship penalty. Everything else is influence.
READ_SKILLS = {"insight", "perception", "investigation"}


@mcp.tool()
def social_check(campaign_id: str, actor_id: str, npc_id: str, skill: str, dc: int) -> dict:
    """An actor's skill check against an NPC, with read-vs-influence semantics.

    INFLUENCE skills (persuasion / deception / intimidation / …) try to move the
    NPC: on success the attitude improves one step on the track (hostile -> wary ->
    indifferent -> friendly -> helpful), on failure it worsens one step.

    READ skills (insight / perception / investigation) only PERCEIVE the NPC and
    NEVER change its attitude — reading or misreading someone is observer clarity,
    not influence. A read returns a `read` block (an accurate sense of the NPC's
    stance on success; a deliberately uncertain, almost-grasped impression on a
    miss) for the DM to narrate — never a flat attitude penalty for a failed read."""
    if skill.lower() not in SKILL_ABILITIES:
        raise ValueError(f"unknown skill {skill!r}")
    if actor_id == npc_id:
        raise ValueError("actor and npc must be different characters")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        actor = _char(c, actor_id)
        the_npc = _char(c, npc_id)
        if the_npc.kind not in ("npc", "monster"):
            raise ValueError("social_check target must be an NPC or monster")
        sk = skill.lower()
        r = dice_mod.roll(f"1d20+{actor.skill_bonus(sk)}")
        success = r.total >= dc
        old = the_npc.attitude
        is_read = sk in READ_SKILLS
        read = None
        if is_read:
            # Perceive, don't influence: attitude is untouched, no state write.
            read = {
                "perceived_attitude": old if success else None,
                "note": (
                    "A clear read — this is the NPC's honest stance toward the actor "
                    "right now; narrate what the actor accurately perceives."
                    if success
                    else "The read won't resolve — play the miss as a specific "
                    "almost-grasped detail that slips away (a calculation behind the "
                    "warmth, a held beat), not a flat blank and not an attitude penalty."
                ),
            }
        else:
            # Influence: move the attitude and persist the change.
            the_npc.attitude = npc_mod.shift_attitude(the_npc.attitude, 1 if success else -1)
            save_campaign(c)
        out = {
            "actor": actor.name,
            "npc": the_npc.name,
            "skill": sk,
            "kind": "read" if is_read else "influence",
            "roll": r.total,
            "natural": r.natural,
            "dc": dc,
            "success": success,
            "old_attitude": old,
            "new_attitude": the_npc.attitude,
        }
        if read is not None:
            out["read"] = read
        return out


@mcp.tool()
def companion_suggest_action(campaign_id: str, companion_id: str) -> dict:
    """Suggest a tactical action for the companion (or any character) given the
    current combat — a deterministic aid the companion persona may follow or
    override. Returns {action, target_id, reason}."""
    c = _require(campaign_id)
    return companion.suggest_action(_char(c, companion_id), c.combat, c.characters)


@mcp.tool()
def companion_advise(campaign_id: str, companion_id: str, situation: str = "") -> dict:
    """Get the companion's in-character take on the CURRENT (non-combat) moment so
    the DM voices it reliably — the storytelling default, not an afterthought. Pass
    a short `situation` (the choice/discovery/lull at hand); it pulls relevant
    memory callbacks via recall and returns the companion's voice_id + personality
    + callbacks + a prompt to voice from. Speak the companion's line in its voice,
    then let the player respond / deliberate with it. Read-only."""
    c = _require(campaign_id)
    comp = _char(c, companion_id)
    callbacks = ledger_mod.recall(campaign_id, situation, limit=3) if situation.strip() else []
    return companion.deliberate(comp, situation, callbacks=callbacks)


def _new_session_id() -> str:
    import uuid

    return f"session-{uuid.uuid4().hex[:8]}"


def _ensure_session(c) -> str:
    """Return the active session id, auto-starting + tracking one if none is active."""
    if not c.active_session_id:
        sid = _new_session_id()
        c.active_session_id = sid
        c.session_ids.append(sid)
    return c.active_session_id


@mcp.tool()
def start_session(campaign_id: str, title: str = "") -> dict:
    """Begin a new play session. Rolls over to a fresh session log and returns a
    'previously on...' recap of the PRIOR session — so reloading and calling this
    resumes the campaign with a recap that spans sessions. Pair with end_session
    when the player stops. (Use this at the top of /session-start.)"""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        prior = c.session_ids[-1] if c.session_ids else None
        previously = (
            recap.recap_from_store(campaign_id, prior) if prior else recap.format_recap([])
        )
        sid = _new_session_id()
        c.session_ids.append(sid)
        c.active_session_id = sid
        append_log(
            campaign_id,
            sid,
            SessionLogEntry(
                kind="system",
                text=f"Session {len(c.session_ids)} began" + (f": {title}" if title else ""),
            ),
        )
        save_campaign(c)
        return {"session_id": sid, "number": len(c.session_ids), "previously_on": previously}


@mcp.tool()
def end_session(campaign_id: str, summary: str = "") -> dict:
    """End the active play session (logs a closing marker + optional summary, then
    clears the active session so the next start_session recaps this one)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if not c.active_session_id:
            return {"ended": None, "note": "no active session"}
        sid = c.active_session_id
        append_log(
            campaign_id,
            sid,
            SessionLogEntry(kind="system", text="Session ended." + (f" {summary}" if summary else "")),
        )
        c.active_session_id = None
        save_campaign(c)
        return {"ended": sid, "number": len(c.session_ids)}


@mcp.tool()
def log_event(campaign_id: str, kind: str, text: str, speaker: str = "") -> dict:
    """Record a story beat in the current session log (kind: narration | dialogue
    | roll | system | combat). Auto-starts a session if none is active. Powers
    recaps and post-compaction recovery."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        sid = _ensure_session(c)
        save_campaign(c)
        entry = SessionLogEntry(kind=kind, text=text, speaker=speaker or None)
        append_log(campaign_id, sid, entry)
        return {"session_id": sid, "logged": entry.model_dump()}


@mcp.tool()
def session_recap(campaign_id: str) -> dict:
    """Return a 'previously on...' recap of the current session, or the most recent
    one if none is active (e.g. right after a reload, before start_session)."""
    c = _require(campaign_id)
    sid = c.active_session_id or (c.session_ids[-1] if c.session_ids else None)
    if not sid:
        return {"recap": recap.format_recap([])}
    return {"recap": recap.recap_from_store(campaign_id, sid)}


@mcp.tool()
def recall(campaign_id: str, query: str, kinds: Optional[list] = None, limit: int = 8) -> dict:
    """Search the WHOLE campaign's history (the memory ledger) — events, dialogue,
    decisions, NPC facts, quest milestones, consequences — ranked by relevance.
    The DM and companions use this to stay consistent and call back to the past
    ("what did we decide about the cult?", "who did we meet in the sump?"). Read-
    only; the index is rebuilt from committed state when stale. `kinds` optionally
    filters (events|dialogue|decision|npc_fact|quest_milestone|consequence)."""
    return {"query": query, "hits": ledger_mod.recall(campaign_id, query, kinds=kinds, limit=limit)}


@mcp.tool()
def recall_npc(campaign_id: str, npc_id: str, limit: int = 12) -> dict:
    """Everything the campaign has recorded about / said by one character — facts
    (`remember`), dialogue, attitude shifts. Use before role-playing a returning
    NPC so they remember the party."""
    return {"npc_id": npc_id, "hits": ledger_mod.recall_npc(campaign_id, npc_id, limit=limit)}


@mcp.tool()
def recall_decisions(campaign_id: str, query: str = "", limit: int = 12) -> dict:
    """The party's past decisions (from record_decision), most recent first, or
    filtered by a text query. Use to honor or call back to earlier choices."""
    return {"hits": ledger_mod.recall_decisions(campaign_id, query, limit=limit)}


@mcp.tool()
def add_consequence(campaign_id: str, in_days: int, text: str, note: str = "") -> dict:
    """Schedule a time-deferred world event to come due `in_days` from now (the
    in-world Campaign.day). Use it whenever the present sets up the future — a
    ritual that completes in 3 days, a spared villain who returns in a week, a
    siege that arrives, a debt called in. `check_consequences` surfaces them when
    the day arrives. This is how the world keeps moving between adventures."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        conseq = consequences_mod.schedule(c, in_days, text, note)
        save_campaign(c)
        return {
            "id": conseq.id,
            "trigger_day": conseq.trigger_day,
            "current_day": c.day,
            "text": conseq.text,
        }


@mcp.tool()
def check_consequences(campaign_id: str) -> dict:
    """Return (and mark fired) any scheduled consequences that have come due as of
    the current in-world day, plus the still-pending ones. Call this after time
    passes (travel with advance_time, a long rest, downtime) so the world's
    deferred events surface for the DM to narrate."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        fired = consequences_mod.due(c)
        save_campaign(c)
        return {
            "current_day": c.day,
            "due": [
                {"id": x.id, "text": x.text, "note": x.note, "trigger_day": x.trigger_day}
                for x in fired
            ],
            "pending": [
                {"id": x.id, "text": x.text, "trigger_day": x.trigger_day}
                for x in consequences_mod.pending(c)
            ],
        }


@mcp.tool()
def world_tick(campaign_id: str) -> dict:
    """Surface BACKGROUND world events — the world's standing threads (a contested
    seat of power, a cult recruiting, factions maneuvering) move on their own, whether
    or not the party is pursuing them, so the world feels alive and the scope stays
    bigger than the room the party is standing in.

    Returns any due "world beats" for the DM to weave in — a crier's notice, an
    overheard rumor, an off-screen development — then escalate it and `remember` what
    changed. Each thread re-arms a few days out, so the world keeps living. This fires
    automatically on time-advancing `travel_to` and `downtime`; call it explicitly to
    check whether the wider world has stirred. Read-only to the party; world-level only."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        beats = worldsim.tick(c)
        save_campaign(c)
        return {
            "current_day": c.day,
            "world_beats": [{"thread_id": b.thread_id, "text": b.text} for b in beats],
            "pending": [{"thread_id": b.thread_id, "trigger_day": b.trigger_day} for b in worldsim.pending_threads(c)],
        }


@mcp.tool()
def add_quest(
    campaign_id: str,
    title: str,
    description: str = "",
    giver_id: str = "",
    location_id: str = "",
    objectives: Optional[list] = None,
) -> dict:
    """Add a quest, optionally linked to the NPC who gave it (giver_id) and the
    location it's anchored to (location_id), so the dashboard and DM can trace
    who-wants-what-where. A campaign has many quests; the opening hook is just one."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        q = Quest(
            title=title,
            description=description,
            giver_id=giver_id or None,
            location_id=location_id or None,
            objectives=list(objectives or []),
        )
        c.quests[q.id] = q
        save_campaign(c)
        return {"id": q.id, "title": q.title, "status": q.status}


@mcp.tool()
def complete_quest(campaign_id: str, quest_id: str, status: str = "completed") -> dict:
    """Resolve a quest. status: completed | failed | active."""
    if status not in ("completed", "failed", "active"):
        raise ValueError("status must be completed | failed | active")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        q = c.quests.get(quest_id)
        if q is None:
            raise ValueError(f"no quest {quest_id!r}")
        q.status = status  # type: ignore[assignment]
        save_campaign(c)
        return {"id": q.id, "title": q.title, "status": q.status}


@mcp.tool()
def campaign_dashboard(campaign_id: str) -> dict:
    """One-call situational rollup for the DM — ideal after a gap or compaction.
    Returns day/time + location, party vitals, active quests (with giver +
    location names resolved), faction standings, and pending (not-yet-due)
    consequences. Read-only."""
    c = _require(campaign_id)

    def _name(cid):
        ch = c.characters.get(cid) if cid else None
        return ch.name if ch else None

    def _loc(lid):
        loc = c.locations.get(lid) if lid else None
        return loc.name if loc else None

    party = [
        {
            "id": cid,
            "name": ch.name,
            "kind": ch.kind,
            "hp": f"{ch.current_hp}/{ch.max_hp}",
            "level": ch.total_level,
        }
        for cid in c.party
        if (ch := c.characters.get(cid))
    ]
    quests = [
        {
            "id": q.id,
            "title": q.title,
            "status": q.status,
            "giver": _name(q.giver_id),
            "location": _loc(q.location_id),
        }
        for q in c.quests.values()
        if q.status == "active"
    ]
    return {
        "title": c.title,
        "day": c.day,
        "time_of_day": c.time_of_day,
        "location": _loc(c.current_location_id),
        "party": party,
        "active_quests": quests,
        "factions": [
            {"name": f.name, "reputation": f.reputation} for f in c.factions.values()
        ],
        "pending_consequences": [
            {"text": x.text, "trigger_day": x.trigger_day}
            for x in consequences_mod.pending(c)
        ],
    }


@mcp.tool()
def downtime(campaign_id: str, days: int, note: str = "") -> dict:
    """Advance the campaign by `days` of downtime (the in-world clock jumps forward,
    resetting to morning), then surface any consequences that come due in that span
    for the DM to narrate. Use between adventures for travel, rest, research, or
    crafting. Returns the new day + the now-due consequences."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        elapsed = max(0, int(days))
        c.day += elapsed
        c.time_of_day = "morning"
        due = consequences_mod.due(c)
        beats = worldsim.tick(c, max_beats=2)  # a long span → a couple of threads stirred
        save_campaign(c)
        return {
            "day": c.day,
            "days_elapsed": elapsed,
            "note": note,
            "due_consequences": [{"text": x.text, "note": x.note} for x in due],
            "world_beats": [b.text for b in beats],
        }


@mcp.tool()
def adjust_reputation(
    campaign_id: str, faction_id: str, delta: int, reason: str = "", name: str = ""
) -> dict:
    """Adjust a faction's standing with the party by `delta` (clamped to -100..100).
    Creates the faction if it doesn't exist yet (pass `name` for a readable label,
    else the id is title-cased). `reason` is recorded. Use when the party's actions
    earn or burn standing with a group. Returns the faction's new reputation."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        fac = c.factions.get(faction_id)
        if fac is None:
            fac = Faction(id=faction_id, name=name or faction_id.replace("-", " ").replace("_", " ").title())
            c.factions[faction_id] = fac
        fac.reputation = max(-100, min(100, fac.reputation + int(delta)))
        save_campaign(c)
        return {"id": fac.id, "name": fac.name, "reputation": fac.reputation, "reason": reason}


@mcp.tool()
def record_decision(
    campaign_id: str,
    summary: str,
    options: Optional[list] = None,
    chosen: str = "",
    rationale: str = "",
    actor_ids: Optional[list] = None,
) -> dict:
    """Record a party decision so the DM and companions can call back to it later
    ('last time we trusted Grett...'). Capture the choice after a deliberation:
    `summary` (the decision), `options` (what was on the table), `chosen`, why
    (`rationale`), and who weighed in (`actor_ids`). Returns the decision id."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        d = Decision(
            day=c.day,
            summary=summary,
            options=list(options or []),
            chosen=chosen,
            rationale=rationale,
            actor_ids=list(actor_ids or []),
        )
        c.decisions.append(d)
        save_campaign(c)
        return {"id": d.id, "summary": d.summary, "chosen": d.chosen, "day": d.day}


@mcp.tool()
def xp_for_cr(cr: str) -> dict:
    """The XP value of a monster's Challenge Rating (e.g. '1/4', '5')."""
    return {"cr": cr, "xp": encounter.xp_for_cr(cr)}


@mcp.tool()
def party_xp_budget(party_levels: list[int]) -> dict:
    """Encounter XP thresholds (easy/medium/hard/deadly) for a party of these levels."""
    return encounter.xp_thresholds(party_levels)


@mcp.tool()
def encounter_difficulty(party_levels: list[int], monster_xps: list[int]) -> dict:
    """Classify an encounter (trivial/easy/medium/hard/deadly) for a party against
    the given monster XP values (applies the SRD encounter-size multiplier)."""
    return {
        "difficulty": encounter.encounter_difficulty(party_levels, monster_xps),
        "thresholds": encounter.xp_thresholds(party_levels),
    }


@mcp.tool()
def validate_adventure(adventure_id: str) -> dict:
    """Validate a bundled adventure module (content/campaigns/<id>/adventure.json)
    against the loader schema. Returns the list of problems (empty == valid)."""
    adv = content_mod.load_adventure_data(adventure_id)
    return {"adventure_id": adventure_id, "problems": generator.validate_adventure(adv)}


@mcp.tool()
def scaffold_adventure(title: str, premise: str = "", min_level: int = 1, max_level: int = 2) -> dict:
    """Return a schema-correct skeleton adventure module for the DM to fill in."""
    return generator.scaffold_adventure(title, premise, (min_level, max_level))


@mcp.tool()
def generate_campaign(
    title: str, premise: str = "", num_acts: int = 3, min_level: int = 1, max_level: int = 5
) -> dict:
    """Generate a MULTI-ACT campaign skeleton (not just a one-shot scaffold): a
    hidden antagonist, `num_acts` arcs each with hook/challenge/climax beats across
    escalating level bands, and a home-base hub connected to one site per act. The
    campaign-author fills in original prose, the NPC roster + companion, and
    CR-balanced encounters per act, then validates with validate_adventure before
    saving under content/campaigns/<id>/. Use for a full campaign rather than a
    single dungeon."""
    return generator.generate_campaign(title, premise, num_acts, (min_level, max_level))


@mcp.tool()
def get_house_rules(campaign_id: str) -> dict:
    """Return the campaign's house-rule configuration."""
    return _require(campaign_id).house_rules.model_dump()


@mcp.tool()
def set_house_rules(campaign_id: str, patch: dict) -> dict:
    """Update house rules (partial merge). Keys: difficulty, critical_max_damage,
    flanking_advantage, slow_natural_healing, feats_allowed, multiclass_allowed,
    dm_can_fudge. Unknown keys are rejected."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        data = c.house_rules.model_dump()
        _deep_update(data, patch)
        c.house_rules = HouseRules.model_validate(data)
        save_campaign(c)
        return c.house_rules.model_dump()


if __name__ == "__main__":
    mcp.run()
