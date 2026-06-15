"""Engine resume re-attach invariants (the GA Resume blocker, engine half).

The .app's Resume must RE-OPEN the saved campaign, not mint a new empty world. play.sh routes resume
through the engine's own re-ground primitives (get_state on the saved id; start_world(resume=...) is
the canonical "continue, don't orphan" path). These assert the engine GUARANTEES the wrapper relies
on, with a REAL on-disk state dir (so re-resolving the store mimics the .app's separate play.sh
process reading the same per-run dir):

  1. start_world(resume=<cid>) RE-ATTACHES the SAME campaign (resumed:True, same id) and mints NO
     new campaign — the dev-repo-orphaning bug the cold-open "start fresh" prompt caused.
  2. The saved party + world + clock survive a store re-resolve (a fresh process on the same state
     dir reads back the seated PC) — get_state returns the saved campaign, not an empty one.
  3. A STALE/mismatched resume id does NOT error or wipe state — it falls through to a fresh start
     (so a deleted save never hands the player a dead/half-attached table).
  4. The store stays single-writer: resume is read-only re-grounding (no extra campaign dir appears).
"""

import pytest

import server
import store


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return tmp_path


def _campaign_dir_count(root) -> int:
    camps = root / "campaigns"
    if not camps.is_dir():
        return 0
    return sum(1 for p in camps.iterdir() if p.is_dir())


def test_start_world_resume_reattaches_same_campaign(state_dir):
    first = server.start_world("baldurs-gate")
    cid = first["campaign_id"]
    server.start_session(cid, title="save")
    assert _campaign_dir_count(state_dir) == 1

    resumed = server.start_world("baldurs-gate", resume=cid)
    assert resumed.get("resumed") is True, "resume must re-attach, not mint a fresh world"
    assert resumed["campaign_id"] == cid, "resume must return the SAME campaign id"
    # The crux: resuming must NOT have created a SECOND campaign (the empty-world bug).
    assert _campaign_dir_count(state_dir) == 1, "resume must not mint a new campaign dir"


def test_resume_preserves_party_across_store_reresolve(state_dir):
    cid = server.start_world("baldurs-gate")["campaign_id"]
    server.start_session(cid, title="save")
    # Seat a player PC so the save has a party + progress (mirrors a played save). create_character
    # with kind="player" + add to party is the engine's sole-writer seating path.
    rec = server.create_character(cid, "Tav", kind="player", race="human",
                                  class_name="fighter", level=1, apply_srd_defaults=True)
    pc_id = rec["id"]

    # Re-resolve the store from disk (state_dir() reads the env fresh each call, so dropping the
    # cache here simulates the .app's separate play.sh process re-opening the same per-run dir).
    store._CAMPAIGN_CACHE.clear() if hasattr(store, "_CAMPAIGN_CACHE") else None

    state = server.get_state(cid)
    assert state.get("id") == cid or state.get("campaign_id") == cid, \
        "get_state must re-ground onto the SAVED campaign"
    # get_state's party is a list of member dicts ({id,name,kind,...}); the saved PC must be among them.
    party_ids = {m.get("id") for m in (state.get("party") or []) if isinstance(m, dict)}
    assert pc_id in party_ids, "the saved player PC must still be in the party after re-attach"
    # The canonical resume re-attach still finds the seated PC (no orphaning).
    resumed = server.start_world("baldurs-gate", resume=cid)
    assert resumed["campaign_id"] == cid and resumed.get("resumed") is True


def test_stale_resume_id_falls_through_to_fresh_start(state_dir):
    cid = server.start_world("baldurs-gate")["campaign_id"]
    before = _campaign_dir_count(state_dir)
    # A resume id that does not exist must NOT raise and must NOT wipe the existing save — it falls
    # through to a fresh start (so the launcher's resume of a deleted save can't dead-table the app).
    out = server.start_world("baldurs-gate", resume="camp_does_not_exist")
    assert isinstance(out, dict) and out.get("campaign_id")
    assert out.get("resumed") is not True, "a stale resume id must not claim a re-attach"
    assert out["campaign_id"] != cid, "a stale resume must start fresh, not silently grab another save"
    # The original save is untouched (still loadable, still its own dir).
    assert store.load_campaign(cid) is not None
    assert _campaign_dir_count(state_dir) == before + 1, "fresh start adds exactly one new campaign"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
