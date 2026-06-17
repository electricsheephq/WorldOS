"""Campaign memory ledger — recall over committed state, drift-free (P3.4)."""

import pytest

import server


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
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
# Combat-event rows (schema worldos.combat_event.v1) and the two engine session
# markers ("Session N began" / "Session ended.") are mechanical bookkeeping — they
# must NOT enter the FTS index and outrank story in recall. A DM-AUTHORED kind=system
# note (a non-marker) MUST stay indexed (SKILL.md:47 contract).


def test_backfill_skips_schema_stamped_combat_events(cid):
    # A schema-stamped combat-event row is mechanical bookkeeping — never recalled.
    server.log_event(
        cid, "combat", "Tough 1 takes 5 force damage (12 -> 7).",
        payload={"schema": "worldos.combat_event.v1", "target": "tough-1"},
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


# ── SYN-10 (F07-2 + F10-5, issue #803): recall_npc split-brain ─────────────────
# The ledger indexes DIALOGUE rows with who=speaker (the engine passes the
# character's NAME — server.py:4000/4411/... speaker=ch.name) but NPC FACTS with
# who=ch.id. recall_npc filtered `WHERE who = ?` exact, so a query by ID returned
# facts ONLY and a query by NAME returned dialogue ONLY — half the memory either
# way. The fix resolves the argument against the roster (read-only load_campaign)
# and matches who IN (id, name) case-insensitively plus the ref belt, so BOTH the
# id key and the name key return BOTH the facts AND the dialogue. Free-text
# speakers (no roster match) keep single-key behavior.
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (SYN-10, F07-2, F10-5).


def _seed_split_brain(cid):
    """An NPC with a fact (indexed who=id) AND a dialogue row (indexed who=name)."""
    nid = server.create_character(cid, "Xara the Bold", kind="npc")["id"]
    name = server.load_campaign(cid).characters[nid].name
    server.remember(cid, nid, "Xara swore vengeance on the party.")
    server.log_event(cid, "dialogue", "I will not forget this.", speaker=name)
    return nid, name


def _kinds(hits):
    return {h["kind"] for h in hits}


def test_recall_npc_by_id_returns_both_facts_and_dialogue(cid):
    nid, _name = _seed_split_brain(cid)
    hits = server.recall_npc(cid, nid)["hits"]
    assert "npc_fact" in _kinds(hits), "fact missing when queried by id"
    assert "dialogue" in _kinds(hits), "dialogue missing when queried by id (split-brain)"
    assert any("vengeance" in h["text"].lower() for h in hits)
    assert any("not forget" in h["text"].lower() for h in hits)


def test_recall_npc_by_name_returns_both_facts_and_dialogue(cid):
    nid, name = _seed_split_brain(cid)
    hits = server.recall_npc(cid, name)["hits"]
    assert "npc_fact" in _kinds(hits), "fact missing when queried by name (split-brain)"
    assert "dialogue" in _kinds(hits), "dialogue missing when queried by name"
    assert any("vengeance" in h["text"].lower() for h in hits)
    assert any("not forget" in h["text"].lower() for h in hits)


def test_recall_npc_id_and_name_agree(cid):
    # The whole point of SYN-10: the two stable keys for the SAME character return
    # the SAME memory. Order may differ, but the row contents are identical.
    nid, name = _seed_split_brain(cid)
    by_id = {h["text"] for h in server.recall_npc(cid, nid)["hits"]}
    by_name = {h["text"] for h in server.recall_npc(cid, name)["hits"]}
    assert by_id == by_name and by_id


def test_recall_npc_name_match_is_case_insensitive(cid):
    nid, name = _seed_split_brain(cid)
    hits = server.recall_npc(cid, name.upper())["hits"]
    assert "npc_fact" in _kinds(hits) and "dialogue" in _kinds(hits)


def test_recall_npc_free_text_speaker_unaffected(cid):
    # A dialogue row whose speaker is NOT a roster character (an ad-hoc voice) must
    # still be retrievable by that exact free-text key, and must NOT cross-match a
    # roster NPC. (Guard 2: the resolution never invents cross-matches.)
    _seed_split_brain(cid)
    server.log_event(cid, "dialogue", "The crowd jeered from the gallows.", speaker="A Stranger")
    hits = server.recall_npc(cid, "A Stranger")["hits"]
    assert any("crowd jeered" in h["text"].lower() for h in hits)
    # The free-text key does not pull in Xara's facts/dialogue.
    assert not any("vengeance" in h["text"].lower() for h in hits)
    assert not any("not forget" in h["text"].lower() for h in hits)


# ── F07-8 (issue #803): content-true digest — no false-positive rebuilds ───────
# The ledger staleness signature keyed the snapshot by mtime:size, so EVERY state
# save (HP, clock, arc progress) flipped it -> a full DROP+reparse of the whole
# campaign even when nothing the index reads changed. The fix digests the CONTENT
# of the indexed projection (the exact strings backfill reads), so a pure-state
# mutation is NOT stale, while a memory/decision/lore change (even one that keeps
# the same list length: forget(Y)+remember(X)) IS detected.
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F07-8).
import ledger as ledger_mod  # noqa: E402


def _count_backfills(monkeypatch):
    """Spy that counts ledger.backfill calls without disabling it."""
    calls = {"n": 0}
    real = ledger_mod.backfill

    def spy(campaign_id):
        calls["n"] += 1
        return real(campaign_id)

    monkeypatch.setattr(ledger_mod, "backfill", spy)
    return calls


def test_pure_state_save_does_not_rebuild_ledger(cid, monkeypatch):
    # Prime the index, then warm it (one rebuild expected on first recall).
    server.create_character(cid, "Mara", kind="npc")
    assert server.recall(cid, "anything")["hits"] == [] or True  # warm
    calls = _count_backfills(monkeypatch)
    server.recall(cid, "warm")  # may rebuild once if first touch after priming
    base = calls["n"]
    # A pure-state mutation that changes the snapshot but NOTHING the index reads.
    pc = server.create_character(cid, "Hero", kind="player")["id"]
    server.set_hp(cid, pc, 7)  # HP is not an indexed projection
    server.recall(cid, "warm")
    assert calls["n"] == base, "pure-state HP save must not rebuild the ledger"


def test_memory_change_same_length_still_rebuilds(cid, monkeypatch):
    nid = server.create_character(cid, "Sable", kind="npc")["id"]
    server.remember(cid, nid, "Y: the old secret")
    assert any("old secret" in h["text"].lower() for h in server.recall_npc(cid, nid)["hits"])
    # forget(Y) + remember(X) keeps len(ch.memory) the same — a length/count digest
    # would MISS this. A content digest must detect it.
    server.forget(cid, nid, "Y: the old secret")
    server.remember(cid, nid, "X: the new secret")
    hits = server.recall_npc(cid, nid)["hits"]
    texts = " ".join(h["text"].lower() for h in hits)
    assert "new secret" in texts, "content change (same length) must be reindexed"
    assert "old secret" not in texts, "stale removed fact must be gone after rebuild"


def test_new_logged_beat_is_reindexed(cid, monkeypatch):
    # A genuinely-new session row legitimately needs the index updated (the log grew).
    server.log_event(cid, "narration", "A spectral hound circled the camp.")
    assert any("spectral hound" in h["text"].lower() for h in server.recall(cid, "spectral hound camp")["hits"])
    server.log_event(cid, "narration", "The hound vanished at the river ford.")
    assert any("river ford" in h["text"].lower() for h in server.recall(cid, "river ford hound")["hits"])
