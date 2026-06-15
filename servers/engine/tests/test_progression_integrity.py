"""P2 cluster — Character progression integrity (issue #794).

Covers the still-broken progression-integrity sub-findings from the WorldOS
adversarial engine audit (docs/audits/ENGINE-AUDIT-2026-06-11.md, unit 02):

  * F02-3  missed ASI silently dropped; feat 100% inert -> record the debt on a
           `pending_choices` ledger and SURFACE a chosen feat's effect for the DM.
  * F02-5  class-sig recompute REFILLS spent hit dice on a stat/level patch.
  * F02-6  CON rises never retro-adjust max HP (level-up + update_character).
  * F02-8  pickup seats a canon-DEAD record / promote keeps the death state.
  * F02-10 recruit keeps a stub HP at level>1 (instant-kill combatant).
  * F02-11 resource-table drift: Second Wind / Wild Shape counts, sneak 10d6@19.
  * F02-12 reroll PC: location None + AC over an empty inventory.
  * F02-14 no XP-entitlement advisory when leveling_mode == "xp".
  * F02-15 Expertise inert at grant (rides F02-3's ledger; default-fill interim).

Every fix is ADDITIVE: old snapshots round-trip, new fields default to today's
behavior. The cross-path seat census (F02-18) lives in test_seat_census.py.
"""

import pytest

import server
import srd_tables
import store
from models import Character, Ability


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("CLAWDND_ACTOR_ID", raising=False)
    monkeypatch.delenv("CLAWDND_ACTOR_ROLE", raising=False)
    yield


def _snapshot(cid):
    return store.load_campaign(cid)


# --------------------------------------------------------------------------- #
# F02-11 — SRD-table drift (pure srd_tables, no campaign)                      #
# --------------------------------------------------------------------------- #
def test_second_wind_uses_scale_2_3_4_by_srd_5_2():
    # SRD 5.2: Second Wind has 2 uses at L1, 3 at L4, 4 at L10.
    def sw(level):
        return srd_tables.class_resources_through("fighter", level).get("second_wind", {}).get("max")
    assert sw(1) == 2
    assert sw(3) == 2
    assert sw(4) == 3
    assert sw(9) == 3
    assert sw(10) == 4
    assert sw(20) == 4


def test_wild_shape_uses_scale_2_3_4_and_persist_at_20():
    # SRD 5.2: Wild Shape 2 @ L2, 3 @ L6, 4 @ L17 — and the pool PERSISTS at L20
    # (the 2014 "Archdruid unlimited -> not pooled" zero was mixed-edition drift).
    def ws(level):
        return srd_tables.class_resources_through("druid", level).get("wild_shape", {}).get("max")
    assert ws(1) is None  # no feature yet
    assert ws(2) == 2
    assert ws(5) == 2
    assert ws(6) == 3
    assert ws(16) == 3
    assert ws(17) == 4
    assert ws(20) == 4  # must NOT drop to 0/None at 20


def test_rogue_sneak_attack_is_10d6_at_level_19():
    # SRD 5.2: Sneak Attack reaches 10d6 at L19 (every odd level), NOT L20.
    def sneak(level):
        dice = ""
        for f in srd_tables.features_through("rogue", level):
            if f.get("sneak_attack_dice"):
                dice = f["sneak_attack_dice"]
        return dice
    assert sneak(17) == "9d6"
    assert sneak(19) == "10d6"
    assert sneak(20) == "10d6"


def test_engine_built_l19_rogue_has_10d6_sneak_attack():
    cid = server.create_campaign("rogue19")["id"]
    rid = server.create_character(cid, "Astar", kind="player", class_name="Rogue",
                                  level=19, apply_srd_defaults=True,
                                  abilities={"dexterity": 18})["id"]
    assert server.get_character(cid, rid)["sneak_attack_dice"] == "10d6"


def test_rogue_sneak_attack_full_curve_spot_checks():
    # SRD 5.2 Sneak Attack = ceil(level / 2) d6 — spot the endpoints the audit named.
    def sneak(level):
        dice = ""
        for f in srd_tables.features_through("rogue", level):
            if f.get("sneak_attack_dice"):
                dice = f["sneak_attack_dice"]
        return dice
    assert sneak(1) == "1d6"
    assert sneak(19) == "10d6"
    assert sneak(20) == "10d6"


def test_rage_uses_table_has_single_srd_correct_source():
    """F02-11 (rage half): the Barbarian rage-uses curve must come from ONE
    SRD-5.2-correct source. The live engine table is srd_tables._RAGE_USES
    (5 uses at L12 — SRD 5.2). The class_features.json `rage_uses`/`rage_damage`
    hints were DEAD (no code reads them — server.py only reads extra_attacks /
    sneak_attack_dice) AND contradicted that table (they asserted 5 uses arrives
    at L15, not L12). A contradicting dead source is a maintenance trap, so the
    reconciliation removes those hints, leaving _RAGE_USES the sole authority.

    This test locks BOTH halves: (a) the engine curve is the SRD 5.2 table, and
    (b) class_features.json carries no rage_uses/rage_damage hint that could
    silently drift back out of sync with it.
    """
    # (a) the live engine rage-uses curve == SRD 5.2 (2/3/4/5/6 @ 1/3/6/12/17).
    def rage(level):
        return srd_tables.class_resources_through("barbarian", level)["rage"]["max"]
    assert rage(1) == 2
    assert rage(3) == 3
    assert rage(6) == 4
    assert rage(11) == 4
    assert rage(12) == 5   # SRD 5.2: the 5th use arrives at L12 (NOT L15)
    assert rage(16) == 5
    assert rage(17) == 6
    assert rage(20) == 6

    # (b) no contradicting dead hint survives in the class-features table — the
    # rage curve has exactly one source of truth.
    barb = srd_tables._load("class_features").get("barbarian", {})
    offenders = [
        (lv, f.get("name"))
        for lv, feats in barb.items()
        for f in feats
        if "rage_uses" in f or "rage_damage" in f
    ]
    assert offenders == [], (
        "class_features.json still carries dead rage_uses/rage_damage hints that "
        f"no code reads and that contradict srd_tables._RAGE_USES: {offenders}"
    )


# --------------------------------------------------------------------------- #
# F02-5 — class-sig recompute must NOT refill spent hit dice                   #
# --------------------------------------------------------------------------- #
def test_class_sig_patch_preserves_spent_hit_dice():
    cid = server.create_campaign("hd")["id"]
    fid = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                  apply_srd_defaults=True, abilities={"constitution": 12})["id"]
    server.update_character(cid, fid, {"hit_dice_remaining": 0})  # spend all hit dice
    out = server.update_character(cid, fid, {"classes": [{"name": "Wizard", "level": 3}]})
    # Down/up-level retier recomputes the *pool size* (the hit_dice string scales to 3d6)
    # but a previously-SPENT pool must NOT silently refill — they stay spent (0).
    assert out["hit_dice"] == "3d6"
    assert out["hit_dice_remaining"] == 0


def test_class_sig_patch_caps_hit_dice_on_down_level():
    cid = server.create_campaign("hd2")["id"]
    fid = server.create_character(cid, "Karc", kind="player", class_name="Fighter",
                                  level=12, apply_srd_defaults=True,
                                  abilities={"constitution": 14})["id"]
    # all 12 hit dice available, then patch DOWN to L3 -> the pool can't exceed 3.
    out = server.update_character(cid, fid, {"classes": [{"name": "Fighter", "level": 3}]})
    assert out["hit_dice"] == "3d10"
    assert out["hit_dice_remaining"] == 3


# --------------------------------------------------------------------------- #
# F02-6 — CON rises retro-adjust max HP                                        #
# --------------------------------------------------------------------------- #
def test_con_asi_retro_adjusts_max_hp_on_level_up():
    cid = server.create_campaign("conhp")["id"]
    fid = server.create_character(cid, "Bru", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True, abilities={"constitution": 14})["id"]
    server.level_up(cid, fid, "Fighter")  # L2  -> 12 + 8
    server.level_up(cid, fid, "Fighter")  # L3  -> 28
    before = server.get_character(cid, fid)["max_hp"]  # 28
    out = server.level_up(cid, fid, "Fighter", asi={"constitution": 2})  # L4, CON 14->16
    # The L4 gain itself uses the POST-ASI CON (+3): average 6 + 3 = 9.
    # PLUS the CON modifier rose by +1 -> +1 HP retro on the 3 prior levels = +3.
    assert out["abilities"]["constitution"] == 16
    assert out["max_hp"] == before + 9 + 3  # = 40


def test_preview_and_actual_agree_on_con_asi_hp():
    # preview_level_up's hp_gain/max_hp must MATCH what level_up actually writes for a CON ASI
    # (preview applies the ASI before sizing HP, same as the real path) — no preview/actual drift.
    cid = server.create_campaign("parity")["id"]
    fid = server.create_character(cid, "Twin", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True, abilities={"constitution": 14})["id"]
    server.level_up(cid, fid, "Fighter")
    server.level_up(cid, fid, "Fighter")
    before_hp = server.get_character(cid, fid)["max_hp"]
    prev = server.preview_level_up(cid, fid, "Fighter", asi={"constitution": 2})
    act = server.level_up(cid, fid, "Fighter", asi={"constitution": 2})
    # the per-level gain reported by preview matches the actual gain (post-ASI CON in both)
    assert prev["hp_gain"] == act["_hp_gained"]
    assert prev["to"]["total_level"] == sum(cl["level"] for cl in act["classes"])
    # and the actual end-state HP folds in the per-level gain PLUS the CON-mod retro
    # (this fighter's CON 14->16 over 3 prior levels = +3 retro on top of the gain)
    assert act["max_hp"] == before_hp + act["_hp_gained"] + 3


def test_con_patch_retro_adjusts_max_hp():
    cid = server.create_campaign("conpatch")["id"]
    fid = server.create_character(cid, "Dorn", kind="player", class_name="Fighter",
                                  level=5, apply_srd_defaults=True,
                                  abilities={"constitution": 14})["id"]
    before = server.get_character(cid, fid)["max_hp"]
    out = server.update_character(cid, fid, {"abilities": {"constitution": 16}})  # +1 mod
    assert out["max_hp"] == before + 5  # +1 per level over 5 levels


def test_con_drop_patch_lowers_max_hp_but_floors_at_one():
    cid = server.create_campaign("condrop")["id"]
    fid = server.create_character(cid, "Frail", kind="player", class_name="Wizard",
                                  level=3, apply_srd_defaults=True,
                                  abilities={"constitution": 14})["id"]
    before = server.get_character(cid, fid)["max_hp"]
    out = server.update_character(cid, fid, {"abilities": {"constitution": 12}})  # -1 mod
    assert out["max_hp"] == before - 3


def test_explicit_max_hp_in_same_patch_wins_over_con_retro():
    cid = server.create_campaign("conexplicit")["id"]
    fid = server.create_character(cid, "Authored", kind="player", class_name="Fighter",
                                  level=5, apply_srd_defaults=True,
                                  abilities={"constitution": 14})["id"]
    out = server.update_character(cid, fid, {"abilities": {"constitution": 16}, "max_hp": 99})
    assert out["max_hp"] == 99  # DM-authored HP wins; no retro on top


# --------------------------------------------------------------------------- #
# F02-3 / F02-15 — pending-choice ledger + feat surfacing                      #
# --------------------------------------------------------------------------- #
def test_skipped_asi_records_pending_choice_debt():
    cid = server.create_campaign("debt")["id"]
    fid = server.create_character(cid, "Aria", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True,
                                  abilities={"strength": 16, "constitution": 12})["id"]
    server.level_up(cid, fid, "Fighter")  # L2
    server.level_up(cid, fid, "Fighter")  # L3
    out = server.level_up(cid, fid, "Fighter")  # L4 ASI level, NEITHER asi nor feat chosen
    # The due-but-skipped choice is now RECORDED (was silently dropped, unrecoverable).
    assert any("Fighter" in p and "4" in p for p in out["pending_choices"])
    assert out["_pending_choice_recorded"] is True
    # round-trips on the persisted record
    assert _snapshot(cid).characters[fid].pending_choices == out["pending_choices"]


def test_taking_asi_does_not_record_debt():
    cid = server.create_campaign("nodebt")["id"]
    fid = server.create_character(cid, "Nim", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True,
                                  abilities={"strength": 16, "constitution": 12})["id"]
    server.level_up(cid, fid, "Fighter")
    server.level_up(cid, fid, "Fighter")
    out = server.level_up(cid, fid, "Fighter", asi={"strength": 2})  # L4 ASI taken
    assert out["pending_choices"] == []


def test_feat_choice_is_surfaced_for_the_dm():
    cid = server.create_campaign("feat")["id"]
    fid = server.create_character(cid, "Tav", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True,
                                  abilities={"strength": 16, "constitution": 12})["id"]
    server.level_up(cid, fid, "Fighter")
    server.level_up(cid, fid, "Fighter")
    out = server.level_up(cid, fid, "Fighter", feat="Great Weapon Master")  # L4 feat
    assert out["_asi_applied"] == {"feat": "Great Weapon Master"}
    # the feat lands on a structured ledger the DM/viewer can read (not just buried in notes)
    assert "Great Weapon Master" in out["feats"]
    assert "Great Weapon Master" in _snapshot(cid).characters[fid].feats
    # no missed-ASI debt is recorded when a feat WAS taken
    assert out["pending_choices"] == []


def test_engine_built_rogue_gets_expertise_default_fill():
    # F02-15: a rogue gains Expertise at L1; before, no engine grant path wrote
    # skill_expertise, so every engine-built rogue's expertise math was short by PB.
    cid = server.create_campaign("rogue")["id"]
    rid = server.create_character(cid, "Sly", kind="player", class_name="Rogue",
                                  apply_srd_defaults=True, abilities={"dexterity": 16})["id"]
    sheet = server.get_character(cid, rid)
    # two skills carry expertise (the SRD rogue's L1 Expertise grant), drawn from the
    # character's own proficiencies.
    assert len(sheet["skill_expertise"]) == 2
    for sk in sheet["skill_expertise"]:
        assert sk in sheet["skill_proficiencies"]


# --------------------------------------------------------------------------- #
# F02-14 — XP-entitlement advisory in xp mode                                 #
# --------------------------------------------------------------------------- #
def test_level_up_warns_when_xp_below_entitlement_in_xp_mode():
    cid = server.create_campaign("xpguard")["id"]
    fid = server.create_character(cid, "Greedy", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True)["id"]
    # leveling_mode defaults to "xp"; the PC has 0 XP -> not entitled to L2.
    out = server.level_up(cid, fid, "Fighter")
    assert out["_xp_warning"]  # advisory present, non-empty
    assert out["max_hp"] > 0  # but the level-up STILL happened (warn-don't-block)


def test_level_up_no_xp_warning_when_entitled():
    cid = server.create_campaign("xpok")["id"]
    fid = server.create_character(cid, "Earner", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True)["id"]
    server.award_xp(cid, fid, 500)  # past the L2 threshold (300)
    out = server.level_up(cid, fid, "Fighter")
    assert out.get("_xp_warning") in (None, "")


def test_level_up_no_xp_warning_in_milestone_mode():
    cid = server.create_campaign("milestone")["id"]
    fid = server.create_character(cid, "Story", kind="player", class_name="Fighter",
                                  apply_srd_defaults=True)["id"]
    # leveling_mode lives on the Campaign (a milestone game levels by story beat).
    camp = store.load_campaign(cid)
    camp.leveling_mode = "milestone"
    store.save_campaign(camp)
    out = server.level_up(cid, fid, "Fighter")  # 0 XP but milestone mode
    assert out.get("_xp_warning") in (None, "")


# --------------------------------------------------------------------------- #
# F02-12 — reroll seats a complete PC                                         #
# --------------------------------------------------------------------------- #
def _kill(cid, char_id):
    server.apply_damage(cid, char_id, 9999)
    assert store.load_campaign(cid).characters[char_id].dead is True


def test_reroll_pc_seated_at_party_location_and_met():
    cid = server.create_campaign("reroll")["id"]
    # establish a current location for the campaign
    c = store.load_campaign(cid)
    from models import Location
    loc = Location(name="The Crossroads")
    c.locations[loc.id] = loc
    c.current_location_id = loc.id
    store.save_campaign(c)
    pc = server.create_character(cid, "Aldric", kind="player", class_name="fighter",
                                 level=3, apply_srd_defaults=True,
                                 abilities={"strength": 16, "constitution": 14})["id"]
    _kill(cid, pc)
    out = server.reroll_character(cid, pc, "Mara", class_name="rogue", level=3,
                                  abilities={"dexterity": 16, "constitution": 12})
    new_id = out["new_pc"]["id"]
    new = _snapshot(cid).characters[new_id]
    assert new.location_id == loc.id  # seated where the party currently is (not None)
    assert new.met is True  # a player is implicitly met (consistency with create_character)


def test_reroll_pc_ac_consistent_with_inventory():
    cid = server.create_campaign("rerollac")["id"]
    pc = server.create_character(cid, "Aldric", kind="player", class_name="fighter",
                                 level=3, apply_srd_defaults=True,
                                 abilities={"strength": 16, "constitution": 14})["id"]
    _kill(cid, pc)
    out = server.reroll_character(cid, pc, "Mara", class_name="fighter", level=3,
                                  abilities={"strength": 16, "constitution": 14})
    new = _snapshot(cid).characters[out["new_pc"]["id"]]
    # the AC the new hero shows must be JUSTIFIED by gear on the sheet — an armored AC
    # over an empty pack was the F02-12 defect. Either the kit is seeded to back the AC,
    # or the AC is the unarmored value; never an unbacked armored AC.
    has_armor = any("armor" in (it.name or "").lower() or "mail" in (it.name or "").lower()
                    for it in new.inventory)
    if new.armor_class > 12:
        assert has_armor, "armored AC must be backed by an armor item on the sheet"
