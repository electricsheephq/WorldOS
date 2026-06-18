"""Per-tool-call timing sidecar (Wave-1 1A) — observability contract tests.

The engine wraps every ``@mcp.tool()`` with a timing shim that, ONLY when
``WORLDOS_TOOLTIMING_PATH`` is set, appends one JSONL line per tool call to that
path. This file pins the three load-bearing guarantees:

  1. env SET   -> a matching JSONL line per call, all five keys, correct types,
                  and the ``tool`` name == the tool actually called.
  2. env UNSET -> NO file written and NO error (the production NO-OP path).
  3. a tool that RAISES still emits a timing line with ``ok: false`` (the
     ``finally:`` records failures too).

A sibling tool reads this sidecar, so the schema is a contract:
  {"ts": float, "tool": str, "wall_ms": float, "ok": bool,
   "campaign_id": str|None}
"""

import json

import pytest

import server


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    # Each test gets its own campaign-state dir so real tools persist cleanly.
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path / "state"))
    yield


def _read_lines(path):
    text = path.read_text(encoding="utf-8")
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


EXPECTED_KEYS = {"ts", "tool", "wall_ms", "ok", "campaign_id"}


def _assert_well_formed(rec):
    assert set(rec.keys()) == EXPECTED_KEYS, rec
    assert isinstance(rec["ts"], float)
    assert isinstance(rec["tool"], str) and rec["tool"]
    assert isinstance(rec["wall_ms"], float)
    assert rec["wall_ms"] >= 0.0
    assert isinstance(rec["ok"], bool)
    assert rec["campaign_id"] is None or isinstance(rec["campaign_id"], str)


def test_sidecar_records_each_tool_call(tmp_path, monkeypatch):
    sidecar = tmp_path / "tooltiming.jsonl"
    monkeypatch.setenv("WORLDOS_TOOLTIMING_PATH", str(sidecar))

    # Call a few REAL tools: a world-seed mutation then a read tool.
    res = server.start_world("baldurs-gate")
    cid = res["campaign_id"]
    server.look_around(cid)
    server.list_worlds()

    assert sidecar.exists(), "sidecar file was not written while env var was set"
    recs = _read_lines(sidecar)
    assert len(recs) == 3, f"expected one line per tool call, got {len(recs)}: {recs}"

    for rec in recs:
        _assert_well_formed(rec)

    by_tool = [r["tool"] for r in recs]
    assert by_tool == ["start_world", "look_around", "list_worlds"], by_tool

    # campaign_id best-effort: look_around's first positional arg is the campaign id.
    look = next(r for r in recs if r["tool"] == "look_around")
    assert look["campaign_id"] == cid
    assert look["ok"] is True

    start = next(r for r in recs if r["tool"] == "start_world")
    assert start["ok"] is True
    # start_world's first positional arg is a world_id (a str), so the best-effort
    # campaign_id grab returns that world_id verbatim — the contract is "first
    # positional str", not "a validated campaign id".
    assert start["campaign_id"] == "baldurs-gate"

    # list_worlds takes no args -> campaign_id is null.
    lw = next(r for r in recs if r["tool"] == "list_worlds")
    assert lw["campaign_id"] is None
    assert lw["ok"] is True


def test_no_sidecar_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("WORLDOS_TOOLTIMING_PATH", raising=False)
    would_be = tmp_path / "should_not_exist.jsonl"

    # Calling a tool must neither write a file nor raise.
    res = server.start_world("baldurs-gate")
    server.look_around(res["campaign_id"])

    assert not would_be.exists()
    # No file should appear anywhere in tmp from the timing writer.
    assert not list(tmp_path.glob("*.jsonl"))


def test_empty_env_is_noop(tmp_path, monkeypatch):
    # An empty (falsy) path must behave like unset — no write, no error.
    monkeypatch.setenv("WORLDOS_TOOLTIMING_PATH", "")
    server.start_world("baldurs-gate")
    assert not list(tmp_path.glob("*.jsonl"))


def test_failing_tool_still_records_ok_false(tmp_path, monkeypatch):
    sidecar = tmp_path / "tooltiming.jsonl"
    monkeypatch.setenv("WORLDOS_TOOLTIMING_PATH", str(sidecar))

    # look_around on a non-existent campaign raises (via _require) — the timing
    # line must still be emitted from the finally: block with ok=false.
    with pytest.raises(Exception):
        server.look_around("camp_does_not_exist")

    recs = _read_lines(sidecar)
    assert len(recs) == 1, recs
    rec = recs[0]
    _assert_well_formed(rec)
    assert rec["tool"] == "look_around"
    assert rec["ok"] is False
    assert rec["campaign_id"] == "camp_does_not_exist"
