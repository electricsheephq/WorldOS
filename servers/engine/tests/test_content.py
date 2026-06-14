import json

import pytest

import content
import server
from models import Campaign

SYNTH = {
    "title": "Test Delve",
    "premise": "A test.",
    "hook": "Go in.",
    "level_range": [1, 2],
    "locations": [
        {"id": "loc_a", "name": "Entrance", "description": "a door", "connections": ["loc_b"]},
        {"id": "loc_b", "name": "Hall", "description": "a hall", "connections": []},
    ],
    "npcs": [
        {"id": "npc_keeper", "name": "Old Keeper", "personality": "gruff", "voice_id": "npc-elder", "attitude": "wary"}
    ],
    "scenes": [{"id": "s1", "name": "Arrival", "type": "exploration"}],
}


# ── SYN-04 content lint: no world over-uses the always-on 'manual' event trigger ──
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (SYN-04 / F05-3). A 'manual' trigger
# is ALWAYS available until resolved; a world authored with many manual events rides
# their full prose into every beat's bundle (~6.5KB at BG's 5 events). Authored events
# should carry real triggers (day_reached / reputation_at / flag_set); at most one
# manual per world is allowed (a deliberate cold-open drop). This lint red-guards the
# regression at the content seam.


def test_shipped_worlds_do_not_overuse_manual_event_trigger():
    for w in content.list_worlds():
        data = content.load_world_data(w["id"])
        events = data.get("events") or []
        if isinstance(events, dict):
            events = list(events.values())
        manual = [
            e for e in events
            if isinstance(e, dict) and (e.get("trigger") or "manual") == "manual"
        ]
        assert len(manual) <= 1, (
            f"world {w['id']!r} has {len(manual)} 'manual' events "
            f"({[e.get('id') for e in manual]}); authored events should use real "
            f"triggers (day_reached/reputation_at/flag_set) so they don't ride every "
            f"beat's bundle as full prose (SYN-04). At most 1 manual per world."
        )


def test_seed_campaign_synthetic():
    c = content.seed_campaign(SYNTH)
    assert isinstance(c, Campaign)
    assert c.title == "Test Delve"
    assert c.current_location_id == "loc_a"
    assert "loc_b" in c.locations
    keeper = c.characters["npc_keeper"]
    assert keeper.kind == "npc" and keeper.voice_id == "npc-elder"
    assert len(c.quests) == 1


def test_seed_campaign_honors_world_id_for_lore():
    # an authored adventure can declare the world it's set in → lookup_lore + era work
    c = content.seed_campaign({"title": "X", "world_id": "baldurs-gate", "era": "1492 DR"})
    assert c.world_id == "baldurs-gate" and c.era == "1492 DR"


def test_load_real_cellar_rats():
    adv = content.load_adventure_data("cellar-rats")
    assert adv["id"] == "cellar-rats"
    c = content.seed_campaign(adv)
    assert "quill" in c.characters
    assert c.characters["quill"].voice_id == "npc-rogue"
    assert c.characters["brakka"].kind == "npc"
    assert "loc-taproom" in c.locations
    assert c.current_location_id is not None
    assert len(c.quests) == 1


def test_duplicate_location_id_raises():
    with pytest.raises(ValueError):
        content.seed_campaign({"title": "X", "locations": [{"id": "dup", "name": "A"}, {"id": "dup", "name": "B"}]})


def test_duplicate_npc_id_raises():
    with pytest.raises(ValueError):
        content.seed_campaign({"title": "X", "npcs": [{"id": "n", "name": "A"}, {"id": "n", "name": "B"}]})


def test_malformed_shape_raises():
    with pytest.raises(ValueError):
        content.seed_campaign({"title": "X", "locations": "not a list"})


def test_scenes_persisted_on_seed():
    # authored scenes must survive seeding so the DM can read them at play time
    c = content.seed_campaign(SYNTH)
    assert len(c.scenes) == 1 and c.scenes[0]["name"] == "Arrival"
    # non-dict scene entries are dropped defensively, not crashed on
    c2 = content.seed_campaign({"title": "X", "scenes": [{"id": "ok"}, "garbage", 5]})
    assert len(c2.scenes) == 1 and c2.scenes[0]["id"] == "ok"


def test_get_scene_surfaces_authored_guidance(tmp_path, monkeypatch):
    # The DM was playing blind: scenes (read_aloud/dm_notes) were dropped at seed.
    # get_scene now surfaces them — incl. the previously-buried Maerith heartbreak cue.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("embergloom-pact")["campaign_id"]
    out = server.get_scene(cid)  # defaults to the current (hub) location
    assert out["count"] >= 1
    assert out["scenes"][0].get("read_aloud") and out["scenes"][0].get("dm_notes")
    assert "Maerith" in " ".join(s.get("dm_notes", "") for s in out["scenes"])
    assert server.get_scene(cid, "loc-nonexistent")["count"] == 0


def test_start_world_seeds_living_world_and_lore_is_recallable(tmp_path, monkeypatch):
    # The generative pivot: a persistent WORLD bible seeds a navigable map + factions +
    # pullable NPCs + lore, and the lore is recallable so a generated story stays
    # consistent with canon (the anti-mush guardrail at world scale).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    out = server.start_world("sundered-reach")
    cid = out["campaign_id"]
    assert out["world"] == "The Sundered Reach"
    assert len(out["regions"]) == 6 and len(out["factions"]) == 4 and len(out["npc_roster"]) == 6
    assert out["starting_at"]["id"] == "loc-brassmoor"  # first starting_option
    assert server.get_state(cid)["location"]["name"] == "Brassmoor"
    # the seeded map is navigable (Brassmoor -> Tideway is wired in the bible)
    assert server.travel_to(cid, "loc-tideway")["to_name"] == "The Tideway"
    # world lore is recallable, tagged kind=lore
    hits = server.recall(cid, "Hollow War seal Pale Choir")["hits"]
    assert hits and any(h["kind"] == "lore" for h in hits)


def test_seed_world_seeds_strategic_state_additively(capsys):
    world = {
        "id": "strategy-test",
        "name": "Strategy Test",
        "premise": "A compact strategic fixture.",
        "era": "now",
        "regions": [
            {"id": "loc-harbor", "name": "Harbor", "description": "docks", "connections": []},
            {"id": "loc-hill", "name": "Hill", "description": "watchpost", "connections": []},
        ],
        "factions": [
            {"id": "fac-civic", "name": "Civic League", "description": "wardens", "reputation": 3},
            {"id": "fac-rivals", "name": "Rival Compact", "description": "claimants", "reputation": -2},
        ],
        "npc_roster": [],
        "history": [],
        "standing_threads": [],
        "starting_options": [{"location_id": "loc-harbor", "framing": "Start at the harbor."}],
        "strategic": {
            "regions": [
                {
                    "location_id": "loc-harbor",
                    "controller_id": "fac-civic",
                    "influence": {"fac-civic": 70, "fac-rivals": 20},
                    "stability": 55,
                    "unrest": 20,
                },
                {
                    "location_id": "loc-missing",
                    "controller_id": "fac-civic",
                    "influence": {"fac-civic": 10},
                },
            ],
            "assets": [
                {
                    "id": "asset-wardens",
                    "faction_id": "fac-civic",
                    "name": "Harbor Wardens",
                    "kind": "army",
                    "location_id": "loc-harbor",
                    "strength": 2,
                },
                {
                    "id": "asset-unknown",
                    "faction_id": "fac-missing",
                    "name": "Unbound Asset",
                    "kind": "army",
                    "location_id": "loc-harbor",
                },
                {"id": "asset-bad-kind", "faction_id": "fac-civic", "name": "Bad", "kind": "fleet"},
            ],
            "clocks": [
                {
                    "id": "clock-rivals",
                    "title": "Rivals gather leverage",
                    "kind": "threat",
                    "scope": "region",
                    "region_id": "loc-hill",
                    "progress": 1,
                    "target": 6,
                    "tick_every_days": 4,
                },
                {
                    "id": "clock-unbound",
                    "title": "Missing place",
                    "kind": "threat",
                    "scope": "region",
                    "region_id": "loc-missing",
                },
            ],
            "projects": [
                {
                    "id": "proj-repair",
                    "title": "Repair the quay",
                    "kind": "construction",
                    "location_id": "loc-harbor",
                    "duration_days": 7,
                },
                {
                    "id": "proj-unbound",
                    "title": "Unknown sponsor",
                    "kind": "research",
                    "faction_id": "fac-missing",
                    "duration_days": 3,
                },
            ],
        },
    }

    c = content.seed_world(world)

    assert set(c.strategic_state.regions) == {"loc-harbor"}
    region = c.strategic_state.regions["loc-harbor"]
    assert region.controller_id == "fac-civic"
    assert region.influence == {"fac-civic": 70, "fac-rivals": 20}
    assert c.strategic_state.assets["asset-wardens"].strength == 2
    assert set(c.strategic_state.assets) == {"asset-wardens"}
    assert c.strategic_state.clocks["clock-rivals"].target == 6
    assert set(c.strategic_state.clocks) == {"clock-rivals"}
    assert c.strategic_state.projects["proj-repair"].duration_days == 7
    assert set(c.strategic_state.projects) == {"proj-repair"}

    out = capsys.readouterr().out
    assert "skipping strategic region" in out
    assert "skipping strategic asset" in out
    assert "skipping strategic clock" in out
    assert "skipping strategic project" in out


def test_seed_world_seeds_world_graph_metadata_without_authorizing_travel(capsys):
    world = {
        "id": "graph-test",
        "name": "Graph Test",
        "premise": "A compact graph fixture.",
        "era": "now",
        "regions": [
            {"id": "loc-harbor", "name": "Harbor", "description": "docks", "connections": ["loc-hill"]},
            {"id": "loc-hill", "name": "Hill", "description": "watchpost", "connections": ["loc-harbor"]},
            {"id": "loc-sealed", "name": "Sealed", "description": "locked", "connections": []},
        ],
        "factions": [],
        "npc_roster": [],
        "history": [],
        "standing_threads": [],
        "starting_options": [{"location_id": "loc-harbor", "framing": "Start at the harbor."}],
        "world_graph": {
            "seed": "graph-fixture",
            "provenance": "authored-test",
            "nodes": [
                {
                    "location_id": "loc-harbor",
                    "biome": "coast",
                    "terrain": "docks",
                    "danger": 2,
                    "atlas_layer": "settlement",
                    "tags": ["port"],
                },
                {"location_id": "loc-missing", "biome": "void"},
            ],
            "edges": [
                {
                    "from_id": "loc-harbor",
                    "to_id": "loc-hill",
                    "route_kind": "road",
                    "minutes": 45,
                    "difficulty": "easy",
                    "danger": 1,
                    "tags": ["patrolled"],
                },
                {"from_id": "loc-harbor", "to_id": "loc-sealed", "route_kind": "trail"},
                {"from_id": "loc-harbor", "to_id": "loc-missing", "route_kind": "road"},
            ],
        },
    }

    c = content.seed_world(world)

    assert set(c.world_graph.nodes) == {"loc-harbor"}
    assert c.world_graph.nodes["loc-harbor"].biome == "coast"
    assert len(c.world_graph.edges) == 1
    assert c.world_graph.edges[0].from_id == "loc-harbor"
    assert c.world_graph.edges[0].to_id == "loc-hill"
    assert "loc-sealed" not in c.locations["loc-harbor"].connections

    out = capsys.readouterr().out
    assert "skipping world_graph node" in out
    assert "not a canonical connection" in out
    assert "unknown location" in out


def test_baldurs_gate_ships_route_kind_world_graph_edges(capsys):
    # Regression guard for #381 + #380: the shipped baldurs-gate world must seed its
    # full world_graph with route_kind metadata. This breaks if (a) the world.json
    # world_graph block is dropped, (b) any edge stops matching a canonical
    # Location.connection, or (c) the WorldGraphEdge.route_kind Literal loses one of the
    # kinds used here ("street"/"bridge"/"road"/"underground"/"portal") — in which case
    # the loader silently drops that edge and the count/kinds assertions fail. #380 added
    # the Sword Coast regional edge + the 5 rumoured-POI routes (underground/portal).
    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w)

    edges = c.world_graph.edges
    pairs = {(e.from_id, e.to_id): e for e in edges}
    expected = {
        # original #381 graph
        ("loc-lower-city", "loc-upper-city"): "street",
        ("loc-lower-city", "loc-outer-city"): "street",
        ("loc-outer-city", "loc-wyrms-crossing"): "bridge",
        ("loc-wyrms-crossing", "loc-elturel"): "road",
        ("loc-wyrms-crossing", "loc-reithwin"): "road",
        ("loc-wyrms-crossing", "loc-candlekeep"): "road",
        # #380: Sword Coast regional pin + rumoured-POI routes
        ("loc-wyrms-crossing", "loc-sword-coast"): "road",
        ("loc-upper-city", "loc-steel-watch-foundry"): "underground",
        ("loc-lower-city", "loc-undercity"): "underground",
        ("loc-undercity", "loc-bhaal-temple"): "underground",
        ("loc-undercity", "loc-underdark"): "underground",
        ("loc-reithwin", "loc-underdark"): "underground",
        ("loc-wyrms-crossing", "loc-avernus-portal"): "portal",
        ("loc-elturel", "loc-avernus-portal"): "portal",
    }
    # Exactly these edges land (no edge silently dropped, none spuriously added).
    assert set(pairs) == set(expected)
    assert len(edges) == 14
    for pair, kind in expected.items():
        assert pairs[pair].route_kind == kind, (pair, pairs[pair].route_kind)

    # The "bridge" kind is the canonical Wyrm's Crossing over the Chionthar and
    # MUST survive the additive-Literal change specifically (it is the load-bearing
    # new member exercised by shipped content).
    crossing = pairs[("loc-outer-city", "loc-wyrms-crossing")]
    assert crossing.route_kind == "bridge"
    assert "chionthar" in crossing.tags

    # Every seeded edge references a canonical Location.connection in at least one
    # direction (the loader's own guard); proven here against the shipped content.
    for e in edges:
        src = c.locations[e.from_id]
        dst = c.locations[e.to_id]
        assert e.to_id in src.connections or e.from_id in dst.connections

    # No "skipping ... edge" diagnostics for the shipped BG graph — all 6 are clean.
    out = capsys.readouterr().out
    assert "skipping world_graph edge" not in out


def test_seed_world_seeds_settlement_pressure_additively(capsys):
    world = {
        "id": "settlement-test",
        "name": "Settlement Test",
        "premise": "A compact settlement fixture.",
        "era": "now",
        "regions": [
            {"id": "loc-harbor", "name": "Harbor", "description": "docks", "connections": []},
            {"id": "loc-hill", "name": "Hill", "description": "watchpost", "connections": []},
        ],
        "factions": [
            {"id": "fac-civic", "name": "Civic League", "description": "wardens", "reputation": 3},
            {"id": "fac-rivals", "name": "Rival Compact", "description": "claimants", "reputation": -2},
        ],
        "npc_roster": [
            {"id": "npc-reeve", "name": "Harbor Reeve", "role": "magistrate"},
        ],
        "history": [],
        "standing_threads": [],
        "starting_options": [{"location_id": "loc-harbor", "framing": "Start at the harbor."}],
        "settlements": [
            {
                "location_id": "loc-harbor",
                "settlement_type": "port",
                "governance": "council",
                "public_safety": "strained",
                "economy": "busy",
                "unrest": 24,
                "public_faction_ids": ["fac-civic", "fac-rivals"],
                "establishments": ["Harbor hall", "Lamp market"],
                "public_npcs": [
                    {"npc_id": "npc-reeve", "role": "hears petitions", "pressure": "Backlog of disputes"},
                ],
                "notes": "private compromise route",
            },
            {
                "location_id": "loc-missing",
                "settlement_type": "village",
            },
            {
                "location_id": "loc-hill",
                "settlement_type": "fort",
                "public_faction_ids": ["fac-missing"],
            },
            {
                "location_id": "loc-hill",
                "settlement_type": "fort",
                "public_npcs": [{"npc_id": "npc-missing", "role": "watch captain"}],
            },
            {"location_id": "loc-hill", "settlement_type": "moonbase"},
        ],
    }

    c = content.seed_world(world)

    assert set(c.strategic_state.settlements) == {"loc-harbor"}
    settlement = c.strategic_state.settlements["loc-harbor"]
    assert settlement.settlement_type == "port"
    assert settlement.location_id == "loc-harbor"
    assert settlement.public_faction_ids == ["fac-civic", "fac-rivals"]
    assert settlement.establishments == ["Harbor hall", "Lamp market"]
    assert settlement.public_npcs[0].npc_id == "npc-reeve"
    assert settlement.notes == "private compromise route"

    out = capsys.readouterr().out
    assert "skipping settlement" in out


def test_legacy_strategic_state_loads_without_settlements():
    c = Campaign.model_validate(
        {
            "id": "camp-old",
            "title": "Old Strategic Save",
            "strategic_state": {
                "regions": {},
                "assets": {},
                "clocks": {},
                "projects": {},
                "last_tick_day": 4,
            },
        }
    )

    assert c.strategic_state.settlements == {}
    assert c.strategic_state.last_tick_day == 4


def test_seed_world_rejects_unknown_start(tmp_path, monkeypatch):
    w = content.load_world_data("sundered-reach")
    with pytest.raises(ValueError, match="not a region"):
        content.seed_world(w, start_at="loc-nope")


def test_start_world_resume_continues_instead_of_orphaning(tmp_path, monkeypatch):
    # adversarial review #4: re-running start_world must not silently orphan a live world.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    first = server.start_world("sundered-reach")
    cid = first["campaign_id"]
    server.add_location(cid, "A Generated Hamlet", connections=[first["starting_at"]["id"]])
    # a fresh start warns that a campaign already exists in this world
    second = server.start_world("sundered-reach")
    assert second["campaign_id"] != cid
    assert any(e["id"] == cid for e in second.get("existing_campaigns", []))
    assert "resume_hint" in second
    # resume returns the SAME campaign with its grown state (the hamlet persists)
    resumed = server.start_world("sundered-reach", resume=cid)
    assert resumed["campaign_id"] == cid and resumed.get("resumed") is True
    assert any(r["name"] == "A Generated Hamlet" for r in resumed["regions"])
    # a bogus resume id falls through to a fresh start, no crash
    fresh = server.start_world("sundered-reach", resume="camp_nonexistent")
    assert fresh["campaign_id"] != cid and "resumed" not in fresh


def test_list_worlds_enumerates_seeds():
    # the front-door discovery tool — enumerates content/worlds/<id>/world.json
    out = server.list_worlds()["worlds"]
    ids = {w["id"] for w in out}
    assert "sundered-reach" in ids
    sr = next(w for w in out if w["id"] == "sundered-reach")
    assert sr["name"] and sr["era"] and sr["lore_pages"] >= 1 and sr["premise"]


def test_lookup_lore_returns_world_canon(tmp_path, monkeypatch):
    # the DM's on-demand "wiki": lookup_lore pulls ranked canon from the world's
    # lore corpus + reports the chronology (era), and is empty/safe off-world.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("sundered-reach")["campaign_id"]
    out = server.lookup_lore(cid, "Brassmoor capital of the Concord")
    assert out["corpus_pages"] >= 1 and out["era"]  # chronology surfaced
    assert out["hits"] and any("brassmoor" in (h["title"] + h["excerpt"]).lower() for h in out["hits"])
    # a campaign not started from a world seed returns empty, no crash
    blank = server.create_campaign("blank")["id"]
    assert server.lookup_lore(blank, "anything")["hits"] == []


# --- post-BG3 ending overlays + character origins ------------------------------------

def test_seed_world_default_is_unchanged_base_state():
    # The DEFAULT path (no ending) must reproduce today's base ENDING-overlay state EXACTLY —
    # no era rewrite, no fate facts, ending_id empty. (baldurs-gate now also ships a
    # quest_variants replayability layer that rolls outcome lore even on the base world —
    # the [Outcome]/[Hook] lines below; that's a separate, deliberate feature, asserted in
    # test_quest_variants.py. The base history + standing threads themselves are untouched.)
    w = content.load_world_data("baldurs-gate")
    base = content.seed_world(w)
    assert base.ending_id == ""
    assert base.era == str(w.get("era"))  # base chronology, untouched
    base_lore = [str(x) for x in (w.get("history", []) + w.get("standing_threads", [])) if str(x).strip()]
    # the base history + threads are all present and unchanged (no overlay retraction)
    assert base.lore[: len(base_lore)] == base_lore
    # the ONLY additions are the quest-variant outcome/hook lines (no overlay fate facts)
    extra = base.lore[len(base_lore):]
    assert all(l.startswith("[Outcome] ") or l.startswith("[Hook] ") for l in extra)
    # each roster NPC carries only its single base hook (no overlay fate fact)
    jaheira = next(ch for ch in base.characters.values() if ch.name == "Jaheira")
    assert len(jaheira.memory) == 1
    # an unknown/empty ending also falls through to the base state (no crash, no overlay change);
    # the campaign id differs so the rolled lore may differ — the base history/threads don't.
    unknown = content.seed_world(w, ending="no-such-ending")
    assert unknown.ending_id == "" and unknown.era == base.era
    assert unknown.lore[: len(base_lore)] == base_lore


def test_seed_world_roster_role_does_not_land_in_attitude(tmp_path, monkeypatch):
    # F10-4: seed_world wrote a roster NPC's prose ROLE ("High Harper, veteran of a
    # hundred years") straight into Character.attitude — the field social_check/shift_attitude
    # OVERWRITE with a track word on first influence (so the role was silently destroyed) and
    # the dashboard bar reads as a disposition. The role must NOT corrupt attitude: it lands
    # in `notes` (free text), attitude is left for the social track.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    world = {
        "id": "role-test", "name": "Role Test", "premise": "p", "era": "now",
        "regions": [{"id": "loc-a", "name": "A", "description": "d", "connections": []}],
        "starting_options": [{"location_id": "loc-a", "framing": "Start."}],
        "npc_roster": [
            {"id": "npc-harper", "name": "Veteran Harper",
             "role": "High Harper, veteran of a hundred years"},
            {"id": "npc-guard", "name": "Gate Guard",
             "role": "watch captain", "attitude": "wary"},  # explicit attitude still honored
        ],
    }
    c = content.seed_world(world)
    harper = c.characters["npc-harper"]
    # the role prose is OUT of attitude (no track-word collision on first influence) ...
    assert harper.attitude == ""
    # ... and preserved as free-text notes the DM can voice.
    assert harper.notes == "High Harper, veteran of a hundred years"

    # an EXPLICIT attitude on the roster entry is still honored (additive, not a regression):
    guard = c.characters["npc-guard"]
    assert guard.attitude == "wary"
    assert guard.notes == "watch captain"


def test_seed_world_shipped_roster_attitude_is_not_role_prose():
    # The flagship world: every seeded roster NPC's attitude is either empty or a real track
    # word — NEVER the long role prose that used to land there (Jaheira's "High Harper, veteran
    # of a hundred years"). Guards the live-world regression directly.
    from npc import ATTITUDE_TRACK
    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w)
    roster_ids = {n.get("id") for n in (w.get("npc_roster") or []) if n.get("id")}
    seeded = [c.characters[i] for i in roster_ids if i in c.characters]
    assert seeded, "expected baldurs-gate to ship a seeded roster"
    for ch in seeded:
        # attitude is empty or a known track band — never prose with spaces/commas.
        assert ch.attitude == "" or ch.attitude in ATTITUDE_TRACK, (
            f"{ch.name!r} attitude {ch.attitude!r} looks like role prose, not a track word"
        )
    jaheira = next(ch for ch in seeded if ch.name == "Jaheira")
    assert "Harper" in jaheira.notes  # the role prose is preserved as notes


def test_seed_world_ending_rewrites_era_and_lands_fate_on_npc():
    # An ending overlay OVERWRITES the era (the chronology guardrail moves to the
    # post-state) and lands each `fates` entry as a memory fact on the matching roster
    # NPC (resolved by id npc-jaheira -> Jaheira), plus a recallable lore line.
    w = content.load_world_data("baldurs-gate")
    base = content.seed_world(w)
    e = content.seed_world(w, ending="gortash-tyranny")
    assert e.ending_id == "gortash-tyranny"
    assert e.era != base.era and "tyrant" in e.era.lower()  # post-state chronology
    assert len(e.lore) > len(base.lore)  # overlay history + threads folded into lore
    jaheira = next(ch for ch in e.characters.values() if ch.name == "Jaheira")
    assert len(jaheira.memory) == 2  # base hook + the overlay fate fact
    assert any("hunted" in m.lower() or "resistance" in m.lower() for m in jaheira.memory)
    # a hero who is NOT in the npc_roster (Gale, only in lore) is still covered by a
    # recallable lore line carrying their fate
    assert any(l.startswith("[") and "Gale" in l for l in e.lore)
    # the post-state threads also got scheduled as background world-beats
    assert any("tyrant" in cq.text.lower() or "watch" in cq.text.lower() for cq in e.consequences)


def test_seed_world_ending_random_resolves_to_a_concrete_overlay():
    w = content.load_world_data("baldurs-gate")
    ids = {x["id"] for x in content.list_endings("baldurs-gate")}
    assert len(ids) >= 4  # the four core post-BG3 endings ship
    r = content.seed_world(w, ending="random")
    assert r.ending_id in ids  # "random" resolves to a concrete shipped overlay


def test_start_world_echoes_chosen_ending(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    out = server.start_world("baldurs-gate", ending="illithid-ascension")
    assert out["ending"] == "illithid-ascension"
    assert out["ending_name"] and out["ending_state"]  # DM has a one-line state to announce
    assert "illithid" in out["era"].lower() or "brain" in out["era"].lower()
    # the base (no ending) start still advertises the available overlays but seeds base
    base = server.start_world("baldurs-gate")
    assert base["ending"] == "" and {e["id"] for e in base["available_endings"]} >= {"gortash-tyranny"}


def test_list_canon_characters_playable_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate")["campaign_id"]
    # SYN-03: the surface is BOUNDED — the unfiltered call no longer dumps all ~2,076
    # records; it returns a `limit`-capped page with a `{total, returned, truncated}`
    # envelope. Query the heroes by name (`q`) instead of scanning the whole roster.
    full = server.list_canon_characters(cid)
    assert full["total"] > full["returned"] and full["truncated"] is True
    assert full["returned"] == len(full["available"]) == 100  # default page
    assert "note" in full and "find_npcs" in full["note"]
    for hero in ("Astarion", "Gale", "Karlach", "Lae'zel", "Shadowheart", "Wyll", "Halsin"):
        hit = server.list_canon_characters(cid, q=hero)["available"]
        by_name = {x["name"]: x for x in hit}
        assert hero in by_name, f"{hero} should be findable via q="
    assert server.list_canon_characters(cid, q="Astarion")["available"][0]["playable"] is False
    minsc = {x["name"]: x for x in server.list_canon_characters(cid, q="Minsc")["available"]}
    assert minsc["Minsc"]["playable"] is True
    # the playable-only filter keeps just the minor figures a player can pick up;
    # the original four plus the three new caster-party additions (Rolan, Lia, Isobel)
    for figure in ("Jaheira", "Minsc", "Withers", "Jergal", "Rolan", "Lia", "Isobel"):
        play = {x["name"] for x in server.list_canon_characters(cid, playable_only=True, q=figure)["available"]}
        assert figure in play
    # a hero is never in the PLAYABLE slice even when queried by name
    assert "Astarion" not in {
        x["name"] for x in server.list_canon_characters(cid, playable_only=True, q="Astarion")["available"]
    }


def test_list_canon_characters_envelope_and_query(tmp_path, monkeypatch):
    # SYN-03: the bounded envelope on the flagship roster — {total, returned, truncated},
    # a working `q` substring filter, and a `limit` that pages without losing the total.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate")["campaign_id"]
    capped = server.list_canon_characters(cid, limit=10)
    assert capped["returned"] == len(capped["available"]) == 10
    assert capped["total"] > 10 and capped["truncated"] is True
    # q narrows the TOTAL, not just the page
    sh = server.list_canon_characters(cid, q="shadow")
    assert sh["total"] >= 1 and all("shadow" in r["name"].lower() for r in sh["available"])
    assert sh["total"] < capped["total"]  # a substring search is a strict subset
    # limit is hard-capped at 200 even if a caller asks for more
    big = server.list_canon_characters(cid, limit=99999)
    assert big["returned"] <= 200


def test_roster_surface_excludes_origins_and_filters():
    # The canon-NPC PICKER projection: the PLAYABLE roster (origins excluded), filtered by
    # race/class/level, each row carrying the picker card fields + a portrait slug, plus the
    # facets for the filter chips. Tested against the shipped baldurs-gate roster.
    r = content.roster_surface("baldurs-gate")
    assert r["world_id"] == "baldurs-gate"
    assert r["total"] > 0 and r["characters"]
    names = {c["name"] for c in r["characters"]}
    # the seven BG3 origin heroes are NEVER offered as a playable pick
    assert not (names & {"Astarion", "Gale", "Karlach", "Lae'zel", "Shadowheart", "Wyll", "Minthara"})
    # every card carries the picker fields, with portrait_scope keyed off the file slug (id)
    c0 = r["characters"][0]
    for field in ("id", "name", "race", "class", "level", "backstory", "portrait_scope", "playable"):
        assert field in c0
    assert all(c["portrait_scope"] == "portrait-" + c["id"] for c in r["characters"])
    assert all(c["playable"] is True for c in r["characters"])
    # facets ride along, frequency-ordered (the post-BG3 roster is Human/Fighter-dense)
    assert r["facets"]["races"][0] == "Human"
    assert r["facets"]["classes"][0] == "Fighter"


def test_roster_surface_filters_and_combine():
    wiz = content.roster_surface("baldurs-gate", char_class="Wizard")
    assert wiz["total"] > 0
    assert all(c["class"] == "Wizard" for c in wiz["characters"])
    # case-insensitive
    assert content.roster_surface("baldurs-gate", char_class="wizard")["total"] == wiz["total"]
    # AND-combine: race + class is a strict subset of class alone, all matching both
    both = content.roster_surface("baldurs-gate", race="Dwarf", char_class="Wizard")
    assert both["total"] <= wiz["total"]
    assert all(c["race"] == "Dwarf" and c["class"] == "Wizard" for c in both["characters"])
    # a LIVING Dwarf-Wizard pick is offered (Hartlebury, a Flaming Fist wizard) — but the
    # canon-DEAD Dal Lightspark is NOT, even though he is a Dwarf Wizard (#305: the playable
    # surface is alive-only by default).
    assert any(c["id"] == "hartlebury" for c in both["characters"])
    assert not any(c["id"] == "dal-lightspark" for c in both["characters"])
    # level filter
    lvl = content.roster_surface("baldurs-gate", level="5")
    assert lvl["total"] > 0 and all(c["level"] == "5" for c in lvl["characters"])


def test_roster_surface_caps_the_unfiltered_roster():
    # The unfiltered playable roster is ~2,000 — too many cards to paint, so the returned list is
    # capped while `total` reports the full match. `limit<=0` disables the cap (the headless path).
    capped = content.roster_surface("baldurs-gate", limit=25)
    assert capped["total"] > capped["returned"]
    assert capped["returned"] == len(capped["characters"]) == 25
    full = content.roster_surface("baldurs-gate", limit=0)
    assert full["returned"] == full["total"] == len(full["characters"])
    assert full["total"] == capped["total"]


def test_start_character_origins(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate")["campaign_id"]
    # nobody_l1 (default) — a fresh level-1 PC, added to the party
    n = server.start_character(cid, name="Nobody")
    assert n["origin"] == "nobody_l1" and n["level"] == 1 and n["kind"] == "player"
    assert n["in_party"]
    # nobody_l1 with a class gets a real SRD level-1 sheet
    n2 = server.start_character(cid, origin="nobody_l1", name="Greenhorn", class_name="fighter")
    sheet = server.get_character(cid, n2["id"])
    assert sheet["proficiency_bonus"] == 2 and sheet["max_hp"] > 1  # SRD defaults applied
    # veteran_l5 — level 5 via the SRD class tables
    v = server.start_character(cid, origin="veteran_l5", name="Vet", class_name="ranger")
    assert v["level"] == 5
    vsheet = server.get_character(cid, v["id"])
    assert vsheet["proficiency_bonus"] == 3  # L5 proficiency
    # veteran_l5 with no class is rejected (a level-5 PC needs a class)
    assert "error" in server.start_character(cid, origin="veteran_l5", name="Bad")
    # template:<id> — a premade build from origins/*.json
    t = server.start_character(cid, origin="template:flaming-fist-deserter")
    assert t["origin"] == "template:flaming-fist-deserter" and t["level"] == 3 and t["class"] == "Fighter"
    # an explicit name overrides the template's
    t2 = server.start_character(cid, origin="template:guild-cutpurse", name="Pip")
    assert t2["name"] == "Pip" and t2["class"] == "Rogue"
    # an unknown template id errors with the available list
    bad_t = server.start_character(cid, origin="template:does-not-exist")
    assert "error" in bad_t and "available" in bad_t


def test_start_character_pickup_rejects_hero_accepts_minor(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate")["campaign_id"]
    # pickup a MINOR canon figure as the PLAYER — allowed, carries their real identity
    pm = server.start_character(cid, origin="pickup:Minsc")
    assert pm["origin"] == "pickup:Minsc" and pm["kind"] == "player" and pm["in_party"]
    sheet = server.get_character(cid, pm["id"])
    assert sheet["race"] == "Human" and sheet.get("backstory")  # canon identity carried over
    # pickup a TOP HERO as the player is REJECTED with a clear message
    ph = server.start_character(cid, origin="pickup:Astarion")
    assert "error" in ph and ph["playable"] is False
    assert "legend" in ph["error"].lower() and "npc" in ph["error"].lower()
    # SYN-03: suggests a FEW pickup-eligible names (did_you_mean), never the whole list
    assert isinstance(ph["did_you_mean"], list) and len(ph["did_you_mean"]) <= 5
    assert ph["available_count"] > 5  # the roster is large; we did NOT dump it
    assert "playable_options" not in ph  # the old full-list key is gone
    # the heroes remain encounterable as NPCs — load_canon_character(kind="npc") is
    # unrestricted (they're already seeded into the world roster by start_world)
    assert server.load_canon_character(cid, "Astarion", kind="npc").get("id") == "npc-astarion"


def test_start_character_pickup_promotes_existing_roster_npc(tmp_path, monkeypatch):
    # B-MED-1: start_world seeds Minsc as a roster NPC (npc-minsc "Minsc and Boo").
    # pickup:Minsc must PROMOTE that record to the player, NOT mint a second Minsc.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate")["campaign_id"]
    from store import load_campaign

    before = [ch for ch in load_campaign(cid).characters.values() if "minsc" in ch.name.lower()]
    assert len(before) == 1 and before[0].id == "npc-minsc" and before[0].kind == "npc"

    pm = server.start_character(cid, origin="pickup:Minsc")
    # the SAME roster record is reused (no duplicate), now the player and in the party
    assert pm["id"] == "npc-minsc" and pm["kind"] == "player" and pm["in_party"]
    assert pm.get("promoted_existing") is True

    after = [ch for ch in load_campaign(cid).characters.values() if "minsc" in ch.name.lower()]
    assert len(after) == 1, f"expected exactly ONE Minsc, got {[(c.id, c.kind) for c in after]}"
    only = after[0]
    assert only.id == "npc-minsc" and only.kind == "player" and only.id in load_campaign(cid).party
    # the canon sheet is applied to the promoted record (race + a real SRD class sheet)
    sheet = server.get_character(cid, "npc-minsc")
    assert sheet["race"] == "Human" and sheet["proficiency_bonus"] == 2  # Ranger L1 SRD


def test_ending_overlay_retracts_contradictory_base_canon():
    # B-HIGH-1: a post-state overlay and the base seed are mutually exclusive. The
    # overlay must RETRACT the base facts it supersedes, so `recall`/the ticking world-
    # sim never carry BOTH the base fact AND the contradicting overlay fact at once.
    w = content.load_world_data("baldurs-gate")
    g = content.seed_world(w, ending="gortash-tyranny")
    lore_blob = " || ".join(g.lore).lower()
    thread_blob = " || ".join(cq.text for cq in g.consequences if cq.thread_id).lower()

    # The base "Gortash dead / power vacuum / Steel Watch wrecked" facts are GONE from
    # both recallable lore AND the ticking world-beats...
    for retracted in (
        "with gortash dead and the steel watch gone",  # base power-vacuum thread
        "his death reopened the seat",                  # base "the city's rule"
        "was detonated/disabled during the battle",      # base wrecked Steel Watch
        "served the brain — and were destroyed",         # base "Gortash destroyed"
    ):
        assert retracted not in lore_blob, f"base fact not retracted from lore: {retracted!r}"
        assert retracted not in thread_blob, f"retired thread still ticking: {retracted!r}"
    # ...while the overlay's mutually-exclusive post-state IS present.
    assert "gortash rules as archduke" in lore_blob
    assert "the tyrant survived" in lore_blob

    # B-LOW-1: threads are seeded ONCE from the merged (surviving-base + overlay) set —
    # exactly one record per thread, with unique ids, so the world-sim never double-ticks.
    thread_ids = [cq.thread_id for cq in g.consequences if cq.thread_id]
    assert len(thread_ids) == len(set(thread_ids)), f"duplicate thread_ids: {thread_ids}"
    # the contradictory base power-vacuum thread was retracted, so the seeded beats are
    # the 2 surviving base threads + the overlay's 3 post-state threads = 5 (was 3+3=6).
    assert len(thread_ids) == 5

    # illithid-ascension likewise retracts the base "Netherbrain destroyed" fact (the
    # brain was CLAIMED here, not destroyed) but keeps the non-conflicting backstory.
    il = content.seed_world(w, ending="illithid-ascension")
    il_blob = " || ".join(il.lore).lower()
    assert "emerged over the lower city and was destroyed in the harbor" not in il_blob
    assert "the new absolute" in il_blob  # the overlay's claimed-the-brain post-state
    assert "enslave an elder brain" in il_blob  # non-conflicting base backstory survives
    il_ids = [cq.thread_id for cq in il.consequences if cq.thread_id]
    assert len(il_ids) == len(set(il_ids))


def test_ending_overlay_default_path_is_byte_identical():
    # The DEFAULT (no-ending) seed must be UNCHANGED by the supersedes/single-seed
    # rework: same lore, same standing-thread beats, same texts (ids/timestamps aside).
    # A world that ships quest_variants (baldurs-gate) additionally rolls [Outcome]/[Hook]
    # outcome lore on the base world — that's the replayability layer (a separate feature);
    # the base history+threads remain byte-identical, only those tagged lines are appended.
    for wid in ("baldurs-gate", "sundered-reach"):
        w = content.load_world_data(wid)
        base = content.seed_world(w)
        expected_lore = [str(x) for x in (w.get("history", []) + w.get("standing_threads", [])) if str(x).strip()]
        # the base history+threads lead the lore unchanged; any tail is quest-variant lines
        assert base.lore[: len(expected_lore)] == expected_lore  # nothing dropped/reordered
        assert all(
            l.startswith("[Outcome] ") or l.startswith("[Hook] ")
            for l in base.lore[len(expected_lore):]
        )
        assert base.ending_id == ""
        # one beat per base standing thread, in order, unique ids, text == the thread
        # (quest_variants append only to lore, never to the world-sim beats)
        beats = [cq for cq in base.consequences if cq.thread_id]
        base_threads = [str(t) for t in w.get("standing_threads", []) if str(t).strip()]
        assert [b.text for b in beats] == base_threads
        assert len({b.thread_id for b in beats}) == len(beats)


def test_ending_overlay_tolerates_malformed_optional_field():
    # B-LOW-2: an externally-authored overlay field present-but-not-a-list must DEGRADE,
    # not raise — start_world should never crash on a hand-edit typo. (The strict
    # adventure-seed path keeps raising; this leniency is overlay-only.)
    from models import Campaign
    c = Campaign(title="W", summary="s")
    c.lore = ["base fact"]
    bad = {
        "id": "x", "name": "X", "era": "later",
        "history_append": "a single string, not a list",  # malformed
        "standing_threads": None,                            # malformed
        "supersedes": "base",                                # malformed (string)
        "fates": "not a dict",                               # already-tolerated shape
    }
    content._apply_ending_overlay(c, bad)  # must not raise
    assert c.era == "later"
    assert "a single string, not a list" in c.lore  # coerced to a one-element list
    assert "base fact" not in c.lore  # supersedes="base" coerced + applied -> retracted


# --- S5: structured WorldState canon + the two-surface (recall vs lookup_lore) fix ----

def test_worldstate_model_typed_tenor_and_canon_header():
    # The typed canon spine: world_tenor is a closed Literal (a typo raises), facts is a
    # free dict (setting-agnostic), and canon_header renders the authoritative one-liner.
    from models import WorldState
    from pydantic import ValidationError as VE
    ws = WorldState(world_tenor="grim", facts={"netherbrain": "claimed", "baldurs_gate": "occupied"})
    h = ws.canon_header()
    assert h.startswith("CURRENT WORLD (authoritative): tenor=grim")
    assert "netherbrain=claimed" in h and "baldurs_gate=occupied" in h
    assert "may describe other timelines" in h  # frames the prose below as background
    # a bad tenor enum is rejected (this is what makes the degrade-not-abort guard matter)
    with pytest.raises(VE):
        WorldState(world_tenor="grimdark")
    # default == today's neutral base (hopeful, no facts) -> header lists just the dial
    assert WorldState().canon_header().startswith("CURRENT WORLD (authoritative): tenor=hopeful.")


def test_seed_world_sets_typed_world_state_from_ending():
    # Each shipped ending now carries a structured world_state block; the overlay sets it
    # on the campaign (+ records the retraction predicate for lookup_lore de-confliction).
    w = content.load_world_data("baldurs-gate")
    g = content.seed_world(w, ending="gortash-tyranny")
    assert g.world_state is not None
    assert g.world_state.world_tenor == "grim"
    # S6 audit fix: the gortash row was mis-templated from the brain-CONTROL branch
    # (netherbrain=claimed/the_absolute=ascendant/the_emperor=slain) and contradicted its
    # own prose ("the brain fell", "the Absolute is broken", a living Emperor). It now
    # matches the prose: brain destroyed, Absolute broken, Gortash lives & rules, Emperor free.
    assert g.world_state.facts.get("netherbrain") == "destroyed"
    assert g.world_state.facts.get("the_absolute") == "broken"
    assert g.world_state.facts.get("gortash") == "archduke"
    assert g.world_state.facts.get("the_emperor") == "free"
    assert g.world_state.facts.get("baldurs_gate") == "occupied"
    assert g.lore_supersedes  # the .md de-confliction predicate was recorded
    # a hopeful ending wires the hopeful tenor + rebuilding city
    n = content.seed_world(w, ending="netherbrain-destroyed-heroes-live")
    assert n.world_state.world_tenor == "hopeful"
    assert n.world_state.facts.get("baldurs_gate") == "rebuilding"
    # all four shipped endings parse to a valid WorldState (no malformed rows ship)
    for e in content.list_endings("baldurs-gate"):
        ws = content.seed_world(w, ending=e["id"]).world_state
        assert ws is not None and ws.world_tenor in ("hopeful", "uneasy", "grim")


def _flatten(text: str) -> str:
    return " ".join(text.split()).lower()


def test_ending_supersedes_substrings_are_live_against_their_targets(tmp_path, monkeypatch):
    # MED fix: every `supersedes` substring must actually MATCH something — the .md corpus
    # (so lookup_lore can redact it) and/or c.lore (so recall's seed-time retraction bites).
    # Several gortash substrings were DEAD: authored against world.json/c.lore wording, they
    # matched neither surface (e.g. "enver gortash is dead" fails — the corpus reads "Enver
    # Gortash** is dead" with inline bold markers). This guards against that regressing: NO
    # substring may be dead against BOTH surfaces, and the CRITICAL invariant — a .md-only
    # substring must NOT also bite c.lore (else the seed-time c.lore retraction shifts and
    # test_ending_overlay_retracts_contradictory_base_canon stops being byte-identical).
    pathlib_md = content._content_dir() / "worlds" / "baldurs-gate" / "lore"
    md_pages = {p.name: _flatten(p.read_text(encoding="utf-8")) for p in pathlib_md.glob("*.md")}
    w = content.load_world_data("baldurs-gate")
    clore = [str(x).lower() for x in (w.get("history", []) + w.get("standing_threads", [])) if str(x).strip()]

    def in_md(s):    return [n for n, t in md_pages.items() if s in t]
    def in_clore(s): return [i for i, l in enumerate(clore) if s in l]

    for eid in ("gortash-tyranny", "illithid-ascension"):
        ov = content.load_ending_data("baldurs-gate", eid)
        assert ov is not None
        md_targeting = 0
        for raw in ov["supersedes"]:
            s = raw.lower()
            md_hits, clore_hits = in_md(s), in_clore(s)
            # (1) no dead substrings: every one must bite at least one surface
            assert md_hits or clore_hits, f"{eid}: DEAD substring (matches neither .md nor c.lore): {raw!r}"
            if md_hits and not clore_hits:
                md_targeting += 1
            # (2) NOTE: a substring may legitimately be dual (e.g. "the empty archduke's seat"
            #     hits both factions.md AND c.lore[5]); that's fine. The byte-identity guard
            #     is the dedicated test below — here we just require every .md-targeting
            #     substring is grep-derived from a page that actually contains it.
        assert md_targeting >= 1, f"{eid}: expected at least one live .md-targeting substring"

    # The specific gortash re-derivations the review called out, asserted concretely:
    gortash = [s.lower() for s in content.load_ending_data("baldurs-gate", "gortash-tyranny")["supersedes"]]
    # the two DEAD ones are gone...
    assert "enver gortash is dead" not in gortash, "the dead 'enver gortash is dead' substring must be dropped"
    assert "steel watch wrecked" not in gortash, "the dead 'steel watch wrecked' substring must be dropped"
    # ...replaced by a substring that LITERALLY occurs in baldurs-gate.md (bold markers and all)
    assert "is dead, his **steel watch** wrecked" in gortash
    assert in_md("is dead, his **steel watch** wrecked") == ["baldurs-gate.md"]
    assert in_clore("is dead, his **steel watch** wrecked") == [], "the new .md substring must NOT bite c.lore"


def test_recall_and_lookup_lore_agree_under_nondefault_ending(tmp_path, monkeypatch):
    # THE acceptance criterion (none existed before): under gortash-tyranny the two
    # retrieval surfaces must AGREE about Gortash. recall reads overlay-de-conflicted
    # c.lore; lookup_lore reads the SEPARATE .md corpus — which previously still asserted
    # "Gortash is dead / brain destroyed". Now the corpus is de-conflicted on the same
    # basis (+ both led by the canon header), so no contradicting assertion reaches the DM.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate", ending="gortash-tyranny")["campaign_id"]

    rec = server.recall(cid, "Gortash")["hits"]
    look = server.lookup_lore(cid, "Gortash")["hits"]

    # both surfaces LEAD with the same authoritative world-state header (S6 fix: the row
    # now matches the prose — brain destroyed, Absolute broken, Gortash rules, Emperor free).
    assert rec[0]["kind"] == "world_state" and "tenor=grim" in rec[0]["text"]
    assert look[0]["source"] == "world-state" and "tenor=grim" in look[0]["excerpt"]
    assert "netherbrain=destroyed" in rec[0]["text"] and "netherbrain=destroyed" in look[0]["excerpt"]
    assert "gortash=archduke" in rec[0]["text"] and "the_emperor=free" in rec[0]["text"]

    # The genuinely-stale "Gortash is dead / Steel Watch wrecked / dukedom empty/contested"
    # assertions no longer appear among the authored lookup_lore hits. NOTE (S6): brain-
    # destruction prose ("destroyed in the harbor") is now CORRECT (netherbrain=destroyed)
    # and is deliberately NOT a contradiction — it may surface, agreeing with the header.
    CONTRADICTIONS = (
        "gortash is dead", "steel watch wrecked", "the dukedom is empty",
        "the seat of power is contested", "the contested dukedom",
    )
    page_hits = [h for h in look if h["source"] != "world-state"]
    assert page_hits, "lookup_lore should still return background canon, just de-conflicted"
    for h in page_hits:
        ex = h["excerpt"].lower()
        assert not any(c in ex for c in CONTRADICTIONS), f"contradiction leaked: {h['source']}"

    # ...and the surviving recall canon affirms the tyrant lives (agreement, not silence)
    rec_blob = " || ".join(h["text"].lower() for h in rec)
    assert "gortash rules as archduke" in rec_blob or "the tyrant survived" in rec_blob


# --- S6 audit: per-ending cross-surface fact agreement + premise/Emperor reconciliation -

# Under EACH grim ending, the facts the overlay FLIPS must AGREE across both retrieval
# surfaces: the canon header (recall + lookup_lore) asserts the corrected value, and NO
# authored .md sentence or recall lore line leaks a contradicting (pre-flip) claim. The
# `header` strings are the corrected facts; `no_leak` are the stale assertions the audits
# found leaking; `queries` exercise the surfaces a DM would actually call to ground a scene.
_CROSS_SURFACE = {
    "gortash-tyranny": {
        # mis-templated from the brain-CONTROL branch; now matches its own prose.
        "header": ["netherbrain=destroyed", "the_absolute=broken", "gortash=archduke",
                   "the_emperor=free", "baldurs_gate=occupied"],
        "no_leak": ["gortash is dead", "is dead, his **steel watch** wrecked",
                    "the dukedom is empty", "seat of power is contested",
                    "the contested dukedom", "the wrecked steel watch foundry below it",
                    "all three were destroyed"],
        "queries": ["Gortash", "Steel Watch", "dukedom", "the Absolute", "the Emperor"],
    },
    "dark-urge-bhaal": {
        # FAIL fix: baldurs_gate fallen->occupied (living Bhaal-cult city), Emperor free.
        "header": ["bhaal=ascendant", "baldurs_gate=occupied", "the_emperor=free",
                   "gortash=dead"],
        "no_leak": ["the city is traumatized and rebuilding",
                    "the city survived; the cost was enormous",
                    "all maneuver for control of the gate"],
        "queries": ["Baldur's Gate city", "the Emperor", "power vacuum", "rebuilding"],
    },
    "illithid-ascension": {
        # Emperor slain->free (a living NPC with a betrayal arc); absolute/city/heroes leaks scrubbed.
        "header": ["netherbrain=dominant", "the_absolute=ascendant", "the_emperor=free",
                   "baldurs_gate=occupied"],
        "no_leak": ["the heroes who ended the absolute", "these are living legends now",
                    "reasonable and seductive, serving only itself", "it serves only itself now",
                    "the city is traumatized and rebuilding",
                    "the winter after the fall of the absolute", "1492 dr, after the absolute",
                    "lately freed from the absolute's grip"],
        "queries": ["the heroes", "the Emperor", "the Absolute", "Baldur's Gate city", "factions"],
    },
}


@pytest.mark.parametrize("ending", sorted(_CROSS_SURFACE))
def test_grim_ending_recall_and_lookup_lore_agree_on_all_flipped_facts(ending, tmp_path, monkeypatch):
    # S6 audit (HIGH): for every grim ending, recall and lookup_lore must AGREE on ALL the
    # facts that ending flips (Gortash / Steel-Watch / the-Absolute / the-city / the-Emperor)
    # — both surfaces lead with the corrected canon header, and NO stale pre-flip assertion
    # survives on EITHER surface that would contradict the (corrected) world_state header.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    spec = _CROSS_SURFACE[ending]
    cid = server.start_world("baldurs-gate", ending=ending)["campaign_id"]
    for q in spec["queries"]:
        rec = server.recall(cid, q)["hits"]
        look = server.lookup_lore(cid, q)["hits"]
        # both surfaces lead with the same authoritative header carrying the corrected facts
        assert rec and rec[0]["kind"] == "world_state", f"{ending}/{q}: no recall header"
        assert look and look[0]["source"] == "world-state", f"{ending}/{q}: no lookup header"
        for fact in spec["header"]:
            assert fact in rec[0]["text"], f"{ending}/{q}: recall header missing {fact!r}"
            assert fact in look[0]["excerpt"], f"{ending}/{q}: lookup header missing {fact!r}"
        # no stale leak that contradicts the corrected header — on EITHER surface
        for h in look:
            if h["source"] == "world-state":
                continue
            ex = h["excerpt"].lower()
            for leak in spec["no_leak"]:
                assert leak not in ex, f"{ending}/{q}: lookup_lore leaked {leak!r} ({h['source']})"
        for h in rec:
            if h["kind"] == "world_state":
                continue
            tx = h["text"].lower()
            for leak in spec["no_leak"]:
                assert leak not in tx, f"{ending}/{q}: recall leaked {leak!r}"


def test_emperor_fact_is_living_when_emperor_has_a_companion_arc():
    # S6 audit (HIGH, the slain-vs-alive class): an ending whose Emperor NPC carries a
    # `companion_seeds` arc (a recruitable/gated/betraying live NPC) MUST set `the_emperor`
    # to a LIVING value in its facts row — a `slain` Emperor cannot also have a live arc.
    # (illithid-ascension shipped the_emperor=slain alongside a live prize_seized betrayal
    # arc — a hard self-contradiction this guards against regressing.) More broadly: any
    # ending that keeps the Emperor alive (fate or arc) must not assert the_emperor=slain.
    LIVING = {"free", "allied", "ruler", "displaced", "at-large"}
    checked_arc = False
    for e in content.list_endings("baldurs-gate"):
        ov = content.load_ending_data("baldurs-gate", e["id"])
        facts = (ov.get("world_state") or {}).get("facts", {})
        emp = facts.get("the_emperor")
        cseeds = ov.get("companion_seeds") or {}
        has_emperor_arc = any(
            str(k).strip().lower() in ("npc-the-emperor", "the emperor") for k in cseeds
        )
        if has_emperor_arc:
            checked_arc = True
            assert emp in LIVING, (
                f"{e['id']}: the_emperor={emp!r} but the Emperor has a live companion arc "
                f"— must be a living value {sorted(LIVING)}"
            )
        # no ending may assert the Emperor is slain at all in this world-set (every ending
        # keeps the Emperor as a live, scheming NPC in its fates/prose).
        assert emp != "slain", f"{e['id']}: the_emperor=slain contradicts the live Emperor prose"
    assert checked_arc, "expected at least one ending to seed an Emperor companion arc (illithid)"


def _rendered_premise(world_id: str, ending: str) -> str:
    # The premise the DM reads to open the table — base premise (or the overlay's full
    # `premise` replacement) + the appended `premise_suffix`, exactly as start_world renders it.
    c = content.seed_world(content.load_world_data(world_id), ending=ending)
    return c.summary.lower()


@pytest.mark.parametrize("ending,dead_clauses,alive_marker", [
    # gortash: the base premise asserted "...Enver Gortash, Orin the Red) dead, the Steel
    # Watch fallen silent ... Gortash's death left a power vacuum"; the overlay suffix
    # asserts "Gortash lived". The rendered premise must NOT carry both — the full `premise`
    # replacement drops every dead-Gortash / fallen-Watch / power-vacuum base clause.
    # (Substrings are taken verbatim from the BASE world.json premise so the test bites.)
    ("gortash-tyranny",
     ["orin the red) dead", "the steel watch fallen silent",
      "power vacuum no one trusts", "death left a power vacuum"],
     "rules the gate as archduke"),
    # dark-urge: Gortash IS dead here (so a dead-Gortash clause is NOT a contradiction), but
    # the base premise's quiet-recovery / heroes-scattered / power-vacuum framing must not
    # co-exist with the crowned-Bhaalspawn overlay — the living-city thriller premise replaces it.
    ("dark-urge-bhaal",
     ["power vacuum no one trusts", "scattered into legend",
      "the heroes who saved the city have scattered", "death left a power vacuum"],
     "crowned bhaalspawn"),
])
def test_rendered_premise_is_internally_consistent(ending, dead_clauses, alive_marker):
    # S6 audit (HIGH, systemic): the rendered premise must not say a thing is both dead and
    # alive in one paragraph (the "Gortash is dead AND Gortash lived" bleed-through caused by
    # appending the overlay suffix to an un-replaced base premise). With the `premise`
    # replacement field these endings render a single coherent opening framing.
    prem = _rendered_premise("baldurs-gate", ending)
    for clause in dead_clauses:
        assert clause not in prem, f"{ending}: rendered premise still carries stale clause {clause!r}"
    assert alive_marker in prem, f"{ending}: rendered premise lost its post-state framing {alive_marker!r}"


def test_overlay_premise_and_story_seeds_replace_are_additive(tmp_path, monkeypatch):
    # S6 engine addition: `premise` (full premise replace) and `story_seeds_replace`/
    # `story_seeds` (replace base seeds) are ADDITIVE — absent => today's append behavior.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    w = content.load_world_data("baldurs-gate")
    base_premise = w.get("premise", "")
    base_seeds = list(w.get("story_seeds", []) or [])

    # heroes-live ships NO premise / story_seeds_replace -> base premise kept (+ its suffix),
    # base seeds kept (+ its appends). The replacement machinery is a no-op here.
    n = content.seed_world(w, ending="netherbrain-destroyed-heroes-live")
    assert n.summary.startswith(base_premise), "no-premise ending must keep the base premise opener"
    out = server.start_world("baldurs-gate", ending="netherbrain-destroyed-heroes-live")
    # base seeds survive in order, then the overlay's appends follow
    assert out["story_seeds"][: len(base_seeds)] == base_seeds

    # gortash ships BOTH a `premise` replacement and a `story_seeds_replace`:
    g = content.seed_world(w, ending="gortash-tyranny")
    assert not g.summary.startswith(base_premise), "premise replacement must supersede the base opener"
    gout = server.start_world("baldurs-gate", ending="gortash-tyranny")
    # the base "contested dukedom: Gortash's empty seat" seed is REPLACED (not present),
    # while non-conflicting flavor is re-authored; appends still follow the replace set.
    assert not any("gortash's empty seat" in s.lower() for s in gout["story_seeds"]), \
        "story_seeds_replace must drop the contradicting base seed"
    assert any("a resistance cell needs a face" in s.lower() for s in gout["story_seeds"]), \
        "story_seeds_append must still apply on top of the replace set"


def test_overlay_premise_and_story_seeds_replace_degrade_not_abort(tmp_path, monkeypatch):
    # S6 engine addition: a malformed `premise` / `story_seeds_replace` must DEGRADE (be
    # ignored, base kept) not abort start_world — mirroring the world_state/companion_seeds
    # guards for hand-edited overlays. We author a custom overlay with bad shapes.
    import shutil
    src_world = content._content_dir() / "worlds" / "baldurs-gate"
    dst_world = tmp_path / "worlds" / "baldurs-gate"
    dst_world.parent.mkdir(parents=True)
    shutil.copytree(src_world, dst_world, ignore=lambda d, names: ["endings"] if "endings" in names else [])
    endings_dir = dst_world / "endings"
    endings_dir.mkdir()
    (endings_dir / "bad-replace.json").write_text(json.dumps({
        "id": "bad-replace", "name": "Bad Replace", "era": "1493 DR",
        "premise": ["not", "a", "string"],          # malformed -> coerced/ignored
        "story_seeds_replace": "not a list",          # malformed -> ignored, base seeds stand
    }), encoding="utf-8")
    monkeypatch.setenv("CLAWDND_CONTENT_DIR", str(tmp_path))
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    w = content.load_world_data("baldurs-gate")
    base_seeds = list(w.get("story_seeds", []) or [])
    c = content.seed_world(w, ending="bad-replace")  # must NOT raise
    assert c.ending_id == "bad-replace"
    # malformed premise (a list) -> str() coercion makes it a non-empty string, which the
    # field treats as a replacement; the key guarantee is simply that it does not CRASH.
    assert isinstance(c.summary, str)
    out = server.start_world("baldurs-gate", ending="bad-replace")
    # malformed story_seeds_replace (a string) is ignored -> the base seeds survive intact.
    assert out["story_seeds"] == base_seeds, "malformed story_seeds_replace must fall back to base seeds"


def test_world_state_default_path_is_byte_identical(tmp_path, monkeypatch):
    # ADDITIVE: with no ending (or an ending without a world_state block) there is no
    # world_state -> recall/lookup_lore emit NO header and NO de-confliction, byte-for-byte
    # as before. Compare a base campaign's surfaces with and against the header path.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    # base world (no ending): world_state is None, lore_supersedes empty
    base_cid = server.start_world("baldurs-gate")["campaign_id"]
    from store import load_campaign
    bc = load_campaign(base_cid)
    assert bc.world_state is None and bc.lore_supersedes == []

    # recall: no synthetic world_state hit prepended
    rec = server.recall(base_cid, "Gortash")["hits"]
    assert all(h["kind"] != "world_state" for h in rec)
    # and it equals the raw ledger result (the wrapper added nothing)
    import ledger as ledger_mod
    assert rec == ledger_mod.recall(base_cid, "Gortash", kinds=None, limit=8)

    # lookup_lore: no header hit, and identical to the bare lorebook call (no de-confliction)
    look = server.lookup_lore(base_cid, "Gortash")["hits"]
    assert all(h["source"] != "world-state" for h in look)
    import lorebook
    assert look == lorebook.lookup_lore("baldurs-gate", "Gortash", 5)


def test_recall_kinds_filter_excludes_world_state_header(tmp_path, monkeypatch):
    # MED fix: the synthetic world_state header is prepended AFTER ledger.recall applied
    # `kinds`, so recall(kinds=["decision"]) used to return an UNREQUESTED world_state row
    # ("world_state" isn't even in ledger.KINDS). The header must now honor the filter:
    # absent when kinds is a subset that doesn't list "world_state"; present when unfiltered
    # or when the caller explicitly asks for "world_state".
    import ledger as ledger_mod
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate", ending="gortash-tyranny")["campaign_id"]
    # log a decision so a kinds=["decision"] filter has a legitimate row to return
    server.record_decision(cid, "Side with the resistance against the Archduke", ["comply", "resist"])

    # unfiltered: header leads (today's framing behavior preserved)
    unfiltered = server.recall(cid, "Archduke resistance")["hits"]
    assert unfiltered and unfiltered[0]["kind"] == "world_state"

    # filtered to a subset WITHOUT world_state: NO synthetic header leaks in...
    only_decisions = server.recall(cid, "Archduke resistance", kinds=["decision"])["hits"]
    assert only_decisions, "the decision itself should still come back"
    assert all(h["kind"] != "world_state" for h in only_decisions), "header leaked past the kinds filter"
    assert all(h["kind"] == "decision" for h in only_decisions)
    # ...and the wrapper added NOTHING beyond the raw filtered ledger result
    assert only_decisions == ledger_mod.recall(cid, "Archduke resistance", kinds=["decision"], limit=8)

    # explicit opt-in: a caller that lists "world_state" gets the header back
    opted_in = server.recall(cid, "Archduke resistance", kinds=["decision", "world_state"])["hits"]
    assert any(h["kind"] == "world_state" for h in opted_in)


def test_world_state_malformed_block_degrades_not_aborts(tmp_path, monkeypatch):
    # DEGRADE-not-abort: a malformed world_state block in an ending must be SKIPPED (the
    # world keeps None state), never crash start_world — mirroring the companion_seeds guard.
    import shutil
    src = content._content_dir() / "worlds" / "baldurs-gate"
    dst_world = tmp_path / "worlds" / "baldurs-gate"
    dst_world.mkdir(parents=True)
    for item in src.iterdir():
        if item.name == "endings":
            continue  # author our own endings/ below
        if item.is_dir():
            shutil.copytree(item, dst_world / item.name)
        else:
            shutil.copy2(item, dst_world / item.name)
    endings_dir = dst_world / "endings"
    endings_dir.mkdir()
    (endings_dir / "bad-ws.json").write_text(json.dumps({
        "id": "bad-ws", "name": "Bad WorldState", "era": "1493 DR, after the crisis",
        # MALFORMED: tenor outside the Literal set + a forbidden extra key
        "world_state": {"world_tenor": "apocalyptic", "facts": {"x": "y"}, "bogus": 1},
        # a valid sibling field still applies, proving only the bad block degraded
        "history_append": ["A new line of history."],
    }), encoding="utf-8")
    monkeypatch.setenv("CLAWDND_CONTENT_DIR", str(tmp_path))

    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w, ending="bad-ws")  # must NOT raise
    assert c.ending_id == "bad-ws"
    assert c.world_state is None, "malformed world_state must degrade to None, not partial"
    assert any("A new line of history." in l for l in c.lore)  # the rest of the overlay applied


def test_world_state_degrade_also_skips_lore_supersedes_coupling(tmp_path, monkeypatch):
    # B-LOW (couple world_state + supersedes): the `.md` de-confliction predicate
    # (`lore_supersedes`) and the mitigating canon header (`world_state`) are belt-and-
    # suspenders. If the world_state block DEGRADES to None, we must NOT still record
    # lore_supersedes — that would let lookup_lore strip authored .md canon WITHOUT the
    # framing header. The whole ending world-state block is all-or-nothing.
    import shutil, lorebook
    src = content._content_dir() / "worlds" / "baldurs-gate"
    dst_world = tmp_path / "worlds" / "baldurs-gate"
    dst_world.mkdir(parents=True)
    for item in src.iterdir():
        if item.name == "endings":
            continue
        shutil.copytree(item, dst_world / item.name) if item.is_dir() else shutil.copy2(item, dst_world / item.name)
    endings_dir = dst_world / "endings"
    endings_dir.mkdir()
    (endings_dir / "bad-ws-sup.json").write_text(json.dumps({
        "id": "bad-ws-sup", "name": "Bad WS + supersedes", "era": "1493 DR",
        # MALFORMED world_state (bad tenor) -> degrades to None...
        "world_state": {"world_tenor": "apocalyptic", "facts": {"x": "y"}},
        # ...and a supersedes that WOULD redact the .md corpus if it were (wrongly) recorded
        "supersedes": ["seat of power is contested", "is dead, his **steel watch** wrecked"],
    }), encoding="utf-8")
    monkeypatch.setenv("CLAWDND_CONTENT_DIR", str(tmp_path))

    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w, ending="bad-ws-sup")  # must NOT raise
    assert c.world_state is None, "malformed world_state degrades to None"
    assert c.lore_supersedes == [], "lore_supersedes must NOT be recorded when world_state degraded"
    # consequence: lookup_lore stays byte-identical to the no-ending path (no header, no
    # redaction) — the authored .md canon is NOT stripped without a mitigating header.
    coupled = lorebook.lookup_lore("baldurs-gate", "Gortash", 5,
                                   supersedes=c.lore_supersedes,
                                   canon_header=(c.world_state.canon_header() if c.world_state else ""))
    assert coupled == lorebook.lookup_lore("baldurs-gate", "Gortash", 5)
    # sanity: the substrings really WOULD have bitten had they been recorded (so the guard matters)
    bitten, _, _ = lorebook._redact_superseded(
        "Archduke Enver Gortash** is dead, his **Steel Watch** wrecked, and the seat of power is contested.",
        ["seat of power is contested"])
    assert "[…superseded…]" in bitten


# --- S4 synthesis: the chosen ENDING pre-loads which companions can turn --------------

def test_ending_companion_seed_preloads_arc_on_roster_companion():
    # The S4 cross-stream synthesis: the chosen post-state PRE-LOADS a canon companion's
    # arc + sealed agenda onto the matching roster Character (resolved by id, exactly as
    # `fates` does). illithid-ascension arms the Emperor's prize_seized betrayal + a
    # personal_quest gate to sever the brain's hold.
    w = content.load_world_data("baldurs-gate")
    il = content.seed_world(w, ending="illithid-ascension")
    emperor = il.characters["npc-the-emperor"]
    assert emperor.arc is not None, "the seeded companion's arc must be pre-loaded"
    assert emperor.arc.agenda is not None
    assert emperor.arc.agenda.trigger == "prize_seized"  # the brain's-whisper betrayal
    assert emperor.arc.agenda.fired is False              # armed, not yet sprung
    # a personal_quest gate is present (sever the Elder Brain's hold)
    kinds = {g.kind for g in emperor.arc.arc_gates}
    assert "personal_quest" in kinds
    assert any(g.threshold == 20 for g in emperor.arc.arc_gates)
    # a companion the overlay does NOT seed carries no arc (the seed is per-companion)
    assert il.characters["npc-jaheira"].arc is None


def test_ending_companion_seed_default_path_sets_no_arcs():
    # ADDITIVE default: an overlay WITHOUT a `companion_seeds` block touches no arcs, and
    # the base (no-ending) seed likewise leaves every companion arc-less — today's behavior.
    w = content.load_world_data("baldurs-gate")
    base = content.seed_world(w)
    assert all(ch.arc is None for ch in base.characters.values())
    # dark-urge-bhaal DOES ship a (lighter) seed, so prove the no-seed branch directly:
    # an overlay dict with no `companion_seeds` key must leave a pre-set arc untouched and
    # never invent one.
    from models import Campaign, Character
    c = Campaign(title="W", summary="s")
    c.characters["npc-x"] = Character(id="npc-x", name="X", kind="npc")
    content._apply_ending_overlay(c, {"id": "noend", "name": "No Seeds", "era": "later"})
    assert c.characters["npc-x"].arc is None  # no companion_seeds key -> no-op


def test_all_shipped_ending_companion_seeds_are_valid_m2():
    # Every authored `companion_seeds` arc must construct WITHOUT a ValidationError —
    # proving M2 compliance: a day_reached/attitude_below agenda carries an explicit
    # `value`, while party_vulnerable/prize_seized need none. (CompanionArc.model_validate
    # is exactly what `_apply_ending_overlay` calls, so a green seed here is a green seed
    # in play.) We also confirm BOTH threshold-trigger kinds appear across the set.
    from models import CompanionArc
    seen_triggers: set[str] = set()
    seeded_any = False
    for e in content.list_endings("baldurs-gate"):
        overlay = content.load_ending_data("baldurs-gate", e["id"])
        seeds = overlay.get("companion_seeds") or {}
        for who, seed in seeds.items():
            seeded_any = True
            arc = CompanionArc.model_validate(seed["arc"])  # raises if M2 is violated
            if arc.agenda is not None:
                seen_triggers.add(arc.agenda.trigger)
                if arc.agenda.trigger in ("attitude_below", "day_reached"):
                    assert arc.agenda.value is not None, f"{e['id']}/{who}: M2 needs a value"
            assert arc.arc_gates, f"{e['id']}/{who}: a seed should carry at least one gate"
    assert seeded_any, "the post-BG3 endings should ship companion seeds"
    # the explicit-value triggers are both exercised by the shipped seeds (real M2 proof)
    assert {"attitude_below", "day_reached"} <= seen_triggers


def test_ending_companion_seed_skips_absent_companion():
    # A `companion_seeds` entry for a companion NOT present in the campaign roster is
    # skipped silently (same id/name resolution as `fates`), never raising.
    from models import Campaign, Character
    c = Campaign(title="W", summary="s")
    c.characters["npc-real"] = Character(id="npc-real", name="Real", kind="npc")
    overlay = {
        "id": "x", "name": "X", "era": "later",
        "companion_seeds": {
            # present -> seeded
            "npc-real": {"arc": {"arc_gates": [{"kind": "loyalty", "threshold": 10}]}},
            # absent (no such id/name) -> skipped, no error
            "npc-ghost": {"arc": {"arc_gates": [{"kind": "betrayal", "threshold": 5}],
                                   "agenda": {"trigger": "party_vulnerable"}}},
        },
    }
    content._apply_ending_overlay(c, overlay)  # must not raise
    assert c.characters["npc-real"].arc is not None
    assert c.characters["npc-real"].arc.arc_gates[0].kind == "loyalty"
    assert "npc-ghost" not in c.characters  # the absent seed minted no character


def test_ending_companion_seed_resolves_by_name_too():
    # Resolution mirrors `fates`: a seed keyed by DISPLAY NAME (not id) lands on the
    # matching roster Character.
    from models import Campaign, Character
    c = Campaign(title="W", summary="s")
    c.characters["npc-7"] = Character(id="npc-7", name="Karlach", kind="npc")
    overlay = {
        "id": "x", "name": "X", "era": "later",
        "companion_seeds": {"Karlach": {"arc": {"arc_gates": [{"kind": "romance", "threshold": 40}]}}},
    }
    content._apply_ending_overlay(c, overlay)
    assert c.characters["npc-7"].arc is not None
    assert c.characters["npc-7"].arc.arc_gates[0].kind == "romance"


def test_start_world_ending_preloads_companion_arc_end_to_end(tmp_path, monkeypatch):
    # The full server path: start_world(ending=…) PERSISTS a campaign whose seeded roster
    # figure already carries the armed arc. The arc is pre-loaded at world-genesis; it
    # surfaces in play once that figure is brought into the party as a companion (the
    # check_companion_arc evaluator gates on kind=="companion"), exactly mirroring real
    # play (recruit_companion / load_canon_character(kind="companion")).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate", ending="illithid-ascension")["campaign_id"]
    from store import load_campaign, save_campaign
    c = load_campaign(cid)
    emperor = c.characters["npc-the-emperor"]
    assert emperor.arc is not None and emperor.arc.agenda.trigger == "prize_seized"
    # promote the seeded roster figure to a party companion (as play would) so the engine
    # evaluates the PRE-LOADED arc — the betrayal is armed but not yet fired at start.
    emperor.kind = "companion"
    save_campaign(c)
    res = server.check_companion_arc(cid, "npc-the-emperor")
    assert res["results"] == []  # armed, nothing newly unlocked/fired (prize not seized)
    # seize the prize -> the pre-loaded betrayal agenda now fires through the live engine
    server.set_flag(cid, "prize_seized")
    fired = server.check_companion_arc(cid, "npc-the-emperor")["results"]
    assert len(fired) == 1 and fired[0]["agenda_fired"] is True
    assert fired[0]["agenda"]["trigger"] == "prize_seized"


# --- S4: ingested areas become navigable Locations (additively) ----------------------

def test_load_world_areas_returns_shipped_samples():
    # The hand-written example areas under content/worlds/baldurs-gate/areas/ load as
    # Location-shaped dicts, deduped by name, each carrying license + attribution.
    areas = content.load_world_areas("baldurs-gate")
    by_name = {a["name"]: a for a in areas}
    assert "Bloomridge Market" in by_name and "the Siltwharf Steps" in by_name
    bm = by_name["Bloomridge Market"]
    assert bm["description"] and bm["region"] == "Baldur's Gate"
    assert isinstance(bm["connections"], list) and bm["license"] and bm["attribution"]


def test_load_world_areas_absent_dir_is_empty():
    # A world with no areas/ dir yields an empty list (the no-op default path).
    assert content.load_world_areas("sundered-reach") == []
    assert content.load_world_areas("no-such-world") == []


def test_seed_world_seeds_areas_additively_with_resolved_connections():
    # ADDITIVE: after the authored regions, all area files are seeded as Locations.
    w = content.load_world_data("baldurs-gate")
    base_regions = len(w["regions"])
    n_areas = len(content.load_world_areas("baldurs-gate"))
    c = content.seed_world(w)
    by_name = {loc.name: loc for loc in c.locations.values()}
    # the authored regions are all still present, plus the ingested areas
    assert "Baldur's Gate — Lower City" in by_name      # an authored region
    assert "Bloomridge Market" in by_name               # an ingested area
    assert "the Siltwharf Steps" in by_name
    assert len(c.locations) == base_regions + n_areas

    bm = by_name["Bloomridge Market"]
    # ingested-area fields carried through (region, tags→notes)
    assert bm.region == "Baldur's Gate"
    assert "market" in bm.notes
    # connection NAMES resolve to seeded location ids where a name matches…
    lower_id = by_name["Baldur's Gate — Lower City"].id
    silt_id = by_name["the Siltwharf Steps"].id
    assert lower_id in bm.connections          # "Baldur's Gate — Lower City" → loc id
    assert silt_id in bm.connections           # cross-link to the other ingested area
    # …and an unmatched name is left verbatim as a hint, not dropped
    assert "the Cloistered Quarter" in bm.connections


def test_seed_world_areas_do_not_double_seed_or_change_start():
    # The party still starts at the authored start; areas never become the start, and a
    # region whose name an area duplicates is NOT seeded twice.
    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w)
    # start is the first starting_option (an authored region), unaffected by areas
    assert c.current_location_id == "loc-lower-city"
    # no two locations share a (case-insensitive) name — dedup held
    names = [loc.name.strip().lower() for loc in c.locations.values()]
    assert len(names) == len(set(names))
    # explicit dedup probe: a region whose NAME an injected area reuses is not re-seeded
    w2 = content.load_world_data("baldurs-gate")
    before = len(content.seed_world(w2).locations)
    # (all areas have unique names, so the count is the regions + number of area files)
    n_areas = len(content.load_world_areas("baldurs-gate"))
    assert before == len(w2["regions"]) + n_areas


def test_seed_world_without_areas_is_unchanged(tmp_path, monkeypatch):
    # No areas/ dir == today's behavior EXACTLY. Point the content dir at a copy of the
    # world that has world.json but NO areas/ subdir, and confirm only regions seed.
    import shutil
    src = content._content_dir() / "worlds" / "baldurs-gate"
    dst_world = tmp_path / "worlds" / "baldurs-gate"
    dst_world.mkdir(parents=True)
    for item in src.iterdir():
        if item.name == "areas":
            continue  # deliberately omit the areas/ dir
        if item.is_dir():
            shutil.copytree(item, dst_world / item.name)
        else:
            shutil.copy2(item, dst_world / item.name)
    monkeypatch.setenv("CLAWDND_CONTENT_DIR", str(tmp_path))
    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w)
    assert content.load_world_areas("baldurs-gate") == []
    # exactly the authored regions, nothing more
    assert len(c.locations) == len(w["regions"])
    assert "Bloomridge Market" not in {loc.name for loc in c.locations.values()}
    # …and with no areas to wire back, every region's connections are byte-identical to
    # world.json (the reverse-edge wiring added for B2 must NOT touch the no-areas path).
    by_id = {loc.id: loc for loc in c.locations.values()}
    for reg in w["regions"]:
        assert by_id[reg["id"]].connections == reg.get("connections", [])


# --- B2 (HIGH): an ingested area must be REACHABLE from its parent region -------------
# The unit test above (test_seed_world_seeds_areas_additively_with_resolved_connections)
# asserts only the FORWARD edge area→region. travel/reachable use DIRECTED edges from the
# CURRENT location, so a forward-only edge leaves the area unreachable while you stand in
# the region — the exact end-to-end gap the original tests missed. These exercise the
# travel.reachable() integration path with the SHIPPED example areas.

def test_seeded_area_is_reachable_from_parent_region():
    # The B2 repro, verbatim: standing in "Baldur's Gate — Lower City", reachable() MUST
    # include "Bloomridge Market" (the area lists loc-lower-city among its connections, so
    # the reverse edge region→area must exist for it to be navigable).
    import travel
    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w)
    by_name = {loc.name: loc for loc in c.locations.values()}
    bm = by_name["Bloomridge Market"]
    lower = by_name["Baldur's Gate — Lower City"]
    silt = by_name["the Siltwharf Steps"]

    # FAILS BEFORE THE FIX: reachable() from Lower City omits Bloomridge (forward edge only).
    c.current_location_id = lower.id
    reach_from_lower = {loc.id for loc in travel.reachable(c)}
    assert bm.id in reach_from_lower, "ingested area unreachable from its parent region (B2)"

    # The reverse edge is on the region itself, deduped (mirrors add_location's wiring).
    assert bm.id in lower.connections
    assert lower.connections.count(bm.id) == 1, "reverse edge must not be duplicated"
    # The FORWARD edge is untouched — both directions now exist (true bidirectional).
    assert lower.id in bm.connections

    # Siltwharf resolves to Wyrm's Crossing too; that region must reach Siltwharf as well.
    wyrm = by_name["Wyrm's Crossing & the Risen Road"]
    c.current_location_id = wyrm.id
    assert silt.id in {loc.id for loc in travel.reachable(c)}
    # …and the two areas cross-link bidirectionally with each other.
    assert silt.id in bm.connections and bm.id in silt.connections
    # An unmatched connection name stays a verbatim hint (no spurious reverse edge minted).
    assert "the Cloistered Quarter" in bm.connections
    assert "the Cloistered Quarter" not in c.locations


def test_seed_world_areas_intra_area_id_collision_guarded(tmp_path, monkeypatch):
    # Two ingested AREA files that share an id but have DIFFERENT names (so load_world_areas'
    # name-dedup lets both through) must NOT silently overwrite each other in c.locations.
    # The first wins; the second is skipped. Without the guard the second would clobber the
    # first under the same key.
    import shutil
    src = content._content_dir() / "worlds" / "baldurs-gate"
    dst_world = tmp_path / "worlds" / "baldurs-gate"
    dst_world.mkdir(parents=True)
    for item in src.iterdir():
        if item.name == "areas":
            continue  # we author our own areas/ below
        if item.is_dir():
            shutil.copytree(item, dst_world / item.name)
        else:
            shutil.copy2(item, dst_world / item.name)
    areas_dir = dst_world / "areas"
    areas_dir.mkdir()
    # Both claim id "loc-dup-area"; the region they connect to is the authored Lower City.
    (areas_dir / "first.json").write_text(json.dumps({
        "id": "loc-dup-area", "name": "First Area", "description": "the first",
        "region": "Baldur's Gate", "connections": ["Baldur's Gate — Lower City"],
    }), encoding="utf-8")
    (areas_dir / "second.json").write_text(json.dumps({
        "id": "loc-dup-area", "name": "Second Area", "description": "the second",
        "region": "Baldur's Gate", "connections": ["Baldur's Gate — Lower City"],
    }), encoding="utf-8")
    monkeypatch.setenv("CLAWDND_CONTENT_DIR", str(tmp_path))

    w = content.load_world_data("baldurs-gate")
    c = content.seed_world(w)  # must not raise; second area is skipped, not clobbering
    # Exactly ONE location holds the shared id, and it's the FIRST file's (sorted: first<second).
    assert "loc-dup-area" in c.locations
    assert c.locations["loc-dup-area"].name == "First Area"
    # "Second Area" never got seeded under that id (the dup was dropped, not overwritten).
    assert "Second Area" not in {loc.name for loc in c.locations.values()}


def test_seed_world_ending_malformed_companion_seed_does_not_abort(tmp_path, monkeypatch):
    # C2 (MED): a dict-but-INVALID companion_seeds arc (a `day_reached` agenda missing its
    # M2-required `value`) must NOT abort start_world. The original unit test only validated
    # the SHIPPED (all-valid) seeds; nothing exercised a malformed arc reaching seed_world.
    # This drives the full seed_world(ending=…) → _apply_ending_overlay path with a custom
    # overlay carrying ONE malformed seed + ONE valid sibling.
    import shutil
    src = content._content_dir() / "worlds" / "baldurs-gate"
    dst_world = tmp_path / "worlds" / "baldurs-gate"
    dst_world.mkdir(parents=True)
    for item in src.iterdir():
        if item.name == "endings":
            continue  # author our own endings/ below
        if item.is_dir():
            shutil.copytree(item, dst_world / item.name)
        else:
            shutil.copy2(item, dst_world / item.name)
    endings_dir = dst_world / "endings"
    endings_dir.mkdir()
    (endings_dir / "broken.json").write_text(json.dumps({
        "id": "broken", "name": "Broken Seed", "era": "1493 DR, after the crisis",
        "companion_seeds": {
            # MALFORMED: day_reached with no `value` -> CompanionAgenda M2 ValidationError
            "npc-the-emperor": {"arc": {"agenda": {"trigger": "day_reached"}}},
            # VALID sibling in the SAME overlay -> must still apply
            "npc-karlach": {"arc": {"arc_gates": [{"kind": "romance", "threshold": 30}]}},
        },
    }), encoding="utf-8")
    monkeypatch.setenv("CLAWDND_CONTENT_DIR", str(tmp_path))

    w = content.load_world_data("baldurs-gate")
    # FAILS BEFORE THE FIX: the unguarded CompanionArc.model_validate raises ValidationError
    # which propagates through seed_world, aborting world creation.
    c = content.seed_world(w, ending="broken")  # must not raise
    # The world loaded (regions + areas seeded) and the ending resolved.
    assert c.ending_id == "broken"
    assert c.characters, "world must still be populated"
    # The malformed companion got NO arc (its bad seed was skipped, not applied).
    assert c.characters["npc-the-emperor"].arc is None
    # The VALID sibling seed in the same overlay STILL applied.
    karlach = c.characters["npc-karlach"]
    assert karlach.arc is not None
    assert karlach.arc.arc_gates and karlach.arc.arc_gates[0].kind == "romance"


def test_apply_ending_overlay_skips_malformed_arc_keeps_valid_sibling():
    # Unit-level companion of the above: _apply_ending_overlay tolerates a malformed arc
    # (bad gate kind / forbidden extra key are also dict-but-invalid) and keeps the valid one.
    from models import Campaign, Character
    c = Campaign(title="W", summary="s")
    c.characters["npc-bad"] = Character(id="npc-bad", name="Bad", kind="npc")
    c.characters["npc-ok"] = Character(id="npc-ok", name="Ok", kind="npc")
    overlay = {
        "id": "x", "name": "X", "era": "later",
        "companion_seeds": {
            # dict-but-invalid: a gate kind outside the Literal set
            "npc-bad": {"arc": {"arc_gates": [{"kind": "not-a-kind", "threshold": 10}]}},
            "npc-ok": {"arc": {"agenda": {"trigger": "party_vulnerable"}}},  # valid (no value needed)
        },
    }
    content._apply_ending_overlay(c, overlay)  # must not raise
    assert c.characters["npc-bad"].arc is None
    assert c.characters["npc-ok"].arc is not None
    assert c.characters["npc-ok"].arc.agenda.trigger == "party_vulnerable"


# ---------------------------------------------------------------------------
# Caster-party origins: Rolan / Lia / Isobel (feat-caster-party)
# ---------------------------------------------------------------------------

def test_caster_origins_json_valid_and_load():
    """All 6 new files (3 characters + 3 origins) parse as JSON without error."""
    import glob
    import os
    base = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..",  # servers/engine/tests -> repo root
        "content", "worlds", "baldurs-gate",
    )
    for pattern in ("characters/rolan.json", "characters/lia.json", "characters/isobel.json",
                    "origins/rolan-evoker.json", "origins/lia-battlemaster.json", "origins/isobel-cleric.json"):
        path = os.path.normpath(os.path.join(base, pattern))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data, f"empty or null JSON at {pattern}"


def test_rolan_evoker_origin_has_spells_known():
    """load_origin_template for rolan-evoker returns a non-empty spells_known list."""
    tpl = content.load_origin_template("baldurs-gate", "rolan-evoker")
    assert tpl is not None, "rolan-evoker template not found"
    assert tpl.get("spells_known"), "rolan-evoker must have non-empty spells_known"
    # cantrips present (level-0 spells)
    assert any(s in tpl["spells_known"] for s in ("Fire Bolt", "Ray of Frost", "Mage Hand")), \
        "expected at least one cantrip in rolan-evoker spells_known"
    # levelled spells present
    assert any(s in tpl["spells_known"] for s in ("Magic Missile", "Shield", "Burning Hands")), \
        "expected at least one levelled spell in rolan-evoker spells_known"


def test_isobel_cleric_origin_has_spells_prepared():
    """load_origin_template for isobel-cleric returns a non-empty spells_prepared list."""
    tpl = content.load_origin_template("baldurs-gate", "isobel-cleric")
    assert tpl is not None, "isobel-cleric template not found"
    assert tpl.get("spells_prepared"), "isobel-cleric must have non-empty spells_prepared"
    assert "Cure Wounds" in tpl["spells_prepared"]
    assert "Sacred Flame" in tpl["spells_prepared"]
    # Light-domain spells present
    assert "Burning Hands" in tpl["spells_prepared"] or "Flaming Sphere" in tpl["spells_prepared"], \
        "expected at least one Light-domain spell in isobel-cleric spells_prepared"


def test_lia_battlemaster_origin_has_no_spells():
    """lia-battlemaster is a martial origin — no spell fields."""
    tpl = content.load_origin_template("baldurs-gate", "lia-battlemaster")
    assert tpl is not None, "lia-battlemaster template not found"
    assert not tpl.get("spells_known"), "lia-battlemaster must NOT have spells_known"
    assert not tpl.get("spells_prepared"), "lia-battlemaster must NOT have spells_prepared"


def test_new_origins_appear_in_list_origin_templates():
    """All three new origin ids surface in list_origin_templates for baldurs-gate."""
    templates = content.list_origin_templates("baldurs-gate")
    ids = {t["id"] for t in templates}
    assert "rolan-evoker" in ids, f"rolan-evoker missing from list_origin_templates; got {ids}"
    assert "lia-battlemaster" in ids, f"lia-battlemaster missing; got {ids}"
    assert "isobel-cleric" in ids, f"isobel-cleric missing; got {ids}"


def test_new_characters_appear_in_list_canon_characters(tmp_path, monkeypatch):
    """Rolan, Lia, and Isobel are discoverable as playable canon characters.
    (SYN-03: the roster is now bounded, so look them up by `q` rather than scanning
    the capped head of the ~2,000-record list.)"""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate")["campaign_id"]
    for who in ("Rolan", "Lia", "Isobel"):
        chars = server.list_canon_characters(cid, q=who)["available"]
        by_name = {c["name"]: c for c in chars}
        assert who in by_name, f"{who} missing from list_canon_characters(q={who!r})"
        # These are minor figures (playable: true, role: "")
        assert by_name[who]["playable"] is True, f"{who} must be playable"
        assert by_name[who].get("role", "") == "", f"{who} role must be empty"


def test_start_character_rolan_evoker_template_yields_spells(tmp_path, monkeypatch):
    """start_character with origin='template:rolan-evoker' produces a PC with non-empty
    spell_slots AND non-empty spells_known (casts > 0 is now POSSIBLE)."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate")["campaign_id"]
    result = server.start_character(cid, origin="template:rolan-evoker")
    assert "error" not in result, f"start_character errored: {result}"
    assert result["level"] == 3 and result["class"] == "Wizard"

    sheet = server.get_character(cid, result["id"])
    # spell_slots populated (a level-3 Wizard has slots)
    assert sheet.get("spell_slots"), "Wizard L3 must have spell_slots"
    # spells_known carried from the template
    assert sheet.get("spells_known"), "rolan-evoker template must populate spells_known on the PC"
    assert "Magic Missile" in sheet["spells_known"] or "Fire Bolt" in sheet["spells_known"], \
        "expected a known spell from the template on the created PC"


def test_start_character_isobel_cleric_template_yields_spells(tmp_path, monkeypatch):
    """start_character with origin='template:isobel-cleric' produces a Cleric with
    non-empty spell_slots AND non-empty spells_prepared."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate")["campaign_id"]
    result = server.start_character(cid, origin="template:isobel-cleric")
    assert "error" not in result, f"start_character errored: {result}"
    assert result["level"] == 3 and result["class"] == "Cleric"

    sheet = server.get_character(cid, result["id"])
    assert sheet.get("spell_slots"), "Cleric L3 must have spell_slots"
    assert sheet.get("spells_prepared"), "isobel-cleric template must populate spells_prepared on the PC"
    assert "Cure Wounds" in sheet["spells_prepared"] or "Sacred Flame" in sheet["spells_prepared"], \
        "expected a prepared spell from the template on the created PC"


def test_start_character_lia_battlemaster_template_no_spells(tmp_path, monkeypatch):
    """start_character with origin='template:lia-battlemaster' produces a Fighter with no spells."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate")["campaign_id"]
    result = server.start_character(cid, origin="template:lia-battlemaster")
    assert "error" not in result, f"start_character errored: {result}"
    assert result["level"] == 3 and result["class"] == "Fighter"

    sheet = server.get_character(cid, result["id"])
    # Fighter has no spell slots and an empty spellbook
    assert not sheet.get("spell_slots"), "Fighter must have no spell_slots"
    assert not sheet.get("spells_known"), "lia-battlemaster must have no spells_known"
    assert not sheet.get("spells_prepared"), "lia-battlemaster must have no spells_prepared"


# --- atlas day-1 discovery seeding (issue #261) --------------------------------------


def _atlas_is_visible(loc) -> bool:
    """Mirror of the viewer's known-location predicate (viewer/server.py
    _atlas_visible_location_ids): a place shows on the atlas when it is visited OR
    discovered OR explicitly RUMOURED (issue #380); it is hidden only when it is
    undiscovered-and-unvisited-and-not-rumoured (or flagged hidden). Inlined here so
    the engine test locks the day-1 contract without importing the viewer."""
    if getattr(loc, "hidden", False):
        return False
    if loc.visited:
        return True
    if getattr(loc, "rumoured", False):
        return True
    return loc.discovered is True or loc.discovered is None


def test_seed_world_marks_known_regions_discovered_day_one():
    # Issue #261 + #380: a fresh baldurs-gate seed renders the shipped nav graph on day 1
    # as a TWO-tier atlas — KNOWN regions (discovered, solid pins) and RUMOURED horizons
    # (visible-but-fogged, heard-of but not yet a confirmed destination). The BG world now
    # ships 13 authored regions: 5 known (the city districts + Wyrm's Crossing + the Sword
    # Coast regional pin) and 8 rumoured (Elturel, Reithwin, Candlekeep, Steel Watch
    # Foundry, Undercity, Bhaal Temple, Underdark, Avernus portal).
    w = content.load_world_data("baldurs-gate")
    region_ids = {str(r["id"]) for r in w["regions"]}
    assert len(region_ids) == 13  # guards the fixture: 5 known + 8 rumoured ship day-1
    rumoured_source_ids = {str(r["id"]) for r in w["regions"] if r.get("rumoured")}
    assert len(rumoured_source_ids) == 8, "8 BG regions are authored as rumoured horizons"

    c = content.seed_world(w)

    # every authored region the world ships is atlas-VISIBLE day-1 (known OR rumoured)
    seeded_regions = {lid: loc for lid, loc in c.locations.items() if lid in region_ids}
    assert seeded_regions.keys() == region_ids
    assert all(_atlas_is_visible(loc) for loc in seeded_regions.values()), (
        "fresh seed_world must surface all shipped BG regions on the day-1 atlas"
    )

    # the KNOWN tier is discovered + not-rumoured; the RUMOURED tier is rumoured + opted
    # out of discovered (so the all-regions-visible default never overrides the fog flag).
    known_regions = {lid: loc for lid, loc in seeded_regions.items() if lid not in rumoured_source_ids}
    rumoured_regions = {lid: loc for lid, loc in seeded_regions.items() if lid in rumoured_source_ids}
    assert all(loc.discovered is True and loc.rumoured is False for loc in known_regions.values()), (
        "known BG regions stay discovered (solid pins) and are not rumoured"
    )
    assert all(loc.rumoured is True and loc.discovered is False for loc in rumoured_regions.values()), (
        "rumoured BG regions are visible-but-fogged: rumoured True, discovered False"
    )
    assert len(known_regions) == 5 and len(rumoured_regions) == 8

    # the start is additionally VISITED (today's behavior, unchanged) — and is one of
    # the now-discovered known regions, not the only visible place.
    start = c.locations[c.current_location_id]
    assert start.visited is True
    assert start.discovered is True
    assert start.rumoured is False
    assert sum(1 for loc in c.locations.values() if loc.visited) == 1

    # every seeded location (regions + any ingested areas) is atlas-visible day-1, so the
    # known nav graph shows immediately rather than collapsing to ~1 location.
    assert all(_atlas_is_visible(loc) for loc in c.locations.values())
    assert sum(1 for loc in c.locations.values() if _atlas_is_visible(loc)) >= 13


def test_location_discovered_defaults_false_and_is_additive():
    # The new field is ADDITIVE: it defaults False (an unseeded/legacy Location is
    # exactly as before), and nothing forces it true outside seed_world's known graph.
    from models import Location

    bare = Location(name="Nowhere")
    assert bare.discovered is False
    # round-trips through the snapshot serialization the viewer reads
    assert json.loads(bare.model_dump_json())["discovered"] is False


def test_seed_world_honors_explicit_region_discovered_opt_out():
    # A world MAY hide a region day-1 (fog-of-war) by declaring discovered=False; the
    # engine must honor that rather than force every region visible. The other region,
    # which omits the flag, still defaults to discovered=True (the #261 day-1 behavior).
    world = {
        "id": "fog-test",
        "name": "Fog Test",
        "premise": "discovery opt-out fixture",
        "era": "now",
        "regions": [
            {"id": "loc-known", "name": "Known", "description": "open", "connections": []},
            {"id": "loc-fog", "name": "Hidden Vale", "description": "secret", "connections": [], "discovered": False},
        ],
        "starting_options": [{"location_id": "loc-known", "framing": "Start in the open."}],
    }

    c = content.seed_world(world)

    assert c.locations["loc-known"].discovered is True
    assert c.locations["loc-fog"].discovered is False
    # the opted-out, unvisited region is fog-of-war in the atlas; the known one shows
    assert _atlas_is_visible(c.locations["loc-known"]) is True
    assert _atlas_is_visible(c.locations["loc-fog"]) is False


def test_location_rumoured_defaults_false_and_round_trips_legacy_snapshot():
    # Issue #380 AC1/AC8: the new `rumoured` field is ADDITIVE. A bare Location defaults
    # to rumoured=False, and a LEGACY snapshot that predates the field (carries only
    # `discovered`) round-trips to a non-rumoured Location — so an old KNOWN place stays
    # KNOWN and the atlas is byte-identical to its pre-PR rendering.
    from models import Location

    bare = Location(name="Nowhere")
    assert bare.rumoured is False
    assert json.loads(bare.model_dump_json())["rumoured"] is False

    # A pre-#380 snapshot row: discovered known place, no `rumoured` key at all.
    legacy = Location.model_validate({"id": "loc-old", "name": "Old Known", "discovered": True})
    assert legacy.rumoured is False
    assert legacy.discovered is True
    assert _atlas_is_visible(legacy) is True  # still KNOWN, unchanged


def test_seed_world_propagates_rumoured_and_never_overrides_with_discovered_default():
    # Issue #380 AC5: seed_world must propagate `rumoured` from the source JSON for BOTH
    # regions AND ingested areas, and an explicit `rumoured: true` must NOT be overridden
    # by the all-places-visible `discovered` default. A rumoured place defaults discovered
    # to False (visible-but-fogged), while a plain place stays discovered=True (known).
    world = {
        "id": "rumour-test",
        "name": "Rumour Test",
        "premise": "rumoured-tier propagation fixture",
        "era": "now",
        "regions": [
            {"id": "loc-here", "name": "Home", "description": "start", "connections": []},
            # rumoured with NO explicit discovered → must come back rumoured + NOT discovered
            {"id": "loc-horizon", "name": "Far Horizon", "description": "heard of", "connections": [], "rumoured": True},
            # rumoured AND explicitly discovered=True → author wins on both flags
            {"id": "loc-both", "name": "Seen Rumour", "description": "both", "connections": [], "rumoured": True, "discovered": True},
        ],
        "starting_options": [{"location_id": "loc-here", "framing": "Start home."}],
    }

    c = content.seed_world(world)

    assert c.locations["loc-here"].rumoured is False
    assert c.locations["loc-here"].discovered is True  # plain place stays KNOWN

    horizon = c.locations["loc-horizon"]
    assert horizon.rumoured is True
    assert horizon.discovered is False, "rumoured default must NOT be overridden by discovered=True"
    assert _atlas_is_visible(horizon) is True  # visible-but-fogged

    both = c.locations["loc-both"]
    assert both.rumoured is True and both.discovered is True  # explicit author values both honored


# ── SYN-03: canon-roster surfaces are BOUNDED + misses resolve-then-suggest ──
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (SYN-03 / F10-3 + F13-2 + F13-3 + F14-1).
# A 2,076-record flagship roster was returned WHOLE (180KB) by list_canon_characters and
# DUMPED AS THE ERROR PAYLOAD (28KB) on a load_canon_character miss. These tests pin the
# additive limit/name_contains filter + the resolve-then-suggest miss path against a
# synthetic large roster, so the contract is verified independent of the shipped BG data.


def _synth_roster_world(root, world_id="synthville", n=300):
    """Write `n` canon-character JSON records into a tmp content dir and return its id.
    Records are Hero 0..n with a couple of known names ('Minsc and Boo', 'Shadowmaiden')
    so the resolve-then-suggest paths have something deterministic to match."""
    cdir = root / "worlds" / world_id / "characters"
    cdir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        rec = {"name": f"Hero {i:03d}", "race": "Human", "class": "Fighter",
               "playable": (i % 2 == 0)}
        (cdir / f"hero-{i:03d}.json").write_text(json.dumps(rec), encoding="utf-8")
    # two named anchors for substring / fuzzy resolution
    (cdir / "minsc-and-boo.json").write_text(
        json.dumps({"name": "Minsc and Boo", "race": "Human", "class": "Ranger", "playable": True}),
        encoding="utf-8")
    (cdir / "shadowmaiden.json").write_text(
        json.dumps({"name": "Shadowmaiden", "race": "Elf", "class": "Cleric", "playable": True}),
        encoding="utf-8")
    return world_id


def test_list_canon_characters_name_contains_is_additive(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_CONTENT_DIR", str(tmp_path / "content"))
    wid = _synth_roster_world(tmp_path / "content")
    # default (no filter) is byte-identical to before: the full 302-record list
    full = content.list_canon_characters(wid)
    assert len(full) == 302
    # name_contains is a case-insensitive substring filter
    minsc = content.list_canon_characters(wid, name_contains="minsc")
    assert [r["name"] for r in minsc] == ["Minsc and Boo"]
    heroes = content.list_canon_characters(wid, name_contains="Hero 01")
    assert len(heroes) == 10 and all("Hero 01" in r["name"] for r in heroes)


def test_suggest_canon_names_substring_then_fuzzy(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_CONTENT_DIR", str(tmp_path / "content"))
    wid = _synth_roster_world(tmp_path / "content")
    # a "Minsc" query is a SUBSTRING of "Minsc and Boo" — resolves by substring, ≤5, total reported
    sugg, total = content.suggest_canon_names(wid, "Minsc")
    assert sugg == ["Minsc and Boo"] and total == 302
    # a typo'd query with no substring hit falls back to difflib fuzzy
    fuzzy, _ = content.suggest_canon_names(wid, "Shadowmaidn")
    assert "Shadowmaiden" in fuzzy and len(fuzzy) <= 5
    # playable_only scopes BOTH the suggestions and the count. Hero 001 is non-playable
    # (odd index), so a playable-only "Hero 001" query never surfaces it.
    play, ptotal = content.suggest_canon_names(wid, "Hero 001", playable_only=True)
    assert "Hero 001" not in play and ptotal < total  # the playable subset is smaller
    # an even-indexed hero IS playable and resolves under playable_only
    play2, _ = content.suggest_canon_names(wid, "Hero 002", playable_only=True)
    assert "Hero 002" in play2


def _campaign_on_synth_world(tmp_path, monkeypatch):
    """A live campaign whose `world_id` points at the synthetic 300-record roster — built
    without a full world.json (the canon tools only read the world's characters dir).

    The campaign is created against the REAL bundled content (cellar-rats adventure), then
    CONTENT_DIR is redirected to the synthetic roster so the canon-character reads resolve
    there. Ordering matters: redirecting first would hide the cellar-rats adventure data."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path / "state"))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    monkeypatch.setenv("WORLDOS_CONTENT_DIR", str(tmp_path / "content"))
    wid = _synth_roster_world(tmp_path / "content")
    with server.campaign_lock(cid):
        c = server._require(cid)
        c.world_id = wid
        server.save_campaign(c)
    return cid


def test_canon_miss_payload_is_small_and_suggestive(tmp_path, monkeypatch):
    # The flagship-class regression: a miss must NOT serialize the whole roster.
    cid = _campaign_on_synth_world(tmp_path, monkeypatch)
    miss = server.load_canon_character(cid, "Nonexistent Person")
    assert "error" in miss  # play.sh reads this key — preserved
    assert "available" not in miss  # the old full-roster dump key is GONE
    assert isinstance(miss["did_you_mean"], list) and len(miss["did_you_mean"]) <= 5
    assert miss["available_count"] == 302  # tells the DM the roster size without listing it
    blob = json.dumps(miss)
    assert len(blob) < 2048, f"miss payload should be <2KB, got {len(blob)}B"


def test_canon_miss_resolves_substring_class_before_suggesting(tmp_path, monkeypatch):
    # "Minsc" should RESOLVE (load_canon_character's unique-substring step), not miss.
    cid = _campaign_on_synth_world(tmp_path, monkeypatch)
    hit = server.load_canon_character(cid, "Minsc")
    assert hit.get("name") == "Minsc and Boo" and "error" not in hit


def test_start_character_pickup_miss_suggests_not_dumps(tmp_path, monkeypatch):
    # SYN-03: the start_character pickup miss must suggest, never dump the playable roster.
    cid = _campaign_on_synth_world(tmp_path, monkeypatch)
    miss = server.start_character(cid, origin="pickup:Shadowmaidn")  # typo
    assert "error" in miss and miss["playable"] is True
    assert "did_you_mean" in miss and len(miss["did_you_mean"]) <= 5
    assert "Shadowmaiden" in miss["did_you_mean"]  # fuzzy-resolved
    assert "playable" in miss and isinstance(miss["available_count"], int)
    assert miss["available_count"] > 5  # large roster, NOT dumped
    assert len(json.dumps(miss)) < 2048
