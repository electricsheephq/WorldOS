"""Canon max_hp derivation (#352: a seated canon PC got a critically-low max_hp — broke combat).

A standalone canon character JSON ships a class + level but (across the shipped corpus) NO
`max_hp` field. `load_canon_character` runs `_apply_srd_class_defaults`, which DOES compute the
correct class+level HP onto the max_hp<=1 stub — but the seat path then UNCONDITIONALLY floored
the result to `max(canon_hp, 10)`, clobbering it back down to a flat 10 for every classed canon
figure. QA ow-living1 (living-PC duo): Charming Latham, a L5 Guild Wizard with no `max_hp`, was
seated at max_hp 10 instead of his class+level 32 — the angry-dm scorer's "single worst seam,
must fix before combat." (Same stat-seeding class as #322, where canon records lacked an
`abilities` block and seated flat-10 ability scores.)

These guard the engine-level fix in server.load_canon_character (and the shared _class_level_hp
helper that both it and _apply_srd_class_defaults now use):
  * a class-typed canon record with NO max_hp -> a class+level-appropriate value (hit-die + CON
    per level), NOT a flat 10 (Latham, a real canon L5 Wizard -> 32);
  * a record whose EXPLICIT max_hp is at or above the class+level floor -> that value is honored
    (a hand-authored / higher-than-formula sheet always wins upward);
  * a record whose EXPLICIT max_hp is BELOW the class+level floor -> the class floor wins (the
    "critically-low canon max_hp" case — a low placeholder must not seat a fragile combatant);
  * a class-less / unknown-class record -> the modest flat-10 stub (today's behavior), since it
    can't be sized for a class+level.
"""

import pytest

import content
import server
import srd_tables
from models import Ability

WORLD = "baldurs-gate"


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = content.seed_world(content.load_world_data(WORLD))
    server.save_campaign(c)
    return c


# --- the _class_level_hp helper, in isolation -------------------------------

def test_class_level_hp_matches_srd_fixed_hp_formula():
    # SRD fixed-HP: max die + CON at L1, then average (die//2+1) + CON per level after.
    # Wizard d6, CON +2, L5: 6+2 + 4*((6//2+1)+2) = 8 + 4*5 = 28? No — die+con at L1 = 8,
    # plus (5-1) levels * (4 + 2) = 4*6 = 24 -> wait: per-level = (die//2+1)+con = 4+2 = 6.
    # 8 + 4*6 = 32. (CON +2 from the L5 wizard's derived array.)
    assert server._class_level_hp("wizard", 5, 2) == 32
    # L1 of any class is just max-die + CON.
    assert server._class_level_hp("wizard", 1, 2) == srd_tables.hit_die("wizard") + 2
    assert server._class_level_hp("fighter", 1, 3) == srd_tables.hit_die("fighter") + 3
    # A d8 cleric at L3, CON +2: 8+2 + 2*((8//2+1)+2) = 10 + 2*7 = 24.
    assert server._class_level_hp("cleric", 3, 2) == 24
    # Negative CON is allowed but the value never drops below 1.
    assert server._class_level_hp("wizard", 1, -5) >= 1


def test_class_level_hp_returns_none_for_unknown_class():
    # An unknown / class-less label can't be sized -> None (the caller keeps the flat-10 stub).
    assert server._class_level_hp("", 1, 0) is None
    assert server._class_level_hp("townsperson", 5, 0) is None


def test_class_level_hp_tolerates_bad_level():
    # A non-int level coerces to L1 rather than raising (canon `level` is a string field).
    assert server._class_level_hp("wizard", "oops", 2) == srd_tables.hit_die("wizard") + 2
    assert server._class_level_hp("wizard", None, 2) == srd_tables.hit_die("wizard") + 2


# --- through the load_canon_character tool ----------------------------------

def test_canon_wizard_seats_with_class_level_maxhp_not_flat_ten(tmp_path, monkeypatch):
    # THE #352 REPRO. Charming Latham (Guild) is a LIVING canon L5 Wizard shipping no `max_hp`
    # field. He must seat at his class+level HP (d6 hit-die + CON per level = 32 with the #322-
    # derived CON +2), NOT the old flat-10 floor that broke combat.
    c = _seed(tmp_path, monkeypatch)
    res = server.load_canon_character(c.id, "Charming Latham", kind="player", add_to_party=True)
    assert "error" not in res
    ch = server._require(c.id).characters[res["id"]]
    con = ch.abilities.modifier(Ability.CON)
    expected = server._class_level_hp("wizard", 5, con)
    assert ch.max_hp == expected == 32, (ch.max_hp, expected)
    assert ch.max_hp > 10, "must NOT be the critically-low flat-10 floor"
    assert ch.current_hp == ch.max_hp  # seats at full health
    assert ch.hit_dice == "5d6"


def test_classed_canon_with_no_hp_derives_floor(tmp_path, monkeypatch):
    # A different class+level (Cleric d8, L3) with no max_hp also seats at its class floor.
    c = _seed(tmp_path, monkeypatch)
    record = {"name": "Plain Cleric", "race": "Human", "class": "Cleric", "level": "3"}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: record)
    res = server.load_canon_character(c.id, "Plain Cleric", kind="npc")
    assert "error" not in res and not res.get("already_present")
    ch = server._require(c.id).characters[res["id"]]
    con = ch.abilities.modifier(Ability.CON)
    assert ch.max_hp == server._class_level_hp("cleric", 3, con) > 10


def test_explicit_canon_maxhp_above_floor_is_preserved(tmp_path, monkeypatch):
    # A hand-authored / higher-than-formula canon max_hp (above the class+level floor) wins upward
    # and is NOT lowered to the formula value. (No record ships one today; inject via the loader.)
    c = _seed(tmp_path, monkeypatch)
    record = {"name": "Beefy Fighter", "race": "Human", "class": "Fighter", "level": "5",
              "max_hp": 99}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: record)
    res = server.load_canon_character(c.id, "Beefy Fighter", kind="companion", add_to_party=True)
    assert "error" not in res and not res.get("already_present")
    ch = server._require(c.id).characters[res["id"]]
    assert ch.max_hp == 99, "an explicit canon max_hp above the class floor must be honored"
    assert ch.current_hp == 99


def test_explicit_canon_maxhp_below_floor_uses_class_floor(tmp_path, monkeypatch):
    # The "critically-low canon max_hp" case: an explicit value BELOW the class+level floor is a
    # low placeholder; the class floor must win so the seated combatant isn't one-shot fragile.
    c = _seed(tmp_path, monkeypatch)
    record = {"name": "Underfed Wizard", "race": "Human", "class": "Wizard", "level": "5",
              "max_hp": 4}  # absurdly low for a L5 wizard
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: record)
    res = server.load_canon_character(c.id, "Underfed Wizard", kind="player", add_to_party=True)
    assert "error" not in res
    ch = server._require(c.id).characters[res["id"]]
    con = ch.abilities.modifier(Ability.CON)
    floor = server._class_level_hp("wizard", 5, con)
    assert ch.max_hp == floor > 4, (ch.max_hp, floor)


def test_classless_canon_keeps_flat_ten_stub(tmp_path, monkeypatch):
    # A class-less / unknown-class record can't be sized for a class+level, so it keeps the modest
    # flat-10 identity stub (today's behavior — never the instant-kill max_hp=1 default).
    c = _seed(tmp_path, monkeypatch)
    record = {"name": "Nameless Ghost", "race": "Human", "class": ""}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: record)
    res = server.load_canon_character(c.id, "Nameless Ghost", kind="npc")
    assert "error" not in res and not res.get("already_present")
    ch = server._require(c.id).characters[res["id"]]
    assert ch.max_hp == 10
    assert ch.current_hp == 10
