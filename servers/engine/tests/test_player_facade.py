"""It.1 — the constrained player-move facade enforces the player's lane in CODE.

The player can only attempt what its sheet supports (no casting a spell it doesn't
know, no using an item it doesn't carry, no bogus skill), and every move is a
structured record — never free-text world narration. The engine stays sole writer;
this facade is read-only on state and only appends moves.
"""
import json

import player_server as ps
from models import Character, Item, SpellSlotLevel


def _pc(**kw) -> Character:
    return Character(name="Vesk", kind="player", **kw)


def test_validate_check_rejects_non_skills():
    assert ps.validate_check("stealth")[0] is True
    assert ps.validate_check("Persuasion")[0] is True
    ok, why = ps.validate_check("flossing")
    assert ok is False and "skill" in why


def test_validate_cast_is_scoped_to_the_sheet():
    pc = _pc(spells_known=["Fire Bolt", "Shield"], spell_slots={1: SpellSlotLevel(maximum=2)})
    assert ps.validate_cast(pc, "shield")[0] is True            # known + has a slot (case-insensitive)
    assert ps.validate_cast(pc, "Fireball")[0] is False         # not known
    assert ps.validate_cast(pc, "")[0] is False                 # empty
    assert ps.validate_cast(pc, "fire bolt")[0] is True         # cantrip — no slot needed


def test_validate_cast_requires_an_available_slot():
    # C1: known-ness alone was the hole — a leveled spell needs an ACTUAL slot, or a
    # tapped-out caster could "cast" and the DM would narrate it as real.
    tapped = _pc(spells_known=["Shield", "Fireball"],
                 spell_slots={1: SpellSlotLevel(maximum=2, used=2)})
    ok, why = ps.validate_cast(tapped, "shield")               # L1 spell, no L1+ slot left
    assert ok is False and "slot" in why
    upcast = _pc(spells_known=["Shield"],
                 spell_slots={1: SpellSlotLevel(maximum=1, used=1), 2: SpellSlotLevel(maximum=1)})
    assert ps.validate_cast(upcast, "shield")[0] is True        # a higher slot counts (upcast)
    assert ps.has_slot_for(upcast, 1) is True and ps.has_slot_for(upcast, 3) is False
    # an SRD-unknown spell isn't false-refused on slots (engine degrades gracefully too)
    assert ps.validate_cast(_pc(spells_known=["Eldritch Whatsit"]), "Eldritch Whatsit")[0] is True


def test_validate_attack_requires_target_and_owned_weapon():
    # H2: attack was a free pass — now it needs a real target, and a named weapon must
    # be one you carry (it deliberately does NOT gate on in-combat — attacks start fights).
    pc = _pc(inventory=[Item(name="Rapier")])
    assert ps.validate_attack(pc, "", "")[0] is False           # no target
    assert ps.validate_attack(pc, "the guard", "")[0] is True   # bare attack is fine
    assert ps.validate_attack(pc, "the guard", "greataxe")[0] is False  # weapon not carried
    assert ps.validate_attack(pc, "the guard", "rapier")[0] is True     # weapon carried


def test_validate_item_is_scoped_to_inventory():
    pc = _pc(inventory=[Item(name="Rope"), Item(name="Lockpicks")])
    assert ps.validate_item(pc, "rope")[0] is True
    assert ps.validate_item(pc, "Holy Avenger")[0] is False


def test_say_and_do_append_structured_moves(tmp_path, monkeypatch):
    moves = tmp_path / "moves.jsonl"
    monkeypatch.setenv("CLAWDND_PLAYER_MOVES", str(moves))
    assert ps.say("'the name, and forty gold'")["ok"] is True
    assert ps.do("I put my back to the wall and palm a dagger")["ok"] is True
    rows = [json.loads(x) for x in moves.read_text(encoding="utf-8").splitlines()]
    assert [m["kind"] for m in rows] == ["say", "do"]
    assert all(m["role"] == "player" for m in rows)


def test_cast_and_use_refused_without_a_character(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))  # empty state -> no campaign
    monkeypatch.setenv("CLAWDND_PLAYER_MOVES", str(tmp_path / "m.jsonl"))
    assert ps.cast_spell("Fireball")["ok"] is False
    assert ps.use_item("Rope")["ok"] is False
    # but a pure-narrative move still records (no sheet needed)
    assert ps.say("I wait.")["ok"] is True
