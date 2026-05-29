"""Canon ability-score derivation (fix: canon characters loaded flat 10/10/10).

A standalone canon character JSON ships a class + level but (in the entire shipped corpus)
NO `abilities` block, so `load_canon_character` used to seat the model's flat 10/10/10/10/10/10
default — a Wizard PC cast every spell at +0 (QA ow-v103-reval: Dal Lightspark, a L5 evoker,
and 11 NPCs all loaded flat-10; only Withers, fleshed out via a different path, was correct).

These guard the engine-level fix in server.load_canon_character:
  * a class-typed canon record with NO abilities -> a class+level-appropriate 5e standard
    array (primary stat highest), NOT flat 10s, and `ability_source == "derived"`;
  * a record WITH an explicit `abilities` block -> those scores UNCHANGED (`ability_source
    == "canon"`) — a hand-authored sheet always wins;
  * a class-less / unknown-class record -> still flat-10 (today's behavior), and because that
    leaves a PLAYER (or any seated spellcaster) un-sized the additive placeholder WARNING fires;
  * the standard-array derivation itself (clean + messy canon class strings) via the helper.
"""

import pytest

import content
import server
from models import Ability, AbilityScores

WORLD = "baldurs-gate"
ABK = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = content.seed_world(content.load_world_data(WORLD))
    server.save_campaign(c)
    return c


# --- the derivation helper, in isolation ------------------------------------

def test_derive_gives_class_appropriate_array_with_primary_highest():
    # Standard array is [15,14,13,12,10,8]; each class's PRIMARY ability is the max.
    primaries = {
        "Wizard": "intelligence", "Cleric": "wisdom", "Druid": "wisdom",
        "Rogue": "dexterity", "Ranger": "dexterity", "Monk": "dexterity",
        "Fighter": "strength", "Paladin": "strength", "Barbarian": "strength",
        "Sorcerer": "charisma", "Warlock": "charisma", "Bard": "charisma",
    }
    for cls, primary in primaries.items():
        a = server._derive_canon_abilities(cls, 1)
        assert a is not None, cls
        vals = {f: getattr(a, f) for f in ABK}
        # the primary is the unique maximum and well above the flat-10 placeholder
        assert vals[primary] == max(vals.values()) > 10, (cls, vals)
        assert sorted(vals.values()) == [8, 10, 12, 13, 14, 15], (cls, vals)


def test_derive_applies_asi_to_primary_with_level():
    # A L5 caster has reached one ASI level (4); +2 to the primary -> 15 -> 17.
    l1 = server._derive_canon_abilities("Wizard", 1)
    l5 = server._derive_canon_abilities("Wizard", 5)
    l8 = server._derive_canon_abilities("Wizard", 8)
    assert l1.intelligence == 15
    assert l5.intelligence == 17  # one ASI (L4) applied
    assert l8.intelligence == 19  # two ASIs (L4, L8) applied
    assert l8.intelligence <= 20  # capped


def test_derive_normalizes_messy_canon_class_strings():
    # The corpus carries free-text classes ("cleric necromancer", "ranger, rogue", "Eldritch
    # Knight") that srd_tables rejects; the leading recognized class word wins.
    assert server._derive_canon_abilities("cleric necromancer", 1).wisdom == 15
    assert server._derive_canon_abilities("druid (circle of the land)", 1).wisdom == 15
    assert server._derive_canon_abilities("ranger, rogue", 1).dexterity == 15  # ranger first
    assert server._derive_canon_abilities("Eldritch Knight", 1).strength == 15  # -> fighter
    # an unknown / class-less record cannot be sized
    assert server._derive_canon_abilities("", 1) is None
    assert server._derive_canon_abilities("human", 1) is None


# --- through the load_canon_character tool ----------------------------------

def test_canon_wizard_loads_with_derived_int_not_flat_ten(tmp_path, monkeypatch):
    # Charming Latham (Guild) is a LIVING canon L5 Wizard shipping NO abilities block — the
    # exact ow-v103-reval defect. He must seat with INT as the highest score, well above 10.
    # (Was Dal Lightspark, but #305 made a dead canon figure un-seatable as the PC; a living
    # wizard exercises the SAME no-abilities derivation path without tripping the seat guard.)
    c = _seed(tmp_path, monkeypatch)
    res = server.load_canon_character(c.id, "Charming Latham", kind="player", add_to_party=True)
    assert "error" not in res
    assert res["ability_source"] == "derived"
    ch = server._require(c.id).characters[res["id"]]
    vals = {f: getattr(ch.abilities, f) for f in ABK}
    assert not all(v == 10 for v in vals.values()), "must NOT be the flat-10 placeholder"
    assert ch.abilities.intelligence == max(vals.values()) > 10  # Wizard -> INT highest, > 10
    assert ch.abilities.modifier(Ability.INT) > 0  # casts at a real positive modifier now
    # a player seated with real abilities raises no placeholder warning
    assert res["warnings"] == []


def test_explicit_canon_abilities_are_preserved_unchanged(tmp_path, monkeypatch):
    # The Withers-style case: a canon record that DOES carry an `abilities` block must keep it
    # verbatim (a hand-authored sheet wins over derivation). No record ships one today, so inject
    # one via the content loader the tool calls. Use a NAME not already in the roster so the
    # fresh-load path runs (not the `already_present` short-circuit).
    c = _seed(tmp_path, monkeypatch)
    explicit = {"strength": 10, "dexterity": 8, "constitution": 14,
                "intelligence": 16, "wisdom": 20, "charisma": 14}  # Withers' QA-observed sheet
    record = {"name": "The Keeper", "class": "Cleric", "level": "5", "abilities": explicit}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: record)
    res = server.load_canon_character(c.id, "The Keeper", kind="companion", add_to_party=True)
    assert "error" not in res and not res.get("already_present")
    assert res["ability_source"] == "canon"
    ch = server._require(c.id).characters[res["id"]]
    for f, v in explicit.items():
        assert getattr(ch.abilities, f) == v, f  # untouched by derivation/SRD defaults
    assert res["warnings"] == []  # a real sheet -> no placeholder warning


def test_classless_player_keeps_placeholder_and_warns(tmp_path, monkeypatch):
    # A class-LESS canon record can't be sized, so it keeps the flat-10 default (today's
    # behavior). Loaded as the PLAYER, that flat-10 sheet is a real defect (every check at +0),
    # so the additive placeholder WARNING must fire so the behavioral gate / QA can catch it.
    c = _seed(tmp_path, monkeypatch)
    record = {"name": "Nameless Wanderer", "class": ""}  # unknown -> no derivation
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: record)
    res = server.load_canon_character(c.id, "Nameless Wanderer", kind="player", add_to_party=True)
    assert "error" not in res
    assert res["ability_source"] == "placeholder"
    ch = server._require(c.id).characters[res["id"]]
    assert all(getattr(ch.abilities, f) == 10 for f in ABK)  # still flat-10
    assert res["warnings"], "a flat-10 player must surface a placeholder warning"
    assert "PLACEHOLDER" in res["warnings"][0]


def test_classless_noncaster_npc_does_not_warn(tmp_path, monkeypatch):
    # The warning is targeted: a plain class-less NPC (no spells, not the player) legitimately
    # has no derived sheet and must NOT spam a warning.
    c = _seed(tmp_path, monkeypatch)
    record = {"name": "Town Crier", "class": ""}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: record)
    res = server.load_canon_character(c.id, "Town Crier", kind="npc")
    assert "error" not in res
    assert res["ability_source"] == "placeholder"
    assert res["warnings"] == []
