"""Companion agency for ClawDnD — a pure, testable module (Epic 9).

The companion is a first-class party member: its own Character (kind="companion")
with its own sheet, voice, personality, and *agency*. This module gives it two
things and nothing more:

1. ``suggest_action`` — a deterministic tactical heuristic. Given the companion,
   the current ``Combat``, and the campaign's characters, it returns a single
   suggested move (aid a downed ally, attack the weakest enemy, defend, or
   roleplay out of combat). It is a tactical *aid* — the deciding agent (the
   in-host persona today, an isolated sub-session tomorrow) is free to follow it,
   improve on it, or ignore it for roleplay reasons.

2. ``CompanionProvider`` — the boundary the DM reaches the companion through
   (``take_turn`` / ``react``). ``InProcessCompanion`` is the Tier-1 wiring (the
   host wears the persona, in-process). ``SubagentCompanion`` is the Tier-2 seam
   (a forked OpenClaw sub-session); it is a documented stub today.

By design this module has NO MCP and NO campaign I/O: it operates on plain model
objects and dicts so it can be unit-tested without any MCP plumbing or store, the
same way ``combat.py`` and ``npc.py`` are pure. The parent integrates the MCP
tools in ``server.py``.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from models import Character, Combat

# Kinds that are on the player's side vs. against them. The companion fights for
# the party (with the player and other companions) and against npcs/monsters.
ALLY_KINDS = frozenset({"player", "companion"})
ENEMY_KINDS = frozenset({"npc", "monster"})

# Spells the heuristic recognizes as restoring HP, and the fraction of max HP at
# or below which a still-standing ally is "critical" enough to heal over attacking.
HEAL_SPELLS = frozenset(
    {"cure wounds", "healing word", "mass cure wounds", "mass healing word",
     "prayer of healing", "heal"}
)
HEAL_THRESHOLD = 0.25


def _can_heal(companion: Character) -> bool:
    """True if the companion has a healing spell known/prepared and a slot free."""
    spells = {
        s.lower() for s in (set(companion.spells_prepared) | set(companion.spells_known))
    }
    if not (spells & HEAL_SPELLS):
        return False
    return any(slot.used < slot.maximum for slot in companion.spell_slots.values())


# Healing spells in priority order — Healing Word first (a bonus action that can
# revive a downed ally at range). The first two are bonus-action casts.
_HEAL_PRIORITY = (
    "Healing Word", "Cure Wounds", "Mass Healing Word", "Mass Cure Wounds",
    "Prayer of Healing", "Heal",
)
_BONUS_ACTION_HEALS = frozenset({"Healing Word", "Mass Healing Word"})


def _has_slot(companion: Character) -> bool:
    return any(slot.used < slot.maximum for slot in companion.spell_slots.values())


def _best_heal_spell(companion: Character) -> Optional[str]:
    """The companion's best healing spell it can ACTUALLY cast right now — None if
    it knows/prepares none OR has no spell slot free (so the suggestion never tells
    the DM to cast a heal the companion can't afford; stabilize another way)."""
    if not _has_slot(companion):
        return None
    have = set(companion.spells_prepared) | set(companion.spells_known)
    return next((name for name in _HEAL_PRIORITY if name in have), None)


def _weakest_living_enemy(in_combat: list) -> Optional[Character]:
    living = [ch for ch in in_combat if ch.kind in ENEMY_KINDS and ch.current_hp > 0]
    return min(living, key=lambda ch: ch.current_hp) if living else None


def suggest_action(
    companion: Character,
    combat: Combat,
    characters: dict[str, Character],
) -> dict:
    """Suggest the companion's next tactical move — deterministic, side-effect free.

    Looks only at the creatures that are *in the initiative order* (``combat.order``),
    resolved against ``characters`` (id -> Character). Priority, highest first:

    1. ``aid_downed`` — an ally (kind in :data:`ALLY_KINDS`), other or self, is at
       ``current_hp == 0``. Targets that downed ally. A downed friend is the most
       urgent thing on the field, so this beats attacking.
    1.5 ``heal`` — a still-standing ally is at or below :data:`HEAL_THRESHOLD` of
       max HP and the companion ``_can_heal`` (a healing spell known/prepared with
       a slot free). Targets the most-wounded such ally. A near-death ally beats
       chipping an enemy.
    2. ``attack`` — a living enemy (kind in :data:`ENEMY_KINDS`, ``current_hp > 0``)
       is present. Targets the living enemy with the LOWEST ``current_hp`` — focus
       fire to remove a combatant from the fight as fast as possible.
    3. ``roleplay`` — combat is not active (``not combat.active``). Out of combat
       the companion's "turn" is to speak / act in the scene, not to swing.
    4. ``defend`` — otherwise (combat is active but there is nothing to aid and no
       living enemy to hit, e.g. enemies all at 0 awaiting cleanup): hold and brace.

    Returns ``{"action": str, "target_id": str | None, "reason": str}``. Combatants
    whose ids are not present in ``characters`` are skipped defensively rather than
    raising — the engine is the authority on who actually exists.
    """
    # Resolve only the combatants we can actually see in the roster, preserving
    # initiative order so ties (e.g. equal-HP enemies) break deterministically.
    in_combat: list[Character] = []
    for combatant in combat.order:
        ch = characters.get(combatant.character_id)
        if ch is not None:
            in_combat.append(ch)

    # 1) A downed-but-savable ally (other or self) — the most urgent priority.
    # A dead ally can't be aided here, and a stable one is no longer in danger.
    for ch in in_combat:
        if ch.kind in ALLY_KINDS and ch.current_hp == 0 and not ch.dead and not ch.stable:
            who = "themselves" if ch.id == companion.id else ch.name
            spell = _best_heal_spell(companion)  # None if no slot/heal -> stabilize instead
            result = {
                "action": "aid_downed",
                "target_id": ch.id,
                "spell": spell,
                "bonus_action": spell in _BONUS_ACTION_HEALS,
            }
            if spell:
                result["reason"] = f"{ch.name} is down at 0 HP — cast {spell} to revive {who} now."
            else:
                result["reason"] = (
                    f"{ch.name} is down at 0 HP and there's no spell slot to heal — "
                    f"stabilize {who} (Spare the Dying, or a Medicine check)."
                )
            if result["bonus_action"]:  # a bonus-action heal leaves the action free
                foe = _weakest_living_enemy(in_combat)
                if foe is not None:
                    result["then_attack_target_id"] = foe.id
                    result["reason"] += f" {spell} is a bonus action — then attack {foe.name}."
            return result

    # 1.5) A critically wounded (still-standing) ally + the companion can heal ->
    # heal before trading blows. An ally one hit from death beats chipping an enemy.
    if _can_heal(companion):
        wounded = [
            ch
            for ch in in_combat
            if ch.kind in ALLY_KINDS
            and not ch.dead
            and 0 < ch.current_hp <= HEAL_THRESHOLD * ch.max_hp
        ]
        if wounded:
            target = min(wounded, key=lambda ch: ch.current_hp / ch.max_hp)
            who = "themselves" if target.id == companion.id else target.name
            spell = _best_heal_spell(companion)
            result = {
                "action": "heal",
                "target_id": target.id,
                "spell": spell,
                "bonus_action": spell in _BONUS_ACTION_HEALS,
                "reason": (
                    f"{target.name} is critically wounded "
                    f"({target.current_hp}/{target.max_hp} HP); cast {spell} on {who} "
                    f"before trading blows."
                ),
            }
            if result["bonus_action"]:  # bonus-action heal -> still attack with the action
                foe = _weakest_living_enemy(in_combat)
                if foe is not None:
                    result["then_attack_target_id"] = foe.id
                    result["reason"] += f" {spell} is a bonus action — then attack {foe.name}."
            return result

    # 2) A living enemy — focus the weakest to drop it fastest.
    living_enemies = [
        ch for ch in in_combat if ch.kind in ENEMY_KINDS and ch.current_hp > 0
    ]
    if living_enemies:
        # min() keeps the first of any HP tie, i.e. the higher-initiative enemy.
        target = min(living_enemies, key=lambda ch: ch.current_hp)
        return {
            "action": "attack",
            "target_id": target.id,
            "reason": (
                f"{target.name} is the weakest living enemy "
                f"({target.current_hp}/{target.max_hp} HP); focus fire to drop it."
            ),
        }

    # 3) Genuinely out of combat (inactive and no initiative order) — roleplay.
    if not combat.active and not combat.order:
        return {
            "action": "roleplay",
            "target_id": None,
            "reason": "No combat is underway; speak up, banter, or act in the scene.",
        }

    # 4) In combat (or a lingering order) with nothing to aid and no living enemy — brace.
    return {
        "action": "defend",
        "target_id": None,
        "reason": "No reachable target; take a defensive stance and hold ready.",
    }


@runtime_checkable
class CompanionProvider(Protocol):
    """The boundary the DM reaches the companion through.

    Implementations decide *how* the companion thinks (in-process today, a forked
    OpenClaw sub-session tomorrow); the DM only ever sees this contract, so a
    Tier-1 -> Tier-2 promotion is a drop-in. ``character_id`` and ``voice_id`` let
    the DM resolve the companion's engine sheet and speak its lines without knowing
    which tier is behind the boundary.
    """

    @property
    def character_id(self) -> str:
        """The id of the companion's Character in the engine."""
        ...

    @property
    def voice_id(self) -> str:
        """The companion's logical voice id (resolved via the voice map)."""
        ...

    def take_turn(self, situation: dict) -> dict:
        """Decide the companion's action for its combat turn.

        ``situation`` is an opaque, JSON-serializable snapshot the DM assembles
        (e.g. the combat state and a character roster). Returns an action dict in
        the same shape as :func:`suggest_action`:
        ``{"action": str, "target_id": str | None, "reason": str}``.
        """
        ...

    def react(self, event: dict) -> Optional[str]:
        """React in-character to something that just happened.

        ``event`` is a JSON-serializable description of a moment in play (a hit
        landing, an ally going down, a discovery). Returns a line of dialogue to
        speak in the companion's voice, or ``None`` to stay quiet.
        """
        ...


class InProcessCompanion:
    """Tier-1 companion: the host wears the persona, in-process.

    Wraps the companion's :class:`~models.Character` and answers ``take_turn`` with
    the deterministic :func:`suggest_action` heuristic. ``react`` is left to the
    persona (the skill/agent guidance drives proactive roleplay); this default
    stays quiet rather than emitting canned banter, so the deciding agent owns the
    voice.

    The ``situation`` passed to ``take_turn`` is expected to carry the live combat
    state and character roster. Two shapes are accepted so callers do not have to
    rebuild model objects they already hold:

    * pre-built models — ``{"combat": Combat, "characters": {id: Character}}``
    * serialized dumps — ``{"combat": <Combat.model_dump()>, "characters": {id: <Character.model_dump()>}}``

    If neither the combat nor a roster is supplied, the companion falls back to a
    roleplay suggestion (there is no field to reason about).
    """

    def __init__(self, character: Character):
        self._character = character

    @property
    def character(self) -> Character:
        return self._character

    @property
    def character_id(self) -> str:
        return self._character.id

    @property
    def voice_id(self) -> str:
        return self._character.voice_id

    def take_turn(self, situation: dict) -> dict:
        combat = _coerce_combat(situation.get("combat"))
        characters = _coerce_characters(situation.get("characters"))
        # Make sure the companion's own sheet is visible to the heuristic even if
        # the caller's roster omitted it (e.g. it only passed the enemies).
        characters.setdefault(self._character.id, self._character)
        if combat is None:
            return {
                "action": "roleplay",
                "target_id": None,
                "reason": "No combat in the situation; the companion acts in the scene.",
            }
        return suggest_action(self._character, combat, characters)

    def react(self, event: dict) -> Optional[str]:
        # The persona supplies proactive banter/worry/opinions; the in-process
        # default declines to speak so it never puts words in the companion's
        # mouth. Callers wanting auto-banter can subclass and override.
        return None


class SubagentCompanion:
    """Tier-2 companion: an isolated OpenClaw sub-session — STUB (Epic 13 seam).

    This is the same :class:`CompanionProvider` contract as
    :class:`InProcessCompanion`, but instead of the host wearing the persona
    in-process, the companion *is its own forked session*. Every method here raises
    :class:`NotImplementedError` until Epic 13 wires it; the class exists now so the
    boundary is real and the promotion is a drop-in.

    Intended Tier-2 wiring
    ----------------------
    On construction (or first turn) the host calls OpenClaw ``sessions_spawn`` with
    ``runtime="subagent"`` and ``context="fork"``. Forking from the player's own
    agent session means the spawned companion inherits the *user's agent identity*
    (its model, system prompt, tools, persona) while getting an isolated transcript
    and its own scratch space — so it can hold campaign memory and make decisions
    without polluting the player's session. The forked session is seeded with the
    ``companion`` skill / ``companion-agent`` persona and pointed at the same
    ``clawdnd-engine`` MCP, so it rolls and mutates state through the engine exactly
    like the in-process companion does.

    * ``take_turn`` would forward ``situation`` into the sub-session (as a prompt /
      tool input), let that session reason — optionally calling ``suggest_action``
      through the engine as a tactical aid — and return the chosen action dict.
    * ``react`` would forward ``event`` and return the spoken line (or ``None``).
    * ``character_id`` / ``voice_id`` come from the forked session's bound companion
      Character, identical to Tier-1, so the DM cannot tell which tier it is talking
      to.

    The seam is deliberately thin: the DM-facing contract does not change between
    tiers, so Epic 13 only has to fill in the bodies below.
    """

    def __init__(self, character: Character, *, session_spawner=None):
        # session_spawner is the (future) OpenClaw sessions_spawn handle; kept on
        # the instance so Epic 13 can wire it without changing the constructor's
        # call sites.
        self._character = character
        self._session_spawner = session_spawner

    @property
    def character_id(self) -> str:
        raise NotImplementedError(
            "SubagentCompanion is the Tier-2 seam (Epic 13). It will expose the "
            "companion Character id bound to the forked OpenClaw sub-session "
            "(sessions_spawn, runtime='subagent', context='fork')."
        )

    @property
    def voice_id(self) -> str:
        raise NotImplementedError(
            "SubagentCompanion is the Tier-2 seam (Epic 13). It will expose the "
            "voice_id of the companion Character bound to the forked sub-session."
        )

    def take_turn(self, situation: dict) -> dict:
        raise NotImplementedError(
            "SubagentCompanion is the Tier-2 seam (Epic 13). take_turn will forward "
            "the situation into a forked OpenClaw sub-session (sessions_spawn, "
            "runtime='subagent', context='fork') that keeps the user's agent "
            "identity plus its own campaign memory, let it decide (optionally using "
            "suggest_action as a tactical aid via the engine), and return its action "
            "dict {'action', 'target_id', 'reason'}."
        )

    def react(self, event: dict) -> Optional[str]:
        raise NotImplementedError(
            "SubagentCompanion is the Tier-2 seam (Epic 13). react will forward the "
            "event into the forked sub-session and return its spoken line (or None)."
        )


# --- internal coercion helpers (keep take_turn tolerant of model-or-dict input) ---


def _coerce_combat(value) -> Optional[Combat]:
    if value is None:
        return None
    if isinstance(value, Combat):
        return value
    if isinstance(value, dict):
        return Combat.model_validate(value)
    raise TypeError(f"situation['combat'] must be a Combat or dict, got {type(value)!r}")


def _coerce_characters(value) -> dict[str, Character]:
    if not value:
        return {}
    out: dict[str, Character] = {}
    for cid, ch in value.items():
        if isinstance(ch, Character):
            out[cid] = ch
        elif isinstance(ch, dict):
            out[cid] = Character.model_validate(ch)
        else:
            raise TypeError(
                f"situation['characters'][{cid!r}] must be a Character or dict, "
                f"got {type(ch)!r}"
            )
    return out
