import pytest

import npc as npc_mod
import server


def test_shift_attitude():
    assert npc_mod.shift_attitude("indifferent", 1) == "friendly"
    assert npc_mod.shift_attitude("indifferent", -1) == "wary"
    assert npc_mod.shift_attitude("hostile", -1) == "hostile"  # floored
    assert npc_mod.shift_attitude("helpful", 1) == "helpful"  # capped
    assert npc_mod.shift_attitude("guarded", 1) == "friendly"  # unknown -> indifferent -> +1
    assert npc_mod.shift_attitude("", 1) == "friendly"


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("Social")["id"]


def test_set_attitude_and_memory(campaign):
    npc_id = server.create_character(campaign, "Brakka", kind="npc")["id"]
    server.set_attitude(campaign, npc_id, "guarded")
    server.remember(campaign, npc_id, "The party bought a round.")
    sheet = server.get_character(campaign, npc_id)
    assert sheet["attitude"] == "guarded"
    assert "The party bought a round." in sheet["memory"]
    server.forget(campaign, npc_id, "The party bought a round.")
    assert "The party bought a round." not in server.get_character(campaign, npc_id)["memory"]


def test_forget_unknown_raises(campaign):
    npc_id = server.create_character(campaign, "NPC", kind="npc")["id"]
    with pytest.raises(Exception):
        server.forget(campaign, npc_id, "never said this")


def test_social_check_success_and_failure(campaign):
    pc = server.create_character(campaign, "Bard", kind="player", abilities={"charisma": 16})["id"]
    npc_id = server.create_character(campaign, "Guard", kind="npc")["id"]

    server.set_attitude(campaign, npc_id, "indifferent")
    out = server.social_check(campaign, pc, npc_id, "persuasion", dc=1)  # always succeeds
    assert out["success"] is True and out["new_attitude"] == "friendly"

    server.set_attitude(campaign, npc_id, "indifferent")
    out2 = server.social_check(campaign, pc, npc_id, "persuasion", dc=100)  # always fails
    assert out2["success"] is False and out2["new_attitude"] == "wary"


def test_social_check_unknown_skill_raises(campaign):
    pc = server.create_character(campaign, "PC", kind="player")["id"]
    npc_id = server.create_character(campaign, "NPC", kind="npc")["id"]
    with pytest.raises(Exception):
        server.social_check(campaign, pc, npc_id, "flossing", dc=10)
