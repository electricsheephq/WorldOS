"""Comprehensive DM-tool arg-name alias sweep (the follow-up to #550's test file).

Opus (the DM) reaches for the intuitive arg name for the situation, but the engine
spells the same concept differently across tools — ``target_id`` (attack / cast_spell /
apply_damage / apply_healing / set_temp_hp), ``character_id`` (get / update / set_hp /
conditions / attitude), ``npc_id`` (social_check / recruit_companion), ``combatant_id``
(zones), ``name`` (spawn_monster / load_canon_character), ``destination_id`` (travel_to).
A mismatch is rejected by the strict MCP schema with "Field required", which flips the
release_gate's ``no_rejected_tool_calls`` FATAL check RED on an otherwise-healthy run.

Each tool below now accepts BOTH the canonical name AND the intuitive alias(es); the
canonical stays primary (wins if more than one is given) and behavior is IDENTICAL to
using the canonical name. These are TARGETED tests (run with ``-k alias_sweep``), not the
full suite. The known leaneffort gap — ``social_check(target_id=…)`` coalescing to
``npc_id`` BEFORE the ephemeral ``target_name`` branch — is proven explicitly.
"""

import pytest

import server


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("AliasSweep")["id"]


def _pc(campaign, name="Hero", **kw):
    kw.setdefault("max_hp", 20)
    kw.setdefault("armor_class", 14)
    return server.create_character(campaign, name, kind="player", **kw)["id"]


def _npc(campaign, name="Brakka", **kw):
    return server.create_character(campaign, name, kind="npc", **kw)["id"]


# =====================================================================================
# social_check — the LEANEFFORT GAP: target_id / character_id / id -> npc_id, plus the
# skill aliases, and the CRITICAL ordering (id resolves before the ephemeral branch).
# =====================================================================================

def test_alias_sweep_social_check_target_id_equals_npc_id(campaign):
    pc = _pc(campaign, "Bard", abilities={"charisma": 16})
    npc = _npc(campaign, "Guard")
    canon = server.social_check(campaign, pc, npc_id=npc, skill="persuasion", dc=1)
    # fresh target so the attitude track starts clean for the alias call
    npc2 = _npc(campaign, "Guard2")
    via_target_id = server.social_check(campaign, pc, target_id=npc2, skill="persuasion", dc=1)
    assert via_target_id["success"] == canon["success"] is True
    assert via_target_id["kind"] == canon["kind"] == "influence"
    assert set(via_target_id) == set(canon)
    # the alias actually moved the RIGHT npc's tracked attitude (not a scene-extra no-op)
    assert server.get_character(campaign, npc2)["attitude_value"] == 15


def test_alias_sweep_social_check_id_and_character_id_aliases(campaign):
    pc = _pc(campaign, "Bard", abilities={"charisma": 16})
    for kw in ("character_id", "id"):
        npc = _npc(campaign, f"NPC_{kw}")
        out = server.social_check(campaign, pc, skill="persuasion", dc=1, **{kw: npc})
        assert out["success"] is True
        assert out["npc"] == f"NPC_{kw}"  # resolved the tracked NPC, not an extra


def test_alias_sweep_social_check_skill_aliases(campaign):
    pc = _pc(campaign, "Bard", abilities={"charisma": 16})
    npc = _npc(campaign, "Guard")
    canon = server.social_check(campaign, pc, npc_id=npc, skill="insight", dc=1)
    npc2 = _npc(campaign, "Guard2")
    via_check = server.social_check(campaign, pc, npc_id=npc2, check="insight", dc=1)
    assert via_check["skill"] == canon["skill"] == "insight"
    assert via_check["kind"] == canon["kind"] == "read"


def test_alias_sweep_social_check_canonical_npc_id_wins(campaign):
    pc = _pc(campaign, "Bard", abilities={"charisma": 16})
    real = _npc(campaign, "Real")
    decoy = _npc(campaign, "Decoy")
    out = server.social_check(campaign, pc, npc_id=real, target_id=decoy, skill="persuasion", dc=1)
    assert out["npc"] == "Real"  # canonical target won; the alias did not redirect
    assert server.get_character(campaign, decoy)["attitude_value"] == 0  # decoy untouched


def test_alias_sweep_social_check_id_alias_resolves_before_ephemeral_branch(campaign):
    """The load-bearing ordering: an alias-only id must NOT fall into the scene-extra
    (target_name) path. If coalescing happened after `if not npc_id`, this tracked NPC's
    attitude would never move (the bug this whole PR guards against)."""
    pc = _pc(campaign, "Bard", abilities={"charisma": 16})
    npc = _npc(campaign, "Tracked")
    out = server.social_check(campaign, pc, target_id=npc, skill="persuasion", dc=1)
    assert out.get("ephemeral") is not True  # took the tracked path, not the extra path
    assert server.get_character(campaign, npc)["attitude_value"] == 15  # real state change


def test_alias_sweep_social_check_ephemeral_still_works(campaign):
    """A genuine scene extra (no id, only target_name) is UNCHANGED by the alias work."""
    pc = _pc(campaign, "Bard", abilities={"charisma": 16})
    out = server.social_check(campaign, pc, skill="persuasion", dc=1, target_name="the fishmonger")
    assert out["ephemeral"] is True and out["npc"] == "the fishmonger"


def test_alias_sweep_social_check_missing_target_and_skill_raise(campaign):
    pc = _pc(campaign, "Bard")
    with pytest.raises(ValueError, match="skill"):
        server.social_check(campaign, pc, npc_id=_npc(campaign), dc=1)
    with pytest.raises(ValueError, match="target"):
        server.social_check(campaign, pc, skill="persuasion", dc=1)


# =====================================================================================
# attack — target_id <- npc_id/id ; attacker_id <- character_id
# =====================================================================================

def test_alias_sweep_attack_target_and_attacker_aliases(campaign):
    atk = _pc(campaign, "Fighter")
    tgt = _npc(campaign, "Orc", max_hp=10, armor_class=1)  # AC 1 => always hits
    out = server.attack(campaign, character_id=atk, npc_id=tgt, attack_bonus=5, damage_dice="1d6+3")
    assert out["hit"] is True
    assert server.get_character(campaign, tgt)["current_hp"] < 10  # real damage applied


def test_alias_sweep_attack_canonical_wins(campaign):
    atk = _pc(campaign, "Fighter")
    real_tgt = _npc(campaign, "RealTarget", max_hp=10, armor_class=1)
    decoy = _npc(campaign, "Decoy", max_hp=10, armor_class=1)
    out = server.attack(campaign, attacker_id=atk, target_id=real_tgt, npc_id=decoy,
                        attack_bonus=5, damage_dice="1d6+3")
    assert out["target"] == "RealTarget"  # canonical target won (attack returns the name)
    assert server.get_character(campaign, decoy)["current_hp"] == 10  # decoy untouched


def test_alias_sweep_attack_missing_ids_raise(campaign):
    atk = _pc(campaign, "Fighter")
    with pytest.raises(ValueError, match="attacker"):
        server.attack(campaign, target_id=_npc(campaign), attack_bonus=5, damage_dice="1d6")
    with pytest.raises(ValueError, match="target"):
        server.attack(campaign, attacker_id=atk, attack_bonus=5, damage_dice="1d6")


# =====================================================================================
# cast_spell — target_id <- npc_id/id ; spell_name <- spell
# =====================================================================================

def test_alias_sweep_cast_spell_spell_and_target_aliases(campaign):
    caster = _pc(campaign, "Wizard", class_name="wizard", abilities={"intelligence": 16})
    tgt = _npc(campaign, "Goblin", max_hp=10, armor_class=10)
    out = server.cast_spell(campaign, caster, spell="Fire Bolt", npc_id=tgt)
    # Fire Bolt is a known cantrip — same path as spell_name="Fire Bolt"
    canon = server.cast_spell(campaign, caster, spell_name="Fire Bolt", target_id=tgt)
    assert out["spell"] == canon["spell"]


def test_alias_sweep_cast_spell_canonical_spell_wins(campaign):
    caster = _pc(campaign, "Wizard", class_name="wizard", abilities={"intelligence": 16})
    out = server.cast_spell(campaign, caster, spell_name="Fire Bolt", spell="Mage Hand")
    assert out["spell"].lower().startswith("fire")


def test_alias_sweep_cast_spell_missing_spell_raises(campaign):
    caster = _pc(campaign, "Wizard")
    with pytest.raises(ValueError, match="spell"):
        server.cast_spell(campaign, caster)


# =====================================================================================
# target_id family: apply_damage / apply_healing / set_temp_hp <- character_id / id
# =====================================================================================

def test_alias_sweep_apply_damage_aliases(campaign):
    pc = _pc(campaign, "Hero")
    canon = server.apply_damage(campaign, target_id=pc, amount=2)
    via_char = server.apply_damage(campaign, character_id=pc, amount=3)
    via_id = server.apply_damage(campaign, id=pc, amount=1)
    assert set(canon) == set(via_char) == set(via_id)
    assert server.get_character(campaign, pc)["current_hp"] == 14  # 20 - 2 - 3 - 1


def test_alias_sweep_apply_healing_and_set_temp_hp_aliases(campaign):
    pc = _pc(campaign, "Hero")
    server.apply_damage(campaign, target_id=pc, amount=10)  # -> 10
    server.apply_healing(campaign, character_id=pc, amount=4)  # alias -> 14
    assert server.get_character(campaign, pc)["current_hp"] == 14
    server.set_temp_hp(campaign, id=pc, amount=7)  # alias
    assert server.get_character(campaign, pc)["temp_hp"] == 7


def test_alias_sweep_target_family_missing_id_raises(campaign):
    with pytest.raises(ValueError, match="target"):
        server.apply_damage(campaign, amount=5)
    with pytest.raises(ValueError, match="target"):
        server.apply_healing(campaign, amount=5)
    with pytest.raises(ValueError, match="target"):
        server.set_temp_hp(campaign, amount=5)


# =====================================================================================
# character_id family: get/update/set_hp/conditions/attitude <- target_id / id
# =====================================================================================

def test_alias_sweep_get_character_aliases(campaign):
    pc = _pc(campaign, "Hero")
    canon = server.get_character(campaign, character_id=pc)
    via_target = server.get_character(campaign, target_id=pc)
    via_id = server.get_character(campaign, id=pc)
    assert canon["id"] == via_target["id"] == via_id["id"] == pc
    assert set(canon) == set(via_target) == set(via_id)


def test_alias_sweep_update_character_aliases(campaign):
    pc = _pc(campaign, "Hero")
    out = server.update_character(campaign, target_id=pc, patch={"armor_class": 18})
    assert out["armor_class"] == 18
    assert server.get_character(campaign, pc)["armor_class"] == 18


def test_alias_sweep_set_hp_alias(campaign):
    pc = _pc(campaign, "Hero")
    server.set_hp(campaign, target_id=pc, current_hp=7)
    assert server.get_character(campaign, pc)["current_hp"] == 7


def test_alias_sweep_conditions_aliases(campaign):
    pc = _pc(campaign, "Hero")
    server.add_condition(campaign, target_id=pc, condition="prone")
    assert "prone" in server.get_character(campaign, pc)["conditions"]
    server.remove_condition(campaign, id=pc, condition="prone")
    assert "prone" not in server.get_character(campaign, pc)["conditions"]


def test_alias_sweep_attitude_aliases(campaign):
    npc = _npc(campaign, "Merchant")
    server.set_attitude(campaign, npc_id=npc, attitude="friendly", value=20)
    assert server.get_character(campaign, npc)["attitude_value"] == 20
    server.adjust_attitude(campaign, target_id=npc, delta=-5)
    assert server.get_character(campaign, npc)["attitude_value"] == 15


def test_alias_sweep_character_family_missing_id_raises(campaign):
    with pytest.raises(ValueError, match="character"):
        server.get_character(campaign)
    with pytest.raises(ValueError, match="character"):
        server.set_hp(campaign, current_hp=5)
    with pytest.raises(ValueError, match="character"):
        server.adjust_attitude(campaign, delta=1)


def test_alias_sweep_character_id_canonical_wins(campaign):
    real = _pc(campaign, "Real")
    decoy = _pc(campaign, "Decoy")
    server.set_hp(campaign, character_id=real, target_id=decoy, current_hp=3)
    assert server.get_character(campaign, real)["current_hp"] == 3
    assert server.get_character(campaign, decoy)["current_hp"] == 20  # decoy untouched


# =====================================================================================
# recruit_companion — npc_id <- character_id / companion_id / id
# =====================================================================================

def test_alias_sweep_recruit_companion_aliases(campaign):
    npc = _npc(campaign, "Minsc")
    out = server.recruit_companion(campaign, character_id=npc, class_name="ranger", level=1)
    assert out["kind"] == "companion"
    assert server.get_character(campaign, npc)["kind"] == "companion"


def test_alias_sweep_recruit_companion_missing_id_raises(campaign):
    with pytest.raises(ValueError, match="id"):
        server.recruit_companion(campaign, class_name="ranger")


# =====================================================================================
# name family: spawn_monster <- monster/monster_name/creature ; load_canon_character
# =====================================================================================

def test_alias_sweep_spawn_monster_aliases(campaign):
    canon = server.spawn_monster(campaign, name="Goblin")
    for kw in ("monster", "monster_name", "creature"):
        out = server.spawn_monster(campaign, **{kw: "Goblin"})
        assert "error" not in out
        assert out["name"] == canon["name"]


def test_alias_sweep_spawn_monster_canonical_wins(campaign):
    out = server.spawn_monster(campaign, name="Goblin", monster="Wolf")
    assert out["name"].lower().startswith("goblin")


def test_alias_sweep_spawn_monster_missing_name_raises(campaign):
    with pytest.raises(ValueError, match="name"):
        server.spawn_monster(campaign)


# =====================================================================================
# travel_to — destination_id <- destination / to / location_id
# =====================================================================================

def test_alias_sweep_travel_to_aliases(campaign):
    # build a tiny two-room map via the public API and travel via an alias
    hall = server.add_location(campaign, "Hall", make_current=True)["id"]
    cellar = server.add_location(campaign, "Cellar", connections=[hall])["id"]
    out = server.travel_to(campaign, to=cellar)  # alias for destination_id
    assert out["to"] == cellar


def test_alias_sweep_travel_to_missing_destination_raises(campaign):
    with pytest.raises(ValueError, match="destination"):
        server.travel_to(campaign)


# =====================================================================================
# combatant_id family: place_combatant / move_to_zone <- character_id / id
# =====================================================================================

def test_alias_sweep_place_combatant_alias(campaign):
    a = _pc(campaign, "A")
    b = _npc(campaign, "B")
    server.start_combat(campaign, [a, b])
    server.set_zones(campaign, [{"name": "front"}, {"name": "back"}])
    out = server.place_combatant(campaign, character_id=a, zone="front")  # alias for combatant_id
    # the placed combatant now carries the zone in the turn order
    placed = next(cb for cb in out["order"] if cb["character_id"] == a)
    assert placed["zone"] == "front"


def test_alias_sweep_place_combatant_missing_id_raises(campaign):
    a = _pc(campaign, "A")
    server.start_combat(campaign, [a])
    with pytest.raises(ValueError, match="combatant"):
        server.place_combatant(campaign, zone="front")


# =====================================================================================
# text family: forget <- text ; log_event <- message/content/note ; add_consequence
# =====================================================================================

def test_alias_sweep_forget_text_alias(campaign):
    npc = _npc(campaign, "Brakka")
    server.remember(campaign, npc, fact="owes a debt")
    out = server.forget(campaign, npc, text="owes a debt")  # alias mirrors remember
    assert "owes a debt" not in out["memory"]


def test_alias_sweep_forget_missing_fact_raises(campaign):
    npc = _npc(campaign, "Brakka")
    with pytest.raises(ValueError, match="fact"):
        server.forget(campaign, npc)


def test_alias_sweep_log_event_text_aliases(campaign):
    for kw in ("message", "content", "note"):
        out = server.log_event(campaign, kind="narration", **{kw: f"beat via {kw}"})
        assert out["logged"]["text"] == f"beat via {kw}"


def test_alias_sweep_log_event_canonical_wins(campaign):
    out = server.log_event(campaign, kind="narration", text="canon", message="alias")
    assert out["logged"]["text"] == "canon"


def test_alias_sweep_log_event_missing_text_raises(campaign):
    with pytest.raises(ValueError, match="text"):
        server.log_event(campaign, kind="narration")


def test_alias_sweep_add_consequence_text_aliases(campaign):
    out = server.add_consequence(campaign, in_days=3, message="the ritual completes")
    assert out["text"] == "the ritual completes"
    # `note` is a SEPARATE field, not folded into text
    out2 = server.add_consequence(campaign, in_days=2, content="siege arrives", note="banner sighted")
    assert out2["text"] == "siege arrives"


def test_alias_sweep_add_consequence_missing_text_raises(campaign):
    with pytest.raises(ValueError, match="text"):
        server.add_consequence(campaign, in_days=3)
