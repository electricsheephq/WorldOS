"""Bestiary intel-tier write-sites (#263).

The party earns bestiary intel per creature TYPE, recorded on Campaign.bestiary_intel
(creature_slug -> max tier) by the engine (the sole writer):

  tier 1 (sighted)  — at spawn_monster + the wandering-encounter spawn.
  tier 2 (engaged)  — at start_combat (per monster combatant).
  tier 3 (slain)    — at _award_kill_xp (the kill-time hook), with the end_combat backstop.

These tests pin: each bump fires at its site; the tier is monotonic (never regresses); a
non-bestiary monster (no creature_slug) is a no-op; tier 3 is recorded regardless of leveling
mode and through every death path; and old snapshots (no bestiary_intel key) round-trip.
"""
import json

import pytest

import bestiary
import server
from models import Campaign, Character


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    yield


def _setup(leveling_mode: str = "xp"):
    """One campaign + one living PC in the party."""
    cid = server.create_campaign("Intel Test")["id"]
    if leveling_mode != "xp":
        c = server._require(cid)
        c.leveling_mode = leveling_mode
        server.save_campaign(c)
    pc_id = server.create_character(cid, "Hero", kind="player", max_hp=30)["id"]
    return cid, pc_id


# ─── tier 1: spawn ──────────────────────────────────────────────────────────

def test_spawn_records_tier1_and_sets_creature_slug():
    cid, _ = _setup()
    res = server.spawn_monster(cid, "Goblin Warrior", count=2)
    c = server._require(cid)
    assert c.bestiary_intel == {"goblin-warrior": 1}
    # every spawned instance carries the canonical slug as the join key
    for s in res["spawned"]:
        assert c.characters[s["id"]].creature_slug == "goblin-warrior"


def test_wandering_spawn_records_tier1():
    """The internal wandering-encounter spawn path bumps tier 1 too (party sees it erupt)."""
    cid, _ = _setup()
    c = server._require(cid)
    with server.campaign_lock(cid):
        spawned = server._spawn_creature_chars(c, "Wolf", 3, None)
        server.save_campaign(c)
    assert spawned and server._require(cid).bestiary_intel == {"wolf": 1}


# ─── tier 2: start_combat ─────────────────────────────────────────────────────

def test_start_combat_records_tier2_for_monsters_only():
    cid, pc_id = _setup()
    mob = server.spawn_monster(cid, "Wolf")["spawned"][0]["id"]
    assert server._require(cid).bestiary_intel == {"wolf": 1}
    server.start_combat(cid, [pc_id, mob])
    intel = server._require(cid).bestiary_intel
    # bumped to 2 for the wolf; the PC (no creature_slug) contributes nothing
    assert intel == {"wolf": 2}


# ─── tier 3: kill (+ backstop, + monotonic max) ───────────────────────────────

def test_kill_records_tier3_via_apply_damage():
    cid, pc_id = _setup()
    mob = server.spawn_monster(cid, "Goblin Warrior")["spawned"][0]["id"]
    server.start_combat(cid, [pc_id, mob])
    assert server._require(cid).bestiary_intel == {"goblin-warrior": 2}
    server.apply_damage(cid, mob, 9999)
    assert server._require(cid).bestiary_intel == {"goblin-warrior": 3}
    # end_combat backstop keeps it at 3 (never lowers / no spurious change)
    server.end_combat(cid)
    assert server._require(cid).bestiary_intel == {"goblin-warrior": 3}


def test_kill_via_set_hp_records_tier3():
    """The set_hp death path also flows through _award_kill_xp -> tier 3."""
    cid, pc_id = _setup()
    mob = server.spawn_monster(cid, "Wolf")["spawned"][0]["id"]
    server.set_hp(cid, mob, 0)
    assert server._require(cid).bestiary_intel.get("wolf") == 3


def test_end_combat_backstop_records_tier3_when_kill_time_missed():
    """A monster that dies OUTSIDE the kill-time hooks is still recorded at tier 3 by the
    end_combat backstop sweep (it re-runs _award_kill_xp over the order)."""
    cid, pc_id = _setup()
    mob = server.spawn_monster(cid, "Wolf")["spawned"][0]["id"]
    server.start_combat(cid, [pc_id, mob])
    # Mutate the monster to dead directly (simulating a death path not wired to the hook),
    # bypassing apply_damage's kill-time award.
    c = server._require(cid)
    c.characters[mob].current_hp = 0
    c.characters[mob].dead = True
    server.save_campaign(c)
    server.end_combat(cid)
    assert server._require(cid).bestiary_intel.get("wolf") == 3


def test_tier3_recorded_in_milestone_mode():
    """Recording intel is independent of leveling mode — a milestone-mode campaign (where
    _award_kill_xp awards no XP) STILL records the kill at tier 3."""
    cid, pc_id = _setup(leveling_mode="milestone")
    mob = server.spawn_monster(cid, "Wolf")["spawned"][0]["id"]
    server.apply_damage(cid, mob, 9999)
    assert server._require(cid).bestiary_intel.get("wolf") == 3


def test_intel_is_monotonic_max():
    """A higher tier already earned is never lowered by a later, lower-tier event."""
    cid, pc_id = _setup()
    mob = server.spawn_monster(cid, "Wolf")["spawned"][0]["id"]
    server.start_combat(cid, [pc_id, mob])
    server.apply_damage(cid, mob, 9999)
    server.end_combat(cid)
    assert server._require(cid).bestiary_intel["wolf"] == 3
    # spawning the same type again (tier 1) must NOT regress the earned tier 3
    server.spawn_monster(cid, "Wolf")
    assert server._require(cid).bestiary_intel["wolf"] == 3


# ─── no-op + round-trip guards ────────────────────────────────────────────────

def test_non_bestiary_monster_is_noop():
    """A hand-built monster Character with no creature_slug never touches bestiary_intel."""
    cid, pc_id = _setup()
    res = server.create_character(cid, "Homebrew Horror", kind="monster", max_hp=10)
    mob = res["id"]
    assert server._require(cid).characters[mob].creature_slug == ""
    server.start_combat(cid, [pc_id, mob])
    server.apply_damage(cid, mob, 9999)
    server.end_combat(cid)
    assert server._require(cid).bestiary_intel == {}  # no slug -> no intel recorded


def test_bump_intel_helper_skips_empty_slug_and_takes_max():
    c = Campaign(title="t")
    server._bump_intel(c, "", 3)         # empty slug -> no-op
    assert c.bestiary_intel == {}
    server._bump_intel(c, "wolf", 1)
    server._bump_intel(c, "wolf", 3)
    server._bump_intel(c, "wolf", 2)     # lower than current -> ignored
    assert c.bestiary_intel == {"wolf": 3}


def test_old_snapshot_without_intel_round_trips():
    """A Campaign / Character lacking the new fields deserializes with the additive defaults."""
    c = Campaign(title="legacy")
    assert c.bestiary_intel == {}                # default empty
    ch = Character(name="legacy mob", kind="monster")
    assert ch.creature_slug == ""                # default empty
    # round-trip through json (mirrors snapshot load) keeps the defaults
    c2 = Campaign.model_validate(c.model_dump(mode="json"))
    assert c2.bestiary_intel == {}


def test_player_bestiary_projection_reveals_earned_tier():
    """End-to-end: the campaign-scoped projection (engine side) reveals exactly the earned tier
    for an encountered creature and gates the rest."""
    cid, pc_id = _setup()
    mob = server.spawn_monster(cid, "Wolf")["spawned"][0]["id"]
    server.start_combat(cid, [pc_id, mob])  # -> tier 2
    intel = dict(server._require(cid).bestiary_intel)
    out = bestiary.player_bestiary("wolf", 20, intel=intel)
    wolf = next(i for i in out["items"] if i.get("name") == "Wolf")
    assert wolf["tier"] == 2 and "ac" in wolf and "speed" in wolf
    assert "hp" not in wolf and "saves" not in wolf  # kill-tier still gated


def test_unencountered_creature_is_redacted_rumour_row():
    """#263 redaction hygiene: an unencountered (tier-0) match is a rumour row that carries NO
    real creature name on the wire — only an opaque, stable render key (``id_hint``). The name
    is the very thing progressive reveal withholds, so it must not ship even though the viewer
    only renders "?????". (Contrast the tier>=1 path above, which DOES carry the earned name.)"""
    # intel records only the wolf, so a "goblin" browse returns nothing but blurred tier-0 rows.
    out = bestiary.player_bestiary("goblin", 20, intel={"wolf": 1})
    items = out["items"]
    assert items, "expected goblin matches in the SRD"
    for item in items:
        assert item.get("tier") == 0 and item.get("unknown") is True
        assert "name" not in item          # the leak being closed (#263)
        assert "id_hint" in item           # a stable, name-free render key remains
        # nothing identifying rides along on a rumour row — not the name, not stats
        for leaked in ("name", "ac", "hp", "cr", "size", "type", "abilities", "saves"):
            assert leaked not in item
    # belt-and-suspenders: no goblin creature name appears anywhere in the rumour payload
    assert "goblin" not in json.dumps(items).lower()
