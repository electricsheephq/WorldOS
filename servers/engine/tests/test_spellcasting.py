import pytest

import server
import spells


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
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


# --- #754: the browsable preparable pool (the full class spell list) ----------
def test_class_spell_list_paladin_full_pool():
    # A prepared caster must be able to BROWSE the full class spell list, not just what's
    # currently prepared. The Paladin SRD list is half-caster (spell levels 1–5) — derived
    # straight from the srd524 `classes` field, so it is SRD-correct, not hand-maintained.
    pool = spells.class_spell_list("paladin")
    names = {s["name"] for s in pool}
    assert "Bless" in names and "Cure Wounds" in names and "Divine Smite" in names
    assert len(pool) >= 30, "the full Paladin list is dozens of spells, not just the prepared few"
    levels = {s["level"] for s in pool}
    assert max(levels) == 5 and 0 not in levels, "Paladin: spell levels 1–5 (half-caster, no cantrips)"
    # sorted (level, name) and each entry carries an integer level
    assert pool == sorted(pool, key=lambda s: (s["level"], s["name"]))


def test_class_spell_list_capped_by_max_level():
    # A L10 Paladin has slots up to level 3 — the browsable pool should cap there so it only
    # shows spells they can actually slot, while still being the WHOLE list at those levels.
    capped = spells.class_spell_list("paladin", max_level=3)
    assert capped, "a capped pool is non-empty"
    assert max(s["level"] for s in capped) == 3
    full = spells.class_spell_list("paladin")
    assert len(capped) < len(full), "capping to L3 drops the L4–L5 Paladin spells"
    # the cap keeps the FULL set at each allowed level (not just the prepared ones)
    l1_capped = {s["name"] for s in capped if s["level"] == 1}
    l1_full = {s["name"] for s in full if s["level"] == 1}
    assert l1_capped == l1_full


def test_class_spell_list_unknown_class_is_empty():
    assert spells.class_spell_list("fighter") == []
    assert spells.class_spell_list("") == []


def test_get_character_surfaces_preparable_pool_for_prepared_caster():
    # The character read-endpoint returns the full class spell list (the preparable pool for
    # class+highest-slot-level) ALONGSIDE the prepared set, so the viewer Spellbook can show
    # BOTH. Additive: prepared/known are unchanged.
    cid = server.create_campaign("S")["id"]
    p = server.create_character(cid, "Wyll", kind="player", class_name="Paladin",
                                level=10, apply_srd_defaults=True)["id"]
    sheet = server.get_character(cid, p)
    pool = sheet["preparable_spells"]
    names = {s["name"] for s in pool}
    # the full browsable pool is far larger than the few prepared, and is capped to the
    # caster's highest slot level (a L10 Paladin -> levels 1–3).
    assert len(pool) > len(sheet["spells_prepared"])
    assert "Divine Smite" in names and "Bless" in names
    assert max(s["level"] for s in pool) == 3
    # prepared/known are untouched (additive)
    assert sheet["spells_prepared"] == ["Bless", "Cure Wounds", "Shield of Faith"]


def test_get_character_non_caster_has_empty_preparable_pool():
    # A Fighter (no caster class) gets an empty preparable pool — never a fabricated list.
    cid = server.create_campaign("S")["id"]
    f = server.create_character(cid, "Brawn", kind="player", class_name="Fighter",
                                apply_srd_defaults=True)["id"]
    sheet = server.get_character(cid, f)
    assert sheet["preparable_spells"] == []


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
    # Prepare Fireball too (F03-8: a non-empty prepared list now gates leveled casts; the
    # seeded loadout doesn't include Fireball, so add it for the scaling tests that cast it).
    server.prepare_spells(cid, w, ["Fireball"], mode="add")
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


# --- F03-7: learn/prepare canonicalize + validate; case-insensitive cast gate ----
def _blank_wizard(cid, **abil):
    """A wizard with NO seeded loadout (apply_srd_defaults False) so the spellbook is exactly
    what the test learns — isolates the gate from the starter-loadout union."""
    ab = {"intelligence": 16, "constitution": 12}
    ab.update(abil)
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities=ab)["id"]
    server.learn_spells(cid, w, [])      # clear seeded known
    server.prepare_spells(cid, w, [])    # clear seeded prepared
    return w


def test_learn_lowercase_then_cast_canonical_passes():
    """F03-7 (THE BUG): learn_spells stored raw strings, so a lowercase learn made the
    canonical-cased cast gate reject every cast. Now names are canonicalized on write."""
    cid = server.create_campaign("S")["id"]
    w = _blank_wizard(cid)
    server.learn_spells(cid, w, ["magic missile"])  # lowercase
    sheet = server.get_character(cid, w)
    assert sheet["spells_known"] == ["Magic Missile"]  # stored proper-cased
    server.cast_spell(cid, w, "Magic Missile")  # previously raised "doesn't know" — now OK


def test_learn_unknown_spell_rejected_at_learn_time_listing_offenders():
    """F03-7: an unknown/typo spell is rejected when LEARNED (not silently stored to fail at
    cast), and the error names every offender so the DM can fix the input."""
    cid = server.create_campaign("S")["id"]
    w = _blank_wizard(cid)
    with pytest.raises(ValueError, match="Definitely Not A Spell"):
        server.learn_spells(cid, w, ["Magic Missile", "Definitely Not A Spell"])
    # Nothing was stored (rejection before any state change).
    assert server.get_character(cid, w)["spells_known"] == []


def test_learn_mode_add_appends_without_relisting():
    """F03-7: mode='add' appends new spells to the known list (de-duped) — teach one without
    re-listing the whole spellbook. Default mode stays 'replace'."""
    cid = server.create_campaign("S")["id"]
    w = _blank_wizard(cid)
    server.learn_spells(cid, w, ["Magic Missile"])
    out = server.learn_spells(cid, w, ["fire bolt", "Magic Missile"], mode="add")  # dup ignored
    assert out["spells_known"] == ["Magic Missile", "Fire Bolt"]


def test_prepare_unknown_spell_rejected():
    """F03-7: prepare_spells validates+canonicalizes identically."""
    cid = server.create_campaign("S")["id"]
    w = _blank_wizard(cid)
    with pytest.raises(ValueError, match="unknown spell"):
        server.prepare_spells(cid, w, ["Not Real"])


def test_legacy_lowercase_snapshot_still_casts():
    """F03-7 round-trip: an OLD snapshot whose spells_known carries raw-cased strings (written
    before canonicalization) must still cast — the gate compares case-insensitively on read."""
    cid = server.create_campaign("S")["id"]
    w = _blank_wizard(cid)
    # Simulate a legacy snapshot via the raw model patch (bypasses learn_spells canonicalization).
    server.update_character(cid, w, patch={"spells_known": ["magic missile"],
                                           "spells_prepared": ["magic missile"]})
    server.cast_spell(cid, w, "Magic Missile")  # casefolded compare accepts the legacy casing


# --- F03-8: prepared discipline — a non-empty prepared list gates leveled casts --
def test_unprepared_leveled_cast_rejected_when_prepared_nonempty():
    """F03-8 (THE NO-OP): with a non-empty prepared list, a leveled spell that is KNOWN but
    NOT PREPARED is now rejected — preparation has mechanical weight (was a union no-op)."""
    cid = server.create_campaign("S")["id"]
    w = _blank_wizard(cid)
    server.learn_spells(cid, w, ["Magic Missile", "Shield"])
    server.prepare_spells(cid, w, ["Shield"])  # Magic Missile known but NOT prepared
    with pytest.raises(ValueError, match="hasn't prepared"):
        server.cast_spell(cid, w, "Magic Missile")
    server.cast_spell(cid, w, "Shield")  # prepared -> allowed


def test_empty_prepared_keeps_lenient_known_gate():
    """F03-8 legacy guard: an EMPTY prepared list keeps the lenient known-only gate (today's
    behavior) — a known-spell cast still works without preparing it (sorcerer / old snapshot)."""
    cid = server.create_campaign("S")["id"]
    w = _blank_wizard(cid)
    server.learn_spells(cid, w, ["Magic Missile"])  # known, prepared stays empty
    server.cast_spell(cid, w, "Magic Missile")  # lenient: empty prepared -> known gate only


def test_cantrip_castable_when_known_even_if_not_prepared():
    """F03-8: cantrips are never 'prepared' in 5e — a known cantrip is castable even with a
    non-empty (leveled) prepared list that doesn't list it."""
    cid = server.create_campaign("S")["id"]
    w = _blank_wizard(cid)
    server.learn_spells(cid, w, ["Fire Bolt", "Magic Missile"])
    server.prepare_spells(cid, w, ["Magic Missile"])  # leveled prep; cantrip not listed
    server.cast_spell(cid, w, "Fire Bolt")  # cantrip -> allowed via known, not prepared


def test_both_lists_empty_stays_fully_lenient():
    """F03-8 / F03-7 additive: with BOTH lists empty the gate is skipped entirely (today's
    behavior for a monster/NPC or an un-loadout-ed caster) — no rejection."""
    cid = server.create_campaign("S")["id"]
    w = _blank_wizard(cid)
    server.cast_spell(cid, w, "Magic Missile")  # no known/prepared -> lenient pass


# --- F03-11: innate casting — monsters/NPCs route a leveled spell through cast_spell ---
def test_monster_leveled_cast_without_slot_is_rejected_with_innate_hint():
    """F03-11 (THE GAP): no spawn path seeds spell_slots, so a monster casting a leveled spell
    fails for lack of a slot — but the error now POINTS at innate=True instead of dead-ending."""
    cid = server.create_campaign("S")["id"]
    foe = server.create_character(cid, "Drow Mage", kind="monster", max_hp=40,
                                  abilities={"charisma": 16})["id"]
    with pytest.raises(ValueError, match="innate=True"):
        server.cast_spell(cid, foe, "Hold Person")  # no slots seeded


def test_innate_leveled_cast_skips_slot_and_sets_concentration():
    """F03-11: innate=True casts a leveled spell with NO slot spent (slot_used='innate') while
    keeping concentration/duration — an enemy caster's spell state is now engine-tracked."""
    cid = server.create_campaign("S")["id"]
    foe = server.create_character(cid, "Drow Mage", kind="monster", max_hp=40,
                                  abilities={"charisma": 16})["id"]
    out = server.cast_spell(cid, foe, "Hold Person", innate=True)
    assert out["slot_used"] == "innate"
    assert out["concentration"] == "Hold Person"
    assert server.get_character(cid, foe)["concentration"] == "Hold Person"


def test_innate_monster_hold_person_concentration_and_release_compose_with_f0306():
    """F03-11 + F03-6: a spawned monster casts Hold Person innate=True on a PC, the PC is
    paralyzed with a self-enforcing marker linked to the MONSTER's concentration — and
    breaking the monster's concentration (drop_concentration) frees the PC immediately."""
    cid = server.create_campaign("S")["id"]
    monster = server.create_character(cid, "Drow Mage", kind="monster", max_hp=40,
                                      abilities={"charisma": 16})["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=30,
                                 abilities={"wisdom": 8})["id"]
    out = server.cast_spell(cid, monster, "Hold Person", target_id=pc, innate=True)
    assert out["slot_used"] == "innate"
    rider = out["condition_rider"]  # the save-ends rider is still surfaced
    server.add_condition(cid, pc, "paralyzed", **{
        k: rider[k] for k in ("repeat_save_ability", "repeat_save_dc", "source_id", "spell_name")
    })
    assert "paralyzed" in server.get_character(cid, pc)["conditions"]
    # Breaking the monster's concentration frees the PC (composes with F03-6's release).
    freed = server.drop_concentration(cid, monster)
    assert {"character_id": pc, "name": "Hold Person"} in freed["freed_targets"]
    assert "paralyzed" not in server.get_character(cid, pc)["conditions"]


def test_innate_downcast_still_rejected():
    """F03-11: innate skips the SLOT check but still honors the downcast guard — you can't
    claim a level-2 spell cast at level 1."""
    cid = server.create_campaign("S")["id"]
    foe = server.create_character(cid, "Drow Mage", kind="monster", max_hp=40,
                                  abilities={"charisma": 16})["id"]
    with pytest.raises(ValueError, match="level-1 slot"):
        server.cast_spell(cid, foe, "Hold Person", slot_level=1, innate=True)


def test_innate_false_default_is_byte_identical_for_pc():
    """ADDITIVE: innate defaults False — a PC's normal slot-spending cast is unchanged."""
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    out = server.cast_spell(cid, w, "Magic Missile")  # default innate=False -> spends a slot
    assert out["slot_used"] == 1
    assert out["slots_remaining"]["1"] == 1  # a slot was consumed


def test_innate_cantrip_unaffected_no_slot_either_way():
    """F03-11 regression: a cantrip needs no slot regardless of innate — the flag is inert for
    a level-0 spell (a monster could already cast cantrips; this stays true)."""
    cid = server.create_campaign("S")["id"]
    foe = server.create_character(cid, "Drow Mage", kind="monster", max_hp=40,
                                  abilities={"charisma": 16})["id"]
    out = server.cast_spell(cid, foe, "Fire Bolt", innate=True)
    assert out["slot_used"] is None  # cantrip path untouched by innate


# --- F03-4: AoE / multi-target cast path (validate-before-spend) ------------------
from dice import DiceRoll  # noqa: E402


def _fixed_roll(d20_natural: int, dmg_total: int):
    """A dice stub: every 1d20 save rolls the given natural (+ the expression's flat mod);
    every other expression (the shared AoE damage) returns dmg_total. Deterministic per-target
    save outcomes + a single known damage figure."""
    def _roll(expression, advantage=False, disadvantage=False, seed=None):
        if expression.startswith("1d20"):
            mod = 0
            if "+" in expression:
                mod = int(expression.split("+", 1)[1])
            return DiceRoll(expression=expression, total=d20_natural + mod, rolls=[d20_natural],
                            modifier=mod, detail="", is_d20=True, natural=d20_natural,
                            crit=(d20_natural == 20), fumble=(d20_natural == 1))
        return DiceRoll(expression=expression, total=dmg_total, rolls=[dmg_total], detail="")
    return _roll


def _aoe_wizard(cid, level=3):
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard", level=level,
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    server.learn_spells(cid, w, ["Burning Hands"])
    server.prepare_spells(cid, w, ["Burning Hands"], mode="add")
    return w


def test_aoe_burning_hands_one_shared_roll_per_target_halving(monkeypatch):
    """F03-4: Burning Hands at three targets — ONE shared damage roll, a per-target DEX save,
    full damage on a fail and half on a save. Slot spent once."""
    cid = server.create_campaign("S")["id"]
    w = _aoe_wizard(cid)
    # Three monsters: one with low DEX (fails) and one with high DEX (we'll force the roll to
    # decide outcome) — outcome is driven by the forced d20 natural below.
    a = server.create_character(cid, "Orc A", kind="monster", max_hp=30, abilities={"dexterity": 10})["id"]
    b = server.create_character(cid, "Orc B", kind="monster", max_hp=30, abilities={"dexterity": 10})["id"]
    c2 = server.create_character(cid, "Orc C", kind="monster", max_hp=30, abilities={"dexterity": 10})["id"]
    # Force a low save (nat 2 -> total 2, under any DC) so all three FAIL -> full damage.
    monkeypatch.setattr(server.dice_mod, "roll", _fixed_roll(2, 12))
    out = server.cast_spell(cid, w, "Burning Hands", target_ids=[a, b, c2])
    assert out["slot_used"] == 1  # one slot, even for three targets
    aoe = out["aoe"]
    assert aoe["shared_damage"]["total"] == 12  # ONE roll shared across the area
    assert aoe["save_ability"] == "dex" and aoe["save_dc"] == out["spell_save_dc"]
    assert len(aoe["targets"]) == 3
    for row in aoe["targets"]:
        assert row["saved"] is False and row["damage_taken"] == 12 and row["halved"] is False
    # Every target actually lost 12 HP through the shared apply_damage pipeline.
    for tid in (a, b, c2):
        assert server.get_character(cid, tid)["current_hp"] == 18


def test_aoe_successful_save_halves(monkeypatch):
    """F03-4: a target who SAVES takes half the shared damage (5e area save-for-half)."""
    cid = server.create_campaign("S")["id"]
    w = _aoe_wizard(cid)
    tgt = server.create_character(cid, "Nimble", kind="monster", max_hp=30,
                                  abilities={"dexterity": 20})["id"]
    monkeypatch.setattr(server.dice_mod, "roll", _fixed_roll(20, 12))  # nat 20 -> save succeeds
    out = server.cast_spell(cid, w, "Burning Hands", target_ids=[tgt])
    row = out["aoe"]["targets"][0]
    assert row["saved"] is True and row["halved"] is True and row["damage_taken"] == 6  # 12 // 2
    assert server.get_character(cid, tgt)["current_hp"] == 24


def test_aoe_paralyzed_target_auto_fails_dex_save(monkeypatch):
    """F03-4: a paralyzed target AUTO-FAILS its DEX save (combat.save_modifiers) — full damage
    even though the forced roll would otherwise succeed."""
    cid = server.create_campaign("S")["id"]
    w = _aoe_wizard(cid)
    tgt = server.create_character(cid, "Held", kind="monster", max_hp=30,
                                  abilities={"dexterity": 20})["id"]
    server.add_condition(cid, tgt, "paralyzed")
    monkeypatch.setattr(server.dice_mod, "roll", _fixed_roll(20, 12))  # nat 20 would save...
    out = server.cast_spell(cid, w, "Burning Hands", target_ids=[tgt])
    row = out["aoe"]["targets"][0]
    assert row["saved"] is False and row.get("auto_fail") is True  # ...but paralysis auto-fails DEX
    assert row["damage_taken"] == 12
    assert server.get_character(cid, tgt)["current_hp"] == 18


def test_aoe_unknown_id_rejected_before_slot_spend(monkeypatch):
    """F03-4 (THE INVARIANT): an unknown id ANYWHERE in target_ids rejects the WHOLE cast
    BEFORE the slot is spent — rejection-before-state-change. The slot is untouched."""
    cid = server.create_campaign("S")["id"]
    w = _aoe_wizard(cid)
    real = server.create_character(cid, "Real", kind="monster", max_hp=30)["id"]
    before = server.get_character(cid, w)["spell_slots"]["1"]["used"]
    with pytest.raises(ValueError, match="unknown target id"):
        server.cast_spell(cid, w, "Burning Hands", target_ids=[real, "ghost_id"])
    after = server.get_character(cid, w)["spell_slots"]["1"]["used"]
    assert after == before  # slot UNSPENT — clean rejection
    assert server.get_character(cid, w)["concentration"] is None
    assert server.get_character(cid, real)["current_hp"] == 30  # no damage applied


def test_aoe_upcast_scales_shared_damage(monkeypatch):
    """F03-4: an upcast AoE rolls the upcast-scaled dice once. Burning Hands at slot 2 is 4d6
    (3d6 + 1d6 upcast) — the shared roll uses that expr."""
    cid = server.create_campaign("S")["id"]
    w = _aoe_wizard(cid, level=3)  # has a 2nd-level slot
    tgt = server.create_character(cid, "Orc", kind="monster", max_hp=40, abilities={"dexterity": 10})["id"]
    monkeypatch.setattr(server.dice_mod, "roll", _fixed_roll(2, 14))
    out = server.cast_spell(cid, w, "Burning Hands", slot_level=2, target_ids=[tgt])
    assert out["slot_used"] == 2
    assert out["aoe"]["shared_damage"]["expr"] == "4d6"  # 3d6 base + 1d6 upcast, rolled once
    assert out["shape"]["type"] == "cone" and out["shape"]["size"] == 15  # F03-4 shape surfacing


def test_single_target_cast_unchanged_no_aoe_key():
    """ADDITIVE: a normal single-target / no-target cast carries NO `aoe` key — byte-identical
    for every existing caller (empty/omitted target_ids = today's behavior)."""
    cid = server.create_campaign("S")["id"]
    w = _aoe_wizard(cid)
    out = server.cast_spell(cid, w, "Burning Hands")  # no target_ids
    assert "aoe" not in out


# --- F14-7 (#812): cast_spell refusals name WHY + what the caster CAN cast -----------
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F14-7). A refusal that just says
# "doesn't know X" or "no level-N slot" wastes a ~100s DM beat (the DM freehands =
# hallucination). The refusal must name the cause AND list the castable affordance
# (known/prepared spells, the slot table) so the next call recovers. ADDITIVE: the
# ValueError TYPE and the leading clause are preserved (consumers still match on them).

def _prepared_wizard(cid, level=3, name="Rolan"):
    w = server.create_character(cid, name, kind="player", class_name="Wizard", level=level,
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    # A prepared caster with a small, known set so the refusal has something to list.
    server.learn_spells(cid, w, ["Magic Missile", "Shield", "Mage Armor"])
    server.prepare_spells(cid, w, ["Magic Missile", "Shield"])
    return w


def test_unprepared_leveled_refusal_lists_prepared_spells():
    """A prepared caster casting a known-but-unprepared spell sees WHAT IS prepared."""
    cid = server.create_campaign("S")["id"]
    w = _prepared_wizard(cid)
    with pytest.raises(ValueError) as ei:
        server.cast_spell(cid, w, "Mage Armor")  # known, not prepared
    msg = str(ei.value)
    assert "hasn't prepared" in msg  # cause preserved
    assert "Magic Missile" in msg and "Shield" in msg  # the prepared affordance is named


def test_unknown_to_known_caster_refusal_lists_known_spells():
    """A known-caster (no prepared list) refusal names the known spells the caster CAN cast."""
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Sorc", kind="player", class_name="Sorcerer", level=3,
                                apply_srd_defaults=True, abilities={"charisma": 16})["id"]
    server.learn_spells(cid, w, ["Magic Missile", "Burning Hands"])
    # Clear the prepared list so the known-only gate (the F14-7 line 6564 path) applies — a
    # sorcerer/legacy snapshot with spells_known but no prepared list.
    server.update_character(cid, w, patch={"spells_prepared": []})
    with pytest.raises(ValueError) as ei:
        server.cast_spell(cid, w, "Fireball")  # not known
    msg = str(ei.value)
    assert "doesn't know or have" in msg  # cause/key preserved
    assert "Magic Missile" in msg  # the known affordance is named


def test_cantrip_refusal_lists_known_cantrips():
    """A cantrip not in the known set names the cantrips the caster DOES know."""
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Mage", kind="player", class_name="Wizard", level=1,
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    server.learn_spells(cid, w, ["Fire Bolt", "Magic Missile"])
    with pytest.raises(ValueError) as ei:
        server.cast_spell(cid, w, "Ray of Frost")  # cantrip not known
    msg = str(ei.value)
    assert "doesn't know" in msg
    assert "Fire Bolt" in msg  # the known affordance is named


def test_slot_exhausted_refusal_shows_slot_table():
    """A PC out of a given slot level sees the remaining slot table (what they CAN cast)."""
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard", level=1,
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    server.learn_spells(cid, w, ["Magic Missile"])
    server.prepare_spells(cid, w, ["Magic Missile"])
    server.cast_spell(cid, w, "Magic Missile")  # burns slot 1 of 2
    server.cast_spell(cid, w, "Magic Missile")  # burns slot 2 of 2
    with pytest.raises(ValueError) as ei:
        server.cast_spell(cid, w, "Magic Missile")  # no level-1 slots left
    msg = str(ei.value)
    assert "no level-1 spell slot" in msg  # cause/key preserved
    assert "slots" in msg.lower()  # the slot table is surfaced


def test_unknown_spell_refusal_suggests_close_match():
    """An unknown (misspelled) spell name surfaces a did-you-mean fuzzy suggestion."""
    cid = server.create_campaign("S")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard", level=3,
                                apply_srd_defaults=True, abilities={"intelligence": 16})["id"]
    with pytest.raises(ValueError) as ei:
        server.cast_spell(cid, w, "Magick Missle")  # typo for Magic Missile
    msg = str(ei.value)
    assert "unknown spell" in msg  # key preserved
    assert "Magic Missile" in msg  # did-you-mean
