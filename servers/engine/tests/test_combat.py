import pytest

import combat
from dice import DiceRoll
from models import Character, Condition


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
