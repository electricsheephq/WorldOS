"""M0 #431 — stable-actor-id guarantee.

A graphical renderer tweens tokens between snapshots keyed by actor id. If an actor's id
churned across snapshot regeneration, the renderer would pop/respawn the token instead of
moving it, and the render-profile's actors[].engine_actor_id foreign key (see
docs/roadmap/contracts/render-profile.schema.json) would dangle.

This locks the guarantee the contract depends on: an actor id is minted ONCE at creation
(char_<uuid12>) and is stable for the actor's lifetime — across repeated reads, across the
snapshot projection (get_state), and across unrelated mutations. Engine-only, additive,
public-API-only (mirrors test_house_biography_persistence.py); CI runs it.
"""

from __future__ import annotations

import re

import server

_CHAR_ID = re.compile(r"^char_[0-9a-f]{12}$")


def _campaign() -> str:
    return server.create_campaign("stable-actor-ids")["id"]


def test_actor_id_format_is_the_documented_char_uuid12():
    """The contract documents engine_actor_id as char_<uuid12>; lock the shape."""
    cid = _campaign()
    aid = server.create_character(cid, "Aubree", kind="player")["id"]
    assert _CHAR_ID.match(aid), f"actor id {aid!r} is not char_<12 hex>"


def test_actor_id_is_stable_across_repeated_reads():
    """Reading the same actor twice returns the same id (no per-read minting)."""
    cid = _campaign()
    aid = server.create_character(cid, "Shadowheart", kind="companion")["id"]
    first = server.get_character(cid, aid)["id"]
    second = server.get_character(cid, aid)["id"]
    assert first == aid == second


def test_actor_id_persists_into_the_snapshot_projection():
    """get_state (the read-model the surfaces project from) carries the SAME id, so a
    renderer can join /character-surface + /combat-surface tokens to the render-profile by
    this key."""
    cid = _campaign()
    aid = server.create_character(cid, "Karlach", kind="player")["id"]
    snap = server.get_state(cid)
    party_ids = {m.get("id") for m in snap.get("party", []) if isinstance(m, dict)}
    assert aid in party_ids, f"{aid!r} not found in get_state party {party_ids}"


def test_actor_id_unchanged_by_an_unrelated_mutation():
    """Creating a SECOND actor must not renumber the first — ids are stable handles, not
    positional indices. (Guards the tweening contract: adding a combatant can't make
    existing tokens jump identity.)"""
    cid = _campaign()
    a1 = server.create_character(cid, "Astarion", kind="player")["id"]
    _a2 = server.create_character(cid, "Gale", kind="companion")["id"]
    # a1 still resolves to the same record under the same id
    assert server.get_character(cid, a1)["id"] == a1
    assert server.get_character(cid, a1)["name"] == "Astarion"


def test_distinct_actors_get_distinct_ids():
    """Two actors with the SAME name still get distinct stable ids (name is not the key)."""
    cid = _campaign()
    a1 = server.create_character(cid, "Cultist", kind="monster")["id"]
    a2 = server.create_character(cid, "Cultist", kind="monster")["id"]
    assert a1 != a2
    assert _CHAR_ID.match(a1) and _CHAR_ID.match(a2)
