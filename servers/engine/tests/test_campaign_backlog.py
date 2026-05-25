"""Proactive living-world Campaign Backlog (P0 + P1).

The world advances on its OWN when in-fiction time passes — factions maneuver, NPCs arrive,
ignored threads escalate — instead of only reacting to the player (epic #60 SUBSUMED: a clock
advance is one `kind` of deterministic backlog development). P0 = the additive model + the
content seed (derived from the world's arc anchors). P1 = the PURE mechanical tick, idempotent
by elapsed days, wired into the same five time-passage tools that already call worldsim.tick.

Mirrors test_worldsim.py / test_consequences.py: direct module import + Campaign(...) for the
pure helpers; an isolated CLAWDND_STATE_DIR fixture for the tool layer.
"""

import pytest
from pydantic import ValidationError

import consequences
import content
import server
import store
import worldsim
from models import BacklogItem, Campaign, CampaignBacklog, Faction


def _camp(day: int = 1) -> Campaign:
    return Campaign(title="T", day=day)


@pytest.fixture
def cid(tmp_path, monkeypatch):
    # A world that SEEDS a backlog (sundered-reach has standing threads + factions).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.start_world("sundered-reach")["campaign_id"]


# --- P0: model is additive (an empty backlog == today's behavior) -----------------------------


def test_empty_backlog_is_present_by_default():
    # Present-by-default (not Optional) so the tick logic + viewer always see a dict-bearing
    # object, and EMPTY == today's behavior.
    c = _camp()
    assert isinstance(c.campaign_backlog, CampaignBacklog)
    assert c.campaign_backlog.items == {}
    assert c.campaign_backlog.last_tick_day == 0


def test_old_snapshot_without_backlog_roundtrips_unchanged():
    # The additive contract: an OLD snapshot dict lacking the key deserializes to the empty
    # default (round-trips byte-identically), so every existing campaign loads unchanged.
    old = {"title": "Old Hold", "day": 7, "time_of_day": "evening"}
    c = Campaign.model_validate(old)
    assert c.campaign_backlog.items == {}
    assert c.campaign_backlog.last_tick_day == 0
    # full dump -> validate -> dump is stable, and re-dumping has the empty block.
    dumped = c.model_dump(mode="json")
    assert dumped["campaign_backlog"] == {"items": {}, "last_tick_day": 0}
    again = Campaign.model_validate(dumped)
    assert again.model_dump(mode="json") == dumped


def test_backlog_item_rejects_bad_enum():
    # Strict model: a typo'd kind is rejected by pydantic (degrade-not-abort drops it at seed).
    with pytest.raises(ValidationError):
        BacklogItem(kind="bogus_kind")


def test_backlog_item_defaults():
    # A bare item is a pending, deterministic, one-shot marker.
    bi = BacklogItem(title="x")
    assert bi.status == "pending"
    assert bi.needs_llm is False
    assert bi.cadence_days == 0
    assert bi.id.startswith("blog_")


# --- P0: seeding from the world's arc anchors -------------------------------------------------


def test_seed_world_populates_backlog_from_threads_and_factions(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = content.seed_world(content.load_world_data("sundered-reach"))
    bl = c.campaign_backlog
    assert bl.items, "no backlog items seeded from a world with threads+factions"
    # last_tick_day starts at c.day so the first advance owes nothing.
    assert bl.last_tick_day == c.day
    kinds = {i.kind for i in bl.items.values()}
    assert "thread_beat" in kinds  # from the standing threads
    assert "faction_move" in kinds  # from the factions
    # EVERY item traces to a real arc anchor (goal-ancestry borrow — no free-floating noise).
    thread_ids = {x.thread_id for x in c.consequences if x.thread_id}
    anchors = set(c.factions) | thread_ids | {h.id for h in c.quest_hooks}
    assert all(i.goal_ref in anchors for i in bl.items.values())
    # The deterministic/creative split: thread escalations need a voice (enqueue), faction
    # drifts are a number (the engine applies them).
    by_kind = {}
    for i in bl.items.values():
        by_kind.setdefault(i.kind, []).append(i)
    assert all(i.needs_llm for i in by_kind["thread_beat"])
    assert all(not i.needs_llm for i in by_kind["faction_move"])
    # Trigger days are staggered into the future (don't all land at once).
    days = sorted(i.trigger_day for i in bl.items.values())
    assert days[0] > c.day and days == sorted(set(days))


def test_seed_spine_hook_becomes_world_event(tmp_path, monkeypatch):
    # baldurs-gate has quest_variants -> questgen produces a spine hook -> a world_event item.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = content.seed_world(content.load_world_data("baldurs-gate"))
    spine_ids = {h.id for h in c.quest_hooks if h.spine}
    assert spine_ids, "fixture expects at least one spine hook in baldurs-gate"
    world_events = [i for i in c.campaign_backlog.items.values() if i.kind == "world_event"]
    assert world_events, "spine hook did not seed a world_event backlog item"
    assert all(i.goal_ref in spine_ids for i in world_events)
    assert all(i.needs_llm for i in world_events)  # the arc advancing off-screen needs narration


def test_seed_skips_malformed_authored_items_without_aborting(tmp_path, monkeypatch):
    # Degrade-not-abort (the companion_seeds path, NOT the loud adventure path): a malformed
    # authored item is skipped with a diagnostic; a valid sibling survives; seed never raises.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    world = {
        "name": "Testland", "id": "testland", "premise": "x",
        "regions": [{"id": "loc-a", "name": "A"}],
        "factions": [{"id": "fac-x", "name": "X"}],
        "standing_threads": ["a standing thread"],
        "campaign_backlog": [
            {"kind": "npc_arrival", "title": "Valid", "goal_ref": "fac-x", "needs_llm": False},
            {"kind": "not_a_real_kind", "title": "bad enum"},  # skipped (bad enum)
            "not even an object",                              # skipped (not a dict)
            {"kind": "clock", "title": "Pinned", "trigger_day": 99},  # keeps its own day
        ],
    }
    c = content.seed_world(world)  # must NOT raise
    items = c.campaign_backlog.items
    titles = {i.title for i in items.values()}
    assert "Valid" in titles
    assert "Pinned" in titles
    assert "bad enum" not in titles
    # the pinned authored item kept its explicit trigger_day (not re-staggered)
    assert any(i.title == "Pinned" and i.trigger_day == 99 for i in items.values())


def test_seed_no_anchors_yields_empty_backlog(tmp_path, monkeypatch):
    # A world with no threads / factions / spine hooks -> empty backlog (today's behavior),
    # last_tick_day still initialized to c.day.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    world = {"name": "Barren", "id": "barren", "premise": "x",
             "regions": [{"id": "loc-a", "name": "A"}]}
    c = content.seed_world(world)
    assert c.campaign_backlog.items == {}
    assert c.campaign_backlog.last_tick_day == c.day


# --- P1: the mechanical tick — fire-once + idempotent by elapsed days --------------------------


def test_tick_fires_due_item_once_and_is_idempotent_same_day():
    c = _camp(day=1)
    item = BacklogItem(kind="faction_move", goal_ref="fac-x", trigger_day=4, needs_llm=False,
                       title="X moves")
    c.campaign_backlog.items[item.id] = item
    c.campaign_backlog.last_tick_day = 1

    assert worldsim.tick_backlog(c) == []  # nothing due at day 1
    assert c.campaign_backlog.last_tick_day == 1

    c.day = 5  # past the day-4 trigger
    fired = worldsim.tick_backlog(c)
    assert len(fired) == 1 and fired[0].id == item.id
    assert item.status == "resolved"  # one-shot deterministic -> resolved
    assert c.campaign_backlog.last_tick_day == 5

    # SAME day again -> idempotent no-op (no double-fire), even though five tools call this.
    assert worldsim.tick_backlog(c) == []


def test_tick_advances_on_a_new_day_but_not_a_non_day_phase():
    # elapsed <= 0 is a pure no-op (covers a phase advance that doesn't roll a new day).
    c = _camp(day=1)
    item = BacklogItem(kind="faction_move", trigger_day=2, needs_llm=False,
                       effect={"flag": "moved"}, title="m")
    c.campaign_backlog.items[item.id] = item
    c.campaign_backlog.last_tick_day = 1
    c.day = 3
    assert len(worldsim.tick_backlog(c)) == 1
    # repeated calls on day 3 (e.g. world_tick + advance_time same day) do nothing more
    assert worldsim.tick_backlog(c) == []
    assert worldsim.tick_backlog(c) == []


def test_deterministic_item_applies_its_effect():
    c = _camp(day=1)
    c.factions["fac-x"] = Faction(id="fac-x", name="X", reputation=10)
    item = BacklogItem(kind="faction_move", goal_ref="fac-x", trigger_day=2, needs_llm=False,
                       effect={"faction_id": "fac-x", "reputation_delta": "-3", "flag": "stirred"},
                       title="X stirs")
    c.campaign_backlog.items[item.id] = item
    c.campaign_backlog.last_tick_day = 1
    c.day = 5
    worldsim.tick_backlog(c)
    assert c.factions["fac-x"].reputation == 7  # the number drifted
    assert c.flags.get("stirred") is True       # the flag flipped
    assert item.summary  # a one-line "what moved" was templated for the DM


def test_recurring_item_rearms_in_place_without_growth():
    # A recurring development re-arms in place (status back to pending) — the record count
    # stays put no matter how long we play (mirrors worldsim.tick re-arming a thread).
    c = _camp(day=1)
    item = BacklogItem(kind="faction_move", trigger_day=4, cadence_days=5, needs_llm=False,
                       effect={"flag": "f"}, title="r")
    c.campaign_backlog.items[item.id] = item
    c.campaign_backlog.last_tick_day = 1
    n0 = len(c.campaign_backlog.items)
    for d in range(5, 60, 5):
        c.day = d
        worldsim.tick_backlog(c, max_events=5)
    assert len(c.campaign_backlog.items) == n0  # no append, no growth
    assert item.status == "pending"             # still live, re-armed
    assert item.trigger_day > c.day


def test_needs_llm_item_is_enqueued_not_auto_resolved():
    # A creative item is ENQUEUED (status=fired) for the later DM/agent — the engine applies
    # NO effect and invents NO prose (P2/P3 own that).
    c = _camp(day=1)
    item = BacklogItem(kind="thread_beat", goal_ref="thread-1", trigger_day=2, needs_llm=True,
                       title="escalation", note="the seal thins")
    c.campaign_backlog.items[item.id] = item
    c.campaign_backlog.last_tick_day = 1
    c.day = 5
    fired = worldsim.tick_backlog(c)
    assert len(fired) == 1
    assert item.status == "fired"   # enqueued, NOT resolved
    assert item.summary == ""       # the engine did not author prose for it


def test_tick_respects_max_events():
    c = _camp(day=1)
    for _ in range(5):
        bi = BacklogItem(kind="world_event", trigger_day=2, needs_llm=True, title="t")
        c.campaign_backlog.items[bi.id] = bi
    c.campaign_backlog.last_tick_day = 1
    c.day = 5
    assert len(worldsim.tick_backlog(c, max_events=2)) == 2


def test_capped_tick_does_not_strand_an_overdue_item():
    """L1 regression: when the per-tick cap stops us with due items still pending, the cursor
    must NOT jump to campaign.day (which would strand the strays behind the same-day idempotency
    guard until the next day-roll). It advances only to the highest day actually fired, so a
    subsequent tick on the SAME day fires the remainder — without re-firing the done ones."""
    c = _camp(day=1)
    # Five deterministic one-shots due at staggered days 2..6; all overdue at day 6.
    ids = []
    for d in range(2, 7):
        item = BacklogItem(kind="faction_move", trigger_day=d, needs_llm=False,
                           effect={"flag": f"f{d}"}, title=f"day{d}")
        c.campaign_backlog.items[item.id] = item
        ids.append((d, item.id))
    c.campaign_backlog.last_tick_day = 1
    c.day = 6

    fired1 = worldsim.tick_backlog(c, max_events=2)  # capped at 2 -> fires the two most-overdue
    assert [f.trigger_day for f in fired1] == [2, 3]
    # Cursor advanced ONLY to the highest day fired (3), NOT to campaign.day (6) — the bug.
    assert c.campaign_backlog.last_tick_day == 3

    # A SAME-DAY re-tick still fires the strays (not stranded), and never re-fires the done ones.
    fired2 = worldsim.tick_backlog(c, max_events=2)
    assert [f.trigger_day for f in fired2] == [4, 5]  # the next two, the day-2/3 ones are guarded
    assert c.campaign_backlog.last_tick_day == 5

    fired3 = worldsim.tick_backlog(c, max_events=2)  # the last stray drains; not capped now
    assert [f.trigger_day for f in fired3] == [6]
    assert c.campaign_backlog.last_tick_day == 6  # fully drained today -> cursor reaches the day
    # All five fired exactly once.
    assert all(c.campaign_backlog.items[i].status == "resolved" for _, i in ids)
    assert worldsim.tick_backlog(c, max_events=2) == []  # idempotent now everything is done


def test_tick_backlog_does_not_touch_consequences():
    # The backlog is a STRICT sibling of the narrative-beat layer: tick_backlog never reads or
    # mutates c.consequences and is never consumed by consequences.due.
    c = _camp(day=1)
    consequences.schedule(c, 0, "a ritual completes")
    worldsim.seed_threads(c, ["a thread"])
    before = [(x.id, x.trigger_day, x.fired, x.thread_id) for x in c.consequences]
    item = BacklogItem(kind="faction_move", trigger_day=2, needs_llm=False, title="m")
    c.campaign_backlog.items[item.id] = item
    c.campaign_backlog.last_tick_day = 1
    c.day = 5
    worldsim.tick_backlog(c)
    after = [(x.id, x.trigger_day, x.fired, x.thread_id) for x in c.consequences]
    assert before == after  # consequences untouched
    # and the plain consequence is still owed to consequences.due
    assert any(x.text == "a ritual completes" for x in consequences.due(_reset_day(c)))


def _reset_day(c: Campaign) -> Campaign:
    c.day = 10
    return c


def test_pending_backlog_lists_unfired_future_items():
    c = _camp(day=1)
    due = BacklogItem(kind="faction_move", trigger_day=1, needs_llm=False, title="due")
    future = BacklogItem(kind="faction_move", trigger_day=9, needs_llm=False, title="future")
    c.campaign_backlog.items[due.id] = due
    c.campaign_backlog.items[future.id] = future
    pend = worldsim.pending_backlog(c)
    assert [p.id for p in pend] == [future.id]  # only the not-yet-due pending item


# --- P1: the wired tools surface world_developments + sole-writer persistence ------------------


def test_world_tick_returns_developments_and_pending(cid):
    out = server.world_tick(cid)
    assert "world_developments" in out and "pending_developments" in out
    assert out["world_developments"] == []          # nothing due yet at seed
    assert len(out["pending_developments"]) >= 1     # the seeded items are queued
    # each pending development carries its goal trace
    assert all("goal_ref" in p for p in out["pending_developments"])


def test_downtime_surfaces_developments_when_items_fire(cid):
    out = server.downtime(cid, 12)  # jump well past the staggered triggers
    assert out["world_developments"], "downtime did not surface fired developments"


def test_advance_time_surfaces_developments_and_is_idempotent(cid):
    # Roll a day -> a development fires; the no-day-roll phase move is a no-op.
    first = None
    for _ in range(8):  # roll enough days to fire something
        res = server.advance_time(cid, phases=4)
        if res["world_developments"]:
            first = res
            break
    assert first is not None, "advance_time never surfaced a development across rolled days"
    # a phases=0 call cannot advance the backlog (idempotent)
    assert server.advance_time(cid, phases=0)["world_developments"] == []


def test_travel_to_surfaces_developments_when_an_item_is_due(cid):
    # A time-advancing travel rides the same seam (worldsim.tick_backlog beside worldsim.tick).
    # A single travel phase rarely rolls a whole day, so to exercise the wire deterministically
    # we put the clock a few days ahead of the cursor (as if days passed off the travel path) and
    # confirm the day-rolling travel fires the now-due backlog through the tool.
    with store.campaign_lock(cid):
        c = store.load_campaign(cid)
        dest = next((x for x in c.locations[c.current_location_id].connections if x in c.locations), None)
        if dest is None:
            pytest.skip("start location has no reachable exits in this seed")
        soonest = min(i.trigger_day for i in c.campaign_backlog.items.values())
        c.day = soonest                 # the in-world day has reached the soonest development...
        c.campaign_backlog.last_tick_day = soonest - 1  # ...but the backlog hasn't ticked it yet
        c.time_of_day = "night"         # so the travel phase advance rolls a fresh day too
        store.save_campaign(c)
    out = server.travel_to(cid, dest, advance_time=True)
    assert out.get("world_developments"), "travel did not surface a due development through the wire"


def test_backlog_state_persists_via_save(cid):
    # Sole-writer: the engine mutates under the lock + save_campaign; reload from disk shows the
    # advanced cursor + fired/re-armed items (no hand-edit of snapshot.json anywhere).
    seeded_cursor = store.load_campaign(cid).campaign_backlog.last_tick_day
    server.downtime(cid, 12)
    c = store.load_campaign(cid)  # fresh read from disk
    # The cursor ADVANCED and persisted. A 12-day jump can fire more items than the per-tick cap
    # (downtime caps at 2), so the cursor may legitimately land BELOW c.day — only as far as the
    # highest day actually fired — leaving the strays for the next tick (L1: a capped tick must
    # not strand a due item by claiming it drained the whole day). It never regresses or overruns.
    assert seeded_cursor < c.campaign_backlog.last_tick_day <= c.day
    # at least one seeded item left its pending-at-seed state (fired, resolved, or re-armed past
    # the original trigger) — the world demonstrably advanced and persisted.
    statuses = [i.status for i in c.campaign_backlog.items.values()]
    assert any(s in ("fired", "resolved") for s in statuses) or any(
        i.trigger_day > c.campaign_backlog.last_tick_day for i in c.campaign_backlog.items.values()
    )
