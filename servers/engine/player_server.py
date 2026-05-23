"""Constrained player-move facade (Sprint S1, It.1) — the player ACTS ONLY through
this limited tool surface, so it cannot narrate the world, voice NPCs, or assert
outcomes. The durable lesson made structural: enforce roles in CODE, not prose.

The player *declares*; the DM + dice *resolve*. This facade is READ-ONLY on campaign
state (the engine stays the sole writer) and only appends structured MOVES to the
file named by ``CLAWDND_PLAYER_MOVES`` for the DM (and the dashboard) to consume.
``cast_spell`` / ``use_item`` / ``request_check`` validate against the PC's ACTUAL
sheet, so you can only attempt what you actually have. This is the same move palette
the human play UI (It.2) will emit.

Run as its own MCP server (the player agent connects to ONLY this), e.g.
``uv run --directory servers/engine python player_server.py``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

import store
from models import SKILL_ABILITIES, Character

mcp = FastMCP("clawdnd-player")


# --- read-only campaign access (the single campaign in this state dir) -----------
def _campaign():
    camps = store.list_campaigns()
    if not camps:
        return None
    return store.load_campaign(camps[0]["id"])


def _pc() -> Optional[Character]:
    c = _campaign()
    if c is None:
        return None
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
    """Append a structured move to the moves file the orchestrator/dashboard reads."""
    move = {"role": "player", "kind": kind, "text": text, **fields}
    p = os.environ.get("CLAWDND_PLAYER_MOVES")
    if p:
        with Path(p).open("a", encoding="utf-8") as f:
            f.write(json.dumps(move) + "\n")
    return {"ok": True, "move": move}


# --- pure validators (unit-testable without MCP/store) ---------------------------
def known_spells(pc: Character) -> set[str]:
    return {s.lower() for s in (list(pc.spells_known) + list(pc.spells_prepared))}


def owned_items(pc: Character) -> set[str]:
    return {it.name.lower() for it in pc.inventory}


def validate_check(skill: str) -> tuple[bool, str]:
    ok = skill.lower() in SKILL_ABILITIES
    return ok, "" if ok else f"{skill!r} is not a 5e skill"


def validate_cast(pc: Character, name: str) -> tuple[bool, str]:
    if not name.strip():
        return False, "name a spell to cast"
    if name.lower() not in known_spells(pc):
        return False, f"{name!r} is not on your sheet — you don't know/prepare it"
    return True, ""


def validate_item(pc: Character, name: str) -> tuple[bool, str]:
    if name.lower() not in owned_items(pc):
        return False, f"you aren't carrying {name!r}"
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
    return _record("check", reason or f"attempt a {skill.lower()} check", skill=skill.lower())


@mcp.tool()
def cast_spell(name: str, target: str = "") -> dict:
    """Cast a spell you actually know/prepared. Refused if it isn't on your sheet; the
    engine spends the slot and resolves it."""
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
    """Attack a target (in combat). The DM/engine rolls and applies it."""
    return _record("attack", f"attack {target}" + (f" with {weapon}" if weapon else ""), target=target, weapon=weapon)


@mcp.tool()
def look() -> dict:
    """Look around: your current location + who is here (read-only)."""
    return _scene()


@mcp.tool()
def my_sheet() -> dict:
    """Your character sheet summary (read-only): HP, AC, skills, spells, inventory."""
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
    }


if __name__ == "__main__":
    mcp.run()
