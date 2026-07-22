"""#1639 CAST PRESENCE — engine-side serializer discipline for the cast the rest/walk surface now
projects.

The rest surface's #1639 addition (viewer/server.py `_scene_stage`) emits resident NPCs + live
monsters at the party's current location by READING each actor's engine-owned `location_id` +
`stage_cell`. This is a PURE viewer projection — the engine stays the sole writer and gains NO new
field. This test pins that invariant from the engine side: a campaign carrying an NPC and a live
monster (each anchored to a location with a seeded `stage_cell` — the exact shape the fixture and
the arrival machinery produce) round-trips through the store BYTE-IDENTICALLY, so a pre-existing
snapshot is never rewritten and the omit-when-None `stage_cell` serializer (models.py) holds.
"""

from __future__ import annotations

import pytest

import server
import store


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("cast-presence-roundtrip")["id"]
    loc = server.add_location(cid, "The Throne Hall")["id"]
    # A party PC (no stage_cell), a resident NPC + a live monster, each anchored to the location
    # with a seeded stage_cell — the cast the #1639 rest projection reads.
    server.create_character(cid, "Aidan", kind="player", add_to_party=True)
    npc = server.create_character(cid, "Keeper Maera", kind="npc", location_id=loc)["id"]
    mon = server.spawn_monster(campaign_id=cid, name="Goblin Boss", count=1)["spawned"][0]["id"]
    c = server._require(cid)
    c.characters[npc].stage_cell = (5, 3)
    c.characters[mon].location_id = loc
    c.characters[mon].stage_cell = (10, 5)
    server.save_campaign(c)
    return cid


def _snapshot_bytes(cid: str) -> str:
    return (store._campaign_dir(cid) / "snapshot.json").read_text()


def test_cast_snapshot_round_trips_byte_identical(cid):
    """load -> save of a snapshot carrying an NPC + a live monster (with stage_cells) rewrites the
    SAME bytes — the serializer is stable, so no pure read/save churns the file."""
    before = _snapshot_bytes(cid)
    c = store.load_campaign(cid)
    server.save_campaign(c)
    after = _snapshot_bytes(cid)
    assert after == before, "a pure load->save of the cast snapshot must be byte-identical"


def test_unwalked_monster_omits_stage_cell_from_the_dump(cid):
    """A monster with NO stage_cell (never placed) serializes WITHOUT a `stage_cell` key at all —
    the omit-when-None discipline (models.py) — so an un-placed foe adds nothing to the wire and a
    legacy snapshot round-trips unchanged."""
    c = server._require(cid)
    # Mint a second, unplaced monster (location-less, no stage_cell) — spawn_monster's default.
    mon2 = server.spawn_monster(campaign_id=c.id, name="Goblin", count=1)["spawned"][0]["id"]
    server.save_campaign(server._require(c.id))
    dump = _snapshot_bytes(c.id)
    reloaded = store.load_campaign(c.id)
    assert reloaded.characters[mon2].stage_cell is None
    # The un-placed monster's record carries no `stage_cell` field in the JSON.
    assert '"stage_cell": null' not in dump
