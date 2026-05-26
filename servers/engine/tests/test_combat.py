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


# --- multi-component damage (#210): per-type resolution, summed as one hit ---
def test_apply_damage_components_sums_both_types():
    """Two typed components with no resistance apply their full sum as one hit
    (Ghoul Bite: 7 piercing + 4 necrotic = 11 to HP)."""
    ch = mk(kind="monster", max_hp=30, current_hp=30)
    out = combat.apply_damage_components(
        ch, [{"amount": 7, "type": "piercing"}, {"amount": 4, "type": "necrotic"}]
    )
    assert ch.current_hp == 19  # 30 - 11
    assert out["total_adjusted"] == 11
    assert out["components"] == [
        {"type": "piercing", "raw": 7, "adjusted": 7},
        {"type": "necrotic", "raw": 4, "adjusted": 4},
    ]


def test_apply_damage_components_per_type_resistance_halves_only_the_matching_one():
    """Resistance to ONE component's type halves only that component; the other is full.
    necrotic-resistant target: 8 piercing (full) + 6 necrotic (->3) = 11 to HP."""
    ch = mk(kind="monster", max_hp=40, current_hp=40, damage_resistances=["necrotic"])
    out = combat.apply_damage_components(
        ch, [{"amount": 8, "type": "piercing"}, {"amount": 6, "type": "necrotic"}]
    )
    assert out["components"] == [
        {"type": "piercing", "raw": 8, "adjusted": 8},
        {"type": "necrotic", "raw": 6, "adjusted": 3},
    ]
    assert out["total_adjusted"] == 11 and ch.current_hp == 29


def test_apply_damage_components_per_type_immunity_zeroes_only_the_matching_one():
    ch = mk(kind="monster", max_hp=40, current_hp=40, damage_immunities=["necrotic"])
    out = combat.apply_damage_components(
        ch, [{"amount": 5, "type": "piercing"}, {"amount": 9, "type": "necrotic"}]
    )
    assert out["total_adjusted"] == 5 and ch.current_hp == 35  # necrotic zeroed


def test_apply_damage_components_single_component_identical_to_apply_damage():
    """A one-element component list is arithmetically identical to apply_damage(damage_type=…)."""
    a = mk(kind="monster", max_hp=40, current_hp=40, damage_resistances=["fire"])
    b = mk(kind="monster", max_hp=40, current_hp=40, damage_resistances=["fire"])
    o_single = combat.apply_damage(a, 10, damage_type="fire")
    o_multi = combat.apply_damage_components(b, [{"amount": 10, "type": "fire"}])
    assert a.current_hp == b.current_hp == 35
    assert o_multi["damage_to_hp"] == o_single["damage_to_hp"]
    assert o_multi["total_adjusted"] == 5


def test_apply_damage_components_half_on_save_halves_each_component_first():
    """half=True halves EACH component before its per-type adjustment (save-for-half,
    mixed types): 10 piercing -> 5; 8 cold -> 4 then resisted -> 2; sum 7."""
    ch = mk(kind="monster", max_hp=40, current_hp=40, damage_resistances=["cold"])
    out = combat.apply_damage_components(
        ch, [{"amount": 10, "type": "piercing"}, {"amount": 8, "type": "cold"}], half=True
    )
    assert out["components"] == [
        {"type": "piercing", "raw": 10, "adjusted": 5},
        {"type": "cold", "raw": 8, "adjusted": 2},
    ]
    assert out["total_adjusted"] == 7 and ch.current_hp == 33


def test_apply_damage_components_concentration_dc_uses_combined_total():
    """The concentration DC is half the COMBINED damage taken (one hit), not per component."""
    ch = mk(max_hp=80, current_hp=80, concentration="Bless")
    out = combat.apply_damage_components(
        ch, [{"amount": 12, "type": "piercing"}, {"amount": 12, "type": "necrotic"}]
    )
    assert out["concentration_dc"] == 12  # max(10, 24//2)


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


# --- advantage-granting rider on the target (Guiding Bolt, #194) ---
def test_attack_modifiers_advantage_granting_effect_on_target():
    """A target carrying an advantage-granting active_effect (Guiding Bolt's rider) auto-
    grants advantage to the attacker — even with no conditions and no explicit flag."""
    gb = ActiveEffect(name="Guiding Bolt", grants_advantage=True, scale="rounds", rounds_remaining=1)
    adv, dis = combat.attack_modifiers(mk(), mk(active_effects=[gb]))
    assert adv and not dis


def test_attack_modifiers_plain_effect_does_not_grant_advantage():
    """An ordinary timed effect (Bless) is NOT a 'next attack has advantage' rider —
    grants_advantage defaults False, so a target carrying it is unaffected (no regression)."""
    bless = ActiveEffect(name="Bless", scale="minutes", rounds_remaining=10)
    adv, dis = combat.attack_modifiers(mk(), mk(active_effects=[bless]))
    assert not adv and not dis
    # ...and a target with no effects at all is likewise unaffected.
    adv0, dis0 = combat.attack_modifiers(mk(), mk())
    assert not adv0 and not dis0


def test_attack_modifiers_marker_combines_with_disadvantage():
    """The marker grants advantage; an attacker's own disadvantage (blinded) still applies —
    the caller/dice.roll cancels them. The marker doesn't suppress condition-derived disadv."""
    gb = ActiveEffect(name="Guiding Bolt", grants_advantage=True, scale="rounds", rounds_remaining=1)
    adv, dis = combat.attack_modifiers(mk(conditions=[Condition.BLINDED]), mk(active_effects=[gb]))
    assert adv and dis


def test_advantage_granting_effect_helper():
    """advantage_granting_effect returns the flagged effect (Guiding Bolt) or None — pure,
    and it does not mutate the target."""
    gb = ActiveEffect(name="Guiding Bolt", grants_advantage=True)
    bless = ActiveEffect(name="Bless")
    tgt = mk(active_effects=[bless, gb])
    found = combat.advantage_granting_effect(tgt)
    assert found is gb
    assert len(tgt.active_effects) == 2  # no mutation
    assert combat.advantage_granting_effect(mk(active_effects=[bless])) is None
    assert combat.advantage_granting_effect(mk()) is None


def test_spell_grants_advantage_registry():
    assert combat.spell_grants_advantage("Guiding Bolt") is True
    assert combat.spell_grants_advantage("Bless") is False
    assert combat.spell_grants_advantage("Fire Bolt") is False


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
    # Pass the outgoing combatant if it's the PC, then advance (#160 enforcement).
    cur = server.get_state(cid)["current_turn"]
    if server.get_character(cid, cur)["kind"] in ("player", "companion"):
        server.use_action(cid, cur, "skip")
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
        # PC must act or pass before next_turn advances past them (#160 enforcement).
        cur = server.get_state(cid)["current_turn"]
        ch = server.get_character(cid, cur)
        if ch["kind"] in ("player", "companion"):
            server.use_action(cid, cur, "skip")
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


# --- turn_brief in next_turn (#166) -------------------------------------------------


def test_next_turn_brief_monster_multiattack(tmp_path, monkeypatch):
    """next_turn returns turn_brief with Bandit Captain's Multiattack count + to-hit (#166).

    The DM sees authoritative Multiattack data AT THE PER-TURN TRIGGER so it can't
    drift back to a single-attack habit by round 3 (the primary combat-adherence gap).
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Brief Monster Test")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=20)["id"]
    res = server.spawn_monster(cid, "Bandit Captain")
    captain_id = res["spawned"][0]["id"]

    server.start_combat(cid, [pc, captain_id])
    # Initiative order is random; the #183 guard blocks advancing past an able PC
    # who hasn't acted, so declare a pass for the PC if it won initiative.
    try:
        nt = server.next_turn(cid)  # advance past the first combatant to the second
    except ValueError:
        server.use_action(cid, pc, "skip")
        nt = server.next_turn(cid)

    assert "turn_brief" in nt, "next_turn must always include turn_brief when combat is active"
    brief = nt["turn_brief"]
    assert "name" in brief
    assert "kind" in brief
    assert "attack" in brief, "turn_brief must include an 'attack' key"

    # If this turn belongs to the Bandit Captain, assert full monster data.
    # If it belongs to the PC (initiative order varies), just confirm the schema.
    if brief["kind"] == "monster":
        atk = brief["attack"]
        assert "attacks_per_turn" in atk, "monster turn_brief must have attacks_per_turn"
        assert atk["attacks_per_turn"] >= 1
        assert "attacks" in atk, "monster turn_brief must have attacks list"
        # Verify the structure of each attack entry
        for a in atk["attacks"]:
            assert "to_hit" in a, f"attack entry missing to_hit: {a}"
            assert isinstance(a["to_hit"], int)
    else:
        # PC schema
        atk = brief["attack"]
        assert "melee_attack_bonus" in atk
        assert "ranged_attack_bonus" in atk
        assert "melee_damage_mod" in atk
        assert "ranged_damage_mod" in atk


def test_next_turn_brief_monster_has_correct_multiattack_count(tmp_path, monkeypatch):
    """Bandit Captain's turn_brief must show attacks_per_turn=2 when it IS the current combatant.

    Strategy: put the Bandit Captain FIRST in initiative by monkeypatching dice.roll so
    the captain always rolls max initiative; the first next_turn then belongs to the PC,
    the second to the captain — ensuring we assert the captain's brief specifically.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    # Force deterministic initiative: hero rolls 1, captain rolls 2 → captain goes first.
    _call_count = [0]
    _orig_roll = server.dice_mod.roll

    def _rigged_roll(expression, **kwargs):
        result = _orig_roll(expression, **kwargs)
        if expression.startswith("1d20"):
            _call_count[0] += 1
            # First initiative roll = hero (low), second = captain (high)
            from dice import DiceRoll
            total = 1 if _call_count[0] == 1 else 25
            return DiceRoll(expression=expression, total=total, rolls=[total],
                            is_d20=True, natural=total)
        return result

    monkeypatch.setattr(server.dice_mod, "roll", _rigged_roll)

    cid = server.create_campaign("Captain Brief Test")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=20)["id"]
    res = server.spawn_monster(cid, "Bandit Captain")
    captain_id = res["spawned"][0]["id"]

    sc = server.start_combat(cid, [pc, captain_id])
    # Captain has higher initiative → captain is turn_index=0 (first).
    # next_turn → advances to the PC's turn. Captain is outgoing (monster) → no guard.
    nt1 = server.next_turn(cid)
    # PC is now current — must act or pass before next_turn (#160 enforcement).
    server.use_action(cid, pc, "skip")
    # next_turn again → wraps back to the captain (new round).
    nt2 = server.next_turn(cid)

    # Identify which response belongs to the captain
    captain_brief = None
    for nt in (nt1, nt2):
        if nt.get("turn_brief", {}).get("name", "").startswith("Bandit Captain"):
            captain_brief = nt["turn_brief"]
            break
    assert captain_brief is not None, (
        f"Expected one of the next_turn responses to carry the Bandit Captain's brief; "
        f"got: {[nt.get('turn_brief', {}).get('name') for nt in (nt1, nt2)]}"
    )
    atk = captain_brief["attack"]
    assert atk["attacks_per_turn"] == 2, (
        f"Bandit Captain must have attacks_per_turn=2 in turn_brief; got {atk['attacks_per_turn']}"
    )
    assert len(atk["attacks"]) >= 2, "Bandit Captain should have at least 2 attack options"
    for a in atk["attacks"]:
        assert isinstance(a["to_hit"], int) and a["to_hit"] > 0
        assert a.get("damage"), f"attack entry missing damage: {a}"


def test_next_turn_brief_pc_attack_numbers(tmp_path, monkeypatch):
    """PC turn_brief carries melee/ranged bonuses derived from the sheet (#166 PC path)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("PC Brief Test")["id"]
    # Fighter L2 with STR 16 (+3), DEX 14 (+2), prof=2 → melee=+5, ranged=+4
    pc = server.create_character(
        cid, "Ren", kind="player", max_hp=20,
        class_name="Fighter", level=2, apply_srd_defaults=True,
        abilities={"strength": 16, "dexterity": 14},
    )["id"]
    monster = server.create_character(cid, "Dummy", kind="monster", max_hp=5)["id"]

    server.start_combat(cid, [pc, monster])
    # Call next_turn until we land on the PC. Pass the outgoing PC if needed (#160 enforcement).
    cur = server.get_state(cid)["current_turn"]
    if server.get_character(cid, cur)["kind"] in ("player", "companion"):
        server.use_action(cid, cur, "skip")
    nt = server.next_turn(cid)
    # If the first next_turn is the monster, pass the monster's outgoing turn and advance once more.
    if nt.get("turn_brief", {}).get("kind") != "player":
        cur2 = server.get_state(cid)["current_turn"]
        if server.get_character(cid, cur2)["kind"] in ("player", "companion"):
            server.use_action(cid, cur2, "skip")
        nt = server.next_turn(cid)

    brief = nt.get("turn_brief", {})
    assert brief.get("kind") == "player", f"Expected PC turn; got kind={brief.get('kind')}"
    atk = brief["attack"]
    assert "melee_attack_bonus" in atk
    assert "ranged_attack_bonus" in atk
    assert "attacks_per_action" in atk
    # Prof=2 + STR mod=3 = melee +5; prof=2 + DEX mod=2 = ranged +4
    assert atk["melee_attack_bonus"] == 5, (
        f"Expected melee_attack_bonus=5 (prof2+STR3); got {atk['melee_attack_bonus']}"
    )
    assert atk["ranged_attack_bonus"] == 4, (
        f"Expected ranged_attack_bonus=4 (prof2+DEX2); got {atk['ranged_attack_bonus']}"
    )


def test_next_turn_brief_pc_with_action_surge(tmp_path, monkeypatch):
    """Fighter with untouched Action Surge shows it in turn_brief.resources (#166)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Action Surge Brief Test")["id"]
    pc = server.create_character(
        cid, "Ren", kind="player", max_hp=20,
        class_name="Fighter", level=2, apply_srd_defaults=True,
        abilities={"strength": 16, "dexterity": 14},
    )["id"]
    monster = server.create_character(cid, "Dummy", kind="monster", max_hp=5)["id"]

    server.start_combat(cid, [pc, monster])
    # Find the PC's turn in next_turn responses. Pass outgoing PCs (#160 enforcement).
    cur = server.get_state(cid)["current_turn"]
    if server.get_character(cid, cur)["kind"] in ("player", "companion"):
        server.use_action(cid, cur, "skip")
    nt = server.next_turn(cid)
    if nt.get("turn_brief", {}).get("kind") != "player":
        cur2 = server.get_state(cid)["current_turn"]
        if server.get_character(cid, cur2)["kind"] in ("player", "companion"):
            server.use_action(cid, cur2, "skip")
        nt = server.next_turn(cid)

    brief = nt.get("turn_brief", {})
    assert brief.get("kind") == "player"
    resources = brief.get("resources", {})
    assert "action_surge" in resources, (
        f"Fighter L2 with untouched Action Surge must appear in turn_brief.resources; "
        f"got resources={resources}"
    )
    assert resources["action_surge"]["remaining"] == 1


def test_next_turn_brief_absent_when_combat_not_active(tmp_path, monkeypatch):
    """turn_brief must NOT appear when there is no active combat (additive guarantee)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("No Combat Test")["id"]
    a = server.create_character(cid, "Hero", kind="player", max_hp=20)["id"]
    b = server.create_character(cid, "Foe", kind="monster", max_hp=5)["id"]

    server.start_combat(cid, [a, b])
    server.end_combat(cid)

    import pytest
    with pytest.raises(ValueError):
        server.next_turn(cid)  # must raise when combat is not active


# =========================================================================
# Issue #210: multi-component damage in attack()
# Issue #211: Multiattack COMPOSITION (which attacks, not just count)
# =========================================================================


# --- #210 / #211 pure parser units (no I/O) ---


def test_parse_damage_components_ghoul_bite():
    """The Ghoul Bite desc -> ordered [piercing 1d6+2, necrotic 1d6] components (#210)."""
    import server

    desc = "Melee Attack Roll: +4, reach 5 ft. 5 (1d6 + 2) Piercing damage plus 3 (1d6) Necrotic damage."
    comps = server._parse_damage_components(desc)
    assert comps == [
        {"dice": "1d6+2", "type": "piercing"},
        {"dice": "1d6", "type": "necrotic"},
    ]


def test_parse_attack_action_attaches_damage_rolls_only_when_multi_type():
    """A multi-type attack carries damage_rolls; a single-type attack does NOT (no churn)."""
    import server

    bite = server._parse_attack_action({
        "name": "Bite", "action_type": "ACTION",
        "desc": "Melee Attack Roll: +4, reach 5 ft. 5 (1d6 + 2) Piercing damage plus 3 (1d6) Necrotic damage.",
    })
    assert bite["damage"] == "1d6+2" and bite["damage_type"] == "piercing"
    assert bite["damage_rolls"] == [
        {"dice": "1d6+2", "type": "piercing"},
        {"dice": "1d6", "type": "necrotic"},
    ]
    claw = server._parse_attack_action({
        "name": "Claw", "action_type": "ACTION",
        "desc": "Melee Attack Roll: +4, reach 5 ft. 4 (1d4 + 2) Slashing damage. If the target...",
    })
    assert claw["damage"] == "1d4+2" and claw["damage_type"] == "slashing"
    assert "damage_rolls" not in claw  # single-type: no multi-component surface


@pytest.mark.parametrize(
    "desc,expected",
    [
        ("The ghoul makes two Bite attacks.", ["Bite", "Bite"]),
        ("It makes one Claw attack and one Bite attack.", ["Claw", "Bite"]),
        (
            "The creature makes one Ram attack, one Bite attack, and one Claw attack.",
            ["Ram", "Bite", "Claw"],
        ),
        ("The dragon makes three Rend attacks.", ["Rend", "Rend", "Rend"]),
    ],
)
def test_parse_multiattack_composition(desc, expected):
    """Multiattack desc -> ordered list of the SPECIFIC attack names, with repeats (#211)."""
    import server

    assert server._parse_multiattack_composition(desc) == expected


def test_parse_multiattack_composition_unparseable_returns_empty():
    """A Multiattack with no '<count> <name> attack(s)' clause degrades to [] (caller
    falls back to count-only surfacing — never aborts)."""
    import server

    assert server._parse_multiattack_composition("The creature uses its dreadful presence.") == []


# --- #211 end-to-end: monster_combat surfaces the Ghoul's two-Bite composition ---


def test_monster_combat_surfaces_ghoul_multiattack_composition(tmp_path, monkeypatch):
    """The Ghoul's Multiattack is 'two Bite attacks' — monster_combat must surface the
    SPECIFIC sequence [Bite, Bite] (each with its piercing+necrotic damage_rolls), not
    just the count, so the DM issues two Bites rather than improvising Bite+Claw (#211).
    The Claw (a separate single-action option with the paralysis rider) stays in the
    general attacks list but is NOT part of the Multiattack sequence."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Ghoul Multiattack #211")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=30)["id"]
    res = server.spawn_monster(cid, "Ghoul")
    ghoul_id = res["spawned"][0]["id"]

    view = server.start_combat(cid, [pc, ghoul_id])
    entries = {e["id"]: e for e in view["monster_combat"]}
    g = entries[ghoul_id]

    assert g["attacks_per_turn"] == 2
    assert "multiattack" in g, "Ghoul monster_combat must carry the Multiattack composition"
    seq = g["multiattack"]["sequence"]
    assert [a["name"] for a in seq] == ["Bite", "Bite"], (
        f"Ghoul Multiattack should be two Bites; got {[a['name'] for a in seq]}"
    )
    # Each surfaced Bite carries its full multi-component damage (#210 ties into #211).
    for bite in seq:
        assert bite["damage_rolls"] == [
            {"dice": "1d6+2", "type": "piercing"},
            {"dice": "1d6", "type": "necrotic"},
        ], f"each Bite must surface piercing+necrotic damage_rolls; got {bite.get('damage_rolls')}"
    # The Claw is still available as a separate attack option, but not in the sequence.
    attack_names = {a["name"] for a in g["attacks"]}
    assert "Claw" in attack_names and "Bite" in attack_names


def test_turn_brief_surfaces_ghoul_multiattack_composition(tmp_path, monkeypatch):
    """The per-turn brief (the surface the DM reads each turn) carries the Ghoul's
    Multiattack composition when it is the Ghoul's turn (#211 + #166)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    # Rig initiative so the Ghoul goes first: hero rolls 1, ghoul rolls 25.
    _n = [0]
    _orig = server.dice_mod.roll

    def _rigged(expression, **kw):
        if expression.startswith("1d20"):
            _n[0] += 1
            from dice import DiceRoll
            total = 1 if _n[0] == 1 else 25
            return DiceRoll(expression=expression, total=total, rolls=[total], is_d20=True, natural=total)
        return _orig(expression, **kw)

    monkeypatch.setattr(server.dice_mod, "roll", _rigged)

    cid = server.create_campaign("Ghoul Brief #211")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=30)["id"]
    res = server.spawn_monster(cid, "Ghoul")
    ghoul_id = res["spawned"][0]["id"]

    server.start_combat(cid, [pc, ghoul_id])
    # Ghoul first → next_turn advances to PC; pass PC; next_turn wraps back to Ghoul.
    nt1 = server.next_turn(cid)
    server.use_action(cid, pc, "skip")
    nt2 = server.next_turn(cid)
    ghoul_brief = next(
        (nt["turn_brief"] for nt in (nt1, nt2)
         if nt.get("turn_brief", {}).get("name", "").startswith("Ghoul")),
        None,
    )
    assert ghoul_brief is not None, "expected a Ghoul turn_brief"
    atk = ghoul_brief["attack"]
    assert atk["attacks_per_turn"] == 2
    assert "multiattack" in atk, "Ghoul turn_brief must carry the Multiattack composition"
    assert [a["name"] for a in atk["multiattack"]["sequence"]] == ["Bite", "Bite"]


def test_monster_combat_no_multiattack_key_for_single_attack_monster(tmp_path, monkeypatch):
    """A monster with NO Multiattack (Wolf) has no 'multiattack' composition key —
    additive: the count-only surfacing is unchanged for non-Multiattack monsters (#211)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Wolf No-Multiattack #211")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=20)["id"]
    res = server.spawn_monster(cid, "Wolf")
    wolf_id = res["spawned"][0]["id"]

    view = server.start_combat(cid, [pc, wolf_id])
    w = {e["id"]: e for e in view["monster_combat"]}[wolf_id]
    assert w["attacks_per_turn"] == 1
    assert "multiattack" not in w, "no-Multiattack monster must not gain a composition key"


# --- #210 end-to-end through attack() ---


def _crit_then_fixed(component_total: int = 5):
    """A dice.roll stub: every d20 is a natural-20 crit; every damage expr returns
    `component_total` and records the expression it was asked to roll (so a test can
    assert each component's dice were crit-doubled)."""
    from dice import DiceRoll

    seen: list[str] = []

    def _roll(expression, advantage=False, disadvantage=False, seed=None):
        if expression.startswith("1d20"):
            return DiceRoll(expression=expression, total=30, rolls=[20], is_d20=True, natural=20, crit=True)
        seen.append(expression)
        return DiceRoll(expression=expression, total=component_total, rolls=[component_total], detail=f"{expression}={component_total}")

    return _roll, seen


def test_attack_multi_component_rolls_and_crit_doubles_each(tmp_path, monkeypatch):
    """attack(damage_rolls=[...]) rolls BOTH components, crit-doubles EACH component's
    dice (leaving flat mods), and surfaces a per-component breakdown (#210)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    roll, seen = _crit_then_fixed(component_total=5)
    monkeypatch.setattr(server.dice_mod, "roll", roll)

    cid = server.create_campaign("Multi-component crit #210")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=40, armor_class=10)["id"]
    foe = server.create_character(cid, "Foe", kind="monster", max_hp=80, armor_class=5)["id"]
    server.start_combat(cid, [hero, foe])
    cur = server.get_state(cid)["current_turn"]
    other = foe if cur == hero else hero

    r = server.attack(
        cid, cur, other, attack_bonus=4,
        damage_rolls=[{"dice": "1d6+2", "type": "piercing"}, {"dice": "1d6", "type": "necrotic"}],
    )
    assert r["crit"] is True and r["hit"] is True
    # Each component's DICE doubled (flat +2 preserved); both components were rolled.
    assert seen == ["2d6+2", "2d6"], f"expected crit-doubled component exprs; got {seen}"
    dmg = r["damage"]
    assert dmg["type"] == "piercing + necrotic"
    assert dmg["total"] == 10  # 5 + 5 pre-resistance
    assert [(c["type"], c["total"]) for c in dmg["components"]] == [("piercing", 5), ("necrotic", 5)]
    # Both components landed (no resistance): 10 to HP.
    assert server.get_character(cid, other)["current_hp"] == 70


def test_attack_multi_component_per_type_resistance_halves_only_matching(tmp_path, monkeypatch):
    """attack(damage_rolls=[...]) against a target resistant to one component's type
    halves ONLY that component (#210). necrotic-resistant: 5 piercing + (5 necrotic ->2)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    from dice import DiceRoll

    def roll(expression, advantage=False, disadvantage=False, seed=None):
        if expression.startswith("1d20"):
            return DiceRoll(expression=expression, total=19, rolls=[15], is_d20=True, natural=15, crit=False)
        return DiceRoll(expression=expression, total=5, rolls=[5], detail="x")

    monkeypatch.setattr(server.dice_mod, "roll", roll)

    cid = server.create_campaign("Multi-component resist #210")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=40, armor_class=10)["id"]
    foe = server.create_character(cid, "Foe", kind="monster", max_hp=80, armor_class=5)["id"]
    server.update_character(cid, foe, {"damage_resistances": ["necrotic"]})
    server.start_combat(cid, [hero, foe])
    cur = server.get_state(cid)["current_turn"]
    other = foe if cur == hero else hero

    r = server.attack(
        cid, cur, other, attack_bonus=4,
        damage_rolls=[{"dice": "1d6+2", "type": "piercing"}, {"dice": "1d6", "type": "necrotic"}],
    )
    assert r["damage"]["total"] == 10  # pre-resistance rolled sum
    assert r["damage"]["applied_total"] == 7  # 5 piercing + 5//2 necrotic
    applied = {c["type"]: c["applied"] for c in r["damage"]["components"]}
    assert applied == {"piercing": 5, "necrotic": 2}
    assert server.get_character(cid, other)["current_hp"] == 73  # 80 - 7


def test_attack_single_damage_dice_unchanged(tmp_path, monkeypatch):
    """A single-damage_dice attack (no damage_rolls) is byte-identical to before:
    result["damage"] keeps its scalar {total,type,expr,detail} shape, no components (#210)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    from dice import DiceRoll

    def roll(expression, advantage=False, disadvantage=False, seed=None):
        if expression.startswith("1d20"):
            return DiceRoll(expression=expression, total=19, rolls=[15], is_d20=True, natural=15, crit=False)
        return DiceRoll(expression=expression, total=7, rolls=[7], detail="1d6+2=7")

    monkeypatch.setattr(server.dice_mod, "roll", roll)

    cid = server.create_campaign("Single dice unchanged #210")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=40, armor_class=10)["id"]
    foe = server.create_character(cid, "Foe", kind="monster", max_hp=80, armor_class=5)["id"]
    server.start_combat(cid, [hero, foe])
    cur = server.get_state(cid)["current_turn"]
    other = foe if cur == hero else hero

    r = server.attack(cid, cur, other, attack_bonus=4, damage_dice="1d6+2", damage_type="slashing")
    assert sorted(r["damage"].keys()) == ["detail", "expr", "total", "type"]
    assert r["damage"] == {"total": 7, "type": "slashing", "expr": "1d6+2", "detail": "1d6+2=7"}
    assert "components" not in r["damage"]
    assert server.get_character(cid, other)["current_hp"] == 73


def test_attack_requires_some_damage_spec(tmp_path, monkeypatch):
    """attack() with neither damage_dice nor damage_rolls is rejected up front with a
    clear message (damage_dice became optional for the multi-component path) — no
    state change, no confusing deep dice error."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("No damage spec #210")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=40, armor_class=10)["id"]
    foe = server.create_character(cid, "Foe", kind="monster", max_hp=80, armor_class=5)["id"]
    server.start_combat(cid, [hero, foe])
    cur = server.get_state(cid)["current_turn"]
    other = foe if cur == hero else hero

    hp_before = server.get_character(cid, other)["current_hp"]
    with pytest.raises(ValueError, match="attack needs damage"):
        server.attack(cid, cur, other, attack_bonus=4)
    # No state change: the action economy did not advance and HP is untouched.
    assert server.get_character(cid, other)["current_hp"] == hp_before
    assert server.get_state(cid)["current_turn"] == cur


# =========================================================================
# Issue #180: monster Multiattack enforcement
# =========================================================================
# These tests verify that the engine now ENFORCES the stat-block Multiattack
# count for monsters, while leaving PC Extra-Attack / Action-Surge completely
# unchanged.


# --- pure combat.py unit tests (no I/O, no campaign) ---


@pytest.mark.parametrize(
    "extra,surge,multiattack,expected",
    [
        (0, 0, 0, 1),   # unchanged: vanilla PC with no extras
        (0, 0, 2, 2),   # Multiattack=2, no Extra Attack -> 2
        (0, 0, 3, 3),   # Multiattack=3 (e.g. vampire)
        (1, 0, 0, 2),   # Extra Attack, no Multiattack -> unchanged
        (1, 0, 2, 2),   # Extra Attack=1 AND Multiattack=2 -> max(2,2)=2 (no double-count)
        (2, 0, 2, 3),   # Extra Attack=2 (ceil=3) beats Multiattack=2 -> 3
        (0, 1, 2, 4),   # Multiattack=2 + Action Surge -> 2*(1+1)=4
        (0, 1, 0, 2),   # unchanged: vanilla + Action Surge
    ],
)
def test_attacks_allowed_with_multiattack(extra, surge, multiattack, expected):
    """attacks_allowed respects multiattack ceiling; multiattack=0 is byte-identical to old behaviour."""
    assert combat.attacks_allowed(extra, surge, multiattack) == expected


def test_check_action_attack_multiattack_rejection_message():
    """When the Multiattack budget is exhausted the rejection names Multiattack, not Extra Attack."""
    ok, reason = combat.check_action_attack(
        is_current=True, attacks_made=2, extra_attacks=0, surge_actions=0, multiattack=2
    )
    assert ok is False
    assert "Multiattack" in reason
    assert "2" in reason


def test_check_action_attack_multiattack_allows_within_budget():
    """First two action-attacks are allowed for a Multiattack=2 creature."""
    ok1, _ = combat.check_action_attack(
        is_current=True, attacks_made=0, extra_attacks=0, surge_actions=0, multiattack=2
    )
    ok2, _ = combat.check_action_attack(
        is_current=True, attacks_made=1, extra_attacks=0, surge_actions=0, multiattack=2
    )
    assert ok1 is True and ok2 is True


# --- end-to-end through attack() via spawn_monster ---


def test_multiattack_monster_makes_two_attacks_third_rejected(tmp_path, monkeypatch):
    """A Bandit Captain (Multiattack=2) can make 2 action-attacks in one turn; the 3rd is rejected.

    This is the core regression guard for issue #180: before the fix the 2nd attack
    was rejected ("one Attack action grants a single attack without the Extra Attack
    feature"). After the fix, the engine reads the stat-block Multiattack count and
    allows exactly 2 attacks per turn.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Multiattack Enforcement #180")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=40, armor_class=10)["id"]
    res = server.spawn_monster(cid, "Bandit Captain")
    captain_id = res["spawned"][0]["id"]

    # Force the Bandit Captain to go first by making its initiative enormous.
    # We do this by monkeypatching dice.roll to return 20 for d20 rolls so start_combat
    # initiative rolls are high; then next_turn places it first.
    # Simpler: start with both, then next_turn until the captain is current.
    server.start_combat(cid, [captain_id, pc])
    # Advance turns until the Bandit Captain is current. Pass outgoing PCs (#160).
    for _ in range(10):
        state = server.get_state(cid)
        if state["current_turn"] == captain_id:
            break
        cur = state["current_turn"]
        if server.get_character(cid, cur)["kind"] in ("player", "companion"):
            server.use_action(cid, cur, "skip")
        server.next_turn(cid)
    assert server.get_state(cid)["current_turn"] == captain_id, (
        "Could not make Bandit Captain the current combatant — adjust test setup"
    )

    # First attack must succeed.
    a1 = server.attack(cid, captain_id, pc, attack_bonus=5, damage_dice="1d6+3")
    assert a1.get("attacks_made_this_turn") == 1
    assert a1.get("attacks_allowed_this_turn") == 2, (
        f"Bandit Captain should be allowed 2 attacks/turn; got {a1.get('attacks_allowed_this_turn')}"
    )

    # Second attack must also succeed (was blocked before the fix).
    a2 = server.attack(cid, captain_id, pc, attack_bonus=5, damage_dice="1d6+3")
    assert a2.get("attacks_made_this_turn") == 2

    # Third attack must be rejected with a Multiattack-specific message.
    import pytest as _pytest
    with _pytest.raises(ValueError, match="[Mm]ultiattack"):
        server.attack(cid, captain_id, pc, attack_bonus=5, damage_dice="1d6+3")


def test_pc_no_extra_attack_still_capped_at_one(tmp_path, monkeypatch):
    """A plain PC (no Extra Attack, no Multiattack) is still capped at 1 action-attack per turn.

    Regression guard: the multiattack change must not silently elevate PC attack budgets.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    import pytest as _pytest

    cid, cur, other, _ids = _combat_with_known_current(server)
    # Ensure no Extra Attack on this PC.
    server.update_character(cid, cur, {"extra_attacks": 0})

    server.attack(cid, cur, other, attack_bonus=3, damage_dice="1d6")  # first: ok
    with _pytest.raises(ValueError, match="already attacked this turn"):
        server.attack(cid, cur, other, attack_bonus=3, damage_dice="1d6")  # second: rejected


def test_pc_extra_attack_still_makes_two(tmp_path, monkeypatch):
    """A PC with extra_attacks=1 (Extra Attack) makes 2 action-attacks; the 3rd is rejected.

    Regression guard: Extra Attack path unchanged by the Multiattack change.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    import pytest as _pytest

    cid, cur, other, _ids = _combat_with_known_current(server)
    server.update_character(cid, cur, {"extra_attacks": 1})

    server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")  # 1st ok
    server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")  # 2nd ok
    with _pytest.raises(ValueError, match="no attacks left"):
        server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")  # 3rd rejected


def test_action_surge_still_grants_extra_action_for_pc(tmp_path, monkeypatch):
    """Action Surge still grants a 2nd action's worth of attacks for a PC (unchanged).

    Regression guard: surge path unchanged by the Multiattack change.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    import pytest as _pytest

    cid, cur, other, _ids = _combat_with_known_current(server)
    server.set_class_resource(cid, cur, "action_surge", max=1, recharge="short")

    server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")  # 1st ok
    # Without surge: 2nd is rejected.
    with _pytest.raises(ValueError):
        server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    # Spend Action Surge -> 2nd action unlocked.
    server.use_resource(cid, cur, "action_surge")
    a2 = server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    assert a2.get("attacks_allowed_this_turn") == 2 and a2.get("attacks_made_this_turn") == 2


def test_multiattack_zero_path_unchanged(tmp_path, monkeypatch):
    """A monster without Multiattack (e.g. Wolf) is capped at 1 action-attack (multiattack=0 path).

    Regression guard: the _attacker_multiattack_count fallback returns 0 for
    non-Multiattack monsters and the cap stays at 1.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    import pytest as _pytest

    cid = server.create_campaign("Wolf #180")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=40, armor_class=10)["id"]
    res = server.spawn_monster(cid, "Wolf")
    wolf_id = res["spawned"][0]["id"]

    server.start_combat(cid, [wolf_id, pc])
    # Advance until the wolf is current. Pass any PC outgoing combatant first (#160).
    for _ in range(10):
        state = server.get_state(cid)
        if state["current_turn"] == wolf_id:
            break
        cur = state["current_turn"]
        if server.get_character(cid, cur)["kind"] in ("player", "companion"):
            server.use_action(cid, cur, "skip")
        server.next_turn(cid)
    assert server.get_state(cid)["current_turn"] == wolf_id

    server.attack(cid, wolf_id, pc, attack_bonus=4, damage_dice="2d4+2")  # 1st ok
    # Wolf has no Multiattack -> 2nd is rejected.
    with _pytest.raises(ValueError, match="already attacked this turn"):
        server.attack(cid, wolf_id, pc, attack_bonus=4, damage_dice="2d4+2")


# =========================================================================
# Issue #160/#166: Round-1 turn-skip enforcement in next_turn
# =========================================================================
# next_turn must BLOCK advancing past a PC/companion who can act but has not
# acted or declared a pass this turn. Monsters/NPCs are never blocked.


def test_next_turn_blocks_pc_who_has_not_acted(tmp_path, monkeypatch):
    """BLOCK: next_turn raises when the outgoing PC has not acted or passed.

    This is the core Round-1 skip defect — the DM calls next_turn immediately
    after start_combat, silently skipping the highest-initiative PC's turn.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    import pytest as _pytest

    cid = server.create_campaign("Turn Skip Block #160")["id"]
    # Force PC to go first so the outgoing combatant on the first next_turn is the PC.
    pc = server.create_character(cid, "Aria", kind="player", max_hp=20, armor_class=14)["id"]
    mob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]
    # Rig initiative so pc is always first.
    import dice as dice_mod
    _orig = dice_mod.roll
    _call = [0]
    def _rigged(expr, **kw):
        r = _orig(expr, **kw)
        if expr.startswith("1d20"):
            _call[0] += 1
            from dice import DiceRoll
            total = 25 if _call[0] == 1 else 1
            return DiceRoll(expression=expr, total=total, rolls=[total], is_d20=True, natural=total)
        return r
    monkeypatch.setattr(server.dice_mod, "roll", _rigged)

    server.start_combat(cid, [pc, mob])
    assert server.get_state(cid)["current_turn"] == pc, "PC must be first for this test"

    # next_turn with no action from PC must be rejected
    with _pytest.raises(ValueError, match="has not acted this turn"):
        server.next_turn(cid)


def test_next_turn_allows_pc_after_attack(tmp_path, monkeypatch):
    """ALLOW: next_turn succeeds when the outgoing PC attacked this turn."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Turn Skip After Attack #160")["id"]
    pc = server.create_character(cid, "Aria", kind="player", max_hp=20, armor_class=14)["id"]
    mob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]

    # Rig PC first
    _call = [0]
    _orig = server.dice_mod.roll
    def _rigged(expr, **kw):
        r = _orig(expr, **kw)
        if expr.startswith("1d20"):
            _call[0] += 1
            from dice import DiceRoll
            total = 25 if _call[0] == 1 else 1
            return DiceRoll(expression=expr, total=total, rolls=[total], is_d20=True, natural=total)
        return r
    monkeypatch.setattr(server.dice_mod, "roll", _rigged)

    server.start_combat(cid, [pc, mob])
    assert server.get_state(cid)["current_turn"] == pc

    server.attack(cid, pc, mob, attack_bonus=5, damage_dice="1d8+3")  # PC acted
    view = server.next_turn(cid)  # must NOT raise
    assert view["active"] is True


def test_next_turn_allows_pc_after_use_action(tmp_path, monkeypatch):
    """ALLOW: next_turn succeeds when the outgoing PC declared use_action(kind='action')."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Turn Skip After use_action #160")["id"]
    pc = server.create_character(cid, "Aria", kind="player", max_hp=20, armor_class=14)["id"]
    mob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]

    _call = [0]
    _orig = server.dice_mod.roll
    def _rigged(expr, **kw):
        r = _orig(expr, **kw)
        if expr.startswith("1d20"):
            _call[0] += 1
            from dice import DiceRoll
            total = 25 if _call[0] == 1 else 1
            return DiceRoll(expression=expr, total=total, rolls=[total], is_d20=True, natural=total)
        return r
    monkeypatch.setattr(server.dice_mod, "roll", _rigged)

    server.start_combat(cid, [pc, mob])
    assert server.get_state(cid)["current_turn"] == pc

    server.use_action(cid, pc, "action")  # Dodge/Dash/Disengage/Ready/Help declared
    view = server.next_turn(cid)  # must NOT raise
    assert view["active"] is True


def test_next_turn_allows_pc_after_skip(tmp_path, monkeypatch):
    """ALLOW: use_action(kind='skip') is the pass escape — next_turn succeeds after it."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Turn Skip Pass Escape #160")["id"]
    pc = server.create_character(cid, "Aria", kind="player", max_hp=20, armor_class=14)["id"]
    mob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]

    _call = [0]
    _orig = server.dice_mod.roll
    def _rigged(expr, **kw):
        r = _orig(expr, **kw)
        if expr.startswith("1d20"):
            _call[0] += 1
            from dice import DiceRoll
            total = 25 if _call[0] == 1 else 1
            return DiceRoll(expression=expr, total=total, rolls=[total], is_d20=True, natural=total)
        return r
    monkeypatch.setattr(server.dice_mod, "roll", _rigged)

    server.start_combat(cid, [pc, mob])
    assert server.get_state(cid)["current_turn"] == pc

    skip_result = server.use_action(cid, pc, "skip")
    assert skip_result["ok"] is True, f"skip must be accepted; got {skip_result}"
    view = server.next_turn(cid)  # must NOT raise after skip
    assert view["active"] is True


def test_next_turn_allows_incapacitated_pc(tmp_path, monkeypatch):
    """NO BLOCK: an incapacitated (stunned/unconscious) PC is advanced without error.

    A PC who CANNOT act must never trigger the skip-guard — they're already unable
    to take an action and forcing a pass would be wrong.
    """
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Incapacitated PC #160")["id"]
    pc = server.create_character(cid, "Aria", kind="player", max_hp=20, armor_class=14)["id"]
    mob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]

    _call = [0]
    _orig = server.dice_mod.roll
    def _rigged(expr, **kw):
        r = _orig(expr, **kw)
        if expr.startswith("1d20"):
            _call[0] += 1
            from dice import DiceRoll
            total = 25 if _call[0] == 1 else 1
            return DiceRoll(expression=expr, total=total, rolls=[total], is_d20=True, natural=total)
        return r
    monkeypatch.setattr(server.dice_mod, "roll", _rigged)

    server.start_combat(cid, [pc, mob])
    assert server.get_state(cid)["current_turn"] == pc

    # Stun the PC — incapacitated, cannot act
    server.add_condition(cid, pc, "stunned")
    view = server.next_turn(cid)  # must NOT raise — incapacitated PC is not blocked
    assert view["active"] is True


def test_next_turn_allows_downed_pc(tmp_path, monkeypatch):
    """NO BLOCK: a PC at 0 hp is advanced without error (they can't act, just death-save)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Downed PC #160")["id"]
    pc = server.create_character(cid, "Aria", kind="player", max_hp=20, armor_class=14)["id"]
    mob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]

    _call = [0]
    _orig = server.dice_mod.roll
    def _rigged(expr, **kw):
        r = _orig(expr, **kw)
        if expr.startswith("1d20"):
            _call[0] += 1
            from dice import DiceRoll
            total = 25 if _call[0] == 1 else 1
            return DiceRoll(expression=expr, total=total, rolls=[total], is_d20=True, natural=total)
        return r
    monkeypatch.setattr(server.dice_mod, "roll", _rigged)

    server.start_combat(cid, [pc, mob])
    assert server.get_state(cid)["current_turn"] == pc

    # Drop the PC to 0 — they're downed, cannot take actions
    server.apply_damage(cid, pc, 20)
    assert server.get_character(cid, pc)["current_hp"] == 0
    view = server.next_turn(cid)  # must NOT raise — 0-hp PC is not blocked
    assert view["active"] is True


def test_next_turn_monster_no_action_advances_freely(tmp_path, monkeypatch):
    """NO BLOCK: a monster who took no action advances without error — guard is PC-only."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    cid = server.create_campaign("Monster Free Advance #160")["id"]
    pc = server.create_character(cid, "Aria", kind="player", max_hp=20, armor_class=14)["id"]
    mob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]

    # Rig monster first
    _call = [0]
    _orig = server.dice_mod.roll
    def _rigged(expr, **kw):
        r = _orig(expr, **kw)
        if expr.startswith("1d20"):
            _call[0] += 1
            from dice import DiceRoll
            total = 25 if _call[0] == 1 else 1
            return DiceRoll(expression=expr, total=total, rolls=[total], is_d20=True, natural=total)
        return r
    monkeypatch.setattr(server.dice_mod, "roll", _rigged)

    server.start_combat(cid, [mob, pc])
    # mob is first (rigged higher initiative)
    assert server.get_state(cid)["current_turn"] == mob

    # Monster takes no action — next_turn must NOT raise
    view = server.next_turn(cid)
    assert view["active"] is True


# =========================================================================
# Battle Master DAMAGE maneuver (#213): use_resource(superiority_dice, maneuver=…)
# rolls the die and the NEXT attack folds it into that strike's damage.
# =========================================================================


def _rigged_roller(d20_total: int, fixed: dict[str, int]):
    """A deterministic dice_mod.roll: d20 expressions return `d20_total` (hit/miss
    control); any other expression returns a fixed total looked up by its (space-stripped)
    text, defaulting to 0 so an unexpected expr is loud rather than silently '6'."""
    from dice import DiceRoll

    def _roll(expr, advantage=False, disadvantage=False, seed=None):
        e = expr.replace(" ", "").lower()
        if e.startswith("1d20"):
            return DiceRoll(
                expression=expr, total=d20_total, rolls=[d20_total - 5],
                modifier=5, detail=f"{expr}={d20_total}", is_d20=True,
                natural=max(1, min(20, d20_total - 5)),
            )
        total = fixed.get(e, 0)
        return DiceRoll(expression=expr, total=total, rolls=[total], detail=f"{expr}[{total}]={total}")

    return _roll


def _bm_combat(server, monkeypatch, tmp_path, *, die_total: int, weapon_total: int, d20: int = 25):
    """Start a Hero-vs-Goblin fight with the Hero current, a 6-die superiority pool (d8),
    and a rigged roller: 1d8 -> `die_total`, 1d6+3 -> `weapon_total`, d20 -> `d20`."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Battle Master")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=20, armor_class=12)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=40, armor_class=10)["id"]
    server.set_class_resource(cid, hero, "superiority_dice", max=6, recharge="short", size="d8")
    # Hero must be the current combatant to take an action-attack; rig initiative so the
    # higher-init Hero goes first (start_combat rolls 1d20+init for each).
    server.start_combat(cid, [hero, gob])
    if server.get_state(cid)["current_turn"] != hero:
        server.next_turn(cid)  # 2-combatant order — advance to the Hero
    assert server.get_state(cid)["current_turn"] == hero
    monkeypatch.setattr(server.dice_mod, "roll", _rigged_roller(d20, {"1d8": die_total, "1d6+3": weapon_total}))
    return cid, hero, gob


def test_damage_maneuver_die_rolled_on_spend_and_pending(tmp_path, monkeypatch):
    # use_resource(superiority_dice, maneuver=…) ROLLS the die and stashes a pending bonus.
    import server
    cid, hero, gob = _bm_combat(server, monkeypatch, tmp_path, die_total=6, weapon_total=7)
    spent = server.use_resource(cid, hero, "superiority_dice", maneuver="Trip Attack")
    assert spent["ok"] is True
    assert spent["remaining"] == 5  # one die spent
    md = spent["maneuver_damage"]
    assert md["maneuver"] == "Trip Attack" and md["die"] == "1d8" and md["rolled"] == 6
    # The rolled bonus is now pending on the character.
    pdb = server.get_character(cid, hero)["pending_damage_bonus"]
    assert pdb is not None and pdb["amount"] == 6 and pdb["source"] == "Trip Attack"


def test_damage_maneuver_adds_die_to_triggering_attack(tmp_path, monkeypatch):
    # The core fix: after spending a die for a maneuver, the next attack's damage STRICTLY
    # exceeds the weapon alone by the rolled die (7 weapon + 6 die = 13).
    import server
    cid, hero, gob = _bm_combat(server, monkeypatch, tmp_path, die_total=6, weapon_total=7)
    server.use_resource(cid, hero, "superiority_dice", maneuver="Trip Attack")
    res = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3", damage_type="slashing")
    assert res["hit"] is True
    # weapon (7) + superiority die (6) folded into ONE hit.
    assert res["damage"]["total"] == 13
    assert res["damage"]["applied_total"] == 13  # no resistance on the goblin
    assert res["maneuver_damage"]["rolled"] == 6 and res["maneuver_damage"]["applied"] is True
    # The target actually took the full 13 (40 - 13 = 27).
    assert server.get_character(cid, gob)["current_hp"] == 27
    # >= +1 over the weapon alone (the regression guard from the issue).
    assert res["damage"]["total"] >= 7 + 1


def test_damage_maneuver_strictly_exceeds_weapon_max(tmp_path, monkeypatch):
    # Sharper form of the issue's assert: a maneuver strike must exceed even the MAX of the
    # weapon+mod alone. Weapon 1d6+3 maxes at 9; with the die it lands at 9 + 1 = 10 (die=1).
    import server
    cid, hero, gob = _bm_combat(server, monkeypatch, tmp_path, die_total=1, weapon_total=9)
    server.use_resource(cid, hero, "superiority_dice", maneuver="Menacing Attack")
    res = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3", damage_type="slashing")
    weapon_max = 6 + 3  # 1d6+3
    assert res["damage"]["total"] >= weapon_max + 1


def test_normal_attack_without_maneuver_unchanged(tmp_path, monkeypatch):
    # ADDITIVE: a normal attack with no maneuver spent is exactly the weapon's damage — no
    # bonus, no maneuver_damage key, the single-type path is untouched.
    import server
    cid, hero, gob = _bm_combat(server, monkeypatch, tmp_path, die_total=6, weapon_total=7)
    res = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3", damage_type="slashing")
    assert res["hit"] is True
    assert res["damage"]["total"] == 7  # weapon only
    assert "maneuver_damage" not in res
    assert res["damage"].get("expr") == "1d6+3"  # stayed on the byte-identical single-type path
    assert server.get_character(cid, gob)["current_hp"] == 33  # 40 - 7


def test_maneuver_die_consumed_once_not_double_applied(tmp_path, monkeypatch):
    # The pending bonus is consumed by the FIRST attack only — a second attack the same
    # combat (e.g. via Extra Attack) gets the weapon alone, never the die again.
    import server
    cid, hero, gob = _bm_combat(server, monkeypatch, tmp_path, die_total=6, weapon_total=7)
    server.update_character(cid, hero, {"extra_attacks": 1})  # two attacks this action
    server.use_resource(cid, hero, "superiority_dice", maneuver="Trip Attack")
    first = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3", damage_type="slashing")
    assert first["damage"]["total"] == 13  # 7 + 6 (die applied once)
    # Pending bonus cleared after the first strike.
    assert server.get_character(cid, gob)["pending_damage_bonus"] is None
    assert server.get_character(cid, hero)["pending_damage_bonus"] is None
    second = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3", damage_type="slashing")
    assert second["damage"]["total"] == 7  # weapon ONLY — the die is gone
    assert "maneuver_damage" not in second


def test_maneuver_die_on_miss_spent_but_no_damage(tmp_path, monkeypatch):
    # A MISS consumes the declared die (it was spent at use_resource time) but adds no
    # damage — and it does NOT carry to a later attack.
    import server
    cid, hero, gob = _bm_combat(server, monkeypatch, tmp_path, die_total=6, weapon_total=7, d20=2)
    # Goblin AC bumped so the rigged low d20 misses.
    server.update_character(cid, gob, {"armor_class": 20})
    server.use_resource(cid, hero, "superiority_dice", maneuver="Trip Attack")
    res = server.attack(cid, hero, gob, attack_bonus=0, damage_dice="1d6+3", damage_type="slashing")
    assert res["hit"] is False
    assert res["maneuver_damage"]["applied"] is False
    assert server.get_character(cid, gob)["current_hp"] == 40  # untouched
    # The die did not survive to the next swing.
    assert server.get_character(cid, hero)["pending_damage_bonus"] is None


def test_maneuver_on_point_pool_refused_no_spend(tmp_path, monkeypatch):
    # A damage maneuver needs a DIE pool. Declaring one against a point pool (Ki, no `size`)
    # is refused with a clean signal and NO spend / no pending bonus.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    cid = server.create_campaign("Point pool")["id"]
    monk = server.create_character(cid, "Monk", kind="player", max_hp=20)["id"]
    server.set_class_resource(cid, monk, "ki", max=5, recharge="short")  # no size -> point pool
    out = server.use_resource(cid, monk, "ki", maneuver="Trip Attack")
    assert out["ok"] is False and "point pool" in out["error"]
    sheet = server.get_character(cid, monk)
    assert sheet["class_resources"]["ki"]["used"] == 0  # nothing spent
    assert sheet["pending_damage_bonus"] is None


def test_maneuver_die_typed_damage_respects_resistance(tmp_path, monkeypatch):
    # A maneuver with an explicit damage_type lands as that type — and a resistant target
    # halves only the maneuver component (the weapon's type is untouched). Trip 'Attack' with
    # fire damage (a homebrew flourish) on a fire-resistant foe: weapon 7 slashing (full) +
    # die 6 fire -> 3 = 10 to HP, but the rolled-sum surfaced total is 13.
    import server
    cid, hero, gob = _bm_combat(server, monkeypatch, tmp_path, die_total=6, weapon_total=7)
    server.update_character(cid, gob, {"damage_resistances": ["fire"]})
    server.use_resource(cid, hero, "superiority_dice", maneuver="Trip Attack", damage_type="fire")
    res = server.attack(cid, hero, gob, attack_bonus=5, damage_dice="1d6+3", damage_type="slashing")
    assert res["damage"]["total"] == 13  # pre-resistance rolled sum (7 + 6)
    assert res["damage"]["applied_total"] == 10  # 7 slashing + 3 (6 fire halved)
    assert server.get_character(cid, gob)["current_hp"] == 30  # 40 - 10


def test_crit_source_attributes_the_right_reason():
    """#219: crit_source names WHY a hit critted, so the DM stops narrating 'nat 20' on a
    paralyzed-target auto-crit. Pure helper; '' when not a crit."""
    healthy = mk()
    paralyzed = mk(conditions=[Condition.PARALYZED])
    unconscious = mk(conditions=[Condition.UNCONSCIOUS])
    # not a crit -> ""
    assert combat.crit_source(False, 13, False, healthy) == ""
    # a natural 20 roll-crit
    assert combat.crit_source(True, 20, True, healthy) == "nat_20"
    # a roll-crit on a lower natural (expanded crit range, e.g. Champion 19-20)
    assert combat.crit_source(True, 19, True, healthy) == "expanded_crit_range"
    # condition auto-crit (atk roll did NOT crit, but is_crit is True) -> the named condition
    assert combat.crit_source(False, 15, True, paralyzed) == "condition_paralyzed"
    assert combat.crit_source(False, 7, True, unconscious) == "condition_unconscious"
    # a roll-crit takes precedence in naming even if the target also happens to be helpless
    assert combat.crit_source(True, 20, True, paralyzed) == "nat_20"


# --- #218: monster defensive reactions (Parry) ---
def test_bestiary_parry_bonus_parses_the_ac_reaction():
    import bestiary
    parry_sb = {"actions": [
        {"name": "Multiattack", "desc": "two attacks", "action_type": "ACTION"},
        {"name": "Parry", "desc": "The captain adds 2 to its AC against one melee attack that would hit it.", "action_type": "REACTION"},
    ]}
    assert bestiary.parry_bonus(parry_sb) == 2
    # a higher-bonus variant
    assert bestiary.parry_bonus({"actions": [{"name": "Parry", "desc": "adds 4 to its AC against that melee attack", "action_type": "REACTION"}]}) == 4
    # no reaction / not a parry / empty -> 0
    assert bestiary.parry_bonus({"actions": [{"name": "Bite", "desc": "1d6 piercing", "action_type": "ACTION"}]}) == 0
    assert bestiary.parry_bonus({"actions": [{"name": "Shield", "desc": "adds 5 to its AC against a spell", "action_type": "REACTION"}]}) == 0  # not melee
    assert bestiary.parry_bonus({}) == 0
    assert bestiary.parry_bonus(None) == 0


def _attack_roll_stub(d20_total: int, d20_natural: int = 14):
    """A dice_mod.roll stub: the 1d20 attack returns a controlled (total, natural); any other
    roll (damage) is small + deterministic so the attack resolves."""
    def _roll(expression, advantage=False, disadvantage=False, seed=None):
        if expression.startswith("1d20"):
            return _ds(d20_total, d20_natural)
        return DiceRoll(expression=expression, total=4, rolls=[3], modifier=1, detail=f"{expression}[3] = 4")
    return _roll


def test_parry_flips_marginal_hit_and_is_spent_once_per_round(tmp_path, monkeypatch):
    """#218: a Bandit Captain (AC 15, Parry +2) spends its reaction to turn a MARGINAL hit
    (16, between AC 15 and AC+2=17) into a miss — but only once per round. The two attackers
    act in the SAME round (a single creature can't attack twice without Extra Attack)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server
    cid = server.create_campaign("Parry")["id"]
    h1 = server.create_character(cid, "Hero One", kind="player", max_hp=30, armor_class=14)["id"]
    h2 = server.create_character(cid, "Hero Two", kind="player", max_hp=30, armor_class=14)["id"]
    cap = server.spawn_monster(cid, "Bandit Captain")["spawned"][0]["id"]
    assert server.get_character(cid, cap)["armor_class"] == 15
    server.start_combat(cid, [h1, h2, cap])

    monkeypatch.setattr(server.dice_mod, "roll", _attack_roll_stub(16))
    # h1's marginal hit 16 -> would hit (>=15); Parry (+2 -> 17) flips it to a miss + spends the reaction
    res = server.attack(cid, h1, cap, attack_bonus=0, damage_dice="1d6+1", damage_type="slashing")
    assert res["hit"] is False, "Parry should have turned the marginal hit aside"
    assert res["parry"] is not None and res["parry"]["ac_bonus"] == 2 and res["parry"]["effective_ac"] == 17
    # h2's marginal hit the SAME round -> the captain's reaction is spent, so it lands
    res2 = server.attack(cid, h2, cap, attack_bonus=0, damage_dice="1d6+1", damage_type="slashing")
    assert res2["hit"] is True and res2["parry"] is None, "only one Parry per round"


def test_parry_does_not_fire_when_it_cannot_flip_or_on_a_crit_or_ranged(tmp_path, monkeypatch):
    """#218 guards: Parry never wastes the reaction on a blow that lands anyway, never stops a
    crit (a crit always hits), and ranged attacks can't be parried. Distinct attackers avoid
    the one-attack-per-turn economy limit."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    import server

    # (a) a clean hit (18 >= AC+2=17) lands + does NOT consume the reaction; a later marginal
    #     hit by a DIFFERENT attacker the same round IS parried (the reaction was still free).
    cid = server.create_campaign("ParryNoWaste")["id"]
    h1 = server.create_character(cid, "Hero One", kind="player", max_hp=30, armor_class=14)["id"]
    h2 = server.create_character(cid, "Hero Two", kind="player", max_hp=30, armor_class=14)["id"]
    cap = server.spawn_monster(cid, "Bandit Captain")["spawned"][0]["id"]  # AC 15, parry 2
    server.start_combat(cid, [h1, h2, cap])
    monkeypatch.setattr(server.dice_mod, "roll", _attack_roll_stub(18))
    res = server.attack(cid, h1, cap, attack_bonus=0, damage_dice="1d6+1", damage_type="slashing")
    assert res["hit"] is True and res["parry"] is None, "Parry must not be wasted on a blow it can't stop"
    monkeypatch.setattr(server.dice_mod, "roll", _attack_roll_stub(16))
    res_b = server.attack(cid, h2, cap, attack_bonus=0, damage_dice="1d6+1", damage_type="slashing")
    assert res_b["hit"] is False and res_b["parry"] is not None, "reaction was still free -> parries"

    # (b) a nat-20 crit with a low total (16 < 17) is NOT parried — a crit always hits.
    cid2 = server.create_campaign("ParryCrit")["id"]
    hc = server.create_character(cid2, "Hero", kind="player", max_hp=30, armor_class=14)["id"]
    cap2 = server.spawn_monster(cid2, "Bandit Captain")["spawned"][0]["id"]
    server.start_combat(cid2, [hc, cap2])
    monkeypatch.setattr(server.dice_mod, "roll", _attack_roll_stub(16, d20_natural=20))
    rc = server.attack(cid2, hc, cap2, attack_bonus=0, damage_dice="1d6+1", damage_type="slashing")
    assert rc["hit"] is True and rc["crit"] is True and rc["parry"] is None

    # (c) a ranged marginal hit (16) is NOT parried (Parry is melee-only).
    cid3 = server.create_campaign("ParryRanged")["id"]
    hr = server.create_character(cid3, "Archer", kind="player", max_hp=30, armor_class=14)["id"]
    cap3 = server.spawn_monster(cid3, "Bandit Captain")["spawned"][0]["id"]
    server.start_combat(cid3, [hr, cap3])
    monkeypatch.setattr(server.dice_mod, "roll", _attack_roll_stub(16))
    rr = server.attack(cid3, hr, cap3, attack_bonus=0, damage_dice="1d6+1", damage_type="piercing", is_ranged=True)
    assert rr["hit"] is True and rr["parry"] is None
