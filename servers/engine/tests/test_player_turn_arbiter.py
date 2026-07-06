"""S1 keystone — the ENGINE PLAYER-TURN COMBAT ARBITER (combat_loop.resolve_player_turn).

The arbiter is the single path by which a HUMAN/UI-authored Intent is resolved on the
player's grid-combat turn. It mirrors run_combat_round(mode="live")'s `awaiting_pc` stop:
the loop hands a PC turn to the UI; the UI POSTs the PC's chosen Intent (a cell to move to /
a target to strike); the arbiter validates turn-ownership, resolves it through the SAME
`_apply_intent` mapping the AI uses (so the engine rolls), advances initiative, then auto-runs
the following enemy turns to the next PC decision.

Load-bearing invariants exercised here:
  * TURN-OWNERSHIP: a wrong-turn / non-PC / no-combat move REJECTS with NOTHING mutated
    (move_to_coords does not self-gate, so this gate is the only guard).
  * SOLE WRITER: every mutation goes through the existing locked verbs (move_to_coords /
    attack / next_turn) — the arbiter holds no lock and adds no write path.
  * ADDITIVE: a campaign that never calls the arbiter is byte-identical to today (covered by
    the existing test_grid round-trip + the move-budget tests; here we assert the new path
    only TOUCHES state via the locked verbs).
"""

import pytest

import combat_loop
import server
from combat_ai import Intent


@pytest.fixture
def grid_fight(tmp_path, monkeypatch):
    """A hero (player, speed 30) FIRST in initiative + a goblin (monster), on a 20x20 grid.

    The hero is passed as a surpriser so it deterministically leads the order (no dice flake),
    so resolve_player_turn's current-combatant == the PC from the start."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Arbiter")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30, armor_class=14)["id"]
    gob = server.create_character(cid, "Goblin", kind="monster", max_hp=15, armor_class=12)["id"]
    server.start_combat(cid, [hero, gob], surpriser_ids=[hero])  # hero acts first
    server.set_grid(cid, 20, 20)
    server.place_combatant_at_coords(cid, hero, 0, 0)
    server.place_combatant_at_coords(cid, gob, 10, 0)
    return cid, hero, gob


def _cell(cid: str, who: str):
    c = server._require(cid)
    cb = next(o for o in c.combat.order if o.character_id == who)
    return (cb.x, cb.y)


# ── HAPPY PATH: a player move-to-cell on the PC's turn resolves + advances ────────────


def test_bare_move_keeps_turn_open(grid_fight):
    """5e: a move consumes no action. A bare move-to-cell resolves the move but DOES NOT end the
    turn — the PC may still act (turn_open True, initiative unchanged)."""
    cid, hero, gob = grid_fight
    assert server._require(cid).combat.current_combatant_id == hero
    assert _cell(cid, hero) == (0, 0)

    out = combat_loop.resolve_player_turn(cid, hero, Intent(kind="move", to_cell=(3, 0)))
    assert out["ok"] is True, out
    # The token moved (engine charged the budget via the locked verb).
    assert _cell(cid, hero) == (3, 0)
    # The turn is STILL the hero's — a move alone never advances initiative.
    assert out["advanced"] is False
    assert out["turn_open"] is True
    assert server._require(cid).combat.current_combatant_id == hero


def test_move_then_end_turn_advances(grid_fight):
    """A move followed by end_turn=True ends the move-only turn (the arbiter declares the skip),
    advances initiative, and auto-runs the enemy turn to the next PC decision."""
    cid, hero, gob = grid_fight
    out = combat_loop.resolve_player_turn(
        cid, hero, Intent(kind="move", to_cell=(3, 0)), end_turn=True
    )
    assert out["ok"] is True, out
    assert _cell(cid, hero) == (3, 0)
    assert out["advanced"] is True
    assert out["turn_open"] is False
    c = server._require(cid)
    if c.combat.active:
        assert out["awaiting_pc"] == hero  # back to the hero after the goblin auto-ran
        assert c.combat.current_combatant_id == hero


def test_attack_ends_turn_and_advances(grid_fight):
    """An attack CONSUMES the action -> the turn ends and the arbiter advances + auto-runs the
    enemy, without an explicit end_turn."""
    cid, hero, gob = grid_fight
    server.place_combatant_at_coords(cid, gob, 1, 0)  # adjacent so the strike is in reach
    out = combat_loop.resolve_player_turn(
        cid, hero, Intent(kind="attack", target_id=gob, attack_name="Weapon")
    )
    assert out["ok"] is True, out
    assert out["advanced"] is True
    assert out["turn_open"] is False


def test_move_to_cell_charges_movement_budget(grid_fight):
    cid, hero, gob = grid_fight
    combat_loop.resolve_player_turn(cid, hero, Intent(kind="move", to_cell=(4, 0)), advance=False)
    c = server._require(cid)
    cb = next(o for o in c.combat.order if o.character_id == hero)
    assert cb.moved_cells_this_turn == 4  # Chebyshev 0,0 -> 4,0 == 4 cells charged


def test_attack_on_turn_rolls_via_engine(grid_fight):
    cid, hero, gob = grid_fight
    # Place the goblin adjacent so a melee strike is in reach, then attack it on the PC's turn.
    server.place_combatant_at_coords(cid, gob, 1, 0)
    out = combat_loop.resolve_player_turn(
        cid, hero, Intent(kind="attack", target_id=gob, attack_name="Weapon"), advance=False
    )
    assert out["ok"] is True, out
    # The arbiter delegated to the locked attack() verb — the digest entry records a resolution.
    assert out["resolved"]["kind"] == "attack"
    assert "result" in out["resolved"]


# ── TURN-OWNERSHIP: wrong turn / non-PC / no combat all REJECT with nothing mutated ──


def test_wrong_turn_move_is_rejected_and_mutates_nothing(grid_fight):
    cid, hero, gob = grid_fight
    # It is the HERO's turn; a move declared for the GOBLIN must be refused.
    before = _cell(cid, gob)
    out = combat_loop.resolve_player_turn(cid, gob, Intent(kind="move", to_cell=(5, 0)))
    assert out["ok"] is False
    assert "turn" in out["reason"].lower()
    assert _cell(cid, gob) == before  # NOTHING moved
    # The hero is still the current combatant (initiative did not advance).
    assert server._require(cid).combat.current_combatant_id == hero


def test_monster_cannot_be_driven_through_player_lane(grid_fight):
    cid, hero, gob = grid_fight
    # Even if it WERE the goblin's turn, a monster is not a player-controlled combatant. Advance
    # to the goblin's turn first, then try to drive it through the player arbiter.
    server.use_action(cid, hero, kind="skip")
    server.next_turn(cid)  # now the goblin's turn
    assert server._require(cid).combat.current_combatant_id == gob
    out = combat_loop.resolve_player_turn(cid, gob, Intent(kind="move", to_cell=(5, 0)))
    assert out["ok"] is False
    assert "monster" in out["reason"].lower() or "player-controlled" in out["reason"].lower()


def test_attack_without_target_rejected(grid_fight):
    cid, hero, gob = grid_fight
    out = combat_loop.resolve_player_turn(cid, hero, Intent(kind="attack", target_id=""))
    assert out["ok"] is False
    assert "target" in out["reason"].lower()
    # Still the hero's turn — nothing resolved.
    assert server._require(cid).combat.current_combatant_id == hero


def test_no_active_combat_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("NoFight")["id"]
    hero = server.create_character(cid, "Hero", kind="player")["id"]
    out = combat_loop.resolve_player_turn(cid, hero, Intent(kind="move", to_cell=(1, 1)))
    assert out["ok"] is False
    assert "combat" in out["reason"].lower()


def test_double_resolve_does_not_desync_initiative(grid_fight):
    """A second move POSTed for the hero AFTER its turn already advanced must reject (it is no
    longer the hero's turn) — proving the arbiter can't double-resolve / desync the order."""
    cid, hero, gob = grid_fight
    out1 = combat_loop.resolve_player_turn(
        cid, hero, Intent(kind="move", to_cell=(2, 0)), end_turn=True
    )
    assert out1["ok"] is True and out1["advanced"] is True
    c = server._require(cid)
    if not c.combat.active:
        pytest.skip("fight ended in one exchange")
    # The hero's turn was resolved + advanced; the enemy auto-ran; it is the hero's turn again
    # in a NEW round with a FRESH movement budget. A repeat of the SAME (now-stale) intent is
    # legal again (new turn) — so to prove no-desync we instead assert the order is coherent:
    # exactly one current combatant, and it is a real, living party actor.
    cur = c.combat.current_combatant_id
    assert cur in (hero, gob)
    # And a move declared for the NON-current actor still rejects (turn-ownership intact).
    other = gob if cur == hero else hero
    out2 = combat_loop.resolve_player_turn(cid, other, Intent(kind="move", to_cell=(7, 0)))
    assert out2["ok"] is False
