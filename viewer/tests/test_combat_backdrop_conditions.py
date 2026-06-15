"""Server-side contract tests backing two combat-screen felt-gap fixes.

These assert the SURFACE FIELDS the viewer JSX renders (the JSX render itself is
not unit-tested here):

  FIX A — Combat scene backdrop: build_combat_surface must expose a servable
    scene image scope on the location block, mirroring the proven session/catalog
    pattern (`location:<id>` when a location exists, "" when it does not). This is
    the data the CombatMap absolute-inset cover image binds to.

  FIX B — Condition chips on tokens: each combat token must carry its `conditions`
    list through to the surface (a no-op empty list when the combatant has none).
    This is the data CombatToken renders as small badge chips.

Both fixes are ADDITIVE. Each assertion below doubles as a revert-check: delete the
viewer wiring and the matching assertion fails, so a future regression that drops
the field is caught here rather than silently shipping a blank backdrop / bare token.
"""

import importlib.util
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


def _surface_with_location() -> dict:
    snapshot = {
        "id": "camp_backdrop",
        "title": "Ambush at the Gate",
        "current_location_id": "gate",
        "locations": {
            "gate": {
                "name": "Basilisk Gate",
                "description": "A rain-slick gatehouse with overturned carts.",
            },
        },
        "party": ["hero"],
        "characters": {
            "hero": {
                "id": "hero",
                "name": "Tav",
                "kind": "player",
                "current_hp": 18,
                "max_hp": 24,
                "armor_class": 17,
                "conditions": ["blessed", "hasted"],
            },
            "gob": {
                "id": "gob",
                "name": "Goblin Sapper",
                "kind": "monster",
                "current_hp": 3,
                "max_hp": 7,
                "conditions": ["prone"],
            },
            "ogre": {
                "id": "ogre",
                "name": "Ogre Brute",
                "kind": "monster",
                "current_hp": 30,
                "max_hp": 30,
            },
        },
        "combat": {
            "active": True,
            "round": 2,
            "turn_index": 0,
            "order": [
                {"character_id": "hero", "initiative": 18, "zone": "gate"},
                {"character_id": "gob", "initiative": 9, "zone": "gate"},
                {"character_id": "ogre", "initiative": 6, "zone": "gate"},
            ],
        },
    }
    return server.build_combat_surface(
        snapshot,
        campaign_id="camp_backdrop",
        live=True,
        is_live_view=True,
    )


class CombatBackdropScopeTests(unittest.TestCase):
    """FIX A — the combat surface exposes a servable scene image scope."""

    def test_location_block_carries_servable_image_scope(self):
        surface = _surface_with_location()
        # Mirrors the proven session-surface (server.py:2069) + catalog (server.py:5597)
        # `location:<id>` pattern that the _safe_scope bridge resolves to ingested art.
        self.assertEqual(surface["location"]["imageScope"], "location:gate")

    def test_image_scope_matches_location_id(self):
        surface = _surface_with_location()
        loc = surface["location"]
        # The scope must be derived from the SAME id the rest of the surface uses,
        # so the backdrop and the encounter header agree on the place.
        self.assertEqual(loc["imageScope"], f"location:{loc['id']}")

    def test_image_scope_empty_string_when_no_location(self):
        # No current_location_id / locations -> id is "" -> scope degrades to "".
        # CombatMap then renders no backdrop (transparent) rather than a broken scope.
        surface = server.build_combat_surface(
            {"title": "Quiet Camp", "party": [], "characters": {}},
            campaign_id="camp_void",
            live=True,
            is_live_view=True,
        )
        self.assertEqual(surface["location"]["imageScope"], "")

    def test_image_scope_is_additive_existing_location_fields_preserved(self):
        surface = _surface_with_location()
        loc = surface["location"]
        # Every pre-existing location field must survive byte-for-byte alongside the
        # new imageScope key (wire-break guard for the additive invariant).
        self.assertEqual(loc["id"], "gate")
        self.assertEqual(loc["name"], "Basilisk Gate")
        self.assertEqual(loc["region"], "Basilisk Gate")
        self.assertEqual(
            loc["description"], "A rain-slick gatehouse with overturned carts."
        )


class CombatTokenConditionsTests(unittest.TestCase):
    """FIX B — combat tokens carry their conditions list through to the surface."""

    def test_tokens_carry_conditions_list(self):
        surface = _surface_with_location()
        by_id = {t["id"]: t for t in surface["tokens"]}
        # Ally conditions flow through verbatim (CombatToken renders these as chips).
        self.assertEqual(by_id["hero"]["conditions"], ["blessed", "hasted"])
        # Foe conditions also flow through (observable status, not hidden stats).
        self.assertEqual(by_id["gob"]["conditions"], ["prone"])

    def test_conditions_is_empty_list_no_op_when_absent(self):
        surface = _surface_with_location()
        by_id = {t["id"]: t for t in surface["tokens"]}
        # A combatant with no conditions yields [] (never missing/None) so the JSX
        # no-ops cleanly — the chip row renders nothing.
        self.assertEqual(by_id["ogre"]["conditions"], [])

    def test_conditions_is_always_a_list_of_strings(self):
        surface = _surface_with_location()
        for token in surface["tokens"]:
            self.assertIsInstance(token["conditions"], list)
            for cond in token["conditions"]:
                self.assertIsInstance(cond, str)


if __name__ == "__main__":
    unittest.main()
