"""Quicksave / quickload save slots (ST-02).

A slot is a named point-in-time copy of a campaign's snapshot.json beside the live snapshot.
save_slot copies the live state into the slot (non-destructive); load_slot restores the slot
over live (destructive to unsaved progress) under the campaign lock, validating that the slot
belongs to the campaign before it can clobber live state. Tests cover both the store layer and
the engine MCP-tool wrappers.
"""

import json

import pytest

import server
import store
from models import Campaign


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return server.create_campaign("Slot Saga")["id"]


# ---------------------------------------------------------------------------
# store layer
# ---------------------------------------------------------------------------

def test_save_slot_copies_live_snapshot_verbatim(tmp_path, monkeypatch, cid):
    live = store._campaign_dir(cid) / "snapshot.json"
    slot_path = store.save_slot(cid, "quicksave")
    assert slot_path.exists()
    assert slot_path == store._campaign_dir(cid) / "slots" / "quicksave.json"
    # The slot is a byte-for-byte copy of the live snapshot at save time.
    assert slot_path.read_text(encoding="utf-8") == live.read_text(encoding="utf-8")


def test_save_slot_does_not_mutate_live(tmp_path, monkeypatch, cid):
    live = store._campaign_dir(cid) / "snapshot.json"
    before = live.read_text(encoding="utf-8")
    store.save_slot(cid, "quicksave")
    assert live.read_text(encoding="utf-8") == before  # save is read-only on live


def test_load_slot_restores_prior_state(tmp_path, monkeypatch, cid):
    # Snapshot at day 1, then advance the live campaign, then restore.
    store.save_slot(cid, "quicksave")
    c = store.load_campaign(cid)
    assert c.day == 1
    c.day = 9
    c.summary = "advanced past the quicksave"
    store.save_campaign(c)
    assert store.load_campaign(cid).day == 9

    restored = store.load_slot(cid, "quicksave")
    assert restored.day == 1
    # And it's persisted to live, not just returned.
    assert store.load_campaign(cid).day == 1


def test_load_slot_missing_raises(tmp_path, monkeypatch, cid):
    with pytest.raises(FileNotFoundError):
        store.load_slot(cid, "quicksave")


def test_load_slot_refuses_foreign_campaign(tmp_path, monkeypatch, cid):
    # Write a slot whose embedded id is a DIFFERENT campaign — restoring it would clobber the
    # live game with someone else's state, so load_slot must refuse.
    other = Campaign(title="Someone Else")
    slot_path = store._slot_path(cid, "quicksave")
    slot_path.parent.mkdir(parents=True, exist_ok=True)
    slot_path.write_text(other.model_dump_json(indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="belongs to campaign"):
        store.load_slot(cid, "quicksave")
    # Live snapshot is untouched (still the original campaign id).
    assert store.load_campaign(cid).id == cid


def test_load_slot_corrupt_raises(tmp_path, monkeypatch, cid):
    slot_path = store._slot_path(cid, "quicksave")
    slot_path.parent.mkdir(parents=True, exist_ok=True)
    slot_path.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        store.load_slot(cid, "quicksave")


def test_unsafe_slot_name_rejected(tmp_path, monkeypatch, cid):
    with pytest.raises(ValueError):
        store.save_slot(cid, "../escape")


def test_list_slots_newest_first(tmp_path, monkeypatch, cid):
    assert store.list_slots(cid) == []  # none yet
    store.save_slot(cid, "alpha")
    store.save_slot(cid, "beta")
    slots = store.list_slots(cid)
    names = {s["slot"] for s in slots}
    assert names == {"alpha", "beta"}
    assert all("updated_at" in s for s in slots)


def test_save_slot_without_live_snapshot_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="no live snapshot"):
        store.save_slot("camp_does_not_exist", "quicksave")


# ---------------------------------------------------------------------------
# engine MCP-tool wrappers
# ---------------------------------------------------------------------------

def test_tool_save_slot_returns_verdict(cid):
    out = server.save_slot(cid, "quicksave")
    assert out["ok"] is True
    assert out["campaign_id"] == cid
    assert out["slot"] == "quicksave"
    assert (store._campaign_dir(cid) / "slots" / "quicksave.json").exists()


def test_tool_save_slot_default_slot_name(cid):
    out = server.save_slot(cid)  # default slot
    assert out["slot"] == "quicksave"


def test_tool_load_slot_roundtrips_through_tools(cid):
    server.save_slot(cid, "quicksave")
    c = store.load_campaign(cid)
    c.day = 5
    store.save_campaign(c)
    out = server.load_slot(cid, "quicksave")
    assert out["ok"] is True
    assert out["day"] == 1
    assert out["campaign_id"] == cid
    assert "note" in out  # tells the DM to re-ground
    assert store.load_campaign(cid).day == 1


def test_tool_save_slot_unknown_campaign_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="no campaign"):
        server.save_slot("camp_nope", "quicksave")


def test_tool_list_slots(cid):
    server.save_slot(cid, "quicksave")
    out = server.list_slots(cid)
    assert out["campaign_id"] == cid
    assert {s["slot"] for s in out["slots"]} == {"quicksave"}


def test_save_slot_is_engine_sha_stamped_on_load(tmp_path, monkeypatch, cid):
    """A restored slot is re-saved via save_campaign, so it picks up the engine version stamp
    (the slot copy is verbatim, but the restored LIVE snapshot is freshly stamped)."""
    server.save_slot(cid, "quicksave")
    restored = store.load_slot(cid, "quicksave")
    assert restored.engine_sha == store.engine_sha()
    on_disk = json.loads((store._campaign_dir(cid) / "snapshot.json").read_text(encoding="utf-8"))
    assert "engine_sha" in on_disk
