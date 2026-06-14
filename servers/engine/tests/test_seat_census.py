"""F02-8 + F02-18 — cross-path SEAT CENSUS net.

Five of unit-02's findings are literally "one seat path missed a fix another got"
(F02-1/4/8/9/10/12). This is the highest-leverage prevention: assert the SAME
invariants across EVERY seat path (create / start fresh / pickup-fresh /
pickup-promote / recruit / reroll / load_canon), so a future single-path patch
that forgets a sibling path trips here.

The invariant bundle (audit F02-18, a)-(l), as it applies to the still-relevant
paths after the F02-1/2/4 fixes landed in #833):
  (a) a seated PC/companion with a known class at level L has hit_dice == "Ld<die>";
  (b) max_hp is at least the SRD class+level floor (no stub HP at level>1);
  (c) an armored AC (>12) is backed by an armor item on the sheet (or is the
      class unarmored default);
  (g) carve-out for reroll's "gear lost with the body": the new hero is seated with
      a kit that JUSTIFIES its AC (we seed gear), so the same (c) check holds;
  (h) a freshly seated, living PC/companion is NOT dead/stable (no death state);
  (i) a canon-DEAD record is never seatable as the PLAYER on ANY pickup path.
"""

import re

import pytest

import content as content_mod
import server
import store
from models import Ability


WORLD = "baldurs-gate"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("CLAWDND_ACTOR_ID", raising=False)
    monkeypatch.delenv("CLAWDND_ACTOR_ROLE", raising=False)
    yield


def _seeded_world_campaign():
    c = content_mod.seed_world(content_mod.load_world_data(WORLD))
    server.save_campaign(c)
    return c.id


# --------------------------------------------------------------------------- #
# Shared seat-invariant assertions                                            #
# --------------------------------------------------------------------------- #
def _assert_seat_invariants(ch, *, expect_alive=True):
    """The cross-path bundle, applied to one seated record."""
    if not ch.classes:
        return  # class-less nobody — nothing to derive
    import srd_tables
    cname = ch.classes[0].name.lower()
    try:
        die = srd_tables.hit_die(cname)
    except ValueError:
        return  # unknown/homebrew class
    level = ch.total_level
    single = len({cl.name.lower() for cl in ch.classes}) == 1
    if single:
        # (a) hit dice string scaled to level
        assert ch.hit_dice == f"{level}d{die}", (
            f"{ch.name}: hit_dice {ch.hit_dice!r} != {level}d{die}")
        # (b) no stub HP at level>1
        floor = server._class_level_hp(cname, level, ch.ability_modifier(Ability.CON))
        assert ch.max_hp >= floor, (
            f"{ch.name}: max_hp {ch.max_hp} below class floor {floor} at L{level}")
    # (c) armored AC is backed by armor on the sheet
    if ch.armor_class > 12:
        has_armor = any(
            re.search(r"armor|mail|plate|leather|shield", (it.name or "").lower())
            for it in ch.inventory
        )
        assert has_armor, (
            f"{ch.name}: AC {ch.armor_class} (>12) with no armor item on the sheet")
    # (h) a living seat carries no death state
    if expect_alive:
        assert not ch.dead and not ch.stable, f"{ch.name}: seated with a death flag set"
        assert ch.death_saves.successes == 0 and ch.death_saves.failures == 0


# --------------------------------------------------------------------------- #
# (a)/(b)/(c)/(h) hold on EVERY living seat path                               #
# --------------------------------------------------------------------------- #
def test_create_character_seat_invariants():
    cid = server.create_campaign("c")["id"]
    fid = server.create_character(cid, "Borg", kind="player", class_name="Fighter",
                                  level=5, apply_srd_defaults=True,
                                  abilities={"constitution": 14})["id"]
    _assert_seat_invariants(store.load_campaign(cid).characters[fid])


def test_start_character_fresh_seat_invariants():
    cid = server.create_campaign("s")["id"]
    out = server.start_character(cid, origin="veteran_l5", name="Vex", class_name="Fighter",
                                 abilities={"constitution": 14})
    _assert_seat_invariants(store.load_campaign(cid).characters[out["id"]])


def test_recruit_companion_seat_invariants_at_level_5():
    cid = server.create_campaign("r")["id"]
    npc = server.create_character(cid, "Helm", kind="npc")["id"]
    out = server.recruit_companion(cid, npc, class_name="Fighter", level=5,
                                   abilities={"constitution": 14})
    _assert_seat_invariants(store.load_campaign(cid).characters[out["id"]])


def test_reroll_seat_invariants():
    cid = server.create_campaign("rr")["id"]
    pc = server.create_character(cid, "Dead", kind="player", class_name="Fighter",
                                 level=4, apply_srd_defaults=True,
                                 abilities={"constitution": 14})["id"]
    server.apply_damage(cid, pc, 9999)
    out = server.reroll_character(cid, pc, "New", class_name="Fighter", level=4,
                                  abilities={"constitution": 14})
    _assert_seat_invariants(store.load_campaign(cid).characters[out["new_pc"]["id"]])


# --------------------------------------------------------------------------- #
# (h) promote-via-pickup must clear a stale death state                       #
# --------------------------------------------------------------------------- #
def test_pickup_promote_clears_death_state_on_a_dead_roster_record(monkeypatch):
    cid = _seeded_world_campaign()
    c = store.load_campaign(cid)
    # Inject a PLAYABLE, ALIVE-in-canon roster NPC that is currently marked dead at the
    # seat (e.g. a stub that died in combat as a bare identity). pickup:promote it.
    from models import Character, DeathSaves
    npc = Character(id="npc-thatcher", name="Thatcher", kind="npc", classes=[])
    npc.dead = True
    npc.stable = True
    npc.death_saves = DeathSaves(failures=3)
    c.characters[npc.id] = npc
    store.save_campaign(c)

    rec = {"name": "Thatcher", "class": "Fighter", "level": 5, "playable": True,
           "abilities": {"strength": 16, "constitution": 14}}
    monkeypatch.setattr(content_mod, "load_canon_character", lambda w, n: rec)
    monkeypatch.setattr(content_mod, "is_playable", lambda r: True)
    monkeypatch.setattr(content_mod, "is_dead_record", lambda r: False)

    out = server.start_character(cid, origin="pickup:Thatcher")
    assert out.get("promoted_existing") is True
    seated = store.load_campaign(cid).characters[out["id"]]
    assert seated.kind == "player"
    assert seated.current_hp > 0
    assert not seated.dead and not seated.stable
    assert seated.death_saves.failures == 0
    _assert_seat_invariants(seated)


# --------------------------------------------------------------------------- #
# (i) a canon-DEAD record is never seatable as the PLAYER via pickup          #
# --------------------------------------------------------------------------- #
def test_pickup_rejects_a_canon_dead_record_as_the_player(monkeypatch):
    cid = _seeded_world_campaign()
    rec = {"name": "Ghoulen", "class": "Wizard", "level": 5, "playable": True,
           "backstory": "Ghoulen is a dead necromancer whose corpse lies in the crypt."}
    monkeypatch.setattr(content_mod, "load_canon_character", lambda w, n: rec)
    monkeypatch.setattr(content_mod, "is_playable", lambda r: True)
    monkeypatch.setattr(content_mod, "is_dead_record", lambda r: True)

    out = server.start_character(cid, origin="pickup:Ghoulen")
    assert "error" in out
    assert out.get("dead_in_canon") is True
    # nothing was seated
    assert all(ch.name != "Ghoulen" or ch.kind != "player"
               for ch in store.load_campaign(cid).characters.values())
