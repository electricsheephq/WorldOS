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
