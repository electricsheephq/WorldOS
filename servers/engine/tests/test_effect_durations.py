"""Engine-tracked spell/effect durations (timed effects auto-expire instead of
relying on the DM to remember). Covers the ActiveEffect lifecycle: parsed from the
spell `duration`, set on cast, decremented per combat round and reported, expired by
the out-of-combat clock tools, and kept consistent with concentration (one source of
truth — when concentration breaks, the effect ends, and vice-versa)."""

import json

import pytest

import combat
import spells
import server
from models import ActiveEffect, Campaign, Character


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


def _cleric(cid, name="Pious", **abil):
    ab = {"wisdom": 16, "constitution": 12}
    ab.update(abil)
    return server.create_character(
        cid, name, kind="player", class_name="Cleric",
        apply_srd_defaults=True, abilities=ab,
    )["id"]


def _effects(cid, char_id):
    return server.get_character(cid, char_id)["active_effects"]


# --- duration parsing (pure) -------------------------------------------------
@pytest.mark.parametrize(
    "text,scale,rounds",
    [
        ("1 minute", "minutes", 10),              # srd524 singular
        ("10 minute", "minutes", 100),
        ("Concentration, up to 1 minute", "minutes", 10),   # curated prose
        ("Concentration, up to 10 minutes", "minutes", 100),
        ("1 round", "rounds", 1),
        ("6 round", "rounds", 6),
    ],
)
def test_parse_duration_rounds_and_minutes(text, scale, rounds):
    d = spells.parse_duration(text)
    assert d["scale"] == scale and d["rounds"] == rounds


@pytest.mark.parametrize(
    "text,scale,mag",
    [("1 hour", "hours", 1), ("8 hour", "hours", 8), ("8 hours", "hours", 8),
     ("24 hour", "hours", 24), ("10 day", "days", 10)],
)
def test_parse_duration_clock_scales(text, scale, mag):
    d = spells.parse_duration(text)
    assert d["scale"] == scale
    assert (d["hours"] if scale == "hours" else d["days"]) == mag


@pytest.mark.parametrize("text", ["Instantaneous", "instantaneous", "until dispelled",
                                  "special", "", None, "permanent"])
def test_parse_duration_untimed_is_none(text):
    assert spells.parse_duration(text) is None


# --- additive contract -------------------------------------------------------
def test_old_snapshot_roundtrips_unchanged():
    """A character snapshot with NO active_effects key loads with [] (today's
    behavior); a typo'd field still raises (extra='forbid' preserved)."""
    old = {
        "id": "camp_x", "title": "Old",
        "characters": {"c1": {"id": "c1", "name": "Hero", "kind": "player",
                              "concentration": "Bless"}},
    }
    c = Campaign.model_validate_json(json.dumps(old))
    assert c.characters["c1"].active_effects == []
    assert c.characters["c1"].concentration == "Bless"  # untouched
    with pytest.raises(Exception):
        Character.model_validate({"name": "X", "active_effectz": []})


def test_empty_active_effects_is_default():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    assert _effects(cid, c) == []  # nothing cast -> no effects


# --- set on cast -------------------------------------------------------------
def test_cast_bless_registers_round_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    out = server.cast_spell(cid, c, "Bless")
    ae = out["active_effect"]
    assert ae["name"] == "Bless" and ae["scale"] == "minutes"
    assert ae["rounds_remaining"] == 10  # 1 minute = 10 rounds
    assert ae["concentration"] is True and ae["holder_id"] == c
    eff = _effects(cid, c)
    assert len(eff) == 1 and eff[0]["name"] == "Bless" and eff[0]["source_id"] == c


def test_instantaneous_spell_registers_no_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    out = server.cast_spell(cid, c, "Cure Wounds")  # Instantaneous
    assert "active_effect" not in out
    assert _effects(cid, c) == []


def test_buff_with_target_tracks_on_target():
    """A non-concentration buff with an explicit target is held on the TARGET."""
    cid = server.create_campaign("S")["id"]
    caster = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard",
        apply_srd_defaults=True, abilities={"intelligence": 16},
    )["id"]
    server.learn_spells(cid, caster, ["Mage Armor"])
    server.prepare_spells(cid, caster, ["Mage Armor"])
    ally = _cleric(cid, "Ally")
    out = server.cast_spell(cid, caster, "Mage Armor", target_id=ally)
    assert out["active_effect"]["holder_id"] == ally
    assert _effects(cid, caster) == []  # caster carries nothing
    assert [e["name"] for e in _effects(cid, ally)] == ["Mage Armor"]
    assert _effects(cid, ally)[0]["source_id"] == caster  # source recorded


# --- decrement + auto-expire in combat ---------------------------------------
def test_next_turn_decrements_and_expires_bless():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30)["id"]
    server.cast_spell(cid, c, "Bless")  # 10 rounds
    server.start_combat(cid, [c, foe])  # round 1
    expired = None
    last_round = 0
    # Two combatants -> 2 next_turns per round. Bless ticks once per round; the 10th
    # decrement (start of round 11) expires it.
    for _ in range(30):
        v = server.next_turn(cid)
        last_round = v["round"]
        if v["expired_effects"]:
            expired = v
            break
    assert expired is not None, "Bless never expired"
    assert expired["round"] == 11  # cast at round 1, 10 rounds later
    assert expired["expired_effects"] == [{"character_id": c, "name": "Bless"}]
    assert _effects(cid, c) == []
    # Concentration cleared too when its effect timed out (one source of truth).
    assert server.get_character(cid, c)["concentration"] is None


def test_round_decrement_is_per_round_not_per_turn():
    """A single turn (no round wrap) does not decrement a round effect."""
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30)["id"]
    server.cast_spell(cid, c, "Bless")
    server.start_combat(cid, [c, foe])
    before = _effects(cid, c)[0]["rounds_remaining"]
    server.next_turn(cid)  # advance one turn (1 of 2) — no round wrap yet
    assert _effects(cid, c)[0]["rounds_remaining"] == before  # unchanged mid-round
    server.next_turn(cid)  # wrap -> round 2 -> decrement
    assert _effects(cid, c)[0]["rounds_remaining"] == before - 1


# --- concentration linkage ---------------------------------------------------
def test_concentration_break_on_failed_save_expires_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")
    assert len(_effects(cid, c)) == 1
    res = server.concentration_save(cid, c, 999)  # forced fail
    assert res["maintained"] is False
    assert res["expired_effects"] == ["Bless"]
    assert _effects(cid, c) == []
    assert server.get_character(cid, c)["concentration"] is None


def test_concentration_break_on_incapacitation_expires_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")
    server.add_condition(cid, c, "stunned")  # incapacitating -> breaks concentration
    assert server.get_character(cid, c)["concentration"] is None
    assert _effects(cid, c) == []


def test_concentration_break_on_drop_to_zero_hp_expires_effect():
    """Pure combat layer: dropping to 0 HP ends concentration AND its effect."""
    ch = Character(name="Caster", kind="player", max_hp=20, current_hp=20,
                   concentration="Bless",
                   active_effects=[ActiveEffect(name="Bless", concentration=True,
                                                scale="minutes", rounds_remaining=10)])
    combat.apply_damage(ch, 25)  # to 0 HP
    assert ch.concentration is None and ch.active_effects == []


def test_replacing_concentration_drops_prior_effect():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")
    server.cast_spell(cid, c, "Shield of Faith")  # 2nd concentration spell
    eff = _effects(cid, c)
    assert [e["name"] for e in eff] == ["Shield of Faith"]  # Bless effect dropped
    assert server.get_character(cid, c)["concentration"] == "Shield of Faith"


def test_recasting_same_spell_refreshes_not_stacks():
    """Recasting a spell on the same holder refreshes its duration (one effect, full
    timer), it does not accumulate duplicates."""
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=30)["id"]
    server.cast_spell(cid, c, "Bless")
    server.start_combat(cid, [c, foe])
    server.next_turn(cid)
    server.next_turn(cid)  # round 2 -> Bless 10 -> 9
    assert _effects(cid, c)[0]["rounds_remaining"] == 9
    server.cast_spell(cid, c, "Bless")  # recast refreshes
    eff = _effects(cid, c)
    assert len(eff) == 1 and eff[0]["rounds_remaining"] == 10  # single, full timer


# --- out-of-combat clock expiry ----------------------------------------------
def test_advance_time_expires_minute_scale():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")  # minute-scale
    out = server.advance_time(cid, phases=1)  # any phase advance ends sub-hour effects
    assert out["expired_effects"] == [{"character_id": c, "name": "Bless"}]
    assert _effects(cid, c) == []


def test_advance_time_no_movement_keeps_effects():
    cid = server.create_campaign("S")["id"]
    c = _cleric(cid)
    server.cast_spell(cid, c, "Bless")
    out = server.advance_time(cid, phases=0)  # clock didn't move
    assert out["expired_effects"] == []
    assert len(_effects(cid, c)) == 1


def test_mage_armor_survives_combat_but_expires_on_long_rest():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard",
        apply_srd_defaults=True, abilities={"intelligence": 16, "constitution": 12},
    )["id"]
    server.learn_spells(cid, w, ["Mage Armor"])
    server.prepare_spells(cid, w, ["Mage Armor"])
    out = server.cast_spell(cid, w, "Mage Armor")  # 8h, non-concentration
    assert out["active_effect"]["scale"] == "hours"
    foe = server.create_character(cid, "Goblin", kind="monster", max_hp=20)["id"]
    server.start_combat(cid, [w, foe])
    for _ in range(12):  # several rounds of combat
        server.next_turn(cid)
    assert [e["name"] for e in _effects(cid, w)] == ["Mage Armor"]  # still up
    server.end_combat(cid)
    lr = server.long_rest(cid, w)  # overnight ends the 8h buff
    assert lr["expired_effects"] == [{"character_id": w, "name": "Mage Armor"}]
    assert _effects(cid, w) == []


def test_short_rest_expires_sub_hour_but_keeps_mage_armor():
    cid = server.create_campaign("S")["id"]
    w = server.create_character(
        cid, "Gale", kind="player", class_name="Wizard",
        apply_srd_defaults=True, abilities={"intelligence": 16, "constitution": 14},
    )["id"]
    for s in ("Mage Armor", "Bless"):
        server.learn_spells(cid, w, [s])
    server.prepare_spells(cid, w, ["Mage Armor", "Bless"])
    server.cast_spell(cid, w, "Mage Armor")  # 8h
    server.cast_spell(cid, w, "Bless")       # 1 minute (sub-hour)
    out = server.short_rest(cid, w)
    names_expired = sorted(e["name"] for e in out["expired_effects"])
    assert names_expired == ["Bless"]  # sub-hour gone, Mage Armor survives
    assert [e["name"] for e in _effects(cid, w)] == ["Mage Armor"]


# --- pure helper edges -------------------------------------------------------
def test_tick_round_effects_only_touches_round_minute_scale():
    ch = Character(name="T", active_effects=[
        ActiveEffect(name="Bless", scale="minutes", rounds_remaining=2),
        ActiveEffect(name="Mage Armor", scale="hours", expires_day=5,
                     expires_phase_index=0, until_long_rest=True),
    ])
    assert combat.tick_round_effects(ch) == []  # Bless 2->1, nothing expires
    assert combat.tick_round_effects(ch) == ["Bless"]  # 1->0 expires
    assert [e.name for e in ch.active_effects] == ["Mage Armor"]  # hour untouched


def test_expire_clock_effects_deadline_and_long_rest():
    ch = Character(name="T", active_effects=[
        ActiveEffect(name="Hex", scale="hours", expires_day=1, expires_phase_index=2),
        ActiveEffect(name="Mage Armor", scale="hours", expires_day=9,
                     expires_phase_index=0, until_long_rest=True),
    ])
    # clock at day 1, phase 1: Hex's deadline (1,2) not yet reached
    assert combat.expire_clock_effects(ch, 1, 1) == []
    # advance to day 1 phase 2: Hex deadline reached
    assert combat.expire_clock_effects(ch, 1, 2) == ["Hex"]
    # Mage Armor's deadline is far off, but a long rest ends it
    assert combat.expire_clock_effects(ch, 1, 3, long_rest=True) == ["Mage Armor"]
    assert ch.active_effects == []
