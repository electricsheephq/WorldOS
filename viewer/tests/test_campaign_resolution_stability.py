"""Regression: the active-PC silent-switch blocker (#735, the keystone zero_critical).

The focused/active player character silently changed between DM beats with no UI
handoff — newbie saw Liara->Rolan then *alternating* every beat, adversarial saw
Florrick->Rolan, narrative saw Rolan->Liara. The root is a NON-DETERMINISTIC
live-campaign pick: the viewer's ``_pick_campaign`` resolved the attached campaign with
``max(snaps, key=(has_player, recency))`` where ``recency`` is the jittery *filesystem*
mtime and there is NO stable tiebreak. When TWO campaigns are BOTH seated (each has a
``kind=="player"``) AND tie on recency, ``max`` returns whichever ``glob`` yielded first
— filesystem-order-dependent, so the picked campaign FLIPS between requests/beats.
Downstream, ``_action_actor`` / ``_lead_pc`` deterministically return the single seated
player in whatever snapshot won → the visible per-beat PC flip.

The engine's authoritative resolver ``store.active_campaign_id`` already breaks the
exact same tie deterministically (largest body ``updated_at``, then the
lexicographically-smallest id). These tests pin the viewer pick to that same rule so the
two resolvers can never diverge, and lock the resolved actor stable across beats.

stdlib-only; each test seeds a throwaway ``CLAWDND_STATE_DIR`` with hand-written
snapshots (no engine process), exactly like ``test_live_view_recovery``.
"""

import contextlib
import importlib.util
import json
import os
import pathlib
import tempfile
import time
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)


@contextlib.contextmanager
def _glob_order(transform):
    """Force a deterministic iteration order on ``Path.glob`` for the duration of the block.

    The bug behind #735 is that ``_pick_campaign`` resolves the live campaign with ``max``
    over the results of ``cdir.glob("*/snapshot.json")`` and has NO stable tiebreak, so on a
    recency tie it returns whichever entry the glob yielded first — which is *filesystem-order
    dependent*. macOS APFS happens to return sorted order (so the flip hides locally), but the
    Linux ext4/tmpfs the QA VM runs on returns hash/insertion order, which produced the
    observed per-beat flip. To make the test reproduce the bug on ANY host, we wrap glob and
    apply a permutation (e.g. ``reversed``) so the resolver MUST be order-independent to pass.
    """
    real_glob = pathlib.Path.glob

    def patched(self, pattern, *a, **k):
        return iter(transform(list(real_glob(self, pattern, *a, **k))))

    pathlib.Path.glob = patched
    try:
        yield
    finally:
        pathlib.Path.glob = real_glob


# Two seated campaigns whose ids straddle the lexicographic order the engine's tiebreak
# uses (camp_aaa... < camp_zzz...). Each has exactly ONE seated player, with a DIFFERENT
# name, so a flip in the picked campaign is directly observable as a flip in the actor.
_AAA = "camp_aaa1111111111"  # Rolan
_ZZZ = "camp_zzz9999999999"  # Liara Portyr


def _seated_snap(campaign_id: str, pc_name: str, updated_at: float) -> dict:
    pc_id = f"pc-{pc_name.split()[0].lower()}"
    return {
        "id": campaign_id,
        "world_id": "baldurs-gate",
        "title": f"{pc_name}'s Tale",
        "updated_at": updated_at,
        "party": [pc_id],
        "characters": {pc_id: {"id": pc_id, "name": pc_name, "kind": "player"}},
    }


class CampaignResolutionStabilityTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._tmp = Path(self._tmpdir.name)
        self._saved = {k: os.environ.get(k) for k in
                       ("CLAWDND_STATE_DIR", "WORLDOS_STATE_DIR")}
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)
        server._openworlds_catalog_cache = None

    def tearDown(self):
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    # -- helpers ---------------------------------------------------------------
    def _write(self, campaign_id: str, payload: dict, *, snap_mtime: float | None = None,
               session_mtime: float | None = None) -> None:
        """Write a campaign snapshot (+ optional session log) with controlled mtimes so the
        filesystem ``_campaign_recency`` can be pinned exactly equal across siblings — that is
        the precondition the bug needs (equal recency + no stable tiebreak)."""
        cdir = self._tmp / "campaigns" / campaign_id
        (cdir / "sessions").mkdir(parents=True, exist_ok=True)
        snap = cdir / "snapshot.json"
        snap.write_text(json.dumps(payload), encoding="utf-8")
        log = cdir / "sessions" / "s1.jsonl"
        log.write_text('{"k":"v"}\n', encoding="utf-8")
        if session_mtime is not None:
            os.utime(log, (session_mtime, session_mtime))
        if snap_mtime is not None:
            os.utime(snap, (snap_mtime, snap_mtime))

    def _touch_session(self, campaign_id: str, mtime: float) -> None:
        log = self._tmp / "campaigns" / campaign_id / "sessions" / "s1.jsonl"
        with log.open("a", encoding="utf-8") as fh:
            fh.write('{"beat":1}\n')
        os.utime(log, (mtime, mtime))

    # -- the core determinism guard (kills the glob/float-tie flip) ------------
    # Each permutation models a different filesystem readdir order. The pick MUST be identical
    # under every one — that order-independence is precisely what the bug lacked.
    _PERMS = {
        "forward": lambda xs: xs,
        "reversed": lambda xs: list(reversed(xs)),
        "swapped": lambda xs: ([xs[-1]] + xs[1:-1] + [xs[0]]) if len(xs) >= 2 else xs,
    }

    def test_pick_is_stable_across_filesystem_orders_on_equal_recency_tie(self):
        """Two BOTH-seated campaigns with EQUAL body updated_at AND equal session/snapshot
        mtimes: the pick must be the SAME id regardless of glob/readdir order (no flip)."""
        ts = 1780000000.0
        self._write(_AAA, _seated_snap(_AAA, "Rolan", ts),
                    snap_mtime=ts, session_mtime=ts)
        self._write(_ZZZ, _seated_snap(_ZZZ, "Liara Portyr", ts),
                    snap_mtime=ts, session_mtime=ts)
        picks = set()
        for name, perm in self._PERMS.items():
            with _glob_order(perm):
                for _ in range(20):
                    picks.add(server._pick_campaign(None))
        self.assertEqual(len(picks), 1,
                         f"the live-campaign pick flips with filesystem order: {picks}")

    def test_actor_is_stable_across_filesystem_orders(self):
        """The visible symptom: the resolved ACTOR (the active PC) must not flip between beats
        when two seated campaigns tie on recency, regardless of filesystem readdir order."""
        ts = 1780000000.0
        self._write(_AAA, _seated_snap(_AAA, "Rolan", ts),
                    snap_mtime=ts, session_mtime=ts)
        self._write(_ZZZ, _seated_snap(_ZZZ, "Liara Portyr", ts),
                    snap_mtime=ts, session_mtime=ts)
        names = set()
        for perm in self._PERMS.values():
            with _glob_order(perm):
                for _ in range(20):
                    actor = server._action_actor(server._read_snapshot(server._pick_campaign(None)))
                    self.assertIsNotNone(actor)
                    names.add(actor["name"])
        self.assertEqual(len(names), 1,
                         f"active PC must be stable across beats, saw flips: {names}")

    # -- agree with the engine's authoritative resolver (locks them together) --
    def test_pick_agrees_with_engine_active_campaign_id_on_tie(self):
        """The divergence IS the bug: the harness re-grounds via the engine's
        ``active_campaign_id`` (largest updated_at, smallest-id tiebreak) while the viewer used
        ``max`` with NO id tiebreak. They must resolve to the SAME id for the same store, so the
        live campaign the viewer projects equals the one the engine writes to."""
        ts = 1780000000.0
        self._write(_AAA, _seated_snap(_AAA, "Rolan", ts),
                    snap_mtime=ts, session_mtime=ts)
        self._write(_ZZZ, _seated_snap(_ZZZ, "Liara Portyr", ts),
                    snap_mtime=ts, session_mtime=ts)
        # Import the engine store against the SAME state dir to read its authoritative pick.
        engine_pick = _engine_active_campaign_id("baldurs-gate")
        # The agreement must hold regardless of the host's readdir order.
        for perm in self._PERMS.values():
            with _glob_order(perm):
                self.assertEqual(server._pick_campaign(None), engine_pick,
                                 "viewer pick must match the engine-authoritative live campaign")
                # And, concretely, the engine breaks the tie on the lexicographically-smallest id.
                self.assertEqual(server._pick_campaign(None), _AAA,
                                 "on an updated_at tie the smallest id wins (mirrors the engine)")

    # -- beat-stability: only a STRICTLY-newer live campaign moves the pick ----
    def test_pick_does_not_flip_across_beats_unless_strictly_newer(self):
        """Re-touch only the WINNER's session log each beat (the live run advances) and assert
        the pick + actor never change. A recency *tie* must never hand the pick to a sibling."""
        ts = 1780000000.0
        self._write(_AAA, _seated_snap(_AAA, "Rolan", ts),
                    snap_mtime=ts, session_mtime=ts)
        self._write(_ZZZ, _seated_snap(_ZZZ, "Liara Portyr", ts),
                    snap_mtime=ts, session_mtime=ts)
        anchor = _AAA  # the deterministic winner on the tie (smallest id, mirrors the engine)
        anchor_actor = server._action_actor(server._read_snapshot(anchor))["name"]
        for beat in range(1, 11):
            # The live run's session log advances; its BODY updated_at is unchanged (a tie on
            # the deterministic key), so the pick must stay put — filesystem jitter must not move it.
            self._touch_session(anchor, ts + beat)
            # Also poison the loser's filesystem recency to be NEWER (a jittery mtime / a poisoned
            # save touch) — the body-updated_at-based pick must still ignore it on the tie.
            self._touch_session(_ZZZ, ts + beat + 0.5)
            for perm in self._PERMS.values():
                with _glob_order(perm):
                    self.assertEqual(server._pick_campaign(None), anchor,
                                     f"beat {beat}: the pick must not flip on a recency tie")
                    actor = server._action_actor(server._read_snapshot(server._pick_campaign(None)))
                    self.assertEqual(actor["name"], anchor_actor,
                                     f"beat {beat}: the active PC must stay put across beats")

    def test_strictly_newer_campaign_does_win(self):
        """The auto-follow must STILL move to a genuinely newer live campaign (the #38 behavior
        the original design intends) — stickiness only resists a TIE, never a real advance."""
        ts = 1780000000.0
        self._write(_AAA, _seated_snap(_AAA, "Rolan", ts),
                    snap_mtime=ts, session_mtime=ts)
        self.assertEqual(server._pick_campaign(None), _AAA)
        # A genuinely newer campaign is written (larger body updated_at) → the pick follows it.
        self._write(_ZZZ, _seated_snap(_ZZZ, "Liara Portyr", ts + 100.0),
                    snap_mtime=ts + 100.0, session_mtime=ts + 100.0)
        self.assertEqual(server._pick_campaign(None), _ZZZ,
                         "a strictly-newer live campaign must win (auto-follow still advances)")

    # -- old snapshots (no body updated_at) round-trip on filesystem recency ---
    def test_legacy_snapshots_without_updated_at_fall_back_to_recency(self):
        """An older save with NO body ``updated_at`` must still resolve by filesystem recency and
        be deterministic — the additive tiebreak must not strand legacy snapshots."""
        old = 1780000000.0
        # No "updated_at" key in either body.
        self._write(_AAA, {"id": _AAA, "world_id": "baldurs-gate", "title": "A",
                           "party": ["p"], "characters": {"p": {"id": "p", "name": "Rolan",
                                                                 "kind": "player"}}},
                    snap_mtime=old, session_mtime=old)
        self._write(_ZZZ, {"id": _ZZZ, "world_id": "baldurs-gate", "title": "B",
                           "party": ["p"], "characters": {"p": {"id": "p", "name": "Liara",
                                                                 "kind": "player"}}},
                    snap_mtime=old + 50, session_mtime=old + 50)
        # Newer filesystem recency wins (the legacy behavior), and it is stable across calls.
        first = server._pick_campaign(None)
        self.assertEqual(first, _ZZZ, "legacy: newer filesystem recency still decides")
        for _ in range(20):
            self.assertEqual(server._pick_campaign(None), first)


def _engine_active_campaign_id(world_id: str) -> str | None:
    """Load the engine ``store`` module against the current CLAWDND_STATE_DIR and return its
    authoritative live-campaign pick (kept local so the viewer test suite has no hard import
    dependency on the engine package layout)."""
    engine_dir = Path(__file__).resolve().parents[2] / "servers" / "engine"
    import sys
    sys.path.insert(0, str(engine_dir))
    try:
        # Fresh import each call so it re-reads the (env-driven) state dir.
        for mod in ("store", "models"):
            sys.modules.pop(mod, None)
        import store  # type: ignore
        return store.active_campaign_id(world_id)
    finally:
        if str(engine_dir) in sys.path:
            sys.path.remove(str(engine_dir))


if __name__ == "__main__":
    unittest.main()
