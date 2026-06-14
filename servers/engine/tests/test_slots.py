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


# ---------------------------------------------------------------------------
# F08-1 — load_slot must roll back the SESSION LOGS too, not just the snapshot.
# A slot that restores only snapshot.json leaves a discarded timeline (an undone TPK, a
# post-slot orphan session) permanently canon in read_log_all / recap (the DM's lean-beat
# memory). save_slot captures a byte-length manifest of the session logs; load_slot archives
# post-slot orphans and truncates grown sessions back to their slot-time length (archiving the
# discarded tail), so the recap/lean surface matches the rolled-back snapshot.
# ---------------------------------------------------------------------------

from models import SessionLogEntry


def _entry(text, t):
    return SessionLogEntry(kind="narration", text=text, t=t)


def test_load_slot_truncates_grown_session_to_slot_length(tmp_path, monkeypatch, cid):
    sid = "sess-1"
    store.append_log(cid, sid, _entry("The party enters the crypt.", 1.0))
    store.save_slot(cid, "quicksave")  # snapshot the log state too
    # The discarded timeline appended to the SAME session after the slot.
    store.append_log(cid, sid, _entry("The party is slaughtered. TPK.", 2.0))
    store.load_slot(cid, "quicksave")
    texts = [e.text for e in store.read_log(cid, sid)]
    assert texts == ["The party enters the crypt."], "post-slot TPK must be rolled back out of the log"
    # read_log_all (the DM's lean-memory / recap surface) no longer replays the discarded beat.
    seen = [e.text for e in store.read_log_all(cid, [sid])]
    assert not any("TPK" in t for t in seen)


def test_load_slot_archives_orphan_post_slot_session(tmp_path, monkeypatch, cid):
    sid = "sess-1"
    store.append_log(cid, sid, _entry("The party enters the crypt.", 1.0))
    store.save_slot(cid, "quicksave")
    # A whole NEW session file created after the slot — an orphan timeline.
    store.append_log(cid, "sess-2-orphan", _entry("Ghosts wander the ruined party.", 3.0))
    store.load_slot(cid, "quicksave")
    sessions_dir = store._campaign_dir(cid) / "sessions"
    # The orphan file is gone from the live (glob-visible) session set...
    live = {p.stem for p in sessions_dir.glob("*.jsonl")}
    assert "sess-2-orphan" not in live and "sess-1" in live
    # ...and read_log_all never replays it.
    seen = [e.text for e in store.read_log_all(cid, [sid])]
    assert not any("Ghosts" in t for t in seen)
    # The discarded timeline is ARCHIVED (recoverable), not destroyed — under a rolled-back-<ts>/
    # subdir that the non-recursive *.jsonl glob can't see.
    archives = list(sessions_dir.glob("rolled-back-*"))
    assert archives and any(a.is_dir() for a in archives)
    archived_orphan = list(sessions_dir.rglob("sess-2-orphan.jsonl"))
    assert archived_orphan, "the orphan session must be archived, not deleted"


def test_load_slot_archives_grown_session_tail(tmp_path, monkeypatch, cid):
    sid = "sess-1"
    store.append_log(cid, sid, _entry("kept beat", 1.0))
    store.save_slot(cid, "quicksave")
    store.append_log(cid, sid, _entry("discarded beat", 2.0))
    store.load_slot(cid, "quicksave")
    sessions_dir = store._campaign_dir(cid) / "sessions"
    # The truncated tail is archived under rolled-back-<ts>/, so the undone beat is recoverable.
    archived = list(sessions_dir.rglob("sess-1.jsonl"))
    archived = [p for p in archived if "rolled-back-" in str(p)]
    assert archived, "the discarded session tail must be archived"
    assert any("discarded beat" in p.read_text(encoding="utf-8") for p in archived)


def test_load_slot_manifestless_slot_degrades_to_today(tmp_path, monkeypatch, cid):
    # A slot written by an OLD engine (no sessions manifest sidecar) must behave EXACTLY as
    # today: restore the snapshot and leave the session logs untouched (no archiving, no raise).
    sid = "sess-1"
    store.append_log(cid, sid, _entry("beat one", 1.0))
    store.save_slot(cid, "quicksave")
    # Simulate a manifest-less (legacy) slot by deleting the manifest sidecar.
    mpath = store._slot_sessions_manifest_path(cid, "quicksave")
    if mpath.exists():
        mpath.unlink()
    store.append_log(cid, sid, _entry("beat two (kept under legacy degrade)", 2.0))
    store.load_slot(cid, "quicksave")
    texts = [e.text for e in store.read_log(cid, sid)]
    assert texts == ["beat one", "beat two (kept under legacy degrade)"], \
        "a manifest-less slot must not touch the session logs (degrade to today's behavior)"


def test_load_slot_shorter_than_manifest_degrades_leave_as_is(tmp_path, monkeypatch, cid):
    # After an intervening restore of an OLDER slot a session file can be SHORTER than a later
    # slot's manifest length. Truncation must degrade to leave-as-is — never pad, never raise.
    sid = "sess-1"
    store.append_log(cid, sid, _entry("beat one", 1.0))
    store.append_log(cid, sid, _entry("beat two", 2.0))
    store.save_slot(cid, "long")  # manifest records the 2-beat length
    # Now make the live session SHORTER than the manifest (e.g. an older-slot restore happened).
    spath = store._campaign_dir(cid) / "sessions" / f"{sid}.jsonl"
    spath.write_text((_entry("beat one", 1.0)).model_dump_json() + "\n", encoding="utf-8")
    # Restoring the longer slot must NOT pad or raise — leave the shorter file as-is.
    store.load_slot(cid, "long")
    texts = [e.text for e in store.read_log(cid, sid)]
    assert texts == ["beat one"], "a session shorter than the manifest must be left as-is, not padded/raised"


def test_load_slot_snapshot_restore_still_byte_identical(tmp_path, monkeypatch, cid):
    # The session-log fencing must not disturb the snapshot restore: live snapshot after a
    # load_slot is still freshly stamped from the slot's campaign (the prior behavior).
    store.save_slot(cid, "quicksave")
    c = store.load_campaign(cid)
    c.day = 7
    store.save_campaign(c)
    restored = store.load_slot(cid, "quicksave")
    assert restored.day == 1
    assert store.load_campaign(cid).day == 1


def test_save_slot_sessions_manifest_not_listed_as_a_slot(tmp_path, monkeypatch, cid):
    # The manifest sidecar must be invisible to list_slots (it is NOT a restore point).
    store.append_log(cid, "sess-1", _entry("beat", 1.0))
    store.save_slot(cid, "quicksave")
    assert {s["slot"] for s in store.list_slots(cid)} == {"quicksave"}


def test_load_slot_no_sessions_dir_is_safe(tmp_path, monkeypatch, cid):
    # A campaign that never logged a session (no sessions/ dir) must load_slot without error.
    store.save_slot(cid, "quicksave")
    store.load_slot(cid, "quicksave")  # must not raise
    assert store.load_campaign(cid).id == cid


def test_tool_load_slot_rolls_back_logs(cid):
    # End-to-end through the MCP tool wrappers (server.save_slot / server.load_slot).
    server.start_session(cid, "opening")
    store.append_log(cid, store.load_campaign(cid).active_session_id, _entry("pre-slot beat", 1.0))
    server.save_slot(cid, "quicksave")
    store.append_log(cid, store.load_campaign(cid).active_session_id, _entry("post-slot TPK beat", 2.0))
    server.load_slot(cid, "quicksave")
    seen = [e.text for e in store.read_log_all(cid, store.load_campaign(cid).session_ids)]
    assert not any("post-slot TPK" in t for t in seen)
