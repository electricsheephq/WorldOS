"""Regressions for bugs surfaced by the autonomous full-plugin playtest (qa/).

The self-play QA run (qa/transcripts/play1) and an independent scorer both flagged:
  - finding 1: melee hits vs an unconscious/paralyzed target should auto-crit;
  - finding 2: damage >= HP max to a creature already at 0 HP is instant death;
  - finding 3: the starter adventure must seed a companion into the party.
"""

import json
from pathlib import Path

import pytest

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


# --- iteration 3: off-turn attack is a reaction (turn-order enforcement) -----


def test_attack_off_turn_is_a_reaction_then_blocked(tmp_path, monkeypatch):
    # Was: an off-turn attack only got an advisory `off_turn_warning`. Now the engine
    # ENFORCES turn order — an off-turn attack is treated as a reaction (an opportunity
    # attack): it resolves once (consuming the combatant's reaction), but a SECOND
    # off-turn attack the same round is REJECTED. An on-turn attack by the current
    # combatant is unaffected. (mechanical-correctness defect 1)
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
    # First off-turn strike resolves as a reaction (no advisory key anymore).
    first = server.attack(cid, off, tgt_off, attack_bonus=0, damage_dice="1d4")
    assert first.get("reaction_used") is True
    assert "off_turn_warning" not in first  # superseded by hard enforcement
    # The reaction is now spent — a second off-turn attack the same round is rejected.
    with pytest.raises(ValueError, match="reaction"):
        server.attack(cid, off, tgt_off, attack_bonus=0, damage_dice="1d4")
    # The current combatant attacking on its OWN turn is fine.
    tgt_cur = next(x for x in ids if x != cur)
    on_turn = server.attack(cid, cur, tgt_cur, attack_bonus=0, damage_dice="1d4")
    assert "off_turn_warning" not in on_turn and on_turn.get("attacks_made_this_turn") == 1


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


def test_recruit_preserves_ending_seeded_arc_end_to_end(tmp_path, monkeypatch):
    # THE S4-C2 SYNTHESIS REACHING LIVE PLAY (the load-bearing connection the two
    # half-tests — seed-lands-arc + recruit-promotes-in-place — never jointly pinned):
    # an ending pre-loads a canon companion's arc onto the roster NPC; RECRUITING that
    # companion must PROMOTE IN PLACE and PRESERVE the seeded arc (so check_companion_arc
    # can later fire the betrayal/loyalty beat). A future refactor that rebuilt the
    # character on recruit would silently kill the synthesis — this catches it.
    import pytest
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.start_world("baldurs-gate", ending="gortash-tyranny")["campaign_id"]

    # the gortash post-state arms Astarion: a loyalty gate + an attitude_below defection.
    seeded = server.get_character(cid, "npc-astarion")
    assert seeded["arc"] is not None, "ending should pre-load Astarion's arc"
    assert seeded["arc"]["agenda"]["trigger"] == "attitude_below"
    assert {g["kind"] for g in seeded["arc"]["arc_gates"]} == {"loyalty"}

    # recruit him as a companion — promotes the EXISTING record in place.
    out = server.recruit_companion(cid, "npc-astarion", class_name="Rogue", level=5,
                                   abilities={"dexterity": 16, "charisma": 14})
    assert out["kind"] == "companion" and "npc-astarion" in out["party"]

    # the seeded arc SURVIVED the recruit (not reset to None, not rebuilt without it).
    after = server.get_character(cid, "npc-astarion")
    assert after["arc"] is not None, "recruit must NOT drop the seeded arc"
    assert after["arc"]["agenda"]["trigger"] == "attitude_below"
    assert after["arc"]["agenda"]["fired"] is False  # still armed, not sprung
    assert {g["kind"] for g in after["arc"]["arc_gates"]} == {"loyalty"}

    # and load_canon_character DEDUPES against the seeded roster NPC — it must NOT spawn a
    # second, arc-less "Astarion" (the duplicate-stub footgun that would bury the synthesis).
    dup = server.load_canon_character(cid, "Astarion", kind="companion", add_to_party=True)
    assert dup.get("already_present") and dup.get("id") == "npc-astarion"  # idempotent, success-shaped
    assert sum(1 for ch in server.get_state(cid)["party"] if ch["name"] == "Astarion") == 1


def test_ability_scores_accept_5e_shorthand(tmp_path, monkeypatch):
    # QA finding (postbg3-validate): start_character failed with "6 validation errors for
    # AbilityScores — Extra inputs are not permitted" because the DM passed the universal
    # 5e shorthand {str, dex, con, int, wis, cha}. The model now aliases short -> long.
    from models import AbilityScores
    import pytest
    a = AbilityScores(**{"str": 12, "dex": 19, "con": 14, "int": 10, "wis": 13, "cha": 8})
    assert (a.strength, a.dexterity, a.constitution) == (12, 19, 14)
    assert (a.intelligence, a.wisdom, a.charisma) == (10, 13, 8)
    # long form unaffected; both-present -> long wins; a genuine typo still trips forbid
    assert AbilityScores(strength=15).strength == 15
    assert AbilityScores(**{"str": 8, "strength": 18}).strength == 18
    with pytest.raises(Exception):
        AbilityScores(**{"strenth": 12})

    # and end-to-end through the tool the QA actually used: create_character with shorthand
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Shorthand")["id"]
    pid = server.create_character(
        cid, "Mira", kind="player", class_name="Rogue",
        abilities={"str": 10, "dex": 17, "con": 13, "int": 12, "wis": 14, "cha": 11},
    )["id"]
    sheet = server.get_character(cid, pid)
    assert sheet["abilities"]["dexterity"] == 17 and sheet["abilities"]["wisdom"] == 14


def test_death_clears_conditions_and_concentration():
    # QA finding (illithid): a dead character kept conditions=['unconscious'] + a stale
    # concentration, an inconsistent record downstream reads trip on. Death supersedes all.
    from models import Character, Condition
    # massive damage while at 0 -> instant death; the unconscious/prone + concentration clear
    ch = Character(name="Nessa", kind="player", max_hp=10, current_hp=0)
    ch.conditions = [Condition.UNCONSCIOUS, Condition.PRONE]
    ch.concentration = "bless"
    combat.apply_damage(ch, 99)
    assert ch.dead is True
    assert ch.conditions == [] and ch.stable is False and ch.concentration is None
    # a monster dies outright at 0 with no lingering condition either
    mon = Character(name="Cultist", kind="monster", max_hp=11, current_hp=5)
    mon.conditions = [Condition.POISONED]
    combat.apply_damage(mon, 5)
    assert mon.dead is True and mon.conditions == []


def test_skill_check_uses_the_sheet_derived_modifier(tmp_path, monkeypatch):
    # QA finding (s7-coldopen2): the DM hand-computed skill modifiers for manual roll() calls and
    # got them wrong (Perception +4 vs +5, Intimidation +3 vs +2). skill_check derives the bonus
    # from the sheet (ability + proficiency/expertise) so it's never hand-computed.
    import pytest
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Checks")["id"]
    pid = server.create_character(cid, "Maren", kind="player", class_name="Ranger",
                                  abilities={"wis": 14, "dex": 16}, skills=["perception"])["id"]
    expected = server._require(cid).characters[pid].skill_bonus("perception")  # the engine's truth
    out = server.skill_check(cid, pid, "Perception", dc=15)
    assert out["modifier"] == expected and out["skill"] == "perception"
    assert "roll" in out and "success" in out  # dc>0 -> pass/fail reported
    assert "success" not in server.skill_check(cid, pid, "Perception")  # dc=0 -> roll only
    with pytest.raises(ValueError, match="unknown skill"):
        server.skill_check(cid, pid, "flossing")


def test_camp_scene_gathers_each_companion_with_standing_and_arc(tmp_path, monkeypatch):
    # The camp social hub (owner ask, Owlcat-style): gather EACH living party companion with a
    # voiceable beat (voice_id + a deliberate prompt), their standing, and a read-only arc summary.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Camp")["id"]
    server.create_character(cid, "Mira", kind="player")  # the PC is NOT a camp "companion" beat
    j = server.create_character(cid, "Jaheira", kind="companion")["id"]
    server.create_character(cid, "Minsc", kind="companion")
    server.adjust_attitude(cid, j, 25)
    server.set_companion_arc(cid, j, {"arc_gates": [{"kind": "loyalty", "threshold": 40, "note": "trust"}]})
    out = server.camp_scene(cid)
    assert set(out["present"]) == {"Jaheira", "Minsc"}            # companions only, not the PC
    assert len(out["beats"]) == 2
    assert all(b.get("prompt") and b.get("voice_id") for b in out["beats"])
    jbeat = next(b for b in out["beats"] if b["companion"] == "Jaheira")
    assert jbeat["attitude_value"] == 25
    assert jbeat["arc"]["next_gate"] == {"kind": "loyalty", "threshold": 40, "points_away": 15}
    mbeat = next(b for b in out["beats"] if b["companion"] == "Minsc")
    assert mbeat["arc"] is None                                   # no arc -> no summary, no crash


def test_long_rest_hints_camp_only_when_companions_present(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Rest")["id"]
    pc = server.create_character(cid, "Lone", kind="player")["id"]
    assert "camp_hint" not in server.long_rest(cid, pc)           # solo -> no camp nudge
    server.create_character(cid, "Karlach", kind="companion")
    assert "camp_hint" in server.long_rest(cid, pc)              # a companion in the party -> nudge


def test_recruit_auto_seeds_default_arc_but_never_overwrites_a_seeded_one(tmp_path, monkeypatch):
    # camp-clarify QA: a freshly-recruited canon companion had arc=null, so camp/arcs were inert.
    # recruit now auto-seeds a light default loyalty arc when none exists — but must NOT clobber a
    # richer ending-seeded arc (the guard is `arc is None`).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Arc")["id"]
    npc = server.create_character(cid, "Bram", kind="npc")["id"]
    out = server.recruit_companion(cid, npc, class_name="Fighter")
    assert out.get("arc_seeded") is True
    arc = server.get_character(cid, npc)["arc"]
    assert arc and [g["kind"] for g in arc["arc_gates"]] == ["loyalty"]  # the default
    # an ending-seeded companion (gortash arms Astarion's attitude_below defection) keeps it
    bg = server.start_world("baldurs-gate", ending="gortash-tyranny")["campaign_id"]
    server.recruit_companion(bg, "npc-astarion", class_name="Rogue", abilities={"dexterity": 16})
    seeded = server.get_character(bg, "npc-astarion")["arc"]
    assert seeded["agenda"]["trigger"] == "attitude_below"  # the seed, NOT overwritten by the default


def test_load_canon_character_resolves_fuller_display_name(tmp_path, monkeypatch):
    # camp-clarify QA: the prelude/roster says "Wyll Ravengard" but the canon file is "Wyll" —
    # load_canon now resolves a unique token-subset match, so the DM doesn't guess-and-retry.
    import content
    rec = content.load_canon_character("baldurs-gate", "Wyll Ravengard")
    assert rec is not None and rec.get("name") == "Wyll"
    # exact still works; a genuinely unknown name still returns None (no wild guess)
    assert content.load_canon_character("baldurs-gate", "Shadowheart") is not None
    assert content.load_canon_character("baldurs-gate", "Nobody McNoface") is None


def test_ability_scores_accept_uppercase_and_mixed_case_keys():
    # easter-eggs QA: the DM used {'STR':10,'DEX':17,...} (uppercase); the shorthand fix handled
    # only lowercase. Now any case works; the long form still wins; a real typo still trips forbid.
    import pytest
    from models import AbilityScores
    a = AbilityScores(**{"STR": 10, "DEX": 17, "Con": 14, "int": 12, "WIS": 13, "cha": 11})
    assert (a.strength, a.dexterity, a.constitution, a.wisdom) == (10, 17, 14, 13)
    assert AbilityScores(**{"str": 8, "STRENGTH": 18}).strength == 18  # long form wins, any case
    with pytest.raises(Exception):
        AbilityScores(**{"STRENTH": 5})


def test_set_quest_status_routes_a_tracked_quest_and_extra_attack_echo(tmp_path, monkeypatch):
    # easter-eggs QA: set_quest_status(quest_id) failed (it only knew hooks). It now routes a
    # tracked add_quest quest too (hook word 'resolved' -> quest 'completed'); + start_combat
    # echoes Extra Attack so the DM makes the right number of attacks.
    import pytest
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("EE")["id"]
    qid = server.add_quest(cid, title="Claudan's Errand")["id"]
    upd = server.set_quest_status(cid, qid, "resolved")     # a QUEST id + hook-vocab -> completed
    assert upd["quest_id"] == qid and upd["status"] == "completed"
    with pytest.raises(ValueError, match="no quest hook or tracked quest"):
        server.set_quest_status(cid, "nope_id", "active")
    # extra-attack reminder: a combatant with extra_attacks>0 -> attacks_per_action = N+1
    bar = server.create_character(cid, "Karlach", kind="companion")["id"]
    server.update_character(cid, bar, {"extra_attacks": 1})
    gob = server.spawn_monster(cid, "Goblin Warrior")["spawned"][0]["id"]
    cv = server.start_combat(cid, [bar, gob])
    assert any(r["name"] == "Karlach" and r["attacks_per_action"] == 2
               for r in cv.get("extra_attack_reminder", []))


def test_unarmored_defense_ac_is_ability_derived(tmp_path, monkeypatch):
    # camp-clarify2 QA: a Barbarian's seeded AC was 1 low — Unarmored Defense is 10 + DEX + CON
    # (Barbarian) / 10 + DEX + WIS (Monk), not a flat table value. Other classes keep the table.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("AC")["id"]
    b = server.create_character(cid, "Karlach", kind="player", class_name="Barbarian",
                                apply_srd_defaults=True, abilities={"dex": 14, "con": 17})["id"]
    assert server.get_character(cid, b)["armor_class"] == 15  # 10 + 2 + 3
    m = server.create_character(cid, "Mei", kind="player", class_name="Monk",
                                apply_srd_defaults=True, abilities={"dex": 16, "wis": 14})["id"]
    assert server.get_character(cid, m)["armor_class"] == 15  # 10 + 3 + 2
    f = server.create_character(cid, "Duren", kind="player", class_name="Fighter",
                                apply_srd_defaults=True, abilities={"dex": 14})["id"]
    assert server.get_character(cid, f)["armor_class"] >= 14    # table-based, not unarmored formula


def test_advance_time_writes_clock_on_narrated_passage(tmp_path, monkeypatch):
    # camp-clarify2 QA: the DM narrated a full day passing but time_of_day stayed 'morning'
    # because nothing called a clock-advancing tool. advance_time fills that gap.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Clock")["id"]
    assert server.get_state(cid)["time_of_day"] == "morning"
    r = server.advance_time(cid, phases=1)
    assert r["time_of_day"] == "afternoon" and r["phases_advanced"] == 1
    # `to` jumps to the named phase within the day…
    r = server.advance_time(cid, to="evening")
    assert r["time_of_day"] == "evening" and r["day"] == 1
    # …and `to` the same/earlier phase rolls into the next day (a full lap).
    r = server.advance_time(cid, to="evening")
    assert r["time_of_day"] == "evening" and r["day"] == 2 and r["phases_advanced"] == 4
    # the write persists (recall/consequences see the moved clock).
    assert server.get_state(cid)["day"] == 2
    # an unknown phase is rejected without mutating the clock.
    bad = server.advance_time(cid, to="midnight")
    assert "error" in bad and server.get_state(cid)["day"] == 2


def test_set_class_resource_registers_and_survives_levelup(tmp_path, monkeypatch):
    # camp-clarify2 QA: a Battle Master's Superiority Dice were untracked — the SRD class
    # tables only know base-class pools. set_class_resource registers the subclass pool; a
    # level-up re-derive must not wipe it (engine = mechanism, DM = the subclass numbers).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Res")["id"]
    f = server.create_character(cid, "Duren", kind="player", class_name="Fighter",
                                level=5, subclass="Battle Master", apply_srd_defaults=True)["id"]
    base = server.get_character(cid, f)["class_resources"]
    assert "superiority_dice" not in base and "second_wind" in base  # SRD knows only base-class pools
    r = server.set_class_resource(cid, f, "Superiority Dice", 6, "short", "d8")
    assert r["resource"] == "superiority_dice" and r["max"] == 6 and r["size"] == "d8" and r["custom"]
    sheet = server.get_character(cid, f)
    assert sheet["class_resources_view"]["superiority_dice"]["label"] == "6/6 d8"  # die surfaced
    assert server.use_resource(cid, f, "superiority_dice", 1)["remaining"] == 5  # spends like any pool
    # a level-up re-derive preserves the custom pool (incl. used) and keeps the SRD pools.
    ch = server._require(cid).characters[f]
    server._recompute_class_resources(ch)
    assert ch.class_resources["superiority_dice"].used == 1  # custom carried forward verbatim
    assert "second_wind" in ch.class_resources                # SRD pools still derived


def test_start_character_seeds_starting_gear_so_ac_and_inventory_agree(tmp_path, monkeypatch):
    # camp-clarify2 QA: a veteran_l5 Fighter had armor_class 16 but inventory [] — internally
    # inconsistent (AC implies armor that doesn't exist). start_character now seeds a kit.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Gear")["id"]
    f = server.start_character(cid, name="Duren", origin="veteran_l5", class_name="Fighter")["id"]
    inv = [i["name"] for i in server.get_character(cid, f)["inventory"]]
    sheet = server.get_character(cid, f)
    assert "Chain Mail" in inv and sheet["armor_class"] == 16  # AC 16 now has the armor to justify it
    assert any("weapon" in i.get("description", "").lower() for i in sheet["inventory"])
    # an Unarmored Defense class (Barbarian) gets NO armor item — its AC is ability-derived.
    b = server.start_character(cid, name="Karlach", origin="veteran_l5", class_name="Barbarian",
                               abilities={"dex": 14, "con": 17})["id"]
    binv = [i["name"] for i in server.get_character(cid, b)["inventory"]]
    assert "Chain Mail" not in binv and not any("mail" in n.lower() or "leather" in n.lower() for n in binv)
    assert server.get_character(cid, b)["armor_class"] == 15  # 10 + DEX(2) + CON(3), unarmored
    # a class-less nobody_l1 keeps an empty inventory (today's behavior — nothing to seed from).
    n = server.start_character(cid, name="Drift", origin="nobody_l1")["id"]
    assert server.get_character(cid, n)["inventory"] == []


def test_add_location_make_current_arrives_in_one_call(tmp_path, monkeypatch):
    # Recurring QA gap: the DM creates the scene the party walks into (add_location) but never
    # travels there, so current_location lags the prose and the new place stays visited=false.
    # make_current arrives in the one call.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Move")["id"]
    sq = server.add_location(cid, "Town Square", description="A plaza.")["id"]
    # generate the next scene AND step onto it in one move, advancing the clock for the walk.
    r = server.add_location(cid, "Siltwharf Steps", connections=[sq],
                            make_current=True, advance_time=True)
    assert r["is_current"] and r["arrived"] and r["visited"]
    assert server.get_state(cid)["location"]["name"] == "Siltwharf Steps"
    assert server.get_state(cid)["time_of_day"] == "afternoon"  # the walk advanced one phase
    # without make_current the place is created but NOT current (explicit travel_to still works).
    hp = server.add_location(cid, "Heapside Room", connections=[r["id"]])
    assert not hp["is_current"] and not hp["visited"]


def test_combat_numbers_surface_authoritative_attack_bonus(tmp_path, monkeypatch):
    # easter2 QA: the DM invented a Rogue's to-hit (+7) by copying another combatant; her sheet
    # gave +3. combat_numbers surfaces the real bonus at creation AND on get_character so the DM
    # passes the sheet's number to attack() instead of inventing one.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("CN")["id"]
    r = server.start_character(cid, name="Vesper", origin="veteran_l5", class_name="Rogue",
                               abilities={"dex": 10, "str": 10})
    cn = r["combat_numbers"]  # echoed at creation
    assert cn["proficiency_bonus"] == 3 and cn["ranged_attack_bonus"] == 3  # prof 3 + DEX 0, NOT +7
    gc = server.get_character(cid, r["id"])["combat_numbers"]  # and on the sheet
    assert gc["ranged_attack_bonus"] == 3 and gc["melee_attack_bonus"] == 3
    assert set(gc["ability_mods"]) == {"str", "dex", "con", "int", "wis", "cha"}


def test_social_check_ephemeral_target_does_not_corrupt_a_roster_npc(tmp_path, monkeypatch):
    # easter2 QA: a Deception vs a dock extra reused a seeded companion's id as the target and
    # silently shifted her standing. An ephemeral target rolls without writing any roster NPC.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Soc")["id"]
    pc = server.create_character(cid, "Hero", kind="player", abilities={"cha": 16})["id"]
    npc = server.create_character(cid, "Jaheira", kind="npc")["id"]
    before = server.get_character(cid, npc)["attitude_value"]
    out = server.social_check(cid, pc, "", "deception", 5, target_name="the fishmonger")
    assert out["ephemeral"] and out["npc"] == "the fishmonger"
    assert server.get_character(cid, npc)["attitude_value"] == before  # untouched
    # the real-NPC path still moves a tracked relationship.
    server.social_check(cid, pc, npc, "persuasion", 1)
    assert server.get_character(cid, npc)["attitude_value"] != before


def test_spawn_monster_resolves_thug_alias_and_suggests_on_miss(tmp_path, monkeypatch):
    # fidelity1 QA: spawn_monster('Thug') failed with empty suggestions. 'Thug' is the 2014 name
    # for the 2024 'Tough'; alias it, and give any miss real recovery suggestions.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("B")["id"]
    r = server.spawn_monster(cid, "Thug")
    assert "spawned" in r and r["spawned"][0]["name"] == "Tough"
    miss = server.spawn_monster(cid, "Zorblax the Unknowable")
    assert "error" in miss and len(miss["suggestions"]) > 0  # never a dead end


# --- adversarial-protagonist QA (bg brawler/operator/wildcard) engine hardening ---


def test_apply_srd_defaults_computes_hp_for_higher_levels(tmp_path, monkeypatch):
    # CRITICAL (operator+wildcard): a level>1 character made with apply_srd_defaults
    # got max_hp:1 — HP was only computed at level 1, so every multi-level companion/
    # legend had 1 HP. Now computed across the full level (SRD fixed-HP).
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("HP")["id"]
    wid = server.create_character(
        cid, "Mage", kind="player", class_name="Wizard", level=3,
        apply_srd_defaults=True, abilities={"constitution": 14},  # d6, CON +2
    )["id"]
    assert server.get_character(cid, wid)["max_hp"] == 20  # 8 + 6 + 6, not 1
    # an explicit max_hp is still respected (not overwritten by the class calc)
    bid = server.create_character(
        cid, "Bruiser", kind="companion", class_name="Barbarian", level=5,
        max_hp=60, apply_srd_defaults=True,
    )["id"]
    assert server.get_character(cid, bid)["max_hp"] == 60


def test_rest_refused_during_active_combat(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Rest")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=10)["id"]
    g = server.create_character(cid, "G", kind="monster", max_hp=10)["id"]
    server.start_combat(cid, [a, g])
    with pytest.raises(ValueError, match="combat"):
        server.long_rest(cid, a)
    with pytest.raises(ValueError, match="combat"):
        server.short_rest(cid, a)
    server.end_combat(cid)
    server.long_rest(cid, a)  # allowed once combat is over


def test_level_up_keeps_hit_dice_string_in_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("LU")["id"]
    wid = server.create_character(
        cid, "W", kind="player", class_name="Wizard", level=3, apply_srd_defaults=True,
    )["id"]
    assert server.get_character(cid, wid)["hit_dice"] == "3d6"
    server.level_up(cid, wid, "wizard")
    assert server.get_character(cid, wid)["hit_dice"] == "4d6"  # was stale at 3d6


def test_remove_last_hostile_auto_ends_combat(tmp_path, monkeypatch):
    # brawler: after the enemies fled/died, only the party remained but combat stayed
    # active and the DM had to end it by hand. No hostiles left -> the fight is over.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("End")["id"]
    a = server.create_character(cid, "Hero", kind="player", max_hp=10)["id"]
    comp = server.create_character(cid, "Ally", kind="companion", max_hp=10)["id"]
    g = server.create_character(cid, "Goblin", kind="monster", max_hp=10)["id"]
    server.start_combat(cid, [a, comp, g])
    assert server.get_state(cid)["in_combat"] is True
    out = server.remove_combatant(cid, g)  # last hostile removed
    assert out["active"] is False and server.get_state(cid)["in_combat"] is False


def test_stabilize_closes_the_aid_downed_loop(tmp_path, monkeypatch):
    import pytest
    # brawler's top finding: companion_suggest_action returns aid_downed with spell:null
    # when there's no heal slot, but there was NO engine path to land a stabilize —
    # the DM had to hand-wave a Medicine check. stabilize() is that path now.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Stab")["id"]
    medic = server.create_character(
        cid, "Medic", kind="companion", abilities={"wisdom": 20}, skills=["medicine"],
    )["id"]
    downed = server.create_character(cid, "Downed", kind="player", max_hp=10)["id"]
    server.apply_damage(cid, downed, 10)  # to 0 HP -> dying, unstable
    assert server.get_character(cid, downed)["current_hp"] == 0
    out = server.stabilize(cid, medic, downed, dc=1)  # auto-succeeds
    assert out["success"] is True and out["stable"] is True
    assert server.get_character(cid, downed)["stable"] is True
    # can't stabilize someone already stable (or not downed)
    with pytest.raises(ValueError, match="downed"):
        server.stabilize(cid, medic, downed)


def test_created_npc_is_anchored_to_current_location(tmp_path, monkeypatch):
    # Dashboard "In the scene" was showing the whole seeded world roster. NPCs/monsters
    # are now anchored to where they're introduced, so the scene = the local cast.
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Anchor")["id"]
    hub = server.add_location(cid, "Hub", "the hub")["id"]  # first location -> current
    npc = server.create_character(cid, "Barkeep", kind="npc")["id"]
    assert server.get_character(cid, npc)["location_id"] == hub          # anchored to current
    far = server.add_location(cid, "Far", "far", connections=[hub])["id"]
    npc2 = server.create_character(cid, "Hermit", kind="npc", location_id=far)["id"]
    assert server.get_character(cid, npc2)["location_id"] == far          # explicit wins
    pc = server.create_character(cid, "Hero", kind="player")["id"]
    assert not server.get_character(cid, pc)["location_id"]               # players unanchored
