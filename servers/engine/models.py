"""WorldOS engine state models (Pydantic v2).

Clean-room D&D 5e (SRD 5.2) campaign state. The Campaign aggregate is the single
persisted unit; the store writes it atomically so a campaign survives context
compaction and spans many sessions. The companion and every NPC are first-class
Characters (each carrying a logical voice_id), so one sheet + voice machinery
serves the player, the companion, and all NPCs.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal, Optional
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
CompanionQuestStatus = Literal["locked", "available", "active", "resolved", "failed"]


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
        """Accept the universal 5e shorthand (str/dex/con/int/wis/cha) AND any case
        (`STR`, `Str`, `Strength`) as well as the full field names. The DM/agent reaches
        for `{"STR": 12, "dex": 19, ...}` reflexively; rejecting it with a bare 'Extra
        inputs are not permitted' was a silent footgun (QA: hit with both lowercase AND
        uppercase). A long key, if also present, wins over its short alias; a genuine typo
        ('strenth') still trips extra='forbid'."""
        if isinstance(data, dict):
            abbr = {"str": "strength", "dex": "dexterity", "con": "constitution",
                    "int": "intelligence", "wis": "wisdom", "cha": "charisma"}
            longs = set(abbr.values())
            out: dict = {}
            shorts: dict = {}
            for k, v in data.items():
                kl = str(k).strip().lower()
                if kl in longs:
                    out[kl] = v          # a full field name (any case) -> canonical lowercase
                elif kl in abbr:
                    shorts[abbr[kl]] = v  # a short alias (any case) -> its long target, deferred
                else:
                    out[k] = v            # unknown -> pass through so a real typo still trips forbid
            for long, v in shorts.items():
                out.setdefault(long, v)   # the short alias fills in only where the long wasn't given
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
    # A die type for pools that ROLL a die rather than spend flat points — "d8" for a Battle
    # Master's Superiority Dice, "d6" for a Psi die, etc. Empty == a point pool (Ki, Rage).
    size: str = ""
    # True == registered by the DM via set_class_resource for a subclass / feat / homebrew
    # resource the SRD class tables don't seed. Custom pools are carried forward verbatim
    # across a level-up re-derive (the engine never recomputes a value it didn't author).
    custom: bool = False


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
    # Optional link into first-class companion quest arcs (#70). A personal_quest gate may
    # make the linked arc/stage available once; it never decides success or failure.
    quest_arc_id: str = ""
    stage_id: str = ""


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

    Decision-gated escalation (Quest & Arc engine, Layer 2):
    `decision_flag` names a CONTENT-defined campaign flag (e.g. "let_daughter_die",
    "took_bribe") that, when present AND True in `Campaign.flags`, ESCALATES an
    ``attitude_below`` agenda — a recorded player CHOICE makes the turn far likelier
    ("let the farmer's daughter die → the knight-companion turns on you"). It BOOSTS the
    rising snap probability (companion_arc._attitude_below_snap_p); it never makes a
    deterministic event fire on its own and never names the breaking point — the companion
    stays in-character, the betrayal is still rolled. ADDITIVE: empty `decision_flag` ==
    today's #142/#158 behavior byte-for-byte, so old snapshots round-trip unchanged. The
    flag NAME lives in content (set via set_flag / record_decision(..., sets_flag=...)),
    never in engine code.
    """

    trigger: Literal["attitude_below", "day_reached", "party_vulnerable", "prize_seized"]
    value: Optional[int] = None  # REQUIRED threshold for attitude_below/day_reached; unused otherwise
    fired: bool = False
    note: str = ""  # the agenda's intent, for the DM to dramatize when it fires
    # A CONTENT-defined campaign flag whose presence+True in Campaign.flags ESCALATES this
    # agenda's betrayal weight (Layer 2). Only the `attitude_below` trigger reads it. Empty
    # == today's behavior. Never engine-coded; the DM/content sets the flag (set_flag /
    # record_decision sets_flag) when the gating choice is made.
    decision_flag: str = ""

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


class CompanionQuestStage(_StrictModel):
    """One engine-owned stage inside a companion's personal quest arc.

    The status is a bounded lifecycle enum, not free prose; an optional `quest_id` points
    at the player-facing tracked Quest projection when the DM explicitly links one."""

    id: str = Field(default_factory=lambda: _new_id("cqstage"))
    title: str
    status: CompanionQuestStatus = "locked"
    unlock_gate_id: str = ""
    location_id: str = ""
    quest_id: str = ""
    note: str = ""


class CompanionQuestArc(_StrictModel):
    """First-class companion personal quest lifecycle (#70).

    This is the character-owned campaign state machine. `Quest` remains the optional
    player-facing tracker; sync is one-way from this arc when an explicit engine API says
    to link/update a Quest."""

    id: str = Field(default_factory=lambda: _new_id("cqarc"))
    companion_id: str = ""
    title: str
    status: CompanionQuestStatus = "locked"
    stages: list[CompanionQuestStage] = Field(default_factory=list)
    quest_ids: list[str] = Field(default_factory=list)
    note: str = ""

    @model_validator(mode="after")
    def _collect_stage_quest_ids(self) -> "CompanionQuestArc":
        seen: list[str] = []
        for qid in self.quest_ids:
            if qid and qid not in seen:
                seen.append(qid)
        for stage in self.stages:
            if stage.quest_id and stage.quest_id not in seen:
                seen.append(stage.quest_id)
        self.quest_ids = seen
        return self


# --- Quest & Arc engine, Layer 3: first-class Event / ParleyOption / Outcome ----------------
# The Kingmaker "stumble-into" decisional: a content-authored choice point whose options carry a
# DETERMINISTIC ripple (set a flag, shift faction reputation, schedule a Consequence) AND can STAGE
# the already-merged Layer-2 companion flip by setting a `decision_flag`. A THIN first-class wrapper
# over machinery that already ships — no new resolver, no new state machine:
#   * the ripple reuses worldsim._apply_structured_effect byte-for-byte;
#   * the L2 flip reuses CompanionAgenda.decision_flag (Layer 2) — they meet at Campaign.flags;
#   * the schedule echo reuses consequences.schedule (Layer 1's rule-of-three pattern).
# ADDITIVE: a campaign with no events behaves exactly as today; old snapshots round-trip.

# A contract-safe trigger reads ONLY engine-MUTATED values (flags / faction reputation / day) —
# never near-constant fiction (the questgen.py:7-19 discipline). Mirrors the CompanionAgenda
# trigger vocabulary so there is one mental model for "when does an arc beat become available".
EventTrigger = Literal["manual", "flag_set", "day_reached", "reputation_at"]


class Outcome(_StrictModel):
    """The DETERMINISTIC ripple a chosen ParleyOption applies (Quest & Arc engine, Layer 3).

    The payload reuses worldsim._apply_structured_effect's keys BYTE-FOR-BYTE (so a picked
    option ripples through the exact same engine path a backlog item / strategic project does),
    plus three thin extension keys the resolver handles inline:

      * the shared keys (applied via _apply_structured_effect): ``flag`` -> campaign.flags[flag]
        = True; ``faction_id`` + ``reputation_delta`` -> clamped reputation shift;
        ``controller_id`` + ``location_id`` -> a ``control:loc=fac`` flag; ``npc_name`` -> an
        ``arrival:`` stub flag.
      * ``decision_flag`` (NEW — the L2<->L3 seam): sets ``campaign.flags[decision_flag] = True``,
        identical to ``record_decision(sets_flag=...)``. This ARMS any ``attitude_below``
        CompanionAgenda whose ``decision_flag`` matches — the owner's "take the bribe -> the
        knight-companion turns". Layer 3 sets the flag; the already-merged Layer 2 reads it.
      * ``schedule_in_days`` + ``schedule_text`` (NEW): schedules a follow-on Consequence via
        consequences.schedule (the rule-of-three echo) so the choice lingers/returns later.
      * ``narrate`` (NEW): a DM-facing one-liner — the human-readable summary of the ripple
        (passed as the `fallback` to _apply_structured_effect).

    EVERY value is CONTENT-defined; the engine never invents a flag name or a reputation delta.
    An empty Outcome is a pure no-op (the engine still records the pick; nothing else moves).
    Setting-agnostic: no engine-coded taxonomy, same discipline as the merged L1/L2."""

    # --- keys shared with worldsim._apply_structured_effect (applied verbatim through it) ---
    flag: str = ""  # -> campaign.flags[flag] = True
    faction_id: str = ""  # paired with reputation_delta -> clamped -100..100 shift
    reputation_delta: int = 0
    controller_id: str = ""  # paired with location_id -> a `control:loc=fac` flag
    location_id: str = ""
    npc_name: str = ""  # -> an `arrival:<name>` stub flag
    # --- the three thin extension keys the resolver handles inline ---
    decision_flag: str = ""  # NEW: campaign.flags[decision_flag]=True — arms a matching L2 agenda
    schedule_in_days: int = 0  # NEW: with schedule_text -> consequences.schedule (rule-of-three echo)
    schedule_text: str = ""
    narrate: str = ""  # DM-facing one-liner (the _apply_structured_effect `fallback`)


class ParleyOption(_StrictModel):
    """One tagged choice in an Event's parley menu (Quest & Arc engine, Layer 3).

    Mirrors the slot shape `generate_parley_options` supplies (an alignment/skill `tag`, an
    optional `skill`+`dc` the DM may gate the pick behind) but ADDS a deterministic `outcome`:
    where today the DM hand-routes a freeform pick to skill_check / social_check / record_decision,
    a ParleyOption already KNOWS its ripple. The engine never authors the prose — the `label` is a
    short tagged choice the DM voices; resolution is the `outcome`."""

    label: str  # the short tagged choice the player picks ("Take the bribe — CN")
    tag: str = ""  # an alignment/skill hint (mirrors generate_parley_options' tagging guidance)
    skill: str = ""  # optional gated check the DM may run before applying the outcome (routes to skill_check)
    dc: int = 0
    outcome: "Outcome" = Field(default_factory=lambda: Outcome())


class Event(_StrictModel):
    """A first-class stumble-into decisional (Quest & Arc engine, Layer 3).

    A content-authored choice point: when its contract-safe ``trigger`` holds (flags / faction
    reputation / day — never fiction), ``present_events`` surfaces it as a soft nudge; the DM
    voices the ``prompt`` and the ``options`` (relayed to the player via the #141 parley surface),
    and ``resolve_event`` applies the chosen option's deterministic Outcome. IDEMPOTENT: a fired
    Event sets ``resolved=True`` and never re-presents / re-applies (like a fired Consequence)."""

    id: str = Field(default_factory=lambda: _new_id("event"))
    trigger: EventTrigger = "manual"  # how the Event becomes available (default: DM/content surfaces it)
    # The predicate operands the trigger reads (engine-mutated values only):
    #   flag_set     -> trigger_value is the flag name; available when campaign.flags[name] is True.
    #   day_reached  -> trigger_threshold is the day; available when campaign.day >= threshold.
    #   reputation_at-> trigger_faction_id + trigger_threshold; available when that faction's
    #                   reputation >= threshold (or <= threshold when threshold is negative — the
    #                   sign picks the direction, mirroring the design's `reputation_delta` sign use).
    #   manual       -> always available until resolved (the DM/content drops it; the default).
    trigger_value: str = ""  # flag name for `flag_set`
    trigger_faction_id: str = ""  # faction id for `reputation_at`
    trigger_threshold: int = 0  # day for `day_reached`; reputation level for `reputation_at`
    prompt: str = ""  # the situation the DM voices
    options: list["ParleyOption"] = Field(default_factory=list)  # the tagged choices; freeform is ALWAYS also allowed (#141)
    anchor_npc_id: str = ""  # optional canon-NPC binding (the owner's priority — bind to a roster NPC)
    resolved: bool = False  # idempotency: a fired Event never re-presents or re-applies


class CompanionDossier(_StrictModel):
    """A companion's structured identity — the OPERATIONAL state the engine's living-world
    systems act on (camp scheduling, banter selection, approval causes, companion quest
    arcs). It is NOT a second copy of the long biographical prose: `personality`/`backstory`
    stay where they are; the dossier holds TERSE, machine-usable tags/summaries so a wound,
    want, value, or banter hook is a real engine fact rather than a line buried in prompt
    prose or a one-off `ArcGate.note` (#68 / epic #58).

    Every field is empty by default, so a Character with `companion_dossier=None` (and a
    seeded dossier with any subset of fields) behaves exactly as today — the additive-
    default contract that keeps every existing snapshot loadable unchanged. Attached to a
    Character via `companion_dossier`; seeded from `npc_roster`, canon character JSON, and
    ending `companion_seeds`; a minimal one is synthesized at `recruit_companion` only when
    none exists.

    Keep entries short (a tag or one clause). Do NOT paste long copied wiki/proprietary
    lore here — the licensing/content guard (no long copied prose in committed content)
    applies; the dossier is for systems to act on, not a biography."""

    # The defining hurt that shapes the companion — kept to a clause, not a chapter.
    wound: str = ""
    # What the companion is pulling toward / pushing away from — short goal/aversion tags.
    wants: list[str] = Field(default_factory=list)
    fears: list[str] = Field(default_factory=list)
    # The moral spine the approval system rewards against ("mercy", "duty", "freedom").
    values: list[str] = Field(default_factory=list)
    # Concrete causes that move the approval gauge — what wins/loses this companion's regard.
    approval_likes: list[str] = Field(default_factory=list)
    approval_dislikes: list[str] = Field(default_factory=list)
    # Themes the (future) deterministic banter scheduler draws on, so camp talk isn't generic.
    banter_tags: list[str] = Field(default_factory=list)
    # Seed prompts the DM can voice at camp — terse situational hooks, not authored prose.
    camp_prompts: list[str] = Field(default_factory=list)
    # Standing ties to other figures: id-or-name -> a short relationship tag ("old ally",
    # "estranged sister"). Keys live in CONTENT (the seed files), never engine code.
    relationships: dict[str, str] = Field(default_factory=dict)


class RepeatSave(_StrictModel):
    """An END-OF-TURN repeat saving throw that the ENGINE rolls for the holder of an
    ActiveEffect, so a "save-ends" spell (Hold Person → paralyzed: "the target repeats
    the save at the end of each of its turns, ending the effect on a success") doesn't
    leave its victim locked forever because the DM forgot to prompt the save (#209).

    Carried on the TARGET-side ActiveEffect (the one whose holder owes the save).
    `next_turn` rolls `saving_throw_bonus(ability)` vs `dc` for the combatant whose turn
    is ENDING; on a SUCCESS the engine removes the effect (and, when `ends_effect`, the
    condition it imposed + the caster's concentration twin); on a FAILURE the effect
    persists for another round. The DM narrates the surfaced result — no prompt needed.

    ADDITIVE: optional on ActiveEffect (None == today's behavior — no end-of-turn save),
    so every existing snapshot round-trips unchanged and only a save-ends effect sets it."""

    ability: Ability  # which save the holder rolls (Hold Person → WIS)
    dc: int  # the caster's spell save DC (fixed at cast)
    # On a successful save, end the effect (the 5e "ending the effect on a success" clause).
    # The few save-ends effects that DON'T end on a single success (e.g. a recurring poison
    # tick) can carry ends_effect=False — the save still rolls + is reported, but the marker
    # stays. Defaults True (the Hold Person / paralysis case).
    ends_effect: bool = True


class ActiveEffect(_StrictModel):
    """A timed spell effect the ENGINE tracks so it auto-expires instead of relying
    on the DM to remember (Bless for 10 rounds, Hex for 1 hour, Mage Armor for 8
    hours). Set by `cast_spell` when the cast spell has a trackable duration; counted
    down by `next_turn` (combat) and the clock-advance tools (`advance_time`,
    `long_rest`, `short_rest`, `travel_to`) out of combat; reported in those tools'
    returns as `expired_effects` so the DM narrates "Bless fades".

    DURATION is stored as a NORMALIZED remaining time, split by granularity (`scale`):
      * "rounds"  — `rounds_remaining` counts combat rounds (1 round = 6s). Decremented
                    per `next_turn`; out of combat, any time-of-day phase advance (a
                    phase ≫ a round) expires it.
      * "minutes" — same field, pre-converted (1 minute = 10 rounds), so combat decrements
                    it naturally; out of combat a phase advance expires it (a phase ≫ a minute).
      * "hours"   — clock-based: expires when the in-world clock reaches/passes
                    (`expires_day`, `expires_phase_index`), AND on a long rest
                    (`until_long_rest=True`) — covers Mage Armor surviving combat but
                    ending overnight.
      * "days"    — clock-based (`expires_day`/`expires_phase_index`); survives a long rest.

    CONCENTRATION: when `concentration=True` this effect is the engine-tracked twin of
    `Character.concentration` (one source of truth) — it is removed the instant
    concentration breaks (failed save / incapacitation / drop to 0 HP / death), via
    `combat.expire_concentration_effects`.

    ADDITIVE: `Character.active_effects` defaults to `[]`, so every existing snapshot
    deserializes unchanged and a campaign that never casts a timed spell behaves exactly
    as today. Conditions-with-durations are OUT OF SCOPE here (spells only)."""

    name: str  # the spell's canonical name
    source_id: str = ""  # the caster's character id (who cast it)
    concentration: bool = False  # mirrors Character.concentration; ends when it breaks
    scale: Literal["rounds", "minutes", "hours", "days"] = "rounds"
    # combat-grained remainder (meaningful for "rounds"/"minutes"; minutes pre-converted
    # to rounds at cast). Decremented per turn in combat.
    rounds_remaining: int = 0
    # clock deadline for hour/day-scale effects, computed at cast from the campaign
    # clock + duration; the effect expires once (day, phase) reaches/passes this.
    expires_day: int = 0
    expires_phase_index: int = 0
    # hour-scale buffs also end on a long rest (an overnight ~8h) regardless of the
    # phase math; day-scale ones survive it.
    until_long_rest: bool = False
    # Combat rider flag (#194): this effect grants ADVANTAGE to the NEXT attack roll made
    # against its holder (Guiding Bolt's "the next attack roll against it has Advantage").
    # combat.attack_modifiers reads it so the engine auto-applies advantage instead of
    # relying on the DM to pass advantage=True; attack() consumes the effect after that one
    # attack resolves (one-shot). Defaults False, so every existing effect (Bless, Hex, Mage
    # Armor) and every old snapshot is untouched — only an advantage-granting rider sets it.
    grants_advantage: bool = False
    # END-OF-TURN repeat save (#209): a "save-ends" spell (Hold Person, Hypnotic Pattern,
    # a monster's hold) carries this so `next_turn` rolls the holder's recurring save and
    # frees them on a success instead of locking them indefinitely. None == no repeat save
    # (every existing effect / old snapshot). See RepeatSave.
    repeat_save: Optional[RepeatSave] = None
    # The condition this effect IMPOSED on its holder (Hold Person → "paralyzed"), so that
    # when a repeat save ENDS the effect the engine can also clear that condition (one source
    # of truth — the marker and the condition came together, they leave together). Empty ==
    # the effect imposes no engine-tracked condition (a pure buff like Bless). Mainly read
    # alongside repeat_save; only a condition-imposing effect sets it.
    imposes_condition: Optional[Condition] = None
    # AC formula metadata for armor-setting effects such as Mage Armor. These default to 0 so
    # old snapshots and non-AC effects deserialize unchanged; attack() can still fall back to
    # live sheet values when an older Mage Armor effect lacks the metadata.
    armor_base_ac: int = 0
    armor_formula_ac: int = 0


class PendingDamageBonus(_StrictModel):
    """A DECLARED-but-not-yet-applied extra damage roll the NEXT attack folds in (#213).

    A Battle Master damage maneuver (Trip Attack, Menacing Attack, …) reads "you add the
    superiority die to the attack's damage roll." But declaring the maneuver and resolving
    the strike are two engine calls (`use_resource(superiority_dice, maneuver=…)` then
    `attack`), so — exactly like a spell's on-hit rider (PendingOnHitRider, #186) — the die
    is ROLLED when the resource is spent and stashed HERE on the combatant; the next
    `attack()` adds `amount` to that strike's damage and clears this record (consumed once,
    never double-applied). One source of truth: the roll happens at spend time so the bonus
    is real without the DM remembering, and the attack just reads the rolled total.

    ADDITIVE: `Character.pending_damage_bonus` defaults to None, so every existing snapshot
    round-trips unchanged and a `use_resource` call with NO maneuver never creates one — a
    plain superiority-die spend (or any other pool) behaves exactly as today."""

    amount: int  # the already-rolled die result added to the next attack's damage
    source: str = ""  # the maneuver name (Trip Attack, Menacing Attack, …) for surfacing
    resource: str = ""  # the pool it was spent from (e.g. "superiority_dice")
    expr: str = ""  # the die expression rolled (e.g. "1d8") for the surfaced breakdown
    detail: str = ""  # the dice-roller's human detail (e.g. "1d8[6] = 6")
    damage_type: str = ""  # type of the added damage; "" == same type as the weapon strike


class PendingOnHitRider(_StrictModel):
    """An attack-roll spell's ON-HIT rider effect that has NOT yet landed (#186).

    5e attack-roll spells (Guiding Bolt: "on a hit ... the next attack roll against
    it has Advantage") grant a timed effect on the TARGET *only when the spell attack
    hits*. But the cast and the attack are two separate engine calls (`cast_spell`
    then `attack`), so the effect must not be written to the target at cast time —
    a missed bolt would wrongly leave the marker, and a re-cast would phantom-stack
    a second one.

    So `cast_spell` records this PENDING rider on the CASTER (one source of truth,
    keyed by `target_id` + spell `name`) instead of writing the ActiveEffect; the
    next `attack()` whose attacker == this caster and target == `target_id`
    materializes the ActiveEffect on the target on a HIT, or discards this record on
    a MISS. It carries the duration descriptor (the same `scale`/`rounds`/clock
    fields `cast_spell` computed) so the effect rebuilt on hit is identical to the
    one that would have been written at cast.

    ADDITIVE: `Character.pending_on_hit_riders` defaults to `[]`, so every existing
    snapshot deserializes unchanged and a campaign that never casts an attack-roll
    rider spell behaves exactly as today. Only attack-roll spells with a timed
    duration aimed at a SEPARATE target ever create one — save spells and self/ally
    buffs are untouched (they write their effect at cast, as before)."""

    name: str  # the spell's canonical name (the materialized effect's name)
    source_id: str  # the caster's character id (matched against attack()'s attacker)
    target_id: str  # who the rider lands on, on a hit (matched against attack()'s target)
    # Duration descriptor captured at cast (mirrors ActiveEffect's timing fields) so the
    # effect materialized on hit is byte-identical to the one cast_spell would have written.
    scale: Literal["rounds", "minutes", "hours", "days"] = "rounds"
    rounds_remaining: int = 0
    expires_day: int = 0
    expires_phase_index: int = 0
    until_long_rest: bool = False


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
    # Loop-10 #383: player-authored identity from the Creation wizard's "Family /
    # House" + "Biography" inputs. PR #369 wired both into the bindHero spec but
    # the engine seating path dropped them at 4 sites (this model, create_character,
    # play.sh, /character-surface). Empty == today's behavior (no field was set
    # before — additive, deserializes existing snapshots unchanged).
    house: str = ""
    biography: str = ""

    abilities: AbilityScores = Field(default_factory=AbilityScores)
    proficiency_bonus: int = 2
    skill_proficiencies: list[str] = Field(default_factory=list)
    skill_expertise: list[str] = Field(default_factory=list)
    saving_throw_proficiencies: list[Ability] = Field(default_factory=list)
    # Printed-total escape hatch for monster saves whose source data doesn't decompose
    # as ability mod + CR-derived proficiency bonus (4/344 srd524 quirks, e.g. the
    # Octopus's CON 30). Keys are Ability values ('dex'); consulted FIRST by
    # saving_throw_bonus. Set at spawn time by _monster_character_from_statblock.
    # Empty == today's behavior; old snapshots round-trip (audit F01-2, #773).
    save_bonus_overrides: dict[str, int] = Field(default_factory=dict)

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
    # Engine-tracked timed spell effects (Bless 10 rounds, Hex 1 hour, Mage Armor 8h)
    # that auto-expire via next_turn / clock-advance tools instead of relying on the DM
    # to remember. Empty == today's behavior. See ActiveEffect; set by cast_spell.
    active_effects: list[ActiveEffect] = Field(default_factory=list)
    # Attack-roll spell on-hit riders (Guiding Bolt's "advantage on the next attack")
    # that have NOT yet landed — recorded here at cast time and materialized onto the
    # target only when the spell attack hits (see PendingOnHitRider, #186). Empty ==
    # today's behavior; held on the CASTER so attack() can match attacker→target.
    pending_on_hit_riders: list[PendingOnHitRider] = Field(default_factory=list)
    # A DECLARED-but-not-yet-applied extra damage roll the NEXT attack folds in — a Battle
    # Master damage maneuver (Trip/Menacing Attack) rolls its superiority die at
    # use_resource(superiority_dice, maneuver=…) time and stashes the result here; the next
    # attack() adds it to that strike's damage and clears it (see PendingDamageBonus, #213).
    # None == today's behavior; a maneuver-less use_resource never sets it.
    pending_damage_bonus: Optional[PendingDamageBonus] = None
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
    # A defensive REACTION that adds this many points to AC against ONE melee attack that
    # would otherwise hit (the Parry reaction — Bandit Captain +2, fallen consular +4, etc.,
    # #218). 0 == no such reaction (today's behavior). Set at spawn from the stat block; the
    # engine spends the creature's reaction to parry only when it would FLIP a hit to a miss.
    parry: int = 0
    xp_value: int = 0  # XP this creature grants when defeated (set when spawned from the bestiary; drives auto-XP)

    # roleplay (companion / npc)
    personality: str = ""
    attitude: str = ""  # npc disposition toward the party (free text: "guarded", or a track value)
    # Numeric per-NPC relationship toward the party, -100..+100 (0 = neutral). The
    # free-text `attitude` reads well for the DM; this gives the viewer a precise bar
    # position (and lets the DM reward/punish choices in points). Additive: 0 == today's
    # behavior, so the dashboard falls back to the keyword heuristic when it's untouched.
    attitude_value: int = 0
    # Has the PARTY actually encountered this NPC in play? A world seed pre-populates a
    # roster of NPCs who EXIST but whom the party hasn't met yet — the dashboard's
    # Relationships view must not list strangers the player "already knows". The engine
    # flips this True at the natural first-contact tools (social_check against a tracked
    # NPC, recruit_companion, load_canon_character into the party); the DM can also set it
    # via update_character. Additive: False == today's behavior, and the viewer keeps a
    # keyword/attitude fallback so existing snapshots (no `met` written) still surface
    # NPCs the party clearly has a standing with. Companions/players are implicitly met.
    met: bool = False
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
    # Structured companion identity — the OPERATIONAL state (wound/wants/values/banter/
    # approval causes/relationships) the living-world systems act on, kept out of the long
    # `personality`/`backstory` prose (#68). None == today's behavior, so an existing
    # snapshot with no `companion_dossier` deserializes unchanged. Seeded from npc_roster /
    # canon JSON / ending companion_seeds; a minimal one is synthesized at recruit_companion.
    companion_dossier: Optional["CompanionDossier"] = None

    # --- structured NPC tagging (the DM's "pull exactly the right canon character" surface) ----
    # ADDITIVE: every field defaults to empty/False == today's behavior, so an existing snapshot
    # (and the ~2,076 canon character JSONs that predate these fields) deserializes unchanged.
    # These give `find_npcs` a STRUCTURAL filter — "the merchant in this region", "this Harper",
    # "a traveling merchant near the party" — instead of grepping prose. Derived (high-confidence
    # only) from canon JSON content; the rest stay empty until a DM/content author sets them.
    #
    # Freeform sortable tags ("merchant", "companion", "villain", "noble", "guard", "child").
    tags: list[str] = Field(default_factory=list)
    # Canonical faction key the NPC belongs to ("harpers", "flaming-fist", "zhentarim"). A short
    # stable token (NOT the prose faction name), so a filter is exact. "" == unaffiliated/unknown.
    faction_id: str = ""
    # Quick boolean for the traveling-merchant / shop features — true == this NPC sells/trades.
    is_merchant: bool = False
    # Where this NPC is canonically found ("last-light-inn", "lower-city") — a location token.
    canon_location_id: str = ""
    # The NPC's role in the campaign's arcs: "companion" | "origin-hero" | "antagonist" | "minor"
    # | "" (untagged). Distinct from `kind` (the engine sheet category) — this is narrative.
    arc_role: str = ""
    # Outcome tag for ending-tied NPCs, projected from an ending overlay's fates.<npc>.status by
    # _apply_ending_overlay: "died" | "survived" | "ambiguous" | "" (no ending / not tied).
    ending_role: str = ""
    # Quest ids/slugs this NPC is tied to (giver, target, ally), so the DM can pull "who's in
    # the X quest". Short slugs; empty == not tied to any tracked quest.
    quest_ties: list[str] = Field(default_factory=list)
    # Canonical bestiary slug for a spawned monster ("goblin-warrior"), so kill/encounter
    # intel can be recorded by TYPE and the intel-tier codex (#263) can join a defeated
    # instance back to its creature type. Set at the spawn sites from the bestiary name via
    # bestiary.creature_slug. "" == not a bestiary spawn (today's behavior); old snapshots
    # round-trip unchanged.
    creature_slug: str = ""

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

    @model_validator(mode="after")
    def _normalize_skill_case(self) -> "Character":
        # Skill names are compared case-sensitively everywhere (skill_bonus below, social_check,
        # the viewer's Skills tab) against the lowercase-underscore SKILL_ABILITIES keys. Canon
        # character records and DM `patch={"skills":["Arcana",...]}` aliases introduce CAPITALIZED
        # (or space-separated) names, which then silently match nothing -> "0 proficient" + skill
        # checks missing the proficiency bonus (QA 2026-06-03: optimizer crit on a L5 Wizard/Sage
        # whose Arcana/History showed +3 not +6). load_canon_character runs this via model_validate,
        # so normalizing at the model boundary fixes every seat/patch path and the saved snapshot.
        self.skill_proficiencies = [str(s).strip().lower().replace(" ", "_") for s in self.skill_proficiencies if str(s).strip()]
        self.skill_expertise = [str(s).strip().lower().replace(" ", "_") for s in self.skill_expertise if str(s).strip()]
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
        # Printed-total override first (monster spawns whose stat-block save totals
        # don't decompose as mod + PB — see save_bonus_overrides; F01-2, #773).
        override = self.save_bonus_overrides.get(ability.value)
        if override is not None:
            return override
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
    # Rule-of-three evolution (Quest & Arc engine, Layer 1) — ADDITIVE: empty ==
    # today's behavior exactly, old snapshots round-trip. When a quest resolves
    # (status -> completed) AND `evolves_to` is set, the engine SCHEDULES a
    # follow-on Consequence so the thread lingers/echoes instead of being
    # one-and-done. The DM weaves the resulting prompt; the engine never
    # auto-acts on the fiction.
    evolves_to: str = ""  # a follow-on hook/quest id or a free seed tag the DM weaves on callback
    callback_in_days: int = 0  # in-world days from resolution before the evolution surfaces (0 = immediately due)
    # idempotency guard: milestone XP for resolving this quest is granted exactly once
    # (xp leveling_mode), so a re-resolve / status flip / complete-via-objective never
    # double-awards. Additive — an old snapshot lacking the key round-trips to False.
    milestone_awarded: bool = False


class Location(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("loc"))
    name: str
    description: str = ""
    connections: list[str] = Field(default_factory=list)  # location ids
    notes: str = ""
    visited: bool = False
    # Whether this place is KNOWN on the map (atlas/nav-graph visibility) — ADDITIVE.
    # The viewer shows a location when it is visited OR discovered; an explicitly
    # undiscovered, unvisited place is fog-of-war. Default False is additive at the
    # engine layer (nothing in the engine reads this flag; it only enriches the
    # snapshot the viewer projects). seed_world flips the world's known day-1 places
    # (authored regions + ingested areas) to True so the atlas renders the shipped
    # nav graph from day one — see content.seed_world.
    discovered: bool = False
    # Whether this place is RUMOURED -- heard-of but not yet a confirmed day-1
    # destination (atlas fog-of-war middle tier, issue #380) -- ADDITIVE. This is the
    # middle of a three-tier visibility model: KNOWN (discovered/visited) renders a
    # solid pin, RUMOURED renders a fogged/dashed pin the player has heard of but not
    # confirmed, and HIDDEN (neither) is fog-of-war. Like `discovered`, nothing in the
    # engine reads this flag -- it only enriches the snapshot the viewer projects; the
    # viewer treats a rumoured place as atlas-VISIBLE but styles it distinctly. Default
    # False is additive: an old snapshot lacking the field round-trips to a non-rumoured
    # Location, so a place that was KNOWN stays KNOWN and the atlas is byte-identical.
    rumoured: bool = False
    # Optional axial-hex (q, r) coords — PRESENTATION ONLY (the viewer renders them).
    # The engine's adjacency/travel is governed solely by `connections`; coords are
    # never used for movement or distance.
    hex: Optional[tuple[int, int]] = None
    # Spatial "walk-time" context (fables-style) — ADDITIVE; empty = today's behavior.
    region: str = ""  # the parent zone this location nests in ("South West Odrun Fell")
    travel_times: dict[str, int] = Field(default_factory=dict)  # connected location id -> walk minutes


class WorldGraphNode(_StrictModel):
    """Player-facing spatial metadata for an existing Location.

    ``Campaign.locations`` remains canonical. This metadata may enrich Atlas and
    travel displays, but it cannot create or authorize locations by itself.
    """

    location_id: str
    x: Optional[float] = None
    y: Optional[float] = None
    biome: str = ""
    terrain: str = ""
    danger: int = Field(0, ge=0, le=10)
    discovered: bool = True
    atlas_layer: Literal["region", "settlement", "site", "dungeon", "route"] = "site"
    tags: list[str] = Field(default_factory=list)


class WorldGraphEdge(_StrictModel):
    """Route metadata for an already-authorized Location connection."""

    from_id: str
    to_id: str
    # #381 (Loop-10): additive route-kind members. The original 7 kinds + the
    # default "road" are unchanged (zero behavior change for existing content):
    #   - "ferry": was already a styled branch in screen-map.jsx edgeStyle but
    #     the model rejected it on author; now authorable.
    #   - "bridge": Wyrm's Crossing over the Chionthar is canonically a bridge,
    #     not a road — a first-class kind so styling can mark the crossing.
    #   - "underground": Underdark passages, sewers, Bhaal Temple stairs (lands
    #     ahead of #380 so the data surface is ready for those POIs).
    route_kind: Literal[
        "street", "road", "trail", "sea", "river", "passage", "portal",
        "ferry", "bridge", "underground",
    ] = "road"
    minutes: Optional[int] = Field(None, ge=1)
    distance: Optional[float] = Field(None, ge=0)
    difficulty: Literal["easy", "normal", "hard", "hazardous"] = "normal"
    danger: int = Field(0, ge=0, le=10)
    tags: list[str] = Field(default_factory=list)


class WorldGraph(_StrictModel):
    """Additive strategic-map metadata owned by the engine.

    This graph is deliberately subordinate to ``Location.connections``: edges
    enrich existing travel links, they never authorize movement on their own.
    """

    nodes: dict[str, WorldGraphNode] = Field(default_factory=dict)
    edges: list[WorldGraphEdge] = Field(default_factory=list)
    seed: str = ""
    provenance: str = "authored"


class Faction(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("fac"))
    name: str
    description: str = ""
    reputation: int = 0  # -100..100

    # --- Faction-growth membership (Quest & Arc engine, faction arcs / #127) -----------------
    # ADDITIVE: every field defaults to "behaves exactly like today when unset", so an old
    # snapshot round-trips byte-for-byte and a world that authors no membership is unchanged.
    # These give the Skyrim/Kingmaker join->grow->lead loop a gauge the engine can READ to gate
    # a faction questline (closing the "reputation is tracked but nothing reads it" gap).
    #
    # `standing` is a MONOTONIC membership/progression gauge — distinct from the bidirectional
    # `reputation`. Reputation (-100..100) is how the faction FEELS about you (it can fall, drifts
    # via the backlog); `standing` (>=0) is how far you've RISEN inside it through service — the
    # Skyrim "rank progress" number, only ever earned upward. A FactionArc stage may gate on
    # EITHER gauge (see FactionArcStage.gauge); both are engine-mutated, so a gate never reads
    # fiction (invariant #3). `rank` is the readable tier the standing/arc has unlocked (0 ==
    # not a ranked member); `joined` is the membership latch `join_faction` sets; `questline_arc_id`
    # links this faction to its FactionArc in Campaign.faction_arcs (empty == no questline).
    rank: int = 0
    standing: int = Field(default=0, ge=0)  # monotonic membership gauge — never negative
    joined: bool = False
    questline_arc_id: str = ""


class FactionArcStage(_StrictModel):
    """One engine-owned stage inside a faction questline (Quest & Arc engine, faction arcs).

    Generalizes ``CompanionQuestStage`` (the proven companion machine) to a FACTION owner. The
    same bounded lifecycle enum (``locked|available|active|resolved|failed``); the difference is
    the UNLOCK GATE is folded directly onto the stage as a gauge threshold — a stage becomes
    ``available`` when the owning faction's gauge has reached ``unlock_at``. This is PURE and
    engine-evaluated: it reads ONLY ``reputation`` / ``standing`` (engine-mutated gauges), NEVER
    fiction (the questgen.py discipline / invariant #3).

    ``gauge`` picks WHICH faction gauge the threshold reads:
      * ``"reputation"`` (default) — the bidirectional trust gauge (a trust-gated stage).
      * ``"standing"`` — the monotonic membership/progression gauge (a service-grind stage).

    A resolved stage may carry a one-shot ``finale_effect`` — an ``Outcome``-shaped world ripple
    (the same payload ``_apply_structured_effect`` consumes) applied EXACTLY ONCE when the stage
    transitions to ``resolved`` (the world-changing finale). Idempotent: ``effect_applied`` latches
    so a re-advance never double-ripples (mirrors a fired Event/Consequence)."""

    id: str = Field(default_factory=lambda: _new_id("fstage"))
    title: str
    status: CompanionQuestStatus = "locked"
    # The gauge threshold that unlocks this stage (locked -> available). Read by the engine
    # against `gauge`; pure, contract-safe (reputation/standing only, never fiction).
    unlock_at: int = 0
    gauge: Literal["reputation", "standing"] = "reputation"
    location_id: str = ""
    quest_id: str = ""  # optional player-facing tracked-Quest projection (one-way, like companion stages)
    note: str = ""
    # The world-changing ripple this stage applies ONCE on resolve (the finale). Reuses the
    # Outcome payload so it ripples through the EXACT engine path the backlog / Events use. Empty
    # == no ripple (a non-finale stage). The engine never authors the payload — it's content.
    finale_effect: Optional["Outcome"] = None
    effect_applied: bool = False  # idempotency latch — a resolved stage's finale ripples at most once


class FactionArc(_StrictModel):
    """A FACTION's joinable, multi-stage questline (Quest & Arc engine, faction arcs / #127).

    The Skyrim/Kingmaker join->grow->lead loop, made a real engine state machine by GENERALIZING
    ``CompanionQuestArc`` onto a faction-owned reputation/standing gauge — NOT a parallel system.
    The companion arc is keyed to a companion; this is keyed to a ``faction_id`` and its stages
    gate on the faction's gauge. ``join_faction`` arms it (flips the faction ``joined`` + links
    ``questline_arc_id``); ``advance_faction_arc`` advances a stage when its gauge gate holds and
    applies a resolved stage's ``finale_effect`` once.

    ADDITIVE: a campaign with no faction arcs behaves exactly as today; old snapshots round-trip.
    Stages are evaluated in author order; ``status`` is the arc-level lifecycle (the arc resolves
    when its terminal stage does)."""

    id: str = Field(default_factory=lambda: _new_id("farc"))
    faction_id: str = ""
    title: str
    status: CompanionQuestStatus = "locked"
    stages: list[FactionArcStage] = Field(default_factory=list)
    # The arc only ARMS after the party joins (join_faction). A locked-but-unjoined arc never
    # advances — the gauge gate is necessary but not sufficient; membership is the precondition.
    requires_joined: bool = True
    note: str = ""

    @model_validator(mode="after")
    def _unique_stage_ids(self) -> "FactionArc":
        seen: set[str] = set()
        for stage in self.stages:
            if stage.id in seen:
                raise ValueError(f"duplicate faction arc stage id {stage.id!r}")
            seen.add(stage.id)
        return self


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
    # Attack-action economy for the CURRENT turn (additive; resets every next_turn):
    #  * action_attacks_made — how many attack() calls have resolved under the
    #    current combatant's Attack action(s) this turn. One Attack action grants
    #    `extra_attacks + 1` attacks; a second action (Action Surge) grants another.
    #  * surge_actions — how many EXTRA Attack actions Action Surge has granted this
    #    turn (incremented when use_resource spends "action_surge" mid-combat for the
    #    current combatant). attacks allowed = (extra_attacks + 1) * (1 + surge_actions).
    action_attacks_made: int = 0
    surge_actions: int = 0
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
    payload: Optional[dict[str, Any]] = None


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


CampBeatKind = Literal["solo", "pair_banter", "arc", "decision_callback"]


class CampBeatRecord(_StrictModel):
    """A camp beat that actually fired at the table.

    Camp prompt generation is read-only; the engine persists history only when an explicit
    recording tool/API appends this record. Cooldown keys are deterministic so a saved campaign
    and a reloaded campaign suppress the same recently-used beats."""

    id: str
    day: int
    companion_ids: list[str]
    kind: CampBeatKind
    tags: list[str] = Field(default_factory=list)
    resolved: bool = False
    note: str = ""
    cooldown_key: str = ""
    pair_key: str = ""


class CampBeatCandidate(_StrictModel):
    """A deterministic frame the DM/companion agents can voice at camp.

    This is a prompt/frame, not final authored dialogue. It deliberately carries only
    player-facing hooks; sealed agendas and private DM notes stay out of this shape."""

    beat_id: str
    kind: CampBeatKind
    priority: int = 0
    companion_ids: list[str]
    prompt: str
    tags: list[str] = Field(default_factory=list)
    cooldown_key: str
    pair_key: str = ""
    cooldown_reason: str = "none"


class CampBeatState(_StrictModel):
    """Persistent camp-beat memory owned by the engine.

    `camp_scene` reads this to avoid recent repeats, but never mutates it. Only
    `record_camp_beat` or another explicit record path should append records. The
    history is compacted by cooldown key and capped so a long campaign cannot grow
    snapshots without bound."""

    records: list[CampBeatRecord] = Field(default_factory=list)
    solo_cooldown_days: int = Field(2, ge=0)
    pair_cooldown_days: int = Field(3, ge=0)
    max_records: int = Field(200, ge=1, le=1000)


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
    # Kingmaker-style WANDERING encounters on time-advancing travel + camp watches.
    # ON by default (at a low per-region rate) so travel/camp carry real combat risk;
    # set False to disable the auto-roll entirely (explicit roll_wandering_encounter
    # still works). Additive: an old snapshot lacking this key loads as True.
    wandering_encounters: bool = True


class SeedParams(_StrictModel):
    """The mutable World-Seed parameters the OpenWorlds Seed screen surfaces and edits
    (#266). These are DM-GUIDANCE dials the DM honors when narrating (read via get_state),
    plus a few rules-affecting toggles. The engine is the SOLE writer (set_seed_param under
    campaign_lock + save_campaign); the viewer only relays an intent.

    ADDITIVE: every field defaults to today's behavior, so a snapshot lacking `seed_params`
    round-trips to this default and nothing changes. Difficulty is NOT mirrored here — it
    stays canonical on `house_rules.difficulty` (set_seed_param routes "difficulty" there);
    the ruleset/system stays `Campaign.ruleset` (locked post-seed). The mutability CLASS of
    each field (free / gated / locked) lives in the engine tool + the viewer read model, not
    on the model itself, so the policy has one home.

    FREELY MUTABLE mid-campaign (cosmetic / narration-only): tone, narration, gm_strictness,
    chronicle_voice, anachronism, chronicler_notes.
    GATED (retroactive / rules-affecting, gated by session-start in set_seed_param):
    permadeath, fate_dice, item_destruction."""

    # — free (DM-guidance / cosmetic) —
    tone: Literal["Heroic", "Grim", "Picaresque", "Mythic"] = "Heroic"
    narration: Literal["terse", "balanced", "florid", "almost_poetic"] = "florid"
    gm_strictness: Literal["permissive", "standard", "strict", "pedantic"] = "standard"
    chronicle_voice: Literal[
        "first_person_singular", "first_person_plural", "second_person",
        "third_person_omniscient", "third_person_close",
    ] = "first_person_plural"
    anachronism: bool = True  # permit a few out-of-period words for clarity (cosmetic)
    chronicler_notes: str = ""  # free text the DM honors; no mechanical effect

    # — gated (retroactive / rules-affecting; the engine gates a mid-session change) —
    permadeath: bool = False        # future death handling only; never resurrects an existing dead PC
    fate_dice: bool = True          # a per-act meta-currency the DM grants/honors
    item_destruction: bool = False  # weapons/armour wear with use


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


class CalendarMonth(_StrictModel):
    """One authored month in a campaign calendar.

    This is display metadata only. The engine's authoritative clock remains
    ``Campaign.day`` plus the tactical ``time_of_day`` phase.
    """

    name: str
    days: int = Field(..., ge=1, le=1000)
    season: str = ""


class CalendarMoon(_StrictModel):
    """A deterministic moon phase track derived from ``Campaign.day``."""

    name: str
    cycle_days: int = Field(..., ge=1, le=10000)
    epoch_phase_day: int = Field(0, ge=0)
    phase_names: list[str] = Field(default_factory=lambda: ["new", "waxing", "full", "waning"])


class CampaignCalendar(_StrictModel):
    """Clean-room, setting-agnostic calendar display metadata.

    Day 1 of the campaign maps to ``epoch_year``/``epoch_month``/``epoch_day``.
    The model deliberately stores no mutable cursor; every rendered date is a
    pure projection from ``Campaign.day``.
    """

    name: str
    era_suffix: str = ""
    epoch_year: int = 1
    epoch_month: int = Field(1, ge=1)
    epoch_day: int = Field(1, ge=1)
    weekdays: list[str] = Field(default_factory=list)
    week_start_index: int = Field(0, ge=0)
    months: list[CalendarMonth] = Field(default_factory=list)
    moons: list[CalendarMoon] = Field(default_factory=list)


class StrategicClock(_StrictModel):
    """A setting-agnostic strategic pressure clock.

    Advanced by the engine's explicit strategic tick paths. ``tick_every_days`` is
    evaluated against ``StrategicState.last_tick_day`` so repeated calls on the same
    in-world day never double-progress it."""

    id: str = Field(default_factory=lambda: _new_id("clock"))
    title: str
    kind: Literal["threat", "opportunity", "mystery", "faction", "project"] = "threat"
    scope: Literal["world", "region", "faction"] = "world"
    region_id: str = ""  # ref into Campaign.locations when scope/binding is regional
    faction_id: str = ""  # ref into Campaign.factions when scope/binding is factional
    progress: int = Field(0, ge=0)
    target: int = Field(6, ge=1)
    tick_every_days: int = Field(0, ge=0)  # 0 = manual/no schedule in this first slice
    note: str = ""

    @model_validator(mode="after")
    def _progress_cannot_exceed_target(self) -> "StrategicClock":
        self.progress = min(self.progress, self.target)
        return self


class FactionAsset(_StrictModel):
    """A compact strategic asset owned by a faction.

    The engine stores ownership/location/strength only; authored names and notes carry
    the flavor so the model stays portable across settings."""

    id: str = Field(default_factory=lambda: _new_id("asset"))
    faction_id: str
    name: str
    kind: Literal["army", "agent", "holding", "resource", "influence", "supply", "special"] = "special"
    location_id: str = ""  # optional ref into Campaign.locations
    strength: int = Field(1, ge=0)
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class RegionControl(_StrictModel):
    """Strategic control state for one seeded Location/region."""

    location_id: str
    controller_id: str = ""  # optional ref into Campaign.factions
    influence: dict[str, int] = Field(default_factory=dict)  # faction_id -> 0..100-ish authored score
    stability: int = Field(50, ge=0, le=100)
    unrest: int = Field(0, ge=0, le=100)
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class DowntimeProject(_StrictModel):
    """A strategic downtime project record.

    Active projects advance on strategic day ticks. A completed project's authored
    ``effect`` is applied exactly once when status transitions to ``complete``."""

    id: str = Field(default_factory=lambda: _new_id("proj"))
    title: str
    kind: Literal["research", "construction", "training", "diplomacy", "recovery", "crafting", "other"] = "other"
    location_id: str = ""  # optional ref into Campaign.locations
    faction_id: str = ""  # optional ref into Campaign.factions
    progress_days: int = Field(0, ge=0)
    duration_days: int = Field(1, ge=1)
    status: Literal["planned", "active", "paused", "complete", "failed"] = "planned"
    effect: dict[str, str] = Field(default_factory=dict)
    note: str = ""

    @model_validator(mode="after")
    def _progress_cannot_exceed_duration(self) -> "DowntimeProject":
        self.progress_days = min(self.progress_days, self.duration_days)
        return self


class SettlementNpcPressure(_StrictModel):
    """Player-safe NPC pressure visible at a settlement.

    This is a public signal, not private motivation or scripted dialogue."""

    npc_id: str = ""  # optional ref into Campaign.characters
    role: str = ""
    pressure: str = ""


class SettlementPressure(_StrictModel):
    """Clean-room settlement texture anchored to one Location.

    The row stores public-facing civic pressure only. Private notes may be retained for
    DM tools, but viewer projections must not expose them."""

    location_id: str
    settlement_type: Literal[
        "hamlet", "village", "town", "city", "district", "port", "fort", "camp", "outpost", "other"
    ] = "town"
    governance: str = ""
    public_safety: str = ""
    economy: str = ""
    unrest: int = Field(0, ge=0, le=100)
    public_faction_ids: list[str] = Field(default_factory=list)
    establishments: list[str] = Field(default_factory=list)
    public_npcs: list[SettlementNpcPressure] = Field(default_factory=list)
    notes: str = ""


class StrategicState(_StrictModel):
    """The campaign's optional strategic board.

    Empty defaults are additive: old snapshots that lack this field deserialize with an
    empty board, and worlds without a `strategic` block seed unchanged. ``last_tick_day``
    is the day cursor for engine-owned strategic advancement."""

    regions: dict[str, RegionControl] = Field(default_factory=dict)  # location_id -> control
    assets: dict[str, FactionAsset] = Field(default_factory=dict)  # asset id -> asset
    clocks: dict[str, StrategicClock] = Field(default_factory=dict)  # clock id -> clock
    projects: dict[str, DowntimeProject] = Field(default_factory=dict)  # project id -> project
    settlements: dict[str, SettlementPressure] = Field(default_factory=dict)  # location_id -> settlement pressure
    last_tick_day: int = 0


class BacklogItem(_StrictModel):
    """A goal-traced unit of PROACTIVE world-work — the world's own to-do, so the campaign
    advances off-screen when in-fiction time passes instead of only reacting to the player.
    (Epic #60's StrategicClock is SUBSUMED: a clock advance is just one `kind` of deterministic
    backlog development.)

    Two classes of item, split by COST and deliberately so:
      * DETERMINISTIC (`needs_llm=False`) — a number, flag, or graph edge the engine resolves
        for free, unforgettably, on the in-world day clock: flip a campaign flag, shift faction
        control/reputation, advance a clock, stub a scheduled NPC arrival. `tick_backlog`
        resolves these in place (status -> "resolved") and templates a one-line `summary`.
      * CREATIVE (`needs_llm=True`) — anything that needs a VOICE, a name, narrated prose: the
        engine only ENQUEUES it (status -> "fired") and leaves the voicing/authoring to the
        later DM digest (P2) / world-agent (P3). The engine NEVER invents prose.

    `goal_ref` is the load-bearing field (Paperclip's "every task carries its why"): it points
    at an existing arc anchor — a worldsim `thread_id`, a `QuestHook.id` (esp. spine), a
    `faction_id`, or a free arc note from `c.summary` — so a faction's move is *"advances the
    Pale Choir's recruiting thread,"* never random noise.

    Additive + setting-agnostic: ids/`kind`/`goal_ref` values come from CONTENT (the seeded
    world), never engine code. Idempotency is by ELAPSED DAYS via the parent block's cursor,
    never a call counter (mirrors worldsim.tick / the #60 StrategicState contract). Kept
    STRICTLY separate from Consequence/worldsim threads — its own typed block, never consumed
    by consequences.due."""

    id: str = Field(default_factory=lambda: _new_id("blog"))
    # What kind of off-screen development this is — a closed Literal so a typo'd kind is
    # rejected by pydantic (degrade-not-abort drops it at seed). `clock` SUBSUMES epic #60's
    # StrategicClock as one deterministic class of backlog development.
    kind: Literal[
        "faction_move", "thread_beat", "npc_arrival", "clock", "world_event"
    ] = "world_event"
    title: str = ""                       # short DM/operator-facing label
    # The "why": a thread_id / quest(_hook) id / faction id / arc anchor so the move TRACES to
    # the campaign's premise. Keys live in content + generated refs, never engine code.
    goal_ref: str = ""
    # The in-world day this comes due (like Consequence.trigger_day). A recurring development
    # re-arms `cadence_days` out after it fires (0 = one-shot).
    trigger_day: int = 0
    cadence_days: int = Field(0, ge=0)    # 0 = one-shot; >0 = re-arm this many days out on fire
    # The lifecycle: pending (armed, not yet due) -> fired (a creative item ENQUEUED for the
    # later DM/agent) | resolved (a deterministic item the engine already applied). A creative
    # item stops at `fired`; a deterministic one goes straight to `resolved`.
    status: Literal["pending", "fired", "resolved"] = "pending"
    # False = a purely MECHANICAL development the engine resolves itself (no agent wake);
    # True = a creative one the engine only enqueues (left for P2/P3 — do NOT invent prose).
    needs_llm: bool = False
    # The structured payload a deterministic tick applies ONCE: e.g. {"flag": "concord_split"},
    # {"faction_id": "fac-choir", "reputation_delta": "-5"}, {"controller_id": "fac-league",
    # "location_id": "loc-brassmoor"}, {"npc_name": "A Choir emissary", "location_id": "..."}.
    # All keys/values live in CONTENT, never engine code (mirrors WorldState.facts). Empty = a
    # marker-only development (the DM reads `summary`/`goal_ref` and plays it).
    effect: dict[str, str] = Field(default_factory=dict)
    summary: str = ""                     # the one-line "what the world did" the DM weaves
    note: str = ""                        # why / source seed (authored), for the DM/agent


class CampaignBacklog(_StrictModel):
    """The world's PROACTIVE work queue — what the world is doing off-screen, so the player
    returns to a world that changed without them. One additive block on Campaign, present by
    default (an EMPTY backlog == today's behavior exactly, and an old snapshot lacking the key
    deserializes to this empty default — round-trips byte-identically).

    Engine is SOLE WRITER (mutate under campaign_lock, then save_campaign); the viewer projects
    it read-only. `last_tick_day` drives IDEMPOTENT mechanical advancement (P1): the in-world
    day the deterministic tick last advanced through, so repeated advance_time/world_tick/
    downtime/travel_to on the SAME day never double-advance (exactly the #60 StrategicState
    pattern — idempotency by elapsed days, never a call counter). Initialized to `c.day` at
    seed so a freshly-seeded world doesn't immediately owe a backlog of ticks on its first
    advance.

    Deliberately a SIBLING of Consequence/worldsim threads, never merged: those are authored
    narrative beats on the per-day clock consumed by consequences.due / worldsim.tick; this is
    the goal-traced proactive layer, its own typed block, never consumed by either."""

    items: dict[str, BacklogItem] = Field(default_factory=dict)  # item id -> item
    last_tick_day: int = 0  # the Campaign.day the mechanical backlog tick last advanced through


class SceneDebt(_StrictModel):
    """A structural story debt detected by the Campaign Director (issue #72).

    ADVISORY ONLY: the engine detects debts + returns them; it NEVER acts on them
    or mutates fiction. The DM reads + chooses. Resolution is EXPLICIT (a
    resolve_scene_debt tool call), never automatic.

    Additive: empty ``scene_debts`` on Campaign == today's behavior.
    Old snapshots without this field deserialise unchanged.

    kind values:
        hook_untracked, quest_stalled, thread_no_payoff, choice_without_outcome,
        due_consequence, thread_pressure, npc_introduced_silent, faction_rank_available
    severity: low | med | high
    """

    id: str = Field(default_factory=lambda: _new_id("debt"))
    kind: str  # one of the six structural debt kinds
    subject: str  # the id of the thing that owes (quest id, hook id, decision id, …)
    detail: str  # one-line DM-facing description of the debt
    severity: Literal["low", "med", "high"] = "med"
    evidence: dict[str, Any] = Field(default_factory=dict)  # structured context for resolution
    resolved: bool = False
    resolution_evidence: str = ""  # DM-supplied evidence when marking resolved


class QuestHook(_StrictModel):
    """S7 — a lore-derived quest SEED the DM pulls and weaves. The engine ASSEMBLES it at
    world-gen (a dramatic SHAPE tag bound to typed lore nouns + a `grievance` — a wrong the
    lore already contains) and the DM narrates/advances it. It is NOT an engine-driven state
    machine: `prereq`/`arc_back` are DATA the DM reads (branching it weaves), never
    engine-evaluated predicates — the engine can't judge fiction (world_state is near-constant
    in play, so a 'monitor' would watch a constant). The DM promotes a hook the party bites on
    into a tracked Quest (add_quest) and sets `status` off its own narration. Additive: no
    hooks == today's behavior. Keys/values live in CONTENT + generated refs, never engine code."""

    id: str = Field(default_factory=lambda: _new_id("hook"))
    title: str = ""                       # short DM-facing label
    shape: str = ""                       # archetype TAG (a label, not a grammar): fetch_plus|investigation|hunt|rescue|heist|escort|faction_war|dilemma|false_accusation|sacrifice_choice|revelation|tragedy_unfolding
    grievance: str = ""                   # the lore "wrong" this addresses — the spine primitive quests derive from
    motivation: str = ""                  # the giver's "why": knowledge|protection|conquest|serenity|wealth|reputation|comfort|ability|equipment
    giver_id: str = ""                    # bound lore noun: the NPC who offers/embodies it (ref into c.characters)
    target_id: str = ""                   # bound lore noun: the NPC or faction the quest concerns (ref into c.characters/c.factions)
    place_id: str = ""                    # bound lore noun: where it points (ref into c.locations)
    item: str = ""                        # bound lore noun: an item/relic at stake (free text)
    prereq: list[str] = Field(default_factory=list)  # hook ids that should resolve first — DM reads, NOT enforced
    arc_back: str = ""                    # how resolving this feeds the main arc — a note the DM weaves
    spine: bool = False                   # a main-arc hook (vs a rib / side thread)
    status: Literal["open", "active", "resolved"] = "open"  # DM-set off its own narration
    note: str = ""                        # the DM-facing seed detail (the prose seed)


class PreludeBeat(_StrictModel):
    """S7 — one of the four guaranteed cold-open beats. The engine guarantees all four exist
    with bound nouns so a session never 'starts mid-quest' or skips 'how the party meets'; the
    DM owns ORDER, framing, and prose (a woven checklist, NOT a rigid rail — a hard template
    every session goes formulaic). Additive: an empty prelude == today's behavior."""

    kind: Literal["arrival", "meeting", "inciting_incident", "threshold"]
    note: str = ""        # the bound seed for this beat (e.g. 'meeting' -> the companion + a shared stake)
    ref_id: str = ""      # the bound noun: companion id for 'meeting', grievance/hook id for 'inciting_incident'


class Campaign(_StrictModel):
    id: str = Field(default_factory=lambda: _new_id("camp"))
    title: str
    ruleset: str = "SRD 5.2"
    summary: str = ""
    created_at: float = Field(default_factory=_now)
    updated_at: float = Field(default_factory=_now)

    # Observability / versioning (additive — defaulted so every existing snapshot round-trips).
    # `schema_version` is a MANUAL constant we bump only on a breaking (non-additive) schema
    # change, so a loaded snapshot records which schema generation wrote it. `engine_sha` is the
    # engine's short git commit SHA at save time, stamped by store.save_campaign — instant
    # "what engine version was this campaign last written by". Pairs with the #165 tolerant load.
    schema_version: int = 1
    engine_sha: str = ""

    current_location_id: Optional[str] = None
    day: int = 1  # in-world day counter
    time_of_day: str = "morning"
    calendar: Optional[CampaignCalendar] = None
    map_kind: Literal["hex", "none"] = "none"  # how the play-view renders the map
    # World-state boolean flags the DM/engine set to gate events — e.g. "prize_seized"
    # drives a companion's prize_seized agenda (S4). Additive: empty == today's behavior.
    flags: dict[str, bool] = Field(default_factory=dict)
    # The disposition of the MOST RECENT combat that ended with hostiles still alive — a
    # flee/surrender/capture/retreat the DM declared via end_combat(resolution=...). The behavioral
    # end_combat_no_living_hostiles gate reads it to tell a legitimate disengagement from a
    # continuity break (a fight abandoned with enemies standing and no reason) — the combat
    # chronicle is NOT in the snapshot the gate reads. Cleared at start_combat so each fight's
    # disposition is fresh. Additive: "" == today's behavior.
    last_combat_resolution: str = Field(default="")
    # The replayability layer (S6): each MAJOR world quest's resolved outcome, picked once
    # at world-gen (ending-tied when the chosen ending's world_state.facts match, else a
    # seeded random roll) — maps quest_id -> the resolved outcome_id. The resolved outcome's
    # lore/hook prose is also appended to `lore` so recall/lookup_lore surface it under the
    # canon header. Additive: empty == today's behavior (a world with no quest_variants).
    # Mirrors `flags`/`world_state` — keys/values live in CONTENT, never engine code.
    quest_outcomes: dict[str, str] = Field(default_factory=dict)
    # S7 — the quest-generation layer: lore-derived quest SEEDS the DM weaves (NOT an engine
    # state machine — assembled from the seeded world + world_state; the DM narrates/advances).
    # `prelude` is the guaranteed 4-beat cold-open (fixes "starts mid-quest" / "how they meet").
    # Generated once at seed_world, AFTER quest_variants (so grievances can draw on resolved
    # outcomes + facts + roster). Additive: empty == today's behavior. Mirrors quest_outcomes.
    quest_hooks: list[QuestHook] = Field(default_factory=list)
    prelude: list[PreludeBeat] = Field(default_factory=list)
    # The PROACTIVE living-world core (epic #60 SUBSUMED): the world's own backlog of off-screen
    # developments, advanced MECHANICALLY for free on the in-world day clock (P1 tick_backlog) so
    # factions maneuver / NPCs arrive / ignored threads escalate even when the party isn't
    # watching. Present-by-default (not Optional) so the viewer projection and tick logic always
    # see a dict-bearing object; EMPTY == today's behavior, and an old snapshot lacking the key
    # round-trips to this empty default. Seeded in content.py from standing_threads/factions/
    # spine quest_hooks (so item #1 traces to a real arc anchor). Engine sole-writer; kept a
    # strict sibling of consequences/worldsim threads (never consumed by consequences.due).
    campaign_backlog: CampaignBacklog = Field(default_factory=CampaignBacklog)
    strategic_state: StrategicState = Field(default_factory=StrategicState)
    world_graph: WorldGraph = Field(default_factory=WorldGraph)
    # Persistent camp-beat memory (#69). Read by camp_scene/scheduler, written only by an
    # explicit record path so prompt generation never advances campaign state by accident.
    camp_beats: CampBeatState = Field(default_factory=CampBeatState)
    # First-class companion personal quest arcs (#70). These complement, but do not replace,
    # tracked Quest objects; explicit server APIs advance them and optionally project status
    # into linked Quests. Empty == old snapshots load unchanged.
    companion_quest_arcs: dict[str, CompanionQuestArc] = Field(default_factory=dict)
    # Faction-growth questlines (Quest & Arc engine, faction arcs / #127). The Skyrim/Kingmaker
    # join->grow->lead loop: a FactionArc generalizes the companion stage-machine onto a faction-
    # owned reputation/standing gauge (each stage gated on stage.unlock_at). join_faction arms one;
    # advance_faction_arc advances a stage when its gauge gate holds and ripples a resolved stage's
    # finale ONCE. Empty == today's behavior byte-for-byte; old snapshots lacking the key round-trip
    # to this empty default. Mirrors companion_quest_arcs/events; seeded from a world/ending
    # `faction_arcs` block (content.py). Engine sole-writer (the tools persist under campaign_lock).
    faction_arcs: dict[str, FactionArc] = Field(default_factory=dict)
    # First-class stumble-into Events (Quest & Arc engine, Layer 3). Content-authored decisionals
    # whose options carry a deterministic Outcome (a world ripple + optionally a Layer-2
    # decision_flag that arms a companion flip). Surfaced read-only by present_events when their
    # contract-safe trigger holds; applied by resolve_event. Empty == today's behavior byte-for-
    # byte; old snapshots lacking the key round-trip to this empty default. Mirrors
    # companion_quest_arcs; seeded from a world/ending `events` block (content.py). Engine
    # sole-writer (resolve_event under campaign_lock + save_campaign).
    events: dict[str, "Event"] = Field(default_factory=dict)

    characters: dict[str, Character] = Field(default_factory=dict)  # id -> Character (PCs, companion, NPCs)
    # Intel-tier bestiary codex (#263): creature_slug -> the HIGHEST intel tier the party has
    # earned for that creature TYPE (1=sighted at spawn, 2=engaged at start_combat, 3=slain).
    # Engine is the SOLE writer; bumped monotonically (max) at the spawn/combat/kill sites. The
    # viewer reads it and threads it into bestiary.player_bestiary to gate the stat reveal.
    # Empty == today's narrow behavior; old snapshots lacking the key round-trip to {}.
    bestiary_intel: dict[str, int] = Field(default_factory=dict)
    party: list[str] = Field(default_factory=list)  # character ids that are PCs / companions
    quests: dict[str, Quest] = Field(default_factory=dict)
    locations: dict[str, Location] = Field(default_factory=dict)
    factions: dict[str, Faction] = Field(default_factory=dict)
    combat: Combat = Field(default_factory=Combat)
    house_rules: HouseRules = Field(default_factory=HouseRules)
    # Mutable World-Seed parameters surfaced + edited by the OpenWorlds Seed screen (#266).
    # DM-guidance dials (tone/narration/voice/…) plus a few rules toggles; the DM reads them
    # via get_state and honors them, exactly like pacing_mode/house_rules. Engine sole-writer
    # (set_seed_param). ADDITIVE: a snapshot lacking this key round-trips to the default and
    # nothing changes — today's behavior byte-for-byte.
    seed_params: SeedParams = Field(default_factory=SeedParams)

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
    # Campaign Director scene-debts (issue #72). ADDITIVE: empty == today's behavior.
    # Old snapshots lacking this key deserialise to the empty default, round-tripping
    # byte-identically. The engine never auto-populates this list; debts are detected
    # read-only by scene_debt.detect() and surfaced via get_scene_debts /
    # get_campaign_director. Resolved debts are marked in-place (resolved=True) by
    # resolve_scene_debt and then preserved here as an audit trail.
    scene_debts: list[SceneDebt] = Field(default_factory=list)
