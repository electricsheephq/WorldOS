import json

import pytest
from pydantic import ValidationError

import store
from models import Ability, Campaign, Character, Condition, StrategicClock


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


def test_campaign_roundtrip():
    c = Campaign(title="Test Hold")
    store.save_campaign(c)
    loaded = store.load_campaign(c.id)
    assert loaded is not None
    assert loaded.title == "Test Hold"
    assert loaded.id == c.id


def test_campaign_roundtrips_with_empty_strategic_state():
    c = Campaign(title="Strategic Default")
    assert c.strategic_state.model_dump(mode="json") == {
        "regions": {},
        "assets": {},
        "clocks": {},
        "projects": {},
        "settlements": {},
        "last_tick_day": 0,
    }

    store.save_campaign(c)
    loaded = store.load_campaign(c.id)
    assert loaded is not None
    assert loaded.strategic_state.model_dump(mode="json") == c.strategic_state.model_dump(mode="json")
    snapshot = store._campaign_dir(c.id) / "snapshot.json"
    assert "strategic_state" in json.loads(snapshot.read_text(encoding="utf-8"))

    old = c.model_dump(mode="json")
    old.pop("strategic_state")
    reloaded = Campaign.model_validate(old)
    assert reloaded.strategic_state.model_dump(mode="json") == c.strategic_state.model_dump(mode="json")


def test_load_missing_returns_none():
    assert store.load_campaign("camp_does_not_exist") is None


def test_list_campaigns():
    a = Campaign(title="A")
    b = Campaign(title="B")
    store.save_campaign(a)
    store.save_campaign(b)
    ids = {x["id"] for x in store.list_campaigns()}
    assert {a.id, b.id} <= ids


def test_character_modifiers():
    ch = Character(name="Hero", abilities={"strength": 16, "dexterity": 14})
    assert ch.ability_modifier(Ability.STR) == 3
    assert ch.ability_modifier(Ability.DEX) == 2
    assert ch.ability_modifier(Ability.CON) == 0  # default 10


def test_skill_and_save_bonus():
    ch = Character(
        name="Rogue",
        abilities={"dexterity": 16},
        proficiency_bonus=2,
        skill_proficiencies=["stealth"],
        skill_expertise=["sleight_of_hand"],
        saving_throw_proficiencies=[Ability.DEX],
    )
    assert ch.skill_bonus("stealth") == 3 + 2  # dex mod + proficiency
    assert ch.skill_bonus("sleight_of_hand") == 3 + 4  # dex mod + 2*prof (expertise)
    assert ch.skill_bonus("acrobatics") == 3  # dex mod only
    assert ch.saving_throw_bonus(Ability.DEX) == 3 + 2
    assert ch.saving_throw_bonus(Ability.STR) == 0  # not proficient, default str 10


def test_character_persists_in_campaign():
    c = Campaign(title="Persistent")
    ch = Character(name="Goblin", kind="npc", max_hp=7, current_hp=7)
    c.characters[ch.id] = ch
    store.save_campaign(c)
    loaded = store.load_campaign(c.id)
    assert ch.id in loaded.characters
    assert loaded.characters[ch.id].name == "Goblin"
    assert loaded.characters[ch.id].kind == "npc"


def test_total_level():
    ch = Character(name="Multi", classes=[])
    assert ch.total_level == 1  # no classes -> level 1
    ch2 = Character(
        name="MC",
        classes=[{"name": "Fighter", "level": 3}, {"name": "Wizard", "level": 2}],
    )
    assert ch2.total_level == 5


def test_extra_field_rejected():
    # H2 regression: a typo'd field must raise, not silently vanish.
    with pytest.raises(ValidationError):
        Character.model_validate({"name": "Typo", "max_hpp": 99})


def test_hp_clamped_to_max():
    assert Character(name="C", max_hp=10, current_hp=99).current_hp == 10


def test_hp_floored_at_zero():
    assert Character(name="C", max_hp=10, current_hp=-5).current_hp == 0


def test_exhaustion_clamped():
    assert Character(name="C", exhaustion=9).exhaustion == 6


def test_condition_enum_validates():
    assert Condition("prone") == Condition.PRONE
    with pytest.raises(ValueError):
        Condition("bogus")


def test_strategic_clock_enum_validates():
    assert StrategicClock(title="A threat", kind="threat", scope="region").kind == "threat"
    with pytest.raises(ValidationError):
        StrategicClock(title="Bad", kind="rumor", scope="region")


def test_engine_tools_end_to_end():
    # Exercises the MCP tools through the locked load->mutate->save path.
    import server

    cid = server.create_campaign("Tools Test")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=20)
    char_id = hero["id"]

    server.set_hp(cid, char_id, 5)
    server.add_condition(cid, char_id, "prone")
    server.add_condition(cid, char_id, "prone")  # idempotent

    sheet = server.get_character(cid, char_id)
    assert sheet["current_hp"] == 5
    assert sheet["conditions"].count("prone") == 1

    server.remove_condition(cid, char_id, "prone")
    assert "prone" not in server.get_character(cid, char_id)["conditions"]

    with pytest.raises(Exception):
        server.update_character(cid, char_id, {"max_hpp": 99})  # typo rejected


def test_pacing_mode_default_set_and_invalid():
    # Feature 2: pacing defaults to "adventure", a valid mode persists + surfaces in
    # get_state, and an invalid mode is rejected (sole-writer validation).
    import server

    cid = server.create_campaign("Pacing")["id"]
    assert server.get_state(cid)["pacing_mode"] == "adventure"  # additive default

    out = server.set_pacing(cid, "downtime")
    assert out["pacing_mode"] == "downtime"
    assert server.get_state(cid)["pacing_mode"] == "downtime"  # persisted

    # round-trips through the snapshot the viewer reads
    assert store.load_campaign(cid).pacing_mode == "downtime"

    server.set_pacing(cid, "adventure")
    assert server.get_state(cid)["pacing_mode"] == "adventure"

    with pytest.raises(Exception):
        server.set_pacing(cid, "leisurely")  # not a valid mode


def test_initiative_bonus_equals_dex_modifier_on_create():
    # #733: a freshly created character's initiative_bonus must equal its DEX modifier.
    # DEX 14 -> +2 (the optimizer report: "+1 shown vs +2 expected").
    import server

    cid = server.create_campaign("Init Create")["id"]
    pid = server.create_character(
        cid, "Tav", kind="player", class_name="Rogue", level=1,
        apply_srd_defaults=True, abilities={"dexterity": 14},
    )["id"]
    sheet = server.get_character(cid, pid)
    assert sheet["abilities"]["dexterity"] == 14
    assert sheet["initiative_bonus"] == 2  # == DEX modifier


def test_initiative_bonus_recomputes_after_dex_patch():
    # #733 root cause: update_character changed the DEX score but left initiative_bonus
    # STALE (it only recomputed level-scaled stats on a class/level change). A DM stat
    # correction / ASI-by-patch therefore left initiative wrong (e.g. +2 frozen while the
    # DEX modifier became +4 or +1) — the exact "+1 shown vs +2 expected" divergence.
    import server

    cid = server.create_campaign("Init Patch")["id"]
    pid = server.create_character(
        cid, "Tav", kind="player", class_name="Rogue", level=1,
        apply_srd_defaults=True, abilities={"dexterity": 14},
    )["id"]
    assert server.get_character(cid, pid)["initiative_bonus"] == 2  # baseline DEX 14 -> +2

    # DEX up: 14 -> 18 (+4). initiative_bonus must track the new modifier.
    server.update_character(cid, pid, patch={"abilities": {"dexterity": 18}})
    sheet = server.get_character(cid, pid)
    assert sheet["abilities"]["dexterity"] == 18
    assert sheet["initiative_bonus"] == 4  # was stale +2 before the fix

    # DEX down: 18 -> 12 (+1). A down-change must also be honored (not floored at the old value).
    server.update_character(cid, pid, patch={"abilities": {"dexterity": 12}})
    sheet = server.get_character(cid, pid)
    assert sheet["abilities"]["dexterity"] == 12
    assert sheet["initiative_bonus"] == 1

    # round-trips through the snapshot the viewer reads
    assert store.load_campaign(cid).characters[pid].initiative_bonus == 1


def test_initiative_bonus_untouched_by_non_dex_patch():
    # The recompute is scoped to DEX-affecting patches: a non-DEX edit (HP, or even a
    # different ability) must NOT disturb a DM-set initiative_bonus.
    import server

    cid = server.create_campaign("Init NonDex")["id"]
    pid = server.create_character(
        cid, "Tav", kind="player", class_name="Fighter", level=1,
        apply_srd_defaults=True, abilities={"dexterity": 14},
    )["id"]
    # DM hand-sets a higher initiative (e.g. an Alert-feat house rule): +7.
    server.update_character(cid, pid, patch={"initiative_bonus": 7})
    assert server.get_character(cid, pid)["initiative_bonus"] == 7

    # A non-DEX patch must leave that explicit value alone.
    server.update_character(cid, pid, patch={"current_hp": 5})
    assert server.get_character(cid, pid)["initiative_bonus"] == 7

    # An explicit initiative_bonus in the SAME patch wins even alongside a DEX change.
    server.update_character(cid, pid, patch={"abilities": {"dexterity": 18}, "initiative_bonus": 9})
    sheet = server.get_character(cid, pid)
    assert sheet["abilities"]["dexterity"] == 18
    assert sheet["initiative_bonus"] == 9  # DM override wins over the DEX-derived +4


def test_start_combat_rolls_with_fresh_initiative_after_dex_patch(monkeypatch):
    # End-to-end: after a DEX patch the COMBAT roll (1d20 + initiative_bonus) must use the
    # fresh modifier, not the stale one. Pin the d20 to a known natural so the modifier is
    # the only variable.
    import server
    import dice as dice_mod

    def _fixed(expression, advantage=False, disadvantage=False, seed=None):
        from dice import DiceRoll
        bonus = 0
        if "+" in expression:
            bonus = int(expression.split("+", 1)[1])
        return DiceRoll(expression=expression, total=10 + bonus, rolls=[10],
                        modifier=bonus, is_d20=True, natural=10)

    cid = server.create_campaign("Init Combat")["id"]
    pid = server.create_character(
        cid, "Tav", kind="player", class_name="Rogue", level=1,
        apply_srd_defaults=True, abilities={"dexterity": 14},
    )["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", abilities={"dexterity": 8})["id"]

    server.update_character(cid, pid, patch={"abilities": {"dexterity": 18}})  # +2 -> +4

    monkeypatch.setattr(server.dice_mod, "roll", _fixed)
    view = server.start_combat(cid, [pid, gob])
    tav_init = next(o["initiative"] for o in view["order"] if o["character_id"] == pid)
    assert tav_init == 14  # natural 10 + fresh DEX modifier +4 (would be 12 with the stale +2)
