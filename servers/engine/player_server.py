"""Constrained move facade (Sprint S1, It.1; S3 multi-agent) — an ACTOR acts ONLY
through this limited tool surface, so it cannot narrate the world, voice NPCs, or
assert outcomes. The durable lesson made structural: enforce roles in CODE, not prose.

The actor *declares*; the DM + dice *resolve*. This facade is READ-ONLY on campaign
state (the engine stays the sole writer) and only appends structured MOVES to the
file named by ``CLAWDND_PLAYER_MOVES`` for the DM (and the dashboard) to consume.
``cast_spell`` / ``use_item`` / ``request_check`` validate against the actor's ACTUAL
sheet, so you can only attempt what you actually have. This is the same move palette
the human play UI (It.2) will emit.

ACTOR PARAMETERIZATION (S3 — the harness-ensemble model). Two env vars retarget the
facade so the SAME constrained surface drives every party member, each as its own
``claude -p`` peer agent:

- ``CLAWDND_ACTOR_ID``   — a character id. When set, the facade resolves THAT
  character (whatever its ``kind``: companion / a 2nd PC / etc.) and validates every
  move against ITS sheet (its own spells/slots/inventory). Unset = today's behavior:
  resolve the ``kind=="player"`` PC in the most-recently-updated campaign.
- ``CLAWDND_ACTOR_ROLE`` — the role string stamped on every emitted move (default
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
import os
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

import spells
import store
from models import SKILL_ABILITIES, Character

mcp = FastMCP("clawdnd-player")


# --- read-only campaign access (the MOST-RECENT campaign in this state dir) -------
def _campaign():
    """The live campaign. ``list_campaigns`` sorts by directory name (UUID), NOT
    recency, so picking ``[0]`` could read a STALE campaign left in a reused state
    dir and validate the player against the wrong character's sheet (H3). Pick the
    most-recently-updated one — that's the session actually being played."""
    camps = store.list_campaigns()
    if not camps:
        return None
    latest = max(camps, key=lambda c: c.get("updated_at") or 0)
    return store.load_campaign(latest["id"])


def _actor_id() -> str:
    """The character this facade speaks for, or "" for default (the player PC). A
    blank/whitespace value is treated as unset — so an empty env var doesn't silently
    select 'no character'."""
    return (os.environ.get("CLAWDND_ACTOR_ID") or "").strip()


def _actor_role() -> str:
    """The role stamped on emitted moves. Default "player" == today's behavior; a
    companion agent sets "companion" so the DM can tell whose declaration it is."""
    return (os.environ.get("CLAWDND_ACTOR_ROLE") or "").strip() or "player"


def _pc() -> Optional[Character]:
    """Resolve the character this facade acts for. When ``CLAWDND_ACTOR_ID`` is set,
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
        return c.characters.get(aid)
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
    The move is tagged with the actor's ROLE (``CLAWDND_ACTOR_ROLE``, default
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
    p = os.environ.get("CLAWDND_PLAYER_MOVES")
    if p:
        with Path(p).open("a", encoding="utf-8") as f:
            with contextlib.suppress(OSError):
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(move) + "\n")
            f.flush()
            with contextlib.suppress(OSError):
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return {"ok": True, "move": move}


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
    with a weapon you don't own" from being relayed to the DM as a valid declaration."""
    if not target.strip():
        return False, "name a target to attack"
    if weapon.strip() and owned_items(pc) and weapon.lower() not in owned_items(pc):
        return False, f"you aren't carrying {weapon!r} to attack with"
    return True, ""


# --- the player's ENTIRE tool surface --------------------------------------------
@mcp.tool()
def say(line: str) -> dict:
    """Speak your character's OWN words (dialogue). Just your line — quotes are fine."""
    return _record("say", line)


@mcp.tool()
def do(action: str) -> dict:
    """Declare a physical action your character ATTEMPTS — intent only. The DM and the
    dice decide if it works; do NOT describe the world or assert the result."""
    return _record("do", action)


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
