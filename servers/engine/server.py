"""WorldOS game-engine MCP server.

Authoritative D&D 5e game state — dice, character sheets, and campaign
persistence — exposed as MCP tools. Every tool reads the campaign from disk,
mutates it, and writes it back atomically (single-writer), so state survives
restarts and context compaction and is never held only in the conversation.

Combat, encounters, leveling, and spellcasting tools build on these foundations
in later epics; this server already owns dice, characters, and persistence.
"""

from __future__ import annotations

import difflib
import random
import re
import sys
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
import events as events_mod
import faction_arc as faction_arc_mod
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
import wrapper_progress as _wrapper_progress_mod
import _env
from pydantic import ValidationError
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
    FactionArc,
    HouseRules,
    Item,
    Location,
    PendingDamageBonus,
    PendingOnHitRider,
    Quest,
    RepeatSave,
    SceneDebt,
    SeedParams,
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
# Reverse: accept either the 3-letter code (the Ability enum value) or the full word
# (how the SRD records spell saving_throw_ability, e.g. "wisdom") and resolve to an Ability.
_FULL_TO_AB3 = {full: ab3 for ab3, full in _AB3_TO_FULL.items()}
from store import append_log, campaign_lock, campaigns_for_world, last_dropped_keys, read_log_all
from store import active_campaign_id as _active_campaign_id
from store import list_campaigns as _list_campaigns
from store import list_slots as _list_slots
from store import load_campaign, save_campaign
from store import load_slot as _load_slot_store
from store import save_slot as _save_slot_store

mcp = FastMCP("clawdnd-engine")


def _parse_ability(value: str) -> Ability:
    """Resolve an ability name to the Ability enum, accepting BOTH the 3-letter code
    ('wis') and the full word ('wisdom') — the latter is how SRD records spell their
    saving_throw_ability, so a rider DC threaded from cast_spell stays usable verbatim."""
    v = (value or "").strip().lower()
    return Ability(_FULL_TO_AB3.get(v, v))


def _require(campaign_id: str) -> Campaign:
    c = load_campaign(campaign_id)
    if c is None:
        raise ValueError(f"no campaign with id {campaign_id!r}")
    return c


def _name_slug(s: str) -> str:
    """Slugify a display name the way the DM does ('Maddala Deadeye' -> 'maddala-deadeye')."""
    return re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")


def _char(c: Campaign, character_id: str) -> Character:
    """Resolve a character id — tolerantly (audit F14-8). ~60 tools route every
    character_id through this ONE site, so a bare dict-get-and-raise turned any id slip
    ('maddala-deadeye' for char_2712a4348f3a "Maddala Deadeye") into a dead-end on all of
    them: a wasted ~100s beat, or a silently-freehanded result. Resolution ladder,
    deterministic and READ-ONLY:
      1. exact dict-key hit (today's behavior — ids stay canonical, zero new cost);
      2. unique case-insensitive match on id, display name, or slugified name;
      3. unique substring match on name or id ('maddala' finds her too);
      4. otherwise raise the SAME-SHAPED ValueError ("no character … in campaign"), now
         carrying a did-you-mean of the <=5 nearest `id (name, kind)` via difflib.
    On ANY ambiguity (two NPCs named "Guard") NEVER resolve — raise listing the
    candidates; a mutating tool must never guess. A fuzzy hit is echoed on stderr (the
    QA harness captures it) so silent resolution stays observable."""
    ch = c.characters.get(character_id)
    if ch is not None:
        return ch
    want = (character_id or "").strip()
    wl = want.lower()
    candidates: list[Character] = []
    if wl:
        slug = _name_slug(want)
        exact = [x for x in c.characters.values()
                 if x.id.lower() == wl or x.name.strip().lower() == wl
                 or (slug and _name_slug(x.name) == slug)]
        if len(exact) == 1:
            print(f"[worldos:_char] resolved {character_id!r} -> {exact[0].id!r} "
                  f"({exact[0].name})", file=sys.stderr)
            return exact[0]
        candidates = exact
        if not exact:
            sub = [x for x in c.characters.values()
                   if wl in x.name.lower() or wl in x.id.lower()]
            if len(sub) == 1:
                print(f"[worldos:_char] resolved {character_id!r} -> {sub[0].id!r} "
                      f"({sub[0].name})", file=sys.stderr)
                return sub[0]
            candidates = sub
    if not candidates and wl:
        # Nearest names/ids/slugs via difflib, mapped back to characters (dedup by id).
        corpus: dict[str, Character] = {}
        for x in c.characters.values():
            for key in (x.id.lower(), x.name.strip().lower(), _name_slug(x.name)):
                if key:
                    corpus.setdefault(key, x)
        seen: set[str] = set()
        for m in difflib.get_close_matches(wl, list(corpus), n=10, cutoff=0.5):
            x = corpus[m]
            if x.id not in seen:
                seen.add(x.id)
                candidates.append(x)
    msg = f"no character {character_id!r} in campaign"
    hint = "; ".join(f"{x.id} ({x.name}, {x.kind})" for x in candidates[:5])
    if hint:
        msg += f". Did you mean: {hint}?"
    raise ValueError(msg)


_COMBAT_EVENT_SCHEMA = "clawdnd.combat_event.v1"


def _combatant_ref(ch: Character) -> dict:
    return {"id": ch.id, "name": ch.name}


def _log_session_entry(
    c: Campaign,
    *,
    kind: str,
    text: str,
    speaker: Optional[str] = "",
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
    # The travelling group = the PC(s) + every companion, whether or not the DM
    # remembered to add them to c.party. A companion brought in via
    # load_canon_character(add_to_party=False) and never recruited still walks with the
    # party (QA state_integrity: Wyll's location froze at a checkpoint the party left).
    # Standalone NPCs / monsters keep their own location_id (they stay put).
    party_ids = set(c.party)
    for cid, member in c.characters.items():
        travels = cid in party_ids or member.kind in ("player", "companion")
        if travels and member.location_id != location_id:
            member.location_id = location_id
            moved.append(cid)
    return moved


def _party_xp_recipients(c: Campaign, include_companions: bool = True) -> list[str]:
    """The ids that share a PARTY XP award — the LIVING travelling group, in a stable
    order. Mirrors `_move_party_to`'s membership rule: the PC(s) plus every
    kind='companion', whether or not the DM remembered to add them to `c.party`.

    This is the XP twin of the relocate sweep (#353): a de-facto companion (kind=
    'companion' but absent from c.party — e.g. loaded via
    load_canon_character(add_to_party=False) and never recruited) walks WITH the party,
    so it must also EARN with the party. Gating XP on `c.party` membership while the
    relocate path gated on KIND left such a companion co-located in every scene yet
    silently stuck at its starting XP (the audit's "award_party_xp does not increment
    companion XP" symptom; the Wyll-froze-at-the-checkpoint family).

    Order: c.party first (preserving the existing remainder-to-first-recipient payout so
    the PC still takes any odd XP), then any de-facto companion not already in c.party.
    Excludes the dead and, when `include_companions=False`, every companion. Standalone
    NPCs / monsters never earn (they aren't part of the group)."""
    party_set = set(c.party)
    kinds = {"player", "companion"} if include_companions else {"player"}
    recipients: list[str] = []
    seen: set[str] = set()

    def _eligible(cid: str) -> bool:
        m = c.characters.get(cid)
        return m is not None and m.kind in kinds and not m.dead

    for cid in c.party:  # c.party order first — keeps the PC as the remainder taker
        if cid not in seen and _eligible(cid):
            recipients.append(cid)
            seen.add(cid)
    if include_companions:  # then de-facto companions the DM never added to c.party
        for cid, member in c.characters.items():
            if cid not in seen and cid not in party_set and member.kind == "companion" \
                    and not member.dead:
                recipients.append(cid)
                seen.add(cid)
    return recipients


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
    """Health check. Returns ok if the WorldOS engine server is reachable."""
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
def active_campaign(world_id: str = "") -> dict:
    """The LIVE campaign in this state dir — the one a harness must re-ground a
    lean/fast beat against (issue #640). Resolved deterministically by the engine
    (the sole source of truth for which save is live) as the MOST-RECENTLY-UPDATED
    campaign, optionally scoped to ``world_id``.

    Harnesses previously picked the lean re-ground ``campaign_id`` by the LARGEST
    snapshot on disk; when a parallel campaign coexists in the state dir (a cold-open
    ``start_world`` retry, or a stale prior save) that can be the WRONG campaign, and
    because ``scene_context`` is strictly campaign-pure it then faithfully folds a
    DIFFERENT save's opening scene into the fast re-ground (cross-chronicle
    contamination). Ask the engine which save is live instead of guessing from file
    sizes. Read-only. Returns ``{"campaign_id": <id> | None}`` (None when no matching
    campaign exists yet)."""
    return {"campaign_id": _active_campaign_id(world_id)}


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
            resumed = {
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
            # F08-3: surface any tolerant-load schema drift on the resume path (see start_session).
            drift = last_dropped_keys(prior.id)
            if drift:
                resumed["schema_drift"] = {
                    "dropped_keys": drift,
                    "note": (
                        "This save was written by a different engine schema; the listed top-level "
                        "field(s) were dropped on load. The original snapshot is preserved at "
                        "snapshot.pre-tolerant.json."
                    ),
                }
            return resumed
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
        # World-Seed dials the DM honors when narrating (#266) — tone/narration register/
        # GM strictness/chronicle voice/anachronism/chronicler_notes + the permadeath/
        # fate_dice/item_destruction toggles. Advisory, exactly like pacing_mode above.
        # difficulty stays under house_rules (set via set_seed_param/set_house_rules).
        "seed_params": c.seed_params.model_dump(),
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
    discovered: bool = True,
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

    `discovered` (default True) controls Atlas visibility for the new place. The Location
    model defaults `discovered=False` so a fog-of-war seed must opt IN (#261/#371), but a
    place the DM names into the world mid-play is, by that act, KNOWN — so the runtime
    default here is True, matching `seed_world`'s day-1 regions and the pre-#371 behavior
    where add_location'd places appeared on the Atlas immediately. Pass `discovered=False`
    to add a RUMOURED/far-off place that stays fog-of-war until the party visits it. (Use
    the separate `hidden` flag to suppress a place from the Atlas entirely.) On the UPDATE
    path — when `location_id` matches an existing place — the existing `discovered` state is
    PRESERVED, never clobbered by this default; reveal a hidden place through its own path.
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
            # discovered: default True so a runtime-named place is visible on the Atlas
            # immediately (the model default is False for fog-of-war seeds — see #261/#371;
            # add_location'd places were visible pre-#371, and seed_world's day-1 regions
            # are discovered=True). Pass discovered=False for a rumoured/far-off place.
            loc = Location(name=name, description=description, hex=coords, region=region,
                           travel_times=tt, discovered=discovered)
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
        strategic_events: list = []
        expired_effects: list[dict] = []
        wandering_encounter: Optional[dict] = None
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
                before_day = c.day
                travel.advance_clock(c, 1)
                world_beats = [b.text for b in worldsim.tick(c, max_beats=1)]
                # The proactive backlog rides the same arrival time-passage (idempotent by day).
                world_developments = [_backlog_line(d) for d in worldsim.tick_backlog(c, max_events=1)]
                # F04-6: this advance path previously stopped here, omitting the sibling
                # side-effects that travel_to's advance runs — the strategic clock, the timed-
                # effect expiry sweep, and the destination wander roll. So a fight staged
                # immediately on a live-gen arrival used STALE buffs (Bless/Mage Armor past their
                # deadline) and never sprang a wandering encounter. Mirror travel_to here.
                strategic_events = worldsim.tick_strategic(c) if c.day > before_day else []
                # A phase elapsed (a journey to the new scene): expire timed spell effects whose
                # duration ran out (minute/round-scale die on any phase advance).
                expired_effects = _expire_clock_effects_all(c)
                # Kingmaker-style WANDERING ENCOUNTER on arrival, mirroring travel_to: roll for
                # the new location's region (composite match per F04-1) and stage a TYPED
                # encounter on a hit. Skipped if a fight is already live (combat never auto-
                # starts). The `arrived` gate already keeps a self-target call a clock no-op.
                if not c.combat.active:
                    staged = _stage_wandering_encounter(
                        c,
                        loc.region,
                        difficulty="medium",
                        location_id=loc.id,
                        match_region=_composite_region_match(loc),
                    )
                    if staged:
                        wandering_encounter = staged
        # Orphan guard: a non-current location with no edges can never be reached.
        if loc.id != c.current_location_id and not loc.connections:
            warnings.append(
                f"location {loc.id!r} has NO connections — it is unreachable; call add_location "
                f"again with connections=[an existing location id] to wire it into the map"
            )
        save_campaign(c)
    result = {
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
    # F04-6: additive keys surfaced ONLY when the advance path actually ran (mirrors
    # travel_to's conditional keys), so the non-advancing / update paths stay byte-identical.
    if strategic_events:
        result["strategic_events"] = strategic_events
    if expired_effects:
        result["expired_effects"] = expired_effects
    if wandering_encounter is not None:
        result["wandering_encounter"] = wandering_encounter
    return result


@mcp.tool()
def travel_to(campaign_id: str, destination_id: str = "", advance_time: bool = False,
              destination: str = "", to: str = "", location_id: str = "") -> dict:
    """Move the party to a connected location along the map graph.

    The destination must be reachable from the current location (listed in its
    connections); travel to an unconnected or unknown location is rejected with
    the reachable exits. Marks the destination visited. The clock advances a
    time-of-day phase only when advance_time=True — leave it False for short
    moves within a site (room to room) so a quick crawl doesn't burn a day; pass
    True for a long or overland journey. Returns ``{from, to, to_name,
    first_visit, day, time_of_day, reachable}`` so the DM knows whether to read
    first-visit boxed text.

    Name the destination via ``destination_id`` (canonical) or the aliases ``destination`` /
    ``to`` / ``location_id`` — ``destination_id`` wins if more than one is given.
    """
    destination_id = destination_id or destination or to or location_id  # accept the id the DM reaches for
    if not destination_id:
        raise ValueError("travel_to needs a destination (pass `destination_id` or an alias: `destination`/`to`/`location_id`)")
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
                    # F04-1: resolve danger off the destination's region + name + tags so
                    # a city street doesn't roll a wilderness ambush (the bare region of a
                    # Baldur's Gate area matches no keyword); the payload region stays bare.
                    match_region=_composite_region_match(dest_loc),
                )
                if staged:
                    result["wandering_encounter"] = staged
        save_campaign(c)
        return result


# Class -> the 5e ability PRIORITY order (highest stat first) for the standard array.
# The standard array is [15, 14, 13, 12, 10, 8]; we assign its values down this list, so
# index 0 gets the 15, index 1 the 14, and so on. This is the canonical "what does a level-1
# <class> put their best scores in" mapping and exists ONLY to give a class-typed canon record
# that ships NO `abilities` block a sane, class-appropriate sheet instead of a flat 10/10/10
# placeholder (a caster left at INT/WIS/CHA 10 casts at +0 — the worst seam in the corpus).
# Primary = the class's key ability (matches srd_tables.casting_ability for the casters);
# CON is the universal second priority (everyone wants hit points); the rest fall in a
# sensible order. Deterministic and self-contained — no SRD list copied, no randomness.
_CLASS_ABILITY_PRIORITY: dict[str, list[str]] = {
    "barbarian": ["str", "con", "dex", "wis", "cha", "int"],
    "bard":      ["cha", "dex", "con", "wis", "int", "str"],
    "cleric":    ["wis", "con", "str", "cha", "dex", "int"],
    "druid":     ["wis", "con", "dex", "int", "cha", "str"],
    "fighter":   ["str", "con", "dex", "wis", "cha", "int"],
    "monk":      ["dex", "wis", "con", "str", "int", "cha"],
    "paladin":   ["str", "cha", "con", "wis", "dex", "int"],
    "ranger":    ["dex", "wis", "con", "str", "int", "cha"],
    "rogue":     ["dex", "con", "int", "wis", "cha", "str"],
    "sorcerer":  ["cha", "con", "dex", "wis", "int", "str"],
    "warlock":   ["cha", "con", "dex", "wis", "int", "str"],
    "wizard":    ["int", "con", "dex", "wis", "cha", "str"],
}


def _normalize_class_token(raw: str) -> str:
    """Reduce a free-text canon `class` string to a single known 5e class key, or "".

    Canon records carry messy class strings — "Cleric, Light domain", "druid (circle of
    the land)", "ranger, rogue", "Eldritch Knight", "any / sorcerer (default)". srd_tables
    raises on those, so the leading recognized class WORD is extracted instead (first match
    wins: "ranger, rogue" -> ranger). A subclass-only label that names its base class
    ("Eldritch Knight" -> fighter) is mapped too. Returns "" when nothing is recognized."""
    s = (raw or "").lower()
    toks = re.findall(r"[a-z]+", s)
    for t in toks:
        if t in _CLASS_ABILITY_PRIORITY:
            return t
    # A few common subclass labels whose base class isn't in the string verbatim.
    subclass_base = {"eldritch": "fighter", "arcane": "fighter"}  # Eldritch/Arcane Knight -> fighter
    for t in toks:
        if t in subclass_base:
            return subclass_base[t]
    return ""


def _derive_canon_abilities(class_name: str, level: int) -> "AbilityScores | None":
    """Derive a class- and level-appropriate AbilityScores from the 5e standard array for a
    canon record that ships NO ability block — so a canon-loaded character (a Wizard PC, a
    Rogue companion) gets a real sheet instead of flat 10/10/10. Returns None for an unknown
    or class-less record (the caller keeps today's flat-10 default and emits a warning).

    Standard array [15,14,13,12,10,8] assigned down _CLASS_ABILITY_PRIORITY (primary stat
    gets 15). Level-based ASIs are applied as +2 to the PRIMARY ability at each of the
    class's ASI levels reached (4/8/12/16/19 for most classes), capped at 20 — so a L1
    Wizard is INT 15, a L5 INT 17 (one ASI), a L8 INT 19 (two). Kept simple and
    deterministic; the secondary stats stay at their array values. The result is overwritten
    by any explicit canon abilities and by a later recruit_companion, so this only ever
    REPLACES the placeholder."""
    key = _normalize_class_token(class_name)
    if not key:
        return None
    priority = _CLASS_ABILITY_PRIORITY[key]
    array = srd_tables.standard_array()  # [15, 14, 13, 12, 10, 8]
    assigned = {ab: array[i] for i, ab in enumerate(priority)}
    # ASIs: +2 to the primary ability for each ASI level reached, capped at 20. (A class-
    # appropriate base array alone fixes the defect; this is the trivial level bump.)
    primary = priority[0]
    try:
        lvl = max(1, int(level))
    except (TypeError, ValueError):
        lvl = 1
    asi_count = sum(1 for lv in range(1, lvl + 1) if srd_tables.is_asi_level(key, lv))
    assigned[primary] = min(20, assigned[primary] + 2 * asi_count)
    return AbilityScores(**{_AB3_TO_FULL[ab]: val for ab, val in assigned.items()})


def _class_level_hp(class_name: str, level: int, con_mod: int) -> "int | None":
    """The SRD fixed-HP max for a single-class character of `class_name` at `level` with the
    given CON modifier: max die + CON at L1, then average (die//2+1) + CON per level after.
    Returns None for an unknown class (the caller can't size it). Deterministic and pure.

    Extracted from _apply_srd_class_defaults so the canon-load seat path can use the SAME
    formula to (a) compute a class+level-appropriate max_hp for a record that ships none and
    (b) recognize a record whose explicit max_hp is BELOW the class+level floor (the #352
    "critically-low canon max_hp" defect — a L5 Wizard seated at a flat 10 instead of ~32)."""
    try:
        die = srd_tables.hit_die(class_name.lower())
    except (ValueError, AttributeError):
        return None
    try:
        lvl = max(1, int(level))
    except (TypeError, ValueError):
        lvl = 1
    per_level_after_first = (die // 2 + 1) + con_mod
    return max(1, die + con_mod + (lvl - 1) * per_level_after_first)


def _expertise_count(class_name: str, level: int) -> int:
    """How many skills carry EXPERTISE (double proficiency) for a single-class character of
    `class_name` at `level`, per SRD 5.2. Rogue: 2 at L1, 4 at L6. Bard: 2 at L2, 4 at L9.
    Every other class: 0. Pure — drives the F02-15 default-fill so an engine-built rogue's
    expertise math is correct out of the box (a real build choice the DM can refine later)."""
    cname = (class_name or "").lower()
    lvl = max(1, int(level)) if str(level).lstrip("-").isdigit() else 1
    if cname == "rogue":
        return 4 if lvl >= 6 else 2
    if cname == "bard":
        return 4 if lvl >= 9 else (2 if lvl >= 2 else 0)
    return 0


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
            ch.max_hp = _class_level_hp(cname, level, con) or ch.max_hp
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
        through = list(srd_tables.features_through(cname, level))
        # #624: a character created directly at/above its subclass-choice level WITH a
        # subclass also gets that subclass's choice-level features (normalize loose
        # names — 'Evocation' -> 'Evoker' — and persist the canonical form).
        sub = next((cl.subclass for cl in ch.classes if cl.name.lower() == cname), None)
        if sub:
            canonical = srd_tables.resolve_subclass(cname, sub)
            if canonical:
                for cl in ch.classes:
                    if cl.name.lower() == cname:
                        cl.subclass = canonical
                slvl = srd_tables.subclass_level(cname)
                if slvl is not None and level >= slvl:
                    through += srd_tables.subclass_features_at(cname, canonical, slvl)
        for f in through:
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
            pool = [str(s).strip().lower() for s in sk.get("from", []) if str(s).strip()]
            # A "choose any N skills" class (Bard, etc.) encodes its pool as the placeholder
            # ["any"] — which is NOT a real skill. Persisting it literally renders 0 proficiencies
            # on the sheet (QA: optimizer crit — a level-1 Bard showed no skills and bailed).
            # Expand "any" to the full skill list so we store concrete proficiencies, keeping
            # any explicitly-listed real skills first.
            if "any" in pool:
                explicit = [s for s in pool if s != "any" and s in SKILL_ABILITIES]
                pool = explicit + [s for s in SKILL_ABILITIES if s not in explicit]
            ch.skill_proficiencies = pool[: int(sk.get("count", 0))]
        # F02-15: a class that grants EXPERTISE (rogue L1/L6, bard L2/L9) had NO engine grant
        # path — every engine-built rogue's expertise-skill math was short by PB (skill_bonus pays
        # 2xPB only from skill_expertise, which only update_character's `expertise` alias ever
        # wrote). Default-fill the expertise picks from the character's OWN proficiencies (so they
        # always map to a real skill), mirroring the skill default-fill above. Only fills an
        # otherwise-empty list, so a hand-authored / DM-chosen expertise set is respected. The
        # actual chosen skills are a build choice the DM/optimizer can later refine via
        # update_character — this just makes the rogue mechanically correct out of the box.
        if not ch.skill_expertise:
            exp_n = _expertise_count(cname, level)
            if exp_n > 0:
                ch.skill_expertise = list(ch.skill_proficiencies[:exp_n])
        _recompute_spellcasting(ch)
        _seed_starting_spells(ch, cname, level)
        _recompute_class_resources(ch)
    except ValueError:
        pass  # unknown class -> keep the explicit values


def _recompute_level_scaled_stats(ch, patch: dict, prior_hit_dice_remaining: "int | None" = None) -> None:
    """OVERWRITE the purely level-derived stats — proficiency bonus, hit dice, max HP, and
    extra attacks — to match ``ch.classes`` after a class/level change, so a DOWN-level retier
    (a canon L12 Fighter patched to L3) does NOT keep the higher tier's inflated math.
    ``_apply_srd_class_defaults`` is FILL-EMPTY (it only computes max_hp at the ``max_hp<=1``
    stub and accumulates extra_attacks via ``max()``), so it can only RAISE those and cannot
    correct a down-level; this resets the level-scaled stats from the new ``total_level``.
    Any of these the SAME ``patch`` set EXPLICITLY is honored (a DM-chosen HP wins). hit_dice /
    max_hp / extra_attacks are recomputed for SINGLE-class sheets only (the SRD formulae are
    single-class; a multiclass sheet keeps its values). Spell slots + class resources are
    re-derived with ``used`` preserved (mirrors level_up). The caller gates this on an actual
    class/level-signature change, so a non-class patch never disturbs these stats."""
    keys = set(patch or {})
    total = ch.total_level
    if "proficiency_bonus" not in keys:
        ch.proficiency_bonus = srd_tables.proficiency_bonus(total)  # by total level (multiclass-safe)
    cname = ch.classes[0].name.lower()
    single_class = len({cl.name.lower() for cl in ch.classes}) == 1
    try:
        if single_class and "hit_dice" not in keys:
            ch.hit_dice = f"{total}d{srd_tables.hit_die(cname)}"
            if "hit_dice_remaining" not in keys:
                # F02-5: cap the SPENT pool against the new total — never REFILL it. The caller
                # passes the pre-recompute remaining because _apply_srd_class_defaults ran first
                # and unconditionally reset hit_dice_remaining to `level` (which would refund every
                # spent die on a stat/level patch). Fall back to the current value when no prior
                # was supplied (the level_up path keeps its own +1 accounting).
                spent_basis = (prior_hit_dice_remaining
                               if prior_hit_dice_remaining is not None
                               else ch.hit_dice_remaining)
                ch.hit_dice_remaining = max(0, min(spent_basis, total))
        if single_class and "max_hp" not in keys:
            recomputed = _class_level_hp(cname, total, ch.ability_modifier(Ability.CON))
            if recomputed:
                ch.max_hp = recomputed
                ch.current_hp = min(ch.current_hp, ch.max_hp)
        if single_class and "extra_attacks" not in keys:
            # RESET then re-derive from features: fill-empty's max() can't LOWER a stale higher tier
            # (a L12 Fighter's extra_attacks=2 must drop to 0 at L3).
            ch.extra_attacks = 0
            for f in srd_tables.features_through(cname, total):
                if "extra_attacks" in f:
                    ch.extra_attacks = max(ch.extra_attacks, int(f["extra_attacks"]))
    except ValueError:
        pass  # unknown class -> leave explicit values (mirrors _apply_srd_class_defaults)
    _recompute_spellcasting(ch)
    _recompute_class_resources(ch)


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


# Class -> a small, canonical starting spell loadout (cantrips always-known + a few prepared
# leveled spells). Every name resolves in the srd524 casting DB so cast_spell works out of the
# box. This exists because _recompute_spellcasting only sizes SLOTS — without seeding spells a
# freshly-built caster has slots but NOTHING to cast (QA: a level-3 Wizard shipped with an empty
# spellbook and never cast once). Half-casters (paladin/ranger) cast from LEVEL 1 under SRD 5.2
# (the 2024 edition the engine's feature data follows — audit F02-2), so they seed from L1 like
# everyone else. Generic SRD spells — no proprietary list copied.
_STARTING_SPELLS: dict[str, dict[str, list[str]]] = {
    "wizard":   {"cantrips": ["Fire Bolt", "Mage Hand", "Light"],
                 "spells": ["Magic Missile", "Shield", "Mage Armor", "Detect Magic"]},
    "sorcerer": {"cantrips": ["Fire Bolt", "Ray of Frost", "Light"],
                 "spells": ["Magic Missile", "Shield", "Burning Hands"]},
    "cleric":   {"cantrips": ["Sacred Flame", "Guidance", "Light"],
                 "spells": ["Cure Wounds", "Bless", "Guiding Bolt", "Healing Word", "Shield of Faith"]},
    "druid":    {"cantrips": ["Druidcraft", "Produce Flame", "Guidance"],
                 "spells": ["Cure Wounds", "Entangle", "Thunderwave"]},
    "bard":     {"cantrips": ["Vicious Mockery", "Mage Hand"],
                 "spells": ["Cure Wounds", "Healing Word", "Dissonant Whispers"]},
    "warlock":  {"cantrips": ["Eldritch Blast", "Chill Touch"],
                 "spells": ["Hex", "Hellish Rebuke", "Charm Person"]},
    "paladin":  {"cantrips": [],
                 "spells": ["Bless", "Cure Wounds", "Shield of Faith"]},
    "ranger":   {"cantrips": [],
                 "spells": ["Hunter's Mark", "Cure Wounds", "Ensnaring Strike"]},
}


def _seed_starting_spells(ch, class_name: str, level: int) -> None:
    """Give a freshly-built caster a canonical, castable starter loadout so they can actually
    cast from turn one. Cantrips land in spells_known (always available); leveled spells land in
    BOTH spells_known and spells_prepared so casting works whether the class is a known- or a
    prepared-caster. Only fires when BOTH spell lists are empty (respects a template/canon record
    that supplied its own spells) and only for the caster classes above. Half-casters seed from
    L1 — SRD 5.2 paladins/rangers have Spellcasting at level 1 (the old `level < 2` gate was a
    2014 assumption that, combined with the round-down caster level, made a L1 half-caster
    unable to cast at all — audit F02-2)."""
    cname = (class_name or "").lower()
    loadout = _STARTING_SPELLS.get(cname)
    if not loadout:
        return
    if ch.spells_known or ch.spells_prepared:
        return  # respect spells a template / canon record already supplied
    cantrips = list(loadout.get("cantrips", []))
    spells = list(loadout.get("spells", []))
    ch.spells_known = cantrips + spells
    ch.spells_prepared = list(spells)


_ABILITY_FIELDS = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")


def _backfill_seat_abilities(ch, class_name: str, level: int, rec_abilities=None) -> str:
    """The ONE ability backfill every seat path shares (audit F02-1). Precedence,
    deterministic and additive:
      * a sheet that is already non-flat (hand-authored roster record, explicit caller
        abilities applied upstream) is NEVER touched -> "explicit";
      * an explicit record `abilities` block wins next -> "canon" (malformed degrades);
      * else derive the class+level standard array (_derive_canon_abilities) -> "derived";
      * a class-less / unknown-class sheet keeps the flat-10 default -> "placeholder".
    initiative_bonus resets from the REAL DEX whenever abilities are (re)assigned, so
    HP/AC/initiative downstream compute off real scores. Returns the ability_source."""
    if any(getattr(ch.abilities, f) != 10 for f in _ABILITY_FIELDS):
        return "explicit"  # a real sheet always wins over derivation
    if isinstance(rec_abilities, dict) and rec_abilities:
        try:
            ch.abilities = AbilityScores(**rec_abilities)
            ch.initiative_bonus = ch.abilities.modifier(Ability.DEX)
            return "canon"
        except (TypeError, ValueError):
            pass  # malformed record block -> fall through to derivation/placeholder
    derived = _derive_canon_abilities(class_name, level)
    if derived is not None:
        ch.abilities = derived
        ch.initiative_bonus = ch.abilities.modifier(Ability.DEX)
        return "derived"
    return "placeholder"


def _finish_seat_sheet(ch, class_name: str, level: int, *, set_base_ac: bool,
                       rec_abilities=None, backfill_abilities: bool = True,
                       seed_gear: bool = True) -> str:
    """EVERY seat path's shared finisher (audit F02-1 + F02-4): ability backfill ->
    SRD class defaults -> starting gear+purse, in that order so HP/AC/initiative are
    computed from REAL ability scores. The five seat paths (create / start fresh +
    promote / load_canon / recruit) each used to hand-roll a different subset of these
    steps — pickup PCs seated flat-10, canon/recruit seats claimed an armor AC over an
    empty pack — so the fix shape is one helper, not another one-path patch. Every step
    self-guards (non-flat sheets, supplied kits/purses, non-stub HP, and unknown classes
    are all left alone), making the whole call additive on an already-complete sheet.
    Returns the ability_source ("explicit" | "canon" | "derived" | "placeholder")."""
    ability_source = "explicit"
    if backfill_abilities:
        ability_source = _backfill_seat_abilities(ch, class_name, level, rec_abilities)
    if class_name:
        _apply_srd_class_defaults(ch, class_name, level, set_base_ac=set_base_ac)
        if seed_gear:
            _seed_starting_gear(ch, class_name)
    return ability_source


def _seat_flat10_warnings(ch, class_label: str, where: str) -> list[str]:
    """The flat-10 PLACEHOLDER warning load_canon_character surfaces, shared with the
    start_character seat paths (audit F02-1): a PLAYER (or any seated spellcaster) standing
    at 10/10/10/10/10/10 acts at +0 on every check/save/DC — not a hard fail (a class-less
    blank sheet is a documented origin), but QA and the DM must SEE it. Returned in the
    tool result and echoed on stderr (the QA harness captures stderr)."""
    is_flat = all(getattr(ch.abilities, f) == 10 for f in _ABILITY_FIELDS)
    is_caster = bool(ch.spell_slots or ch.spells_known or ch.spells_prepared)
    if not (is_flat and (ch.kind == "player" or is_caster)):
        return []
    who = "player" if ch.kind == "player" else "spellcaster"
    warn = (f"{ch.name!r} ({class_label or 'class-less'}) seated as a {who} with a "
            f"PLACEHOLDER 10/10/10/10/10/10 ability array — its checks, saves, and spell "
            f"DCs are all +0. Pass `abilities` or flesh the sheet out via update_character.")
    print(f"[worldos:{where}] WARNING: {warn}", file=sys.stderr)
    return [warn]


def _seed_companion_operational_state(ch) -> None:
    """Seed a companion's ARC and DOSSIER if absent — the shared finisher every
    companion-creation path runs so the relationship system (camp_scene /
    check_companion_arc / agendas) has state to track (F06-1).

    Two of the three creation paths used to SKIP this (create_character — the dominant
    path at 111 calls — seeded neither; load_canon_character seeded the dossier but never
    an arc), so 20/20 live snapshot companions had arc=None/dossier=None and the whole arc
    machine was structurally inert. recruit_companion already did this inline; this is the
    extracted shared helper (the same logic, byte-for-byte) called by all three.

    Both writes are None-GUARDED, so an ending-seeded / canon / roster arc or dossier is
    NEVER overwritten — the DM can still author a richer one via set_companion_arc /
    update_character. Caller holds campaign_lock; this mutates the passed Character in
    place and does not save. NOT called for players/npcs/monsters — companion-only on every
    path."""
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
        # backstory/personality are the recruit/canon sources; biography is the
        # create_character authoring field — fall through them so every path has a hint.
        seed_hint = (ch.backstory or ch.personality or ch.biography or "").strip()
        camp_prompts: list[str] = []
        if seed_hint:
            # one short clause, not the whole biography — keep the dossier operational
            clause = seed_hint.replace("\n", " ").split(". ")[0].strip()
            if clause:
                camp_prompts.append(clause[:200])
        # the NPC's seeded hook / remembered facts make good, character-specific camp talk
        camp_prompts.extend(m.strip() for m in ch.memory if m.strip())
        ch.companion_dossier = CompanionDossier(camp_prompts=camp_prompts[:4])


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
    house: str = "",
    biography: str = "",
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
            # Loop-10 #383: player-authored identity prose threaded from the
            # Creation wizard's house + biography inputs. Empty strings are
            # today's behavior — the projection drops them through too, so the
            # /character-surface payload only carries them when set.
            house=house,
            biography=biography,
        )
        if skills:  # explicit skill choices win over the class default-fill
            ch.skill_proficiencies = [s.lower() for s in skills if s.lower() in SKILL_ABILITIES]
        if apply_srd_defaults and class_name:
            # create_character is the DM's direct authoring surface: an omitted `abilities`
            # stays the explicit flat sheet (today's contract) — no derivation here.
            _finish_seat_sheet(ch, class_name, level, set_base_ac=(armor_class == 10),
                               backfill_abilities=False,
                               seed_gear=(kind in ("player", "companion")))
        # Anchor NPCs/monsters to where they're introduced so "who's in the scene" is
        # the current location's cast — not the whole seeded world roster. Explicit
        # location_id wins; otherwise default to the party's current location.
        if kind in ("npc", "monster"):
            ch.location_id = location_id or c.current_location_id
        # F06-1: a companion made on THIS path (the dominant one, 111 calls) used to get
        # NO arc and NO dossier, so camp/gates/agendas were inert (20/20 live snapshots).
        # Route it through the shared seeding helper — companion-only, None-guarded so an
        # explicitly-passed arc/dossier is never clobbered.
        if kind == "companion":
            _seed_companion_operational_state(ch)
        c.characters[ch.id] = ch
        # INVARIANT: a kind="player" character is always in the party (it's the protagonist),
        # even if add_to_party=False was passed; a companion joins only when add_to_party.
        if (kind == "player" or (add_to_party and kind == "companion")) and ch.id not in c.party:
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
            # SYN-03: resolve-then-suggest scoped to PICKUP-ELIGIBLE figures — never dump
            # the whole playable roster as the error payload. Keep the `error` key.
            did_you_mean, count = (
                content_mod.suggest_canon_names(c0.world_id, who, playable_only=True)
                if c0.world_id else ([], 0)
            )
            return {
                "error": f"no canon character {who!r} for world {c0.world_id!r}",
                "playable": True,
                "did_you_mean": did_you_mean,
                "available_count": count,
                "note": "list_canon_characters(playable_only=True, q=…) to search pickups.",
            }
        if not content_mod.is_playable(rec):
            # A real figure, just not a pickup. Suggest a FEW pickup-eligible alternatives
            # near the requested name (or the roster head when there's nothing close) —
            # not the whole playable list.
            did_you_mean, count = (
                content_mod.suggest_canon_names(c0.world_id, who, playable_only=True)
                if c0.world_id else ([], 0)
            )
            return {
                "error": (
                    f"{rec.get('name', who)!r} is a legend of this era — they appear as an "
                    f"NPC/quest-giver, not a hero you play. Pick a minor figure, or use "
                    f"load_canon_character to encounter them in the world."
                ),
                "playable": False,
                "did_you_mean": did_you_mean,
                "available_count": count,
                "note": "list_canon_characters(playable_only=True, q=…) to search pickups.",
            }
        # HARD GATE (F02-8, mirrors load_canon_character's #305 player gate): a canon-DEAD figure
        # may be a lore NPC but must NEVER be seated as the PLAYER — the prestige-CRPG framing
        # breaks if the PC's canon-truth is "dead and rotting". pickup only checked is_playable
        # (a flag that defaults True), never is_dead_record — so a playable-but-dead record slipped
        # through. Return the same {"error", "dead_in_canon"} shape so play.sh falls back to a living pick.
        if content_mod.is_dead_record(rec):
            return {
                "error": (f"{rec.get('name', who)} is dead in canon and cannot be the player "
                          f"character — pick a living figure (list_canon_characters("
                          f"playable_only=True) lists only living, playable figures)."),
                "dead_in_canon": True,
                "name": rec.get("name", who),
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
            ability_source = "explicit"
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
                # The shared finisher (F02-1/F02-4): backfill-when-flat (a hand-fleshed
                # roster sheet wins; a flat-10 stub is repaired from the canon rec / the
                # class array), SRD defaults, gear+purse — same ladder as every seat path.
                ability_source = _finish_seat_sheet(
                    ch, cn, lvl, set_base_ac=(int(build["armor_class"]) == 10),
                    rec_abilities=(rec or {}).get("abilities"),
                    backfill_abilities=not build["abilities"])
            # F02-8: promoting a roster STUB to the PLAYER must clear any stale death state — a
            # bare identity stub (max_hp=1) can be flagged dead/stable after one combat hit, and
            # seating a dead PC deadlocks the facade (the PC can't act, can't long_rest). Mirrors
            # recruit_companion's clear. Guarded on living HP so this never resurrects a 0-HP record.
            if ch.current_hp > 0 and (ch.dead or ch.stable or ch.death_saves.successes
                                      or ch.death_saves.failures):
                ch.dead = False
                ch.stable = False
                ch.death_saves = DeathSaves()
                ch.conditions = [cond for cond in ch.conditions if cond != Condition.UNCONSCIOUS]
            ch.met = True  # an active player is, by convention, met
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
                # Which precedence seated the abilities (explicit/canon/derived/placeholder)
                # + the same flat-10 warning load_canon surfaces — QA/DM must SEE a +0 PC.
                "ability_source": ability_source,
                "warnings": _seat_flat10_warnings(ch, cn, "start_character"),
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
        # nobody_l1), via the shared finisher (F02-1/F02-4): when no explicit `abilities`
        # were passed, the canon rec's block -> the class+level standard array backfills
        # the flat-10 placeholder BEFORE HP/AC/initiative are computed (the pickup-origin
        # PC used to seat all-10s). set_base_ac only when AC is the unarmored default,
        # mirroring create_character so an explicit/template AC is preserved.
        ability_source = "explicit" if build["abilities"] else "placeholder"
        if cn:
            ability_source = _finish_seat_sheet(
                ch, cn, lvl, set_base_ac=(int(build["armor_class"]) == 10),
                rec_abilities=(rec or {}).get("abilities"),
                backfill_abilities=not build["abilities"])
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
        # Which precedence seated the abilities (explicit/canon/derived/placeholder) + the
        # same flat-10 warning load_canon surfaces — QA/DM must SEE a +0 PC (F02-1).
        "ability_source": ability_source,
        "warnings": _seat_flat10_warnings(ch, cn, "start_character"),
        "combat_numbers": _combat_numbers(ch),  # authoritative to-hit/damage — don't invent
    }


@mcp.tool()
def recruit_companion(
    campaign_id: str,
    npc_id: str = "",
    class_name: str = "",
    level: int = 1,
    abilities: Optional[dict] = None,
    subclass: Optional[str] = None,
    max_hp: int = 0,
    armor_class: int = 0,
    apply_srd_defaults: bool = True,
    skills: Optional[list] = None,
    character_id: str = "",
    companion_id: str = "",
    id: str = "",
) -> dict:
    """Promote an EXISTING roster NPC into the party's companion — the clean way to
    bring a world-seed candidate (e.g. "Minsc is ready", "Bram is ready") into the
    party. Use this INSTEAD of create_character for someone who already exists in the
    world: it flips the record npc->companion, adds it to the party (once), and fills
    a real sheet so you never invent modifiers. Pass `class_name`+`level`+`abilities`
    for the companion's build; `apply_srd_defaults` sets saves/HP/AC/features (HP is
    auto-set only at level 1 — pass `max_hp` for a higher-level companion). Idempotent
    if already a companion. This prevents the duplicate-stub bug (a roster NPC plus a
    second hand-built companion of the same name).

    Identify the roster figure via ``npc_id`` (canonical) or the aliases ``character_id`` /
    ``companion_id`` / ``id`` — ``npc_id`` wins if more than one is given."""
    npc_id = npc_id or character_id or companion_id or id  # accept the id the DM reaches for
    if not npc_id:
        raise ValueError("recruit_companion needs an id (pass `npc_id` or an alias: `character_id`/`companion_id`/`id`)")
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
            # The shared seat finisher (F02-1/F02-4): a flat-10 roster stub gains a class-
            # appropriate array (explicit `abilities` / a hand-fleshed sheet always win),
            # then SRD defaults, then the gear+purse kit the claimed AC implies — recruit
            # used to apply the AC but never the armor (53 wild AC>=14-no-armor records).
            _finish_seat_sheet(ch, class_name, level, set_base_ac=(armor_class <= 0),
                               backfill_abilities=not abilities, seed_gear=True)
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
        # A companion needs an ARC + DOSSIER for the relationship system (camp_scene /
        # check_companion_arc / banter / approval causes) to have state to track — QA found a
        # freshly-recruited canon companion with arc=null, so camp + the gates were inert.
        # The shared seeding helper (F06-1) gives a None-guarded default arc (a loyalty gate so
        # the bond can deepen) + a minimal dossier synthesized from what the record already
        # carries (backstory/personality/memory -> terse camp prompts). Guarded on None so an
        # ending-seeded arc/dossier is NEVER overwritten; the DM authors richer ones later.
        _seed_companion_operational_state(ch)
        ch.met = True  # joining the party means the party has met them
        if ch.id not in c.party:
            c.party.append(ch.id)
        # A recruit travels with the party from this instant — co-locate them with the
        # party's current location so they don't enter carrying a stale/None location_id
        # that _move_party_to only fixes on the NEXT travel (QA: a just-recruited
        # companion shown a scene behind the party). Mirrors _move_party_to's contract.
        if c.current_location_id is not None:
            ch.location_id = c.current_location_id
        # F06-7 (audit 2026-06-11): backfill the recruit's XP to the party's CURRENT parity so a
        # mid-run join isn't a guaranteed false `companion_xp_synced_on_award` WARN. recruit
        # co-locates the recruit in this same call, so a recruit left at xp=0 is EXACTLY the WARN
        # predicate (kind=companion, not dead, location==current, xp==0, pc_xp_max>0) the moment
        # the party has earned anything. The new ally also LEVELS WITH the party (the #739/#353
        # "join together → level together" rule the award/relocate paths already enforce). Only
        # ever RAISES toward the living party's max XP — never lowers a recruit already ahead
        # (a seasoned guest keeps their earned XP). In xp leveling mode only; no party XP -> 0
        # (today's behavior byte-for-byte).
        xp_backfilled = 0
        if c.leveling_mode == "xp":
            party_xp_max = max(
                (m.xp for m in (c.characters.get(i) for i in _party_xp_recipients(c))
                 if m is not None and m.id != ch.id),
                default=0,
            )
            if party_xp_max > ch.xp:
                xp_backfilled = party_xp_max - ch.xp
                ch.xp = party_xp_max
        save_campaign(c)
        out = {"id": ch.id, "name": ch.name, "kind": ch.kind, "party": list(c.party),
               "arc_seeded": ch.arc is not None,
               "dossier_seeded": ch.companion_dossier is not None}
        if xp_backfilled:
            out["xp"] = ch.xp
            out["xp_backfilled"] = xp_backfilled
            out["level_available"] = srd_tables.level_for_xp(ch.xp)
        return out


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

    The DEAD PC's gear and gold are LOST with the body — they stay on the corpse, never
    transferred to the new hero (use `add_item`/`adjust_currency` if the fiction lets the party
    loot it). The new character is seated with their OWN class-appropriate starting kit (a modest
    purse + the armor/weapon that justify their AC), at the party's current location. HOW the new
    hero arrives in the world is the DM's narration, not this tool's job. On a party WIPE, call
    this once per fallen member (there is no separate batch tool).

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
            # F02-12: seat the new hero IN THE SCENE — at the party's current location, not a
            # null location_id that only the next travel would fix (a just-rerolled PC was shown
            # a scene behind the party). met=True for consistency (a player is implicitly met —
            # create_character sets it; the model comment documents the convention).
            location_id=c.current_location_id,
            met=True,
        )
        if skills:  # explicit skill choices win over the class default-fill
            new.skill_proficiencies = [s.lower() for s in skills if s.lower() in SKILL_ABILITIES]
        if class_name:
            # F02-12: route through the shared seat finisher so the new hero's AC is BACKED by a
            # real kit (the old path set an armored class_base_ac over an empty inventory — AC 16
            # with no armor). The "gear lost with the body" rule applies to the DEAD PC's loot;
            # the new character still earns their own starting kit (the docstring says so), so AC
            # and inventory AGREE. backfill only when no explicit abilities were passed.
            _finish_seat_sheet(new, class_name, lvl, set_base_ac=True,
                               backfill_abilities=not abilities, seed_gear=True)

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
def generate_image(kind: str, prompt: str, seed: Optional[int] = None,
                   scope: Optional[str] = None, force: bool = False) -> dict:
    """Kick off (fire-and-forget) an image for the campaign and return IMMEDIATELY.
    `kind` is 'map' (region/dungeon), 'portrait' (NPC/PC), or 'scene' (illustration);
    `prompt` is the visual brief. The active provider is chosen by CLAWDND_IMAGE_PROVIDER
    (default 'null' → a deterministic placeholder, no network).

    Pass `scope` as the ENTITY id the viewer fetches by, NOT a world/campaign id:
    `portrait-<character_id>` for an NPC/PC face, or the `<location_id>` for a scene/map.
    The viewer keys /image and /portrait-* off that exact scope, so a mismatched scope
    leaves the art unfetchable.

    This is an OPTIONAL OVERLAY that never blocks the engine or the DM's turn: a cache
    hit returns the descriptor straight away (`status="ready"`); when the world's ingested
    art already covers the scope it returns `status="ingested"` without spending; a miss
    enqueues a background resolver and returns a `status="pending"` handle (with the
    `scope`/`hash` the viewer keys off) in well under a second — the actual generation
    (which can take tens of seconds against a real gateway) happens off the turn path. The
    dashboard's /image?scope=… serves a placeholder until the image lands, so play never
    waits on art. Pass `force=True` to regenerate even when ingested art exists.

    The image cache is a derived, rebuildable artifact written by the resolver; it never
    touches campaign state (engine stays sole writer)."""
    return imagegen.async_generate(kind, prompt, seed=seed, scope=scope, force=force)


_SHORT_TO_FULL_AB = {
    "str": "strength", "dex": "dexterity", "con": "constitution",
    "int": "intelligence", "wis": "wisdom", "cha": "charisma",
}


def _bump_intel(c: Campaign, slug: str, tier: int) -> None:
    """Record the party's bestiary intel for a creature TYPE at a monotonic max (#263).

    ``slug`` is the canonical bestiary slug (bestiary.creature_slug); ``tier`` is 1=sighted,
    2=engaged, 3=slain. No-op for an empty slug (a non-bestiary monster), so today's behavior
    is unchanged. ``max()`` keeps the tier non-regressing — a kill (3) past an earlier sighting
    (1) lands at 3, and a higher tier already recorded is never lowered. The caller already
    holds ``campaign_lock`` and persists via ``save_campaign`` (sole-writer respected)."""
    if not slug:
        return
    c.bestiary_intel[slug] = max(c.bestiary_intel.get(slug, 0), int(tier))


def _monster_character_from_statblock(sb: dict, label: str, *, location_id=None) -> Character:
    """Build a combat-ready monster Character from a bestiary stat block — the SINGLE
    construction path shared by spawn_monster and _spawn_creature_chars (F01-2, #773;
    the two hand-rolled ctors had drifted: the wandering path silently lost Parry —
    F01-11). Transfers abilities, AC/HP/hit dice, the CR-derived proficiency bonus,
    initiative, R/I/V + condition immunities, Parry, actions-on-notes, XP value, and
    the creature slug — plus the creature's PRINTED save proficiencies:
      - ``saving_throw_proficiencies``: a flag per save the stat block lists (with the
        CR-derived PB, mod + PB reproduces the printed total for 128/132 creatures);
      - ``save_bonus_overrides``: the printed total for the residual srd524 data
        quirks, so ``saving_throw_bonus`` == the printed stat block for ALL creatures.
    Pure construction: the caller registers the Character on the campaign and saves
    (sole-writer), anchoring at ``location_id`` (wandering) or leaving it None."""
    scores = AbilityScores(**{_SHORT_TO_FULL_AB[k]: v for k, v in sb["abilities"].items()})
    actions_note = " | ".join(f"{a['name']}: {a['desc']}" for a in sb["actions"][:10])
    summary = f"CR {sb['cr']}, {sb['xp']} XP. {sb['size']} {sb['type']}. Actions: {actions_note}"
    pb = int(sb["proficiency_bonus"])
    save_profs: list[Ability] = []
    overrides: dict[str, int] = {}
    for short, printed in (sb.get("saves") or {}).items():
        try:
            ab = Ability(short)
        except ValueError:
            continue  # defensive: unknown ability key in authored data
        save_profs.append(ab)
        if scores.modifier(ab) + pb != int(printed):
            overrides[ab.value] = int(printed)
    return Character(
        name=label,
        kind="monster",
        abilities=scores,
        max_hp=sb["hp"],
        current_hp=sb["hp"],
        armor_class=sb["ac"],
        hit_dice=sb["hit_dice"],
        proficiency_bonus=pb,
        saving_throw_proficiencies=save_profs,
        save_bonus_overrides=overrides,
        initiative_bonus=sb["initiative_bonus"] or scores.modifier(Ability.DEX),
        damage_resistances=sb["damage_resistances"],
        damage_immunities=sb["damage_immunities"],
        damage_vulnerabilities=sb["damage_vulnerabilities"],
        condition_immunities=sb["condition_immunities"],
        parry=bestiary.parry_bonus(sb),
        notes=summary,
        xp_value=sb["xp"],
        location_id=location_id,
        creature_slug=bestiary.creature_slug(sb["name"]),
    )


@mcp.tool()
def spawn_monster(campaign_id: str, name: str = "", count: int = 1,
                  monster: str = "", monster_name: str = "", creature: str = "") -> dict:
    """Spawn combat-ready monster(s) from the bundled SRD bestiary by name.

    Looks the creature up (case-insensitive) in the ~330-creature SRD data and
    creates Character(kind="monster") records with HP, AC, abilities, proficiency
    and initiative bonuses, and damage resistances/immunities/vulnerabilities all
    pre-filled — so you never hand-transcribe a stat block (and never leave a
    duplicate NPC record). The creature's actions/attacks are stored on the
    monster's `notes` (with the to-hit/damage text) for you to drive `attack`.
    count>1 spawns numbered copies. Unknown name -> {"error", "suggestions"} from a
    fuzzy search (try e.g. 'Goblin Warrior', 'Wolf'). Returns the spawned ids + a
    stat summary incl. xp_each (the encounter reward); pass the ids to start_combat.

    Name the creature via ``name`` (canonical) or any of the aliases ``monster`` /
    ``monster_name`` / ``creature`` — ``name`` wins if more than one is given."""
    name = name or monster or monster_name or creature  # accept the name the DM reaches for
    if not name:
        raise ValueError("spawn_monster needs a name (pass `name` or an alias: `monster`/`monster_name`/`creature`)")
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
    slug = bestiary.creature_slug(sb["name"])  # stable intel/art join key for this TYPE
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        spawned = []
        # ID-reconcile (combat seam): re-staging a creature already standing pristine on the
        # table — a second spawn_monster("Ogre") for "the ogre" the DM just described — must
        # NOT mint a fresh duplicate id. A duplicate the DM never targets stays in the
        # initiative order at full HP, so end_combat reports a live hostile after the REAL
        # foe is killed, the behavioral gate goes RED, and a clean fight is judged broken
        # (dc0d625 sweep: end_combat fired with a living Ogre while the duo had actually won).
        # Reuse pristine, in-scene copies of this TYPE — alive, undamaged, no conditions, NOT
        # already in the active combat order (so two foes the DM is genuinely fighting are
        # never collapsed) — before minting any new record. Deterministic (sorted by id).
        in_combat_ids = {cb.character_id for cb in c.combat.order}
        reusable = sorted(
            (
                ch for ch in c.characters.values()
                if ch.kind == "monster"
                and getattr(ch, "creature_slug", "") == slug
                and slug != ""
                and not ch.dead
                and ch.current_hp == ch.max_hp
                and ch.current_hp > 0
                and ch.temp_hp == 0
                and not ch.conditions
                and ch.xp_value == sb["xp"]
                and ch.id not in in_combat_ids
            ),
            key=lambda ch: ch.id,
        )
        for i in range(n):
            label = f"{sb['name']} {i + 1}" if n > 1 else sb["name"]
            if reusable:
                # Reconcile onto the existing record: reuse its id (the DM's "the ogre"),
                # refresh the label so numbering stays consistent, and report reused=True.
                ch = reusable.pop(0)
                ch.name = label
                spawned.append({"id": ch.id, "name": ch.name, "reused": True})
                continue
            ch = _monster_character_from_statblock(sb, label)
            c.characters[ch.id] = ch
            spawned.append({"id": ch.id, "name": ch.name})
        # Tier 1 — sighted: spawning puts the creature on the table; the party has laid
        # eyes on it. Bumped once for the type (monotonic max).
        _bump_intel(c, slug, 1)
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
    campaign (mutates; caller holds the lock + saves). Constructs through the SAME
    `_monster_character_from_statblock` factory as `spawn_monster` (F01-2; the old
    hand-rolled copy had drifted and silently lost Parry — F01-11) — but anchors each
    spawn at `location_id` (the scene the wandering encounter erupts in) so the local
    cast shows it, and returns nothing on an unresolvable name (defensive; the picker
    only hands us resolvable names). Returns `[{"id","name"}]` for the spawned foes."""
    sb = bestiary.stat_block(canonical)
    if sb is None:
        return []
    n = max(1, min(int(count), 20))
    slug = bestiary.creature_slug(sb["name"])  # stable intel/art join key for this TYPE
    spawned: list[dict] = []
    for i in range(n):
        label = f"{sb['name']} {i + 1}" if n > 1 else sb["name"]
        ch = _monster_character_from_statblock(sb, label, location_id=location_id)
        c.characters[ch.id] = ch
        spawned.append({"id": ch.id, "name": ch.name})
    # Tier 1 — sighted: a wandering encounter erupting into the scene = the party sees it.
    # Caller holds the lock + saves (sole-writer).
    _bump_intel(c, slug, 1)
    return spawned


def _composite_region_match(loc) -> str:
    """The MATCH string the wander resolver should see for a location (F04-1).

    A location's danger signal lives across THREE fields: `region` (the parent zone,
    often a bare name like "Baldur's Gate" that matches no keyword), `name` (e.g.
    "The Lower City"), and `notes` (where ingest joins the area's tags — "market city
    hub"). The bare `region` alone is what the resolver historically saw, so every
    Baldur's Gate scene fell through to the wilderness BASE_RATE. Joining all three
    lets the substring matcher catch the real keyword ("city"/"market"/"sewer"). The
    payload's WIRE `region` value stays `loc.region` (the seams pass this only to the
    resolver, never as the displayed region) so the contract's semantics don't shift."""
    if loc is None:
        return ""
    parts = [loc.region or "", loc.name or "", loc.notes or ""]
    return " ".join(p for p in parts if p).strip()


def _stage_wandering_encounter(
    c: Campaign,
    region: str,
    *,
    difficulty: str = "medium",
    modifiers: dict | None = None,
    location_id=None,
    force: bool = False,
    rng: random.Random | None = None,
    match_region: str | None = None,
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
    inside `pick_typed_encounter`, so a hit always yields SOME staged encounter.

    F04-1: `match_region` (when given) is the composite "<region> <name> <notes>" the
    seam built so the resolver can read the location's danger keyword from its NAME +
    tags, not just the bare parent zone (a Baldur's Gate area's region="Baldur's Gate"
    matches no keyword). The chance/pool/type are picked off `match_region`; the
    returned payload's `region` key stays the DISPLAY `region` so the wire value's
    semantics are unchanged. `match_region=None` (the default) reproduces today's
    behavior exactly (resolve off `region`)."""
    if not force and not c.house_rules.wandering_encounters:
        return None
    region = region or ""
    # The resolver reads the COMPOSITE (region + name + notes) when the seam supplied
    # one; the displayed `region` stays bare. Default None -> resolve off `region`.
    resolve_region = match_region if match_region is not None else region
    if not force and not wander.roll_encounter(resolve_region, modifiers, rng=rng):
        return None
    levels = _party_levels(c)
    r = rng or random.Random()
    picked = wander.pick_typed_encounter(
        levels,
        resolve_region,
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
    hand it, so the correct number has to be visible at the point of attack. Melee uses STR —
    UNLESS the character carries a FINESSE weapon (audit F01-4 / #774): finesse uses
    max(STR, DEX) on attack AND damage, so a rapier rogue's surfaced melee numbers are the
    DEX ones, not a wrong STR line. Ranged uses DEX; damage modifiers are the same ability
    mod. A rogue's Sneak Attack dice are surfaced too (audit F01-5 / #166) — the sheet tracks
    them but the attack trigger never showed them, hiding ~half the class's damage."""
    prof = ch.proficiency_bonus
    str_mod = ch.ability_modifier(Ability.STR)
    dex_mod = ch.ability_modifier(Ability.DEX)
    nums = {
        "proficiency_bonus": prof,
        "ability_mods": {a.value: ch.ability_modifier(a) for a in Ability},
        "melee_attack_bonus": prof + str_mod,        # STR weapon
        "ranged_attack_bonus": prof + dex_mod,        # DEX / finesse weapon
        "melee_damage_mod": str_mod,
        "ranged_damage_mod": dex_mod,
        "note": "Pass these to attack(attack_bonus=…, damage_dice='NdM+<mod>'); the engine "
                "trusts the number, so use the sheet's — never copy another combatant's.",
    }
    # FINESSE (#774): if the character carries a finesse weapon (equipped first, else any
    # carried), melee attack AND damage use max(STR, DEX) — 5e's finesse rule. Read-surface
    # only: attack() keeps trusting the bonus the DM passes; non-finesse loadouts are
    # byte-identical (no key, same numbers).
    fin = next((it.name for it in ch.inventory
                if it.equipped and srd_tables.is_finesse_weapon(it.name)), None)
    if fin is None:
        fin = next((it.name for it in ch.inventory
                    if srd_tables.is_finesse_weapon(it.name)), None)
    if fin is not None:
        best = max(str_mod, dex_mod)
        ability = "dex" if dex_mod > str_mod else "str"
        nums["melee_attack_bonus"] = prof + best
        nums["melee_damage_mod"] = best
        nums["finesse"] = {
            "weapon": fin,
            "ability": ability,
            "note": (f"{fin} is a finesse weapon — melee attack/damage use "
                     f"max(STR, DEX) = {ability.upper()} here."),
        }
    # SNEAK ATTACK (F01-5, enriches #166): surface the sheet's dice + the 5e trigger at the
    # point of attack, as a ready-to-pass damage_rolls component — the engine then rolls it
    # (and crit-doubles it) through the existing multi-component path (#210). Absent for
    # non-rogues (byte-identical).
    if ch.sneak_attack_dice:
        nums["sneak_attack"] = {
            "dice": ch.sneak_attack_dice,
            "note": (f"Sneak Attack, ONCE PER TURN: when the attack has advantage, OR an "
                     f"ally is within 5 ft of the target and the attack lacks disadvantage, "
                     f"add it to that attack's damage_rolls as a component "
                     f"{{'dice': '{ch.sneak_attack_dice}', 'type': <weapon damage type>}} — "
                     f"the engine rolls it and doubles it on a crit."),
        }
    return nums


@mcp.tool()
def get_character(campaign_id: str, character_id: str = "", target_id: str = "", id: str = "") -> dict:
    """Return a character's full sheet, including depletable class-resource pools
    (Rage, Ki, Lay on Hands, Channel Divinity, …) under `class_resources` plus a
    `class_resources_view` with fables-style remaining/max bars, and a `combat_numbers`
    block (sheet-derived attack/damage bonuses) so the DM never hand-invents a to-hit.

    Identify the character via ``character_id`` (canonical) or the aliases ``target_id`` /
    ``id`` — equivalent; ``character_id`` wins if more than one is given."""
    character_id = character_id or target_id or id  # accept the id the DM reaches for
    if not character_id:
        raise ValueError("get_character needs a character (pass `character_id` or an alias: `target_id`/`id`)")
    c = _require(campaign_id)
    ch = _char(c, character_id)  # ONE resolve-then-suggest site (F14-8), not an inline copy
    sheet = ch.model_dump(mode="json")
    sheet["class_resources_view"] = _class_resources_view(ch)
    sheet["combat_numbers"] = _combat_numbers(ch)
    return sheet


@mcp.tool()
def list_canon_characters(
    campaign_id: str, playable_only: bool = False, q: str = "", limit: int = 100
) -> dict:
    """Who's available to pull into THIS world from the ingested canon roster. Use
    load_canon_character to bring one in as an NPC/companion.

    The flagship world ships a LARGE roster (the post-BG3 wiki-first ingest is ~2,000
    canon records), so this surface is BOUNDED: filter with `q` and page with `limit`
    instead of dumping the whole cast (~180KB) every call. For a STRUCTURED pull
    ("the merchant in this region", "a Harper", "an antagonist") reach for `find_npcs`
    (tags / faction / role / location) — it is the precise picker.

      * `q`      — case-insensitive substring of the display name (e.g. "shadow",
                   "minsc"). Empty (default) lists the roster head.
      * `limit`  — max rows returned (default 100, capped at 200). When more match than
                   fit, `truncated` is True and `note` points at `find_npcs`/`q`.

    Returns ``{world_id, total, returned, available:[{name, race, class, playable,
    role}], truncated, note?}`` — `total` is the full match count, `available` the
    bounded page. The top heroes of the era (the BG3 origin companions) are
    `playable: false` — legends/quest-givers/encounterable NPCs, not a hero the player
    embodies; pass `playable_only=True` for just the minor figures a player may pick up
    as their PC via start_character(origin="pickup:<name>")."""
    c = _require(campaign_id)
    if not c.world_id:
        return {"world_id": "", "total": 0, "returned": 0, "available": [], "truncated": False}
    n = max(1, min(int(limit), 200))
    matches = content_mod.list_canon_characters(
        c.world_id, playable_only=playable_only, name_contains=q
    )
    total = len(matches)
    page = matches[:n]
    out = {
        "world_id": c.world_id,
        "total": total,
        "returned": len(page),
        "available": page,
        "truncated": total > len(page),
    }
    if out["truncated"]:
        out["note"] = (
            f"{total} canon characters match; showing {len(page)}. Narrow with q=… "
            f"(name substring) or use find_npcs(tag/faction_id/arc_role/canon_location_id) "
            f"for a structured pull."
        )
    return out


@mcp.tool()
def find_npcs(
    campaign_id: str,
    tag: str = "",
    faction_id: str = "",
    is_merchant: bool = False,
    canon_location_id: str = "",
    arc_role: str = "",
    name_contains: str = "",
    limit: int = 50,
) -> dict:
    """Pull EXACTLY the canon characters you need by STRUCTURE, not by guessing names — the DM's
    "this merchant in this region", "this Harper", "a traveling merchant near the party" surface.
    Filters the world's ingested canon roster (content/worlds/<id>/characters/*.json) on the
    structured tagging fields and returns the matches with their key fields. READ-ONLY.

    Filters (give any subset; they AND-combine — a match satisfies ALL the ones you pass):
      * `tag`            — membership in the record's `tags` ("merchant", "companion", "noble", …)
      * `faction_id`     — canonical faction key ("harpers", "flaming-fist", "zhentarim")
      * `is_merchant`    — pass True to keep only traveling-merchant / shopkeeper NPCs (False, the
                           default, is treated as "don't filter on this" — it does NOT exclude
                           merchants; use `tag="merchant"` if you want the inverse intent)
      * `canon_location_id` — where the NPC is canonically found ("last-light-inn", "lower-city")
      * `arc_role`       — "companion" | "origin-hero" | "antagonist" | "minor"
      * `name_contains`  — case-insensitive substring of the display name
      * `limit`          — cap on returned matches (default 50)

    Returns {world_id, count, matches:[{name, race, class, tags, faction_id, is_merchant,
    canon_location_id, arc_role, role, playable, source_url}]}. Bring one in with
    load_canon_character. An empty result means nothing matched (or the world ships no tagged
    roster yet)."""
    c = _require(campaign_id)
    if not c.world_id:
        return {"world_id": "", "count": 0, "matches": []}
    matches = content_mod.find_canon_characters(
        c.world_id,
        tag=tag,
        faction_id=faction_id,
        # `False` is the MCP-tool default and means "unset" here (callers can't pass None over the
        # wire); only an explicit True narrows to merchants. The underlying content helper takes a
        # true tri-state (None == ignore) for tests that want to assert the is_merchant=False slice.
        is_merchant=True if is_merchant else None,
        canon_location_id=canon_location_id,
        arc_role=arc_role,
        name_contains=name_contains,
        limit=limit,
    )
    return {"world_id": c.world_id, "count": len(matches), "matches": matches}


@mcp.tool()
def load_canon_character(campaign_id: str, name: str = "", kind: str = "npc", add_to_party: bool = False,
                         character_name: str = "", canon_name: str = "") -> dict:
    """Pull a CANON character (e.g. Shadowheart, Astarion, Gale) from the world's ingested
    roster into this campaign — with their real identity: race, class, and the
    appearance / personality / mannerisms / backstory the DM voices from. Makes the
    post-BG3 cast encounterable instead of re-invented. `kind`="npc" (default) or
    "companion"; `add_to_party` brings a companion along. For a full COMBAT sheet, follow
    with apply_srd_defaults / recruit_companion. Refuses a duplicate name.

    Name the character via ``name`` (canonical) or the aliases ``character_name`` /
    ``canon_name`` — ``name`` wins if more than one is given."""
    name = name or character_name or canon_name  # accept the name the DM reaches for
    if not name:
        raise ValueError("load_canon_character needs a name (pass `name` or an alias: `character_name`/`canon_name`)")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        rec = content_mod.load_canon_character(c.world_id, name) if c.world_id else None
        if rec is None:
            # SYN-03: resolve-then-suggest — NEVER dump the whole roster (180KB on the
            # flagship world). load_canon_character already does exact + unique-substring
            # resolution, so a miss here is a typo or a wrong name: offer up-to-5
            # did_you_mean names + the roster size, keep the `error` key (play.sh reads it).
            did_you_mean, count = (
                content_mod.suggest_canon_names(c.world_id, name) if c.world_id else ([], 0)
            )
            return {
                "error": f"no canon character {name!r} for world {c.world_id!r}",
                "did_you_mean": did_you_mean,
                "available_count": count,
                "note": "list_canon_characters(q=…) to search the roster.",
            }
        canonical = rec.get("name", name)
        # HARD GATE (#305): a canon-DEAD figure (a corpse like Dal Lightspark, whose lineage
        # opens "a dead gold dwarven Harper whose corpse is in the Shadow-Cursed Lands") may
        # still be pulled in as a LORE npc, but must NEVER be seated as the PLAYER CHARACTER —
        # the prestige-CRPG framing breaks if the PC's canon-truth is "dead and rotting". This
        # backstops the picker (roster_surface alive_only) AND the play.sh / run_duo seat path,
        # so neither the UI nor a scripted run can ever bind a dead PC. Returns the standard
        # {"error": …} dict (play.sh checks rec.get("error") and falls back to a living pick).
        if kind == "player" and content_mod.is_dead_record(rec):
            return {
                "error": (f"{canonical} is dead in canon and cannot be the player character — "
                          f"pick a living canon NPC (list_canon_characters(playable_only=True) "
                          f"lists only living, playable figures)."),
                "dead_in_canon": True,
                "name": canonical,
            }
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
            # A canon figure can be pulled in as the PROTAGONIST (the player), not just an
            # npc/companion — using one as the PC is a documented QA path for player
            # personas. Coercing kind down to "npc"/
            # "companion" meant a canon-loaded PC left `party` with ZERO kind=="player" members
            # and tripped the player_in_party behavioral gate (QA: ow-duoF went RED). Allow
            # "player" through; anything unexpected still defaults to "npc".
            kind=kind if kind in ("npc", "companion", "player") else "npc",
            race=rec.get("race", ""),
            classes=classes,
            alignment=rec.get("alignment", ""),
            personality=rec.get("personality", ""),
            appearance=rec.get("appearance", ""),
            mannerisms=rec.get("mannerisms", ""),
            backstory=rec.get("backstory", ""),
            notes=rec.get("voice_hint", ""),
            location_id=c.current_location_id,
            # ADDITIVE: carry the canon record's STRUCTURED tags onto the live Character so a
            # pulled NPC keeps its merchant/faction/arc identity (and `find_npcs`-derived data
            # isn't lost the moment the DM brings the figure in). All empty-default, so a record
            # with no tagging fields behaves exactly as before. (The owner also flagged that the
            # canon `role`/`playable` were being dropped at load — `arc_role` now preserves the
            # narrative role, and `playable` stays available on the returned rec.)
            tags=[str(t) for t in (rec.get("tags") or []) if str(t).strip()],
            faction_id=str(rec.get("faction_id", "") or ""),
            is_merchant=bool(rec.get("is_merchant", False)),
            canon_location_id=str(rec.get("canon_location_id", "") or ""),
            arc_role=str(rec.get("arc_role", "") or ""),
            quest_ties=[str(q) for q in (rec.get("quest_ties") or []) if str(q).strip()],
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
        # A canon record carries class + level but NOT a combat sheet, and the DM often pulls a
        # canon figure straight in as the PC/companion without a follow-up apply_srd_defaults — so
        # apply the SRD class defaults here so a canon-loaded character has real proficiencies, prof
        # bonus, HP, saves, features, and (for casters) a castable spellbook instead of a bare stub
        # (QA: a canon-loaded level-5 Wizard's Arcana came out at raw INT +3, missing the class
        # proficiency). _apply_srd_class_defaults is idempotent and only fills EMPTY values (skills,
        # HP at the max_hp<=1 stub, spells), so explicit canon stats and a later recruit_companion
        # are both respected. Classless canon figures fall through to the HP floor below.
        # ABILITY SCORES (#fix canon flat-10): a canon record ships class + level but almost never
        # an `abilities` block, and the Character default is a flat 10/10/10/10/10/10 — so a
        # canon-loaded character (incl. a Wizard PC or a Rogue companion) was casting/acting at +0
        # across the board (QA ow-v103-reval: Dal Lightspark, a L5 evoker, loaded at INT 10 → all
        # spell DCs/attacks at +0; 11 NPCs flat-10 too). Derive a class- and level-appropriate 5e
        # standard array (15,14,13,12,10,8 down the class's priority, +2 to primary per ASI level)
        # BEFORE the SRD defaults run, so HP/AC/initiative compute off real CON/DEX. Honor an
        # explicit canon `abilities` block unchanged where one exists (forward-compatible — none in
        # the current corpus, but a hand-authored sheet must win). A class-less / unknown-class
        # record can't be sized, so it KEEPS the flat-10 default (today's behavior) and we warn.
        # All of the above now runs through the shared seat finisher (F02-1/F02-4):
        # canon `abilities` block -> derived class array -> placeholder, then the SRD
        # defaults, then — for a PARTY seat (player/companion) — the starting gear+purse
        # the claimed AC implies. load_canon used to apply the SRD AC but never the armor
        # (Jun-9 Alfira: AC 14, inventory [], 0 gp — one of 53 wild AC>=14-no-armor
        # records); a lore NPC pull stays gearless. The seeder self-guards, so a record
        # that ships its own kit/purse is untouched.
        ability_source = _finish_seat_sheet(
            ch,
            classes[0].name if classes else "",
            classes[0].level if classes else 1,
            set_base_ac=(ch.armor_class == 10),
            rec_abilities=rec.get("abilities"),
            seed_gear=(ch.kind in ("player", "companion")),
        )
        # MAX_HP (#352 — canon PC seated with a critically-low max_hp). The Character default is a
        # placeholder max_hp=1, and an identity stub left at 1 HP is an INSTANT-KILL combatant: the
        # first hit trips combat's SRD massive-damage rule (damage >= max_hp at 0 HP) and flags it
        # dead before it's ever fleshed out (QA: a canon NPC pulled into a fight pre-recruit died in
        # one hit). The OLD code floored every canon load to `max(canon_hp, 10)` — but for a CLASSED
        # record that floor CLOBBERED the class+level HP _apply_srd_class_defaults just computed
        # above (QA ow-living1: Latham, a L5 Guild Wizard with no `max_hp` field, was seated at a
        # flat 10 instead of his class+level 32 — the angry-dm scorer's "single worst seam, must fix
        # before combat"). Mirror the #322 ability-derivation: when the canon record lacks a SENSIBLE
        # max_hp, DERIVE a class+level-appropriate one (hit-die + CON modifier per level, the same
        # _class_level_hp formula the SRD defaults use) so a seated canon combatant is sized for its
        # class+level. Precedence, deterministic + additive:
        #   1. _apply_srd_class_defaults already set max_hp from the class+level floor (it ran on the
        #      max_hp<=1 stub) — that is the class-appropriate value; keep it as the floor.
        #   2. an EXPLICIT canon max_hp/hit_points that is >= that class floor is honored (a
        #      hand-authored sheet, or a higher-than-formula canon value, always wins upward).
        #   3. a class-less / unknown-class record (no floor) keeps the modest flat-10 stub default.
        # An explicit canon HP BELOW the class+level floor is treated as a low placeholder and the
        # class floor wins (the issue's "absent, OR below the class+level floor" case).
        try:
            canon_hp = int(rec.get("max_hp") or rec.get("hit_points") or 0)
        except (TypeError, ValueError):
            canon_hp = 0
        # The class+level floor (None for a class-less / unknown-class record). Computed off the now-
        # seated abilities (derived/canon/placeholder), so CON is real — matches the SRD-defaults HP.
        con_mod = ch.abilities.modifier(Ability.CON)
        class_floor = _class_level_hp(classes[0].name, classes[0].level, con_mod) if classes else None
        if class_floor is not None:
            # Classed: never below the class+level floor; an explicit canon value above it wins.
            ch.max_hp = max(class_floor, canon_hp)
        else:
            # Class-less / unknown class: honor an explicit canon HP, else the modest flat-10 stub.
            ch.max_hp = max(canon_hp, 10)
        ch.current_hp = ch.max_hp  # a fresh identity stub stands at full health
        # INVARIANT: a kind="player" character IS the party's protagonist — always in the
        # party, regardless of add_to_party. QA ow-rv1: the brief told the DM to load a canon
        # PC via load_canon_character(kind="player"), but add_to_party defaults False, so the
        # PC got kind="player" yet sat OUTSIDE c.party → player_in_party gate RED (party had
        # only a recruited companion). Force the player in.
        if add_to_party or ch.kind == "player":
            ch.met = True  # brought into the party => met
            if ch.id not in c.party:
                c.party.append(ch.id)
        # F06-1: this path seeded the DOSSIER (above) but never an ARC, so a canon-loaded
        # companion had arc=null and camp/gates were inert. Run the shared seeding helper
        # (companion-only, None-guarded) so it adds the default arc — and only synthesizes a
        # dossier if the canon record didn't already carry one (_coerce_dossier left it None).
        if ch.kind == "companion":
            _seed_companion_operational_state(ch)
        c.characters[ch.id] = ch
        save_campaign(c)
        # ADDITIVE WARNING (non-fatal): a PLAYER or a SPELLCASTER that still ended at the flat-10
        # placeholder array is a real gameplay defect (a caster's DCs/attacks are all +0). It's not
        # a hard fail — a class-less canon NPC legitimately has no derived sheet — but the behavioral
        # gate / QA should SEE it. Surface it both in the tool return (`ability_source` + `warnings`)
        # and on stderr (which the QA harness captures). A character WITH explicit/derived abilities,
        # or a non-caster NPC, produces no warning.
        warnings: list[str] = []
        ABK = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
        is_flat_ten = all(getattr(ch.abilities, f) == 10 for f in ABK)
        is_caster = bool(ch.spell_slots or ch.spells_known or ch.spells_prepared)
        if is_flat_ten and (ch.kind == "player" or is_caster):
            who = "player" if ch.kind == "player" else "spellcaster"
            warn = (f"canon character {ch.name!r} ({rec.get('class') or 'class-less'}) loaded as a "
                    f"{who} with a PLACEHOLDER 10/10/10/10/10/10 ability array — its checks, saves, "
                    f"and spell DCs are all +0. The canon record carries no `abilities` block and "
                    f"its class could not be sized; flesh it out via update_character / recruit_companion.")
            warnings.append(warn)
            print(f"[worldos:load_canon_character] WARNING: {warn}", file=sys.stderr)
        return {
            "id": ch.id, "name": ch.name, "race": ch.race, "kind": ch.kind,
            "class": rec.get("class", ""), "source": rec.get("source_url", ""),
            "in_party": ch.id in c.party,
            # Where the seated ability scores came from: "canon" (the record carried an explicit
            # block), "derived" (a class+level-appropriate standard array we generated), or
            # "placeholder" (flat 10s — class-less/unknown record we couldn't size).
            "ability_source": ability_source,
            "warnings": warnings,
            "note": "Identity loaded. For a full combat sheet, call apply_srd_defaults or recruit_companion.",
        }


def _readable_validation_error(exc, *, where: str) -> str:
    """A bounded, DM-readable one-liner from a pydantic ValidationError (F14-11 / #812).

    The raw `str(ValidationError)` is a multi-KB wall ending in an ``errors.pydantic.dev``
    URL — a DM who reads that gives up on the tool and freehands. We surface only the first
    few field errors as ``field: message`` pairs, ≤~400 chars, no URL. ADDITIVE: callers
    still get a ``ValueError`` so the strict typo-forbid guard (a bad patch still RAISES)
    is preserved — only the wording shrinks."""
    parts: list[str] = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    n = len(exc.errors())
    more = f" (+{n - 3} more)" if n > 3 else ""
    body = "; ".join(parts) + more
    return f"{where}: {body[:400]}"


@mcp.tool()
def update_character(campaign_id: str, character_id: str = "", patch: dict = None,
                     target_id: str = "", id: str = "") -> dict:
    """Apply a partial update to a character and persist it.

    `patch` is a dict of fields to change (deep-merged for nested objects), e.g.
    {"current_hp": 12, "armor_class": 15}. Unknown field names are REJECTED.

    CLASS / LEVEL: level lives inside `classes` (a list of {name, level, subclass}),
    so the canonical retier patch is `{"classes":[{"name":"Wizard","level":3}]}`. As a
    convenience the flat aliases `level`, `class_name`, and `subclass` are also accepted
    and folded into the head class entry. When the class/level changes — by EITHER form —
    the engine recomputes the level-scaled SRD math for the new level: proficiency bonus,
    saves, hit dice, max HP, and extra attacks (so a down-level retier doesn't keep the old
    tier's inflated stats). Any stat the SAME patch sets explicitly is honored (a DM-chosen
    HP/AC wins), and a DM-chosen AC is never auto-clobbered. A genuine unknown field (a typo)
    is still rejected.

    WARNING: list fields (conditions, inventory, spells_known, classes) are
    REPLACED wholesale by the patch, not merged. To change a single condition
    use add_condition / remove_condition; for HP use set_hp. Vitals are clamped
    to valid ranges (current_hp to 0..max_hp, exhaustion to 0..6).
    Identify the character via ``character_id`` (canonical) or the aliases ``target_id`` /
    ``id`` — equivalent; ``character_id`` wins if more than one is given (this is the
    character-identity arg, distinct from the flat class aliases inside ``patch``).
    """
    character_id = character_id or target_id or id  # accept the id the DM reaches for
    if not character_id:
        raise ValueError("update_character needs a character (pass `character_id` or an alias: `target_id`/`id`)")
    if patch is None:
        raise ValueError("update_character needs a `patch` object")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        data = ch.model_dump(mode="json")
        _deep_update(data, patch)
        data["id"] = character_id  # identity is IMMUTABLE: the stored model's id must equal its
        # dict key, so a patch can't strand the character under a visible-but-unusable id (#41).
        # DM affordance: accept the intuitive flat class keys ({"level":3,"class_name":"Wizard",
        # "subclass":...}) by folding them into the canonical `classes` patch, so a reasonable
        # level/class edit isn't rejected by the model's extra="forbid" (QA ow-fixC: a DM's
        # level-3 retarget failed and the PC played the whole session at the wrong tier). The
        # strict rejection is load-bearing (test_engine asserts a typo like "max_hpp" must raise)
        # so we TRANSLATE these three known aliases here in the tool rather than loosen the model —
        # a genuine typo ("levl") still trips forbid.
        flat_level = data.pop("level", None)
        flat_class = data.pop("class_name", None)
        flat_subclass = data.pop("subclass", None)
        if flat_level is not None or flat_class is not None or flat_subclass is not None:
            existing = data.get("classes") or [{}]
            head = dict(existing[0]) if isinstance(existing[0], dict) else {}
            if flat_class is not None:
                head["name"] = flat_class
            if flat_level is not None:
                head["level"] = flat_level
            if flat_subclass is not None:
                head["subclass"] = flat_subclass
            head.setdefault("name", ch.classes[0].name if ch.classes else "")
            data["classes"] = [head] + list(existing[1:])
        # DM affordance: 'skills'/'expertise' are the intuitive names for the model's
        # skill_proficiencies/skill_expertise (QA ow-swB: a DM set proficiencies via
        # patch={"skills":["Arcana",...]} and tripped extra="forbid", flipping the
        # no_rejected_tool_calls gate RED). Translate the two known aliases here, same as
        # the class aliases above — a genuine typo ("skilz") still trips forbid.
        flat_skills = data.pop("skills", None)
        flat_expertise = data.pop("expertise", None)
        if flat_skills is not None:
            data["skill_proficiencies"] = flat_skills
        if flat_expertise is not None:
            data["skill_expertise"] = flat_expertise
        # F14-11 (#812): `in_party` is a COMPUTED read field (ch.id in c.party), NOT a Character
        # field — a DM who patched it tripped extra="forbid" with a raw multi-KB pydantic wall.
        # Pop it BEFORE model_validate and translate to a c.party mutation that mirrors
        # recruit_companion / dismiss (the sole-writer membership edit). Truthy -> join; falsey ->
        # leave. Omitted -> membership untouched (byte-identical to today). The mutation is applied
        # AFTER the validated sheet is stored below, so a rejected patch never strands membership.
        in_party_intent = data.pop("in_party", None)
        # F14-11: a genuine type/typo error inside the model is wrapped into ONE bounded, readable
        # line (no errors.pydantic.dev wall) so the DM can fix it in the next call instead of
        # freehanding. STILL raises a ValueError, so the strict typo-forbid guard stays load-bearing.
        try:
            new_ch = Character.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                _readable_validation_error(exc, where="update_character patch is invalid")
            ) from exc
        # Recompute derived class math when the class/level SIGNATURE changed — via EITHER the flat
        # aliases OR a direct `classes` patch. (RRI 2026-06-09: a canon L12 Fighter "Gravedigger
        # Karcen" was patched to L3 via the canonical {"classes":[{"name":"Fighter","level":3}]}
        # form — the OLD guard fired only on the flat aliases, so this skipped recompute entirely and
        # he fought with L12 prof_bonus(+4)/max_hp(100)/12d10/extra_attacks(2): the angry-dm
        # "correctly-wrong numbers" that capped the mech score.) _apply_srd_class_defaults fills
        # saves/skills/features and resets prof_bonus + the hit-dice string, but it is FILL-EMPTY for
        # max_hp (only at the <=1 stub) and accumulates extra_attacks via max(), so it cannot LOWER
        # them on a down-level — _recompute_level_scaled_stats overwrites those from the new
        # total_level. Any of these the SAME patch set EXPLICITLY wins (a DM-chosen HP/AC); a typo
        # still trips extra="forbid". A non-class patch (set_hp, conditions, ...) is untouched.
        old_sig = [(cl.name.lower(), cl.level, (cl.subclass or "")) for cl in ch.classes]
        new_sig = [(cl.name.lower(), cl.level, (cl.subclass or "")) for cl in new_ch.classes]
        if old_sig != new_sig and new_ch.classes:
            # F02-5: a class/level retier must re-derive the POOL SIZE (hit_dice string scales
            # to the new level) but must NOT silently refill a previously-SPENT pool. Capture the
            # pre-recompute remaining BEFORE _apply_srd_class_defaults unconditionally resets it
            # to `level`, so _recompute_level_scaled_stats can cap against the spent count (a DM
            # who patched a brand-new full-pool record still gets `min(spent, total)` == today).
            prior_hit_dice_remaining = new_ch.hit_dice_remaining
            _apply_srd_class_defaults(new_ch, new_ch.classes[0].name,
                                      new_ch.total_level, set_base_ac=False)
            _recompute_level_scaled_stats(new_ch, patch or {},
                                          prior_hit_dice_remaining=prior_hit_dice_remaining)
        # #733: keep initiative_bonus == DEX modifier when a patch changes the DEX score.
        # Every engine write derives initiative_bonus from the DEX modifier (create_character,
        # level_up's ASI path, the canon-load derivation), but update_character — the path a DM
        # uses to correct/raise a stat directly — only recomputed the level-scaled class math, so
        # a DEX edit left initiative_bonus FROZEN (optimizer RRI: "+1 shown vs +2 expected" — the
        # combat roll 1d20+initiative_bonus and the heroes-screen `initiative` both read the stale
        # value). Recompute from the new modifier when DEX moved, UNLESS the same patch set
        # initiative_bonus EXPLICITLY (a DM Alert-feat/house-rule override wins). Purely additive:
        # a non-DEX patch leaves the field exactly as before.
        dex_changed = ch.ability_modifier(Ability.DEX) != new_ch.ability_modifier(Ability.DEX)
        if dex_changed and "initiative_bonus" not in (patch or {}):
            new_ch.initiative_bonus = new_ch.ability_modifier(Ability.DEX)
        # F02-6: a CON change retro-adjusts max HP by the CON-modifier DELTA across every level
        # the character has (raising CON grants +1 HP per level; a corrective drop removes them,
        # floored at 1). Delta-based — it respects a DM-authored HP base and a multiclass sheet
        # rather than re-deriving from scratch. Skipped when the SAME patch set max_hp explicitly
        # (a DM-chosen HP wins) or already recomputed the class math above (a class-sig retier
        # re-derives max_hp from the new total_level, which already reflects the new CON).
        con_changed = ch.ability_modifier(Ability.CON) != new_ch.ability_modifier(Ability.CON)
        class_sig_changed = old_sig != new_sig and new_ch.classes
        if (con_changed and "max_hp" not in (patch or {}) and not class_sig_changed
                and new_ch.total_level > 0):
            con_delta = new_ch.ability_modifier(Ability.CON) - ch.ability_modifier(Ability.CON)
            hp_delta = con_delta * new_ch.total_level
            new_ch.max_hp = max(1, new_ch.max_hp + hp_delta)
            new_ch.current_hp = max(1, min(new_ch.current_hp, new_ch.max_hp))
        c.characters[character_id] = new_ch
        # F14-11 (#812): apply the translated party-membership intent AFTER the validated sheet is
        # stored (a rejected patch never reaches here). Mirrors recruit_companion / dismiss: a
        # truthy in_party joins, a falsey one leaves. Idempotent + order-preserving.
        if in_party_intent is not None:
            if in_party_intent:
                if character_id not in c.party:
                    c.party.append(character_id)
            else:
                c.party = [pid for pid in c.party if pid != character_id]
        save_campaign(c)
        out = c.characters[character_id].model_dump(mode="json")
        # Surface the computed membership so a DM who set in_party sees it took (mirrors the
        # `in_party` key recruit_companion / load_canon_character already return). Always present
        # for a stable shape; additive (a field that was never on the model_dump).
        out["in_party"] = character_id in c.party
        return out


@mcp.tool()
def add_condition(
    campaign_id: str,
    character_id: str = "",
    condition: str = "",
    repeat_save_ability: str = "",
    repeat_save_dc: int = 0,
    source_id: str = "",
    spell_name: str = "",
    target_id: str = "",
    id: str = "",
) -> dict:
    """Add a 5e condition to a character (idempotent). Prefer this over patching
    the whole conditions list. Valid values: blinded, charmed, deafened,
    frightened, grappled, incapacitated, invisible, paralyzed, petrified,
    poisoned, prone, restrained, stunned, unconscious.

    SAVE-ENDS conditions (#209): for a "the target repeats the save at the end of
    each of its turns" effect (Hold Person → paralyzed, Hold Monster, a monster's
    hold), pass `repeat_save_ability` (str/dex/con/int/wis/cha) + `repeat_save_dc`
    (the caster's spell save DC) so the ENGINE rolls that recurring save in next_turn
    and frees the target on a success — instead of the target staying locked because
    the DM forgot to prompt the save. `source_id` (the caster) + `spell_name` link the
    effect to its concentration so a successful escape also ends the caster's
    concentration. ADDITIVE: omit these and the condition behaves exactly as before
    (no end-of-turn save — the DM resolves any save by hand). cast_spell surfaces the
    `condition_rider` hint telling you exactly which values to pass here.

    Identify the character via ``character_id`` (canonical) or the aliases ``target_id`` /
    ``id`` — ``character_id`` wins if more than one is given."""
    character_id = character_id or target_id or id  # accept the id the DM reaches for
    if not character_id:
        raise ValueError("add_condition needs a character (pass `character_id` or an alias: `target_id`/`id`)")
    if not condition:
        raise ValueError("add_condition needs a `condition`")
    cond = Condition(condition.lower())
    rs_ability = _parse_ability(repeat_save_ability) if repeat_save_ability else None
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
            was_conc = ch.concentration
            ch.concentration = None  # SRD: incapacitation breaks concentration
            combat.expire_concentration_effects(ch)  # ...and its engine-tracked effect
            # F3-6: incapacitation ends the spell — free its held victims NOW (Hold Person
            # paralysis, an allied Bless child), not a round later at next_turn's sweep.
            _release_held_targets(c, character_id, was_conc or "")
        # Save-ends linkage (#209): record a TARGET-side ActiveEffect carrying the recurring
        # end-of-turn save + the condition it imposed, so next_turn self-enforces the escape.
        # The marker is NOT a concentration twin (concentration=False) — the caster's twin
        # lives on the caster; this only remembers "what to roll, what to clear on success".
        # Re-applying the same save-ends spell/condition refreshes (doesn't stack) the marker.
        if rs_ability is not None and repeat_save_dc > 0:
            eff_name = spell_name or f"{cond.value} (save ends)"
            # Keep source_id as a CONCENTRATION link only when the source spell actually
            # concentrates (Hold Person does). Then it precisely signals "this marker is the
            # twin of source_id's concentration" so next_turn frees the target if that
            # concentration ends. A non-concentration save-ends source (a monster's innate
            # hold) still self-enforces its end-of-turn save, but carries no concentration link
            # (source_id dropped) — so the inverse reconciliation never wrongly sweeps it.
            concentrates = False
            if spell_name:
                rec = spells.srd_spell(spell_name)
                try:
                    cur_rec = spells.spell_data(spell_name)
                except ValueError:
                    cur_rec = None
                concentrates = bool(
                    (cur_rec.get("concentration") if cur_rec else None)
                    or (rec.get("concentration") if rec else None)
                )
            ch.active_effects = [e for e in ch.active_effects if e.name != eff_name]
            ch.active_effects.append(
                ActiveEffect(
                    name=eff_name,
                    source_id=source_id if concentrates else "",
                    imposes_condition=cond,
                    repeat_save=RepeatSave(
                        ability=rs_ability, dc=repeat_save_dc, ends_effect=True
                    ),
                )
            )
        save_campaign(c)
        return {**ch.model_dump(mode="json"), "immune": False, "added": added}


@mcp.tool()
def remove_condition(campaign_id: str, character_id: str = "", condition: str = "",
                     target_id: str = "", id: str = "") -> dict:
    """Remove a 5e condition from a character (no-op if not present). Identify the character
    via ``character_id`` (canonical) or the aliases ``target_id`` / ``id`` — ``character_id``
    wins if more than one is given."""
    character_id = character_id or target_id or id  # accept the id the DM reaches for
    if not character_id:
        raise ValueError("remove_condition needs a character (pass `character_id` or an alias: `target_id`/`id`)")
    if not condition:
        raise ValueError("remove_condition needs a `condition`")
    cond = Condition(condition.lower())
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.conditions = [x for x in ch.conditions if x != cond]
        if cond == Condition.GRAPPLED:
            ch.grappled_by = None  # F01-8: hold lifted — clear the grappler link
        save_campaign(c)
        return ch.model_dump(mode="json")


@mcp.tool()
def set_hp(
    campaign_id: str, character_id: str = "", current_hp: int = 0, temp_hp: Optional[int] = None,
    target_id: str = "", id: str = ""
) -> dict:
    """Set a character's current HP (and optionally temporary HP). Values are
    clamped to valid ranges by the engine (current_hp to 0..max_hp, temp_hp >= 0).
    Identify the character via ``character_id`` (canonical) or the aliases ``target_id`` /
    ``id`` — ``character_id`` wins if more than one is given."""
    character_id = character_id or target_id or id  # accept the id the DM reaches for
    if not character_id:
        raise ValueError("set_hp needs a character (pass `character_id` or an alias: `target_id`/`id`)")
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


def _multiattack_counting_clause(desc: str) -> str:
    """Reduce a Multiattack desc to its COUNTING clause before parsing (#771, F01-1).

    Two SRD wording families inflated the naive number-sum for 13/344 creatures
    (~+50% DPR, ENFORCED by attack()'s ceiling and INSTRUCTED via "Run N attack
    call(s)"):
      - substitution riders: "It can replace one attack with a Bite attack." (+1)
      - alternatives: "..., or it makes two Hurl Flame attacks." (both branches summed)
    1) sentence-split and drop any sentence containing 'replace' / 'instead of';
    2) split on the alternative-CLAUSE pattern ',? or (it|the X) makes' and keep the
       FIRST alternative — NOT bare ' or ', which also appears INSIDE counting
       clauses ("two Javelin or Morningstar attacks" — Bugbear Stalker; "using
       Shortsword or Light Crossbow in any combination" — Assassin) and must
       survive untouched.
    Falls back to the raw desc when filtering leaves nothing (defensive). Pure."""
    import re as _re
    sentences = _re.split(r"(?<=[.!?])\s+", desc)
    kept = [
        s for s in sentences
        if not _re.search(r"\breplace\b|\binstead of\b", s, _re.IGNORECASE)
    ]
    clause = _re.split(
        r",?\s+or\s+(?:it|the\s+\w+)\s+makes\b", " ".join(kept), flags=_re.IGNORECASE
    )[0]
    return clause if clause.strip() else desc


def _parse_multiattack_count(desc: str) -> int:
    """Return the total number of attack rolls a monster makes when it uses Multiattack.

    Handles the three SRD wording patterns:
    - "makes two X attacks"              → 2
    - "makes one X attack and one Y attack" → 1+1 = 2
    - "makes one Ram attack, one Bite attack, and one Claw attack" → 3
    Counts every (number + 'attack/attacks') clause, summing them — AFTER reducing
    the desc to its counting clause (replace/instead-of riders and ', or it makes'
    alternatives dropped; see _multiattack_counting_clause, #771).
    Falls back to the first number word in the desc when no clause matches,
    and ultimately defaults to 1 (conservative).
    """
    import re as _re
    desc = _multiattack_counting_clause(desc)
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


def _parse_multiattack_composition(desc: str) -> list[str]:
    """Return the ORDERED list of attack NAMES a Multiattack is composed of (#211),
    with repeats — so the DM issues the right attacks, not an improvised mix. Handles
    the SRD clause wordings:
      - "makes two Bite attacks"                         -> ['Bite', 'Bite']
      - "makes one Claw attack and one Bite attack"      -> ['Claw', 'Bite']
      - "makes one Ram attack, one Bite attack, and one Claw attack"
                                                          -> ['Ram', 'Bite', 'Claw']
    The attack name is the word(s) between the count and 'attack(s)'. Best-effort:
    returns [] when no '<count> <Name> attack(s)' clause parses (the caller then
    degrades to the count-only surfacing — never aborts). Title-cases each name so it
    matches the stat-block action names (which the resolver looks up case-sensitively
    only for display). Shares _parse_multiattack_count's counting-clause pre-filter
    (#771) so the sequence never spans a ', or it makes' alternative (Medusa surfaced
    a fully-resolving wrong 6-attack sequence) or a 'replace one attack' rider."""
    import re as _re
    desc = _multiattack_counting_clause(desc)
    _WORD_TO_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    names: list[str] = []
    # <count> <name words> attack(s) — name is 1-3 words, stops at 'attack(s)'.
    for m in _re.finditer(
        r"\b(one|two|three|four|five|six|\d+)\s+([A-Za-z][A-Za-z'\- ]*?)\s+attacks?\b",
        desc,
        _re.IGNORECASE,
    ):
        tok = m.group(1).lower()
        count = int(tok) if tok.isdigit() else _WORD_TO_NUM.get(tok, 1)
        # Trim filler that can precede the weapon name ("with its Claws", "melee").
        raw = m.group(2).strip()
        raw = _re.sub(r"^(?:with\s+(?:its\s+)?|melee\s+|ranged\s+)", "", raw, flags=_re.IGNORECASE).strip()
        if not raw:
            continue
        name = raw.title()
        names.extend([name] * max(1, count))
    return names


def _parse_damage_components(desc: str) -> list[dict]:
    """Extract EVERY typed damage component of an attack desc as an ordered list of
    ``{"dice", "type"}`` (#210). Each SRD component reads ``N (XdY + Z) <Type> damage``
    (the flat average, the dice in parens, then the type) and multiple components are
    joined by 'plus' — e.g. the Ghoul Bite ``5 (1d6 + 2) Piercing damage plus 3 (1d6)
    Necrotic damage`` -> ``[{dice:'1d6+2', type:'piercing'}, {dice:'1d6', type:
    'necrotic'}]``. The dice are normalized (whitespace stripped) so they pass straight
    to attack(damage_rolls=...). Best-effort: returns [] when no parenthesized dice are
    found (the caller then degrades to the flat single-`damage` string)."""
    import re as _re
    out: list[dict] = []
    # `(<dice>) <Type> damage` — Type is the word(s) immediately before 'damage'.
    for m in _re.finditer(
        r"\(\s*(\d*d\d+(?:\s*[+-]\s*\d+)?)\s*\)\s*([A-Za-z]+)\s+damage",
        desc,
        _re.IGNORECASE,
    ):
        dice = m.group(1).replace(" ", "")
        dtype = m.group(2).strip().lower()
        out.append({"dice": dice, "type": dtype})
    return out


def _parse_attack_action(action: dict) -> dict | None:
    """Parse a monster attack action dict (from bestiary.stat_block) into
    {name, to_hit, damage, damage_type, damage_rolls} with authoritative numeric
    to_hit pulled from the SRD desc ('Melee/Ranged Attack Roll: +5'). ``damage`` is
    the FIRST component's dice (back-compat with the count-only surfacing); when the
    attack deals more than one damage type (#210) ``damage_rolls`` carries every
    component ({dice,type}) ready to pass to attack(damage_rolls=...) and
    ``damage_type`` is the first component's type. Returns None for non-attack actions
    (no 'Attack Roll' in desc and action_type != 'ACTION').
    """
    import re as _re
    if action.get("action_type") != "ACTION":
        return None
    desc = action.get("desc", "")
    hit_m = _re.search(r"Attack Roll:\s*([+-]\d+)", desc, _re.IGNORECASE)
    if hit_m is None:
        return None  # not an attack action (e.g. Parry, Spellcasting)
    to_hit = int(hit_m.group(1))
    components = _parse_damage_components(desc)
    if components:
        damage = components[0]["dice"]
        damage_type = components[0]["type"]
    else:
        # Fallback: first bare dice expression anywhere (pre-#210 behaviour).
        dmg_m = _re.search(r"(\d+d\d+(?:\s*[+-]\s*\d+)?)", desc, _re.IGNORECASE)
        damage = dmg_m.group(1).replace(" ", "") if dmg_m else ""
        damage_type = ""
    out = {"name": action["name"], "to_hit": to_hit, "damage": damage, "damage_type": damage_type}
    # Only attach damage_rolls for genuinely MULTI-component attacks — a single-type
    # attack stays a plain {name,to_hit,damage} entry (no surface churn for the 95%
    # of attacks that are single-type; the DM uses damage_dice as before).
    if len(components) > 1:
        out["damage_rolls"] = components
    return out


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
    # Determine attacks_per_turn AND its composition from the Multiattack action.
    attacks_per_turn = 1
    multiattack_desc = ""
    composition_names: list[str] = []
    for act in actions:
        if act["name"].lower() == "multiattack":
            multiattack_desc = act.get("desc", "")
            attacks_per_turn = _parse_multiattack_count(multiattack_desc)
            composition_names = _parse_multiattack_composition(multiattack_desc)
            break
    # Collect authoritative attack actions (have 'Attack Roll' in desc)
    attack_list = []
    for act in actions:
        parsed = _parse_attack_action(act)
        if parsed is not None and act["name"].lower() != "multiattack":
            attack_list.append(parsed)
    entry = {
        "id": ch.id,
        "name": ch.name,
        "attacks_per_turn": attacks_per_turn,
        "attacks": attack_list,
        "note": (
            f"Run {attacks_per_turn} attack call(s) per Attack action using the surfaced "
            "to_hit/damage — never invent bonuses (mirrors PC _combat_numbers rule)."
        ),
    }
    # MULTIATTACK COMPOSITION (#211): surface WHICH attacks make up the Multiattack
    # (e.g. the Ghoul's 'two Bite attacks' -> two Bite entries, each with its full
    # multi-component damage), so the DM issues the stat-block's attacks rather than an
    # improvised mix (Bite+Claw). Best-effort + degrade-not-abort: only attach when the
    # desc parsed into names AND every name resolves to a known attack action; otherwise
    # leave the count-only surfacing untouched.
    if composition_names:
        by_name = {a["name"].lower(): a for a in attack_list}
        resolved = [by_name.get(n.lower()) for n in composition_names]
        if all(r is not None for r in resolved):
            entry["multiattack"] = {
                "desc": multiattack_desc,
                "sequence": [
                    {k: v for k, v in a.items()}  # full attack entry incl. damage_rolls
                    for a in resolved
                ],
                "note": (
                    "This monster's Multiattack is the SPECIFIC sequence below — issue "
                    "exactly these attacks (with each attack's surfaced to_hit/damage, and "
                    "damage_rolls for multi-type attacks), not an improvised mix."
                ),
            }
    # LEGENDARY ACTIONS (F01-13, audit 2026-06-11 — v1 SURFACE). 31 SRD bosses carry
    # LEGENDARY_ACTION-typed stat-block entries the engine never exposed, so a dragon never
    # got its between-turns actions and felt like a sack of HP. v1 SURFACES them so the DM
    # runs them; the v2 class_resources budget pool is deferred (flagged med-confidence in
    # the audit). Budget defaults to the SRD-standard 3 legendary actions per round (the
    # 2024 default for legendary creatures); each option's cost is 1 unless its desc says
    # "Costs N Actions". Additive — absent for the 95% of creatures with no legendary actions.
    legendary = [a for a in actions if str(a.get("action_type", "")).upper() == "LEGENDARY_ACTION"]
    if legendary:
        import re as _re2
        options = []
        for a in legendary:
            desc = str(a.get("desc", ""))
            cost_m = _re2.search(r"Costs?\s+(\d+)\s+Action", desc, _re2.IGNORECASE)
            options.append({
                "name": a.get("name", ""),
                "cost": int(cost_m.group(1)) if cost_m else 1,
                "desc": desc,
            })
        entry["legendary_actions"] = {
            "budget": 3,  # SRD default: 3 legendary actions per round, spent between turns
            "options": options,
            "note": (
                f"{ch.name} is a LEGENDARY creature: it may spend up to 3 legendary actions "
                "per round, ONE AT A TIME, at the END of another combatant's turn (not on its "
                "own turn; the budget refreshes at the start of its turn). Pick an option "
                "below (each costs 1 action unless noted) and resolve it via the normal verbs "
                "(attack / saving_throw / cast_spell). v1 SURFACE — the engine does not yet "
                "track the per-round budget; spend them in the fiction."
            ),
        }
    return entry


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


def _gate_combat_verb(c: "Campaign", actor: "Character", *, verb: str, consumes: str) -> None:
    """Enforce the SRD combat gates on a contest/utility verb (F01-7, audit 2026-06-11):
    grapple / shove (Attack-action options of the Unarmed Strike — 2024) and
    escape_grapple / stabilize (an Action). These verbs previously went straight to their
    DC + save resolution with NO incapacitation check, NO turn ownership, and NO economy
    write — the exact carve-out attack() / cast_spell already close. This mirrors that
    pattern, runs BEFORE any roll (a rejected verb changes NOTHING), and CONSUMES the
    economy on success.

    ``consumes``:
      * ``"attack"`` — grapple/shove: counts as one attack of the Attack action's budget
        (check_action_attack + bump action_attacks_made), so a grapple+attack in one turn
        is correctly limited by Extra Attack / Action Surge, and a grapple-only turn
        satisfies the PC-skip guard.
      * ``"action"`` — escape_grapple/stabilize: requires the actor's unspent Action and
        marks action_used.

    Inert when no combat is active OR the actor isn't in the initiative order (an
    out-of-initiative scuffle is the DM's call) — purely additive over today's behaviour.
    Raises ValueError on an illegal verb (incapacitated actor, wrong turn, no budget)."""
    # SRD: an incapacitated creature can take no action — refuse outright (mirrors attack()).
    if combat.is_incapacitated(actor):
        incap = ", ".join(cn.value for cn in actor.conditions if cn in combat.INCAPACITATING)
        raise ValueError(f"{actor.name} is incapacitated ({incap}) and cannot {verb}")
    if not c.combat.active:
        return  # out-of-initiative: inert, as before
    actor_cb = next(
        (cb for cb in c.combat.order if cb.character_id == actor.id), None
    )
    if actor_cb is None:
        return  # not a combatant in this fight: left to the DM
    is_current = c.combat.current_combatant_id == actor.id
    if not is_current:
        cur = c.characters.get(c.combat.current_combatant_id)
        cur_name = cur.name if cur else c.combat.current_combatant_id
        raise ValueError(
            f"it is not {actor.name}'s turn (it is {cur_name}'s) — {verb} is an action on "
            f"your own turn, not an off-turn move. Advance with next_turn so the order stays "
            f"in sync."
        )
    if consumes == "attack":
        ma = _attacker_multiattack_count(actor, c)
        ok, reason = combat.check_action_attack(
            is_current=True,
            attacks_made=c.combat.action_attacks_made,
            extra_attacks=getattr(actor, "extra_attacks", 0),
            surge_actions=c.combat.surge_actions,
            multiattack=ma,
        )
        if not ok:
            raise ValueError(f"{actor.name} cannot {verb}: {reason}")
        c.combat.action_attacks_made += 1
        c.combat.action_used = True  # the Attack action is now declared/used this turn
    elif consumes == "action":
        # An action verb (escape/stabilize) is mutually exclusive with the Attack action —
        # you have ONE action. If it's already spent (an attack made, or another action verb),
        # reject. On success, mark action_used AND exhaust the attack budget so a later
        # attack() this turn is rejected by attack()'s existing economy gate (one action gone).
        if c.combat.action_used or c.combat.action_attacks_made > 0:
            raise ValueError(
                f"{actor.name} has already used its action this turn and cannot {verb} "
                f"(it is an Action). Advance with next_turn for a fresh turn."
            )
        c.combat.action_used = True
        c.combat.action_attacks_made = combat.attacks_allowed(
            getattr(actor, "extra_attacks", 0), c.combat.surge_actions
        )


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
        c.last_combat_resolution = ""  # a fresh fight -> any prior disposition no longer applies
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
        # Tier 2 — engaged: the party has joined battle with each monster type in the fight.
        # Bumped per combatant that is a bestiary monster (has a creature_slug); monotonic max.
        for cid in combatant_ids:
            ch = c.characters.get(cid)
            if ch is not None and getattr(ch, "kind", "") == "monster":
                _bump_intel(c, getattr(ch, "creature_slug", ""), 2)
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
        # F06-8 (audit 2026-06-11): companion combat PARTICIPATION advisory. start_combat builds
        # the order STRICTLY from the passed ids — a co-located companion the DM forgot to include
        # was silently sidelined, with no engine signal (the audit's "companion combat
        # participation unenforced + unobserved"). Surface a `companions_omitted` advisory — the
        # same engine-tells pattern as extra_attack_reminder / outlook — listing every LIVING
        # companion (in c.party OR a de-facto companion, the #353/#739 rule) that is co-located
        # with the party yet absent from this fight, so the DM pulls them in or narrates why they
        # sit out. NEVER auto-adds a combatant (the DM is the authority on who joins). Purely
        # additive — absent when every companion is already in (today's view byte-for-byte).
        combatant_set = set(combatant_ids)
        cur_loc = c.current_location_id
        omitted = [
            {"id": ch.id, "name": ch.name}
            for ch in c.characters.values()
            if ch.kind == "companion"
            and not ch.dead
            and ch.current_hp > 0
            and ch.id not in combatant_set
            # co-located with the party (or unplaced) — a companion off elsewhere isn't "omitted".
            and (cur_loc is None or ch.location_id in (cur_loc, None))
        ]
        if omitted:
            view["companions_omitted"] = omitted
            view["companions_omitted_note"] = (
                "these living companions are with the party but NOT in this fight — add them to "
                "the combatants (re-call start_combat or have them act) or narrate why they hold "
                "back; the engine never auto-adds a combatant."
            )
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
def place_combatant(campaign_id: str, combatant_id: str = "", zone: str = "",
                    character_id: str = "", id: str = "") -> dict:
    """Place a combatant directly into a tactical `zone` (S2.7) — the initial setup
    move, with NO opportunity-attack check (use move_to_zone for in-combat movement
    that may provoke). The combatant must be in the initiative order. `zone` should
    name a declared zone (set_zones); an unknown name is accepted but flagged in
    `warnings` so a typo doesn't silently strand a fighter. Returns the combat view
    (now carrying each placed combatant's `zone`).

    Identify the combatant via ``combatant_id`` (canonical) or the aliases ``character_id`` /
    ``id`` — ``combatant_id`` wins if more than one is given."""
    combatant_id = combatant_id or character_id or id  # accept the id the DM reaches for
    if not combatant_id:
        raise ValueError("place_combatant needs a combatant (pass `combatant_id` or an alias: `character_id`/`id`)")
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
def move_to_zone(campaign_id: str, combatant_id: str = "", zone: str = "",
                 character_id: str = "", id: str = "") -> dict:
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
    plus `from`, `to`, `opportunity_attack`, and `provokers`.

    Identify the combatant via ``combatant_id`` (canonical) or the aliases ``character_id`` /
    ``id`` — ``combatant_id`` wins if more than one is given."""
    combatant_id = combatant_id or character_id or id  # accept the id the DM reaches for
    if not combatant_id:
        raise ValueError("move_to_zone needs a combatant (pass `combatant_id` or an alias: `character_id`/`id`)")
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
        # F01-8: a DISENGAGED mover (it took the Disengage action this turn) provokes NO
        # opportunity attacks; and a creature can never provoke an OA from the grappler that
        # is holding it (mover.grappled_by) — the grappler would have to release to follow.
        provokers: list[dict] = []
        disengaged = bool(getattr(cb, "disengaged", False))
        grappler_id = getattr(mover, "grappled_by", None) if mover is not None else None
        if from_zone and zone != from_zone and mover is not None and not disengaged:
            ally_kinds = {"player", "companion"}
            mover_ally = mover.kind in ally_kinds
            for other_cb in c.combat.order:
                if other_cb.character_id == combatant_id or other_cb.zone != from_zone:
                    continue
                if other_cb.character_id == grappler_id:
                    continue  # the grappler can't OA the creature it holds
                other = c.characters.get(other_cb.character_id)
                if other is None or other.dead:
                    continue
                other_ally = other.kind in ally_kinds
                if other_ally != mover_ally:  # opposing side -> it can take an OA
                    provokers.append({"id": other.id, "name": other.name})

        # F01-8: a creature whose movement is reduced to 0 — Grappled or Restrained — CANNOT
        # use movement to change zones. Advisory-doctrine-preserving (NEVER blocks; the DM may
        # have a special movement or the creature may break free first): we flag the move as
        # `movement_illegal` so the DM is told the Speed-0 condition was bypassed, and STILL
        # move so a deliberate ruling isn't blocked. Only meaningful on an actual zone change.
        movement_illegal: dict | None = None
        if mover is not None and from_zone and zone != from_zone:
            speed_zero = [
                cn.value for cn in (Condition.GRAPPLED, Condition.RESTRAINED)
                if cn in mover.conditions
            ]
            if speed_zero:
                movement_illegal = {
                    "mover": mover.name,
                    "conditions": speed_zero,
                    "note": (
                        f"{mover.name} is {', '.join(speed_zero)} (Speed 0) and cannot normally "
                        "move between zones — escape the grapple / end the restraint first "
                        "(escape_grapple), or rule a special movement. Moved anyway (advisory)."
                    ),
                }

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
                "disengaged": disengaged,
                "movement_illegal": movement_illegal,
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
    if disengaged:
        view["disengaged"] = True  # F01-8: this move drew no OAs (Disengage action)
    if movement_illegal is not None:
        view["movement_illegal"] = movement_illegal
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
            # Carry the Multiattack composition (#211) to the per-turn brief so the DM
            # issues the SPECIFIC attacks at the turn trigger (the surface it actually
            # reads each turn), not just the count. Absent when no composition resolved.
            if "multiattack" in entry:
                brief["attack"]["multiattack"] = entry["multiattack"]
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
            for cue in ("finesse", "sneak_attack"):  # F01-4/F01-5: carry the cues per-turn
                if cue in nums:
                    brief["attack"][cue] = nums[cue]
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
        # F01-4/F01-5 (#774/#166): the per-turn brief is the surface the DM actually reads
        # each turn — carry the finesse and Sneak Attack cues here too, not just on the sheet.
        for cue in ("finesse", "sneak_attack"):
            if cue in nums:
                brief["attack"][cue] = nums[cue]
        brief["note"] = (
            f"Declare use_action(kind='action') then make {attacks_per_action} attack call(s) "
            "using the sheet bonuses above — never invent or copy another combatant's."
        )
    # --- limited resources (class_resources with remaining > 0) ---
    # Each surfaced resource gets a tactical `suggested_when` trigger (#A3): turn_brief
    # already listed what's LEFT, but a flat {remaining:1} gave the DM no reason to spend it,
    # so nova features (Second Wind / Action Surge / Channel Divinity) ended every sprint at
    # full charge. The trigger names the moment to use it. Context: HP fraction + round.
    hp_frac = (ch.current_hp / ch.max_hp) if ch.max_hp else 1.0
    rnd = c.combat.round if c.combat.active else 0
    resources: dict = {}
    for rid, res in ch.class_resources.items():
        remaining = res.max - res.used
        if remaining > 0:
            entry_r = {
                "remaining": remaining,
                "max": res.max,
                "label": f"{remaining}/{res.max}" + (f" {res.size}" if res.size else ""),
            }
            rid_l = rid.lower()
            if rid_l == "second_wind" and hp_frac < 0.75:
                entry_r["suggested_when"] = (
                    f"{ch.name} is at {ch.current_hp}/{ch.max_hp} HP — Second Wind (a BONUS "
                    "action, 1d10+level, no spell slot) is available right now.")
            elif rid_l == "action_surge" and rnd >= 2:
                entry_r["suggested_when"] = (
                    "Action Surge grants a full EXTRA action this turn — use it to finish a "
                    "bloodied foe or land a second Attack action.")
            elif rid_l == "channel_divinity":
                entry_r["suggested_when"] = (
                    "Channel Divinity is available (e.g. War Domain Guided Strike: +10 to one "
                    "attack roll) — spend it to turn a key miss into a hit.")
            resources[rid] = entry_r
    brief["resources"] = resources
    # --- reactions (#A2): surface a monster's stat-block reaction (Parry) so the DM knows
    # it's on the table this round even when the engine won't auto-spend it (it spends Parry
    # only when it would flip a hit). Keeps the reaction visible/narratable. PCs/companions
    # track reactions through their own resources/economy. ---
    if ch.kind == "monster" and getattr(ch, "parry", 0) > 0:
        brief["reactions"] = {
            "parry": {
                "ac_bonus": ch.parry,
                "note": (f"{ch.name} has Parry (+{ch.parry} AC vs one melee hit it can see) as "
                         "its reaction — narrate it turning a blow aside when one lands."),
            }
        }
    # --- spell slots with at least 1 remaining ---
    slots: dict = {}
    for lvl, slot in ch.spell_slots.items():
        rem = slot.maximum - slot.used
        if rem > 0:
            slots[str(lvl)] = rem
    if slots:
        brief["spell_slots"] = slots
    # --- concentration cue (#E1): if this combatant is still holding a concentration
    # spell, surface it on the proven channel so a DM who narrates it lapsing actually
    # calls drop_concentration instead of narrate-and-forget (which desyncs state into
    # the next session). Absent when not concentrating (byte-identical to before). ---
    if getattr(ch, "concentration", None):
        brief["concentrating"] = {
            "spell": ch.concentration,
            "note": (f"{ch.name} is still concentrating on {ch.concentration} — if you "
                     "narrate it lapsing or being broken (without a damage save), call "
                     "drop_concentration so the engine state matches the fiction."),
        }
    return brief


@mcp.tool()
def next_turn(campaign_id: str) -> dict:
    """Advance to the next LIVING combatant's turn (round increments on wrap;
    dead or removed combatants are skipped). Returns whose turn it is, whether
    they owe a death save (downed and unstable), and a ``turn_brief`` with their
    authoritative attack line + available limited resources so the DM never drifts
    from the sheet numbers mid-combat (#166).

    Also self-enforces SAVE-ENDS effects (#209): the OUTGOING combatant (whose turn
    just ended) automatically rolls any repeat-save it carries (Hold Person → a WIS
    save to shake off paralysis), reported in ``repeat_saves`` — a success frees it
    (clearing the condition + the caster's concentration); a failure keeps the lock.
    A paralyzed target whose caster loses concentration is freed here too (the inverse
    link), surfaced in ``expired_effects``. So nothing stays locked because the DM
    forgot to prompt the save."""
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
        # --- End-of-turn REPEAT SAVES (#209): engine-rolls-and-tells ----------------
        # A save-ends effect (Hold Person → paralyzed: "the target repeats the save at
        # the END of each of its turns, ending the effect on a success") must get that
        # save automatically, or its victim stays locked forever when the DM forgets to
        # prompt it. So for the OUTGOING combatant (whose turn is ending — same anchor as
        # the PC-skip guard above), roll each repeat-save-bearing active_effect's save via
        # the shared resolver and free them on a success. The engine resolves it; results
        # are surfaced in the return so the DM narrates the escape (or the continued hold).
        repeat_save_results: list[dict] = []
        if previous is not None:
            # Snapshot the list — end_repeat_save_effect mutates previous.active_effects.
            for eff in list(previous.active_effects):
                rs = eff.repeat_save
                if rs is None:
                    continue
                auto_fail, disadvantage = combat.save_modifiers(previous, rs.ability)
                r = dice_mod.roll(
                    f"1d20+{previous.saving_throw_bonus(rs.ability)}",
                    disadvantage=disadvantage,
                )
                # NUMERIC RIDERS (SYN-06 / #780): the end-of-turn repeat save is a saving
                # throw — fold the holder's engine-tracked save bonus dice (Bless/Bane).
                rs_rider_bonus, rs_rider_rolls = _roll_effect_bonus_dice(
                    previous, "save_bonus_dice"
                )
                rs_total = r.total + rs_rider_bonus
                success = (not auto_fail) and rs_total >= rs.dc
                entry = {
                    "character_id": previous.id,
                    "name": eff.name,
                    "ability": rs.ability.value,
                    "dc": rs.dc,
                    "roll": rs_total,
                    "natural": r.natural,
                    "success": success,
                    "ended": False,
                }
                if rs_rider_rolls:
                    entry["bonus_dice"] = rs_rider_rolls
                if auto_fail:
                    forcing = ", ".join(
                        cn.value for cn in previous.conditions if cn in combat.SAVE_AUTOFAIL
                    )
                    entry["reason"] = (
                        f"condition auto-fail: {previous.name} is {forcing} — "
                        f"STR/DEX saves automatically fail"
                    )
                if disadvantage:
                    entry["disadvantage"] = True
                if success and rs.ends_effect:
                    ended_condition = eff.imposes_condition
                    # End the CASTER's concentration twin if this effect came from a
                    # concentration spell (the twin lives on the caster, not on this
                    # target-side marker). One source of truth: the spell is over, so the
                    # caster's concentration field + its flagged effect both clear.
                    caster = c.characters.get(eff.source_id) if eff.source_id else None
                    if (
                        caster is not None
                        and caster is not previous
                        and caster.concentration == eff.name
                    ):
                        caster.concentration = None
                        combat.expire_concentration_effects(caster)
                        entry["concentration_ended_for"] = caster.id
                    # Remove the effect from the target + clear the condition it imposed.
                    combat.end_repeat_save_effect(previous, eff)
                    entry["ended"] = True
                    if ended_condition is not None:
                        entry["cleared_condition"] = ended_condition.value
                repeat_save_results.append(entry)
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
                    cb.disengaged = False  # F01-8: Disengage is per-turn — clear at turn start
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
        # CONCENTRATION-LINK reconciliation (#209, the inverse direction): a repeat-save
        # marker (Hold Person → paralyzed) is the TARGET-side twin of the CASTER's
        # concentration. If that concentration has ended for ANY reason — its duration just
        # ticked out above, or it broke earlier (failed save / incapacitation / 0 HP / a
        # recast) — the spell is over, so the target must be freed here rather than staying
        # paralyzed indefinitely. Sweep every combatant's markers: when the source caster no
        # longer concentrates on that spell, drop the marker + clear the condition it imposed.
        # SYN-06 (#780) extends the same sweep to concentration-linked NUMERIC-rider children
        # (Bless on an ally lives target-side as a linked child) — before that, the naive
        # caster-side expiry provably never reached them and the buff outlived the spell.
        for cb in order:
            holder = c.characters.get(cb.character_id)
            if holder is None:
                continue
            for eff in list(holder.active_effects):
                if eff.repeat_save is None and not eff.linked_to_concentration:
                    continue
                if not eff.source_id:
                    continue
                caster = c.characters.get(eff.source_id)
                # Marker is orphaned when the caster is gone, or no longer concentrating on
                # this spell (concentration twin broken/expired). A non-concentration save-ends
                # source (caster never concentrated) is left alone — nothing ties it to a twin.
                caster_concentrating = caster is not None and caster.concentration == eff.name
                if caster is not None and not caster_concentrating:
                    combat.end_repeat_save_effect(holder, eff)
                    expired.append({"character_id": holder.id, "name": eff.name})
        # --- AUTO-ROLL the dying PC's death save at the START of its turn (F01-10, audit
        # 2026-06-11) ----------------------------------------------------------------------
        # A downed PC/companion (0 HP, not dead, not stable) rolls a death save at the start
        # of EACH of its turns (SRD 2024). `death_save_due` only SURFACED that — the
        # DM-initiated roll_death_save was skippable, so the dying clock could silently stop
        # forever (QA). ENGINE ROLLS, DM IS TOLD: roll it here via the same dice-free resolver
        # the manual tool uses, ordered BEFORE turn_brief so the brief reflects a nat-20
        # self-revive. Monsters die outright at 0 HP (no death saves) — only PCs/companions
        # are clocked. The manual roll_death_save tool stays as an explicit override. A nat 20
        # restores 1 HP; this turn then proceeds normally (the PC-skip guard already exempts a
        # combatant that was downed at turn start via current_hp>0 / is_incapacitated).
        death_save_auto = None
        if (
            cur is not None
            and cur.kind in ("player", "companion")
            and cur.current_hp == 0
            and not cur.dead
            and not getattr(cur, "stable", False)
        ):
            roll = dice_mod.roll("1d20")
            ds_out = combat.resolve_death_save(cur, roll)
            death_save_auto = {
                "character_id": cur.id,
                "name": cur.name,
                "roll": roll.natural,
                "result": ds_out.get("result"),
                "successes": cur.death_saves.successes,
                "failures": cur.death_saves.failures,
                "note": (
                    f"{cur.name} is dying — the engine rolled their death save at turn start "
                    f"(natural {roll.natural} → {ds_out.get('result')}). Narrate the result; "
                    "no manual roll_death_save needed."
                ),
            }
            _log_combat_event(
                c,
                f"{cur.name} rolls an automatic death save: {ds_out.get('result')}.",
                {
                    "event": "death_save",
                    "auto": True,
                    "target": _combatant_ref(cur),
                    "roll": {"total": roll.total, "natural": roll.natural, "detail": roll.detail},
                    "result": ds_out.get("result"),
                    "successes": cur.death_saves.successes,
                    "failures": cur.death_saves.failures,
                },
                speaker=cur.name,
            )
        view = _combat_view(c)
        view["current_name"] = cur.name if cur else None
        view["death_save_due"] = bool(cur and cur.current_hp == 0 and not cur.dead and not cur.stable)
        if death_save_auto is not None:
            view["death_saves_rolled"] = death_save_auto
        view["expired_effects"] = expired
        # End-of-turn repeat saves the engine just rolled for the OUTGOING combatant (#209):
        # each carries success + whether the effect/condition ended, so the DM narrates the
        # escape ("the paralysis loosens its grip") or the continued hold. Empty == none owed.
        if repeat_save_results:
            view["repeat_saves"] = repeat_save_results
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
                "repeat_saves": repeat_save_results,
            },
            speaker=cur.name if cur else "",
        )
        save_campaign(c)
        return view


@mcp.tool()
def use_action(campaign_id: str, character_id: str, kind: str = "action") -> dict:
    """Track a combatant's action economy. kind: action | bonus | reaction | free | skip
    | disengage.
    `action`/`bonus` are legal only on the creature's OWN turn and only once each
    per turn; `reaction` is legal any time but once per round (it refreshes at the
    start of the creature's turn via next_turn); `free`/movement isn't rate-limited.
    `skip` (a.k.a. pass) declares an intentional do-nothing turn for the current
    combatant — sets action_used so next_turn's PC-skip guard is satisfied without
    actually attacking or casting. Use it when a PC Dodges, Dashes, Readies, or simply
    passes their turn.
    `disengage` (F01-8) is the 5e DISENGAGE ACTION: it spends the current combatant's
    action (like skip) AND sets a per-turn `disengaged` flag so a following move_to_zone
    provokes NO opportunity attacks — the engine then suppresses every provoker until the
    creature's turn ends (the flag resets at the start of its next turn). Use it instead of
    skip when a PC steps away from a foe without drawing an attack.
    Returns {ok, reason, action_available, bonus_available, reaction_available, disengaged}
    so you can flag an illegal double-action. NOTE: multiattack (Extra Attack) is ONE
    action — declare a single `action`, then make several attack() calls under it."""
    kind = kind.lower()
    if kind not in ("action", "bonus", "reaction", "free", "movement", "skip", "disengage"):
        raise ValueError("kind must be action | bonus | reaction | free | skip | disengage")
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
        if combat.is_incapacitated(ch) and kind in ("action", "bonus", "reaction", "disengage"):
            ok, reason = False, (
                f"{ch.name} is incapacitated ("
                f"{', '.join(c.value for c in ch.conditions if c in combat.INCAPACITATING)}) "
                f"and can't take an action, bonus action, or reaction"
            )
        elif kind == "disengage":
            # F01-8: the Disengage ACTION — spend the action (own turn only, once) AND set the
            # per-turn disengaged flag so a subsequent move_to_zone provokes no OAs.
            if not is_current:
                ok, reason = False, f"it is not {ch.name}'s turn — disengage must be declared on your own turn"
            elif c.combat.action_used:
                ok, reason = False, "action already used this turn (disengage is an action)"
            else:
                c.combat.action_used = True
                combatant.disengaged = True
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
            "disengaged": combatant.disengaged,
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
def add_combatant(campaign_id: str, character_id: str = "", initiative: Optional[int] = None,
                  id: str = "") -> dict:
    """Add a combatant to a RUNNING fight — mid-combat reinforcements (a second wave, a
    summoned ally, a guard who heard the noise). F01-12 (audit 2026-06-11): before this,
    start_combat REFUSED to run while a combat was active and there was no other path into
    the order, so a mid-fight spawn either (a) never joined initiative, or (b) attacked with
    EVERY combat gate bypassed (attack()'s economy gates only engage for combatants in the
    order). This is the missing verb.

    The ENGINE ROLLS the newcomer's initiative (1d20 + their initiative_bonus) and inserts
    them into the order at their initiative position (desc, ties broken by DEX then by going
    after existing same-count combatants). The current combatant's turn is preserved (the
    pointer is adjusted exactly like remove_combatant's index math). Pass an explicit
    ``initiative`` to override the roll (a readied ambush, a fixed slot). Once added, the
    reinforcement is subject to the SAME turn-order/action-economy gates as everyone else.

    Identify the character via ``character_id`` (canonical) or the alias ``id``. Raises if no
    combat is active or the character is already in the order. No model change — additive."""
    character_id = character_id or id
    if not character_id:
        raise ValueError("add_combatant needs a character (pass `character_id` or its alias `id`)")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if not c.combat.active:
            raise ValueError("no active combat — call start_combat to begin a fight")
        ch = _char(c, character_id)
        order = c.combat.order
        if any(cb.character_id == character_id for cb in order):
            raise ValueError(f"{ch.name} is already in the combat order")
        if initiative is None:
            roll = dice_mod.roll(f"1d20+{ch.initiative_bonus}")
            init_total = roll.total
            natural = roll.natural
        else:
            init_total = int(initiative)
            natural = None
        dex_mod = ch.ability_modifier(Ability.DEX)
        # Insertion position: keep the order sorted desc by initiative, ties broken by DEX
        # modifier; among an exact (initiative, dex) tie the newcomer slots AFTER existing
        # combatants (stable — they were rolled first). Find the first slot the newcomer
        # outranks; insert there (or at the end if it outranks nobody).
        insert_at = len(order)
        for i, cb in enumerate(order):
            other = c.characters.get(cb.character_id)
            other_dex = other.ability_modifier(Ability.DEX) if other is not None else 0
            if (init_total, dex_mod) > (cb.initiative, other_dex):
                insert_at = i
                break
        order.insert(insert_at, Combatant(character_id=character_id, initiative=init_total))
        # Preserve whose turn it is: an insertion at or before the live pointer shifts the
        # current combatant one slot to the right, so bump turn_index to track them (mirrors
        # remove_combatant's `idx < turn_index` adjustment, inverted for an insert).
        if insert_at <= c.combat.turn_index:
            c.combat.turn_index += 1
        c.combat.turn_index %= len(order)
        # Tier 2 — engaged: a bestiary monster joining the fight bumps its intel (parity with
        # start_combat).
        if getattr(ch, "kind", "") == "monster":
            _bump_intel(c, getattr(ch, "creature_slug", ""), 2)
        _log_combat_event(
            c,
            f"{ch.name} joins the fight (initiative {init_total}).",
            {
                "event": "combatant_added",
                "combatant": _combatant_ref(ch),
                "initiative": init_total,
                "natural": natural,
                "position": insert_at,
            },
            speaker=ch.name,
        )
        save_campaign(c)
        view = _combat_view(c)
    view["added"] = {"id": character_id, "name": ch.name, "initiative": init_total}
    # Surface a reinforcement's authoritative combat numbers + legendary surface (F01-13) so
    # the DM runs it correctly the moment it joins — same data start_combat provides.
    entry = _monster_combat_entry(ch, c)
    if entry is not None:
        view["monster_combat"] = [entry]
    return view


def _roll_effect_bonus_dice(ch: Character, field: str) -> tuple[int, list[dict]]:
    """Roll the numeric-rider bonus dice carried by ``ch``'s active effects (SYN-06 / #780:
    Bless +1d4 / Bane -1d4) for the given field ('attack_bonus_dice' or 'save_bonus_dice').
    THE ENGINE ROLLS — each component is rolled here and surfaced so the DM narrates the
    d4 instead of being told a buff exists and then watching the engine ignore it. A
    leading '-' subtracts the rolled amount (Bane). Returns (signed_total, components);
    (0, []) for the overwhelmingly common no-rider case — byte-identical behavior."""
    total = 0
    rolls: list[dict] = []
    for eff in ch.active_effects or []:
        expr = (getattr(eff, field, "") or "").strip()
        if not expr:
            continue
        sign = -1 if expr.startswith("-") else 1
        r = dice_mod.roll(expr.lstrip("+-"))
        signed = sign * r.total
        total += signed
        rolls.append({"source": eff.name, "dice": expr, "rolled": signed, "detail": r.detail})
    return total, rolls


def _auto_concentration_save(ch: Character, dc: int) -> dict | None:
    """F01-9 (audit 2026-06-11): when a concentrating creature TAKES damage, 5e checks
    concentration the instant the damage lands. The engine already computes the DC
    (combat._apply_total_to_hp: max(10, damage_taken//2)) and surfaced it as a CUE — but a
    cue is ignorable, and QA confirmed the DM routinely never called concentration_save, so
    the spell hung on indefinitely. ENGINE ROLLS, DM IS TOLD: roll the CON save here (mirrors
    the manual concentration_save tool, including SYN-06 Bless/Bane riders), break
    concentration on a failure, and return the result so the damage tool surfaces it. The
    manual concentration_save tool stays as an explicit override. ``dc`` is the value
    _apply_total_to_hp returned (None/0 == the target wasn't concentrating → no-op, returns
    None). combat.py stays dice-free; all dice are rolled here. Caller persists (sole-writer)."""
    if not dc or not getattr(ch, "concentration", None):
        return None
    r = dice_mod.roll(f"1d20+{ch.saving_throw_bonus(Ability.CON)}")
    rider_bonus, rider_rolls = _roll_effect_bonus_dice(ch, "save_bonus_dice")
    total = r.total + rider_bonus
    maintained = total >= dc
    held = ch.concentration
    expired: list[str] = []
    if not maintained:
        ch.concentration = None
        expired = combat.expire_concentration_effects(ch)
    out = {
        "target": ch.name,
        "rolled": True,
        "ability": "con",
        "dc": dc,
        "roll": total,
        "natural": r.natural,
        "maintained": maintained,
        "spell": held,
        "concentration": ch.concentration,
        "expired_effects": expired,
        "note": (
            f"{ch.name} maintained concentration on {held} (rolled {total} vs DC {dc})."
            if maintained else
            f"{ch.name} LOST concentration on {held} (rolled {total} vs DC {dc}) — the spell ends."
        ),
    }
    if rider_rolls:
        out["bonus_dice"] = rider_rolls
    return out


def _effective_armor_class(ch: Character) -> tuple[int, dict | None]:
    """Return the AC the attack resolver should use, including engine-tracked buffs:
    Mage Armor's set-AC formula (the original special case) plus any additive ``ac_bonus``
    riders (SYN-06 / #780 — Shield of Faith +2, Shield +5). Bonuses stack on top of
    whichever base/formula AC wins, and each contribution is itemized in the detail dict
    (``ac_bonuses``) so the DM sees WHY the AC moved."""
    base_ac = int(ch.armor_class or 10)
    mage_armor = next(
        (
            eff
            for eff in (ch.active_effects or [])
            if (getattr(eff, "name", "") or "").lower() == "mage armor"
        ),
        None,
    )
    if mage_armor is None:
        ac, detail = base_ac, None
    else:
        dex_mod = ch.ability_modifier(Ability.DEX)
        mage_ac = mage_armor.armor_formula_ac or (13 + dex_mod)
        stored_base_ac = mage_armor.armor_base_ac or base_ac
        if mage_ac <= base_ac:
            ac, detail = base_ac, {
                "source": "Mage Armor",
                "base_ac": stored_base_ac,
                "formula_ac": mage_ac,
                "dex_modifier": dex_mod,
                "applied": False,
            }
        else:
            ac, detail = mage_ac, {
                "source": "Mage Armor",
                "base_ac": stored_base_ac,
                "formula_ac": mage_ac,
                "dex_modifier": dex_mod,
                "applied": True,
            }
    # Additive AC riders (SYN-06): sum every active effect's ac_bonus (Shield of Faith +2,
    # Shield +5) on top. No riders == today's return exactly (incl. detail None).
    bonus_effects = [
        eff for eff in (ch.active_effects or [])
        if int(getattr(eff, "ac_bonus", 0) or 0) != 0
    ]
    if bonus_effects:
        ac += sum(int(eff.ac_bonus) for eff in bonus_effects)
        detail = dict(detail) if detail is not None else {}
        detail["ac_bonuses"] = [
            {"source": eff.name, "bonus": int(eff.ac_bonus)} for eff in bonus_effects
        ]
    return ac, detail


@mcp.tool()
def attack(
    campaign_id: str,
    attacker_id: str = "",
    target_id: str = "",
    attack_bonus: int = 0,
    damage_dice: str = "",
    damage_type: str = "",
    advantage: bool = False,
    disadvantage: bool = False,
    is_ranged: bool = False,
    is_reaction: bool = False,
    damage_rolls: list[dict] | None = None,
    character_id: str = "",
    npc_id: str = "",
    id: str = "",
) -> dict:
    """Resolve an attack. The DM supplies attack_bonus and damage_dice (e.g.
    '1d8+3'); the engine rolls 1d20+bonus vs the target's AC, auto-hits on a
    natural 20 and auto-misses on a natural 1, doubles damage dice on a crit, and
    applies the damage. Condition-based advantage/disadvantage is detected (set
    is_ranged=True so a prone target gives disadvantage rather than advantage) and
    combined with the explicit flags (they cancel if both apply).

    MULTI-COMPONENT DAMAGE (#210): an attack that deals more than one damage type in
    a single strike — e.g. a Ghoul Bite (1d6+2 piercing PLUS 1d6 necrotic) — passes
    ``damage_rolls=[{"dice": "1d6+2", "type": "piercing"}, {"dice": "1d6", "type":
    "necrotic"}]``. EACH component is rolled, crit-doubles its OWN dice, and has the
    target's resistance/immunity/vulnerability applied for ITS OWN type before the
    components are summed and applied as one hit. The per-component breakdown is
    surfaced under result["damage"]["components"]. When ``damage_rolls`` is given it
    supersedes ``damage_dice``/``damage_type``; omit it (the default) and the single
    ``damage_dice``/``damage_type`` path is byte-identical to before. The monster_combat
    / turn_brief surfaces emit ready-to-pass ``damage_rolls`` for multi-type attacks.

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
    # Coalesce intuitive arg-name aliases to the canonical ids. The ATTACKER is the acting
    # character (alias `character_id`); the TARGET is the thing struck (aliases `npc_id`/`id`).
    # Canonical names win. (target_id ⇄ character_id is intentionally NOT done — `character_id`
    # is the attacker alias here, so it would be ambiguous.)
    attacker_id = attacker_id or character_id
    target_id = target_id or npc_id or id
    if not attacker_id:
        raise ValueError("attack needs an attacker (pass `attacker_id` or its alias `character_id`)")
    if not target_id:
        raise ValueError("attack needs a target (pass `target_id` or an alias: `npc_id`/`id`)")
    # Damage spec: exactly one of damage_dice / damage_rolls. ``damage_dice`` became
    # optional (default "") so a multi-component caller can omit it, but a hit needs
    # SOME damage to roll — reject a spec-less call up front with a clear message rather
    # than failing deep in the dice roller on a hit (and only on a hit). No state change.
    if not damage_dice and not damage_rolls:
        raise ValueError(
            "attack needs damage: pass damage_dice (e.g. '1d8+3') for a single type, or "
            "damage_rolls=[{'dice','type'}, ...] for a multi-type attack (#210)."
        )
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
        # Capture any advantage-granting rider on the target (Guiding Bolt's "next attack
        # has advantage" marker) BEFORE the roll so we can both auto-apply its advantage
        # (via attack_modifiers) and consume it after this one attack resolves (#194).
        adv_marker = combat.advantage_granting_effect(target)
        # Capture + CLEAR any pending damage-maneuver bonus (Battle Master Trip/Menacing,
        # #213) the attacker declared via use_resource(superiority_dice, maneuver=…). The
        # superiority die was rolled at spend time; THIS attack is the one strike that
        # consumes it. Consume it whatever the outcome (the die is already spent — it can't
        # carry to a later swing), but fold the rolled damage into the strike only on a HIT
        # (a miss does no damage). None == no maneuver declared (the default path). Mirrors
        # the adv_marker / on-hit-rider consume-once discipline so it can't double-apply.
        man_bonus = attacker.pending_damage_bonus
        if man_bonus is not None:
            attacker.pending_damage_bonus = None
        cadv, cdis = combat.attack_modifiers(attacker, target, is_ranged=is_ranged)
        adv = advantage or cadv
        dis = disadvantage or cdis
        atk = dice_mod.roll(f"1d20+{attack_bonus}", advantage=adv, disadvantage=dis)
        # NUMERIC RIDERS (SYN-06 / #780): fold the attacker's engine-tracked bonus dice
        # (Bless +1d4 / Bane -1d4) into the attack total — the engine ROLLS the rider it
        # advertises instead of tracking it as theater. Nat-20 auto-hit / nat-1 auto-miss
        # still read the natural die; no riders == atk.total exactly as before.
        rider_bonus, rider_rolls = _roll_effect_bonus_dice(attacker, "attack_bonus_dice")
        atk_total = atk.total + rider_bonus
        target_ac, target_ac_detail = _effective_armor_class(target)
        hit = atk.crit or (not atk.fumble and atk_total >= target_ac)
        # PARRY (#218): a defender with an available defensive reaction (+N AC vs one melee
        # attack it can see) turns the blow aside — but ONLY when doing so FLIPS this hit to a
        # miss. The engine never wastes the reaction on a crit it can't stop or a blow that
        # lands anyway; this is the "enforce in the engine, don't rely on the DM" discipline of
        # #209/#213, closing the recurring "monsters never use their reaction" sprint defect. A
        # nat-20 crit can't be parried (a crit always hits); ranged attacks and an incapacitated
        # or blinded defender can't react. Only meaningful in active combat (reaction economy).
        parry_info = None
        if (
            hit and not atk.crit and not is_ranged and target.parry > 0
            and c.combat.active
            and not combat.is_incapacitated(target)
            and Condition.BLINDED not in target.conditions
            and atk_total < target_ac + target.parry
        ):
            target_cb = next(
                (cb for cb in c.combat.order if cb.character_id == target_id), None
            )
            if target_cb is not None and not target_cb.reaction_used:
                hit = False
                target_cb.reaction_used = True
                eff_ac = target_ac + target.parry
                parry_info = {
                    "defender": target.name,
                    "ac_bonus": target.parry,
                    "effective_ac": eff_ac,
                    "note": (
                        f"{target.name} spends its reaction to Parry — AC rises to {eff_ac}, "
                        f"and the blow ({atk_total}) turns aside"
                    ),
                }
        # SRD: a melee hit against an unconscious/paralyzed creature auto-crits.
        is_crit = atk.crit or (hit and combat.melee_auto_crit(target, is_ranged))
        # WHY it critted, so the DM narrates the right reason (#219): a nat 20, an expanded
        # crit range, or the auto-crit vs a helpless target — NOT "nat 20" on every crit.
        crit_why = combat.crit_source(atk.crit, atk.natural, is_crit, target)
        result = {
            "attacker": attacker.name,
            "target": target.name,
            "attack_roll": {"total": atk_total, "natural": atk.natural, "detail": atk.detail},
            "advantage": adv,
            "disadvantage": dis,
            "crit": is_crit,
            "crit_source": crit_why,
            "parry": parry_info,
            "hit": hit,
            "target_ac": target_ac,
            "target_base_ac": target.armor_class,
            "damage": None,
        }
        if rider_rolls:
            # The engine-rolled rider components (SYN-06), itemized so the DM narrates
            # "the blessing guides the blade (+3)" — and sees the buff actually counted.
            result["attack_roll"]["bonus_dice"] = rider_rolls
        if target_ac_detail is not None:
            result["target_ac_detail"] = target_ac_detail
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
        # F3-6: capture the target's concentration BEFORE damage may down it (combat.apply_damage*
        # clears it but is Character-pure and can't free the victim) — see the release after.
        was_conc_target = target.concentration
        if hit:
            # A pending DAMAGE-maneuver bonus (#213) folds the already-rolled superiority die
            # into THIS strike as one extra typed component. The die is NOT re-rolled and NOT
            # crit-doubled (5e: only weapon dice double; the maneuver die is added once),
            # which is exactly what apply_damage_components does — it takes pre-rolled amounts.
            # Defaults to the weapon's damage_type when the maneuver didn't specify one, so the
            # bonus shares the strike's resistance treatment unless typed otherwise.
            man_part = None
            if man_bonus is not None and man_bonus.amount > 0:
                man_part = {
                    "amount": man_bonus.amount,
                    "type": man_bonus.damage_type or damage_type,
                }
            if damage_rolls or man_part is not None:
                # MULTI-COMPONENT (#210): roll + crit-double EACH component on its own
                # dice, then apply per-type resistance/immunity/vulnerability per
                # component before summing. The pre-adjustment roll total per component
                # is surfaced so the DM sees what each type contributed; the post-
                # resistance figure lives in target_state["components"]. A single-type
                # strike carrying a maneuver bonus joins this path too (its one weapon
                # component + the maneuver component), so the bonus lands as ONE hit.
                comp_results: list[dict] = []
                parts_for_apply: list[dict] = []
                base_specs = damage_rolls or (
                    [{"dice": damage_dice, "type": damage_type}] if damage_dice else []
                )
                for spec in base_specs:
                    cd = str(spec.get("dice", "") or "")
                    ct = str(spec.get("type", "") or "")
                    if not cd:
                        continue
                    cexpr = combat.double_dice(cd) if is_crit else cd
                    cdmg = dice_mod.roll(cexpr)
                    ctotal = max(0, cdmg.total)
                    comp_results.append(
                        {"type": ct, "total": ctotal, "expr": cexpr, "detail": cdmg.detail}
                    )
                    parts_for_apply.append({"amount": ctotal, "type": ct})
                if man_part is not None:
                    # The maneuver die is a flat pre-rolled add (no expr to re-roll/double).
                    comp_results.append({
                        "type": man_part["type"],
                        "total": man_bonus.amount,
                        "expr": man_bonus.expr,
                        "detail": man_bonus.detail,
                        "maneuver": man_bonus.source,
                    })
                    parts_for_apply.append(man_part)
                outcome = combat.apply_damage_components(
                    target, parts_for_apply, crit=is_crit
                )
                rolled_total = sum(cr["total"] for cr in comp_results)
                # Align each surfaced component with its post-resistance landed amount.
                for cr, adj in zip(comp_results, outcome.get("components", [])):
                    cr["applied"] = adj.get("adjusted", cr["total"])
                # "type" / "total" stay scalar for back-compat with log/UI readers:
                # report the pre-resistance rolled sum and a '+'-joined type label.
                type_label = " + ".join(
                    dict.fromkeys(cr["type"] for cr in comp_results if cr["type"])
                )
                result["damage"] = {
                    "total": rolled_total,
                    "type": type_label,
                    "applied_total": outcome.get("total_adjusted", rolled_total),
                    "components": comp_results,
                    "detail": "; ".join(cr["detail"] for cr in comp_results),
                }
            else:
                expr = combat.double_dice(damage_dice) if is_crit else damage_dice
                dmg = dice_mod.roll(expr)
                outcome = combat.apply_damage(target, max(0, dmg.total), crit=is_crit, damage_type=damage_type)
                result["damage"] = {"total": max(0, dmg.total), "type": damage_type, "expr": expr, "detail": dmg.detail}
            if man_bonus is not None:
                # Surface the maneuver's contribution explicitly so the DM/log sees the die
                # that landed (and that it WAS applied), distinct from the weapon dice.
                result["maneuver_damage"] = {
                    "maneuver": man_bonus.source,
                    "die": man_bonus.expr,
                    "rolled": man_bonus.amount,
                    "detail": man_bonus.detail,
                    "applied": man_bonus.amount > 0,
                }
            result["target_state"] = outcome
            # F3-6: if this strike downed/killed a concentrating caster, free its held targets
            # NOW (Hold Person paralysis, an allied Bless child) — the combat layer cleared the
            # caster's concentration but can't see the campaign-wide victims.
            if was_conc_target and target.concentration is None:
                freed = _release_held_targets(c, target_id, was_conc_target)
                if freed:
                    result["freed_targets"] = freed
            kx = _award_kill_xp(c, target)
            if kx:
                result["kill_xp"] = kx
        elif man_bonus is not None:
            # The strike MISSED, so the maneuver die adds no damage — but it was already spent
            # at use_resource time (and consumed above), so report it as spent-not-applied
            # rather than silently swallowing it. The die does NOT carry to a later attack.
            result["maneuver_damage"] = {
                "maneuver": man_bonus.source,
                "die": man_bonus.expr,
                "rolled": man_bonus.amount,
                "detail": man_bonus.detail,
                "applied": False,
                "note": "attack missed — superiority die spent but no damage added",
            }
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
                        # Flag the rider as advantage-granting (Guiding Bolt) so the NEXT
                        # attack against this target auto-gets advantage via
                        # combat.attack_modifiers and is consumed there (#194).
                        grants_advantage=combat.spell_grants_advantage(r.name),
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
        # CONSUME the advantage-granting rider (Guiding Bolt) that auto-granted advantage to
        # THIS attack (#194). 5e: "the next attack roll made against it has Advantage" — the
        # marker is spent by the next attack ROLL (hit OR miss), so it benefits exactly one
        # attack. ``adv_marker`` was captured before the roll, so a Guiding-Bolt SPELL attack
        # that just materialized a fresh marker on this same target (above) is not consumed
        # here (that path had no pre-existing marker). Removed by identity so re-applied
        # same-name effects aren't clobbered.
        if adv_marker is not None:
            target.active_effects = [
                e for e in target.active_effects if e is not adv_marker
            ]
            result["advantage_source"] = adv_marker.name
            result["advantage_consumed"] = True
        # --- ADHERENCE CUES (tool-return, the proven channel — #A1/#A2/#A4) ---------------
        # These NEVER auto-apply or block; they surface a deterministic signal the DM keeps
        # skipping from prose alone. Only meaningful while a fight is active.
        if c.combat.active:
            # (A4 → F01-9) The engine AUTO-ROLLS the concentration save the instant the
            # damage lands, rather than surfacing a cue the DM kept deferring mid-Multiattack
            # (the concentration spell hung on indefinitely). combat.py returned the DC in
            # target_state; we roll the CON save here (engine rolls, DM is told), break
            # concentration on a failure, and surface the result at the TOP of the return so
            # it's read reliably. The manual concentration_save tool remains as an override.
            ts = result.get("target_state") or {}
            conc = _auto_concentration_save(target, ts.get("concentration_dc"))
            if conc is not None:
                result["concentration_save"] = conc
            # (A1) Ranged-in-melee disadvantage nudge. The engine has no positional model
            # (theater-of-mind), so it can't auto-apply the penalty — but a ranged attack with
            # no adv/dis flag, fired while a living opposing hostile is in the order, is the
            # exact pattern that needs disadvantage if a foe is within 5 ft. Conservative: a
            # nudge to re-issue, never a block, honoring theater-of-mind (the foe MAY be 10 ft
            # away). Only when the attacker is a combatant and didn't already flag adv/dis.
            if is_ranged and not adv and not dis and attacker_cb is not None:
                opp = "monster" if attacker.kind in ("player", "companion") else "player"
                foes_live = any(
                    (h := c.characters.get(cb.character_id)) is not None
                    and h.id != attacker_id and h.current_hp > 0 and not h.dead
                    and (h.kind == opp or (opp == "player" and h.kind == "companion"))
                    for cb in c.combat.order
                )
                if foes_live:
                    result["ranged_in_melee_check"] = {
                        "note": (f"ranged attack with no disadvantage flag — if a hostile is "
                                 f"within 5 ft of {attacker.name} (theater-of-mind), 5e requires "
                                 "disadvantage:true. Re-issue with disadvantage=True if so."),
                    }
            # (A2) Parry-available cue. The engine spends Parry ONLY when it would FLIP the hit
            # to a miss (above); when the blow lands anyway the reaction is correctly NOT spent
            # — but then it's invisible and the DM never narrates the attempted defense (the
            # recurring "reaction never fired" ding). Surface that the defender HAS Parry and
            # why it wasn't spent, so the failed-but-attempted defense is narratable, WITHOUT
            # wasting the reaction. Only on a landed melee hit vs a parry-capable, reaction-able
            # defender whose parry wouldn't have stopped this blow.
            if (
                hit and not is_ranged and parry_info is None and target.parry > 0
                and not combat.is_incapacitated(target)
                and Condition.BLINDED not in target.conditions
            ):
                target_cb = next(
                    (cb for cb in c.combat.order if cb.character_id == target_id), None
                )
                if target_cb is not None and not target_cb.reaction_used:
                    eff_ac = target_ac + target.parry
                    result["parry_available"] = {
                        "defender": target.name,
                        "ac_bonus": target.parry,
                        "effective_ac": eff_ac,
                        "note": (f"{target.name} has a Parry reaction (+{target.parry} AC vs one "
                                 f"melee hit) but it would not have stopped this blow (roll "
                                 f"{atk_total} >= effective AC {eff_ac}). Reaction NOT spent — "
                                 "narrate the attempted-but-overwhelmed defense if you like."),
                    }
        else:
            # F01-14: an attack made with NO active combat resolves at full effect (a real
            # blow is a real blow — trap/hazard inertness is preserved). But a PC striking a
            # living foe outside initiative is the exact pattern that should have been a
            # combat: the turn-order/economy gates are all invisible, and QA can't see the
            # fight at all (assert_behavioral's combat-integrity checks nest under
            # start_combat>0). Surface a NUDGE to call start_combat — advisory, never a block.
            # Only when attacker AND target are living creatures on OPPOSING sides (so a
            # one-off environmental strike, or hitting an ally, doesn't nag).
            ally_kinds = {"player", "companion"}
            atk_ally = attacker.kind in ally_kinds
            tgt_ally = target.kind in ally_kinds
            if (
                attacker.kind in ally_kinds | {"monster", "npc"}
                and target.kind in ally_kinds | {"monster", "npc"}
                and atk_ally != tgt_ally
                and attacker.current_hp > 0 and not attacker.dead
                and not target.dead
            ):
                result["combat_not_active"] = {
                    "note": (
                        f"{attacker.name} attacked {target.name} with NO active combat — the "
                        "turn-order and action-economy gates are inert and QA can't see this "
                        "as a fight. If this is a real encounter, call start_combat([…ids…]) "
                        "first. (The attack still fully resolved — this is advisory.)"
                    ),
                }
        outcome_label = "crit" if is_crit else ("hit" if hit else "miss")
        if hit and result["damage"]:
            # Use the resolved damage type label so a multi-component strike (#210)
            # narrates "piercing + necrotic", not the empty scalar damage_type.
            _dt_label = result["damage"].get("type") or damage_type
            dtype = f" {_dt_label}" if _dt_label else ""
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
                "target": {
                    **_combatant_ref(target),
                    "ac": target_ac,
                    "armor_class": target_ac,
                    "base_ac": target.armor_class,
                    "ac_detail": target_ac_detail,
                },
                "roll": {
                    "total": atk_total,
                    "natural": atk.natural,
                    "detail": atk.detail,
                    "attack_bonus": attack_bonus,
                    "advantage": adv,
                    "disadvantage": dis,
                    # SYN-06: engine-rolled rider components (Bless/Bane), when any
                    **({"bonus_dice": rider_rolls} if rider_rolls else {}),
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
    campaign_id: str, target_id: str = "", amount: int = 0, damage_type: str = "", crit: bool = False,
    half: bool = False, character_id: str = "", id: str = ""
) -> dict:
    """Apply damage to a character. Temp HP is absorbed first; HP floors at 0;
    massive damage causes instant death; dropping to 0 makes the target unconscious
    and dying; a hit while already down adds a death-save failure (two on a crit).
    Set half=True for a successful save vs a 'half on save' spell (halves the amount).
    `damage_type` (e.g. 'fire', 'slashing') applies the target's resistance (half),
    immunity (none), or vulnerability (double). Returns the new state, including
    any concentration_dc to roll. Identify the target via ``target_id`` (canonical) or the
    aliases ``character_id`` / ``id`` — equivalent; ``target_id`` wins if more than one is given."""
    target_id = target_id or character_id or id  # accept the id the DM reaches for
    if not target_id:
        raise ValueError("apply_damage needs a target (pass `target_id` or an alias: `character_id`/`id`)")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        target = _char(c, target_id)
        was_conc = target.concentration  # F3-6: capture before damage may down the caster
        out = combat.apply_damage(target, amount, crit=crit, half=half, damage_type=damage_type)
        # F01-9: the engine auto-rolls the concentration save the instant damage lands
        # (combat.py returned the DC; it stays dice-free). Surfaced under
        # ``concentration_save`` so the DM narrates the break/hold without having to call
        # concentration_save by hand. None == target wasn't concentrating (unchanged path).
        # This MAY break concentration (clears target.concentration on a failed save) — the
        # F3-6 block below then frees any victims that spell was holding.
        conc = _auto_concentration_save(target, out.get("concentration_dc"))
        if conc is not None:
            out["concentration_save"] = conc
        # F3-6: if this damage ended the caster's concentration — by downing/killing it
        # (combat.apply_damage cleared it, but is Character-pure and can't see the victims) OR
        # by the auto-rolled save above failing — free its held targets NOW. Inert when the
        # target wasn't concentrating or is still concentrating.
        freed = _release_held_targets(c, target_id, was_conc or "") if (
            was_conc and target.concentration is None
        ) else []
        if freed:
            out["freed_targets"] = freed
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
def apply_healing(campaign_id: str, target_id: str = "", amount: int = 0,
                  character_id: str = "", id: str = "") -> dict:
    """Heal a character (up to max HP). Healing above 0 HP ends the dying state
    and resets death saves. Cannot revive the dead. Identify the target via ``target_id``
    (canonical) or the aliases ``character_id`` / ``id`` — ``target_id`` wins if more than one."""
    target_id = target_id or character_id or id  # accept the id the DM reaches for
    if not target_id:
        raise ValueError("apply_healing needs a target (pass `target_id` or an alias: `character_id`/`id`)")
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
def set_temp_hp(campaign_id: str, target_id: str = "", amount: int = 0,
                character_id: str = "", id: str = "") -> dict:
    """Grant temporary HP. Temp HP does NOT stack — keeps the higher of current
    and new (SRD rule). Identify the target via ``target_id`` (canonical) or the aliases
    ``character_id`` / ``id`` — ``target_id`` wins if more than one is given."""
    target_id = target_id or character_id or id  # accept the id the DM reaches for
    if not target_id:
        raise ValueError("set_temp_hp needs a target (pass `target_id` or an alias: `character_id`/`id`)")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, target_id)
        ch.temp_hp = max(ch.temp_hp, max(0, amount))
        save_campaign(c)
        return {"temp_hp": ch.temp_hp, "hp": f"{ch.current_hp}/{ch.max_hp}"}


def _release_held_targets(c, caster_id: str, spell_name: str) -> list[dict]:
    """Free every TARGET still locked by the just-ended concentration `spell_name` of
    caster `caster_id` (F3-6): a repeat-save marker (Hold Person -> paralyzed) OR a
    concentration-linked numeric-rider child (Bless on an ally, SYN-06/#780) is the
    victim's twin of the caster's concentration — with the concentration gone the spell
    is over, so drop the effect and lift the condition it imposed. This is the SAME
    inverse-link reconciliation next_turn performs (server.py ~4017), run NOW at the
    concentration-end site instead of a round later — so the four non-drop end paths
    (failed concentration_save, caster incapacitation, caster 0 HP/death, a recast that
    displaces the prior concentration) release the victim immediately, exactly like
    drop_concentration already does. Returns the freed-target list (possibly empty).

    A no-op when `spell_name` is falsy (the caster wasn't concentrating on anything) —
    so every caller can pass the captured prior concentration unconditionally."""
    freed: list[dict] = []
    if not spell_name:
        return freed
    for holder in list(c.characters.values()):
        for eff in list(holder.active_effects):
            # A repeat-save marker (Hold Person) OR a concentration-linked numeric-rider
            # child (Bless on an ally): both are target-side twins of THIS caster's
            # concentration and end with it. A non-concentration save-ends source (a
            # monster's innate hold) carries no source_id link, so it's never swept here.
            if eff.repeat_save is None and not eff.linked_to_concentration:
                continue
            if eff.source_id != caster_id:
                continue
            if eff.name != spell_name:
                continue
            combat.end_repeat_save_effect(holder, eff)
            freed.append({"character_id": holder.id, "name": eff.name})
    return freed


def _aoe_damage_spec(curated, srd, slot_level: int, caster_level: int, casting_mod: int):
    """The damage expression + save ability + on-save rule + damage type for an AoE/multi-target
    SAVE spell (F03-4), from whichever data source carries it, or None when the spell isn't a
    resolvable save-for-damage area spell. Returns ``{damage, save_ability, on_save, damage_type}``
    (save_ability/damage_type as short strings). Curated spells use resolve_effect (so upcast is
    applied); an srd524-only spell uses its base damage_roll (upcast is prose-only there — kept
    consistent with cast_spell's documented degrade contract: the BASE dice are rolled, the DM
    upcasts by hand if the prose says so)."""
    if curated is not None:
        eff = spells.resolve_effect(curated, slot_level, caster_level, casting_mod)
        if eff.get("kind") != "save" or not eff.get("damage"):
            return None
        return {
            "damage": eff["damage"],
            "save_ability": (eff.get("save_ability") or "dex"),
            "on_save": (eff.get("on_save") or "half"),
            "damage_type": (eff.get("damage_type") or ""),
        }
    if srd is not None:
        save_ab = (srd.get("saving_throw_ability") or "").strip().lower()
        dmg = srd.get("damage_roll") or ""
        if not save_ab or not dmg:
            return None  # not a save-for-damage area spell (e.g. Hold Person: save, no damage)
        types = srd.get("damage_types") or []
        return {
            "damage": dmg,
            "save_ability": save_ab,
            "on_save": "half",  # SRD area save-for-half is the overwhelming default
            "damage_type": (types[0] if types else ""),
        }
    return None


@mcp.tool()
def concentration_save(campaign_id: str, character_id: str, dc: int) -> dict:
    """Roll a concentration saving throw (CON save) at the given DC (usually
    max(10, damage//2) from apply_damage). On failure, concentration is lost."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        r = dice_mod.roll(f"1d20+{ch.saving_throw_bonus(Ability.CON)}")
        # NUMERIC RIDERS (SYN-06 / #780): a concentration save is a saving throw — fold
        # the engine-tracked save bonus dice (Bless +1d4 / Bane -1d4) like saving_throw.
        rider_bonus, rider_rolls = _roll_effect_bonus_dice(ch, "save_bonus_dice")
        total = r.total + rider_bonus
        maintained = total >= dc
        expired: list[str] = []
        freed: list[dict] = []
        if not maintained:
            was = ch.concentration
            ch.concentration = None
            # The engine-tracked concentration effect ends with the concentration.
            expired = combat.expire_concentration_effects(ch)
            # F3-6: a broken concentration ends the spell — so free its held victims NOW
            # (Hold Person paralysis, an allied Bless child), not a round later at next_turn.
            freed = _release_held_targets(c, character_id, was or "")
        save_campaign(c)
        out = {
            "roll": total,
            "natural": r.natural,
            "dc": dc,
            "maintained": maintained,
            "concentration": ch.concentration,
            "expired_effects": expired,
            "freed_targets": freed,
        }
        if rider_rolls:
            out["bonus_dice"] = rider_rolls
        return out


@mcp.tool()
def drop_concentration(campaign_id: str, character_id: str, reason: str = "") -> dict:
    """VOLUNTARILY end a caster's concentration — the verb for the most common narrative
    concentration event: the caster lets a spell lapse, or it's broken without a damage
    save (the DM narrates "the Hold Person shatters"). Until this tool existed, the only
    paths that cleared `concentration` were a failed concentration_save, incapacitation/0
    HP/death, and casting a NEW concentration spell — so a DM who narrated a drop with no
    follow-up cast left `concentration` (and its tracked effect) set, desyncing state into
    the next session (QA ow-cs2: a phantom multi-round Hold Person persisted past close and
    corrupted a later session). This clears the caster's `concentration` field, expires the
    caster's concentration-flagged ActiveEffects, AND frees every TARGET still locked by a
    repeat-save twin of this concentration (e.g. a paralyzed Hold Person victim) — the same
    inverse-link reconciliation next_turn performs, run NOW instead of next round. A no-op
    (concentration stays None) when the caster wasn't concentrating."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        was = ch.concentration
        ch.concentration = None
        # The caster's own engine-tracked concentration effect(s) end with the concentration.
        expired = combat.expire_concentration_effects(ch)
        # TARGET-side twin: free every holder still locked by this caster's just-dropped
        # concentration (Hold Person victim, an allied Bless child). Mirrors next_turn's
        # inverse-link sweep so a voluntarily-dropped hold releases its victim immediately,
        # not a round later. We can only reconcile the spell we just dropped (`was`).
        freed = _release_held_targets(c, character_id, was or "")
        save_campaign(c)
        return {
            "ended": was is not None,
            "was_concentrating_on": was,
            "concentration": ch.concentration,
            "expired_effects": expired,
            "freed_targets": freed,
            "reason": reason,
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
        # F01-7: stabilizing a downed ally (a DC 10 Medicine check) uses the actor's ACTION —
        # gate it (incapacitation/turn/action, before the roll; inert outside combat).
        _gate_combat_verb(c, actor, verb="stabilize an ally", consumes="action")
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
    # Tier 3 — slain: record bestiary intel the MOMENT a bestiary monster dies (#263).
    # Placed BEFORE the xp-mode/xp-value gates so a kill is recorded in EVERY leveling mode
    # and through every death path that reaches this hook (set_hp / attack / apply_damage /
    # end_combat's backstop sweep). Idempotent via _bump_intel's monotonic max — a re-call
    # never regresses. No-op for non-monsters, the living, or a monster with no creature_slug.
    if getattr(monster, "kind", "") == "monster" and monster.dead:
        _bump_intel(c, getattr(monster, "creature_slug", ""), 3)
    if c.leveling_mode != "xp":
        return None
    if getattr(monster, "kind", "") != "monster" or not monster.dead or monster.xp_value <= 0:
        return None
    # The whole travelling group earns (PC + de-facto companions), #353 — mirrors the
    # relocate sweep so a companion that co-locates also levels.
    recipients = [c.characters[i] for i in _party_xp_recipients(c)]
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


def _award_milestone_xp(c: Campaign, amount: int, reason: str) -> "dict | None":
    """Award a deterministic, modest milestone XP grant to the living party, split
    evenly (remainder to the first recipient) — the non-combat sibling of
    `_award_kill_xp`. Story/social/exploration progress (quest resolution, a session
    that genuinely advanced) pays XP here so an "xp" `leveling_mode` campaign never
    ends a real win at 0 XP. No-op outside "xp" mode, for a non-positive amount, or an
    empty/dead party. Caller persists (sole-writer) and guards idempotency."""
    if c.leveling_mode != "xp" or amount <= 0:
        return None
    # The whole travelling group earns (PC + de-facto companions), #353.
    recipients = [c.characters[i] for i in _party_xp_recipients(c)]
    if not recipients:
        return None
    each, rem = divmod(amount, len(recipients))
    grants = []
    for idx, ch in enumerate(recipients):
        amt = each + (rem if idx == 0 else 0)
        ch.xp = max(0, ch.xp + amt)
        available = srd_tables.level_for_xp(ch.xp)
        grants.append({"id": ch.id, "name": ch.name, "xp_gained": amt, "xp": ch.xp,
                       "level_available": available, "can_level_up": available > ch.total_level})
    return {"xp_awarded": amount, "grants": grants, "reason": reason}


@mcp.tool()
def end_combat(campaign_id: str, resolution: str = "") -> dict:
    """End combat (clears initiative, round, and turn order). Character HP and
    conditions persist past the encounter.

    If the campaign is in "xp" `leveling_mode` (the default), the XP of monsters
    defeated THIS encounter is auto-awarded to the party, split evenly — so
    progression isn't a manual chore. Returns `xp_awarded` + per-character `grants`
    (with `can_level_up`) when any was granted. "milestone" mode leaves leveling to
    the DM (no auto-XP).

    `resolution`: REQUIRED when you end combat while hostile monsters are still alive
    (a flee / surrender / capture / retreat / parley) — a short clause naming HOW they
    left, e.g. "the surviving bandits flee into the alley" / "the captain surrenders".
    It is recorded into the combat_end event so the save isn't a continuity break (a
    fight left with enemies standing and no reason logged). If every hostile was brought
    to 0 HP, omit it. When hostiles remain and you pass nothing, the result carries
    `needs_resolution: true` + `warning_live_hostiles` — resolve them or narrate the
    disengagement and pass `resolution`."""
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
        # Live-hostile advisory (#E2, NON-blocking): 5e ends combat only when a side is
        # incapacitated / flees / surrenders. Ending it administratively with hostile
        # monsters still standing at >0 HP leaves them alive in state, a continuity break
        # for the next load (QA ow-cs2: end_combat fired with a Ghoul at 22/22 + Bandit
        # Captain at 34/52, both dead:false). We do NOT block — a legitimate retreat/parley
        # ends combat with foes alive — we surface it, mirroring the existing range_warning /
        # extra_attack_reminder advisory pattern. Computed from the order BEFORE the reset.
        live_hostiles = [
            {"id": ch.id, "name": ch.name, "hp": f"{ch.current_hp}/{ch.max_hp}"}
            for cb in c.combat.order
            if (ch := c.characters.get(cb.character_id)) is not None
            and ch.kind == "monster" and ch.current_hp > 0 and not ch.dead
        ]
        res = (resolution or "").strip()
        if res:
            # Persist the DM-declared disposition so the end_combat_no_living_hostiles gate can tell
            # a legitimate flee/surrender from a continuity break (the combat chronicle is NOT in the
            # snapshot the gate reads). Only set when a reason is given; cleared at start_combat.
            c.last_combat_resolution = res
        if live_hostiles:
            result["warning_live_hostiles"] = {
                "count": len(live_hostiles),
                "hostiles": live_hostiles,
                "resolved": bool(res),
                "note": (
                    f"combat ended with {len(live_hostiles)} hostile(s) still alive at >0 HP "
                    "— if they didn't flee/surrender/die, this leaves them standing in state. "
                    "Bring them to 0 via attack/apply_damage, or pass `resolution=` naming how "
                    "they left (fled/surrendered/captured/retreated), before ending."
                ),
            }
            if not res:
                # Continuity nudge the DM-wrapper surfaces: a fight cannot end with enemies
                # standing and no logged reason (the end_combat_no_living_hostiles gate).
                result["needs_resolution"] = True
        if was_active or c.combat.order:
            ended_order = [
                _combatant_ref(c.characters[cb.character_id])
                for cb in c.combat.order
                if cb.character_id in c.characters
            ]
            end_text = f"Combat ends — {res}" if res else "Combat ends."
            payload = {
                "event": "combat_end",
                "round": c.combat.round,
                "combatants": ended_order,
                "xp_awarded": result.get("xp_awarded", 0),
            }
            if res:
                # Record the DM-authored disposition into the event so the save explains why a
                # fight ended with foes alive (resolves the continuity-break behavioral gate).
                payload["resolution"] = res
            _log_combat_event(c, end_text, payload)
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
        # The travelling group earns together (#353): include de-facto companions —
        # kind='companion' but not in c.party — the same set the relocate sweep co-locates,
        # unless the caller opts out with include_companions=False.
        recipients = _party_xp_recipients(c, include_companions=include_companions)
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

        # F02-6: apply a CON-raising ASI BEFORE sizing this level's HP so the new level
        # uses the POST-ASI CON (the SRD intent — a +CON at this level adds HP at this
        # level too), and so we can retro-adjust the prior levels by the CON-mod delta.
        # `con_before` is captured here; `applied`/abilities mutate just below.
        con_before = ch.ability_modifier(Ability.CON)
        prior_total_level = ch.total_level  # levels the character ALREADY had before this one

        # Normalize a chosen subclass to its canonical SRD name when the table knows
        # it ('Evocation' -> 'Evoker'); an unknown/world-canon name passes through
        # verbatim (additive — the DM still finalizes a homebrew tradition by name).
        if subclass:
            subclass = srd_tables.resolve_subclass(cname, subclass) or subclass

        # #624 backfill: is this level-up SETTING a subclass that was unset until
        # now? (Captured before mutation — drives the missed-choice feature grant.)
        subclass_newly_set = bool(
            subclass and existing is not None and not (existing.subclass or "").strip()
        )

        if existing:
            existing.level += 1
            if subclass:
                existing.subclass = subclass
        else:
            ch.classes.append(ClassLevel(name=class_name.capitalize(), level=1, subclass=subclass))

        applied = None
        pending_choice_recorded = False
        if is_asi_level:
            if pending_asi:
                for ability, inc in pending_asi.items():
                    setattr(ch.abilities, ability, min(20, getattr(ch.abilities, ability) + inc))
                applied = {"asi": pending_asi}
            elif feat:
                applied = {"feat": feat}
                # F02-3: record the chosen feat on a STRUCTURED ledger the DM/viewer can read,
                # not just buried in free-text notes (the feat path was 100% inert). The note
                # line stays for back-compat; `feats` is the surface the engine/viewer key off.
                if feat not in ch.feats:
                    ch.feats.append(feat)
                ch.notes = (ch.notes + f" | feat: {feat}").strip(" |")
            else:
                # F02-3: an ASI/feat was DUE this level but NEITHER was supplied. Previously
                # this choice was silently dropped and no surface ever offered it again. RECORD
                # the debt on the pending-choice ledger so the DM (or update_character) can
                # settle it later. Additive: a level-up that DOES take the choice records nothing.
                label = f"ASI/feat due: {class_name.capitalize()} level {new_class_level}"
                if label not in ch.pending_choices:
                    ch.pending_choices.append(label)
                pending_choice_recorded = True

        # F02-6: now that any CON-raising ASI has landed, size THIS level's HP with the
        # POST-ASI CON, and retro-adjust the HP already banked at the prior levels by the
        # CON-modifier delta (raising CON grants +1 HP per level you already have; lowering it
        # via a corrective drop removes them, floored so HP never goes < 1).
        con_after = ch.ability_modifier(Ability.CON)
        if hp_method == "roll":
            base = hp_roll if hp_roll is not None else dice_mod.roll(f"1d{die}", seed=seed).total
        else:
            base = srd_tables.average_hp(die)
        gain = max(1, base + con_after)
        con_retro = (con_after - con_before) * prior_total_level
        ch.max_hp = max(1, ch.max_hp + gain + con_retro)
        ch.current_hp = max(1, ch.current_hp + gain + con_retro)
        ch.hit_dice_remaining += 1
        # keep the hit_dice string in sync (single-class; was left stale after level_up)
        if len({cl.name.lower() for cl in ch.classes}) == 1:
            ch.hit_dice = f"{sum(cl.level for cl in ch.classes)}d{die}"

        ch.proficiency_bonus = srd_tables.proficiency_bonus(ch.total_level)
        ch.initiative_bonus = ch.ability_modifier(Ability.DEX)
        _recompute_spellcasting(ch)
        _recompute_class_resources(ch)

        # Class/subclass features gained at this new class level — leveling now
        # grants real features (and the mechanical hints the engine references),
        # not just HP and slots.
        gained = srd_tables.features_at(cname, new_class_level)
        # #624: if the character chose a subclass and this is the subclass-choice
        # level, also grant that subclass's choice-level features (e.g. an Evoker's
        # Evocation Savant + Sculpt Spells) — not just the generic placeholder.
        cur_subclass = next(
            (cl.subclass for cl in ch.classes if cl.name.lower() == cname), None
        )
        gained = gained + srd_tables.subclass_features_at(cname, cur_subclass, new_class_level)
        # #624 backfill: a subclass chosen LATE (the missed-choice case — set for
        # the first time at a level PAST the choice level) still grants its
        # choice-level features at the level-up that sets it, not nothing.
        slvl = srd_tables.subclass_level(cname)
        if subclass_newly_set and slvl is not None and new_class_level > slvl:
            gained = gained + srd_tables.subclass_features_at(cname, cur_subclass, slvl)
        for f in gained:
            if f["name"] not in ch.features:
                ch.features.append(f["name"])
            if "extra_attacks" in f:
                ch.extra_attacks = max(ch.extra_attacks, int(f["extra_attacks"]))
            if f.get("sneak_attack_dice"):
                ch.sneak_attack_dice = f["sneak_attack_dice"]

        # F02-14: in XP leveling mode, WARN (never block — the engine stays the sole writer and
        # the DM may have a reason) when the character isn't yet XP-entitled to the level they
        # just took. Milestone campaigns level by story beat, so the check is xp-mode-only.
        # `prior_total_level` is the level BEFORE this up; entitlement is by current xp.
        xp_warning = None
        if c.leveling_mode == "xp":
            entitled = srd_tables.level_for_xp(ch.xp)
            new_total = ch.total_level
            if new_total > entitled:
                xp_warning = (
                    f"{ch.name} leveled to {new_total} but has only enough XP for level "
                    f"{entitled} ({ch.xp} XP). Award XP first (award_xp/award_party_xp) or set "
                    f"house_rules / leveling_mode='milestone' if leveling by story beat."
                )

        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        save_campaign(c)
        sheet = c.characters[character_id].model_dump(mode="json")
        sheet["_hp_gained"] = gain
        sheet["_asi_applied"] = applied
        sheet["_features_gained"] = gained
        sheet["_pending_choice_recorded"] = pending_choice_recorded
        sheet["_xp_warning"] = xp_warning
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
    # F02-6 (preview/actual parity): mirror level_up — apply a CON-raising ASI BEFORE sizing
    # this level's HP, and retro-adjust the prior levels by the CON-mod delta, so the previewed
    # hp_gain/max_hp match what level_up will actually write.
    con_before = preview.ability_modifier(Ability.CON)
    prior_total_level = preview.total_level

    if existing:
        existing.level += 1
        if subclass:
            existing.subclass = subclass
        new_class_level = existing.level
    else:
        preview.classes.append(ClassLevel(name=class_name.capitalize(), level=1, subclass=subclass))
        new_class_level = 1

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

    con_after = preview.ability_modifier(Ability.CON)
    if hp_method == "roll":
        base = hp_roll if hp_roll is not None else dice_mod.roll(f"1d{die}", seed=seed).total
    else:
        base = srd_tables.average_hp(die)
    gain = max(1, base + con_after)
    con_retro = (con_after - con_before) * prior_total_level
    preview.max_hp = max(1, preview.max_hp + gain + con_retro)
    preview.current_hp = max(1, preview.current_hp + gain + con_retro)
    preview.hit_dice_remaining += 1
    if len({cl.name.lower() for cl in preview.classes}) == 1:
        preview.hit_dice = f"{sum(cl.level for cl in preview.classes)}d{die}"

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


def _subclass_block_for(cname: str, next_class_level: int, current_subclass: Optional[str]) -> Optional[dict]:
    """#624: the subclass-choice block a build option carries when leveling INTO a
    class's subclass-choice level without a subclass already set — the legal SRD
    options (each with a brief feature preview) so the surface renders a real list
    instead of a free-text box. None when no choice is due at this level.

    Backfill (rc2 audit): a character ALREADY PAST the choice level with the
    subclass still unset (the pendingSubclass case — e.g. an L5 wizard with no
    Arcane Tradition leveling to L6) is offered the missed choice at the next
    level-up, matching common-practice 5e table rules. With a subclass already
    set, nothing is offered past the choice level (unchanged)."""
    slvl = srd_tables.subclass_level(cname)
    if slvl is None:
        return None
    unset = not bool((current_subclass or "").strip())
    if next_class_level != slvl and not (unset and next_class_level > slvl):
        return None
    options = srd_tables.subclass_options(cname)
    if not options:
        return None
    return {
        "required": unset,
        "group_label": srd_tables.subclass_group_label(cname),
        "current": current_subclass,
        "options": options,
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

    existing_subclass = {
        cl.name.lower(): cl.subclass for cl in before.classes
    }
    for cname in class_names:
        preview = preview_level_up(campaign_id, character_id, cname)
        option = _build_option_from_preview(
            preview,
            c.house_rules.feats_allowed,
            c.house_rules.multiclass_allowed,
        )
        # #624: surface the subclass picker (options + previews) when this path
        # levels into the class's subclass-choice level without one chosen yet.
        next_class_level = preview["to"]["class_level"]
        sub_block = _subclass_block_for(cname, next_class_level, existing_subclass.get(cname))
        if sub_block is not None:
            option["subclass"] = sub_block
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


def _castable_affordance(ch) -> str:
    """A terse "what this caster CAN cast right now" clause for a cast_spell refusal
    (F14-7 / #812): the prepared spells (or the known list when there's no prepared
    list) plus the available-vs-max slot table. A refusal that just says "doesn't know X"
    wastes a DM beat (the DM freehands a hallucinated spell); naming the castable set lets
    the very next call recover. Bounded (≤8 names) so the message stays scannable; pure —
    reads the sheet, never mutates."""
    spells_list = ch.spells_prepared or ch.spells_known
    label = "prepared" if ch.spells_prepared else "known"
    parts: list[str] = []
    if spells_list:
        shown = ", ".join(spells_list[:8])
        more = "" if len(spells_list) <= 8 else f" (+{len(spells_list) - 8} more)"
        parts.append(f"{label}: {shown}{more}")
    # available/max per slot level, low→high; only levels the caster actually has
    slots = [
        f"L{lvl} {s.maximum - s.used}/{s.maximum}"
        for lvl, s in sorted(ch.spell_slots.items())
        if s.maximum > 0
    ]
    if slots:
        parts.append("slots " + ", ".join(slots))
    return ("; ".join(parts)) if parts else ""


@mcp.tool()
def cast_spell(
    campaign_id: str,
    character_id: str,
    spell_name: str = "",
    slot_level: Optional[int] = None,
    target_id: str = "",
    is_melee: bool = False,
    is_reaction: bool = False,
    spell: str = "",
    npc_id: str = "",
    id: str = "",
    as_ritual: bool = False,
    innate: bool = False,
    target_ids: Optional[list] = None,
) -> dict:
    """Cast a spell — works for ANY of the ~339 SRD spells. Consumes a spell slot
    (cantrips use none); upcasts when slot_level exceeds the spell's level; sets
    concentration if the spell concentrates (breaking any prior). If spells_known/
    prepared are set, the spell must be among them (skipped leniently when empty).

    RITUAL CASTING (#813): pass ``as_ritual=True`` for a ritual-tagged spell (Detect
    Magic, Identify…) to cast it WITHOUT spending a slot — the ritual takes 10 extra
    minutes instead, so it is refused during active combat. Concentration/duration
    semantics are unchanged. A normal cast of a ritual spell surfaces
    ``ritual_available: true`` so you know the slot-free option exists.

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
    never gated. Inert when no zones are declared (theater-of-the-mind).

    Name the spell via ``spell_name`` (canonical) or ``spell`` (alias); identify an explicit
    target via ``target_id`` (canonical) or the aliases ``npc_id`` / ``id``. (``character_id``
    is the CASTER and is unchanged.) Canonical names win if more than one is given."""
    # Coalesce intuitive arg-name aliases to the canonical params. `character_id` (the caster)
    # is canonical and untouched; the alias ids resolve only the explicit `target_id`.
    spell_name = spell_name or spell
    target_id = target_id or npc_id or id
    if not spell_name:
        raise ValueError("cast_spell needs a spell (pass `spell_name` or its alias `spell`)")
    curated = None
    try:
        curated = spells.spell_data(spell_name)
    except ValueError:
        curated = None
    srd = spells.srd_spell(spell_name)
    if curated is None and srd is None:
        # F14-7 (#812): a bare "unknown spell 'X'" on a typo wastes a DM beat. Surface a
        # did-you-mean of the nearest SRD spell name(s) so the next call recovers. ADDITIVE:
        # the "unknown spell {name!r}" key/prefix is preserved (consumers match on it).
        near = difflib.get_close_matches(spell_name, spells.all_spell_names(), n=3, cutoff=0.6)
        hint = f" — did you mean {', '.join(repr(n) for n in near)}?" if near else ""
        raise ValueError(f"unknown spell {spell_name!r}{hint}")
    canonical = (curated or srd).get("name", spell_name)
    spell_level = int((curated.get("level", 0) if curated else srd.get("level", 0)) or 0)
    concentrates = bool(curated.get("concentration") if curated else srd.get("concentration"))
    # Ritual tag (#813): the srd524 records carry `ritual`; curated records may too.
    is_ritual = bool((curated or {}).get("ritual") or (srd or {}).get("ritual"))
    if as_ritual and not is_ritual:
        raise ValueError(
            f"{canonical} is not a ritual spell — only a ritual-tagged spell can be "
            f"cast with as_ritual=True. Cast it normally (spending a slot) instead."
        )
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
        # AoE / MULTI-TARGET VALIDATION (F03-4): validate-BEFORE-spend. Resolve EVERY id in
        # `target_ids` up front; if ANY is unknown, reject the WHOLE cast cleanly here — BEFORE
        # the slot spend / concentration set (the engine's rejection-before-state-change
        # discipline). A non-empty list means "the engine resolves the area's per-target damage
        # saves itself" (one shared damage roll, full/half per save). Empty/None = today's
        # single-target behavior, byte-identical. Order-preserving + de-duped.
        aoe_targets: list = []
        if target_ids:
            seen_ids: set[str] = set()
            for tid in target_ids:
                tid = str(tid)
                if tid in seen_ids:
                    continue
                tgt = c.characters.get(tid)
                if tgt is None:
                    raise ValueError(
                        f"unknown target id {tid!r} in target_ids — the whole AoE cast is "
                        f"rejected (no slot spent). Check the id and re-cast."
                    )
                seen_ids.add(tid)
                aoe_targets.append(tgt)
        # SRD: an incapacitated creature can't take an action — and a spell's casting time is
        # (almost always) an action/bonus/reaction, so refuse the cast outright rather than
        # spend the caster's slot / set concentration. Mirrors the attack() guard. (extends #42)
        if combat.is_incapacitated(ch):
            incap = ", ".join(cn.value for cn in ch.conditions if cn in combat.INCAPACITATING)
            raise ValueError(f"{ch.name} is incapacitated ({incap}) and cannot cast a spell")
        # Ritual casting takes 10 extra MINUTES — combat runs in 6-second rounds, so a
        # ritual can't be performed while combat is active (#813). Rejected BEFORE any
        # state change (no slot, no concentration), like every other cast rejection.
        if as_ritual and c.combat.active:
            raise ValueError(
                f"cannot cast {canonical} as a ritual during active combat — a ritual "
                f"takes 10 extra minutes. Cast it normally (spending a slot) or wait "
                f"until combat ends."
            )
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
        # KNOWN / PREPARED GATE (F03-7 + F03-8). Compare CASE-INSENSITIVELY (F03-7): the
        # cast-side `canonical` is the proper-cased SRD name, but legacy snapshots may carry
        # raw-cased strings (learn_spells/prepare_spells now canonicalize on write, but old
        # campaigns round-trip), so casefold both sides — a lowercase-stored "magic missile"
        # still matches the canonical "Magic Missile".
        known_cf = {s.strip().lower() for s in ch.spells_known}
        prepared_cf = {s.strip().lower() for s in ch.spells_prepared}
        cf = canonical.strip().lower()
        # F14-7 (#812): a known/prepared/cantrip refusal must NAME what the caster CAN cast so
        # the DM's next call recovers instead of freehanding a hallucinated spell. The leading
        # clause (the error KEY consumers match on) is unchanged; the affordance is appended.
        if spell_level == 0:
            # Cantrips are never "prepared" in 5e — a known cantrip is always castable. Gate
            # only against the union (lenient when both lists are empty, today's behavior).
            if (known_cf or prepared_cf) and cf not in (known_cf | prepared_cf):
                cantrips = [s for s in (ch.spells_known + ch.spells_prepared)]
                known_str = ", ".join(dict.fromkeys(cantrips)) or "(none)"
                raise ValueError(f"{ch.name} doesn't know {canonical!r} — knows: {known_str}")
        elif prepared_cf:
            # F03-8: a prepared caster (non-empty prepared list) casts a LEVELED spell only if
            # it is PREPARED — knowing it is not enough. This gives preparation real mechanical
            # weight (Rolan's snapshot: known(7) ⊋ prepared(4) was a no-op union before).
            if cf not in prepared_cf:
                raise ValueError(
                    f"{ch.name} knows {canonical!r} but hasn't prepared it — "
                    f"prepare_spells it first, or cast a prepared spell. "
                    f"Castable now: {_castable_affordance(ch)}"
                )
        elif known_cf:
            # Legacy / known-caster path: no prepared list (sorcerer, or an old snapshot that
            # only set spells_known) keeps the lenient known-only gate — byte-identical behavior.
            if cf not in known_cf:
                raise ValueError(
                    f"{ch.name} doesn't know or have {canonical!r} prepared. "
                    f"Castable now: {_castable_affordance(ch)}"
                )
        slot_used = None
        # INNATE CASTING (F03-11): a monster/NPC casts a leveled spell from an innate/at-will
        # trait (a Mage Hand Press archmage, a drow's Darkness) — there is no Vancian slot to
        # spend. `innate=True` skips the slot CHECK and SPEND but keeps every other cast
        # semantic (concentration, duration, the on-hit/save-ends rider, the DC), so an enemy
        # Hold Person routes through cast_spell and composes with F03-6's release. No-slot
        # state integrity: slot_used is reported as "innate" (not a level). Still honors the
        # downcast guard so you can't claim a sub-level cast.
        if spell_level > 0 and not as_ritual and innate:
            lvl = spell_level if slot_level is None else slot_level
            if lvl < spell_level:
                raise ValueError(f"cannot cast a level-{spell_level} spell with a level-{lvl} slot")
            slot_used = "innate"
        # A ritual cast consumes NO slot (#813) — the +10 minutes is the cost; the
        # spell resolves at its base level (a ritual can't be upcast).
        elif spell_level > 0 and not as_ritual:
            lvl = spell_level if slot_level is None else slot_level
            if lvl < spell_level:
                raise ValueError(f"cannot cast a level-{spell_level} spell with a level-{lvl} slot")
            slot = ch.spell_slots.get(lvl)
            if slot is None or slot.used >= slot.maximum:
                # Enrich the affordance for a monster/NPC caster (F03-11): no spawn path seeds
                # spell_slots, so a stat-block caster has none — point at innate=True rather than
                # leaving a dead end. PCs keep the plain slot-exhausted message.
                if ch.kind in ("monster", "npc"):
                    raise ValueError(
                        f"{ch.name} has no level-{lvl} spell slot — a monster/NPC stat block "
                        f"carries no Vancian slots. For an innate/at-will trait cast, pass "
                        f"innate=True (no slot spent); otherwise seed spell_slots first."
                    )
                # F14-7 (#812): a PC out of this slot level must SEE the slot table (what they CAN
                # still cast / upcast with) — a bare "no slot" sends the DM freehanding. The
                # "no level-{lvl} spell slot available" key/prefix is preserved (additive tail).
                slot_table = ", ".join(
                    f"L{l} {s.maximum - s.used}/{s.maximum}"
                    for l, s in sorted(ch.spell_slots.items()) if s.maximum > 0
                ) or "(no slots remaining)"
                raise ValueError(
                    f"no level-{lvl} spell slot available — slots: {slot_table}. "
                    f"Cast a cantrip, upcast into a higher slot, or rest."
                )
            slot.used += 1
            slot_used = lvl
        if concentrates:
            # A caster concentrates on ONE spell at a time, so casting a concentration
            # spell breaks any prior concentration — and its engine-tracked effect, so the
            # two stay one source of truth. Drop ALL prior concentration effects (covers
            # both replacing a different spell and recasting the same one — the fresh
            # effect registered below is authoritative). Do it BEFORE setting the field.
            displaced_conc = ch.concentration
            combat.expire_concentration_effects(ch)
            # F3-6: a recast that DISPLACES a different prior concentration ends that spell —
            # free its held victims NOW (e.g. the cleric drops Hold Person to cast Bless), not
            # a round later. Skip when recasting the SAME spell (the fresh marker rewrites it).
            if displaced_conc and displaced_conc != canonical:
                _release_held_targets(c, character_id, displaced_conc)
            ch.concentration = canonical  # replaces (breaks) any prior concentration
        # Register an engine-tracked timed effect so the spell auto-expires (instead of
        # relying on the DM to remember it). Concentration spells hold the effect on the
        # CASTER (so it stays the twin of ch.concentration, one source of truth); a
        # non-concentration buff with an explicit target holds it on that target.
        effect_holder = ch
        pending_rider = None  # set when the effect DEFERS to the spell-attack hit (#186)
        # NUMERIC RIDERS (SYN-06 / #780): the curated <=4-spell registry (Bless, Bane,
        # Shield of Faith, Shield) whose tracked effect carries a mechanical modifier the
        # engine itself applies. A CONCENTRATION rider cast at a SEPARATE target ALSO
        # writes a linked CHILD effect on that target (the caster-side twin stays the
        # concentration tracker; the child carries the numbers and is released by the
        # sweep paths when concentration ends). None for every other spell == byte-identical.
        rider_fields = combat.spell_effect_riders(canonical)
        rider_child_target = None
        if duration is not None:
            if not concentrates and target_id and target_id != character_id:
                tgt = c.characters.get(target_id)
                if tgt is not None:
                    effect_holder = tgt
            if rider_fields and concentrates and target_id and target_id != character_id:
                rider_child_target = c.characters.get(target_id)
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
                if canonical.lower() == "mage armor":
                    eff.armor_base_ac = int(effect_holder.armor_class or 10)
                    eff.armor_formula_ac = 13 + effect_holder.ability_modifier(Ability.DEX)
                if duration["scale"] in ("hours", "days"):
                    eff.expires_day, eff.expires_phase_index = _effect_clock_deadline(
                        c, duration["hours"], duration["days"]
                    )
                # SYN-06: when THIS effect is the beneficiary record (self-cast / Shield /
                # a non-concentration targeted buff), copy the curated rider numbers onto
                # it. With a separate child target the caster-side twin stays a pure
                # concentration tracker (blessing an ally must not also bless the caster).
                if rider_fields and rider_child_target is None:
                    eff.ac_bonus = int(rider_fields.get("ac_bonus", 0))
                    eff.attack_bonus_dice = rider_fields.get("attack_bonus_dice", "")
                    eff.save_bonus_dice = rider_fields.get("save_bonus_dice", "")
                # Recasting the SAME spell on a holder refreshes (doesn't stack) it.
                effect_holder.active_effects = [
                    e for e in effect_holder.active_effects if e.name != canonical
                ]
                effect_holder.active_effects.append(eff)
                # SYN-06: the concentration-linked CHILD on the separate target — same
                # clock as the twin, carries the mechanical rider, flagged so BOTH sweep
                # paths (next_turn's inverse sweep + drop_concentration) release it the
                # moment the caster's concentration ends. Refresh-not-stack, like the twin.
                if rider_child_target is not None:
                    child = ActiveEffect(
                        name=canonical,
                        source_id=character_id,
                        concentration=False,
                        scale=duration["scale"],
                        rounds_remaining=duration["rounds"],
                        until_long_rest=(duration["scale"] == "hours"),
                        expires_day=eff.expires_day,
                        expires_phase_index=eff.expires_phase_index,
                        linked_to_concentration=True,
                        ac_bonus=int(rider_fields.get("ac_bonus", 0)),
                        attack_bonus_dice=rider_fields.get("attack_bonus_dice", ""),
                        save_bonus_dice=rider_fields.get("save_bonus_dice", ""),
                    )
                    rider_child_target.active_effects = [
                        e for e in rider_child_target.active_effects if e.name != canonical
                    ]
                    rider_child_target.active_effects.append(child)
        mod = _casting_mod(ch)
        prof = ch.proficiency_bonus
        c.characters[character_id] = Character.model_validate(ch.model_dump(mode="json"))
        if effect_holder is not ch:
            c.characters[effect_holder.id] = Character.model_validate(
                effect_holder.model_dump(mode="json")
            )
        if rider_child_target is not None and rider_child_target is not effect_holder:
            c.characters[rider_child_target.id] = Character.model_validate(
                rider_child_target.model_dump(mode="json")
            )
        # AoE / MULTI-TARGET RESOLUTION (F03-4). Targets were validated up front (before the
        # slot spend). Now the area save-for-damage spell is resolved by the ENGINE: ONE shared
        # damage roll for the whole area, then a per-target saving throw vs the caster's DC,
        # applying full damage on a fail and half on a success (5e area-save default). Existing
        # save discipline applies per target via combat.save_modifiers (a paralyzed target
        # auto-fails its DEX save; restrained → disadvantage), and damage runs through the same
        # combat.apply_damage pipeline as everything else (resistances, temp HP, downing, the
        # concentration-check DC). One lock, one write. Surfaced as a per-target result table.
        aoe_result = None
        if aoe_targets:
            spec = _aoe_damage_spec(
                curated, srd,
                slot_used if isinstance(slot_used, int) else spell_level,
                ch.total_level, mod,
            )
            save_dc = 8 + prof + mod
            if spec is None:
                # A non-damage area spell (or an un-resolvable record): we still validated the
                # ids and report them, but the DM resolves the effect (no engine damage to roll).
                aoe_result = {
                    "shared_damage": None,
                    "save_dc": save_dc,
                    "note": (
                        "No engine-resolvable area damage for this spell — the targets are "
                        "validated; resolve the effect per target by hand (saving_throw + "
                        "apply_damage / add_condition)."
                    ),
                    "targets": [{"character_id": t.id, "name": t.name} for t in aoe_targets],
                }
            else:
                save_ab = Ability(spec["save_ability"])
                dmg = dice_mod.roll(spec["damage"])  # ONE roll shared across the whole area
                rows: list[dict] = []
                for t in aoe_targets:
                    auto_fail, disadvantage = combat.save_modifiers(t, save_ab)
                    sr = dice_mod.roll(
                        f"1d20+{t.saving_throw_bonus(save_ab)}", disadvantage=disadvantage
                    )
                    saved = (not auto_fail) and sr.total >= save_dc
                    half = saved and spec["on_save"] == "half"
                    # A successful save vs an on_save != "half" spell (rare for pure damage)
                    # negates entirely; resolve that as zero applied.
                    if saved and spec["on_save"] != "half":
                        outcome = {**combat.status(t), "damage_to_hp": 0, "absorbed": 0,
                                   "concentration_dc": None}
                    else:
                        was_tc = t.concentration  # F03-6: free this target's held victims if downed
                        outcome = combat.apply_damage(
                            t, dmg.total, half=half, damage_type=spec["damage_type"]
                        )
                        if was_tc and t.concentration is None:
                            _release_held_targets(c, t.id, was_tc)
                    c.characters[t.id] = Character.model_validate(t.model_dump(mode="json"))
                    kx = _award_kill_xp(c, t)
                    row = {
                        "character_id": t.id,
                        "name": t.name,
                        "save_roll": sr.total,
                        "natural": sr.natural,
                        "saved": saved,
                        "damage_taken": outcome.get("damage_to_hp", 0),
                        "current_hp": outcome.get("current_hp"),
                        "halved": half,
                    }
                    if auto_fail:
                        row["auto_fail"] = True
                    if disadvantage:
                        row["disadvantage"] = True
                    if outcome.get("concentration_dc"):
                        row["concentration_dc"] = outcome["concentration_dc"]
                    if kx:
                        row["kill_xp"] = kx
                    rows.append(row)
                aoe_result = {
                    "shared_damage": {"total": dmg.total, "expr": spec["damage"],
                                      "type": spec["damage_type"], "detail": dmg.detail},
                    "save_ability": save_ab.value,
                    "save_dc": save_dc,
                    "on_save": spec["on_save"],
                    "targets": rows,
                }
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
        # AoE / multi-target table (F03-4): the engine-resolved per-target save+damage outcomes
        # (one shared damage roll, full/half per save). Present only when target_ids was passed.
        if aoe_result is not None:
            result["aoe"] = aoe_result
        # AREA SHAPE (F03-4): surface the spell's geometry (Cone/Sphere/Line + size) from the
        # srd524 record so the DM can describe the area and pick who's caught — 52 spells carry
        # it and it was previously never told. Additive; absent when the record has no shape.
        shape_rec = srd or {}
        if shape_rec.get("shape_type"):
            shape = {"type": shape_rec.get("shape_type")}
            if shape_rec.get("shape_size"):
                shape["size"] = shape_rec.get("shape_size")
                shape["unit"] = shape_rec.get("shape_size_unit") or "feet"
            result["shape"] = shape
        # Ritual surfacing (#813): a ritual cast is told to the DM explicitly (no slot
        # was spent); a NORMAL cast of a ritual-tagged spell advertises the slot-free
        # option so the DM learns it exists. Both fields are additive.
        if as_ritual:
            result["ritual_cast"] = True
        elif is_ritual:
            result["ritual_available"] = True
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
            # SYN-06 (#780): tell the DM the buff has ENGINE-APPLIED teeth — which numbers,
            # on whom — so it isn't narrated as flavor and then double-applied by hand.
            if rider_fields:
                result["effect_riders"] = {
                    "holder_id": (
                        rider_child_target.id if rider_child_target is not None
                        else effect_holder.id
                    ),
                    **rider_fields,
                    "note": (
                        "Engine-applied: ac_bonus is folded into the holder's effective AC; "
                        "attack/save bonus dice are rolled automatically on the holder's "
                        "attack and saving throws and itemized in each roll's bonus_dice — "
                        "do NOT add them again by hand."
                    ),
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
            # Damage-cantrip tier scaling (F03-2 / #808): the srd record stores the
            # LEVEL-1 dice, but the note below tells the DM to resolve with these
            # values — so at caster levels 5/11/17 `base_damage` must carry the
            # tier-scaled dice (an L11 Ray of Frost is 3d8, not 1d8). The original
            # die is kept additively in `base_damage_level1`. Eldritch Blast (beam-
            # scaled) and non-scaling cantrips fall back to the verbatim roll + prose.
            base_damage = srd.get("damage_roll") or None
            scaled = spells.scaled_cantrip_damage(srd, ch.total_level)
            if scaled is not None:
                result["base_damage_level1"] = base_damage
                base_damage = scaled
            result["base_damage"] = base_damage
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
            # SAVE-ENDS rider hint (#209): for a "repeats the save at the end of each of
            # its turns, ending on a success" condition spell (Hold Person → paralyzed,
            # Hold Monster), tell the DM exactly which self-enforcing add_condition call to
            # make on a FAILED initial save. Passing repeat_save_ability/dc/source/spell to
            # add_condition wires the ENGINE to roll the recurring save in next_turn and free
            # the target on a success — no manual end-of-turn prompting. Only the unambiguous
            # single-condition pattern surfaces this (spells.repeat_save_rider is conservative).
            rider = spells.repeat_save_rider(srd)
            if rider is not None and target_id and target_id != character_id:
                save_dc = 8 + prof + mod
                # Normalize the ability to the canonical 3-letter code (the Ability enum value
                # next_turn reports + saving_throw expects) — SRD records carry the full word.
                rs_ab = _parse_ability(rider["ability"]).value
                result["condition_rider"] = {
                    "condition": rider["condition"],
                    "repeat_save_ability": rs_ab,
                    "repeat_save_dc": save_dc,
                    "source_id": character_id,
                    "spell_name": canonical,
                    "target_id": target_id,
                    "note": (
                        f"On a FAILED initial {rs_ab} save, apply via "
                        f"add_condition(character_id='{target_id}', condition='{rider['condition']}', "
                        f"repeat_save_ability='{rs_ab}', repeat_save_dc={save_dc}, "
                        f"source_id='{character_id}', spell_name='{canonical}'). The engine then "
                        f"rolls the end-of-turn repeat save in next_turn and frees the target on a "
                        f"success (ending this concentration) — no manual prompting."
                    ),
                }
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
        # F01-14: a spell cast at a living OPPOSING target with NO active combat is the same
        # out-of-initiative loophole as attack() — surface the start_combat nudge (advisory,
        # never a block). Only when there's an explicit hostile target so a buff/heal/utility
        # cast doesn't nag. Mirrors attack()'s combat_not_active cue.
        if not c.combat.active and target_id:
            tgt = c.characters.get(target_id)
            if tgt is not None and not tgt.dead:
                ally_kinds = {"player", "companion"}
                caster_ally = ch.kind in ally_kinds
                tgt_ally = tgt.kind in ally_kinds
                if caster_ally != tgt_ally:
                    result["combat_not_active"] = {
                        "note": (
                            f"{ch.name} cast {canonical} at {tgt.name} with NO active combat — "
                            "the turn-order/economy gates are inert and QA can't see this as a "
                            "fight. If this is a real encounter, call start_combat([…ids…]) "
                            "first. (The spell still resolved — this is advisory.)"
                        ),
                    }
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
    auto_fail, disadvantage = combat.save_modifiers(ch, ab)
    r = dice_mod.roll(f"1d20+{ch.saving_throw_bonus(ab)}", disadvantage=disadvantage)
    # NUMERIC RIDERS (SYN-06 / #780): fold the engine-tracked save bonus dice (Bless +1d4 /
    # Bane -1d4) into the total — the engine rolls the rider it advertises.
    rider_bonus, rider_rolls = _roll_effect_bonus_dice(ch, "save_bonus_dice")
    total = r.total + rider_bonus
    out = {"ability": ab.value, "roll": total, "natural": r.natural, "dc": dc,
           "success": (not auto_fail) and total >= dc}
    if rider_rolls:
        out["bonus_dice"] = rider_rolls
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

        # F01-7: a Grapple is an Attack-action option (2024 Unarmed Strike) — enforce the
        # incapacitation/turn/economy gates BEFORE any roll (inert outside combat). Consumes
        # one attack of the budget.
        _gate_combat_verb(c, attacker, verb="grapple", consumes="attack")

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
            # F01-8: record WHO holds the grapple so move_to_zone won't list the grappler as
            # a provoker against its own captive. Set whenever the target ends up grappled by
            # this attacker (incl. a re-grapple that was already grappled — refresh the holder).
            if not immune:
                target.grappled_by = attacker_id

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

        # F01-7: a Shove is an Attack-action option (2024 Unarmed Strike) — same gate as
        # grapple (incapacitation/turn/economy, before any roll; consumes one attack).
        _gate_combat_verb(c, attacker, verb="shove", consumes="attack")

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

        # F01-7: escaping a grapple uses the escapee's ACTION — gate it (incapacitation/turn/
        # action, before any roll; inert outside combat). Consumes the action.
        _gate_combat_verb(c, escapee, verb="escape a grapple", consumes="action")

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
            escapee.grappled_by = None  # F01-8: the hold is broken — clear the grappler link
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


def _armor_ac_tag(rec: dict) -> str:
    """F09-6 — the AC fragment for an armor/shield catalog record, rendered per the SRD
    DEX-mod rule instead of the bare ``AC {ac_base}`` that misread a Shield as "AC 2" and
    a Breastplate as a flat "AC 14". A shield is a +N BONUS; body armor states its DEX
    contribution (light = + DEX, medium = + DEX (max +N), heavy = no DEX)."""
    ac = rec.get("ac")
    cat = rec.get("armor_category")
    if cat == "shield":
        return f"AC +{rec.get('ac_bonus', ac)} (shield)"
    if not ac:
        return ""
    if cat == "light":
        return f"AC {ac} + DEX"
    if cat == "medium":
        cap = rec.get("ac_dex_cap", 2)
        return f"AC {ac} + DEX (max +{cap})"
    if cat == "heavy":
        return f"AC {ac} (no DEX)"
    return f"AC {ac}"  # unknown/homebrew flat AC — today's behavior


def _catalog_describe(rec: dict) -> str:
    """A one-line description for an inventory item granted from the catalog:
    the SRD prose, prefixed with the mechanical tags (kind/rarity/attunement/
    damage/AC) the bare Item model can't hold as structured fields."""
    tags = [rec["kind"]]
    if rec.get("rarity"):
        tags.append(rec["rarity"])
    if rec.get("damage"):
        tags.append(f"{rec['damage']} {rec.get('damage_type', '')}".strip())
    ac_tag = _armor_ac_tag(rec)
    if ac_tag:
        tags.append(ac_tag)
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


def _catalog_item_stats(rec: dict | None) -> dict | None:
    """F09-7 — extract the structured stats to PERSIST onto a granted Item from a catalog
    record. COPIES every value (the catalog `rec` and its `properties` list are live
    lru-cache references — aliasing them into a saved Character would let a later mutation
    leak across campaigns). Maps the catalog's `cost` → Item.`cost_gp`. Returns None for a
    free-text grant (no resolved record) so the Item keeps its empty-default stats."""
    if rec is None:
        return None
    return {
        "kind": rec.get("kind", "") or "",
        "rarity": rec.get("rarity", "") or "",
        "cost_gp": rec.get("cost"),
        "damage": rec.get("damage", "") or "",
        "damage_type": rec.get("damage_type", "") or "",
        "ac": rec.get("ac"),
        "armor_category": rec.get("armor_category", "") or "",
        "ac_dex_mod": rec.get("ac_dex_mod", "") or "",
        "ac_dex_cap": rec.get("ac_dex_cap"),
        "properties": list(rec.get("properties") or []),  # COPY — never alias the cache list
    }


def _equip_mechanics(ch: Character, item_name: str, equipped: bool) -> Optional[dict]:
    """F09-5 stage 1 — TELL, don't enforce: report the mechanical consequences of
    an equip/unequip so the DM can apply them through the existing writer paths.
    The engine deliberately does NOT auto-write armor_class here: AC effects (e.g.
    Mage Armor) flow through update_character today, and a silent equip-side write
    would fight that path (stage 2 / #806 owns the AC-ownership design). Returns
    None for items with no catalog mechanics (free-text gear) so the response stays
    exactly the pre-existing payload."""
    rec = itemcatalog.resolve(item_name)
    if rec is None:
        return None
    current_ac, _ = _effective_armor_class(ch)
    if rec.get("kind") == "armor" and (rec.get("ac") or rec.get("ac_bonus")):
        dex_mod = ch.ability_modifier(Ability.DEX)
        category = rec.get("armor_category")
        if category == "shield" or (category is None and "shield" in rec["name"].lower()):
            # F09-6: a shield is a +N BONUS on top of the wearer's AC, not a base AC.
            # The SRD smuggles the bonus in as ac_base=2; prefer the structured ac_bonus.
            bonus = int(rec.get("ac_bonus") or rec.get("ac") or 2)
            suggested = current_ac + bonus if equipped else current_ac - bonus
            basis = f"shield {'+' if equipped else '-'}{bonus}"
        elif equipped:
            # F09-6: apply the armor's DEX-mod rule to derive the worn AC directly —
            # light = base + DEX, medium = base + min(DEX, cap), heavy = base flat.
            base = int(rec["ac"])
            if category == "light":
                suggested = base + dex_mod
                basis = f"light armor: base {base} + DEX ({dex_mod:+d})"
            elif category == "medium":
                cap = int(rec.get("ac_dex_cap") or 2)
                applied_dex = min(dex_mod, cap)
                suggested = base + applied_dex
                basis = f"medium armor: base {base} + DEX capped at +{cap} ({applied_dex:+d})"
            elif category == "heavy":
                suggested = base
                basis = f"heavy armor: flat AC {base} (no DEX)"
            else:
                # Unknown/homebrew flat AC — keep today's behavior (bare base AC).
                suggested = base
                basis = f"base AC {base}; apply the armor's DEX-mod rule on top"
        else:
            suggested = 10 + dex_mod
            basis = "unarmored baseline 10 + DEX"
        return {
            "applied": False,
            "current_ac": current_ac,
            "suggested_ac": suggested,
            "ac_delta": suggested - current_ac,
            "note": (
                f"AC does not change on its own ({basis}): call "
                f"update_character(armor_class={suggested}) to apply it"
            ),
        }
    if rec.get("damage"):
        return {
            "applied": False,
            "damage": rec["damage"],
            "damage_type": rec.get("damage_type", ""),
            "note": (
                "weapon stats are not auto-applied: pass damage_dice="
                f"{rec['damage']!r} (plus ability/magic bonuses) and the matching "
                "attack_bonus to attack()"
            ),
        }
    return None


@mcp.tool()
def lookup_item(name: str) -> dict:
    """Look up a single SRD item by name (case-insensitive) in the bundled
    ~960-item catalog (magic items, weapons, armor, gear, potions, etc.). Returns
    the flattened record — {name, kind, rarity, requires_attunement, weight, cost,
    description, properties} plus damage/damage_type for weapons and ac for armor —
    or {"error", "suggestions"} on a miss. `cost` is the listed price in gp, or
    null when the SRD lists no price (every magic item) — null means the DM sets
    the price, NOT that it is free. Use this (then add_item with item_name=...)
    to grant a REAL item instead of free-texting it."""
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
    name, weight, requires_attunement, description, rec = _apply_item_catalog(
        item_name, name, weight, requires_attunement, description
    )
    if not name:
        raise ValueError("add_item needs a name (or an item_name that resolves in the catalog)")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        # F09-7: persist the catalog's structured stats onto the granted Item (#756 root).
        inventory.add_item(ch, name, quantity, weight, requires_attunement, description,
                           stats=_catalog_item_stats(rec))
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
    """Equip an item (or unequip with equipped=False). Equipping is ADVISORY for
    mechanics: for catalog-recognized armor/shields/weapons the response carries a
    `mechanics` block ({suggested_ac, ac_delta, note} or {damage, damage_type,
    note}) — the engine does NOT change armor_class or attacks on its own, so
    apply the AC via update_character(armor_class=...) and pass weapon stats to
    attack()."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        it = inventory.set_equipped(ch, name, equipped)
        save_campaign(c)
        out = it.model_dump()
        mech = _equip_mechanics(ch, it.name, equipped)
        if mech is not None:
            out["mechanics"] = mech
        return out


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
    campaign_id: str, character_id: str, cp: int = 0, sp: int = 0, ep: int = 0, gp: int = 0,
    pp: int = 0, spend_gp: float = 0.0, earn_gp: float = 0.0,
) -> dict:
    """Adjust a character's purse. Two paths (additive — use either or both):

    - DENOMINATION path (cp/sp/ep/gp/pp): add or subtract SPECIFIC coins. Exact, but
      cannot "make change" — subtracting more gp than the character holds in gp coins
      raises, even when the total value is affordable in mixed coin.
    - VALUE path (F09-10): `spend_gp` deducts a gp VALUE making change from the whole
      purse (so 5 gp comes out of 50 sp), and `earn_gp` credits a gp value. Use these to
      spend/earn money WITHOUT itemizing coins — the change-making the denomination path
      can't do. Decimal-exact for 2-decimal gp.

    Raises (and persists nothing) on a negative result; the denomination-underflow error
    points you at `spend_gp` when the value is actually affordable."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        if spend_gp < 0 or earn_gp < 0:
            raise ValueError("spend_gp and earn_gp must be non-negative (use them to spend/earn a value)")
        # VALUE path first: change-making spend/earn over the whole purse (Decimal-exact).
        if earn_gp:
            inventory.gain(ch, earn_gp)
        if spend_gp:
            try:
                inventory.pay(ch, spend_gp)
            except ValueError:
                raise ValueError(
                    f"insufficient funds to spend {spend_gp} gp "
                    f"(purse holds {inventory.total_copper(ch.currency) / 100:g} gp of value)"
                )
        # DENOMINATION path: specific coins, with a change-making hint on underflow (F09-10).
        if cp or sp or ep or gp or pp:
            try:
                inventory.adjust_currency(ch, cp, sp, ep, gp, pp)
            except ValueError:
                have = ch.currency
                raise ValueError(
                    "a coin denomination would go negative "
                    f"(have cp={have.cp} sp={have.sp} ep={have.ep} gp={have.gp} pp={have.pp}); "
                    "to spend a VALUE making change across coins, use spend_gp= instead"
                )
        save_campaign(c)
        return ch.currency.model_dump()


@mcp.tool()
def buy_item(
    campaign_id: str, character_id: str, name: str = "", cost_gp: float = -1.0, quantity: int = 1,
    weight: float = 0.0, requires_attunement: Optional[bool] = None, description: str = "", item_name: str = "",
) -> dict:
    """Buy an item: pay cost_gp PER UNIT x quantity (making change from the purse)
    and add it to inventory. Raises if the character can't afford the total.

    Pass `item_name` to buy a REAL SRD item by name: name, weight, attunement, a
    rich description, AND the catalog price are filled from the bundled catalog
    (leave `cost_gp` unset to charge the SRD price, or pass `cost_gp` to override —
    e.g. a haggled or marked-up price). Items with NO listed SRD price (every magic
    item) require an explicit cost_gp — the DM sets the price (cost_gp=0 only for a
    deliberate free grant; or just use add_item). Omit `item_name` for the original
    free-text path (then `name` and `cost_gp` are required).

    Returns the updated purse + inventory plus {unit_cost_gp, total_cost_gp}."""
    name, weight, requires_attunement, description, rec = _apply_item_catalog(
        item_name, name, weight, requires_attunement, description
    )
    if cost_gp < 0:  # sentinel: caller didn't state a price
        catalog_cost = rec.get("cost") if rec else None
        if catalog_cost is None:
            if rec is not None:  # resolved, but the SRD lists no price (F09-3: priceless != free)
                raise ValueError(
                    f"{rec['name']!r} has no listed price in the SRD catalog — pass an "
                    "explicit cost_gp (the DM sets the price; cost_gp=0 only if deliberately free)"
                )
            raise ValueError("buy_item needs cost_gp (or an item_name that resolves in the catalog)")
        cost_gp = catalog_cost
    if not name:
        raise ValueError("buy_item needs a name (or an item_name that resolves in the catalog)")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    total_cp = inventory.gp_to_cp(cost_gp) * int(quantity)  # F09-2: unit x qty, copper-exact
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        inventory.pay_cp(ch, total_cp)
        # F09-7: a bought catalog item persists its structured stats too (#756 root).
        inventory.add_item(ch, name, quantity, weight, requires_attunement, description,
                           stats=_catalog_item_stats(rec))
        save_campaign(c)
        return {
            "currency": ch.currency.model_dump(),
            "inventory": [i.model_dump() for i in ch.inventory],
            "unit_cost_gp": float(cost_gp),
            "total_cost_gp": total_cp / 100,
        }


def _sell_price_reference(name: str, ch: Character) -> Optional[float]:
    """F09-9 — the listed price of the item being sold, for sell-price sanity. Prefers the
    structured cost_gp persisted on the OWNED item (F09-7) so a haggled/custom grant keeps
    its real value; falls back to the SRD catalog by name. None == no reference price."""
    owned = next(
        (it for it in ch.inventory if it.name.lower() == name.lower()
         and getattr(it, "cost_gp", None) is not None),
        None,
    )
    if owned is not None:
        return float(owned.cost_gp)
    rec = itemcatalog.resolve(name)
    if rec and rec.get("cost") is not None:
        return float(rec["cost"])
    return None


@mcp.tool()
def sell_item(campaign_id: str, character_id: str, name: str, price_gp: float, quantity: int = 1) -> dict:
    """Sell an item: remove `quantity` of it and add price_gp PER UNIT x quantity to the
    purse. Returns the updated purse + inventory plus {unit_price_gp, total_price_gp}.

    F09-9 — price sanity (TELL, don't block by default): the response carries
    `catalog_cost_gp` (the item's listed/owned reference price, or null) and, when the sale
    price is implausibly above it, a `warning`. The DM sees list price in-context at the
    moment of sale. House-rule `enforce_sell_cap` (off by default) turns the rail hard:
    a price above `sell_cap_multiple`× the reference is then REJECTED instead of warned."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if price_gp < 0:
        raise ValueError("cannot gain a negative amount")
    total_cp = inventory.gp_to_cp(price_gp) * int(quantity)  # F09-2 mirror: unit x qty
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        # F09-9: resolve the reference price BEFORE the item is removed (the owned record
        # is gone afterwards) so the warning/cap can compare against it.
        ref = _sell_price_reference(name, ch)
        warning = None
        if ref is not None and ref > 0:
            cap = float(c.house_rules.sell_cap_multiple)
            if price_gp > ref * cap:
                msg = (
                    f"sell price {price_gp} gp is {price_gp / ref:.1f}× the listed "
                    f"{ref} gp for {name!r} (cap {cap:.1f}×)"
                )
                if c.house_rules.enforce_sell_cap:
                    raise ValueError(
                        msg + " — enforce_sell_cap is on; lower the price or disable the cap"
                    )
                warning = msg + " — selling above list (TELL only; no buy-back economy in SRD)"
        inventory.remove_item(ch, name, quantity)
        inventory.gain_cp(ch, total_cp)
        save_campaign(c)
        out = {
            "currency": ch.currency.model_dump(),
            "inventory": [i.model_dump() for i in ch.inventory],
            "unit_price_gp": float(price_gp),
            "total_price_gp": total_cp / 100,
            "catalog_cost_gp": ref,  # the reference list price (or null)
        }
        if warning:
            out["warning"] = warning
        return out


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
        # ONE-LONG-REST-PER-DAY GUARD (SRD 5.2: at most one long rest per 24h). A long
        # rest landing on a morning costs zero clock time (steps == 0), so without this a
        # party could re-rest 6x at dawn and clear exhaustion 6->0 for free. Refuse a second
        # long rest the same calendar day the character last finished one — checked at ENTRY,
        # BEFORE rests.long_rest mutates, so a blocked call changes NO state (F04-3). The stamp
        # (set after the clock advance below) records the day the rest landed; this compares it
        # to today's `day`. Per-character so a party still converges on a single dawn: each
        # member gets their one rest, and only a REPEAT by the same member that day is blocked.
        if ch.last_long_rest_day == c.day:
            return {
                "ok": False,
                "error": (f"{ch.name} has already taken a long rest today (day {c.day}); a "
                          f"character can benefit from only one long rest per day. Advance the "
                          f"clock (travel / advance_time / downtime) before resting again."),
                "day": c.day,
                "time_of_day": c.time_of_day,
            }
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
        # F04-3: stamp the day this rest LANDED on (the morning it ended) so a second long
        # rest the same calendar day is refused by the entry guard above. Mutate the
        # re-validated copy that is persisted (not the pre-validation `ch`).
        c.characters[character_id].last_long_rest_day = c.day
        # An overnight (~8h) ends timed spell effects: minute/round-scale, hour/day-scale
        # past their deadline, AND every hour-scale buff (Mage Armor/Aid/Longstrider) via
        # long_rest=True — even when resting in the morning is a clock no-op (steps == 0).
        expired = _expire_clock_effects_all(c, long_rest=True)
        # F04-4: a long rest ends concentration. The clock sweep above already drops a
        # TWINNED concentration effect (via _commit_expiry), but a DEGRADED-path concentration
        # (a duration-less concentration spell sets ch.concentration without an effect twin —
        # server.py cast_spell) survives the sweep. Clear it on the RESTER (others' buffs are
        # the clock sweep's job) and surface the released effect names so the DM narrates it.
        rester = c.characters[character_id]
        if rester.concentration is not None:
            for name in combat.expire_concentration_effects(rester):
                expired.append({"character_id": rester.id, "name": name})
            rester.concentration = None
        out["expired_effects"] = expired
        # F04-7: a long rest rolls the day over but every OTHER day-moving seam (travel_to,
        # advance_time, downtime) also ticks the world. Without this the overnight's standing
        # threads / proactive backlog / strategic clock land LATE (at the next tick-bearing
        # seam, mid-next-leg) instead of "the world moved while you slept". Gate on the same
        # once-per-overnight `steps > 0` so a per-member party rolls ONCE; idempotent by
        # elapsed days so a second member's morning rest is a no-op (no double-advance).
        if steps > 0:
            out["world_beats"] = [b.text for b in worldsim.tick(c, max_beats=2)]
            out["world_developments"] = [_backlog_line(d) for d in worldsim.tick_backlog(c, max_events=2)]
            out["strategic_events"] = worldsim.tick_strategic(c)
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
                # F04-1: resolve the camp-watch danger off the camp location's region +
                # name + tags (a city camp is a guarded inn, not a wilderness bivouac).
                match_region=_composite_region_match(cur_loc),
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
def use_resource(
    campaign_id: str,
    character_id: str,
    resource: str,
    amount: int = 1,
    maneuver: str = "",
    damage_type: str = "",
) -> dict:
    """Spend from a depletable class-resource pool (Rage, Ki, Lay on Hands, Channel
    Divinity, Bardic Inspiration, Sorcery Points, Second Wind, Action Surge, Wild
    Shape, …). Deducts `amount` (default 1; for Lay on Hands pass the hit points to
    spend). Returns ``{ok: True, remaining, max, used}`` on success; ``{ok: False,
    error, remaining, max}`` without changing state when the character lacks that
    pool or hasn't enough left, so the DM gets a clean signal instead of an
    exception. Pools refresh via short_rest / long_rest.

    DAMAGE MANEUVER (Battle Master, #213): pass ``maneuver`` (the maneuver's name, e.g.
    "Trip Attack" / "Menacing Attack") when the spent die is a DAMAGE maneuver — "add the
    superiority die to the attack's damage roll." The engine ROLLS the pool's die (`amount`
    × the resource's `size`, e.g. 1d8) at spend time and stashes the rolled total as a
    PENDING damage bonus on the character; the NEXT ``attack()`` by this character folds it
    into that strike's damage and clears it (consumed once — never double-applied), so the
    maneuver's damage is REAL without the DM remembering to add it. The rolled die + bonus
    are surfaced under ``maneuver_damage``. ``damage_type`` optionally types the added
    damage (default "" == the same type as the weapon strike). A maneuver only makes sense
    for a die pool: spending a POINT pool (no `size`) for a maneuver is refused with a clean
    signal and NO state change. The maneuver's save/condition (Trip → prone, Menacing →
    frightened) stays a separate DM ``saving_throw`` call — only the DAMAGE bonus is wired
    here. ADDITIVE: omit ``maneuver`` (the default) and a spend is byte-identical to before
    — no pending bonus is ever set, so a non-maneuver pool behaves exactly as today."""
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
        # A DAMAGE maneuver adds the spent die to the next attack's damage — so the pool MUST
        # roll a die. Refuse a maneuver against a point pool (no `size`) up front, BEFORE
        # spending, so the DM gets a clean signal instead of a silently-wasted point with no
        # bonus (and no phantom pending record gets written). Inert when `maneuver` is empty.
        man = maneuver.strip()
        if man and not res.size.strip():
            return {
                "ok": False,
                "error": (
                    f"{resource!r} is a point pool (no die) — a damage maneuver needs a "
                    f"die pool like Superiority Dice; nothing spent"
                ),
                "resource": resource,
                "remaining": remaining,
                "max": res.max,
            }
        res.used += amount
        man_damage = None
        if man:
            # Roll the spent die(s) NOW (engine-rolls-and-tells, like an on-hit rider): one
            # source of truth — the result is fixed at declare time, and the next attack just
            # reads it. `amount` × the pool's die size (1 die per maneuver in 5e RAW, so this
            # is 1d8 for the default Superiority Die).
            die_expr = f"{amount}{res.size.strip()}"
            roll = dice_mod.roll(die_expr)
            bonus = max(0, roll.total)
            ch.pending_damage_bonus = PendingDamageBonus(
                amount=bonus,
                source=man,
                resource=resource,
                expr=die_expr,
                detail=roll.detail,
                damage_type=damage_type.strip(),
            )
            man_damage = {
                "maneuver": man,
                "die": die_expr,
                "rolled": bonus,
                "detail": roll.detail,
                "damage_type": damage_type.strip(),
                "applies_to": "next attack's damage",
            }
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
        out = {
            "ok": True,
            "resource": resource,
            "spent": amount,
            "remaining": new.max - new.used,
            "max": new.max,
            "used": new.used,
            "recharge": new.recharge,
        }
        if man_damage is not None:
            out["maneuver_damage"] = man_damage
        return out


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


def _canonicalize_spell_list(spells_list: list, existing: list, mode: str) -> list:
    """Validate + canonicalize a learn/prepare spell list (F03-7). Each entry must be a
    known SRD spell (any casing); unknown entries raise listing ALL of them, so the cast
    gate compares the proper-cased name against proper-cased stored names. `mode='replace'`
    (default) substitutes the list; `mode='add'` appends the new spells to `existing`
    (de-duped, canonical-cased, order-preserving). Raises ValueError on an unknown name or
    an unrecognized mode — rejection BEFORE any state change, like every engine write."""
    if mode not in ("replace", "add"):
        raise ValueError(f"mode must be 'replace' or 'add', got {mode!r}")
    canonical: list[str] = []
    unknown: list[str] = []
    for raw in spells_list:
        name = spells.canonical_name(str(raw))
        if name is None:
            unknown.append(str(raw))
        else:
            canonical.append(name)
    if unknown:
        raise ValueError(
            "unknown spell(s) (not in the SRD): "
            + ", ".join(repr(u) for u in unknown)
            + " — check spelling; only SRD spells can be learned/prepared"
        )
    if mode == "add":
        result = list(existing)
        have = {s.strip().lower() for s in existing}
        for name in canonical:
            if name.strip().lower() not in have:
                result.append(name)
                have.add(name.strip().lower())
        return result
    # replace: de-dupe the incoming list (keep first-seen canonical casing/order)
    seen: set[str] = set()
    deduped: list[str] = []
    for name in canonical:
        if name.strip().lower() not in seen:
            deduped.append(name)
            seen.add(name.strip().lower())
    return deduped


@mcp.tool()
def learn_spells(campaign_id: str, character_id: str, spells_list: list,
                 mode: str = "replace") -> dict:
    """Set a character's KNOWN spells. Each name is VALIDATED + CANONICALIZED (F03-7): an
    unknown spell (typo, non-SRD) is rejected listing the offenders, and any casing you pass
    ("fire bolt") is stored proper-cased ("Fire Bolt") so the case-sensitive cast gate accepts
    a later cast. `mode='replace'` (default) substitutes the whole list; `mode='add'` appends
    the new spells to the existing known list (de-duped) — so you can teach one spell without
    re-listing the whole spellbook. Rejection happens BEFORE any change (nothing is stored on
    an unknown name)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.spells_known = _canonicalize_spell_list(spells_list, ch.spells_known, mode)
        save_campaign(c)
        return {"spells_known": ch.spells_known}


@mcp.tool()
def prepare_spells(campaign_id: str, character_id: str, spells_list: list,
                   mode: str = "replace") -> dict:
    """Set a character's PREPARED spells. Each name is VALIDATED + CANONICALIZED (F03-7) like
    learn_spells. With a non-empty prepared list, cast_spell now enforces preparation for
    LEVELED spells (F03-8): a prepared caster (cleric/wizard/druid) casts only what it has
    prepared, while cantrips stay always-castable once known. `mode='replace'` (default)
    substitutes the list (your daily preparation); `mode='add'` appends. An unknown spell is
    rejected before any change."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        ch.spells_prepared = _canonicalize_spell_list(spells_list, ch.spells_prepared, mode)
        save_campaign(c)
        return {"spells_prepared": ch.spells_prepared}


def _clamp_attitude(value: int) -> int:
    """Keep a numeric per-NPC relationship within the -100..+100 scale (0 = neutral)."""
    return max(-100, min(100, int(value)))


@mcp.tool()
def set_attitude(
    campaign_id: str, character_id: str = "", attitude: str = "", value: int | None = None,
    target_id: str = "", npc_id: str = "", id: str = ""
) -> dict:
    """Set an NPC's attitude (free text, e.g. 'guarded', or a track value:
    hostile / wary / indifferent / friendly / helpful).

    Pass `value` (-100..+100, 0 = neutral) to ALSO set the numeric per-NPC
    relationship the dashboard bar reads; omit it to leave the number untouched
    (the free-text track keeps working exactly as before).

    Identify the NPC via ``character_id`` (canonical) or the aliases ``target_id`` /
    ``npc_id`` / ``id`` — ``character_id`` wins if more than one is given."""
    character_id = character_id or target_id or npc_id or id  # accept the id the DM reaches for
    if not character_id:
        raise ValueError("set_attitude needs a character (pass `character_id` or an alias: `target_id`/`npc_id`/`id`)")
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
def adjust_attitude(campaign_id: str, character_id: str = "", delta: int = 0,
                    target_id: str = "", npc_id: str = "", id: str = "") -> dict:
    """Nudge an NPC's numeric relationship (`attitude_value`) by `delta`, clamped to
    -100..+100. For the DM to reward a kindness or punish a betrayal directly, outside
    a social check. Leaves the free-text `attitude` track unchanged.

    Identify the NPC via ``character_id`` (canonical) or the aliases ``target_id`` /
    ``npc_id`` / ``id`` — ``character_id`` wins if more than one is given."""
    character_id = character_id or target_id or npc_id or id  # accept the id the DM reaches for
    if not character_id:
        raise ValueError("adjust_attitude needs a character (pass `character_id` or an alias: `target_id`/`npc_id`/`id`)")
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
def remember(campaign_id: str, character_id: str, fact: str = "", text: str = "") -> dict:
    """Append a fact to a character's (usually an NPC's) persistent memory, so it
    is recalled in later sessions. Pass the fact as ``fact`` (canonical) or ``text``
    (alias) — they are equivalent; ``fact`` wins if both are given."""
    fact = fact if fact else text  # `text` is an accepted alias for the canonical `fact`
    if not fact:
        raise ValueError("remember needs a fact (pass `fact` or its alias `text`)")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        ch = _char(c, character_id)
        if fact not in ch.memory:  # de-dupe identical facts
            ch.memory.append(fact)
        save_campaign(c)
        return {"id": ch.id, "name": ch.name, "memory": ch.memory}


@mcp.tool()
def forget(campaign_id: str, character_id: str, fact: str = "", text: str = "") -> dict:
    """Remove a remembered fact (exact match) from a character's memory. Pass the fact as
    ``fact`` (canonical) or ``text`` (alias) — equivalent, mirroring ``remember``; ``fact``
    wins if both are given."""
    fact = fact if fact else text  # `text` is an accepted alias for the canonical `fact` (mirrors remember)
    if not fact:
        raise ValueError("forget needs a fact (pass `fact` or its alias `text`)")
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

# Surfaced in the RETURN payload of any FAILED contested check (skill_check / social_check
# with a DC). Storycraft's #1 scored lever: when a player check fails, the scene must not
# resolve through an NPC's action or a narrated freebie — the protagonist becomes a
# bystander to his own beat. The skill prose already says this and the DM still does it
# every session; surfacing the directive in the tool result the DM is already reading is
# the proven channel (the same move that lifted combat fidelity). Not a schema change to
# the roll — a guidance field the DM cannot miss at the exact moment of the miss.
_ON_FAILURE_DIRECTIVE = {
    "directive": (
        "A failed check must COST or COMPLICATE and then HAND THE TURN BACK to the "
        "player — it does not end the beat. Narrate what the failure changes (a new "
        "obstacle, a price, a closing door), then ask the player what they do. Do NOT "
        "resolve this obstacle via an NPC's action, and do NOT narrate the PC's next "
        "move for them."
    ),
    "forbid": "npc_resolves_scene",
}


@mcp.tool()
def social_check(campaign_id: str, actor_id: str, npc_id: str = "", skill: str = "", dc: int = 0,
                 target_name: str = "", target_id: str = "", character_id: str = "",
                 id: str = "", ability: str = "", skill_name: str = "", check: str = "") -> dict:
    """An actor's skill check against a tracked NPC, monster, or COMPANION, with
    read-vs-influence semantics.

    The target (``npc_id``) may be an NPC, a monster, or a COMPANION — a party member is
    a legitimate social target (persuade/intimidate/read a companion); it moves the SAME
    attitude/approval track an NPC uses (the gauge companion arcs evaluate). Only a PLAYER
    character cannot be the target. Identify the target via ``npc_id`` (canonical) or any
    of the aliases ``target_id`` / ``character_id`` / ``id`` — equivalent; ``npc_id`` wins
    if more than one is given. Name the skill via ``skill`` (canonical) or the aliases
    ``ability`` / ``skill_name`` / ``check``.

    INFLUENCE skills (persuasion / deception / intimidation / …) try to move the
    target: on success the attitude improves one step on the track (hostile -> wary ->
    indifferent -> friendly -> helpful), on failure it worsens one step.

    READ skills (insight / perception / investigation) only PERCEIVE the target and
    NEVER change its attitude — reading or misreading someone is observer clarity,
    not influence. A read returns a `read` block (an accurate sense of the target's
    stance on success; a deliberately uncertain, almost-grasped impression on a
    miss) for the DM to narrate — never a flat attitude penalty for a failed read.

    For a SCENE-LOCAL EXTRA you won't track — a fishmonger, a gate guard, a barkeep —
    pass ``npc_id=""`` and ``target_name="the fishmonger"``: the engine rolls the check
    and returns pass/fail WITHOUT creating or mutating any roster NPC. Use a real
    ``npc_id`` ONLY when you mean to move a tracked NPC's relationship — reusing a
    standing NPC's id as a throwaway target silently corrupts their attitude across the
    whole campaign (QA: a Deception vs a dock extra accidentally shifted a seeded
    companion's standing because her id was passed as the target)."""
    # Coalesce intuitive arg-name aliases to the canonical params BEFORE any branching.
    # `npc_id` (canonical) wins; the id MUST resolve before the ephemeral/target_name path
    # below, or an alias-only call would wrongly take the scene-extra branch (npc_id="").
    npc_id = npc_id or target_id or character_id or id  # accept the id the DM reaches for
    skill = skill or ability or skill_name or check  # match skill_check's accepted aliases
    if not skill:
        raise ValueError(
            "social_check needs a skill (pass `skill` or an alias: `ability`/`skill_name`/`check`)"
        )
    if not npc_id and not target_name.strip():
        raise ValueError(
            "social_check needs a target: pass `npc_id` (a tracked NPC/monster/companion; "
            "aliases `target_id`/`character_id`/`id`) or `target_name` (a scene extra)"
        )
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
            is_read_ext = sk in READ_SKILLS
            out_ext = {
                "actor": actor.name,
                "npc": target_name.strip(),
                "skill": sk,
                "kind": "read" if is_read_ext else "influence",
                "roll": r.total,
                "natural": r.natural,
                "dc": dc,
                "success": r.total >= dc,
                "ephemeral": True,
                "note": "Scene extra — nothing persisted; narrate the outcome.",
            }
            # A failed INFLUENCE attempt (persuade/intimidate/deceive) must still hand the
            # turn back to the player, not get resolved by the extra or a freebie. A failed
            # READ already returns its own non-resolving "almost-grasped" guidance, so the
            # agency directive only attaches to influence misses (the agency-snap domain).
            if not out_ext["success"] and not is_read_ext:
                out_ext["on_failure"] = _ON_FAILURE_DIRECTIVE
            return out_ext
        if actor_id == npc_id:
            raise ValueError("actor and npc must be different characters")
        the_npc = _char(c, npc_id)
        # A companion IS a valid social target: persuading/intimidating/reading a party
        # member moves the SAME attitude/attitude_value track an NPC uses (it's the approval
        # gauge companion arcs already evaluate), so the influence/read logic below applies
        # unchanged. Only a PLAYER (the actor's own party-of-one PCs) is not a social target.
        if the_npc.kind not in ("npc", "monster", "companion"):
            raise ValueError("social_check target must be an NPC, monster, or companion")
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
        # A failed INFLUENCE check must not be resolved by the NPC or a narrated freebie —
        # hand the turn back to the player (storycraft's #1 scored lever). A failed READ
        # already carries its own non-resolving guidance (the `read` note above), so the
        # directive attaches only to influence misses.
        if not success and not is_read:
            out["on_failure"] = _ON_FAILURE_DIRECTIVE
        return out


@mcp.tool()
def skill_check(campaign_id: str, character_id: str, skill: str = "", dc: int = 0,
                advantage: bool = False, disadvantage: bool = False,
                ability: str = "", skill_name: str = "", check: str = "") -> dict:
    """Roll a skill check for a character with the CORRECT modifier derived from their sheet
    (ability modifier + proficiency if proficient, doubled where they have expertise) — so you
    NEVER hand-compute a bonus, the most common mechanical error. Use this for any non-social
    check (Perception, Investigation, Stealth, Athletics, Arcana, …) against a DC, or just to see
    the roll (``dc=0`` -> roll only, no pass/fail). For a check that targets an NPC's attitude
    (persuade / intimidate / read someone), use ``social_check`` instead. 5e RAW: a skill check
    does NOT auto-succeed on a natural 20 — success is total vs DC; the natural is reported so you
    can flavor a 20/1. Read-only (a check is a roll, not a state change).

    Name the skill via ``skill`` (canonical) or any of the aliases ``ability`` / ``skill_name`` /
    ``check`` — they are equivalent; the canonical ``skill`` wins if more than one is given."""
    skill = skill or ability or skill_name or check  # accept intuitive aliases; `skill` is canonical
    if not skill:
        raise ValueError("skill_check needs a skill (pass `skill` or an alias: `ability`/`skill_name`/`check`)")
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
        if not out["success"]:
            out["on_failure"] = _ON_FAILURE_DIRECTIVE  # keep the player's agency on a miss
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
    event_id: str = "",
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
    (and optional ``adjust_reputation``). Read-only.

    Quest & Arc engine, Layer 3 — `event_id` (OPTIONAL): when a first-class stumble-into Event
    is live (from ``present_events``), pass its id to attach the Event's AUTHORED options to the
    surface as ``event: {id, prompt, options:[{label, tag, skill, dc}]}``. The Event's options
    are the menu slots; the free-form path is STILL always present (never a closed set). A picked
    Event option routes to ``resolve_event(campaign_id, event_id, option_label)`` (the engine
    applies its deterministic ripple) — not to skill_check/record_decision. An unknown
    `event_id` or one that is already resolved simply omits the block (degrades to today's
    freeform parley)."""
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
    # Quest & Arc engine, Layer 3: when a live Event is named, attach its authored options as
    # the menu slots (the free-form path above stays). A resolved/unknown Event omits the block,
    # degrading to today's freeform parley. resolve_event applies a picked option's ripple.
    if event_id:
        ev = c.events.get(event_id)
        if ev is not None and not ev.resolved:
            out["event"] = {
                "id": ev.id,
                "prompt": ev.prompt,
                "anchor_npc_id": ev.anchor_npc_id,
                "options": [
                    {"label": opt.label, "tag": opt.tag, "skill": opt.skill, "dc": opt.dc}
                    for opt in ev.options
                ],
                "resolve_with": "resolve_event",
            }
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


def _attitude_band(attitude_value: int) -> str:
    """Map the numeric approval gauge (-100..+100, 0 = neutral) to a single band label
    from the canonical attitude vocabulary (npc.ATTITUDE_TRACK), so companion_advise can
    tell the DM the companion's CURRENT leaning. A pure read of the gauge only — the same
    five bands a social_check moves a companion through, just keyed off the number instead
    of the free-text track. Cutoffs are deterministic and centered on 0 (neutral)."""
    if attitude_value <= -50:
        return "hostile"
    if attitude_value <= -15:
        return "wary"
    if attitude_value < 25:
        return "indifferent"
    if attitude_value < 60:
        return "friendly"
    return "helpful"


@mcp.tool()
def companion_advise(campaign_id: str, companion_id: str, situation: str = "") -> dict:
    """Get the companion's in-character take on the CURRENT (non-combat) moment so
    the DM voices it reliably — the storytelling default, not an afterthought. Pass
    a short `situation` (the choice/discovery/lull at hand); it pulls relevant
    memory callbacks via recall and returns the companion's voice_id + personality
    + callbacks + a prompt to voice from. Speak the companion's line in its voice,
    then let the player respond / deliberate with it. Read-only.

    Also folds in the companion's STANDING (the approval band derived from its gauge),
    its dossier's `approval_likes`/`approval_dislikes` (so the DM can judge whether THIS
    moment wins or loses the companion's regard), and an `arc` summary (gates + how far
    the next locked gate is) — so the voiced line already carries the relationship,
    instead of a blank-neutral opinion. The engine reads the gauge; the DM judges the
    cause (it never auto-moves approval here). Best-effort: a companion with no dossier/
    arc still returns the base frame (F06-3)."""
    c = _require(campaign_id)
    comp = _char(c, companion_id)
    callbacks = ledger_mod.recall(campaign_id, situation, limit=3) if situation.strip() else []
    # F06-3: the standing band is a pure read of the engine-mutated approval gauge.
    standing = {"band": _attitude_band(comp.attitude_value),
                "attitude_value": comp.attitude_value}
    out = companion.deliberate(
        comp, situation, callbacks=callbacks,
        standing=standing,
        dossier=getattr(comp, "companion_dossier", None),
    )
    # Arc/gate-distance summary (same read _camp_arc_summary gives camp); None when no arc.
    out["arc"] = _camp_arc_summary(comp)
    return out


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
        # F06-10: surface this companion's personal QUEST ARCs at camp too — a non-locked
        # arc/stage is a ripe personal-quest beat to play in the camp round. Pure read; absent
        # when the companion owns none (today's solo-beat shape unchanged).
        quest_arcs = [
            {
                "id": a.id,
                "title": a.title,
                "status": a.status,
                "open_stages": [
                    {"id": s.id, "title": s.title, "status": s.status}
                    for s in a.stages if s.status != "locked"
                ],
            }
            for a in sorted(c.companion_quest_arcs.values(), key=lambda a: (a.title, a.id))
            if a.companion_id == comp.id
        ]
        if quest_arcs:
            out["quest_arcs"] = quest_arcs
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
    # F06-5: gather EVERY living companion (incl. de-facto companions not in c.party) via the
    # shared scheduler helper — camp was the one seam that gated on c.party while relocate/XP
    # include any kind=='companion'. One source of truth for "who's at camp".
    companions = companion_banter._living_companions(c)
    sit = setting.strip() or "an unpressured moment in camp — the day's danger behind you, the fire low"
    # F06-5 leg (c): a flat max_beats=len(companions) STARVED pair banter — solo priorities
    # (50-90) always outrank pairs (40+len(tags) <= 43), so every pair sorted past the cut.
    # Budget room for each companion's solo AND a bounded slate of the top pair beats so the
    # camp round can actually reveal cross-companion tension/warmth. C(n,2) pairs exist; cap the
    # pair allowance at len(companions) so a big party doesn't dump every pairing in one scene.
    n = len(companions)
    pair_budget = min(n * (n - 1) // 2, n) if n >= 2 else 0
    scheduled = companion_banter.schedule_camp_beats(c, max_beats=n + pair_budget)
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
        out = {"session_id": sid, "number": len(c.session_ids), "previously_on": previously}
        # F08-3: if this campaign's snapshot needed the TOLERANT load (unknown top-level keys from
        # a newer/older schema were dropped), SURFACE the dropped key names on the resume path so
        # schema-evolution data-loss is visible at the table — not just buried in a log line. The
        # original bytes are recoverable from campaigns/<id>/snapshot.pre-tolerant.json.
        drift = last_dropped_keys(campaign_id)
        if drift:
            out["schema_drift"] = {
                "dropped_keys": drift,
                "note": (
                    "This save was written by a different engine schema; the listed top-level "
                    "field(s) were dropped on load. The original snapshot is preserved at "
                    "snapshot.pre-tolerant.json."
                ),
            }
        return out


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
        # Reward backstop: a session that genuinely advanced must not close at 0 XP in
        # xp-mode (QA: real social/exploration wins, party still at 0 — a broken reward
        # loop the scorer docks). Only fires when (a) xp-mode, (b) at least one living party
        # member is still at 0 XP, and (c) the session advanced (clock moved past the
        # opening phase OR >1 location visited). Modest, deterministic; the session was
        # already cleared above, so it can only fire once per session close.
        #
        # PER-MEMBER top-up (QA ow-fixD): the DM often uses the single-target award_xp on the
        # PC only, so a companion who fought all session closes at 0 while the PC banked XP.
        # The old all-zero guard could not rescue that (the PC already had XP -> guard False).
        # So: if the session advanced and ANY living member is at 0 while the party has earned
        # XP, top those individuals up to the party's max XP (level them together). Only ever
        # RAISES a 0-XP member to parity, never lowers anyone. When NO ONE earned XP (the whole
        # party is at 0) fall back to the original modest milestone grant.
        award = None
        if c.leveling_mode == "xp":
            # The travelling group (PC + de-facto companions) shares the parity top-up, #353 —
            # a companion that fought all session but was never added to c.party closed at 0
            # while the PC banked XP; the relocate sweep already walks it, so the reward
            # backstop must too.
            living = [c.characters[i] for i in _party_xp_recipients(c)]
            advanced = (c.day > 1 or c.time_of_day != "morning"
                        or sum(1 for loc in c.locations.values() if loc.visited) > 1)
            if living and advanced:
                target = max((ch.xp for ch in living), default=0)
                zeros = [ch for ch in living if ch.xp == 0]
                if target > 0 and zeros:
                    for ch in zeros:
                        ch.xp = target
                    award = {
                        "xp_awarded": target * len(zeros),
                        "grants": [
                            {
                                "id": ch.id, "name": ch.name, "xp": ch.xp,
                                "level_available": srd_tables.level_for_xp(ch.xp),
                                "can_level_up": srd_tables.level_for_xp(ch.xp) > ch.total_level,
                            }
                            for ch in zeros
                        ],
                        "reason": "session close: companion XP parity",
                    }
                elif all(ch.xp == 0 for ch in living):
                    award = _award_milestone_xp(c, 100 * max(_party_levels(c)), "session close")
        save_campaign(c)
        out = {"ended": sid, "number": len(c.session_ids)}
        if award is not None:
            out["xp_awarded"] = award["xp_awarded"]
            out["grants"] = award["grants"]
        return out


@mcp.tool()
def save_slot(campaign_id: str, slot: str = "quicksave") -> dict:
    """Copy the campaign's CURRENT state into a named save slot (default 'quicksave').

    A slot is a point-in-time anchor the player can return to (e.g. before a risky fight or a
    fork). It copies the live snapshot verbatim — non-destructive, the live game is untouched —
    so a later load_slot can roll the chronicle back to exactly this moment. Pairs with load_slot.
    Raises if the campaign has no saved state yet. The engine is the sole writer; runs under the
    campaign lock so it can't race a concurrent tool call."""
    with campaign_lock(campaign_id):
        _require(campaign_id)  # 404 cleanly on an unknown campaign
        path = _save_slot_store(campaign_id, slot)
        return {"ok": True, "campaign_id": campaign_id, "slot": slot, "path": str(path)}


@mcp.tool()
def load_slot(campaign_id: str, slot: str = "quicksave") -> dict:
    """Restore a named save slot, OVERWRITING the campaign's current live state with it.

    This rolls the whole chronicle back to the moment the slot was written (default 'quicksave').
    DESTRUCTIVE to unsaved progress: anything that happened since that slot is discarded. The slot
    is validated as belonging to this campaign before it is restored, so a corrupt/foreign snapshot
    can never clobber the live game. The engine is the sole writer; runs under the campaign lock,
    and the very next tool call re-loads the restored state. Pair with save_slot. Raises if the
    slot is absent, corrupt, or for a different campaign."""
    with campaign_lock(campaign_id):
        _require(campaign_id)  # the campaign must exist to be restored over
        c = _load_slot_store(campaign_id, slot)
        loc = c.locations.get(c.current_location_id) if c.current_location_id else None
        return {
            "ok": True,
            "campaign_id": campaign_id,
            "slot": slot,
            "title": c.title,
            "day": c.day,
            "time_of_day": c.time_of_day,
            "current_location": loc.name if loc else None,
            "note": "Live state was rolled back to this slot. Call get_state / session_recap to re-ground before narrating.",
        }


@mcp.tool()
def list_slots(campaign_id: str) -> dict:
    """List a campaign's named save slots (slot name + last-saved time), newest first.
    Read-only — shows what restore points exist (e.g. the 'quicksave') without touching state."""
    return {"campaign_id": campaign_id, "slots": _list_slots(campaign_id)}


@mcp.tool()
def log_event(
    campaign_id: str,
    kind: str,
    text: str = "",
    speaker: Optional[str] = "",
    payload: Optional[dict] = None,
    message: str = "",
    content: str = "",
    note: str = "",
) -> dict:
    """Record a story beat in the current session log (kind: narration | dialogue
    | roll | system | combat). Auto-starts a session if none is active. Powers
    recaps and post-compaction recovery.

    Pass the beat as ``text`` (canonical) or any of the aliases ``message`` / ``content`` /
    ``note`` — ``text`` wins if more than one is given."""
    text = text or message or content or note  # accept the text the DM reaches for
    if not text:
        raise ValueError("log_event needs text (pass `text` or an alias: `message`/`content`/`note`)")
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
def add_consequence(campaign_id: str, in_days: int = 0, text: str = "", note: str = "",
                    message: str = "", content: str = "") -> dict:
    """Schedule a time-deferred world event to come due `in_days` from now (the
    in-world Campaign.day). Use it whenever the present sets up the future — a
    ritual that completes in 3 days, a spared villain who returns in a week, a
    siege that arrives, a debt called in. `check_consequences` surfaces them when
    the day arrives. This is how the world keeps moving between adventures.

    Pass the event as ``text`` (canonical) or the aliases ``message`` / ``content`` —
    ``text`` wins if more than one is given. (``note`` is a SEPARATE optional field, not an
    alias.)"""
    text = text or message or content  # accept the text the DM reaches for (NOT `note` — distinct field)
    if not text:
        raise ValueError("add_consequence needs text (pass `text` or an alias: `message`/`content`)")
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
def set_companion_arc(campaign_id: str, companion_id: str = "", arc: dict = None,
                      companion: str = "", character_id: str = "") -> dict:
    """Attach (or REPLACE) a companion's relationship arc + sealed agenda, so the DM —
    or the ending-seed loader — can author what a bond grows into and what a saboteur is
    planning. `arc` is `{arc_gates: [{kind, threshold, note?}], agenda: {trigger, value?,
    note?}}` where gate `kind` is personal_quest|romance|loyalty|betrayal and agenda
    `trigger` is attitude_below|day_reached|party_vulnerable|prize_seized. The companion
    must exist and be a companion. `check_companion_arc` then evaluates it each beat.

    Identify the companion via ``companion_id`` (canonical) or the aliases ``companion`` /
    ``character_id`` — equivalent; canonical ``companion_id`` wins if more than one is given."""
    companion_id = companion_id or companion or character_id  # accept intuitive aliases
    if not companion_id:
        raise ValueError("set_companion_arc needs a companion id (pass `companion_id` or an alias: `companion`/`character_id`)")
    if arc is None:
        raise ValueError("set_companion_arc needs an `arc` object")
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
            # F05-7: stamp the arrival day so quest_stalled measures from when the engine
            # learned of the quest, NOT from day 1. A quest added late is therefore NOT
            # flaggable on the next beat (the stall clock starts now, under the lock).
            last_progress_day=c.day,
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
def complete_quest(
    campaign_id: str,
    quest_id: str,
    status: str = "completed",
    evolves_to: str = "",
    callback_in_days: int = 0,
) -> dict:
    """Resolve a quest. status: completed | failed | active.

    Rule-of-three evolution (Quest & Arc engine, Layer 1): close a won thread with an
    ECHO so a session becomes a saga. Pass ``evolves_to`` (a follow-on hook/quest id or
    a free seed tag the DM weaves on callback) and optionally ``callback_in_days`` (the
    in-world days before it returns; 0 = due immediately) — when the quest resolves
    (status -> completed) the engine schedules a Consequence that surfaces later via
    ``check_consequences`` / ``scene_context``. The grateful family becomes a feud, the
    spared villain returns. You may instead pre-set ``evolves_to`` on the quest (content/
    questgen) and call this without the kwarg — a field already on the quest is honored.

    Additive + idempotent: omit ``evolves_to`` (and leave the field empty) == today's
    behavior, byte-for-byte; passing an empty ``evolves_to`` never clobbers a field the
    quest already carries; re-resolving never double-schedules (the ``evolves_from:<id>``
    note guards it)."""
    if status not in ("completed", "failed", "active"):
        raise ValueError("status must be completed | failed | active")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        q = c.quests.get(quest_id)
        if q is None:
            raise ValueError(f"no quest {quest_id!r}")
        q.status = status  # type: ignore[assignment]
        q.last_progress_day = c.day  # F05-7: resolving a quest IS progress — reset the stall clock.
        # F05-1: make the skill-documented evolution seam reachable. Set the rule-of-three
        # fields from the kwargs ONLY when explicitly provided, so an empty kwarg never
        # clobbers a field content/questgen already authored on the quest. Assigned under
        # the lock BEFORE _maybe_schedule_quest_evolution reads them (engine = sole writer).
        if evolves_to.strip():
            q.evolves_to = evolves_to.strip()
        if callback_in_days:
            q.callback_in_days = max(0, int(callback_in_days))
        evolution = _maybe_schedule_quest_evolution(c, q)
        # A completed quest is an unambiguous "real win" — auto-award milestone XP in
        # xp-mode (deterministic, no LLM judgment) so progression isn't a manual chore the
        # DM reliably forgets (QA: a full session, a quest won, party still at 0 XP).
        # Guarded by milestone_awarded so a re-complete / status flip never double-awards.
        milestone = None
        if status == "completed" and not q.milestone_awarded:
            milestone = _award_milestone_xp(c, 150 * max(_party_levels(c)), f"quest: {q.title}")
            if milestone:
                q.milestone_awarded = True
        save_campaign(c)
        out = {"id": q.id, "title": q.title, "status": q.status}
        if evolution is not None:
            out["evolution_scheduled"] = {
                "consequence_id": evolution.id,
                "trigger_day": evolution.trigger_day,
                "evolves_to": q.evolves_to,
            }
        if milestone is not None:
            out["xp_awarded"] = milestone["xp_awarded"]
            out["grants"] = milestone["grants"]
        return out


def _resolve_objective(q: Quest, objective: str) -> str:
    """Resolve a DM-supplied objective reference to one of the quest's exact objective
    strings, so the DM doesn't have to echo the text byte-for-byte. Match precedence:
      1. exact string match against q.objectives;
      2. a 0-based index into q.objectives (e.g. "1" -> the second objective);
      3. a UNIQUE case-insensitive substring of exactly one objective.
    Raises ValueError on no match or an ambiguous substring (matches >1) rather than
    guessing — a wrong objective marked done is worse than an explicit error."""
    text = objective.strip()
    if not text:
        raise ValueError("objective must be a non-empty string, index, or substring")
    # 1) exact text
    if text in q.objectives:
        return text
    # 2) 0-based index
    if text.lstrip("-").isdigit():
        idx = int(text)
        if 0 <= idx < len(q.objectives):
            return q.objectives[idx]
        raise ValueError(
            f"objective index {idx} out of range (quest has {len(q.objectives)} objectives)")
    # 3) unique case-insensitive substring
    needle = text.lower()
    matches = [o for o in q.objectives if needle in o.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"objective {objective!r} is ambiguous — it matches {len(matches)} objectives "
            f"({matches!r}); pass the exact text or index")
    raise ValueError(f"no objective matching {objective!r} on quest {q.id!r}")


@mcp.tool()
def complete_objective(campaign_id: str, quest_id: str, objective: str) -> dict:
    """Mark one of a quest's objectives complete as the party achieves it in the
    fiction. `objective` matches by exact text, a 0-based index, or a unique
    case-insensitive substring of an objective (so you needn't echo the text exactly).
    Moves it into completed_objectives (idempotent — re-marking is a no-op). When the
    LAST open objective is completed, the quest auto-resolves to 'completed' (which, in
    'xp' leveling_mode, awards milestone XP once). Returns the quest's status, its
    completed_objectives, and any still-remaining objectives. Use this as the party hits
    each objective on-screen instead of waiting to call complete_quest at the end."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        q = c.quests.get(quest_id)
        if q is None:
            raise ValueError(f"no quest {quest_id!r}")
        target = _resolve_objective(q, objective)
        if target not in q.completed_objectives:
            q.completed_objectives.append(target)
        # F05-7: completing an objective IS progress — stamp the day so the quest_stalled
        # detector measures from the last engine-known advancement (not Decision prose).
        q.last_progress_day = c.day
        auto = None
        evolution = None
        remaining = [o for o in q.objectives if o not in q.completed_objectives]
        # Auto-complete only when the quest HAS objectives and every one is done — an
        # empty-objective quest never auto-resolves (the all-done rule can't misfire on a
        # quest with no tracked objectives, and "optional" objectives stay safe).
        if q.objectives and not remaining and q.status != "completed":
            q.status = "completed"
            # Reuse the Defect-2 helper so finishing all objectives pays the milestone once.
            if not q.milestone_awarded:
                m = _award_milestone_xp(c, 150 * max(_party_levels(c)), f"quest: {q.title}")
                if m:
                    q.milestone_awarded = True
                    auto = m
            # F05-2: this auto-resolve is a real "quest won" verb — route it through the SAME
            # rule-of-three evolution seam complete_quest uses, so a quest that finished by
            # ticking its last objective still schedules its follow-on echo. Idempotent via the
            # evolves_from note guard (a later complete_quest won't double-schedule).
            evolution = _maybe_schedule_quest_evolution(c, q)
        save_campaign(c)
        out = {"quest_id": q.id, "title": q.title, "status": q.status,
               "completed_objectives": list(q.completed_objectives),
               "remaining": remaining}
        if auto is not None:
            out["xp_awarded"] = auto["xp_awarded"]
            out["grants"] = auto["grants"]
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
        # F04-5: a zero/negative downtime is a no-op, NOT a clock rewind. The unconditional
        # `c.time_of_day = "morning"` reset below would turn "day 3 night" into "day 3 morning"
        # (time runs BACKWARD) for downtime(0) or any negative span — the only non-monotonic
        # clock path in the engine. worldsim.tick also fires+re-arms a due thread regardless of
        # elapsed, so a 0-day downtime could consume a standing beat. Return early WITHOUT
        # touching the clock or running any tick/expiry sweep; point the DM at advance_time for
        # within-day passage. (advance_time itself floors at 0 steps and is a true no-op.)
        if elapsed <= 0:
            return {
                "day": c.day,
                "days_elapsed": 0,
                "note": note,
                "no_op": True,
                "message": ("downtime needs at least 1 day; the clock was not changed. To move "
                            "time within the current day, use advance_time (phases / to)."),
                "due_consequences": [],
                "world_beats": [],
                "world_developments": [],
                "strategic_events": [],
                "expired_effects": [],
            }
        c.day += elapsed
        c.time_of_day = "morning"
        due = consequences_mod.due(c)
        beats = worldsim.tick(c, max_beats=2)  # a long span → a couple of threads stirred
        dev = worldsim.tick_backlog(c, max_events=2)  # ...and the backlog advances over the span
        strategic = worldsim.tick_strategic(c)
        # The clock jumped forward days — expire timed effects like every sibling time-seam
        # (advance_time/travel_to/long_rest/short_rest). A multi-day downtime clears hour/day-scale
        # buffs (Mage Armor) and any sub-hour leftover; this was the only seam that omitted it.
        # (elapsed is guaranteed > 0 here — the zero/negative case returned early above.)
        expired = _expire_clock_effects_all(c)
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


# --- Faction-growth questlines (Quest & Arc engine, faction arcs / #127) -----------------------
# The Skyrim/Kingmaker join->grow->lead loop. Mirrors the companion-quest-arc tool surface
# (set_companion_quest_arc / advance_companion_quest_arc / get_companion_quest_arcs +
# check_companion_arc) — generalized onto a FACTION-owned reputation/standing gauge. The engine
# advances a stage only when its gauge gate holds (pure, contract-safe) and ripples a resolved
# stage's finale ONCE; the advisory surface (check_faction_arcs) detects-but-never-acts.


def _faction_arc_view(c: Campaign, arc: FactionArc) -> dict:
    """A DM-facing view of a faction arc with its faction name + the current gauge values resolved
    (so the DM sees how close each locked stage is to unlocking without a second call)."""
    fac = c.factions.get(arc.faction_id)
    out = arc.model_dump()
    out["faction_name"] = fac.name if fac else ""
    out["reputation"] = fac.reputation if fac else None
    out["standing"] = fac.standing if fac else None
    out["joined"] = fac.joined if fac else False
    out["rank"] = fac.rank if fac else 0
    out["linked_quests"] = [
        {"id": q.id, "title": q.title, "status": q.status}
        for s in arc.stages
        if s.quest_id and (q := c.quests.get(s.quest_id)) is not None
    ]
    return out


def _validate_faction_arc_links(c: Campaign, arc: FactionArc) -> None:
    """Validate a faction arc's references against campaign state (mirrors
    _validate_companion_quest_arc_links). A faction arc must name a real faction; each stage's
    optional tracked-Quest projection must point at an existing Quest."""
    if not arc.faction_id or arc.faction_id not in c.factions:
        raise ValueError(f"faction arc {arc.id!r} names unknown faction {arc.faction_id!r}")
    for stage in arc.stages:
        if stage.quest_id and stage.quest_id not in c.quests:
            raise ValueError(f"no tracked quest {stage.quest_id!r} for faction arc stage {stage.id!r}")


@mcp.tool()
def grant_standing(campaign_id: str, faction_id: str, amount: int, reason: str = "") -> dict:
    """Raise (or lower) a faction's MONOTONIC membership `standing` by `amount` — the Skyrim-style
    "rank progress" gauge, distinct from `reputation` (how the faction FEELS about you). Use it
    when the party performs SERVICE that advances them inside a faction (completing a faction
    job, proving themselves) — standing is what unlocks the next stage of a faction questline
    gated on `gauge="standing"`. Floored at 0 (it never goes negative — you don't un-rise through
    service; `reputation` is the gauge that can be burned). The faction must already exist (join
    it / earn reputation first). Returns the faction's new standing.

    NOTE: this does NOT auto-advance a faction arc — call `check_faction_arcs` after to see whether
    a stage just unlocked, then `advance_faction_arc` to take it (the advise-not-act contract)."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        fac = c.factions.get(faction_id)
        if fac is None:
            raise ValueError(f"no faction {faction_id!r} — join it or earn reputation first")
        fac.standing = max(0, fac.standing + int(amount))
        save_campaign(c)
        return {"id": fac.id, "name": fac.name, "standing": fac.standing, "reason": reason}


@mcp.tool()
def set_faction_arc(campaign_id: str, arc: dict) -> dict:
    """Create or replace an engine-owned FACTION questline (the join->grow->lead state machine).

    `arc` is `{faction_id, title, stages:[{title, unlock_at, gauge?, location_id?, quest_id?,
    note?, finale_effect?}], requires_joined?, note?}`. `gauge` is "reputation" (default) or
    "standing" — which faction gauge a stage's `unlock_at` threshold reads. `finale_effect` is an
    Outcome-shaped world ripple (flag / faction_id+reputation_delta / controller_id+location_id /
    npc_name / decision_flag / schedule_in_days+schedule_text / narrate) applied ONCE when the
    stage resolves — the world-changing finale. The faction must already exist; any stage
    `quest_id` must point at an existing tracked Quest. LINKS the arc to its faction
    (`questline_arc_id`). The engine evaluates the gauge gates each beat via `check_faction_arcs`;
    `join_faction` arms the arc; `advance_faction_arc` advances a stage / applies the finale."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        parsed = FactionArc.model_validate(dict(arc or {}))
        _validate_faction_arc_links(c, parsed)
        c.faction_arcs[parsed.id] = parsed
        # Link the faction back to its questline so get_state / the viewer can find it.
        c.factions[parsed.faction_id].questline_arc_id = parsed.id
        save_campaign(c)
        return {"faction_arc": _faction_arc_view(c, parsed)}


@mcp.tool()
def join_faction(campaign_id: str, faction_id: str, rank: int = 1) -> dict:
    """JOIN a faction — the membership latch that ARMS its questline (the Skyrim/Kingmaker
    join->grow->lead loop). Sets the faction `joined=True` and its starting `rank` (default 1 —
    the lowest membership tier), then ARMS any linked FactionArc: a `requires_joined` arc that was
    `locked` opens to `available`, and any stage whose gauge gate ALREADY holds unlocks. Use it
    when the party formally enlists with / is inducted into a group.

    The faction must already exist (earn some reputation first, or it's seeded by the world).
    Idempotent on the membership latch (re-joining just re-evaluates the arc). Returns the
    faction's membership state + any stages that just became available. After this, grow
    `reputation`/`standing` through service, then `advance_faction_arc` to climb the questline."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        fac = c.factions.get(faction_id)
        if fac is None:
            raise ValueError(f"no faction {faction_id!r} — earn reputation with it first (adjust_reputation)")
        fac.joined = True
        if rank > fac.rank:
            fac.rank = int(rank)
        newly_available: list[str] = []
        arc = c.faction_arcs.get(fac.questline_arc_id) if fac.questline_arc_id else None
        if arc is not None:
            res = faction_arc_mod.evaluate(arc, c)
            newly_available = res["newly_available"]
        save_campaign(c)
        return {
            "id": fac.id,
            "name": fac.name,
            "joined": fac.joined,
            "rank": fac.rank,
            "standing": fac.standing,
            "reputation": fac.reputation,
            "questline_arc_id": fac.questline_arc_id,
            "newly_available_stage_ids": newly_available,
        }


@mcp.tool()
def get_faction_arcs(campaign_id: str, faction_id: str = "", status: str = "") -> dict:
    """Read faction questlines (the join->grow->lead arcs), optionally filtered by faction and
    lifecycle status. Read-only; does NOT evaluate gates or advance anything (use
    `check_faction_arcs` to advance locked->available, `advance_faction_arc` to take a stage). Each
    arc view resolves the faction's name + current reputation/standing/joined/rank so the DM can
    see how close each locked stage sits to its `unlock_at`."""
    c = _require(campaign_id)
    wanted = _companion_quest_status(status, "status") if status else ""
    arcs = list(c.faction_arcs.values())
    if faction_id:
        arcs = [a for a in arcs if a.faction_id == faction_id]
    if wanted:
        arcs = [a for a in arcs if a.status == wanted]
    arcs.sort(key=lambda a: (a.faction_id, a.title, a.id))
    return {"faction_arcs": [_faction_arc_view(c, a) for a in arcs], "count": len(arcs)}


@mcp.tool()
def check_faction_arcs(campaign_id: str, faction_id: str = "") -> dict:
    """Advance faction questlines' gauge gates against the CURRENT state and surface the rank-ups
    that just became live — the faction analog of `check_companion_arc`. Call it each beat: when a
    stage UNLOCKS (the faction's reputation/standing reached its `unlock_at` and the party has
    joined), play that "you've earned a promotion / the next mission opens" beat.

    Pass `faction_id` to evaluate one faction's arc, or leave blank to evaluate ALL armed arcs. A
    stage flips locked->available when its gauge gate holds AND the faction is joined; the engine
    NEVER auto-advances past `available` — taking a stage (active/resolved/failed) and rippling its
    finale is an EXPLICIT `advance_faction_arc` call (advise-not-act). Idempotent — an
    already-unlocked stage is not re-reported. Also returns an ADVISORY `nudges` list (available /
    in-flight questline stages) mirroring the Director's scene-debt surface; read-only, never acts."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        if faction_id and faction_id not in c.factions:
            raise ValueError(f"no faction {faction_id!r}")
        results = []
        for arc in c.faction_arcs.values():
            if faction_id and arc.faction_id != faction_id:
                continue
            res = faction_arc_mod.evaluate(arc, c)
            if res["newly_available"]:
                fac = c.factions.get(arc.faction_id)
                results.append(
                    {
                        "arc_id": arc.id,
                        "faction_id": arc.faction_id,
                        "faction_name": fac.name if fac else "",
                        "title": arc.title,
                        "newly_available_stage_ids": res["newly_available"],
                    }
                )
        save_campaign(c)
        # The advisory nudge surface (read-only — runs on the just-evaluated state).
        nudges = faction_arc_mod.detect_rank_available(c)
        if faction_id:
            nudges = [n for n in nudges if n["faction_id"] == faction_id]
        return {"results": results, "nudges": nudges}


@mcp.tool()
def advance_faction_arc(
    campaign_id: str,
    arc_id: str,
    stage_id: str = "",
    stage_status: str = "",
    status: str = "",
    rank: int = 0,
) -> dict:
    """Explicitly advance a FACTION questline — take an available stage, resolve a finale, fail a
    branch (the faction analog of `advance_companion_quest_arc`). The engine ripples a resolved
    stage's world-changing finale ONCE; you narrate it.

    `stage_id` + `stage_status` advances one stage (e.g. `available`->`active`->`resolved`).
    `status` advances the arc-level lifecycle. `rank` (optional) sets the faction's new membership
    rank (a promotion). GATE-CHECKED: a stage can only leave `locked`/`available` toward `active`
    when its gauge gate holds (the party earned it) — advancing an un-earned stage is rejected.

    When a stage moves to `resolved` AND carries a `finale_effect`, the engine applies that ripple
    through the SAME path the living world uses (a set flag, a faction reputation shift, a
    control/arrival marker, an optional `decision_flag` that arms a companion flip, an optional
    follow-on Consequence). IDEMPOTENT: re-resolving an already-resolved stage applies the finale
    NOTHING further (the `effect_applied` latch) — calling it twice is safe. Returns the updated
    arc view + what the finale moved (`finale`)."""
    next_stage_status = _companion_quest_status(stage_status, "stage_status") if stage_status else ""
    next_status = _companion_quest_status(status, "status") if status else ""
    if not any((next_stage_status, next_status, rank)):
        raise ValueError("advance_faction_arc requires stage_status, status, or rank")
    if next_stage_status and not stage_id:
        raise ValueError("stage_status requires stage_id")

    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        arc = c.faction_arcs.get(arc_id)
        if arc is None:
            raise ValueError(f"no faction arc {arc_id!r}")
        fac = c.factions.get(arc.faction_id)
        if fac is None:
            raise ValueError(f"faction arc {arc_id!r} names unknown faction {arc.faction_id!r}")

        finale: dict | None = None
        if next_stage_status:
            stage = next((s for s in arc.stages if s.id == stage_id), None)
            if stage is None:
                raise ValueError(f"no stage {stage_id!r} in faction arc {arc_id!r}")
            # Gate enforcement: a stage may only begin (-> active) once its gauge gate holds.
            # Moving toward active/resolved from locked without the gate is rejected (the engine
            # enforces "earned trust", invariant #3 — a pure gauge check, never fiction).
            advancing = next_stage_status in ("available", "active", "resolved")
            if stage.status == "locked" and advancing and not faction_arc_mod.stage_gate_holds(stage, fac):
                raise ValueError(
                    f"faction arc stage {stage_id!r} is gated: {stage.gauge} "
                    f"{faction_arc_mod.gauge_value(fac, stage.gauge)} has not reached unlock_at {stage.unlock_at}"
                )
            stage.status = next_stage_status
            if next_stage_status == "resolved":
                finale = faction_arc_mod.apply_finale(c, stage)
        if next_status:
            arc.status = next_status
        if rank and rank > fac.rank:
            fac.rank = int(rank)

        save_campaign(c)
        out = {"faction_arc": _faction_arc_view(c, arc)}
        if finale is not None:
            out["finale"] = finale
        return out


@mcp.tool()
def record_decision(
    campaign_id: str,
    summary: str = "",
    options: Optional[list] = None,
    chosen: str = "",
    rationale: str = "",
    actor_ids: Optional[list] = None,
    sets_flag: str = "",
    decision: str = "",
) -> dict:
    """Record a party decision so the DM and companions can call back to it later
    ('last time we trusted Grett...'). Capture the choice after a deliberation:
    `summary` (the decision; pass it as `summary` (canonical) or `decision` (alias) —
    equivalent, `summary` wins if both given), `options` (what was on the table),
    `chosen`, why (`rationale`), and who weighed in (`actor_ids`). Returns the decision id.

    `sets_flag` (optional, Quest & Arc engine Layer 2) ties this CHOICE to a CONTENT-
    defined campaign flag it raises — the one-step path for the owner's model ("let the
    farmer's daughter die → the knight-companion turns on you"). When set, the named flag
    is flipped True in `Campaign.flags` (same store as `set_flag`), which ESCALATES any
    `attitude_below` companion agenda whose `decision_flag` matches (the betrayal chance
    spikes). The flag NAME is yours (e.g. "let_daughter_die", "took_bribe") — never
    engine-coded. Equivalent to a `set_flag` call alongside the record; omit it (or use
    plain `set_flag`) when no agenda is gated on the choice. Returns the flag in `flag`."""
    summary = summary if summary else decision  # `decision` is an accepted alias for `summary`
    if not summary:
        raise ValueError("record_decision needs a summary (pass `summary` or its alias `decision`)")
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
def update_decision(
    campaign_id: str,
    decision_id: str,
    chosen: str = "",
    rationale: str = "",
) -> dict:
    """Record the OUTCOME of a decision that was offered earlier but left pending — the DM
    calls this once the party actually commits (F05-5). Sets the decision's ``chosen`` (and
    optionally enriches its ``rationale``); this is the resolution for a
    ``choice_without_outcome`` scene-debt the Director nudges you about.

    Use it when a previous ``record_decision`` (or a present_events options layout) recorded a
    choice with ``options`` but no ``chosen`` yet, and the party has now decided. ``chosen``
    must be non-empty (the whole point is to fill the outcome); pass the option the party took
    (free text — it need not be one of the listed options, since the player may always act
    outside the menu). ``rationale`` is optional and APPENDED to any existing rationale, never
    overwriting it.

    Narrow + additive: it mutates ONLY ``chosen``/``rationale`` on an existing Decision under
    the campaign lock (engine = sole writer); it never re-orders, deletes, or invents a
    decision, and it does not touch flags (use ``record_decision(sets_flag=)`` for that).
    Returns the updated decision view."""
    if not chosen or not chosen.strip():
        raise ValueError("update_decision needs a non-empty `chosen` — what did the party decide?")
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        d = next((x for x in c.decisions if x.id == decision_id), None)
        if d is None:
            raise ValueError(
                f"no decision {decision_id!r} — use get_state / the choice_without_outcome "
                f"debt's evidence.decision_id to find the pending decision's id."
            )
        d.chosen = chosen.strip()
        extra = rationale.strip()
        if extra:
            # APPEND, never clobber — a later commit may add the 'why' to an existing note.
            d.rationale = f"{d.rationale}\n{extra}".strip() if d.rationale else extra
        save_campaign(c)
        return {"id": d.id, "summary": d.summary, "chosen": d.chosen,
                "rationale": d.rationale, "day": d.day}


@mcp.tool()
def present_events(campaign_id: str) -> dict:
    """Surface the first-class stumble-into EVENTS that are available right now (Quest & Arc
    engine, Layer 3) — the Kingmaker-style decisionals whose moment has arrived. Call it each
    beat like `check_consequences` / `check_companion_arc`: it returns the unresolved Events
    whose CONTRACT-SAFE trigger holds (a set flag, a faction's reputation reaching a level, or a
    reached day — never fiction), so you can drop a soft nudge ("a man in Flaming-Fist colors
    falls into step beside you...") and lay out its tagged options.

    Each returned Event carries its `prompt` (the situation you voice), its `options` (tagged
    choices the player picks — relay them via the parley surface; a free-form path is ALWAYS
    also allowed), and any `anchor_npc_id` (bind the scene to that canon NPC). READ-ONLY: it
    never fires or mutates anything; resolved Events are skipped (idempotent). When the player
    picks an option, call `resolve_event(campaign_id, event_id, option_label)` to apply its
    deterministic ripple — that is the engine resolution; you narrate the result."""
    c = _require(campaign_id)
    available = events_mod.present(c)
    return {
        "events": [
            {
                "id": ev.id,
                "prompt": ev.prompt,
                "trigger": ev.trigger,
                "anchor_npc_id": ev.anchor_npc_id,
                "options": [
                    {"label": opt.label, "tag": opt.tag, "skill": opt.skill, "dc": opt.dc}
                    for opt in ev.options
                ],
            }
            for ev in available
        ],
        "free_form": True,  # the player may ALWAYS act outside the menu (#141 — never a closed set)
    }


@mcp.tool()
def resolve_event(campaign_id: str, event_id: str, option_label: str) -> dict:
    """Resolve a stumble-into EVENT by applying the chosen option's DETERMINISTIC outcome
    (Quest & Arc engine, Layer 3) — the engine ripples; you narrate. Call this AFTER the player
    picks one of the options `present_events` laid out (a free-form pick the player invents is
    NOT an Event option — adjudicate that yourself, then record_decision / adjust_reputation as
    usual).

    Looks up the Event by `event_id` and the chosen `option_label` (case-insensitive), then
    applies that option's `Outcome` through the SAME engine path the living world uses: a set
    `flag`, a clamped faction `reputation_delta`, a `control:`/`arrival:` marker, an optional
    follow-on Consequence scheduled `schedule_in_days` out (the thread lingers), and — the
    decision-gated flip — an optional `decision_flag` set True in `Campaign.flags`, which ARMS a
    matching `attitude_below` companion agenda (the owner's "take the bribe -> the knight turns";
    same store as `record_decision(sets_flag=...)`). Then marks the Event resolved.

    IDEMPOTENT: re-resolving an already-resolved Event is a no-op (applies nothing) — calling it
    twice is safe. Returns the narrated line + what moved (`flags_set`, `rep_shift`, `scheduled`,
    `decision_flag`) for you to weave. If you set a `decision_flag`, follow up with
    `check_companion_arc` to see whether the spiked betrayal fires."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        event = c.events.get(event_id)
        if event is None:
            raise ValueError(f"no event {event_id!r} in campaign")
        if event.resolved:
            # Idempotency: a fired Event applies NOTHING on a re-resolve. No save needed (no
            # mutation), so a double-call can never double-ripple.
            return {
                "event_id": event.id,
                "resolved": True,
                "noop": True,
                "note": "event already resolved — no effect applied",
            }
        option = events_mod.find_option(event, option_label)
        if option is None:
            labels = [opt.label for opt in event.options]
            raise ValueError(
                f"event {event_id!r} has no option labelled {option_label!r}; options are {labels}"
            )
        result = events_mod.resolve(c, event, option)
        save_campaign(c)
        return result


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
        # F04-1: when `region` is defaulted to the current location, resolve danger off
        # that location's COMPOSITE (region + name + tags) so a city scene reads civilized.
        # An EXPLICIT `region` arg matches off itself (no location context to enrich) — the
        # caller chose that string deliberately.
        if not region_in:
            cur_loc = c.locations.get(c.current_location_id) if c.current_location_id else None
            region_in = cur_loc.region if cur_loc is not None else ""
            match_region = _composite_region_match(cur_loc)
        else:
            match_region = region_in
        staged = _stage_wandering_encounter(
            c, region_in, difficulty=difficulty, location_id=c.current_location_id,
            force=True, match_region=match_region,
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
            q.last_progress_day = c.day  # F05-7: advancing a quest IS progress — reset stall clock.
            # Mirror complete_quest: a tracked quest reaching "completed" auto-awards
            # milestone XP once (xp-mode) — set_quest_status is the DM's equivalent verb,
            # so both close-of-quest paths pay the same deterministic reward.
            milestone = None
            evolution = None
            if qs == "completed" and not q.milestone_awarded:
                milestone = _award_milestone_xp(c, 150 * max(_party_levels(c)), f"quest: {q.title}")
                if milestone:
                    q.milestone_awarded = True
            # F05-2: set_quest_status is a full quest-completion verb too — route a "completed"
            # flip through the SAME rule-of-three evolution seam complete_quest uses, so a quest
            # the DM resolves via this verb still schedules its follow-on echo (the saga
            # mechanism was dead on 2 of 3 verbs). Reads any evolves_to content/questgen
            # pre-authored on the quest; idempotent via the evolves_from note guard.
            if qs == "completed":
                evolution = _maybe_schedule_quest_evolution(c, q)
            save_campaign(c)
            out = {"quest_id": q.id, "title": q.title, "status": q.status}
            if milestone is not None:
                out["xp_awarded"] = milestone["xp_awarded"]
                out["grants"] = milestone["grants"]
            if evolution is not None:
                out["evolution_scheduled"] = {
                    "consequence_id": evolution.id,
                    "trigger_day": evolution.trigger_day,
                    "evolves_to": q.evolves_to,
                }
            return out
        raise ValueError(f"no quest hook or tracked quest with id {hook_id!r}")


@mcp.tool()
def set_house_rules(campaign_id: str, patch: dict) -> dict:
    """Update house rules (partial merge). Keys: difficulty, critical_max_damage,
    flanking_advantage, slow_natural_healing, feats_allowed, multiclass_allowed,
    dm_can_fudge, wandering_encounters, enforce_sell_cap, sell_cap_multiple (F09-9:
    when enforce_sell_cap is on, sell_item rejects a price above sell_cap_multiple× the
    item's listed cost). Unknown keys are rejected."""
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
    # F05-4: live() = detect() minus the still-snoozed resolved debts, so a debt the DM
    # cleared via resolve_scene_debt stops re-surfacing in this list every beat.
    live = _scene_debt_mod.live(c)
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

        # F05-4: a resolved record only blocks a re-resolve while it's STILL SNOOZED (within
        # the suppression window). Once the snooze lapses and the same structural fact is
        # detected again, the DM can re-resolve it — the record is UPDATED (re-stamped) in
        # place rather than appended a second time, so the audit trail stays one-per-debt and
        # the fact never gets permanently silenced by a single stale resolution.
        existing = next((d for d in c.scene_debts if d.id == debt_id and d.resolved), None)
        if existing is not None and _scene_debt_mod.is_snoozed(existing, c.day):
            return {"message": "already resolved", "debt": existing.model_dump()}

        # Detect live debts to find the matching one (raw detect — the snoozed-suppression
        # is the live() filter; here we want the underlying structural fact if it recurs).
        live = _scene_debt_mod.detect(c)
        debt = next((d for d in live if d.id == debt_id), None)
        if debt is None:
            raise ValueError(
                f"No live scene-debt with id {debt_id!r}. "
                f"Use get_scene_debts to see current debt ids."
            )

        if existing is not None:
            # Re-resolution after the snooze lapsed: update the existing record in place.
            existing.resolution_evidence = evidence.strip()
            existing.resolved_day = c.day
            save_campaign(c)
            return {"message": "resolved", "debt": existing.model_dump()}

        # First resolution: mark resolved, stamp the day, append to the audit trail.
        debt.resolved = True
        debt.resolution_evidence = evidence.strip()
        debt.resolved_day = c.day  # F05-4: when it was cleared, for the snooze window + audit.
        c.scene_debts.append(debt)
        save_campaign(c)

        return {"message": "resolved", "debt": debt.model_dump()}


# --- World-Seed write-lane (#266) -------------------------------------------
# The mutability matrix for set_seed_param, in ONE place so the policy has a single
# home (the viewer read model mirrors these classes so the UI hardcodes no policy).
#   free   — cosmetic / DM-guidance; always settable, no warning.
#   gated  — rules-affecting / retroactive; settable freely BEFORE the first session
#            (Campaign.session_ids == []), else refused unless force=True (then a warning).
#   locked — out of scope to change post-seed; always raises.
# A free/gated param either lives on SeedParams or (difficulty) routes to house_rules.
SEED_PARAMS_FREE = (
    "tone", "narration", "gm_strictness", "chronicle_voice", "anachronism", "chronicler_notes",
)
SEED_PARAMS_GATED = ("difficulty", "permadeath", "fate_dice", "item_destruction")
SEED_PARAMS_LOCKED = ("system",)
# "difficulty" is the lone gated param that is NOT a SeedParams field — it stays canonical
# on house_rules.difficulty (so the DM/engine reads one source). Everything else is a field.
SEED_PARAM_HOUSE_RULE = {"difficulty": "difficulty"}


def _seed_param_class(param: str) -> str:
    """free | gated | locked for a known param, else '' (unknown)."""
    if param in SEED_PARAMS_FREE:
        return "free"
    if param in SEED_PARAMS_GATED:
        return "gated"
    if param in SEED_PARAMS_LOCKED:
        return "locked"
    return ""


@mcp.tool()
def set_seed_param(campaign_id: str, param: str, value, force: bool = False) -> dict:
    """Set ONE World-Seed parameter on a campaign — the OpenWorlds Seed screen's mutable
    write-lane (#266). The engine is the SOLE WRITER: this mutates under campaign_lock then
    save_campaign. ADDITIVE — every seed field defaults to today's behavior, so old snapshots
    round-trip and an unset param reads as its default.

    Mutability (the matrix is fixed; ``get_seed_surface`` in the viewer mirrors it):

    - FREE (cosmetic / DM-guidance, always settable): tone, narration, gm_strictness,
      chronicle_voice, anachronism, chronicler_notes.
    - GATED (rules-affecting / retroactive): difficulty, permadeath, fate_dice,
      item_destruction. Settable freely BEFORE a session has started (Campaign.session_ids
      is empty). ONCE a session has started the change is REFUSED (applied=False, a
      ``warning`` explains why) unless ``force=True``, in which case it applies AND the
      return carries a ``warning`` describing the retroactive risk so the UI can confirm.
      (``difficulty`` routes to house_rules.difficulty; it is not stored on seed_params.
      Note permadeath only governs FUTURE death handling — it never resurrects an
      already-dead PC; the warning says so.)
    - LOCKED: system (the ruleset). A whole-ruleset swap post-seed is a re-seed, not a param
      edit — this raises.

    ``value`` is validated against the field's type (a bad Literal/bool raises). Returns::

        {"id", "param", "value", "applied": bool, "warning": str, "mutability": "free|gated|locked"}
    """
    param = str(param).strip()
    cls = _seed_param_class(param)
    if not cls:
        known = sorted([*SEED_PARAMS_FREE, *SEED_PARAMS_GATED, *SEED_PARAMS_LOCKED])
        raise ValueError(f"unknown seed param {param!r}; known: {known}")
    if cls == "locked":
        raise ValueError(
            f"seed param {param!r} is LOCKED post-seed — a ruleset/system swap is a re-seed, "
            "not a param edit."
        )

    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        session_started = bool(c.session_ids)

        # Gate: a rules-affecting change after a session has started is refused unless forced.
        warning = ""
        if cls == "gated" and session_started and not force:
            return {
                "id": c.id,
                "param": param,
                "value": value,
                "applied": False,
                "mutability": "gated",
                "warning": (
                    f"{param!r} is a retroactive, rules-affecting change and this chronicle "
                    f"has already begun ({len(c.session_ids)} session(s) on record). "
                    "It can shift the felt difficulty/economy of a run already in progress; "
                    "re-submit with force=True to apply it anyway."
                ),
            }
        if cls == "gated" and session_started and force:
            if param == "permadeath":
                warning = (
                    "Applied mid-chronicle. Permadeath governs only FUTURE death handling — "
                    "it does NOT resurrect or strand a hero who has already died."
                )
            else:
                warning = (
                    f"Applied mid-chronicle: {param!r} changes a rule of a run already in "
                    "progress, so the felt balance may shift unevenly from here on."
                )

        # Apply. difficulty routes to house_rules (its canonical home); validate via the
        # model so a bad value raises exactly like set_house_rules / set_pacing.
        if param in SEED_PARAM_HOUSE_RULE:
            hr_key = SEED_PARAM_HOUSE_RULE[param]
            data = c.house_rules.model_dump()
            data[hr_key] = value
            c.house_rules = HouseRules.model_validate(data)
            applied_value = getattr(c.house_rules, hr_key)
        else:
            data = c.seed_params.model_dump()
            data[param] = value
            c.seed_params = SeedParams.model_validate(data)
            applied_value = getattr(c.seed_params, param)

        save_campaign(c)
        return {
            "id": c.id,
            "param": param,
            "value": applied_value,
            "applied": True,
            "mutability": cls,
            "warning": warning,
        }


# --- Per-beat round-trip collapse (latency) ---------------------------------
# The #1 wall-clock cost of a DM beat is tool round-trips (each MCP call is a
# ~3–6s network hop). The beat cycle in skills/dungeon-master/SKILL.md prescribes
# a CLUSTER of reads at the START of every beat (step 1: get_state +
# get_campaign_director + present_events + check_companion_arc, plus an optional
# recall) and a CLUSTER of writes at the END (step 7: log_event + remember(s) +
# record_decision + advance_time). Each was its own round-trip. These two ADDITIVE
# tools collapse each cluster into a single call. They are pure composition over
# the existing tools — every underlying tool stays, and the old per-call path is
# byte-identical to before (an unused combined tool ships nothing).


def _scene_durable_threads(c: Campaign) -> dict:
    """Derive the compact, continuity-CRITICAL durable threads a transcript-free
    re-ground must not lose (#compact-scene-context).

    READ-ONLY + pure derivation — never mutates state (scene_context's sole-writer
    invariant). These are the *standing* threads (not this-beat deltas) that the
    bundle's other sections under-surface:

      - ``open_quests``        — every non-completed/non-failed Quest with its OPEN
                                 objectives. get_state's ``active_quests`` carries
                                 only ``{id,title}``; the unresolved objectives are
                                 the actual continuity (what the party still OWES).
      - ``npc_relationships``  — each NPC the party has actually MET, with its
                                 approval gauge (``attitude_value``) + free-text
                                 ``attitude`` + any authored ``relationships`` tags.
                                 get_state surfaces only ``npc_count``; who the party
                                 stands with (and how) is otherwise transcript-only.
      - ``companions``         — each companion's STANDING bond state: approval
                                 gauge, whether an arc / a sealed betrayal agenda is
                                 attached. (``companion_arcs`` reports only what just
                                 *turned* this beat; this is the durable baseline.)
      - ``factions``           — each faction's ``reputation`` (bidirectional trust)
                                 + ``standing`` (monotonic membership) — the engine-
                                 mutated gauges that gate faction events.
      - ``flags``              — the set world-state flags (gates already armed).

    Everything is a thin projection; empty collections == today's behavior. The
    DM's `recall`/`get_character`/`get_faction` reach the full record on demand —
    this is the always-pinned spine, not the whole world.
    """
    chars = list(c.characters.values())

    # EVERY attribute read below is defensive (``getattr`` with a safe default) so
    # scene_context — the DM's primary re-ground tool — NEVER raises an
    # ``AttributeError`` because some object is missing a field the durable block
    # expects (e.g. a partially-built Character, an older/variant model, or a field
    # the schema never grew). A missing attribute degrades to omit/empty for that
    # sub-field; it must never throw the whole tool. (#compact-scene-context: the
    # `relationships` read here threw for any Character lacking that attribute.)

    # OPEN quests (status not completed/failed) with their still-open objectives.
    open_quests = []
    for q in c.quests.values():
        status = getattr(q, "status", "active")
        if status in ("completed", "failed"):
            continue
        objectives = getattr(q, "objectives", None) or []
        completed = getattr(q, "completed_objectives", None) or []
        open_quests.append(
            {
                "id": getattr(q, "id", None),
                "title": getattr(q, "title", None),
                "status": status,
                "open_objectives": [o for o in objectives if o not in completed],
            }
        )

    # NPC relationships the party has a standing with: only NPCs actually met
    # (a world seed pre-populates strangers; `met` gates them out — same rule the
    # dashboard's Relationships view uses).
    npc_relationships = []
    for ch in chars:
        if getattr(ch, "kind", None) != "npc" or not getattr(ch, "met", False):
            continue
        attitude = getattr(ch, "attitude", None)
        relationships = getattr(ch, "relationships", None)
        npc_relationships.append(
            {
                "id": getattr(ch, "id", None),
                "name": getattr(ch, "name", None),
                "attitude_value": getattr(ch, "attitude_value", None),
                **({"attitude": attitude} if attitude else {}),
                **({"relationships": relationships} if relationships else {}),
            }
        )

    # Companions' STANDING bond state (loyalty gauge + whether a sealed
    # betrayal agenda is attached) — the durable baseline check_companion_arc's
    # delta report assumes you already know.
    companions = []
    for ch in chars:
        if getattr(ch, "kind", None) != "companion":
            continue
        arc = getattr(ch, "arc", None)
        agenda = getattr(arc, "agenda", None) if arc is not None else None
        entry = {
            "id": getattr(ch, "id", None),
            "name": getattr(ch, "name", None),
            "attitude_value": getattr(ch, "attitude_value", None),
            "has_arc": arc is not None,
            "has_betrayal_agenda": bool(
                agenda is not None
                and getattr(agenda, "trigger", None) == "attitude_below"
            ),
        }
        # F06-10 (audit 2026-06-11): surface this companion's personal QUEST ARCs — until now
        # the engine-complete CompanionQuestArc machine was invisible to the DM at re-ground
        # (durable.companions showed gates/flags only, no quest-arc mention anywhere DM-facing),
        # so an authored personal quest never got played. Pure read: each owned arc's
        # id/title/status + any non-locked stage, so the DM can advance a ripe one. Absent (no
        # key) when the companion owns no quest arcs (today's shape byte-for-byte).
        cqa = [
            a for a in c.companion_quest_arcs.values()
            if getattr(a, "companion_id", "") == getattr(ch, "id", None)
        ]
        if cqa:
            entry["quest_arcs"] = [
                {
                    "id": a.id,
                    "title": a.title,
                    "status": a.status,
                    "open_stages": [
                        {"id": s.id, "title": s.title, "status": s.status}
                        for s in a.stages if s.status != "locked"
                    ],
                }
                for a in sorted(cqa, key=lambda a: (a.title, a.id))
            ]
        companions.append(entry)

    # Faction gauges (engine-mutated; these gate events — invariant #3).
    factions = [
        {
            "id": getattr(f, "id", None),
            "name": getattr(f, "name", None),
            "reputation": getattr(f, "reputation", None),
            "standing": getattr(f, "standing", None),
        }
        for f in c.factions.values()
    ]

    flags = getattr(c, "flags", None) or {}
    out = {
        "open_quests": open_quests,
        "npc_relationships": npc_relationships,
        "companions": companions,
        "factions": factions,
        "flags": sorted(k for k, v in flags.items() if v),
    }
    # F06-5 leg (a): the camp/banter pillar was UNREACHABLE — its only pointer was long_rest's
    # camp_hint (census 0), and the every-beat re-ground (scene_context.durable) had no camp
    # affordance at all, so the DM never learned camp_scene exists. Surface a lightweight
    # advisory when there are living companions AND no fight is underway (camp is a between-
    # beats pillar): the DM can gather them via camp_scene. Present only when actionable, so a
    # solo run / mid-combat re-ground keeps today's durable shape byte-for-byte.
    living_companions = [
        ch for ch in chars
        if getattr(ch, "kind", None) == "companion"
        and not getattr(ch, "dead", False)
        and (getattr(ch, "current_hp", 0) or 0) > 0
    ]
    in_combat = bool(getattr(getattr(c, "combat", None), "active", False))
    if living_companions and not in_combat:
        out["camp_available"] = {
            "companions": [getattr(ch, "name", None) for ch in living_companions],
            "note": (
                "companions are present and out of danger — call camp_scene to gather them for a "
                "character round (banter, worries, ripe arc/quest beats) between adventures"
            ),
        }
    return out


# SYN-08 / F07-11: how many RAW rows to over-read for each requested player-facing
# beat. recent_narration filters out bookkeeping (rolls / system / wrapper-heartbeat /
# combat-log) before taking the last N, so the bounded tail must hand back more raw
# rows than N. 8x covers a tail that is mostly bookkeeping while still short-circuiting
# the whole-history re-parse on a long campaign.
_RECENT_RAW_SLACK = 8


def _recent_narration_max_chars() -> int:
    """SYN-08 / F14-17: optional per-beat soft cap (chars) for recent_narration's
    prose tail. DEFAULT-OFF (0): bounding the WINDOW (last-N) is lossless and always
    on, but byte-capping the CONTENT drops story, so it only engages when a wrapper
    sets ``WORLDOS_RECENT_NARRATION_MAX_CHARS`` (legacy ``CLAWDND_*`` honored). Story
    is the north star — ride a long-campaign duo A/B before any default change.
    Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F14-17, SYN-08)."""
    raw = _env.env_var("RECENT_NARRATION_MAX_CHARS", "0")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _scene_recent_narration(c: Campaign, limit: int) -> list[dict]:
    """The last ``limit`` PLAYER-FACING beats (kind in narration|dialogue) across
    the WHOLE campaign's session logs, in CHRONOLOGICAL order — the prose tail a
    transcript-free (lean) beat needs as its short-term memory of the story so far.

    READ-ONLY: reads the session jsonl files via store.read_log_all (never writes).
    Reads CAMPAIGN-WIDE, not just the current session: under lean / fast-turn play
    EACH beat starts a fresh session id, so the current session log is typically
    empty and a single-session read would deliver nothing — the prose-tail would
    silently never arrive. read_log_all walks every ``sessions/*.jsonl`` (canonical
    session_ids order, defensive disk tail, stable by timestamp) so the last N
    beats surface regardless of which session wrote them.

    SYN-08 / F07-11: the read is BOUNDED (``read_log_all(tail=…)``) so this
    every-beat surface no longer re-parses the entire append-only campaign history
    just to return the last handful — it scans a bounded newest-first window. This
    is lossless: the same last-N player-facing beats come back (the slack covers the
    bookkeeping rows the filter below drops).

    Rolls / system / combat-log rows are bookkeeping noise and are dropped (mirrors
    recap's _STORY_KINDS, minus combat: this is the spoken story).
    """
    if limit <= 0:
        return []
    # F07-11: bound the read to a newest-first window instead of re-parsing the whole
    # append-only history. We OVER-read the raw tail (``limit * _RECENT_RAW_SLACK``)
    # because the filter below drops bookkeeping rows (rolls/system/wrapper/combat-log)
    # — so the post-filter last-`limit` player-facing beats are intact even when the
    # raw tail is heavily interleaved with bookkeeping. read_log_all(tail=…) returns
    # exactly the last K RAW rows the full walk would, so this stays lossless.
    raw_tail = limit * _RECENT_RAW_SLACK
    entries = read_log_all(c.id, getattr(c, "session_ids", None), tail=raw_tail)
    # #749: drop the wrapper progress heartbeat (exact-match) — it is the QA/play wrappers'
    # mid-turn liveness filler, not the DM's prose. Feeding it back here told a lean
    # (transcript-free) DM that canned filler was its own canon.
    facing = [
        e for e in entries
        if e.kind in ("narration", "dialogue")
        and not _wrapper_progress_mod.is_wrapper_progress_line(e.text)
    ]
    # F14-17: per-beat soft cap is DEFAULT-OFF (0 -> verbatim, today's behavior).
    cap = _recent_narration_max_chars()

    def _text(e) -> str:
        return recap._soft_truncate(e.text, cap) if cap > 0 else e.text

    return [
        {"text": _text(e), **({"speaker": e.speaker} if e.speaker else {})}
        for e in facing[-limit:]
    ]


def _scene_fire_due_consequences(campaign_id: str) -> list[dict]:
    """F14-4: fire (and surface) the authored Consequences that have come due as of the
    current day, ON the every-beat scene_context path. Source: ENGINE-AUDIT-2026-06-11
    (F14-4) — add_consequence was write-only; nothing on the beat loop called
    ``consequences.due()``, so scheduled world-beats structurally never fired. This is the
    READ the WRITE was missing.

    Acquires the campaign_lock, fires the due consequences (``consequences.due`` marks each
    fired so it never re-tells on a later beat — idempotent — and SKIPS worldsim thread
    beats, which belong to ``world_tick``), then saves ONLY when something fired (no
    churn on the common no-op beat). Mirrors check_consequences' return shape so the DM
    sees the same fields it would from the standalone tool."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        fired = consequences_mod.due(c)
        if fired:  # save only on an actual state change (avoid an updated_at bump per beat)
            save_campaign(c)
        return [
            {"id": x.id, "text": x.text, "note": x.note, "trigger_day": x.trigger_day}
            for x in fired
        ]


# SYN-04: how many full manual-prose events scene_context surfaces per beat (the read
# valve). The audit measured 5 full BG event prompts (~6.5KB ≈ 1.6K tok) riding EVERY
# beat; surfacing at most one keeps the bundle lean while the rest rotate / wait.
_SCENE_EVENTS_FULL_PER_BEAT = 1
# A presented-event stub's prompt head length (enough to recognise the thread, not the
# full ~1KB prose).
_EVENT_STUB_HEAD_CHARS = 60


def _event_full_projection(ev: Event) -> dict:
    """The FULL event projection (same shape present_events surfaces)."""
    return {
        "id": ev.id,
        "prompt": ev.prompt,
        "trigger": ev.trigger,
        "anchor_npc_id": ev.anchor_npc_id,
        "options": [
            {"label": opt.label, "tag": opt.tag, "skill": opt.skill, "dc": opt.dc}
            for opt in ev.options
        ],
    }


def _event_stub_projection(ev: Event) -> dict:
    """A COMPACT stub for an already-presented event — enough to keep the thread in the
    DM's view (recognise it, re-offer its options) WITHOUT re-sending the full prose every
    beat. Source: SYN-04 leg (c)."""
    head = (ev.prompt or "")[:_EVENT_STUB_HEAD_CHARS]
    return {
        "id": ev.id,
        "prompt_head": head,
        "option_labels": [opt.label for opt in ev.options],
        "note": "already presented — resolve_event by id when the player picks",
    }


def _scene_events_throttled(campaign_id: str) -> dict:
    """SYN-04: the scene_context EVENTS block — surfaces at most ``_SCENE_EVENTS_FULL_PER_BEAT``
    NOT-YET-PRESENTED event(s) in full, stamps each ``first_presented_day`` under the
    campaign_lock (engine = sole writer), renders already-presented live events as compact
    STUBS, and reports the remaining unpresented count as ``manual_queued`` (they rotate in
    on later beats). Source: ENGINE-AUDIT-2026-06-11 (SYN-04 / F05-3 + F07-3).

    The standalone ``present_events`` tool is UNCHANGED (full payload, read-only) — only this
    every-beat bundle throttles. Queued / stubbed events stay resolvable: ``resolve_event``
    looks them up by id directly from ``c.events``."""
    with campaign_lock(campaign_id):
        c = _require(campaign_id)
        live = events_mod.present(c)  # unresolved + trigger holds, id-ordered
        fresh = [ev for ev in live if ev.first_presented_day is None]
        already = [ev for ev in live if ev.first_presented_day is not None]

        surfaced = fresh[:_SCENE_EVENTS_FULL_PER_BEAT]
        queued = fresh[_SCENE_EVENTS_FULL_PER_BEAT:]

        stamped = False
        for ev in surfaced:
            ev.first_presented_day = c.day  # stamp: presented this beat
            stamped = True
        if stamped:  # save only when we actually stamped (no per-beat churn otherwise)
            save_campaign(c)

        return {
            "events": [_event_full_projection(ev) for ev in surfaced],
            "presented": [_event_stub_projection(ev) for ev in already],
            "manual_queued": len(queued),
            "free_form": True,  # #141: the player may ALWAYS act outside the menu
        }


@mcp.tool()
def scene_context(
    campaign_id: str,
    recall_query: str = "",
    recall_limit: int = 6,
    recent_narration: int = 0,
) -> dict:
    """ONE-CALL beat re-ground — the whole start-of-beat read cluster in a single
    round-trip (latency collapse; additive — the individual tools all still exist).

    Returns, in one payload, exactly what the DM reads at the top of every beat
    (SKILL.md step 1) so it makes 1 call instead of 3–4. Field ordering is STABLE
    and durable-first (the threads that persist across the campaign come before the
    volatile clock/HP in ``state``) so a re-grounding DM — and prompt caches — see
    the unchanging spine first:

      - ``durable``   — the continuity-CRITICAL standing threads pinned so a
                        transcript-free (lean) re-ground loses NOTHING: every
                        ``open_quests`` (+ its still-open objectives),
                        ``npc_relationships`` the party has MET (approval +
                        relationship tags), each companion's standing bond
                        (``companions``: approval / arc / sealed betrayal agenda),
                        ``factions`` (reputation + standing gauges), and the set
                        ``flags``. Always present; empty collections == today.
      - ``director``  — get_campaign_director(campaign_id): the top structural
                        debts the campaign OWES right now (advisory, read-only).
      - ``events``    — the THROTTLED stumble-into decisionals (SYN-04): at most ONE
                        not-yet-presented Event surfaces in FULL per beat (``events``);
                        the engine stamps its ``first_presented_day`` so a later beat
                        renders it as a compact STUB (``presented``) instead of re-sending
                        its full ~1KB prose every turn; the rest queue as ``manual_queued``
                        (a count, rotating in over later beats). Resolve any of them by id
                        via ``resolve_event``. The standalone ``present_events`` tool still
                        returns the FULL, read-only payload when you want every option.
      - ``consequences_due`` — the authored Consequences that come DUE this beat, FIRED
                        (and surfaced) here so the deferred world actually moves (F14-4).
                        Always present; ``[]`` when nothing is due. Idempotent: each fires
                        exactly once (worldsim thread-beats are left for ``world_tick``).
      - ``companion_arcs`` — check_companion_arc(campaign_id): bonds that just
                        turned / a ``betrayal_warning`` to foreshadow. (This is the
                        one sub-call that persists arc progress — same effect as
                        calling the tool directly; idempotent across beats.)
      - ``recent_narration`` — present ONLY when ``recent_narration`` (the param)
                        is > 0: the last N player-facing beats (narration/dialogue)
                        across ALL of the campaign's session logs, chronological,
                        each ``{text, speaker?}``. This is the lean-beat memory: in
                        fast-turn mode the beat runs with NO prior transcript AND
                        each beat opens a fresh session, so reading campaign-wide
                        (not just the current session) is what makes this prose
                        tail (plus ``durable``) the actual story-so-far.
                        Default 0 = omitted (no log read, today's behavior).
      - ``recall``    — present ONLY when ``recall_query`` is non-empty: the same
                        fuzzy memory search recall(query, limit) returns. Pass it
                        when the moment touches the past ("have we met this NPC?",
                        "what did we decide about the cult?"); leave it blank
                        otherwise and no recall is run (no wasted work).
      - ``state``     — get_state(campaign_id): scene, party vitals, day/time,
                        active quests, combat status, pacing_mode, seed_params.
                        LAST because it carries the volatile values (clock, HP).

    Read-MOSTLY: ``durable`` / ``recent_narration`` / ``recall`` / ``state`` are pure
    reads/derivations. Three sub-paths persist engine-mutated progress under the
    campaign_lock — the same shape check_companion_arc has always had on this bundle:
    ``companion_arcs`` saves arc progress, ``events`` stamps each surfaced Event's
    ``first_presented_day`` (so it stops re-riding every beat, SYN-04), and
    ``consequences_due`` marks fired the consequences it surfaces (so the deferred world
    actually moves AND never re-fires, F14-4). Each save is guarded — it happens only when
    that sub-path actually changed state, so a beat with nothing to fire/stamp writes only
    what check_companion_arc already wrote. Use this every beat in place of the separate
    get_state / get_campaign_director / present_events / check_companion_arc /
    check_consequences calls. For a returning NPC you still want recall_npc(npc_id) /
    get_scene on arrival — those stay their own calls.
    """
    # Each delegate takes (and fully releases) the per-campaign flock before the
    # next runs — sequential, never nested — so this is deadlock-free even though
    # check_companion_arc acquires the lock. (Nesting campaign_lock in one process
    # WOULD deadlock: flock is not reentrant across fds.) The durable/recent reads
    # are lock-free snapshot reads via _require / read_log_all.
    #
    # Built durable-first so the dict preserves the stable, cache-friendly order
    # (durable threads → advisory → this-beat deltas → … → volatile state last).
    out: dict = {
        "durable": _scene_durable_threads(_require(campaign_id)),
        "director": get_campaign_director(campaign_id),
        # SYN-04: the THROTTLED events view (<=1 full event/beat + first_presented_day
        # stamping + stubs + manual_queued) instead of present_events' full every-beat
        # dump. The standalone present_events tool is unchanged.
        "events": _scene_events_throttled(campaign_id),
        # F14-4: fire (and surface) the authored consequences that come due this beat —
        # the every-beat READ that add_consequence's WRITE was missing. Always present
        # (empty list when nothing is due) so the DM can rely on the key.
        "consequences_due": _scene_fire_due_consequences(campaign_id),
        "companion_arcs": check_companion_arc(campaign_id),
    }
    if recent_narration and recent_narration > 0:
        out["recent_narration"] = _scene_recent_narration(
            _require(campaign_id), recent_narration
        )
    if recall_query and recall_query.strip():
        out["recall"] = recall(campaign_id, recall_query.strip(), limit=recall_limit)
    out["state"] = get_state(campaign_id)
    return out


@mcp.tool()
def persist_beat(
    campaign_id: str,
    events: Optional[list] = None,
    memories: Optional[list] = None,
    decision: Optional[dict] = None,
    advance: Optional[dict] = None,
) -> dict:
    """ONE-CALL end-of-beat persistence — batches the whole save cluster (SKILL.md
    step 7) into a single round-trip AND a single disk write (latency collapse;
    additive — log_event / remember / record_decision / advance_time all still
    exist for one-off use).

    Persistence is OFF the player's critical path: write the player-facing prose
    FIRST, then make ONE persist_beat call LAST with everything the beat produced.
    N writes become 1 MCP hop and 1 atomic snapshot save (the individual tools each
    take the lock + fsync separately; this takes them ONCE).

    All fields are optional — pass only what the beat produced; an empty call is a
    no-op. Order of application: events -> memories -> decision, all inside ONE
    campaign_lock + ONE save, then (if given) advance_time as its OWN locked call.

      - ``events``   — list of beat log entries, each a dict
                       ``{"kind","text","speaker"?,"payload"?}`` (kind:
                       narration|dialogue|roll|system|combat). Same as log_event.
                       NOTE: player-facing narration/dialogue should be STREAMED
                       live via log_event DURING the turn (the viewer tails the
                       session log mid-beat), NOT batched here — passing prose you
                       already streamed re-logs it twice. Leave events empty unless
                       you have a record row you did NOT already log live.
      - ``memories`` — list of facts to append, each
                       ``{"character_id","fact"}`` (``character_id`` also accepts the
                       ``id``/``npc_id`` aliases and is resolved tolerantly — a slug or
                       a name finds the character, an unknown id raises with a
                       did-you-mean). Same as remember (de-duped per character). Target
                       the COMPANION's id after a character beat AND the PC's id for what
                       the hero learns — symmetric memory.
      - ``decision`` — a single dict ``{"summary", "options"?, "chosen"?,
                       "rationale"?, "actor_ids"?, "sets_flag"?}``. Same as
                       record_decision (records the choice; sets_flag arms a gated
                       companion agenda). Omit when the beat had no real decision.
      - ``advance``  — a dict ``{"phases"?, "to"?, "note"?}`` to move the in-world
                       clock when the fiction moved time forward. Same as
                       advance_time (no-op / skipped during combat). For a real
                       JOURNEY or a rest keep using travel_to(advance_time=True) /
                       long_rest — those are their own beats, not a persist step.

    Returns a per-section summary: ``{"logged":[...], "remembered":[...],
    "decision":{...}|None, "time":{...}|None}``. Each ``remembered`` row is the slim
    ``{"id","fact","memory_count"}`` (the applied fact + the character's new fact count),
    not the whole memory list.

    The whole batch is VALIDATED before the first write: an unresolvable memories id, a
    text-less events item, or a bad decision raises BEFORE any session-log row is
    appended, so a failed call leaves no partial chronicle (retry-safe).
    """
    logged: list[dict] = []
    remembered: list[dict] = []
    decision_out: Optional[dict] = None

    # ONE critical section for every simple write (log/remember/decision). This is
    # the batching win: a single load -> mutate-all -> save, instead of one
    # lock+load+fsync-save per write. (advance_time is handled AFTER, as its own
    # locked call, because its body — worldsim ticks, effect expiry, combat guard —
    # is non-trivial and re-entering campaign_lock here would deadlock.)
    #
    # F14-3 (#795): VALIDATE-THEN-APPLY. _log_session_entry writes the session jsonl
    # IMMEDIATELY (append_log -> disk), so the old apply-and-validate interleave left a
    # crash mid-batch with the events leg already on disk and the rest dropped — a retry
    # then duplicated the chronicle rows. The non-atomic window is EVENTS ONLY (memories/
    # decision mutate the in-memory snapshot and persist only at the block-end save, so a
    # raise discards them). So we resolve EVERY item — coalesce event text + reject empty,
    # resolve every memories character_id via the _char resolver (#786, F14-8) with id
    # aliases, build the Decision with null-coerced str fields — BEFORE the first
    # append_log. Any failure now precedes the first write -> atomic-in-effect, retry-safe.
    if events or memories or decision:
        with campaign_lock(campaign_id):
            c = _require(campaign_id)

            # ---- PHASE 1: validate the whole batch (no writes) ----
            planned_events: list[dict] = []
            for i, ev in enumerate(events or []):
                if not isinstance(ev, dict):
                    raise ValueError(f"events index {i}: each item must be a dict {{kind,text,...}}")
                # log_event's alias set: text | message | content | note (text wins).
                text = ev.get("text") or ev.get("message") or ev.get("content") or ev.get("note") or ""
                if not text:
                    raise ValueError(
                        f"events index {i}: needs text (pass `text` or an alias: "
                        f"`message`/`content`/`note`)"
                    )
                planned_events.append({
                    "kind": ev.get("kind") or "narration",
                    "text": text,
                    "speaker": ev.get("speaker") or "",
                    "payload": ev.get("payload"),
                })

            planned_memories: list[tuple] = []  # (Character, fact)
            for i, mem in enumerate(memories or []):
                if not isinstance(mem, dict):
                    raise ValueError(f"memories index {i}: each item must be a dict {{character_id,fact}}")
                # Accept character_id or the id/npc_id aliases the top-level tools tolerate,
                # instead of a bare KeyError ('character_id') — the worst string on the surface.
                cid_in = mem.get("character_id") or mem.get("id") or mem.get("npc_id")
                if not cid_in:
                    raise ValueError(
                        f"memories index {i}: missing character_id "
                        f"(pass `character_id`, or the alias `id`/`npc_id`)"
                    )
                try:
                    ch = _char(c, cid_in)  # resolve-then-suggest (raises ValueError w/ did-you-mean)
                except ValueError as e:
                    raise ValueError(f"memories index {i}: {e}") from None
                fact = mem.get("fact") or ""
                planned_memories.append((ch, fact))

            planned_decision: Optional[Decision] = None
            decision_flag = ""
            if decision:
                if not isinstance(decision, dict):
                    raise ValueError("decision must be a dict {summary,...}")
                # None-coerce every str field: the DM legitimately passes chosen=null for a
                # still-open decision; `.get(k, "")` only defaults a MISSING key, an explicit
                # null still reaches pydantic's str field and string_type-crashes the batch.
                planned_decision = Decision(
                    day=c.day,
                    summary=decision.get("summary") or "",
                    options=list(decision.get("options") or []),
                    chosen=decision.get("chosen") or "",
                    rationale=decision.get("rationale") or "",
                    actor_ids=list(decision.get("actor_ids") or []),
                )
                decision_flag = str(decision.get("sets_flag") or "").strip()

            # ---- PHASE 2: apply (every item validated; first write is here) ----
            for pe in planned_events:
                entry = _log_session_entry(
                    c,
                    kind=pe["kind"],
                    text=pe["text"],
                    speaker=pe["speaker"],
                    payload=pe["payload"],
                )
                logged.append(entry.model_dump())
            for ch, fact in planned_memories:
                if fact and fact not in ch.memory:  # de-dupe identical facts (matches remember)
                    ch.memory.append(fact)
                # Slim row (#795): the FACT just applied + a count, NOT the whole growing
                # memory list per item (the old O(items x memory) quadratic echo).
                remembered.append({"id": ch.id, "fact": fact, "memory_count": len(ch.memory)})
            if planned_decision is not None:
                c.decisions.append(planned_decision)
                if decision_flag:
                    c.flags[decision_flag] = True  # content-defined; arms a matching agenda's decision_flag
                decision_out = {
                    "id": planned_decision.id, "summary": planned_decision.summary,
                    "chosen": planned_decision.chosen, "day": planned_decision.day,
                }
                if decision_flag:
                    decision_out["flag"] = decision_flag
            save_campaign(c)  # ONE atomic write for all of the above

    # advance_time as its own locked call (sequential, not nested → no deadlock).
    time_out: Optional[dict] = None
    if advance is not None:
        if not isinstance(advance, dict):
            raise ValueError("advance must be a dict {phases?,to?,note?}")
        time_out = advance_time(
            campaign_id,
            phases=int(advance.get("phases", 0) or 0),
            to=str(advance.get("to", "") or ""),
            note=str(advance.get("note", "") or ""),
        )

    return {
        "logged": logged,
        "remembered": remembered,
        "decision": decision_out,
        "time": time_out,
    }


if __name__ == "__main__":
    mcp.run()
