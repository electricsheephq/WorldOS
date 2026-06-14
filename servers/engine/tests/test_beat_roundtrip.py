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
    # consequences_due (F14-4) is always present; events is the throttled view (SYN-04).
    assert set(sc) == {
        "durable", "director", "events", "companion_arcs", "consequences_due", "state"
    }
    # Each DELEGATED section is byte-equal to calling the tool directly (state can
    # carry no time-varying fields here, so a direct compare is safe).
    assert sc["state"] == server.get_state(cid)
    assert sc["director"] == server.get_campaign_director(cid)
    # companion_arc is idempotent across beats, so a second call matches too.
    assert sc["companion_arcs"] == server.check_companion_arc(cid)
    # The throttled events view (SYN-04): empty fixture -> no events surfaced/stubbed.
    assert sc["events"] == {
        "events": [], "presented": [], "manual_queued": 0, "free_form": True
    }
    assert sc["consequences_due"] == []  # nothing scheduled in the fixture


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


def test_scene_context_recent_narration_spans_multiple_sessions(cid):
    """DEFECT 2 — the lean case: under fast-turn play each beat opens a FRESH
    session, so prose lives across several session logs. recent_narration must read
    the campaign WIDE and return the last N player-facing beats regardless of which
    session wrote them — the prior pre-fix single-session read returned [] here
    (the current session's log was empty).
    """
    # Session 1: a couple of player-facing beats.
    server.log_event(cid, kind="narration", text="The market wakes at dawn.")
    server.log_event(cid, kind="dialogue", text="Coin first.", speaker="Sael")

    # Roll over to a FRESH session (this is what lean/fast-turn does each beat).
    server.start_session(cid, title="next beat")
    c = store.load_campaign(cid)
    assert len(c.session_ids) >= 2  # we are genuinely on a new session now

    # Session 2: more beats (plus bookkeeping that must be dropped).
    server.log_event(cid, kind="roll", text="Insight 12")  # dropped
    server.log_event(cid, kind="narration", text="A cloaked figure watches.")
    server.log_event(cid, kind="dialogue", text="You're being followed.", speaker="Sael")

    sc = server.scene_context(cid, recent_narration=3)
    rn = sc["recent_narration"]
    # Last 3 player-facing beats, in chronological order, SPANNING both sessions:
    # the tail crosses the session boundary (one from session 1, two from session 2).
    assert [e["text"] for e in rn] == [
        "Coin first.",
        "A cloaked figure watches.",
        "You're being followed.",
    ]
    assert rn[0]["speaker"] == "Sael"  # speaker preserved across the session boundary
    assert "speaker" not in rn[1]  # narration has no speaker

    # And a wide-enough window returns ALL player-facing beats from both sessions
    # in order (the system/roll rows from start_session + the explicit roll dropped).
    wide = server.scene_context(cid, recent_narration=99)["recent_narration"]
    assert [e["text"] for e in wide] == [
        "The market wakes at dawn.",
        "Coin first.",
        "A cloaked figure watches.",
        "You're being followed.",
    ]


# ── SYN-08 / F14-17 (issue #805): recent_narration byte-cap is DEFAULT-OFF ──
# F14-17: bounding the WINDOW (last-N) is lossless and stays on; byte-capping the
# CONTENT drops story, so it is DEFAULT-OFF (story is the north star) and only
# engages when WORLDOS_RECENT_NARRATION_MAX_CHARS is set. With the cap OFF the
# tail is verbatim (today's behavior, byte-identical). The read also now uses the
# bounded tail walk internally (F07-11) but returns the SAME last-N rows.
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F14-17, F07-11, SYN-08).


def test_scene_context_recent_narration_verbatim_by_default(cid):
    """DEFAULT-OFF byte-cap: with no env knob set, a long narration beat comes back
    VERBATIM (only the count is bounded) — no truncation creeps into the lean DM's
    story memory."""
    long_text = "The cathedral bell tolled. " * 60  # ~1.6KB, well over any cap
    server.log_event(cid, kind="narration", text=long_text.strip())
    rn = server.scene_context(cid, recent_narration=1)["recent_narration"]
    assert rn[-1]["text"] == long_text.strip()  # byte-identical, not truncated


def test_scene_context_recent_narration_byte_cap_opt_in(cid, monkeypatch):
    """When WORLDOS_RECENT_NARRATION_MAX_CHARS is set, each returned beat is soft-
    capped to that many chars (opt-in, wrapper-tunable per the F14-17 spec)."""
    monkeypatch.setenv("WORLDOS_RECENT_NARRATION_MAX_CHARS", "120")
    long_text = "The cathedral bell tolled. " * 60
    server.log_event(cid, kind="narration", text=long_text.strip())
    rn = server.scene_context(cid, recent_narration=1)["recent_narration"]
    assert len(rn[-1]["text"]) <= 140  # ~120 cap + a little boundary slack
    assert rn[-1]["text"].startswith("The cathedral bell tolled.")


def test_scene_context_recent_narration_cap_keeps_short_verbatim(cid, monkeypatch):
    """Even with the cap engaged, a short beat is untouched (the cap is a CEILING,
    never padding or rewriting)."""
    monkeypatch.setenv("WORLDOS_RECENT_NARRATION_MAX_CHARS", "120")
    server.log_event(cid, kind="dialogue", text="Halt!", speaker="Guard")
    rn = server.scene_context(cid, recent_narration=1)["recent_narration"]
    assert rn[-1] == {"text": "Halt!", "speaker": "Guard"}


# ── scene_context: the pinned durable continuity threads ─────────────────────


def test_scene_context_durable_threads_present(cid):
    """The durable pin carries the continuity-critical standing threads with stable
    shapes (open_quests + objectives, met-NPC relationships, companion bonds,
    faction gauges, set flags) so a transcript-free re-ground loses nothing."""
    dur = server.scene_context(cid)["durable"]
    # The always-present durable keys. `camp_available` (F06-5) is ADDITIVE — present only when
    # living companions are with the party and out of combat (an advisory the DM can act on).
    assert {
        "open_quests",
        "npc_relationships",
        "companions",
        "factions",
        "flags",
    } <= set(dur)
    assert set(dur) - {
        "open_quests", "npc_relationships", "companions", "factions", "flags",
    } <= {"camp_available"}

    # open_quests: every non-completed/failed quest with its still-open objectives;
    # mirrors get_state's active_quests ids but adds the objective continuity.
    c = store.load_campaign(cid)
    open_ids = {q.id for q in c.quests.values() if q.status not in ("completed", "failed")}
    assert {q["id"] for q in dur["open_quests"]} == open_ids
    for q in dur["open_quests"]:
        assert set(q) == {"id", "title", "status", "open_objectives"}

    # companions: standing bond shape (gauge + arc/betrayal flags), one row per companion.
    # `quest_arcs` (F06-10) is ADDITIVE — present only when the companion owns a personal quest arc.
    comp_ids = {ch.id for ch in c.characters.values() if ch.kind == "companion"}
    assert {x["id"] for x in dur["companions"]} == comp_ids
    for x in dur["companions"]:
        assert {"id", "name", "attitude_value", "has_arc", "has_betrayal_agenda"} <= set(x)
        assert set(x) - {
            "id", "name", "attitude_value", "has_arc", "has_betrayal_agenda",
        } <= {"quest_arcs"}

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


class _BareCharacter:
    """A stand-in 'character' that is MISSING every durable-block attribute
    (relationships, attitude_value, attitude, kind, met, arc, ...).

    Reproduces the #compact-scene-context crash class: the durable block read
    ``ch.relationships`` (a field the real Character never even had), so any
    object lacking an expected attribute threw AttributeError and took the whole
    scene_context tool down. The hardened block must getattr-default every read
    and degrade gracefully — never raise — on such an object.
    """


def test_scene_durable_threads_never_throws_on_missing_attrs(cid):
    """DEFECT 1 — scene_context must NOT throw when a character object lacks the
    attributes the durable block expects; it degrades (omits/empties) instead.

    We splice a bare object (no relationships / attitude_value / kind / met / arc)
    into the in-memory roster and derive the durable threads directly. Pre-fix this
    raised ``AttributeError: 'Character' object has no attribute 'relationships'``;
    post-fix it returns normally and simply contributes nothing for that object.
    """
    c = store.load_campaign(cid)
    baseline = server._scene_durable_threads(c)

    c.characters["bare-1"] = _BareCharacter()  # validate_assignment is off — fine in-mem
    dur = server._scene_durable_threads(c)  # must NOT raise

    # The bare object has no kind/met/arc → it joins no durable list; the real
    # threads are unchanged from the baseline (graceful degradation, not a crash).
    # (`camp_available`, F06-5, is additive and present only when companions are with the
    # party out of combat — the bare object never adds or removes it, so dur==baseline holds.)
    assert {"open_quests", "npc_relationships", "companions", "factions", "flags"} <= set(dur)
    assert set(dur) - {
        "open_quests", "npc_relationships", "companions", "factions", "flags",
    } <= {"camp_available"}
    assert dur == baseline


def test_scene_context_met_npc_without_relationships_attr(cid):
    """DEFECT 1 (end-to-end) — a MET npc surfaces in durable.npc_relationships and
    scene_context returns cleanly even though Character has no ``relationships``
    attribute at all (the exact field whose non-defensive read threw). The row
    carries the attitude that IS set and simply omits the absent relationships.
    """
    pc = server.create_character(cid, "Probe", kind="player", abilities={"charisma": 16})["id"]
    npc = server.create_character(cid, "Sael", kind="npc")["id"]
    # A successful social check flips `met` True (first-contact) and sets attitude.
    server.social_check(cid, pc, npc, "persuasion", dc=1)

    sc = server.scene_context(cid)  # must NOT raise
    row = next(n for n in sc["durable"]["npc_relationships"] if n["id"] == npc)
    assert row["name"] == "Sael"
    assert "relationships" not in row  # Character has no such attribute → omitted, not a crash
    assert "attitude" in row  # the set free-text disposition is still surfaced


def test_scene_context_does_not_deadlock(cid):
    """check_companion_arc acquires campaign_lock; the others may too. Calling them
    sequentially inside scene_context must NOT nest the (non-reentrant) flock — if
    it did, this call would hang. Reaching the assert proves it returns."""
    sc = server.scene_context(cid, recall_query="taproom")
    assert sc["state"]["id"] == cid


# ── F14-4: scene_context fires due consequences each beat ────────────────────
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F14-4). add_consequence was
# write-only — nothing on the every-beat path called consequences.due(), so 18
# writes fired 0 times. scene_context now fires (and surfaces) them under lock.


def test_scene_context_fires_due_consequences(cid):
    """A consequence scheduled for today surfaces in scene_context['consequences_due']
    AND is marked fired (engine rolls, the DM is told). Source: F14-4."""
    server.add_consequence(cid, 0, "The siege engines arrive at the gate.")
    sc = server.scene_context(cid)
    assert "consequences_due" in sc
    texts = [d["text"] for d in sc["consequences_due"]]
    assert "The siege engines arrive at the gate." in texts
    # marked fired in persisted state (idempotent — won't re-fire next beat)
    c = store.load_campaign(cid)
    assert all(con.fired for con in c.consequences)


def test_scene_context_consequences_fire_once_then_stop(cid):
    """Idempotent: a consequence fires on the beat it comes due and NOT on later beats
    (the DM isn't re-told the same world-beat every turn). Source: F14-4."""
    server.add_consequence(cid, 0, "A spared villain returns.")
    first = server.scene_context(cid)
    assert any(d["text"] == "A spared villain returns." for d in first["consequences_due"])
    second = server.scene_context(cid)
    assert second["consequences_due"] == []  # already fired -> empty, not re-told


def test_scene_context_consequences_due_empty_when_none_due(cid):
    """A not-yet-due consequence does NOT fire and the key is present-but-empty (so the
    DM can rely on it). Source: F14-4."""
    server.add_consequence(cid, 5, "Reinforcements arrive next week.")
    sc = server.scene_context(cid)
    assert sc["consequences_due"] == []
    c = store.load_campaign(cid)
    assert not any(con.fired for con in c.consequences)  # untouched


def test_scene_context_does_not_fire_worldsim_thread_beats(cid):
    """Consequences carrying a thread_id belong to worldsim (world_tick), NOT the
    authored-consequence lane — scene_context must not consume them. Source: F14-4
    (mirrors consequences.due's thread_id skip)."""
    with store.campaign_lock(cid):
        c = store.load_campaign(cid)
        from models import Consequence
        c.consequences.append(
            Consequence(trigger_day=c.day, text="A background thread ticks.",
                        thread_id="thread-x")
        )
        store.save_campaign(c)
    sc = server.scene_context(cid)
    assert sc["consequences_due"] == []  # worldsim beat left for world_tick
    c = store.load_campaign(cid)
    assert not c.consequences[0].fired  # not consumed


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


# ── F14-3 (issue #795): validate-before-write, chosen:null guard, no bare KeyError ──


def _session_log_lines(cid: str) -> list:
    """Every session-log entry across the campaign, read DISK-FIRST (atomicity guard).

    Uses ``store.read_log_all`` (which walks every ``sessions/*.jsonl`` on disk, INCLUDING
    files not yet registered in the persisted ``session_ids``), NOT a ``read_log`` over
    ``session_ids`` only. That distinction is load-bearing for the F14-3 atomicity guard
    below: a NON-atomic persist_beat appends an event to disk and THEN raises (before
    ``save_campaign``), leaving the orphan file on disk but NOT in the persisted
    ``session_ids``. A ``session_ids``-only reader would miss that orphan and report a
    FALSE GREEN (the prior version of this helper did exactly that — the cellar-rats
    fixture opens no session, so the leaked beat's session id was never persisted and the
    test passed even against the buggy apply-before-validate code). read_log_all's
    defensive disk tail witnesses the leaked row, so the guard can actually fail red."""
    c = store.load_campaign(cid)
    return list(store.read_log_all(cid, getattr(c, "session_ids", None)))


def test_persist_beat_chosen_null_does_not_crash(cid):
    # The DM legitimately records a still-open decision with chosen=null. It must
    # succeed (coerced to "") instead of pydantic string_type-crashing the batch.
    out = server.persist_beat(
        cid,
        decision={"summary": "Trust the broker or walk", "chosen": None},
    )
    assert out["decision"] is not None
    assert out["decision"]["chosen"] == ""
    after = store.load_campaign(cid)
    assert after.decisions[-1].chosen == ""


def test_persist_beat_decision_null_str_fields_coerced(cid):
    # summary / rationale passed as null must coerce, not crash (same latent class).
    out = server.persist_beat(
        cid,
        decision={"summary": None, "options": None, "chosen": None,
                  "rationale": None, "actor_ids": None},
    )
    assert out["decision"] is not None
    after = store.load_campaign(cid)
    d = after.decisions[-1]
    assert d.summary == "" and d.chosen == "" and d.rationale == ""


def test_persist_beat_bad_memory_id_is_actionable_not_bare_keyerror(cid):
    # A mis-keyed / unknown memories character_id must yield an ACTIONABLE error
    # (names the section + index + did-you-mean), never a bare KeyError 'character_id'.
    with pytest.raises(Exception) as ei:
        server.persist_beat(cid, memories=[{"fact": "no id here"}])
    msg = str(ei.value)
    assert "character_id" not in msg or "memories" in msg  # not the bare KeyError string
    assert "memories" in msg and ("index 0" in msg or "[0]" in msg)


def test_persist_beat_events_not_applied_when_later_section_fails(cid):
    """ATOMICITY guard for the F14-3 events-only window — a REAL red→green test.

    A persist_beat with a GOOD events section but a section that FAILS validation AFTER it
    (a bad memories id) must leave ZERO new session-log rows: the whole batch is validated
    BEFORE the first ``append_log``, so the events leg is never half-applied (a crash
    mid-batch otherwise leaves an orphan chronicle row that a retry then duplicates).

    This drives the atomicity path the prior version did NOT:
      * a session is OPENED FIRST (start_session), so a leaked event lands in a session that
        IS on disk AND registered — the orphan is observable to any reader, not hidden by an
        unpersisted session_ids list (the false-green the old fixture created);
      * the count is taken via read_log_all (disk tail), which sees a leaked row even if the
        save that would register its session never happens.

    Proof it would FAIL a non-atomic implementation: the OLD persist_beat appended the event
    to the session jsonl IMMEDIATELY (events → _log_session_entry → append_log), THEN
    validated memories — so against that code this asserts the leaked "ORPHAN …" row, the
    count goes up by one, and the test goes RED. Verified by hand-reproducing the
    apply-before-validate interleave (event written to disk, then the bad id raises): the
    disk tail shows before→before+1 while a session_ids-only read shows before→before. Only
    the validate-then-apply HEAD code keeps the count flat. """
    server.start_session(cid)  # open a session so a leaked event row is on the persisted path
    orphan = "ORPHAN row that must NEVER persist when a later section fails."
    before = _session_log_lines(cid)
    n_before = len(before)
    with pytest.raises(Exception):
        server.persist_beat(
            cid,
            events=[{"kind": "narration", "text": orphan}],
            memories=[{"character_id": "no-such-id", "fact": "x"}],
        )
    after = _session_log_lines(cid)
    assert len(after) == n_before  # the events leg was NOT applied (no orphan row)
    assert all(orphan not in (e.text or "") for e in after)  # specifically, the orphan text never landed


def test_persist_beat_event_text_alias_honored(cid):
    # An events item keyed `message` (log_event's alias) must log the text, not empty.
    out = server.persist_beat(
        cid, events=[{"kind": "narration", "message": "Alias text lands."}]
    )
    assert out["logged"][0]["text"] == "Alias text lands."


def test_persist_beat_event_all_text_aliases_missing_is_rejected(cid):
    # An events item with no text under any alias must be REJECTED (not empty-logged).
    before = len(_session_log_lines(cid))
    with pytest.raises(Exception):
        server.persist_beat(cid, events=[{"kind": "narration"}])
    assert len(_session_log_lines(cid)) == before  # nothing written


def test_persist_beat_memory_id_alias_resolves(cid):
    # A memories item keyed `id` (instead of character_id) must resolve, not KeyError.
    char = _a_char(cid)
    out = server.persist_beat(cid, memories=[{"id": char, "fact": "Reached via id alias."}])
    assert out["remembered"][0]["id"] == char
    assert "Reached via id alias." in store.load_campaign(cid).characters[char].memory


def test_persist_beat_remembered_return_is_not_quadratic(cid):
    # 4 facts for one character must return O(items) rows carrying the FACT + a count,
    # NOT the whole growing memory list per item (the quadratic echo).
    char = _a_char(cid)
    facts = [f"Fact number {i}." for i in range(4)]
    out = server.persist_beat(
        cid, memories=[{"character_id": char, "fact": f} for f in facts]
    )
    assert len(out["remembered"]) == 4
    for row in out["remembered"]:
        assert "memory" not in row  # no embedded growing list
        assert row["id"] == char
        assert "fact" in row and "memory_count" in row
    # the per-item fact echoes back, not the whole list
    assert [r["fact"] for r in out["remembered"]] == facts
