"""Multi-session continuity: start/end session + cross-session recap (P2.5)."""

import pytest

from models import SessionLogEntry
import server
import store


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("Saga")["id"]


def test_cross_session_recap_spans_sessions(cid):
    s1 = server.start_session(cid, title="The Beginning")
    assert s1["number"] == 1
    server.log_event(cid, "narration", "The heroes entered the haunted keep.")
    server.log_event(cid, "combat", "They felled the skeleton guardians.")
    server.end_session(cid, summary="Cleared the gatehouse.")

    s2 = server.start_session(cid, title="Deeper In")
    assert s2["number"] == 2
    # session 2's 'previously on' recaps session 1's story beats
    assert "Previously on" in s2["previously_on"]
    assert "haunted keep" in s2["previously_on"] or "skeleton" in s2["previously_on"]


def test_first_session_uses_new_adventure_message(cid):
    s1 = server.start_session(cid)
    assert s1["number"] == 1 and "new adventure" in s1["previously_on"].lower()


def test_log_event_autostarts_and_tracks_session(cid):
    server.log_event(cid, "narration", "An unplanned beat.")
    camp = store.load_campaign(cid)
    assert camp.active_session_id is not None
    assert camp.session_ids == [camp.active_session_id]  # tracked in history


def test_plain_session_log_entry_parses_without_payload():
    entry = SessionLogEntry.model_validate(
        {"kind": "combat", "text": "They felled the skeleton guardians."}
    )
    assert entry.payload is None


def test_session_recap_falls_back_to_last_after_end(cid):
    server.start_session(cid)
    server.log_event(cid, "narration", "A memorable deed was done.")
    server.end_session(cid)
    # active is None now, but the recap still finds the most recent session
    assert "memorable deed" in server.session_recap(cid)["recap"]
