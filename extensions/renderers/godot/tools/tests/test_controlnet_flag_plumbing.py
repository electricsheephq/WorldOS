#!/usr/bin/env python3
"""Flag-plumbing tests for generate_room.py --controlnet (W6.3b, #1470).

The base/layout pass can now be conditioned on the room greybox as a ControlNet depth|canny control
image (Pipeline A, scenario_gen._cmd_controlnet, proven 2026-06-22) instead of unconditioned img2img.
These tests assert the REQUEST-SHAPE contract WITHOUT any network / credentials (the Scenario helpers
are mocked):

  * absent --controlnet -> BYTE-IDENTICAL img2img body (image + strength) on the recipe base_model,
    so every already-scored plate path is untouched.
  * --controlnet depth|canny -> ControlNet body (controlImage + controlModality + controlStrength),
    NO img2img seed fields, routed to the resolved control model (proven model_bfl-flux-1-dev default,
    overridable via recipe `controlnet` block / --control-model / --control-strength). The z-image
    painterly LoRA is NOT force-applied (flux.1-dev rejects it — HTTP 400 observed 2026-07-10).
  * end-to-end main() (mocked API) posts the right body and records the conditioning in the meta.

Run: python3 -m pytest extensions/renderers/godot/tools/tests/test_controlnet_flag_plumbing.py -q
"""
import argparse
import copy
import os
import sys
import unittest
from unittest import mock

_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import generate_room  # noqa: E402


def _args(**overrides):
    """A defaults-filled Namespace mirroring generate_room's argparse (the knobs the two helpers read)."""
    base = dict(
        room="crypt", base_plate="/tmp/gb.png", refine_from=None, out=None,
        strength=0.45, steps=40, guidance=7.5, num_outputs=4, width=1344, height=768,
        seed=None, lighting="firelit", timeout=600, layered=False, day=False,
        controlnet=None, control_strength=None, control_model=None, dry_run=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class ResolveControlnetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipe = generate_room._load_recipe()

    def test_off_by_default_returns_none(self):
        self.assertIsNone(generate_room._resolve_controlnet(self.recipe, _args()))

    def test_depth_uses_proven_defaults(self):
        cn = generate_room._resolve_controlnet(self.recipe, _args(controlnet="depth"))
        self.assertEqual(cn["modality"], "depth")
        self.assertEqual(cn["model"], generate_room._CONTROLNET_DEFAULT_MODEL)
        self.assertEqual(cn["model"], "model_bfl-flux-1-dev")
        self.assertEqual(cn["strength"], generate_room._CONTROLNET_DEFAULT_STRENGTH)
        # No LoRA inherited by default (the z-image painterly LoRA is flux-incompatible).
        self.assertEqual(cn["loras"], [])

    def test_cli_overrides_win(self):
        cn = generate_room._resolve_controlnet(
            self.recipe, _args(controlnet="canny", control_model="model_custom", control_strength=0.55)
        )
        self.assertEqual(cn["modality"], "canny")
        self.assertEqual(cn["model"], "model_custom")
        self.assertEqual(cn["strength"], 0.55)

    def test_recipe_block_between_default_and_cli(self):
        recipe = copy.deepcopy(self.recipe)
        recipe["controlnet"] = {"model": "model_from_recipe", "control_strength": 0.4,
                                "loras": ["model_flux_lora"], "lora_scales": [0.8]}
        cn = generate_room._resolve_controlnet(recipe, _args(controlnet="depth"))
        self.assertEqual(cn["model"], "model_from_recipe")
        self.assertEqual(cn["strength"], 0.4)
        self.assertEqual(cn["loras"], ["model_flux_lora"])
        # CLI still beats the recipe block for model/strength.
        cn2 = generate_room._resolve_controlnet(
            recipe, _args(controlnet="depth", control_model="model_cli", control_strength=0.9)
        )
        self.assertEqual(cn2["model"], "model_cli")
        self.assertEqual(cn2["strength"], 0.9)

    def test_recipe_lora_scales_length_mismatch_exits(self):
        # Symmetry with scenario_gen's --loras/--loras-scale check and this file's own
        # _resolve_style_pass loras/lorasScale check (evaos-code-review-bot finding).
        recipe = copy.deepcopy(self.recipe)
        recipe["controlnet"] = {"loras": ["model_a", "model_b"], "lora_scales": [0.8]}
        with self.assertRaises(SystemExit):
            generate_room._resolve_controlnet(recipe, _args(controlnet="depth"))

    def test_rejects_z_image_lora_declared_on_a_flux_controlnet_block(self):
        # PLATE SPRINT Phase 3: the same guard scenario_gen.py's --controlnet command applies (a
        # z-image-trained LoRA on a flux model is a live HTTP 400) fires here too, catching a recipe
        # author mistakenly declaring the incompatible combo in room_recipes.json's controlnet block.
        recipe = copy.deepcopy(self.recipe)
        recipe["controlnet"] = {"loras": [recipe["lora"]], "lora_scales": [recipe["lora_scale"]]}
        with self.assertRaises(SystemExit) as ctx:
            generate_room._resolve_controlnet(recipe, _args(controlnet="depth"))
        self.assertIn("REJECTED", str(ctx.exception))


class BuildBasePassRequestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipe = generate_room._load_recipe()
        cls.positive, cls.negative = generate_room._build_prompt(cls.recipe, "crypt")

    def test_img2img_body_byte_identical_when_controlnet_off(self):
        args = _args()
        endpoint, body = generate_room._build_base_pass_request(
            self.recipe, args, self.positive, self.negative, "asset_XYZ", None
        )
        self.assertEqual(
            endpoint,
            generate_room.API_BASE + generate_room.CONTROLNET_PATH.format(model_id=self.recipe["base_model"]),
        )
        self.assertEqual(body, {
            "prompt": self.positive,
            "negativePrompt": self.negative,
            "image": "asset_XYZ",
            "strength": 0.45,
            "numInferenceSteps": 40,
            "guidance": 7.5,
            "numSamples": 4,
            "width": 1344,
            "height": 768,
            "loras": [self.recipe["lora"]],
            "lorasScale": [self.recipe["lora_scale"]],
        })
        # No ControlNet fields ever leak into the default path.
        for k in ("controlImage", "controlModality", "controlStrength"):
            self.assertNotIn(k, body)

    def test_controlnet_body_shape_and_model(self):
        args = _args(controlnet="depth")
        cn = generate_room._resolve_controlnet(self.recipe, args)
        endpoint, body = generate_room._build_base_pass_request(
            self.recipe, args, self.positive, self.negative, "asset_GREY", cn
        )
        # Routed to the resolved control model, NOT the z-image base_model.
        self.assertEqual(
            endpoint,
            generate_room.API_BASE + generate_room.CONTROLNET_PATH.format(model_id="model_bfl-flux-1-dev"),
        )
        self.assertEqual(body["controlImage"], "asset_GREY")
        self.assertEqual(body["controlModality"], "depth")
        self.assertEqual(body["controlStrength"], 0.7)
        # img2img seed fields must be ABSENT (this is conditioning, not img2img).
        self.assertNotIn("image", body)
        self.assertNotIn("strength", body)
        # Default control pass carries NO LoRA (flux rejects the z-image painterly LoRA).
        self.assertNotIn("loras", body)
        # Prompt / knobs carried through unchanged.
        self.assertEqual(body["prompt"], self.positive)
        self.assertEqual(body["negativePrompt"], self.negative)
        self.assertEqual(body["numSamples"], 4)

    def test_controlnet_body_includes_declared_compatible_loras(self):
        recipe = copy.deepcopy(self.recipe)
        recipe["controlnet"] = {"loras": ["model_flux_lora"], "lora_scales": [0.8]}
        args = _args(controlnet="canny")
        cn = generate_room._resolve_controlnet(recipe, args)
        _endpoint, body = generate_room._build_base_pass_request(
            recipe, args, self.positive, self.negative, "asset_G", cn
        )
        self.assertEqual(body["loras"], ["model_flux_lora"])
        self.assertEqual(body["lorasScale"], [0.8])

    def test_seed_carried_into_both_shapes(self):
        for cn_arg in (None, "canny"):
            args = _args(controlnet=cn_arg, seed=1234)
            cn = generate_room._resolve_controlnet(self.recipe, args)
            _endpoint, body = generate_room._build_base_pass_request(
                self.recipe, args, self.positive, self.negative, "asset_S", cn
            )
            self.assertEqual(body["seed"], 1234, f"seed dropped for controlnet={cn_arg!r}")


class MainPlumbingTest(unittest.TestCase):
    """Drive main() with every Scenario helper mocked — assert the posted body + written meta."""

    def _run_main(self, argv):
        captured = {}

        def fake_post(url, headers, body):
            captured["url"] = url
            captured["body"] = copy.deepcopy(body)
            return {"job": {"jobId": "job_test"}}

        def fake_write_meta(out_dir, meta):
            captured["meta"] = copy.deepcopy(meta)

        with mock.patch.object(generate_room, "_load_credentials", return_value=("k", "s")), \
             mock.patch.object(generate_room, "_auth_headers", return_value={}), \
             mock.patch.object(generate_room, "_upload_image", return_value="asset_UPLOADED"), \
             mock.patch.object(generate_room, "_post_json", side_effect=fake_post), \
             mock.patch.object(generate_room, "_job_id_from_create", return_value="job_test"), \
             mock.patch.object(generate_room, "_poll_job", return_value={"job": {}}), \
             mock.patch.object(generate_room, "_download_job_assets",
                               return_value=[{"asset_id": "a1", "path": "/tmp/a1.png", "bytes": 1}]), \
             mock.patch.object(generate_room, "_write_meta", side_effect=fake_write_meta), \
             mock.patch.object(generate_room, "_maybe_run_drift_gate", return_value=None):
            # _maybe_run_drift_gate is mocked here: these tests assert REQUEST-SHAPE plumbing, not the
            # drift gate (which is ON by default for crypt/camp_clearing_night — see
            # test_drift_gate_default_on.py for its dedicated coverage) and the fake downloaded paths
            # above (/tmp/a1.png) don't exist on disk for it to check.
            generate_room.main(argv)
        return captured

    def test_default_path_posts_img2img_body(self):
        cap = self._run_main(["--room", "crypt", "--base-plate", "/tmp/gb.png", "--out", "/tmp/o"])
        self.assertTrue(cap["url"].endswith("/generate/custom/model_z-image"))
        self.assertEqual(cap["body"]["image"], "asset_UPLOADED")
        self.assertIn("strength", cap["body"])
        self.assertNotIn("controlImage", cap["body"])
        self.assertEqual(cap["meta"]["source"], "generate_room-img2img")
        self.assertNotIn("controlnet", cap["meta"])

    def test_controlnet_path_posts_conditioned_body_and_meta(self):
        cap = self._run_main(
            ["--room", "camp_clearing_night", "--base-plate", "/tmp/gb.png",
             "--controlnet", "depth", "--out", "/tmp/o"]
        )
        self.assertTrue(cap["url"].endswith("/generate/custom/model_bfl-flux-1-dev"))
        self.assertEqual(cap["body"]["controlImage"], "asset_UPLOADED")
        self.assertEqual(cap["body"]["controlModality"], "depth")
        self.assertEqual(cap["body"]["controlStrength"], 0.7)
        self.assertNotIn("image", cap["body"])
        self.assertEqual(cap["meta"]["source"], "generate_room-controlnet")
        self.assertEqual(cap["meta"]["controlnet"]["modality"], "depth")
        self.assertEqual(cap["meta"]["controlnet"]["control_model"], "model_bfl-flux-1-dev")
        self.assertEqual(cap["meta"]["controlnet"]["control_image_ref"], "asset_UPLOADED")


if __name__ == "__main__":
    unittest.main()
