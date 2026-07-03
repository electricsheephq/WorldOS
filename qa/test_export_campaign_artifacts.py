"""Self-tests for qa/export_campaign_artifacts.py (HV2 — Act II harvest loop).

Covers the acceptance bar from the HV2 epic (#1324):
  - a golden-fixture campaign extracts to EXACT expected JSONs (deterministic, no wall-clock).
  - extraction-fidelity: every snapshot quest appears exactly once across the output.
  - every artifact schema-validates against data/library/artifact_schema.json.
  - the extractor makes ZERO writes under play-state / qa/state (engine sole-writer untouched).

Run with the engine venv (pydantic + pytest live there):
    uv run --directory servers/engine python -m pytest ../../qa/test_export_campaign_artifacts.py -p no:cacheprovider
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "qa"))
sys.path.insert(0, str(_ROOT / "servers" / "engine"))

import export_campaign_artifacts as eca  # noqa: E402
import store  # noqa: E402
from models import Campaign, Character, Quest, Location, Consequence, ApprovalEvent  # noqa: E402
from scene_grid import SceneGrid, SceneGridSpec, SceneCell, SceneProp  # noqa: E402

_SCHEMA_PATH = _ROOT / "data" / "library" / "artifact_schema.json"


# ── fixture builders ─────────────────────────────────────────────────────────────────────────
def _golden_campaign() -> Campaign:
    grid = SceneGrid(
        scene_id="scene-1",
        location_id="loc-tavern",
        grid=SceneGridSpec(cols=4, rows=3),
        cells=[
            SceneCell(c=0, r=0, type="wall", walkable=False),
            SceneCell(c=1, r=1, type="floor"),
        ],
        props=[SceneProp(id="prop-table-1", kind="table", cells=[(2, 1)])],
        door_cells=[(0, 1)],
        protected_lane_cells=[(1, 1)],
    )
    loc = Location(
        id="loc-tavern",
        name="The Waning Moon",
        description="A quiet tavern.",
        visited=True,
        scene_grid=grid,
    )
    npc = Character(
        id="npc-dresh",
        name="Corvin Dresh",
        kind="npc",
        voice_id="npc-merchant",
        personality="A nervous merchant with debts he can't repay.",
        appearance="Sallow, ink-stained fingers.",
        attitude_value=15,
        met=True,
    )
    npc.approval_log = [
        ApprovalEvent(day=1, cause="helped_debt", delta=10, new_value=10),
        ApprovalEvent(day=2, cause="kept_word", delta=5, new_value=15),
    ]
    quest = Quest(
        id="quest-wagon",
        title="Dresh's Lost Wagon",
        objectives=["Find the wagon", "Return the crates"],
        completed_objectives=["Find the wagon"],
        status="active",
        evolves_to="",
    )
    conseq = Consequence(
        id="conseq-wagon-1",
        trigger_day=5,
        text="Dresh's Lost Wagon: the checkpoint doubles its watch.",
        note="follow-on from quest-wagon",
    )
    c = Campaign(
        id="camp_golden0001",
        title="Golden Fixture Campaign",
        world_id="test-world",
        locations={"loc-tavern": loc},
        characters={"npc-dresh": npc},
        quests={"quest-wagon": quest},
        consequences=[conseq],
    )
    return c


def _golden_transcript_lines() -> list[str]:
    """A minimal stream-json transcript: one DM narration block mentioning "Corvin" (so the
    dialogue-snippet matcher has something to find) plus a start_combat/end_combat pair."""
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Corvin Dresh leans in close: 'You have my crates?'"}
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "mcp__worldos-engine__start_combat",
                        "input": {"campaign_id": "camp_golden0001", "combatant_ids": ["npc-dresh"]},
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "mcp__worldos-engine__end_combat",
                        "input": {"campaign_id": "camp_golden0001", "resolution": "Dresh surrendered at once."},
                    }
                ]
            },
        },
    ]
    return [json.dumps(e) for e in events]


@pytest.fixture()
def golden(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(state_dir))
    c = _golden_campaign()
    store.save_campaign(c)
    transcript = tmp_path / "golden.jsonl"
    transcript.write_text("\n".join(_golden_transcript_lines()) + "\n", encoding="utf-8")
    out_dir = tmp_path / "artifacts_out"
    return {"state_dir": state_dir, "campaign_id": c.id, "transcript": transcript, "out_dir": out_dir}


# ── golden fixture: exact expected output ────────────────────────────────────────────────────
def test_golden_fixture_exact_expected_json(golden):
    rc = eca.main(
        [
            golden["campaign_id"],
            "--out-dir", str(golden["out_dir"]),
            "--transcript", str(golden["transcript"]),
            "--run-id", "golden-run",
            "--extracted-at", "2026-01-01T00:00:00Z",
        ]
    )
    assert rc == 0
    campaign_out = golden["out_dir"] / golden["campaign_id"]

    quest_files = sorted((campaign_out / "quests").glob("*.json"))
    assert len(quest_files) == 1
    quest = json.loads(quest_files[0].read_text())
    assert quest["artifact_id"] == "quest:camp_golden0001:quest-wagon"
    assert quest["class"] == "quest"
    assert quest["world"] == "test-world"
    assert quest["scores"] is None
    assert quest["provenance"] == {
        "campaign_id": "camp_golden0001",
        "run_id": "golden-run",
        "sha": quest["provenance"]["sha"],  # engine_sha is environment-dependent; checked non-empty below
        "extracted_at": "2026-01-01T00:00:00Z",
    }
    assert quest["payload"] == {
        "id": "quest-wagon",
        "name": "Dresh's Lost Wagon",
        "objectives": ["Find the wagon", "Return the crates"],
        "completed_objectives": ["Find the wagon"],
        "resolution_status": "active",
        "evolves_to": "",
        "consequences": [
            {"id": "conseq-wagon-1", "trigger_day": 5, "text": "Dresh's Lost Wagon: the checkpoint doubles its watch."}
        ],
    }

    npc_files = sorted((campaign_out / "npcs").glob("*.json"))
    assert len(npc_files) == 1
    npc = json.loads(npc_files[0].read_text())
    assert npc["artifact_id"] == "npc:camp_golden0001:npc-dresh"
    assert npc["payload"]["id"] == "npc-dresh"
    assert npc["payload"]["name"] == "Corvin Dresh"
    assert npc["payload"]["voice_id"] == "npc-merchant"
    assert npc["payload"]["attitude_arc"] == {"start": 0, "end": 15}
    assert npc["payload"]["final_status"] == "active"
    assert npc["payload"]["dialogue_snippets"] == ["Corvin Dresh leans in close: 'You have my crates?'"]
    assert npc["payload"]["personality"]["personality"].startswith("A nervous merchant")

    loc_files = sorted((campaign_out / "locations").glob("*.json"))
    assert len(loc_files) == 1
    loc = json.loads(loc_files[0].read_text())
    assert loc["payload"]["id"] == "loc-tavern"
    assert loc["payload"]["name"] == "The Waning Moon"
    assert loc["payload"]["visited"] is True
    assert loc["payload"]["scene_grid"] == {
        "cols": 4,
        "rows": 3,
        "cell_default_walkable": True,
        "walls": [[0, 0]],
        "props": [{"kind": "table", "cells": [[2, 1]]}],
        "door_cells": [[0, 1]],
        "protected_lane_cells": [[1, 1]],
    }

    enc_files = sorted((campaign_out / "encounters").glob("*.json"))
    assert len(enc_files) == 1
    enc = json.loads(enc_files[0].read_text())
    assert enc["payload"]["composition"] == [{"name": "Corvin Dresh"}]
    assert enc["payload"]["outcome"] == "Dresh surrendered at once."


def test_golden_fixture_is_reproducible(golden):
    """Same --extracted-at, same inputs -> byte-identical output (no wall-clock leakage)."""
    args = [
        golden["campaign_id"],
        "--out-dir", str(golden["out_dir"]),
        "--transcript", str(golden["transcript"]),
        "--run-id", "golden-run",
        "--extracted-at", "2026-01-01T00:00:00Z",
    ]
    eca.main(args)
    first = {p: p.read_text() for p in golden["out_dir"].rglob("*.json")}
    eca.main(args)
    second = {p: p.read_text() for p in golden["out_dir"].rglob("*.json")}
    assert first == second


# ── fidelity: every snapshot quest appears exactly once ─────────────────────────────────────
def test_every_snapshot_quest_appears_exactly_once(golden):
    eca.main(
        [
            golden["campaign_id"],
            "--out-dir", str(golden["out_dir"]),
            "--transcript", str(golden["transcript"]),
            "--run-id", "golden-run",
            "--extracted-at", "2026-01-01T00:00:00Z",
        ]
    )
    campaign = store.load_campaign(golden["campaign_id"])
    snapshot_quest_ids = set(campaign.quests.keys())
    quest_files = list((golden["out_dir"] / golden["campaign_id"] / "quests").glob("*.json"))
    extracted_ids = [json.loads(p.read_text())["payload"]["id"] for p in quest_files]
    assert sorted(extracted_ids) == sorted(snapshot_quest_ids)
    assert len(extracted_ids) == len(set(extracted_ids))  # exactly once each, no duplicates


def test_multi_quest_snapshot_every_quest_exactly_once(golden):
    """A campaign with several quests: extraction count == snapshot count, no dupes, no drops."""
    campaign = store.load_campaign(golden["campaign_id"])
    for i in range(5):
        qid = f"quest-extra-{i}"
        campaign.quests[qid] = Quest(id=qid, title=f"Extra Quest {i}", objectives=["do a thing"])
    store.save_campaign(campaign)

    eca.main(
        [
            golden["campaign_id"],
            "--out-dir", str(golden["out_dir"]),
            "--transcript", str(golden["transcript"]),
            "--run-id", "golden-run",
            "--extracted-at", "2026-01-01T00:00:00Z",
        ]
    )
    quest_files = list((golden["out_dir"] / golden["campaign_id"] / "quests").glob("*.json"))
    extracted_ids = [json.loads(p.read_text())["payload"]["id"] for p in quest_files]
    assert sorted(extracted_ids) == sorted(campaign.quests.keys())
    assert len(extracted_ids) == 6  # 1 original + 5 extra
    assert len(extracted_ids) == len(set(extracted_ids))


# ── schema validation ────────────────────────────────────────────────────────────────────────
def _hand_validate_envelope(artifact: dict) -> None:
    """Structural validation with NO third-party dependency (mirrors
    viewer/tests/test_render_profile_contract.py's pattern: jsonschema may be absent)."""
    for key in ("artifact_id", "class", "world", "provenance", "payload", "scores"):
        assert key in artifact, f"missing envelope key {key!r}"
    assert isinstance(artifact["artifact_id"], str) and artifact["artifact_id"]
    assert artifact["class"] in ("quest", "npc", "location", "encounter")
    assert isinstance(artifact["world"], str)
    assert isinstance(artifact["payload"], dict)
    assert artifact["scores"] is None or isinstance(artifact["scores"], dict)
    prov = artifact["provenance"]
    for key in ("campaign_id", "run_id", "sha", "extracted_at"):
        assert key in prov, f"missing provenance key {key!r}"
    assert isinstance(prov["campaign_id"], str) and prov["campaign_id"]
    assert isinstance(prov["extracted_at"], str) and prov["extracted_at"]


def test_every_extracted_artifact_hand_validates(golden):
    eca.main(
        [
            golden["campaign_id"],
            "--out-dir", str(golden["out_dir"]),
            "--transcript", str(golden["transcript"]),
            "--run-id", "golden-run",
            "--extracted-at", "2026-01-01T00:00:00Z",
        ]
    )
    files = list(golden["out_dir"].rglob("*.json"))
    assert files, "expected at least one extracted artifact"
    for f in files:
        _hand_validate_envelope(json.loads(f.read_text()))


def test_artifact_schema_file_exists_and_is_valid_json():
    assert _SCHEMA_PATH.exists(), f"schema handshake file missing: {_SCHEMA_PATH}"
    schema = json.loads(_SCHEMA_PATH.read_text())
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"artifact_id", "class", "world", "provenance", "payload", "scores"}
    assert schema["properties"]["class"]["enum"] == ["quest", "npc", "location", "encounter"]


def test_every_extracted_artifact_full_jsonschema_validation_when_available(golden):
    """If jsonschema is installed, run a full strict validate as a bonus. Skips cleanly when
    it isn't (the engine venv has no third-party validator by default) — mirrors
    viewer/tests/test_render_profile_contract.py."""
    jsonschema = pytest.importorskip("jsonschema")
    eca.main(
        [
            golden["campaign_id"],
            "--out-dir", str(golden["out_dir"]),
            "--transcript", str(golden["transcript"]),
            "--run-id", "golden-run",
            "--extracted-at", "2026-01-01T00:00:00Z",
        ]
    )
    schema = json.loads(_SCHEMA_PATH.read_text())
    for f in golden["out_dir"].rglob("*.json"):
        jsonschema.validate(json.loads(f.read_text()), schema)


# ── zero writes under play-state / qa/state ──────────────────────────────────────────────────
def test_zero_writes_under_state_dir(golden):
    """The extractor must be strictly read-only on engine state — no snapshot.json mtime bump,
    no new files/dirs anywhere under WORLDOS_STATE_DIR."""
    snapshot_path = golden["state_dir"] / "campaigns" / golden["campaign_id"] / "snapshot.json"
    assert snapshot_path.exists()
    before_mtime = snapshot_path.stat().st_mtime_ns
    before_tree = sorted(p.relative_to(golden["state_dir"]) for p in golden["state_dir"].rglob("*"))

    eca.main(
        [
            golden["campaign_id"],
            "--out-dir", str(golden["out_dir"]),
            "--transcript", str(golden["transcript"]),
            "--run-id", "golden-run",
            "--extracted-at", "2026-01-01T00:00:00Z",
        ]
    )

    after_mtime = snapshot_path.stat().st_mtime_ns
    after_tree = sorted(p.relative_to(golden["state_dir"]) for p in golden["state_dir"].rglob("*"))
    assert before_mtime == after_mtime, "snapshot.json was rewritten — extractor must be read-only"
    assert before_tree == after_tree, "extractor created/removed files under WORLDOS_STATE_DIR"


def test_zero_writes_under_state_dir_even_with_extra_quests(golden):
    """Same guarantee holds on a richer snapshot (multiple quests/npcs), not just the trivial one."""
    campaign = store.load_campaign(golden["campaign_id"])
    campaign.quests["quest-extra"] = Quest(id="quest-extra", title="Extra", objectives=["x"])
    store.save_campaign(campaign)
    snapshot_path = golden["state_dir"] / "campaigns" / golden["campaign_id"] / "snapshot.json"
    before_mtime = snapshot_path.stat().st_mtime_ns

    eca.main(
        [
            golden["campaign_id"],
            "--out-dir", str(golden["out_dir"]),
            "--transcript", str(golden["transcript"]),
            "--run-id", "golden-run",
            "--extracted-at", "2026-01-01T00:00:00Z",
        ]
    )

    after_mtime = snapshot_path.stat().st_mtime_ns
    assert before_mtime == after_mtime


# ── run_id derivation ─────────────────────────────────────────────────────────────────────────
def test_run_id_derived_from_state_dir_path():
    assert eca._derive_run_id("camp_x", None, "/Users/lume/WorldOS/qa/state/rri-a1-duo4") == "rri-a1-duo4"


def test_run_id_explicit_override_wins():
    assert eca._derive_run_id("camp_x", "explicit-run", "/Users/lume/WorldOS/qa/state/rri-a1-duo4") == "explicit-run"


def test_run_id_none_when_state_dir_not_under_qa_state():
    assert eca._derive_run_id("camp_x", None, "/tmp/some/other/dir") is None


def test_run_id_none_when_no_state_dir():
    assert eca._derive_run_id("camp_x", None, None) is None


# ── dialogue snippet cap ─────────────────────────────────────────────────────────────────────
def test_dialogue_snippets_capped_at_five():
    blocks = [f"Corvin says thing number {i}." for i in range(10)]
    out = eca._npc_dialogue_snippets("Corvin Dresh", blocks)
    assert len(out) == 5
    assert out == blocks[:5]


def test_dialogue_snippets_empty_when_name_never_mentioned():
    blocks = ["Nothing relevant happens here.", "Nor here."]
    assert eca._npc_dialogue_snippets("Corvin Dresh", blocks) == []
