import pytest

import npc as npc_mod
import server


def test_shift_attitude():
    assert npc_mod.shift_attitude("indifferent", 1) == "friendly"
    assert npc_mod.shift_attitude("indifferent", -1) == "wary"
    assert npc_mod.shift_attitude("hostile", -1) == "hostile"  # floored
    assert npc_mod.shift_attitude("helpful", 1) == "helpful"  # capped
    assert npc_mod.shift_attitude("guarded", 1) == "indifferent"  # guarded -> wary -> +1
    assert npc_mod.shift_attitude("guarded", -1) == "hostile"  # wary -> -1
    assert npc_mod.shift_attitude("", 1) == "friendly"  # blank -> indifferent -> +1


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
    assert out["kind"] == "influence" and "read" not in out  # influence is tagged, no read block


def test_social_check_read_skills_perceive_without_shifting_attitude(campaign):
    # A READ (insight/perception/investigation) tells the actor something; it must
    # NEVER change how the NPC feels — reading or MISreading someone is observer
    # clarity, not influence. (A failed Insight wrongly souring a warmth-first
    # antagonist was the QA-flagged bug this fixes.)
    pc = server.create_character(campaign, "Watcher", kind="player", abilities={"wisdom": 16})["id"]
    npc_id = server.create_character(campaign, "Stranger", kind="npc")["id"]

    server.set_attitude(campaign, npc_id, "indifferent")
    ok = server.social_check(campaign, pc, npc_id, "insight", dc=1)  # always succeeds
    assert ok["kind"] == "read" and ok["success"] is True
    assert ok["old_attitude"] == "indifferent" and ok["new_attitude"] == "indifferent"
    assert ok["read"]["perceived_attitude"] == "indifferent"  # a clear read reveals the stance

    miss = server.social_check(campaign, pc, npc_id, "insight", dc=100)  # always fails
    assert miss["kind"] == "read" and miss["success"] is False
    assert miss["new_attitude"] == "indifferent"  # a flubbed read is NOT an attitude penalty
    assert miss["read"]["perceived_attitude"] is None  # uncertain read, nothing asserted as truth

    # perception and investigation are reads too — attitude holds either way
    for sk in ("perception", "investigation"):
        out = server.social_check(campaign, pc, npc_id, sk, dc=100)
        assert out["kind"] == "read" and out["new_attitude"] == "indifferent"


def test_social_check_unknown_skill_raises(campaign):
    pc = server.create_character(campaign, "PC", kind="player")["id"]
    npc_id = server.create_character(campaign, "NPC", kind="npc")["id"]
    with pytest.raises(Exception):
        server.social_check(campaign, pc, npc_id, "flossing", dc=10)


def test_social_check_self_or_pc_target_raises(campaign):
    pc = server.create_character(campaign, "PC", kind="player", abilities={"charisma": 14})["id"]
    with pytest.raises(Exception):
        server.social_check(campaign, pc, pc, "persuasion", dc=10)  # actor == target
    pc2 = server.create_character(campaign, "PC2", kind="player")["id"]
    with pytest.raises(Exception):
        server.social_check(campaign, pc, pc2, "persuasion", dc=10)  # target is a PC


def test_remember_dedupes(campaign):
    npc_id = server.create_character(campaign, "NPC", kind="npc")["id"]
    server.remember(campaign, npc_id, "owes the party")
    out = server.remember(campaign, npc_id, "owes the party")  # duplicate
    assert out["memory"].count("owes the party") == 1


def test_forget_case_insensitive(campaign):
    npc_id = server.create_character(campaign, "NPC", kind="npc")["id"]
    server.remember(campaign, npc_id, "The Party Helped")
    server.forget(campaign, npc_id, "the party helped")  # different case
    assert server.get_character(campaign, npc_id)["memory"] == []
