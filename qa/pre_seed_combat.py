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
coverage so the rubric doesn't self-penalise), and a hostile encounter.

Enrichment (#195) — the seed deliberately spans the FULL 5e combat surface so the
combat-sprint stops being a coverage-capped weapon-swing and starts surfacing the
next batch of real engine/adherence gaps. The Angry-DM rubric's section 8 docks a
"level-3 Battle Master who never has/uses Superiority Dice" and its section 5 names
"a ghoul's paralysis" rider; this seed populates exactly those hooks:
  - Aldric is a **Battle Master** Fighter; Superiority Dice are seeded with
    set_class_resource (the SRD tables only auto-derive BASE-class pools — a Battle
    Master's dice are invisible to the engine until registered), so maneuvers
    (Riposte / Trip Attack / Precision) are exercisable.
  - Maren is a **War Domain** Cleric; her Channel Divinity pool is auto-seeded by
    apply_srd_defaults (Cleric CD from L2), and her prepared list carries SAVE-
    requiring spells (Bane, Hold Person) so the cast_spell -> spell_save_dc ->
    saving_throw -> add_condition pipeline can fire.
  - The encounter includes a **Ghoul**, whose Claw forces a CON save or Paralyzed —
    so saves are forced on the party REGARDLESS of the casters' spell choices.
  - The mook count is tuned so the fight CAN run to at least one enemy at 0 HP in
    ~3-5 rounds (the XP-award path validates) while staying non-trivial.

Usage (from repo root):
    WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python qa/pre_seed_combat.py <state_dir>
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
    os.environ["WORLDOS_STATE_DIR"] = state_dir

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

    # ── 3. Fighter PC (L4 Battle Master) ─────────────────────────────────────
    # apply_srd_defaults=True: sets proficiency bonus, HP (Fighter d10 + CON),
    # saving throws, base-class pools (Second Wind, Action Surge), and AC via chain
    # mail so the rubric sees a real AC value.
    # subclass="Battle Master": the Martial Archetype Aldric took at L3. The SRD
    # tables auto-derive only BASE-class pools, so the subclass's Superiority Dice
    # are seeded explicitly below (set_class_resource) — without that a Battle
    # Master has no dice to spend and the rubric flags the missing feature exercise.
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
        subclass="Battle Master",
        apply_srd_defaults=True,
        skills=["athletics", "perception", "intimidation", "history"],
    )
    player_id = fighter["id"]

    # Seed the Battle Master's Superiority Dice (SRD 5.2: 4 dice at L3-6, each a d8
    # until L10; short-rest recharge). Now Riposte / Trip Attack / Precision Attack /
    # Menacing Attack have a real pool to spend against during the sprint.
    server.set_class_resource(
        campaign_id=campaign_id,
        character_id=player_id,
        resource="superiority_dice",
        max=4,
        recharge="short",
        size="d8",
    )

    # ── 4. Cleric companion (L4 War Domain, caster coverage) ──────────────────
    # The Angry-DM rubric docks points when a session has no caster; the cleric
    # companion ensures cast_spell / saving_throw paths are exercised.
    # subclass="War Domain": a domain Cleric whose Channel Divinity (Guided Strike /
    # War God's Blessing) is auto-seeded by apply_srd_defaults (Cleric CD from L2 ->
    # 2 uses at L4), so a subclass feature pool is populated + testable without any
    # extra set_class_resource call.
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
        subclass="War Domain",
        apply_srd_defaults=True,
        skills=["medicine", "religion", "insight", "persuasion"],
    )
    companion_id = cleric["id"]

    # Maren's spellbook — known + prepared (clerics PREPARE from the full list; the
    # engine reads spells_known | spells_prepared when validating a cast).
    # Rotation deliberately mixes the families the rubric tracks:
    #   - SAVE-requiring control: Bane (CHA save, debuff) + Hold Person (WIS save ->
    #     Paralyzed) — these drive cast_spell -> spell_save_dc -> saving_throw ->
    #     add_condition, the pipeline the vanilla seed never exercised.
    #   - Attack-roll / radiant: Guiding Bolt, Sacred Flame, Spiritual Weapon.
    #   - Healing: Cure Wounds, Healing Word (apply_healing path).
    spell_list = [
        "Bane",
        "Hold Person",
        "Cure Wounds",
        "Healing Word",
        "Guiding Bolt",
        "Sacred Flame",
        "Spiritual Weapon",
    ]
    # learn_spells sets spells_known; prepare_spells sets spells_prepared (clerics
    # prepare). Seed BOTH so the cast-validation never rejects a save-spell as unknown.
    server.learn_spells(campaign_id, companion_id, spell_list)
    server.prepare_spells(campaign_id, companion_id, spell_list)

    # ── 5. Hostile encounter: 1 Bandit + 1 Bandit Captain + 1 Ghoul ──────────
    # Tuned so the fight CAN finish (run-to-kill -> XP-award path validates) while
    # staying non-trivial, AND so a SAVE is forced regardless of the casters' choices:
    #   - Bandit       CR 1/8, 11 HP, AC 12  — the soft mook: a single longsword hit
    #                  or Guiding Bolt drops it, so at least one enemy reaches 0 HP in
    #                  round 1-2 and end_combat auto-awards XP.
    #   - Bandit Captain CR 2, 52 HP, AC 15  — the durable "boss" the party grinds; a
    #                  real threat (Multiattack + a parry reaction) that keeps the
    #                  3-5 round fight honest rather than a walkover.
    #   - Ghoul        CR 1, 22 HP, AC 12    — its Claw forces a DC 10 CON save or the
    #                  target is Paralyzed (a SEPARATE saving_throw + add_condition
    #                  rider). This guarantees the save -> condition pipeline fires on
    #                  the PARTY even if Maren only ever casts attack-roll/heal spells.
    # Raw XP 25 + 450 + 200 = 675; deadly-band for a 2× L4 party but very survivable
    # because two of the three foes are low-HP and fall fast.
    bandits = server.spawn_monster(campaign_id, "Bandit", count=1)
    captain = server.spawn_monster(campaign_id, "Bandit Captain", count=1)
    ghoul = server.spawn_monster(campaign_id, "Ghoul", count=1)

    bandit_ids = [s["id"] for s in bandits["spawned"]]
    captain_ids = [s["id"] for s in captain["spawned"]]
    ghoul_ids = [s["id"] for s in ghoul["spawned"]]
    monster_ids = bandit_ids + captain_ids + ghoul_ids

    all_combatant_ids = [player_id, companion_id] + monster_ids

    result = {
        "campaign_id": campaign_id,
        "player_id": player_id,
        "companion_id": companion_id,
        "monster_ids": monster_ids,
        # Named ids so the DM prompt can route the save-enemy + save-spells precisely
        # (the Ghoul's paralysis rider, Hold Person on the Captain, etc.).
        "bandit_ids": bandit_ids,
        "captain_id": captain_ids[0] if captain_ids else None,
        "ghoul_id": ghoul_ids[0] if ghoul_ids else None,
        "all_combatant_ids": all_combatant_ids,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
