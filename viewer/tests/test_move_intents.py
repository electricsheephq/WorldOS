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


if __name__ == "__main__":
    unittest.main()
