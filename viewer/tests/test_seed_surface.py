"""World-Seed read model + write lane viewer tests (#266).

Covers GET /seed-surface (the de-faked identity block + live params + mutability matrix +
session_started + empty-state) and the POST /seed-param intent bridge (mirrors /move: it
appends a validated set_seed_param intent line to $WORLDOS_PLAYER_MOVES and NEVER writes
snapshot state; read-only when there's no live game; refuses a write tagged for a non-live
campaign). The engine remains the SOLE writer — this lane only relays a validated request.
"""

import http.client
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)


# A snapshot with REAL seed-identity fields + a started session (so gated params need force)
# and NO seed_params block (exercises the additive default projection).
_SNAPSHOT = {
    "id": "camp_seed",
    "title": "The Bone Kings of Aelven",
    "ruleset": "SRD 5.2",
    "created_at": 1717000000.0,
    "world_id": "baldurs-gate",
    "era": "1492 DR, the winter after the Absolute",
    "engine_sha": "92c6105abcdef",
    "ending_id": "",
    "session_ids": ["session-1"],
    "house_rules": {"difficulty": "hard"},
}

# A snapshot WITH an explicit seed_params block (its values must round-trip into params).
_SNAPSHOT_WITH_PARAMS = {
    "id": "camp_seeded2",
    "title": "Grim Marches",
    "ruleset": "SRD 5.2",
    "created_at": 1717000000.0,
    "world_id": "",
    "session_ids": [],
    "house_rules": {"difficulty": "easy"},
    "seed_params": {
        "tone": "Grim",
        "narration": "terse",
        "gm_strictness": "strict",
        "chronicle_voice": "second_person",
        "anachronism": False,
        "chronicler_notes": "Trust the book.",
        "permadeath": True,
        "fate_dice": False,
        "item_destruction": True,
    },
}


# ── projection unit tests (no server) ───────────────────────────────────────


class SeedProjectionTests(unittest.TestCase):
    def test_identity_defakes_from_real_fields(self):
        surface = server.build_seed_surface(
            _SNAPSHOT, campaign_id="camp_seed", live=False, is_live_view=False
        )
        self.assertTrue(surface["present"])
        self.assertEqual(surface["title"], "The Bone Kings of Aelven")
        ident = surface["identity"]
        self.assertEqual(ident["by"], "baldurs-gate")
        self.assertEqual(ident["era"], "1492 DR, the winter after the Absolute")
        # engine pairs ruleset + the SHORT engine sha (never a hardcoded "Chronicle II")
        self.assertEqual(ident["engine"], "SRD 5.2 · 92c6105")
        # pattern is a STABLE id fingerprint, not the literal "9b3d-2f1e-77ac"
        self.assertEqual(ident["pattern"], server._seed_pattern("camp_seed"))
        self.assertNotEqual(ident["pattern"], "9b3d-2f1e-77ac")
        self.assertEqual(ident["seeded_epoch"], 1717000000.0)
        self.assertTrue(ident["seeded"])  # a formatted real-world date string

    def test_params_default_when_no_seed_block(self):
        surface = server.build_seed_surface(
            _SNAPSHOT, campaign_id="camp_seed", live=False, is_live_view=False
        )
        params = surface["params"]
        # additive defaults (today's behavior) for the absent seed_params
        self.assertEqual(params["tone"], "Heroic")
        self.assertEqual(params["narration"], "florid")
        self.assertEqual(params["anachronism"], True)
        self.assertEqual(params["fate_dice"], True)
        # difficulty comes off house_rules (REAL), not a default
        self.assertEqual(params["difficulty"], "hard")
        # system is the ruleset (locked)
        self.assertEqual(params["system"], "SRD 5.2")

    def test_params_reflect_explicit_seed_block(self):
        surface = server.build_seed_surface(
            _SNAPSHOT_WITH_PARAMS, campaign_id="camp_seeded2", live=False, is_live_view=False
        )
        params = surface["params"]
        self.assertEqual(params["tone"], "Grim")
        self.assertEqual(params["narration"], "terse")
        self.assertEqual(params["chronicle_voice"], "second_person")
        self.assertEqual(params["anachronism"], False)
        self.assertEqual(params["chronicler_notes"], "Trust the book.")
        self.assertEqual(params["permadeath"], True)
        self.assertEqual(params["item_destruction"], True)
        self.assertEqual(params["difficulty"], "easy")
        # provenance falls back when world_id is empty
        self.assertEqual(surface["identity"]["by"], "the chronicle")

    def test_mutability_matrix_classes(self):
        surface = server.build_seed_surface(
            _SNAPSHOT, campaign_id="camp_seed", live=False, is_live_view=False
        )
        mut = surface["mutability"]
        for free in ("tone", "narration", "gm_strictness", "chronicle_voice", "anachronism", "chronicler_notes"):
            self.assertEqual(mut[free], "free", free)
        for gated in ("difficulty", "permadeath", "fate_dice", "item_destruction"):
            self.assertEqual(mut[gated], "gated", gated)
        self.assertEqual(mut["system"], "locked")

    def test_session_started_flag(self):
        started = server.build_seed_surface(_SNAPSHOT, campaign_id="camp_seed", live=False, is_live_view=False)
        self.assertTrue(started["session_started"])
        fresh = server.build_seed_surface(_SNAPSHOT_WITH_PARAMS, campaign_id="camp_seeded2", live=False, is_live_view=False)
        self.assertFalse(fresh["session_started"])

    def test_empty_state_when_no_campaign(self):
        surface = server.build_seed_surface({}, campaign_id="", live=False, is_live_view=False)
        self.assertFalse(surface["present"])
        self.assertEqual(surface["params"], {})
        self.assertEqual(surface["identity"], {})
        # the matrix is still present so the UI can label controls even in the empty-state
        self.assertEqual(surface["mutability"]["system"], "locked")
        self.assertFalse(surface["can_act"])

    def test_envelope(self):
        surface = server.build_seed_surface(_SNAPSHOT, campaign_id="camp_seed", live=True, is_live_view=True)
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"]["endpoint"], "/seed-param")
        self.assertEqual(surface["write_lane"]["authority"], "engine")
        self.assertTrue(surface["can_act"])


# ── sanitizer unit tests ─────────────────────────────────────────────────────


class SeedSanitizerTests(unittest.TestCase):
    def test_free_string_param_ok(self):
        intent, why = server.sanitize_seed_param({"param": "tone", "value": "Mythic"})
        self.assertEqual(why, "")
        self.assertEqual(intent, {"role": "player", "kind": "set_seed_param", "param": "tone", "value": "Mythic"})

    def test_bool_param_ok(self):
        intent, why = server.sanitize_seed_param({"param": "permadeath", "value": True, "force": True})
        self.assertEqual(why, "")
        self.assertEqual(intent["value"], True)
        self.assertTrue(intent["force"])

    def test_freetext_is_capped(self):
        long = "x" * 5000
        intent, why = server.sanitize_seed_param({"param": "chronicler_notes", "value": long})
        self.assertEqual(why, "")
        self.assertLessEqual(len(intent["value"]), 2000)

    def test_difficulty_value_validated(self):
        ok, _ = server.sanitize_seed_param({"param": "difficulty", "value": "hard"})
        self.assertIsNotNone(ok)
        bad, why = server.sanitize_seed_param({"param": "difficulty", "value": "ludicrous"})
        self.assertIsNone(bad)
        self.assertTrue(why)

    def test_bad_value_rejected(self):
        bad, why = server.sanitize_seed_param({"param": "tone", "value": "Nope"})
        self.assertIsNone(bad)
        self.assertTrue(why)

    def test_wrong_type_rejected(self):
        bad, why = server.sanitize_seed_param({"param": "fate_dice", "value": "yes"})
        self.assertIsNone(bad)

    def test_locked_param_rejected(self):
        bad, why = server.sanitize_seed_param({"param": "system", "value": "Free Form"})
        self.assertIsNone(bad)
        self.assertIn("locked", why)

    def test_unknown_param_rejected(self):
        bad, why = server.sanitize_seed_param({"param": "bogus", "value": 1})
        self.assertIsNone(bad)
        self.assertIn("unknown", why)


# ── live route tests (GET + POST) ────────────────────────────────────────────


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class SeedRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        self._old_moves = os.environ.get("WORLDOS_PLAYER_MOVES")
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)
        # Live game: a writable moves sink flips POST /seed-param from read-only to accepting.
        self._moves = self._tmp / "player_moves.jsonl"
        os.environ["WORLDOS_PLAYER_MOVES"] = str(self._moves)
        self._write("camp_seed", _SNAPSHOT)
        _QuietHandler.campaign_id = "camp_seed"  # the viewer is "launched on" this campaign
        _QuietHandler.transcript_path = ""
        _QuietHandler.chat_path = ""
        _QuietHandler.pinned = True
        self._httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._host, self._port = self._httpd.server_address

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        for key, old in (("WORLDOS_STATE_DIR", self._old_state), ("WORLDOS_PLAYER_MOVES", self._old_moves)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def _write(self, campaign_id: str, payload: dict) -> None:
        cdir = self._tmp / "campaigns" / campaign_id
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "snapshot.json").write_text(json.dumps(payload), encoding="utf-8")

    def _get_json(self, path: str) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, (json.loads(body.decode("utf-8")) if body else {})
        finally:
            conn.close()

    def _post(self, path: str, payload) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            body = json.dumps(payload).encode("utf-8")
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw else {})
        finally:
            conn.close()

    def _moves_lines(self) -> list[dict]:
        if not self._moves.exists():
            return []
        return [json.loads(ln) for ln in self._moves.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def test_get_seed_surface_route(self):
        status, surface = self._get_json("/seed-surface?campaign=camp_seed")
        self.assertEqual(status, 200)
        self.assertTrue(surface["present"])
        self.assertEqual(surface["identity"]["by"], "baldurs-gate")
        self.assertEqual(surface["params"]["difficulty"], "hard")
        self.assertTrue(surface["session_started"])

    def test_get_seed_surface_empty_without_campaign(self):
        # No ?campaign and the launched campaign exists, so it resolves; force the empty path
        # by pointing at a campaign that does not exist on disk via the validated override
        # (an unknown id falls back to the attached one, so instead read with a blank handler).
        _QuietHandler.campaign_id = ""
        _QuietHandler.pinned = False
        try:
            # remove the only snapshot so recency resolution finds nothing
            (self._tmp / "campaigns" / "camp_seed" / "snapshot.json").unlink()
            status, surface = self._get_json("/seed-surface")
            self.assertEqual(status, 200)
            self.assertFalse(surface["present"])
            self.assertEqual(surface["params"], {})
        finally:
            _QuietHandler.campaign_id = "camp_seed"
            _QuietHandler.pinned = True

    def test_post_seed_param_appends_intent(self):
        status, body = self._post("/seed-param", {"param": "tone", "value": "Grim", "campaign": "camp_seed"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        lines = self._moves_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], {"role": "player", "kind": "set_seed_param", "param": "tone", "value": "Grim"})
        # CRITICAL: the viewer must NOT have written snapshot state — the engine is sole writer.
        snap = json.loads((self._tmp / "campaigns" / "camp_seed" / "snapshot.json").read_text())
        self.assertNotIn("seed_params", snap)  # unchanged by the viewer

    def test_post_seed_param_force_relayed(self):
        status, body = self._post("/seed-param", {"param": "permadeath", "value": True, "force": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        lines = self._moves_lines()
        self.assertTrue(lines[-1]["force"])
        self.assertEqual(lines[-1]["param"], "permadeath")

    def test_post_seed_param_rejects_bad_value(self):
        status, body = self._post("/seed-param", {"param": "tone", "value": "Nope"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertEqual(self._moves_lines(), [])  # nothing relayed

    def test_post_seed_param_rejects_locked(self):
        status, body = self._post("/seed-param", {"param": "system", "value": "Free Form"})
        self.assertFalse(body["ok"])
        self.assertEqual(self._moves_lines(), [])

    def test_post_seed_param_refuses_non_live_campaign(self):
        status, body = self._post("/seed-param", {"param": "tone", "value": "Grim", "campaign": "camp_other"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertEqual(self._moves_lines(), [])

    def test_post_seed_param_read_only_without_live_game(self):
        # Drop the moves sink → no live game → the lane refuses (same gate as /move).
        os.environ.pop("WORLDOS_PLAYER_MOVES", None)
        try:
            status, body = self._post("/seed-param", {"param": "tone", "value": "Grim"})
            self.assertEqual(status, 200)
            self.assertFalse(body["ok"])
            self.assertIn("read-only", body["reason"])
        finally:
            os.environ["WORLDOS_PLAYER_MOVES"] = str(self._moves)


if __name__ == "__main__":
    unittest.main()
