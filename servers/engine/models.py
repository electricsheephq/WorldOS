"""ClawDnD engine state models (Pydantic v2).

Clean-room D&D 5e (SRD 5.2) campaign state. The Campaign aggregate is the single
persisted unit; the store writes it atomically so a campaign survives context
compaction and spans many sessions. The companion and every NPC are first-class
Characters (each carrying a logical voice_id), so one sheet + voice machinery
serves the player, the companion, and all NPCs.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _now() -> float:
    return time.time()


class Ability(str, Enum):
    STR = "str"
    DEX = "dex"
    CON = "con"
    INT = "int"
    WIS = "wis"
    CHA = "cha"


_ABILITY_FIELD = {
    Ability.STR: "strength",
    Ability.DEX: "dexterity",
    Ability.CON: "constitution",
    Ability.INT: "intelligence",
    Ability.WIS: "wisdom",
    Ability.CHA: "charisma",
}

# Skill -> governing ability (SRD 5.2)
SKILL_ABILITIES: dict[str, Ability] = {
    "acrobatics": Ability.DEX,
    "animal_handling": Ability.WIS,
    "arcana": Ability.INT,
    "athletics": Ability.STR,
    "deception": Ability.CHA,
    "history": Ability.INT,
    "insight": Ability.WIS,
    "intimidation": Ability.CHA,
    "investigation": Ability.INT,
    "medicine": Ability.WIS,
    "nature": Ability.INT,
    "perception": Ability.WIS,
    "performance": Ability.CHA,
    "persuasion": Ability.CHA,
    "religion": Ability.INT,
    "sleight_of_hand": Ability.DEX,
    "stealth": Ability.DEX,
    "survival": Ability.WIS,
}


class Condition(str, Enum):
    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"


CharacterKind = Literal["player", "companion", "npc", "monster"]


class _StrictModel(BaseModel):
    """Base for all state models: reject unknown fields so a typo'd update
    (e.g. {"max_hpp": 99}) raises instead of silently vanishing."""

    model_config = ConfigDict(extra="forbid")


class AbilityScores(_StrictModel):
    strength: int = 10
    dexterity: int = 10
    constitution: int = 10
    intelligence: int = 10
    wisdom: int = 10
    charisma: int = 10

    @model_validator(mode="before")
    @classmethod
    def _accept_5e_shorthand(cls, data):
        """Accept the universal 5e shorthand (str/dex/con/int/wis/cha) as well as the
        full field names. The DM/agent reaches for `{"str": 12, "dex": 19, ...}`
        reflexively; rejecting it with a bare 'Extra inputs are not permitted' was a
        silent footgun (QA finding). A long key, if also present, wins over its short
        alias; a genuine typo ('strenth') still trips extra='forbid'."""
        if isinstance(data, dict):
            abbr = {"str": "strength", "dex": "dexterity", "con": "constitution",
                    "int": "intelligence", "wis": "wisdom", "cha": "charisma"}
            out = dict(data)
            for short, long in abbr.items():
                if short in out:
                    out.setdefault(long, out[short])  # long form wins if both present
                    out.pop(short, None)
            return out
        return data

    def score(self, ability: Ability) -> int:
        return getattr(self, _ABILITY_FIELD[ability])

    def modifier(self, ability: Ability) -> int:
        return (self.score(ability) - 10) // 2


class ClassLevel(_StrictModel):
    name: str
    level: int = 1
    subclass: Optional[str] = None


class Item(_StrictModel):
    name: str
    quantity: int = Field(1, ge=0)
    weight: float = Field(0.0, ge=0)  # lbs per item
    equipped: bool = False
    requires_attunement: bool = False
    attuned: bool = False
    description: str = ""


class Currency(_StrictModel):
    cp: int = 0
    sp: int = 0
    ep: int = 0
    gp: int = 0
    pp: int = 0


class SpellSlotLevel(_StrictModel):
    maximum: int = 0
    used: int = 0


class ClassResource(_StrictModel):
    """A depletable per-rest class resource pool (Rage, Ki, Lay on Hands, Channel
    Divinity, Bardic Inspiration, Sorcery Points, Second Wind, Action Surge, Wild
    Shape, …). `used` counts expended points; `max - used` is what's left.
    `recharge` says which rest refills it: "short" pools refresh on a short OR long
    rest, "long" only on a long rest, "none" never (DM restores manually). Pools are
    data-driven from class + level; empty `class_resources` == today's behavior."""

    max: int = 0
    used: int = 0
    recharge: Literal["short", "long", "none"] = "long"


class DeathSaves(_StrictModel):
    successes: int = 0
    failures: int = 0


class ArcGate(_StrictModel):
    """One milestone on a companion's relationship arc — a personal-quest reveal, a
    romance beat, a deepened loyalty. It UNLOCKS when the companion's `attitude_value`
    (the approval gauge) reaches `threshold`; the DM then dramatizes `note`. Inert
    until unlocked, idempotent once it is — the engine reports each gate's unlock
    exactly once. Additive: a companion with no arc has no gates."""

    id: str = Field(default_factory=lambda: _new_id("gate"))
    kind: Literal["personal_quest", "romance", "loyalty", "betrayal"]
    threshold: int  # the attitude_value at/above which this gate unlocks
    unlocked: bool = False
    note: str = ""  # what the unlock means, for the DM to play


class CompanionAgenda(_StrictModel):
    """A companion's SEALED turn — the saboteur's betrayal, the zealot's defection —
    made a REAL engine-evaluated event instead of an ephemeral prompt string. It
    FIRES once when its `trigger` holds, and the DM dramatizes the fallout (a betrayal
    fires as a real `attack`, not narration). One agenda per companion; idempotent
    (a fired agenda never re-reports).

    Triggers:
    - "attitude_below": `attitude_value < value` (approval curdled past a breaking point)
    - "day_reached":    `campaign.day >= value` (a plan that comes due on a fixed day)
    - "party_vulnerable": any party member at `current_hp <= 0` OR `<= 25% of max_hp`
                          (strikes when the party is weakest)
    - "prize_seized":   the campaign flag `prize_seized` is set (the goal is in hand)
    """

    trigger: Literal["attitude_below", "day_reached", "party_vulnerable", "prize_seized"]
    value: Optional[int] = None  # REQUIRED threshold for attitude_below/day_reached; unused otherwise
    fired: bool = False
    note: str = ""  # the agenda's intent, for the DM to dramatize when it fires

    @model_validator(mode="after")
    def _require_threshold(self):
        # A threshold trigger needs an explicit value. Defaulting to 0 was a footgun: a
        # `day_reached` agenda with the value omitted satisfied `day(>=1) >= 0` and fired
        # IMMEDIATELY (M2). Fail loud at author time instead of silently arming.
        if self.trigger in ("attitude_below", "day_reached") and self.value is None:
            raise ValueError(f"agenda trigger {self.trigger!r} requires an explicit `value`")
        return self


class CompanionArc(_StrictModel):
    """A companion's relationship arc + sealed agenda — the durable engine record so a
    companion's loyalty/betrayal/personal-quest is a REAL evaluated event, not a line
    that lives only in a QA prompt. Attached to a Character via `arc`; evaluated by
    `companion_arc.evaluate`. Additive: a Character with `arc=None` behaves exactly as
    today."""

    arc_gates: list[ArcGate] = Field(default_factory=list)
    agenda: Optional[CompanionAgenda] = None


class Character(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("char"))
    name: str
    kind: CharacterKind = "player"
    # logical voice id; resolved to a real backend voice via content/voices/voice-map.json
    voice_id: str = "narrator-dm"
    # which location an NPC/monster is anchored to (where it was introduced), so the
    # play-view shows the local cast "in the scene" — not the whole world roster.
    location_id: Optional[str] = None

    # identity / build
    race: str = ""
    classes: list[ClassLevel] = Field(default_factory=list)
    background: str = ""
    alignment: str = ""

    abilities: AbilityScores = Field(default_factory=AbilityScores)
    proficiency_bonus: int = 2
    skill_proficiencies: list[str] = Field(default_factory=list)
    skill_expertise: list[str] = Field(default_factory=list)
    saving_throw_proficiencies: list[Ability] = Field(default_factory=list)

    # vitals
    armor_class: int = 10
    max_hp: int = 1
    current_hp: int = 1
    temp_hp: int = 0
    hit_dice: str = ""  # e.g. "3d8"
    hit_dice_remaining: int = 0
    speed: int = 30
    initiative_bonus: int = 0

    # status
    conditions: list[Condition] = Field(default_factory=list)
    exhaustion: int = 0  # 0-6
    concentration: Optional[str] = None  # spell currently concentrated on
    death_saves: DeathSaves = Field(default_factory=DeathSaves)
    dead: bool = False
    stable: bool = False  # stabilized at 0 HP; no longer rolling death saves

    # damage modifiers — free-text damage types ("fire", "bludgeoning") and, for
    # condition_immunities, condition names ("poisoned"). Usually set when spawning
    # a monster from the bestiary; honored by combat.apply_damage.
    damage_resistances: list[str] = Field(default_factory=list)
    damage_immunities: list[str] = Field(default_factory=list)
    damage_vulnerabilities: list[str] = Field(default_factory=list)
    condition_immunities: list[str] = Field(default_factory=list)

    # resources
    inventory: list[Item] = Field(default_factory=list)
    currency: Currency = Field(default_factory=Currency)
    spell_slots: dict[int, SpellSlotLevel] = Field(default_factory=dict)  # slot level -> slots
    spells_known: list[str] = Field(default_factory=list)
    spells_prepared: list[str] = Field(default_factory=list)
    # Depletable per-rest class resource pools, keyed by a stable resource id
    # ("rage", "ki", "lay_on_hands", "channel_divinity", "bardic_inspiration",
    # "sorcery_points", "second_wind", "action_surge", "wild_shape"). Derived from
    # class + level; empty == today's behavior, so existing campaigns deserialize
    # unchanged. See ClassResource / srd_tables.class_resources_through.
    class_resources: dict[str, ClassResource] = Field(default_factory=dict)

    # progression
    xp: int = 0
    features: list[str] = Field(default_factory=list)  # class/subclass features gained
    extra_attacks: int = 0  # extra attacks per Attack action (Extra Attack feature)
    sneak_attack_dice: str = ""  # e.g. "3d6" (rogue Sneak Attack), "" if none
    xp_value: int = 0  # XP this creature grants when defeated (set when spawned from the bestiary; drives auto-XP)

    # roleplay (companion / npc)
    personality: str = ""
    attitude: str = ""  # npc disposition toward the party (free text: "guarded", or a track value)
    # Numeric per-NPC relationship toward the party, -100..+100 (0 = neutral). The
    # free-text `attitude` reads well for the DM; this gives the viewer a precise bar
    # position (and lets the DM reward/punish choices in points). Additive: 0 == today's
    # behavior, so the dashboard falls back to the keyword heuristic when it's untouched.
    attitude_value: int = 0
    memory: list[str] = Field(default_factory=list)  # facts the npc/companion remembers
    notes: str = ""
    # structured identity — fed by canon ingestion (S2.5) + shown in portrait cards;
    # all optional (empty = today's behavior). Give the DM concrete material to voice.
    appearance: str = ""   # physical description (hair, build, scars, dress) → portrait prompts
    mannerisms: str = ""   # tics/gestures/speech habits ("taps two fingers when thinking")
    backstory: str = ""    # origin + current want, in brief
    # Companion relationship arc + sealed agenda (S4). None == today's behavior, so an
    # existing snapshot with no `arc` deserializes unchanged. Evaluated by
    # companion_arc.evaluate; populated by set_companion_arc / the ending-seed loader.
    arc: Optional["CompanionArc"] = None

    @model_validator(mode="after")
    def _clamp_vitals(self) -> "Character":
        # The engine is the authority; keep vitals within valid 5e ranges.
        self.max_hp = max(1, self.max_hp)
        self.current_hp = max(0, min(self.current_hp, self.max_hp))
        self.temp_hp = max(0, self.temp_hp)
        self.exhaustion = max(0, min(self.exhaustion, 6))
        for slot in self.spell_slots.values():
            slot.maximum = max(0, slot.maximum)
            slot.used = max(0, min(slot.used, slot.maximum))
        for res in self.class_resources.values():
            res.max = max(0, res.max)
            res.used = max(0, min(res.used, res.max))
        return self

    @property
    def total_level(self) -> int:
        return sum(c.level for c in self.classes) or 1

    def ability_modifier(self, ability: Ability) -> int:
        return self.abilities.modifier(ability)

    def skill_bonus(self, skill: str) -> int:
        ability = SKILL_ABILITIES[skill]
        bonus = self.ability_modifier(ability)
        if skill in self.skill_expertise:
            bonus += 2 * self.proficiency_bonus
        elif skill in self.skill_proficiencies:
            bonus += self.proficiency_bonus
        return bonus

    def saving_throw_bonus(self, ability: Ability) -> int:
        bonus = self.ability_modifier(ability)
        if ability in self.saving_throw_proficiencies:
            bonus += self.proficiency_bonus
        return bonus


QuestStatus = Literal["active", "completed", "failed"]


class Quest(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("quest"))
    title: str
    description: str = ""
    status: QuestStatus = "active"
    objectives: list[str] = Field(default_factory=list)
    completed_objectives: list[str] = Field(default_factory=list)
    giver_id: Optional[str] = None  # the NPC who gave the quest
    location_id: Optional[str] = None  # where it's anchored


class Location(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("loc"))
    name: str
    description: str = ""
    connections: list[str] = Field(default_factory=list)  # location ids
    notes: str = ""
    visited: bool = False
    # Optional axial-hex (q, r) coords — PRESENTATION ONLY (the viewer renders them).
    # The engine's adjacency/travel is governed solely by `connections`; coords are
    # never used for movement or distance.
    hex: Optional[tuple[int, int]] = None
    # Spatial "walk-time" context (fables-style) — ADDITIVE; empty = today's behavior.
    region: str = ""  # the parent zone this location nests in ("South West Odrun Fell")
    travel_times: dict[str, int] = Field(default_factory=dict)  # connected location id -> walk minutes


class Faction(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("fac"))
    name: str
    description: str = ""
    reputation: int = 0  # -100..100


class Combatant(_StrictModel):
    character_id: str
    initiative: int = 0
    reaction_used: bool = False  # one reaction per round; refreshes at turn start
    # Tactical position — the named region this combatant occupies (S2.7). "" =
    # theater-of-the-mind (no positional model); set only when the scene declares
    # zones via set_zones. Additive: existing combats deserialize with "".
    zone: str = ""


class Zone(_StrictModel):
    """A named tactical region of a combat scene ("the doorway", "the rafters",
    "the altar dais") — the engine's positional model. NOT a coordinate grid: LLM
    agents reason about named regions and their adjacency far more reliably than
    (x, y). Movement and melee range are governed by `adjacent`; `description` is
    flavor for the DM. Additive — a combat with no zones is theater-of-the-mind."""

    name: str
    description: str = ""
    adjacent: list[str] = Field(default_factory=list)  # names of directly-reachable zones


class Combat(_StrictModel):
    active: bool = False
    round: int = 0
    turn_index: int = 0
    order: list[Combatant] = Field(default_factory=list)  # sorted desc by initiative
    action_used: bool = False  # current turn's action economy
    bonus_action_used: bool = False
    # Tactical regions for THIS fight (S2.7). Empty = theater-of-the-mind: range/
    # movement gating is inert and nothing changes. Additive default.
    zones: list[Zone] = Field(default_factory=list)

    @property
    def current_combatant_id(self) -> Optional[str]:
        if not self.active or not self.order:
            return None
        return self.order[self.turn_index % len(self.order)].character_id


class SessionLogEntry(_StrictModel):
    t: float = Field(default_factory=_now)
    kind: str = "narration"  # narration | dialogue | roll | system | combat
    text: str
    speaker: Optional[str] = None  # character id or name


class Consequence(_StrictModel):
    """A time-deferred world event: something that comes due on a future in-world
    day (a ritual completes, a rival acts, reinforcements arrive). Makes a series
    of adventures feel like a living campaign rather than disconnected dungeons."""

    id: str = Field(default_factory=lambda: _new_id("conseq"))
    trigger_day: int  # the in-world day this comes due (Campaign.day)
    text: str  # what happens, for the DM to narrate
    note: str = ""  # why / source (e.g. "the player let the cultist escape")
    fired: bool = False
    thread_id: str = ""  # non-empty => a recurring background "world beat" from a standing thread (world-sim); reschedules itself on tick


class Decision(_StrictModel):
    """A choice the party made — recorded so the DM/companions can call back to it
    later ('last time we trusted Grett...'). Snapshot is the source of truth; the
    memory ledger indexes these for search."""

    id: str = Field(default_factory=lambda: _new_id("decision"))
    t: float = Field(default_factory=_now)
    day: int = 1
    summary: str  # the decision in one line
    options: list[str] = Field(default_factory=list)  # what was on the table
    chosen: str = ""  # what the party went with
    rationale: str = ""  # why
    actor_ids: list[str] = Field(default_factory=list)  # who weighed in / decided


class HouseRules(_StrictModel):
    """Campaign-level rule toggles the DM honors when adjudicating. Most are
    advisory (the DM applies them); a few may be wired into the engine over time."""

    difficulty: Literal["easy", "standard", "hard"] = "standard"
    critical_max_damage: bool = False  # crits add max die value instead of doubling dice
    flanking_advantage: bool = False  # flanking grants advantage
    slow_natural_healing: bool = False  # long rest restores no HP without spending Hit Dice
    feats_allowed: bool = True
    multiclass_allowed: bool = True
    dm_can_fudge: bool = False  # allow DM dice fudging (off by default)


class WorldState(_StrictModel):
    """The campaign's canonical, structured world-state — the load-bearing FACTS the
    DM must never narrate against (set by the chosen ending; default == today's base
    world). The free-text `era`/`lore` prose is a *view* over this; when prose and
    this row disagree (the two-surface bug: `recall` vs `lookup_lore`), THIS is canon.

    Two halves, deliberately split so the engine stays setting-agnostic (lorebook's
    "never hard-code a setting" contract):
    - `world_tenor` — a TYPED, GENERIC dial (every setting has a mood). It is the only
      field the engine may branch on (e.g. difficulty later); a closed Literal so a
      typo can't silently invent a tenor.
    - `facts` — the SETTING-SPECIFIC decisionals as a free `dict[str,str]` (e.g.
      {"netherbrain":"claimed","the_emperor":"slain","baldurs_gate":"occupied"}). The
      keys live in CONTENT (the ending files), never in engine code, so Sundered Reach
      (or any world) defines its own. Surfaced verbatim as a canon header on lore reads.

    ADDITIVE: an empty/absent WorldState is today's behavior (no header, no
    de-confliction). Malformed ending blocks DEGRADE (the setter skips them), they
    never abort start_world — mirroring the companion_seeds guard."""

    world_tenor: Literal["hopeful", "uneasy", "grim"] = "hopeful"
    facts: dict[str, str] = Field(default_factory=dict)

    def canon_header(self) -> str:
        """Render this state as a compact, authoritative one-liner the engine prepends to
        lore reads (recall + lookup_lore) so the DM's ground truth for the scene is the
        structured row — with the prose pages below explicitly framed as background that
        may describe other timelines. Setting-agnostic: it just lists the dial + whatever
        facts the content defined, never naming a specific setting's keys in engine code."""
        facts = ", ".join(f"{k}={v}" for k, v in self.facts.items() if str(v).strip())
        body = f"tenor={self.world_tenor}" + (f" — {facts}" if facts else "")
        return (
            f"CURRENT WORLD (authoritative): {body}. "
            "Treat as canon; pages/memories below are background and may describe other timelines."
        )


class Campaign(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("camp"))
    title: str
    ruleset: str = "SRD 5.2"
    summary: str = ""
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)

    current_location_id: Optional[str] = None
    day: int = 1  # in-world day counter
    time_of_day: str = "morning"
    map_kind: Literal["hex", "none"] = "none"  # how the play-view renders the map
    # World-state boolean flags the DM/engine set to gate events — e.g. "prize_seized"
    # drives a companion's prize_seized agenda (S4). Additive: empty == today's behavior.
    flags: dict[str, bool] = Field(default_factory=dict)
    # The replayability layer (S6): each MAJOR world quest's resolved outcome, picked once
    # at world-gen (ending-tied when the chosen ending's world_state.facts match, else a
    # seeded random roll) — maps quest_id -> the resolved outcome_id. The resolved outcome's
    # lore/hook prose is also appended to `lore` so recall/lookup_lore surface it under the
    # canon header. Additive: empty == today's behavior (a world with no quest_variants).
    # Mirrors `flags`/`world_state` — keys/values live in CONTENT, never engine code.
    quest_outcomes: dict[str, str] = Field(default_factory=dict)

    characters: dict[str, Character] = Field(default_factory=dict)  # id -> Character (PCs, companion, NPCs)
    party: list[str] = Field(default_factory=list)  # character ids that are PCs / companions
    quests: dict[str, Quest] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)
    factions: dict[str, Faction] = Field(default_factory=dict)
    combat: Combat = Field(default_factory=Combat)
    house_rules: HouseRules = Field(default_factory=HouseRules)

    active_session_id: Optional[str] = None
    session_ids: list[str] = Field(default_factory=list)  # play sessions in order
    consequences: list[Consequence] = Field(default_factory=list)  # time-deferred world events
    decisions: list[Decision] = Field(default_factory=list)  # party choices, for callbacks
    scenes: list[dict] = Field(default_factory=list)  # authored scene guidance (read_aloud, dm_notes, checks) the DM reads via get_scene — inert content, never computed on
    lore: list[str] = Field(default_factory=list)  # world-bible facts (history, standing threads) — indexed into recall so the DM keeps a generated world consistent
    world_id: str = ""  # the world seed this campaign was started from (for lookup_lore over its lore corpus)
    era: str = ""  # in-world chronology ("1492 DR, the winter after the Absolute") so the DM keeps the timeline straight — who's alive, what's already happened
    ending_id: str = ""  # the post-state ending OVERLAY this world was seeded in (content/worlds/<id>/endings/<id>.json), or "" for the base/default state
    # The canonical, structured world-state set by the chosen ending — the authoritative
    # FACTS the DM narrates within (surfaced as a canon header on recall/lookup_lore).
    # Additive: None == today's behavior (no header, no de-confliction). Mirrors ending_id/flags.
    world_state: Optional[WorldState] = None
    # The active ending's retraction predicate: case-insensitive substrings whose presence
    # in an authored lore excerpt marks a now-SUPERSEDED fact (e.g. "Gortash is dead" under
    # the tyranny ending). Stored at seed time so lookup_lore can de-conflict the .md corpus
    # on the SAME basis _apply_ending_overlay already de-conflicts c.lore. Additive: [] == today.
    lore_supersedes: list[str] = Field(default_factory=list)
    leveling_mode: Literal["xp", "milestone"] = "xp"  # "xp": end_combat auto-awards defeated monsters' XP to the party; "milestone": DM levels by story beat (no auto-XP)
    # Narrative pacing the DM honors when setting scene density. "adventure" (default):
    # tension, momentum, encounters. "downtime": slower — let scenes breathe, lean into
    # social/shopping/recovery. Advisory (the DM reads it via get_state); never computed on.
    pacing_mode: Literal["adventure", "downtime"] = "adventure"
