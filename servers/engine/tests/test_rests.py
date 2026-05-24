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


# --- long rest advances the in-world clock (a confirmed cause of campaigns
# frozen at day=1: the tool restored resources but never rolled the calendar) ---
def test_long_rest_from_evening_rolls_into_next_morning(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Clock")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 1, "evening"  # bed down at dusk on day 1
    server.save_campaign(c)
    pc = server.create_character(cid, "Astarion", kind="player", class_name="Wizard",
                                 apply_srd_defaults=True,
                                 abilities={"intelligence": 16, "constitution": 12})["id"]
    server.cast_spell(cid, pc, "Magic Missile")  # consume a slot to prove restore still happens
    out = server.long_rest(cid, pc)
    # evening -> next dawn rolls the day over
    assert out["day"] == 2 and out["time_of_day"] == "morning"
    persisted = server._require(cid)
    assert persisted.day == 2 and persisted.time_of_day == "morning"  # change was saved
    sheet = server.get_character(cid, pc)
    assert sheet["spell_slots"]["1"]["used"] == 0  # slots still restored
    assert sheet["current_hp"] == sheet["max_hp"]  # HP still restored


def test_long_rest_at_morning_is_a_clock_no_op(tmp_path, monkeypatch):
    # A long rest taken when it is already morning leaves the clock at this morning —
    # so the "call long_rest for each party member" pattern doesn't burn a day per PC.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Clock2")["id"]
    c = server._require(cid)
    assert (c.day, c.time_of_day) == (1, "morning")  # fresh-campaign baseline
    pc = server.create_character(cid, "Wyll", kind="player", class_name="Fighter",
                                 apply_srd_defaults=True, abilities={"constitution": 14})["id"]
    out = server.long_rest(cid, pc)
    assert out["day"] == 1 and out["time_of_day"] == "morning"


def test_long_rest_whole_party_converges_on_one_morning(tmp_path, monkeypatch):
    # The documented usage: the DM calls long_rest for EACH party member after an
    # overnight. The first call rolls evening -> next morning; subsequent members
    # resting that same morning must NOT each advance another day.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Clock3")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 3, "evening"
    server.save_campaign(c)
    a = server.create_character(cid, "Karlach", kind="player", class_name="Fighter",
                                apply_srd_defaults=True)["id"]
    b = server.create_character(cid, "Gale", kind="player", class_name="Wizard",
                                apply_srd_defaults=True)["id"]
    out_a = server.long_rest(cid, a)
    out_b = server.long_rest(cid, b)
    assert (out_a["day"], out_a["time_of_day"]) == (4, "morning")
    assert (out_b["day"], out_b["time_of_day"]) == (4, "morning")  # NOT day 5
    assert server._require(cid).day == 4  # the whole party rested into a single dawn
