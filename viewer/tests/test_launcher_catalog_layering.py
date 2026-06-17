"""GA blocker (#933 follow-up) — the OpenWorlds launcher catalog must surface the SHIPPED .app's
LAYERED saves, with the correct run_id, or Resume is unreachable.

THE DEFECT (empirically reproduced): the shipped .app exports a BARE user state home
(~/.worldos/state else ~/.worldos/state) as WORLDOS_STATE_DIR, and scripts/play.sh nests each game
under ``<home>/<run>/campaigns/<id>`` (STATE_DIR = <home>/<run>). The viewer launcher catalog
(``_campaign_catalog_roots``) used to scan only ``<home>/campaigns`` (the BARE current root — empty
under the .app, since games live under per-run subdirs), plus repo-local play-state/* and
qa/state/*. It NEVER scanned ``<home>/<run>/campaigns``, so a freshly played save was INVISIBLE to
/openworlds/campaigns.json (0 cards) → no Resume affordance → the whole Group-B resume-reattach was
unreachable. And had the bare-root scan happened to pick it up, it would have projected
``run_id='state'`` (the _catalog_run_id recency fallback), mismatching the real ``<run>`` so
play.sh's resume gate ``[ -f "$STATE_DIR/campaigns/$id/snapshot.json" ]`` — which keys off that
``<run>`` — would miss it.

These tests run AGAINST THE REAL viewer catalog (``_campaign_catalog_roots`` /
``_openworlds_campaigns`` — the exact code path behind /openworlds/campaigns.json). They write a
snapshot at ``<tmp-home>/<run>/campaigns/<id>/snapshot.json``, point the catalog at the bare
``<tmp-home>`` (exactly as the .app does), and assert the launcher surfaces it with run_id=<run> and
canResume — so play.sh's resume gate would find it. A catalog that only scans the bare root, or
projects run_id='state', FAILS here.

DE-CONFLATION: the sibling fixtures (qa/tests/test_play_state_isolation_resume.sh, and the older
catalog tests) set the engine state-root == the per-run dir, hiding this layering. Here the home and
the per-run dir are DISTINCT — the home is the bare ``<tmp>``; the save is one level deeper at
``<tmp>/<run>/campaigns/<id>`` — so the test exercises the real shipped layout.

stdlib-only; hand-written snapshots (no engine process), like test_campaign_resolution_stability.
The viewer stays a move-sink; the engine remains the sole writer (these tests only READ the catalog).
"""

import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)


def _snapshot(campaign_id: str, *, title: str, world: str = "baldurs-gate") -> dict:
    return {
        "id": campaign_id,
        "world_id": world,
        "title": title,
        "updated_at": time.time(),
        "day": 3,
        "party": ["pc1"],
        "characters": {"pc1": {"id": "pc1", "name": "Tav", "kind": "player"}},
        "current_location_id": "blighted-village",
    }


class LauncherCatalogLayeringTests(unittest.TestCase):
    """The catalog must scan the user state home's per-run children — the shipped .app layout."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # The BARE user state home, exactly what the shipped .app exports as WORLDOS_STATE_DIR.
        self._home = Path(self._tmpdir.name)
        self._saved = {k: os.environ.get(k) for k in ("WORLDOS_STATE_DIR", "WORLDOS_STATE_DIR")}
        os.environ["WORLDOS_STATE_DIR"] = str(self._home)
        os.environ["WORLDOS_STATE_DIR"] = str(self._home)
        server._openworlds_catalog_cache = None
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        server._openworlds_catalog_cache = None

    def _seed_layered(self, run_id: str, campaign_id: str, *, title: str = "The Embergloom Pact") -> Path:
        """Write a snapshot at <home>/<run>/campaigns/<id>/snapshot.json — play.sh's exact layout
        (STATE_DIR = <home>/<run>). The home is one level ABOVE; the catalog only ever sees the home."""
        cdir = self._home / run_id / "campaigns" / campaign_id
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "snapshot.json").write_text(json.dumps(_snapshot(campaign_id, title=title)), encoding="utf-8")
        return cdir

    def _cards(self) -> list[dict]:
        server._openworlds_catalog_cache = None
        return server._openworlds_campaigns("")["campaigns"]

    def _card_for(self, campaign_id: str) -> dict:
        hits = [c for c in self._cards() if c.get("campaign_id") == campaign_id]
        self.assertTrue(
            hits,
            f"the layered save {campaign_id} at <home>/<run>/campaigns/{campaign_id} is INVISIBLE to "
            f"/openworlds/campaigns.json — the catalog did not scan the user home's per-run dir "
            f"(this is the reproduced GA defect: 0 cards → no Resume affordance).",
        )
        return hits[0]

    # -- the catalog roots scan ------------------------------------------------
    def test_catalog_roots_scan_the_user_home_per_run_dir(self):
        run_id, cid = "play-20260601-101010", "camp_feedface01"
        self._seed_layered(run_id, cid)
        roots = server._campaign_catalog_roots()
        user_roots = [r for r in roots if r["source"] == "user" and r["run_id"] == run_id]
        self.assertEqual(
            len(user_roots), 1,
            "the user state home's <home>/<run>/campaigns child must be a single 'user' catalog root",
        )
        root = user_roots[0]
        self.assertTrue(root.get("resumable"), "a user-home per-run root must be resumable")
        self.assertEqual(
            server._resolved(root["campaigns_dir"]),
            server._resolved(self._home / run_id / "campaigns"),
            "the user root's campaigns_dir must be the per-run <home>/<run>/campaigns",
        )

    # -- the end-to-end catalog surface (the /openworlds/campaigns.json path) ---
    def test_layered_save_is_visible_with_correct_run_id_and_resumable(self):
        run_id, cid = "play-20260601-101010", "camp_feedface02"
        self._seed_layered(run_id, cid)
        card = self._card_for(cid)
        # run_id MUST be the per-run dir name <run> — NOT the 'state' recency fallback — so play.sh's
        # resume gate ([ -f "$STATE_DIR/campaigns/$id/snapshot.json" ], STATE_DIR=<home>/<run>) finds it.
        self.assertEqual(
            card["runId"], run_id,
            "the catalog must project run_id=<run> (the per-run dir name), not 'state' — otherwise "
            "play.sh's resume gate, which keys off <run>, cannot locate the save.",
        )
        self.assertIs(card["canResume"], True, "the user's own save must offer Resume (re-attach)")
        self.assertEqual(card["source"], "user")
        self.assertEqual(card["id"], f"user:{run_id}:{cid}",
                         "the card id must be user:<run>:<campaign> so the launcher resumeIdentity "
                         "carries the run_id+campaign_id play.sh re-attaches by")
        self.assertEqual(card["title"], "The Embergloom Pact")

    def test_resume_gate_would_find_the_catalogued_save_on_disk(self):
        # Tie the catalog's projected identity back to play.sh's on-disk resume gate: the gate checks
        # <STATE_DIR>/campaigns/<id>/snapshot.json with STATE_DIR = <home>/<runId>. Using the runId the
        # catalog projected, that exact path must exist — proving the catalog and the gate agree.
        run_id, cid = "play-20260601-202020", "camp_feedface03"
        self._seed_layered(run_id, cid)
        card = self._card_for(cid)
        state_dir = self._home / card["runId"]      # what play.sh sets STATE_DIR to for this card
        gate_path = state_dir / "campaigns" / cid / "snapshot.json"
        self.assertTrue(
            gate_path.is_file(),
            "play.sh's resume gate path <home>/<catalog runId>/campaigns/<id>/snapshot.json must "
            "exist — if the catalog projected a wrong run_id this would be a dead path and Resume "
            "would silently cold-open a fresh empty world.",
        )

    def test_multiple_runs_each_surface_under_their_own_run_id(self):
        self._seed_layered("play-aaaa", "camp_aaaa0001", title="Alpha")
        self._seed_layered("play-bbbb", "camp_bbbb0002", title="Beta")
        by_cid = {c["campaign_id"]: c for c in self._cards()}
        self.assertIn("camp_aaaa0001", by_cid)
        self.assertIn("camp_bbbb0002", by_cid)
        self.assertEqual(by_cid["camp_aaaa0001"]["runId"], "play-aaaa")
        self.assertEqual(by_cid["camp_bbbb0002"]["runId"], "play-bbbb")

    # -- byte-identical when there is no per-run layering (dev/QA) --------------
    def test_bare_home_with_direct_campaigns_yields_no_user_rows(self):
        # The dev/legacy layout: campaigns live DIRECTLY under <home>/campaigns (no per-run subdir).
        # That is the existing 'play' current-state scan; it must NOT spawn a spurious 'user' row, so
        # the catalog stays byte-identical for dev/QA when WORLDOS_STATE_DIR is unset/bare.
        cdir = self._home / "campaigns" / "camp_direct0001"
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "snapshot.json").write_text(json.dumps(_snapshot("camp_direct0001", title="Direct")), encoding="utf-8")
        roots = server._campaign_catalog_roots()
        self.assertEqual(
            [r for r in roots if r["source"] == "user"], [],
            "a bare home whose campaigns are DIRECT children (<home>/campaigns) must not produce a "
            "'user' per-run root — that would change dev/QA catalog output (invariant: additive).",
        )
        # And the direct campaign is still the current-state 'play' card, exactly as before.
        card = self._card_for("camp_direct0001")
        self.assertEqual(card["source"], "play")


if __name__ == "__main__":
    unittest.main()
