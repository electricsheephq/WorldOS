"""Hot-swap conformance test for the view-layer asset registry (gfx M-C, #1195).

This is the LOGIC-level guarantee behind the modular-defaults invariant: the
renderer names a SLOT and the registry ALWAYS hands back a non-null ref — the
real asset on a hit, a default template on a miss — so swapping/regenerating
any asset is ZERO renderer edits.

The resolver is VIEW-LAYER and stdlib-only, so this runs in the viewer-tests CI
lane (`python -m pytest viewer/tests -q -p no:xdist`) with no engine deps.
"""

import os
import sys
import unittest
from pathlib import Path

_VIEWER = Path(__file__).resolve().parents[1]  # .../viewer
if str(_VIEWER) not in sys.path:
    sys.path.insert(0, str(_VIEWER))

import asset_registry  # noqa: E402
from asset_registry import AssetRegistry  # noqa: E402

_REGISTRY_JSON = (
    _VIEWER.parent / "data" / "asset-registry" / "registry.json"
)


class AssetRegistryConformanceTests(unittest.TestCase):
    """exact -> alias -> defaults[kind] -> defaults['__any__'] -> floor."""

    def setUp(self):
        # Pin to the committed registry so the test is hermetic regardless of any
        # WORLDOS_ASSET_REGISTRY env override in the runner.
        self.assertTrue(
            _REGISTRY_JSON.is_file(),
            "committed registry.json missing at %s" % _REGISTRY_JSON,
        )
        self.reg = AssetRegistry(path=str(_REGISTRY_JSON))

    # -- exact hit: real ref, default_used False --------------------------
    def test_exact_hit_returns_real_ref_no_default(self):
        r = self.reg.resolve("fighter", "character")
        self.assertEqual(r["asset_id"], "fighter")
        self.assertEqual(r["resolved_via"], "exact")
        self.assertFalse(r["default_used"])
        # #1418: fighter now points at the real skinned asset (was the stale
        # clipless Assets/painterly/models/hero.fbx placeholder); albedo_ref/anim_ref
        # are null -- the model's own embedded material + embedded Idle clip are used.
        self.assertEqual(r["model_ref"], "Assets/cast/fighter/fighter.fbx")
        self.assertIsNone(r["albedo_ref"])

    def test_goblin_exact_hit_is_monster_real_ref(self):
        r = self.reg.resolve("goblin", "monster")
        self.assertEqual(r["asset_id"], "goblin")
        self.assertEqual(r["resolved_via"], "exact")
        self.assertFalse(r["default_used"])
        self.assertEqual(r["model_ref"], "Assets/chars_v2/goblin/goblin.fbx")

    # -- alias hit: resolves to target asset, default_used True -----------
    def test_alias_resolves_to_target_asset(self):
        r = self.reg.resolve("hero", "character")  # alias -> fighter
        self.assertEqual(r["asset_id"], "fighter")
        self.assertEqual(r["resolved_via"], "alias")
        self.assertTrue(r["default_used"])
        self.assertEqual(r["model_ref"], "Assets/cast/fighter/fighter.fbx")

    # -- miss on a character -> template_human, default:character ---------
    def test_character_miss_falls_to_template_human(self):
        r = self.reg.resolve("nonexistent_npc", "character")
        self.assertEqual(r["asset_id"], "template_human")
        self.assertEqual(r["resolved_via"], "default:character")
        self.assertTrue(r["default_used"])
        self.assertIsNotNone(r["model_ref"])

    # -- miss on a monster -> template_demon -----------------------------
    def test_monster_miss_falls_to_template_demon(self):
        r = self.reg.resolve("kraken", "monster")
        self.assertEqual(r["asset_id"], "template_demon")
        self.assertEqual(r["resolved_via"], "default:monster")
        self.assertTrue(r["default_used"])
        self.assertIsNotNone(r["model_ref"])

    def test_room_and_effect_and_sound_defaults(self):
        self.assertEqual(self.reg.resolve("x", "room")["asset_id"], "template_room_crypt")
        self.assertEqual(self.reg.resolve("x", "effect")["asset_id"], "fx_default_slash")
        self.assertEqual(self.reg.resolve("x", "sound")["asset_id"], "snd_default_hit")

    # -- unknown kind -> __any__ floor -----------------------------------
    def test_unknown_kind_falls_to_any_default(self):
        r = self.reg.resolve("mystery", "tarot")  # 'tarot' is not a known kind
        self.assertEqual(r["asset_id"], "template_human")  # __any__ -> template_human
        self.assertEqual(r["resolved_via"], "floor")
        self.assertTrue(r["default_used"])
        self.assertIsNotNone(r["model_ref"])

    def test_no_kind_falls_to_any_default(self):
        r = self.reg.resolve("mystery")  # kind omitted
        self.assertEqual(r["asset_id"], "template_human")
        self.assertEqual(r["resolved_via"], "floor")
        self.assertTrue(r["default_used"])

    # -- NEVER throws, NEVER returns None --------------------------------
    def test_resolve_never_throws_never_none(self):
        wild_inputs = [
            (None, None),
            ("", ""),
            (None, "character"),
            ("fighter", None),
            ("   ", "monster"),
            (" weird", "room"),
            ("a" * 5000, "effect"),
            (123, 456),  # wrong types — must still degrade gracefully
        ]
        for aid, kind in wild_inputs:
            r = self.reg.resolve(aid, kind)  # must not raise
            self.assertIsNotNone(r, "resolve(%r,%r) returned None" % (aid, kind))
            self.assertIsInstance(r, dict)
            # at least one usable ref so the renderer never spawns nothing
            self.assertTrue(
                r.get("model_ref") is not None or r.get("anim_ref") is not None,
                "resolve(%r,%r) returned a ref with no usable asset" % (aid, kind),
            )

    # -- missing/corrupt registry -> in-code floor, still non-null -------
    def test_missing_registry_degrades_to_floor(self):
        reg = AssetRegistry(path="/nonexistent/path/registry.json")
        r = reg.resolve("anything", "character")
        self.assertIsNotNone(r)
        self.assertEqual(r["resolved_via"], "floor")
        self.assertTrue(r["default_used"])
        self.assertIsNotNone(r["model_ref"])

    # -- module-level convenience uses the same rule ---------------------
    def test_module_level_resolve(self):
        os.environ["WORLDOS_ASSET_REGISTRY"] = str(_REGISTRY_JSON)
        try:
            # fresh singleton path: just assert it returns a valid dict
            r = asset_registry.resolve("fighter", "character")
            self.assertIsNotNone(r)
            self.assertIn("default_used", r)
            self.assertIn("resolved_via", r)
        finally:
            os.environ.pop("WORLDOS_ASSET_REGISTRY", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
