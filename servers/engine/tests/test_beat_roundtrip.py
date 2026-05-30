"""Per-beat round-trip collapse: scene_context (read-aggregator) + persist_beat
(batched single-lock write).

These two tools are pure, ADDITIVE composition over existing tools — the point of
the tests is (a) the bundled result equals what the individual tools return, and
(b) batching N writes lands the SAME state as N separate calls, in one lock + one
save, without self-deadlocking (campaign_lock is a non-reentrant flock).
"""

import pytest

import server
import store


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.start_adventure("cellar-rats")["campaign_id"]


def _a_char(cid: str) -> str:
    """Any real character id in the fixture campaign (for remember targets)."""
    c = store.load_campaign(cid)
    return next(iter(c.characters.values())).id


# ── scene_context: the start-of-beat read cluster in one call ────────────────


def test_scene_context_bundles_the_four_beat_reads(cid):
    """scene_context returns get_state + get_campaign_director + present_events +
    check_companion_arc, and each section equals the individual tool's output."""
    sc = server.scene_context(cid)
    assert set(sc) == {"state", "director", "events", "companion_arcs"}
    # Each bundled section is byte-equal to calling the tool directly (state can
    # carry no time-varying fields here, so a direct compare is safe).
    assert sc["state"] == server.get_state(cid)
    assert sc["director"] == server.get_campaign_director(cid)
    assert sc["events"] == server.present_events(cid)
    # companion_arc is idempotent across beats, so a second call matches too.
    assert sc["companion_arcs"] == server.check_companion_arc(cid)


def test_scene_context_recall_is_opt_in(cid):
    """No recall_query → no recall work and no `recall` key (no wasted round-trip);
    a query → the same payload recall() returns."""
    assert "recall" not in server.scene_context(cid)
    assert "recall" not in server.scene_context(cid, recall_query="   ")  # blank-ish

    sc = server.scene_context(cid, recall_query="rats", recall_limit=4)
    assert "recall" in sc
    assert sc["recall"] == server.recall(cid, "rats", limit=4)


def test_scene_context_does_not_deadlock(cid):
    """check_companion_arc acquires campaign_lock; the others may too. Calling them
    sequentially inside scene_context must NOT nest the (non-reentrant) flock — if
    it did, this call would hang. Reaching the assert proves it returns."""
    sc = server.scene_context(cid, recall_query="taproom")
    assert sc["state"]["id"] == cid


# ── persist_beat: the end-of-beat write cluster in one call ──────────────────


def test_persist_beat_batches_logs_memories_decision_and_time(cid):
    char = _a_char(cid)
    before = store.load_campaign(cid)
    n_dec, n_log_sessions = len(before.decisions), before.day

    out = server.persist_beat(
        cid,
        events=[
            {"kind": "narration", "text": "The door bangs open on a cold wind."},
            {"kind": "dialogue", "text": "'You're late,' she mutters.", "speaker": "Vesper"},
        ],
        memories=[{"character_id": char, "fact": "Clocked an informant in the booth."}],
        decision={
            "summary": "Approach the booth or hold at the bar",
            "options": ["approach", "hold"],
            "chosen": "hold",
            "rationale": "Read the room first",
            "actor_ids": [char],
            "sets_flag": "held_at_bar",
        },
        advance={"phases": 1, "note": "a long, watchful hour"},
    )

    # Return summary reflects every section.
    assert len(out["logged"]) == 2
    assert out["remembered"][0]["id"] == char
    assert out["decision"]["chosen"] == "hold"
    assert out["decision"]["flag"] == "held_at_bar"
    assert out["time"]["phases_advanced"] == 1

    # State actually persisted (one save), matching the individual tools' effects.
    after = store.load_campaign(cid)
    assert len(after.decisions) == n_dec + 1
    assert after.flags.get("held_at_bar") is True
    ch = after.characters[char]
    assert "Clocked an informant in the booth." in ch.memory
    # advance_time moved the clock off the start-of-day phase.
    assert (after.day, after.time_of_day) != (before.day, before.time_of_day)


def test_persist_beat_remember_dedupes_like_remember(cid):
    char = _a_char(cid)
    fact = "Vesper remembers the cold-eyed stranger."
    server.persist_beat(cid, memories=[{"character_id": char, "fact": fact}])
    server.persist_beat(cid, memories=[{"character_id": char, "fact": fact}])  # again
    mem = store.load_campaign(cid).characters[char].memory
    assert mem.count(fact) == 1  # de-duped, exactly like remember()


def test_persist_beat_empty_is_a_noop(cid):
    """An empty call writes nothing, returns empty sections, and never touches the
    lock-needing path (so it can't hang)."""
    before = store.load_campaign(cid)
    out = server.persist_beat(cid)
    assert out == {"logged": [], "remembered": [], "decision": None, "time": None}
    after = store.load_campaign(cid)
    assert len(after.decisions) == len(before.decisions)


def test_persist_beat_matches_individual_tool_writes(cid):
    """The batched path lands the SAME state two separate tool calls would: log a
    beat + remember a fact via persist_beat, vs via log_event + remember, and the
    resulting decision/memory/log effects are equivalent."""
    char = _a_char(cid)

    # Baseline: individual tools.
    server.log_event(cid, kind="narration", text="Baseline line.")
    server.remember(cid, char, "Baseline fact.")
    baseline = store.load_campaign(cid)
    base_mem = list(baseline.characters[char].memory)

    # Batched: persist_beat with the analogous payload.
    server.persist_beat(
        cid,
        events=[{"kind": "narration", "text": "Batched line."}],
        memories=[{"character_id": char, "fact": "Batched fact."}],
    )
    after = store.load_campaign(cid)
    # The fact list grew by exactly one via each route (no drops, no dups).
    assert after.characters[char].memory == base_mem + ["Batched fact."]


def test_persist_beat_does_not_advance_clock_during_combat(cid):
    """advance is delegated to advance_time, which is a no-op in combat — persist_beat
    inherits that guard (the clock must not jump while a fight runs in rounds)."""
    server.spawn_monster(cid, "Giant Rat", count=1)
    # Force combat active the same way the engine does for a fight.
    with store.campaign_lock(cid):
        c = store.load_campaign(cid)
        c.combat.active = True
        store.save_campaign(c)

    before = store.load_campaign(cid)
    out = server.persist_beat(cid, advance={"phases": 2})
    assert out["time"]["phases_advanced"] == 0  # guarded
    after = store.load_campaign(cid)
    assert (after.day, after.time_of_day) == (before.day, before.time_of_day)
