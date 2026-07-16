"""W1 (#1318) SCENE-AT-REST — the additive `stage` block on build_combat_surface.

The surface gains an additive `stage` = {mode, tokens}. In REST mode (no active combat) the
tokens are a pure deterministic projection of the party + present NPCs onto scene_grid.spawns
cells; in COMBAT mode the block is {mode:"combat", tokens:[]} so a rest token never leaks onto
the tactical board. Engine stays sole writer; the block is a NEW key only, so every existing
consumer of the combat surface is byte-unchanged.

Invariants asserted here:
  * TEXT-TIER BYTE-IDENTITY — deleting `stage` yields EXACTLY today's payload (same keys, same
    values) for both a rest and a combat snapshot, so existing consumers are unaffected.
  * ADDITIVE WIRE — `stage` is the only new top-level key.
  * PROJECTION CORRECTNESS — an NPC at the current location appears at its spawn cell; an NPC at
    another location does NOT; the party is placed; combat mode carries no stage tokens.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_rest", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


def _surface(snapshot: dict) -> dict:
    return server.build_combat_surface(
        snapshot, campaign_id="c", live=False, is_live_view=False, recent_events=[]
    )


# The full top-level key set the surface carried BEFORE W1 (the "today's payload" contract). If
# build_combat_surface adds/removes a NON-stage key this list must change and the byte-identity
# test below breaks LOUDLY — that is the point: only `stage` may be new.
_LEGACY_KEYS = {
    "campaign_id", "turnToken", "combatFrameScope", "title", "world", "dayLabel", "location",
    "encounter", "grid", "lastPath", "impassable", "doors", "occluders", "tokens", "initiative",
    "zones", "selectedTokenId", "actionEconomy", "commandCenter", "actionBar", "battleLog",
    "live", "is_live_view", "can_act", "state_authority", "write_lane",
    # W4 (#1321) The Living Stage — the clock-driven day/night lighting token. Additive
    # presentation-only key (see build_combat_surface); folded into the established set so the
    # `stage`-is-the-only-NEW-key guard still trips on any UNEXPECTED (non-additive) key change.
    "timePhase",
    # #1582 rest-walk path audit — the most-recent REST walk route (combat.last_walk_path), the
    # additive sibling of lastPath (which stays combat-only). Same deliberate fold-in as timePhase.
    "lastWalkPath",
}


def _tavern_scene_grid(loc_id: str = "loc1") -> dict:
    """A minimal but valid scene_grid carrying party + npc spawn buckets (as the generators emit)."""
    return {
        "scene_id": f"w:{loc_id}",
        "location_id": loc_id,
        "kind": "tavern",
        "grid": {"cols": 14, "rows": 10, "cell_size_ft": 5, "projection": "dimetric-2to1"},
        "cell_default": {"type": "floor", "walkable": True, "cost": 1},
        "cells": [],
        "spawns": {
            "party": [[6, 8], [7, 8], [8, 8]],
            "foes": [[6, 2], [8, 3]],
            "npcs": [[3, 3], [10, 3], [9, 7]],
        },
        "zone_anchors": {"the bar": [3, 2], "the hearth": [11, 2]},
    }


def _rest_snapshot() -> dict:
    return {
        "current_location_id": "loc1",
        "locations": {"loc1": {"name": "The Tavern", "scene_grid": _tavern_scene_grid()}},
        "party": ["pc_hero"],
        "characters": {
            "pc_hero": {"id": "pc_hero", "name": "Aria", "kind": "player", "location_id": "loc1"},
            "npc_keeper": {"id": "npc_keeper", "name": "Innkeeper Bram",
                           "kind": "npc", "location_id": "loc1"},
            "npc_elsewhere": {"id": "npc_elsewhere", "name": "Distant Priest",
                              "kind": "npc", "location_id": "loc2"},
        },
    }


def _combat_snapshot() -> dict:
    snap = _rest_snapshot()
    snap["combat"] = {
        "active": True,
        "turn_index": 0,
        "order": [{"character_id": "pc_hero", "name": "Aria", "kind": "player", "initiative": 12}],
    }
    return snap


class SceneAtRestByteIdentityTests(unittest.TestCase):
    def test_rest_payload_minus_stage_is_todays_keys(self):
        """Deleting `stage` from a rest surface leaves EXACTLY the legacy key set — no existing
        key was added, removed, or renamed (text-tier byte-identity for existing consumers)."""
        surface = _surface(_rest_snapshot())
        self.assertIn("stage", surface)
        without_stage = {k: v for k, v in surface.items() if k != "stage"}
        self.assertEqual(set(without_stage), _LEGACY_KEYS)

    def test_combat_payload_minus_stage_is_todays_keys(self):
        """Same byte-identity guarantee under active combat — the combat board's payload is
        untouched by the additive stage block."""
        surface = _surface(_combat_snapshot())
        without_stage = {k: v for k, v in surface.items() if k != "stage"}
        self.assertEqual(set(without_stage), _LEGACY_KEYS)

    def test_stage_is_the_only_new_key(self):
        surface = _surface(_rest_snapshot())
        self.assertEqual(set(surface) - _LEGACY_KEYS, {"stage"})

    def test_empty_snapshot_is_rest_with_no_tokens(self):
        """A degenerate snapshot (no location/scene_grid) still yields a well-formed additive
        stage — rest mode, empty tokens — and does not perturb the legacy keys."""
        surface = _surface({})
        self.assertEqual(surface["stage"], {"mode": "rest", "tokens": []})
        self.assertEqual(set(surface) - _LEGACY_KEYS, {"stage"})


class SceneAtRestProjectionTests(unittest.TestCase):
    def test_rest_mode_places_party_and_present_npc(self):
        stage = _surface(_rest_snapshot())["stage"]
        self.assertEqual(stage["mode"], "rest")
        by_id = {t["id"]: t for t in stage["tokens"]}
        # The party PC is placed on the first party spawn cell.
        self.assertIn("pc_hero", by_id)
        self.assertEqual((by_id["pc_hero"]["x"], by_id["pc_hero"]["y"]), (6, 8))
        # The present NPC (location_id == current) is placed on the first npc spawn cell.
        self.assertIn("npc_keeper", by_id)
        self.assertEqual((by_id["npc_keeper"]["x"], by_id["npc_keeper"]["y"]), (3, 3))
        # Rest tokens are idle-posed derived hints, never authoritative.
        self.assertEqual(by_id["npc_keeper"]["pose"], "idle")
        self.assertEqual(by_id["npc_keeper"]["positionAuthority"], "derived")
        # W3 (#1363): each rest token carries a rest_role so the board tells a walkable party mover
        # from a click-to-talk NPC target (both are team "ally"). The party PC is "party"; a
        # present non-party NPC is "npc".
        self.assertEqual(by_id["pc_hero"]["rest_role"], "party")
        self.assertEqual(by_id["npc_keeper"]["rest_role"], "npc")

    def test_npc_at_another_location_is_not_placed(self):
        stage = _surface(_rest_snapshot())["stage"]
        ids = {t["id"] for t in stage["tokens"]}
        self.assertNotIn("npc_elsewhere", ids)

    def test_dead_npc_is_never_placed_in_the_rest_scene(self):
        """A corpse never stands at the hearth: a character at the current location whose
        `dead` flag is set is excluded from the rest projection (combat handles the fallen)."""
        snap = _rest_snapshot()
        snap["characters"]["npc_dead"] = {
            "kind": "npc", "name": "Slain Bandit", "location_id": snap["current_location_id"],
            "dead": True,
        }
        stage = _surface(snap)["stage"]
        ids = {t["id"] for t in stage["tokens"]}
        self.assertNotIn("npc_dead", ids)

    def test_dead_party_member_is_never_placed_in_the_rest_scene(self):
        """The corpse-at-the-hearth rule holds for the PARTY branch too: a party member whose
        `dead` flag is set is excluded from the rest projection (the NPC branch already excludes
        the dead; this pins the party branch)."""
        snap = _rest_snapshot()
        snap["party"] = ["pc_hero", "pc_slain"]
        snap["characters"]["pc_slain"] = {
            "id": "pc_slain", "name": "Fallen Kael", "kind": "player",
            "location_id": "loc1", "dead": True,
        }
        stage = _surface(snap)["stage"]
        ids = {t["id"] for t in stage["tokens"]}
        self.assertNotIn("pc_slain", ids)
        self.assertIn("pc_hero", ids)

    def test_downed_but_not_dead_party_member_is_not_stood_up_at_rest(self):
        """Thread PRRT_...G9Xv: a party member at current_hp==0 who is NOT dead (the engine
        models stable/unconscious separately from `dead` — combat.py `_ensure_unconscious`) must
        not be placed as an idle standing rest token either. Covers both the dying (unstable, 0
        HP) and the stabilized (0 HP, stable=True) cases."""
        snap = _rest_snapshot()
        snap["party"] = ["pc_hero", "pc_dying", "pc_stable"]
        snap["characters"]["pc_dying"] = {
            "id": "pc_dying", "name": "Dying Rook", "kind": "player",
            "location_id": "loc1", "current_hp": 0, "dead": False, "stable": False,
        }
        snap["characters"]["pc_stable"] = {
            "id": "pc_stable", "name": "Stabilized Finn", "kind": "player",
            "location_id": "loc1", "current_hp": 0, "dead": False, "stable": True,
        }
        stage = _surface(snap)["stage"]
        ids = {t["id"] for t in stage["tokens"]}
        self.assertNotIn("pc_dying", ids)
        self.assertNotIn("pc_stable", ids)
        self.assertIn("pc_hero", ids)

    def test_downed_but_not_dead_npc_is_not_stood_up_at_rest(self):
        """Same rule for the NPC branch: a present NPC at 0 HP who is not `dead` is excluded."""
        snap = _rest_snapshot()
        snap["characters"]["npc_downed"] = {
            "kind": "npc", "name": "Downed Guard", "location_id": snap["current_location_id"],
            "current_hp": 0, "dead": False,
        }
        stage = _surface(snap)["stage"]
        ids = {t["id"] for t in stage["tokens"]}
        self.assertNotIn("npc_downed", ids)

    def test_fallback_anchor_on_a_blocked_cell_is_skipped(self):
        """Overflow actors fall back to zone_anchors; a zone anchor that sits on a blocking
        prop/wall (anchors are narration-authored, NOT walkable-validated) must be dropped so an
        actor never stands on a column."""
        snap = _rest_snapshot()
        sg = snap["locations"]["loc1"]["scene_grid"]
        # One authored npc cell; two present NPCs -> the 2nd overflows to zone_anchors.
        sg["spawns"]["npcs"] = [[5, 5]]
        # "the bar" anchor (3,2) is a blocking prop cell; "the hearth" (11,2) is walkable floor.
        sg["cells"] = [{"c": 3, "r": 2, "type": "prop", "walkable": False, "prop_ref": "bar"}]
        snap["characters"]["npc_a"] = {"kind": "npc", "name": "Aleph", "location_id": "loc1"}
        snap["characters"]["npc_b"] = {"kind": "npc", "name": "Bet", "location_id": "loc1"}
        stage = _surface(snap)["stage"]
        placed = {(t["x"], t["y"]) for t in stage["tokens"]}
        # No token landed on the blocked bar anchor (3,2).
        self.assertNotIn((3, 2), placed)

    def test_combat_mode_carries_no_stage_tokens(self):
        """No double-paint: under active combat the authoritative tokens are the top-level
        `tokens`; the stage block reports combat mode but places nothing."""
        surface = _surface(_combat_snapshot())
        self.assertEqual(surface["stage"]["mode"], "combat")
        self.assertEqual(surface["stage"]["tokens"], [])
        # ...while the combat board itself still has its tokens (unchanged path).
        self.assertTrue(surface["tokens"])

    def test_projection_is_deterministic(self):
        a = _surface(_rest_snapshot())["stage"]
        b = _surface(_rest_snapshot())["stage"]
        self.assertEqual(a, b)

    def test_companion_present_but_not_in_party_is_placed(self):
        snap = _rest_snapshot()
        snap["characters"]["comp_shade"] = {
            "id": "comp_shade", "name": "Shade", "kind": "companion", "location_id": "loc1",
        }
        stage = _surface(snap)["stage"]
        ids = {t["id"] for t in stage["tokens"]}
        self.assertIn("comp_shade", ids)


class SceneAtRestStageCellTests(unittest.TestCase):
    """W2 (#1350): the rest projection RENDERS a character at its engine-authoritative
    ``Character.stage_cell`` (walk_to's sole-writer field) when set, so a click-to-move walk lands
    the token at the confirmed destination on the next surface reload — instead of snapping it back
    to the authored spawn cell. ADDITIVE: stage_cell is None until a first walk, so an un-walked
    character projects at its spawn cell exactly as before (byte-identical)."""

    def test_walked_pc_renders_at_stage_cell_not_spawn(self):
        snap = _rest_snapshot()
        # pc_hero has WALKED: its engine stage_cell is (2, 3), NOT the party spawn cell (6, 8).
        snap["characters"]["pc_hero"]["stage_cell"] = [2, 3]
        by_id = {t["id"]: t for t in _surface(snap)["stage"]["tokens"]}
        self.assertIn("pc_hero", by_id)
        self.assertEqual((by_id["pc_hero"]["x"], by_id["pc_hero"]["y"]), (2, 3))

    def test_unwalked_pc_still_renders_at_spawn_cell(self):
        """No stage_cell == today: the character falls straight through to the spawn projection."""
        snap = _rest_snapshot()  # pc_hero carries no stage_cell
        by_id = {t["id"]: t for t in _surface(snap)["stage"]["tokens"]}
        self.assertEqual((by_id["pc_hero"]["x"], by_id["pc_hero"]["y"]), (6, 8))

    def test_stage_cell_token_stays_a_derived_hint(self):
        """The engine remains the sole writer: a stage_cell-placed token is still a derived render
        hint, never authoritative — same discipline as the spawn projection."""
        snap = _rest_snapshot()
        snap["characters"]["pc_hero"]["stage_cell"] = [4, 4]
        by_id = {t["id"]: t for t in _surface(snap)["stage"]["tokens"]}
        self.assertEqual(by_id["pc_hero"]["positionAuthority"], "derived")


if __name__ == "__main__":
    unittest.main()
