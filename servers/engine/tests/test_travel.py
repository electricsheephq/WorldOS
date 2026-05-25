"""Tests for exploration/travel (Epic 6: travel half)."""

import pytest

import content
import server
import store
import travel
from models import Campaign, Location


def _camp(current="a") -> Campaign:
    """A small connected graph: a<->b, a<->c, and an isolated d."""
    return Campaign(
        title="T",
        current_location_id=current,
        locations={
            "a": Location(id="a", name="Hall", connections=["b", "c"]),
            "b": Location(id="b", name="Cellar", connections=["a"]),
            "c": Location(id="c", name="Vault", connections=["a"]),
            "d": Location(id="d", name="Sealed Room", connections=[]),
        },
    )


# --- reachable -------------------------------------------------------------


def test_reachable_lists_connected():
    names = {loc.id for loc in travel.reachable(_camp("a"))}
    assert names == {"b", "c"}


def test_reachable_empty_when_unplaced():
    assert travel.reachable(_camp(current=None)) == []


def test_reachable_skips_dangling_connection_ids():
    c = _camp("a")
    c.locations["a"].connections = ["b", "ghost"]  # ghost is not a known location
    ids = {loc.id for loc in travel.reachable(c)}
    assert ids == {"b"}  # dangling id silently dropped, no error


# --- travel_to -------------------------------------------------------------


def test_travel_to_connected_moves_and_marks_visited():
    c = _camp("a")
    out = travel.travel_to(c, "b")
    assert c.current_location_id == "b"
    assert c.locations["b"].visited is True
    assert out["first_visit"] is True and out["to_name"] == "Cellar"
    assert {r["id"] for r in out["reachable"]} == {"a"}


def test_travel_revisit_is_not_first_visit():
    c = _camp("a")
    travel.travel_to(c, "b")
    travel.travel_to(c, "a")
    out = travel.travel_to(c, "b")
    assert out["first_visit"] is False


def test_travel_to_unconnected_raises_with_exits():
    c = _camp("a")
    with pytest.raises(ValueError) as ei:
        travel.travel_to(c, "d")  # d is known but not connected to a
    msg = str(ei.value)
    assert "not connected" in msg
    assert "Cellar" in msg and "Vault" in msg  # the reachable exits are listed
    assert "Hall" not in msg  # the current location isn't an exit of itself
    assert c.current_location_id == "a"  # unchanged


def test_travel_to_unknown_raises():
    c = _camp("a")
    with pytest.raises(ValueError, match="unknown location"):
        travel.travel_to(c, "nope")


def test_travel_to_current_rejected():
    c = _camp("a")
    with pytest.raises(ValueError, match="already at"):
        travel.travel_to(c, "a")


def test_initial_placement_allows_any_known_location():
    c = _camp(current=None)
    out = travel.travel_to(c, "d")  # no current loc -> place anywhere known
    assert c.current_location_id == "d" and out["first_visit"] is True


def test_advance_time_false_keeps_clock():
    c = _camp("a")
    day0, tod0 = c.day, c.time_of_day
    travel.travel_to(c, "b", advance_time=False)
    assert (c.day, c.time_of_day) == (day0, tod0)


def test_travel_advances_one_phase_when_opted_in():
    c = _camp("a")  # starts day 1, morning
    out = travel.travel_to(c, "b", advance_time=True)
    assert out["time_of_day"] == "afternoon" and out["day"] == 1


def test_travel_default_does_not_advance_clock():
    c = _camp("a")  # short moves (room to room) shouldn't burn time by default
    out = travel.travel_to(c, "b")
    assert out["time_of_day"] == "morning" and out["day"] == 1


def test_failed_travel_is_atomic():
    """A rejected travel must not advance the clock or mark the target visited
    (guards against a refactor that moves the writes above the guards)."""
    c = _camp("a")
    d0, t0 = c.day, c.time_of_day
    with pytest.raises(ValueError):
        travel.travel_to(c, "d")  # known but unconnected
    assert (c.day, c.time_of_day) == (d0, t0)
    assert c.locations["d"].visited is False
    assert c.current_location_id == "a"


# --- advance_clock ---------------------------------------------------------


@pytest.mark.parametrize(
    "start_tod,steps,exp_tod,exp_day_delta",
    [
        ("morning", 1, "afternoon", 0),
        ("afternoon", 1, "evening", 0),
        ("evening", 1, "night", 0),
        ("night", 1, "morning", 1),  # rolls over
        ("morning", 4, "morning", 1),  # full lap
        ("morning", 5, "afternoon", 1),
        ("evening", 3, "afternoon", 1),
        ("dawn", 1, "afternoon", 0),  # unknown phase normalized to morning
    ],
)
def test_advance_clock_table(start_tod, steps, exp_tod, exp_day_delta):
    c = _camp("a")
    c.time_of_day = start_tod
    base_day = c.day
    day, tod = travel.advance_clock(c, steps)
    assert tod == exp_tod
    assert day == base_day + exp_day_delta


def test_advance_clock_noop_for_nonpositive():
    c = _camp("a")
    assert travel.advance_clock(c, 0) == (c.day, c.time_of_day)
    assert travel.advance_clock(c, -3) == (1, "morning")


# --- look_around -----------------------------------------------------------


def test_look_around_reports_location_and_exits():
    out = travel.look_around(_camp("a"))
    assert out["location"]["name"] == "Hall"
    assert {e["id"] for e in out["exits"]} == {"b", "c"}
    assert all(e["visited"] is False for e in out["exits"])


def test_look_around_unplaced():
    out = travel.look_around(_camp(current=None))
    assert out["location"] is None and out["exits"] == []


# --- content seed integration ---------------------------------------------


def test_seed_marks_start_location_visited():
    adv = {
        "title": "Mini",
        "locations": [
            {"id": "start", "name": "Gate", "connections": ["yard"]},
            {"id": "yard", "name": "Yard", "connections": ["start"]},
        ],
    }
    c = content.seed_campaign(adv)
    assert c.current_location_id == "start"
    assert c.locations["start"].visited is True
    assert c.locations["yard"].visited is False


def test_seed_rejects_non_list_connections():
    """Malformed per-location connections fail loudly at seed time rather than
    being silently coerced (the start location stays safe to load)."""
    adv = {"title": "T", "locations": [{"id": "a", "name": "A", "connections": "bcd"}]}
    with pytest.raises(Exception):
        content.seed_campaign(adv)


# --- server tool integration (real Cellar Rats graph) ----------------------


@pytest.fixture
def started(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.start_adventure("cellar-rats")["campaign_id"]


def test_look_around_and_travel_through_engine(started):
    cid = started
    here = server.look_around(cid)
    assert here["location"]["id"] == "loc-taproom"
    assert here["location"]["visited"] is True
    exit_ids = {e["id"] for e in here["exits"]}
    assert "loc-cellar-stairs" in exit_ids

    moved = server.travel_to(cid, "loc-cellar-stairs")
    assert moved["to"] == "loc-cellar-stairs" and moved["first_visit"] is True

    # the move persisted across a fresh load
    assert server.look_around(cid)["location"]["id"] == "loc-cellar-stairs"


def test_travel_through_engine_rejects_unconnected(started):
    cid = started
    # taproom only connects to cellar-stairs; the sump is not reachable directly
    with pytest.raises(Exception):
        server.travel_to(cid, "loc-sump")


# --- P3.1: map coords (presentation only) ----------------------------------


def test_location_hex_roundtrips_and_map_kind_derived():
    adv = {
        "title": "M",
        "locations": [
            {"id": "a", "name": "A", "connections": ["b"], "hex": [0, 0]},
            {"id": "b", "name": "B", "connections": ["a"], "hex": [1, 0]},
        ],
    }
    c = content.seed_campaign(adv)
    assert c.map_kind == "hex"  # derived from coords
    assert c.locations["a"].hex == (0, 0) and c.locations["b"].hex == (1, 0)


def test_map_kind_none_without_coords():
    c = content.seed_campaign({"title": "M", "locations": [{"id": "a", "name": "A"}]})
    assert c.map_kind == "none" and c.locations["a"].hex is None


# --- party-location propagation on travel (state_integrity defect 2) --------
# When the PARTY moves, current_location_id AND every party member's location_id
# (the PC + companions, who travel together) move to the new place; a standalone
# NPC not in the party stays put.


@pytest.fixture
def party_world(tmp_path, monkeypatch):
    """A campaign with two connected locations, a PC + a companion in the party, and a
    standalone NPC anchored at the start. Returns (cid, pc, companion, npc, dest_id)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Party Travel")["id"]
    # Start location, then a connected destination.
    start = server.add_location(cid, "Undercroft")["id"]  # becomes current (first location)
    dest = server.add_location(cid, "Quay", connections=[start])["id"]
    pc = server.create_character(cid, "Renn", kind="player")["id"]  # add_to_party default True
    comp = server.create_character(cid, "Cinder", kind="companion")["id"]
    npc = server.create_character(cid, "Barkeep", kind="npc", add_to_party=False)["id"]
    # Anchor everyone at the start so "stale location" is meaningful, and confirm the NPC
    # is NOT in the party.
    server.update_character(cid, pc, {"location_id": start})
    server.update_character(cid, comp, {"location_id": start})
    server.update_character(cid, npc, {"location_id": start})
    return cid, pc, comp, npc, start, dest


def test_travel_to_moves_party_members_and_leaves_npc(party_world):
    cid, pc, comp, npc, start, dest = party_world
    out = server.travel_to(cid, dest)
    assert out["to"] == dest
    # The party (PC + companion) is co-located with the new current location...
    assert server.get_state(cid)["location"]["id"] == dest
    assert server.get_character(cid, pc)["location_id"] == dest
    assert server.get_character(cid, comp)["location_id"] == dest
    assert set(out.get("party_relocated", [])) == {pc, comp}
    # ...but the standalone NPC stays where they were.
    assert server.get_character(cid, npc)["location_id"] == start


def test_travel_to_updates_current_location_id(party_world):
    cid, _pc, _comp, _npc, _start, dest = party_world
    server.travel_to(cid, dest)
    c = store.load_campaign(cid)
    assert c.current_location_id == dest


def test_add_location_make_current_propagates_party(party_world):
    cid, pc, comp, npc, _start, _dest = party_world
    # Generate a brand-new scene and arrive there in one call (the live-gen pattern).
    res = server.add_location(cid, "Siltwharf Steps", make_current=True)
    new_id = res["id"]
    assert res["arrived"] is True
    assert set(res.get("party_relocated", [])) == {pc, comp}
    assert server.get_character(cid, pc)["location_id"] == new_id
    assert server.get_character(cid, comp)["location_id"] == new_id
    # The standalone NPC didn't follow the party into the new scene.
    assert server.get_character(cid, npc)["location_id"] != new_id


def test_add_location_make_current_on_current_does_not_advance_time(party_world):
    """L3 regression: make_current=True targeting the place the party is ALREADY in (a
    self-update via location_id) must not 'arrive' anywhere, so advance_time=True must NOT
    burn a phase — no travel happened, so no time passes."""
    cid, _pc, _comp, _npc, start, _dest = party_world
    before = server.get_state(cid)
    day_before, tod_before = before["day"], before["time_of_day"]
    # Re-add the CURRENT location with make_current + advance_time -> a no-op arrival.
    res = server.add_location(cid, "Undercroft", location_id=start,
                              make_current=True, advance_time=True)
    assert res["is_current"] is True
    assert res["arrived"] is False               # we were already here
    assert res["world_beats"] == []              # the clock seam didn't fire
    assert res["world_developments"] == []
    after = server.get_state(cid)
    assert (after["day"], after["time_of_day"]) == (day_before, tod_before)  # clock unchanged


def test_add_location_make_current_to_new_place_still_advances_time(party_world):
    """The L3 gate is arrival-scoped: arriving at a genuinely NEW place with advance_time=True
    still rolls the clock one phase (the live-gen journey path is unchanged)."""
    cid, _pc, _comp, _npc, _start, _dest = party_world
    before = server.get_state(cid)
    res = server.add_location(cid, "Siltwharf Steps", make_current=True, advance_time=True)
    assert res["arrived"] is True
    after = server.get_state(cid)
    # The clock advanced (a phase moved, possibly rolling the day) — not the same instant.
    assert (after["day"], after["time_of_day"]) != (before["day"], before["time_of_day"])


def test_travel_party_propagation_pure_no_party_is_noop():
    # The pure travel.travel_to still works on a party-less Campaign graph (the engine
    # wrapper adds propagation; the pure layer just moves the pointer).
    c = _camp("a")
    travel.travel_to(c, "b")
    assert c.current_location_id == "b"  # unchanged behavior for the pure helper
