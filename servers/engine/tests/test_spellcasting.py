import pytest

import server
import spells


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


# --- effect resolution (pure) ---
def test_firebolt_cantrip_scaling():
    fb = spells.spell_data("Fire Bolt")
    assert spells.resolve_effect(fb, 0, 1, 3)["damage"] == "1d10"
    assert spells.resolve_effect(fb, 0, 5, 3)["damage"] == "2d10"
    assert spells.resolve_effect(fb, 0, 11, 3)["damage"] == "3d10"
    assert spells.resolve_effect(fb, 0, 17, 3)["damage"] == "4d10"


def test_magic_missile_darts_and_upcast():
    mm = spells.spell_data("Magic Missile")
    base = spells.resolve_effect(mm, 1, 5, 3)
    assert base["darts"] == 3 and base["damage"] == "3d4+3"
    up = spells.resolve_effect(mm, 3, 5, 3)
    assert up["darts"] == 5 and up["damage"] == "5d4+5"


def test_cure_wounds_upcast_and_mod():
    cw = spells.spell_data("Cure Wounds")
    assert spells.resolve_effect(cw, 1, 1, 3)["heal"] == "1d8+3"
    assert spells.resolve_effect(cw, 3, 1, 3)["heal"] == "3d8+3"
    assert spells.resolve_effect(cw, 1, 1, 0)["heal"] == "1d8"  # M3: no "+0"
    assert spells.resolve_effect(cw, 1, 1, -1)["heal"] == "1d8-1"


def test_burning_hands_save_and_upcast():
    bh = spells.spell_data("Burning Hands")
    e1 = spells.resolve_effect(bh, 1, 1, 3)
    assert e1["kind"] == "save" and e1["save_ability"] == "dex" and e1["damage"] == "3d6"
    assert spells.resolve_effect(bh, 2, 1, 3)["damage"] == "4d6"


# --- cast_spell tool ---
def test_cast_consumes_slot_and_dc():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True,
                                abilities={"intelligence": 16, "constitution": 12})["id"]
    out = server.cast_spell(cid, w, "Magic Missile")
    assert out["slot_used"] == 1 and out["slots_remaining"]["1"] == 1
    assert out["spell_save_dc"] == 13 and out["spell_attack_bonus"] == 5
    assert out["effect"]["damage"] == "3d4+3"
    server.cast_spell(cid, w, "Magic Missile")  # uses the second slot
    with pytest.raises(Exception):
        server.cast_spell(cid, w, "Magic Missile")  # no slots left


def test_cantrip_uses_no_slot():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    out = server.cast_spell(cid, w, "Fire Bolt")
    assert out["slot_used"] is None and out["effect"]["damage"] == "1d10"


def test_upcast_with_higher_slot():
    cid = server.create_campaign("S")["id"]
    # a level-3 wizard has a 2nd-level slot to upcast Magic Missile
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    server.level_up(cid, w, "Wizard")
    server.level_up(cid, w, "Wizard")  # level 3 -> 4/3/2 slots
    out = server.cast_spell(cid, w, "Magic Missile", slot_level=2)
    assert out["slot_used"] == 2 and out["effect"]["darts"] == 4  # 3 + 1 upcast


def test_concentration_set_on_cast():
    cid = server.create_campaign("S")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     apply_srd_defaults=True,
                                     abilities={"wisdom": 16, "constitution": 12})["id"]
    out = server.cast_spell(cid, cleric, "Bless")
    assert out["concentration"] == "Bless"
    assert server.get_character(cid, cleric)["concentration"] == "Bless"


def test_saving_throw():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"dexterity": 14})["id"]
    out = server.saving_throw(cid, w, "dex", 10)
    assert isinstance(out["success"], bool) and out["ability"] == "dex"


def test_learn_and_prepare():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard", apply_srd_defaults=True)["id"]
    server.learn_spells(cid, w, ["Fire Bolt", "Magic Missile"])
    server.prepare_spells(cid, w, ["Magic Missile"])
    sheet = server.get_character(cid, w)
    assert "Magic Missile" in sheet["spells_known"] and sheet["spells_prepared"] == ["Magic Missile"]


def test_unknown_spell_raises():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard", apply_srd_defaults=True)["id"]
    with pytest.raises(Exception):
        server.cast_spell(cid, w, "Wish")  # not bundled


# --- hardening regressions (from adversarial review) ---
def test_half_on_save_damage():  # C1
    cid = server.create_campaign("S")["id"]
    g = server.create_character(cid, "Goblin", kind="monster", max_hp=20)["id"]
    out = server.apply_damage(cid, g, 11, half=True)  # 11 // 2 = 5
    assert out["current_hp"] == 15


def test_non_caster_cannot_cast():  # H1
    cid = server.create_campaign("S")["id"]
    f = server.create_character(cid, "Grunt", kind="player", class_name="Fighter",
                                apply_srd_defaults=True, abilities={"strength": 16})["id"]
    with pytest.raises(Exception):
        server.cast_spell(cid, f, "Fire Bolt")
    with pytest.raises(Exception):
        server.spell_save_dc(cid, f)


def test_known_prepared_enforced():  # H2
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    server.learn_spells(cid, w, ["Magic Missile"])
    server.cast_spell(cid, w, "Magic Missile")  # known -> ok
    with pytest.raises(Exception):
        server.cast_spell(cid, w, "Cure Wounds")  # not known


def test_zero_or_downcast_slot_rejected():  # M1
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    with pytest.raises(Exception):
        server.cast_spell(cid, w, "Magic Missile", slot_level=0)


def test_concentration_replacement():  # M2
    cid = server.create_campaign("S")["id"]
    cleric = server.create_character(cid, "Pious", kind="player", class_name="Cleric",
                                     apply_srd_defaults=True, abilities={"wisdom": 16})["id"]
    server.cast_spell(cid, cleric, "Bless")
    assert server.get_character(cid, cleric)["concentration"] == "Bless"
    server.cast_spell(cid, cleric, "Shield of Faith")  # 2nd concentration spell drops Bless
    assert server.get_character(cid, cleric)["concentration"] == "Shield of Faith"


def test_caster_ships_with_a_castable_starter_loadout():
    """A freshly-built caster must get a real, castable spellbook — slots without spells
    leaves a wizard unable to cast (QA: a level-3 Wizard shipped with an empty spellbook and
    never cast once). A martial class gets none."""
    cid = server.create_campaign("S")["id"]
    wiz = server.create_character(cid, "Dal", kind="player", class_name="Wizard", level=3,
                                  apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    sheet = server.get_character(cid, wiz)
    known = set(sheet["spells_known"]) | set(sheet["spells_prepared"])
    assert known, "a level-3 wizard must know at least some spells"
    assert "Magic Missile" in known
    # The seeded spells must actually resolve through the cast path.
    server.cast_spell(cid, wiz, "Magic Missile")  # raises if not known / not castable
    # A martial class gets no spellbook.
    ftr = server.create_character(cid, "Brawn", kind="player", class_name="Fighter", level=3,
                                  apply_srd_defaults=True)["id"]
    fsheet = server.get_character(cid, ftr)
    assert not fsheet["spells_known"] and not fsheet["spells_prepared"]


# --- F03-2 (#808): srd damage-cantrip tier scaling --------------------------------
# The srd degrade path used to copy the LEVEL-1 damage_roll verbatim into
# `base_damage` while the note tells the DM to "resolve with the values above" —
# actively wrong at caster levels 5/11/17. The structured field must carry the
# tier-scaled dice; the original die survives additively in `base_damage_level1`.

# The 13 cited non-curated damage cantrips (the 14th, Fire Bolt, is curated and
# already scaled via resolve_effect). Ten tier-scale per their own higher_level
# prose; Eldritch Blast is beam-scaled (excluded BY NAME — pooled "3d10" would
# misstate 3 separate 1d10 attack rolls); Guidance/Resistance carry a 1d4 in the
# srd dump's damage_roll but genuinely never scale (empty higher_level).
_SCALING_CANTRIPS = {
    "Acid Splash": "d6",
    "Chill Touch": "d10",
    "Poison Spray": "d12",
    "Produce Flame": "d8",
    "Ray of Frost": "d8",
    "Sacred Flame": "d8",
    "Shocking Grasp": "d8",
    "Sorcerous Burst": "d8",
    "Starry Wisp": "d8",
    "Vicious Mockery": "d6",
}
_NON_SCALING_CANTRIPS = ["Eldritch Blast", "Guidance", "Resistance"]


@pytest.mark.parametrize("name,die", sorted(_SCALING_CANTRIPS.items()))
@pytest.mark.parametrize("lvl,tier", [(1, 1), (4, 1), (5, 2), (10, 2), (11, 3), (16, 3), (17, 4), (20, 4)])
def test_scaled_cantrip_damage_table(name, die, lvl, tier):
    rec = spells.srd_spell(name)
    assert spells.scaled_cantrip_damage(rec, lvl) == f"{tier}{die}"


@pytest.mark.parametrize("name", _NON_SCALING_CANTRIPS)
@pytest.mark.parametrize("lvl", [1, 5, 11, 17])
def test_non_scaling_cantrips_return_none(name, lvl):
    assert spells.scaled_cantrip_damage(spells.srd_spell(name), lvl) is None


def test_leveled_spells_never_tier_scale():
    assert spells.scaled_cantrip_damage(spells.srd_spell("Fireball"), 17) is None


def _wizard_at(cid, level, name="Gale"):
    w = server.create_character(cid, name, kind="player", class_name="Wizard", level=level,
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    server.learn_spells(cid, w, ["Acid Splash", "Eldritch Blast", "Fireball"])
    return w


@pytest.mark.parametrize("lvl,expected", [(1, "1d6"), (5, "2d6"), (11, "3d6"), (17, "4d6")])
def test_cast_srd_cantrip_reports_tier_scaled_base_damage(lvl, expected):
    cid = server.create_campaign("S")["id"]
    w = _wizard_at(cid, lvl, name=f"Gale{lvl}")
    out = server.cast_spell(cid, w, "Acid Splash")
    assert out["automated"] is False
    assert out["base_damage"] == expected
    assert out["base_damage_level1"] == "1d6"  # the original die is never lost


def test_eldritch_blast_base_damage_stays_per_beam():
    """Eldritch Blast scales in BEAMS (separate attack roll each) — a pooled '3d10'
    would be wrong. The structured field stays the per-beam die; the prose explains."""
    cid = server.create_campaign("S")["id"]
    w = _wizard_at(cid, 11)
    out = server.cast_spell(cid, w, "Eldritch Blast")
    assert out["base_damage"] == "1d10"
    assert "base_damage_level1" not in out
    assert "beam" in (out["upcast"] or "").lower()


def test_leveled_srd_spell_base_damage_untouched():
    cid = server.create_campaign("S")["id"]
    w = _wizard_at(cid, 11)
    out = server.cast_spell(cid, w, "Fireball")
    assert out["base_damage"] == "8d6"
    assert "base_damage_level1" not in out


# --- F03-3 (#813): ritual casting --------------------------------------------------
# cast_spell(as_ritual=True) on a ritual-tagged spell consumes NO slot (the ritual
# takes +10 minutes instead); concentration/duration semantics are unchanged.
# as_ritual on a non-ritual spell raises; as_ritual during active combat raises;
# a normal cast of a ritual spell surfaces `ritual_available: true`.

def _ritual_wizard(cid):
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    server.learn_spells(cid, w, ["Detect Magic", "Magic Missile"])
    return w


def test_ritual_cast_spends_no_slot_and_keeps_concentration():
    cid = server.create_campaign("S")["id"]
    w = _ritual_wizard(cid)
    before = server.get_character(cid, w)["spell_slots"]
    out = server.cast_spell(cid, w, "Detect Magic", as_ritual=True)
    assert out["slot_used"] is None
    assert out["ritual_cast"] is True
    assert out["concentration"] == "Detect Magic"  # concentration semantics unchanged
    after = server.get_character(cid, w)["spell_slots"]
    assert after == before  # not a single slot spent
    # the engine-tracked timed effect still registers (10 minutes)
    assert out["active_effect"]["name"] == "Detect Magic"


def test_normal_cast_of_ritual_spell_spends_slot_and_flags_ritual_available():
    cid = server.create_campaign("S")["id"]
    w = _ritual_wizard(cid)
    out = server.cast_spell(cid, w, "Detect Magic")
    assert out["slot_used"] == 1  # today's behavior: the slot is spent
    assert out["ritual_available"] is True  # ...but the DM learns the option exists
    assert "ritual_cast" not in out


def test_as_ritual_on_non_ritual_spell_rejected_before_any_spend():
    cid = server.create_campaign("S")["id"]
    w = _ritual_wizard(cid)
    with pytest.raises(ValueError, match="not a ritual"):
        server.cast_spell(cid, w, "Magic Missile", as_ritual=True)
    sheet = server.get_character(cid, w)
    assert sheet["spell_slots"]["1"]["used"] == 0  # rejected cleanly, nothing spent
    assert sheet["concentration"] is None


def test_as_ritual_refused_in_active_combat():
    cid = server.create_campaign("S")["id"]
    w = _ritual_wizard(cid)
    foe = server.create_character(cid, "Grunt", kind="monster", max_hp=10)["id"]
    server.start_combat(cid, [w, foe])
    with pytest.raises(ValueError, match="combat"):
        server.cast_spell(cid, w, "Detect Magic", as_ritual=True)
    sheet = server.get_character(cid, w)
    assert sheet["spell_slots"]["1"]["used"] == 0
    assert sheet["concentration"] is None


def test_non_ritual_normal_casts_unchanged_no_ritual_fields():
    """Default-path regression: a plain cast of a non-ritual spell carries neither
    ritual field (byte-identical for existing callers)."""
    cid = server.create_campaign("S")["id"]
    w = _ritual_wizard(cid)
    out = server.cast_spell(cid, w, "Magic Missile")
    assert "ritual_cast" not in out and "ritual_available" not in out
