import pytest

import rests
import server
from dice import DiceRoll
from models import Character, Condition


def mk(**kw) -> Character:
    return Character(name="T", **kw)


def fixed_roll(total: int):
    def _roll(expr, *a, **k):
        return DiceRoll(expression=expr, total=total, rolls=[total])

    return _roll


# --- short rest ---
def test_short_rest_spends_hit_dice():
    ch = mk(max_hp=20, current_hp=5, hit_dice="3d8", hit_dice_remaining=3,
            abilities={"constitution": 14})
    out = rests.short_rest(ch, 2, fixed_roll(5))  # 2 dice, each 5 + 2(CON) = 7 -> 14
    assert out["hp_restored"] == 14 and ch.current_hp == 19 and ch.hit_dice_remaining == 1


def test_short_rest_caps_at_max_hp():
    ch = mk(max_hp=10, current_hp=8, hit_dice="1d8", hit_dice_remaining=1)
    rests.short_rest(ch, 1, fixed_roll(8))
    assert ch.current_hp == 10


def test_short_rest_cannot_overspend_dice():
    ch = mk(max_hp=20, current_hp=5, hit_dice="2d8", hit_dice_remaining=1)
    out = rests.short_rest(ch, 5, fixed_roll(4))  # only 1 die available
    assert out["dice_spent"] == 1 and ch.hit_dice_remaining == 0


def test_short_rest_warlock_recovers_slots():
    ch = mk(max_hp=10, current_hp=10, hit_dice="1d8", hit_dice_remaining=1,
            classes=[{"name": "Warlock", "level": 3}],
            spell_slots={2: {"maximum": 2, "used": 2}})
    out = rests.short_rest(ch, 0, fixed_roll(1))
    assert out["pact_slots_recovered"] is True and ch.spell_slots[2].used == 0


def test_short_rest_non_warlock_keeps_slots():
    ch = mk(max_hp=10, current_hp=10, hit_dice="1d6", hit_dice_remaining=1,
            classes=[{"name": "Wizard", "level": 2}],
            spell_slots={1: {"maximum": 3, "used": 2}})
    out = rests.short_rest(ch, 0, fixed_roll(1))
    assert out["pact_slots_recovered"] is False and ch.spell_slots[1].used == 2


# --- long rest ---
def test_long_rest_restores_everything():
    ch = mk(max_hp=20, current_hp=4, hit_dice="4d8", hit_dice_remaining=0, exhaustion=2,
            classes=[{"name": "Wizard", "level": 4}],
            spell_slots={1: {"maximum": 4, "used": 4}, 2: {"maximum": 3, "used": 1}})
    rests.long_rest(ch)
    assert ch.current_hp == 20
    assert ch.hit_dice_remaining == 2  # half of total level 4
    assert ch.exhaustion == 1
    assert ch.spell_slots[1].used == 0 and ch.spell_slots[2].used == 0


def test_rest_requires_positive_hp():  # C1: a 0-HP creature can't benefit from a rest
    ch = mk(max_hp=10, current_hp=0, conditions=[Condition.UNCONSCIOUS])
    with pytest.raises(ValueError):
        rests.long_rest(ch)
    with pytest.raises(ValueError):
        rests.short_rest(ch, 1, fixed_roll(5))


def test_long_rest_dead_raises():
    ch = mk(max_hp=10, current_hp=10, dead=True)
    with pytest.raises(ValueError):
        rests.long_rest(ch)


def test_short_rest_warlock_resets_only_pact_slot():  # H1
    ch = mk(max_hp=10, current_hp=10, hit_dice="1d8", hit_dice_remaining=1,
            classes=[{"name": "Warlock", "level": 3}],
            spell_slots={2: {"maximum": 2, "used": 2}, 5: {"maximum": 1, "used": 1}})
    rests.short_rest(ch, 0, fixed_roll(1))
    assert ch.spell_slots[2].used == 0  # pact level (Warlock 3 -> slot level 2)
    assert ch.spell_slots[5].used == 1  # stray non-pact entry untouched


def test_short_rest_negative_con_floors_at_zero():
    ch = mk(max_hp=20, current_hp=5, hit_dice="2d8", hit_dice_remaining=2,
            abilities={"constitution": 8})  # CON mod -1
    out = rests.short_rest(ch, 2, fixed_roll(1))  # each die: 1 + (-1) = 0, floored
    assert out["hp_restored"] == 0 and ch.hit_dice_remaining == 0


def test_long_rest_hit_dice_cap_and_exhaustion_floor():
    ch = mk(max_hp=20, current_hp=20, hit_dice="4d8", hit_dice_remaining=4, exhaustion=0,
            classes=[{"name": "Fighter", "level": 4}])
    rests.long_rest(ch)
    assert ch.hit_dice_remaining == 4  # already at total; capped
    assert ch.exhaustion == 0  # floored at 0


# --- tools persist ---
def test_rest_tools_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("R")["id"]
    w = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True,
                                abilities={"intelligence": 16, "constitution": 12})["id"]
    server.cast_spell(cid, w, "Magic Missile")  # consume a slot
    server.long_rest(cid, w)
    assert server.get_character(cid, w)["spell_slots"]["1"]["used"] == 0
