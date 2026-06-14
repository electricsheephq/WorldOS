"""S6 — "re-roll and continue" death mechanic.

D&D has no save states: when the PC dies you do not rewind — you re-roll a NEW
character at the same level and continue the quest; on a party wipe everyone
re-rolls and the world carries on. The engine never resurrects the dead (that
one-way rule is unchanged); re-roll is purely *forward* motion.

These tests pin the contract:
  * `reroll_character` builds the new PC at the dead PC's level, kind=="player", in
    party; demotes the corpse off kind=="player" (-> npc), out of party, record kept;
  * the player FACADE (`player_server._pc()`) now resolves the NEW pc, not the corpse
    (the keystone — the facade picks the active PC by kind=="player", no dead-filter);
  * gear/gold are NOT transferred (lost with the body);
  * a non-dead target is refused;
  * `get_state` exposes `dead`/`stable` per party member (F1) and `party_down` flips
    true on a full wipe (F2);
  * a campaign with NO death is unchanged except the new always-present keys (additive).
"""

import pytest

import player_server as ps
import server
import store
from models import Ability, Item


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    # The facade must use its DEFAULT path (first kind=="player" in party) — clear any
    # bound-actor env a prior test may have set so _pc() resolves by kind, not id.
    monkeypatch.delenv("CLAWDND_ACTOR_ID", raising=False)
    monkeypatch.delenv("CLAWDND_ACTOR_ROLE", raising=False)
    yield


def _party_at_level_3(cid: str) -> tuple[str, str]:
    """A level-3 fighter PC (with gear + gold) and a cleric companion. Returns (pc, comp)."""
    pc = server.create_character(
        cid, "Aldric", kind="player", class_name="fighter", level=3,
        apply_srd_defaults=True, abilities={"strength": 16, "constitution": 14},
    )["id"]
    # Give the PC distinctive gear + gold so we can assert it is NOT inherited by the re-roll.
    c = store.load_campaign(cid)
    c.characters[pc].inventory = [Item(name="Flametongue Greatsword"), Item(name="Healing Potion")]
    c.characters[pc].currency.gp = 250
    store.save_campaign(c)
    comp = server.create_character(
        cid, "Brenna", kind="companion", class_name="cleric", level=3, apply_srd_defaults=True,
    )["id"]
    return pc, comp


def _kill(cid: str, char_id: str) -> None:
    """Drop a character to dead via massive damage (overkill >= max_hp = instant death)."""
    server.apply_damage(cid, char_id, 9999)
    assert store.load_campaign(cid).characters[char_id].dead is True


# --- the core swap ----------------------------------------------------------------

def test_reroll_builds_new_pc_at_dead_pcs_level_in_party():
    cid = server.create_campaign("S6 reroll")["id"]
    pc, _comp = _party_at_level_3(cid)
    dead_level = store.load_campaign(cid).characters[pc].total_level
    assert dead_level == 3
    _kill(cid, pc)

    out = server.reroll_character(
        cid, pc, name="Wren", class_name="rogue",
        abilities={"dexterity": 16, "constitution": 12},
    )

    c = store.load_campaign(cid)
    new_id = out["new_pc"]["id"]
    new = c.characters[new_id]
    # The new hero is a player, at the FALLEN hero's level, in the party.
    assert new.kind == "player"
    assert new.total_level == dead_level == 3
    assert new.name == "Wren"
    assert new_id in c.party
    assert out["new_pc"]["level"] == 3
    assert out["new_pc"]["in_party"] is True
    # SRD sheet was applied at level 3 (proficiency bonus 2, real HP, rogue Sneak Attack).
    assert new.proficiency_bonus == 2
    assert new.max_hp > 1
    assert new.sneak_attack_dice  # rogue feature granted through level 3


def test_reroll_level_override():
    cid = server.create_campaign("S6 override")["id"]
    pc, _comp = _party_at_level_3(cid)
    _kill(cid, pc)
    out = server.reroll_character(cid, pc, name="Kestrel", class_name="wizard", level=5)
    assert out["new_pc"]["level"] == 5
    assert store.load_campaign(cid).characters[out["new_pc"]["id"]].total_level == 5


def test_reroll_demotes_corpse_off_player_and_out_of_party_record_kept():
    cid = server.create_campaign("S6 memorial")["id"]
    pc, _comp = _party_at_level_3(cid)
    _kill(cid, pc)
    out = server.reroll_character(cid, pc, name="Wren", class_name="rogue")

    c = store.load_campaign(cid)
    # The corpse is demoted off kind=="player" (a memorial npc), removed from the party,
    # but the record PERSISTS — its story/death is remembered by the world.
    corpse = c.characters[pc]
    assert corpse.kind == "npc"
    assert corpse.dead is True  # still dead — death is one-way, never undone
    assert pc not in c.party
    assert pc in c.characters  # never hard-deleted
    assert out["memorial"] == {"id": pc, "name": "Aldric", "now_kind": "npc"}


def test_reroll_does_not_transfer_gear_or_gold():
    cid = server.create_campaign("S6 loot")["id"]
    pc, _comp = _party_at_level_3(cid)
    _kill(cid, pc)
    out = server.reroll_character(cid, pc, name="Wren", class_name="rogue")

    c = store.load_campaign(cid)
    new = c.characters[out["new_pc"]["id"]]
    # The DEAD PC's gear/gold is LOST with the body — never TRANSFERRED to the new hero.
    # (F02-12: the new character is seated with their OWN class-appropriate starting kit so
    # their AC is backed by armor on the sheet, but NONE of the corpse's distinctive loot.)
    assert not any(i.name == "Flametongue Greatsword" for i in new.inventory)
    assert not any(i.name == "Healing Potion" for i in new.inventory)
    assert new.currency.gp != 250  # not the corpse's purse
    # The corpse keeps what it died with (the fiction hook for a lootable body).
    corpse = c.characters[pc]
    assert any(i.name == "Flametongue Greatsword" for i in corpse.inventory)
    assert corpse.currency.gp == 250


# --- THE KEYSTONE: the player facade resolves the NEW pc, not the corpse ----------

def test_facade_resolves_new_pc_after_reroll():
    cid = server.create_campaign("S6 facade")["id"]
    pc, _comp = _party_at_level_3(cid)
    # Before death, the facade's default path resolves the original PC.
    assert ps._pc() is not None and ps._pc().id == pc
    _kill(cid, pc)
    out = server.reroll_character(cid, pc, name="Wren", class_name="rogue")
    new_id = out["new_pc"]["id"]

    # KEYSTONE: the facade now hands moves to the NEW pc — never the corpse. (The default
    # path picks the first kind=="player" in party order with no dead-filter, so this only
    # works because the re-roll demoted the corpse off kind=="player".)
    resolved = ps._pc()
    assert resolved is not None
    assert resolved.id == new_id
    assert resolved.id != pc
    assert resolved.name == "Wren"


# --- refusals ---------------------------------------------------------------------

def test_reroll_refuses_a_living_target():
    cid = server.create_campaign("S6 refuse-living")["id"]
    pc, _comp = _party_at_level_3(cid)
    # PC is alive — re-roll is for the dead, not a live swap.
    with pytest.raises(ValueError, match="not dead"):
        server.reroll_character(cid, pc, name="Wren", class_name="rogue")
    # Nothing changed: the living PC is still the player, still in party.
    c = store.load_campaign(cid)
    assert c.characters[pc].kind == "player"
    assert pc in c.party


def test_reroll_refuses_a_dead_monster():
    cid = server.create_campaign("S6 refuse-monster")["id"]
    _pc, _comp = _party_at_level_3(cid)
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=7, armor_class=15)["id"]
    _kill(cid, gob)  # a monster dies outright at 0 HP
    with pytest.raises(ValueError, match="only a fallen player or companion"):
        server.reroll_character(cid, gob, name="Wren", class_name="rogue")


def test_reroll_refuses_unknown_id():
    cid = server.create_campaign("S6 refuse-missing")["id"]
    _party_at_level_3(cid)
    with pytest.raises(ValueError, match="no character"):
        server.reroll_character(cid, "char-nope", name="Wren", class_name="rogue")


# --- a fallen companion can be re-rolled too --------------------------------------

def test_reroll_a_dead_companion():
    cid = server.create_campaign("S6 dead-comp")["id"]
    _pc, comp = _party_at_level_3(cid)
    _kill(cid, comp)
    out = server.reroll_character(cid, comp, name="Sage", class_name="bard")
    c = store.load_campaign(cid)
    # The re-rolled replacement is a player (the new playable hero); the fallen companion
    # is demoted to a memorial npc and dropped from the party.
    assert c.characters[out["new_pc"]["id"]].kind == "player"
    assert c.characters[comp].kind == "npc"
    assert comp not in c.party


# --- F1: get_state exposes death per party member ---------------------------------

def test_get_state_exposes_dead_and_stable_per_party_member():
    cid = server.create_campaign("S6 f1")["id"]
    pc, comp = _party_at_level_3(cid)
    # Healthy party: every entry carries dead=False/stable=False (always present, additive).
    for entry in server.get_state(cid)["party"]:
        assert entry["dead"] is False
        assert entry["stable"] is False
    # Kill the PC; the entry now reads dead=True so the DM sees the re-roll trigger.
    _kill(cid, pc)
    by_id = {e["id"]: e for e in server.get_state(cid)["party"]}
    assert by_id[pc]["dead"] is True
    assert by_id[comp]["dead"] is False


# --- F2: party_down flips true on a full wipe -------------------------------------

def test_party_down_flips_true_on_full_wipe():
    cid = server.create_campaign("S6 f2")["id"]
    pc, comp = _party_at_level_3(cid)
    # A live party is not down.
    assert server.get_state(cid)["party_down"] is False
    # One down, one up -> still not a wipe.
    _kill(cid, pc)
    assert server.get_state(cid)["party_down"] is False
    # Everyone down -> TPK signal fires.
    _kill(cid, comp)
    assert server.get_state(cid)["party_down"] is True


def test_party_down_counts_a_bleeding_out_ally_as_down():
    cid = server.create_campaign("S6 f2-dying")["id"]
    pc, comp = _party_at_level_3(cid)
    # Drop the companion to exactly 0 HP from full -> dying (not dead, not stable).
    comp_max = store.load_campaign(cid).characters[comp].max_hp
    server.apply_damage(cid, comp, comp_max)
    assert store.load_campaign(cid).characters[comp].dead is False  # dying, still saveable
    # Kill the PC. Now: PC dead + companion bleeding out = the whole party is down.
    _kill(cid, pc)
    assert server.get_state(cid)["party_down"] is True


def test_party_down_false_when_an_ally_is_merely_stabilized():
    cid = server.create_campaign("S6 f2-stable")["id"]
    pc, comp = _party_at_level_3(cid)
    # Companion is downed but STABILIZED (0 HP, not dead, not dying) -> still "up" for the
    # wipe check: a stabilized ally can be revived, so this is not a TPK.
    c = store.load_campaign(cid)
    c.characters[comp].current_hp = 0
    c.characters[comp].stable = True
    store.save_campaign(c)
    _kill(cid, pc)
    assert server.get_state(cid)["party_down"] is False


# --- additive: a no-death campaign is unchanged except the new always-present keys --

def test_get_state_additive_no_death_campaign():
    cid = server.create_campaign("S6 additive")["id"]
    pc, comp = _party_at_level_3(cid)
    state = server.get_state(cid)
    # The new top-level key is present and quiet (no wipe).
    assert state["party_down"] is False
    # Every party entry carries exactly the prior keys PLUS the two new death keys —
    # nothing else changed in the entry shape.
    expected = {"id", "name", "kind", "hp", "ac", "conditions", "voice_id", "dead", "stable"}
    for entry in state["party"]:
        # `resources` may also appear for a class with resource pools; allow it but require
        # the core additive keys to be exactly the new superset of the old shape.
        assert expected <= set(entry.keys())
        assert set(entry.keys()) - expected <= {"resources", "dying", "death_saves"}
