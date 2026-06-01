import contextlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


@contextlib.contextmanager
def _env(key: str, value: str):
    """Temporarily set an env var (restored on exit) — for pointing _state_dir() at a temp dir."""
    old = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


def _find_action(surface: dict, action_id: str) -> dict:
    found = next((a for a in surface["availableActions"] if a["id"] == action_id), None)
    if found is None:
        available = [a.get("id") for a in surface["availableActions"] if isinstance(a, dict)]
        raise AssertionError(f"action {action_id!r} not found in availableActions: {available}")
    return found


class SessionSurfaceTests(unittest.TestCase):
    def test_session_surface_projects_safe_read_model_without_private_fields(self):
        snapshot = {
            "id": "camp_safe",
            "title": "Smoke at the Gate",
            "summary": "The party studies the sealed gate as dusk gathers.",
            "world_id": "baldurs-gate",
            "day": 12,
            "time_of_day": "dusk",
            "current_location_id": "lower-city",
            "locations": {
                "lower-city": {
                    "name": "Lower City",
                    "region": "Baldur's Gate",
                    "description": "Rain gathers along the cobbles outside the Basilisk Gate.",
                    "notes": "secret Zhentarim cache under the third stone",
                },
            },
            "party": ["tav", "jaheira"],
            "characters": {
                "tav": {
                    "id": "tav",
                    "name": "Tav",
                    "kind": "player",
                    "classes": [{"name": "Fighter", "level": 5}],
                    "current_hp": 31,
                    "max_hp": 42,
                    "armor_class": 18,
                    "conditions": ["blessed"],
                    "inventory": [
                        {"name": "Torch", "quantity": 2, "type": "gear", "notes": "private stash marker"},
                        {"name": "Potion of Healing", "qty": 1},
                    ],
                    "notes": "secret player note",
                    "personality": "private personality seed",
                    "backstory": "private backstory seed",
                    "companion_dossier": {"sealed_agenda": "private agenda"},
                },
                "jaheira": {
                    "id": "jaheira",
                    "name": "Jaheira",
                    "kind": "companion",
                    "classes": [{"name": "Druid", "level": 5}],
                    "current_hp": 38,
                    "max_hp": 38,
                    "armor_class": 16,
                    "conditions": [],
                },
            },
            "quests": {
                "q_gate": {
                    "title": "Ashes at the Gate",
                    "description": "Find who sealed the Basilisk Gate after moonrise.",
                    "status": "active",
                    "objectives": ["Speak to Harper Tull", "Inspect the ash circle"],
                    "completed_objectives": ["Inspect the ash circle"],
                    "location_id": "lower-city",
                    "notes": "secret quest solution",
                },
                "q_done": {"title": "Closed Door", "status": "completed"},
            },
            "scenes": [{"dm_notes": "hidden agenda"}],
            "lore": ["private canon recall input"],
            "dm_notes": "private dm note",
        }

        surface = server.build_session_surface(
            snapshot,
            campaign_id="camp_safe",
            live=False,
            is_live_view=False,
            recent_events=[{"kind": "narration", "detail": "A bell rings once beyond the gate.", "t": 123.5}],
        )

        self.assertEqual(surface["campaign_id"], "camp_safe")
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertFalse(surface["can_act"])
        self.assertEqual(surface["title"], "Smoke at the Gate")
        self.assertEqual(surface["world"], "baldurs-gate")
        self.assertEqual(surface["dayLabel"], "Day 12 · dusk")
        self.assertEqual(surface["location"]["name"], "Lower City")
        self.assertEqual(surface["location"]["region"], "Baldur's Gate")
        self.assertEqual(surface["scene"]["summary"], "The party studies the sealed gate as dusk gathers.")
        self.assertEqual(surface["actor"], {"id": "tav", "name": "Tav", "kind": "player"})
        self.assertEqual([p["name"] for p in surface["party"]], ["Tav", "Jaheira"])
        self.assertEqual(surface["party"][0]["class"], "Fighter")
        self.assertEqual(surface["party"][0]["level"], 5)
        self.assertEqual(surface["party"][0]["hp"], 31)
        self.assertEqual(surface["party"][0]["hpMax"], 42)
        self.assertEqual(surface["party"][0]["ac"], 18)
        self.assertEqual(surface["conditions"][0]["name"], "Blessed")
        self.assertEqual(surface["conditions"][0]["who"], "Tav")
        self.assertEqual(surface["activeQuests"][0]["title"], "Ashes at the Gate")
        self.assertEqual(surface["activeQuests"][0]["objective"], "Speak to Harper Tull")
        self.assertEqual([i["name"] for i in surface["quickInventory"]], ["Torch", "Potion of Healing"])
        self.assertEqual(surface["recentEvents"][0]["text"], "A bell rings once beyond the gate.")
        self.assertEqual(surface["recentEvents"][0]["eventAt"], 123.5)

        encoded = json.dumps(surface)
        for forbidden in (
            "notes",
            "dm_notes",
            "scenes",
            "personality",
            "backstory",
            "companion_dossier",
            "sealed_agenda",
            "private canon",
            "secret",
            "private",
            "hidden agenda",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assert_no_private_keys(surface)

    def test_session_surface_routes_enabled_actions_through_move_contract(self):
        snapshot = {
            "title": "Live Save",
            "party": ["pc"],
            "characters": {"pc": {"id": "pc", "name": "Vela", "kind": "player"}},
        }

        surface = server.build_session_surface(snapshot, campaign_id="camp_live", live=True, is_live_view=True)

        self.assertTrue(surface["can_act"])
        self.assertEqual(surface["actor"], {"id": "pc", "name": "Vela", "kind": "player"})
        self.assertEqual(surface["write_lane"], "/move")
        continue_action = _find_action(surface, "continue")
        look_action = _find_action(surface, "look")
        say_action = _find_action(surface, "say")
        self.assertTrue(continue_action["available"])
        self.assertEqual(continue_action["move"], {"kind": "do", "text": "continue"})
        self.assertEqual(continue_action["detail"], "Press onward")
        self.assertTrue(look_action["available"])
        self.assertEqual(look_action["move"], {"kind": "do", "text": "look around"})
        self.assertEqual(look_action["detail"], "Survey scene")
        self.assertTrue(say_action["available"])
        self.assertEqual(say_action["detail"], "Speak aloud")
        self.assertEqual(say_action["ui"], "focus-say")
        self.assertEqual(_find_action(surface, "do")["detail"], "Act in world")
        self.assertEqual(_find_action(surface, "check")["detail"], "Roll a skill")
        self.assertEqual(_find_action(surface, "save")["detail"], "Resist danger")
        self.assertGreaterEqual(len(surface["enabledActions"]), 6)
        self.assertNotIn("snapshot", json.dumps(surface))

    def test_session_surface_projects_calendar_display_without_state_authority(self):
        snapshot = {
            "title": "Calendar Save",
            "world_id": "calendar-test",
            "day": 32,
            "time_of_day": "dusk",
            "calendar": {
                "name": "Dale Reckoning",
                "era_suffix": "DR",
                "epoch_year": 1492,
                "epoch_month": 1,
                "epoch_day": 1,
                "weekdays": ["Firstday", "Secondday", "Thirdday", "Fourthday", "Fifthday"],
                "months": [
                    {"name": "Hammer", "days": 30, "season": "Deepwinter"},
                    {"name": "Alturiak", "days": 30, "season": "The Claw of Winter"},
                ],
                "moons": [
                    {
                        "name": "Selune",
                        "cycle_days": 8,
                        "phase_names": ["new", "waxing", "full", "waning"],
                    }
                ],
            },
            "party": ["pc"],
            "characters": {"pc": {"id": "pc", "name": "Vela", "kind": "player"}},
        }

        surface = server.build_session_surface(snapshot, campaign_id="camp_calendar", live=True, is_live_view=True)

        self.assertEqual(surface["day"], 32)
        self.assertEqual(surface["time_of_day"], "dusk")
        self.assertEqual(surface["dayLabel"], "Secondday, 2 Alturiak 1492 DR · dusk")
        self.assertEqual(
            surface["calendar"],
            {
                "available": True,
                "calendar": "Dale Reckoning",
                "canonical_day": 32,
                "year": 1492,
                "month": "Alturiak",
                "day_of_month": 2,
                "weekday": "Secondday",
                "season": "The Claw of Winter",
                "date_label": "Secondday, 2 Alturiak 1492 DR",
                "label": "Secondday, 2 Alturiak 1492 DR · dusk",
                "moons": [{"name": "Selune", "age": 7, "cycle_days": 8, "phase": "waning"}],
            },
        )
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")
        self.assertNotIn("calendar_write", json.dumps(surface))

    def test_session_surface_includes_combat_order_and_disabled_reasons(self):
        snapshot = {
            "party": ["pc"],
            "characters": {
                "pc": {
                    "id": "pc",
                    "name": "Vela",
                    "kind": "player",
                    "current_hp": 12,
                    "max_hp": 20,
                    "armor_class": 15,
                },
                "gob": {"id": "gob", "name": "Goblin", "kind": "monster", "current_hp": 7, "max_hp": 7},
            },
            "combat": {
                "active": True,
                "round": 3,
                "turn_index": 0,
                "action_used": True,
                "bonus_action_used": False,
                "order": [
                    {"character_id": "pc", "initiative": 18, "reaction_used": True},
                    {"character_id": "gob", "initiative": 9},
                ],
            },
        }

        surface = server.build_session_surface(snapshot, campaign_id="camp_combat", live=True, is_live_view=True)

        self.assertTrue(surface["encounter"]["active"])
        self.assertEqual(surface["encounter"]["summary"], "Combat round 3")
        self.assertEqual([row["name"] for row in surface["roundOrder"]], ["Vela", "Goblin"])
        self.assertTrue(surface["roundOrder"][0]["active"])
        self.assertEqual(_find_action(surface, "attack")["disabled_reason"], "action spent")
        self.assertEqual(_find_action(surface, "reaction")["disabled_reason"], "reaction spent")

    def test_session_surface_projects_action_context_and_write_lane_metadata(self):
        snapshot = {
            "title": "Due Consequence",
            "summary": "The council waits for the party's answer.",
            "day": 8,
            "time_of_day": "night",
            "current_location_id": "council",
            "locations": {
                "council": {
                    "name": "Council Hall",
                    "description": "Lanterns burn low.",
                    "notes": "private council leverage",
                },
            },
            "party": ["pc"],
            "characters": {"pc": {"id": "pc", "name": "Vela", "kind": "player"}},
            "quests": {
                "q_council": {
                    "title": "The Council Vote",
                    "description": "Choose who receives the charter.",
                    "status": "active",
                    "objectives": ["Name a claimant"],
                    "notes": "private winning answer",
                },
            },
            "consequences": [
                {
                    "id": "charter_due",
                    "trigger_day": 7,
                    "resolved": False,
                    "note": "private baron betrayal",
                },
                {
                    "id": "winter_later",
                    "trigger_day": 12,
                    "fired": False,
                    "note": "private winter plan",
                },
            ],
            "dm_notes": "private session agenda",
        }

        surface = server.build_session_surface(
            snapshot,
            campaign_id="camp_context",
            live=False,
            is_live_view=False,
        )

        self.assertEqual(surface["writeLane"]["endpoint"], "/move")
        self.assertEqual(surface["writeLane"]["authority"], "engine")
        self.assertFalse(surface["writeLane"]["writesCampaignSnapshot"])
        self.assertIn("do", surface["writeLane"]["allowedKinds"])
        self.assertEqual([a["id"] for a in surface["enabledActions"]], [])
        blocked = {a["id"]: a for a in surface["blockedActions"]}
        self.assertEqual(blocked["continue"]["disabled_reason"], "no live move sink")
        self.assertEqual(blocked["attack"]["disabled_reason"], "not in combat")
        self.assertEqual(surface["actionContext"]["scene"]["location"], "Council Hall")
        self.assertEqual(surface["actionContext"]["quests"][0]["title"], "The Council Vote")
        self.assertEqual(surface["actionContext"]["consequences"]["dueCount"], 1)
        self.assertEqual(surface["actionContext"]["consequences"]["pendingCount"], 1)
        self.assertEqual(surface["actionContext"]["consequences"]["signals"][0]["id"], "charter_due")
        action_model_blocked = {a["id"]: a for a in surface["actionModel"]["blockedActions"]}
        self.assertEqual(action_model_blocked["continue"]["disabled_reason"], "no live move sink")
        encoded = json.dumps(surface)
        self.assertNotIn("private", encoded)
        self.assertNotIn("baron", encoded)
        self.assertNotIn("winning answer", encoded)
        self.assert_no_private_keys(surface)

    def test_session_event_tail_rejects_unsafe_active_session_id(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        campaign_dir = root / "campaigns" / "camp_safe"
        (campaign_dir / "sessions").mkdir(parents=True)
        (campaign_dir / "evil.jsonl").write_text(
            json.dumps({"kind": "narration", "detail": "private traversal event"}) + "\n",
            encoding="utf-8",
        )
        (campaign_dir / "sessions" / "sess_1.jsonl").write_text(
            "".join(
                json.dumps({"kind": "narration", "detail": f"event {i}"}) + "\n"
                for i in range(20)
            ),
            encoding="utf-8",
        )

        unsafe_values = [
            "../evil",
            "../../evil",
            "sessions/../evil",
            "/etc/passwd",
            "evil\x00sid",
            "evil/界",
        ]
        unsafe = [
            server._session_event_tail_from_dir(campaign_dir, {"active_session_id": sid})
            for sid in unsafe_values
        ]
        safe_tail = server._session_event_tail_from_dir(campaign_dir, {"active_session_id": "sess_1"}, limit=3)

        self.assertEqual(unsafe, [[] for _ in unsafe_values])
        self.assertEqual([row["detail"] for row in safe_tail], ["event 17", "event 18", "event 19"])

    def test_session_surface_rejects_unsafe_active_session_id(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        campaign_dir = root / "campaigns" / "camp_surface"
        (campaign_dir / "sessions").mkdir(parents=True)
        (campaign_dir / "evil.jsonl").write_text(
            json.dumps({"kind": "narration", "detail": "private traversal event"}) + "\n",
            encoding="utf-8",
        )
        snapshot = {
            "title": "Unsafe Session",
            "active_session_id": "../evil",
            "party": ["pc"],
            "characters": {"pc": {"id": "pc", "name": "Vela", "kind": "player"}},
        }
        recent_events = server._session_event_tail_from_dir(campaign_dir, snapshot)

        surface = server.build_session_surface(
            snapshot,
            campaign_id="camp_surface",
            live=False,
            is_live_view=False,
            recent_events=recent_events,
        )

        self.assertEqual(surface["recentEvents"], [])
        self.assertEqual(surface["title"], "Unsafe Session")

    # --- #405: the session-log tail stamps each row with its ABSOLUTE line index as a stable `seq`,
    # and that seq survives into the surface's recentEvents — so the viewer can dedup the history band
    # against the live /events tail by ID (immune to a reworded copy), not by prose. ----------------
    def test_session_event_tail_stamps_stable_absolute_seq(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        campaign_dir = root / "campaigns" / "camp_seq"
        (campaign_dir / "sessions").mkdir(parents=True)
        # 20 lines; tail of 5 → absolute indices 15..19 (NOT 0..4). This is the load-bearing property:
        # the seq is the line's index in the WHOLE log, so it matches the /events feed's per-line id.
        (campaign_dir / "sessions" / "sess_1.jsonl").write_text(
            "".join(
                json.dumps({"kind": "narration", "text": f"event {i}"}) + "\n"
                for i in range(20)
            ),
            encoding="utf-8",
        )
        tail = server._session_event_tail_from_dir(campaign_dir, {"active_session_id": "sess_1"}, limit=5)
        self.assertEqual([row["seq"] for row in tail], [15, 16, 17, 18, 19],
                         "each tailed row must carry its ABSOLUTE session-log line index as `seq`")
        self.assertEqual([row["text"] for row in tail], [f"event {i}" for i in range(15, 20)])

        # And the seq propagates through the surface projection (recentEvents) for the viewer's dedup.
        surface = server.build_session_surface(
            {"title": "Seq Session", "active_session_id": "sess_1"},
            campaign_id="camp_seq",
            live=False,
            is_live_view=False,
            recent_events=tail,
        )
        seqs = [row.get("seq") for row in surface["recentEvents"]]
        self.assertTrue(all(isinstance(s, int) for s in seqs), "recentEvents rows carry the stable seq")
        self.assertEqual(seqs, sorted(seqs), "recentEvents seq is monotonic (session-log order)")

    # --- #405: /events stamps each entry with its absolute line index, consistent ACROSS polls (the
    # cursor advances), so the client's seq-keyed dedup has a single stable id space per session log. -
    def test_read_events_stamps_absolute_seq_across_polls(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(_env("WORLDOS_STATE_DIR", str(root)))
        campaign_dir = root / "campaigns" / "camp_evt"
        (campaign_dir / "sessions").mkdir(parents=True)
        log = campaign_dir / "sessions" / "sess_1.jsonl"
        (campaign_dir / "snapshot.json").write_text(
            json.dumps({"title": "Evt", "active_session_id": "sess_1"}), encoding="utf-8"
        )
        # First two lines.
        log.write_text(
            json.dumps({"kind": "narration", "text": "line 0"}) + "\n"
            + json.dumps({"kind": "narration", "text": "line 1"}) + "\n",
            encoding="utf-8",
        )
        first, nxt = server._read_events("camp_evt", 0)
        self.assertEqual([e["seq"] for e in first], [0, 1], "first poll stamps absolute indices 0,1")
        self.assertEqual(nxt, 2)
        # Append a third line; the next poll (since=cursor) must stamp it seq 2 (absolute), not 0.
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "narration", "text": "line 2"}) + "\n")
        second, nxt2 = server._read_events("camp_evt", nxt)
        self.assertEqual([e["seq"] for e in second], [2],
                         "the seq is the ABSOLUTE line index, stable across polls (not reset to 0)")
        self.assertEqual(nxt2, 3)

    def assert_no_private_keys(self, value) -> None:
        private_keys = {
            "notes",
            "dm_notes",
            "scenes",
            "lore",
            "memory",
            "personality",
            "backstory",
            "companion_dossier",
            "sealed_agenda",
            "agenda",
        }
        if isinstance(value, dict):
            for key, child in value.items():
                self.assertNotIn(key, private_keys)
                self.assert_no_private_keys(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_private_keys(child)


if __name__ == "__main__":
    unittest.main()
