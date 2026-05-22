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
