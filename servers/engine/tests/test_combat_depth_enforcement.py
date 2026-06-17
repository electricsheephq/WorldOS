"""P2 cluster "Combat depth & enforcement" (#792) — engine gating + auto-rolls.

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (unit 01, Part C).

Sub-findings exercised here:
  F01-7  grapple/shove/escape_grapple/stabilize bypass every combat gate
  F01-8  Grappled/Restrained don't gate zone movement; no Disengage state
  F01-9  Concentration check surfaced-but-ignorable (auto-roll at damage time)
  F01-10 Death saves skippable (auto-roll at the start of a dying PC's turn)
  F01-12 No way to add a combatant to a running fight
  F01-13 Legendary actions unmodeled (v1 surface)
  F01-14 attack/cast outside combat = full effect, no nudge

F01-11 (wandering spawns lose Parry) was already fixed by #832's shared
`_monster_character_from_statblock` factory — not retested here.

Dice are forced by monkeypatching `server.dice_mod.roll` to a controlled DiceRoll
so outcomes are deterministic (the idiom used by test_grapple_shove.py).
"""

from __future__ import annotations

import pytest
from dice import DiceRoll

import combat
import server
from models import Ability, Condition


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    yield


def _fake_roll(natural: int, bonus: int = 0) -> DiceRoll:
    total = natural + bonus
    return DiceRoll(
        expression=f"1d20+{bonus}",
        total=total,
        rolls=[natural],
        is_d20=True,
        natural=natural,
        crit=(natural == 20),
        fumble=(natural == 1),
    )


def _force_roll(monkeypatch, natural: int):
    """Force every server-side d20/dice roll to a fixed natural (bonus 0)."""
    monkeypatch.setattr("server.dice_mod.roll", lambda *a, **kw: _fake_roll(natural))


def _two_player_fight(server):
    """A combat between two players A and B; returns (cid, current_id, other_id, ids)."""
    cid = server.create_campaign("depth")["id"]
    ids = [
        server.create_character(cid, n, kind="player", max_hp=30, armor_class=12)["id"]
        for n in ("A", "B")
    ]
    server.start_combat(cid, ids)
    cur = server.get_state(cid)["current_turn"]
    other = next(i for i in ids if i != cur)
    return cid, cur, other, ids


# ---------------------------------------------------------------------------
# F01-7 — grapple/shove/escape_grapple/stabilize honor the combat gates
# ---------------------------------------------------------------------------

def test_grapple_by_incapacitated_attacker_is_rejected(monkeypatch):
    """An incapacitated (stunned/paralyzed/…) creature can't grapple — reject before
    any roll/state change. Mirrors attack()'s is_incapacitated guard."""
    cid, cur, other, _ = _two_player_fight(server)
    server.add_condition(cid, cur, "stunned")
    _force_roll(monkeypatch, 1)  # would auto-fail the save if it ran
    with pytest.raises(ValueError, match="incapacitated"):
        server.grapple(cid, cur, other)
    # No grappled condition applied (the action never resolved).
    assert "grappled" not in server.get_character(cid, other)["conditions"]


def test_grapple_off_turn_without_reaction_is_rejected(monkeypatch):
    """A grapple is an Attack-action option — illegal off your turn (the off-turn
    creature has no action). Rejected with no state change."""
    cid, cur, other, _ = _two_player_fight(server)
    _force_roll(monkeypatch, 1)
    with pytest.raises(ValueError, match="turn"):
        # `other` is NOT the current combatant.
        server.grapple(cid, other, cur)
    assert "grappled" not in server.get_character(cid, cur)["conditions"]


def test_grapple_consumes_one_attack_of_the_action_budget(monkeypatch):
    """A grapple on your own turn consumes one Attack-action's worth of attack budget
    (2024: grapple is an Unarmed Strike option of the Attack action). A subsequent
    plain attack with no Extra Attack is then rejected (budget spent)."""
    cid, cur, other, _ = _two_player_fight(server)
    _force_roll(monkeypatch, 10)
    server.grapple(cid, cur, other)
    # The Attack action's single strike is now spent → a follow-up attack is rejected.
    with pytest.raises(ValueError, match="cannot attack|attack"):
        server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")


def test_grapple_outside_combat_is_inert_to_gates(monkeypatch):
    """No active combat → the grapple resolves with no economy gating (an out-of-
    initiative scuffle is the DM's call), exactly as before."""
    cid = server.create_campaign("nocombat")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=20)["id"]
    b = server.create_character(cid, "B", kind="player", max_hp=20)["id"]
    _force_roll(monkeypatch, 1)
    out = server.grapple(cid, a, b)  # must not raise
    assert out["applied"] is True


def test_shove_off_turn_is_rejected(monkeypatch):
    cid, cur, other, _ = _two_player_fight(server)
    _force_roll(monkeypatch, 1)
    with pytest.raises(ValueError, match="turn"):
        server.shove(cid, other, cur)


def test_escape_grapple_off_turn_is_rejected(monkeypatch):
    """Escaping a grapple is an action — only on your own turn while in initiative."""
    cid, cur, other, _ = _two_player_fight(server)
    # Make `other` grappled by `cur` first (out of the gate path: set directly).
    server.add_condition(cid, other, "grappled")
    _force_roll(monkeypatch, 20)
    with pytest.raises(ValueError, match="turn"):
        server.escape_grapple(cid, other, cur)  # other is not current
    assert "grappled" in server.get_character(cid, other)["conditions"]


def test_escape_grapple_on_own_turn_consumes_action(monkeypatch):
    cid, cur, other, _ = _two_player_fight(server)
    server.add_condition(cid, cur, "grappled")
    _force_roll(monkeypatch, 20)
    server.escape_grapple(cid, cur, other)
    # Action spent → a same-turn attack is rejected.
    with pytest.raises(ValueError, match="cannot attack|attack|action"):
        server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")


def test_stabilize_off_turn_is_rejected(monkeypatch):
    """Stabilizing a downed ally (Medicine check) is an action — own turn only."""
    cid = server.create_campaign("stab")["id"]
    ids = [
        server.create_character(cid, n, kind="player", max_hp=30)["id"]
        for n in ("A", "B", "C")
    ]
    server.start_combat(cid, ids)
    cur = server.get_state(cid)["current_turn"]
    others = [i for i in ids if i != cur]
    actor, downed = others[0], others[1]  # neither is the current combatant
    server.set_hp(cid, downed, 0)  # downed ally
    _force_roll(monkeypatch, 20)
    with pytest.raises(ValueError, match="turn"):
        server.stabilize(cid, actor, downed)  # actor is NOT current → no action
    assert server.get_character(cid, downed)["stable"] is False


def test_stabilize_by_incapacitated_actor_is_rejected(monkeypatch):
    cid, cur, other, _ = _two_player_fight(server)
    server.set_hp(cid, other, 0)  # downed ally
    server.add_condition(cid, cur, "paralyzed")  # actor can't act
    _force_roll(monkeypatch, 20)
    with pytest.raises(ValueError, match="incapacitated"):
        server.stabilize(cid, cur, other)


# ---------------------------------------------------------------------------
# F01-8 — conditions gate zone movement; Disengage suppresses OAs
# ---------------------------------------------------------------------------

def _zone_fight(server):
    """A 2-zone fight: hero (player) + gob (monster), both in 'doorway'."""
    cid = server.create_campaign("zones")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30, armor_class=14)["id"]
    gob = server.create_character(cid, "Gob", kind="monster", max_hp=12, armor_class=12)["id"]
    server.start_combat(cid, [hero, gob])
    server.set_zones(cid, [
        {"name": "doorway", "adjacent": ["hall"]},
        {"name": "hall", "adjacent": ["doorway"]},
    ])
    server.place_combatant(cid, hero, "doorway")
    server.place_combatant(cid, gob, "doorway")
    return cid, hero, gob


def test_grappled_mover_movement_flagged_illegal():
    cid, hero, gob = _zone_fight(server)
    server.add_condition(cid, hero, "grappled")  # Speed 0
    out = server.move_to_zone(cid, hero, "hall")
    assert out.get("movement_illegal"), "a grappled (Speed 0) mover's move must be flagged illegal"


def test_restrained_mover_movement_flagged_illegal():
    cid, hero, gob = _zone_fight(server)
    server.add_condition(cid, hero, "restrained")
    out = server.move_to_zone(cid, hero, "hall")
    assert out.get("movement_illegal")


def test_grappler_is_not_a_provoker_against_its_own_captive():
    """If the grappled creature somehow moves, the grappler holding it should not be
    listed as an OA provoker against the creature it's restraining (it would have to
    release to move-with). Suppress the grappler (Character.grappled_by) from provokers."""
    cid, hero, gob = _zone_fight(server)
    # gob holds hero in a grapple — the engine records the holder in grappled_by.
    server.add_condition(cid, hero, "grappled")
    server.update_character(cid, hero, {"grappled_by": gob})
    out = server.move_to_zone(cid, hero, "hall")
    prov_ids = [p["id"] for p in out.get("provokers", [])]
    assert gob not in prov_ids, "the grappler must not provoke an OA on its own captive"


def test_grapple_records_the_grappler(monkeypatch):
    """A successful grapple records the holder in Character.grappled_by; a successful
    escape clears it."""
    cid = server.create_campaign("grab")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=20,
                                abilities={"strength": 18})["id"]
    b = server.create_character(cid, "B", kind="player", max_hp=20)["id"]
    _force_roll(monkeypatch, 1)  # target fails the save → grappled
    server.grapple(cid, a, b)
    assert server.get_character(cid, b)["grappled_by"] == a
    _force_roll(monkeypatch, 20)  # escape succeeds
    server.escape_grapple(cid, b, a)
    assert server.get_character(cid, b)["grappled_by"] is None


def _make_current(server, cid, who):
    """Advance turns until `who` is the current combatant (skip-and-advance others)."""
    guard = 0
    while server.get_state(cid)["current_turn"] != who and guard < 8:
        cur = server.get_state(cid)["current_turn"]
        ch = server.get_character(cid, cur)
        if ch["kind"] in ("player", "companion"):
            server.use_action(cid, cur, kind="skip")
        server.next_turn(cid)
        guard += 1


def test_disengage_suppresses_opportunity_attacks(monkeypatch):
    """use_action(kind='disengage') sets a per-turn disengaged flag; a subsequent
    move_to_zone provokes NO opportunity attacks (5e Disengage)."""
    cid, hero, gob = _zone_fight(server)
    _force_roll(monkeypatch, 10)
    _make_current(server, cid, hero)
    # Sanity: without disengage, leaving a zone shared with the hostile provokes.
    base = server.move_to_zone(cid, hero, "hall")
    assert base["opportunity_attack"] is True
    server.move_to_zone(cid, hero, "doorway")  # back
    # Now Disengage, then move — no provokers.
    res = server.use_action(cid, hero, kind="disengage")
    assert res["ok"] is True
    assert res["disengaged"] is True
    out = server.move_to_zone(cid, hero, "hall")
    assert out["opportunity_attack"] is False
    assert out["provokers"] == []


def test_disengage_flag_clears_after_the_turn(monkeypatch):
    """The disengaged flag is per-turn; once `cur` ends its turn and acts again on a
    later turn, the prior Disengage no longer suppresses OAs (it reset)."""
    import server
    cid, hero, gob = _zone_fight(server)
    # Ensure it's the hero's turn (skip until then).
    _force_roll(monkeypatch, 10)
    guard = 0
    while server.get_state(cid)["current_turn"] != hero and guard < 6:
        server.use_action(cid, server.get_state(cid)["current_turn"], kind="skip")
        server.next_turn(cid)
        guard += 1
    server.use_action(cid, hero, kind="disengage")
    # Hero passes their turn; advance a full round back to the hero.
    server.use_action(cid, hero, kind="skip")
    server.next_turn(cid)  # → gob
    server.next_turn(cid)  # back → hero (gob is a monster, advances freely)
    # The disengaged flag must have reset: leaving the shared zone provokes again.
    out = server.move_to_zone(cid, hero, "hall")
    assert out["opportunity_attack"] is True
    assert any(p["id"] == gob for p in out["provokers"])


# ---------------------------------------------------------------------------
# F01-9 — concentration auto-rolls at damage time (engine rolls, DM is told)
# ---------------------------------------------------------------------------

def test_apply_damage_auto_rolls_concentration_and_breaks_on_failure(monkeypatch):
    cid = server.create_campaign("conc")["id"]
    caster = server.create_character(cid, "Caster", kind="player", max_hp=30,
                                     abilities={"constitution": 10})["id"]
    server.update_character(cid, caster, {"concentration": "Bless"})
    _force_roll(monkeypatch, 2)  # CON save total 2 vs DC 10 → fail
    out = server.apply_damage(cid, caster, amount=10, damage_type="slashing")
    cs = out.get("concentration_save")
    assert cs is not None and cs["rolled"] is True
    assert cs["maintained"] is False
    assert server.get_character(cid, caster)["concentration"] is None


def test_apply_damage_auto_concentration_maintained_on_success(monkeypatch):
    cid = server.create_campaign("conc2")["id"]
    caster = server.create_character(cid, "Caster", kind="player", max_hp=30,
                                     abilities={"constitution": 10})["id"]
    server.update_character(cid, caster, {"concentration": "Bless"})
    _force_roll(monkeypatch, 19)  # total 19 vs DC 10 → success
    out = server.apply_damage(cid, caster, amount=10, damage_type="slashing")
    cs = out["concentration_save"]
    assert cs["maintained"] is True
    assert server.get_character(cid, caster)["concentration"] == "Bless"


def test_apply_damage_no_concentration_is_unchanged(monkeypatch):
    cid = server.create_campaign("conc3")["id"]
    t = server.create_character(cid, "T", kind="player", max_hp=30)["id"]
    _force_roll(monkeypatch, 2)
    out = server.apply_damage(cid, t, amount=10, damage_type="slashing")
    assert "concentration_save" not in out  # nobody was concentrating


def test_attack_auto_rolls_concentration_on_landed_hit(monkeypatch):
    """A landed attack against a concentrating target auto-rolls its concentration
    save and surfaces it — the DM no longer has to remember concentration_save()."""
    cid, cur, other, _ = _two_player_fight(server)
    server.update_character(cid, other, {"concentration": "Hold Person",
                                         "abilities": {"constitution": 10}})
    _force_roll(monkeypatch, 19)  # hits AND the CON save total 19 ≥ DC 10
    res = server.attack(cid, cur, other, attack_bonus=10, damage_dice="2d6+5")
    assert "concentration_save" in res
    assert res["concentration_save"]["rolled"] is True


# ---------------------------------------------------------------------------
# F01-10 — death saves auto-roll at the start of a dying PC's turn
# ---------------------------------------------------------------------------

def test_next_turn_auto_rolls_death_save_for_dying_pc(monkeypatch):
    cid = server.create_campaign("ds")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=20)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=20)["id"]
    server.start_combat(cid, [a, b])
    # Down the PC.
    server.set_hp(cid, a, 0)
    # Force every roll to a natural 15 (≥10 → a death-save SUCCESS).
    _force_roll(monkeypatch, 15)
    # Advance turns until it becomes A's turn; the engine should auto-roll A's death save.
    saw_auto = False
    for _ in range(4):
        view = server.next_turn(cid)
        if view.get("death_saves_rolled"):
            saw_auto = True
            break
    assert saw_auto, "next_turn must auto-roll the dying PC's death save"
    ch = server.get_character(cid, a)
    assert ch["death_saves"]["successes"] >= 1


def test_death_save_auto_roll_nat20_revives(monkeypatch):
    cid = server.create_campaign("ds20")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=20)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=20)["id"]
    server.start_combat(cid, [a, b])
    server.set_hp(cid, a, 0)
    _force_roll(monkeypatch, 20)  # nat 20 → regain 1 HP
    for _ in range(4):
        server.next_turn(cid)
        if server.get_character(cid, a)["current_hp"] > 0:
            break
    assert server.get_character(cid, a)["current_hp"] == 1


def test_stable_pc_is_not_auto_rolled(monkeypatch):
    cid = server.create_campaign("dsstable")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=20)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=20)["id"]
    server.start_combat(cid, [a, b])
    server.set_hp(cid, a, 0)
    server.update_character(cid, a, {"stable": True})
    _force_roll(monkeypatch, 1)  # would fail if it rolled
    for _ in range(4):
        view = server.next_turn(cid)
    ch = server.get_character(cid, a)
    assert ch["death_saves"]["failures"] == 0  # never rolled


# ---------------------------------------------------------------------------
# F01-12 — add a combatant to a running fight (engine rolls initiative)
# ---------------------------------------------------------------------------

def test_add_combatant_inserts_into_running_order(monkeypatch):
    cid = server.create_campaign("reinf")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=20)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=20)["id"]
    reinforcement = server.create_character(cid, "Reinf", kind="monster", max_hp=15)["id"]
    server.start_combat(cid, [a, b])
    _force_roll(monkeypatch, 12)
    view = server.add_combatant(cid, reinforcement)
    order_ids = [cb["character_id"] for cb in view["order"]]
    assert reinforcement in order_ids
    assert len(order_ids) == 3
    assert view["added"]["id"] == reinforcement
    # The current combatant must be unchanged by the insertion.
    assert server.get_state(cid)["current_turn"] in (a, b)


def test_add_combatant_requires_active_combat():
    cid = server.create_campaign("reinf2")["id"]
    m = server.create_character(cid, "M", kind="monster", max_hp=20)["id"]
    with pytest.raises(ValueError, match="no active combat|combat"):
        server.add_combatant(cid, m)


def test_mid_fight_spawn_is_gated_after_being_added(monkeypatch):
    """A reinforcement added to the order is now subject to the same turn gate —
    it can't attack off-turn for free (the F01-12 'bypass half')."""
    cid = server.create_campaign("reinf3")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=30, armor_class=10)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=30, armor_class=10)["id"]
    reinf = server.create_character(cid, "Reinf", kind="monster", max_hp=30, armor_class=10)["id"]
    server.start_combat(cid, [a, b])
    _force_roll(monkeypatch, 1)  # reinf rolls low initiative → not current
    server.add_combatant(cid, reinf)
    cur = server.get_state(cid)["current_turn"]
    if cur != reinf:
        _force_roll(monkeypatch, 15)
        # First off-turn strike is treated as a reaction (allowed once); the SECOND is rejected.
        server.attack(cid, reinf, a, attack_bonus=5, damage_dice="1d6")
        with pytest.raises(ValueError, match="reaction"):
            server.attack(cid, reinf, a, attack_bonus=5, damage_dice="1d6")


# ---------------------------------------------------------------------------
# F01-13 — legendary actions are surfaced (v1)
# ---------------------------------------------------------------------------

def test_start_combat_surfaces_legendary_actions(monkeypatch):
    cid = server.create_campaign("legend")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=40)["id"]
    dragon = server.spawn_monster(cid, "Adult Red Dragon")["spawned"][0]["id"]
    view = server.start_combat(cid, [pc, dragon])
    mc = view.get("monster_combat", [])
    legendary_entries = [e for e in mc if e.get("legendary_actions")]
    assert legendary_entries, "a legendary creature must surface legendary_actions in monster_combat"
    la = legendary_entries[0]["legendary_actions"]
    assert la["budget"] >= 1
    assert len(la["options"]) >= 1


def test_non_legendary_monster_has_no_legendary_surface(monkeypatch):
    cid = server.create_campaign("nolegend")["id"]
    pc = server.create_character(cid, "Hero", kind="player", max_hp=40)["id"]
    gob = server.spawn_monster(cid, "Goblin")["spawned"][0]["id"]
    view = server.start_combat(cid, [pc, gob])
    for e in view.get("monster_combat", []):
        assert "legendary_actions" not in e


# ---------------------------------------------------------------------------
# F01-14 — attack/cast outside combat surfaces a start_combat nudge
# ---------------------------------------------------------------------------

def test_attack_outside_combat_surfaces_combat_nudge(monkeypatch):
    cid = server.create_campaign("oob")["id"]
    a = server.create_character(cid, "A", kind="player", max_hp=20)["id"]
    b = server.create_character(cid, "B", kind="monster", max_hp=20, armor_class=5)["id"]
    _force_roll(monkeypatch, 15)
    res = server.attack(cid, a, b, attack_bonus=5, damage_dice="1d6")
    assert "combat_not_active" in res, "an out-of-combat attack must nudge to start_combat"
    # …and the attack still fully resolves (never blocked — trap/hazard inertness preserved).
    assert "hit" in res


def test_attack_in_combat_has_no_combat_nudge(monkeypatch):
    cid, cur, other, _ = _two_player_fight(server)
    _force_roll(monkeypatch, 15)
    res = server.attack(cid, cur, other, attack_bonus=5, damage_dice="1d6")
    assert "combat_not_active" not in res
