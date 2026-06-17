"""cast_spell handles ALL SRD spells: curated ones auto-resolve, the rest degrade
gracefully (slot spent + structured values) instead of erroring (P2.4)."""

import pytest

import server
import spells


def test_srd_spell_lookup():
    fb = spells.srd_spell("Fireball")
    assert fb is not None and int(fb["level"]) == 3 and fb["damage_roll"] == "8d6"
    assert fb["saving_throw_ability"] == "dexterity"
    assert spells.srd_spell("definitely not a spell") is None


@pytest.fixture
def caster(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    wid = server.create_character(
        cid, "Wizard", kind="player", class_name="Wizard", level=5,
        apply_srd_defaults=True, abilities={"intelligence": 16},
    )["id"]
    # know both an un-modeled (Fireball) and a curated (Cure Wounds) spell
    server.update_character(cid, wid, {"spells_prepared": ["Fireball", "Cure Wounds"]})
    return cid, wid


def test_cast_unmodeled_spell_degrades_not_errors(caster):
    cid, wid = caster
    out = server.cast_spell(cid, wid, "Fireball", slot_level=3)
    assert out["automated"] is False
    assert out["base_damage"] == "8d6" and out["save_ability"] == "dexterity"
    assert out["slot_used"] == 3
    assert out["slots_remaining"]["3"] == 1  # a level-3 slot was spent
    assert out["spell_save_dc"] >= 8


def test_cast_curated_spell_still_auto_resolves(caster):
    cid, wid = caster
    out = server.cast_spell(cid, wid, "Cure Wounds", slot_level=1)
    assert out["automated"] is True and "effect" in out


def test_cast_truly_unknown_spell_raises(caster):
    cid, wid = caster
    with pytest.raises(Exception):
        server.cast_spell(cid, wid, "Xyzzy Bolt")
