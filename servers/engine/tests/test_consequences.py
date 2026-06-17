"""Time-deferred consequences — the living-world clock hook (P2.6)."""

import pytest

import consequences
import server
import store
from models import Campaign


def _camp(day: int = 1) -> Campaign:
    return Campaign(title="T", day=day)


def test_schedule_then_due_when_day_arrives():
    c = _camp(day=1)
    consequences.schedule(c, 3, "The ritual completes.", note="cult left alone")
    assert consequences.due(c) == []  # day 1; triggers on day 4
    c.day = 4
    fired = consequences.due(c)
    assert len(fired) == 1 and fired[0].text == "The ritual completes."
    assert consequences.due(c) == []  # already fired -> not returned again


def test_pending_excludes_fired_and_unscheduled():
    c = _camp(day=1)
    consequences.schedule(c, 2, "A")
    consequences.schedule(c, 10, "B")
    c.day = 3
    consequences.due(c)  # fires A
    assert [p.text for p in consequences.pending(c)] == ["B"]


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign("World")["id"]


def test_consequence_tools_roundtrip(cid):
    out = server.add_consequence(cid, 2, "Reinforcements arrive.", note="alarm raised")
    assert out["trigger_day"] == out["current_day"] + 2
    assert server.check_consequences(cid)["due"] == []  # not due yet
    # advance the in-world day to the trigger
    with store.campaign_lock(cid):
        c = store.load_campaign(cid)
        c.day = out["trigger_day"]
        store.save_campaign(c)
    res = server.check_consequences(cid)
    assert len(res["due"]) == 1 and res["due"][0]["text"] == "Reinforcements arrive."
    assert res["pending"] == []
