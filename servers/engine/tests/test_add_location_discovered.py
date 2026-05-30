"""Regression: runtime add_location must default to discovered=True (#261/#371 follow-up).

PR #371 added `discovered: bool = False` to the Location model (fog-of-war seeds opt IN)
and updated seed_world to set discovered=True on day-1 regions + ingested areas. But the
runtime world-building path `add_location` was NOT updated — so a location the DM named
into the world mid-play serialized discovered=False and was hidden from the Atlas until
the party visited it (the viewer predicate _atlas_visible_location_ids shows a place only
when visited OR discovered-is-not-False). That is a behavior change vs. pre-#371, where
add_location'd places appeared immediately.

These tests pin the intended contract:
  * default add_location → discovered=True (visible immediately, even before a visit)
  * discovered=False → opt-in fog-of-war (hidden until visited)
  * the UPDATE path (location_id reuse) PRESERVES an existing place's discovered state
  * make_current still arrives the party (visible regardless of discovered)

Engine-only; additive. Does NOT touch PR #371.
"""

from __future__ import annotations

import server


def _campaign() -> str:
    return server.create_campaign("add_location discovered")["id"]


def test_add_location_default_is_discovered_and_visible_without_a_visit():
    """A second (non-current) location added with no kwargs is discovered=True and NOT
    visited — so it is Atlas-visible purely via the discovered flag (the regression fix)."""
    cid = _campaign()
    server.add_location(cid, "Harbor Start")          # first place → becomes current
    spire = server.add_location(cid, "Rumour Spire")["id"]  # second → NOT current
    loc = server._require(cid).locations[spire]
    assert loc.discovered is True
    assert loc.visited is False           # proves visibility comes from discovered, not a visit
    assert cid and loc.id == spire


def test_add_location_fog_of_war_is_opt_in():
    """discovered=False preserves the deliberate fog-of-war path: hidden until visited."""
    cid = _campaign()
    server.add_location(cid, "Harbor Start")
    rumour = server.add_location(cid, "The Underdark (rumoured)", discovered=False)["id"]
    loc = server._require(cid).locations[rumour]
    assert loc.discovered is False
    assert loc.visited is False


def test_update_path_preserves_existing_discovered_state():
    """Re-calling add_location with an existing location_id (the update path) must NOT
    clobber that place's discovered with the True default — a fog-of-war place stays fog
    when the DM only edits its description/connections."""
    cid = _campaign()
    server.add_location(cid, "Harbor Start")
    rumour = server.add_location(cid, "Hidden Vault", discovered=False)["id"]
    # Update the same place (default discovered=True) — must not reveal it.
    server.add_location(cid, "Hidden Vault", location_id=rumour, description="A sealed door.")
    loc = server._require(cid).locations[rumour]
    assert loc.discovered is False        # preserved, not clobbered to True
    assert loc.description == "A sealed door."


def test_make_current_place_is_visible_regardless_of_discovered():
    """make_current arrives the party (visited=True), so the place is visible via the
    current/visited path independent of the discovered flag."""
    cid = _campaign()
    server.add_location(cid, "Harbor Start")
    res = server.add_location(cid, "Siltwharf Steps", make_current=True)
    here = res["id"]
    loc = server._require(cid).locations[here]
    assert loc.visited is True
    assert server._require(cid).current_location_id == here
