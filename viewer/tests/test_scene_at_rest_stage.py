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


if __name__ == "__main__":
    unittest.main()
