"""Background world-sim (R2): standing threads advance on the clock, unwatched."""

import consequences
import worldsim
from models import Campaign


def _camp(day: int = 1) -> Campaign:
    return Campaign(title="T", day=day)


def test_seed_threads_schedules_staggered_future_beats():
    c = _camp(day=1)
    n = worldsim.seed_threads(c, ["dukedom contested", "cult recruiting", "factions maneuver"])
    assert n == 3
    beats = [x for x in c.consequences if x.thread_id]
    assert len(beats) == 3
    assert sorted(b.trigger_day for b in beats) == [4, 6, 8]   # day1 + start_day3 + i*stagger2
    assert len({b.thread_id for b in beats}) == 3              # distinct thread ids
    assert all(b.trigger_day > c.day for b in beats)           # all in the future
    # blank threads are skipped
    assert worldsim.seed_threads(_camp(), ["", "  "]) == 0


def test_tick_fires_due_and_reschedules_the_thread():
    c = _camp(day=1)
    worldsim.seed_threads(c, ["the dukedom edges toward open conflict"])
    assert worldsim.tick(c) == []          # nothing due at day 1
    c.day = 5                              # advance past the day-4 trigger
    fired = worldsim.tick(c)
    assert len(fired) == 1 and "dukedom" in fired[0].text and fired[0].fired
    # the thread re-armed itself a few days out — it keeps ticking
    pend = worldsim.pending_threads(c)
    assert len(pend) == 1
    assert pend[0].thread_id == fired[0].thread_id
    assert pend[0].trigger_day == 5 + worldsim._RECUR_DAYS


def test_tick_leaves_plain_consequences_to_the_consequence_engine():
    # world-sim must fire ONLY thread beats; ordinary consequences belong to consequences.due
    c = _camp(day=10)
    consequences.schedule(c, 0, "a ritual completes")
    assert worldsim.tick(c) == []
    plain = [x for x in c.consequences if not x.thread_id]
    assert len(plain) == 1 and not plain[0].fired


def test_tick_respects_max_beats():
    c = _camp(day=1)
    worldsim.seed_threads(c, ["a", "b", "c", "d"])  # triggers at days 4,6,8,10
    c.day = 20                                       # now all four are due
    fired = worldsim.tick(c, max_beats=2)
    assert len(fired) == 2


def test_start_world_seeds_threads_and_downtime_surfaces_them(tmp_path, monkeypatch):
    # Integration: start_world seeds the world's standing threads as recurring beats,
    # and advancing the clock (downtime / world_tick) makes them fire unprompted.
    import server
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("sundered-reach")["campaign_id"]
    early = server.world_tick(cid)
    assert early["world_beats"] == [] and len(early["pending"]) >= 1   # seeded, not yet due
    moved = server.downtime(cid, 12)                                    # jump the clock
    assert moved["world_beats"]                                         # the world moved on its own
