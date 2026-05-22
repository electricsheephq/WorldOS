"""Integration tests for the MCP tool wrappers added when wiring the parallel
blitz modules (companion / recap / encounter / generator) + house rules."""

import pytest

import generator
import server


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


def test_house_rules_get_set():
    cid = server.create_campaign("HR")["id"]
    assert server.get_house_rules(cid)["difficulty"] == "standard"
    out = server.set_house_rules(cid, {"difficulty": "hard", "flanking_advantage": True})
    assert out["difficulty"] == "hard" and out["flanking_advantage"] is True
    with pytest.raises(Exception):
        server.set_house_rules(cid, {"bogus_rule": True})  # unknown key rejected


def test_log_and_recap():
    cid = server.create_campaign("Log")["id"]
    server.log_event(cid, "narration", "The heroes descend into the cellar.")
    server.log_event(cid, "combat", "A goblin ambush erupts!")
    rec = server.session_recap(cid)["recap"]
    assert "cellar" in rec.lower() or "goblin" in rec.lower()


def test_recap_empty_session():
    cid = server.create_campaign("Empty")["id"]
    assert isinstance(server.session_recap(cid)["recap"], str)


def test_companion_suggest_action_tool():
    cid = server.create_campaign("Comp")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=10)["id"]
    comp = server.create_character(cid, "Ally", kind="companion", max_hp=10)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=7)["id"]
    server.start_combat(cid, [hero, comp, gob])
    out = server.companion_suggest_action(cid, comp)
    assert out["action"] in {"attack", "aid_downed", "defend", "roleplay"}


def test_encounter_tools():
    assert server.xp_for_cr("1/4")["xp"] == 50
    out = server.encounter_difficulty([1, 1, 1, 1], [50, 50, 50, 50])
    assert out["difficulty"] in {"easy", "medium", "hard", "deadly"}
    assert set(server.party_xp_budget([1, 1, 1, 1])) >= {"easy", "medium", "hard", "deadly"}


def test_adventure_tools():
    assert server.validate_adventure("cellar-rats")["problems"] == []
    scaf = server.scaffold_adventure("Test Delve", premise="A test", min_level=1, max_level=3)
    assert generator.validate_adventure(scaf) == []
