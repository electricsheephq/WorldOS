#!/usr/bin/env python3
"""Zero-LLM pre-seed for the combat-sprint QA lane.

Builds a combat-ready campaign by importing the engine directly (no MCP server,
no LLM), then prints a single JSON line with the IDs the DM prompt needs.

Choice: minimal campaign via create_campaign + add_location (make_current=True)
rather than start_world("baldurs-gate"). Rationale: start_world loads and indexes
the entire Baldur's Gate lore corpus (~330 locations, all wiki pages) — several
seconds of I/O that is irrelevant to the Angry-DM rubric (get_state / attack /
next_turn never touch world_id). The minimal path is ~50 ms and gives the rubric
everything it needs: a real location, a fighter PC, a cleric companion (caster
coverage so the rubric doesn't self-penalise), and 4 hostiles.

Usage (from repo root):
    CLAWDND_STATE_DIR=<dir> uv run --directory servers/engine python qa/pre_seed_combat.py <state_dir>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: pre_seed_combat.py <state_dir>", file=sys.stderr)
        sys.exit(1)

    state_dir = sys.argv[1]
    os.environ["CLAWDND_STATE_DIR"] = state_dir

    # Add the engine root to sys.path so `import server` works when this script
    # is invoked via `uv run --directory servers/engine python <abs-path>/qa/pre_seed_combat.py`.
    # uv --directory resolves the venv but does NOT add the project root to sys.path
    # for scripts given by absolute path; we mirror what pyproject.toml pythonpath=["."] does
    # for pytest. Use __file__ to find this script's repo root, then descend into servers/engine.
    _engine_dir = Path(__file__).resolve().parents[1] / "servers" / "engine"
    if str(_engine_dir) not in sys.path:
        sys.path.insert(0, str(_engine_dir))

    # Import AFTER patching env + sys.path.
    import server  # noqa: PLC0415

    # ── 1. Campaign + location ───────────────────────────────────────────────
    camp = server.create_campaign(
        title="Combat Sprint Seed",
        summary="Automated pre-seed for Angry-DM combat QA — no world corpus needed.",
    )
    campaign_id = camp["id"]

    # Minimal location: one room the party and hostiles share.
    server.add_location(
        campaign_id=campaign_id,
        name="Elfsong Tavern Cellar",
        description=(
            "A low stone cellar beneath the Elfsong Tavern. Barrels line the walls; "
            "a single lantern throws long shadows. Exits: the taproom stairs."
        ),
        make_current=True,
    )

    # ── 2. Start a session ───────────────────────────────────────────────────
    server.start_session(campaign_id, title="Combat Sprint")

    # ── 3. Fighter PC (L4) ───────────────────────────────────────────────────
    # apply_srd_defaults=True: sets proficiency bonus, HP (Fighter d10 + CON),
    # saving throws, and AC via chain mail so the rubric sees a real AC value.
    fighter = server.create_character(
        campaign_id=campaign_id,
        name="Aldric",
        kind="player",
        race="human",
        class_name="fighter",
        level=4,
        abilities={
            "strength": 18,
            "dexterity": 14,
            "constitution": 16,
            "intelligence": 10,
            "wisdom": 12,
            "charisma": 10,
        },
        background="soldier",
        apply_srd_defaults=True,
        skills=["athletics", "perception", "intimidation", "history"],
    )
    player_id = fighter["id"]

    # ── 4. Cleric companion (L4, caster coverage) ────────────────────────────
    # The Angry-DM rubric docks points when a session has no caster; the cleric
    # companion ensures cast_spell / saving_throw paths are exercised.
    cleric = server.create_character(
        campaign_id=campaign_id,
        name="Maren",
        kind="companion",
        race="half-elf",
        class_name="cleric",
        level=4,
        abilities={
            "strength": 12,
            "dexterity": 10,
            "constitution": 14,
            "intelligence": 12,
            "wisdom": 18,
            "charisma": 14,
        },
        background="acolyte",
        apply_srd_defaults=True,
        skills=["medicine", "religion", "insight", "persuasion"],
    )
    companion_id = cleric["id"]

    # Give Maren her spells (replaces the list — signatures: campaign_id, character_id, spells_list)
    server.learn_spells(campaign_id, companion_id, [
        "Cure Wounds",
        "Guiding Bolt",
        "Sacred Flame",
        "Spiritual Weapon",
        "Healing Word",
    ])

    # ── 5. Hostile encounter: 3 Bandits + 1 Bandit Captain ───────────────────
    # Balanced-deadly for a L4 party of 2: CR 1/8 × 3 + CR 2 × 1 ≈ 850 XP
    # (deadly threshold for a 2-person L4 party is ~700 XP — tight but fair).
    bandits = server.spawn_monster(campaign_id, "Bandit", count=3)
    captain = server.spawn_monster(campaign_id, "Bandit Captain", count=1)

    bandit_ids = [s["id"] for s in bandits["spawned"]]
    captain_ids = [s["id"] for s in captain["spawned"]]
    monster_ids = bandit_ids + captain_ids

    all_combatant_ids = [player_id, companion_id] + monster_ids

    result = {
        "campaign_id": campaign_id,
        "player_id": player_id,
        "companion_id": companion_id,
        "monster_ids": monster_ids,
        "all_combatant_ids": all_combatant_ids,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
