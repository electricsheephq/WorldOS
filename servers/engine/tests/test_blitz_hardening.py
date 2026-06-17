"""Regressions for the engine-side adversarial audit of the blitz batch."""

import pytest

import companion
import recap
import server
from models import Character, Combat, Combatant, SessionLogEntry


def _combat(*ids) -> Combat:
    return Combat(active=True, round=1, turn_index=0, order=[Combatant(character_id=i) for i in ids])


def test_suggest_action_skips_dead_ally():  # C1
    comp = Character(name="Ally", kind="companion", max_hp=10, current_hp=10)
    dead = Character(name="Fallen", kind="player", max_hp=10, current_hp=0, dead=True)
    gob = Character(name="Goblin", kind="monster", max_hp=7, current_hp=7)
    chars = {comp.id: comp, dead.id: dead, gob.id: gob}
    out = companion.suggest_action(comp, _combat(comp.id, dead.id, gob.id), chars)
    assert out["action"] == "attack" and out["target_id"] == gob.id


def test_suggest_action_skips_stable_ally():  # H1
    comp = Character(name="Ally", kind="companion", max_hp=10, current_hp=10)
    stable = Character(name="Resting", kind="player", max_hp=10, current_hp=0, stable=True)
    gob = Character(name="Goblin", kind="monster", max_hp=7, current_hp=7)
    chars = {comp.id: comp, stable.id: stable, gob.id: gob}
    assert companion.suggest_action(comp, _combat(comp.id, stable.id, gob.id), chars)["action"] == "attack"


def test_suggest_action_still_aids_truly_downed():
    comp = Character(name="Ally", kind="companion", max_hp=10, current_hp=10)
    downed = Character(name="Hurt", kind="player", max_hp=10, current_hp=0)  # not dead, not stable
    gob = Character(name="Goblin", kind="monster", max_hp=7, current_hp=7)
    chars = {comp.id: comp, downed.id: downed, gob.id: gob}
    out = companion.suggest_action(comp, _combat(comp.id, downed.id, gob.id), chars)
    assert out["action"] == "aid_downed" and out["target_id"] == downed.id


def test_recap_sanitizes_quotes_and_newlines():  # M3
    entries = [SessionLogEntry(kind="dialogue", speaker='Bob"x', text='hi\nthere "friend"')]
    out = recap.format_recap(entries)
    assert "\n" not in out and out.count('"') == 2  # only the wrapping quotes survive


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign("Hard")["id"]


def test_invalid_difficulty_rejected(cid):  # M1
    with pytest.raises(Exception):
        server.set_house_rules(cid, {"difficulty": "ludicrous"})


def test_log_event_session_persists(cid):
    import store

    server.log_event(cid, "narration", "first beat")
    sid = store.load_campaign(cid).active_session_id
    assert sid is not None
    server.log_event(cid, "combat", "second beat")
    rec = server.session_recap(cid)["recap"]
    assert "first beat" in rec and "second beat" in rec
    assert store.load_campaign(cid).active_session_id == sid  # same session reused


def test_encounter_empty_party_raises():  # audit-2 H1
    import encounter

    with pytest.raises(ValueError):
        encounter.encounter_difficulty([], [50])


def test_validate_flags_dangling_connection():  # audit-2 L1
    import generator

    adv = {"title": "T", "locations": [{"id": "a", "name": "A", "connections": ["ghost"]}]}
    assert any("ghost" in p for p in generator.validate_adventure(adv))
