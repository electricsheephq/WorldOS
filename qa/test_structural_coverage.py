"""Self-tests for the per-run STRUCTURAL-COVERAGE block (the owner's "full circle"; pairs with
the #961 structural_completeness gate).

Covers:
  * story_readout.structural_coverage_from_state — the shared helper that derives the block from
    the engine snapshot (ground truth) + DM tool counts. A system-skipping run must yield a
    LOW/false block; a complete run must yield acts 3/3 + all ✓.
  * qa/inject_structural_coverage.py — the sweep-side merge: resolve the snapshot + DM transcript
    from a play-state store, compute the block, merge it into score.json additively.
  * qa/ui_playtest_score.py — the run-dir-resolvable path (when meta.json pins a state dir).

Stdlib + pytest only; self-contained. Run single-process:
    uv run --directory servers/engine python -m pytest qa/test_structural_coverage.py -p no:xdist
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import story_readout as sr  # noqa: E402
import inject_structural_coverage as inj  # noqa: E402

QA = Path(__file__).resolve().parent


# ── fixtures ────────────────────────────────────────────────────────────────────

def _skipping_state() -> dict:
    """A SYSTEM-SKIPPING run's final snapshot: a companion is in the party but stuck at
    attitude 0, no one ever long-rested, a multi-location quest is left `active`, locations are
    untagged (no authored acts), no monsters/consequences. The owner's failure shape."""
    return {
        "day": 5,
        "party": ["pc1", "comp1"],
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "last_long_rest_day": -1},
            "comp1": {"name": "Brother Toll", "kind": "companion",
                      "attitude_value": 0, "last_long_rest_day": -1},
        },
        "quests": {"q1": {"title": "The Embergloom Pact", "status": "active"}},
        "locations": {"a": {"name": "The Grove", "visited": True},
                      "b": {"name": "The Road", "visited": True}},
        "consequences": [],
    }


def _complete_state() -> dict:
    """A COMPLETE run's final snapshot: companion recruited + approval moved + camped, a quest
    resolved AND carrying an `evolves_to`, three AUTHORED act-tagged visited locations, a monster
    engaged (combat), a scheduled consequence."""
    return {
        "day": 20,
        "party": ["pc1", "comp1"],
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "last_long_rest_day": 5},
            "comp1": {"name": "Karlach", "kind": "companion",
                      "attitude_value": 40, "last_long_rest_day": 5},
            "mon1": {"name": "Goblin Boss", "kind": "monster"},
        },
        "quests": {"q1": {"title": "The Embergloom Pact", "status": "completed",
                          "evolves_to": "the cult regroups"}},
        "locations": {
            "a": {"name": "The Emerald Grove (Act 1)", "visited": True},
            "b": {"name": "Moonrise Towers (Act 2)", "visited": True},
            "c": {"name": "The Lower City (Act 3)", "visited": True},
        },
        "consequences": [{"due": 21, "text": "the cult regroups"}],
    }


# ── structural_coverage_from_state ───────────────────────────────────────────────

def test_skipping_run_yields_low_block():
    block = sr.structural_coverage_from_state(_skipping_state(), {})
    # The DEAD systems must read false — the whole point of the tracker.
    assert block["approval_moved"] is False
    assert block["camped"] is False
    assert block["quest_resolved"] is False
    assert block["quest_evolved"] is False
    assert block["combat"] is False  # no monster, no start_combat
    assert block["betrayal"] is False
    # Untagged 2-location arc: traveled True, but acts can only be PROVEN at 1.
    assert block["traveled"] is True
    assert block["acts_reached"] == 1
    # A companion IS in the party (recruit happened) — the failure is the frozen relationship,
    # not the recruit. recruit ✓ but everything downstream ·.
    assert block["recruited"] is True
    assert "·" in block["summary"]


def test_complete_run_yields_full_block():
    block = sr.structural_coverage_from_state(
        _complete_state(), {"start_combat": 1, "trigger_companion_agenda": 1})
    assert block["acts_reached"] == 3
    assert block["recruited"] is True
    assert block["approval_moved"] is True
    assert block["camped"] is True
    assert block["quest_resolved"] is True
    assert block["quest_evolved"] is True
    assert block["traveled"] is True
    assert block["combat"] is True
    assert block["betrayal"] is True
    assert block["summary"].startswith("acts 3/3 ·")
    assert "·" not in block["summary"].replace(" · ", "")  # every mark is ✓ (no lone ·)


def test_acts_from_authored_tags_partial():
    """An arc that reached Act 2 (tagged) but not Act 3 reports acts_reached == 2 — distinct
    act-tags over the VISITED location names, max wins."""
    state = _complete_state()
    state["locations"] = {
        "a": {"name": "The Grove (Act 1)", "visited": True},
        "b": {"name": "Moonrise (Act 2)", "visited": True},
        "c": {"name": "The City (Act 3)", "visited": False},  # NOT visited → not counted
    }
    block = sr.structural_coverage_from_state(state)
    assert block["acts_reached"] == 2


def test_roman_numeral_act_tags():
    state = _skipping_state()
    state["locations"] = {"a": {"name": "Ruins (Act II)", "visited": True}}
    block = sr.structural_coverage_from_state(state)
    assert block["acts_reached"] == 2


def test_combat_from_snapshot_when_no_tool_counts():
    """With tool_counts=None, combat falls back to the snapshot (a kind=monster engaged)."""
    state = _skipping_state()
    state["characters"]["mon1"] = {"name": "Goblin", "kind": "monster"}
    block = sr.structural_coverage_from_state(state, None)
    assert block["combat"] is True


def test_quest_evolved_via_scheduled_consequence():
    """quest_evolved fires when the engine SCHEDULED a follow-on consequence, even if the
    completed quest carries no evolves_to seed."""
    state = _complete_state()
    state["quests"]["q1"]["evolves_to"] = ""
    state["consequences"] = [{"due": 30}]
    block = sr.structural_coverage_from_state(state)
    assert block["quest_evolved"] is True


def test_empty_state_is_all_false_not_crash():
    block = sr.structural_coverage_from_state({})
    assert block["acts_reached"] == 0
    assert block["recruited"] is False
    assert block["traveled"] is False
    assert isinstance(block["summary"], str)


def test_list_shaped_collections_are_handled():
    """Some snapshots serialize quests/locations as lists, not dicts — the helper must cope."""
    state = {
        "party": [],
        "characters": [{"name": "Toll", "kind": "companion", "attitude_value": 10}],
        "quests": [{"title": "x", "status": "completed", "evolves_to": "y"}],
        "locations": [{"name": "A (Act 1)", "visited": True}, {"name": "B (Act 2)", "visited": True}],
    }
    block = sr.structural_coverage_from_state(state)
    assert block["approval_moved"] is True
    assert block["quest_resolved"] is True
    assert block["acts_reached"] == 2
    assert block["recruited"] is True  # companion present, no party list → recruited


# ── inject_structural_coverage.py (the sweep-side merge) ──────────────────────────

def _make_store(tmp_path: Path, state: dict, transcript_lines: list[dict] | None = None) -> Path:
    store = tmp_path / "store"
    camp = store / "campaigns" / "camp1"
    camp.mkdir(parents=True)
    (camp / "snapshot.json").write_text(json.dumps(state), encoding="utf-8")
    if transcript_lines is not None:
        (store / "dm.combined.jsonl").write_text(
            "\n".join(json.dumps(x) for x in transcript_lines), encoding="utf-8")
    return store


def _tool_use(name: str) -> dict:
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": f"mcp__engine__{name}", "input": {}}]}}


def test_injector_merges_block_and_prints_summary(tmp_path):
    store = _make_store(tmp_path, _skipping_state(), [_tool_use("start_combat")])
    score = tmp_path / "score.json"
    score.write_text(json.dumps({"persona": "newbie", "pass": False}), encoding="utf-8")
    block = inj.compute(str(store))
    assert block is not None
    # combat came from the transcript's start_combat (no monster in the snapshot roster).
    assert block["combat"] is True
    assert block["approval_moved"] is False

    # Full CLI: merges additively, preserves existing fields, prints the summary.
    out = subprocess.run(
        [sys.executable, str(QA / "inject_structural_coverage.py"), str(score), str(store)],
        capture_output=True, text=True, check=True)
    merged = json.loads(score.read_text(encoding="utf-8"))
    assert merged["persona"] == "newbie"          # untouched
    assert merged["pass"] is False                # untouched
    assert "structural_coverage" in merged        # added
    assert merged["structural_coverage"]["combat"] is True
    assert out.stdout.strip().startswith("acts 1/3 ·")


def test_injector_noop_without_snapshot(tmp_path):
    """No snapshot under the store → no block, no merge, no crash (honest no-op)."""
    empty = tmp_path / "emptystore"
    empty.mkdir()
    assert inj.compute(str(empty)) is None
    score = tmp_path / "score.json"
    score.write_text(json.dumps({"persona": "x"}), encoding="utf-8")
    subprocess.run(
        [sys.executable, str(QA / "inject_structural_coverage.py"), str(score), str(empty)],
        capture_output=True, text=True, check=True)
    # score.json untouched (no structural_coverage key injected).
    assert "structural_coverage" not in json.loads(score.read_text(encoding="utf-8"))


def test_injector_picks_largest_snapshot(tmp_path):
    """A lock-only orphan campaign dir with an empty snapshot must not win over the real save."""
    store = tmp_path / "store"
    (store / "campaigns" / "orphan").mkdir(parents=True)
    (store / "campaigns" / "orphan" / "snapshot.json").write_text("", encoding="utf-8")
    real = store / "campaigns" / "real"
    real.mkdir(parents=True)
    real.joinpath("snapshot.json").write_text(json.dumps(_complete_state()), encoding="utf-8")
    block = inj.compute(str(store))
    assert block is not None and block["acts_reached"] == 3


# ── ui_playtest_score.py run-dir-resolvable path ──────────────────────────────────

def test_score_stamps_structural_when_snapshot_in_rundir(tmp_path):
    """When meta.json pins a state dir holding a snapshot, ui_playtest_score.py stamps the block
    into score.json directly (the in-run-dir / engine-duo layout)."""
    rundir = tmp_path / "run1"
    (rundir / "player").mkdir(parents=True)
    statedir = rundir / "state"
    camp = statedir / "campaigns" / "c1"
    camp.mkdir(parents=True)
    camp.joinpath("snapshot.json").write_text(json.dumps(_complete_state()), encoding="utf-8")
    (rundir / "meta.json").write_text(
        json.dumps({"run": "run1", "persona": "veteran", "world": "baldurs-gate",
                    "state_dir": str(statedir)}), encoding="utf-8")
    # minimal player artifacts so the scorer runs
    (rundir / "player" / "actions.ndjson").write_text("", encoding="utf-8")

    rc = subprocess.run(
        [sys.executable, str(QA / "ui_playtest_score.py"), str(rundir)],
        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr
    score = json.loads((rundir / "score.json").read_text(encoding="utf-8"))
    assert "structural_coverage" in score
    assert score["structural_coverage"]["acts_reached"] == 3


def test_score_omits_structural_when_no_snapshot(tmp_path):
    """No resolvable snapshot from the run dir → score.json simply lacks the key (the sweep
    injects it for those runs). Additive: the rest of the score is unchanged."""
    rundir = tmp_path / "run2"
    (rundir / "player").mkdir(parents=True)
    (rundir / "meta.json").write_text(
        json.dumps({"run": "run2", "persona": "newbie", "world": "baldurs-gate"}),
        encoding="utf-8")
    (rundir / "player" / "actions.ndjson").write_text("", encoding="utf-8")
    rc = subprocess.run(
        [sys.executable, str(QA / "ui_playtest_score.py"), str(rundir)],
        capture_output=True, text=True)
    assert rc.returncode == 0, rc.stderr
    score = json.loads((rundir / "score.json").read_text(encoding="utf-8"))
    assert "structural_coverage" not in score
    assert score["persona"] == "newbie"  # rest of the score intact
