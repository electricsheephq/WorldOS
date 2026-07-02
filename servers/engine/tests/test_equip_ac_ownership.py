"""#806 stage 2 — equipment-owned AC with a provenance flag (armor_ac_source).

The equip path used to be advisory only (stage 1, #831): it computed a suggested_ac
but never wrote armor_class. Stage 2 lets EQUIPMENT own the worn-armor base scalar —
but only when the DM hasn't manually overridden it, and never for a legacy "" base.
Ownership is tracked by Character.armor_ac_source ("" | "equipment" | "manual") and
handed back to equipment via update_character(rederive_ac=true).

Invariants under test:
  * equip WRITES armor_class only when armor_ac_source == "equipment" (applied=True);
  * a manual update_character(armor_class=...) override stamps "manual" and BLOCKS the
    equip write (stays advisory, applied=False);
  * a legacy/unknown "" base is manual-safe — equip never clobbers it;
  * rederive_ac=true clears the manual flag and recomputes from equipped items;
  * Mage Armor still layers on at read time regardless of who owns the base.
"""

import pytest

import itemcatalog
import server
from models import Character


@pytest.fixture
def hero(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("ACOwnership")["id"]
    h = server.create_character(cid, "Warden", kind="player")["id"]
    return cid, h


def _ch(cid, hid):
    return server._require(cid).characters[hid]


# ---------------------------------------------------------------------------
# Provenance flag defaults + additive round-trip
# ---------------------------------------------------------------------------

def test_new_field_defaults_empty_and_round_trips():
    # Additive: a bare model has "" and an old snapshot without the key round-trips.
    assert Character(name="X").armor_ac_source == ""
    revived = Character.model_validate({"name": "Old", "armor_class": 15})
    assert revived.armor_ac_source == ""
    assert revived.armor_class == 15  # legacy AC preserved exactly


# ---------------------------------------------------------------------------
# Legacy "" base is manual-safe — equip stays advisory, never clobbers
# ---------------------------------------------------------------------------

def test_legacy_empty_source_equip_is_advisory_and_never_clobbers(hero):
    cid, h = hero
    assert _ch(cid, h).armor_ac_source == ""  # fresh character is legacy-safe
    server.add_item(cid, h, item_name="Chain Mail")
    out = server.equip_item(cid, h, "Chain Mail")
    assert out["mechanics"]["applied"] is False
    assert out["mechanics"]["suggested_ac"] == 16  # heavy, flat
    # The legacy base (10) is untouched and the source stays "".
    assert _ch(cid, h).armor_class == 10
    assert _ch(cid, h).armor_ac_source == ""


# ---------------------------------------------------------------------------
# update_character(armor_class=...) is a manual override -> stamps "manual"
# ---------------------------------------------------------------------------

def test_manual_ac_write_stamps_manual_source(hero):
    cid, h = hero
    server.update_character(cid, h, patch={"armor_class": 17})
    assert _ch(cid, h).armor_class == 17
    assert _ch(cid, h).armor_ac_source == "manual"


def test_manual_override_blocks_equip_write(hero):
    cid, h = hero
    server.update_character(cid, h, patch={"armor_class": 20})  # DM houserule
    server.add_item(cid, h, item_name="Leather Armor")
    out = server.equip_item(cid, h, "Leather Armor")
    # Advisory only: the DM's 20 wins, equip does NOT clobber it.
    assert out["mechanics"]["applied"] is False
    assert _ch(cid, h).armor_class == 20
    assert _ch(cid, h).armor_ac_source == "manual"
    # The advisory note points the DM at the re-derive affordance.
    assert "rederive_ac" in out["mechanics"]["note"]


# ---------------------------------------------------------------------------
# Re-derive hands ownership back to equipment and recomputes from equipped items
# ---------------------------------------------------------------------------

def test_rederive_clears_manual_and_recomputes_from_equipped(hero):
    cid, h = hero
    server.update_character(cid, h, patch={"abilities": {"dexterity": 10}})
    server.add_item(cid, h, item_name="Chain Mail")   # heavy, base 16
    server.equip_item(cid, h, "Chain Mail")            # advisory (source "")
    server.update_character(cid, h, patch={"armor_class": 99})  # manual override
    assert _ch(cid, h).armor_ac_source == "manual"

    out = server.update_character(cid, h, patch={"rederive_ac": True})
    assert out["armor_class"] == 16                    # recomputed from Chain Mail
    assert _ch(cid, h).armor_ac_source == "equipment"


def test_rederive_with_no_equipped_armor_is_unarmored_baseline(hero):
    cid, h = hero
    server.update_character(cid, h, patch={"abilities": {"dexterity": 14}})  # +2
    out = server.update_character(cid, h, patch={"rederive_ac": True})
    assert out["armor_class"] == 12                    # 10 + DEX(+2)
    assert _ch(cid, h).armor_ac_source == "equipment"


# ---------------------------------------------------------------------------
# Once equipment owns AC, equip WRITES it (applied=True)
# ---------------------------------------------------------------------------

def test_equip_writes_when_equipment_owns_base(hero):
    cid, h = hero
    server.update_character(cid, h, patch={"abilities": {"dexterity": 14}})  # +2
    server.update_character(cid, h, patch={"rederive_ac": True})  # -> "equipment"
    assert _ch(cid, h).armor_ac_source == "equipment"

    server.add_item(cid, h, item_name="Studded Leather Armor")  # light, base 12
    out = server.equip_item(cid, h, "Studded Leather Armor")
    assert out["mechanics"]["applied"] is True
    assert _ch(cid, h).armor_class == 14               # 12 + DEX(+2)
    assert _ch(cid, h).armor_ac_source == "equipment"


def test_shield_add_and_remove_round_trip_when_equipment_owned(hero):
    cid, h = hero
    server.update_character(cid, h, patch={"abilities": {"dexterity": 10}})
    server.update_character(cid, h, patch={"rederive_ac": True})  # base 10, equipment
    assert _ch(cid, h).armor_class == 10

    server.add_item(cid, h, item_name="Shield")
    on = server.equip_item(cid, h, "Shield")
    assert on["mechanics"]["applied"] is True
    assert _ch(cid, h).armor_class == 12               # +2 shield

    off = server.equip_item(cid, h, "Shield", equipped=False)
    assert off["mechanics"]["applied"] is True
    assert _ch(cid, h).armor_class == 10               # back to base


# ---------------------------------------------------------------------------
# Mage Armor still layers at read time regardless of who owns the base
# ---------------------------------------------------------------------------

def test_mage_armor_layers_over_equipment_owned_base(hero):
    cid, h = hero
    server.update_character(cid, h, patch={"abilities": {"dexterity": 14}})  # +2
    server.update_character(cid, h, patch={"rederive_ac": True})  # base 12 (10+DEX)
    ch = _ch(cid, h)
    # Mage Armor: 13 + DEX = 15 > base 12, so it wins at read time WITHOUT touching
    # the stored base scalar (the resolver is untouched by stage 2).
    from models import ActiveEffect
    ch.active_effects.append(ActiveEffect(name="Mage Armor", armor_formula_ac=13 + 2))
    eff_ac, detail = server._effective_armor_class(ch)
    assert eff_ac == 15
    assert detail is not None and detail["source"] == "Mage Armor" and detail["applied"] is True
    # The equipment-owned base scalar is unchanged by the read-time layer.
    assert ch.armor_class == 12
    assert ch.armor_ac_source == "equipment"


def test_manual_ac_not_touched_by_unrelated_patch(hero):
    # A patch that never mentions armor_class must leave the source flag alone
    # (byte-identical to today for every non-AC patch).
    cid, h = hero
    server.update_character(cid, h, patch={"armor_class": 18})  # -> manual
    server.update_character(cid, h, patch={"max_hp": 30})       # unrelated
    assert _ch(cid, h).armor_ac_source == "manual"
    assert _ch(cid, h).armor_class == 18
