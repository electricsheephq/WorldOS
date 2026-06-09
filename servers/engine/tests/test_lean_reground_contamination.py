"""Regression: lean re-ground cross-chronicle contamination (issue #640).

THE BUG (A/B-proven, 100% reproducible with CLAWDND_LEAN_BEATS=1): a continuing
lean beat starts a FRESH transcript-free session and re-grounds from the engine's
``scene_context(campaign_id=…)``. The play/QA harnesses resolved that
``campaign_id`` by the LARGEST snapshot on disk
(``qa/lib_beat_driver.sh:clawdnd_snapshot_path`` -> ``ls -S | head -1``). When TWO
campaigns coexist in ONE state dir — a cold-open ``start_world`` retry minting a
parallel campaign, or a stale prior save — the largest snapshot can be the WRONG
(parallel) campaign. ``scene_context`` is strictly campaign-pure, so it then
faithfully folds a DIFFERENT save's opening scene (wrong HP, wrong day, wrong scene
art) into the fast re-ground tail — the observed contamination.

This file pins BOTH halves of the diagnosis:

  1. SOURCE-OF-TRUTH PROOF — ``scene_context``/``recent_narration`` is campaign-PURE
     for the id it is given (two campaigns in one state dir never bleed). So the
     engine is the right source of truth; the contamination is a WRONG-ID selection,
     not an engine bleed.

  2. THE BUG + THE FIX — the harness's "largest snapshot" heuristic mis-selects the
     stale/parallel campaign, while the engine's new authoritative resolver
     ``active_campaign(world_id)`` (``store.active_campaign_id``) pins the LIVE
     (most-recently-updated) save. The lean re-ground, pointed at the live id,
     returns ONLY the live campaign's prose.
"""

import json

import pytest

import server
import store


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A clean state dir, like the harness's freshly-wiped campaigns/ tree."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return tmp_path


def _seed_campaign(narration: list[str], world_id: str = "baldurs-gate") -> str:
    """Mint a campaign, stamp its world seed (so the world-scoped resolver has a real
    world_id to filter on — bundled adventures leave world_id empty), and log a
    distinct player-facing narration tail. Returns its id."""
    cid = server.start_adventure("cellar-rats")["campaign_id"]
    if world_id:
        c = store.load_campaign(cid)
        c.world_id = world_id
        store.save_campaign(c)
    server.start_session(cid, title="play")
    for line in narration:
        server.log_event(cid, kind="narration", text=line)
    return cid


def _set_updated_at(cid: str, when: float) -> None:
    """Force a campaign's ``updated_at`` ON DISK, bypassing save_campaign's re-stamp.

    save_campaign() overwrites ``updated_at`` with time.time() on EVERY write (the
    sole-writer stamp), so two campaigns saved back-to-back can tie on the wall clock
    to microsecond resolution — which would make a recency test flaky. The resolver's
    job is "newest updated_at wins", so the test pins explicit, well-separated
    timestamps directly in the snapshot JSON to exercise that selection
    deterministically (mirrors a real play session, where the LIVE save is written
    many seconds after any stale leftover)."""
    snap = store._campaign_dir(cid) / "snapshot.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["updated_at"] = when
    snap.write_text(json.dumps(data), encoding="utf-8")


# ── 1) SOURCE-OF-TRUTH: the engine re-ground is campaign-PURE for a given id ──────


def test_scene_context_recent_narration_is_campaign_pure(state):
    """Two campaigns in ONE state dir. scene_context(campaign_id=LIVE) must return
    ONLY the LIVE campaign's narration tail — never a line from the parallel one.
    (Proves the contamination is a wrong-id selection upstream, NOT an engine bleed.)"""
    live = _seed_campaign(["LIVE: you stand in the cellar, rats stirring."])
    other = _seed_campaign(
        ["OTHER: a Basilisk Gate refugee scene — wrong day, wrong HP 16/20."]
    )
    assert live != other

    rn_live = server.scene_context(live, recent_narration=8)["recent_narration"]
    texts_live = " ".join(e["text"] for e in rn_live)
    assert "LIVE:" in texts_live
    assert "Basilisk Gate" not in texts_live  # the parallel save NEVER bleeds in

    # …and symmetrically the other id only sees the other prose.
    rn_other = server.scene_context(other, recent_narration=8)["recent_narration"]
    texts_other = " ".join(e["text"] for e in rn_other)
    assert "Basilisk Gate" in texts_other
    assert "LIVE:" not in texts_other


def test_scene_context_state_is_campaign_pure(state):
    """The volatile `state` block (day/title/party) is likewise scoped to the id —
    a re-ground against the LIVE id can't read the parallel campaign's clock."""
    live = _seed_campaign(["LIVE: cellar."])
    other = _seed_campaign(["OTHER: refugees."])
    # Diverge the parallel campaign's clock so a bleed would be visible as a day jump.
    server.advance_time(other, phases=3, note="parallel save drifts forward")

    st_live = server.scene_context(live, recent_narration=4)["state"]
    st_other = server.scene_context(other, recent_narration=4)["state"]
    assert st_live["id"] == live
    assert st_other["id"] == other
    # The live re-ground's day is the live campaign's, NOT the drifted parallel one.
    assert st_live["day"] == server.get_state(live)["day"]


# ── 2) THE BUG: "largest snapshot" mis-selects; the engine resolver pins LIVE ─────


def _largest_snapshot_campaign_id(state_dir) -> str:
    """Reproduce the harness's clawdnd_snapshot_path selection: the parent of the
    LARGEST non-empty snapshot.json under campaigns/ (ls -S | head -1)."""
    snaps = list((state_dir / "campaigns").glob("*/snapshot.json"))
    biggest = max(snaps, key=lambda p: p.stat().st_size)
    return biggest.parent.name


def _fatten_snapshot(cid: str, npcs: int = 12) -> None:
    """Grow a campaign's SNAPSHOT (not just session logs) so it is reliably the
    largest on disk — narration lives in sessions/*.jsonl, so snapshot size is driven
    by campaign STATE (characters/locations/quests). A stale parallel save that ran
    longer has more of these. Creates NPCs with biographies to bloat the snapshot,
    matching the cold-open-retry leftover that out-massed the fresh live save."""
    for i in range(npcs):
        server.create_character(
            cid,
            name=f"Refugee {i}",
            kind="npc",
            add_to_party=False,
            biography="A Basilisk Gate refugee. " * 40,
        )


def test_largest_snapshot_heuristic_picks_the_WRONG_campaign(state):
    """The harness's OLD selection. A STALE parallel save (a cold-open-retry leftover)
    was written FIRST and accumulated a fat snapshot; the LIVE save is seeded after
    and is small/fresh. "Largest snapshot" then resolves to the PARALLEL campaign —
    the wrong id that, fed to the lean re-ground, produced the contamination.

    Realism note: the engine stamps ``updated_at`` on EVERY save (sole-writer), so
    "most-recently-updated" == "most-recently-written" — and the LIVE campaign is the
    one the harness is actively playing each beat, so it is genuinely the last writer.
    We pin explicit, well-separated timestamps (``_set_updated_at``) so the recency
    selection is exercised DETERMINISTICALLY, not via back-to-back wall-clock writes
    that can tie to microsecond resolution."""
    # The stale parallel save is born first and is FAT (a roster of NPCs bloats its
    # SNAPSHOT — narration lives in session logs, so snapshot size is driven by state).
    stale = _seed_campaign(["OTHER: Basilisk Gate refugees."])
    _fatten_snapshot(stale)
    # The live save is seeded after and stays small; it is the one being played, so it
    # is the most-recently-updated (a play session writes it many seconds later).
    live = _seed_campaign(["LIVE: you stand in the cellar."])
    _set_updated_at(stale, 1_000.0)
    _set_updated_at(live, 2_000.0)  # the live save was written later (it is being played)

    # The OLD heuristic picks the fat stale save → the contamination source.
    assert _largest_snapshot_campaign_id(state) == stale  # FAILS to find the live save

    # The FIX: the engine's authoritative resolver pins the LIVE (most-recent-writer) save.
    assert store.active_campaign_id() == live
    assert store.active_campaign_id("baldurs-gate") == live


def test_active_campaign_resolver_feeds_a_clean_lean_reground(state):
    """End-to-end: resolve the lean re-ground id via the FIX, then call the exact
    lean re-ground (scene_context with a narration tail). The result is the LIVE
    campaign's prose only — the contamination cannot occur."""
    # Stale parallel save first (fat snapshot), then the live save that keeps playing.
    stale = _seed_campaign(["OTHER: Basilisk Gate, the refugee at the gate."])
    _fatten_snapshot(stale)
    live = _seed_campaign(["LIVE: rats stir in the cellar dark."])
    _set_updated_at(stale, 1_000.0)
    _set_updated_at(live, 2_000.0)  # the live save is most-recently-updated
    assert _largest_snapshot_campaign_id(state) == stale  # the bug's wrong selection

    pinned = server.active_campaign("baldurs-gate")["campaign_id"]
    assert pinned == live  # NOT the larger stale save

    rn = server.scene_context(pinned, recent_narration=8)["recent_narration"]
    joined = " ".join(e["text"] for e in rn)
    assert "LIVE:" in joined
    assert "Basilisk Gate" not in joined  # no cross-chronicle bleed


def test_active_campaign_scopes_to_world(state):
    """world_id scoping: a newer save from a DIFFERENT world never shadows the live
    one (the harness always knows which world it launched)."""
    live = _seed_campaign(["LIVE: cellar."])
    # A different-world campaign that is NEWER overall but must be ignored when we
    # scope to this run's world seed (the harness always knows which world it launched).
    other_world = store.load_campaign(live).model_copy(deep=True)
    other_world.id = "camp_otherworld"
    other_world.world_id = "some-other-world"
    store.save_campaign(other_world)
    _set_updated_at(live, 1_000.0)
    _set_updated_at("camp_otherworld", 2_000.0)  # newer, but wrong world

    # Unscoped, the newer other-world save wins; scoped to this world, the live save.
    assert store.active_campaign_id() == "camp_otherworld"
    assert store.active_campaign_id("baldurs-gate") == live


def test_active_campaign_none_when_empty(state):
    """No campaigns yet → None (the harness then no-ops lean, today's behavior)."""
    assert store.active_campaign_id() is None
    assert server.active_campaign()["campaign_id"] is None


# ── 3) THE TIEBREAK: equal updated_at must resolve DETERMINISTICALLY (#735) ────────


def test_active_campaign_id_breaks_updated_at_tie_on_smallest_id(state):
    """The keystone determinism guard behind #735 (active PC flips between beats).

    When TWO seated campaigns coexist in one state dir and tie on ``updated_at`` (the
    real precondition the QA harness manufactured — two re-run mints with the same
    wall-clock save), the resolver MUST still return ONE id every call. ``active_campaign_id``
    iterates ``sorted(iterdir())`` and keeps the FIRST-seen on a strict ``>`` tie, so the
    lexicographically-SMALLEST id wins — fully deterministic. The viewer's
    ``_pick_campaign`` mirrors this exact rule so the live campaign (and thus the active PC)
    can never flip between beats on a recency tie. This pins the engine half of that contract.
    """
    one = _seed_campaign(["First save."])
    two = _seed_campaign(["Second save."])
    assert one != two
    # EXACT-equal updated_at on both → a pure tie that ONLY the id tiebreak can resolve.
    _set_updated_at(one, 5_000.0)
    _set_updated_at(two, 5_000.0)

    smallest = min(one, two)  # the deterministic winner the resolver must return
    picked = store.active_campaign_id("baldurs-gate")
    assert picked == smallest, (
        f"the lexicographically-smallest id must win on an updated_at tie "
        f"(got {picked!r}, expected {smallest!r} of {sorted((one, two))})")
    # Stable across repeated calls — no iteration-order jitter, regardless of mint order.
    for _ in range(20):
        assert store.active_campaign_id("baldurs-gate") == smallest
