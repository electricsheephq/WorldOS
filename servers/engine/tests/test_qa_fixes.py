"""Regressions for bugs surfaced by the autonomous full-plugin playtest (qa/).

The self-play QA run (qa/transcripts/play1) and an independent scorer both flagged:
  - finding 1: melee hits vs an unconscious/paralyzed target should auto-crit;
  - finding 2: damage >= HP max to a creature already at 0 HP is instant death;
  - finding 3: the starter adventure must seed a companion into the party.
"""

import json
from pathlib import Path

import combat
import content
import server
from models import Character, Condition

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
