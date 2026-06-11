"""F01-2 (#773): spawned monsters must carry their printed save proficiencies, and
monster proficiency bonus must be CR-derived — not the flat 2 the srd524 dump's
universal `proficiency_bonus: null` collapsed every creature to.

Two stacked defects on main:
  1. Neither spawn constructor set `saving_throw_proficiencies` — 132/344 creatures
     with printed proficient saves lost them on spawn (Hold Monster vs a dragon used
     the bare ability mod: marquee save-or-suck trivialized).
  2. ALL 344 stat blocks surfaced `proficiency_bonus == 2` regardless of CR — monster
     skill checks and `combat.grapple_save_dc` (8 + STR mod + prof) wrong at high CR.

Fix shape (per the audit's corrected spec): derive PB from CR at the bestiary layer
(PB = 2 + (ceil(CR)-1)//4), set the save flags via the shared
`_monster_character_from_statblock` factory used by BOTH spawn paths, and carry a
printed-total `save_bonus_overrides` for the residual data quirks — so
`saving_throw_bonus` equals the printed stat block for ALL creatures.
Old snapshots round-trip untouched (the fix applies at spawn time).
"""

import pytest


# Standard 5e CR -> proficiency bonus table boundaries.
CR_PB_CASES = [
    ("0", 2), ("1/8", 2), ("1/4", 2), ("1/2", 2), ("1", 2), ("4", 2),
    ("5", 3), ("8", 3), ("9", 4), ("12", 4), ("13", 5), ("16", 5),
    ("17", 6), ("20", 6), ("21", 7), ("24", 7), ("25", 8), ("28", 8),
    ("29", 9), ("30", 9),
]


@pytest.mark.parametrize("cr,pb", CR_PB_CASES)
def test_pb_from_cr_table(cr, pb):
    """PB = 2 + (ceil(CR)-1)//4 over the full standard table, fractions included."""
    import bestiary

    assert bestiary._pb_from_cr(cr) == pb


def test_pb_from_cr_garbage_defaults_to_2():
    import bestiary

    assert bestiary._pb_from_cr("") == 2
    assert bestiary._pb_from_cr("unknown") == 2


def test_stat_block_pb_is_cr_derived():
    """The flattened stat block carries the CR-derived PB (raw field is null in srd524)."""
    import bestiary

    assert bestiary.stat_block("Adult Gold Dragon")["proficiency_bonus"] == 6  # CR 17
    assert bestiary.stat_block("Aboleth")["proficiency_bonus"] == 4  # CR 10
    assert bestiary.stat_block("Goblin Warrior")["proficiency_bonus"] == 2  # CR 1/4


def test_spawned_dragon_keeps_printed_saves(tmp_path, monkeypatch):
    """Red-first marquee case: a spawned Adult Gold Dragon (CR 17) must make a DEX
    save at the PRINTED +8 (mod +2 + PB 6) — on main it rolled at the bare +2."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    from models import Ability

    cid = server.create_campaign("Dragon Saves F01-2")["id"]
    did = server.spawn_monster(cid, "Adult Gold Dragon")["spawned"][0]["id"]
    # Read the PERSISTED model back (snapshot round-trip included for free).
    ch = server._require(cid).characters[did]
    assert ch.proficiency_bonus == 6
    assert ch.saving_throw_bonus(Ability.DEX) == 8, "printed DEX save is +8"
    assert ch.saving_throw_bonus(Ability.WIS) == 8, "printed WIS save is +8"
    # Non-proficient saves stay at the bare ability modifier.
    assert Ability.CON not in ch.saving_throw_proficiencies
    assert ch.saving_throw_bonus(Ability.CON) == ch.ability_modifier(Ability.CON)


def test_full_sweep_saving_throw_bonus_matches_printed():
    """Every bestiary creature with printed proficient saves spawns with
    saving_throw_bonus == the printed total — all of them, no exceptions
    (the residual data quirks ride save_bonus_overrides)."""
    import bestiary
    import server
    from models import Ability

    checked = 0
    seen: set[str] = set()
    for key in bestiary._index().keys():
        sb = bestiary.stat_block(key)
        if sb is None or sb["name"] in seen:
            continue
        seen.add(sb["name"])
        saves = sb.get("saves") or {}
        if not saves:
            continue
        ch = server._monster_character_from_statblock(sb, sb["name"])
        for short, printed in saves.items():
            got = ch.saving_throw_bonus(Ability(short))
            assert got == printed, (
                f"{sb['name']} {short.upper()} save: spawned {got} != printed {printed}"
            )
        checked += 1
    # Tracks the committed srd524 dataset (audit F01-2 measured exactly 132).
    assert checked == 132, f"expected 132 creatures with printed saves; swept {checked}"


def test_residual_data_quirk_rides_override():
    """A creature whose printed total doesn't decompose as mod + CR-derived PB gets a
    printed-total override (audit found 4 such srd524 quirks; Octopus is one)."""
    import bestiary
    import server
    from models import Ability

    sb = bestiary.stat_block("Octopus")
    assert sb["saves"], "Octopus carries printed saves in srd524"
    ch = server._monster_character_from_statblock(sb, "Octopus")
    assert ch.save_bonus_overrides, "the quirk must be carried as an override"
    for short, printed in sb["saves"].items():
        assert ch.saving_throw_bonus(Ability(short)) == printed


def test_no_saves_creature_has_no_flags_or_overrides():
    """A creature with no printed save proficiencies spawns clean (no flags, no overrides)."""
    import bestiary
    import server

    sb = bestiary.stat_block("Wolf")
    assert not sb["saves"]
    ch = server._monster_character_from_statblock(sb, "Wolf")
    assert ch.saving_throw_proficiencies == []
    assert ch.save_bonus_overrides == {}


def test_grapple_dc_uses_cr_derived_pb():
    """combat.grapple_save_dc (8 + STR mod + prof) heals with the CR-derived PB —
    on main every high-CR grappler imposed a DC computed with PB=2."""
    import bestiary
    import combat
    import server
    from models import Ability

    sb = bestiary.stat_block("Adult Gold Dragon")
    ch = server._monster_character_from_statblock(sb, "Adult Gold Dragon")
    assert combat.grapple_save_dc(ch) == 8 + ch.ability_modifier(Ability.STR) + 6


def test_monster_skill_bonus_uses_cr_derived_pb():
    """Character.skill_bonus reads the same proficiency_bonus — a spawned high-CR
    monster's proficient-skill math is CR-correct (companion defect in F01-2)."""
    import bestiary
    import server

    sb = bestiary.stat_block("Adult Gold Dragon")
    ch = server._monster_character_from_statblock(sb, "Adult Gold Dragon")
    ch.skill_proficiencies = ["insight"]
    from models import Ability

    assert ch.skill_bonus("insight") == ch.ability_modifier(Ability.WIS) + 6


def test_wander_spawn_parity_with_spawn_monster(tmp_path, monkeypatch):
    """Both spawn paths construct through the SAME factory: the wandering-encounter
    path must carry the identical PB / save flags / overrides / Parry as spawn_monster
    (the hand-rolled wander ctor had drifted — it silently lost Parry, audit F01-11)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Wander Parity F01-2")["id"]
    sid = server.spawn_monster(cid, "Bandit Captain")["spawned"][0]["id"]
    with server.campaign_lock(cid):
        c = server._require(cid)
        wid = server._spawn_creature_chars(c, "Bandit Captain", 1, None)[0]["id"]
        server.save_campaign(c)
    chars = server._require(cid).characters  # the persisted models
    spawn_ch, wander_ch = chars[sid], chars[wid]
    assert wander_ch.parry == spawn_ch.parry == 2, "Bandit Captain's Parry is +2 on BOTH paths"
    assert wander_ch.proficiency_bonus == spawn_ch.proficiency_bonus
    assert wander_ch.saving_throw_proficiencies == spawn_ch.saving_throw_proficiencies != []
    assert wander_ch.save_bonus_overrides == spawn_ch.save_bonus_overrides


def test_old_snapshot_round_trips_without_new_field():
    """Additive-by-default: a pre-fix snapshot (no save_bonus_overrides key, PB=2,
    no save flags) deserializes unchanged — the fix applies at spawn time only."""
    from models import Character

    legacy = {
        "name": "Old Ogre",
        "kind": "monster",
        "max_hp": 59,
        "current_hp": 59,
        "proficiency_bonus": 2,
    }
    ch = Character.model_validate(legacy)
    assert ch.save_bonus_overrides == {}
    assert ch.saving_throw_proficiencies == []
    assert ch.proficiency_bonus == 2  # legacy stored value respected, no migration
    # And the new field survives a JSON round-trip.
    again = Character.model_validate(ch.model_dump(mode="json"))
    assert again.save_bonus_overrides == {}
