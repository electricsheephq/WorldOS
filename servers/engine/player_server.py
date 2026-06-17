"""Constrained move facade (Sprint S1, It.1; S3 multi-agent) — an ACTOR acts ONLY
through this limited tool surface, so it cannot narrate the world, voice NPCs, or
assert outcomes. The durable lesson made structural: enforce roles in CODE, not prose.

The actor *declares*; the DM + dice *resolve*. This facade is READ-ONLY on campaign
state (the engine stays the sole writer) and only appends structured MOVES to the
file named by ``WORLDOS_PLAYER_MOVES`` for the DM (and the dashboard) to consume.
``cast_spell`` / ``use_item`` / ``request_check`` validate against the actor's ACTUAL
sheet, so you can only attempt what you actually have. This is the same move palette
the human play UI (It.2) will emit.

ACTOR PARAMETERIZATION (S3 — the harness-ensemble model). Two env vars retarget the
facade so the SAME constrained surface drives every party member, each as its own
``claude -p`` peer agent:

- ``WORLDOS_ACTOR_ID``   — a character id. When set, the facade resolves THAT
  character (whatever its ``kind``: companion / a 2nd PC / etc.) and validates every
  move against ITS sheet (its own spells/slots/inventory). Unset = today's behavior:
  resolve the ``kind=="player"`` PC in the most-recently-updated campaign.
- ``WORLDOS_ACTOR_ROLE`` — the role string stamped on every emitted move (default
  ``"player"``). A companion run sets ``"companion"`` so the DM/dashboard can tell
  whose declaration it is.

DEFAULT (neither env set) == the original single-player facade EXACTLY — the env is
purely additive, so existing duo runs and tests are unchanged. The security boundary
is UNCHANGED and per-actor: an actor emits ONLY its own legal moves and can NEVER
narrate outcomes or act as the DM. A saboteur companion can therefore only propose
LEGAL moves (say/do/attack/cast); the engine resolves them, so a betrayal becomes
real combat, never narration.

Run as its own MCP server (each actor agent connects to ONLY this), e.g.
``uv run --directory servers/engine python player_server.py``.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

import spells
import store
from _env import env_var
from models import SKILL_ABILITIES, Character

mcp = FastMCP("worldos-player")


# --- read-only campaign access (the MOST-RECENT campaign in this state dir) -------
def _campaign():
    """The live campaign this facade speaks to.

    F12-15 / SYN-07 — PIN, don't re-resolve. ``WORLDOS_CAMPAIGN_ID`` (when set) names the
    EXACT campaign this actor belongs to, so the facade reads THAT campaign on every call
    regardless of which one is freshest. This closes the #640 silent-switch family: with a
    parallel campaign B running, the old max(updated_at) heuristic re-resolved "the live
    campaign" each call, and an ACTOR_ID bound to a character that only lives in campaign A
    silently resolved to None the moment B took the lead (the companion went mute / its
    moves were refused). A pure facade READ must never flip which campaign is live.

    ADDITIVE: the pin is unset by default. Unset (or blank/whitespace) -> the original
    selector below, byte-identical. An unknown pin (a stale/typo'd id not on disk) degrades
    to the heuristic rather than resolving to None — a bad pin must not silently mute the
    actor; it falls back to today's behavior.

    The heuristic (no pin): ``list_campaigns`` sorts by directory name (UUID), NOT recency,
    so picking ``[0]`` could read a STALE campaign left in a reused state dir and validate
    the player against the wrong character's sheet (H3). Pick the most-recently-updated one
    — that's the session actually being played."""
    pinned = (env_var("CAMPAIGN_ID") or "").strip()
    if pinned:
        c = store.load_campaign(pinned)
        if c is not None:
            return c
        # Unknown/stale pin -> fall through to the heuristic (never silently mute the actor).
    camps = store.list_campaigns()
    if not camps:
        return None
    latest = max(camps, key=lambda c: c.get("updated_at") or 0)
    return store.load_campaign(latest["id"])


def _actor_id() -> str:
    """The character this facade speaks for, or "" for default (the player PC). A
    blank/whitespace value is treated as unset — so an empty env var doesn't silently
    select 'no character'."""
    return (env_var("ACTOR_ID") or "").strip()


_ACTOR_ROLES = ("player", "companion")  # the only roles the ensemble emits


def _actor_role() -> str:
    """The role stamped on emitted moves. Default "player" == today's behavior; a
    companion agent sets "companion" so the DM can tell whose declaration it is.

    A-LOW-2: ``WORLDOS_ACTOR_ROLE`` is operator-supplied free text. Clamp it to the
    allowlist so a typo (or an injected value) can't smuggle an arbitrary role onto
    every move the DM/dashboard then trusts — blank -> "player" (today's default),
    any unknown value -> "companion" (the safe non-narrator peer role)."""
    raw = (env_var("ACTOR_ROLE") or "").strip().lower()
    if not raw:
        return "player"
    return raw if raw in _ACTOR_ROLES else "companion"


def _pc() -> Optional[Character]:
    """Resolve the character this facade acts for. When ``WORLDOS_ACTOR_ID`` is set,
    return THAT character by id (any kind — a companion, a 2nd PC), so its moves
    validate against its OWN sheet. Unset = today's behavior: the ``kind=="player"``
    PC of the live campaign (party first, then any player record)."""
    c = _campaign()
    if c is None:
        return None
    aid = _actor_id()
    if aid:
        # Explicit actor: bind to THAT character's sheet (validators use it). If the id
        # isn't in the live campaign, return None — the actor has no sheet to act with
        # (its moves are then refused, the same as "no character yet" for the player).
        ch = c.characters.get(aid)
        # A-LOW-1: only a live PLAYER/COMPANION may emit moves through this facade. An
        # actor id pointing at a monster/npc (not a party peer) or a DEAD character must
        # resolve to no sheet, so its moves are refused — the move palette is the human-
        # play surface (It.2 reuses this), never a way to drive a monster or a corpse.
        if ch is not None and (ch.kind not in ("player", "companion") or ch.dead):
            return None
        return ch
    for cid in c.party:
        ch = c.characters.get(cid)
        if ch is not None and ch.kind == "player":
            return ch
    return next((ch for ch in c.characters.values() if ch.kind == "player"), None)


def _scene() -> dict:
    c = _campaign()
    if c is None:
        return {"note": "the adventure hasn't started yet"}
    loc = c.locations.get(c.current_location_id) if c.current_location_id else None
    here = [
        ch.name for ch in c.characters.values()
        if ch.kind in ("npc", "monster") and ch.location_id == c.current_location_id
    ]
    return {
        "location": loc.name if loc else None,
        "description": loc.description if loc else "",
        "present": here,
        "companions": [
            c.characters[i].name for i in c.party
            if i in c.characters and c.characters[i].kind == "companion"
        ],
    }


def _record(kind: str, text: str, **fields) -> dict:
    """Append a structured move to the moves file the orchestrator/dashboard reads.
    The move is tagged with the actor's ROLE (``WORLDOS_ACTOR_ROLE``, default
    "player") and, when an explicit actor is bound, its ``actor_id`` — so the
    orchestrator can relay each actor's moves to the DM under the right banner and
    the dashboard can attribute them. Default (no env) == the original
    ``role:"player"`` record, no ``actor_id`` key, so existing consumers are unchanged.

    flock the append (L9): a second writer (a companion / 2nd PC, the viewer's /move
    path) must not interleave-corrupt a half-written JSONL line — the engine flocks
    every campaign write; the moves file gets the same guarantee. With N companion
    agents all appending to (separate, but possibly shared) moves files, this lock is
    what keeps each line atomic."""
    move = {"role": _actor_role(), "kind": kind, "text": text, **fields}
    aid = _actor_id()
    if aid:
        move["actor_id"] = aid
    p = env_var("PLAYER_MOVES")
    if p:
        with Path(p).open("a", encoding="utf-8") as f:
            with contextlib.suppress(OSError):
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(move) + "\n")
            f.flush()
            with contextlib.suppress(OSError):
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return {"ok": True, "move": move}


_CLARIFY_PER_TURN = 3  # cap consecutive questions so clarify can't become a forever ping-pong


def _consecutive_clarifies() -> int:
    """How many ``clarify`` moves THIS actor has emitted since its last REAL (non-clarify) move —
    the 'this turn' proxy that bounds the question budget. Reads the moves-file tail; a real action
    (say/do/attack/…) resets it. 0 when there's no moves file or it's unreadable (fail-open: a read
    glitch must never block a legitimate question)."""
    p = env_var("PLAYER_MOVES")
    if not p or not Path(p).exists():
        return 0
    role, aid = _actor_role(), _actor_id()
    count = 0
    try:
        for line in reversed(Path(p).read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if m.get("role") != role or (m.get("actor_id", "") or "") != aid:
                continue  # only THIS actor's moves count against its own budget
            if m.get("kind") == "clarify":
                count += 1
            else:
                break  # a real move ends the run -> the per-turn clarify budget resets
    except OSError:
        return 0
    return count


# --- pure validators (unit-testable without MCP/store) ---------------------------
def known_spells(pc: Character) -> set[str]:
    return {s.strip().lower() for s in (list(pc.spells_known) + list(pc.spells_prepared))}


def owned_items(pc: Character) -> set[str]:
    return {it.name.strip().lower() for it in pc.inventory}


def _norm_skill(skill: str) -> str:
    """Normalize a skill name to the engine's key form — SKILL_ABILITIES uses underscores
    (sleight_of_hand, animal_handling), so a player asking for 'Sleight of Hand' must map
    to the same key (else a core rogue skill is false-refused)."""
    return skill.strip().lower().replace(" ", "_")


def validate_check(skill: str) -> tuple[bool, str]:
    ok = _norm_skill(skill) in SKILL_ABILITIES
    return ok, "" if ok else f"{skill!r} is not a 5e skill"


def spell_base_level(name: str) -> Optional[int]:
    """The spell's base level from the rules data (0 = cantrip); None if the spell is
    unknown to both the curated set and SRD — in which case we DON'T refuse on slots
    (the engine degrades gracefully on un-modeled spells; the facade shouldn't be
    stricter than the engine and false-refuse a real spell)."""
    data = None
    try:
        data = spells.spell_data(name)
    except ValueError:
        data = None
    if data is None:
        data = spells.srd_spell(name)
    if data is None:
        return None
    return int(data.get("level", 0) or 0)


def has_slot_for(pc: Character, level: int) -> bool:
    """True if the PC has an unspent slot usable for a spell of ``level`` — upcast-aware
    (any slot at >= that level counts), mirroring the engine (server.py cast_spell:
    ``lvl >= spell_level`` and ``slot.used < slot.maximum``). Cantrips (level 0) are
    always castable."""
    if level <= 0:
        return True
    return any(lv >= level and slot.used < slot.maximum
               for lv, slot in pc.spell_slots.items())


def validate_cast(pc: Character, name: str) -> tuple[bool, str]:
    if not name.strip():
        return False, "name a spell to cast"
    if name.strip().lower() not in known_spells(pc):
        return False, f"{name!r} is not on your sheet — you don't know/prepare it"
    # C1: a leveled spell needs an actual slot — known-ness alone was the hole that let
    # a tapped-out caster "cast" with no slots, which the DM would then narrate as real.
    level = spell_base_level(name)
    if level is not None and not has_slot_for(pc, level):
        return False, f"no level-{level}+ spell slot left — you're out of slots for {name!r}"
    return True, ""


def validate_item(pc: Character, name: str) -> tuple[bool, str]:
    if name.strip().lower() not in owned_items(pc):
        return False, f"you aren't carrying {name!r}"
    return True, ""


def validate_attack(pc: Character, target: str, weapon: str) -> tuple[bool, str]:
    """H2: ``attack`` was a free pass (no validation at all). Require a real target,
    and if a weapon is named it must be one you actually carry (same spirit as
    ``use_item``). We deliberately do NOT gate on in-combat — declaring an attack can
    legitimately START a fight; the DM rolls initiative and resolves. The engine
    remains the authority on hit/damage; this just stops "attack nothing" / "attack
    with a weapon you don't own" from being relayed to the DM as a valid declaration.

    F12-19: the weapon compare is now STRIPPED+lowered to match ``owned_items`` (so a
    trailing space — "sword " — is no longer false-refused), and an EMPTY inventory no
    longer bypasses the check (it means you own nothing, so a named weapon is invalid —
    unarmed/improvised attacks pass a blank weapon)."""
    if not target.strip():
        return False, "name a target to attack"
    if weapon.strip() and weapon.strip().lower() not in owned_items(pc):
        return False, f"you aren't carrying {weapon!r} to attack with"
    return True, ""


# --- the player's ENTIRE tool surface --------------------------------------------
def _text_arg(canonical: str, *aliases: str) -> str:
    """Coalesce the canonical free-text arg with the intuitive aliases an LLM reaches for —
    the SAME additive discipline the engine uses across server.py (``travel_to`` accepts
    ``destination``/``to``; ``spawn_monster`` accepts ``monster``/``creature``; etc.).

    #928: a companion peer-agent's ``say`` call arrived as ``{message: ...}`` (not the
    canonical ``line``), so FastMCP's ``sayArguments`` model raised ``Field required
    [type=missing]`` BEFORE the function body ran — a hard schema rejection that trips the
    FATAL ``no_rejected_tool_calls`` behavioral gate and REDs the whole party run before a
    companion thaw can even be measured. The fix is purely additive: accept the canonical
    name OR the common aliases (``message`` / ``text``), coalescing to the first non-blank.
    The emitted MOVE record is byte-identical (it always carried ``text``), so the relay
    (``[\\(.kind)] \\(.text)``) and every existing consumer are unchanged. ``canonical``
    wins if more than one is supplied."""
    for v in (canonical, *aliases):
        if (v or "").strip():
            return v
    return canonical


@mcp.tool()
def say(line: str = "", message: str = "", text: str = "") -> dict:
    """Speak your character's OWN words (dialogue). Just your line — quotes are fine.

    Pass your words via ``line`` (canonical) or the aliases ``message`` / ``text`` —
    ``line`` wins if more than one is given (so a companion agent that reaches for the
    intuitive ``message``/``text`` is never schema-refused, #928)."""
    return _record("say", _text_arg(line, message, text))


@mcp.tool()
def do(action: str = "", message: str = "", text: str = "") -> dict:
    """Declare a physical action your character ATTEMPTS — intent only. The DM and the
    dice decide if it works; do NOT describe the world or assert the result.

    Pass the action via ``action`` (canonical) or the aliases ``message`` / ``text``."""
    return _record("do", _text_arg(action, message, text))


@mcp.tool()
def clarify(question: str = "", message: str = "", text: str = "") -> dict:
    """Ask the DM a CLARIFYING QUESTION before you commit to an action — exactly like asking a
    real Dungeon Master at the table ("Is the guard armed? How far is the door? Do I recognize
    this sigil? What do I actually know about this person?"). This is NOT an action and does NOT
    advance the scene or spend your turn: the DM answers what your character could plausibly
    perceive or know, then you still get to act. Reach for it when the scene is ambiguous and the
    answer would change your choice — better a quick question than a blind guess. Bounded: up to
    3 questions before you must act (so it can't become an endless back-and-forth).

    Pass the question via ``question`` (canonical) or the aliases ``message`` / ``text``."""
    question = _text_arg(question, message, text)
    if not question.strip():
        return {"ok": False, "error": "ask a real question"}
    if _consecutive_clarifies() >= _CLARIFY_PER_TURN:
        return {"ok": False, "error": (
            f"you've asked {_CLARIFY_PER_TURN} questions this turn — act now; the DM will fill in "
            f"the rest as you play.")}
    return _record("clarify", question.strip())


@mcp.tool()
def request_check(skill: str, reason: str = "") -> dict:
    """Ask the DM to roll a skill check for you (e.g. 'stealth', 'persuasion'). The DM
    rolls with your real modifiers and narrates the outcome."""
    ok, why = validate_check(skill)
    if not ok:
        return {"ok": False, "error": why}
    s = _norm_skill(skill)
    return _record("check", reason or f"attempt a {s} check", skill=s)


@mcp.tool()
def cast_spell(name: str, target: str = "") -> dict:
    """Declare casting a spell you actually know/prepared AND have a slot for. Refused
    if it isn't on your sheet, or if you're out of slots for it. This records your
    INTENT; the DM resolves it through the engine (which spends the slot)."""
    pc = _pc()
    if pc is None:
        return {"ok": False, "error": "no character yet"}
    ok, why = validate_cast(pc, name)
    if not ok:
        return {"ok": False, "error": why}
    return _record("cast", f"cast {name}" + (f" at {target}" if target else ""), name=name, target=target)


@mcp.tool()
def use_item(name: str) -> dict:
    """Use an item you actually carry. Refused if it isn't in your inventory."""
    pc = _pc()
    if pc is None:
        return {"ok": False, "error": "no character yet"}
    ok, why = validate_item(pc, name)
    if not ok:
        return {"ok": False, "error": why}
    return _record("use_item", f"use {name}", name=name)


@mcp.tool()
def attack(target: str, weapon: str = "") -> dict:
    """Attack a target. Name who/what you're attacking; if you name a weapon it must be
    one you carry. The DM/engine rolls to hit and applies damage (and starts combat if
    needed)."""
    pc = _pc()
    if pc is None:
        return {"ok": False, "error": "no character yet"}
    ok, why = validate_attack(pc, target, weapon)
    if not ok:
        return {"ok": False, "error": why}
    return _record("attack", f"attack {target}" + (f" with {weapon}" if weapon else ""), target=target, weapon=weapon)


@mcp.tool()
def look() -> dict:
    """Look around: your current location + who is here (read-only)."""
    return _scene()


@mcp.tool()
def my_sheet() -> dict:
    """Your character sheet summary (read-only): HP, AC, skills, spells, inventory,
    spell slots, and your ``attitude`` toward the party. ``attitude_value`` (-100..+100,
    0 = neutral) lets a companion read its OWN standing — the betrayal hook: a sealed
    agenda can say "when your attitude_value drops below -40, turn on them"."""
    pc = _pc()
    if pc is None:
        return {"error": "no character yet"}
    return {
        "name": pc.name,
        "hp": f"{pc.current_hp}/{pc.max_hp}",
        "ac": pc.armor_class,
        "skills": list(pc.skill_proficiencies),
        "spells": sorted(known_spells(pc)),
        "inventory": [it.name for it in pc.inventory],
        # slots remaining per level, so a caster knows what it can actually cast.
        "spell_slots": {lv: f"{max(0, s.maximum - s.used)}/{s.maximum}"
                        for lv, s in sorted(pc.spell_slots.items())},
        "attitude": pc.attitude,
        "attitude_value": pc.attitude_value,
    }


if __name__ == "__main__":
    mcp.run()
