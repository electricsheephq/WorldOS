"""Tests for the F08 persistence-robustness cluster (audit ENGINE-AUDIT-2026-06-11).

Covers four skeptic-verified defects in the single-writer persistence layer:

* F08-2  save_campaign saves UNCONDITIONALLY on a pure read -> bumps updated_at ->
         flips the #640 live-campaign pointer. A zero-mutation save must be a no-op
         (no rewrite, no fresh stamp); a genuine mutation must STILL save.
* F08-3  the tolerant load drops unknown top-level keys, then the next save destroys
         them permanently. The tolerant path must first stash the original bytes in a
         write-once ``snapshot.pre-tolerant.json`` and surface the dropped key names.
* F08-4  the enumerators (list_campaigns / campaigns_for_world / active_campaign_id)
         use the STRICT parse only, so a tolerant-loadable (unknown-key) campaign is
         invisible to the #640 resolver and to listings. They must share the tolerant
         loader.
* F08-5  one torn session-log line poisons read_log / read_log_all until hand-repair.
         The reader must skip-and-warn the torn line; append must newline-heal a file
         whose last byte isn't a newline so the poison can't grow by concatenation.
"""

import json
import logging

import pytest

import server
import store
from models import Campaign, SessionLogEntry


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))


def _snapshot_path(tmp_path, cid: str):
    return tmp_path / "campaigns" / cid / "snapshot.json"


def _write_raw_snapshot(tmp_path, monkeypatch, data: dict) -> str:
    _state(tmp_path, monkeypatch)
    cid = data["id"]
    snap_dir = tmp_path / "campaigns" / cid
    snap_dir.mkdir(parents=True)
    (snap_dir / "snapshot.json").write_text(json.dumps(data), encoding="utf-8")
    return cid


def _minimal_snapshot(extra: dict | None = None) -> dict:
    c = Campaign(title="Test Campaign")
    base = json.loads(c.model_dump_json())
    if extra:
        base.update(extra)
    return base


# ===========================================================================
# F08-2 — pure read does NOT save / bump updated_at; a mutation DOES
# ===========================================================================

def test_pure_resave_does_not_rewrite_or_bump_updated_at(tmp_path, monkeypatch):
    """A load -> (no mutation) -> save_campaign must be a no-op: the on-disk bytes
    are byte-identical and updated_at is unchanged, so a pure read never flips the
    #640 'most-recently-updated' live pointer."""
    _state(tmp_path, monkeypatch)
    c = Campaign(title="Pure-read campaign")
    store.save_campaign(c)
    path = _snapshot_path(tmp_path, c.id)

    first_bytes = path.read_bytes()
    first_mtime = path.stat().st_mtime_ns
    first_updated = c.updated_at

    # Re-load a fresh copy (as every tool does) and save it back WITHOUT mutating.
    loaded = store.load_campaign(c.id)
    assert loaded is not None
    store.save_campaign(loaded)

    assert path.read_bytes() == first_bytes, "pure re-save must not rewrite the snapshot"
    assert path.stat().st_mtime_ns == first_mtime, "pure re-save must not touch the file"
    assert loaded.updated_at == first_updated, "pure re-save must not bump updated_at"


def test_mutating_resave_does_save_and_bumps_updated_at(tmp_path, monkeypatch):
    """A genuine mutation must STILL save and bump updated_at — the dirty-skip guard
    must not over-guard and drop a real write."""
    _state(tmp_path, monkeypatch)
    c = Campaign(title="Mutating campaign")
    store.save_campaign(c)
    path = _snapshot_path(tmp_path, c.id)
    before_updated = c.updated_at

    loaded = store.load_campaign(c.id)
    assert loaded is not None
    loaded.day = loaded.day + 1  # a real mutation
    # Make sure wall-clock advances so updated_at strictly increases.
    import time as _t
    _t.sleep(0.01)
    store.save_campaign(loaded)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["day"] == c.day + 1, "a real mutation must be persisted to disk"
    assert loaded.updated_at > before_updated, "a real mutation must bump updated_at"


def test_pure_read_path_does_not_flip_live_pointer(tmp_path, monkeypatch):
    """The #640 class: with two campaigns coexisting, a pure load+save of the OLDER
    one must NOT make it the most-recently-updated (active) campaign."""
    _state(tmp_path, monkeypatch)
    import time as _t

    a = Campaign(title="Camp A", world_id="w1")
    store.save_campaign(a)
    _t.sleep(0.01)
    b = Campaign(title="Camp B", world_id="w1")
    store.save_campaign(b)  # B is now the live (most-recently-updated) campaign

    assert store.active_campaign_id("w1") == b.id

    # A pure load+save of the OLDER campaign A must not steal the live pointer.
    loaded_a = store.load_campaign(a.id)
    assert loaded_a is not None
    store.save_campaign(loaded_a)

    assert store.active_campaign_id("w1") == b.id, (
        "a zero-mutation save of camp A must not flip the live pointer to A"
    )


# ===========================================================================
# F08-3 — tolerant load stashes original bytes write-once + surfaces dropped keys;
#          the next save no longer destroys the unknown data irrecoverably
# ===========================================================================

def test_tolerant_load_writes_pre_tolerant_backup_once(tmp_path, monkeypatch):
    """A tolerant load (unknown top-level key) must stash the ORIGINAL bytes in a
    write-once ``snapshot.pre-tolerant.json`` so the dropped data is recoverable; a
    second tolerant load must NOT create a second backup (first-skew bytes win)."""
    _state(tmp_path, monkeypatch)
    snapshot = _minimal_snapshot(extra={"future_field_from_newer_engine": {"x": 1}})
    cid = _write_raw_snapshot(tmp_path, monkeypatch, snapshot)
    original_bytes = _snapshot_path(tmp_path, cid).read_bytes()

    backup = tmp_path / "campaigns" / cid / "snapshot.pre-tolerant.json"
    assert not backup.exists()

    result = store.load_campaign(cid)
    assert result is not None
    assert backup.exists(), "tolerant load must stash the original bytes"
    assert backup.read_bytes() == original_bytes, "backup must be the verbatim pre-drop bytes"
    backup_mtime = backup.stat().st_mtime_ns

    # A second tolerant load must NOT clobber the first-skew backup (write-once).
    store.load_campaign(cid)
    assert backup.stat().st_mtime_ns == backup_mtime, "pre-tolerant backup is write-once"
    # The valuable unknown key is still recoverable from the backup.
    assert "future_field_from_newer_engine" in json.loads(backup.read_text(encoding="utf-8"))


def test_tolerant_load_surfaces_dropped_keys(tmp_path, monkeypatch):
    """The dropped key names must be observable (not log-only) so schema-evolution
    data-loss is visible to the resume path."""
    _state(tmp_path, monkeypatch)
    snapshot = _minimal_snapshot(extra={"gone_key_a": 1, "gone_key_b": 2})
    cid = _write_raw_snapshot(tmp_path, monkeypatch, snapshot)

    store.load_campaign(cid)
    dropped = store.last_dropped_keys(cid)
    assert set(dropped) == {"gone_key_a", "gone_key_b"}


def test_clean_load_records_no_dropped_keys_and_no_backup(tmp_path, monkeypatch):
    """A normal (strict) load drops nothing: no pre-tolerant backup, empty dropped set."""
    _state(tmp_path, monkeypatch)
    c = Campaign(title="Clean")
    store.save_campaign(c)
    store.load_campaign(c.id)

    assert store.last_dropped_keys(c.id) == []
    assert not (tmp_path / "campaigns" / c.id / "snapshot.pre-tolerant.json").exists()


def test_start_session_surfaces_schema_drift_on_resume(tmp_path, monkeypatch):
    """The resume path (start_session) must SURFACE the dropped keys, not just log them —
    so a campaign written by a different engine schema announces the data-loss at the table."""
    _state(tmp_path, monkeypatch)
    snapshot = _minimal_snapshot(extra={"newer_engine_field": {"x": 1}})
    cid = _write_raw_snapshot(tmp_path, monkeypatch, snapshot)

    out = server.start_session(cid)
    assert "schema_drift" in out, "start_session must surface tolerant-load drift"
    assert out["schema_drift"]["dropped_keys"] == ["newer_engine_field"]


def test_start_session_no_drift_key_on_clean_campaign(tmp_path, monkeypatch):
    """A clean (strict-loadable) campaign must NOT carry a schema_drift key (no false alarm)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("Clean resume")["id"]
    out = server.start_session(cid)
    assert "schema_drift" not in out


def test_backup_failure_does_not_abort_tolerant_load(tmp_path, monkeypatch, caplog):
    """A backup-write failure must DEGRADE (log + still load), never abort the load."""
    _state(tmp_path, monkeypatch)
    snapshot = _minimal_snapshot(extra={"unknown_x": 1})
    cid = _write_raw_snapshot(tmp_path, monkeypatch, snapshot)

    import store as _store

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(_store, "_atomic_write", _boom)
    with caplog.at_level(logging.WARNING, logger="store"):
        result = _store.load_campaign(cid)
    assert result is not None, "a backup failure must not brick the load"


# ===========================================================================
# F08-4 — enumerators see a tolerant-loadable (unknown-key) campaign
# ===========================================================================

def test_list_campaigns_includes_tolerant_loadable(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    snapshot = _minimal_snapshot(extra={"future_only_field": 1})
    cid = _write_raw_snapshot(tmp_path, monkeypatch, snapshot)

    ids = [row["id"] for row in store.list_campaigns()]
    assert cid in ids, "list_campaigns must see a tolerant-loadable campaign"


def test_campaigns_for_world_includes_tolerant_loadable(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    c = Campaign(title="World-scoped", world_id="w-tol")
    snapshot = json.loads(c.model_dump_json())
    snapshot["future_only_field"] = 1
    cid = _write_raw_snapshot(tmp_path, monkeypatch, snapshot)

    ids = [row["id"] for row in store.campaigns_for_world("w-tol")]
    assert cid in ids, "campaigns_for_world must see a tolerant-loadable campaign"


def test_active_campaign_id_resolves_tolerant_loadable(tmp_path, monkeypatch):
    """The #640 resolver must not skip a tolerant-loadable campaign — otherwise the
    live pointer silently resolves the WRONG (strict-only) campaign."""
    _state(tmp_path, monkeypatch)
    c = Campaign(title="Newest but tolerant", world_id="w-res")
    snapshot = json.loads(c.model_dump_json())
    snapshot["updated_at"] = 9_999_999_999.0  # clearly the most-recent
    snapshot["future_only_field"] = 1
    cid = _write_raw_snapshot(tmp_path, monkeypatch, snapshot)

    assert store.active_campaign_id("w-res") == cid, (
        "active_campaign_id must resolve the most-recent campaign even if it needs tolerant load"
    )


def test_enumerators_are_read_only_on_tolerant_loadable(tmp_path, monkeypatch):
    """The enumerators must stay PURE reads: encountering a tolerant-loadable campaign must
    NOT write the pre-tolerant backup or touch the snapshot (the 'pure reads don't write'
    invariant — only the callable load path stashes the backup)."""
    _state(tmp_path, monkeypatch)
    c = Campaign(title="RO", world_id="w-ro")
    snapshot = json.loads(c.model_dump_json())
    snapshot["future_only_field"] = 1
    cid = _write_raw_snapshot(tmp_path, monkeypatch, snapshot)
    cdir = tmp_path / "campaigns" / cid
    snap_mtime = (cdir / "snapshot.json").stat().st_mtime_ns

    store.list_campaigns()
    store.campaigns_for_world("w-ro")
    store.active_campaign_id("w-ro")

    assert not (cdir / "snapshot.pre-tolerant.json").exists(), "enumeration must not write a backup"
    assert (cdir / "snapshot.json").stat().st_mtime_ns == snap_mtime, "enumeration must not touch the snapshot"


def test_enumerators_skip_genuinely_corrupt(tmp_path, monkeypatch):
    """A snapshot that is corrupt even after unknown-key stripping must still be
    skipped (not crash the listing) — tolerance must not swallow real corruption."""
    _state(tmp_path, monkeypatch)
    good = Campaign(title="Good")
    store.save_campaign(good)
    bad_dir = tmp_path / "campaigns" / "camp-broken"
    bad_dir.mkdir(parents=True)
    # Missing required 'title' -> not loadable even tolerantly.
    snap = _minimal_snapshot()
    del snap["title"]
    snap["id"] = "camp-broken"
    (bad_dir / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")

    ids = [row["id"] for row in store.list_campaigns()]
    assert good.id in ids
    assert "camp-broken" not in ids


# ===========================================================================
# F08-5 — torn session-log line is skipped (warn) instead of poisoning the read
# ===========================================================================

def test_read_log_skips_torn_final_line(tmp_path, monkeypatch, caplog):
    _state(tmp_path, monkeypatch)
    cid = "camp-torn"
    store.append_log(cid, "s1", SessionLogEntry(t=1.0, kind="narration", text="good-1"))
    store.append_log(cid, "s1", SessionLogEntry(t=2.0, kind="narration", text="good-2"))
    # Simulate an OOM-killed half-write: append a truncated JSON line with no newline.
    path = tmp_path / "campaigns" / cid / "sessions" / "s1.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"t": 3.0, "kind": "narration", "text": "tor')  # torn, unterminated

    with caplog.at_level(logging.WARNING, logger="store"):
        entries = store.read_log(cid, "s1")
    texts = [e.text for e in entries]
    assert texts == ["good-1", "good-2"], "read_log must skip the torn line, not raise"


def test_read_log_all_tolerates_torn_line(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    cid = "camp-torn-all"
    store.append_log(cid, "s1", SessionLogEntry(t=1.0, kind="narration", text="a"))
    path = tmp_path / "campaigns" / cid / "sessions" / "s1.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not even json")
    # Must not raise.
    texts = [e.text for e in store.read_log_all(cid, ["s1"])]
    assert texts == ["a"]


def test_append_heals_missing_trailing_newline(tmp_path, monkeypatch):
    """If the last byte of an existing log isn't a newline (a torn write), the next
    append must NOT concatenate onto it — it must newline-heal first so the poison
    can't grow into a single line carrying both the torn entry and the good one."""
    _state(tmp_path, monkeypatch)
    cid = "camp-heal"
    sessions = tmp_path / "campaigns" / cid / "sessions"
    sessions.mkdir(parents=True)
    path = sessions / "s1.jsonl"
    # A torn line with NO trailing newline already on disk.
    path.write_text('{"t": 1.0, "kind": "narration", "text": "tor', encoding="utf-8")

    store.append_log(cid, "s1", SessionLogEntry(t=2.0, kind="narration", text="good"))

    raw = path.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 2, f"append must newline-heal, not concatenate; got {raw!r}"
    # The good entry must be parseable as its own line and recoverable.
    texts = [e.text for e in store.read_log(cid, "s1")]
    assert "good" in texts


def test_append_to_clean_log_adds_no_extra_newline(tmp_path, monkeypatch):
    """A normal append onto a well-terminated log must NOT inject a blank line (the
    heal only fires when the last byte isn't already a newline)."""
    _state(tmp_path, monkeypatch)
    cid = "camp-clean-append"
    store.append_log(cid, "s1", SessionLogEntry(t=1.0, kind="narration", text="a"))
    store.append_log(cid, "s1", SessionLogEntry(t=2.0, kind="narration", text="b"))
    path = tmp_path / "campaigns" / cid / "sessions" / "s1.jsonl"
    raw = path.read_text(encoding="utf-8")
    assert "\n\n" not in raw, f"no blank line on a clean append; got {raw!r}"
    assert [e.text for e in store.read_log(cid, "s1")] == ["a", "b"]


# ===========================================================================
# F08-2 (tool level) — pure check_*/world_tick does NOT bump updated_at;
#                       a mutating call DOES (the #640 live-pointer flip)
# ===========================================================================

@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.create_campaign("F08-2 World")["id"]


def _updated_at(cid: str) -> float:
    c = store.load_campaign(cid)
    assert c is not None
    return c.updated_at


def test_check_consequences_pure_call_does_not_bump_updated_at(cid):
    """check_consequences with nothing due is a PURE read — it must not bump updated_at
    (which would flip the #640 live pointer onto this campaign)."""
    before = _updated_at(cid)
    res = server.check_consequences(cid)
    assert res["due"] == []  # nothing scheduled -> zero mutation
    assert _updated_at(cid) == before, "a no-op check_consequences must not bump updated_at"


def test_check_consequences_mutating_call_does_bump_updated_at(cid):
    """When a consequence actually fires (state mutates), the save must still land."""
    out = server.add_consequence(cid, 2, "Reinforcements arrive.")
    with store.campaign_lock(cid):
        c = store.load_campaign(cid)
        c.day = out["trigger_day"]
        store.save_campaign(c)
    import time as _t
    before = _updated_at(cid)
    _t.sleep(0.01)
    res = server.check_consequences(cid)
    assert len(res["due"]) == 1, "the consequence must fire (a real mutation)"
    assert _updated_at(cid) > before, "a firing check_consequences must bump updated_at"


def test_world_tick_repeat_same_day_does_not_bump_updated_at(cid):
    """world_tick records its bookkeeping (``last_tick_day``) on the FIRST call of a day —
    a genuine mutation that must save. A SECOND world_tick on the SAME day mutates nothing,
    so it must be a true no-op (no rewrite, no updated_at bump, no live-pointer flip). This
    is exactly why the F08-2 fix is a byte-compare chokepoint, not a blanket 'never save':
    it distinguishes the real per-day mutation from the no-op repeat."""
    server.world_tick(cid)  # first call: mutates last_tick_day -> saves (expected)
    import time as _t
    after_first = _updated_at(cid)
    _t.sleep(0.01)
    server.world_tick(cid)  # same day, nothing new due: a true no-op
    assert _updated_at(cid) == after_first, "a same-day repeat world_tick must not bump updated_at"


def test_check_companion_arc_pure_call_does_not_bump_updated_at(cid):
    """check_companion_arc with no companions/arcs is a pure read — no updated_at bump."""
    before = _updated_at(cid)
    res = server.check_companion_arc(cid)
    assert res["results"] == []
    assert _updated_at(cid) == before, "a no-op check_companion_arc must not bump updated_at"


def test_check_faction_arcs_pure_call_does_not_bump_updated_at(cid):
    """check_faction_arcs with no arcs is a pure read — no updated_at bump."""
    before = _updated_at(cid)
    res = server.check_faction_arcs(cid)
    assert res["results"] == []
    assert _updated_at(cid) == before, "a no-op check_faction_arcs must not bump updated_at"


def test_two_campaigns_pure_check_does_not_steal_live_pointer(tmp_path, monkeypatch):
    """End-to-end #640 class: with two campaigns coexisting, calling a pure check_* on
    the OLDER one must not make it the active (most-recently-updated) campaign."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    import time as _t
    a = server.create_campaign("Camp A")["id"]
    _t.sleep(0.01)
    b = server.create_campaign("Camp B")["id"]  # B is the live campaign
    assert store.active_campaign_id() == b

    # Pure inspections of the OLDER campaign (zero mutation -> must not save).
    server.check_consequences(a)
    server.check_companion_arc(a)
    server.check_faction_arcs(a)

    assert store.active_campaign_id() == b, (
        "pure check_* on camp A must not flip the live pointer to A"
    )
