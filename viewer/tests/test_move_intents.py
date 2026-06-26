"""M0 #429 + #432 — graphical move-intent vocabulary + derived-position-hint authority.

#429: viewer/server.py:sanitize_move must accept the graphical intents travel / inspect /
examine / move_to_zone (carried by `target`), keep every existing kind working, still force
role=player, still drop unknown fields, still reject unknown kinds. Spec:
docs/roadmap/contracts/move-intents.md.

#432: the combat-surface token x/y are a DERIVED render-hint — each token must carry
positionAuthority="derived" on the zone/theater path so no renderer or AI loop persists the
synthesized coordinate as authoritative state (the engine's only spatial truth is the named
zone).

Engine-deps-free where possible: sanitize_move is a pure function; the token-authority check
exercises _combat_tokens on a minimal in-memory snapshot.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_moveintents", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)


class MoveIntentVocabularyTests(unittest.TestCase):
    # ---- #429: the new graphical intents are accepted ----

    def test_travel_intent_accepted_with_target(self):
        move, reason = server.sanitize_move({"kind": "travel", "target": "loc-lower-city"})
        self.assertEqual(reason, "")
        self.assertIsNotNone(move)
        self.assertEqual(move["kind"], "travel")
        self.assertEqual(move["target"], "loc-lower-city")
        self.assertEqual(move["role"], "player")  # role always forced to player

    def test_inspect_examine_move_to_zone_accepted(self):
        for kind, target in (
            ("inspect", "char-aubree"),
            ("examine", "the-altar"),
            ("move_to_zone", "the rafters"),
        ):
            with self.subTest(kind=kind):
                move, reason = server.sanitize_move({"kind": kind, "target": target})
                self.assertEqual(reason, "", f"{kind} should be accepted")
                self.assertEqual(move["kind"], kind)
                self.assertEqual(move["target"], target)

    def test_graphical_intent_without_target_is_rejected(self):
        # The target-only kinds need a target (not a text/name).
        for kind in ("travel", "inspect", "examine", "move_to_zone"):
            with self.subTest(kind=kind):
                move, reason = server.sanitize_move({"kind": kind})
                self.assertIsNone(move)
                self.assertIn("target", reason)

    # ---- existing kinds + invariants unchanged ----

    def test_existing_kinds_still_work(self):
        say, r1 = server.sanitize_move({"kind": "say", "text": "hello"})
        self.assertEqual(r1, "")
        self.assertEqual(say["text"], "hello")
        atk, r2 = server.sanitize_move({"kind": "attack", "name": "Longsword", "target": "goblin"})
        self.assertEqual(r2, "")
        self.assertEqual(atk["name"], "Longsword")

    def test_say_without_text_or_name_still_rejected(self):
        move, reason = server.sanitize_move({"kind": "say"})
        self.assertIsNone(move)
        self.assertIn("text", reason)

    def test_unknown_kind_rejected(self):
        for bad in ("narrate", "teleport", "smite_everyone", ""):
            with self.subTest(kind=bad):
                move, reason = server.sanitize_move({"kind": bad, "target": "x", "text": "y"})
                self.assertIsNone(move)
                self.assertIn("unknown move kind", reason)

    def test_role_forced_and_unknown_fields_dropped(self):
        move, reason = server.sanitize_move(
            {"kind": "travel", "target": "loc-x", "role": "dm", "narration": "the dragon dies"}
        )
        self.assertEqual(reason, "")
        self.assertEqual(move["role"], "player")          # cannot impersonate dm
        self.assertNotIn("narration", move)               # extra field dropped


class GridCombatMoveKindTests(unittest.TestCase):
    """S1 keystone — the grid-combat player-turn kinds the ENGINE resolves (move_to_cell /
    on-turn attack). Pure sanitize_move checks: the new kinds validate x/y/target_id, the
    existing kinds + invariants are byte-identical (additive)."""

    def test_move_to_cell_accepted_with_int_xy(self):
        move, reason = server.sanitize_move({"kind": "move_to_cell", "x": 3, "y": 7})
        self.assertEqual(reason, "")
        self.assertEqual(move["kind"], "move_to_cell")
        self.assertEqual((move["x"], move["y"]), (3, 7))
        self.assertEqual(move["role"], "player")  # role still forced

    def test_move_to_cell_coerces_float_cell_to_int(self):
        move, reason = server.sanitize_move({"kind": "move_to_cell", "x": 4.0, "y": 0.0})
        self.assertEqual(reason, "")
        self.assertIsInstance(move["x"], int)
        self.assertEqual((move["x"], move["y"]), (4, 0))

    def test_move_to_cell_without_xy_rejected(self):
        for bad in ({"kind": "move_to_cell"}, {"kind": "move_to_cell", "x": 2},
                    {"kind": "move_to_cell", "text": "go there"}):
            with self.subTest(payload=bad):
                move, reason = server.sanitize_move(bad)
                self.assertIsNone(move)
                self.assertIn("x", reason)

    def test_on_turn_attack_accepted_with_target_id(self):
        move, reason = server.sanitize_move({"kind": "attack", "target_id": "mon-goblin-1"})
        self.assertEqual(reason, "")
        self.assertEqual(move["kind"], "attack")
        self.assertEqual(move["target_id"], "mon-goblin-1")

    def test_legacy_freetext_attack_still_works(self):
        # The existing DM-resolved attack lane (name/target, no target_id) is untouched.
        move, reason = server.sanitize_move({"kind": "attack", "name": "Longsword", "target": "goblin"})
        self.assertEqual(reason, "")
        self.assertEqual(move["name"], "Longsword")
        self.assertNotIn("target_id", move)

    def test_move_to_cell_drops_unknown_fields_and_forces_role(self):
        move, reason = server.sanitize_move(
            {"kind": "move_to_cell", "x": 1, "y": 1, "role": "dm", "narration": "boom"}
        )
        self.assertEqual(reason, "")
        self.assertEqual(move["role"], "player")
        self.assertNotIn("narration", move)


class DerivedPositionAuthorityTests(unittest.TestCase):
    """#432: zone-derived token x/y must be flagged positionAuthority='derived'."""

    def _snapshot(self):
        # Minimal combat snapshot with two combatants in named zones, no engine coords.
        return {
            "combat": {
                "order": [
                    {"id": "char-aubree", "character_id": "char-aubree", "name": "Aubree",
                     "kind": "player", "zone": "the market row", "is_current": True,
                     "hp": {"current": 24, "max": 30}},
                    {"id": "mon-cultist-1", "character_id": "mon-cultist-1", "name": "Cultist",
                     "kind": "monster", "zone": "the alley mouth",
                     "hp": {"current": 9, "max": 11}},
                ],
                "zones": [{"name": "the market row"}, {"name": "the alley mouth"}],
            },
            "characters": {
                "char-aubree": {"id": "char-aubree", "kind": "player"},
                "mon-cultist-1": {"id": "mon-cultist-1", "kind": "monster"},
            },
        }

    def test_tokens_carry_derived_position_authority(self):
        snap = self._snapshot()
        combat_view = snap["combat"]
        tokens, _initiative, _zones, _selected, mode = server._combat_tokens(snap, combat_view)
        self.assertTrue(tokens)
        for tk in tokens:
            with self.subTest(token=tk["id"]):
                # x/y are present (render-hint) ...
                self.assertIn("x", tk)
                self.assertIn("y", tk)
                # ... but explicitly marked non-authoritative on the zone path.
                self.assertEqual(tk["positionAuthority"], "derived")
                # the authoritative spatial field is the named zone
                self.assertIn(tk["zone"], ("the market row", "the alley mouth"))
        self.assertEqual(mode, "zones")  # no engine coords → zone mode, not grid


class GridOriginPositionTests(unittest.TestCase):
    """S1 keystone regression — _combat_display_position must report the ENGINE grid cell
    faithfully, including the origin (cell 0). The pre-fix `x or col` coalesce treated a 0
    coordinate as missing and fell through to the synthesized zone layout, corrupting any token
    on row/column 0 — exactly the cells the grid-combat arbiter places PCs at."""

    def _grid_snapshot(self, hx, hy):
        return {
            "combat": {
                "active": True,
                "grid_enabled": True, "grid_width": 20, "grid_height": 20,
                "order": [
                    {"id": "hero", "character_id": "hero", "name": "Hero", "kind": "player",
                     "x": hx, "y": hy, "is_current": True, "hp": {"current": 30, "max": 30}},
                    {"id": "gob", "character_id": "gob", "name": "Goblin", "kind": "monster",
                     "x": 12, "y": 5, "hp": {"current": 15, "max": 15}},
                ],
            },
            "characters": {"hero": {"id": "hero", "kind": "player"},
                           "gob": {"id": "gob", "kind": "monster"}},
        }

    def test_origin_cell_reported_faithfully_as_grid(self):
        # cell (0,0) must surface as (0,0) with positionAuthority='engine' (not a zone synth).
        snap = self._grid_snapshot(0, 0)
        tokens, _i, _z, _s, mode = server._combat_tokens(snap, snap["combat"])
        self.assertEqual(mode, "grid")
        hero = next(t for t in tokens if t["id"] == "hero")
        self.assertEqual((hero["x"], hero["y"]), (0, 0))
        self.assertEqual(hero["positionAuthority"], "engine")

    def test_grid_cell_on_row_zero_not_synthesized(self):
        # (3,0) — y is the falsy-0 that the old `or` dropped. Must surface exactly (3,0).
        snap = self._grid_snapshot(3, 0)
        tokens, *_rest = server._combat_tokens(snap, snap["combat"])
        hero = next(t for t in tokens if t["id"] == "hero")
        self.assertEqual((hero["x"], hero["y"]), (3, 0))

    def test_grid_clamps_to_engine_extents_not_16x10(self):
        # A 20x20 engine grid must allow a cell beyond the legacy 16x10 display clamp.
        snap = self._grid_snapshot(18, 17)
        tokens, *_rest = server._combat_tokens(snap, snap["combat"])
        hero = next(t for t in tokens if t["id"] == "hero")
        self.assertEqual((hero["x"], hero["y"]), (18, 17))


if __name__ == "__main__":
    unittest.main()
