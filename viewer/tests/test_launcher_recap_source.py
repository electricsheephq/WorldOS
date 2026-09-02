"""#1764 (B): "Where last we stood" must show the SESSION, not the seed's fixture blurb.

The launcher's chronicle detail card projected `snapshot["summary"]` verbatim — for a seeded
campaign that is the AUTHORING blurb, so a player twelve beats into a crypt read
"A five-room quest loop: camp hub <-> tavern … The A-series adventure-eval fixture." with the
ASCII arrows and the harness wording included. The engine already writes the session log the
chronicle band reads; the launcher simply never used it.

These tests exercise the real `build_openworlds_campaign_summary` (the code path behind
/openworlds/campaigns.json) against a hand-written campaign dir:

  • a PLAYED campaign recaps its own last beat and never the fixture summary;
  • a NEVER-PLAYED campaign still falls back to the summary, labelled "New campaign".

stdlib-only, no engine process; the viewer only READS (engine stays sole writer).
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_recap_source", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)

FIXTURE_SUMMARY = (
    "A five-room quest loop: camp hub <-> tavern (Keeper Maera) <-> shop, and camp <-> crypt "
    "(goblins) <-> throne hall (the Goblin Boss). The A-series adventure-eval fixture."
)
LAST_BEAT = (
    "The slate-carrier drops its burden and the crypt goes loud; an arrow takes you high on the "
    "left shoulder as the green lamp gutters."
)


def _snapshot(*, session_id: str | None) -> dict:
    snap = {
        "id": "adventure_demo_v1",
        "world_id": "adventure-demo",
        "title": "The Crypt Below",
        "summary": FIXTURE_SUMMARY,
        "day": 1,
        "time_of_day": "afternoon",
        "party": ["pc1"],
        "characters": {"pc1": {"id": "pc1", "name": "Aidan", "kind": "player"}},
        "current_location_id": "crypt",
        "locations": {"crypt": {"id": "crypt", "name": "The Crypt"}},
        "quests": {"q1": {"id": "q1", "status": "active", "title": "Clear the crypt"}},
    }
    if session_id:
        snap["active_session_id"] = session_id
        snap["session_ids"] = [session_id]
    return snap


class LauncherRecapSourceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _campaign_dir(self, *, played: bool) -> tuple[Path, dict]:
        cdir = self._tmp / "campaigns" / "adventure_demo_v1"
        (cdir / "sessions").mkdir(parents=True, exist_ok=True)
        sid = "session-f8a9a52f" if played else None
        snap = _snapshot(session_id=sid)
        (cdir / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
        if played:
            rows = [
                {"t": 1788350000.0, "kind": "system", "text": "Session 1 began: The Crypt Below",
                 "speaker": None, "payload": None},
                {"t": 1788350100.0, "kind": "narration", "text": LAST_BEAT,
                 "speaker": None, "payload": None},
                {"t": 1788350200.0, "kind": "combat", "text": "Aidan rolls a death save: pending.",
                 "speaker": "Aidan", "payload": {"event": "death_save"}},
            ]
            (cdir / "sessions" / f"{sid}.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )
        return cdir, snap

    def _summary(self, *, played: bool) -> dict:
        cdir, snap = self._campaign_dir(played=played)
        return server.build_openworlds_campaign_summary(
            "play",
            "run-1",
            "adventure_demo_v1",
            snap,
            campaign_dir=cdir,
            state_root=self._tmp,
            last_played=time.time(),
            current=True,
            can_resume=True,
            now=time.time(),
        )

    def test_played_campaign_never_shows_the_fixture_summary(self):
        """The seed's authoring blurb must not reach a player who has played (#1764)."""
        row = self._summary(played=True)
        self.assertNotIn("adventure-eval fixture", row["recap"])
        self.assertNotIn("<->", row["recap"])
        self.assertNotEqual(row["recap"], FIXTURE_SUMMARY)

    def test_played_campaign_recaps_its_own_session_history(self):
        """The recap is derived from the session log's last narration beat."""
        row = self._summary(played=True)
        self.assertIn("slate-carrier", row["recap"])
        self.assertEqual(row["recapSource"], "session")
        self.assertEqual(row["recapLabel"], "Where last we stood")

    def test_never_played_campaign_falls_back_to_the_summary_as_new(self):
        """With no session history the fixture summary is still shown — labelled New campaign."""
        row = self._summary(played=False)
        self.assertEqual(row["recap"], FIXTURE_SUMMARY)
        self.assertEqual(row["recapSource"], "fixture")
        self.assertEqual(row["recapLabel"], "New campaign")


if __name__ == "__main__":
    unittest.main()
