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

import dice as dice_mod
from models import Ability, AbilityScores, Campaign, Character, ClassLevel
from store import list_campaigns as _list_campaigns
from store import load_campaign, save_campaign

mcp = FastMCP("clawdnd-engine")


def _require(campaign_id: str) -> Campaign:
    c = load_campaign(campaign_id)
    if c is None:
        raise ValueError(f"no campaign with id {campaign_id!r}")
    return c


def _deep_update(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


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
    add_to_party: bool = True,
) -> dict:
    """Create a character (player, companion, npc, or monster) and persist it.

    `abilities` is an optional dict like {"strength": 15, "dexterity": 14, ...}.
    Returns the new character id.
    """
    c = _require(campaign_id)
    scores = AbilityScores(**(abilities or {}))
    ch = Character(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        race=race,
        voice_id=voice_id,
        classes=[ClassLevel(name=class_name, level=level)] if class_name else [],
        abilities=scores,
        max_hp=max_hp,
        current_hp=max_hp,
        armor_class=armor_class,
        initiative_bonus=scores.modifier(Ability.DEX),
    )
    c.characters[ch.id] = ch
    if add_to_party and kind in ("player", "companion"):
        c.party.append(ch.id)
    save_campaign(c)
    return {"id": ch.id, "name": ch.name, "kind": ch.kind}


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

    `patch` is a dict of fields to change (deep-merged), e.g.
    {"current_hp": 12, "conditions": ["prone"]}. The result is re-validated
    against the schema, so invalid changes are rejected.
    """
    c = _require(campaign_id)
    ch = c.characters.get(character_id)
    if ch is None:
        raise ValueError(f"no character {character_id!r} in campaign")
    data = ch.model_dump(mode="json")
    _deep_update(data, patch)
    c.characters[character_id] = Character.model_validate(data)
    save_campaign(c)
    return c.characters[character_id].model_dump(mode="json")


if __name__ == "__main__":
    mcp.run()
