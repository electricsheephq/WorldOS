"""Action economy tracker — action/bonus/reaction budget (P2.2)."""

import pytest

import server


@pytest.fixture
def combat(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    ids = [
        server.create_character(cid, n, kind=k, max_hp=10, armor_class=12)["id"]
        for n, k in (("A", "player"), ("B", "player"), ("M", "monster"))
    ]
    server.start_combat(cid, ids)
    return cid, ids, server.get_state(cid)["current_turn"]


def test_second_action_same_turn_is_flagged(combat):
    cid, _ids, cur = combat
    first = server.use_action(cid, cur, "action")
    assert first["ok"] is True and first["action_available"] is False
    second = server.use_action(cid, cur, "action")
    assert second["ok"] is False and "already used" in second["reason"]


def test_action_and_bonus_are_independent(combat):
    cid, _ids, cur = combat
    assert server.use_action(cid, cur, "action")["ok"] is True
    assert server.use_action(cid, cur, "bonus")["ok"] is True  # bonus still available


def test_off_turn_action_rejected_but_reaction_allowed(combat):
    cid, ids, cur = combat
    other = next(i for i in ids if i != cur)
    assert server.use_action(cid, other, "action")["ok"] is False  # not their turn
    assert server.use_action(cid, other, "reaction")["ok"] is True  # reactions act off-turn
    assert server.use_action(cid, other, "reaction")["ok"] is False  # only one per round


def test_next_turn_refreshes_the_budget(combat):
    cid, _ids, cur = combat
    server.use_action(cid, cur, "action")
    server.use_action(cid, cur, "bonus")
    server.next_turn(cid)
    new_cur = server.get_state(cid)["current_turn"]
    assert server.use_action(cid, new_cur, "action")["ok"] is True  # fresh turn
