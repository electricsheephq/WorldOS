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


def test_recall_is_fuzzy_not_all_terms_required(cid):
    # A natural query carries intent-words ("timeline", "creature") that aren't
    # in the stored text. Recall must still surface the relevant memory (OR/rank),
    # not return nothing because one word is missing (the old implicit-AND bug).
    server.log_event(cid, "narration", "The party crossed the ashen barrow and met a ghoul.")
    hits = server.recall(cid, "ghoul barrow timeline creature evidence")["hits"]
    assert any("ghoul" in h["text"].lower() for h in hits)


def test_recall_ranks_best_match_first(cid):
    server.log_event(cid, "narration", "A merchant sold the party some rope.")
    server.log_event(cid, "narration", "The lich raised a barrow-wight from the ashen mound.")
    # Most query terms hit the lich line -> it must outrank the rope line.
    hits = server.recall(cid, "lich barrow wight ashen mound")["hits"]
    assert hits and "lich" in hits[0]["text"].lower()


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


# ── F07-1 (issue #772): backfill skips combat/system bookkeeping ───────────────
# Combat-event rows (schema clawdnd.combat_event.v1) and the two engine session
# markers ("Session N began" / "Session ended.") are mechanical bookkeeping — they
# must NOT enter the FTS index and outrank story in recall. A DM-AUTHORED kind=system
# note (a non-marker) MUST stay indexed (SKILL.md:47 contract).


def test_backfill_skips_schema_stamped_combat_events(cid):
    # A schema-stamped combat-event row is mechanical bookkeeping — never recalled.
    server.log_event(
        cid, "combat", "Tough 1 takes 5 force damage (12 -> 7).",
        payload={"schema": "clawdnd.combat_event.v1", "target": "tough-1"},
    )
    # A narrative combat beat IS story — recallable.
    server.log_event(cid, "combat", "The obsidian wyrm coiled through the smoke.")
    hits = server.recall(cid, "wyrm smoke force damage tough")["hits"]
    texts = [h["text"].lower() for h in hits]
    assert any("wyrm" in t for t in texts)            # story survives
    assert not any("force damage" in t for t in texts)  # bookkeeping gone


def test_backfill_skips_engine_session_markers(cid):
    # The engine's own session markers are bookkeeping, not memory.
    server.start_session(cid, title="The Ashen Gate")  # logs "Session N began: ..."
    server.end_session(cid, summary="They fled the ruin.")  # logs "Session ended. ..."
    server.log_event(cid, "narration", "The ashen gate groaned open before them.")
    hits = server.recall(cid, "session began ended ashen gate")["hits"]
    texts = [h["text"].lower() for h in hits]
    assert any("ashen gate" in t for t in texts)
    assert not any(t.startswith("session ") and ("began" in t or "ended" in t) for t in texts)


def test_backfill_keeps_dm_authored_system_note(cid):
    # SKILL.md:47: a DM-authored kind=system note feeds recall. It is NOT a session
    # marker and NOT a combat event, so it MUST stay indexed.
    server.log_event(cid, "system", "The blood-moon ritual will crest at the third bell.")
    hits = server.recall(cid, "blood moon ritual third bell")["hits"]
    assert any("blood-moon ritual" in h["text"].lower() for h in hits)
