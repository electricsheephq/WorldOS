"""Companion XP-sync (#353): a de-facto companion earns party XP just as it travels.

The travel/relocate path (`_move_party_to`) co-locates the travelling group by KIND —
the PC(s) plus every kind='companion', whether or not the DM remembered to add them to
`c.party` (the Wyll-froze-at-the-checkpoint bug, fixed in test_travel.py). The XP-award
paths used to gate recipients on `c.party` MEMBERSHIP instead, so a de-facto companion
(kind='companion', loaded via load_canon_character(add_to_party=False) or otherwise not
in c.party) walked with the group but EARNED NOTHING — it read as co-located in the
scene yet fell behind on progression (the audit's "award_party_xp does not increment
companion XP" symptom).

This locks the symmetry: the set that travels together earns party XP together. Covers
all three award paths — award_party_xp (tool), _award_kill_xp, _award_milestone_xp.
"""
import pytest

import server
import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    yield


def _pc_and_defacto_companion():
    """A campaign (xp mode) with a PC in the party and a de-facto companion that is
    kind='companion' but NOT in c.party (simulating load_canon_character(add_to_party=False)
    / a never-recruited NPC-loaded companion). Returns (cid, pc, comp)."""
    cid = server.create_campaign("Companion XP Sync")["id"]
    assert server._require(cid).leveling_mode == "xp"
    pc = server.create_character(cid, "Renn", kind="player", max_hp=20)["id"]
    comp = server.create_character(cid, "Cinder", kind="companion", max_hp=16)["id"]
    # Drop the companion from the party array while it stays kind='companion' — the
    # de-facto-companion shape the relocate sweep already handles but the XP paths did not.
    c = store.load_campaign(cid)
    c.party = [p for p in c.party if p != comp]
    store.save_campaign(c)
    assert comp not in store.load_campaign(cid).party  # precondition
    return cid, pc, comp


# ─── award_party_xp (the tool the DM calls directly) ────────────────────────

def test_award_party_xp_includes_defacto_companion():
    cid, pc, comp = _pc_and_defacto_companion()
    out = server.award_party_xp(cid, 200, reason="quest")
    granted = {g["name"]: g["granted"] for g in out["grants"]}
    # Both the PC and the de-facto companion share the award (100 each), not 200-to-the-PC.
    assert granted == {"Renn": 100, "Cinder": 100}
    assert out["split_between"] == 2
    assert server.get_character(cid, comp)["xp"] == 100


def test_award_party_xp_include_companions_false_still_excludes_companion():
    """The opt-out still works: include_companions=False pays only the PC, even though the
    companion is a de-facto party member."""
    cid, pc, comp = _pc_and_defacto_companion()
    out = server.award_party_xp(cid, 200, reason="solo glory", include_companions=False)
    granted = {g["name"]: g["granted"] for g in out["grants"]}
    assert granted == {"Renn": 200}
    assert server.get_character(cid, comp)["xp"] == 0


def test_award_party_xp_does_not_pay_standalone_npc():
    """Broadening to de-facto companions must NOT drag a standalone kind='npc' into the split."""
    cid, pc, comp = _pc_and_defacto_companion()
    npc = server.create_character(cid, "Barkeep", kind="npc", add_to_party=False)["id"]
    out = server.award_party_xp(cid, 200, reason="quest")
    names = {g["name"] for g in out["grants"]}
    assert "Barkeep" not in names
    assert server.get_character(cid, npc)["xp"] == 0


# ─── _award_kill_xp / end_combat backstop ───────────────────────────────────

def test_kill_xp_includes_defacto_companion():
    cid, pc, comp = _pc_and_defacto_companion()
    res = server.spawn_monster(cid, "Goblin Warrior")
    mob_id, mob_xp = res["spawned"][0]["id"], res["xp_each"]
    assert mob_xp > 0
    server.start_combat(cid, [pc, comp, mob_id])
    server.set_hp(cid, mob_id, 0)  # killing blow → kill-time award fires
    server.end_combat(cid, resolution="the goblin falls")
    pc_xp = server.get_character(cid, pc)["xp"]
    comp_xp = server.get_character(cid, comp)["xp"]
    # The de-facto companion earned a share of the kill (not 0); PC + comp split the value.
    assert comp_xp > 0
    assert pc_xp + comp_xp == mob_xp


# ─── session-close XP-parity backstop ───────────────────────────────────────

def test_session_close_parity_tops_up_defacto_companion():
    """The end-session reward backstop levels a 0-XP de-facto companion to the party's XP
    parity (it fought all session but was never in c.party). Mirrors the in-party fixD case."""
    cid, pc, comp = _pc_and_defacto_companion()
    server.start_session(cid)
    # The PC banked XP via the single-target tool; the de-facto companion is still at 0.
    server.award_xp(cid, pc, 300, reason="solo award")
    # Advance the world so the backstop's `advanced` gate is satisfied.
    c = store.load_campaign(cid)
    c.day = 2
    store.save_campaign(c)
    out = server.end_session(cid)
    # The companion was raised to the PC's XP (parity), not left at 0.
    assert server.get_character(cid, comp)["xp"] == 300
    assert any(g["name"] == "Cinder" for g in out.get("grants", []))


# ─── _award_milestone_xp (story/social/exploration top-up) ──────────────────

def test_milestone_xp_includes_defacto_companion():
    cid, pc, comp = _pc_and_defacto_companion()
    c = store.load_campaign(cid)
    res = server._award_milestone_xp(c, 100, reason="quest resolved")
    store.save_campaign(c)
    assert res is not None
    names = {g["name"] for g in res["grants"]}
    assert names == {"Renn", "Cinder"}
    assert server.get_character(cid, comp)["xp"] == 50


# ─── F06-7: mid-run recruit XP backfill (no guaranteed false WARN) ──────────

def test_recruit_companion_backfills_xp_to_party_parity():
    """F06-7 (audit 2026-06-11): recruit_companion co-locates the recruit in the SAME call but
    wrote zero XP — so the moment the party had earned anything, the freshly-recruited companion
    sat co-located at xp=0, which is exactly the `companion_xp_synced_on_award` WARN predicate
    (kind=companion, not dead, location==current, xp==0, pc_xp_max>0). The recruit must be
    backfilled to the party's XP parity at recruit time so a mid-run join isn't a guaranteed
    false positive (and the new ally levels WITH the party)."""
    cid = server.create_campaign("Recruit Backfill")["id"]
    pc = server.create_character(cid, "Renn", kind="player", max_hp=20)["id"]
    server.award_xp(cid, pc, 900, reason="the road so far")  # party has banked XP
    # A roster NPC the party meets mid-run, then recruits.
    npc = server.create_character(cid, "Cinder", kind="npc", add_to_party=False)["id"]
    assert server.get_character(cid, npc)["xp"] == 0

    out = server.recruit_companion(cid, npc, class_name="fighter", level=1)
    assert out["kind"] == "companion"
    # Backfilled to the party's current XP parity, NOT left at 0.
    assert server.get_character(cid, npc)["xp"] == 900


def test_recruit_companion_does_not_lower_a_companion_already_ahead():
    """Backfill only RAISES toward parity — it never lowers a recruit who already carries MORE
    XP than the party (a high-level guest joining a low-level party keeps their earned XP)."""
    cid = server.create_campaign("No Lower")["id"]
    pc = server.create_character(cid, "Renn", kind="player", max_hp=20)["id"]
    server.award_xp(cid, pc, 100, reason="early")
    npc = server.create_character(cid, "Veteran", kind="npc", add_to_party=False)["id"]
    c = store.load_campaign(cid)
    c.characters[npc].xp = 5000  # already a seasoned figure
    store.save_campaign(c)

    server.recruit_companion(cid, npc, class_name="fighter", level=5, max_hp=40)
    assert server.get_character(cid, npc)["xp"] == 5000  # unchanged (never lowered)


def test_recruit_companion_with_no_party_xp_stays_zero():
    """Default/byte-for-byte: recruiting into a party that has earned NOTHING yet leaves the
    recruit at 0 (no XP to backfill from — today's behavior)."""
    cid = server.create_campaign("Zero Party")["id"]
    server.create_character(cid, "Renn", kind="player", max_hp=20)
    npc = server.create_character(cid, "Cinder", kind="npc", add_to_party=False)["id"]
    server.recruit_companion(cid, npc, class_name="fighter", level=1)
    assert server.get_character(cid, npc)["xp"] == 0


def test_recruit_backfill_clears_the_companion_xp_synced_warn():
    """End-to-end: after a backfilled recruit, the behavioral WARN predicate
    (`companion_xp_synced_on_award`) no longer sees a co-located 0-XP companion."""
    cid = server.create_campaign("WARN Clear")["id"]
    pc = server.create_character(cid, "Renn", kind="player", max_hp=20)["id"]
    server.award_xp(cid, pc, 600, reason="progress")
    npc = server.create_character(cid, "Cinder", kind="npc", add_to_party=False)["id"]
    server.recruit_companion(cid, npc, class_name="fighter", level=1)
    c = store.load_campaign(cid)
    comp = c.characters[npc]
    # The recruit is co-located (recruit co-locates) AND now carries XP → not a false 0.
    assert comp.location_id == c.current_location_id
    assert comp.xp > 0
