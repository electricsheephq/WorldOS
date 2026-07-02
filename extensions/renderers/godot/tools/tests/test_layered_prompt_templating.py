#!/usr/bin/env python3
"""Regression tests for the layered pass2/pass3 prompt templating fix (2026-07-03).

DIAGNOSED DEFECT: room_recipes.json's layered_pipeline_2026_07_02.pass2_detail_populate
and .pass3_staging_last prompts were HARDCODED with crypt nouns ("sarcophagus effigy",
"vast dark crypt", etc). generate_room.py --layered applied them unconditionally regardless
of --room, so a tavern pass1 base got silently repainted into a crypt by pass2/pass3.

FIX: the prompts are now `prompt_template` strings with {room_*} slots, filled per-room
from rooms.<room>.layered.pass2_slots / pass3_slots by generate_room._render_pass_prompt.

REGRESSION GUARD (mandatory per the fix spec): crypt's rendered prompts must be BYTE-IDENTICAL
to the prior hardcoded literals, so the adopted/scored crypt path is untouched by this change.

Run: python3 -m pytest extensions/renderers/godot/tools/tests/test_layered_prompt_templating.py -q
(no network / credentials required — pure string-templating + JSON-schema checks)
"""
import copy
import os
import sys
import unittest

import pytest

_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import generate_room  # noqa: E402


# Byte-identical to the pre-2026-07-03 hardcoded literals in room_recipes.json
# (layered_pipeline_2026_07_02.pass2_detail_populate.prompt / pass3_staging_last.prompt,
# as they existed before this fix — copied verbatim for the regression assertion).
LEGACY_CRYPT_PASS2_PROMPT = (
    "Edit this painterly isometric dungeon plate. PRESERVE the exact composition, camera, "
    "room layout, and the dark dramatic lighting with its warm fire pools and deep shadows "
    "— do not brighten the scene. IMPROVE only the craft: re-sculpt every carved relief, "
    "capital, and the sarcophagus effigy so they read CRISPLY CHISELED with clean edge "
    "highlights; make every pillar and wall panel UNIQUE (remove cloned repeats); paint any "
    "flat gray or unfinished surfaces as weathered stone; add small environmental-storytelling "
    "clutter along walls and edges only (bones, fallen weapons, cobwebs, toppled urns, "
    "scattered coins, moss) keeping the open floor areas clear; deepen the black voids at the "
    "openings. Keep the hand-painted oil-paint brushwork feel throughout. Do NOT add any text, "
    "letters, numbers, runic writing that reads as text, labels, legends, map insets, UI "
    "elements, frames, or borders — the plate must contain ONLY the diegetic painted scene."
)

LEGACY_CRYPT_PASS3_PROMPT = (
    "Edit this painterly isometric dungeon plate. PRESERVE every detail exactly — the room "
    "layout, all carved reliefs, props, bones, coins, cobwebs, pillars, the sarcophagus — "
    "change NOTHING about the content or composition. ONLY restage the LIGHTING into dramatic "
    "chiaroscuro: make the central fire pit the dominant warm key light with a bright hot pool "
    "around it, let the wall torches each cast a small local warm pool with HARD directional "
    "cast shadows from the pillars and props, and sink everything between the pools into deep "
    "cool blue-violet shadow — corners, far rooms, and floors away from lights should fall "
    "near-black. Steep warm-to-cool falloff, warm rim light on edges facing flames, cool bounce "
    "on shadow-side stone. The scene must read as small pools of firelight in a vast dark "
    "crypt, keeping the hand-painted oil feel. Do NOT add any text, letters, numbers, runic "
    "writing that reads as text, labels, legends, map insets, UI elements, frames, or borders "
    "— the plate must contain ONLY the diegetic painted scene."
)

# Nouns that must NOT leak into a tavern's rendered pass2/pass3 prompts (crypt-specific, not
# generic clutter vocabulary that could legitimately appear in multiple rooms).
CRYPT_ONLY_NOUNS = [
    "sarcophagus", "crypt", "dungeon", "fire pit",
]

# Nouns that SHOULD appear in tavern's rendered prompts (drawn from tavern's own
# room_detail_tokens / layered slot vocabulary).
TAVERN_NOUNS_PASS2 = ["tavern", "bar counter", "hearth", "timber"]
TAVERN_NOUNS_PASS3 = ["tavern", "hearth", "lantern"]


class LayeredPromptTemplatingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.recipe = generate_room._load_recipe()
        cls.layered = cls.recipe["layered_pipeline_2026_07_02"]

    # --- regression guard: crypt byte-identical to the pre-fix hardcoded prompt ---

    def test_crypt_pass2_prompt_byte_identical_to_legacy_hardcoded(self):
        rendered = generate_room._render_pass_prompt(
            self.recipe, "crypt", self.layered["pass2_detail_populate"], "pass2_slots"
        )
        self.assertEqual(rendered, LEGACY_CRYPT_PASS2_PROMPT)

    def test_crypt_pass3_prompt_byte_identical_to_legacy_hardcoded(self):
        rendered = generate_room._render_pass_prompt(
            self.recipe, "crypt", self.layered["pass3_staging_last"], "pass3_slots"
        )
        self.assertEqual(rendered, LEGACY_CRYPT_PASS3_PROMPT)

    # --- defect guard: tavern must render tavern content, never crypt nouns ---

    def test_tavern_pass2_prompt_contains_tavern_nouns(self):
        rendered = generate_room._render_pass_prompt(
            self.recipe, "tavern", self.layered["pass2_detail_populate"], "pass2_slots"
        )
        lowered = rendered.lower()
        for noun in TAVERN_NOUNS_PASS2:
            self.assertIn(noun, lowered, f"tavern pass2 prompt missing expected noun {noun!r}")

    def test_tavern_pass3_prompt_contains_tavern_nouns(self):
        rendered = generate_room._render_pass_prompt(
            self.recipe, "tavern", self.layered["pass3_staging_last"], "pass3_slots"
        )
        lowered = rendered.lower()
        for noun in TAVERN_NOUNS_PASS3:
            self.assertIn(noun, lowered, f"tavern pass3 prompt missing expected noun {noun!r}")

    def test_tavern_pass2_prompt_contains_no_crypt_nouns(self):
        rendered = generate_room._render_pass_prompt(
            self.recipe, "tavern", self.layered["pass2_detail_populate"], "pass2_slots"
        )
        lowered = rendered.lower()
        for noun in CRYPT_ONLY_NOUNS:
            self.assertNotIn(noun, lowered, f"tavern pass2 prompt leaked crypt noun {noun!r}")

    def test_tavern_pass3_prompt_contains_no_crypt_nouns(self):
        rendered = generate_room._render_pass_prompt(
            self.recipe, "tavern", self.layered["pass3_staging_last"], "pass3_slots"
        )
        lowered = rendered.lower()
        for noun in CRYPT_ONLY_NOUNS:
            self.assertNotIn(noun, lowered, f"tavern pass3 prompt leaked crypt noun {noun!r}")

    def test_tavern_pass2_and_pass3_prompts_differ_from_crypt(self):
        tavern_p2 = generate_room._render_pass_prompt(
            self.recipe, "tavern", self.layered["pass2_detail_populate"], "pass2_slots"
        )
        tavern_p3 = generate_room._render_pass_prompt(
            self.recipe, "tavern", self.layered["pass3_staging_last"], "pass3_slots"
        )
        self.assertNotEqual(tavern_p2, LEGACY_CRYPT_PASS2_PROMPT)
        self.assertNotEqual(tavern_p3, LEGACY_CRYPT_PASS3_PROMPT)

    # --- bosshall recipe presence + wiring ---

    def test_bosshall_room_recipe_exists(self):
        rooms = self.recipe["rooms"]
        self.assertIn("bosshall", rooms)
        rc = rooms["bosshall"]
        for key in ("key_light", "shadow_casters", "room_detail_tokens"):
            self.assertIn(key, rc)
            self.assertTrue(rc[key], f"bosshall.{key} must be non-empty")

    def test_bosshall_layered_slots_present_and_render_without_error(self):
        rc = self.recipe["rooms"]["bosshall"]
        self.assertIn("layered", rc)
        self.assertIn("pass2_slots", rc["layered"])
        self.assertIn("pass3_slots", rc["layered"])
        p2 = generate_room._render_pass_prompt(
            self.recipe, "bosshall", self.layered["pass2_detail_populate"], "pass2_slots"
        )
        p3 = generate_room._render_pass_prompt(
            self.recipe, "bosshall", self.layered["pass3_staging_last"], "pass3_slots"
        )
        self.assertIn("throne", p2.lower())
        self.assertIn("throne", p3.lower())
        # No leftover unfilled {slot} braces after formatting.
        self.assertNotIn("{", p2)
        self.assertNotIn("{", p3)

    def test_bosshall_base_prompt_uses_shared_staging_law_template(self):
        # bosshall must use the same firelit_positive_template/negative machinery as the other
        # rooms (no room-specific pass1 duplication) — build_prompt should succeed cleanly and
        # carry bosshall's own key_light/shadow_casters/room_detail_tokens + the shared
        # staging-law language adopted in #1274 (no-two-columns-identical, wall clutter).
        positive, _negative = generate_room._build_prompt(self.recipe, "bosshall")
        self.assertIn("bosshall", positive)
        self.assertIn("carved throne", positive)
        self.assertIn("no two columns identical", positive.lower())
        self.assertIn("environmental storytelling clutter along the walls", positive.lower())

    # --- every room with a `layered` block must render cleanly (no missing-slot crashes) ---

    def test_every_room_with_layered_block_renders_both_passes(self):
        rooms = self.recipe["rooms"]
        for room, rc in rooms.items():
            if "layered" not in rc:
                continue
            with self.subTest(room=room):
                p2 = generate_room._render_pass_prompt(
                    self.recipe, room, self.layered["pass2_detail_populate"], "pass2_slots"
                )
                p3 = generate_room._render_pass_prompt(
                    self.recipe, room, self.layered["pass3_staging_last"], "pass3_slots"
                )
                self.assertNotIn("{", p2)
                self.assertNotIn("{", p3)

    def test_missing_layered_block_fails_loud_not_silent(self):
        # crypt_stair intentionally omitted here would previously fall through silently to the
        # crypt prompt; now a room with NO layered block must sys.exit with a clear error rather
        # than silently reusing another room's content.
        recipe_copy = copy.deepcopy(self.recipe)
        recipe_copy["rooms"]["_no_layered_room"] = {
            "key_light": "x", "shadow_casters": "y", "room_detail_tokens": "z",
        }
        with pytest.raises(SystemExit):
            generate_room._render_pass_prompt(
                recipe_copy, "_no_layered_room", self.layered["pass2_detail_populate"], "pass2_slots"
            )


if __name__ == "__main__":
    unittest.main()
