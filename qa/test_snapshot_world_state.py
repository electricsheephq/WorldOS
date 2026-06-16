"""Tests for qa/snapshot_world_state.py — the read-only golden-state extractor.

These lock the THREE regression-detection contracts the extractor exists to
provide:

  (i)  DETERMINISM — the same snapshot hashes identically across two calls (the
       projection is canonically ordered, so a re-run never spuriously diffs).
  (ii) CANONICAL + FICTION-EXCLUDED — reordering a snapshot's dict keys, or
       adding a fiction/narration/prose field (or noise like timestamps / RNG
       seed), does NOT change the hash. Only engine-mutated, regression-relevant
       state is projected.
  (iii) SENSITIVE — flipping an engine-mutated regression value (a flag, a
       faction reputation, a quest stage/status, an NPC attitude, the day) DOES
       change the hash, so a real regression is caught.

The extractor is READ-ONLY: it never imports the engine writer path and never
touches disk except to read the snapshot file it is handed.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Import the module-under-test by file path. It lives beside this test in qa/;
# we don't rely on qa/ being on sys.path (the engine's pytest pythonpath is the
# engine dir), so load it explicitly.
_QA_DIR = Path(__file__).resolve().parent
_MOD_PATH = _QA_DIR / "snapshot_world_state.py"
_spec = importlib.util.spec_from_file_location("snapshot_world_state", _MOD_PATH)
assert _spec and _spec.loader
snapshot_world_state = importlib.util.module_from_spec(_spec)
sys.modules["snapshot_world_state"] = snapshot_world_state
_spec.loader.exec_module(snapshot_world_state)

project_snapshot = snapshot_world_state.project_snapshot
golden_hash = snapshot_world_state.golden_hash
load_snapshot = snapshot_world_state.load_snapshot


def _fixture_snapshot() -> dict:
    """A minimal but representative campaign snapshot dict.

    Uses the engine's real Campaign field names so the extractor's projection is
    exercised against the actual schema. Carries each regression-relevant family
    (flags / faction reputation+standing / quest status+stage / NPC
    attitude_value / day+time_of_day / quest_outcomes / party roster / world_state
    facts) PLUS plenty of fiction/prose/timestamp/RNG noise that MUST be excluded.
    """
    return {
        "id": "camp_fixture",
        "title": "The Embergloom Pact",
        "summary": "Long prose summary the DM narrates — pure fiction, must be excluded.",
        "ruleset": "SRD 5.2",
        # --- noise that MUST NOT affect the golden hash ---
        "created_at": 1700000000.0,
        "updated_at": 1700009999.5,
        "engine_sha": "abc1234",
        # --- engine-mutated, regression-relevant state ---
        "day": 7,
        "time_of_day": "evening",
        "flags": {"prize_seized": True, "gate_open": False},
        "quest_outcomes": {"main_arc": "tyranny_ending"},
        "last_combat_resolution": "fled",
        "party": ["char_pc", "char_companion"],
        "world_state": {
            "world_tenor": "grim",
            "facts": {"netherbrain": "claimed", "the_emperor": "slain"},
        },
        "characters": {
            "char_pc": {
                "id": "char_pc",
                "name": "Rolan",
                "kind": "player",
                "current_hp": 18,
                "max_hp": 24,
                "xp": 900,
                "attitude": "guarded",
                "attitude_value": 0,
                "personality": "Prose about Rolan the wizard — fiction, excluded.",
                "backstory": "A long backstory the DM voices — fiction, excluded.",
            },
            "char_npc": {
                "id": "char_npc",
                "name": "Shadowheart",
                "kind": "npc",
                "attitude": "warming",
                "attitude_value": 35,
                "met": True,
                "memory": ["She remembers the night raid — fiction, excluded."],
                "notes": "DM notes prose — excluded.",
            },
        },
        "quests": {
            "q_main": {
                "id": "q_main",
                "title": "Break the Pact",
                "description": "Long quest prose — fiction, excluded.",
                "status": "active",
                "objectives": ["find the relic", "confront the warlock"],
                "completed_objectives": ["find the relic"],
            }
        },
        "factions": {
            "fac_harpers": {
                "id": "fac_harpers",
                "name": "The Harpers",
                "description": "Faction lore prose — fiction, excluded.",
                "reputation": 25,
                "standing": 10,
                "rank": 2,
                "joined": True,
            }
        },
        # A fiction-heavy block that must be wholly excluded from the projection.
        "lore": ["Page one of world-bible prose.", "Page two of world-bible prose."],
        "scenes": [{"read_aloud": "Boxed text the DM reads aloud.", "dm_notes": "secret"}],
        "session_ids": ["sess_a", "sess_b"],
        "active_session_id": "sess_b",
    }


def test_determinism_same_snapshot_hashes_identically(tmp_path):
    snap = _fixture_snapshot()
    p = tmp_path / "snapshot.json"
    p.write_text(json.dumps(snap), encoding="utf-8")

    h1 = golden_hash(load_snapshot(p))
    h2 = golden_hash(load_snapshot(p))
    assert h1 == h2
    assert isinstance(h1, str) and len(h1) == 64  # sha256 hexdigest


def test_reordering_keys_does_not_change_hash():
    snap = _fixture_snapshot()
    # Build a deeply key-reordered copy: reverse the insertion order of every dict.
    def reorder(obj):
        if isinstance(obj, dict):
            return {k: reorder(obj[k]) for k in reversed(list(obj.keys()))}
        if isinstance(obj, list):
            return [reorder(x) for x in obj]
        return obj

    reordered = reorder(snap)
    assert list(reordered.keys()) != list(snap.keys())  # genuinely reordered
    assert golden_hash(reordered) == golden_hash(snap)


def test_adding_fiction_field_does_not_change_hash():
    snap = _fixture_snapshot()
    base = golden_hash(snap)

    noisy = copy.deepcopy(snap)
    # Add / mutate ONLY fiction / prose / narration / timestamp / RNG noise.
    noisy["summary"] = "A completely different summary — still fiction."
    noisy["lore"].append("A brand-new lore page.")
    noisy["scenes"].append({"read_aloud": "New boxed text."})
    noisy["updated_at"] = 9999999999.0
    noisy["created_at"] = 1.0
    noisy["engine_sha"] = "deadbeef"
    noisy["rng_seed"] = 424242  # unknown noise key
    noisy["recent_narration"] = "The torch gutters as you descend the stair."
    noisy["characters"]["char_pc"]["personality"] = "rewritten personality prose"
    noisy["characters"]["char_pc"]["backstory"] = "rewritten backstory prose"
    noisy["characters"]["char_npc"]["memory"].append("a new remembered fiction beat")
    noisy["quests"]["q_main"]["description"] = "rewritten quest prose"
    noisy["factions"]["fac_harpers"]["description"] = "rewritten faction prose"

    assert golden_hash(noisy) == base


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s: s["flags"].update({"prize_seized": False}), id="flag_flip"),
        pytest.param(lambda s: s["factions"]["fac_harpers"].update({"reputation": -50}), id="reputation"),
        pytest.param(lambda s: s["factions"]["fac_harpers"].update({"standing": 99}), id="standing"),
        pytest.param(lambda s: s["quests"]["q_main"].update({"status": "completed"}), id="quest_status"),
        pytest.param(
            lambda s: s["quests"]["q_main"]["completed_objectives"].append("confront the warlock"),
            id="quest_stage",
        ),
        pytest.param(lambda s: s["characters"]["char_npc"].update({"attitude_value": -40}), id="attitude_value"),
        pytest.param(lambda s: s.update({"day": 99}), id="day"),
        pytest.param(lambda s: s.update({"time_of_day": "midnight"}), id="time_of_day"),
        pytest.param(lambda s: s["quest_outcomes"].update({"main_arc": "hope_ending"}), id="quest_outcome"),
        pytest.param(
            lambda s: s["world_state"]["facts"].update({"the_emperor": "allied"}),
            id="world_state_fact",
        ),
        pytest.param(lambda s: s["party"].append("char_new"), id="party_roster"),
        pytest.param(lambda s: s.update({"last_combat_resolution": "captured"}), id="combat_resolution"),
    ],
)
def test_changing_regression_value_changes_hash(mutate):
    snap = _fixture_snapshot()
    base = golden_hash(snap)
    mutated = copy.deepcopy(snap)
    mutate(mutated)
    assert mutated != snap  # the mutation actually changed the dict
    assert golden_hash(mutated) != base


def test_projection_excludes_fiction_keys():
    """The projection json must not carry any prose/narration field verbatim."""
    snap = _fixture_snapshot()
    proj = project_snapshot(snap)
    blob = json.dumps(proj)
    for forbidden in (
        "world-bible prose",
        "Boxed text",
        "backstory the DM voices",
        "Prose about Rolan",
        "Long quest prose",
        "Faction lore prose",
        "night raid",
    ):
        assert forbidden not in blob, f"fiction leaked into projection: {forbidden!r}"


def test_cli_hash_only(tmp_path):
    """The CLI prints a bare 64-char sha256 with --hash-only, and JSON otherwise."""
    import subprocess

    snap = _fixture_snapshot()
    p = tmp_path / "snapshot.json"
    p.write_text(json.dumps(snap), encoding="utf-8")

    out_hash = subprocess.run(
        [sys.executable, str(_MOD_PATH), str(p), "--hash-only"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert len(out_hash) == 64
    assert out_hash == golden_hash(snap)

    out_json = subprocess.run(
        [sys.executable, str(_MOD_PATH), str(p)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # The non-hash output is the canonical projection JSON.
    parsed = json.loads(out_json)
    assert parsed == project_snapshot(snap)
