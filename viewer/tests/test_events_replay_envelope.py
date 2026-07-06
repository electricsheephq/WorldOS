"""#645 / R645.1 — the live `/events` Action-Replay envelope emission test.

The Godot GT2 renderer (#1120) animates an engine-run fight by polling `/events` and
playing each record as a discrete beat in the Action-Replay envelope shape
`{seq, actor_fk, verb, target_fk, result, anim_hint}`
(docs/roadmap/contracts/action-replay-envelope.md). This test proves the VIEWER side
of that contract: the viewer projects the engine's session-log combat events into that
envelope.

It exercises BOTH halves:
  1. A REAL engine combat — seed a sandbox party-vs-goblins fight via the engine and
     drive a few rounds (the same competent-engine-AI path qa/preview_combat.sh uses),
     so the session log carries genuine `worldos.combat_event.v1` rows. Then GET
     `/events` + `/events-replay` and assert the projection: combat rows carry the
     envelope fields, the verbs are the Godot dispatcher's known set, `result` carries
     the ENGINE-decided hp/damage/roll (never recomputed), and `seq` is strictly
     ordered + idempotent on re-fetch.
  2. A hand-seeded canonical combat log (an attack → damage → heal → death sequence)
     for a deterministic field-by-field assertion of the verb map + result projection,
     independent of any RNG drift in the engine path.

Additive: the endpoint is new/extended; it is a read-only projection (the engine stays
sole writer). The web `#battle` narration feed (which drops kind:"combat" rows and reads
only `text`) is unaffected — asserted here by checking the original keys survive.
"""

import http.client
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVER_PATH = _REPO_ROOT / "viewer" / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_events_replay", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)


# The archived Godot dispatcher (extensions/renderers/godot/scenes/WorldView.gd `_play_beat`) animates exactly these
# verbs; every other verb is accept-and-ignored (a non-animated beat). The envelope's
# projected verbs must stay inside this closed set so a live beat is never undefined.
_GODOT_KNOWN_VERBS = {
    "attack", "cast", "damage", "heal", "condition", "death", "move_to_zone", "zone_move",
}
# The full closed envelope verb vocabulary (contract §verb): the animated set above plus
# the non-animated beats the renderer accept-and-ignores.
_ENVELOPE_VERBS = _GODOT_KNOWN_VERBS | {"save", "check", "travel", "narrate", "combat"}


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)
        _QuietHandler.campaign_id = ""
        _QuietHandler.transcript_path = ""
        _QuietHandler.chat_path = ""
        _QuietHandler.pinned = False
        self._httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._host, self._port = self._httpd.server_address

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        if self._old_state is None:
            os.environ.pop("WORLDOS_STATE_DIR", None)
        else:
            os.environ["WORLDOS_STATE_DIR"] = self._old_state

    def _get_json(self, path: str) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, (json.loads(body.decode("utf-8")) if body else {})
        finally:
            conn.close()

    def _seed_session_log(self, campaign_id: str, sid: str, rows: list[dict]) -> None:
        """Write a model-shaped snapshot + a session JSONL of raw log rows (exactly the
        shape the engine's _log_session_entry / _log_combat_event write)."""
        cdir = self._tmp / "campaigns" / campaign_id
        (cdir / "sessions").mkdir(parents=True)
        snap = {"id": campaign_id, "title": "Replay Test", "active_session_id": sid,
                "session_ids": [sid]}
        (cdir / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
        log = cdir / "sessions" / f"{sid}.jsonl"
        log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _combat_row(event: str, **payload) -> dict:
    """A raw session-log combat row (kind:'combat' + the worldos.combat_event.v1 payload)."""
    payload = {"schema": "worldos.combat_event.v1", "event": event, **payload}
    return {"kind": "combat", "text": f"{event} beat", "payload": payload}


class HandSeededEnvelopeTests(_ServerCase):
    """Deterministic field-by-field assertions on a canonical attack→damage→heal→death
    log — no RNG, so the exact verb/result projection is pinned."""

    def _seed_canonical(self) -> str:
        cid = "camp_replay"
        rows = [
            {"kind": "narration", "text": "The cultists turn as the door bursts open."},
            _combat_row("attack",
                        outcome="hit",
                        actor={"id": "char_aubree01", "name": "Aubree"},
                        target={"id": "mon_cultist01", "name": "Cultist"},
                        roll={"total": 21, "natural": 17},
                        damage={"total": 8, "type": "slashing"},
                        target_state={"current_hp": 6}),
            _combat_row("damage",
                        target={"id": "mon_cultist01", "name": "Cultist"},
                        amount=8, damage_type="slashing",
                        result={"current_hp": 6, "dead": False, "dying": False, "stable": False}),
            _combat_row("healing",
                        target={"id": "char_aubree01", "name": "Aubree"},
                        amount=9,
                        result={"current_hp": 24, "healed": 9, "revived": False, "dead": False}),
            _combat_row("zone_movement",
                        actor={"id": "char_aubree01", "name": "Aubree"},
                        from_zone="the doorway", to_zone="the dais"),
            _combat_row("death_save",
                        target={"id": "mon_cultist01", "name": "Cultist"},
                        roll={"total": 1, "natural": 1}, result="dead",
                        state={"current_hp": 0, "dead": True, "stable": False, "dying": False}),
        ]
        self._seed_session_log(cid, "sess_0001", rows)
        return cid

    def test_combat_rows_project_to_envelope_verbs(self):
        cid = self._seed_canonical()
        status, body = self._get_json(f"/events-replay?campaign={cid}")
        self.assertEqual(status, 200)
        entries = body["entries"]
        by_verb = {e.get("verb"): e for e in entries if isinstance(e, dict)}

        # The narration row → a non-animated narrate beat.
        self.assertEqual(entries[0]["verb"], "narrate")

        # attack → attack, with the engine roll/damage carried verbatim in `result`.
        atk = by_verb["attack"]
        self.assertEqual(atk["actor_fk"], "char_aubree01")
        self.assertEqual(atk["target_fk"], "mon_cultist01")
        self.assertEqual(atk["anim_hint"], "melee_swing")
        self.assertEqual(atk["result"]["outcome"], "hit")
        self.assertEqual(atk["result"]["roll"], {"natural": 17, "total": 21})
        self.assertEqual(atk["result"]["damage"], {"total": 8, "type": "slashing"})
        # hp_after is projected from the engine's target_state (never recomputed).
        self.assertEqual(atk["result"]["hp_after"], 6)

        # damage → damage, hp_after from the engine's nested result block.
        dmg = by_verb["damage"]
        self.assertEqual(dmg["anim_hint"], "damage_flinch")
        self.assertEqual(dmg["result"]["hp_after"], 6)

        # healing → a `cast` beat with a heal_pulse hint (the dispatcher pulses the
        # TARGET green). The engine's `healing` event records only the target (the
        # healed char), NOT the caster, so actor_fk is legitimately null — the renderer
        # pulses the target regardless.
        heal = by_verb["cast"]
        self.assertIsNone(heal["actor_fk"])
        self.assertEqual(heal["target_fk"], "char_aubree01")
        self.assertEqual(heal["anim_hint"], "heal_pulse")
        self.assertEqual(heal["result"]["heal"], 9)
        self.assertEqual(heal["result"]["hp_after"], 24)
        self.assertEqual(heal["result"]["outcome"], "heal")

        # zone_movement → move_to_zone, the destination zone is the target_fk.
        mv = by_verb["move_to_zone"]
        self.assertEqual(mv["target_fk"], "the dais")
        self.assertEqual(mv["anim_hint"], "zone_move")
        self.assertEqual(mv["result"]["zone"], "the dais")

        # death_save on a dead target → a `death` beat.
        death = by_verb["death"]
        self.assertEqual(death["target_fk"], "mon_cultist01")
        self.assertEqual(death["anim_hint"], "death_fall")
        self.assertEqual(death["result"]["outcome"], "dead")

        # Every projected verb is in the closed envelope vocabulary.
        for e in entries:
            self.assertIn(e.get("verb"), _ENVELOPE_VERBS, f"verb out of vocabulary: {e}")

    def test_seq_strictly_ordered_and_idempotent_replay(self):
        cid = self._seed_canonical()
        _status, full = self._get_json(f"/events-replay?campaign={cid}")
        seqs = [e["seq"] for e in full["entries"]]
        # Strict monotonic order within the session.
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)), "seq values must be unique")
        # Idempotent replay: re-fetching from a mid cursor returns the matching tail
        # beat-for-beat (same seq, same result) — no reorder, no drift.
        mid = seqs[3]
        _s2, tail = self._get_json(f"/events-replay?campaign={cid}&since={mid}")
        tail_entries = tail["entries"]
        self.assertTrue(tail_entries, "a since=<mid> fetch should return the remaining beats")
        # The tail's beats match the corresponding slice of the full replay exactly.
        full_by_seq = {e["seq"]: e for e in full["entries"]}
        for beat in tail_entries:
            self.assertIn(beat["seq"], full_by_seq)
            ref = full_by_seq[beat["seq"]]
            self.assertEqual(beat["verb"], ref["verb"])
            self.assertEqual(beat["result"], ref["result"])
            self.assertGreater(beat["seq"], mid - 1)

    def test_events_route_is_additively_enriched_web_keys_preserved(self):
        """The shared /events feed gains envelope keys on combat rows WITHOUT losing the
        keys the web narration feed reads (kind/text/payload/seq)."""
        cid = self._seed_canonical()
        _status, body = self._get_json(f"/events?campaign={cid}")
        combat = [e for e in body["entries"] if e.get("kind") == "combat"]
        self.assertTrue(combat)
        for row in combat:
            # web feed keys preserved
            self.assertIn("text", row)
            self.assertIn("payload", row)
            self.assertEqual(row["payload"]["schema"], "worldos.combat_event.v1")
            self.assertIn("seq", row)
            # envelope keys added
            self.assertIn("verb", row)
            self.assertIn("result", row)
            self.assertIn("anim_hint", row)
        # sid carried for the ${sid}:${seq} dedup key
        self.assertEqual(body["sid"], "sess_0001")

    def test_unknown_future_event_degrades_to_narrate(self):
        cid = "camp_future"
        rows = [_combat_row("some_future_event", actor={"id": "char_x", "name": "X"})]
        self._seed_session_log(cid, "sess_f", rows)
        _status, body = self._get_json(f"/events-replay?campaign={cid}")
        self.assertEqual(body["entries"][0]["verb"], "narrate")
        self.assertEqual(body["entries"][0]["anim_hint"], "none")


def _engine_importable() -> bool:
    """The real-combat test needs the engine package (servers/engine), which pulls in
    `mcp`/`pydantic`. The viewer CI lane installs only pydantic+pytest, so this test
    self-skips there (and runs wherever the engine deps are present, e.g. the engine
    venv / qa lane)."""
    engine_dir = _REPO_ROOT / "servers" / "engine"
    if not (engine_dir / "server.py").is_file():
        return False
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))
    return importlib.util.find_spec("mcp") is not None


@unittest.skipUnless(
    _engine_importable(),
    "engine package + deps (mcp) required for the real-combat projection test",
)
class RealEngineCombatEnvelopeTests(_ServerCase):
    """Drive a REAL engine combat (the competent-engine-AI path qa/preview_combat.sh
    uses) and assert the viewer projects its genuine combat log into the envelope."""

    def _drive_real_combat(self, campaign_id_hint: str = "real") -> str:
        engine_dir = _REPO_ROOT / "servers" / "engine"
        sys.path.insert(0, str(engine_dir))
        # Import the engine modules (they live in servers/engine). These are heavy but
        # the per-process cost is one import; the fight is a handful of deterministic rounds.
        import dice  # noqa: E402
        import combat_loop  # noqa: E402
        import store as engine_store  # noqa: E402
        import server as engine_server  # noqa: E402

        # The engine writes to WORLDOS_STATE_DIR (set by setUp to our tmp), so its session
        # log lands exactly where the viewer's _read_events looks.
        dice.reseed_process_rng(7)
        cid = engine_server.create_campaign("Combat Preview")["id"]
        engine_server.add_location(campaign_id=cid, name="Ruined Keep",
                                   description="a broken hall lit by torchlight", make_current=True)
        c = engine_server._require(cid)
        c.is_sandbox = True
        engine_store.save_campaign(c)

        def mk(name, kind, race, cls, lvl, ab):
            return engine_server.create_character(cid, name, kind=kind, race=race, class_name=cls,
                                                  level=lvl, abilities=ab, apply_srd_defaults=True)["id"]

        STR = {"strength": 17, "dexterity": 12, "constitution": 16, "intelligence": 10, "wisdom": 12, "charisma": 10}
        WIS = {"strength": 12, "dexterity": 12, "constitution": 14, "intelligence": 10, "wisdom": 17, "charisma": 12}
        DEX = {"strength": 10, "dexterity": 17, "constitution": 14, "intelligence": 12, "wisdom": 12, "charisma": 10}
        party = [
            mk("Borin", "player", "dwarf", "fighter", 5, STR),
            mk("Mira", "companion", "human", "cleric", 5, WIS),
            mk("Sly", "companion", "halfling", "rogue", 5, DEX),
        ]
        foes = [m["id"] for m in engine_server.spawn_monster(cid, "Goblin", count=4)["spawned"]]
        engine_server.start_combat(cid, party + foes)
        for _ in range(25):
            c = engine_server._require(cid)
            if not c.combat.active:
                break
            rr = combat_loop.run_combat_round(cid, mode="test")
            if not rr["combat_active"] or len(rr.get("living_sides", [])) < 2:
                break
        return cid

    def test_real_combat_log_projects_to_valid_envelope(self):
        cid = self._drive_real_combat()
        status, body = self._get_json(f"/events-replay?campaign={cid}")
        self.assertEqual(status, 200)
        entries = body["entries"]
        self.assertTrue(entries, "a real fight should produce session-log beats")

        combat_beats = [e for e in entries if isinstance(e, dict)
                        and e.get("verb") in _GODOT_KNOWN_VERBS]
        self.assertTrue(combat_beats, "the fight should yield animated combat beats")
        # The fight involved attacks → there must be attack beats carrying an engine roll.
        attacks = [e for e in combat_beats if e["verb"] == "attack"]
        self.assertTrue(attacks, "an engine fight rolls attacks")
        self.assertTrue(any("roll" in a["result"] for a in attacks),
                        "an attack beat carries the engine-decided roll in `result`")
        self.assertTrue(any("damage" in a.get("result", {}) for a in combat_beats),
                        "a landed hit carries the engine damage in `result`")

        # Every beat: a verb in the closed vocabulary, a dict result, seq present, and an
        # actor_fk that looks like an engine id (or null for an environment beat).
        seqs = [e["seq"] for e in entries]
        self.assertEqual(seqs, sorted(seqs), "beats are seq-ordered")
        self.assertEqual(len(seqs), len(set(seqs)), "seq values are unique")
        for e in entries:
            self.assertIn(e.get("verb"), _ENVELOPE_VERBS, f"verb out of vocabulary: {e.get('verb')}")
            self.assertIsInstance(e.get("result", {}), dict)
            self.assertIn("seq", e)


if __name__ == "__main__":
    unittest.main()
