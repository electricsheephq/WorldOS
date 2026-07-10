#!/usr/bin/env python3
"""Payload-shape + guard tests for scenario_gen.py's `controlnet` command --loras handling
(PLATE SPRINT Phase 3 codification, ARM C follow-up, 2026-07-10).

Bug fixed here: the Scenario custom-model endpoint expects `loras` as a list of bare model-id STRINGS
(["<model_id>", ...]) plus a parallel `lorasScale` list of floats. The old body built
`[{"assetId": lid} for lid in lora_list]` — a dict-shaped payload the live API SILENTLY DROPPED (ARM C
confirmed live), so --loras had zero effect despite looking wired.

Also covers guard_flux_lora_compat: a LoRA trained on model_z-image is REJECTED (HTTP 400) by any
flux.1-dev-family ControlNet model; the guard now catches that combo loudly (sys.exit) before any
credits are spent, shared between scenario_gen.py's own --controlnet command and generate_room.py's
--controlnet base pass (see test_controlnet_flag_plumbing.py's guard test for the generate_room side).

Run: python3 -m pytest extensions/renderers/godot/tools/tests/test_scenario_controlnet_loras_shape.py -q
"""
import copy
import os
import sys
import unittest
from unittest import mock

_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import scenario_gen  # noqa: E402

_Z_IMAGE_LORA = "model_MB22WaRCBLtfhi5R2CRpHoEL"  # the WorldOS painterly LoRA (z-image-trained)


class GuardFluxLoraCompatTest(unittest.TestCase):
    def test_no_loras_is_a_noop(self):
        scenario_gen.guard_flux_lora_compat("model_bfl-flux-1-dev", [])
        scenario_gen.guard_flux_lora_compat("model_bfl-flux-1-dev", None)

    def test_rejects_z_image_lora_on_flux_model(self):
        with self.assertRaises(SystemExit) as ctx:
            scenario_gen.guard_flux_lora_compat("model_bfl-flux-1-dev", [_Z_IMAGE_LORA])
        self.assertIn("REJECTED", str(ctx.exception))
        self.assertIn(_Z_IMAGE_LORA, str(ctx.exception))

    def test_allows_z_image_lora_on_a_non_flux_model(self):
        # model_z-image is the LoRA's own trained-on base model — no incompatibility.
        scenario_gen.guard_flux_lora_compat("model_z-image", [_Z_IMAGE_LORA])

    def test_allows_a_flux_compatible_lora_on_flux(self):
        scenario_gen.guard_flux_lora_compat("model_bfl-flux-1-dev", ["model_some_flux_lora"])


class ControlnetLorasShapeTest(unittest.TestCase):
    """Drive scenario_gen.main(["controlnet", ...]) with every Scenario helper mocked — assert the
    posted body's `loras`/`lorasScale` shape."""

    def _run_controlnet(self, extra_argv, model_id="model_bfl-flux-1-dev"):
        captured = {}

        def fake_post(url, headers, body):
            captured["url"] = url
            captured["body"] = copy.deepcopy(body)
            return {"job": {"jobId": "job_test"}}

        with mock.patch.object(scenario_gen, "_load_credentials", return_value=("k", "s")), \
             mock.patch.object(scenario_gen, "_auth_headers", return_value={}), \
             mock.patch.object(scenario_gen, "_post_json", side_effect=fake_post), \
             mock.patch.object(scenario_gen, "_job_id_from_create", return_value="job_test"), \
             mock.patch.object(scenario_gen, "_poll_job", return_value={"job": {}}), \
             mock.patch.object(scenario_gen, "_download_job_assets",
                               return_value=[{"asset_id": "a1", "path": "/tmp/a1.png", "bytes": 1}]), \
             mock.patch.object(scenario_gen, "_write_meta", return_value=None):
            argv = [
                "controlnet", "--control-asset-id", "asset_GB", "--prompt", "a scene",
                "--model-id", model_id, "--out", "/tmp/o",
            ] + extra_argv
            scenario_gen.main(argv)
        return captured

    def test_loras_is_a_list_of_bare_strings_not_dicts(self):
        cap = self._run_controlnet(["--loras", "model_flux_lora_1,model_flux_lora_2"])
        self.assertEqual(cap["body"]["loras"], ["model_flux_lora_1", "model_flux_lora_2"])
        for entry in cap["body"]["loras"]:
            self.assertIsInstance(entry, str, "loras entries must be bare id strings, not {'assetId':...} dicts")
        self.assertNotIn("lorasScale", cap["body"])

    def test_loras_scale_carried_as_parallel_float_list(self):
        cap = self._run_controlnet(
            ["--loras", "model_flux_lora_1,model_flux_lora_2", "--loras-scale", "0.8,0.5"]
        )
        self.assertEqual(cap["body"]["loras"], ["model_flux_lora_1", "model_flux_lora_2"])
        self.assertEqual(cap["body"]["lorasScale"], [0.8, 0.5])
        for entry in cap["body"]["lorasScale"]:
            self.assertIsInstance(entry, float)

    def test_mismatched_loras_and_scale_lengths_exits(self):
        with self.assertRaises(SystemExit):
            self._run_controlnet(["--loras", "model_a,model_b", "--loras-scale", "0.8"])

    def test_no_loras_flag_omits_loras_key_entirely(self):
        cap = self._run_controlnet([])
        self.assertNotIn("loras", cap["body"])
        self.assertNotIn("lorasScale", cap["body"])

    def test_z_image_lora_on_default_flux_model_rejected_loudly(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run_controlnet(["--loras", _Z_IMAGE_LORA])
        self.assertIn("REJECTED", str(ctx.exception))

    def test_z_image_lora_on_non_flux_model_id_is_allowed(self):
        cap = self._run_controlnet(["--loras", _Z_IMAGE_LORA], model_id="model_z-image")
        self.assertEqual(cap["body"]["loras"], [_Z_IMAGE_LORA])


if __name__ == "__main__":
    unittest.main()
