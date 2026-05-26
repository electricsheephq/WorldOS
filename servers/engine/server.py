"""ClawDnD game-engine MCP server.

Authoritative D&D 5e game state — dice, character sheets, and campaign
persistence — exposed as MCP tools. Every tool reads the campaign from disk,
mutates it, and writes it back atomically (single-writer), so state survives
restarts and context compaction and is never held only in the conversation.

Combat, encounters, leveling, and spellcasting tools build on these foundations
in later epics; this server already owns dice, characters, and persistence.
"""

from __future__ import annotations

import random
import re
from typing import Optional

from mcp.server.fastmcp import FastMCP

import bestiary
import combat
import companion
import companion_banter
import companion_arc
import consequences as consequences_mod
import content as content_mod
import dice as dice_mod
import encounter
import generator
import imagegen
import inventory
import itemcatalog
import ledger as ledger_mod
import lorebook
import npc as npc_mod
import recap
import rests
import spells
import srd_tables
import director
import scene_debt as _scene_debt_mod
import travel
import wander
import worldsim
from models import (
    SKILL_ABILITIES,
    Ability,
    AbilityScores,
    ActiveEffect,
    BacklogItem,
    CampBeatCandidate,
    CampBeatRecord,
    Campaign,
    Character,
    ClassLevel,
    ClassResource,
    Combat,
    Combatant,
    CompanionArc,
    CompanionDossier,
    CompanionQuestArc,
    Condition,
    Consequence,
    DeathSaves,
    Decision,
    Faction,
    HouseRules,
    Item,
    Location,
    PendingOnHitRider,
    Quest,
    SceneDebt,
    SessionLogEntry,
    SpellSlotLevel,
    Zone,
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


_COMBAT_EVENT_SCHEMA = "clawdnd.combat_event.v1"


def _combatant_ref(ch: Character) -> dict:
    return {"id": ch.id, "name": ch.name}


def _log_session_entry(
    c: Campaign,
    *,
    kind: str,
    text: str,
    speaker: str = "",
    payload: Optional[dict] = None,
) -> SessionLogEntry:
    sid = _ensure_session(c)
    entry = SessionLogEntry(kind=kind, text=text, speaker=speaker or None, payload=payload)
    append_log(c.id, sid, entry)
    return entry


def _log_combat_event(c: Campaign, text: str, payload: dict, speaker: str = "") -> None:
    payload.setdefault("schema", _COMBAT_EVENT_SCHEMA)
    _log_session_entry(c, kind="combat", text=text, speaker=speaker, payload=payload)


def _backlog_line(item: BacklogItem) -> str:
    """The DM-facing one-liner for a fired proactive-backlog development — what the world did
    off-screen, for the DM to weave into the scene (a crier's notice, a changed face, a door now
    barred). A deterministic item carries its applied `summary`; a creative (needs_llm) item is
    only ENQUEUED for the later DM digest / world-agent, so it surfaces its authored seed
    (`note`/`title`) here — the engine still never invents prose for it."""
    return (item.summary or item.note or item.title or item.kind).strip()


def _backlog_dict(item: BacklogItem) -> dict:
    """A structured rollup of a fired proactive-backlog development (for world_tick), carrying its
    goal trace so the DM/agent sees the 'why' behind the move."""
    return {
        "id": item.id,
        "kind": item.kind,
        "goal_ref": item.goal_ref,
        "status": item.status,
        "needs_llm": item.needs_llm,
        "line": _backlog_line(item),
    }


def _combat_view(c: Campaign) -> dict:
    order = []
    for cb in c.combat.order:
        ch = c.characters.get(cb.character_id)
        entry = {
            "character_id": cb.character_id,
            "name": ch.name if ch else "?",
            "initiative": cb.initiative,
        }
        if cb.zone:  # only surface position when zones are in play (S2.7)
            entry["zone"] = cb.zone
        order.append(entry)
    view = {
        "active": c.combat.active,
        "round": c.combat.round,
        "turn_index": c.combat.turn_index,
        "current": c.combat.current_combatant_id,
        "order": order,
    }
    if c.combat.zones:  # theater-of-the-mind fights omit this entirely
        view["zones"] = [z.model_dump() for z in c.combat.zones]
    return view


def _combatant(c: Campaign, character_id: str) -> Combatant:
    """The Combatant record for a character in the active order, or raise."""
    cb = next((x for x in c.combat.order if x.character_id == character_id), None)
    if cb is None:
        raise ValueError(f"{character_id!r} is not in the combat order")
    return cb


def _move_party_to(c: Campaign, location_id: str) -> list[str]:
    """Co-locate the whole PARTY with the location the party just moved to: set
    `current_location_id` AND every party member's `location_id` to `location_id`.

    The party (`c.party`) is the PC plus any recruited companions — they travel
    together, so when the party moves they're all in the new scene. Standalone NPCs
    and monsters (not in the party) keep their own `location_id` (they stay put). This
    keeps "who's in the scene" honest: the QA state_integrity defect was companions
    left carrying a stale location_id (a burned-down tavern) after the party moved on,
    and a denouement narrated somewhere the party's pointer didn't reflect.

    Returns the ids of party members whose `location_id` was changed (for surfacing).
    Caller persists (sole-writer)."""
    c.current_location_id = location_id
    moved: list[str] = []
    for pid in c.party:
        member = c.characters.get(pid)
        if member is not None and member.location_id != location_id:
            member.location_id = location_id
            moved.append(pid)
    return moved


def _party_levels(c: Campaign) -> list[int]:
    """The total-level of every LIVING party member (PC + companion) — the input the
    encounter sizing (`encounter.py` / `wander.pick_encounter`) needs to budget a fight
    to the party. Falls back to [1] when the party is empty/all-down so sizing always
    has a non-empty list (encounter.xp_thresholds requires one)."""
    levels = [
        c.characters[i].total_level
        for i in c.party
        if i in c.characters and not c.characters[i].dead and c.characters[i].current_hp > 0
    ]
    return levels or [1]


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


def _validated_asi_choice(asi: dict) -> dict[str, int]:
    pending: dict[str, int] = {}
    total_inc = 0
    for ability, raw_inc in asi.items():
        if ability not in _AB3_TO_FULL.values():
            raise ValueError(f"unknown ability {ability!r} in asi")
        try:
            inc = int(raw_inc)
        except (TypeError, ValueError):
            raise ValueError(f"invalid ASI increment {raw_inc!r} for {ability!r}") from None
        pending[ability] = pending.get(ability, 0) + inc
        total_inc += inc
    if len(pending) > 2 or total_inc != 2 or any(inc < 1 or inc > 2 for inc in pending.values()):
        raise ValueError("asi must be +2 to one ability or +1 to two abilities")
    return pending


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


def _recompute_class_resources(ch: Character) -> None:
    """Recompute depletable class-resource pools (Rage, Ki, Lay on Hands, Channel
    Divinity, Bardic Inspiration, Sorcery Points, Second Wind, Action Surge, Wild
    Shape) from the character's class levels, preserving `used` so a level-up or
    re-derive doesn't silently refill a half-spent pool. Multiclass pools merge by
    resource id: same-id pools (e.g. Cleric + Paladin Channel Divinity) sum their
    max and take the more generous (short) recharge. Additive — a character whose
    classes grant no pools ends up with an empty dict (today's behavior)."""
    cha = ch.ability_modifier(Ability.CHA)
    derived: dict[str, dict] = {}
    for cl in ch.classes:
        for res_id, spec in srd_tables.class_resources_through(cl.name, cl.level, cha).items():
            if res_id in derived:
                derived[res_id]["max"] += spec["max"]
                if spec["recharge"] == "short":  # the more generous recharge wins
                    derived[res_id]["recharge"] = "short"
            else:
                derived[res_id] = dict(spec)
    new_res: dict[str, ClassResource] = {}
    for res_id, spec in derived.items():
        prev = ch.class_resources.get(res_id)
        used = min(prev.used, spec["max"]) if prev else 0
        new_res[res_id] = ClassResource(max=spec["max"], used=used, recharge=spec["recharge"])
    # Carry custom (DM-registered, non-SRD-table) pools forward verbatim — a level-up
    # re-derive must not wipe a Battle Master's Superiority Dice or any homebrew pool the
    # tables don't know about. A custom id never collides with a derived one (derived ids
    # are SRD class resources); if it somehow does, the SRD derivation wins.
    for res_id, res in ch.class_resources.items():
        if getattr(res, "custom", False) and res_id not in new_res:
            new_res[res_id] = res
    ch.class_resources = new_res


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
def start_world(world_id: str, start_at: str = "", resume: str = "", ending: str = "") -> dict:
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

    Pass `ending`=<ending_id> to seed the world in a specific POST-STATE rather than its
    default present — e.g. a post-campaign aftermath where a war ended one way or another
    (content/worlds/<world_id>/endings/<ending_id>.json). The overlay rewrites the era,
    folds its standing threads + history into recallable lore, and sets each named
    figure's fate. `ending="random"` picks one of the world's overlays; omitting it (the
    default) seeds the BASE world exactly as before. The result echoes the chosen
    `ending`, its `name`, and a one-line `ending_state` so you can ANNOUNCE the world's
    aftermath at the table. After start_world, call `start_character` to build the PC
    (a new nobody — never one of this era's top heroes), then start_session.

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
                "ending": prior.ending_id,
                "day": prior.day,
                "time_of_day": prior.time_of_day,
                "dm_guidance": world.get("dm_guidance", ""),
                "lore_corpus_pages": lorebook.page_count(prior.world_id),
                "current_location": {"id": ploc.id, "name": ploc.name} if ploc else None,
                "regions": [{"id": l.id, "name": l.name} for l in prior.locations.values()],
                "note": "Resumed an existing campaign. Call session_recap / get_state to re-ground, then start_session.",
            }
        # invalid/mismatched resume id -> fall through to a fresh start

    c = content_mod.seed_world(world, start_at=start_at, ending=ending)
    save_campaign(c)
    loc = c.locations.get(c.current_location_id) if c.current_location_id else None
    # Surface the chosen post-state overlay so the DM announces the world's aftermath.
    # story_seeds are the base seeds plus any the overlay appends (DM reference list).
    overlay = content_mod.load_ending_data(world_id, c.ending_id) if c.ending_id else None
    story_seeds = list(world.get("story_seeds", []) or [])
    if overlay is not None:
        # S6 audit: an overlay may REPLACE the base story_seeds (not just append) for
        # endings whose first base seed contradicts the post-state (e.g. "the contested
        # dukedom: Gortash's empty seat" under a Gortash-rules ending). `story_seeds_replace`
        # (or the alias `story_seeds`) supplants the base list; absent -> the base seeds are
        # kept and only `story_seeds_append` adds to them (today's behavior). Degrade-not-
        # abort: a present-but-non-list replace field is ignored (the base seeds stand),
        # mirroring the world_state / companion_seeds tolerance for hand-edited overlays.
        replace_raw = overlay.get("story_seeds_replace", overlay.get("story_seeds"))
        if isinstance(replace_raw, list):
            story_seeds = [str(s) for s in replace_raw if str(s).strip()]
        story_seeds = story_seeds + [
            str(s) for s in (overlay.get("story_seeds_append") or []) if str(s).strip()
        ]
    # Standing threads shown to the DM must be the PERSISTED, post-overlay threads — the base
    # world.json list can contradict a chosen ending (e.g. gortash-tyranny's "Gortash dead, the
    # Steel Watch gone"). seed_world rewrites the live threads into thread-tagged consequences;
    # project THOSE (fall back to base only if none were seeded). (#46)
    live_threads = [cq.text for cq in c.consequences if cq.thread_id]
    result = {
        "campaign_id": c.id,
        "world": c.title,
        "premise": c.summary,
        "era": c.era,
        "ending": c.ending_id,
        "tone": world.get("tone", ""),
        "dm_guidance": world.get("dm_guidance", ""),
        "lore_corpus_pages": lorebook.page_count(c.world_id),
        "standing_threads": live_threads or world.get("standing_threads", []),
        "story_seeds": story_seeds,
        "starting_at": {"id": loc.id, "name": loc.name} if loc else None,
        "starting_options": world.get("starting_options", []),
        "regions": [{"id": l.id, "name": l.name} for l in c.locations.values()],
        "factions": [{"id": f.id, "name": f.name} for f in c.factions.values()],
        "npc_roster": [
            {
                "id": ch.id,
                "name": ch.name,
                "role": ch.attitude,
                "attitude_value": ch.attitude_value,
                "voice_id": ch.voice_id,
            }
            for ch in c.characters.values()
            if ch.kind == "npc"
        ],
        "lore_count": len(c.lore),
        "map_kind": c.map_kind,
    }
    # The replayability layer (S6): echo the resolved major-quest outcomes so the DM sees
    # which way each major thread went this world-gen (ending-tied or rolled). The full map
    # plus a count + a few examples; the prose + follow-up hooks are recallable as
    # [Outcome]/[Hook] lore lines. Absent when the world ships no quest_variants ({} -> []).
    if c.quest_outcomes:
        result["quest_outcomes"] = dict(c.quest_outcomes)
        result["quest_outcomes_count"] = len(c.quest_outcomes)
        result["quest_outcomes_sample"] = [
            {"quest_id": qid, "outcome_id": oid}
            for qid, oid in list(c.quest_outcomes.items())[:4]
        ]
    # When an ending overlay seeded a post-state, echo its name + a one-line summary so
    # the DM can announce "the world you step into" at the table.
    if overlay is not None:
        result["ending_name"] = overlay.get("name", c.ending_id)
        suffix = str(overlay.get("premise_suffix") or "").strip()
        one_line = suffix.split(". ")[0].strip()
        if one_line and not one_line.endswith("."):
            one_line += "."
        result["ending_state"] = one_line or (str(overlay.get("era") or "").split(". ")[0])
        result["available_endings"] = content_mod.list_endings(world_id)
    elif content_mod.list_endings(world_id):
        # No ending chosen, but this world ships post-state overlays — advertise them so
        # the DM can offer to seed the world in a specific aftermath.
        result["available_endings"] = content_mod.list_endings(world_id)
    others = [x for x in campaigns_for_world(world_id) if x["id"] != c.id]
    if others:
        result["existing_campaigns"] = others
        result["resume_hint"] = (
            "Other campaigns already exist in this world — to CONTINUE one instead of "
            "this fresh start, call start_world(world_id, resume=<campaign_id>)."
        )
    # S7 — surface the cold-open + quest seeds at session open so the DM opens a REAL scene
    # (not mid-quest). The PRELUDE is the 4 guaranteed beats to weave (Arrival -> Meeting ->
    # Inciting Incident -> Threshold); quest_hooks are lore-derived seeds to pull. Absent when
    # the world generated none (no quest_variants/locations -> today's behavior).
    if c.prelude:
        result["prelude"] = [{"kind": b.kind, "note": b.note, "ref_id": b.ref_id} for b in c.prelude]
    if c.quest_hooks:
        result["quest_hooks_count"] = len(c.quest_hooks)
        spine = next((h for h in c.quest_hooks if h.spine), None)
        if spine is not None:
            result["spine_grievance"] = spine.grievance
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
    # F2: TPK / wipe detector — read-only. A "down" party member is dead OR at 0 HP and
    # not stabilized (i.e. dead or bleeding out). `party_down` is true only when EVERY
    # living-role member (player/companion) is down AND there is at least one such member
    # (an empty party is not a wipe). The DM reads this to RECOGNIZE the wipe moment and
    # offer all-re-roll-and-continue vs a tragic end; the engine never auto-acts on it.
    fighters_total = 0
    fighters_down = 0
    for cid in c.party:
        ch = c.characters.get(cid)
        if ch is None:
            continue
        entry = {
            "id": ch.id,
            "name": ch.name,
            "kind": ch.kind,
            "hp": f"{ch.current_hp}/{ch.max_hp}",
            "ac": ch.armor_class,
            "conditions": [x.value for x in ch.conditions],
            "voice_id": ch.voice_id,
            # F1: surface the death state per party member so a DEAD PC doesn't read as
            # alive. `dead` is the re-roll trigger (vs `dying`, which is still saveable);
            # `stable` flags a downed-but-not-dying ally. Always present (additive keys) so
            # the DM can tell "died" from "dying" without a second get_character call.
            "dead": ch.dead,
            "stable": ch.stable,
        }
        # Count toward the wipe signal only the living-role members (a monster/npc that
        # somehow sits in `party` doesn't make a TPK). "Down" = dead or at 0 HP & not stable.
        if ch.kind in ("player", "companion"):
            fighters_total += 1
            if ch.dead or (ch.current_hp <= 0 and not ch.stable):
                fighters_down += 1
        # Surface the two states a re-grounding DM most needs but the thin summary hid:
        # a DYING ally (0 HP, not dead/stabilized) with their death-save tally, and any
        # remaining class-resource pools (Rage/Ki/Channel Divinity/…) as "left/max". The
        # dashboard reads these from the full snapshot, but a DM re-grounding via get_state
        # shouldn't have to make a second call to learn an ally is bleeding out.
        if ch.current_hp <= 0 and not ch.dead and not ch.stable:
            entry["dying"] = True
            entry["death_saves"] = {
                "successes": ch.death_saves.successes,
                "failures": ch.death_saves.failures,
            }
        if ch.class_resources:
            entry["resources"] = {
                rid: f"{max(0, res.max - res.used)}/{res.max}"
                for rid, res in ch.class_resources.items()
            }
        party.append(entry)
    return {
        "id": c.id,
        "title": c.title,
        "ruleset": c.ruleset,
        "day": c.day,
        "time_of_day": c.time_of_day,
        "location": {"id": loc.id, "name": loc.name} if loc else None,
        "party": party,
        # F2: true when every player/companion in the party is down (dead or bleeding out)
        # and the party is non-empty — the read-only signal a DM uses to recognize a wipe.
        "party_down": fighters_total > 0 and fighters_down == fighters_total,
        "active_quests": [
            {"id": q.id, "title": q.title}
            for q in c.quests.values()
            if q.status == "active"
        ],
        "in_combat": c.combat.active,
        "current_turn": c.combat.current_combatant_id,
        "npc_count": sum(1 for x in c.characters.values() if x.kind == "npc"),
        "pacing_mode": c.pacing_mode,
        "leveling_mode": c.leveling_mode,
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
    # De-conflict the .md corpus against the chosen ending on the SAME basis the overlay
    # de-conflicts c.lore (recall's surface): demote/drop authored hits asserting a fact
    # the ending superseded, and frame every hit with the authoritative world-state header
    # — so recall and lookup_lore agree under a non-default ending (the two-surface fix).
    # Both args are empty for a base/no-ending campaign -> byte-identical to before.
    header = c.world_state.canon_header() if c.world_state else ""
    hits = (
        lorebook.lookup_lore(
            c.world_id, query, max(1, limit),
            supersedes=c.lore_supersedes, canon_header=header,
        )
        if c.world_id
        else []
    )
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
    make_current: bool = False,
    advance_time: bool = False,
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

    **When you're GENERATING the scene the party is walking into, pass `make_current=True`
    — that arrives them HERE in this one call** (sets current_location_id, marks the place
    visited), instead of leaving the party's location pointing at the place they just left
    while your prose describes the new room. This is the common live-gen pattern: create the
    Siltwharf Steps and step onto them in a single move. `advance_time=True` also rolls the
    clock one phase (a journey, not a step next door) and stirs a standing thread.
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
        # Live-gen arrival: the DM is generating the scene the party walks INTO, so move them
        # here in this one call (the recurring QA gap was a created-but-never-traveled-to scene
        # — current_location stuck at the previous place while the prose described the new one).
        world_beats: list[str] = []
        world_developments: list[str] = []
        arrived = False
        party_relocated: list[str] = []
        if make_current and c.current_location_id != loc.id:
            c.current_location_id = loc.id
            arrived = True
            # The party arrives together at the generated scene — co-locate every party
            # member (PC + companions) so none is left at the place they just departed
            # (QA state_integrity defect). Standalone NPCs/monsters stay put.
            party_relocated = _move_party_to(c, loc.id)
        if make_current:
            loc.visited = True  # arriving (or already here) marks it visited, like travel_to
            # The clock only rolls when we actually ARRIVED somewhere NEW (a journey) — not when
            # make_current targets the place the party is already standing in (no travel = no time
            # passes). Gating on `arrived` stops a self-target add_location from burning a phase.
            if advance_time and arrived:
                travel.advance_clock(c, 1)
                world_beats = [b.text for b in worldsim.tick(c, max_beats=1)]
                # The proactive backlog rides the same arrival time-passage (idempotent by day).
                world_developments = [_backlog_line(d) for d in worldsim.tick_backlog(c, max_events=1)]
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
        "arrived": arrived,
        "party_relocated": party_relocated,
        "visited": loc.visited,
        "day": c.day,
        "time_of_day": c.time_of_day,
        "world_beats": world_beats,
        "world_developments": world_developments,
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
        before_day = c.day
        result = travel.travel_to(c, destination_id, advance_time=advance_time)
        # The PARTY travels together: co-locate every party member (PC + companions) with
        # the new location so companions don't carry a stale location_id (QA state_integrity
        # defect). travel.travel_to already set current_location_id; this moves the members.
        moved = _move_party_to(c, destination_id)
        if moved:
            result["party_relocated"] = moved
        if advance_time:  # time passed → ONE standing thread may stir (one discrete beat, not a list)
            beats = worldsim.tick(c, max_beats=1)
            if beats:
                result["world_beats"] = [b.text for b in beats]
            # ...and the proactive backlog advances on the SAME time-passage seam (idempotent by
            # elapsed days — a non-day-rolling phase move is a no-op).
            dev = worldsim.tick_backlog(c, max_events=1)
            if dev:
                result["world_developments"] = [_backlog_line(d) for d in dev]
            result["strategic_events"] = worldsim.tick_strategic(c) if c.day > before_day else []
            # A phase elapsed (overland travel): expire timed spell effects whose
            # duration ran out (minute/round-scale die on any phase advance).
            result["expired_effects"] = _expire_clock_effects_all(c)
            # Kingmaker-style WANDERING ENCOUNTER: a time-advancing travel leg may
            # spring something. Roll for the DESTINATION's region (mirroring how
            # world_beats rides this same seam); on a hit, stage a TYPED encounter
            # (combat / skill / social / hazard / boon — most are NOT fights) under
            # `wandering_encounter`. A combat type spawns sized foes + an outlook; the DM
            # runs the encounter per its `type`. Combat never auto-fights. Skip the roll
            # if a fight is already live (parity with long_rest / roll_wandering_encounter).
            dest_loc = c.locations.get(destination_id)
            if not c.combat.active:
                staged = _stage_wandering_encounter(
                    c,
                    dest_loc.region if dest_loc is not None else "",
                    difficulty="medium",
                    location_id=destination_id,
                )
                if staged:
                    result["wandering_encounter"] = staged
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
            # Unarmored Defense is ABILITY-derived, not a flat table value: a Barbarian is
            # 10 + DEX + CON and a Monk is 10 + DEX + WIS when wearing no armor (the abilities are
            # already on the sheet at this point). The flat class_base_ac mis-set it (QA: a
            # Barbarian's AC came out 1 low). Compute it for those classes; others use the table.
            dex = ch.abilities.modifier(Ability.DEX)
            if cname == "barbarian":
                ch.armor_class = 10 + dex + ch.abilities.modifier(Ability.CON)
            elif cname == "monk":
                ch.armor_class = 10 + dex + ch.abilities.modifier(Ability.WIS)
            else:
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
        _recompute_class_resources(ch)
    except ValueError:
        pass  # unknown class -> keep the explicit values


# Class -> a minimal, internally-consistent starting kit. The ARMOR matches the AC the SRD
# default sets (Chain Mail = 16 for the heavy martials; light armor for the AC-13/14 classes)
# so AC and inventory AGREE; Unarmored Defense classes (Barbarian/Monk) and the non-armored
# casters (Wizard/Sorcerer) get NO armor by design. Generic item names — no SRD list copied.
_STARTING_ARMOR = {
    "fighter": "Chain Mail", "paladin": "Chain Mail", "cleric": "Chain Mail",
    "ranger": "Studded Leather", "rogue": "Studded Leather", "bard": "Studded Leather",
    "warlock": "Leather Armor", "druid": "Leather Armor",
}
_STARTING_WEAPON = {
    "fighter": "Longsword", "paladin": "Longsword", "cleric": "Mace", "ranger": "Longbow",
    "rogue": "Shortsword", "bard": "Rapier", "warlock": "Light Crossbow", "druid": "Quarterstaff",
    "barbarian": "Greataxe", "monk": "Quarterstaff", "wizard": "Quarterstaff", "sorcerer": "Dagger",
}


def _seed_starting_gear(ch, class_name: str) -> None:
    """Seed a minimal class-appropriate kit (the armor that justifies the AC, a primary weapon,
    a pack) AND a modest starting purse onto a freshly-built PC/companion sheet — so a new hero
    isn't standing there with an empty pack and 0 gold (QA: a level-3 rogue had inventory [] and
    no currency). Currency is seeded only when the purse is empty, gear only when inventory is
    empty (so a template / canon record that supplied its own kit is respected). Unknown/
    class-less characters get nothing."""
    cname = (class_name or "").lower()
    if cname not in _STARTING_WEAPON:
        return  # unknown / class-less -> leave the sheet as-is (today's behavior)
    cur = ch.currency
    if not (cur.cp or cur.sp or cur.ep or cur.gp or cur.pp):  # don't clobber an explicit grant
        cur.gp = 10 + 5 * max(0, ch.total_level - 1)  # a modest, level-scaled purse
    if ch.inventory:
        return  # respect gear a template / canon record already supplied
    armor = _STARTING_ARMOR.get(cname)
    if armor:
        ch.inventory.append(Item(name=armor, equipped=True, description="Starting armor."))
    ch.inventory.append(Item(name=_STARTING_WEAPON[cname], equipped=True, description="Starting weapon."))
    ch.inventory.append(Item(name="Explorer's Pack", description="Bedroll, rations, rope, torches, and the like."))


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
    met: bool = False,
) -> dict:
    """Create a character (player, companion, npc, or monster) and persist it.

    `abilities` is an optional dict like {"strength": 15, "dexterity": 14, ...}.
    If `apply_srd_defaults=True` and `class_name` is a known SRD class, the engine
    sets saving-throw proficiencies, proficiency bonus, hit dice, level-1 HP
    (max hit die + CON), spell slots, and a class-appropriate AC (only when
    armor_class is left at the unarmored default of 10) from that class; otherwise
    the explicit max_hp/armor_class are used as-is. Returns the new character id.

    `met` marks whether the PARTY has encountered this NPC — it drives the dashboard's
    Relationships view, which must not list strangers the player hasn't met. Pass
    met=True when you create an NPC the party is meeting ON-SCREEN right now (a named
    figure they walk up to and talk with); leave it False for a background/off-screen
    NPC you're only registering. Players and companions are always met. (social_check
    and recruit_companion also flip met=True automatically on first contact.)
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
            met=bool(met) or kind in ("player", "companion"),  # PC/companion are always "met"
        )
        if skills:  # explicit skill choices win over the class default-fill
            ch.skill_proficiencies = [s.lower() for s in skills if s.lower() in SKILL_ABILITIES]
        if apply_srd_defaults and class_name:
            _apply_srd_class_defaults(ch, class_name, level, set_base_ac=(armor_class == 10))
            if kind in ("player", "companion"):
                _seed_starting_gear(ch, class_name)  # gear + purse (was only seeded via start_character)
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


def _find_existing_roster_match(c, canon_name: str):
    """Find an EXISTING roster record (npc/companion) that IS this canon figure, so a
    `pickup:` promotes it in place instead of minting a duplicate (B-MED-1). start_world
    seeds e.g. `npc-minsc` "Minsc and Boo"; a later pickup:Minsc must reuse THAT record,
    not create a second Minsc as the player. Monsters/other players are never matched.
    Match (case-insensitive), most-specific first:
      1) exact name (canon "Jaheira" -> roster "Jaheira"),
      2) the roster-id convention npc-<slug> (canon "Minsc" -> id "npc-minsc", whose
         display name "Minsc and Boo" wouldn't match by name),
      3) the canon name as a leading whole word of the roster name ("Minsc and Boo")."""
    want = (canon_name or "").strip().lower()
    if not want:
        return None
    cand = [ch for ch in c.characters.values() if ch.kind in ("npc", "companion")]
    # 1) exact name
    for ch in cand:
        if ch.name.strip().lower() == want:
            return ch
    # 2) the npc-<slug> id convention (slug = canon name, spaces/punct -> hyphens)
    slug = re.sub(r"[^a-z0-9]+", "-", want).strip("-")
    for ch in cand:
        if ch.id.strip().lower() == f"npc-{slug}":
            return ch
    # 3) canon name is the leading whole word(s) of the roster display name
    for ch in cand:
        nm = ch.name.strip().lower()
        if nm == want or nm.startswith(want + " ") or nm.startswith(want + ","):
            return ch
    return None


@mcp.tool()
def start_character(
    campaign_id: str,
    origin: str = "nobody_l1",
    name: str = "",
    class_name: str = "",
    race: str = "",
    abilities: Optional[dict] = None,
    background: str = "",
    subclass: Optional[str] = None,
    skills: Optional[list] = None,
    voice_id: str = "narrator-dm",
) -> dict:
    """Build the PLAYER character via a chosen ORIGIN, and add them to the party.

    The intended flow: after start_world, present a 4-item menu, then call this with the
    player's pick. In a post-ending world the PC is always a NEW nobody — the famous
    heroes of the era are NPCs/quest-givers, never a hero the player embodies.

    `origin` selects how the PC is built:
      - "nobody_l1"  (DEFAULT) — a fresh level-1 character (a BG4-style nobody). If a
                       `class_name` is given, a real SRD level-1 sheet is filled
                       (saves, HP, proficiency, class AC/features); otherwise a blank
                       sheet the DM fleshes out. This is exactly today's create_character
                       level-1 player path.
      - "veteran_l5" — a level-5 character via the SRD class tables (a seasoned but still
                       original PC). Requires `class_name`.
      - "template:<id>" — a premade build from content/worlds/<world>/origins/<id>.json
                       (race/class/level/abilities/skills/background/subclass and flavor),
                       finished with SRD class defaults. Explicit args override the file.
      - "pickup:<canon_name>" — adopt a MINOR canon figure as the PLAYER (their real
                       race/class + canon identity), finished with SRD defaults. REJECTED
                       for a top hero (playable: false) — they appear as an NPC, not a PC.

    Returns the created PC (id, name, kind, origin, level)."""
    spec = (origin or "nobody_l1").strip()
    lower = spec.lower()
    pickup_canon_name = ""  # set for pickup: so we can promote an existing roster NPC

    # Resolve the origin into concrete build params (then funnel through one builder).
    build = {
        "name": name,
        "class_name": class_name,
        "race": race,
        "level": 1,
        "abilities": dict(abilities or {}),
        "background": background,
        "subclass": subclass,
        "skills": list(skills) if skills else None,
        "armor_class": 10,
        "appearance": "",
        "personality": "",
        "alignment": "",
        "from_canon": None,  # set for pickup: so we carry the full identity over
        "spells_known": [],
        "spells_prepared": [],
    }

    if lower in ("nobody_l1", "nobody", "l1", ""):
        resolved = "nobody_l1"
        build["level"] = 1
    elif lower in ("veteran_l5", "veteran", "l5"):
        resolved = "veteran_l5"
        build["level"] = 5
        if not build["class_name"]:
            return {"error": "origin 'veteran_l5' needs a class_name (a level-5 PC has a class)."}
    elif lower.startswith("template:"):
        tid = spec.split(":", 1)[1].strip()
        c0 = _require(campaign_id)
        tpl = content_mod.load_origin_template(c0.world_id, tid) if c0.world_id else None
        if tpl is None:
            avail = [t["id"] for t in (content_mod.list_origin_templates(c0.world_id) if c0.world_id else [])]
            return {"error": f"no origin template {tid!r} for world {c0.world_id!r}", "available": avail}
        resolved = f"template:{tpl.get('id', tid)}"
        # File supplies defaults; explicit args (passed in) win over them.
        build["name"] = name or tpl.get("name", "")
        build["class_name"] = class_name or tpl.get("class_name", "") or tpl.get("class", "")
        build["race"] = race or tpl.get("race", "")
        build["level"] = max(1, int(tpl.get("level", 1) or 1))
        build["abilities"] = dict(abilities) if abilities else dict(tpl.get("abilities", {}) or {})
        build["background"] = background or tpl.get("background", "")
        build["subclass"] = subclass or tpl.get("subclass")
        build["skills"] = (list(skills) if skills else None) or tpl.get("skills")
        build["armor_class"] = int(tpl.get("armor_class", 10) or 10)
        build["appearance"] = tpl.get("appearance", "")
        build["personality"] = tpl.get("personality", "")
        build["alignment"] = tpl.get("alignment", "")
        build["spells_known"] = list(tpl.get("spells_known", []) or [])
        build["spells_prepared"] = list(tpl.get("spells_prepared", []) or [])
    elif lower.startswith("pickup:"):
        who = spec.split(":", 1)[1].strip()
        c0 = _require(campaign_id)
        rec = content_mod.load_canon_character(c0.world_id, who) if c0.world_id else None
        if rec is None:
            avail = [x["name"] for x in (content_mod.list_canon_characters(c0.world_id, playable_only=True) if c0.world_id else [])]
            return {"error": f"no canon character {who!r} for world {c0.world_id!r}", "playable": avail}
        if not content_mod.is_playable(rec):
            return {
                "error": (
                    f"{rec.get('name', who)!r} is a legend of this era — they appear as an "
                    f"NPC/quest-giver, not a hero you play. Pick a minor figure, or use "
                    f"load_canon_character to encounter them in the world."
                ),
                "playable": False,
                "playable_options": [x["name"] for x in (content_mod.list_canon_characters(c0.world_id, playable_only=True) if c0.world_id else [])],
            }
        resolved = f"pickup:{rec.get('name', who)}"
        pickup_canon_name = str(rec.get("name", who) or who)  # match the roster by canon identity
        build["name"] = name or rec.get("name", who)
        build["class_name"] = class_name or str(rec.get("class", "") or "")
        build["race"] = race or rec.get("race", "")
        try:
            build["level"] = max(1, int(rec.get("level") or 1))
        except (TypeError, ValueError):
            build["level"] = 1
        build["appearance"] = rec.get("appearance", "")
        build["personality"] = rec.get("personality", "")
        build["alignment"] = rec.get("alignment", "")
        build["from_canon"] = rec
    else:
        return {
            "error": (
                f"unknown origin {origin!r}. Use 'nobody_l1', 'veteran_l5', "
                f"'template:<id>', or 'pickup:<canon_name>'."
            )
        }

    if not build["name"]:
        return {"error": "a PC needs a name — pass name=… (or pick an origin that supplies one)."}

    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        scores = AbilityScores(**(build["abilities"] or {}))
        cn = build["class_name"]
        lvl = int(build["level"])
        rec = build["from_canon"]

        # B-MED-1: start_world seeds the canon figure as a roster NPC (e.g. npc-minsc).
        # A pickup: of that same figure must PROMOTE the existing record to the player —
        # not mint a second one. Mirror recruit_companion's promote-in-place (flip kind,
        # add to party, apply the SRD sheet) so the world keeps exactly one of them.
        existing = _find_existing_roster_match(c, pickup_canon_name) if pickup_canon_name else None
        if existing is not None:
            ch = existing
            ch.kind = "player"  # type: ignore[assignment]
            if build["abilities"]:
                ch.abilities = scores
                ch.initiative_bonus = scores.modifier(Ability.DEX)
            if cn:
                ch.classes = [ClassLevel(name=cn.capitalize(), level=lvl, subclass=build["subclass"])]
            ch.voice_id = voice_id
            # Carry the canon identity (only fill blanks so a hand-set roster value wins).
            for attr, key in (("race", "race"), ("alignment", "alignment"),
                              ("appearance", "appearance"), ("personality", "personality"),
                              ("background", "background")):
                val = build.get(key) or (rec.get(key, "") if rec else "")
                if val and not getattr(ch, attr, ""):
                    setattr(ch, attr, val)
            if rec is not None:
                ch.mannerisms = ch.mannerisms or rec.get("mannerisms", "")
                ch.backstory = ch.backstory or rec.get("backstory", "")
                ch.notes = ch.notes or rec.get("voice_hint", "")
            if build["skills"]:
                ch.skill_proficiencies = [s.lower() for s in build["skills"] if s.lower() in SKILL_ABILITIES]
            if cn:
                _apply_srd_class_defaults(ch, cn, lvl, set_base_ac=(int(build["armor_class"]) == 10))
                _seed_starting_gear(ch, cn)  # AC/inventory consistency (no-op if gear already present)
            if ch.id not in c.party:
                c.party.append(ch.id)
            save_campaign(c)
            return {
                "id": ch.id,
                "name": ch.name,
                "kind": ch.kind,
                "origin": resolved,
                "race": ch.race,
                "class": cn,
                "level": ch.total_level,
                "in_party": ch.id in c.party,
                "promoted_existing": True,  # reused the roster record (no duplicate minted)
                "combat_numbers": _combat_numbers(ch),  # authoritative to-hit/damage — don't invent
            }

        ch = Character(
            name=build["name"],
            kind="player",
            race=build["race"],
            background=build["background"],
            alignment=build["alignment"],
            appearance=build["appearance"],
            personality=build["personality"],
            voice_id=voice_id,
            classes=[ClassLevel(name=cn.capitalize(), level=lvl, subclass=build["subclass"])] if cn else [],
            abilities=scores,
            armor_class=int(build["armor_class"]),
            initiative_bonus=scores.modifier(Ability.DEX),
            location_id=c.current_location_id,  # the PC starts where the session opens (QA: was null)
        )
        # A canon pickup carries the rest of its identity for the DM to voice from.
        if rec is not None:
            ch.mannerisms = rec.get("mannerisms", "")
            ch.backstory = rec.get("backstory", "")
            ch.notes = rec.get("voice_hint", "")
        if build["skills"]:  # explicit/template skill choices win over the class default-fill
            ch.skill_proficiencies = [s.lower() for s in build["skills"] if s.lower() in SKILL_ABILITIES]
        # Fill a real SRD sheet whenever a class is known (every origin but a class-less
        # nobody_l1). set_base_ac only when AC is the unarmored default, mirroring
        # create_character so an explicit/template AC is preserved.
        if cn:
            _apply_srd_class_defaults(ch, cn, lvl, set_base_ac=(int(build["armor_class"]) == 10))
            _seed_starting_gear(ch, cn)  # AC/inventory consistency (no-op if gear already present)
        # Apply template-supplied spellbooks (additive: empty list == today's behavior).
        if build.get("spells_known"):
            ch.spells_known = list(build["spells_known"])
        if build.get("spells_prepared"):
            ch.spells_prepared = list(build["spells_prepared"])
        c.characters[ch.id] = ch
        if ch.id not in c.party:
            c.party.append(ch.id)
        save_campaign(c)
    return {
        "id": ch.id,
        "name": ch.name,
        "kind": ch.kind,
        "origin": resolved,
        "race": ch.race,
        "class": cn,
        "level": ch.total_level,
        "in_party": ch.id in c.party,
        "combat_numbers": _combat_numbers(ch),  # authoritative to-hit/damage — don't invent
    }


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
        # Recruiting fleshes out a real combat sheet — so a candidate who was flagged dead while
        # still a bare identity STUB (the load_canon_character stub spawns at max_hp=1, and one hit
        # in combat trips the SRD massive-damage instant-death rule) must NOT stay dead once they
        # have living HP. QA found a recruited companion stuck dead=true: inert, unable to act, and
        # long_rest raised "cannot rest while dead". An alive-HP companion is, by definition, alive —
        # clear the death state (dead/stable + death saves). Guarded on current_hp>0 so this can
        # never silently resurrect a 0-HP record.
        if ch.current_hp > 0 and (ch.dead or ch.stable or ch.death_saves.successes or ch.death_saves.failures):
            ch.dead = False
            ch.stable = False
            ch.death_saves = DeathSaves()
            ch.conditions = [cond for cond in ch.conditions if cond != Condition.UNCONSCIOUS]
        # A companion needs an ARC for the relationship system (camp_scene / check_companion_arc)
        # to have anything to track — QA found a freshly-recruited canon companion with arc=null,
        # so camp + the gates were inert. If none was seeded (i.e. not an ending-tied
        # companion_seed), give a light DEFAULT: one loyalty gate at a moderate approval, so the
        # bond can deepen at camp. Guarded on None, so an ending-seeded arc is never overwritten;
        # the DM can set_companion_arc to author a richer, character-specific arc.
        if ch.arc is None:
            ch.arc = CompanionArc.model_validate({"arc_gates": [
                {"kind": "loyalty", "threshold": 25,
                 "note": f"a deepening trust with {ch.name}, earned fighting beside them"}]})
        # A companion also needs a DOSSIER for the living-world systems (camp scheduling,
        # banter selection, approval causes) to have operational state to act on (#68). If
        # none was seeded (i.e. not an ending/roster/canon dossier), synthesize a MINIMAL
        # one from what the record ALREADY carries — the personality/backstory hint and the
        # memory facts become terse camp prompts, so a freshly-recruited companion isn't a
        # blank slate at camp. Guarded on None so a seeded dossier is NEVER overwritten; the
        # DM can flesh it out via update_character. We don't invent wants/values/approval
        # causes the record doesn't imply — the DM authors those as the bond develops.
        if ch.companion_dossier is None:
            seed_hint = (ch.backstory or ch.personality or "").strip()
            camp_prompts: list[str] = []
            if seed_hint:
                # one short clause, not the whole biography — keep the dossier operational
                clause = seed_hint.replace("\n", " ").split(". ")[0].strip()
                if clause:
                    camp_prompts.append(clause[:200])
            # the NPC's seeded hook / remembered facts make good, character-specific camp talk
            camp_prompts.extend(m.strip() for m in ch.memory if m.strip())
            ch.companion_dossier = CompanionDossier(camp_prompts=camp_prompts[:4])
        ch.met = True  # joining the party means the party has met them
        if ch.id not in c.party:
            c.party.append(ch.id)
        save_campaign(c)
        return {"id": ch.id, "name": ch.name, "kind": ch.kind, "party": list(c.party),
                "arc_seeded": ch.arc is not None,
                "dossier_seeded": ch.companion_dossier is not None}


@mcp.tool()
def reroll_character(
    campaign_id: str,
    dead_id: str,
    name: str,
    class_name: str = "",
    race: str = "",
    abilities: Optional[dict] = None,
    background: str = "",
    subclass: Optional[str] = None,
    skills: Optional[list] = None,
    voice_id: str = "narrator-dm",
    level: Optional[int] = None,
) -> dict:
    """Re-roll a NEW player character after a PC dies, and continue the same quest — the
    D&D-table answer to "no save states". Death is one-way (the engine never resurrects
    the fallen); this is *forward* motion: a new hero, at the dead PC's level, joins the
    ongoing campaign. The world-state — quests, day, locations, lore, factions, surviving
    companions and their memories — is untouched (it lives on the Campaign, not the PC).

    Atomic swap under the campaign lock:
      1. Validate `dead_id` is a player/companion record that is actually `dead` (re-roll
         is for death, not a live swap — refuses a living target).
      2. Build the new PC at the dead PC's `total_level` by default (pass `level` to
         override), `kind="player"`, with a full SRD sheet when `class_name` is a known
         class (saves/HP/AC/features), else a blank sheet the DM fleshes out.
      3. DEMOTE the corpse OFF `kind=="player"` → `kind="npc"`, and REMOVE it from the
         party. This is the keystone: the player facade resolves the active PC by
         `kind=="player"` (party-order-first, no dead-filter), so the corpse MUST be
         demoted or the facade keeps handing moves to it and the new PC can't act. The
         record is KEPT in `characters` (a memorial — the fallen hero the world remembers).
      4. Add the new PC to the party as the sole `kind=="player"`.

    Gear and gold are LOST with the body by default — a new character earns their own kit
    (use `add_item`/`adjust_currency` if the fiction lets the party loot the corpse). HOW
    the new hero arrives in the world is the DM's narration, not this tool's job. On a
    party WIPE, call this once per fallen member (there is no separate batch tool).

    Returns ``{new_pc: {id, name, kind, level, in_party}, memorial: {id, name, now_kind}}``.
    """
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        dead = _char(c, dead_id)  # raises if the id isn't in the campaign
        # Guard: re-roll is the answer to DEATH, not a swap of a living PC. A character is
        # only re-rollable once it has actually died (combat marks `dead=True` on 3 failed
        # death saves / massive damage / a killing blow while down).
        if not dead.dead:
            raise ValueError(
                f"{dead_id!r} ({dead.name!r}) is not dead — re-roll is for a fallen "
                f"character, not swapping a living one. Heal/stabilize them instead, or "
                f"use create_character for a brand-new party member."
            )
        if dead.kind not in ("player", "companion"):
            raise ValueError(
                f"{dead_id!r} is a {dead.kind!r}; only a fallen player or companion can be "
                f"re-rolled. (Monsters/NPCs die outright and are not party members.)"
            )
        lvl = level if level is not None else dead.total_level

        # Build the new PC at the dead PC's level (mirrors create_character's core). A known
        # class gets a full SRD sheet via _apply_srd_class_defaults (HP/saves/AC/features at
        # `lvl`); no class -> a blank level-N sheet the DM fleshes out (like `nobody`).
        scores = AbilityScores(**(abilities or {}))
        new = Character(
            name=name,
            kind="player",
            race=race,
            background=background,
            voice_id=voice_id,
            classes=[ClassLevel(name=class_name.capitalize(), level=lvl, subclass=subclass)]
            if class_name
            else [],
            abilities=scores,
            initiative_bonus=scores.modifier(Ability.DEX),
        )
        if skills:  # explicit skill choices win over the class default-fill
            new.skill_proficiencies = [s.lower() for s in skills if s.lower() in SKILL_ABILITIES]
        if class_name:
            _apply_srd_class_defaults(new, class_name, lvl, set_base_ac=True)

        # KEYSTONE — demote the corpse off kind=="player" so the facade stops resolving it,
        # and remove it from the party. Keep the record in `characters` as a memorial (it
        # stays dead=True, anchored where it fell — the ledger/decisions may reference it,
        # and the world remembers the fallen hero). Gear/gold stay ON the corpse (lost with
        # the body) — we deliberately do NOT transfer inventory or currency to the new PC.
        dead.kind = "npc"  # type: ignore[assignment]
        if dead.location_id is None:  # anchor the fallen one where the party currently is
            dead.location_id = c.current_location_id
        c.party = [pid for pid in c.party if pid != dead_id]

        # Add the new PC last, so it is the ONLY kind=="player" the facade can resolve
        # (clean regardless of party order).
        c.characters[new.id] = new
        c.party.append(new.id)
        save_campaign(c)
        return {
            "new_pc": {
                "id": new.id,
                "name": new.name,
                "kind": new.kind,
                "level": new.total_level,
                "in_party": new.id in c.party,
            },
            "memorial": {"id": dead.id, "name": dead.name, "now_kind": dead.kind},
        }


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
        # Offer the best recovery hints: substring matches first, else token-prefix near-misses,
        # else a few common low-CR humanoid foes that DO exist (so the DM never dead-ends on a miss).
        sugg = bestiary.find(name) or bestiary._token_prefix_matches(name)
        if not sugg:
            sugg = [s for s in ("Bandit", "Guard", "Cultist", "Tough", "Scout") if bestiary.resolve(s)]
        return {"error": f"no creature named {name!r} in the bestiary", "suggestions": sugg}
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
def list_bestiary(query: str = "", limit: int = 20, player_safe: bool = True) -> dict:
    """Read-only bestiary/codex lookup.

    Defaults to the player-safe projection: identity, type/size/CR, action names,
    and authored-content license/source/provenance metadata only. It never mutates
    bestiary packs, campaign state, or combat state. Pass ``player_safe=False`` only
    for a DM-side preview that may include mechanical stat blocks.
    """
    n = max(1, min(int(limit), 50))
    if player_safe:
        return bestiary.player_bestiary(query, n)
    names = bestiary.find(query, n)
    return {
        "items": [sb for name in names if (sb := bestiary.stat_block(name)) is not None],
        "validation_errors": bestiary.authored_validation_errors(),
    }


def _spawn_creature_chars(c: Campaign, canonical: str, count: int, location_id) -> list[dict]:
    """Add `count` combat-ready monster Characters for a canonical bestiary name to the
    campaign (mutates; caller holds the lock + saves). Mirrors `spawn_monster`'s
    construction EXACTLY — same stat-block flattening, abilities, AC/HP, resistances,
    actions-on-notes, and `xp_value` — but anchors each spawn at `location_id` (the
    scene the wandering encounter erupts in) so the local cast shows it, and returns
    nothing on an unresolvable name (defensive; the picker only hands us resolvable
    names). Returns `[{"id","name"}]` for the spawned foes."""
    sb = bestiary.stat_block(canonical)
    if sb is None:
        return []
    n = max(1, min(int(count), 20))
    scores = AbilityScores(**{_SHORT_TO_FULL_AB[k]: v for k, v in sb["abilities"].items()})
    actions_note = " | ".join(f"{a['name']}: {a['desc']}" for a in sb["actions"][:10])
    summary = f"CR {sb['cr']}, {sb['xp']} XP. {sb['size']} {sb['type']}. Actions: {actions_note}"
    spawned: list[dict] = []
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
            location_id=location_id,
        )
        c.characters[ch.id] = ch
        spawned.append({"id": ch.id, "name": ch.name})
    return spawned


def _stage_wandering_encounter(
    c: Campaign,
    region: str,
    *,
    difficulty: str = "medium",
    modifiers: dict | None = None,
    location_id=None,
    force: bool = False,
    rng: random.Random | None = None,
) -> Optional[dict]:
    """Roll + (on a hit) STAGE a Kingmaker-style wandering encounter (mutates; caller
    holds the lock + saves). Composes the pure `wander` module with the existing spawn
    path. As of the typed-encounter wave a wandering encounter is no longer ALWAYS a
    fight — `wander.pick_typed_encounter` picks a TYPE (combat / skill / social /
    hazard / boon; most of which are NOT combat), so travel/camp feels VARIED:

      1. unless `force`, roll `wander.roll_encounter(region, modifiers)` — a miss (or
         the `house_rules.wandering_encounters` flag being off) returns None and leaves
         the campaign untouched (today's behavior);
      2. on a hit, `wander.pick_typed_encounter` picks the TYPE + its fields, sized to
         the LIVING party's XP budget (combat) / DC-banded off house difficulty
         (skill/social/hazard);
      3. for a COMBAT pick: spawn the foes as monster Characters via
         `_spawn_creature_chars`, anchored at `location_id` (defaults to the party's
         current location) so they're already in the campaign, ready to fight, AND fold
         in the SRD over-match `outlook` (the same math as `encounter_outlook` — band,
         overmatch_ratio, must_offer_out, guidance) so the DM gets the must-offer-an-out
         signal automatically. For a NON-combat pick: spawn NOTHING, return the typed
         descriptor for the DM to run (skill_check / social_check / a save / narrate);
      4. return the **`wandering_encounter`** payload (mirroring `world_beats`). It now
         ALWAYS carries `type` + type-specific fields; combat additionally carries
         `foes`/`difficulty`/`surprise`/`encounter_xp` + `outlook`. `surprise` is a coin
         flip the DM HONORS. Combat is NOT auto-started — the DM narrates + calls
         `start_combat` on the staged foe ids (and surfaces a cost-bearing OUT when
         `outlook.must_offer_out`).

    Returns None when nothing was staged (flag off, roll missed) so the seam simply
    omits the key. A combat pick that can't spawn / can't size degrades to a boon
    inside `pick_typed_encounter`, so a hit always yields SOME staged encounter."""
    if not force and not c.house_rules.wandering_encounters:
        return None
    region = region or ""
    if not force and not wander.roll_encounter(region, modifiers, rng=rng):
        return None
    levels = _party_levels(c)
    r = rng or random.Random()
    picked = wander.pick_typed_encounter(
        levels,
        region,
        rng=r,
        target_difficulty=difficulty,
        house_difficulty=c.house_rules.difficulty,
    )
    etype = picked.get("type", "combat")
    base = {"staged": True, "type": etype, "region": region}

    if etype != "combat":
        # skill / social / hazard / boon — no foes spawned; hand the DM the descriptor.
        descriptor = {k: v for k, v in picked.items() if k != "type"}
        return {**base, **descriptor}

    # combat — spawn the foes AND fold in the over-match outlook.
    where = location_id if location_id is not None else c.current_location_id
    foes: list[dict] = []
    foe_xps: list[int] = []
    encounter_xp = 0
    for spec in picked.get("foes", []):
        ids = _spawn_creature_chars(c, spec["name"], spec["count"], where)
        if not ids:
            continue
        xp_each = int(spec.get("xp_each") or 0)
        encounter_xp += xp_each * len(ids)
        foe_xps.extend([xp_each] * len(ids))
        foes.append(
            {
                "name": spec["name"],
                "count": len(ids),
                "cr": spec.get("cr", ""),
                "xp_each": spec.get("xp_each", 0),
                "ids": [s["id"] for s in ids],
            }
        )
    if not foes:
        return None  # everything failed to spawn -> treat as no encounter (no half-staged state)
    return {
        **base,
        "difficulty": difficulty,
        "surprise": r.random() < 0.5,
        "foes": foes,
        "encounter_xp": encounter_xp,
        # the SAME over-match math the DM would get from encounter_outlook, computed off
        # the staged foes' XP — so the must-offer-an-out signal rides along automatically
        # (the Wave-12 fix: no new tool to remember).
        "outlook": _outlook_for_xps(levels, foe_xps),
    }


def _class_resources_view(ch: Character) -> dict:
    """fables-style resource bars: {resource_id: {"remaining", "max", "used",
    "recharge", "label"}} — what the play-view renders as e.g. Lay on Hands 15/15.
    Empty when the character has no pools."""
    return {
        rid: {
            "remaining": res.max - res.used,
            "max": res.max,
            "used": res.used,
            "recharge": res.recharge,
            "size": getattr(res, "size", ""),
            "label": f"{res.max - res.used}/{res.max}"
            + (f" {res.size}" if getattr(res, "size", "") else ""),
        }
        for rid, res in ch.class_resources.items()
    }


def _combat_numbers(ch: Character) -> dict:
    """The sheet-derived attack/save numbers the DM must pass to `attack` — surfaced so the
    DM reads AUTHORITATIVE values instead of inventing them (QA: a Rogue's to-hit was narrated
    as +7 by copying another combatant when the sheet gave +3). `attack` trusts the bonus you
    hand it, so the correct number has to be visible at the point of attack. Melee uses STR,
    ranged/finesse uses DEX; damage modifiers are the same ability mod."""
    prof = ch.proficiency_bonus
    str_mod = ch.ability_modifier(Ability.STR)
    dex_mod = ch.ability_modifier(Ability.DEX)
    return {
        "proficiency_bonus": prof,
        "ability_mods": {a.value: ch.ability_modifier(a) for a in Ability},
        "melee_attack_bonus": prof + str_mod,        # STR weapon
        "ranged_attack_bonus": prof + dex_mod,        # DEX / finesse weapon
        "melee_damage_mod": str_mod,
        "ranged_damage_mod": dex_mod,
        "note": "Pass these to attack(attack_bonus=…, damage_dice='NdM+<mod>'); the engine "
                "trusts the number, so use the sheet's — never copy another combatant's.",
    }


@mcp.tool()
def get_character(campaign_id: str, character_id: str) -> dict:
    """Return a character's full sheet, including depletable class-resource pools
    (Rage, Ki, Lay on Hands, Channel Divinity, …) under `class_resources` plus a
    `class_resources_view` with fables-style remaining/max bars, and a `combat_numbers`
    block (sheet-derived attack/damage bonuses) so the DM never hand-invents a to-hit."""
    c = _require(campaign_id)
    ch = c.characters.get(character_id)
    if ch is None:
        raise ValueError(f"no character {character_id!r} in campaign")
    sheet = ch.model_dump(mode="json")
    sheet["class_resources_view"] = _class_resources_view(ch)
    sheet["combat_numbers"] = _combat_numbers(ch)
    return sheet


@mcp.tool()
def list_canon_characters(campaign_id: str, playable_only: bool = False) -> dict:
    """Who's available to pull into THIS world from the ingested canon roster (the
    post-BG3 cast — Shadowheart, Astarion, Gale, …). Returns {name, race, class,
    playable, role} each. Use load_canon_character to bring one in as an NPC/companion.

    The top heroes of the era (the BG3 origin companions) are `playable: false` — they
    remain legends/quest-givers/encounterable NPCs but are NOT a hero the player embodies.
    Pass `playable_only=True` for just the minor figures a player may pick up as their PC
    via start_character(origin="pickup:<name>")."""
    c = _require(campaign_id)
    return {
        "world_id": c.world_id,
        "available": content_mod.list_canon_characters(c.world_id, playable_only=playable_only) if c.world_id else [],
    }


@mcp.tool()
def load_canon_character(campaign_id: str, name: str, kind: str = "npc", add_to_party: bool = False) -> dict:
    """Pull a CANON character (e.g. Shadowheart, Astarion, Gale) from the world's ingested
    roster into this campaign — with their real identity: race, class, and the
    appearance / personality / mannerisms / backstory the DM voices from. Makes the
    post-BG3 cast encounterable instead of re-invented. `kind`="npc" (default) or
    "companion"; `add_to_party` brings a companion along. For a full COMBAT sheet, follow
    with apply_srd_defaults / recruit_companion. Refuses a duplicate name."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        rec = content_mod.load_canon_character(c.world_id, name) if c.world_id else None
        if rec is None:
            avail = [x["name"] for x in (content_mod.list_canon_characters(c.world_id) if c.world_id else [])]
            return {"error": f"no canon character {name!r} for world {c.world_id!r}", "available": avail}
        canonical = rec.get("name", name)
        dup = next((ch for ch in c.characters.values()
                    if ch.name.strip().lower() == canonical.strip().lower()), None)
        if dup is not None:
            # Already present (commonly: the cold-open PRELUDE seeds the companion NPC, then the
            # DM tries to load them) — return a SUCCESS-shaped response, not a hard error, so the
            # DM proceeds straight to recruit_companion without an error-handling detour. (QA: the
            # error path forced a needless two-step + read as a failure.)
            return {
                "already_present": True,
                "id": dup.id, "name": dup.name, "kind": dup.kind,
                "in_party": dup.id in c.party,
                "note": (f"{canonical} is already in this campaign — recruit_companion({dup.id!r}) "
                         f"to bring them into the party, or update_character to flesh them out."),
            }
        classes = []
        if rec.get("class"):
            try:
                lvl = max(1, int(rec.get("level") or 1))
            except (TypeError, ValueError):
                lvl = 1
            classes = [ClassLevel(name=str(rec["class"]), level=lvl)]
        ch = Character(
            name=canonical,
            kind=kind if kind in ("npc", "companion") else "npc",
            race=rec.get("race", ""),
            classes=classes,
            alignment=rec.get("alignment", ""),
            personality=rec.get("personality", ""),
            appearance=rec.get("appearance", ""),
            mannerisms=rec.get("mannerisms", ""),
            backstory=rec.get("backstory", ""),
            notes=rec.get("voice_hint", ""),
            location_id=c.current_location_id,
        )
        # ADDITIVE (#68): a canon record may carry a structured companion dossier — the
        # operational identity (wound/wants/values/banter/approval causes/relationships) the
        # living-world systems act on, kept out of the long appearance/backstory prose. Pulled
        # ONLY from an explicit `companion_dossier`/`dossier` block (NOT the canon `relationships`
        # field, which is a different list-shaped notion). A malformed block DEGRADES to no
        # dossier — the canon load never fails on a hand-edit typo. Absent -> None (today's behavior).
        ch.companion_dossier = content_mod._coerce_dossier(
            rec.get("companion_dossier", rec.get("dossier")),
            where=f"canon character {canonical!r}",
        )
        # The Character default HP is a placeholder max_hp=1 (the model's bare default). An identity
        # stub left at 1 HP is an INSTANT-KILL combatant: the first hit trips combat's SRD massive-
        # damage rule (damage >= max_hp at 0 HP) and flags it dead before it's ever fleshed out
        # (QA: a canon NPC pulled into a fight pre-recruit died in one hit, then stayed dead). Give
        # the stub a SANE floor so a fresh canon figure can take a swing while awaiting its real
        # sheet: honor a canon HP hint if the record carries one, else a modest default. (The full
        # combat sheet still comes from apply_srd_defaults / recruit_companion.)
        try:
            canon_hp = int(rec.get("max_hp") or rec.get("hit_points") or 0)
        except (TypeError, ValueError):
            canon_hp = 0
        ch.max_hp = max(canon_hp, 10)
        ch.current_hp = ch.max_hp  # a fresh identity stub stands at full (placeholder) health
        if add_to_party:
            ch.met = True  # brought into the party => met
            if ch.id not in c.party:
                c.party.append(ch.id)
        c.characters[ch.id] = ch
        save_campaign(c)
        return {
            "id": ch.id, "name": ch.name, "race": ch.race, "kind": ch.kind,
            "class": rec.get("class", ""), "source": rec.get("source_url", ""),
            "in_party": ch.id in c.party,
            "note": "Identity loaded. For a full combat sheet, call apply_srd_defaults or recruit_companion.",
        }


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
        data["id"] = character_id  # identity is IMMUTABLE: the stored model's id must equal its
        # dict key, so a patch can't strand the character under a visible-but-unusable id (#41).
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
        # Enforce bestiary/sheet condition immunities (stored as condition-name strings) — a
        # creature immune to 'poisoned' can't be given the poisoned condition. No-op + immune flag.
        if cond.value in {i.strip().lower() for i in ch.condition_immunities}:
            return {**ch.model_dump(mode="json"), "immune": True, "added": False}
        added = cond not in ch.conditions
        if added:
            ch.conditions.append(cond)
        if cond in combat.INCAPACITATING:
            ch.concentration = None  # SRD: incapacitation breaks concentration
            combat.expire_concentration_effects(ch)  # ...and its engine-tracked effect
        save_campaign(c)
        return {**ch.model_dump(mode="json"), "immune": False, "added": added}


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
        was_down = ch.current_hp == 0  # remember the prior 0-state for the wake transition
        ch.current_hp = current_hp
        if temp_hp is not None:
            ch.temp_hp = temp_hp
        # Re-validate so the clamp invariants apply (current_hp floored to 0..max_hp).
        ch = Character.model_validate(ch.model_dump(mode="json"))
        # Mirror the combat path's 0-HP semantics (one source of truth): a manual set TO 0 clears
        # concentration + its twin effect and downs the character (unconscious + dying/death-saves,
        # or death for monsters/NPCs); a set FROM 0 to >0 wakes them. Run on the CLAMPED object so
        # a negative input that clamps to 0 still triggers the downed transition.
        combat.apply_hp_set_transition(ch, was_down)
        c.characters[character_id] = ch
        kx = _award_kill_xp(c, ch)
        out = c.characters[character_id].model_dump(mode="json")
        if kx:
            out["kill_xp"] = kx
        save_campaign(c)
        return out


def _parse_multiattack_count(desc: str) -> int:
    """Return the total number of attack rolls a monster makes when it uses Multiattack.

    Handles the three SRD wording patterns:
    - "makes two X attacks"              → 2
    - "makes one X attack and one Y attack" → 1+1 = 2
    - "makes one Ram attack, one Bite attack, and one Claw attack" → 3
    Counts every (number + 'attack/attacks') clause, summing them.
    Falls back to the first number word in the desc when no clause matches,
    and ultimately defaults to 1 (conservative).
    """
    import re as _re
    _WORD_TO_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    # Split on 'and' and ',' to get individual attack clauses
    parts = _re.split(r"\band\b|\,", desc, flags=_re.IGNORECASE)
    total = 0
    for part in parts:
        m = _re.search(r"\b(one|two|three|four|five|six|\d+)\b", part, _re.IGNORECASE)
        if m and _re.search(r"\battacks?\b", part, _re.IGNORECASE):
            tok = m.group(1).lower()
            total += int(tok) if tok.isdigit() else _WORD_TO_NUM.get(tok, 1)
    if total > 0:
        return total
    # Fallback: first number word anywhere in the desc
    m2 = _re.search(r"\b(one|two|three|four|five|six|\d+)\b", desc, _re.IGNORECASE)
    if m2:
        tok = m2.group(1).lower()
        return int(tok) if tok.isdigit() else _WORD_TO_NUM.get(tok, 1)
    return 1


def _parse_attack_action(action: dict) -> dict | None:
    """Parse a monster attack action dict (from bestiary.stat_block) into
    {name, to_hit, damage} with authoritative numeric to_hit pulled from the
    SRD desc ('Melee/Ranged Attack Roll: +5').  Returns None for non-attack
    actions (no 'Attack Roll' in desc and action_type != 'ACTION').
    """
    import re as _re
    if action.get("action_type") != "ACTION":
        return None
    desc = action.get("desc", "")
    hit_m = _re.search(r"Attack Roll:\s*([+-]\d+)", desc, _re.IGNORECASE)
    if hit_m is None:
        return None  # not an attack action (e.g. Parry, Spellcasting)
    to_hit = int(hit_m.group(1))
    dmg_m = _re.search(r"(\d+d\d+(?:\s*[+-]\s*\d+)?)", desc, _re.IGNORECASE)
    damage = dmg_m.group(1).replace(" ", "") if dmg_m else ""
    return {"name": action["name"], "to_hit": to_hit, "damage": damage}


def _monster_combat_entry(ch: "Character", c: "Campaign") -> dict | None:
    """Build a monster_combat entry for a combatant: Multiattack count + attack list.
    Returns None if the character is not a monster or has no bestiary data available.

    Source of truth: the bestiary stat block identified by the monster's base name
    (strip trailing ' N' numbering). If no stat block resolves, falls back to the
    character's own ability scores + proficiency (mirrors _combat_numbers derivation).
    """
    if ch.kind != "monster":
        return None
    # Resolve canonical bestiary name: strip trailing ' N' (e.g. 'Bandit Captain 2')
    import re as _re
    base_name = _re.sub(r"\s+\d+$", "", ch.name).strip()
    sb = bestiary.stat_block(base_name)
    if sb is None:
        canonical = bestiary.resolve(base_name)
        sb = bestiary.stat_block(canonical) if canonical else None
    if sb is None:
        return None
    actions = sb.get("actions", [])
    # Determine attacks_per_turn from Multiattack action
    attacks_per_turn = 1
    for act in actions:
        if act["name"].lower() == "multiattack":
            attacks_per_turn = _parse_multiattack_count(act.get("desc", ""))
            break
    # Collect authoritative attack actions (have 'Attack Roll' in desc)
    attack_list = []
    for act in actions:
        parsed = _parse_attack_action(act)
        if parsed is not None and act["name"].lower() != "multiattack":
            attack_list.append(parsed)
    return {
        "id": ch.id,
        "name": ch.name,
        "attacks_per_turn": attacks_per_turn,
        "attacks": attack_list,
        "note": (
            f"Run {attacks_per_turn} attack call(s) per Attack action using the surfaced "
            "to_hit/damage — never invent bonuses (mirrors PC _combat_numbers rule)."
        ),
    }


def _attacker_multiattack_count(ch: "Character", c: "Campaign") -> int:
    """Return the number of attacks a combatant may make via Multiattack (0 for PCs
    and monsters without a Multiattack stat-block entry). Reuses _monster_combat_entry
    so the lookup path is identical to what the DM sees in the combat view. Returns 0
    on any lookup failure so the caller degrades to normal Extra-Attack behaviour."""
    try:
        entry = _monster_combat_entry(ch, c)
        if entry is None:
            return 0
        apt = entry.get("attacks_per_turn", 1)
        # attacks_per_turn=1 is also the fallback for monsters WITHOUT Multiattack;
        # only counts > 1 represent a real Multiattack action in the stat block.
        return int(apt) if int(apt) > 1 else 0
    except Exception:
        return 0


@mcp.tool()
def start_combat(
    campaign_id: str,
    combatant_ids: list[str],
    surpriser_ids: list[str] | None = None,
) -> dict:
    """Begin combat: roll initiative (1d20 + initiative_bonus) for each combatant
    and build the turn order (desc, ties broken by DEX modifier then input order).
    Pass the character ids of everyone in the fight.

    surpriser_ids (optional): ids of combatants who initiated the fight with a
    surprise attack (an ambush, a betrayal opener, an attack on an unready target).
    They are placed FIRST in the turn order so they act before initiative would
    otherwise allow; everyone else follows in normal rolled initiative order.
    The return carries a ``surprise`` key signalling the opening attack should
    use advantage=True (the edge is going-first + advantage; the target's AC still
    applies — no auto-kill). Unknown ids in surpriser_ids are silently skipped.
    Default [] = today's exact initiative-only behaviour (purely additive).
    # v2 idea: also apply the 5e "surprised creatures skip round 1" rule."""
    if not combatant_ids:
        raise ValueError("combatant_ids must be non-empty")
    surpriser_ids = [sid for sid in (surpriser_ids or []) if sid in combatant_ids]
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
        # Surprise ordering: surprisers go first (in their relative rolled order),
        # then non-surprisers in normal rolled initiative order.
        if surpriser_ids:
            surpriser_set = set(surpriser_ids)
            surprise_slots = [item for item in indexed if item[1][0] in surpriser_set]
            normal_slots = [item for item in indexed if item[1][0] not in surpriser_set]
            indexed = surprise_slots + normal_slots
        c.combat = Combat(
            active=True,
            round=1,
            turn_index=0,
            order=[Combatant(character_id=o[0], initiative=o[1]) for _, o in indexed],
        )
        save_campaign(c)
        view = _combat_view(c)
        # Surface the surprise edge in the runtime view so the DM resolves the opener
        # with attack(advantage=True).  Not persisted — old snapshots round-trip cleanly.
        if surpriser_ids:
            surpriser_names = [
                c.characters[sid].name for sid in surpriser_ids if sid in c.characters
            ]
            view["surprise"] = {
                "surprisers": surpriser_ids,
                "surpriser_names": surpriser_names,
                "note": (
                    "opening attack has advantage; the target's AC still applies — "
                    "call attack(advantage=True) for the opener. NO auto-kill."
                ),
            }
        # Reminder: surface anyone with Extra Attack so the DM makes the right number of attacks
        # per turn (QA: a Barbarian-5 with extra_attacks=1 made a single attack). One action =
        # extra_attacks + 1 attack calls; the engine tracks the economy via use_action.
        ea = [{"id": cid, "name": c.characters[cid].name,
               "attacks_per_action": int(getattr(c.characters[cid], "extra_attacks", 0)) + 1}
              for cid in combatant_ids
              if c.characters.get(cid) is not None and int(getattr(c.characters[cid], "extra_attacks", 0)) > 0]
        if ea:
            view["extra_attack_reminder"] = ea
        # Surface monster combat numbers (Multiattack count + authoritative attack to-hit/damage)
        # so the DM never invents monster attack bonuses and always runs the right number of
        # attacks per turn. Mirrors the PC _combat_numbers approach: authoritative sheet values,
        # never invented. Purely additive — absent when no monsters are in the fight.
        monster_combat_entries = []
        for cid in combatant_ids:
            ch = c.characters.get(cid)
            if ch is not None:
                entry = _monster_combat_entry(ch, c)
                if entry is not None:
                    monster_combat_entries.append(entry)
        if monster_combat_entries:
            view["monster_combat"] = monster_combat_entries
        ordered = [
            {
                "id": cb.character_id,
                "name": c.characters[cb.character_id].name,
                "initiative": cb.initiative,
            }
            for cb in c.combat.order
            if cb.character_id in c.characters
        ]
        names = ", ".join(item["name"] for item in ordered)
        _log_combat_event(
            c,
            f"Combat begins: {names}.",
            {"event": "combat_start", "round": c.combat.round, "combatants": ordered},
        )
        # Fold in over-match outlook for DM-staged set-pieces (not just wander encounters):
        # any time monsters are in the fight the DM may have forgotten to call
        # encounter_outlook, so we auto-surface `must_offer_out` here.  We only attach
        # `outlook` when the fight is over-matched (must_offer_out OR deadly) so a
        # fair fight's view stays UNCHANGED (purely additive).
        monster_ids_in_combat = [
            cid for cid in combatant_ids
            if c.characters.get(cid) is not None and c.characters[cid].kind == "monster"
        ]
        if monster_ids_in_combat and _party_levels(c):
            try:
                monster_xps = _resolve_monster_xps(c, None, monster_ids_in_combat)
            except ValueError:
                monster_xps = []
            if monster_xps:
                outlook = _outlook_for_xps(_party_levels(c), monster_xps)
                if outlook.get("must_offer_out") or outlook.get("band") == "deadly":
                    view["outlook"] = outlook
        # Surface whose turn it is at combat-start and make clear they must act BEFORE
        # calling next_turn (the root cause of Round-1 skips: DM reads start_combat.current
        # as already-done and immediately calls next_turn).
        first_combatant = c.characters.get(c.combat.current_combatant_id)
        if first_combatant is not None:
            view["turn_instruction"] = (
                f"It is now {first_combatant.name}'s turn (Round 1). "
                f"Resolve their action (attack / cast_spell / use_action) BEFORE calling next_turn. "
                f"'current' is WHO MUST ACT NOW — not a completed turn."
            )
        save_campaign(c)
        return view


@mcp.tool()
def set_zones(campaign_id: str, zones: list[dict]) -> dict:
    """Declare the TACTICAL ZONES of the current scene — the engine's positional
    model for combat (S2.7). OPTIONAL: use it only when terrain matters (a doorway
    to hold, rafters to climb to, an altar dais to reach). With no zones declared,
    combat is theater-of-the-mind and nothing about range or movement changes.

    Each zone is `{"name", "description"?, "adjacent"?}` — `adjacent` is a list of
    OTHER zone names directly reachable from it (a melee step / move_to_zone hop).
    Adjacency is treated as symmetric, so you only wire each edge once: if "doorway"
    lists "hall", the hall is reachable from the doorway and vice-versa. NOT a
    coordinate grid — name regions the way you'd describe them at the table ("the
    rafters", "the altar dais"), which an LLM reasons about far more reliably than
    (x, y). REPLACES the scene's zone set wholesale; call again to reshape terrain.
    Then place_combatant each fighter into a zone. Returns the stored zones."""
    parsed = [Zone.model_validate(z) for z in zones]
    names = {z.name for z in parsed}
    warnings: list[str] = []
    # Advisory: an adjacency pointing at a zone that wasn't declared is almost
    # always a typo — surface it (don't reject; the DM may add the zone next).
    for z in parsed:
        unknown = [a for a in z.adjacent if a not in names]
        if unknown:
            warnings.append(f"zone {z.name!r} lists unknown adjacent zone(s): {unknown}")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        c.combat.zones = parsed
        save_campaign(c)
        view = _combat_view(c)
    view["warnings"] = warnings
    return view


def _zone_exists(c: Campaign, zone: str) -> bool:
    return any(z.name == zone for z in c.combat.zones)


@mcp.tool()
def place_combatant(campaign_id: str, combatant_id: str, zone: str) -> dict:
    """Place a combatant directly into a tactical `zone` (S2.7) — the initial setup
    move, with NO opportunity-attack check (use move_to_zone for in-combat movement
    that may provoke). The combatant must be in the initiative order. `zone` should
    name a declared zone (set_zones); an unknown name is accepted but flagged in
    `warnings` so a typo doesn't silently strand a fighter. Returns the combat view
    (now carrying each placed combatant's `zone`)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if not c.combat.active:
            raise ValueError("no active combat")
        cb = _combatant(c, combatant_id)
        ch = c.characters.get(combatant_id)
        warnings: list[str] = []
        if c.combat.zones and not _zone_exists(c, zone):
            warnings.append(
                f"{zone!r} is not a declared zone — call set_zones to define it, or "
                f"check the name. Placed anyway."
            )
        cb.zone = zone
        save_campaign(c)
        view = _combat_view(c)
    view["placed"] = {"id": combatant_id, "name": ch.name if ch else "?", "zone": zone}
    view["warnings"] = warnings
    return view


@mcp.tool()
def move_to_zone(campaign_id: str, combatant_id: str, zone: str) -> dict:
    """Move a combatant across the zone graph DURING combat (S2.7). Unlike
    place_combatant, this models leaving the current zone: if the combatant is
    LEAVING a zone that still holds a hostile (a creature of a different
    side — player/companion vs monster/npc), the result sets `opportunity_attack`
    (with `provokers` = the hostiles left behind) so the DM can resolve each one's
    reaction (a melee attack via attack(); track it with use_action(kind=reaction)).
    The engine does NOT auto-roll the OA — staying-vs-disengage and who reacts is a
    table call.

    `zone` should be the SAME as or ADJACENT to the current zone (a single move);
    a non-adjacent hop is allowed but flagged in `warnings` (advisory, never
    blocked — the DM may rule a Dash or special movement). Returns the combat view
    plus `from`, `to`, `opportunity_attack`, and `provokers`."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if not c.combat.active:
            raise ValueError("no active combat")
        cb = _combatant(c, combatant_id)
        mover = c.characters.get(combatant_id)
        from_zone = cb.zone
        warnings: list[str] = []
        if c.combat.zones and not _zone_exists(c, zone):
            warnings.append(f"{zone!r} is not a declared zone — moved anyway; check the name.")

        # Opportunity attacks: hostiles SHARING the zone the mover is leaving. A
        # creature is hostile if it's on the opposing side (players/companions vs
        # monsters/npcs). Only meaningful when actually changing zones.
        provokers: list[dict] = []
        if from_zone and zone != from_zone and mover is not None:
            ally_kinds = {"player", "companion"}
            mover_ally = mover.kind in ally_kinds
            for other_cb in c.combat.order:
                if other_cb.character_id == combatant_id or other_cb.zone != from_zone:
                    continue
                other = c.characters.get(other_cb.character_id)
                if other is None or other.dead:
                    continue
                other_ally = other.kind in ally_kinds
                if other_ally != mover_ally:  # opposing side -> it can take an OA
                    provokers.append({"id": other.id, "name": other.name})

        # Advisory non-adjacency note (only when zones are declared and we know both).
        if c.combat.zones and from_zone and zone != from_zone:
            if not combat.zones_in_melee(c.combat.zones, from_zone, zone):
                warnings.append(
                    f"{zone!r} is not adjacent to {from_zone!r} — a single move normally "
                    f"reaches only the same or an adjacent zone (this may need a Dash). "
                    f"Moved anyway."
                )

        cb.zone = zone
        _log_combat_event(
            c,
            f"{mover.name if mover else combatant_id} moves from {from_zone or 'an unset zone'} to {zone}.",
            {
                "event": "zone_movement",
                "actor": _combatant_ref(mover) if mover else {"id": combatant_id, "name": "?"},
                "from_zone": from_zone,
                "to_zone": zone,
                "opportunity_attack": bool(provokers),
                "provokers": provokers,
                "warnings": list(warnings),
            },
            speaker=mover.name if mover else "",
        )
        save_campaign(c)
        view = _combat_view(c)
    view["from"] = from_zone
    view["to"] = zone
    view["opportunity_attack"] = bool(provokers)
    view["provokers"] = provokers
    view["warnings"] = warnings
    return view


@mcp.tool()
def combatants_in_zone(campaign_id: str, zone: str) -> dict:
    """List the combatants currently in a tactical `zone` (S2.7) — the targeting
    helper for an AREA-OF-EFFECT spell or ability ("everyone on the dais"). Returns
    each occupant `{id, name, kind, hp}` so you can saving_throw / apply_damage them
    in turn. Read-only. Empty list if no one is in that zone (or no zones declared)."""
    c = _require(campaign_id)
    occupants = []
    for cb in c.combat.order:
        if cb.zone != zone:
            continue
        ch = c.characters.get(cb.character_id)
        if ch is None:
            continue
        occupants.append(
            {
                "id": ch.id,
                "name": ch.name,
                "kind": ch.kind,
                "hp": f"{ch.current_hp}/{ch.max_hp}",
            }
        )
    return {"zone": zone, "count": len(occupants), "combatants": occupants}


def _turn_brief(ch: "Character", c: "Campaign") -> dict:
    """Build the per-turn brief for the combatant whose turn it just became.

    Surfaced in next_turn's return so the DM has authoritative attack/resource
    data AT THE TURN TRIGGER — the root cause of combat-adherence drift (#166):
    the DM reads monster_combat once at start_combat then forgets it by round 3.

    Schema:
      name        — combatant name (for quick DM reference)
      kind        — "monster" | "player" | "npc" | "companion"
      attack      — for monsters: {attacks_per_turn, attacks (list of {name, to_hit, damage})};
                    for PCs/companions/NPCs: {melee_attack_bonus, ranged_attack_bonus,
                                              melee_damage_mod, ranged_damage_mod,
                                              extra_attacks} (from _combat_numbers)
      resources   — {resource_id: {remaining, max, label}} for every class_resource
                    with remaining > 0; empty dict if none or all spent.
      spell_slots — {level: remaining} for slot levels with at least 1 remaining;
                    empty dict if no slots or all used.
      note        — brief action instruction for this combatant type.
    """
    brief: dict = {
        "name": ch.name,
        "kind": ch.kind,
    }
    # --- attack line ---
    if ch.kind == "monster":
        entry = _monster_combat_entry(ch, c)
        if entry is not None:
            brief["attack"] = {
                "attacks_per_turn": entry["attacks_per_turn"],
                "attacks": entry["attacks"],
            }
            brief["note"] = (
                f"Run {entry['attacks_per_turn']} attack call(s) using the listed "
                "to_hit/damage — never invent bonuses."
            )
        else:
            # Fallback: no bestiary data, use derived numbers like a PC
            nums = _combat_numbers(ch)
            brief["attack"] = {
                "melee_attack_bonus": nums["melee_attack_bonus"],
                "ranged_attack_bonus": nums["ranged_attack_bonus"],
                "melee_damage_mod": nums["melee_damage_mod"],
                "ranged_damage_mod": nums["ranged_damage_mod"],
                "extra_attacks": int(getattr(ch, "extra_attacks", 0)),
            }
            brief["note"] = "No bestiary data — use derived bonuses above; never invent."
    else:
        nums = _combat_numbers(ch)
        extra = int(getattr(ch, "extra_attacks", 0))
        attacks_per_action = extra + 1
        brief["attack"] = {
            "melee_attack_bonus": nums["melee_attack_bonus"],
            "ranged_attack_bonus": nums["ranged_attack_bonus"],
            "melee_damage_mod": nums["melee_damage_mod"],
            "ranged_damage_mod": nums["ranged_damage_mod"],
            "extra_attacks": extra,
            "attacks_per_action": attacks_per_action,
        }
        brief["note"] = (
            f"Declare use_action(kind='action') then make {attacks_per_action} attack call(s) "
            "using the sheet bonuses above — never invent or copy another combatant's."
        )
    # --- limited resources (class_resources with remaining > 0) ---
    resources: dict = {}
    for rid, res in ch.class_resources.items():
        remaining = res.max - res.used
        if remaining > 0:
            resources[rid] = {
                "remaining": remaining,
                "max": res.max,
                "label": f"{remaining}/{res.max}" + (f" {res.size}" if res.size else ""),
            }
    brief["resources"] = resources
    # --- spell slots with at least 1 remaining ---
    slots: dict = {}
    for lvl, slot in ch.spell_slots.items():
        rem = slot.maximum - slot.used
        if rem > 0:
            slots[str(lvl)] = rem
    if slots:
        brief["spell_slots"] = slots
    return brief


@mcp.tool()
def next_turn(campaign_id: str) -> dict:
    """Advance to the next LIVING combatant's turn (round increments on wrap;
    dead or removed combatants are skipped). Returns whose turn it is, whether
    they owe a death save (downed and unstable), and a ``turn_brief`` with their
    authoritative attack line + available limited resources so the DM never drifts
    from the sheet numbers mid-combat (#166)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        order = c.combat.order
        if not c.combat.active or not order:
            raise ValueError("no active combat")
        previous = c.characters.get(c.combat.current_combatant_id)
        # --- PC-skip guard (#160/#166): enforce that a PC/companion who CAN act has
        # actually acted (or explicitly passed) before we advance past their turn.
        # Rules-correct 5e: a creature takes an action or explicitly declares a pass;
        # silent skip is never legal. Monsters/NPCs advance freely (DM runs them).
        if previous is not None and previous.kind in ("player", "companion"):
            outgoing_able = (
                not combat.is_incapacitated(previous)
                and previous.current_hp > 0
                and not previous.dead
                and not getattr(previous, "stable", False)  # stable/unconscious can't act anyway
            )
            # "Stable" PCs at 0 hp are unconscious — is_incapacitated covers most
            # of this, but we also gate on hp>0 to be explicit.
            outgoing_acted = (
                c.combat.action_used
                or c.combat.action_attacks_made > 0
                or c.combat.bonus_action_used
            )
            if outgoing_able and not outgoing_acted:
                raise ValueError(
                    f"{previous.name} has not acted this turn — resolve their action "
                    f"(attack / cast_spell / use_action) or declare a pass via "
                    f"use_action(kind='skip') before advancing."
                )
        n = len(order)
        cur = None
        new_round = False
        for _ in range(n):  # at most one full lap; skip dead/removed combatants
            # Keep turn_index NORMALIZED to [0, n) — it's a position, not a running
            # tally. (A monotonic counter desynced remove_combatant's index math,
            # skipping the current turn after a few rounds.) A wrap back to the top
            # of the initiative order starts a new round.
            c.combat.turn_index = (c.combat.turn_index + 1) % n
            if c.combat.turn_index == 0:
                c.combat.round += 1
                new_round = True
            candidate = c.characters.get(c.combat.current_combatant_id)
            if candidate is not None and not candidate.dead:
                cur = candidate
                break
        # Fresh action economy for the new turn; the current combatant's reaction
        # recharges at the start of their turn.
        c.combat.action_used = False
        c.combat.bonus_action_used = False
        # Reset the per-turn attack-action economy too: a new turn starts with one
        # Attack action's worth of strikes and no Action Surge spent yet.
        c.combat.action_attacks_made = 0
        c.combat.surge_actions = 0
        if cur is not None:
            for cb in order:
                if cb.character_id == cur.id:
                    cb.reaction_used = False
                    break
        # Tick round/minute-scale timed effects ONCE per new round (a "10 rounds"
        # effect lasts 10 rounds, not 10 turns) and auto-expire those that hit 0.
        # We decrement every combatant's effects (an effect can sit on any of them).
        expired: list[dict] = []
        if new_round:
            for cb in order:
                holder = c.characters.get(cb.character_id)
                if holder is None:
                    continue
                for name in combat.tick_round_effects(holder):
                    expired.append({"character_id": holder.id, "name": name})
        view = _combat_view(c)
        view["current_name"] = cur.name if cur else None
        view["death_save_due"] = bool(cur and cur.current_hp == 0 and not cur.dead and not cur.stable)
        view["expired_effects"] = expired
        # Surface per-turn authoritative attack line + available resources so the DM
        # has the sheet numbers AT THE TRIGGER POINT — not just at start_combat (#166).
        # Only when combat is active and there's a living current combatant.
        if cur is not None:
            view["turn_brief"] = _turn_brief(cur, c)
            # Clarify to the DM that 'current' is WHO MUST ACT NOW — not a completed turn.
            # This directly addresses the Round-1 skip pattern where the DM misread
            # start_combat/next_turn's 'current' as the turn just finished.
            view["turn_instruction"] = (
                f"It is now {cur.name}'s turn (Round {c.combat.round}). "
                f"Resolve their action (attack / cast_spell / use_action) BEFORE calling next_turn. "
                f"'current' is WHO MUST ACT NOW — not a completed turn."
            )
        _log_combat_event(
            c,
            f"Turn advances to {cur.name}." if cur else "Turn advances with no living combatant.",
            {
                "event": "turn_advanced",
                "round": c.combat.round,
                "new_round": new_round,
                "previous": _combatant_ref(previous) if previous else None,
                "current": _combatant_ref(cur) if cur else None,
                "turn_index": c.combat.turn_index,
                "death_save_due": view["death_save_due"],
                "expired_effects": expired,
            },
            speaker=cur.name if cur else "",
        )
        save_campaign(c)
        return view


@mcp.tool()
def use_action(campaign_id: str, character_id: str, kind: str = "action") -> dict:
    """Track a combatant's action economy. kind: action | bonus | reaction | free | skip.
    `action`/`bonus` are legal only on the creature's OWN turn and only once each
    per turn; `reaction` is legal any time but once per round (it refreshes at the
    start of the creature's turn via next_turn); `free`/movement isn't rate-limited.
    `skip` (a.k.a. pass) declares an intentional do-nothing turn for the current
    combatant — sets action_used so next_turn's PC-skip guard is satisfied without
    actually attacking or casting. Use it when a PC Dodges, Dashes, Disengages,
    Readies, or simply passes their turn.
    Returns {ok, reason, action_available, bonus_available, reaction_available} so
    you can flag an illegal double-action. NOTE: multiattack (Extra Attack) is ONE
    action — declare a single `action`, then make several attack() calls under it."""
    kind = kind.lower()
    if kind not in ("action", "bonus", "reaction", "free", "movement", "skip"):
        raise ValueError("kind must be action | bonus | reaction | free | skip")
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
        # SRD: an incapacitated creature (incl. stunned/paralyzed/petrified/unconscious) can take
        # NO actions, bonus actions, or reactions. Block before consuming the budget.
        if combat.is_incapacitated(ch) and kind in ("action", "bonus", "reaction"):
            ok, reason = False, (
                f"{ch.name} is incapacitated ("
                f"{', '.join(c.value for c in ch.conditions if c in combat.INCAPACITATING)}) "
                f"and can't take an action, bonus action, or reaction"
            )
        elif kind == "skip":
            # Declare an intentional pass: satisfies the PC-skip guard in next_turn so
            # the DM can advance the turn without the combatant attacking or casting.
            # Only valid on the current combatant's turn; marks action_used so any
            # subsequent use_action(kind='action') is properly rejected.
            if not is_current:
                ok, reason = False, f"it is not {ch.name}'s turn — skip must be declared on your own turn"
            elif c.combat.action_used:
                ok, reason = False, "action already used this turn (already acted or skipped)"
            else:
                c.combat.action_used = True
        elif kind in ("action", "bonus"):
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
    is_reaction: bool = False,
) -> dict:
    """Resolve an attack. The DM supplies attack_bonus and damage_dice (e.g.
    '1d8+3'); the engine rolls 1d20+bonus vs the target's AC, auto-hits on a
    natural 20 and auto-misses on a natural 1, doubles damage dice on a crit, and
    applies the damage. Condition-based advantage/disadvantage is detected (set
    is_ranged=True so a prone target gives disadvantage rather than advantage) and
    combined with the explicit flags (they cancel if both apply).

    TURN ORDER + ACTION ECONOMY (enforced while a combat is active and the attacker
    is in the initiative order): an attack as your ACTION is legal only on your own
    turn, and only up to one Attack action's worth of strikes — 1 attack, or
    `extra_attacks + 1` with the Extra Attack feature. A further attack needs another
    Attack action: spend Action Surge (use_resource(resource='action_surge')) first.
    OFF-TURN attacks are treated as a REACTION (an opportunity attack): legal once
    per round and gated by the combatant's reaction — pass is_reaction=True to mark
    an opportunity attack explicitly. An illegal attack (wrong turn / out of attacks /
    no reaction left) is REJECTED with a clear error and NO state change (no roll,
    no damage). Inert when no combat is active or the attacker isn't a combatant."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        attacker = _char(c, attacker_id)
        target = _char(c, target_id)
        # SRD: an incapacitated creature can't attack — refuse the illegal action outright
        # (an unconscious/paralyzed/stunned attacker must not roll to hit).
        if combat.is_incapacitated(attacker):
            incap = ", ".join(cn.value for cn in attacker.conditions if cn in combat.INCAPACITATING)
            raise ValueError(f"{attacker.name} is incapacitated ({incap}) and cannot attack")
        # Turn ownership + action economy. Only enforced while combat is active AND the
        # attacker is actually in the initiative order (a non-combatant strike — an
        # environmental hazard, an out-of-initiative scene — is left to the DM). The
        # gate runs BEFORE the roll so a rejected attack changes NOTHING (no roll, no
        # damage, no economy spend) — the QA defect was an attack by the wrong creature
        # / a second attack with no mechanical basis silently resolving.
        attacker_cb = next(
            (cb for cb in c.combat.order if cb.character_id == attacker_id), None
        ) if c.combat.active else None
        consume_reaction = False
        ma = 0  # multiattack count; set in action-attack branch below for monsters
        if attacker_cb is not None:
            is_current = c.combat.current_combatant_id == attacker_id
            # Off-turn, or an explicitly-declared opportunity attack, is a REACTION:
            # legal once per round, gated by reaction_used (so OAs legitimately happen
            # off-turn) — never blocked by the per-action attack budget.
            if is_reaction or not is_current:
                if attacker_cb.reaction_used:
                    cur = c.characters.get(c.combat.current_combatant_id)
                    cur_name = cur.name if cur else c.combat.current_combatant_id
                    detail = (
                        "an opportunity attack" if is_reaction
                        else f"acting off-turn (it is {cur_name}'s turn)"
                    )
                    raise ValueError(
                        f"{attacker.name} has already used its reaction this round and "
                        f"cannot attack again until its next turn ({detail}). "
                        f"Advance with next_turn so the order stays in sync."
                    )
                consume_reaction = True
            else:
                # An attack as the current combatant's ACTION: enforce one Attack
                # action's worth of strikes (Extra Attack / Action Surge aware,
                # and Multiattack-aware for monsters with a stat-block entry).
                ma = _attacker_multiattack_count(attacker, c)
                ok, reason = combat.check_action_attack(
                    is_current=True,
                    attacks_made=c.combat.action_attacks_made,
                    extra_attacks=getattr(attacker, "extra_attacks", 0),
                    surge_actions=c.combat.surge_actions,
                    multiattack=ma,
                )
                if not ok:
                    raise ValueError(f"{attacker.name} cannot attack: {reason}")
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
        # Commit the action economy now — an attack spends its action/reaction whether
        # or not it lands (a missed swing still used your action). The gate above already
        # proved it legal; record it so a second attack this turn is judged correctly.
        if attacker_cb is not None:
            if consume_reaction:
                attacker_cb.reaction_used = True
                result["reaction_used"] = True
            else:
                c.combat.action_used = True  # an Attack action consumes the turn's action
                c.combat.action_attacks_made += 1
                result["attacks_made_this_turn"] = c.combat.action_attacks_made
                result["attacks_allowed_this_turn"] = combat.attacks_allowed(
                    getattr(attacker, "extra_attacks", 0),
                    c.combat.surge_actions,
                    ma,
                )
        # Zone-aware range (S2.7): a MELEE attack needs attacker & target in the same
        # or an adjacent zone; ranged reaches any zone. Advisory only — surface a
        # warning, never hard-block. Inert when no zones are declared. Position lives
        # on the Combatant records, so look up each side's zone (absent = unplaced).
        if not is_ranged and c.combat.zones:
            az = next((cb.zone for cb in c.combat.order if cb.character_id == attacker_id), "")
            tz = next((cb.zone for cb in c.combat.order if cb.character_id == target_id), "")
            warn = combat.melee_range_warning(c.combat.zones, attacker, target, az, tz)
            if warn:
                result["range_warning"] = warn
        if hit:
            expr = combat.double_dice(damage_dice) if is_crit else damage_dice
            dmg = dice_mod.roll(expr)
            outcome = combat.apply_damage(target, max(0, dmg.total), crit=is_crit, damage_type=damage_type)
            result["damage"] = {"total": max(0, dmg.total), "type": damage_type, "expr": expr, "detail": dmg.detail}
            result["target_state"] = outcome
            kx = _award_kill_xp(c, target)
            if kx:
                result["kill_xp"] = kx
        # ON-HIT RIDER RESOLUTION (#186). An attack-roll spell (Guiding Bolt) recorded a
        # PENDING rider on the caster at cast_spell time instead of writing its timed effect
        # to the target. This attack resolves that spell attack when it's the caster striking
        # this same target: on a HIT, materialize the rider's ActiveEffect on the target now
        # (refresh-not-stack, identical to a cast-time write); on a MISS, discard it (no free
        # advantage). A weapon attack with no matching pending rider is wholly unaffected.
        riders = [
            r for r in attacker.pending_on_hit_riders if r.target_id == target_id
        ]
        if riders:
            # Drop every matched rider from the caster whatever the outcome — hit applies,
            # miss simply discards. (Unmatched riders, e.g. a bolt aimed at a different
            # target, stay pending.)
            attacker.pending_on_hit_riders = [
                r for r in attacker.pending_on_hit_riders if r.target_id != target_id
            ]
            if hit:
                applied = []
                for r in riders:
                    eff = ActiveEffect(
                        name=r.name,
                        source_id=r.source_id,
                        concentration=False,
                        scale=r.scale,
                        rounds_remaining=r.rounds_remaining,
                        expires_day=r.expires_day,
                        expires_phase_index=r.expires_phase_index,
                        until_long_rest=r.until_long_rest,
                    )
                    # Refresh, don't stack (mirrors cast_spell's write).
                    target.active_effects = [
                        e for e in target.active_effects if e.name != r.name
                    ]
                    target.active_effects.append(eff)
                    applied.append(r.name)
                result["on_hit_effect_applied"] = applied
            else:
                result["on_hit_effect_discarded"] = [r.name for r in riders]
        outcome_label = "crit" if is_crit else ("hit" if hit else "miss")
        if hit and result["damage"]:
            dtype = f" {damage_type}" if damage_type else ""
            text = (
                f"{attacker.name} critically hits {target.name} for "
                f"{result['damage']['total']}{dtype} damage."
                if is_crit
                else f"{attacker.name} hits {target.name} for {result['damage']['total']}{dtype} damage."
            )
        else:
            text = f"{attacker.name} misses {target.name}."
        _log_combat_event(
            c,
            text,
            {
                "event": "attack",
                "outcome": outcome_label,
                "actor": _combatant_ref(attacker),
                "target": {**_combatant_ref(target), "ac": target.armor_class},
                "roll": {
                    "total": atk.total,
                    "natural": atk.natural,
                    "detail": atk.detail,
                    "attack_bonus": attack_bonus,
                    "advantage": adv,
                    "disadvantage": dis,
                },
                "damage": result["damage"],
                "target_state": result.get("target_state"),
            },
            speaker=attacker.name,
        )
        # Persist regardless of hit/miss: a miss still consumed the action/reaction
        # economy above, and that bookkeeping must survive (sole-writer discipline).
        save_campaign(c)
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
        target = _char(c, target_id)
        out = combat.apply_damage(target, amount, crit=crit, half=half, damage_type=damage_type)
        kx = _award_kill_xp(c, target)
        if kx:
            out["kill_xp"] = kx
        dtype = f" {damage_type}" if damage_type else ""
        _log_combat_event(
            c,
            f"{target.name} takes {out.get('damage_to_hp', 0)}{dtype} damage.",
            {
                "event": "damage",
                "target": _combatant_ref(target),
                "amount": amount,
                "damage_type": damage_type,
                "crit": crit,
                "half": half,
                "result": out,
            },
            speaker=target.name,
        )
        save_campaign(c)
        return out


@mcp.tool()
def apply_healing(campaign_id: str, target_id: str, amount: int) -> dict:
    """Heal a character (up to max HP). Healing above 0 HP ends the dying state
    and resets death saves. Cannot revive the dead."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        target = _char(c, target_id)
        out = combat.apply_healing(target, amount)
        _log_combat_event(
            c,
            f"{target.name} regains {out.get('healed', 0)} hit points.",
            {
                "event": "healing",
                "target": _combatant_ref(target),
                "amount": amount,
                "result": out,
            },
            speaker=target.name,
        )
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
        expired: list[str] = []
        if not maintained:
            ch.concentration = None
            # The engine-tracked concentration effect ends with the concentration.
            expired = combat.expire_concentration_effects(ch)
        save_campaign(c)
        return {
            "roll": r.total,
            "natural": r.natural,
            "dc": dc,
            "maintained": maintained,
            "concentration": ch.concentration,
            "expired_effects": expired,
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
        roll = dice_mod.roll("1d20")
        out = combat.resolve_death_save(ch, roll)
        _log_combat_event(
            c,
            f"{ch.name} rolls a death save: {out.get('result')}.",
            {
                "event": "death_save",
                "target": _combatant_ref(ch),
                "roll": {
                    "total": roll.total,
                    "natural": roll.natural,
                    "detail": roll.detail,
                },
                "result": out.get("result"),
                "successes": ch.death_saves.successes,
                "failures": ch.death_saves.failures,
                "state": {
                    "current_hp": ch.current_hp,
                    "stable": ch.stable,
                    "dead": ch.dead,
                    "dying": ch.current_hp == 0 and not ch.dead and not ch.stable,
                },
            },
            speaker=ch.name,
        )
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


def _award_kill_xp(c, monster) -> "dict | None":
    """Award a single defeated monster's XP to the living party THE MOMENT it dies
    (robust to DM sequencing — see end_combat). Idempotent: zeroes xp_value, so a
    re-call (or end_combat's backstop sweep) never double-awards. No-op outside 'xp'
    leveling mode, for non-monsters, the living, or zero-value foes."""
    if c.leveling_mode != "xp":
        return None
    if getattr(monster, "kind", "") != "monster" or not monster.dead or monster.xp_value <= 0:
        return None
    recipients = [c.characters[i] for i in c.party if i in c.characters and not c.characters[i].dead]
    if not recipients:
        return None
    total = monster.xp_value
    each, rem = divmod(total, len(recipients))
    grants = []
    for idx, ch in enumerate(recipients):
        amt = each + (rem if idx == 0 else 0)
        ch.xp = max(0, ch.xp + amt)
        available = srd_tables.level_for_xp(ch.xp)
        grants.append({"id": ch.id, "name": ch.name, "xp_gained": amt, "xp": ch.xp,
                       "level_available": available, "can_level_up": available > ch.total_level})
    monster.xp_value = 0  # consumed — idempotent guard against double-award
    return {"xp_awarded": total, "grants": grants}


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
        was_active = c.combat.active
        # Backstop sweep: award XP for any dead monsters still in the order that weren't
        # caught by the kill-time _award_kill_xp calls (their xp_value is already 0 in
        # the normal case — so this loop is mostly a no-op after kill-time awarding). It
        # still catches any death path not yet wired to the helper.
        if c.leveling_mode == "xp":
            combat_ids = {cb.character_id for cb in c.combat.order}
            all_total = 0
            all_grants: list[dict] = []
            for ch in list(c.characters.values()):
                if ch.id in combat_ids:
                    kx = _award_kill_xp(c, ch)
                    if kx:
                        all_total += kx["xp_awarded"]
                        all_grants.extend(kx["grants"])
            if all_total > 0:
                result["xp_awarded"] = all_total
                result["grants"] = all_grants
        if was_active or c.combat.order:
            ended_order = [
                _combatant_ref(c.characters[cb.character_id])
                for cb in c.combat.order
                if cb.character_id in c.characters
            ]
            _log_combat_event(
                c,
                "Combat ends.",
                {
                    "event": "combat_end",
                    "round": c.combat.round,
                    "combatants": ended_order,
                    "xp_awarded": result.get("xp_awarded", 0),
                },
            )
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
        multiclass = existing is None and bool(ch.classes)
        if multiclass:
            if not c.house_rules.multiclass_allowed:
                raise ValueError("multiclassing is disabled by campaign house rules")
            if not _meets_prereq(ch, cname):
                raise ValueError(f"does not meet the multiclass prerequisite for {class_name}")

        new_class_level = existing.level + 1 if existing else 1
        is_asi_level = srd_tables.is_asi_level(cname, new_class_level)
        pending_asi = None
        if is_asi_level:
            if asi and feat:
                raise ValueError("choose either asi or feat, not both")
            if asi:
                pending_asi = _validated_asi_choice(asi)
            elif feat and not c.house_rules.feats_allowed:
                raise ValueError("feats are disabled by campaign house rules")
        elif asi or feat:
            raise ValueError(f"{class_name} level {new_class_level} does not grant an ASI or feat choice")

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
        else:
            ch.classes.append(ClassLevel(name=class_name.capitalize(), level=1, subclass=subclass))

        ch.max_hp += gain
        ch.current_hp += gain
        ch.hit_dice_remaining += 1
        # keep the hit_dice string in sync (single-class; was left stale after level_up)
        if len({cl.name.lower() for cl in ch.classes}) == 1:
            ch.hit_dice = f"{sum(cl.level for cl in ch.classes)}d{die}"

        applied = None
        if is_asi_level:
            if pending_asi:
                for ability, inc in pending_asi.items():
                    setattr(ch.abilities, ability, min(20, getattr(ch.abilities, ability) + inc))
                applied = {"asi": pending_asi}
            elif feat:
                applied = {"feat": feat}
                ch.notes = (ch.notes + f" | feat: {feat}").strip(" |")

        ch.proficiency_bonus = srd_tables.proficiency_bonus(ch.total_level)
        ch.initiative_bonus = ch.ability_modifier(Ability.DEX)
        _recompute_spellcasting(ch)
        _recompute_class_resources(ch)

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


def _spell_slot_maxes(ch: Character) -> dict[int, int]:
    return {int(level): slot.maximum for level, slot in ch.spell_slots.items()}


def _spell_slot_deltas(before: Character, after: Character) -> dict[str, dict]:
    before_max = _spell_slot_maxes(before)
    after_max = _spell_slot_maxes(after)
    deltas: dict[str, dict] = {}
    for level in sorted(set(before_max) | set(after_max)):
        old = before_max.get(level, 0)
        new = after_max.get(level, 0)
        if old != new:
            deltas[str(level)] = {"from_max": old, "to_max": new, "delta": new - old}
    return deltas


def _resource_deltas(before: Character, after: Character) -> dict[str, dict]:
    deltas: dict[str, dict] = {}
    for rid in sorted(set(before.class_resources) | set(after.class_resources)):
        old = before.class_resources.get(rid)
        new = after.class_resources.get(rid)
        old_max = old.max if old else 0
        new_max = new.max if new else 0
        if old_max != new_max:
            res = new or old
            deltas[rid] = {
                "from_max": old_max,
                "to_max": new_max,
                "delta": new_max - old_max,
                "recharge": res.recharge if res else "none",
            }
    return deltas


@mcp.tool()
def preview_level_up(
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
    """Preview a level-up without writing campaign state.

    Returns the requested class/level transition, HP gain, class features,
    spell-slot and class-resource max deltas, required ASI/feat choices, and any
    rule errors. This intentionally clones the persisted character and never
    calls save_campaign, so the engine remains the sole writer for real level-ups.
    """
    c = _require(campaign_id)
    original = _char(c, character_id)
    before = Character.model_validate(original.model_dump(mode="json"))
    preview = Character.model_validate(original.model_dump(mode="json"))
    cname = class_name.lower()
    errors: list[str] = []
    choice_requirements: list[dict] = []
    applied = None

    try:
        srd_tables.class_data(cname)
    except ValueError as exc:
        return {
            "ok": False,
            "character_id": character_id,
            "class_name": class_name,
            "from": {"total_level": before.total_level, "class_level": 0, "class_name": cname},
            "to": {"total_level": before.total_level, "class_level": 0, "class_name": cname},
            "hp_gain": None,
            "features_gained": [],
            "spell_slot_deltas": {},
            "resource_deltas": {},
            "choice_requirements": [],
            "errors": [str(exc)],
        }

    existing = next((cl for cl in preview.classes if cl.name.lower() == cname), None)
    from_class_level = existing.level if existing else 0
    multiclass = existing is None and bool(preview.classes)
    if multiclass:
        if not c.house_rules.multiclass_allowed:
            errors.append("multiclassing is disabled by campaign house rules")
        if not _meets_prereq(preview, cname):
            errors.append(f"does not meet the multiclass prerequisite for {class_name}")

    die = srd_tables.hit_die(cname)
    con = preview.ability_modifier(Ability.CON)
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
        preview.classes.append(ClassLevel(name=class_name.capitalize(), level=1, subclass=subclass))
        new_class_level = 1

    preview.max_hp += gain
    preview.current_hp += gain
    preview.hit_dice_remaining += 1
    if len({cl.name.lower() for cl in preview.classes}) == 1:
        preview.hit_dice = f"{sum(cl.level for cl in preview.classes)}d{die}"

    if srd_tables.is_asi_level(cname, new_class_level):
        choice_requirements.append(
            {"type": "asi_or_feat", "class_name": cname, "class_level": new_class_level}
        )
        if asi and feat:
            errors.append("choose either asi or feat, not both")
        elif asi:
            try:
                pending = _validated_asi_choice(asi)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                for ability, inc in pending.items():
                    setattr(preview.abilities, ability, min(20, getattr(preview.abilities, ability) + inc))
                applied = {"asi": pending}
        elif feat:
            if not c.house_rules.feats_allowed:
                errors.append("feats are disabled by campaign house rules")
            else:
                applied = {"feat": feat}
    elif asi or feat:
        errors.append(f"{class_name} level {new_class_level} does not grant an ASI or feat choice")

    preview.proficiency_bonus = srd_tables.proficiency_bonus(preview.total_level)
    preview.initiative_bonus = preview.ability_modifier(Ability.DEX)
    _recompute_spellcasting(preview)
    _recompute_class_resources(preview)

    gained = srd_tables.features_at(cname, new_class_level)
    for f in gained:
        if f["name"] not in preview.features:
            preview.features.append(f["name"])
        if "extra_attacks" in f:
            preview.extra_attacks = max(preview.extra_attacks, int(f["extra_attacks"]))
        if f.get("sneak_attack_dice"):
            preview.sneak_attack_dice = f["sneak_attack_dice"]

    return {
        "ok": not errors,
        "character_id": character_id,
        "character_name": original.name,
        "class_name": cname,
        "multiclass": multiclass,
        "from": {
            "total_level": before.total_level,
            "class_level": from_class_level,
            "class_name": cname,
        },
        "to": {
            "total_level": preview.total_level,
            "class_level": new_class_level,
            "class_name": cname,
        },
        "hp_gain": gain,
        "hp_method": hp_method,
        "features_gained": gained,
        "spell_slot_deltas": _spell_slot_deltas(before, preview),
        "resource_deltas": _resource_deltas(before, preview),
        "choice_requirements": choice_requirements,
        "applied_choice": applied,
        "errors": errors,
    }


def _build_option_from_preview(preview: dict, feats_allowed: bool, multiclass_allowed: bool) -> dict:
    asi_required = any(req.get("type") == "asi_or_feat" for req in preview["choice_requirements"])
    return {
        "class_name": preview["class_name"],
        "legal": preview["ok"],
        "multiclass": preview["multiclass"],
        "from": {"level": preview["from"]["total_level"], "class_level": preview["from"]["class_level"]},
        "to": {"level": preview["to"]["total_level"], "class": preview["class_name"]},
        "hp_gain": preview["hp_gain"],
        "features_gained": preview["features_gained"],
        "spell_slots_delta": preview["spell_slot_deltas"],
        "resources_delta": preview["resource_deltas"],
        "choices": {
            "asi_required": asi_required,
            "feat_allowed": feats_allowed and asi_required,
            "multiclass_allowed": multiclass_allowed if preview["multiclass"] else True,
        },
        "errors": list(preview["errors"]),
        "preview": preview,
    }


@mcp.tool()
def build_options(campaign_id: str, character_id: str) -> dict:
    """Return legal one-level build paths for a character without mutating state.

    This is the engine-owned build-planner surface: it derives each candidate by
    calling preview_level_up, exposes legal options for the dashboard to render,
    and keeps illegal multiclass/rule-blocked paths in blocked_options for
    diagnostics instead of offering them as actionable choices.
    """
    c = _require(campaign_id)
    ch = _char(c, character_id)
    before = Character.model_validate(ch.model_dump(mode="json"))
    current_classes = [cl.name.lower() for cl in before.classes]
    available_classes = sorted(
        name for name, data in srd_tables.classes().items() if isinstance(data, dict)
    )
    class_names = list(dict.fromkeys(current_classes + available_classes))
    options: list[dict] = []
    blocked_options: list[dict] = []

    for cname in class_names:
        preview = preview_level_up(campaign_id, character_id, cname)
        option = _build_option_from_preview(
            preview,
            c.house_rules.feats_allowed,
            c.house_rules.multiclass_allowed,
        )
        if option["legal"]:
            options.append(option)
        else:
            blocked_options.append(option)

    asi_required = any(option["choices"]["asi_required"] for option in options)
    return {
        "character_id": character_id,
        "character_name": before.name,
        "from": {
            "level": before.total_level,
            "classes": [
                {"name": cl.name.lower(), "level": cl.level, "subclass": cl.subclass}
                for cl in before.classes
            ],
        },
        "choices": {
            "asi_required": asi_required,
            "feat_allowed": c.house_rules.feats_allowed,
            "multiclass_allowed": c.house_rules.multiclass_allowed,
        },
        "options": options,
        "blocked_options": blocked_options,
        "errors": [],
    }


@mcp.tool()
def spell_save_dc(campaign_id: str, character_id: str) -> dict:
    """Return a caster's spell save DC (8 + proficiency + casting modifier) and
    spell attack bonus (proficiency + casting modifier)."""
    c = _require(campaign_id)
    ch = _char(c, character_id)
    mod = _casting_mod(ch)
    return {"spell_save_dc": 8 + ch.proficiency_bonus + mod, "spell_attack_bonus": ch.proficiency_bonus + mod}


# A time-of-day phase is a coarse ~6h slice (24h / 4 phases). Hour/day-scale spell
# durations are quantized onto that grid for their clock deadline: an N-hour spell
# lasts ceil(N/6) phases (min 1, so a 1-hour Hex outlives the current phase but ends
# on the next phase boundary), an N-day spell lasts N*4 phases. Minute/round-scale
# effects don't use this — they're round-decremented and die on any phase advance.
_HOURS_PER_PHASE = 6


def _effect_clock_deadline(c: Campaign, hours: int, days: int) -> tuple[int, int]:
    """The (day, phase_index) at which an hour/day-scale effect cast NOW expires —
    the current clock advanced by the duration, quantized to whole phases. Pure
    arithmetic over travel.PHASES; doesn't mutate the campaign."""
    phases = travel.PHASES
    try:
        cur_idx = phases.index(c.time_of_day)
    except ValueError:
        cur_idx = 0
    if days > 0:
        steps = days * len(phases)
    else:
        steps = max(1, -(-hours // _HOURS_PER_PHASE))  # ceil(hours / 6), min 1
    total = cur_idx + steps
    return c.day + total // len(phases), total % len(phases)


def _expire_clock_effects_all(c: Campaign, *, long_rest: bool = False) -> list[dict]:
    """Expire every character's clock-elapsed timed effects at the campaign's CURRENT
    clock (call AFTER advancing it). Returns ``[{character_id, name}, ...]`` for the DM
    to narrate ("Bless fades"). `long_rest=True` also ends hour-scale buffs (Mage Armor).
    Mutates the campaign's characters; the caller persists."""
    try:
        phase_idx = travel.PHASES.index(c.time_of_day)
    except ValueError:
        phase_idx = 0
    report: list[dict] = []
    for ch in c.characters.values():
        if not ch.active_effects:
            continue
        for name in combat.expire_clock_effects(ch, c.day, phase_idx, long_rest=long_rest):
            report.append({"character_id": ch.id, "name": name})
    return report


@mcp.tool()
def cast_spell(
    campaign_id: str,
    character_id: str,
    spell_name: str,
    slot_level: Optional[int] = None,
    target_id: str = "",
    is_melee: bool = False,
    is_reaction: bool = False,
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
    apply_damage(half=<save succeeded>); heals via apply_healing.

    ZONES (S2.7): pass `target_id` + `is_melee=True` for a TOUCH/melee spell (e.g.
    Shocking Grasp, Inflict Wounds) to get the same advisory `range_warning` as a
    melee attack when caster and target aren't in the same or an adjacent zone.
    Ranged spells reach any zone — leave `is_melee` False (the default) and they're
    never gated. Inert when no zones are declared (theater-of-the-mind)."""
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
    # The spell's timed duration (if any), normalized from whichever data source carries
    # it — both curated and srd524 records have a `duration` string (see spells.parse_duration).
    duration = spells.parse_duration(curated.get("duration") if curated else srd.get("duration"))
    # Does this spell resolve via an ATTACK ROLL (vs a saving throw / auto-hit / buff)?
    # SRD records carry an explicit `attack_roll` bool; a curated spell is an attack spell
    # when its mechanics kind is "attack" (Fire Bolt — a damage cantrip). Drives the #186
    # on-hit-rider defer below: an attack-roll spell's timed effect on a SEPARATE target
    # is a 5e on-hit rider (Guiding Bolt) and must wait for the attack to HIT.
    is_attack_roll_spell = bool(
        (curated.get("mechanics", {}).get("kind") == "attack") if curated
        else srd.get("attack_roll")
    )
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        # SRD: an incapacitated creature can't take an action — and a spell's casting time is
        # (almost always) an action/bonus/reaction, so refuse the cast outright rather than
        # spend the caster's slot / set concentration. Mirrors the attack() guard. (extends #42)
        if combat.is_incapacitated(ch):
            incap = ", ".join(cn.value for cn in ch.conditions if cn in combat.INCAPACITATING)
            raise ValueError(f"{ch.name} is incapacitated ({incap}) and cannot cast a spell")
        # Turn ownership (mirrors attack()): while combat is active and the caster is in
        # the initiative order, an action-cast is legal only on the caster's own turn.
        # An off-turn cast (or an explicitly-declared reaction spell — Shield, Counterspell,
        # an Absorb Elements) is a REACTION: legal once per round, gated by reaction_used.
        # A rejected cast spends NOTHING (no slot, no concentration). Inert with no combat
        # or for a non-combatant caster. Spells aren't subject to the per-Attack-action
        # budget, so this enforces ownership only (not an attack count).
        caster_cb = next(
            (cb for cb in c.combat.order if cb.character_id == character_id), None
        ) if c.combat.active else None
        cast_consumes_reaction = False
        if caster_cb is not None:
            is_current = c.combat.current_combatant_id == character_id
            if is_reaction or not is_current:
                if caster_cb.reaction_used:
                    cur = c.characters.get(c.combat.current_combatant_id)
                    cur_name = cur.name if cur else c.combat.current_combatant_id
                    where = "as a reaction" if is_reaction else f"off-turn (it is {cur_name}'s turn)"
                    raise ValueError(
                        f"{ch.name} has already used its reaction this round and cannot "
                        f"cast {where}. A non-reaction spell is an action on your own turn — "
                        f"advance with next_turn so the order stays in sync."
                    )
                cast_consumes_reaction = True
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
            # A caster concentrates on ONE spell at a time, so casting a concentration
            # spell breaks any prior concentration — and its engine-tracked effect, so the
            # two stay one source of truth. Drop ALL prior concentration effects (covers
            # both replacing a different spell and recasting the same one — the fresh
            # effect registered below is authoritative). Do it BEFORE setting the field.
            combat.expire_concentration_effects(ch)
            ch.concentration = canonical  # replaces (breaks) any prior concentration
        # Register an engine-tracked timed effect so the spell auto-expires (instead of
        # relying on the DM to remember it). Concentration spells hold the effect on the
        # CASTER (so it stays the twin of ch.concentration, one source of truth); a
        # non-concentration buff with an explicit target holds it on that target.
        effect_holder = ch
        pending_rider = None  # set when the effect DEFERS to the spell-attack hit (#186)
        if duration is not None:
            if not concentrates and target_id and target_id != character_id:
                tgt = c.characters.get(target_id)
                if tgt is not None:
                    effect_holder = tgt
            # ON-HIT RIDER DEFER (#186). An ATTACK-ROLL spell whose timed effect lands on a
            # SEPARATE target is a 5e on-hit rider (Guiding Bolt: "on a hit, the next attack
            # against it has Advantage"). The cast and the spell attack are two calls, so the
            # effect must NOT be written to the target at cast time — a MISS would leave a
            # phantom marker (free advantage) and a re-cast would stack a second one. Record a
            # PENDING rider on the CASTER instead; the next attack() (attacker == caster,
            # target == this target) materializes it on a HIT or discards it on a MISS. Save
            # spells (attack_roll False) and self/ally buffs (holder == caster) are unaffected
            # — they fall through to the immediate write below, exactly as before.
            defer_on_hit = (
                is_attack_roll_spell
                and effect_holder is not ch
                and not concentrates
            )
            if defer_on_hit:
                rider = PendingOnHitRider(
                    name=canonical,
                    source_id=character_id,
                    target_id=effect_holder.id,
                    scale=duration["scale"],
                    rounds_remaining=duration["rounds"],
                    until_long_rest=(duration["scale"] == "hours"),
                )
                if duration["scale"] in ("hours", "days"):
                    rider.expires_day, rider.expires_phase_index = _effect_clock_deadline(
                        c, duration["hours"], duration["days"]
                    )
                # Re-casting at the SAME target replaces the pending rider for this spell —
                # no phantom second marker (the bug). Keyed by (target_id, name).
                ch.pending_on_hit_riders = [
                    r for r in ch.pending_on_hit_riders
                    if not (r.target_id == rider.target_id and r.name == canonical)
                ]
                ch.pending_on_hit_riders.append(rider)
                pending_rider = rider
                effect_holder = ch  # nothing written to the target yet; caster carries the pending record
            else:
                eff = ActiveEffect(
                    name=canonical,
                    source_id=character_id,
                    concentration=concentrates,
                    scale=duration["scale"],
                    rounds_remaining=duration["rounds"],
                    until_long_rest=(duration["scale"] == "hours"),
                )
                if duration["scale"] in ("hours", "days"):
                    eff.expires_day, eff.expires_phase_index = _effect_clock_deadline(
                        c, duration["hours"], duration["days"]
                    )
                # Recasting the SAME spell on a holder refreshes (doesn't stack) it.
                effect_holder.active_effects = [
                    e for e in effect_holder.active_effects if e.name != canonical
                ]
                effect_holder.active_effects.append(eff)
        mod = _casting_mod(ch)
        prof = ch.proficiency_bonus
        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        if effect_holder is not ch:
            c.characters[effect_holder.id] = Character.model_validate(
                effect_holder.model_dump(mode="json")
            )
        # An off-turn / reaction cast spends the caster's reaction (the combatant record
        # is separate from the character, so the re-validation above didn't touch it).
        if cast_consumes_reaction and caster_cb is not None:
            caster_cb.reaction_used = True
        # An on-turn cast consumes the combatant's action (mirrors attack()'s action_used
        # bookkeeping). Required so next_turn's PC-skip guard recognises a spell as "acted".
        elif caster_cb is not None and not cast_consumes_reaction:
            c.combat.action_used = True
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
        # Surface the engine-tracked timed effect (if any) so the DM knows it'll
        # auto-expire — and on whom it's tracked. An ON-HIT rider (#186) is NOT on the
        # target yet, so report it as a `pending_effect` keyed to its target: it lands
        # only when the spell attack hits (and the DM resolves that via attack()).
        if pending_rider is not None:
            result["pending_effect"] = {
                "name": canonical,
                "target_id": pending_rider.target_id,
                "scale": duration["scale"],
                "rounds_remaining": duration["rounds"],
                "on_hit": True,
                "note": (
                    "On-hit rider: applied to the target only when the spell attack HITS "
                    "(resolve via attack(attacker=this caster, target=this target)); "
                    "discarded on a miss."
                ),
            }
        elif duration is not None:
            result["active_effect"] = {
                "name": canonical,
                "holder_id": effect_holder.id,
                "scale": duration["scale"],
                "rounds_remaining": duration["rounds"],
                "concentration": concentrates,
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
        # Zone-aware range for a TOUCH/melee spell (S2.7): same rule as a melee
        # attack — advisory, never blocks, inert without declared zones. Position
        # lives on the Combatant records.
        if is_melee and target_id and c.combat.zones:
            tgt = c.characters.get(target_id)
            if tgt is not None:
                az = next((cb.zone for cb in c.combat.order if cb.character_id == character_id), "")
                tz = next((cb.zone for cb in c.combat.order if cb.character_id == target_id), "")
                warn = combat.melee_range_warning(c.combat.zones, ch, tgt, az, tz)
                if warn:
                    result["range_warning"] = warn
        return result


@mcp.tool()
def saving_throw(campaign_id: str, character_id: str, ability: str, dc: int) -> dict:
    """Roll a saving throw for a character against a DC. ability is one of
    str/dex/con/int/wis/cha. Returns the roll and whether it succeeded.

    Enforces the SRD condition rules so save outcomes don't depend on the DM
    remembering them: paralyzed / petrified / stunned / unconscious AUTO-FAIL STR
    and DEX saves; restrained gives DISADVANTAGE on DEX saves. A forced failure
    still reports the roll, plus a `reason`."""
    c = _require(campaign_id)
    ch = _char(c, character_id)
    ab = Ability(ability.lower())
    conds = set(ch.conditions)
    auto_fail = ab in (Ability.STR, Ability.DEX) and bool(conds & combat.SAVE_AUTOFAIL)
    disadvantage = ab == Ability.DEX and Condition.RESTRAINED in conds
    r = dice_mod.roll(f"1d20+{ch.saving_throw_bonus(ab)}", disadvantage=disadvantage)
    out = {"ability": ab.value, "roll": r.total, "natural": r.natural, "dc": dc,
           "success": (not auto_fail) and r.total >= dc}
    if auto_fail:
        forcing = ", ".join(cn.value for cn in ch.conditions if cn in combat.SAVE_AUTOFAIL)
        out["reason"] = f"condition auto-fail: {ch.name} is {forcing} — STR/DEX saves automatically fail"
    if disadvantage:
        out["disadvantage"] = True
    return out


@mcp.tool()
def grapple(
    campaign_id: str,
    attacker_id: str,
    target_id: str,
    save_ability: str = "",
) -> dict:
    """Resolve a Grapple attempt (SRD 5.2 / 2024 Unarmed Strike option).

    The target makes a Strength or Dexterity saving throw (target's choice — by
    default the engine picks whichever gives the higher bonus) against
    DC = 8 + attacker's Strength modifier + attacker's proficiency bonus.

    On a FAILED save: the target gains the Grappled condition (Speed 0, disadvantage
    on attack rolls against anyone other than the grappler). On a SUCCESS: no effect.

    To escape: the grappled creature uses its action and calls `escape_grapple`.

    Returns {dc, save_ability, save_roll, natural, success, applied, attacker, target}.
    `applied` is true when the Grappled condition was added (i.e. the save failed AND
    the target is not immune to the grappled condition)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        attacker = _char(c, attacker_id)
        target = _char(c, target_id)

        # SRD 2024: DC = 8 + attacker STR mod + proficiency bonus (server.py:2643 pattern)
        dc = combat.grapple_save_dc(attacker)

        # Resolve the save ability: explicit override or best of STR/DEX
        if save_ability:
            ab = Ability(save_ability.lower())
        else:
            ab = combat.best_save_ability(target)

        # Auto-fail if the target has a paralysis/stun/petrify/unconscious condition
        conds = set(target.conditions)
        auto_fail = ab in (Ability.STR, Ability.DEX) and bool(conds & combat.SAVE_AUTOFAIL)
        disadvantage = ab == Ability.DEX and Condition.RESTRAINED in conds

        r = dice_mod.roll(
            f"1d20+{target.saving_throw_bonus(ab)}", disadvantage=disadvantage
        )
        success = (not auto_fail) and r.total >= dc

        applied = False
        if not success:
            # Apply grappled condition via the same path add_condition uses
            cond = Condition.GRAPPLED
            immune = cond.value in {i.strip().lower() for i in target.condition_immunities}
            if not immune and cond not in target.conditions:
                target.conditions.append(cond)
                applied = True

        save_campaign(c)

        out: dict = {
            "attacker": attacker.name,
            "target": target.name,
            "dc": dc,
            "save_ability": ab.value,
            "save_roll": r.total,
            "natural": r.natural,
            "success": success,
            "applied": applied,
        }
        if auto_fail:
            forcing = ", ".join(cn.value for cn in target.conditions if cn in combat.SAVE_AUTOFAIL)
            out["reason"] = f"condition auto-fail: {target.name} is {forcing} — STR/DEX saves automatically fail"
        if disadvantage:
            out["disadvantage"] = True
        return out


@mcp.tool()
def shove(
    campaign_id: str,
    attacker_id: str,
    target_id: str,
    mode: str = "prone",
) -> dict:
    """Resolve a Shove attempt (SRD 5.2 / 2024 Unarmed Strike option).

    Same save as Grapple: DC = 8 + attacker's Strength modifier + attacker's proficiency
    bonus. The target makes a STR or DEX save (best of the two by default).

    `mode` controls what happens on a FAILED save:
      - "prone"  (default): the target gains the Prone condition (must use movement to
                 stand; melee attacks against it have Advantage, ranged have Disadvantage).
      - "push":  the target is pushed 5 feet away (no condition; narrative only).

    Returns {dc, save_ability, save_roll, natural, success, mode, applied, pushed, attacker, target}."""
    mode = mode.lower()
    if mode not in ("prone", "push"):
        raise ValueError(f"shove mode must be 'prone' or 'push', got {mode!r}")

    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        attacker = _char(c, attacker_id)
        target = _char(c, target_id)

        dc = combat.grapple_save_dc(attacker)

        # Best of STR/DEX for the target (same rule as grapple — 2024 Unarmed Strike option)
        ab = combat.best_save_ability(target)

        conds = set(target.conditions)
        auto_fail = ab in (Ability.STR, Ability.DEX) and bool(conds & combat.SAVE_AUTOFAIL)
        disadvantage = ab == Ability.DEX and Condition.RESTRAINED in conds

        r = dice_mod.roll(
            f"1d20+{target.saving_throw_bonus(ab)}", disadvantage=disadvantage
        )
        success = (not auto_fail) and r.total >= dc

        applied = False
        pushed = 0
        if not success:
            if mode == "prone":
                cond = Condition.PRONE
                immune = cond.value in {i.strip().lower() for i in target.condition_immunities}
                if not immune and cond not in target.conditions:
                    target.conditions.append(cond)
                    applied = True
            else:  # push
                pushed = 5  # SRD: 5 feet (no grid model; narrative)

        save_campaign(c)

        out: dict = {
            "attacker": attacker.name,
            "target": target.name,
            "dc": dc,
            "save_ability": ab.value,
            "save_roll": r.total,
            "natural": r.natural,
            "success": success,
            "mode": mode,
            "applied": applied,
            "pushed": pushed,
        }
        if auto_fail:
            forcing = ", ".join(cn.value for cn in target.conditions if cn in combat.SAVE_AUTOFAIL)
            out["reason"] = f"condition auto-fail: {target.name} is {forcing} — STR/DEX saves automatically fail"
        if disadvantage:
            out["disadvantage"] = True
        return out


@mcp.tool()
def escape_grapple(
    campaign_id: str,
    character_id: str,
    grappler_id: str,
) -> dict:
    """Attempt to escape a Grapple (SRD 5.2 / 2024).

    The grappled creature uses its action to make a Strength (Athletics) or Dexterity
    (Acrobatics) check — the engine picks whichever gives the higher total — against the
    same DC the grappler used: 8 + grappler's Strength modifier + grappler's proficiency
    bonus.

    On a SUCCESS: the Grappled condition is removed. On a FAILURE: the creature remains
    grappled.

    Returns {dc, skill, skill_roll, natural, success, escaped, character, grappler}."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        escapee = _char(c, character_id)
        grappler = _char(c, grappler_id)

        # Recompute the same DC from the grappler's current sheet
        dc = combat.grapple_save_dc(grappler)

        # SRD: Athletics (STR) or Acrobatics (DEX) — best of the two
        ath_bonus = escapee.skill_bonus("athletics")
        acr_bonus = escapee.skill_bonus("acrobatics")
        if acr_bonus > ath_bonus:
            skill = "acrobatics"
            bonus = acr_bonus
        else:
            skill = "athletics"
            bonus = ath_bonus

        r = dice_mod.roll(f"1d20+{bonus}")
        success = r.total >= dc

        escaped = False
        if success and Condition.GRAPPLED in escapee.conditions:
            escapee.conditions = [x for x in escapee.conditions if x != Condition.GRAPPLED]
            escaped = True

        save_campaign(c)

        return {
            "character": escapee.name,
            "grappler": grappler.name,
            "dc": dc,
            "skill": skill,
            "skill_roll": r.total,
            "natural": r.natural,
            "success": success,
            "escaped": escaped,
        }


def _catalog_describe(rec: dict) -> str:
    """A one-line description for an inventory item granted from the catalog:
    the SRD prose, prefixed with the mechanical tags (kind/rarity/attunement/
    damage/AC) the bare Item model can't hold as structured fields."""
    tags = [rec["kind"]]
    if rec.get("rarity"):
        tags.append(rec["rarity"])
    if rec.get("damage"):
        tags.append(f"{rec['damage']} {rec.get('damage_type', '')}".strip())
    if rec.get("ac"):
        tags.append(f"AC {rec['ac']}")
    if rec.get("requires_attunement"):
        tags.append("requires attunement")
    for p in rec.get("properties", []):
        tags.append(p)
    head = f"[{'; '.join(tags)}] " if tags else ""
    return (head + (rec.get("description") or "")).strip()


def _apply_item_catalog(
    item_name: str, name: str, weight: float, requires_attunement: Optional[bool], description: str
) -> tuple[str, float, bool, str, dict | None]:
    """If `item_name` is given and resolves in the SRD catalog, fill the item's
    name/weight/attunement/description from the real record — but a caller value
    that was explicitly set always wins, so this stays purely additive over the
    free-text path. `requires_attunement` is TRI-STATE: None = take the catalog's
    value; True/False = the caller's explicit override (so you CAN force a catalog
    attuned item down to False — M1). Returns the (possibly enriched) tuple plus
    the catalog record (None if `item_name` empty or unresolved)."""
    if not item_name:
        return name, weight, bool(requires_attunement), description, None
    rec = itemcatalog.resolve(item_name)
    if rec is None:
        return name, weight, bool(requires_attunement), description, None
    attune = rec.get("requires_attunement", False) if requires_attunement is None else requires_attunement
    return (
        name or rec["name"],
        weight if weight else rec.get("weight", 0.0),
        bool(attune),
        description or _catalog_describe(rec),
        rec,
    )


@mcp.tool()
def lookup_item(name: str) -> dict:
    """Look up a single SRD item by name (case-insensitive) in the bundled
    ~960-item catalog (magic items, weapons, armor, gear, potions, etc.). Returns
    the flattened record — {name, kind, rarity, requires_attunement, weight, cost,
    description, properties} plus damage/damage_type for weapons and ac for armor —
    or {"error", "suggestions"} on a miss. Use this (then add_item with
    item_name=...) to grant a REAL item instead of free-texting it."""
    rec = itemcatalog.resolve(name)
    if rec is None:
        return {"error": f"no item named {name!r} in the SRD catalog",
                "suggestions": itemcatalog.suggest(name)}
    return rec


@mcp.tool()
def find_items(query: str, limit: int = 10) -> dict:
    """Search the bundled SRD item catalog by name (case-insensitive substring),
    e.g. find_items("potion") or find_items("sword"). Returns up to `limit`
    matching catalog records (same shape as lookup_item). Empty query lists the
    first `limit` items. The DM's catalog browser for handing out loot."""
    matches = itemcatalog.find(query, max(1, min(int(limit), 50)))
    return {"query": query, "count": len(matches), "items": matches}


@mcp.tool()
def add_item(
    campaign_id: str, character_id: str, name: str = "", quantity: int = 1, weight: float = 0.0,
    requires_attunement: Optional[bool] = None, description: str = "", item_name: str = "",
) -> dict:
    """Add an item to a character's inventory (stacks with an identical unequipped,
    non-attuned item).

    Pass `item_name` to grant a REAL SRD item by name: weight, attunement, and a
    rich description (with damage/AC/rarity tags) are filled from the bundled
    catalog. Any value you also pass explicitly (name, weight, requires_attunement,
    description) overrides the catalog fill. Omit `item_name` for the original
    free-text path (then `name` is required)."""
    name, weight, requires_attunement, description, _ = _apply_item_catalog(
        item_name, name, weight, requires_attunement, description
    )
    if not name:
        raise ValueError("add_item needs a name (or an item_name that resolves in the catalog)")
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
    campaign_id: str, character_id: str, name: str = "", cost_gp: float = -1.0, quantity: int = 1,
    weight: float = 0.0, requires_attunement: Optional[bool] = None, description: str = "", item_name: str = "",
) -> dict:
    """Buy an item: pay cost_gp (making change from the purse) and add it to inventory.
    Raises if the character can't afford it.

    Pass `item_name` to buy a REAL SRD item by name: name, weight, attunement, a
    rich description, AND the catalog price are filled from the bundled catalog
    (leave `cost_gp` unset to charge the SRD price, or pass `cost_gp` to override —
    e.g. a haggled or marked-up price). Omit `item_name` for the original free-text
    path (then `name` and `cost_gp` are required)."""
    name, weight, requires_attunement, description, rec = _apply_item_catalog(
        item_name, name, weight, requires_attunement, description
    )
    if cost_gp < 0:  # sentinel: caller didn't state a price
        cost_gp = rec.get("cost", 0.0) if rec else -1.0
        if cost_gp < 0:
            raise ValueError("buy_item needs cost_gp (or an item_name that resolves in the catalog)")
    if not name:
        raise ValueError("buy_item needs a name (or an item_name that resolves in the catalog)")
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
        # A short rest is ~1 in-world hour: expire sub-hour (minute/round-scale) timed
        # effects and any whose absolute clock deadline already passed. Hour-scale buffs
        # not yet due (e.g. 8h Mage Armor) survive. The clock isn't advanced by a short rest.
        try:
            phase_idx = travel.PHASES.index(c.time_of_day)
        except ValueError:
            phase_idx = 0
        expired: list[dict] = []
        for who in c.characters.values():
            if who.active_effects:
                for name in combat.expire_short_rest_effects(who, c.day, phase_idx):
                    expired.append({"character_id": who.id, "name": name})
        out["expired_effects"] = expired
        save_campaign(c)
        return out


@mcp.tool()
def long_rest(campaign_id: str, character_id: str, watch: str = "") -> dict:
    """Take a long rest: restore all HP, recover half total Hit Dice (min 1), reset
    all spell slots, reduce exhaustion by 1, and end the dying state. The DM should
    call this for each party member. Cannot rest while dead.

    A long rest is ~8 in-world hours (overnight), so it advances the campaign clock
    to the NEXT MORNING (``time_of_day = 'morning'``, rolling ``day`` over) and returns
    the new ``day``/``time_of_day`` so the DM narrates the new dawn. Resting from
    afternoon/evening/night rolls the day forward by one; resting when it is ALREADY
    morning is a clock no-op, so calling long_rest once per party member on the same
    night converges on a single morning instead of each member burning a day. The
    rollover is persisted with the rest.

    CAMP WATCH (Kingmaker-style): the night the rest actually rolls the clock over (the
    FIRST member's rest that overnight — so a per-member party rolls ONCE, not once each)
    rolls a wandering-encounter check for the camp's region. On a hit the result carries a
    `wandering_encounter` payload with a `type` (combat / skill / social / hazard / boon —
    most are NOT a night ambush): combat stages sized foes + an `outlook` for the DM to
    `start_combat`; the other types hand the DM a descriptor (an obstacle to skill-check, a
    visitor to parley, a hazard to save against, or a quiet boon). Pass `watch` (e.g.
    "careful", "camouflaged", "hidden") to lower the chance — a well-kept watch / concealed
    camp is safer. Disabled by `house_rules.wandering_encounters = False`."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if c.combat.active:
            raise ValueError("cannot rest during active combat — call end_combat first")
        ch = _char(c, character_id)
        out = rests.long_rest(ch)
        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        # A long rest is an overnight (~8h) and ends the next morning: advance the
        # in-world clock to "morning", rolling the day over. Reuse the travel helper
        # (advance_clock) for the phase/day math rather than hand-rolling it.
        #
        # The step count is `(morning - now) mod 4`, so afternoon/evening/night roll
        # forward into the next day's morning. When ALREADY at morning the step count
        # is 0 (a no-op): this is deliberate so the documented "call long_rest for each
        # party member" pattern converges on ONE morning — after the first rest sets the
        # clock to morning, the rest of the party resting that same night doesn't each
        # burn another day. (Trade-off: a party that explicitly long-rests during a
        # morning won't roll to the next day; the common overnight case is correct.)
        phases = travel.PHASES
        try:
            cur = phases.index(c.time_of_day)
        except ValueError:
            cur = 0  # normalize an unknown phase to the canonical cycle
        steps = (phases.index("morning") - cur) % len(phases)
        day, tod = travel.advance_clock(c, steps)
        out["day"] = day
        out["time_of_day"] = tod
        # An overnight (~8h) ends timed spell effects: minute/round-scale, hour/day-scale
        # past their deadline, AND every hour-scale buff (Mage Armor/Aid/Longstrider) via
        # long_rest=True — even when resting in the morning is a clock no-op (steps == 0).
        out["expired_effects"] = _expire_clock_effects_all(c, long_rest=True)
        # CAMP-WATCH AMBUSH — gated on `steps > 0` so it rolls ONCE per overnight (the
        # member whose rest actually rolls the clock to morning), NOT once per party
        # member resting the same night. Region = the camp's current location; a careful/
        # hidden `watch` lowers the chance (a camouflage modifier). Same stage-only
        # contract as travel: foes are staged + surfaced, never auto-fought.
        if steps > 0:
            cur_loc = c.locations.get(c.current_location_id) if c.current_location_id else None
            watch_lower = watch.strip().lower()
            modifiers = {"camouflage": True} if watch_lower in (
                "careful", "camouflage", "camouflaged", "hidden", "concealed", "stealth", "stealthy"
            ) else None
            staged = _stage_wandering_encounter(
                c,
                cur_loc.region if cur_loc is not None else "",
                difficulty="medium",
                modifiers=modifiers,
                location_id=c.current_location_id,
            )
            if staged:
                out["wandering_encounter"] = staged
        save_campaign(c)
        # A long rest is the natural moment for a CAMP scene — nudge the DM to gather the party
        # (companions breathe here) when there are companions to gather.
        if any(c.characters.get(i) is not None and c.characters[i].kind == "companion" for i in c.party):
            out["camp_hint"] = ("the party makes camp — call camp_scene to gather the companions "
                                "for a character round before pressing on.")
        return out


@mcp.tool()
def use_resource(campaign_id: str, character_id: str, resource: str, amount: int = 1) -> dict:
    """Spend from a depletable class-resource pool (Rage, Ki, Lay on Hands, Channel
    Divinity, Bardic Inspiration, Sorcery Points, Second Wind, Action Surge, Wild
    Shape, …). Deducts `amount` (default 1; for Lay on Hands pass the hit points to
    spend). Returns ``{ok: True, remaining, max, used}`` on success; ``{ok: False,
    error, remaining, max}`` without changing state when the character lacks that
    pool or hasn't enough left, so the DM gets a clean signal instead of an
    exception. Pools refresh via short_rest / long_rest."""
    if amount < 1:
        raise ValueError("amount must be >= 1")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        res = ch.class_resources.get(resource)
        if res is None:
            return {
                "ok": False,
                "error": f"{ch.name} has no {resource!r} pool",
                "available": sorted(ch.class_resources.keys()),
            }
        remaining = res.max - res.used
        if amount > remaining:
            return {
                "ok": False,
                "error": f"not enough {resource}: need {amount}, have {remaining}",
                "resource": resource,
                "remaining": remaining,
                "max": res.max,
            }
        res.used += amount
        # Action Surge grants a fresh Action this turn — so the Attack-action economy
        # (attack()) must allow another Attack action's worth of strikes. Record it on
        # the combat when spent mid-fight by the CURRENT combatant (resets each turn in
        # next_turn). Additive: out of combat, or for any other resource, this is inert.
        if (
            resource == "action_surge"
            and c.combat.active
            and c.combat.current_combatant_id == character_id
        ):
            c.combat.surge_actions += amount
        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        save_campaign(c)
        new = ch.class_resources[resource]
        return {
            "ok": True,
            "resource": resource,
            "spent": amount,
            "remaining": new.max - new.used,
            "max": new.max,
            "used": new.used,
            "recharge": new.recharge,
        }


@mcp.tool()
def set_class_resource(
    campaign_id: str,
    character_id: str,
    resource: str,
    max: int,
    recharge: str = "short",
    size: str = "",
    used: int = 0,
) -> dict:
    """Register (or update) a CUSTOM depletable pool the SRD class tables don't seed — a
    SUBCLASS, feat, or homebrew resource. The SRD tables only know base-class pools (Rage,
    Ki, Second Wind, Action Surge, Sorcery Points, …), so a Battle Master's **Superiority
    Dice**, a Psi Warrior's **Energy Dice**, an Arcane Archer's **Arcane Shots**, etc. are
    invisible to the engine until you register them here. The engine supplies the *mechanism*
    (a tracked pool `use_resource` spends and rests recharge); YOU supply the subclass numbers
    (the engine stays SRD-only and ships no non-SRD subclass tables).

    `resource` is an id (e.g. "superiority_dice"); `max` the pool size; `recharge` one of
    short|long|none; `size` the die it rolls ("d8" for Superiority Dice) or "" for a point
    pool; `used` how many are already spent (default 0 — a fresh pool). Idempotent: calling
    again with the same id updates max/recharge/size in place and clamps `used` to the new max.
    Marked `custom` so a level-up re-derive never wipes it. Returns the pool's new state.

    Seed these at character creation for any subclassed martial/caster (a Battle Master 5 →
    `set_class_resource(.., "superiority_dice", 6, "short", "d8")`) so the first combat has a
    pool to track Riposte / Precision Attack / etc. against."""
    if int(max) < 0:
        raise ValueError("max must be >= 0")
    rech = recharge if recharge in ("short", "long", "none") else "short"
    rid = resource.strip().lower().replace(" ", "_").replace("-", "_")
    if not rid:
        raise ValueError("resource id must be non-empty")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        mx = int(max)
        u = int(used)
        u = mx if u > mx else (0 if u < 0 else u)  # clamp 0..mx (param `max` shadows the builtin)
        ch.class_resources[rid] = ClassResource(
            max=mx, used=u, recharge=rech, size=size.strip(), custom=True
        )
        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        save_campaign(c)
        res = c.characters[character_id].class_resources[rid]
        return {
            "ok": True,
            "resource": rid,
            "max": res.max,
            "used": res.used,
            "remaining": res.max - res.used,
            "recharge": res.recharge,
            "size": res.size,
            "custom": True,
        }


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


def _clamp_attitude(value: int) -> int:
    """Keep a numeric per-NPC relationship within the -100..+100 scale (0 = neutral)."""
    return max(-100, min(100, int(value)))


@mcp.tool()
def set_attitude(
    campaign_id: str, character_id: str, attitude: str, value: int | None = None
) -> dict:
    """Set an NPC's attitude (free text, e.g. 'guarded', or a track value:
    hostile / wary / indifferent / friendly / helpful).

    Pass `value` (-100..+100, 0 = neutral) to ALSO set the numeric per-NPC
    relationship the dashboard bar reads; omit it to leave the number untouched
    (the free-text track keeps working exactly as before)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.attitude = attitude
        if value is not None:
            ch.attitude_value = _clamp_attitude(value)
        save_campaign(c)
        return {
            "id": ch.id,
            "name": ch.name,
            "attitude": ch.attitude,
            "attitude_value": ch.attitude_value,
        }


@mcp.tool()
def adjust_attitude(campaign_id: str, character_id: str, delta: int) -> dict:
    """Nudge an NPC's numeric relationship (`attitude_value`) by `delta`, clamped to
    -100..+100. For the DM to reward a kindness or punish a betrayal directly, outside
    a social check. Leaves the free-text `attitude` track unchanged."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        old = ch.attitude_value
        ch.attitude_value = _clamp_attitude(ch.attitude_value + delta)
        save_campaign(c)
        return {
            "id": ch.id,
            "name": ch.name,
            "old_attitude_value": old,
            "attitude_value": ch.attitude_value,
        }


PACING_MODES = ("adventure", "downtime")


@mcp.tool()
def set_pacing(campaign_id: str, mode: str) -> dict:
    """Set the campaign's narrative pacing. "adventure" (default): tension, momentum,
    encounters. "downtime": slower — let scenes breathe, lean into social / shopping /
    recovery. Advisory: the DM reads it via get_state and shifts narration density."""
    if mode not in PACING_MODES:
        raise ValueError(f"pacing mode must be one of {PACING_MODES}, got {mode!r}")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        c.pacing_mode = mode
        save_campaign(c)
        return {"id": c.id, "pacing_mode": c.pacing_mode}


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
def social_check(campaign_id: str, actor_id: str, npc_id: str, skill: str, dc: int,
                 target_name: str = "") -> dict:
    """An actor's skill check against an NPC, with read-vs-influence semantics.

    INFLUENCE skills (persuasion / deception / intimidation / …) try to move the
    NPC: on success the attitude improves one step on the track (hostile -> wary ->
    indifferent -> friendly -> helpful), on failure it worsens one step.

    READ skills (insight / perception / investigation) only PERCEIVE the NPC and
    NEVER change its attitude — reading or misreading someone is observer clarity,
    not influence. A read returns a `read` block (an accurate sense of the NPC's
    stance on success; a deliberately uncertain, almost-grasped impression on a
    miss) for the DM to narrate — never a flat attitude penalty for a failed read.

    For a SCENE-LOCAL EXTRA you won't track — a fishmonger, a gate guard, a barkeep —
    pass ``npc_id=""`` and ``target_name="the fishmonger"``: the engine rolls the check
    and returns pass/fail WITHOUT creating or mutating any roster NPC. Use a real
    ``npc_id`` ONLY when you mean to move a tracked NPC's relationship — reusing a
    standing NPC's id as a throwaway target silently corrupts their attitude across the
    whole campaign (QA: a Deception vs a dock extra accidentally shifted a seeded
    companion's standing because her id was passed as the target)."""
    if skill.lower() not in SKILL_ABILITIES:
        raise ValueError(f"unknown skill {skill!r}")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        actor = _char(c, actor_id)
        sk = skill.lower()
        # Ephemeral target: a nameless scene extra. Roll it, persist NOTHING — no roster
        # lookup, no attitude write — so a one-off social beat can't corrupt a tracked NPC.
        if not npc_id:
            if not target_name.strip():
                raise ValueError("pass either npc_id (a tracked NPC) or target_name (a scene extra)")
            r = dice_mod.roll(f"1d20+{actor.skill_bonus(sk)}")
            return {
                "actor": actor.name,
                "npc": target_name.strip(),
                "skill": sk,
                "kind": "read" if sk in READ_SKILLS else "influence",
                "roll": r.total,
                "natural": r.natural,
                "dc": dc,
                "success": r.total >= dc,
                "ephemeral": True,
                "note": "Scene extra — nothing persisted; narrate the outcome.",
            }
        if actor_id == npc_id:
            raise ValueError("actor and npc must be different characters")
        the_npc = _char(c, npc_id)
        if the_npc.kind not in ("npc", "monster"):
            raise ValueError("social_check target must be an NPC or monster")
        r = dice_mod.roll(f"1d20+{actor.skill_bonus(sk)}")
        success = r.total >= dc
        old = the_npc.attitude
        old_value = the_npc.attitude_value
        # Interacting with a tracked NPC means the party has now MET them (even a READ —
        # you can't read someone you haven't encountered). `met` is a discovery flag, not
        # an attitude change, so flipping it here doesn't violate the read-doesn't-influence
        # rule; the read branch saves only if the flag actually changed.
        newly_met = not the_npc.met
        the_npc.met = True
        is_read = sk in READ_SKILLS
        read = None
        if is_read:
            # Perceive, don't influence: attitude (text AND number) is untouched. Persist
            # only if `met` just flipped (the discovery), never the attitude.
            if newly_met:
                save_campaign(c)
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
            # Influence: move BOTH tracks and persist. The free-text track steps one
            # band; the numeric value nudges (+15 on a success, -10 on a failure),
            # clamped to the -100..+100 scale.
            the_npc.attitude = npc_mod.shift_attitude(the_npc.attitude, 1 if success else -1)
            the_npc.attitude_value = _clamp_attitude(old_value + (15 if success else -10))
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
            "old_attitude_value": old_value,
            "new_attitude_value": the_npc.attitude_value,
        }
        if read is not None:
            out["read"] = read
        return out


@mcp.tool()
def skill_check(campaign_id: str, character_id: str, skill: str, dc: int = 0,
                advantage: bool = False, disadvantage: bool = False) -> dict:
    """Roll a skill check for a character with the CORRECT modifier derived from their sheet
    (ability modifier + proficiency if proficient, doubled where they have expertise) — so you
    NEVER hand-compute a bonus, the most common mechanical error. Use this for any non-social
    check (Perception, Investigation, Stealth, Athletics, Arcana, …) against a DC, or just to see
    the roll (``dc=0`` -> roll only, no pass/fail). For a check that targets an NPC's attitude
    (persuade / intimidate / read someone), use ``social_check`` instead. 5e RAW: a skill check
    does NOT auto-succeed on a natural 20 — success is total vs DC; the natural is reported so you
    can flavor a 20/1. Read-only (a check is a roll, not a state change)."""
    sk = skill.strip().lower().replace(" ", "_")
    if sk not in SKILL_ABILITIES:
        raise ValueError(f"unknown skill {skill!r}")
    c = _require(campaign_id)
    ch = _char(c, character_id)
    bonus = ch.skill_bonus(sk)
    r = dice_mod.roll(f"1d20+{bonus}", advantage=advantage, disadvantage=disadvantage)
    out = {
        "character": ch.name, "skill": sk, "modifier": bonus,
        "roll": r.total, "natural": r.natural, "detail": r.detail,
        "advantage": advantage, "disadvantage": disadvantage,
    }
    if dc and dc > 0:
        out["dc"] = dc
        out["success"] = r.total >= dc  # RAW: no auto-success on a nat 20 for a skill check
    return out


# Suggested-DC band keyed off the situation `difficulty` (P-B). The engine SUPPLIES
# the DC so the DM never hand-computes one — the #1 mechanical error per skill_check's
# docstring. HouseRules.difficulty then nudges the whole band (+2 hard / -2 easy).
_PARLEY_DC_BAND = {"easy": 10, "medium": 14, "hard": 18}
# The skills always offered at a social beat, on top of the actor's own proficient/
# expertise skills — the four every parley reaches for regardless of build.
_PARLEY_CORE_SKILLS = ("persuasion", "deception", "intimidation", "insight")


def _lead_pc_id(c: Campaign) -> str:
    """The default actor for a parley: the first PLAYER in the party (the lead PC),
    falling back to the first party member, then any character. '' if the campaign
    has no characters at all."""
    for pid in c.party:
        ch = c.characters.get(pid)
        if ch is not None and ch.kind == "player":
            return pid
    for pid in c.party:
        if pid in c.characters:
            return pid
    return next(iter(c.characters), "")


def _suggested_dc(difficulty: str, house_difficulty: str) -> int:
    """A parley skill DC: the situation band (easy 10 / medium 14 / hard 18) shifted
    by the campaign's house difficulty (+2 when 'hard', -2 when 'easy')."""
    base = _PARLEY_DC_BAND.get(difficulty.strip().lower(), _PARLEY_DC_BAND["medium"])
    shift = {"hard": 2, "easy": -2}.get(house_difficulty, 0)
    return base + shift


@mcp.tool()
def generate_parley_options(
    campaign_id: str,
    actor_id: str = "",
    situation: str = "",
    difficulty: str = "medium",
    skills: Optional[list[str]] = None,
    include_alignment: bool = True,
) -> dict:
    """Call this BEFORE narrating a social encounter or any choice point: it lays out the
    PLAYER'S available options with sheet-correct DCs so you author a real Parley menu
    instead of railroading to one narrated path. This is NOT `companion_advise` (the
    companion's in-character take) or `get_scene` (the authored scene beats) — it returns
    the lead PC's own alignment + the actual skill modifiers off their sheet + a suggested
    DC per skill, so you write 2-4 tagged choices WITHOUT hand-computing anything.

    Returns ``{actor, alignment, skills:[{skill, modifier, suggested_dc}], free_form: true,
    guidance}``. `actor_id` defaults to the lead PC. `skills` defaults to the actor's
    proficient/expertise skills plus persuasion/deception/intimidation/insight. `modifier`
    is the sheet-correct bonus (ability mod + proficiency, doubled on expertise — the engine
    computes it via the character's skill_bonus, you never invent it). `suggested_dc` comes
    from a fixed band (easy 10 / medium 14 / hard 18) keyed off `difficulty`, shifted +2 when
    HouseRules.difficulty is 'hard' and -2 when 'easy'.

    This tool authors NOTHING and never rolls — it hands you slots, not lines. Voice the
    prose yourself, tag each option by alignment + skill+DC + a reputation/consequence hint,
    and ALWAYS leave a free-form path (`free_form` is always true). Then ROUTE the pick:
    a chosen skill option -> ``skill_check(actor, skill, dc)``; a social option vs a tracked
    NPC -> ``social_check`` (or ``target_name`` for a scene extra); a combat option ->
    ``start_combat``; a free-form/alignment beat you adjudicate, then ``record_decision``
    (and optional ``adjust_reputation``). Read-only."""
    c = _require(campaign_id)
    aid = actor_id or _lead_pc_id(c)
    if not aid:
        raise ValueError("campaign has no characters to parley with; create the PC first")
    actor = _char(c, aid)

    # Default skill set: the actor's own proficient/expertise skills UNION the four core
    # social skills every parley reaches for. Dedup while preserving a stable order
    # (sheet skills first, then any core skills not already present).
    if skills is None:
        chosen = list(dict.fromkeys(actor.skill_proficiencies + actor.skill_expertise))
        for s in _PARLEY_CORE_SKILLS:
            if s not in chosen:
                chosen.append(s)
    else:
        chosen = list(dict.fromkeys(s.strip().lower().replace(" ", "_") for s in skills))

    dc = _suggested_dc(difficulty, c.house_rules.difficulty)
    skill_rows: list[dict] = []
    for sk in chosen:
        if sk not in SKILL_ABILITIES:
            raise ValueError(f"unknown skill {sk!r}")
        # modifier comes straight off the sheet — never recomputed by hand
        skill_rows.append({"skill": sk, "modifier": actor.skill_bonus(sk), "suggested_dc": dc})

    out: dict = {
        "actor": actor.name,
        "skills": skill_rows,
        "free_form": True,
        "guidance": (
            "Author 2-4 SHORT options tagged by alignment + skill+DC + a "
            "reputation/consequence hint, then ALWAYS leave a free-form path. Voice the "
            "prose yourself — these are slots, not lines. Route a chosen skill option -> "
            "skill_check(actor, skill, dc); a social option vs an NPC -> social_check; a "
            "combat option -> start_combat."
        ),
    }
    if include_alignment:
        out["alignment"] = actor.alignment
    return out


def _resolve_monster_xps(
    c: Campaign, monster_xps: Optional[list[int]], monster_ids: Optional[list[str]]
) -> list[int]:
    """The per-monster XP list for an outlook: prefer an explicit `monster_xps`, else
    resolve each id in `monster_ids` to its XP — first from a staged monster Character's
    `xp_value` (already in the campaign), else from the bestiary stat block by name."""
    if monster_xps:
        return [int(x) for x in monster_xps]
    xps: list[int] = []
    for mid in monster_ids or []:
        ch = c.characters.get(mid)
        if ch is not None and ch.xp_value > 0:
            xps.append(int(ch.xp_value))
            continue
        sb = bestiary.stat_block(mid)
        if sb and int(sb.get("xp") or 0) > 0:
            xps.append(int(sb["xp"]))
            continue
        raise ValueError(f"could not resolve XP for monster {mid!r}; pass monster_xps instead")
    return xps


@mcp.tool()
def encounter_outlook(
    campaign_id: str,
    monster_xps: Optional[list[int]] = None,
    monster_ids: Optional[list[str]] = None,
) -> dict:
    """Call this BEFORE staging a fight to see how over-matched it is against the LIVING
    party: it makes the SRD over-match math legible so the balancing doctrine is followable.
    The engine NEVER alters combat — the dragon stays a dragon. Returns the SRD difficulty
    band PLUS an `overmatch_ratio` (the band alone caps at 'deadly' and can't tell a
    winnable 1.12x troll from a guaranteed-wipe 6.25x dragon) and a `must_offer_out` flag
    that fires only in the unwinnable low-level zone.

    Returns ``{band, overmatch_ratio, avg_party_level, must_offer_out, guidance}``.
    `overmatch_ratio = adjusted_xp(xps) / xp_thresholds(party_levels)["deadly"]`, where xps
    come from `monster_xps` or are resolved from `monster_ids` (a staged foe's xp_value, or
    the bestiary by name). `must_offer_out = (avg_party_level <= 5) and (overmatch_ratio >=
    2.0)` — empirically the line that passes a deadly-but-fair troll and catches a dragon.

    When `must_offer_out` is true, you MUST surface at least one non-combat branch WITH A
    COST (escape leaving something behind, parley/relent, a hazard buying retreat) via
    generate_parley_options — do NOT auto-soften, do NOT TPK. Over level 5, a chosen fight
    may kill. Read-only."""
    c = _require(campaign_id)
    xps = _resolve_monster_xps(c, monster_xps, monster_ids)
    if not xps:
        raise ValueError("pass monster_xps or monster_ids — no XP to evaluate")
    levels = _party_levels(c)
    return _outlook_for_xps(levels, xps)


def _outlook_for_xps(party_levels: list[int], monster_xps: list[int]) -> dict:
    """The SRD over-match outlook for `monster_xps` vs `party_levels` — the shared
    math behind both the `encounter_outlook` tool AND the wandering-encounter staging
    seam (so a staged combat carries the exact same band/ratio/flag the DM would get
    from calling the tool by hand). Pure: no campaign I/O.

    Returns ``{band, overmatch_ratio, avg_party_level, must_offer_out, guidance}``.
    `overmatch_ratio = adjusted_xp / deadly_threshold`; `must_offer_out =
    avg_party_level <= 5 and overmatch_ratio >= 2.0` (the troll/dragon boundary)."""
    assert party_levels, "party_levels must be non-empty"
    avg_party_level = sum(party_levels) / len(party_levels)
    deadly = encounter.xp_thresholds(party_levels)["deadly"]
    adjusted = encounter.adjusted_xp(monster_xps)
    # deadly is always > 0 (the SRD table has no zero), so this division is safe.
    overmatch_ratio = round(adjusted / deadly, 2)
    band = encounter.encounter_difficulty(party_levels, monster_xps)
    must_offer_out = (avg_party_level <= 5) and (overmatch_ratio >= 2.0)

    if must_offer_out:
        guidance = (
            f"This fight is ~{overmatch_ratio}x over the party's deadly budget at level "
            "<=5. The world is SET — do NOT auto-soften it, and do NOT TPK. REQUIRED: "
            "surface at least one non-combat branch with a COST (escape leaving something "
            "behind, parley/relent, a hazard that buys retreat) via generate_parley_options. "
            "Over level 5, a chosen fight may kill."
        )
    elif band == "deadly":
        guidance = (
            f"Deadly but winnable (~{overmatch_ratio}x the deadly budget). A real, scary "
            "fight — let them sweat; no escape branch is mandated."
        )
    else:
        guidance = (
            f"A fair {band} encounter (~{overmatch_ratio}x the deadly budget). Run it "
            "straight — no out required."
        )
    return {
        "band": band,
        "overmatch_ratio": overmatch_ratio,
        "avg_party_level": avg_party_level,
        "must_offer_out": must_offer_out,
        "guidance": guidance,
    }


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


def _camp_arc_summary(comp) -> Optional[dict]:
    """Read-only summary of a companion's relationship arc for a camp scene: each gate + how close
    the next LOCKED gate is to unlocking (by attitude_value). None when the companion has no arc."""
    arc = getattr(comp, "arc", None)
    if arc is None:
        return None
    gates = [{"kind": g.kind, "threshold": g.threshold, "unlocked": g.unlocked} for g in arc.arc_gates]
    locked = [g for g in arc.arc_gates if not g.unlocked]
    out: dict = {"attitude_value": comp.attitude_value, "gates": gates}
    if locked:
        nxt = min(locked, key=lambda g: g.threshold)
        out["next_gate"] = {"kind": nxt.kind, "threshold": nxt.threshold,
                            "points_away": max(0, nxt.threshold - comp.attitude_value)}
    return out


def _camp_beat_view(c: Campaign, beat: CampBeatCandidate) -> dict:
    out = beat.model_dump(mode="json")
    participants = [c.characters[cid] for cid in beat.companion_ids if cid in c.characters]
    out["participants"] = [
        {"id": comp.id, "name": comp.name, "voice_id": comp.voice_id} for comp in participants
    ]
    if beat.kind == "solo" and participants:
        comp = participants[0]
        out.update(
            {
                "companion": comp.name,
                "voice_id": comp.voice_id,
                "personality": comp.personality,
                "attitude": comp.attitude,
                "attitude_value": comp.attitude_value,
                "arc": _camp_arc_summary(comp),
            }
        )
    return out


@mcp.tool()
def camp_scene(campaign_id: str, setting: str = "") -> dict:
    """Gather the party for a CAMP scene — the hub where companions breathe between adventures
    (around the campfire, at the tavern bar, in a safe house). It returns deterministic
    scheduled frames for living companions: `voice_id`/participants, current standing
    (`attitude` + `attitude_value` for solo beats), player-facing prompts, and read-only
    relationship `arc` summaries. Run it at a long rest / downtime / on reaching a safe hub:
    voice the returned frames, let the player talk to any of them, and play any arc beat that's
    ripe — then `check_companion_arc` to fire/mark it and `record_camp_beat` to persist that the
    camp beat happened. Read-only (advice + state; it changes nothing)."""
    c = _require(campaign_id)
    companions = [
        c.characters[i]
        for i in c.party
        if i in c.characters
        and c.characters[i].kind == "companion"
        and not c.characters[i].dead
        and c.characters[i].current_hp > 0
    ]
    sit = setting.strip() or "an unpressured moment in camp — the day's danger behind you, the fire low"
    scheduled = companion_banter.schedule_camp_beats(c, max_beats=len(companions))
    beats = [_camp_beat_view(c, beat) for beat in scheduled]
    return {
        "setting": sit,
        "present": [comp.name for comp in companions],
        "beats": beats,
        "camp_beat_history": {
            "records": len(c.camp_beats.records),
            "solo_cooldown_days": c.camp_beats.solo_cooldown_days,
            "pair_cooldown_days": c.camp_beats.pair_cooldown_days,
            "max_records": c.camp_beats.max_records,
        },
        "guidance": (
            "Run a camp round: give each companion a moment IN TURN (a worry, a memory surfaced, a "
            "question for the player, banter with another companion), grounded in their standing + "
            "recent events. Let the player talk to any of them — this is character time, not a menu. "
            "If an arc gate is one beat from unlocking, lean toward it; play any ripe beat and then "
            "`check_companion_arc` to fire it. Record a played camp beat with `record_camp_beat`; "
            "reading this scene alone does not advance beat history."
        ),
    }


def _camp_beat_keys(record: CampBeatRecord) -> set[str]:
    keys = {record.id, record.cooldown_key}
    if record.pair_key:
        keys.add(f"pair:{record.pair_key}")
    return {key for key in keys if key}


def _camp_beat_cooldown_days(c: Campaign, record: CampBeatRecord) -> int:
    if record.kind == "pair_banter":
        return c.camp_beats.pair_cooldown_days
    return c.camp_beats.solo_cooldown_days


def _raise_if_camp_beat_on_cooldown(c: Campaign, record: CampBeatRecord) -> None:
    keys = _camp_beat_keys(record)
    cooldown_days = max(0, _camp_beat_cooldown_days(c, record))
    for existing in c.camp_beats.records:
        if not keys.intersection(_camp_beat_keys(existing)):
            continue
        if existing.day == c.day:
            raise ValueError(f"camp beat {record.id!r} was already recorded today")
        if cooldown_days > 0 and c.day - existing.day < cooldown_days:
            ready_day = existing.day + cooldown_days
            raise ValueError(f"camp beat {record.id!r} is on cooldown until day {ready_day}")


def _compact_camp_beat_records(c: Campaign) -> None:
    latest_by_key: dict[str, tuple[int, int, CampBeatRecord]] = {}
    for index, record in enumerate(c.camp_beats.records):
        key = record.cooldown_key or record.id
        current = latest_by_key.get(key)
        if current is None or (record.day, index) >= (current[0], current[1]):
            latest_by_key[key] = (record.day, index, record)
    retained = sorted(latest_by_key.values(), key=lambda item: (item[0], item[1]))
    c.camp_beats.records = [record for _, _, record in retained[-c.camp_beats.max_records :]]


@mcp.tool()
def record_camp_beat(
    campaign_id: str,
    beat_id: str,
    companion_ids: Optional[list] = None,
    kind: str = "",
    tags: Optional[list] = None,
    note: str = "",
    resolved: bool = False,
) -> dict:
    """Persist that a camp beat actually fired.

    This is the explicit write path for camp-beat history. `camp_scene` and the pure scheduler
    are read-only; they only propose frames. For normal use, pass a `beat_id` returned by
    `camp_scene`. The optional explicit fields support recording an externally-framed beat
    without letting prompt generation mutate state."""
    if not beat_id.strip():
        raise ValueError("beat_id is required")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        candidates = companion_banter.schedule_camp_beats(c, max_beats=100)
        candidate = next((beat for beat in candidates if beat.beat_id == beat_id), None)
        if candidate is not None:
            ids = list(candidate.companion_ids)
            record_kind = candidate.kind
            record_tags = list(candidate.tags)
            cooldown_key = candidate.cooldown_key
            pkey = candidate.pair_key
        else:
            ids = sorted({str(cid) for cid in (companion_ids or []) if str(cid).strip()})
            if not ids:
                raise ValueError(f"beat_id {beat_id!r} is not currently scheduled; provide companion_ids to record explicitly")
            for cid in ids:
                comp = _char(c, cid)
                if comp.kind != "companion" or comp.dead or comp.current_hp <= 0:
                    raise ValueError(f"{cid!r} is not a living companion")
            record_kind = kind or ("pair_banter" if len(ids) == 2 else "solo")
            if record_kind == "pair_banter" and len(ids) != 2:
                raise ValueError("pair_banter records require exactly two living companions")
            if record_kind == "solo" and len(ids) != 1:
                raise ValueError("solo records require exactly one living companion")
            record_tags = [str(tag) for tag in (tags or []) if str(tag).strip()]
            pkey = companion_banter.pair_key(*ids) if record_kind == "pair_banter" else ""
            cooldown_key = f"pair:{pkey}:{beat_id}" if pkey else f"solo:{ids[0]}:{beat_id}"
        record = CampBeatRecord(
            id=beat_id,
            day=c.day,
            companion_ids=ids,
            kind=record_kind,  # type: ignore[arg-type]
            tags=record_tags,
            resolved=resolved,
            note=note,
            cooldown_key=cooldown_key,
            pair_key=pkey,
        )
        _raise_if_camp_beat_on_cooldown(c, record)
        c.camp_beats.records.append(record)
        _compact_camp_beat_records(c)
        save_campaign(c)
        return {"record": record.model_dump(mode="json"), "history_count": len(c.camp_beats.records)}


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
def log_event(campaign_id: str, kind: str, text: str, speaker: str = "", payload: Optional[dict] = None) -> dict:
    """Record a story beat in the current session log (kind: narration | dialogue
    | roll | system | combat). Auto-starts a session if none is active. Powers
    recaps and post-compaction recovery."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        entry = _log_session_entry(c, kind=kind, text=text, speaker=speaker, payload=payload)
        save_campaign(c)
        return {"session_id": c.active_session_id, "logged": entry.model_dump()}


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
    hits = ledger_mod.recall(campaign_id, query, kinds=kinds, limit=limit)
    # Frame the recalled memory with the campaign's authoritative world-state (the chosen
    # ending), exactly as lookup_lore does — so both surfaces lead with the same canon and
    # the DM never narrates against it. recall already reads overlay-de-conflicted c.lore;
    # the header makes that authority explicit + consistent across the two retrieval tools.
    # No world_state (base/no-ending campaign) -> no header, byte-identical to before.
    # load_campaign (not _require) so a missing campaign stays a no-op (recall returns []),
    # never a new raise the original wrapper didn't have.
    #
    # `kinds` honor: the synthetic header is a SYNTHETIC row of kind "world_state" (not a
    # real ledger.KINDS value). If the caller filtered to a kinds subset that doesn't ask
    # for "world_state", prepending it anyway would return an UNREQUESTED row that the
    # filter just excluded — so respect the filter and skip the header in that case. An
    # unfiltered recall (kinds falsy) still leads with the header as before; a caller that
    # explicitly lists "world_state" opts back in.
    c = load_campaign(campaign_id)
    header_allowed = (not kinds) or ("world_state" in kinds)
    if c is not None and c.world_state is not None and header_allowed:
        header = {
            "kind": "world_state", "who": "world",
            "text": c.world_state.canon_header(), "ref": "", "day": c.day,
        }
        hits = [header] + hits
    return {"query": query, "hits": hits}


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
        dev = worldsim.tick_backlog(c)
        strategic = worldsim.tick_strategic(c)
        save_campaign(c)
        return {
            "current_day": c.day,
            "world_beats": [{"thread_id": b.thread_id, "text": b.text} for b in beats],
            "pending": [{"thread_id": b.thread_id, "trigger_day": b.trigger_day} for b in worldsim.pending_threads(c)],
            # The proactive backlog's off-screen developments that fired this tick, plus what's
            # still queued (the goal-traced living-world layer, distinct from the thread beats).
            "world_developments": [_backlog_dict(d) for d in dev],
            "pending_developments": [
                {"id": p.id, "kind": p.kind, "goal_ref": p.goal_ref, "trigger_day": p.trigger_day}
                for p in worldsim.pending_backlog(c)
            ],
            "strategic_events": strategic,
        }


def _require_companion(c: Campaign, companion_id: str) -> Character:
    """Resolve a companion by id and assert it IS a companion — so an arc isn't
    attached to a PC, NPC, or monster by mistake."""
    ch = _char(c, companion_id)
    if ch.kind != "companion":
        raise ValueError(f"character {companion_id!r} is a {ch.kind!r}, not a companion")
    return ch


_COMPANION_QUEST_STATUSES = {"locked", "available", "active", "resolved", "failed"}
_TRACKED_QUEST_STATUSES = {"active", "completed", "failed"}


def _companion_quest_status(status: str, field: str) -> str:
    s = (status or "").strip().lower()
    if s not in _COMPANION_QUEST_STATUSES:
        raise ValueError(f"{field} must be locked|available|active|resolved|failed, got {status!r}")
    return s


def _tracked_quest_status(status: str) -> str:
    s = (status or "").strip().lower()
    s = {"resolved": "completed", "open": "active", "available": "active"}.get(s, s)
    if s not in _TRACKED_QUEST_STATUSES:
        raise ValueError(f"quest_status must be active|completed|failed (or resolved), got {status!r}")
    return s


def _quest_projection_status(companion_status: str) -> str:
    return {
        "available": "active",
        "active": "active",
        "resolved": "completed",
        "failed": "failed",
    }.get(companion_status, "")


def _validate_companion_quest_arc_links(c: Campaign, arc: CompanionQuestArc) -> None:
    seen_stages: set[str] = set()
    for stage in arc.stages:
        if stage.id in seen_stages:
            raise ValueError(f"duplicate companion quest stage id {stage.id!r}")
        seen_stages.add(stage.id)
    for qid in arc.quest_ids:
        if qid not in c.quests:
            raise ValueError(f"no tracked quest {qid!r} for companion quest arc {arc.id!r}")
    for stage in arc.stages:
        if stage.quest_id and stage.quest_id not in c.quests:
            raise ValueError(f"no tracked quest {stage.quest_id!r} for companion quest stage {stage.id!r}")


def _validate_companion_arc_quest_links(c: Campaign, companion_id: str, arc: CompanionArc) -> None:
    for gate in arc.arc_gates:
        if gate.kind != "personal_quest":
            continue
        if gate.stage_id and not gate.quest_arc_id:
            raise ValueError("personal_quest gate stage_id requires quest_arc_id")
        if not gate.quest_arc_id:
            continue
        quest_arc = c.companion_quest_arcs.get(gate.quest_arc_id)
        if quest_arc is None:
            raise ValueError(f"no companion quest arc {gate.quest_arc_id!r}")
        if quest_arc.companion_id and quest_arc.companion_id != companion_id:
            raise ValueError(
                f"companion quest arc {gate.quest_arc_id!r} belongs to "
                f"{quest_arc.companion_id!r}, not {companion_id!r}"
            )
        if gate.stage_id and not any(stage.id == gate.stage_id for stage in quest_arc.stages):
            raise ValueError(f"no stage {gate.stage_id!r} in companion quest arc {gate.quest_arc_id!r}")


def _validate_replacing_companion_quest_arc(c: Campaign, arc: CompanionQuestArc) -> None:
    for ch in c.characters.values():
        if ch.arc is None:
            continue
        for gate in ch.arc.arc_gates:
            if gate.kind != "personal_quest" or gate.quest_arc_id != arc.id:
                continue
            if arc.companion_id and arc.companion_id != ch.id:
                raise ValueError(
                    f"existing personal_quest gate on {ch.id!r} references companion quest arc {arc.id!r}, "
                    f"which would belong to {arc.companion_id!r}"
                )
            if gate.stage_id and not any(stage.id == gate.stage_id for stage in arc.stages):
                raise ValueError(
                    f"existing personal_quest gate on {ch.id!r} references missing stage {gate.stage_id!r} "
                    f"in companion quest arc {arc.id!r}"
                )


def _companion_quest_arc_view(c: Campaign, arc: CompanionQuestArc) -> dict:
    companion = c.characters.get(arc.companion_id)
    out = arc.model_dump()
    out["companion_name"] = companion.name if companion else ""
    out["linked_quests"] = [
        {"id": q.id, "title": q.title, "status": q.status}
        for qid in arc.quest_ids
        if (q := c.quests.get(qid)) is not None
    ]
    return out


@mcp.tool()
def check_companion_arc(campaign_id: str, companion_id: str = "") -> dict:
    """Advance companions' relationship arcs against the CURRENT state and surface the
    moments that just became live — the storytelling default for making an ally's bond
    or betrayal REAL, not a line that evaporates. Call it each beat (like
    `check_consequences`): when a gate UNLOCKS (a personal_quest opens, a romance turns,
    loyalty deepens) play that beat; when a betrayal AGENDA FIRES, the companion turns
    NOW — resolve it as a real `attack`, never soften it to narration.

    Pass `companion_id` to evaluate one companion, or leave it blank to evaluate ALL
    companions that carry an arc. A gate unlocks when the companion's `attitude_value`
    (the approval gauge) reaches its threshold; the sealed agenda fires when its trigger
    holds. Idempotent — a gate/agenda already resolved on a prior call is NOT reported
    again, so calling every beat is safe.

    A result may also carry an ADVISORY `betrayal_warning` (Layer 2): when a companion's
    unfired `attitude_below` agenda sits in the danger band (~ -20..-40 approval), this
    telegraphs an approaching turn so you can FORESHADOW it — it never fires the agenda
    or mutates state. A `decision_flag` set by a recorded choice (set_flag /
    record_decision sets_flag) spikes that betrayal's odds; the warning flags when one is
    already active."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if companion_id:
            targets = [_require_companion(c, companion_id)]
        else:
            targets = [ch for ch in c.characters.values() if ch.kind == "companion" and ch.arc is not None]
        results = []
        for ch in targets:
            if ch.arc is None:
                continue
            res = companion_arc.evaluate(ch, c)
            if (
                res["newly_unlocked"]
                or res["agenda_fired"]
                or res.get("companion_quest_unlocks")
                or res.get("betrayal_warning")
            ):
                results.append({"companion_id": ch.id, "name": ch.name, **res})
        save_campaign(c)
        return {"results": results}


@mcp.tool()
def set_companion_arc(campaign_id: str, companion_id: str, arc: dict) -> dict:
    """Attach (or REPLACE) a companion's relationship arc + sealed agenda, so the DM —
    or the ending-seed loader — can author what a bond grows into and what a saboteur is
    planning. `arc` is `{arc_gates: [{kind, threshold, note?}], agenda: {trigger, value?,
    note?}}` where gate `kind` is personal_quest|romance|loyalty|betrayal and agenda
    `trigger` is attitude_below|day_reached|party_vulnerable|prize_seized. The companion
    must exist and be a companion. `check_companion_arc` then evaluates it each beat."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _require_companion(c, companion_id)
        parsed = CompanionArc.model_validate(arc)
        _validate_companion_arc_quest_links(c, companion_id, parsed)
        ch.arc = parsed
        save_campaign(c)
        return {"id": ch.id, "name": ch.name, "arc": ch.arc.model_dump()}


@mcp.tool()
def set_companion_quest_arc(campaign_id: str, companion_id: str, arc: dict) -> dict:
    """Create or replace an engine-owned companion personal quest arc.

    CompanionQuestArc is the lifecycle owner for personal quests. Linked tracked Quests
    remain optional player-facing projections and must already exist when referenced."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        _require_companion(c, companion_id)
        data = dict(arc or {})
        if data.get("companion_id") and data["companion_id"] != companion_id:
            raise ValueError(
                f"companion quest arc companion_id {data['companion_id']!r} does not match {companion_id!r}"
            )
        data["companion_id"] = companion_id
        parsed = CompanionQuestArc.model_validate(data)
        _validate_companion_quest_arc_links(c, parsed)
        _validate_replacing_companion_quest_arc(c, parsed)
        c.companion_quest_arcs[parsed.id] = parsed
        save_campaign(c)
        return {"companion_quest_arc": _companion_quest_arc_view(c, parsed)}


@mcp.tool()
def get_companion_quest_arcs(campaign_id: str, companion_id: str = "", status: str = "") -> dict:
    """Read companion personal quest arcs, optionally filtered by companion and lifecycle
    status. Read-only; does not evaluate gates or advance quests."""
    c = _require(campaign_id)
    if companion_id:
        _require_companion(c, companion_id)
    wanted_status = _companion_quest_status(status, "status") if status else ""
    arcs = list(c.companion_quest_arcs.values())
    if companion_id:
        arcs = [a for a in arcs if a.companion_id == companion_id]
    if wanted_status:
        arcs = [a for a in arcs if a.status == wanted_status]
    arcs.sort(key=lambda a: (a.companion_id, a.title, a.id))
    return {"companion_quest_arcs": [_companion_quest_arc_view(c, a) for a in arcs], "count": len(arcs)}


@mcp.tool()
def advance_companion_quest_arc(
    campaign_id: str,
    arc_id: str,
    status: str = "",
    stage_id: str = "",
    stage_status: str = "",
    quest_id: str = "",
    quest_status: str = "",
) -> dict:
    """Explicitly advance a companion personal quest arc and optionally project that
    change into linked tracked Quests.

    `status` updates the CompanionQuestArc. `stage_id` + `stage_status` updates one
    stage. `quest_id` links a tracked Quest to the arc (and stage when provided).
    If a quest is linked and no `quest_status` is given, the engine derives a one-way
    projection from the companion status: available/active -> active, resolved ->
    completed, failed -> failed. Prose never advances this state."""
    next_status = _companion_quest_status(status, "status") if status else ""
    next_stage_status = _companion_quest_status(stage_status, "stage_status") if stage_status else ""
    next_quest_status = _tracked_quest_status(quest_status) if quest_status else ""
    if not any((next_status, next_stage_status, quest_id, next_quest_status)):
        raise ValueError("advance_companion_quest_arc requires status, stage_status, quest_id, or quest_status")
    if next_quest_status and not (next_status or next_stage_status):
        raise ValueError("quest_status requires status or stage_status so Quest projection follows companion arc state")

    arc_projection = _quest_projection_status(next_status) if next_status else ""
    stage_projection = _quest_projection_status(next_stage_status) if next_stage_status else ""
    if next_status == "locked" and stage_projection:
        raise ValueError(f"stage_status {next_stage_status!r} cannot advance while companion quest status is 'locked'")
    if arc_projection and stage_projection and arc_projection != stage_projection:
        raise ValueError(
            f"status {next_status!r} and stage_status {next_stage_status!r} imply conflicting "
            f"quest projections {arc_projection!r} and {stage_projection!r}"
        )
    derived_quest_status = arc_projection or stage_projection
    derived_from = next_status if arc_projection or (next_status and not stage_projection) else next_stage_status
    if next_quest_status and derived_from and not derived_quest_status:
        raise ValueError(f"quest_status cannot project from companion quest status {derived_from!r}")
    if next_quest_status and derived_quest_status and next_quest_status != derived_quest_status:
        raise ValueError(
            f"quest_status {next_quest_status!r} is inconsistent with companion quest status "
            f"{derived_from!r}; use {derived_quest_status!r}"
        )
    final_quest_status = next_quest_status or derived_quest_status

    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        current = c.companion_quest_arcs.get(arc_id)
        if current is None:
            raise ValueError(f"no companion quest arc {arc_id!r}")
        data = current.model_dump(mode="json")
        if next_status:
            data["status"] = next_status

        stage_data = None
        if stage_id:
            for stage in data.get("stages", []):
                if stage.get("id") == stage_id:
                    stage_data = stage
                    break
            if stage_data is None:
                raise ValueError(f"no stage {stage_id!r} in companion quest arc {arc_id!r}")
            if next_stage_status:
                stage_data["status"] = next_stage_status
        elif next_stage_status:
            raise ValueError("stage_status requires stage_id")

        if quest_id:
            if quest_id not in c.quests:
                raise ValueError(f"no tracked quest {quest_id!r}")
            quest_ids = list(data.get("quest_ids", []))
            if quest_id not in quest_ids:
                quest_ids.append(quest_id)
            data["quest_ids"] = quest_ids
            if stage_data is not None:
                stage_data["quest_id"] = quest_id

        next_arc = CompanionQuestArc.model_validate(data)
        _validate_companion_quest_arc_links(c, next_arc)

        quest_targets: list[str] = []
        if final_quest_status:
            if quest_id:
                quest_targets = [quest_id]
            elif stage_data is not None:
                stage_quest_id = str(stage_data.get("quest_id") or "")
                if not stage_quest_id:
                    raise ValueError("stage quest projection requires quest_id or a linked stage quest_id")
                quest_targets = [stage_quest_id]
            else:
                quest_targets = list(next_arc.quest_ids)
            if not quest_targets:
                raise ValueError("quest_status or projected quest status requires quest_id or an existing linked quest")

        quest_updates = []
        for qid in quest_targets:
            q = c.quests.get(qid)
            if q is None:
                raise ValueError(f"no tracked quest {qid!r}")
            if q.status != final_quest_status:
                quest_updates.append({"quest_id": q.id, "previous_status": q.status, "status": final_quest_status})

        c.companion_quest_arcs[next_arc.id] = next_arc
        for update in quest_updates:
            c.quests[update["quest_id"]].status = update["status"]  # type: ignore[assignment]
        save_campaign(c)
        return {
            "companion_quest_arc": _companion_quest_arc_view(c, next_arc),
            "quest_updates": quest_updates,
        }


@mcp.tool()
def set_flag(campaign_id: str, flag: str, value: bool = True) -> dict:
    """Set a world-state boolean flag the engine gates events on — notably
    `prize_seized`, which arms a companion's `prize_seized` betrayal agenda (the goal is
    in hand, and the knife comes out). Returns the full flag map."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        c.flags[flag] = bool(value)
        save_campaign(c)
        return {"flags": dict(c.flags)}


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


def _evolution_note(quest_id: str) -> str:
    """The deterministic `Consequence.note` tag that links a scheduled evolution
    back to the quest it grew from. Used both to author the link and to guard
    against double-scheduling on a re-resolve. Format: ``evolves_from:<quest_id>``."""
    return f"evolves_from:{quest_id}"


def _maybe_schedule_quest_evolution(c: Campaign, q: Quest) -> Optional[Consequence]:
    """Rule-of-three evolution (Quest & Arc engine, Layer 1). When a quest reaches
    a RESOLVED terminal state (status == "completed") AND carries an ``evolves_to``
    hook/seed, SCHEDULE a follow-on ``Consequence`` so the thread lingers and
    surfaces later via the existing ``check_consequences`` path (immediately if
    ``callback_in_days`` is 0, or on a later return). The DM weaves the prompt; the
    engine never auto-acts on the fiction.

    ADDITIVE + idempotent: empty ``evolves_to`` schedules nothing (today's behavior).
    The guard is the deterministic ``evolves_from:<quest_id>`` note — if an evolution
    consequence for this quest already exists, re-resolving does NOT double-schedule.
    Caller MUST hold the campaign lock (engine is the sole writer). Returns the new
    Consequence, or None if nothing was scheduled.

    NOTE on traceability: the link back to the quest is carried in ``note``
    (``evolves_from:<quest_id>``), NOT in ``Consequence.thread_id`` — a non-empty
    ``thread_id`` marks a worldsim background beat that ``consequences.due()`` /
    ``check_consequences`` deliberately SKIP (those surface only via ``world_tick``).
    Using ``thread_id`` here would silently hide the evolution from the DM, so the
    quest id rides in ``note`` instead, which preserves both surfacing and trace-back."""
    if q.status != "completed":
        return None
    if not (q.evolves_to or "").strip():
        return None
    note = _evolution_note(q.id)
    # Idempotency: never double-schedule if this quest already spawned an evolution.
    if any(con.note == note for con in c.consequences):
        return None
    in_days = max(0, int(q.callback_in_days))
    text = (
        f"Bring back / evolve the resolved thread '{q.title}' -> {q.evolves_to}: "
        f"weave a follow-on beat that pays off this quest."
    )
    return consequences_mod.schedule(c, in_days=in_days, text=text, note=note)


@mcp.tool()
def complete_quest(campaign_id: str, quest_id: str, status: str = "completed") -> dict:
    """Resolve a quest. status: completed | failed | active.

    Rule-of-three evolution (Quest & Arc engine, Layer 1): if the quest resolves
    (status -> completed) and carries an ``evolves_to`` follow-on, the engine
    schedules a Consequence so the thread echoes later (surfaced via
    check_consequences). Additive + idempotent: empty ``evolves_to`` == today's
    behavior; re-resolving never double-schedules."""
    if status not in ("completed", "failed", "active"):
        raise ValueError("status must be completed | failed | active")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        q = c.quests.get(quest_id)
        if q is None:
            raise ValueError(f"no quest {quest_id!r}")
        q.status = status  # type: ignore[assignment]
        evolution = _maybe_schedule_quest_evolution(c, q)
        save_campaign(c)
        out = {"id": q.id, "title": q.title, "status": q.status}
        if evolution is not None:
            out["evolution_scheduled"] = {
                "consequence_id": evolution.id,
                "trigger_day": evolution.trigger_day,
                "evolves_to": q.evolves_to,
            }
        return out


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
        dev = worldsim.tick_backlog(c, max_events=2)  # ...and the backlog advances over the span
        strategic = worldsim.tick_strategic(c) if elapsed > 0 else []
        # The clock jumped forward days — expire timed effects like every sibling time-seam
        # (advance_time/travel_to/long_rest/short_rest). A multi-day downtime clears hour/day-scale
        # buffs (Mage Armor) and any sub-hour leftover; this was the only seam that omitted it.
        expired = _expire_clock_effects_all(c) if elapsed > 0 else []
        save_campaign(c)
        return {
            "day": c.day,
            "days_elapsed": elapsed,
            "note": note,
            "due_consequences": [{"text": x.text, "note": x.note} for x in due],
            "world_beats": [b.text for b in beats],
            "world_developments": [_backlog_line(d) for d in dev],
            "strategic_events": strategic,
            "expired_effects": expired,
        }


@mcp.tool()
def advance_time(campaign_id: str, phases: int = 0, to: str = "", note: str = "") -> dict:
    """Advance the in-world clock when you NARRATE time passing WITHOUT a travel / rest /
    downtime call — a long city day, an afternoon of legwork, "by the time they're back the
    evening bell has rung twice." Without this the clock silently stays put (`time_of_day`
    frozen at 'morning') even though the fiction moved hours; this writes day/time_of_day to
    campaign state so the sheet, recall, and time-deferred consequences agree with the story.

    Pass EITHER `phases` (how many time-of-day steps to step forward: morning→afternoon→
    evening→night, rolling `day` over at night) OR `to` (a target phase name to jump to —
    advancing into the *next* day if that phase is already past or current, so `to='morning'`
    at morning means the following dawn). `to` wins if both are given. Stirs at most one
    standing thread, like travel_to. Returns {day, time_of_day, phases_advanced, world_beats}."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        # Combat time is measured in ROUNDS (6s) via next_turn, NOT world phases. Advancing the
        # phase clock mid-combat would expire every round/minute-scale effect at once (Bless,
        # Hex) and drop concentration — see combat.expire_clock_effects. So while combat is
        # active, do NOT move the clock and do NOT expire clock effects; round/minute effects are
        # correctly decremented by next_turn. (Defense in depth with the harness soft-tick, which
        # also skips while combat is active.)
        if c.combat.active:
            return {
                "note": "clock not advanced during combat (combat runs in rounds; use next_turn)",
                "day": c.day,
                "time_of_day": c.time_of_day,
                "phases_advanced": 0,
                "world_beats": [],
                "world_developments": [],
                "expired_effects": [],
            }
        phases_list = travel.PHASES
        target = (to or "").strip().lower()
        if target:
            if target not in phases_list:
                return {"error": f"unknown time-of-day {to!r}. Use one of {list(phases_list)}.",
                        "day": c.day, "time_of_day": c.time_of_day, "phases_advanced": 0}
            try:
                cur = phases_list.index(c.time_of_day)
            except ValueError:
                cur = 0
            tgt = phases_list.index(target)
            steps = (tgt - cur) % len(phases_list)
            if steps == 0:  # already at/over that phase → the NEXT occurrence (a full day on)
                steps = len(phases_list)
        else:
            steps = max(0, int(phases))
        day, tod = travel.advance_clock(c, steps)
        beats = worldsim.tick(c, max_beats=1) if steps > 0 else []
        # The proactive backlog rides this same time-passage seam (the harness soft-tick drives
        # advance_time(phases=1) every idle beat → the world advances for free). Idempotent by
        # elapsed days: a phase move that doesn't roll a new day is a no-op.
        dev = worldsim.tick_backlog(c, max_events=1) if steps > 0 else []
        # The clock moved — expire any timed spell effect whose duration has elapsed
        # (minute/round-scale die on any phase advance; hour/day-scale at their deadline).
        expired = _expire_clock_effects_all(c) if steps > 0 else []
        save_campaign(c)
        return {
            "day": day,
            "time_of_day": tod,
            "phases_advanced": steps,
            "note": note,
            "world_beats": [b.text for b in beats],
            "world_developments": [_backlog_line(d) for d in dev],
            "expired_effects": expired,
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
    sets_flag: str = "",
) -> dict:
    """Record a party decision so the DM and companions can call back to it later
    ('last time we trusted Grett...'). Capture the choice after a deliberation:
    `summary` (the decision), `options` (what was on the table), `chosen`, why
    (`rationale`), and who weighed in (`actor_ids`). Returns the decision id.

    `sets_flag` (optional, Quest & Arc engine Layer 2) ties this CHOICE to a CONTENT-
    defined campaign flag it raises — the one-step path for the owner's model ("let the
    farmer's daughter die → the knight-companion turns on you"). When set, the named flag
    is flipped True in `Campaign.flags` (same store as `set_flag`), which ESCALATES any
    `attitude_below` companion agenda whose `decision_flag` matches (the betrayal chance
    spikes). The flag NAME is yours (e.g. "let_daughter_die", "took_bribe") — never
    engine-coded. Equivalent to a `set_flag` call alongside the record; omit it (or use
    plain `set_flag`) when no agenda is gated on the choice. Returns the flag in `flag`."""
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
        flag = sets_flag.strip()
        if flag:
            c.flags[flag] = True  # content-defined; arms a matching agenda's decision_flag
        save_campaign(c)
        out = {"id": d.id, "summary": d.summary, "chosen": d.chosen, "day": d.day}
        if flag:
            out["flag"] = flag
        return out


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
def roll_wandering_encounter(campaign_id: str, region: str = "", difficulty: str = "medium") -> dict:
    """EXPLICITLY stage a Kingmaker-style wandering encounter (the manual trigger for
    the DM, a QA harness, or the "Stir the world" UI button).

    Unlike the automatic checks on time-advancing `travel_to` / `long_rest` (which only
    fire on a probabilistic per-region roll), this ALWAYS stages an encounter. It picks a
    TYPED encounter for the region (`wander.pick_typed_encounter`) — most are NOT fights:

      * ``{type:"combat", ...}`` — region foe(s) sized to the LIVING party's XP budget
        for `difficulty`, spawned as monster Characters anchored at the party's current
        location, plus `surprise`, `encounter_xp`, and an `outlook`
        ``{band, overmatch_ratio, must_offer_out, guidance}`` (the `encounter_outlook`
        math, folded in): narrate the ambush, honor `surprise`, surface a cost-bearing
        OUT when `must_offer_out`, then `start_combat` with the returned foe ids. (Combat
        is never auto-started.)
      * ``{type:"skill", challenge, skill, dc}`` — a region obstacle; run `skill_check`.
      * ``{type:"social", who, stance, skill, dc}`` — a road-meeting; voice the NPC + a
        Parley moment, then `social_check`.
      * ``{type:"hazard", peril, save_or_skill, dc}`` — a danger; run a save or skill.
      * ``{type:"boon", find}`` — a small positive find; just narrate it.

    Every payload carries `staged`, `type`, and `region`. `region` defaults to the
    party's current location's region. Returns ``{"staged": False}`` only if a combat
    pick's foes all failed to spawn (an empty/all-down party is floored to level 1, and a
    combat pick that can't size degrades to a boon, so it effectively always stages)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if c.combat.active:
            raise ValueError("combat already active — resolve it (end_combat) before staging another encounter")
        region_in = region.strip()
        if not region_in:
            cur_loc = c.locations.get(c.current_location_id) if c.current_location_id else None
            region_in = cur_loc.region if cur_loc is not None else ""
        staged = _stage_wandering_encounter(
            c, region_in, difficulty=difficulty, location_id=c.current_location_id, force=True
        )
        if staged is None:
            return {"staged": False, "region": region_in, "difficulty": difficulty}
        save_campaign(c)
        return staged


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
def get_quest_outcomes(campaign_id: str) -> dict:
    """Return the campaign's resolved MAJOR-quest outcomes (the replayability layer) —
    a `{quest_id: outcome_id}` map picked once at world-gen (ending-tied to the chosen
    ending's world-state, else a seeded random roll) plus a `count`. Each resolved
    outcome's narrative + any follow-up hook is ALSO in recallable lore as
    `[Outcome] …` / `[Hook] …` lines (surfaced under the canon header by recall /
    lookup_lore) — so this tool is the structured index, recall is the prose. Empty
    `{}` for a world that ships no quest_variants. Read-only."""
    c = _require(campaign_id)
    return {"quest_outcomes": dict(c.quest_outcomes), "count": len(c.quest_outcomes)}


def _hook_view(c, h) -> dict:
    """A DM-facing view of a quest hook with its bound nouns resolved to names (so the DM can
    weave it without cross-referencing ids)."""
    giver = c.characters.get(h.giver_id)
    target = c.characters.get(h.target_id) or c.factions.get(h.target_id)
    place = c.locations.get(h.place_id)
    return {
        "id": h.id, "title": h.title, "shape": h.shape, "grievance": h.grievance,
        "motivation": h.motivation, "note": h.note, "status": h.status, "spine": h.spine,
        "arc_back": h.arc_back, "prereq": list(h.prereq),
        "giver": giver.name if giver else "", "giver_id": h.giver_id,
        "target": target.name if target else "", "target_id": h.target_id,
        "place": place.name if place else "", "place_id": h.place_id,
        "item": h.item,
    }


@mcp.tool()
def get_quest_hooks(campaign_id: str, status: str = "", spine_only: bool = False) -> dict:
    """S7 — the lore-derived quest SEEDS the DM pulls and weaves (NOT an engine state machine:
    the engine assembled each from the seeded world; the DM narrates/advances). Each hook is a
    dramatic SHAPE tag bound to typed lore nouns (giver/target/place/item) + a `grievance` (a
    wrong the lore contains), with `prereq`/`arc_back` LABELS the DM reads and a DM-set `status`.
    The `spine` hook is the main arc; ribs `arc_back` to it. Pull a hook the party bites on into
    a tracked Quest with add_quest, and call set_quest_status as you advance it. Optional filters:
    `status` ('open'|'active'|'resolved') and `spine_only`. Empty for a world with no hooks.
    Read-only."""
    c = _require(campaign_id)
    hooks = c.quest_hooks
    if spine_only:
        hooks = [h for h in hooks if h.spine]
    if status:
        s = status.strip().lower()
        hooks = [h for h in hooks if h.status == s]
    return {"quest_hooks": [_hook_view(c, h) for h in hooks], "count": len(hooks)}


@mcp.tool()
def get_prelude(campaign_id: str) -> dict:
    """S7 — the guaranteed 4-beat cold-open the DM weaves so a session never opens 'mid-quest'
    or skips 'how the party meets': Arrival (ground the PC) -> Meeting (a companion + a shared
    stake) -> Inciting Incident (the wrong lands in front of the party) -> Threshold (commit;
    the first thread goes live). The engine guarantees the four beats + bound nouns (ref_id);
    the DM owns ORDER, framing, and prose — weave it, don't read it as a rail. `ref_id` resolves
    to a location (arrival), a companion (meeting), or the spine hook (inciting/threshold).
    Empty for a world that generated no prelude. Read-only."""
    c = _require(campaign_id)
    out = []
    for b in c.prelude:
        ref_name = ""
        if b.ref_id:
            ref = c.locations.get(b.ref_id) or c.characters.get(b.ref_id)
            if ref is not None:
                ref_name = ref.name
            else:  # inciting/threshold bind the spine HOOK id -> show its grievance
                hook = next((h for h in c.quest_hooks if h.id == b.ref_id), None)
                ref_name = hook.grievance if hook else ""
        out.append({"kind": b.kind, "note": b.note, "ref_id": b.ref_id, "ref_name": ref_name})
    return {"prelude": out, "count": len(out)}


@mcp.tool()
def set_quest_status(campaign_id: str, hook_id: str, status: str) -> dict:
    """Advance a quest as the DM narrates it. `hook_id` accepts EITHER:
      - an S7 quest-HOOK id (status: 'open' | 'active' | 'resolved'), or
      - a tracked-QUEST id from `add_quest` (status: 'active' | 'completed' | 'failed';
        'resolved' is accepted and recorded as 'completed', 'open' as 'active').
    The engine routes by which the id matches, so you don't have to remember which tool — one call
    advances a seed OR a promoted quest. (Equivalent to `complete_quest` for a tracked quest.) The
    engine never auto-detects completion; only the DM judges the fiction. Returns the updated view."""
    s = status.strip().lower()
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        # 1) an S7 quest HOOK (the lore-derived seed)
        h = next((x for x in c.quest_hooks if x.id == hook_id), None)
        if h is not None:
            if s not in ("open", "active", "resolved"):
                raise ValueError(f"hook status must be open|active|resolved, got {status!r}")
            h.status = s  # type: ignore[assignment]
            save_campaign(c)
            return _hook_view(c, h)
        # 2) a tracked QUEST from add_quest (a different status vocab) — route here so the DM
        # needn't know which tool; map the hook word 'resolved'->'completed', 'open'->'active'.
        q = c.quests.get(hook_id)
        if q is not None:
            qs = {"resolved": "completed", "open": "active"}.get(s, s)
            if qs not in ("active", "completed", "failed"):
                raise ValueError(f"quest status must be active|completed|failed (or resolved), got {status!r}")
            q.status = qs  # type: ignore[assignment]
            save_campaign(c)
            return {"quest_id": q.id, "title": q.title, "status": q.status}
        raise ValueError(f"no quest hook or tracked quest with id {hook_id!r}")


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


@mcp.tool()
def get_campaign_director(campaign_id: str) -> dict:
    """Campaign Director — advisory beat-start tool (issue #72).

    ADVISORY ONLY. Detects structural story debts from the current campaign state
    and returns the top 3 (by severity) with a one-line nudge each. Read-only:
    the engine NEVER acts on debts, mutates fiction, or forces a resolution.

    Call this at the start of a beat to see what the campaign structurally OWES.
    The DM reads the advisory and CHOOSES what to honour. One nudge at a time —
    this is not a mandatory checklist. Resolution is always explicit via
    resolve_scene_debt.

    Returns::

        {
            "debts": [<top 3 SceneDebt dicts, highest severity first>],
            "advisory": ["one-line nudge per debt"],
            "total_debts": <int>,
        }

    An empty ``debts`` list means no structural debts — today's behavior.
    """
    c = _require(campaign_id)
    return director.compute(c)


@mcp.tool()
def get_scene_debts(campaign_id: str) -> dict:
    """Campaign Director — raw scene-debt list (issue #72).

    ADVISORY ONLY. Returns the full list of structural scene-debts detected from
    the current campaign state — all kinds and severities, unranked. Use
    get_campaign_director for the prioritised top-3 advisory instead.

    Read-only: the engine NEVER acts on debts or mutates fiction.

    Also includes any previously-resolved debts persisted on the campaign snapshot
    (resolved=True) as an audit trail, accessible from Campaign.scene_debts.
    Live-detected debts (from detect()) are merged with the resolved persisted ones
    so the DM can see what was owed and what was cleared.

    Returns::

        {
            "live_debts": [<all currently detected SceneDebt dicts>],
            "resolved_debts": [<SceneDebt dicts with resolved=True from snapshot>],
            "total_live": <int>,
        }
    """
    c = _require(campaign_id)
    live = _scene_debt_mod.detect(c)
    resolved_persisted = [d for d in c.scene_debts if d.resolved]
    return {
        "live_debts": [d.model_dump() for d in live],
        "resolved_debts": [d.model_dump() for d in resolved_persisted],
        "total_live": len(live),
    }


@mcp.tool()
def resolve_scene_debt(campaign_id: str, debt_id: str, evidence: str) -> dict:
    """Campaign Director — mark a scene-debt resolved (issue #72).

    EXPLICIT RESOLUTION ONLY. The DM calls this when they have addressed a debt
    in play (e.g. called add_quest for a hook_untracked, surfaced a consequence,
    gave an NPC a line). The engine NEVER auto-resolves debts.

    ``debt_id`` must match the ``id`` field of a SceneDebt in the live-detected
    list (from get_scene_debts). ``evidence`` is a DM-written note explaining
    what was done (required; must be non-empty). The resolved debt is persisted
    on the campaign snapshot (scene_debts) as an audit trail with resolved=True
    and the provided evidence.

    Returns the persisted SceneDebt record.
    """
    if not evidence or not evidence.strip():
        raise ValueError("evidence is required — describe what was done to resolve this debt.")

    with campaign_lock(campaign_id):
        c = _require(campaign_id)

        # Check if already resolved and persisted
        existing = next((d for d in c.scene_debts if d.id == debt_id and d.resolved), None)
        if existing:
            return {"message": "already resolved", "debt": existing.model_dump()}

        # Detect live debts to find the matching one
        live = _scene_debt_mod.detect(c)
        debt = next((d for d in live if d.id == debt_id), None)
        if debt is None:
            raise ValueError(
                f"No live scene-debt with id {debt_id!r}. "
                f"Use get_scene_debts to see current debt ids."
            )

        # Mark resolved and persist
        debt.resolved = True
        debt.resolution_evidence = evidence.strip()
        # Append to campaign.scene_debts (additive audit trail)
        c.scene_debts.append(debt)
        save_campaign(c)

        return {"message": "resolved", "debt": debt.model_dump()}


if __name__ == "__main__":
    mcp.run()
