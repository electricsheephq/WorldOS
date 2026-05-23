"""Quest graph + campaign_dashboard + downtime (P2.8)."""

import pytest

import server


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.start_adventure("cellar-rats")["campaign_id"]


def test_add_and_complete_quest(cid):
    q = server.add_quest(
        cid, "Slay the rat-king", giver_id="brakka", location_id="loc-sump",
        objectives=["find the sump"],
    )
    assert q["status"] == "active"
    assert server.complete_quest(cid, q["id"], "completed")["status"] == "completed"


def test_complete_quest_rejects_bad_status(cid):
    q = server.add_quest(cid, "X")
    with pytest.raises(Exception):
        server.complete_quest(cid, q["id"], "ludicrous")


def test_dashboard_rollup_resolves_links(cid):
    server.create_character(cid, "Hero", kind="player", max_hp=10)
    server.add_quest(cid, "Find the drain", giver_id="brakka", location_id="loc-sump")
    server.add_consequence(cid, 5, "The undercity floods upward.")
    dash = server.campaign_dashboard(cid)
    assert dash["location"] is not None
    assert any(p["name"] == "Vesper" for p in dash["party"])  # seeded companion present
    fd = next(q for q in dash["active_quests"] if q["title"] == "Find the drain")
    assert fd["giver"] is not None and fd["location"] is not None  # names resolved
    assert any("undercity" in pc["text"] for pc in dash["pending_consequences"])


def test_downtime_advances_days_and_fires_consequences(cid):
    server.add_consequence(cid, 3, "Reinforcements arrive at the keep.")
    out = server.downtime(cid, 5, note="travel to the capital")
    assert out["days_elapsed"] == 5 and out["day"] >= 6  # cellar-rats starts on day 1
    assert any("Reinforcements" in d["text"] for d in out["due_consequences"])
