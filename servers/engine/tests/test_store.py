"""Tests for store.py — specifically the tolerant load_campaign path (#165).

GA-readiness: an old snapshot with an unrecognised top-level key (removed or
renamed field) must load successfully (with a WARNING) rather than hard-failing.
Sub-model strictness and normal round-trips must be unaffected.
"""

import json
import logging

import pytest

import store
from models import Campaign, SessionLogEntry


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_snapshot(tmp_path, monkeypatch, data: dict) -> str:
    """Write a raw snapshot dict to disk under a temp state dir and return the campaign id."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = data["id"]
    snap_dir = tmp_path / "campaigns" / cid
    snap_dir.mkdir(parents=True)
    (snap_dir / "snapshot.json").write_text(json.dumps(data), encoding="utf-8")
    return cid


def _minimal_snapshot(extra: dict | None = None) -> dict:
    """Return a dict that looks like a valid Campaign snapshot, with optional extras."""
    c = Campaign(title="Test Campaign")
    base = json.loads(c.model_dump_json())
    if extra:
        base.update(extra)
    return base


# ---------------------------------------------------------------------------
# (a) snapshot with an unknown extra top-level key loads without raising,
#     and the warning is emitted
# ---------------------------------------------------------------------------

def test_load_with_unknown_top_level_key_succeeds(tmp_path, monkeypatch, caplog):
    snapshot = _minimal_snapshot(extra={"removed_field_from_old_schema": "some_value"})
    cid = _write_snapshot(tmp_path, monkeypatch, snapshot)

    with caplog.at_level(logging.WARNING, logger="store"):
        result = store.load_campaign(cid)

    assert result is not None, "load_campaign should return a Campaign, not None"
    assert isinstance(result, Campaign)
    assert result.title == "Test Campaign"

    # The warning must name the dropped key
    warning_text = caplog.text
    assert "removed_field_from_old_schema" in warning_text, (
        f"Expected dropped key name in warning; got: {warning_text!r}"
    )


def test_load_with_multiple_unknown_keys_succeeds(tmp_path, monkeypatch, caplog):
    snapshot = _minimal_snapshot(extra={
        "old_field_one": 42,
        "legacy_debug_mode": True,
    })
    cid = _write_snapshot(tmp_path, monkeypatch, snapshot)

    with caplog.at_level(logging.WARNING, logger="store"):
        result = store.load_campaign(cid)

    assert result is not None
    assert isinstance(result, Campaign)
    warning_text = caplog.text
    assert "old_field_one" in warning_text
    assert "legacy_debug_mode" in warning_text


# ---------------------------------------------------------------------------
# (b) normal save→load round-trip works unchanged (no warning)
# ---------------------------------------------------------------------------

def test_normal_roundtrip(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))

    c = Campaign(title="Round-trip Campaign")
    store.save_campaign(c)

    with caplog.at_level(logging.WARNING, logger="store"):
        loaded = store.load_campaign(c.id)

    assert loaded is not None
    assert loaded.id == c.id
    assert loaded.title == "Round-trip Campaign"
    # No warning should be emitted for a clean round-trip
    assert caplog.text == "", f"Unexpected warning on clean round-trip: {caplog.text!r}"


# ---------------------------------------------------------------------------
# (c) snapshot missing an optional field still loads (it has a default)
# ---------------------------------------------------------------------------

def test_load_missing_optional_field(tmp_path, monkeypatch):
    snapshot = _minimal_snapshot()
    # Remove an optional field that has a default (e.g. summary, which defaults to "")
    snapshot.pop("summary", None)
    snapshot.pop("era", None)
    cid = _write_snapshot(tmp_path, monkeypatch, snapshot)

    result = store.load_campaign(cid)
    assert result is not None
    assert isinstance(result, Campaign)
    assert result.summary == ""
    assert result.era == ""


# ---------------------------------------------------------------------------
# (d) a snapshot that is GENUINELY incompatible (missing a required field
#     even after unknown-key stripping) re-raises with a clear message
# ---------------------------------------------------------------------------

def test_load_genuinely_incompatible_raises(tmp_path, monkeypatch):
    snapshot = _minimal_snapshot()
    # "title" is a required field with no default — removing it makes the parse
    # fail even after the unknown-key strip.
    del snapshot["title"]
    cid = _write_snapshot(tmp_path, monkeypatch, snapshot)

    with pytest.raises(RuntimeError, match="incompatible with the current schema"):
        store.load_campaign(cid)


# ---------------------------------------------------------------------------
# (e) observability version-stamp: save→load preserves schema_version +
#     engine_sha, and an OLD snapshot lacking both fields still loads (defaults)
# ---------------------------------------------------------------------------

def test_save_stamps_and_roundtrips_version_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))

    c = Campaign(title="Versioned Campaign")
    store.save_campaign(c)

    # The save path stamps engine_sha (== the cached resolver) and keeps schema_version set.
    assert c.engine_sha == store.engine_sha()
    assert c.schema_version == 1

    loaded = store.load_campaign(c.id)
    assert loaded is not None
    assert loaded.schema_version == c.schema_version
    assert loaded.engine_sha == c.engine_sha


def test_engine_sha_is_stamped_onto_disk(tmp_path, monkeypatch):
    """The serialized snapshot on disk carries engine_sha — the whole point of the stamp."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    c = Campaign(title="On-disk SHA")
    path = store.save_campaign(c)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "engine_sha" in on_disk
    assert "schema_version" in on_disk
    assert on_disk["engine_sha"] == store.engine_sha()
    assert on_disk["schema_version"] == 1


def test_load_old_snapshot_without_version_fields(tmp_path, monkeypatch):
    """An OLD snapshot predating the version-stamp (no schema_version / engine_sha keys at all)
    must still load, falling back to the model defaults — the additive-default contract."""
    snapshot = _minimal_snapshot()
    snapshot.pop("schema_version", None)
    snapshot.pop("engine_sha", None)
    assert "schema_version" not in snapshot and "engine_sha" not in snapshot
    cid = _write_snapshot(tmp_path, monkeypatch, snapshot)

    result = store.load_campaign(cid)
    assert result is not None
    assert isinstance(result, Campaign)
    # Defaults applied for the absent fields.
    assert result.schema_version == 1
    assert result.engine_sha == ""


# ---------------------------------------------------------------------------
# (f) read_log_all: campaign-wide narration reader (the lean-beat fix, #compact-
#     scene-context defect 2). Each lean beat opens a fresh session, so a tail of
#     the story must concatenate ALL session logs in chronological order.
# ---------------------------------------------------------------------------

def test_read_log_all_concatenates_sessions_in_order(tmp_path, monkeypatch):
    """read_log_all walks EVERY sessions/*.jsonl and returns them in canonical
    session order (session_ids first), within-file append order preserved — the
    cross-session continuity a single read_log misses under lean play."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = "camp-wide"

    store.append_log(cid, "s1", SessionLogEntry(t=1.0, kind="narration", text="a"))
    store.append_log(cid, "s1", SessionLogEntry(t=2.0, kind="dialogue", text="b"))
    store.append_log(cid, "s2", SessionLogEntry(t=3.0, kind="narration", text="c"))
    store.append_log(cid, "s2", SessionLogEntry(t=4.0, kind="narration", text="d"))

    # Per-session reads only see their own file (this is the lean trap: s2 read in
    # isolation never shows s1's prose).
    assert [e.text for e in store.read_log(cid, "s1")] == ["a", "b"]
    assert [e.text for e in store.read_log(cid, "s2")] == ["c", "d"]

    # Campaign-wide read stitches them in canonical chronological order.
    allg = store.read_log_all(cid, ["s1", "s2"])
    assert [e.text for e in allg] == ["a", "b", "c", "d"]


def test_read_log_all_empty_when_no_sessions(tmp_path, monkeypatch):
    """No sessions dir / no files → [] (never raises), so an early lean beat with
    nothing logged yet degrades to an empty tail."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    assert store.read_log_all("no-such-campaign", None) == []
    assert store.read_log_all("no-such-campaign", ["s1"]) == []


def test_read_log_all_includes_files_not_in_session_ids(tmp_path, monkeypatch):
    """A *.jsonl on disk not named in session_ids (orphan/external) is still read
    (defensive tail), so no committed prose is silently lost."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = "camp-orphan"
    store.append_log(cid, "listed", SessionLogEntry(t=1.0, kind="narration", text="listed"))
    store.append_log(cid, "orphan", SessionLogEntry(t=2.0, kind="narration", text="orphan"))

    texts = [e.text for e in store.read_log_all(cid, ["listed"])]
    assert "listed" in texts and "orphan" in texts


def test_read_log_all_is_read_only(tmp_path, monkeypatch):
    """Sole-writer invariant: read_log_all must not create or modify any file."""
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    cid = "camp-ro"
    store.append_log(cid, "s1", SessionLogEntry(t=1.0, kind="narration", text="x"))
    sessions_dir = tmp_path / "campaigns" / cid / "sessions"
    before = {p.name: p.stat().st_mtime_ns for p in sessions_dir.glob("*.jsonl")}

    store.read_log_all(cid, ["s1"])

    after = {p.name: p.stat().st_mtime_ns for p in sessions_dir.glob("*.jsonl")}
    assert before == after  # no writes, no new files
