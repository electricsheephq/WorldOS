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


def test_social_check_influence_raises_and_lowers_attitude_value(campaign):
    # Feature 1: an INFLUENCE check nudges the numeric per-NPC relationship in
    # ADDITION to the free-text track — +15 on a success, -10 on a failure, clamped.
    pc = server.create_character(campaign, "Bard", kind="player", abilities={"charisma": 16})["id"]
    npc_id = server.create_character(campaign, "Guard", kind="npc")["id"]

    # value starts at the neutral default
    assert server.get_character(campaign, npc_id)["attitude_value"] == 0

    win = server.social_check(campaign, pc, npc_id, "persuasion", dc=1)  # always succeeds
    assert win["old_attitude_value"] == 0 and win["new_attitude_value"] == 15
    assert server.get_character(campaign, npc_id)["attitude_value"] == 15

    loss = server.social_check(campaign, pc, npc_id, "persuasion", dc=100)  # always fails
    assert loss["old_attitude_value"] == 15 and loss["new_attitude_value"] == 5
    assert server.get_character(campaign, npc_id)["attitude_value"] == 5


def test_social_check_read_leaves_attitude_value_untouched(campaign):
    # Feature 1 + the read-vs-influence invariant: an Insight READ must NOT move the
    # numeric value any more than it moves the free-text track — observer clarity is
    # not influence. (Mirrors the read-skills attitude test, for the number.)
    pc = server.create_character(campaign, "Watcher", kind="player", abilities={"wisdom": 16})["id"]
    npc_id = server.create_character(campaign, "Stranger", kind="npc")["id"]
    server.set_attitude(campaign, npc_id, "indifferent", value=22)

    ok = server.social_check(campaign, pc, npc_id, "insight", dc=1)  # clear read
    assert ok["kind"] == "read"
    assert ok["old_attitude_value"] == 22 and ok["new_attitude_value"] == 22

    miss = server.social_check(campaign, pc, npc_id, "insight", dc=100)  # flubbed read
    assert miss["new_attitude_value"] == 22  # a missed read is not a numeric penalty
    assert server.get_character(campaign, npc_id)["attitude_value"] == 22


def test_social_check_attitude_value_clamps_at_bounds(campaign):
    # Repeated successes can't push the value past +100; repeated failures can't drop
    # it below -100.
    pc = server.create_character(campaign, "Diplomat", kind="player", abilities={"charisma": 18})["id"]
    npc_id = server.create_character(campaign, "Noble", kind="npc")["id"]

    server.set_attitude(campaign, npc_id, "friendly", value=95)
    out = server.social_check(campaign, pc, npc_id, "persuasion", dc=1)  # +15 would be 110
    assert out["new_attitude_value"] == 100  # clamped at the ceiling

    server.set_attitude(campaign, npc_id, "wary", value=-95)
    out2 = server.social_check(campaign, pc, npc_id, "persuasion", dc=100)  # -10 would be -105
    assert out2["new_attitude_value"] == -100  # clamped at the floor


def test_set_attitude_value_and_default(campaign):
    # Feature 1: set_attitude(value=...) sets the number; omitting value leaves it.
    npc_id = server.create_character(campaign, "Innkeep", kind="npc")["id"]
    assert server.get_character(campaign, npc_id)["attitude_value"] == 0  # default neutral

    out = server.set_attitude(campaign, npc_id, "friendly", value=40)
    assert out["attitude"] == "friendly" and out["attitude_value"] == 40

    # free-text-only call must NOT reset the number it didn't touch
    out2 = server.set_attitude(campaign, npc_id, "guarded")
    assert out2["attitude"] == "guarded" and out2["attitude_value"] == 40

    # value is clamped to the -100..+100 scale
    assert server.set_attitude(campaign, npc_id, "devoted", value=999)["attitude_value"] == 100
    assert server.set_attitude(campaign, npc_id, "hostile", value=-999)["attitude_value"] == -100


def test_adjust_attitude_nudges_and_clamps(campaign):
    # Feature 1: adjust_attitude nudges the number by a delta, clamped, free-text intact.
    npc_id = server.create_character(campaign, "Merchant", kind="npc")["id"]
    server.set_attitude(campaign, npc_id, "wary", value=10)

    up = server.adjust_attitude(campaign, npc_id, 25)
    assert up["old_attitude_value"] == 10 and up["attitude_value"] == 35
    assert server.get_character(campaign, npc_id)["attitude"] == "wary"  # text unchanged

    down = server.adjust_attitude(campaign, npc_id, -50)
    assert down["attitude_value"] == -15

    assert server.adjust_attitude(campaign, npc_id, 1000)["attitude_value"] == 100  # ceiling
    assert server.adjust_attitude(campaign, npc_id, -1000)["attitude_value"] == -100  # floor


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
