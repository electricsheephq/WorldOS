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


def test_scene_context_bundles_the_beat_reads(cid):
    """scene_context returns the durable-threads pin + get_campaign_director +
    present_events + check_companion_arc + get_state, and each delegated section
    equals the individual tool's output (the additions never regress the bundle)."""
    sc = server.scene_context(cid)
    assert set(sc) == {"durable", "director", "events", "companion_arcs", "state"}
    # Each DELEGATED section is byte-equal to calling the tool directly (state can
    # carry no time-varying fields here, so a direct compare is safe).
    assert sc["state"] == server.get_state(cid)
    assert sc["director"] == server.get_campaign_director(cid)
    assert sc["events"] == server.present_events(cid)
    # companion_arc is idempotent across beats, so a second call matches too.
    assert sc["companion_arcs"] == server.check_companion_arc(cid)


def test_scene_context_is_durable_first_then_volatile_state(cid):
    """STABLE, cache-friendly field order: the durable threads come first and the
    volatile clock/HP (`state`) comes last."""
    keys = list(server.scene_context(cid))
    assert keys[0] == "durable"
    assert keys[-1] == "state"


def test_scene_context_durable_and_recent_reads_do_not_mutate_state(cid):
    """Sole-writer invariant: the NEW durable-threads + recent_narration paths only
    READ/derive — they must not change campaign state. (check_companion_arc, a
    pre-existing delegate, idempotently re-saves and bumps `updated_at`; that's the
    documented exception, so we compare substantive state with that churn excluded.)
    """
    server.log_event(cid, kind="narration", text="A quiet beat passes.")

    def _substantive(campaign_id):
        d = store.load_campaign(campaign_id).model_dump()
        d.pop("updated_at", None)  # the only field check_companion_arc's save touches
        return d

    before = _substantive(cid)
    server.scene_context(cid, recent_narration=5, recall_query="rats")
    assert _substantive(cid) == before


def test_scene_context_recall_is_opt_in(cid):
    """No recall_query → no recall work and no `recall` key (no wasted round-trip);
    a query → the same payload recall() returns."""
    assert "recall" not in server.scene_context(cid)
    assert "recall" not in server.scene_context(cid, recall_query="   ")  # blank-ish

    sc = server.scene_context(cid, recall_query="rats", recall_limit=4)
    assert "recall" in sc
    assert sc["recall"] == server.recall(cid, "rats", limit=4)


# ── scene_context: recent_narration (the lean-beat prose tail) ───────────────


def test_scene_context_recent_narration_default_off(cid):
    """recent_narration defaults to 0 → no `recent_narration` key and no log read
    (exactly today's behavior)."""
    assert "recent_narration" not in server.scene_context(cid)
    assert "recent_narration" not in server.scene_context(cid, recent_narration=0)


def test_scene_context_recent_narration_returns_last_n_in_order(cid):
    """recent_narration=N → the last N PLAYER-FACING beats (narration|dialogue),
    in chronological order, each {text, speaker?}; bookkeeping kinds are dropped."""
    server.log_event(cid, kind="narration", text="The cellar reeks of damp.")
    server.log_event(cid, kind="roll", text="Perception 14")  # bookkeeping → dropped
    server.log_event(cid, kind="dialogue", text="Stay close.", speaker="Vesper")
    server.log_event(cid, kind="system", text="Session note")  # bookkeeping → dropped
    server.log_event(cid, kind="narration", text="A rat skitters past.")

    sc = server.scene_context(cid, recent_narration=2)
    rn = sc["recent_narration"]
    # Only the last 2 player-facing beats, chronological, rolls/system excluded.
    assert [e["text"] for e in rn] == ["Stay close.", "A rat skitters past."]
    # speaker is present only when set.
    assert rn[0]["speaker"] == "Vesper"
    assert "speaker" not in rn[1]


def test_scene_context_recent_narration_capped_by_available(cid):
    """Asking for more than exist returns all player-facing beats (no padding)."""
    server.log_event(cid, kind="narration", text="One.")
    server.log_event(cid, kind="dialogue", text="Two.", speaker="Vesper")
    rn = server.scene_context(cid, recent_narration=99)["recent_narration"]
    assert [e["text"] for e in rn] == ["One.", "Two."]


# ── scene_context: the pinned durable continuity threads ─────────────────────


def test_scene_context_durable_threads_present(cid):
    """The durable pin carries the continuity-critical standing threads with stable
    shapes (open_quests + objectives, met-NPC relationships, companion bonds,
    faction gauges, set flags) so a transcript-free re-ground loses nothing."""
    dur = server.scene_context(cid)["durable"]
    assert set(dur) == {
        "open_quests",
        "npc_relationships",
        "companions",
        "factions",
        "flags",
    }

    # open_quests: every non-completed/failed quest with its still-open objectives;
    # mirrors get_state's active_quests ids but adds the objective continuity.
    c = store.load_campaign(cid)
    open_ids = {q.id for q in c.quests.values() if q.status not in ("completed", "failed")}
    assert {q["id"] for q in dur["open_quests"]} == open_ids
    for q in dur["open_quests"]:
        assert set(q) == {"id", "title", "status", "open_objectives"}

    # companions: standing bond shape (gauge + arc/betrayal flags), one row per companion.
    comp_ids = {ch.id for ch in c.characters.values() if ch.kind == "companion"}
    assert {x["id"] for x in dur["companions"]} == comp_ids
    for x in dur["companions"]:
        assert set(x) == {"id", "name", "attitude_value", "has_arc", "has_betrayal_agenda"}

    # factions: both engine-mutated gauges surfaced.
    for f in dur["factions"]:
        assert set(f) == {"id", "name", "reputation", "standing"}

    # npc_relationships: only NPCs the party has actually MET (not seeded strangers).
    met_npc_ids = {ch.id for ch in c.characters.values() if ch.kind == "npc" and ch.met}
    assert {n["id"] for n in dur["npc_relationships"]} == met_npc_ids


def test_scene_context_durable_open_quests_drops_completed_and_objectives(cid):
    """A completed quest leaves open_quests; a completed objective leaves
    open_objectives — the pin reflects only what's still unresolved."""
    # Author a quest with two objectives, then complete one of them.
    qid = server.add_quest(
        cid, title="Clear the cellar", objectives=["find the nest", "burn it"]
    )["id"]
    server.complete_objective(cid, qid, "find the nest")

    dur = server.scene_context(cid)["durable"]
    row = next(x for x in dur["open_quests"] if x["id"] == qid)
    assert row["open_objectives"] == ["burn it"]  # the completed objective is gone

    # Mark the whole quest complete → it drops out of open_quests entirely.
    server.complete_quest(cid, qid, status="completed")
    dur2 = server.scene_context(cid)["durable"]
    assert qid not in {x["id"] for x in dur2["open_quests"]}


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


def test_persist_beat_accepts_null_speaker(cid):
    out = server.persist_beat(
        cid,
        events=[{"kind": "narration", "text": "The lantern gutters.", "speaker": None}],
    )

    assert out["logged"][0]["speaker"] is None


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
