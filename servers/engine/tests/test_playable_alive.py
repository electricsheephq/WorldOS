"""A canon-DEAD figure must never be selectable or seatable as the PLAYER (#305).

Dal Lightspark ships as a canon record whose lineage OPENS "Harper Lightspark is a dead gold
dwarven Harper whose corpse is in the Shadow-Cursed Lands …" — he has no structured status
field and no ingested portrait (he is dead in canon), yet the roster picker listed him as a
playable PC and the seat path bound him as the protagonist. Playing a character whose
canon-truth is "dead and rotting" breaks the prestige-CRPG framing (NORTH-STAR P3: characters
STAY themselves).

These guard the engine fix:
  * `content.is_dead_record` — the conservative death signal: a structured `alive:false` /
    `dead:true` flag, a `status`/`fate` string classified "died", or a death SELF-declaration
    in the backstory OPENER. It must flag Dal and the corpse corpus, must NOT flag a living
    figure whose bio merely mentions death, and must NOT flag the "Dead Eyes" bandit gang.
  * `content.list_canon_characters(playable_only=True)` / `content.roster_surface(...)` — the
    player-facing projections drop the dead (alive_only follows playable_only by default).
  * `server.load_canon_character(kind="player", …)` — the HARD GATE: a dead figure is rejected
    as the PC (clear error) but may still be pulled in as a lore NPC.
"""

import json

import content
import server

WORLD = "baldurs-gate"
DAL = "Dal Lightspark"          # canon-DEAD: "a dead gold dwarven Harper whose corpse is in …"
LIVING_PC = "Aubree"            # living half-elf ranger of the Flaming Fist, has an ingested portrait
# canon-DEAD where the death is declared NOT in the opener but later in the bio (dogfood MAJOR):
# Alexander Rainforest's opener reads as a living citizen — "… Rainforest is already dead." lands
# in the THIRD sentence, so the opener-only scan missed it and the picker offered a corpse as a PC.
RAINFOREST = "Alexander Rainforest"  # "… When the party reach him in Act Three, Rainforest is already dead."
PEARTREE = "Franc Peartree"          # "… Peartree (if encountered) is already dead."
PRINSKI = "Waldemar Prinski"         # "… he is found dead in the Iron Throne soon after."


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    c = content.seed_world(content.load_world_data(WORLD))
    server.save_campaign(c)
    return c


# --- is_dead_record, in isolation -------------------------------------------

def test_is_dead_record_flags_the_backstory_opener_declaration():
    # The exact #305 record: death declared only in the lineage opener (no status field).
    dal = content.load_canon_character(WORLD, DAL)
    assert dal is not None and "corpse" in dal.get("backstory", "").lower()
    assert content.is_dead_record(dal) is True


def test_is_dead_record_honours_structured_signals():
    assert content.is_dead_record({"name": "X", "alive": False}) is True
    assert content.is_dead_record({"name": "X", "dead": True}) is True
    assert content.is_dead_record({"name": "X", "status": "slain at the bridge"}) is True
    assert content.is_dead_record({"name": "X", "fate": "died in Act One"}) is True
    # a LIVE structured status is not dead
    assert content.is_dead_record({"name": "X", "status": "alive and still himself"}) is False


def test_is_dead_record_does_not_false_positive_on_a_living_figure():
    # FALSE-POSITIVE GUARD: a LIVING figure whose bio merely MENTIONS death (a dead enemy/kin,
    # a "deadly" weapon, the "Dead Eyes" bandit gang, a relative they avenged) must NOT be
    # excluded — only a SELF-declaration in the opener counts.
    living = [
        # subject mentions a dead relative / avenged death later in the bio
        {"name": "Avenger", "class": "Fighter",
         "backstory": "Borin is a dwarf smith. He avenged his dead brother, slain by a goblin raid."},
        # "deadly"/"deadeye" must not trip the \bdead\b word-boundary guard
        {"name": "Sharpshooter", "class": "Ranger",
         "backstory": "Vael is a deadeye archer with a deadly aim, very much alive in the Lower City."},
        # the "Dead Eyes" bandit GANG — its members are alive
        {"name": "Gang Member", "class": "Rogue",
         "backstory": "Sly is a member of the Dead Eyes, a bandit group in the Lower City."},
        # a LIVING figure who ACTIVELY killed someone (subject is the killer, not the victim)
        {"name": "Killer", "class": "Barbarian",
         "backstory": "Grok is a warrior who killed a goblin chief and lives to boast of it."},
    ]
    for rec in living:
        assert content.is_dead_record(rec) is False, rec["name"]


def test_is_dead_record_flags_a_self_death_declared_later_in_the_bio():
    # Dogfood MAJOR: a corpse whose death is declared NOT in the opener but mid-bio. The opener
    # ("Alexander Rainforest was a citizen of Baldur's Gate.") reads as a living NPC, so the
    # opener-only scan let the picker offer him as a playable hero. The body cue must catch the
    # later self-declaration ("… Rainforest is already dead.").
    for nm in (RAINFOREST, PEARTREE, PRINSKI):
        rec = content.load_canon_character(WORLD, nm)
        assert rec is not None, nm
        assert content.is_dead_record(rec) is True, nm


def test_body_dead_cue_does_not_false_positive_on_a_third_party_death():
    # FALSE-POSITIVE GUARD for the widened (whole-bio) scan: a LIVING figure whose bio mentions
    # SOMEONE ELSE's death — "appears … if Alfira is dead", "if he is dead" (a different NPC),
    # "declaration that Aylin was dead" — must NOT be flagged. The cue is subject-anchored and
    # ignores the conditional "if X is dead" third-party form.
    # NB: the third-party death cue lands in a LATER sentence (the opener is a clean living
    # intro), mirroring the real corpus — so this exercises the widened whole-bio scan, not the
    # separate opener cue. A conditional "if X is dead" must never flag the SUBJECT.
    living = [
        {"name": "Chell-like", "class": "Bard", "level": "3",
         "backstory": "Chell is a tiefling bard of the Lower City. She appears as a replacement character for Alfira during the camp celebration if Alfira is dead."},
        {"name": "Kavil-like", "class": "Fighter", "level": "4",
         "backstory": "Kavil is a stout dwarf of the Flaming Fist. He appears as a replacement for Bex during the camp celebration if he is dead."},
        {"name": "Olys-like", "class": "Ranger", "level": "5",
         "backstory": "Olys is an Asmodeus tiefling ranger and Harper. If one of the original Harper allies is dead, he can take their place during the assault."},
        {"name": "Isobel-like", "class": "Cleric", "level": "6",
         "backstory": "Isobel is a cleric of Selune who lives at Last Light Inn. She saw a change in her father, and with his declaration that Aylin was dead, she could see no recourse but to run."},
    ]
    for rec in living:
        assert content.is_dead_record(rec) is False, rec["name"]


def test_living_canon_pc_is_not_flagged_dead():
    aubree = content.load_canon_character(WORLD, LIVING_PC)
    assert aubree is not None
    assert content.is_dead_record(aubree) is False


# --- the playable projections drop the dead ---------------------------------

def test_list_canon_characters_playable_only_excludes_the_dead():
    names = {r["name"] for r in content.list_canon_characters(WORLD, playable_only=True)}
    assert DAL not in names, "a dead figure must not appear in the playable list"
    assert LIVING_PC in names, "a living, playable canon figure must still be listed"


def test_list_canon_characters_alive_only_overridable():
    # alive_only follows playable_only by default; force it off and the dead reappear.
    raw = {r["name"] for r in content.list_canon_characters(WORLD, playable_only=False, alive_only=False)}
    alive = {r["name"] for r in content.list_canon_characters(WORLD, playable_only=False, alive_only=True)}
    assert DAL in raw and DAL not in alive


def test_roster_surface_excludes_the_dead_from_cards_and_facets():
    # The picker surface (playable_only=True) must not carry Dal as a card, and his race ("Dwarf")
    # must still be a real facet (living dwarves exist) — but he himself contributes nothing.
    surf = content.roster_surface(WORLD, playable_only=True, limit=0)  # limit<=0 -> no cap
    names = {c["name"] for c in surf["characters"]}
    assert DAL not in names
    assert LIVING_PC in names
    # the dead-only id slug must not be present either
    assert "dal-lightspark" not in {c["id"] for c in surf["characters"]}


def test_roster_surface_excludes_a_mid_bio_dead_figure():
    # The UI complement to #912: the picker must never OFFER what the seat REFUSES. A corpse
    # whose death is declared mid-bio (Alexander Rainforest) must be absent from every card.
    surf = content.roster_surface(WORLD, playable_only=True, limit=0)
    names = {c["name"] for c in surf["characters"]}
    for nm in (RAINFOREST, PEARTREE, PRINSKI):
        assert nm not in names, nm
    assert "alexander-rainforest" not in {c["id"] for c in surf["characters"]}


# --- require_stats: drop entries illegible in a level-based picker ----------

def test_roster_surface_require_stats_drops_records_lacking_both_class_and_level():
    # "Amanita Szarr — Vampire or Vampire Spawn" has neither class nor level — confusing in a
    # level-based picker. require_stats=True drops records missing BOTH (a townsperson with a
    # level but no class, or a class but no level, still rides along — only the doubly-blank go).
    full = content.roster_surface(WORLD, playable_only=True, limit=0)
    stats = content.roster_surface(WORLD, playable_only=True, require_stats=True, limit=0)
    full_names = {c["name"] for c in full["characters"]}
    stats_names = {c["name"] for c in stats["characters"]}
    assert "Amanita Szarr" in full_names, "default surface keeps the classless/levelless figure"
    assert "Amanita Szarr" not in stats_names, "require_stats drops the doubly-blank figure"
    # Every surviving card has at least ONE of class / level so it is legible in the picker.
    for c in stats["characters"]:
        assert (c.get("class") or "").strip() or (c.get("level") or "").strip(), c["name"]
    # require_stats is additive-narrowing: a strict subset, and it never re-admits the dead.
    assert stats_names <= full_names
    assert RAINFOREST not in stats_names


def test_roster_surface_require_stats_defaults_off_so_default_surface_is_unchanged():
    # The default (require_stats unset) call is byte-identical to before — no silent narrowing.
    a = content.roster_surface(WORLD, playable_only=True, limit=0)
    b = content.roster_surface(WORLD, playable_only=True, require_stats=False, limit=0)
    assert a == b


# --- recommended_only: a curated beginner subset ----------------------------

def test_roster_surface_recommended_only_is_a_small_curated_legible_subset():
    # BEGINNER ENTRY: a newcomer should not be dropped into ~2,000 alphabetical names. The
    # recommended subset leads with playable+alive mid-tier figures that carry BOTH a class AND
    # a level AND a backstory — legible, characterful picks — and is small.
    rec = content.roster_surface(WORLD, playable_only=True, recommended_only=True)
    cards = rec["characters"]
    assert cards, "the recommended subset should not be empty for the shipped roster"
    assert rec["recommended"] is True
    assert len(cards) <= 24, "the beginner subset stays small (not the whole roster)"
    full = content.roster_surface(WORLD, playable_only=True, limit=0)
    assert rec["total"] < full["total"], "recommended is a strict narrowing of the full roster"
    rec_names = {c["name"] for c in cards}
    for c in cards:
        assert (c.get("class") or "").strip(), c["name"]
        assert (c.get("level") or "").strip(), c["name"]
        assert (c.get("backstory") or "").strip(), c["name"]
    # never the dead, never a malformed figure
    assert RAINFOREST not in rec_names and DAL not in rec_names
    assert "Amanita Szarr" not in rec_names


def test_roster_surface_recommended_only_defaults_off():
    a = content.roster_surface(WORLD, playable_only=True, limit=0)
    b = content.roster_surface(WORLD, playable_only=True, recommended_only=False, limit=0)
    assert a == b
    assert a.get("recommended") in (None, False)


# --- the hard seat gate ------------------------------------------------------

def test_seat_guard_rejects_a_dead_player(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    res = server.load_canon_character(c.id, DAL, kind="player", add_to_party=True)
    assert res.get("error"), "seating a dead figure as the PC must return an error"
    assert res.get("dead_in_canon") is True
    assert "dead in canon" in res["error"].lower()
    # and nothing got seated as the player
    chars = server._require(c.id).characters.values()
    assert not any(ch.kind == "player" for ch in chars)


def test_dead_figure_still_loadable_as_a_lore_npc(tmp_path, monkeypatch):
    # The gate is targeted at the PC only — a corpse can still be an encounterable lore NPC
    # (Bestiary Persons tab), so kind="npc" is NOT blocked.
    c = _seed(tmp_path, monkeypatch)
    res = server.load_canon_character(c.id, DAL, kind="npc")
    assert "error" not in res, "a dead figure must still load as a lore NPC"
    assert res["kind"] == "npc"


def test_living_canon_pc_is_seatable(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    res = server.load_canon_character(c.id, LIVING_PC, kind="player", add_to_party=True)
    assert "error" not in res, res
    ch = server._require(c.id).characters[res["id"]]
    assert ch.kind == "player" and ch.id in server._require(c.id).party


def test_synthetic_dead_record_blocked_living_record_allowed(tmp_path, monkeypatch):
    # Belt-and-suspenders with a SYNTHETIC pair so the test is robust to canon-corpus edits:
    # a dead record is gated, an otherwise-identical living record is not. Patch the content
    # loader the tool calls (same technique as test_canon_abilities).
    c = _seed(tmp_path, monkeypatch)
    dead_rec = {"name": "Corpse McGee", "class": "Fighter", "level": "3",
                "backstory": "Corpse McGee is a dead human whose corpse can be found in the sewers."}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: dead_rec)
    res = server.load_canon_character(c.id, "Corpse McGee", kind="player", add_to_party=True)
    assert res.get("dead_in_canon") is True and res.get("error")

    live_rec = {"name": "Hale Brightblade", "class": "Fighter", "level": "3",
                "backstory": "Hale Brightblade is a living human guard who patrols the Lower City."}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: live_rec)
    res2 = server.load_canon_character(c.id, "Hale Brightblade", kind="player", add_to_party=True)
    assert "error" not in res2 and not res2.get("dead_in_canon")
