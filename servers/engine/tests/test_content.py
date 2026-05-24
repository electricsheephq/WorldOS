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
    # The DEFAULT path (no ending) must reproduce today's base seed EXACTLY — no era
    # rewrite, no extra lore, no fate facts, ending_id empty.
    w = content.load_world_data("baldurs-gate")
    base = content.seed_world(w)
    assert base.ending_id == ""
    assert base.era == str(w.get("era"))  # base chronology, untouched
    expected_lore = [str(x) for x in (w.get("history", []) + w.get("standing_threads", [])) if str(x).strip()]
    assert base.lore == expected_lore  # exactly the base history + threads, nothing appended
    # each roster NPC carries only its single base hook (no overlay fate fact)
    jaheira = next(ch for ch in base.characters.values() if ch.name == "Jaheira")
    assert len(jaheira.memory) == 1
    # an unknown/empty ending also falls through to the base state (no crash, no change)
    unknown = content.seed_world(w, ending="no-such-ending")
    assert unknown.ending_id == "" and unknown.era == base.era and unknown.lore == base.lore


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
    allc = server.list_canon_characters(cid)["available"]
    names = {x["name"] for x in allc}
    # every record now reports a playable flag; the seven origin heroes are NOT playable
    assert {"Astarion", "Gale", "Karlach", "Lae'zel", "Shadowheart", "Wyll", "Halsin"} <= names
    by_name = {x["name"]: x for x in allc}
    assert by_name["Astarion"]["playable"] is False and by_name["Astarion"]["role"] == "hero"
    assert by_name["Minsc"]["playable"] is True
    # the playable-only filter keeps just the minor figures a player can pick up
    play = {x["name"] for x in server.list_canon_characters(cid, playable_only=True)["available"]}
    assert play == {"Jaheira", "Minsc", "Withers", "Jergal"}
    assert "Astarion" not in play and "Gale" not in play


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
    assert "Minsc" in ph["playable_options"]  # points the player at who they CAN pick up
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
    for wid in ("baldurs-gate", "sundered-reach"):
        w = content.load_world_data(wid)
        base = content.seed_world(w)
        expected_lore = [str(x) for x in (w.get("history", []) + w.get("standing_threads", [])) if str(x).strip()]
        assert base.lore == expected_lore  # exactly base history + threads, nothing dropped/added
        assert base.ending_id == ""
        # one beat per base standing thread, in order, unique ids, text == the thread
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
    # ADDITIVE: after the 7 authored regions, the 2 example areas are seeded as Locations.
    w = content.load_world_data("baldurs-gate")
    base_regions = len(w["regions"])
    c = content.seed_world(w)
    by_name = {loc.name: loc for loc in c.locations.values()}
    # the authored regions are all still present, plus the ingested areas
    assert "Baldur's Gate — Lower City" in by_name      # an authored region
    assert "Bloomridge Market" in by_name               # an ingested area
    assert "the Siltwharf Steps" in by_name
    assert len(c.locations) == base_regions + 2

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
    # (the example areas have unique names, so the count is the regions + 2 areas)
    assert before == len(w2["regions"]) + 2


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
