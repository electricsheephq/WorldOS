#!/usr/bin/env python3
"""Flag-plumbing tests for generate_room.py --style-pass (ARM A, PLATE SPRINT).

The ARM A recipe runs a REGISTERED base pass (typically --controlnet depth) and then a SECOND img2img
STYLE pass on the style model (z-image) + the painterly LoRA over that base, re-painting the locked
geometry in the house style at a low denoise strength. These tests assert the REQUEST-SHAPE + wiring
contract WITHOUT any network / credentials (the Scenario helpers are mocked):

  * absent --style-pass -> _resolve_style_pass is None and main() posts exactly ONE body
    (byte-identical to the pre-ARM-A behavior).
  * --style-pass JSON (inline string OR a file path) -> resolved {model,loras,lora_scales,strength}
    defaulting to the recipe painterly recipe (model_z-image + model_MB22… @ 0.78); explicit fields win;
    `lorasScale` and `lora_scales` are both accepted.
  * _build_style_pass_request -> img2img body (image + strength + loras/lorasScale, numSamples=1) on the
    style model, NO ControlNet fields.
  * end-to-end main() (mocked API) with --controlnet depth --style-pass posts TWO bodies (flux control
    base, then z-image style img2img seeded from the base output asset) and records the style_pass meta.

Run: python3 -m pytest extensions/renderers/godot/tools/tests/test_style_pass_flag_plumbing.py -q
"""
import argparse
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import generate_room  # noqa: E402


def _args(**overrides):
    """A defaults-filled Namespace mirroring generate_room's argparse (the knobs the helpers read)."""
    base = dict(
        room="crypt", base_plate="/tmp/gb.png", refine_from=None, out=None,
        strength=0.45, steps=40, guidance=7.5, num_outputs=4, width=1344, height=768,
        seed=None, lighting="firelit", timeout=600, layered=False, day=False,
        controlnet=None, control_strength=None, control_model=None, style_pass=None, dry_run=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class ResolveStylePassTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipe = generate_room._load_recipe()

    def test_off_by_default_returns_none(self):
        self.assertIsNone(generate_room._resolve_style_pass(self.recipe, _args()))

    def test_minimal_json_defaults_to_recipe_painterly(self):
        sp = generate_room._resolve_style_pass(self.recipe, _args(style_pass='{"strength": 0.35}'))
        self.assertEqual(sp["model"], self.recipe["base_model"])
        self.assertEqual(sp["model"], "model_z-image")
        self.assertEqual(sp["loras"], [self.recipe["lora"]])
        self.assertEqual(sp["loras"], ["model_MB22WaRCBLtfhi5R2CRpHoEL"])
        self.assertEqual(sp["lora_scales"], [self.recipe["lora_scale"]])
        self.assertEqual(sp["lora_scales"], [0.78])
        self.assertEqual(sp["strength"], 0.35)

    def test_explicit_fields_win(self):
        payload = json.dumps({"model": "model_custom", "loras": ["model_L1", "model_L2"],
                              "lorasScale": [0.5, 0.6], "strength": 0.25})
        sp = generate_room._resolve_style_pass(self.recipe, _args(style_pass=payload))
        self.assertEqual(sp["model"], "model_custom")
        self.assertEqual(sp["loras"], ["model_L1", "model_L2"])
        self.assertEqual(sp["lora_scales"], [0.5, 0.6])
        self.assertEqual(sp["strength"], 0.25)

    def test_lora_scales_alias_accepted(self):
        sp = generate_room._resolve_style_pass(
            self.recipe, _args(style_pass='{"loras": ["model_X"], "lora_scales": [0.9], "strength": 0.4}')
        )
        self.assertEqual(sp["lora_scales"], [0.9])

    def test_strength_defaults_to_args_strength_when_absent(self):
        sp = generate_room._resolve_style_pass(self.recipe, _args(style_pass="{}", strength=0.42))
        self.assertEqual(sp["strength"], 0.42)

    def test_reads_from_a_json_file_path(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"strength": 0.45}, f)
            path = f.name
        try:
            sp = generate_room._resolve_style_pass(self.recipe, _args(style_pass=path))
            self.assertEqual(sp["strength"], 0.45)
            self.assertEqual(sp["model"], "model_z-image")
        finally:
            os.unlink(path)


class BuildStylePassRequestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipe = generate_room._load_recipe()
        cls.positive, cls.negative = generate_room._build_prompt(cls.recipe, "crypt")

    def test_img2img_body_shape_on_style_model(self):
        args = _args(style_pass='{"strength": 0.35}')
        sp = generate_room._resolve_style_pass(self.recipe, args)
        endpoint, body = generate_room._build_style_pass_request(
            self.recipe, args, self.positive, self.negative, "asset_BASE", sp
        )
        self.assertEqual(
            endpoint,
            generate_room.API_BASE + generate_room.CONTROLNET_PATH.format(model_id="model_z-image"),
        )
        self.assertEqual(body["image"], "asset_BASE")
        self.assertEqual(body["strength"], 0.35)
        self.assertEqual(body["loras"], [self.recipe["lora"]])
        self.assertEqual(body["lorasScale"], [self.recipe["lora_scale"]])
        # Exactly one final styled plate per config (the reject-retry candidate).
        self.assertEqual(body["numSamples"], 1)
        self.assertEqual(body["prompt"], self.positive)
        self.assertEqual(body["negativePrompt"], self.negative)
        # This is img2img, never ControlNet conditioning.
        for k in ("controlImage", "controlModality", "controlStrength"):
            self.assertNotIn(k, body)

    def test_seed_carried_when_set(self):
        args = _args(style_pass='{"strength": 0.35}', seed=99)
        sp = generate_room._resolve_style_pass(self.recipe, args)
        _endpoint, body = generate_room._build_style_pass_request(
            self.recipe, args, self.positive, self.negative, "asset_B", sp
        )
        self.assertEqual(body["seed"], 99)


class MainPlumbingTest(unittest.TestCase):
    """Drive main() with every Scenario helper mocked — assert the posted bodies + the style_pass meta."""

    def _run_main(self, argv):
        captured = {"posts": [], "meta": None}
        counter = {"n": 0}

        def fake_post(url, headers, body):
            captured["posts"].append({"url": url, "body": copy.deepcopy(body)})
            return {"job": {"jobId": "job_test"}}

        def fake_download(headers, job, out_dir, stem):
            # Distinct asset per call so the style pass's input_ref (base output) is verifiable.
            counter["n"] += 1
            return [{"asset_id": "asset_%d" % counter["n"], "path": "/tmp/a%d.png" % counter["n"], "bytes": 1}]

        def fake_write_meta(out_dir, meta):
            captured["meta"] = copy.deepcopy(meta)

        with mock.patch.object(generate_room, "_load_credentials", return_value=("k", "s")), \
             mock.patch.object(generate_room, "_auth_headers", return_value={}), \
             mock.patch.object(generate_room, "_upload_image", return_value="asset_UPLOADED"), \
             mock.patch.object(generate_room, "_post_json", side_effect=fake_post), \
             mock.patch.object(generate_room, "_job_id_from_create", return_value="job_test"), \
             mock.patch.object(generate_room, "_poll_job", return_value={"job": {}}), \
             mock.patch.object(generate_room, "_download_job_assets", side_effect=fake_download), \
             mock.patch.object(generate_room, "_downscale_to_plate", return_value=None), \
             mock.patch.object(generate_room, "_write_meta", side_effect=fake_write_meta):
            generate_room.main(argv)
        return captured

    def test_no_style_pass_posts_single_body_and_no_meta(self):
        cap = self._run_main(["--room", "crypt", "--base-plate", "/tmp/gb.png",
                              "--controlnet", "depth", "--out", "/tmp/o"])
        self.assertEqual(len(cap["posts"]), 1)
        self.assertNotIn("style_pass", cap["meta"])

    def test_controlnet_base_then_style_pass_posts_two_bodies(self):
        cap = self._run_main(
            ["--room", "crypt", "--base-plate", "/tmp/gb.png", "--controlnet", "depth",
             "--style-pass", '{"strength": 0.35}', "--out", "/tmp/o"]
        )
        self.assertEqual(len(cap["posts"]), 2)
        base_post, style_post = cap["posts"]
        # Base pass = flux ControlNet conditioning.
        self.assertTrue(base_post["url"].endswith("/generate/custom/model_bfl-flux-1-dev"))
        self.assertEqual(base_post["body"]["controlModality"], "depth")
        # Style pass = z-image img2img seeded from the base output asset, carrying the painterly LoRA.
        self.assertTrue(style_post["url"].endswith("/generate/custom/model_z-image"))
        self.assertEqual(style_post["body"]["image"], "asset_1")  # first download = base output
        self.assertEqual(style_post["body"]["strength"], 0.35)
        self.assertEqual(style_post["body"]["loras"], ["model_MB22WaRCBLtfhi5R2CRpHoEL"])
        self.assertEqual(style_post["body"]["numSamples"], 1)
        # Meta records the style pass, its input_ref (the registered base), and the final plate.
        self.assertIn("style_pass", cap["meta"])
        self.assertEqual(cap["meta"]["style_pass"]["input_ref"], "asset_1")
        self.assertEqual(cap["meta"]["style_pass"]["strength"], 0.35)
        self.assertEqual(cap["meta"]["style_pass"]["final_plate"]["asset_id"], "asset_2")

    def test_style_pass_composes_after_plain_img2img_base(self):
        # No --controlnet: base is plain z-image img2img, style pass still runs over its output.
        cap = self._run_main(
            ["--room", "crypt", "--base-plate", "/tmp/gb.png",
             "--style-pass", '{"strength": 0.45}', "--out", "/tmp/o"]
        )
        self.assertEqual(len(cap["posts"]), 2)
        self.assertIn("style_pass", cap["meta"])
        self.assertEqual(cap["meta"]["style_pass"]["input_ref"], "asset_1")


if __name__ == "__main__":
    unittest.main()
