"""Faction reputation tool + party-decision recording (P3.2, P3.3)."""

import pytest

import content
import server
import store


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("F")["id"]


def test_adjust_reputation_creates_on_miss_and_clamps(cid):
    out = server.adjust_reputation(cid, "goblin-crew", -30, reason="killed their scout")
    assert out["name"] == "Goblin Crew" and out["reputation"] == -30
    assert server.adjust_reputation(cid, "goblin-crew", -100)["reputation"] == -100  # clamp
    assert server.adjust_reputation(cid, "goblin-crew", +500)["reputation"] == 100  # clamp


def test_seed_factions_from_adventure():
    c = content.seed_campaign(
        {"title": "T", "factions": [{"id": "crown", "name": "The Crown", "reputation": 10}]}
    )
    assert c.factions["crown"].name == "The Crown" and c.factions["crown"].reputation == 10


def test_record_decision_persists_to_snapshot(cid):
    out = server.record_decision(
        cid, "Spare Quill", options=["spare", "kill"], chosen="spare",
        rationale="she's a refugee", actor_ids=["hero"],
    )
    assert out["chosen"] == "spare"
    c = store.load_campaign(cid)
    assert len(c.decisions) == 1
    assert c.decisions[0].summary == "Spare Quill" and c.decisions[0].chosen == "spare"
