"""Kill-time XP awarding — hardened behavior tests.

Validates that _award_kill_xp fires at the moment a monster dies, making XP
award robust to DM sequencing errors that previously silently lost progression:

  A) Normal in-combat kill + end_combat → XP awarded (regression guard).
  B) Kill then remove_combatant before end_combat → XP NOW awarded (kill-time fires
     before removal clears the order; end_combat finds xp_value=0 and is a no-op).
  C) end_combat while foe alive → foe killed post-combat via apply_damage → XP NOW
     awarded (kill-time award fires on the post-combat killing blow).
  D) Idempotency: killing blow + end_combat backstop ≤ no double-award.

Supersedes /tmp/test_xp_repro.py, which asserted the OLD buggy behavior.
"""
import pytest

import server
import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


def _setup():
    """One campaign (xp mode), one PC auto-added to party."""
    cid = server.create_campaign("XP Kill-Time Test")["id"]
    assert server._require(cid).leveling_mode == "xp"
    pc_id = server.create_character(cid, "Hero", kind="player", max_hp=20)["id"]
    assert pc_id in server._require(cid).party
    return cid, pc_id


# ─── A: normal in-combat kill + end_combat ──────────────────────────────────

def test_A_normal_kill_awards_xp():
    """Monster dead inside combat bracket; end_combat (backstop) awards XP."""
    cid, pc_id = _setup()
    res = server.spawn_monster(cid, "Goblin Warrior")
    mob_id, mob_xp = res["spawned"][0]["id"], res["xp_each"]
    assert mob_xp > 0

    server.start_combat(cid, [pc_id, mob_id])
    kill = server.apply_damage(cid, mob_id, 9999)
    assert kill["dead"] is True

    # kill-time award fires here already
    assert "kill_xp" in kill, f"apply_damage should return kill_xp on monster death; got {kill}"
    assert kill["kill_xp"]["xp_awarded"] == mob_xp

    # PC XP already updated before end_combat
    pc_after_kill = server._require(cid).characters[pc_id]
    assert pc_after_kill.xp == mob_xp

    # end_combat backstop: xp_value already 0 → no double-award
    out = server.end_combat(cid)
    assert out["active"] is False
    # backstop yields nothing (already consumed)
    assert out.get("xp_awarded", 0) == 0

    # PC xp unchanged (not doubled)
    pc_final = server._require(cid).characters[pc_id]
    assert pc_final.xp == mob_xp


# ─── B: kill then remove_combatant before end_combat ────────────────────────

def test_B_kill_then_remove_combatant_awards_xp():
    """Kill-time award fires BEFORE remove_combatant resets the combat.
    XP must land even though end_combat finds an empty or PC-only order."""
    cid, pc_id = _setup()
    res = server.spawn_monster(cid, "Goblin Warrior")
    mob_id, mob_xp = res["spawned"][0]["id"], res["xp_each"]

    server.start_combat(cid, [pc_id, mob_id])
    kill = server.apply_damage(cid, mob_id, 9999)
    assert kill["dead"] is True
    # kill-time award fired immediately
    assert kill["kill_xp"]["xp_awarded"] == mob_xp

    pc_mid = server._require(cid).characters[pc_id].xp
    assert pc_mid == mob_xp

    # DM removes combatant (may auto-end combat if only PCs remain)
    server.remove_combatant(cid, mob_id)

    # monster's xp_value was zeroed by kill-time award
    mob_final = server._require(cid).characters[mob_id].xp_value
    assert mob_final == 0, f"xp_value should have been zeroed at kill time; got {mob_final}"

    # PC XP held — no rollback from remove_combatant
    pc_final = server._require(cid).characters[pc_id].xp
    assert pc_final == mob_xp


# ─── C: end_combat while foe alive → post-combat kill ───────────────────────

def test_C_post_combat_kill_awards_xp():
    """Wave2-b root cause: DM calls end_combat while monster at 1 HP (still alive),
    then narration-kills it via apply_damage AFTER combat ends.

    OLD behavior: XP lost forever (end_combat saw it alive, no future combat).
    NEW behavior: kill-time award fires on the post-combat apply_damage call."""
    cid, pc_id = _setup()
    res = server.spawn_monster(cid, "Goblin Warrior")
    mob_id, mob_xp = res["spawned"][0]["id"], res["xp_each"]

    server.start_combat(cid, [pc_id, mob_id])

    # Damage to 1 HP — monster still alive
    mob = server._require(cid).characters[mob_id]
    server.apply_damage(cid, mob_id, mob.max_hp - 1)
    assert server._require(cid).characters[mob_id].dead is False
    assert server._require(cid).characters[mob_id].current_hp == 1

    # DM calls end_combat while monster lives (the DM sequencing mistake)
    out = server.end_combat(cid)
    assert out["active"] is False
    # no XP yet — monster was alive
    assert out.get("xp_awarded", 0) == 0
    assert server._require(cid).characters[pc_id].xp == 0

    # Post-combat narration-kill via apply_damage
    kill = server.apply_damage(cid, mob_id, 9999)
    assert kill["dead"] is True
    # kill-time award fires here despite combat being over
    assert "kill_xp" in kill, f"post-combat kill must fire kill_xp; got {kill}"
    assert kill["kill_xp"]["xp_awarded"] == mob_xp

    pc_final = server._require(cid).characters[pc_id].xp
    assert pc_final == mob_xp, f"PC xp={pc_final}, expected {mob_xp}"


# ─── D: idempotency — no double-award ───────────────────────────────────────

def test_D_idempotency_no_double_award():
    """kill-time award zeros xp_value; end_combat backstop finds 0 → no double-award."""
    cid, pc_id = _setup()
    res = server.spawn_monster(cid, "Goblin Warrior")
    mob_id, mob_xp = res["spawned"][0]["id"], res["xp_each"]

    server.start_combat(cid, [pc_id, mob_id])
    kill = server.apply_damage(cid, mob_id, 9999)
    assert kill["dead"] is True
    assert kill["kill_xp"]["xp_awarded"] == mob_xp

    pc_after_kill = server._require(cid).characters[pc_id].xp
    assert pc_after_kill == mob_xp  # awarded once

    # Backstop sweep in end_combat must NOT re-award
    server.end_combat(cid)
    pc_final = server._require(cid).characters[pc_id].xp
    assert pc_final == mob_xp, (
        f"Double-award detected: PC xp={pc_final}, expected exactly {mob_xp}"
    )


# ─── milestone mode: kill-time award is a no-op ─────────────────────────────

def test_milestone_mode_no_kill_time_award():
    """In milestone leveling mode _award_kill_xp returns None — no XP, ever."""
    cid, pc_id = _setup()
    c = store.load_campaign(cid)
    c.leveling_mode = "milestone"
    store.save_campaign(c)

    res = server.spawn_monster(cid, "Goblin Warrior")
    mob_id = res["spawned"][0]["id"]

    server.start_combat(cid, [pc_id, mob_id])
    kill = server.apply_damage(cid, mob_id, 9999)
    assert kill["dead"] is True
    assert "kill_xp" not in kill  # no award in milestone mode

    out = server.end_combat(cid)
    assert "xp_awarded" not in out
    assert server._require(cid).characters[pc_id].xp == 0
