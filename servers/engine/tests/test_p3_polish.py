"""P3 polish bundle (milestone v1.0.5) — cheap, additive, SRD-correct fixes from the
2026-06-11 full-engine adversarial audit. Each test pins one finding's fix.

Source: docs/audits/ENGINE-AUDIT-2026-06-11.md
Clusters: #814 (combat), #815 (character/spell), #816 (world/story), #817 (memory).

Findings covered here (the server-tool-level ones):
  F01-15  monster spawn transfers walk speed
  F01-16  saving_throw accepts situational advantage/disadvantage
  F04-13  long_rest camp-watch keyword matches as a SUBSTRING
  F07-6   log_event / persist_beat reject a typo'd (invisible) kind
"""

import pytest

import server


def _camp(tmp_path, monkeypatch, world="baldurs-gate"):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    return server.start_world(world)["campaign_id"]


# --- F01-15: monster speed never transferred (was always Character.speed default 30) ---

def test_spawn_monster_transfers_walk_speed(tmp_path, monkeypatch):
    cid = _camp(tmp_path, monkeypatch)
    out = server.spawn_monster(cid, "Wolf")  # SRD Wolf walks 40 ft
    mid = out["spawned"][0]["id"]
    assert server.get_character(cid, mid)["speed"] == 40   # was 30 on main


def test_spawn_monster_speed_defaults_30_when_walk_absent(tmp_path, monkeypatch):
    # A Goblin walks 30 (and any walk-less authored stat block keeps the 30 default) —
    # additive: the default path is unchanged.
    cid = _camp(tmp_path, monkeypatch)
    out = server.spawn_monster(cid, "Goblin")
    mid = out["spawned"][0]["id"]
    assert server.get_character(cid, mid)["speed"] == 30


# --- F01-16: saving_throw can now express situational advantage/disadvantage ------------

def test_saving_throw_accepts_advantage(tmp_path, monkeypatch):
    cid = _camp(tmp_path, monkeypatch)
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    out = server.saving_throw(cid, actor, "con", 10, advantage=True)
    assert out.get("advantage") is True
    assert out.get("disadvantage") is None


def test_saving_throw_caller_disadvantage_surfaced(tmp_path, monkeypatch):
    cid = _camp(tmp_path, monkeypatch)
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    out = server.saving_throw(cid, actor, "con", 10, disadvantage=True)
    assert out.get("disadvantage") is True


def test_saving_throw_adv_and_dis_cancel(tmp_path, monkeypatch):
    # 5e: one advantage + one disadvantage = a straight roll (neither flag surfaced).
    cid = _camp(tmp_path, monkeypatch)
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    out = server.saving_throw(cid, actor, "con", 10, advantage=True, disadvantage=True)
    assert out.get("advantage") is None and out.get("disadvantage") is None


def test_saving_throw_advantage_cancels_condition_disadvantage(tmp_path, monkeypatch):
    # Restrained gives DEX-save disadvantage; a caller advantage cancels it to a straight roll.
    cid = _camp(tmp_path, monkeypatch)
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    server.add_condition(cid, actor, "restrained")
    assert server.saving_throw(cid, actor, "dex", 10).get("disadvantage") is True  # baseline
    out = server.saving_throw(cid, actor, "dex", 10, advantage=True)
    assert out.get("disadvantage") is None and out.get("advantage") is None  # cancelled


def test_saving_throw_default_path_unchanged(tmp_path, monkeypatch):
    # Omitting the new kwargs is byte-identical to before: a plain save reports neither flag.
    cid = _camp(tmp_path, monkeypatch)
    actor = server.create_character(cid, "Actor", kind="player")["id"]
    out = server.saving_throw(cid, actor, "con", 10)
    assert "advantage" not in out and "disadvantage" not in out


# --- F04-13: camp-watch keyword credit is now SUBSTRING, not whole-string membership ----

def test_watch_is_careful_substring():
    assert server._watch_is_careful("we keep a careful watch") is True
    assert server._watch_is_careful("set a hidden camp in the brush") is True
    assert server._watch_is_careful("a bare-token camouflage") is True
    assert server._watch_is_careful("just standing around loudly") is False
    assert server._watch_is_careful("") is False


# --- F07-6: a typo'd log kind is rejected (it would be invisible to every recall filter) ---

def test_log_event_rejects_unknown_kind(tmp_path, monkeypatch):
    cid = _camp(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        server.log_event(cid, "narrative", "the gate groans open")  # typo of 'narration'


def test_log_event_accepts_canonical_kind(tmp_path, monkeypatch):
    cid = _camp(tmp_path, monkeypatch)
    out = server.log_event(cid, "narration", "the gate groans open")
    assert out["logged"]["kind"] == "narration"


def test_log_event_normalizes_kind_case(tmp_path, monkeypatch):
    cid = _camp(tmp_path, monkeypatch)
    out = server.log_event(cid, "Dialogue", "well met, traveler")
    assert out["logged"]["kind"] == "dialogue"


def test_persist_beat_rejects_unknown_event_kind(tmp_path, monkeypatch):
    cid = _camp(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        server.persist_beat(cid, events=[{"kind": "narrative", "text": "a beat"}])
