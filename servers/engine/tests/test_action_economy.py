"""Action economy tracker — action/bonus/reaction budget (P2.2) + cross-tool
per-TURN economy (#778): the action is a single per-turn resource shared across
attack() / cast_spell() / use_action(), keyed by casting time, so a caster-martial
turn can't double-act (cast+attack / attack+cast / double-cast) and a bonus-action
spell (Healing Word) burns the BONUS action, not the action."""

import pytest

import server


@pytest.fixture
def combat(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    ids = [
        server.create_character(cid, n, kind=k, max_hp=10, armor_class=12)["id"]
        for n, k in (("A", "player"), ("B", "player"), ("M", "monster"))
    ]
    server.start_combat(cid, ids)
    return cid, ids, server.get_state(cid)["current_turn"]


# --- caster-martial fixture (#778): a Wizard who is always first in initiative, ---
# knows Healing Word (bonus), Fireball (action), Magic Missile (action), and has --
# both L1 and L3 slots, plus a monster to round out the fight. --------------------
@pytest.fixture
def caster(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("AE778")["id"]
    wiz = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard", apply_srd_defaults=True
    )["id"]
    # Deterministic turn order: the Wizard always wins initiative and is the current combatant.
    server.update_character(cid, wiz, patch={"initiative_bonus": 100})
    server.learn_spells(cid, wiz, ["Healing Word", "Fireball", "Magic Missile", "Fire Bolt"])
    server.prepare_spells(cid, wiz, ["Healing Word", "Fireball", "Magic Missile"])
    server.update_character(
        cid, wiz, patch={"spell_slots": {"1": {"maximum": 4, "used": 0},
                                         "3": {"maximum": 2, "used": 0}}}
    )
    goblin = server.create_character(cid, "Goblin", kind="monster", max_hp=20, armor_class=10)["id"]
    server.start_combat(cid, [wiz, goblin])
    assert server.get_state(cid)["current_turn"] == wiz, "fixture: Wizard must be current"
    return cid, wiz, goblin


def _cast(cid, caster_id, spell, **kw):
    """cast_spell shorthand — we only exercise the ECONOMY, not spell resolution."""
    return server.cast_spell(cid, caster_id, spell, **kw)


# ============================ pre-#778 baseline ==============================

def test_second_action_same_turn_is_flagged(combat):
    cid, _ids, cur = combat
    first = server.use_action(cid, cur, "action")
    assert first["ok"] is True and first["action_available"] is False
    second = server.use_action(cid, cur, "action")
    assert second["ok"] is False and "already used" in second["reason"]


def test_action_and_bonus_are_independent(combat):
    cid, _ids, cur = combat
    assert server.use_action(cid, cur, "action")["ok"] is True
    assert server.use_action(cid, cur, "bonus")["ok"] is True  # bonus still available


def test_off_turn_action_rejected_but_reaction_allowed(combat):
    cid, ids, cur = combat
    other = next(i for i in ids if i != cur)
    assert server.use_action(cid, other, "action")["ok"] is False  # not their turn
    assert server.use_action(cid, other, "reaction")["ok"] is True  # reactions act off-turn
    assert server.use_action(cid, other, "reaction")["ok"] is False  # only one per round


def test_next_turn_refreshes_the_budget(combat):
    cid, _ids, cur = combat
    server.use_action(cid, cur, "action")
    server.use_action(cid, cur, "bonus")
    server.next_turn(cid)
    new_cur = server.get_state(cid)["current_turn"]
    assert server.use_action(cid, new_cur, "action")["ok"] is True  # fresh turn


# ==================== #778 red-first: cross-tool double-act rejected =========

def test_cast_then_cast_rejected(caster):
    cid, wiz, gob = caster
    _cast(cid, wiz, "Magic Missile", target_id=gob)  # action-cost cast spends the action
    with pytest.raises(ValueError, match="already used its action"):
        _cast(cid, wiz, "Fireball", target_id=gob)   # a 2nd action-cost cast is illegal


def test_cast_then_attack_rejected(caster):
    cid, wiz, gob = caster
    _cast(cid, wiz, "Magic Missile", target_id=gob)  # action spent on the cast
    with pytest.raises(ValueError, match="already cast a spell"):
        server.attack(cid, wiz, gob, attack_bonus=5, damage_dice="1d10")


def test_attack_then_cast_rejected(caster):
    cid, wiz, gob = caster
    server.attack(cid, wiz, gob, attack_bonus=5, damage_dice="1d10")  # Attack action spends it
    with pytest.raises(ValueError, match="already used its action"):
        _cast(cid, wiz, "Fireball", target_id=gob)


# ==================== #778: the legitimate turns that must PASS ==============

def test_healing_word_then_fireball_allowed(caster):
    """Healing Word (bonus action) + Fireball (action) is a legal 5e turn: the
    bonus-action heal must NOT burn the action."""
    cid, wiz, gob = caster
    hw = _cast(cid, wiz, "Healing Word", target_id=wiz)
    assert hw["spell"] == "Healing Word"
    fb = _cast(cid, wiz, "Fireball", target_id=gob)  # action still free -> allowed
    assert fb["spell"] == "Fireball"


def test_healing_word_then_use_action_allowed(caster):
    """The bug the issue calls out: a bonus-action cast wrongly refused the follow-up
    use_action('action'). It must be allowed."""
    cid, wiz, _gob = caster
    _cast(cid, wiz, "Healing Word", target_id=wiz)
    out = server.use_action(cid, wiz, "action")
    assert out["ok"] is True, "bonus-action cast must leave the action free"


def test_healing_word_burns_only_bonus_action(caster):
    cid, wiz, _gob = caster
    _cast(cid, wiz, "Healing Word", target_id=wiz)
    # Bonus is now spent; a second bonus-action verb is refused, the action untouched.
    assert server.use_action(cid, wiz, "bonus")["ok"] is False
    assert server.use_action(cid, wiz, "action")["ok"] is True


def test_surge_actions_enable_a_second_cast(caster):
    """Action Surge grants a fresh action, so a 2nd action-cost cast is legal after it."""
    cid, wiz, gob = caster
    server.update_character(
        cid, wiz, patch={"class_resources": {"action_surge": {"max": 1, "used": 0}}}
    )
    _cast(cid, wiz, "Magic Missile", target_id=gob)  # 1st action-cost cast
    server.use_resource(cid, wiz, "action_surge")     # grants a 2nd action this turn
    fb = _cast(cid, wiz, "Fireball", target_id=gob)   # now allowed by the surge
    assert fb["spell"] == "Fireball"
    # The surge is spent: a THIRD action-cost cast is refused again.
    with pytest.raises(ValueError, match="already used its action"):
        _cast(cid, wiz, "Magic Missile", target_id=gob)


def test_minute_and_hour_casts_refused_in_active_combat(caster):
    cid, wiz, _gob = caster
    server.learn_spells(cid, wiz, ["Identify"])   # casting time: 1 minute (ritual-scale)
    server.prepare_spells(cid, wiz, ["Identify"])
    with pytest.raises(ValueError, match="casting time"):
        _cast(cid, wiz, "Identify", target_id=wiz)


# ==================== #778: declared-action + Extra Attack stays green =======

def test_declared_action_then_extra_attack_still_green(tmp_path, monkeypatch):
    """A fighter with Extra Attack makes its two strikes under ONE Attack action —
    action_purpose stays "" for the attack path, so #778 does not touch it."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("AE778x")["id"]
    fighter = server.create_character(
        cid, "Lae", kind="player", class_name="Fighter", apply_srd_defaults=True,
        max_hp=30, armor_class=16,
    )["id"]
    server.update_character(cid, fighter, patch={"initiative_bonus": 100, "extra_attacks": 1})
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=40, armor_class=5)["id"]
    server.start_combat(cid, [fighter, gob])
    assert server.get_state(cid)["current_turn"] == fighter
    a1 = server.attack(cid, fighter, gob, attack_bonus=8, damage_dice="1d8+3")
    a2 = server.attack(cid, fighter, gob, attack_bonus=8, damage_dice="1d8+3")  # Extra Attack
    assert a1["attacks_made_this_turn"] == 1 and a2["attacks_made_this_turn"] == 2
    # A THIRD strike (no surge, one Extra Attack) is over budget — the existing gate rejects it.
    with pytest.raises(ValueError, match="already attacked|cannot attack"):
        server.attack(cid, fighter, gob, attack_bonus=8, damage_dice="1d8+3")


def test_declared_generic_action_then_attack_allowed(caster):
    """use_action('action') declares a generic action with no purpose stamp; the
    Attack action that follows (its concrete resolution) must still be legal — the
    #778 guard only fires on a cast/skip purpose."""
    cid, wiz, gob = caster
    assert server.use_action(cid, wiz, "action")["ok"] is True
    # action_purpose stays "" (generic), so an attack is NOT blocked by the cast/skip guard.
    out = server.attack(cid, wiz, gob, attack_bonus=5, damage_dice="1d10")
    assert out["attacks_made_this_turn"] == 1


# ==================== #778: skip stamps a purpose too =======================

def test_skip_then_attack_rejected(caster):
    cid, wiz, gob = caster
    server.use_action(cid, wiz, "skip")  # intentional pass — stamps action_purpose="skip"
    with pytest.raises(ValueError, match="skipped this turn"):
        server.attack(cid, wiz, gob, attack_bonus=5, damage_dice="1d10")


def test_skip_then_cast_rejected(caster):
    cid, wiz, gob = caster
    server.use_action(cid, wiz, "skip")
    with pytest.raises(ValueError, match="already used its action"):
        _cast(cid, wiz, "Fireball", target_id=gob)


# ==================== #778: next_turn clears the purpose ====================

def test_next_turn_clears_action_purpose(caster):
    cid, wiz, gob = caster
    _cast(cid, wiz, "Magic Missile", target_id=gob)     # purpose="cast"
    server.next_turn(cid)                                # -> goblin's turn
    server.next_turn(cid)                                # -> back to the Wizard, fresh
    assert server.get_state(cid)["current_turn"] == wiz
    fb = _cast(cid, wiz, "Fireball", target_id=gob)      # a fresh action -> allowed
    assert fb["spell"] == "Fireball"


# ==================== #778: additive — old snapshots round-trip =============

def test_old_snapshot_without_action_purpose_defaults_empty():
    """A snapshot written before #778 has no `action_purpose` on Combat; the load
    must deserialize it to "" (today's behaviour) and re-serialize identically."""
    from models import Combat

    legacy = {
        "active": True,
        "round": 2,
        "turn_index": 1,
        "order": [],
        "action_used": True,
        "bonus_action_used": False,
        "action_attacks_made": 1,
        "surge_actions": 0,
        # NOTE: no `action_purpose` key — this is the pre-#778 shape.
    }
    loaded = Combat.model_validate(legacy)
    assert loaded.action_purpose == "", "missing field must default to '' (today's behaviour)"
    assert loaded.action_used is True and loaded.action_attacks_made == 1
    # Round-trips: the field serializes back and reloads to the same value.
    reloaded = Combat.model_validate(loaded.model_dump(mode="json"))
    assert reloaded.action_purpose == ""
