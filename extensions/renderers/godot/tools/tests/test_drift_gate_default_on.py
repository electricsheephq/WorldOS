#!/usr/bin/env python3
"""Tests for generate_room.py's --drift-gate (PLATE SPRINT Phase 3 codification, #1462 follow-up).

--drift-gate is ON BY DEFAULT: after generating the final plate, gate it against the room's committed
qa/check_plate_drift.py manifest — but ONLY for a REGEN of a room that already has an adopted
`canonical_plate` in room_recipes.json AND a committed qa/room_manifests/*.cells.json manifest. Any
room without a canonical_plate (not-yet-adopted/ad-hoc) or without a committed manifest is a NO-OP,
identical to pre-#1462-follow-up behavior, regardless of the flag. `--no-drift-gate` opts out even when
a manifest is found. DRIFT fails loud (SystemExit).

These tests mock check_plate_drift's own functions (imported directly here so generate_room's internal
`import check_plate_drift` picks up the same, already-patched, module object) — no real image files or
network calls.

Run: python3 -m pytest extensions/renderers/godot/tools/tests/test_drift_gate_default_on.py -q
"""
import argparse
import os
import sys
import tempfile
import unittest
import unittest.mock as mock

_TOOLS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
_QA_DIR = os.path.normpath(os.path.join(_TOOLS_DIR, "..", "..", "..", "..", "qa"))
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import generate_room  # noqa: E402
import check_plate_drift  # noqa: E402


class ArgparseDefaultTest(unittest.TestCase):
    """The real CLI parser defaults --drift-gate to True; --no-drift-gate flips it off. Checked via
    --dry-run (no network, returns before any generation/gate code runs) so the parsed Namespace is
    the only thing under test."""

    def _parsed_drift_gate(self, extra_argv):
        captured = {}
        orig_parse_args = argparse.ArgumentParser.parse_args

        def capture_parse_args(self, argv=None, namespace=None):
            ns = orig_parse_args(self, argv, namespace)
            captured["args"] = ns
            return ns

        with mock.patch.object(argparse.ArgumentParser, "parse_args", capture_parse_args):
            generate_room.main(["--room", "crypt", "--base-plate", "/tmp/gb.png", "--dry-run"] + extra_argv)
        return captured["args"].drift_gate

    def test_default_is_true(self):
        self.assertTrue(self._parsed_drift_gate([]))

    def test_no_drift_gate_flips_false(self):
        self.assertFalse(self._parsed_drift_gate(["--no-drift-gate"]))


class MaybeRunDriftGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recipe = generate_room._load_recipe()

    def test_noop_when_disabled(self):
        # enabled=False must short-circuit before even touching the (nonexistent) plate path.
        generate_room._maybe_run_drift_gate("crypt", self.recipe, "/definitely/not/a/real/file.png",
                                             enabled=False)

    def test_noop_for_room_without_canonical_plate(self):
        # 'tavern' has no canonical_plate in room_recipes.json -> ad-hoc/not-yet-adopted, no-op even
        # with enabled=True and a bogus plate path, and WITHOUT needing to mock check_plate_drift at
        # all (proves the function never even imports/touches it for this case).
        self.assertIsNone(self.recipe["rooms"]["tavern"].get("canonical_plate"))
        generate_room._maybe_run_drift_gate("tavern", self.recipe, "/definitely/not/a/real/file.png",
                                             enabled=True)

    def test_noop_when_no_manifest_found_for_canonical_room(self):
        # market_square has a canonical_plate but genuinely NO committed qa/room_manifests/*.cells.json
        # manifest in this checkout -- the lightweight (json-only) pre-check must recognize this and
        # never even attempt `import check_plate_drift`: simulating totally broken Pillow/numpy deps
        # via sys.modules must NOT raise, proving a manifestless canonical room stays a no-op without
        # paying (or needing) the image-stack import cost (chatgpt-codex-connector finding on this PR).
        self.assertEqual(self.recipe["rooms"]["market_square"].get("canonical_plate"), "market_square_v1.png")
        with mock.patch.dict(sys.modules, {"check_plate_drift": None}):
            generate_room._maybe_run_drift_gate("market_square", self.recipe,
                                                 "/definitely/not/a/real/file.png", enabled=True)

    def test_wrong_size_reason_surfaces_not_generic_zero_checked_message(self):
        # A wrong-size candidate comes back passed=False, checked=0, with the REAL reason in
        # result.reasons -- must surface THAT reason, not the generic "ZERO fingerprintable props"
        # message reserved for a PASSED-but-nothing-verified result (evaos-code-review-bot finding).
        fake_manifest_path = mock.Mock()
        fake_manifest_path.name = "crypt_dense_v1.cells.json"
        fake_result = check_plate_drift.DriftResult(
            passed=False, room="crypt", checked=0,
            reasons=["plate candidate.png is 100x100, expected the contract 1344x768"],
        )
        with mock.patch.object(check_plate_drift, "_find_manifest_for_recipe", return_value=fake_manifest_path), \
             mock.patch.object(check_plate_drift, "load_manifest", return_value={"room": "crypt_dense_v1"}), \
             mock.patch.object(check_plate_drift, "check_plate_drift", return_value=fake_result):
            with self.assertRaises(SystemExit) as ctx:
                generate_room._maybe_run_drift_gate("crypt", self.recipe, "/tmp/candidate.png", enabled=True)
        self.assertIn("expected the contract 1344x768", str(ctx.exception))
        self.assertNotIn("ZERO fingerprintable", str(ctx.exception))

    def test_drift_fails_loud(self):
        fake_manifest_path = mock.Mock()
        fake_manifest_path.name = "crypt_dense_v1.cells.json"
        fake_result = check_plate_drift.DriftResult(
            passed=False, room="crypt", checked=1,
            reasons=["sarcophagus drifted (NCC 0.40 < 0.75) — painted prop no longer on authored cell(s)"],
        )
        with mock.patch.object(check_plate_drift, "_find_manifest_for_recipe", return_value=fake_manifest_path), \
             mock.patch.object(check_plate_drift, "load_manifest", return_value={"room": "crypt_dense_v1"}), \
             mock.patch.object(check_plate_drift, "check_plate_drift", return_value=fake_result):
            with self.assertRaises(SystemExit) as ctx:
                generate_room._maybe_run_drift_gate("crypt", self.recipe, "/tmp/candidate.png", enabled=True)
        self.assertIn("DRIFT", str(ctx.exception))
        self.assertIn("sarcophagus drifted", str(ctx.exception))

    def test_pass_does_not_raise(self):
        fake_manifest_path = mock.Mock()
        fake_manifest_path.name = "crypt_dense_v1.cells.json"
        fake_result = check_plate_drift.DriftResult(passed=True, room="crypt", checked=3)
        with mock.patch.object(check_plate_drift, "_find_manifest_for_recipe", return_value=fake_manifest_path), \
             mock.patch.object(check_plate_drift, "load_manifest", return_value={"room": "crypt_dense_v1"}), \
             mock.patch.object(check_plate_drift, "check_plate_drift", return_value=fake_result):
            generate_room._maybe_run_drift_gate("crypt", self.recipe, "/tmp/candidate.png", enabled=True)

    def test_no_drift_gate_opts_out_even_with_a_manifest(self):
        # enabled=False must skip even when a manifest WOULD be found -- assert by never patching
        # check_plate_drift's lookup and confirming no exception/side effect occurs.
        with mock.patch.object(check_plate_drift, "_find_manifest_for_recipe") as m_find:
            generate_room._maybe_run_drift_gate("crypt", self.recipe, "/tmp/candidate.png", enabled=False)
            m_find.assert_not_called()

    def test_import_failure_fails_loud_not_silent_skip(self):
        # A "default-on safety gate" that silently no-ops when its Pillow/numpy deps are missing (the
        # common non-qa-image-venv environment) gives false confidence -- must fail loud instead
        # (chatgpt-codex-connector review finding on this PR).
        with mock.patch.dict(sys.modules, {"check_plate_drift": None}):
            with self.assertRaises(SystemExit) as ctx:
                generate_room._maybe_run_drift_gate("crypt", self.recipe, "/tmp/candidate.png", enabled=True)
        self.assertIn("could not be imported", str(ctx.exception))

    def test_zero_checked_fails_loud_on_the_real_crypt_manifest(self):
        # crypt_dense_v1.cells.json genuinely has 3 props and ZERO embedded ref_fp, and
        # crypt_firelit_v2.png (its canonical_plate) is not locally available in this checkout (only a
        # subset of plates are committed) -- so with NO mocking of check_plate_drift's internals at
        # all, a real contract-sized candidate plate must hit the zero-checked fail-loud path, not a
        # false-confidence PASS (chatgpt-codex-connector review finding on this PR).
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            candidate = os.path.join(td, "candidate.png")
            Image.new("RGB", (check_plate_drift.PX_W, check_plate_drift.PX_H), (10, 10, 10)).save(candidate)
            with self.assertRaises(SystemExit) as ctx:
                generate_room._maybe_run_drift_gate("crypt", self.recipe, candidate, enabled=True)
        self.assertIn("ZERO fingerprintable", str(ctx.exception))


class MainEndToEndDriftGateTest(unittest.TestCase):
    """Drive main() with every Scenario helper mocked, verifying the gate actually fires post-generation
    for a canonical room and is skippable via --no-drift-gate."""

    def _run_main(self, argv, plate_path="/tmp/candidate.png", captured=None):
        # `captured` is accepted (and mutated in place) rather than only returned, because a
        # gate-failing run raises SystemExit out of main() -- and out of this helper -- before ever
        # reaching a `return`; a caller that wants to inspect what happened before the raise must pass
        # its own dict in, since the normal return value is unreachable on that path.
        if captured is None:
            captured = {}
        captured.setdefault("write_meta_called", False)

        def fake_write_meta(out_dir, meta):
            captured["write_meta_called"] = True

        with mock.patch.object(generate_room, "_load_credentials", return_value=("k", "s")), \
             mock.patch.object(generate_room, "_auth_headers", return_value={}), \
             mock.patch.object(generate_room, "_upload_image", return_value="asset_UPLOADED"), \
             mock.patch.object(generate_room, "_post_json", return_value={"job": {"jobId": "job_test"}}), \
             mock.patch.object(generate_room, "_job_id_from_create", return_value="job_test"), \
             mock.patch.object(generate_room, "_poll_job", return_value={"job": {}}), \
             mock.patch.object(generate_room, "_download_job_assets",
                               return_value=[{"asset_id": "a1", "path": plate_path, "bytes": 1}]), \
             mock.patch.object(generate_room, "_write_meta", side_effect=fake_write_meta):
            generate_room.main(argv)
        return captured

    def test_drift_gate_fires_and_fails_loud_for_canonical_room(self):
        fake_manifest_path = mock.Mock()
        fake_manifest_path.name = "crypt_dense_v1.cells.json"
        fake_result = check_plate_drift.DriftResult(passed=False, room="crypt", checked=1, reasons=["prop drifted"])
        with mock.patch.object(check_plate_drift, "_find_manifest_for_recipe", return_value=fake_manifest_path), \
             mock.patch.object(check_plate_drift, "load_manifest", return_value={"room": "crypt_dense_v1"}), \
             mock.patch.object(check_plate_drift, "check_plate_drift", return_value=fake_result):
            with self.assertRaises(SystemExit) as ctx:
                self._run_main(["--room", "crypt", "--base-plate", "/tmp/gb.png", "--out", "/tmp/o"])
        self.assertIn("DRIFT", str(ctx.exception))

    def test_no_drift_gate_flag_skips_even_on_drift(self):
        fake_manifest_path = mock.Mock()
        fake_manifest_path.name = "crypt_dense_v1.cells.json"
        fake_result = check_plate_drift.DriftResult(passed=False, room="crypt", checked=1, reasons=["prop drifted"])
        with mock.patch.object(check_plate_drift, "_find_manifest_for_recipe", return_value=fake_manifest_path), \
             mock.patch.object(check_plate_drift, "load_manifest", return_value={"room": "crypt_dense_v1"}), \
             mock.patch.object(check_plate_drift, "check_plate_drift", return_value=fake_result):
            # Must NOT raise: --no-drift-gate opts out even though the (mocked) gate would DRIFT.
            self._run_main(["--room", "crypt", "--base-plate", "/tmp/gb.png", "--out", "/tmp/o",
                            "--no-drift-gate"])

    def test_non_canonical_room_never_touches_check_plate_drift(self):
        # 'tavern' has no canonical_plate -> the gate must no-op WITHOUT calling
        # _find_manifest_for_recipe at all, even though drift-gate defaults on.
        with mock.patch.object(check_plate_drift, "_find_manifest_for_recipe") as m_find:
            self._run_main(["--room", "tavern", "--base-plate", "/tmp/gb.png", "--out", "/tmp/o"])
            m_find.assert_not_called()

    def test_write_meta_called_even_when_drift_gate_fails(self):
        # scenario_meta.json (job_id/assets/prompts/provenance) must still be written when the gate
        # sys.exits -- that's exactly the run an operator needs the audit trail for (evaos-code-
        # review-bot finding on this PR).
        fake_manifest_path = mock.Mock()
        fake_manifest_path.name = "crypt_dense_v1.cells.json"
        fake_result = check_plate_drift.DriftResult(passed=False, room="crypt", checked=1, reasons=["prop drifted"])
        captured = {}
        with mock.patch.object(check_plate_drift, "_find_manifest_for_recipe", return_value=fake_manifest_path), \
             mock.patch.object(check_plate_drift, "load_manifest", return_value={"room": "crypt_dense_v1"}), \
             mock.patch.object(check_plate_drift, "check_plate_drift", return_value=fake_result):
            with self.assertRaises(SystemExit):
                self._run_main(["--room", "crypt", "--base-plate", "/tmp/gb.png", "--out", "/tmp/o"],
                               captured=captured)
        self.assertTrue(captured["write_meta_called"],
                        "scenario_meta.json must be written even when --drift-gate fails")


if __name__ == "__main__":
    unittest.main()
