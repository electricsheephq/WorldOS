"""Regressions for bugs surfaced by the autonomous full-plugin playtest (qa/).

The self-play QA run (qa/transcripts/play1) and an independent scorer both flagged:
  - finding 1: melee hits vs an unconscious/paralyzed target should auto-crit;
  - finding 2: damage >= HP max to a creature already at 0 HP is instant death;
  - finding 3: the starter adventure must seed a companion into the party.
"""

import json
from pathlib import Path

import combat
import companion
import content
import server
from models import Character, Combat, Combatant, Condition

_ADV = (
    Path(__file__).resolve().parents[3]
    / "content" / "campaigns" / "cellar-rats" / "adventure.json"
)


def _downed(max_hp: int = 7) -> Character:
    """A player character at 0 HP and unstable (dying). Death-save accrual is a
    player/companion mechanic — monsters die outright at 0 (see the monster tests)."""
    return Character(name="Hero", kind="player", max_hp=max_hp, current_hp=0)


# --- finding 2: instant death when damage >= HP max while already at 0 ------


def test_massive_damage_to_downed_creature_is_instant_death():
    ch = _downed(max_hp=7)
    out = combat.apply_damage(ch, 8)  # 8 >= max_hp 7 while at 0 HP
    assert ch.dead is True and out["dead"] is True


def test_small_hit_to_downed_creature_adds_failure_not_death():
    ch = _downed(max_hp=12)
    combat.apply_damage(ch, 3)  # 3 < 12 -> a death-save failure, not death
    assert ch.dead is False and ch.death_saves.failures == 1


def test_crit_hit_to_downed_creature_adds_two_failures():
    ch = _downed(max_hp=12)
    combat.apply_damage(ch, 3, crit=True)
    assert ch.dead is False and ch.death_saves.failures == 2


def test_massive_damage_from_full_is_instant_death_regression():
    ch = Character(name="Goblin", kind="monster", max_hp=7, current_hp=7)
    combat.apply_damage(ch, 20)  # overkill 13 >= max_hp 7
    assert ch.dead is True


# --- finding 1: melee auto-crit vs a helpless target ------------------------


def test_melee_auto_crit_vs_unconscious():
    t = Character(name="T", max_hp=10, current_hp=0, conditions=[Condition.UNCONSCIOUS])
    assert combat.melee_auto_crit(t, is_ranged=False) is True
    assert combat.melee_auto_crit(t, is_ranged=True) is False  # ranged: not within 5 ft


def test_melee_auto_crit_vs_paralyzed():
    t = Character(name="T", max_hp=10, current_hp=10, conditions=[Condition.PARALYZED])
    assert combat.melee_auto_crit(t) is True


def test_no_auto_crit_vs_healthy_target():
    t = Character(name="T", max_hp=10, current_hp=10)
    assert combat.melee_auto_crit(t) is False


# --- finding 3: companion seeded into the party -----------------------------


def test_companion_seeded_into_party_synthetic():
    adv = {
        "title": "T",
        "companions": [
            {
                "name": "Sidekick",
                "classes": [{"name": "Cleric", "level": 1}],
                "max_hp": 10,
                "armor_class": 15,
                "voice_id": "companion-default",
                "spell_slots": {"1": {"maximum": 2, "used": 0}},
            }
        ],
    }
    c = content.seed_campaign(adv)
    assert len(c.party) == 1
    comp = c.characters[c.party[0]]
    assert comp.kind == "companion" and comp.name == "Sidekick"
    assert comp.current_hp == comp.max_hp == 10  # joins at full health
    assert comp.spell_slots[1].maximum == 2  # int-keyed slot coerced from JSON


def test_cellar_rats_ships_a_companion_in_party():
    c = content.seed_campaign(json.loads(_ADV.read_text(encoding="utf-8")))
    comps = [c.characters[i] for i in c.party if c.characters[i].kind == "companion"]
    assert len(comps) == 1 and comps[0].name == "Vesper"
    assert comps[0].current_hp == comps[0].max_hp  # full health at start


# --- iteration 2: monsters/NPCs die at 0; PCs/companions still get death saves


def test_monster_dies_instantly_at_zero_hp():
    m = Character(name="Goblin", kind="monster", max_hp=7, current_hp=7)
    out = combat.apply_damage(m, 7)  # exactly lethal (not massive) -> still dead
    assert m.dead is True and out["dead"] is True and out["dying"] is False


def test_npc_dies_instantly_at_zero_hp():
    n = Character(name="Thug", kind="npc", max_hp=11, current_hp=5)
    combat.apply_damage(n, 5)
    assert n.dead is True


def test_player_still_gets_death_saves_at_zero():
    p = Character(name="Hero", kind="player", max_hp=12, current_hp=4)
    out = combat.apply_damage(p, 4)  # to 0, not massive -> dying, not dead
    assert p.dead is False and out["dying"] is True


def test_companion_still_gets_death_saves_at_zero():
    comp = Character(name="Ally", kind="companion", max_hp=10, current_hp=3)
    out = combat.apply_damage(comp, 3)
    assert comp.dead is False and out["dying"] is True


# --- iteration 2: party-XP split -------------------------------------------


def test_award_party_xp_splits_evenly(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]  # seeds Vesper
    server.create_character(cid, "Hero", kind="player", max_hp=10)
    out = server.award_party_xp(cid, 150, reason="cleared the cellar")
    assert out["split_between"] == 2  # Vesper + Hero
    assert all(g["granted"] == 75 for g in out["grants"])
    assert sum(g["granted"] for g in out["grants"]) == 150


# --- iteration 3: off-turn attack warning ----------------------------------


def test_attack_flags_off_turn_action(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    ids = [
        server.create_character(cid, n, kind=k, max_hp=10, armor_class=30)["id"]
        for n, k in (("A", "player"), ("B", "player"), ("Goblin", "monster"))
    ]
    server.start_combat(cid, ids)
    cur = server.get_state(cid)["current_turn"]
    off = next(x for x in ids if x != cur)
    tgt_off = next(x for x in ids if x != off)
    assert "off_turn_warning" in server.attack(cid, off, tgt_off, attack_bonus=0, damage_dice="1d4")
    tgt_cur = next(x for x in ids if x != cur)
    assert "off_turn_warning" not in server.attack(cid, cur, tgt_cur, attack_bonus=0, damage_dice="1d4")


# --- iteration 4: class baseline AC + companion heal triage ----------------


def test_apply_srd_defaults_sets_class_ac(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    fid = server.create_character(
        cid, "Mira", kind="player", class_name="Fighter",
        apply_srd_defaults=True, abilities={"constitution": 14},
    )["id"]
    assert server.get_character(cid, fid)["armor_class"] == 16  # chain-mail baseline


def test_apply_srd_defaults_respects_explicit_ac(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    fid = server.create_character(
        cid, "Mage", kind="player", class_name="Wizard",
        armor_class=15, apply_srd_defaults=True,
    )["id"]
    assert server.get_character(cid, fid)["armor_class"] == 15  # explicit AC kept


def _heal_scene(slots_used: int):
    healer = Character(
        name="Vesper", kind="companion", max_hp=10, current_hp=10,
        spells_prepared=["Healing Word"],
        spell_slots={1: {"maximum": 2, "used": slots_used}},
    )
    hurt = Character(name="Hero", kind="player", max_hp=12, current_hp=1)  # 8% max
    gob = Character(name="Goblin", kind="monster", max_hp=7, current_hp=7)
    chars = {healer.id: healer, hurt.id: hurt, gob.id: gob}
    cbt = Combat(
        active=True, round=1, turn_index=0,
        order=[Combatant(character_id=i) for i in (healer.id, hurt.id, gob.id)],
    )
    return healer, hurt, gob, chars, cbt


def test_companion_heals_critically_wounded_ally():
    healer, hurt, _gob, chars, cbt = _heal_scene(slots_used=0)
    out = companion.suggest_action(healer, cbt, chars)
    assert out["action"] == "heal" and out["target_id"] == hurt.id


def test_companion_attacks_when_no_heal_available():
    healer, _hurt, gob, chars, cbt = _heal_scene(slots_used=2)  # slots exhausted
    out = companion.suggest_action(healer, cbt, chars)
    assert out["action"] == "attack" and out["target_id"] == gob.id


def test_heal_suggestion_names_the_concrete_spell():
    healer, _hurt, _gob, chars, cbt = _heal_scene(slots_used=0)
    out = companion.suggest_action(healer, cbt, chars)
    assert out["action"] == "heal" and out["spell"] == "Healing Word"


def test_aid_downed_suggestion_names_the_concrete_spell():
    healer, hurt, _gob, chars, cbt = _heal_scene(slots_used=0)
    hurt.current_hp = 0  # a downed ally -> aid_downed, with the revive spell named
    out = companion.suggest_action(healer, cbt, chars)
    assert out["action"] == "aid_downed" and out["target_id"] == hurt.id
    assert out["spell"] == "Healing Word"


def test_aid_downed_without_slots_recommends_stabilize_not_a_heal():
    # Embergloom-QA fix: don't tell the DM to cast a heal with no slots left.
    healer, hurt, _gob, chars, cbt = _heal_scene(slots_used=2)  # slots exhausted
    hurt.current_hp = 0
    out = companion.suggest_action(healer, cbt, chars)
    assert out["action"] == "aid_downed" and out["spell"] is None
    assert "stabilize" in out["reason"].lower()


def test_bonus_action_heal_suggests_followup_attack():
    # Healing Word is a bonus action -> the companion's action is still free.
    healer, _hurt, gob, chars, cbt = _heal_scene(slots_used=0)
    out = companion.suggest_action(healer, cbt, chars)
    assert out["action"] == "heal" and out["bonus_action"] is True
    assert out["then_attack_target_id"] == gob.id


# --- story-QA (story1): guard against re-creating an adventure-seeded companion
# The story-first playtest created a second "Brother Toll" via create_character
# even though start_adventure had already seeded companion-toll -> two Tolls in
# the party (one with blank personality). The engine must reject the duplicate.


def test_create_character_rejects_duplicate_companion(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("embergloom-pact")["campaign_id"]  # seeds Brother Toll
    before = len([i for i in server.get_state(cid)["party"]])
    with pytest.raises(ValueError, match="already exists"):
        server.create_character(cid, "Brother Toll", kind="companion", class_name="Cleric")
    # name match is case/space-insensitive
    with pytest.raises(ValueError, match="already exists"):
        server.create_character(cid, "  brother toll ", kind="companion")
    assert len(server.get_state(cid)["party"]) == before  # no duplicate added


# --- generative QA: live world-building (add_location) ---
# The live-GENERATED playtest scored 4.1-4.2 story-craft (above the authored
# benchmark) but flagged its #1 gap: no way to persist a location during play —
# look_around returned location:null all session; the world lived only in prose.


def test_add_location_persists_world_for_live_play(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Generated World")["id"]
    # the first location becomes current (get_state.location was null before)
    a = server.add_location(cid, "Ashenveil", "an ash-choked village")
    assert a["is_current"] and a["location_count"] == 1
    assert server.get_state(cid)["location"]["name"] == "Ashenveil"
    # a connected location is reachable BOTH ways (bidirectional wiring)
    b = server.add_location(cid, "The Silent Mill", "a stopped wheel", connections=[a["id"]])
    assert a["id"] in b["connections"]
    assert server.travel_to(cid, b["id"])["to_name"] == "The Silent Mill"
    assert server.travel_to(cid, a["id"])["to_name"] == "Ashenveil"  # reverse edge exists


def test_add_location_upserts_a_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Gen")["id"]
    server.add_location(cid, "Placeholder", location_id="loc-fillme")
    out = server.add_location(cid, "Hollowmere", "now fully described", location_id="loc-fillme")
    assert out["location_count"] == 1  # updated in place, not duplicated
    assert server.get_state(cid)["location"]["name"] == "Hollowmere"


def test_add_location_warns_on_orphan_dup_and_bad_connections(tmp_path, monkeypatch):
    # adversarial review #5: silent orphans + duplicate names break travel/recall.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("W")["id"]
    hub = server.add_location(cid, "Hub", "the hub")          # first -> current
    assert hub["is_current"] and not hub["warnings"]
    orphan = server.add_location(cid, "Far Tower", connections=["loc-typo"])  # all conns bad
    assert any("unreachable" in w for w in orphan["warnings"])
    assert any("unknown connection" in w for w in orphan["warnings"])
    dup = server.add_location(cid, "Hub", connections=[hub["id"]])            # duplicate name
    assert any("already exists" in w for w in dup["warnings"])
    good = server.add_location(cid, "Market", connections=[hub["id"]])       # properly wired
    assert not good["warnings"]


def test_create_character_allows_distinct_companion_and_npc_dupes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_adventure("embergloom-pact")["campaign_id"]
    # a differently-named companion is fine
    quill = server.create_character(cid, "Sister Quill", kind="companion", class_name="Bard")
    assert quill["id"] and quill["kind"] == "companion"
    # the guard is companion-scoped: duplicate NPC names are legitimate (two guards)
    g1 = server.create_character(cid, "Town Guard", kind="npc")["id"]
    g2 = server.create_character(cid, "Town Guard", kind="npc")["id"]
    assert g1 and g2 and g1 != g2  # both created, distinct ids — not blocked


# --- living-world QA (bg runs): recruiting a roster NPC made a DUPLICATE ----
# The BG playtests showed two failure modes when bringing a world-seed candidate
# (e.g. Minsc) into the party: (a) using the roster NPC stub directly (kind=npc,
# abilities all 10, not in the party array -> the DM invents modifiers), or
# (b) create_character a second Minsc (a duplicate stub + real companion).
# recruit_companion promotes the EXISTING roster record in place — one record,
# in the party, with a real sheet.


def test_recruit_companion_promotes_roster_npc_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Recruit")["id"]
    # a thin roster NPC, as a world seed would create (flat stats, not in party)
    npc = server.create_character(cid, "Minsc", kind="npc", voice_id="companion-default")["id"]
    assert npc not in server.get_state(cid)["party"]

    out = server.recruit_companion(
        cid, npc, class_name="Ranger", level=5,
        abilities={"strength": 18, "dexterity": 14, "constitution": 14},
        max_hp=45,
    )
    assert out["kind"] == "companion" and npc in out["party"]
    sheet = server.get_character(cid, npc)
    assert sheet["kind"] == "companion"
    assert sheet["max_hp"] == 45 and sheet["armor_class"] >= 10
    assert sheet["hit_dice"] == "5d10"                       # SRD ranger defaults filled
    assert sheet["saving_throw_proficiencies"]               # not the empty stub anymore
    assert sheet["skill_proficiencies"]                      # class skills auto-granted (was [])
    assert "Extra Attack" in sheet["features"]               # level-5 ranger feature
    # NO duplicate: the promotion mutated the existing record in place, so the party
    # holds exactly the one Minsc (now a companion) and no NPC stub is left behind.
    state = server.get_state(cid)
    assert [p for p in state["party"] if p["id"] == npc]     # the promoted Minsc is in the party
    assert sum(1 for p in state["party"] if p["name"] == "Minsc") == 1  # no clone
    assert state["npc_count"] == 0                           # the stub was promoted, not duplicated


def test_apply_srd_defaults_grants_and_overrides_skill_proficiencies(tmp_path, monkeypatch):
    # bg-QA HIGH (both runs): live-made characters had skill_proficiencies:[] so skill
    # checks (incl. social_check) missed the proficiency bonus and the DM invented
    # modifiers. apply_srd_defaults now fills the class's default skills; an explicit
    # `skills` list overrides that default.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Skills")["id"]
    rid = server.create_character(
        cid, "Sneak", kind="player", class_name="Rogue", apply_srd_defaults=True,
        abilities={"dexterity": 16},
    )["id"]
    rogue = server.get_character(cid, rid)
    assert len(rogue["skill_proficiencies"]) == 4              # rogue chooses 4 — not empty
    # explicit choices win over the default-fill
    wid = server.create_character(
        cid, "Pick", kind="player", class_name="Wizard", apply_srd_defaults=True,
        skills=["arcana", "perception"],
    )["id"]
    assert set(server.get_character(cid, wid)["skill_proficiencies"]) == {"arcana", "perception"}


def test_recruit_companion_is_idempotent_and_guards_kind(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Recruit2")["id"]
    npc = server.create_character(cid, "Bram", kind="npc")["id"]
    server.recruit_companion(cid, npc, class_name="Fighter")
    ids_after_first = [p["id"] for p in server.get_state(cid)["party"]]
    server.recruit_companion(cid, npc, class_name="Fighter")  # again
    assert [p["id"] for p in server.get_state(cid)["party"]] == ids_after_first  # not double-added
    # a monster cannot be recruited; an unknown id raises
    mon = server.create_character(cid, "Goblin", kind="monster")["id"]
    with pytest.raises(ValueError, match="recruited"):
        server.recruit_companion(cid, mon)
    with pytest.raises(Exception):
        server.recruit_companion(cid, "char_nonexistent")
