"""World-clock & rest-seam correctness — P2 cluster (issue #799).

Covers the seam-level defects from the 2026-06-11 full-engine audit (unit 04):

  * F04-3  one-long-rest-per-day guard (no repeatable free instant restore)
  * F04-4  long rest clears temp HP + ends degraded-path (twin-less) concentration
  * F04-5  downtime(0)/negative is a no-op, not a clock rewind
  * F04-6  add_location(make_current, advance_time) runs travel_to's sibling seams
           (strategic tick + effect expiry + a destination wander roll)
  * F04-7  long_rest ticks the world (beats / backlog / strategic) once per overnight
  * F04-8  the wandering-combat picker is level-banded before the seeded draw

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (Part C, unit 04).
"""

import random

import pytest

import encounter
import server
import wander
from models import ActiveEffect, Character, Consequence


def _pc(cid, name="Hero", **kw):
    return server.create_character(cid, name, kind="player", class_name="Fighter",
                                   apply_srd_defaults=True, **kw)["id"]


# =========================================================================
# F04-3 — one long rest per 24h (calendar day) guard
# =========================================================================
def test_f04_3_second_long_rest_same_day_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Guard")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 3, "evening"
    server.save_campaign(c)
    pc = _pc(cid, "Karlach")
    first = server.long_rest(cid, pc)
    assert first.get("ok", True) is not False  # the first rest succeeds
    assert (first["day"], first["time_of_day"]) == (4, "morning")
    # a SECOND long rest the same calendar day (day 4 morning) is refused, no state change
    before = server.get_character(cid, pc)
    second = server.long_rest(cid, pc)
    assert second["ok"] is False
    assert "already taken a long rest" in second["error"]
    assert (second["day"], second["time_of_day"]) == (4, "morning")
    assert server.get_character(cid, pc) == before  # byte-identical sheet — nothing mutated


def test_f04_3_blocked_rest_does_not_clear_exhaustion(tmp_path, monkeypatch):
    # The exploit: at morning a rest costs 0 clock time, so 6 rests would clear exhaustion
    # 6->0 instantly. The guard stops the chain after the first.
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Exhaust")["id"]
    pc = _pc(cid, "Worn")
    ch = server._require(cid)
    ch.characters[pc].exhaustion = 6
    server.save_campaign(ch)
    server.long_rest(cid, pc)  # day-1 morning rest: exhaustion 6 -> 5 (one rest = one level)
    assert server.get_character(cid, pc)["exhaustion"] == 5
    for _ in range(5):  # five more same-day attempts all blocked
        out = server.long_rest(cid, pc)
        assert out["ok"] is False
    assert server.get_character(cid, pc)["exhaustion"] == 5  # still 5 — not driven to 0


def test_f04_3_next_day_rest_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("NextDay")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 1, "evening"
    server.save_campaign(c)
    pc = _pc(cid, "Shadowheart")
    server.long_rest(cid, pc)                       # finishes day 2 morning
    server.advance_time(cid, to="morning")          # roll to day 3 morning
    out = server.long_rest(cid, pc)                 # a genuinely new day -> allowed
    assert out.get("ok", True) is not False
    assert out["day"] == 3 and out["time_of_day"] == "morning"


def test_f04_3_party_still_converges_on_one_morning(tmp_path, monkeypatch):
    # Per-character stamps must NOT break the documented convergence: each member rests once
    # the same overnight and they all land on a single dawn (the guard only blocks a REPEAT
    # by the same member, never a different member resting that night).
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Converge")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 5, "night"
    server.save_campaign(c)
    a = _pc(cid, "Lae'zel")
    b = _pc(cid, "Gale")
    d = _pc(cid, "Wyll")
    outs = [server.long_rest(cid, x) for x in (a, b, d)]
    assert all(o.get("ok", True) is not False for o in outs)         # all three rested
    assert all((o["day"], o["time_of_day"]) == (6, "morning") for o in outs)  # one dawn
    assert server._require(cid).day == 6


# =========================================================================
# F04-4 — long rest clears temp HP and ends degraded-path concentration
# =========================================================================
def test_f04_4_long_rest_clears_temp_hp(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("TempHP")["id"]
    pc = _pc(cid, "Warded")
    c = server._require(cid)
    c.characters[pc].temp_hp = 8
    c.day, c.time_of_day = 1, "evening"  # ensure the rest actually rolls over
    server.save_campaign(c)
    out = server.long_rest(cid, pc)
    assert out["temp_hp_cleared"] == 8
    assert server.get_character(cid, pc)["temp_hp"] == 0


def test_f04_4_long_rest_ends_degraded_path_concentration(tmp_path, monkeypatch):
    # The GENUINE degraded path (the F04-4 defect): a duration-less concentration spell sets
    # ch.concentration WITHOUT registering an effect twin. No clock sweep ever clears a twin-less
    # concentration (only _commit_expiry of an effect does, and there is none) — so before this
    # fix it survived the night. Rest at MORNING (steps == 0) so the clock sweep can't even run a
    # phase advance — isolating the new rest-seam clear as the only thing that ends it.
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Conc")["id"]
    pc = _pc(cid, "Focused")
    c = server._require(cid)
    ch = c.characters[pc]
    ch.concentration = "Bless"           # set, but NO active_effects twin (degraded path)
    assert ch.active_effects == []
    c.day, c.time_of_day = 2, "morning"  # already morning -> the clock sweep won't expire anything
    server.save_campaign(c)
    out = server.long_rest(cid, pc)
    assert out.get("ok", True) is not False
    assert server.get_character(cid, pc)["concentration"] is None  # the rest seam cleared it


def test_f04_4_long_rest_ends_twinned_concentration_and_names_it(tmp_path, monkeypatch):
    # The twinned path is already handled by the clock sweep, but the rest seam must surface the
    # released effect name in expired_effects regardless of which path cleared it.
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Conc2")["id"]
    pc = _pc(cid, "Bound")
    c = server._require(cid)
    ch = c.characters[pc]
    ch.concentration = "Hold Person"
    ch.active_effects = [ActiveEffect(name="Hold Person", scale="minutes", concentration=True,
                                      rounds_remaining=10)]
    c.day, c.time_of_day = 2, "evening"
    server.save_campaign(c)
    out = server.long_rest(cid, pc)
    assert server.get_character(cid, pc)["concentration"] is None
    names = {e["name"] for e in out["expired_effects"]}
    assert "Hold Person" in names


def test_f04_4_rests_helper_clears_temp_hp_unit():
    ch = Character(name="T", max_hp=20, current_hp=20, temp_hp=5,
                   classes=[{"name": "Fighter", "level": 4}])
    out = __import__("rests").long_rest(ch)
    assert ch.temp_hp == 0 and out["temp_hp_cleared"] == 5


# =========================================================================
# F04-5 — downtime(0)/negative is a no-op, not a clock rewind
# =========================================================================
def test_f04_5_downtime_zero_does_not_rewind_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("DT0")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 3, "night"  # the bug: this would become day-3 MORNING (backward)
    server.save_campaign(c)
    out = server.downtime(cid, 0)
    assert out.get("no_op") is True and out["days_elapsed"] == 0
    persisted = server._require(cid)
    assert (persisted.day, persisted.time_of_day) == (3, "night")  # clock untouched


def test_f04_5_downtime_negative_does_not_rewind_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("DTneg")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 4, "evening"
    server.save_campaign(c)
    out = server.downtime(cid, -3)
    assert out["days_elapsed"] == 0
    assert (server._require(cid).day, server._require(cid).time_of_day) == (4, "evening")


def test_f04_5_downtime_zero_does_not_fire_a_due_thread(tmp_path, monkeypatch):
    # worldsim.tick fires a thread-beat whenever trigger_day <= day regardless of elapsed, so a
    # 0-day downtime must NOT consume (and re-arm) a standing thread beat.
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("DTthread")["id"]
    c = server._require(cid)
    c.day = 5
    c.consequences.append(Consequence(text="the bell tolls", trigger_day=5,
                                       thread_id="thread-1"))
    server.save_campaign(c)
    out = server.downtime(cid, 0)
    assert out["world_beats"] == [] and out["due_consequences"] == []
    # the thread stays armed at day 5 (NOT re-armed forward, i.e. not consumed)
    assert server._require(cid).consequences[0].trigger_day == 5


def test_f04_5_downtime_positive_still_advances(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("DT2")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 2, "evening"
    server.save_campaign(c)
    out = server.downtime(cid, 2)
    assert out["days_elapsed"] == 2
    assert (server._require(cid).day, server._require(cid).time_of_day) == (4, "morning")


# =========================================================================
# F04-6 — add_location advance path mirrors travel_to's sibling seams
# =========================================================================
def test_f04_6_add_location_advance_expires_stale_effects(tmp_path, monkeypatch):
    # A minutes-scale effect on the party member should die when add_location advances the
    # clock (previously it survived because the seam skipped the expiry sweep).
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("AL")["id"]
    pc = _pc(cid, "Caster")
    server.add_location(cid, "Start", make_current=True)
    c = server._require(cid)
    c.characters[pc].active_effects = [
        ActiveEffect(name="Bless", scale="minutes", rounds_remaining=1)]
    server.save_campaign(c)
    out = server.add_location(cid, "Siltwharf Steps", make_current=True, advance_time=True)
    assert out["arrived"] is True
    names = {e["name"] for e in out.get("expired_effects", [])}
    assert "Bless" in names
    assert server.get_character(cid, pc)["active_effects"] == []


def test_f04_6_add_location_no_advance_is_byte_identical(tmp_path, monkeypatch):
    # The non-advancing path must NOT grow the new sibling keys (additive-only on advance).
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("AL2")["id"]
    server.add_location(cid, "Hub", make_current=True)
    out = server.add_location(cid, "Side Room", make_current=True, advance_time=False,
                              connections=[])
    for k in ("strategic_events", "expired_effects", "wandering_encounter"):
        assert k not in out  # no advance ran -> no sibling keys


def test_f04_6_add_location_advance_can_stage_wander(tmp_path, monkeypatch):
    # With a forced encounter roll, the advance path stages a destination wandering encounter
    # (mirroring travel_to) — previously this seam never rolled one.
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(wander, "roll_encounter", lambda *a, **k: True)
    cid = server.create_campaign("ALwander")["id"]
    _pc(cid, "Scout", abilities={"constitution": 14})
    server.add_location(cid, "Camp", make_current=True)
    out = server.add_location(cid, "Deep Forest", make_current=True, advance_time=True,
                              region="forest")
    assert "wandering_encounter" in out
    assert out["wandering_encounter"].get("type")  # a typed payload was produced


# =========================================================================
# F04-7 — long_rest ticks the world once per overnight
# =========================================================================
def test_f04_7_long_rest_ticks_the_world(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Tick")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 4, "evening"
    # a standing thread beat due today/tomorrow should FIRE when the overnight rolls the day
    # over (previously long_rest ticked nothing, so the beat landed late at the next seam)
    c.consequences.append(Consequence(text="the cult's ritual stirs", trigger_day=5,
                                       thread_id="thread-7"))
    server.save_campaign(c)
    pc = _pc(cid, "Sleeper")
    out = server.long_rest(cid, pc)
    # the rest now exposes the world-tick keys (they were absent on the broken seam)
    assert "world_beats" in out and "world_developments" in out and "strategic_events" in out
    assert "the cult's ritual stirs" in out["world_beats"]  # the dawn beat fired during the rest
    # ...and the thread re-armed forward (the timer rolled), proving the tick really ran
    assert server._require(cid).consequences[0].trigger_day > 5


def test_f04_7_morning_rest_does_not_tick(tmp_path, monkeypatch):
    # A second member's already-morning rest (steps == 0) must NOT re-tick the world.
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Tick2")["id"]
    c = server._require(cid)
    c.day, c.time_of_day = 1, "morning"
    server.save_campaign(c)
    pc = _pc(cid, "Dawn")
    out = server.long_rest(cid, pc)  # already morning -> clock no-op -> no world tick keys
    assert "world_beats" not in out


# =========================================================================
# F04-8 — wandering-combat picker is level-banded
# =========================================================================
def test_f04_8_low_party_never_staged_a_party_wiping_solo():
    # An L1 party in an undead region must never be ambushed by a >= 2-bands-over solo
    # (the CR-5 Wraith was staged as a deadly solo against [1, 1]).
    party = [1, 1]
    order = {d: i for i, d in enumerate(("trivial",) + encounter.DIFFICULTIES)}
    want = order["medium"]
    for seed in range(200):
        spec = wander.pick_encounter(party, "the Haunted Barrow",
                                     rng=random.Random(seed))[0]
        band = order[encounter.encounter_difficulty(party, [spec["xp_each"]] * spec["count"])]
        solo = order[encounter.encounter_difficulty(party, [spec["xp_each"]])]
        assert solo < want + 2          # never a >=2-band-over solo
        assert band <= want + 1         # the staged group stays near the target band


def test_f04_8_high_party_never_staged_an_all_trivial_swarm():
    # An L15 party in a low-CR region must never field a 12-count trivial swarm.
    party = [15, 15, 15, 15]
    order = {d: i for i, d in enumerate(("trivial",) + encounter.DIFFICULTIES)}
    staged_trivial_full = 0
    for seed in range(200):
        spec = wander.pick_encounter(party, "city", rng=random.Random(seed))[0]
        band = encounter.encounter_difficulty(party, [spec["xp_each"]] * spec["count"])
        if band == "trivial" and spec["count"] == 12:
            staged_trivial_full += 1
    assert staged_trivial_full == 0


def test_f04_8_band_filter_never_empties_a_nonempty_pool():
    # Even a wildly mismatched pool/party must yield a (nearest-band) foe, never [].
    for party in ([1], [1, 1], [20, 20, 20, 20]):
        for region in ("forest", "swamp", "the Haunted Barrow", "city"):
            specs = wander.pick_encounter(party, region, rng=random.Random(3))
            assert specs and specs[0]["xp_each"] > 0


def test_f04_8_pick_still_deterministic_under_seed():
    a = wander.pick_encounter([5, 5, 5], "swamp", rng=random.Random(99))
    b = wander.pick_encounter([5, 5, 5], "swamp", rng=random.Random(99))
    assert a == b


def test_f04_8_band_pool_pure_helper_drops_overmatch():
    # Direct unit check of the filter: a chunky solo (>= 2 bands over) is dropped for a low
    # party but a budget-appropriate creature survives.
    party = [1, 1]
    pool = wander._resolved_pool("the Haunted Barrow")
    assert pool  # the region resolves creatures
    filtered = wander._level_band_pool(pool, party, "medium")
    order = {d: i for i, d in enumerate(("trivial",) + encounter.DIFFICULTIES)}
    want = order["medium"]
    for _canon, _name, xp in filtered:
        solo = order[encounter.encounter_difficulty(party, [xp])]
        assert solo < want + 2
