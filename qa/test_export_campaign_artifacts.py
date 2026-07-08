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
    """A minimal stream-json transcript: one DM narration block with an IN-VOICE attributed quote
    from Corvin (so the voice-line miner has an attributable line), a quest wrap-up beat that names
    the quest AND carries resolution language, plus a start_combat/end_combat pair."""
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "\"You have my crates?\" Corvin Dresh asks, leaning in close."}
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "In the end, Dresh's Lost Wagon is resolved: the crates are returned and the debt repaid."}
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
        "description": "",
        "objectives": ["Find the wagon", "Return the crates"],
        "completed_objectives": ["Find the wagon"],
        "resolution_status": "active",
        "resolution": {
            "status": "active",
            "evolves_to": "",
            "callback_in_days": 0,
            "wrap_up": [
                "In the end, Dresh's Lost Wagon is resolved: the crates are returned and the debt repaid."
            ],
        },
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
    assert npc["payload"]["dialogue_snippets"] == ["You have my crates?"]
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
        "impassable": [[0, 0], [2, 1]],
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


def test_dialogue_snippets_skip_leading_article():
    """An NPC named 'The Emperor' must match on 'Emperor', not the article 'The' (which would
    otherwise contaminate almost every narration block)."""
    blocks = ["The door creaks open.", "The Emperor regards you coldly."]
    assert eca._npc_dialogue_snippets("The Emperor", blocks) == ["The Emperor regards you coldly."]


def test_dialogue_snippets_word_boundary_no_substring_false_positive():
    """First name 'Boo' must not match 'book' (word-boundary, not bare substring)."""
    blocks = ["She opens a dusty book.", "Boo squeaks in Minsc's pocket."]
    assert eca._npc_dialogue_snippets("Boo", blocks) == ["Boo squeaks in Minsc's pocket."]


# ── in-voice dialogue mining (HV2 #1329): the quality lever ──────────────────────────────────
def test_voice_lines_extracts_attributed_quote_not_narration():
    """An NPC that SPOKE carries the IN-VOICE quoted line, not the surrounding third-person prose.
    The attribution can precede ("Sefa says: ...") or follow ("...", Roe says) the quote."""
    blocks = [
        '"You have my crates?" Corvin asks, leaning in close.',
        'Sefa says, low and fast: "Take the coin and forget my name."',
    ]
    assert eca._npc_voice_lines("Corvin Dresh", blocks) == ["You have my crates?"]
    assert eca._npc_voice_lines("Sefa", blocks) == ["Take the coin and forget my name."]


def test_voice_lines_excludes_third_person_mention_without_speech():
    """A block that NAMES the NPC in combat/scene narration but is NOT them speaking must NOT be
    harvested — this is the measured false positive (a party-roster line 'Party (Maren 31/31)...')
    that the old mention-matcher wrongly kept, capping voice_distinctiveness under the rubric."""
    blocks = [
        "Party (Aldric 40/40, Maren 31/31) and all three hostiles confirmed in the cellar.",
        "Maren steps back as the ghoul lunges.",  # named, no quote, no speech verb
    ]
    assert eca._npc_voice_lines("Maren", blocks) == []


def test_voice_lines_requires_both_name_and_quote_proximity():
    """A quote NOT attributed to the NPC (another speaker in the window) isn't the NPC's line."""
    blocks = ['"Get back!" the captain barks at his men.']
    assert eca._npc_voice_lines("Corvin", blocks) == []


def test_voice_lines_deduped_and_capped():
    """Repeated identical lines dedupe; the result is capped at five distinct lines."""
    blocks = ['"Again," Corvin says.'] * 3 + [f'"Line {i} spoken now," Corvin says.' for i in range(6)]
    out = eca._npc_voice_lines("Corvin", blocks)
    assert out[0] == "Again,"
    assert len(out) == 5
    assert len(out) == len(set(out))


def test_voice_lines_curly_quotes_supported():
    blocks = ["“The debt is mine to settle,” Drast says, not a question."]
    assert eca._npc_voice_lines("Drast", blocks) == ["The debt is mine to settle,"]


def test_voice_lines_drops_mid_sentence_fragment():
    """A quote opening lowercase is a narration slice spliced through a quote mark, not a fresh
    spoken line — it is dropped even when name+verb are adjacent (the double-attribution defect)."""
    blocks = ['Corren says something about "that the tidy-book man is enjoying this far too much."']
    assert eca._npc_voice_lines("Corren", blocks) == []


def test_voice_lines_straight_single_quote_possessive_not_captured():
    """A straight apostrophe (possessive/contraction) is NOT a quote delimiter, so a block with no
    real double/curly-quoted speech yields nothing — 'Kervan's hand ... doesn't' is not a line."""
    blocks = ["Kervan's hand is still out, and he doesn't look away, Kervan says nothing."]
    assert eca._npc_voice_lines("Kervan", blocks) == []


def test_voice_lines_silent_npc_returns_empty():
    """A silent NPC (never speaks a quoted line) returns [] — a valid, graceful artifact."""
    assert eca._npc_voice_lines("Ghost", ["The room is empty and cold."]) == []
    assert eca._npc_voice_lines("", ['"hello," someone says.']) == []


# ── quest wrap-up mining (HV2 #1329) ─────────────────────────────────────────────────────────
def test_quest_wrap_up_captures_resolution_beat():
    """A closing beat that names the quest AND carries resolution language is captured."""
    blocks = [
        "The party sets out toward the Lower City.",  # mid-quest mention, no resolution cue
        "In the end, The Price of Silence is resolved: Oln's debt is repaid and the ledger burned.",
    ]
    out = eca._quest_wrap_up("The Price of Silence", blocks)
    assert out == ["In the end, The Price of Silence is resolved: Oln's debt is repaid and the ledger burned."]


def test_quest_wrap_up_ignores_mid_quest_mention_without_resolution_cue():
    blocks = ["Bresser Oln is somewhere in the Lower City, the Price of Silence still open."]
    assert eca._quest_wrap_up("The Price of Silence", blocks) == []


def test_quest_wrap_up_empty_when_never_resolved():
    """A quest with no closing beats returns [] (graceful) — not every quest wraps up on-screen."""
    assert eca._quest_wrap_up("The Four Hundred", ["Rumours of the Four Hundred drift through the market."]) == []
    assert eca._quest_wrap_up("", ["anything resolved here"]) == []


# ── combat scanner edge cases (dangling / consecutive / rejected / string-arg) ─────────────────
def _tool_use(name: str, inp: dict, tid: str = "") -> dict:
    block = {"type": "tool_use", "name": name, "input": inp}
    if tid:
        block["id"] = tid
    return {"type": "assistant", "message": {"content": [block]}}


def _tool_result(tid: str, *, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": is_error, "content": "ok"}]},
    }


def test_consecutive_start_combat_flushes_earlier_as_dangling():
    """Two start_combat with no end_combat between them: the first is emitted (outcome='')
    rather than silently dropped, honoring 'every started encounter is accounted for'."""
    lines = [
        json.dumps(_tool_use("mcp__x__start_combat", {"combatant_ids": ["a"]})),
        json.dumps(_tool_use("mcp__x__start_combat", {"combatant_ids": ["b"]})),
        json.dumps(_tool_use("mcp__x__end_combat", {"resolution": "won"})),
    ]
    encs = eca._combat_encounters_from_transcript(lines, {})
    assert [e["outcome"] for e in encs] == ["", "won"]
    assert [c["name"] for e in encs for c in e["composition"]] == ["a", "b"]


def test_start_combat_with_error_result_is_skipped():
    """A rejected start_combat (tool_result is_error=true) yields no fake encounter."""
    lines = [
        json.dumps(_tool_use("mcp__x__start_combat", {"combatant_ids": ["a"]}, tid="tu-1")),
        json.dumps(_tool_result("tu-1", is_error=True)),
        json.dumps(_tool_use("mcp__x__end_combat", {"resolution": "won"})),
    ]
    assert eca._combat_encounters_from_transcript(lines, {}) == []


def test_combatant_ids_bare_string_not_iterated_char_by_char():
    """combatant_ids logged as a bare/comma-separated STRING is coerced to a list, not iterated
    into single-character names (mirrors the engine's StrListArg coercion)."""
    lines = [
        json.dumps(_tool_use("mcp__x__start_combat", {"combatant_ids": "npc-a,npc-b"})),
        json.dumps(_tool_use("mcp__x__end_combat", {"resolution": "done"})),
    ]
    encs = eca._combat_encounters_from_transcript(lines, {})
    assert encs[0]["composition"] == [{"name": "npc-a"}, {"name": "npc-b"}]


def test_golden_fixture_exercises_dangling_and_consecutive(golden):
    """End-to-end on disk: a rejected start_combat (skipped), a consecutive start_combat that
    flushes the prior as dangling (outcome=''), and a final ended combat (outcome='resolved')."""
    lines = [
        json.dumps(_tool_use("mcp__x__start_combat", {"combatant_ids": ["npc-dresh"]}, tid="tu-bad")),
        json.dumps(_tool_result("tu-bad", is_error=True)),  # rejected -> no encounter
        json.dumps(_tool_use("mcp__x__start_combat", {"combatant_ids": ["npc-dresh"]})),  # opens A
        json.dumps(_tool_use("mcp__x__start_combat", {"combatant_ids": ["npc-dresh"]})),  # A dangles, opens B
        json.dumps(_tool_use("mcp__x__end_combat", {"resolution": "resolved"})),  # closes B
    ]
    golden["transcript"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    eca.main([
        golden["campaign_id"], "--out-dir", str(golden["out_dir"]),
        "--transcript", str(golden["transcript"]), "--run-id", "golden-run",
        "--extracted-at", "2026-01-01T00:00:00Z",
    ])
    enc_files = sorted((golden["out_dir"] / golden["campaign_id"] / "encounters").glob("*.json"))
    outcomes = sorted(json.loads(p.read_text())["payload"]["outcome"] for p in enc_files)
    assert outcomes == ["", "resolved"]  # dangling A + ended B; rejected start produced nothing


# ── downed NPC status ─────────────────────────────────────────────────────────────────────────
def test_npc_final_status_downed_when_hp_zero_and_not_dead_or_stable():
    assert eca._npc_final_status({"current_hp": 0, "dead": False, "stable": False}) == "downed"
    assert eca._npc_final_status({"current_hp": 0, "dead": True}) == "dead"
    assert eca._npc_final_status({"current_hp": 0, "stable": True}) == "stable"
    assert eca._npc_final_status({"current_hp": 5}) == "active"


# ── explicit missing transcript raises ─────────────────────────────────────────────────────────
def test_explicit_missing_transcript_raises(golden):
    with pytest.raises(FileNotFoundError):
        eca.main([
            golden["campaign_id"], "--out-dir", str(golden["out_dir"]),
            "--transcript", str(golden["transcript"].parent / "does-not-exist.jsonl"),
            "--run-id", "golden-run", "--extracted-at", "2026-01-01T00:00:00Z",
        ])


def test_absent_inferred_transcript_falls_back_to_empty(golden):
    """No --transcript and no inferrable file: extraction still succeeds with zero encounters."""
    rc = eca.main([
        golden["campaign_id"], "--out-dir", str(golden["out_dir"]),
        "--run-id", "no-such-run", "--extracted-at", "2026-01-01T00:00:00Z",
    ])
    assert rc == 0
    enc_files = list((golden["out_dir"] / golden["campaign_id"] / "encounters").glob("*.json"))
    assert enc_files == []


# ── stale-artifact cleanup on re-run ───────────────────────────────────────────────────────────
def test_rerun_clears_stale_artifacts(golden):
    """A quest removed from the snapshot between runs must not leave an orphaned JSON behind."""
    args = [
        golden["campaign_id"], "--out-dir", str(golden["out_dir"]),
        "--transcript", str(golden["transcript"]), "--run-id", "golden-run",
        "--extracted-at", "2026-01-01T00:00:00Z",
    ]
    campaign = store.load_campaign(golden["campaign_id"])
    campaign.quests["quest-temp"] = Quest(id="quest-temp", title="Temp", objectives=["x"])
    store.save_campaign(campaign)
    eca.main(args)
    quests_dir = golden["out_dir"] / golden["campaign_id"] / "quests"
    assert len(list(quests_dir.glob("*.json"))) == 2

    campaign = store.load_campaign(golden["campaign_id"])
    del campaign.quests["quest-temp"]
    store.save_campaign(campaign)
    eca.main(args)
    remaining = [json.loads(p.read_text())["payload"]["id"] for p in quests_dir.glob("*.json")]
    assert remaining == ["quest-wagon"]  # orphaned quest-temp.json cleared


# ── quality pass 2 (#1386 item 5b): sentence-boundary truncation ───────────────────────────────
def test_truncate_at_sentence_short_text_unchanged():
    assert eca._truncate_at_sentence("A short line.") == "A short line."


def test_truncate_at_sentence_cuts_at_boundary_not_mid_word():
    """The old blunt rule (`text[:399] + '…'`) clipped mid-word; the new rule finishes the
    sentence in progress at the soft limit instead of chopping through it."""
    lead = "Filler. " * 60  # well past the 400-char soft limit, all clean sentence boundaries
    text = lead + "The blade goes where you send it, clean and final. He drops the way a sack drops."
    out = eca._truncate_at_sentence(text, soft_limit=400, hard_limit=640)
    assert not out.endswith("…")
    assert out.endswith(".")
    # never chopped inside a word: the char right after the returned prefix (if any) is
    # whitespace, not a letter continuing a word.
    assert text[len(out):len(out) + 1] in ("", " ")


def test_truncate_at_sentence_no_boundary_falls_back_to_blunt_ellipsis():
    """Unpunctuated text with no sentence boundary anywhere in the window still gets bounded."""
    text = "word " * 200  # no . ! ? … anywhere
    out = eca._truncate_at_sentence(text, soft_limit=400, hard_limit=640)
    assert out.endswith("…")
    assert len(out) <= 400


def test_truncate_at_sentence_reused_by_wrap_up_and_dialogue_snippets():
    """The narration-mining functions route long text through the shared helper — regression
    guard against a future edit reintroducing a one-off blunt chop in any of them."""
    long_body = ("Winter has its teeth in the city tonight, and the debt collector walks in low "
                 "and fast, shoulder into the door before it finishes swinging. " * 6)
    blocks = [f"In the end, Wrap Test is resolved: {long_body}"]
    out = eca._quest_wrap_up("Wrap Test", blocks)
    assert len(out) == 1
    assert out[0][-1] in ".!?…\""  # never a mid-word chop with no terminal punctuation

    dlg_blocks = [f"Corvin Dresh says: {long_body}"]
    dlg = eca._npc_dialogue_snippets("Corvin Dresh", dlg_blocks)
    assert len(dlg) == 1
    assert dlg[0][-1] in ".!?…\""


# ── quality pass 2 (#1386 item 5b): giver / location grounding ─────────────────────────────────
def test_extract_quests_resolves_giver_and_location_when_recorded():
    """Quest.giver_id / Quest.location_id (recorded by the engine's add_quest, server.py) are
    resolved to {id, name} and surfaced on the payload — data the engine already tracks that the
    pre-pass-2 extractor dropped on the floor entirely."""
    campaign = {
        "id": "camp_x",
        "world_id": "test-world",
        "quests": {
            "q1": {
                "id": "q1", "title": "The Four Hundred", "description": "", "objectives": [],
                "completed_objectives": [], "status": "active", "evolves_to": "",
                "callback_in_days": 0, "giver_id": "char-davan", "location_id": "loc-sunkbell",
            }
        },
        "consequences": [],
        "characters": {"char-davan": {"name": "Davan Relle"}},
        "locations": {"loc-sunkbell": {"name": "The Sunk Bell"}},
    }
    out = eca.extract_quests(campaign, lambda: {}, "test-world", [])
    payload = out[0]["payload"]
    assert payload["giver"] == {"id": "char-davan", "name": "Davan Relle"}
    assert payload["location"] == {"id": "loc-sunkbell", "name": "The Sunk Bell"}


def test_extract_quests_omits_giver_and_location_when_not_recorded():
    """A quest whose engine record never set giver_id/location_id (some DMs call add_quest
    without them) omits both keys entirely — no None placeholder, no invented linkage — so an
    old snapshot round-trips to the exact payload shape it produced before this change."""
    campaign = {
        "id": "camp_x",
        "world_id": "test-world",
        "quests": {
            "q1": {
                "id": "q1", "title": "No Giver", "description": "", "objectives": [],
                "completed_objectives": [], "status": "active", "evolves_to": "",
                "callback_in_days": 0,
            }
        },
        "consequences": [],
        "characters": {},
        "locations": {},
    }
    out = eca.extract_quests(campaign, lambda: {}, "test-world", [])
    payload = out[0]["payload"]
    assert "giver" not in payload
    assert "location" not in payload


def test_extract_quests_giver_falls_back_to_id_when_character_unresolvable():
    """A giver_id that doesn't resolve to a known character (stale ref) falls back to the raw id
    rather than raising or silently dropping the field — mirrors the encounter composition
    resolver's `characters.get(i, {}).get('name', i)` fallback pattern used elsewhere."""
    campaign = {
        "id": "camp_x",
        "world_id": "test-world",
        "quests": {
            "q1": {
                "id": "q1", "title": "Stale Ref", "description": "", "objectives": [],
                "completed_objectives": [], "status": "active", "evolves_to": "",
                "callback_in_days": 0, "giver_id": "char-ghost",
            }
        },
        "consequences": [],
        "characters": {},
        "locations": {},
    }
    out = eca.extract_quests(campaign, lambda: {}, "test-world", [])
    assert out[0]["payload"]["giver"] == {"id": "char-ghost", "name": "char-ghost"}
