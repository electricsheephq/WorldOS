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


class DeathSaves(_StrictModel):
    successes: int = 0
    failures: int = 0


class Character(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("char"))
    name: str
    kind: CharacterKind = "player"
    # logical voice id; resolved to a real backend voice via content/voices/voice-map.json
    voice_id: str = "narrator-dm"

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

    # progression
    xp: int = 0
    features: list[str] = Field(default_factory=list)  # class/subclass features gained
    extra_attacks: int = 0  # extra attacks per Attack action (Extra Attack feature)
    sneak_attack_dice: str = ""  # e.g. "3d6" (rogue Sneak Attack), "" if none

    # roleplay (companion / npc)
    personality: str = ""
    attitude: str = ""  # npc disposition toward the party
    memory: list[str] = Field(default_factory=list)  # facts the npc/companion remembers
    notes: str = ""

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


class Faction(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("fac"))
    name: str
    description: str = ""
    reputation: int = 0  # -100..100


class Combatant(_StrictModel):
    character_id: str
    initiative: int = 0
    reaction_used: bool = False  # one reaction per round; refreshes at turn start


class Combat(_StrictModel):
    active: bool = False
    round: int = 0
    turn_index: int = 0
    order: list[Combatant] = Field(default_factory=list)  # sorted desc by initiative
    action_used: bool = False  # current turn's action economy
    bonus_action_used: bool = False

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
