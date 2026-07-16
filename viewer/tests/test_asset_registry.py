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
        # clipless Assets/painterly/models/hero.fbx placeholder). anim_ref stays
        # null -- its own embedded Idle clip is used. #1423: albedo_ref was ALSO
        # null here, but that was a real gap (not an intentional "own material"
        # convention) -- fighter.fbx's own imported material has no texture bound
        # at all, so the Unity renderer's registry-miss fallback was silently
        # substituting the DEFAULT TEMPLATE's hero_albedo.png (a different mesh's
        # UVs) onto it. Fixed by extracting the real albedo from fighter's source
        # Meshy model.glb (extract_glb_albedo.py) and wiring it here.
        self.assertEqual(r["model_ref"], "Assets/cast/fighter/fighter.fbx")
        self.assertEqual(r["albedo_ref"], "Assets/cast/fighter/albedo.jpg")

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

    # -- #1601 anti-T-pose invariants ------------------------------------
    # A runtime-spawned actor is resolved by its in-game NAME (slugified), so a
    # rogue like 'Gauge'/'Sable' is neither an asset nor an alias key and falls to
    # defaults.character. That floor MUST be an animated humanoid, never the
    # clipless hero.fbx template (isHuman=false, no resolvable idle) that rendered
    # the reported sideways T-pose. These lints keep the floor animatable so a
    # T-pose can never re-enter through a registry regression.

    _TPOSE_MODEL = "Assets/painterly/models/hero.fbx"

    def _assert_animatable_character(self, r, ctx):
        """A resolved character ref that can NEVER be a T-pose: has a model, is not
        flagged needs_remodel, and can actually play an idle — either a valid
        humanoid avatar (controller-driven) OR a separate idle moveset (anim_ref,
        the per-frame idle-graph fallback path)."""
        self.assertIsNotNone(r.get("model_ref"), "%s: no model_ref" % ctx)
        self.assertNotEqual(
            r.get("model_ref"), self._TPOSE_MODEL,
            "%s: resolves to the clipless T-pose template hero.fbx" % ctx,
        )
        self.assertNotEqual(
            r.get("needs_remodel"), True,
            "%s: resolves to a needs_remodel asset (T-pose risk)" % ctx,
        )
        self.assertTrue(
            r.get("humanoid") is True or bool(r.get("anim_ref")),
            "%s: resolved asset has no humanoid avatar and no idle moveset -> T-pose" % ctx,
        )

    def test_reported_rogues_gauge_and_sable_animate(self):
        # The two #1601 repros: names that match no asset/alias -> character floor.
        for name in ("Gauge", "Sable", "gauge", "sable"):
            r = self.reg.resolve(name.lower(), "character")
            self.assertEqual(r["asset_id"], "template_human")
            self._assert_animatable_character(r, "rogue %r" % name)

    def test_arbitrary_character_names_never_tpose(self):
        for name in ("nobody", "a-strange-name", "rogue", "cleric", "ranger", ""):
            r = self.reg.resolve(name, "character")
            self._assert_animatable_character(r, "character %r" % name)

    def test_unknown_kind_floor_is_animatable(self):
        # __any__ also points at the character floor; it must animate too.
        self._assert_animatable_character(
            self.reg.resolve("mystery", "tarot"), "__any__ floor",
        )

    def test_in_code_floor_is_animatable(self):
        # Registry missing/corrupt -> _HARDCODED_FLOOR. It must NOT be hero.fbx.
        reg = AssetRegistry(path="/nonexistent/path/registry.json")
        self._assert_animatable_character(
            reg.resolve("anything", "character"), "in-code floor",
        )

    def test_every_default_and_alias_target_resolves(self):
        import json as _json
        raw = _json.loads(_REGISTRY_JSON.read_text(encoding="utf-8"))
        assets = raw.get("assets", {})
        for kind, target in raw.get("defaults", {}).items():
            self.assertIn(target, assets, "defaults[%r] -> missing asset %r" % (kind, target))
        for alias, target in raw.get("aliases", {}).items():
            self.assertIn(target, assets, "alias %r -> missing asset %r" % (alias, target))
        # Every character/monster asset row names a model (sound rows legitimately
        # carry only anim_ref) -> the renderer always has something to instantiate.
        for aid, row in assets.items():
            if row.get("kind") in ("character", "monster"):
                self.assertIsNotNone(row.get("model_ref"), "asset %r has no model_ref" % aid)

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
