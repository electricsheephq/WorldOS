"""Cross-path character-seat integrity (audit F02-1 + F02-4, issues #788 / #802).

The five seat paths (create_character / start_character fresh+promote / load_canon_character /
recruit_companion) had drifted into asymmetry: each hand-rolled a different subset of the
finishing steps, so a pickup-origin PC seated at flat 10/10/10/10/10/10 (every check/save/DC
at +0 — the May-31 Alfira) while canon-loaded players and recruited companions seated with an
SRD armor AC over an EMPTY inventory and 0 gp (53 wild snapshot records).

These tests guard the shared finisher (`server._finish_seat_sheet`) that every seat path now
routes through:
  * F02-1 — ability backfill: rec `abilities` block -> class+level derived array -> placeholder,
    with initiative reset from the real DEX; explicit args always win; promote repairs a
    flat-10 roster stub but never a hand-fleshed sheet; `ability_source` surfaces the winner.
  * F02-4 — gear+purse seeding on load_canon (player/companion seats) and recruit_companion,
    self-guarded so an authored kit/purse is untouched; lore NPC loads stay gearless.
"""

import pytest

import content
import server
from models import Ability, Item

WORLD = "baldurs-gate"
ABK = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = content.seed_world(content.load_world_data(WORLD))
    server.save_campaign(c)
    return c


def _vals(ch):
    return {f: getattr(ch.abilities, f) for f in ABK}


def _is_flat10(ch):
    return all(v == 10 for v in _vals(ch).values())


def _fake_canon(monkeypatch, rec):
    """Route the content loader at a synthetic canon record (the test-canon pattern
    test_canon_abilities.py established) so the FRESH-BUILD pickup path runs (the name
    matches no seeded roster NPC, so no promote-in-place)."""
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: dict(rec))


# --- F02-1: pickup / veteran / classed-nobody ability backfill ---------------------


def test_pickup_origin_derives_abilities_not_flat10(tmp_path, monkeypatch):
    # THE F02-1 defect: a pickup-origin PC seated all-10s (May-31 Alfira fingerprint).
    c = _seed(tmp_path, monkeypatch)
    _fake_canon(monkeypatch, {"name": "Vess Tallow", "class": "Ranger", "level": "5"})
    res = server.start_character(c.id, origin="pickup:Vess Tallow")
    assert "error" not in res
    assert res["ability_source"] == "derived"
    ch = server._require(c.id).characters[res["id"]]
    assert not _is_flat10(ch), "pickup PC must NOT seat at the flat-10 placeholder"
    # Ranger: DEX primary (15, +2 from the L4 ASI at level 5 -> 17); initiative from real DEX.
    assert ch.abilities.dexterity == max(_vals(ch).values()) > 10
    assert ch.initiative_bonus == ch.abilities.modifier(Ability.DEX) > 0
    # HP computed off the REAL CON (the flat-10 probe seated a L5 ranger at 34).
    assert ch.max_hp > 34
    assert res["warnings"] == []  # a real sheet -> no placeholder warning


def test_pickup_origin_honors_canon_abilities_block(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    explicit = {"strength": 8, "dexterity": 17, "constitution": 13,
                "intelligence": 12, "wisdom": 14, "charisma": 11}
    _fake_canon(monkeypatch, {"name": "Vess Tallow", "class": "Ranger", "level": "3",
                              "abilities": dict(explicit)})
    res = server.start_character(c.id, origin="pickup:Vess Tallow")
    assert "error" not in res
    assert res["ability_source"] == "canon"
    ch = server._require(c.id).characters[res["id"]]
    for f, v in explicit.items():
        assert getattr(ch.abilities, f) == v, f
    assert ch.initiative_bonus == ch.abilities.modifier(Ability.DEX) == 3


def test_pickup_origin_explicit_abilities_arg_wins_over_rec(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    _fake_canon(monkeypatch, {"name": "Vess Tallow", "class": "Ranger", "level": "1",
                              "abilities": {"dexterity": 17}})
    mine = {"strength": 14, "dexterity": 12, "constitution": 15,
            "intelligence": 10, "wisdom": 13, "charisma": 8}
    res = server.start_character(c.id, origin="pickup:Vess Tallow", abilities=dict(mine))
    assert "error" not in res
    assert res["ability_source"] == "explicit"
    ch = server._require(c.id).characters[res["id"]]
    for f, v in mine.items():
        assert getattr(ch.abilities, f) == v, f


def test_pickup_promote_repairs_flat10_roster_stub(tmp_path, monkeypatch):
    # B-MED-1 promote path: the roster stub is flat-10; pickup must repair it (F02-1's
    # promote-when-flat), not keep the placeholder.
    c = _seed(tmp_path, monkeypatch)
    res = server.start_character(c.id, origin="pickup:Minsc")
    assert "error" not in res and res.get("promoted_existing") is True
    assert res["ability_source"] in ("canon", "derived")
    ch = server._require(c.id).characters[res["id"]]
    assert not _is_flat10(ch), "promoted roster stub must be repaired, not left flat-10"
    assert ch.initiative_bonus == ch.abilities.modifier(Ability.DEX)
    assert res["warnings"] == []


def test_pickup_promote_keeps_hand_fleshed_roster_sheet(tmp_path, monkeypatch):
    # A roster record someone already fleshed out (non-flat) must WIN over derivation.
    c = _seed(tmp_path, monkeypatch)
    hand = {"strength": 13, "dexterity": 9, "constitution": 16,
            "intelligence": 11, "wisdom": 15, "charisma": 12}
    server.create_character(c.id, "Vess Tallow", kind="npc", abilities=dict(hand))
    _fake_canon(monkeypatch, {"name": "Vess Tallow", "class": "Ranger", "level": "2"})
    res = server.start_character(c.id, origin="pickup:Vess Tallow")
    assert "error" not in res and res.get("promoted_existing") is True
    assert res["ability_source"] == "explicit"
    ch = server._require(c.id).characters[res["id"]]
    for f, v in hand.items():
        assert getattr(ch.abilities, f) == v, f


def test_veteran_l5_without_abilities_is_not_flat10(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    res = server.start_character(c.id, origin="veteran_l5", name="Vet", class_name="ranger")
    assert "error" not in res
    assert res["ability_source"] == "derived"
    ch = server._require(c.id).characters[res["id"]]
    assert not _is_flat10(ch)
    assert ch.abilities.dexterity == 17  # ranger primary 15 + one ASI (L4)
    assert ch.initiative_bonus == ch.abilities.modifier(Ability.DEX) == 3


def test_classed_nobody_l1_without_abilities_is_not_flat10(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    res = server.start_character(c.id, origin="nobody_l1", name="Greenhorn", class_name="fighter")
    assert "error" not in res
    assert res["ability_source"] == "derived"
    ch = server._require(c.id).characters[res["id"]]
    assert ch.abilities.strength == 15  # fighter primary, no ASI at L1
    assert not _is_flat10(ch)


def test_classless_nobody_keeps_flat10_and_warns(tmp_path, monkeypatch):
    # A class-less nobody is the documented blank sheet — flat-10 stays, but the SAME
    # placeholder warning load_canon emits must surface so QA/the DM SEES the +0 PC.
    c = _seed(tmp_path, monkeypatch)
    res = server.start_character(c.id, origin="nobody_l1", name="Drifter")
    assert "error" not in res
    assert res["ability_source"] == "placeholder"
    ch = server._require(c.id).characters[res["id"]]
    assert _is_flat10(ch)  # unchanged contract for the class-less origin
    assert res["warnings"], "a flat-10 player must surface a placeholder warning"
    assert "PLACEHOLDER" in res["warnings"][0]


# --- F02-4: gear + purse on the load_canon and recruit seat paths -------------------


def test_load_canon_player_seat_seeds_gear_and_purse(tmp_path, monkeypatch):
    # Jun-9 Alfira fingerprint: AC 14 / inventory [] / 0 gp via load_canon.
    c = _seed(tmp_path, monkeypatch)
    rec = {"name": "Sera Quickstep", "class": "Rogue", "level": "3"}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: dict(rec))
    res = server.load_canon_character(c.id, "Sera Quickstep", kind="player", add_to_party=True)
    assert "error" not in res
    ch = server._require(c.id).characters[res["id"]]
    names = [i.name for i in ch.inventory]
    assert "Studded Leather" in names, f"AC-justifying armor missing: {names}"
    assert any(i.name == "Studded Leather" and i.equipped for i in ch.inventory)
    assert ch.currency.gp > 0, "seated player must not start broke (merchant pillar)"


def test_load_canon_companion_seat_seeds_gear_and_purse(tmp_path, monkeypatch):
    c = _seed(tmp_path, monkeypatch)
    rec = {"name": "Brother Hale", "class": "Cleric", "level": "2"}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: dict(rec))
    res = server.load_canon_character(c.id, "Brother Hale", kind="companion", add_to_party=True)
    assert "error" not in res
    ch = server._require(c.id).characters[res["id"]]
    assert any(i.name == "Chain Mail" and i.equipped for i in ch.inventory)
    assert ch.currency.gp > 0


def test_load_canon_lore_npc_stays_gearless(tmp_path, monkeypatch):
    # The seeding is a PARTY-seat affordance — a lore NPC pulled in for an encounter
    # must not pocket a PC starting kit.
    c = _seed(tmp_path, monkeypatch)
    rec = {"name": "Quartermaster Lunt", "class": "Fighter", "level": "4"}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: dict(rec))
    res = server.load_canon_character(c.id, "Quartermaster Lunt", kind="npc")
    assert "error" not in res
    ch = server._require(c.id).characters[res["id"]]
    assert ch.inventory == []
    assert ch.currency.gp == 0


def test_recruit_companion_seeds_gear_and_purse(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Recruit gear")["id"]
    npc = server.create_character(cid, "Bram", kind="npc")["id"]
    server.recruit_companion(cid, npc, class_name="Fighter")
    ch = server._require(cid).characters[npc]
    assert any(i.name == "Chain Mail" and i.equipped for i in ch.inventory)
    assert any(i.name == "Longsword" for i in ch.inventory)
    assert ch.currency.gp > 0


def test_recruit_companion_respects_authored_kit_and_purse(tmp_path, monkeypatch):
    # The seeder self-guards: an authored inventory AND a non-zero purse win verbatim.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Recruit guard")["id"]
    npc = server.create_character(cid, "Maeve", kind="npc")["id"]
    c = server._require(cid)
    c.characters[npc].inventory = [Item(name="Heirloom Blade", equipped=True)]
    c.characters[npc].currency.gp = 3
    server.save_campaign(c)
    server.recruit_companion(cid, npc, class_name="Fighter")
    ch = server._require(cid).characters[npc]
    assert [i.name for i in ch.inventory] == ["Heirloom Blade"]
    assert ch.currency.gp == 3


def test_recruit_companion_backfills_flat10_stub_abilities(tmp_path, monkeypatch):
    # Routing recruit through the SAME shared finisher means a bare roster stub gains a
    # class-appropriate array (and HP computed off real CON), not a +0-everything sheet.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Recruit backfill")["id"]
    npc = server.create_character(cid, "Bram", kind="npc")["id"]
    server.recruit_companion(cid, npc, class_name="Fighter")
    ch = server._require(cid).characters[npc]
    assert not _is_flat10(ch)
    assert ch.abilities.strength == 15
    assert ch.initiative_bonus == ch.abilities.modifier(Ability.DEX)


def test_recruit_companion_explicit_abilities_still_win(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Recruit explicit")["id"]
    npc = server.create_character(cid, "Bram", kind="npc")["id"]
    server.recruit_companion(cid, npc, class_name="Fighter",
                             abilities={"strength": 18, "constitution": 14})
    ch = server._require(cid).characters[npc]
    assert ch.abilities.strength == 18 and ch.abilities.constitution == 14


# --- the cross-path net: every classed party seat is armed, funded, and statted -----


def test_all_party_seat_paths_yield_playable_funded_sheets(tmp_path, monkeypatch):
    """Census-lite (the F02-18 spirit on the paths this fix-set touches): every classed
    player/companion seat ends non-flat with a non-empty pack and a non-zero purse."""
    c = _seed(tmp_path, monkeypatch)
    cid = c.id
    seated = []
    seated.append(server.create_character(
        cid, "Crea", kind="companion", class_name="rogue", apply_srd_defaults=True,
        abilities={"dexterity": 16, "constitution": 12})["id"])
    seated.append(server.start_character(cid, name="Star", class_name="fighter")["id"])
    rec = {"name": "Canon Cleric", "class": "Cleric", "level": "2"}
    monkeypatch.setattr(server.content_mod, "load_canon_character", lambda world_id, name: dict(rec))
    seated.append(server.load_canon_character(cid, "Canon Cleric", kind="companion",
                                              add_to_party=True)["id"])
    npc = server.create_character(cid, "Rook", kind="npc")["id"]
    server.recruit_companion(cid, npc, class_name="Ranger")
    seated.append(npc)
    live = server._require(cid)
    for sid in seated:
        ch = live.characters[sid]
        assert not _is_flat10(ch), f"{ch.name} seated flat-10"
        assert ch.inventory, f"{ch.name} seated with an empty pack"
        assert ch.currency.gp > 0, f"{ch.name} seated broke"
        assert ch.initiative_bonus == ch.abilities.modifier(Ability.DEX), ch.name
