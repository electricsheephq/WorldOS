import pytest

import store
from models import Ability, Campaign, Character


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
