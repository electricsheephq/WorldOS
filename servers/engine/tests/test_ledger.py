"""Campaign memory ledger — recall over committed state, drift-free (P3.4)."""

import pytest

import server


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.start_adventure("embergloom-pact")["campaign_id"]


def test_recall_finds_logged_event_without_manual_backfill(cid):
    server.log_event(cid, "narration", "The party crossed the ashen barrow and met a ghoul.")
    hits = server.recall(cid, "ghoul barrow")["hits"]
    assert any("ghoul" in h["text"].lower() for h in hits)


def test_recall_rebuilds_when_state_changes(cid):
    assert server.recall(cid, "obsidian dragon")["hits"] == []  # nothing yet
    server.log_event(cid, "narration", "An obsidian dragon coiled in the dark.")
    # the log changed -> the stale index is rebuilt from committed state
    assert any("dragon" in h["text"].lower() for h in server.recall(cid, "obsidian dragon")["hits"])


def test_recall_decisions(cid):
    server.record_decision(cid, "Seal the drain", chosen="seal", rationale="spare the refugees")
    hits = server.recall_decisions(cid)["hits"]
    assert hits and hits[0]["kind"] == "decision" and "drain" in hits[0]["text"].lower()


def test_recall_npc_facts(cid):
    nid = server.create_character(cid, "Graveltongue", kind="npc")["id"]
    server.remember(cid, nid, "swore vengeance on the party")
    assert any("vengeance" in h["text"].lower() for h in server.recall_npc(cid, nid)["hits"])


def test_recall_garbage_query_is_safe(cid):
    server.log_event(cid, "narration", "Something happened.")
    assert server.recall(cid, "!@#$%^&*()")["hits"] == []  # sanitized, no crash
