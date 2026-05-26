import pytest

import combat
from dice import DiceRoll
from models import ActiveEffect, Character, Condition
import store


def mk(**kw) -> Character:
    return Character(name="T", **kw)


def _ds(total: int, natural: int) -> DiceRoll:
    return DiceRoll(
        expression="1d20",
        total=total,
        rolls=[natural],
        is_d20=True,
        natural=natural,
        crit=(natural == 20),
        fumble=(natural == 1),
    )


def _fixed_roll(expression: str, advantage: bool = False, disadvantage: bool = False, seed: int | None = None) -> DiceRoll:
    if expression.startswith("1d20"):
        return DiceRoll(
            expression=expression,
            total=20,
            rolls=[15],
            modifier=5,
            detail=f"{expression}[15] = 20",
            is_d20=True,
            natural=15,
        )
    return DiceRoll(expression=expression, total=6, rolls=[3], detail=f"{expression}[3] = 6")


# --- crit dice-doubling ---
@pytest.mark.parametrize(
    "expr,expected",
    [
        ("1d8+3", "2d8+3"),
        ("2d6", "4d6"),
        ("1d10+1d6+4", "2d10+2d6+4"),
        ("5", "5"),
        ("1d4-1", "2d4-1"),
    ],
)
def test_double_dice(expr, expected):
    assert combat.double_dice(expr) == expected


# --- damage resolution order ---
def test_temp_hp_absorbs_first():
    ch = mk(max_hp=20, current_hp=20, temp_hp=5)
    out = combat.apply_damage(ch, 3)
    assert ch.temp_hp == 2 and ch.current_hp == 20 and out["absorbed"] == 3


def test_damage_floors_at_zero_and_downs():
    ch = mk(max_hp=10, current_hp=4)
    out = combat.apply_damage(ch, 9)
    assert ch.current_hp == 0 and out["dying"] is True and ch.dead is False
    assert "unconscious" in out["conditions"]


def test_massive_damage_instant_death():
    ch = mk(max_hp=10, current_hp=6)  # remaining after 0 = 10 >= max_hp -> dead
    out = combat.apply_damage(ch, 16)
    assert ch.dead is True and out["dead"] is True


def test_hit_while_down_adds_failures():
    ch = mk(max_hp=10, current_hp=0)
    combat.apply_damage(ch, 3)
    assert ch.death_saves.failures == 1
    combat.apply_damage(ch, 3, crit=True)  # crit while down = two failures -> 3 -> dead
    assert ch.death_saves.failures == 3 and ch.dead is True


def test_concentration_dc():
    ch = mk(max_hp=50, current_hp=50, concentration="Bless")
    assert combat.apply_damage(ch, 9)["concentration_dc"] == 10  # max(10, 4)
    ch2 = mk(max_hp=50, current_hp=50, concentration="Haste")
    assert combat.apply_damage(ch2, 30)["concentration_dc"] == 15  # max(10, 15)


# --- healing ---
def test_healing_revives_from_dying():
    ch = mk(max_hp=10, current_hp=0)
    combat.apply_damage(ch, 1)
    out = combat.apply_healing(ch, 5)
    assert ch.current_hp == 5 and out["revived"] is True
    assert ch.death_saves.failures == 0 and "unconscious" not in out["conditions"]


def test_cannot_heal_dead():
    ch = mk(max_hp=10, current_hp=0, dead=True)
    out = combat.apply_healing(ch, 5)
    assert ch.current_hp == 0 and out["revived"] is False


# --- H1: manual HP-set transition (one source of truth with damage/heal) ---
def test_hp_set_transition_to_zero_downs_and_clears_concentration():
    """A manual set TO 0 (set_hp path) must mirror apply_damage: clear concentration + its
    twin effect, and down a PC/companion (unconscious + fresh death saves, not stable)."""
    eff = ActiveEffect(name="Bless", concentration=True, scale="minutes", rounds_remaining=10)
    ch = mk(max_hp=20, current_hp=0, concentration="Bless", active_effects=[eff], stable=True)
    out = combat.apply_hp_set_transition(ch, was_down=False)  # was up before, now at 0
    assert ch.concentration is None and ch.active_effects == []
    assert "unconscious" in out["conditions"] and out["dying"] is True
    assert ch.stable is False and ch.dead is False


def test_hp_set_transition_to_zero_kills_monster():
    """Monsters/NPCs die outright at 0 (no death saves) — same rule as apply_damage."""
    ch = mk(kind="monster", max_hp=15, current_hp=0)
    out = combat.apply_hp_set_transition(ch, was_down=False)
    assert ch.dead is True and out["dead"] is True


def test_hp_set_transition_from_zero_wakes():
    """A manual set FROM 0 to >0 wakes the character (mirrors apply_healing's un-down)."""
    ch = mk(max_hp=20, current_hp=8, conditions=[Condition.UNCONSCIOUS])
    ch.death_saves.failures = 2
    out = combat.apply_hp_set_transition(ch, was_down=True)
    assert out["revived"] is True and "unconscious" not in out["conditions"]
    assert ch.death_saves.failures == 0 and ch.stable is False


def test_hp_set_transition_staying_up_is_noop():
    """Setting HP between two positive values touches nothing (no spurious down/wake)."""
    eff = ActiveEffect(name="Bless", concentration=True, scale="minutes", rounds_remaining=10)
    ch = mk(max_hp=20, current_hp=12, concentration="Bless", active_effects=[eff])
    out = combat.apply_hp_set_transition(ch, was_down=False)
    assert ch.concentration == "Bless" and [e.name for e in ch.active_effects] == ["Bless"]
    assert out["revived"] is False and "unconscious" not in out["conditions"]


# --- death-save state machine ---
def test_three_successes_stabilize():
    ch = mk(max_hp=10, current_hp=0)
    combat.resolve_death_save(ch, _ds(12, 12))
    combat.resolve_death_save(ch, _ds(11, 11))
    out = combat.resolve_death_save(ch, _ds(15, 15))
    assert out["result"] == "stabilized" and ch.stable is True


def test_three_failures_die():
    ch = mk(max_hp=10, current_hp=0)
    combat.resolve_death_save(ch, _ds(5, 5))
    out = combat.resolve_death_save(ch, _ds(1, 1))  # nat 1 = two failures -> total 3
    assert out["result"] == "dead" and ch.dead is True


def test_nat20_revives():
    ch = mk(max_hp=10, current_hp=0)
    out = combat.resolve_death_save(ch, _ds(20, 20))
    assert ch.current_hp == 1 and out["result"] == "regain_1hp"


# --- condition hooks ---
def test_attack_modifiers():
    adv, dis = combat.attack_modifiers(mk(), mk(conditions=[Condition.PRONE]))
    assert adv and not dis
    adv2, dis2 = combat.attack_modifiers(mk(conditions=[Condition.BLINDED]), mk())
    assert dis2 and not adv2


# --- end-to-end through the MCP tools ---
def test_combat_flow_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Combat Test")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=12, armor_class=12)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]

    view = server.start_combat(cid, [hero, gob])
    assert view["active"] and len(view["order"]) == 2 and view["round"] == 1

    res = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3", damage_type="slashing")
    assert isinstance(res["hit"], bool) and "attack_roll" in res

    out = server.apply_damage(cid, gob, 100)  # heavy hit -> defeated (0 HP)
    assert out["current_hp"] == 0

    nt = server.next_turn(cid)
    assert nt["round"] >= 1
    server.end_combat(cid)
    assert server.get_state(cid)["in_combat"] is False


def test_attack_logs_structured_combat_event_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    monkeypatch.setattr(server.dice_mod, "roll", _fixed_roll)

    cid = server.create_campaign("Combat Event Cards")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=12, armor_class=12)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]

    server.start_combat(cid, [hero, gob])
    server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3", damage_type="slashing")

    camp = store.load_campaign(cid)
    entries = store.read_log(cid, camp.active_session_id)
    attack_entry = next(e for e in entries if e.payload and e.payload.get("event") == "attack")

    assert attack_entry.kind == "combat"
    assert attack_entry.payload["schema"] == "clawdnd.combat_event.v1"
    assert attack_entry.payload["outcome"] == "hit"
    assert attack_entry.payload["actor"] == {"id": hero, "name": "Hero"}
    assert attack_entry.payload["target"]["id"] == gob
    assert attack_entry.payload["damage"]["total"] == 6


def test_turn_advance_logs_structured_combat_event_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Turn Event Cards")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=12, armor_class=12)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]

    before = server.start_combat(cid, [hero, gob])
    advanced = server.next_turn(cid)

    camp = store.load_campaign(cid)
    entries = store.read_log(cid, camp.active_session_id)
    turn_entry = next(e for e in entries if e.payload and e.payload.get("event") == "turn_advanced")

    assert turn_entry.kind == "combat"
    assert turn_entry.payload["schema"] == "clawdnd.combat_event.v1"
    assert turn_entry.payload["previous"]["id"] == before["current"]
    assert turn_entry.payload["current"]["id"] == advanced["current"]
    assert turn_entry.payload["round"] == advanced["round"]
    assert turn_entry.payload["death_save_due"] == advanced["death_save_due"]


def test_death_save_logs_structured_combat_event_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    monkeypatch.setattr(server.dice_mod, "roll", _fixed_roll)

    cid = server.create_campaign("Death Save Event Cards")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=12, armor_class=12)["id"]
    server.apply_damage(cid, hero, 12)

    out = server.roll_death_save(cid, hero)

    camp = store.load_campaign(cid)
    entries = store.read_log(cid, camp.active_session_id)
    death_entry = next(e for e in entries if e.payload and e.payload.get("event") == "death_save")

    assert death_entry.kind == "combat"
    assert death_entry.payload["schema"] == "clawdnd.combat_event.v1"
    assert death_entry.payload["target"] == {"id": hero, "name": "Hero"}
    assert death_entry.payload["roll"]["natural"] == 15
    assert death_entry.payload["result"] == out["result"] == "pending"
    assert death_entry.payload["successes"] == 1
    assert death_entry.payload["state"]["dying"] is True


# --- hardening regressions (from adversarial review) ---
def test_death_save_guard_on_stable():  # C1
    ch = mk(max_hp=10, current_hp=0, stable=True)
    out = combat.resolve_death_save(ch, _ds(5, 5))
    assert out["result"] == "not_dying"
    assert ch.death_saves.failures == 0 and ch.stable is True


def test_death_save_guard_not_at_zero():  # C1
    ch = mk(max_hp=10, current_hp=10)
    out = combat.resolve_death_save(ch, _ds(5, 5))
    assert out["result"] == "not_dying" and ch.death_saves.failures == 0


def test_concentration_ends_when_downed():  # H3
    ch = mk(max_hp=10, current_hp=6, concentration="Bless")
    out = combat.apply_damage(ch, 6)  # to exactly 0
    assert ch.concentration is None and out["concentration_dc"] is None


def test_concentration_dc_when_surviving():  # H3
    ch = mk(max_hp=30, current_hp=30, concentration="Bless")
    out = combat.apply_damage(ch, 10)
    assert ch.concentration == "Bless" and out["concentration_dc"] == 10


def test_prone_ranged_vs_melee():  # M6
    adv_m, dis_m = combat.attack_modifiers(mk(), mk(conditions=[Condition.PRONE]), is_ranged=False)
    assert adv_m and not dis_m
    adv_r, dis_r = combat.attack_modifiers(mk(), mk(conditions=[Condition.PRONE]), is_ranged=True)
    assert dis_r and not adv_r


def test_next_turn_skips_dead(tmp_path, monkeypatch):  # H2
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("turns")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=10)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=10)["id"]
    d = server.create_character(cid, "Doomed", kind="monster", max_hp=10)["id"]
    server.start_combat(cid, [a, b, d])
    server.apply_damage(cid, d, 100)  # d is dead
    seen = set()
    for _ in range(6):  # two laps
        v = server.next_turn(cid)
        if v["current"]:
            seen.add(v["current"])
    assert d not in seen


def test_remove_combatant(tmp_path, monkeypatch):  # H2
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("rm")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=10)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=10)["id"]
    server.start_combat(cid, [a, b])
    view = server.remove_combatant(cid, b)
    assert all(cb["character_id"] != b for cb in view["order"])


def test_remove_combatant_after_many_rounds_keeps_current_and_round_sane(tmp_path, monkeypatch):
    # Regression: turn_index was a MONOTONIC counter (next_turn only did +=1) while
    # remove_combatant treated it as a normalized index ("idx < turn_index"). After
    # several rounds turn_index >> n, so removing an already-acted EARLIER combatant
    # wrongly decremented the pointer and SKIPPED the current combatant's turn (and
    # drifted the round counter). turn_index is now normalized to [0, n).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("rounds")["id"]
    ids = [server.create_character(cid, f"C{i}", kind="monster", max_hp=10)["id"] for i in range(4)]
    view = server.start_combat(cid, ids)
    order = [cb["character_id"] for cb in view["order"]]  # the rolled initiative order

    # Advance 6 turns: with n=4 the pointer lands on order[2]; turn_index would have
    # reached 6 under the old monotonic scheme — large enough to trigger the bug.
    last = None
    for _ in range(6):
        last = server.next_turn(cid)
    assert last["current"] == order[2]
    round_before = last["round"]

    # Remove order[0] — already acted this cycle, EARLIER than the current combatant.
    # Whose turn it is must NOT change, and the round must not jump.
    after = server.remove_combatant(cid, order[0])
    assert after["current"] == order[2], "removing an earlier combatant skipped the current turn"
    assert after["round"] == round_before
    assert 0 <= after["turn_index"] < len(after["order"])  # normalized, not monotonic


# --- turn ownership + action economy (mechanical-correctness defect 1) ------
# Pure-helper rules (attacks-per-action) tested first, then end-to-end through attack().


@pytest.mark.parametrize(
    "extra,surge,expected",
    [
        (0, 0, 1),   # vanilla: one attack per Attack action
        (1, 0, 2),   # Extra Attack (fighter L5): two attacks under one action
        (2, 0, 3),   # higher Extra Attack
        (0, 1, 2),   # Action Surge: a 2nd whole action -> 2 attacks total
        (1, 1, 4),   # Extra Attack + Action Surge: (1+1) * (1+1)
        (-3, -3, 1), # negatives clamp to 0 -> one action's single attack
    ],
)
def test_attacks_allowed_formula(extra, surge, expected):
    assert combat.attacks_allowed(extra, surge) == expected


def test_check_action_attack_rejects_non_current():
    ok, reason = combat.check_action_attack(
        is_current=False, attacks_made=0, extra_attacks=0, surge_actions=0
    )
    assert ok is False and "not this creature's turn" in reason


def test_check_action_attack_first_attack_ok_second_rejected_without_extra():
    # one Attack action, no Extra Attack: first attack ok, second has no basis.
    ok1, _ = combat.check_action_attack(
        is_current=True, attacks_made=0, extra_attacks=0, surge_actions=0
    )
    ok2, reason2 = combat.check_action_attack(
        is_current=True, attacks_made=1, extra_attacks=0, surge_actions=0
    )
    assert ok1 is True
    assert ok2 is False and "already attacked this turn" in reason2


def test_check_action_attack_extra_attack_allows_multiple_then_blocks():
    # extra_attacks=1 -> 2 attacks allowed under the one action; the 3rd is blocked.
    assert combat.check_action_attack(
        is_current=True, attacks_made=1, extra_attacks=1, surge_actions=0
    )[0] is True
    blocked, reason = combat.check_action_attack(
        is_current=True, attacks_made=2, extra_attacks=1, surge_actions=0
    )
    assert blocked is False and "no attacks left" in reason


def test_check_action_attack_action_surge_grants_more():
    # 2 attacks already made, no Extra Attack, but an Action Surge was spent -> a 2nd
    # action's attack is allowed (budget (0+1)*(1+1)=2 -> wait: with surge, attacks_made=1
    # leaves room). Verify the surge raises the ceiling.
    assert combat.attacks_allowed(0, 1) == 2
    assert combat.check_action_attack(
        is_current=True, attacks_made=1, extra_attacks=0, surge_actions=1
    )[0] is True


def _combat_with_known_current(server):
    """Start a 3-combatant fight and return (cid, current_id, other_id). Initiative is
    random, so we read who's current from get_state rather than assume it."""
    cid = server.create_campaign("turn-order")["id"]
    ids = [
        server.create_character(cid, n, kind=k, max_hp=30, armor_class=12)["id"]
        for n, k in (("A", "player"), ("B", "player"), ("M", "monster"))
    ]
    server.start_combat(cid, ids)
    cur = server.get_state(cid)["current_turn"]
    other = next(i for i in ids if i != cur)
    return cid, cur, other, ids


def test_attack_by_non_current_combatant_is_rejected_after_reaction_spent(tmp_path, monkeypatch):
    # An off-turn action-attack is treated as a reaction (an opportunity attack): it
    # resolves ONCE this round, then a further off-turn attack by the same creature is
    # rejected (reaction already used). This both ALLOWS the legitimate OA and stops a
    # non-current creature from taking a free action-attack (the QA defect: Kield
    # attacked on Renn's turn).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid, cur, other, _ids = _combat_with_known_current(server)
    # `other` is NOT the current combatant. First off-turn strike = a reaction, resolves.
    first = server.attack(cid, other, cur, attack_bonus=5, damage_dice="1d6")
    assert first.get("reaction_used") is True
    # Reaction spent -> a second off-turn attack by `other` this round is rejected, no state change.
    hp_before = server.get_character(cid, cur)["current_hp"]
    with pytest.raises(ValueError, match="reaction"):
        server.attack(cid, other, cur, attack_bonus=5, damage_dice="1d6")
    assert server.get_character(cid, cur)["current_hp"] == hp_before  # rejected attack rolled nothing


def test_explicit_opportunity_attack_off_turn_is_allowed(tmp_path, monkeypatch):
    # A reaction (opportunity attack) legitimately happens off-turn — is_reaction=True is
    # accepted for a non-current combatant and gated only by reaction_used.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid, cur, other, _ids = _combat_with_known_current(server)
    oa = server.attack(cid, other, cur, attack_bonus=5, damage_dice="1d6", is_reaction=True)
    assert oa.get("reaction_used") is True and "hit" in oa  # resolved off-turn
    # And the reaction is now spent (one per round).
    with pytest.raises(ValueError, match="reaction"):
        server.attack(cid, other, cur, attack_bonus=5, damage_dice="1d6", is_reaction=True)


def test_second_attack_same_turn_rejected_without_extra_attack(tmp_path, monkeypatch):
    # The current combatant attacks on its own turn: the FIRST attack consumes the Attack
    # action; a SECOND with no Extra Attack and no Action Surge is rejected (the QA defect:
    # two full attacks in one round with no mechanical basis).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid, cur, other, _ids = _combat_with_known_current(server)
    first = server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    assert first.get("attacks_made_this_turn") == 1
    assert first.get("attacks_allowed_this_turn") == 1
    with pytest.raises(ValueError, match="already attacked this turn"):
        server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    # The Attack action was consumed, so use_action(action) also reflects it.
    assert server.use_action(cid, cur, "action")["ok"] is False


def test_fighter_with_extra_attacks_makes_its_attacks_under_one_action(tmp_path, monkeypatch):
    # A fighter with Extra Attack (extra_attacks=1) makes its TWO attacks under one action;
    # the third is rejected. The bonus action stays available (Extra Attack is all one action).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid, cur, other, _ids = _combat_with_known_current(server)
    server.update_character(cid, cur, {"extra_attacks": 1})  # grant Extra Attack to the current combatant
    a1 = server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    assert a1["attacks_allowed_this_turn"] == 2 and a1["attacks_made_this_turn"] == 1
    a2 = server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    assert a2["attacks_made_this_turn"] == 2  # second attack of the multiattack
    with pytest.raises(ValueError, match="no attacks left"):
        server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")  # third has no basis
    # Bonus action untouched by spending the Attack action.
    assert server.use_action(cid, cur, "bonus")["ok"] is True


def test_action_surge_grants_a_second_attack_action(tmp_path, monkeypatch):
    # A fighter with no Extra Attack: one attack, then the second is blocked — UNTIL an
    # Action Surge is spent (a fresh action), which grants another attack.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid, cur, other, _ids = _combat_with_known_current(server)
    # Give the current combatant an action_surge pool (1 use).
    server.set_class_resource(cid, cur, "action_surge", max=1, recharge="short")
    server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")  # first attack ok
    with pytest.raises(ValueError, match="already attacked|no attacks left"):
        server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    # Spend Action Surge — a fresh action -> another attack is now allowed.
    surge = server.use_resource(cid, cur, "action_surge")
    assert surge["ok"] is True
    after = server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    assert after["attacks_allowed_this_turn"] == 2 and after["attacks_made_this_turn"] == 2


def test_next_turn_resets_attack_economy(tmp_path, monkeypatch):
    # The attack budget refreshes each turn: after exhausting it, advancing the turn lets
    # the new current combatant attack again.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid, cur, other, _ids = _combat_with_known_current(server)
    server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    with pytest.raises(ValueError):
        server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")  # exhausted
    server.next_turn(cid)
    new_cur = server.get_state(cid)["current_turn"]
    new_other = next(i for i in _ids if i != new_cur)
    fresh = server.attack(cid, new_cur, new_other, attack_bonus=5, damage_dice="1d6")
    assert fresh.get("attacks_made_this_turn") == 1  # fresh turn, fresh budget


def test_cast_spell_by_non_current_caster_is_rejected_after_reaction_spent(tmp_path, monkeypatch):
    # cast_spell mirrors attack: an off-turn cast is a reaction (resolves once), then a
    # second off-turn cast the same round is rejected (turn ownership for action-casts).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid, cur, other, _ids = _combat_with_known_current(server)
    # `other` casts a cantrip (no slot needed) off-turn -> resolves as a reaction.
    first = server.cast_spell(cid, other, "Fire Bolt", target_id=cur)
    assert "spell" in first  # resolved
    with pytest.raises(ValueError, match="reaction"):
        server.cast_spell(cid, other, "Fire Bolt", target_id=cur)  # reaction spent


def test_next_turn_with_all_combatants_dead_returns_no_current_without_raising(tmp_path, monkeypatch):
    # A TPK / mutual-kill edge: with EVERY combatant dead, next_turn's one-lap loop finds no
    # living candidate, so `cur` stays None and the `cur.name if cur else None` path (server.py
    # ~2028) yields current_name=None — and the call must NOT raise. (`current` itself is the
    # computed turn_index slot, which stays set while combat is active; the None is current_name.)
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("tpk")["id"]
    a = server.create_character(cid, "A", kind="monster", max_hp=10)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=10)["id"]
    server.start_combat(cid, [a, b])
    server.apply_damage(cid, a, 100)  # both down for good (monsters die outright at 0)
    server.apply_damage(cid, b, 100)
    view = server.next_turn(cid)  # no living combatant left -> must not raise
    assert view["current_name"] is None      # the `cur.name if cur else None` else-branch
    assert view["death_save_due"] is False    # no one is owed a death save (nobody's alive/dying)


def test_action_surge_by_non_current_combatant_does_not_unlock_current_turns_second_attack(tmp_path, monkeypatch):
    # Turn-ownership for Action Surge: surge_actions only rises for the CURRENT combatant
    # (server.py ~3184). A NON-current creature spending action_surge must NOT raise the current
    # combatant's attack ceiling — so the current combatant's 2nd attack is still rejected.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    import store

    cid, cur, other, _ids = _combat_with_known_current(server)
    # Give the OFF-TURN combatant an action_surge pool and spend it — succeeds as a pool spend,
    # but it isn't this combatant's turn, so the combat-wide surge_actions must stay 0.
    server.set_class_resource(cid, other, "action_surge", max=1, recharge="short")
    spent = server.use_resource(cid, other, "action_surge")
    assert spent["ok"] is True
    assert store.load_campaign(cid).combat.surge_actions == 0  # NOT incremented by a non-current spend

    # The CURRENT combatant (no Extra Attack, no surge of its own) gets ONE attack; the second is
    # rejected — the off-turn surge granted it nothing.
    target = next(i for i in _ids if i != cur)
    server.attack(cid, cur, target, attack_bonus=5, damage_dice="1d6")
    with pytest.raises(ValueError, match="already attacked this turn"):
        server.attack(cid, cur, target, attack_bonus=5, damage_dice="1d6")


# =========================================================================
# Change 2: start_combat outlook fold-in
# =========================================================================


def test_start_combat_outlook_present_for_overmatch(tmp_path, monkeypatch):
    """L3 party vs dragon-tier foe → start_combat view has 'outlook' with must_offer_out true.

    Uses spawn_monster("Troll") — CR 5, 1800 XP — and two copies to hit the 2x+ overmatch
    threshold for a level-3 party (deadly budget ~1600 XP, adjusted_xp for 2 trolls with
    x1.5 multiplier = 5400, ratio ~3.375 > 2.0).  Must_offer_out fires for avg_level <= 5."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Outlook Fold-in Test")["id"]
    # 4x level-3 player characters
    pc_ids = [
        server.create_character(cid, f"PC{i}", kind="player",
                                class_name="fighter", level=3)["id"]
        for i in range(4)
    ]
    # Spawn 2 Trolls — the spawn path sets xp_value on the Character automatically
    spawned = server.spawn_monster(cid, "Troll", count=2)
    troll_ids = [s["id"] for s in spawned["spawned"]]

    view = server.start_combat(cid, pc_ids + troll_ids)
    assert "outlook" in view, "over-matched fight must surface 'outlook' in start_combat view"
    assert view["outlook"]["must_offer_out"] is True


def test_start_combat_no_outlook_for_fair_fight(tmp_path, monkeypatch):
    """A balanced fight (L5 party vs a single Bandit) must NOT add 'outlook' to the view.

    Bandit = CR 1/8, 25 XP — trivially below even the easy budget for 4x L5 PCs.
    The view must be UNCHANGED (no 'outlook' key added for a non-deadly fight)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Fair Fight Test")["id"]
    pc_ids = [
        server.create_character(cid, f"PC{i}", kind="player",
                                class_name="fighter", level=5)["id"]
        for i in range(4)
    ]
    # Spawn a Bandit (CR 1/8, 25 XP) — fair for 4x L5
    spawned = server.spawn_monster(cid, "Bandit", count=1)
    bandit_id = spawned["spawned"][0]["id"]

    view = server.start_combat(cid, pc_ids + [bandit_id])
    assert "outlook" not in view, "a fair/easy fight must NOT add 'outlook' to start_combat view"


# =========================================================================
# Change 3: start_combat surpriser_ids — surprise-attack affordance (#153)
# =========================================================================


def test_start_combat_surpriser_is_first_in_turn_order(tmp_path, monkeypatch):
    """Surpriser must be placed first in the turn order regardless of initiative rolls."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Surprise Test")["id"]
    # Give the target a sky-high initiative bonus so it would naturally roll first,
    # then verify the attacker (low DEX) still leads when named as surpriser.
    attacker = server.create_character(
        cid, "Attacker", kind="player", max_hp=10, armor_class=12,
    )["id"]
    target = server.create_character(
        cid, "Target", kind="monster", max_hp=10, armor_class=14,
    )["id"]

    view = server.start_combat(cid, [attacker, target], surpriser_ids=[attacker])

    order_ids = [entry["character_id"] for entry in view["order"]]
    assert order_ids[0] == attacker, "surpriser must be first in turn order"
    assert "surprise" in view, "start_combat must surface a 'surprise' key when surpriser_ids is set"
    assert view["surprise"]["surprisers"] == [attacker]
    assert "advantage" in view["surprise"]["note"]


def test_start_combat_no_surpriser_ids_unchanged_behaviour(tmp_path, monkeypatch):
    """Default call (no surpriser_ids) must not add a 'surprise' key — purely additive."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("No Surprise Test")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=10)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=10)["id"]

    view = server.start_combat(cid, [a, b])

    assert "surprise" not in view, "no surpriser_ids must not add 'surprise' key"
    assert len(view["order"]) == 2


def test_start_combat_unknown_surpriser_id_is_skipped_gracefully(tmp_path, monkeypatch):
    """An id not in combatant_ids must be silently ignored — no error, no corruption."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Bad Surpriser Test")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=10)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=10)["id"]

    # "ghost-id" is not a real combatant_id — must not raise, must not appear in order
    view = server.start_combat(cid, [a, b], surpriser_ids=["ghost-id"])

    assert view["active"] is True
    assert len(view["order"]) == 2
    # Unknown id stripped → no surprise key surfaced (no valid surprisers remain)
    assert "surprise" not in view


# --- monster_combat surfacing (issue #157: Bandit Captain Multiattack) ---------


def test_monster_combat_bandit_captain_multiattack(tmp_path, monkeypatch):
    """Bandit Captain → monster_combat shows attacks_per_turn=2 with Scimitar + Pistol.

    This is the primary regression guard for issue #157: the Bandit Captain's
    Multiattack ('makes two attacks') was silently lost — the DM made one attack
    per turn and halved the monster's threat. The engine now surfaces authoritative
    Multiattack count + per-attack to-hit/damage at start_combat so the DM can't
    miss it.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Multiattack Test")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=20)["id"]
    res = server.spawn_monster(cid, "Bandit Captain")
    captain_id = res["spawned"][0]["id"]

    view = server.start_combat(cid, [pc, captain_id])

    assert "monster_combat" in view, "monster_combat key must be present when a monster is in the fight"
    entries = {e["id"]: e for e in view["monster_combat"]}
    assert captain_id in entries, "Bandit Captain must have a monster_combat entry"

    entry = entries[captain_id]
    assert entry["attacks_per_turn"] == 2, (
        f"Bandit Captain Multiattack should be 2; got {entry['attacks_per_turn']}"
    )
    attack_names = {a["name"] for a in entry["attacks"]}
    assert "Scimitar" in attack_names, f"Scimitar attack missing; got {attack_names}"
    assert "Pistol" in attack_names, f"Pistol attack missing; got {attack_names}"
    for atk in entry["attacks"]:
        assert isinstance(atk["to_hit"], int), f"to_hit must be int; got {atk}"
        assert atk["to_hit"] > 0, f"to_hit must be positive; got {atk}"
        assert atk["damage"], f"damage must be non-empty; got {atk}"


def test_monster_combat_single_attack_monster(tmp_path, monkeypatch):
    """Wolf (no Multiattack) → attacks_per_turn=1, no false extra attacks.

    Verifies that the Multiattack parser doesn't invent an extra attack when the
    creature has only a single attack action and no Multiattack entry.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Single Attack Test")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=20)["id"]
    res = server.spawn_monster(cid, "Wolf")
    wolf_id = res["spawned"][0]["id"]

    view = server.start_combat(cid, [pc, wolf_id])

    assert "monster_combat" in view
    entries = {e["id"]: e for e in view["monster_combat"]}
    assert wolf_id in entries
    wolf_entry = entries[wolf_id]
    assert wolf_entry["attacks_per_turn"] == 1, (
        f"Wolf has no Multiattack — attacks_per_turn should be 1; got {wolf_entry['attacks_per_turn']}"
    )
    # Wolf has a Bite attack with authoritative to-hit
    assert len(wolf_entry["attacks"]) >= 1
    bite = next((a for a in wolf_entry["attacks"] if "Bite" in a["name"]), None)
    assert bite is not None, "Wolf should have a Bite attack entry"
    assert isinstance(bite["to_hit"], int) and bite["to_hit"] > 0


def test_monster_combat_absent_with_no_monsters(tmp_path, monkeypatch):
    """monster_combat key must NOT appear when the fight is PC-only (additive, no regressions)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("PC Only Test")["id"]
    a = server.create_character(cid, "Hero A", kind="player", max_hp=20)["id"]
    b = server.create_character(cid, "Hero B", kind="player", max_hp=18)["id"]

    view = server.start_combat(cid, [a, b])
    assert "monster_combat" not in view, (
        "monster_combat key must be absent when no monsters are in the fight"
    )
