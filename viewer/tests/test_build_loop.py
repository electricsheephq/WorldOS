"""M3 — the gated AI build-loop: generate -> gate -> emit, with the contract frozen (#449-#452).

Offline, stdlib-only (jsonschema import-guarded). Asserts:
  - generate_profile emits a schema-valid render-profile from a seed (defaultable -> partial ok),
    resolves art scope_keys, stamps ai_disclosure, and routes unmapped fields to the human gate
    WITHOUT inventing them into the profile (the loop must not mutate the contract).
  - gate.py accepts a good profile, REJECTS a bad one (empty art / coords-in-core / dup actor),
    and always emits the human-gate queue (taste/story/rights/contract).
  - emit_glue.py produces a self-contained page that injects the profile + loads the correct
    GENERIC renderer (vendored Phaser, no CDN) for the scene_kind.
  - run_loop.build_one ties it together with the gate enforced.
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_BL = _REPO / "viewer" / "openworlds" / "render" / "build_loop"
_SCHEMA_PATH = _REPO / "docs" / "roadmap" / "contracts" / "render-profile.schema.json"
_SEED_PATH = _BL / "example-seed.json"


def _load(modname: str):
    spec = importlib.util.spec_from_file_location(modname, _BL / f"{modname}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # build_loop modules import each other by bare name; make the dir importable.
    if str(_BL) not in sys.path:
        sys.path.insert(0, str(_BL))
    spec.loader.exec_module(mod)
    return mod


generate_profile = _load("generate_profile")
gate = _load("gate")
emit_glue = _load("emit_glue")
run_loop = _load("run_loop")


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.seed = json.loads(_SEED_PATH.read_text())
        self.schema = json.loads(_SCHEMA_PATH.read_text())

    def test_generates_schema_valid_profile(self):
        prof = generate_profile.generate_profile(self.seed, date="2026-06-02")
        clean = generate_profile.strip_unmapped(prof)
        self.assertEqual(clean["schema_version"], 1)
        self.assertEqual(clean["core"]["scene_kind"], "backdrop")
        self.assertEqual(clean["core"]["positioning"], "zone")
        # art scope keys resolved for every location + actor
        for loc in clean["core"]["locations"]:
            self.assertTrue(loc["art"]["scope_key"].startswith("scene-"))
        scopes = {a["art"]["scope_key"] for a in clean["core"]["actors"]}
        self.assertTrue(any(s.startswith("portrait-") for s in scopes))
        self.assertTrue(any(s.startswith("creature-") for s in scopes))
        # ai_disclosure stamped
        self.assertEqual(clean["core"]["ai_disclosure"]["date"], "2026-06-02")

    def test_partial_seed_is_valid(self):
        prof = generate_profile.generate_profile({"title": "Tiny"}, date="2026-06-02")
        clean = generate_profile.strip_unmapped(prof)
        self.assertEqual(clean["core"]["scene_kind"], "tilemap")  # default
        self.assertEqual(clean["core"]["locations"], [])
        self.assertEqual(clean["core"]["actors"], [])

    def test_unmapped_fields_routed_not_invented(self):
        seed = dict(self.seed)
        seed["combat_rules"] = {"initiative": "homebrew"}  # no home in the contract
        seed["scene_kind"] = "isometric-voxel"  # invalid enum -> defaulted + flagged
        prof = generate_profile.generate_profile(seed, date="2026-06-02")
        clean = generate_profile.strip_unmapped(prof)
        # the bogus fields must NOT appear in the emitted profile
        self.assertNotIn("combat_rules", clean)
        self.assertNotIn("combat_rules", clean["core"])
        self.assertEqual(clean["core"]["scene_kind"], "tilemap")  # safe default
        # ...but they MUST be routed to the human gate
        wheres = {u["where"] for u in prof["_unmapped"]}
        self.assertIn("seed.combat_rules", wheres)
        self.assertIn("core.scene_kind", wheres)

    def test_grid_positioning_routed_to_human(self):
        seed = dict(self.seed)
        seed["positioning"] = "grid"  # the evidence-gated Future epic, not v1
        prof = generate_profile.generate_profile(seed, date="2026-06-02")
        self.assertEqual(generate_profile.strip_unmapped(prof)["core"]["positioning"], "zone")
        self.assertTrue(any(u["where"] == "core.positioning" for u in prof["_unmapped"]))

    def test_full_jsonschema_when_available(self):
        jsonschema = __import__("pytest").importorskip("jsonschema")
        clean = generate_profile.strip_unmapped(
            generate_profile.generate_profile(self.seed, date="2026-06-02"))
        jsonschema.validate(clean, self.schema)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(_SCHEMA_PATH.read_text())
        self.seed = json.loads(_SEED_PATH.read_text())
        self.good = generate_profile.strip_unmapped(
            generate_profile.generate_profile(self.seed, date="2026-06-02"))

    def test_good_profile_accepted(self):
        report = gate.run_gate(self.good, self.schema)
        self.assertTrue(report["accepted"], report["gates"])
        # required gates present + passing
        for g in ("schema_valid", "contract_invariants", "art_present", "no_overlap"):
            self.assertTrue(report["gates"][g]["passed"], (g, report["gates"][g]))
        # optional gates are SKIP (None), never silently passed
        self.assertIsNone(report["gates"]["renders_clean"]["passed"])
        self.assertIsNone(report["gates"]["blind_playtester"]["passed"])

    def test_empty_art_rejected(self):
        bad = json.loads(json.dumps(self.good))
        bad["core"]["actors"][0]["art"]["scope_key"] = ""
        report = gate.run_gate(bad, self.schema)
        self.assertFalse(report["accepted"])
        self.assertFalse(report["gates"]["art_present"]["passed"])

    def test_coords_in_core_rejected(self):
        bad = json.loads(json.dumps(self.good))
        bad["core"]["locations"][0]["x"] = 5  # coordinate leak into core
        report = gate.run_gate(bad, self.schema)
        self.assertFalse(report["accepted"])
        self.assertFalse(report["gates"]["contract_invariants"]["passed"])

    def test_duplicate_actor_rejected_by_no_overlap(self):
        bad = json.loads(json.dumps(self.good))
        bad["core"]["actors"].append(dict(bad["core"]["actors"][0]))  # dup id
        report = gate.run_gate(bad, self.schema)
        self.assertFalse(report["accepted"])
        self.assertFalse(report["gates"]["no_overlap"]["passed"])

    def test_human_gate_queue_always_populated(self):
        report = gate.run_gate(self.good, self.schema)
        kinds = {item["kind"] for item in report["human_gate_queue"]}
        self.assertIn("art-taste-signoff", kinds)
        self.assertIn("story-signoff", kinds)
        self.assertIn("ai-disclosure-and-rights", kinds)

    def test_proposed_contract_change_routed_to_human(self):
        seed = dict(self.seed)
        seed["combat_rules"] = {"x": 1}
        prof = generate_profile.generate_profile(seed, date="2026-06-02")
        report = gate.run_gate(prof, self.schema)  # pass the annotated profile (carries _unmapped)
        kinds = {item["kind"] for item in report["human_gate_queue"]}
        self.assertIn("contract-change-proposed", kinds)


class EmitGlueTests(unittest.TestCase):
    def setUp(self):
        self.seed = json.loads(_SEED_PATH.read_text())

    def test_backdrop_glue_loads_generic_backdrop_renderer(self):
        prof = generate_profile.strip_unmapped(
            generate_profile.generate_profile(self.seed, date="2026-06-02"))
        html = emit_glue.emit_glue(prof)
        self.assertIn("renderer-backdrop.js", html)
        self.assertNotIn("renderer-tilemap.js", html)
        self.assertIn("vendor/phaser-3.80.1.min.js", html)
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertIn("window.WORLDOS_PROFILE", html)
        self.assertIn(prof["game_id"], html)

    def test_tilemap_seed_loads_tilemap_renderer(self):
        prof = generate_profile.strip_unmapped(
            generate_profile.generate_profile({"title": "Pixel Quest", "scene_kind": "tilemap"},
                                              date="2026-06-02"))
        html = emit_glue.emit_glue(prof)
        self.assertIn("renderer-tilemap.js", html)
        self.assertNotIn("renderer-backdrop.js", html)

    def test_embedded_profile_is_script_safe(self):
        # an injected profile string must not break out of the <script> element
        prof = generate_profile.strip_unmapped(
            generate_profile.generate_profile({"title": "X </script> Y"}, date="2026-06-02"))
        html = emit_glue.emit_glue(prof)
        self.assertNotIn("</script> Y", html)  # the dangerous sequence is escaped


class RunLoopTests(unittest.TestCase):
    def test_build_one_ties_it_together(self):
        seed = json.loads(_SEED_PATH.read_text())
        schema = json.loads(_SCHEMA_PATH.read_text())
        res = run_loop.build_one(seed, schema, date="2026-06-02")
        self.assertTrue(res["accepted"])
        self.assertIsNotNone(res["profile"])
        self.assertIsNotNone(res["glue"])
        self.assertEqual(res["game_id"], "embergloom-pact-gt2")

    def test_build_one_rejects_and_withholds_output(self):
        # a seed that yields empty art is impossible (names always slugify), so force a bad gate
        # by injecting a coordinate into core via a custom seed path: duplicate actor ids.
        seed = {"title": "Dup", "actors": [{"engine_actor_id": "char-a", "name": "A"},
                                            {"engine_actor_id": "char-a", "name": "A2"}]}
        schema = json.loads(_SCHEMA_PATH.read_text())
        res = run_loop.build_one(seed, schema, date="2026-06-02")
        self.assertFalse(res["accepted"])
        self.assertIsNone(res["profile"])  # rejected -> no shippable output
        self.assertIsNone(res["glue"])


if __name__ == "__main__":
    unittest.main()
