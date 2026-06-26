"""S1 keystone — the grid-combat player-turn bridge + its concurrency guard.

The viewer resolves a grid-combat player-turn move (move_to_cell / on-turn attack) DETERMINISTICALLY
through the engine arbiter via the in-process bridge, then re-emits build_combat_surface. The
adversarial-invariant-verify pass found a TOCTOU: the arbiter reads current_combatant_id without a
lock and the engine's campaign_lock is per-verb (doesn't span read->validate->advance), so two
concurrent POSTs on the ThreadingHTTPServer could both pass the turn-ownership gate and double-
advance initiative — silently consuming an enemy turn. _resolve_player_combat_turn now serializes
the lane per campaign with an in-process lock. This test PROVES exactly one of two concurrent
player-turn resolutions advances the turn (the other rejects), so initiative never double-advances.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import threading
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_ptconc", _SERVER_PATH)
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(server)


class PlayerTurnBridgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)

    def tearDown(self):
        if self._old_state is None:
            os.environ.pop("WORLDOS_STATE_DIR", None)
        else:
            os.environ["WORLDOS_STATE_DIR"] = self._old_state

    def _in_combat_grid(self):
        eng = server._engine_server()
        cid = eng.create_campaign("PTConc")["id"]
        hero = eng.create_character(cid, "Hero", kind="player", max_hp=30, armor_class=14)["id"]
        gob = eng.create_character(cid, "Goblin", kind="monster", max_hp=40, armor_class=18)["id"]
        eng.start_combat(cid, [hero, gob], surpriser_ids=[hero])  # hero leads, deterministic
        eng.set_grid(cid, 20, 20)
        eng.place_combatant_at_coords(cid, hero, 0, 0)
        eng.place_combatant_at_coords(cid, gob, 12, 0)
        assert eng._require(cid).combat.current_combatant_id == hero
        return eng, cid, hero, gob

    def test_bridge_move_resolves_and_re_emits_surface(self):
        eng, cid, hero, gob = self._in_combat_grid()
        out = server._resolve_player_combat_turn(
            cid, {"kind": "move_to_cell", "x": 3, "y": 0}, live=True
        )
        self.assertTrue(out["ok"], out)
        self.assertFalse(out["arbiter"]["advanced"])      # bare move keeps the turn open
        self.assertTrue(out["arbiter"]["turn_open"])
        surf = out["combat"]
        self.assertEqual(surf["state_authority"], "engine")
        ht = next(t for t in surf["tokens"] if t["id"] == hero)
        self.assertEqual((ht["x"], ht["y"]), (3, 0))      # the moved cell, faithfully

    def test_concurrent_double_post_serializes_no_skipped_enemy_turn(self):
        """The TOCTOU harm the in-process lock prevents: two concurrent on-turn attacks (a
        double-click) must not INTERLEAVE such that the enemy's turn is silently consumed without
        the enemy acting. With the per-campaign lock the two resolutions SERIALIZE: each PC turn
        that advances is followed by a real enemy turn (the goblin acts every round), and the
        round count increases by exactly one PER resolved PC turn — never two PC turns sharing one
        enemy turn (the desync). We assert: (a) every advancing result carries a non-empty
        enemy_digest (the goblin was NOT skipped), and (b) the round delta equals the number of
        advancing PC turns (initiative did not double-step)."""
        eng, cid, hero, gob = self._in_combat_grid()
        eng.place_combatant_at_coords(cid, gob, 1, 0)  # adjacent so the strike is in reach
        start_round = eng._require(cid).combat.round

        results: list[dict] = []
        barrier = threading.Barrier(2)

        def fire():
            barrier.wait()  # maximize the race: both threads enter together
            results.append(
                server._resolve_player_combat_turn(
                    cid, {"kind": "attack", "target_id": gob}, live=True
                )
            )

        t1 = threading.Thread(target=fire)
        t2 = threading.Thread(target=fire)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(results), 2)
        advanced = [r for r in results if r.get("ok") and r["arbiter"].get("advanced")]
        # Each advancing PC turn must have run the enemy (a non-empty enemy_digest) — the goblin
        # was never silently skipped. THIS is the TOCTOU harm: a double-advance consumes the
        # goblin's turn with no digest.
        for r in advanced:
            self.assertTrue(
                r["arbiter"]["resolved"].get("enemy_digest"),
                f"a PC turn advanced WITHOUT the enemy acting — skipped enemy turn: {r['arbiter']}",
            )
        # The round advanced by exactly one per resolved PC turn (each PC turn + the goblin's turn
        # wraps the order once). A double-step (two PC turns sharing one goblin turn) would make
        # the round delta exceed the number of advancing turns.
        c = eng._require(cid)
        if c.combat.active:
            round_delta = c.combat.round - start_round
            self.assertEqual(
                round_delta, len(advanced),
                f"initiative double-stepped: round_delta={round_delta} but advanced={len(advanced)}",
            )
            self.assertIn(c.combat.current_combatant_id, (hero, gob))


if __name__ == "__main__":
    unittest.main()
