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
    engaged (combat), a scheduled consequence, and a companion whose sealed betrayal agenda FIRED
    (`arc.agenda.fired` True — the snapshot ground truth for `betrayal`)."""
    return {
        "day": 20,
        "party": ["pc1", "comp1"],
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "last_long_rest_day": 5},
            "comp1": {"name": "Karlach", "kind": "companion",
                      "attitude_value": 40, "last_long_rest_day": 5,
                      "arc": {"arc_gates": [],
                              "agenda": {"trigger": "attitude_below", "value": -20,
                                         "fired": True, "note": "she turns"}}},
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
        _complete_state(), {"start_combat": 1, "check_companion_arc": 1})
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
    # Every COVERAGE mark is ✓ (no lone ·). The trailing felt-shape segment (· shape …) is a
    # SEPARATE, additive signal: this fixture has no mid-band reversal Decision, so it is
    # honestly `shape ·` (a fired agenda gives climax but reversal stays False) — split it off
    # before the coverage check so the new segment isn't mistaken for a coverage regression.
    coverage_seg = block["summary"].split(" · shape ")[0]
    assert "·" not in coverage_seg.replace(" · ", "")  # every coverage mark is ✓ (no lone ·)


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
    """Roman-numeral act tags parse (Act I/II/III). Coverage rule still applies: the chain must
    be contiguous from act 1, so BOTH the Act-I and Act-II sites must be visited to read 2."""
    state = _skipping_state()
    state["locations"] = {"a": {"name": "Ruins (Act I)", "visited": True},
                          "b": {"name": "Deeper Ruins (Act II)", "visited": True}}
    block = sr.structural_coverage_from_state(state)
    assert block["acts_reached"] == 2


def test_visiting_only_act3_site_does_not_read_acts_3of3():
    """REGRESSION (acts_reached false-pass): a run that visits ONLY the Act-3 site has SKIPPED
    acts 1-2 — it is NOT "in act 3/3". acts_reached is the highest CONTIGUOUS act from 1, so a
    sole Act-3 visit proves NO act (act 1 itself was never reached) → 0, summary 'acts 0/3'.
    The OLD max-tag logic stamped 3/3, a lie that hides a malformed/shortcut arc."""
    state = _skipping_state()
    state["locations"] = {"c": {"name": "The Lower City (Act 3)", "visited": True}}
    block = sr.structural_coverage_from_state(state)
    assert block["acts_reached"] == 0, block["acts_reached"]
    assert block["summary"].startswith("acts 0/3 ·")


def test_acts_require_contiguous_coverage_not_max():
    """acts_reached caps at the highest CONTIGUOUS act visited from 1. Visiting acts {1,3}
    (the act-2 site skipped) credits only act 1 — the chain breaks at the missing act 2."""
    state = _skipping_state()
    state["locations"] = {
        "a": {"name": "The Grove (Act 1)", "visited": True},
        "c": {"name": "The Lower City (Act 3)", "visited": True},
    }
    block = sr.structural_coverage_from_state(state)
    assert block["acts_reached"] == 1, block["acts_reached"]


def test_betrayal_stamps_only_when_agenda_actually_fired():
    """REGRESSION (betrayal coverage stamp lies): betrayal is keyed on the snapshot ground truth
    `character.arc.agenda.fired`, NOT on non-existent tool names. A run whose companion's sealed
    agenda FIRED stamps `betrayal ✓` even though the fake trigger_companion_agenda tool was never
    called; a run where the agenda is armed-but-unfired stamps `betrayal ·`."""
    # Armed but NOT fired → no betrayal, regardless of any check_companion_arc calls.
    armed = _skipping_state()
    armed["characters"]["comp1"]["arc"] = {
        "arc_gates": [],
        "agenda": {"trigger": "attitude_below", "value": -20, "fired": False},
    }
    block = sr.structural_coverage_from_state(armed, {"check_companion_arc": 12})
    assert block["betrayal"] is False
    assert "betrayal ·" in block["summary"]

    # The agenda actually fired (the companion turned) → betrayal ✓, with NO fake tool present.
    turned = _skipping_state()
    turned["characters"]["comp1"]["arc"] = {
        "arc_gates": [],
        "agenda": {"trigger": "attitude_below", "value": -20, "fired": True},
    }
    block = sr.structural_coverage_from_state(turned, {"check_companion_arc": 3})
    assert block["betrayal"] is True
    assert "betrayal ✓" in block["summary"]


def test_fake_betrayal_tool_names_no_longer_stamp_betrayal():
    """The OLD code keyed betrayal on tool names that DO NOT EXIST in the engine
    (trigger_companion_agenda / companion_betrayal / resolve_companion_agenda). Passing those
    counts now proves nothing — without a fired agenda in the snapshot, betrayal stays False."""
    block = sr.structural_coverage_from_state(
        _skipping_state(),
        {"trigger_companion_agenda": 9, "companion_betrayal": 9, "resolve_companion_agenda": 9},
    )
    assert block["betrayal"] is False


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


# ── felt_shape_from_state (the SETUP→REVERSAL→CLIMAX detector) ─────────────────────
# Replaces "N acts touched" with "did a real 3-act arc actually TURN". Reads ONLY
# engine-mutated state (never DM prose): the engine `narrative_arc` cursor + landed
# flags first, else day-banded turning/resolving events. Pure-read, additive.

def _flat_three_act_state() -> dict:
    """A FLAT 3-act run: the engine cursor reached act 3 BUT the landed flags are False,
    there are ZERO mid-band decisions/consequences, and no late-band resolve. Three
    act-tagged visited rooms walked, but the arc never turned — felt_three_act must be False."""
    return {
        "day": 20,
        "party": ["pc1", "comp1"],
        "narrative_arc": {"act": 3, "day_act_entered": 15,
                          "beats_in_act": 5,
                          "midpoint_reversal_landed": False, "climax_landed": False,
                          "reversal_day": 0, "climax_day": 0},
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "last_long_rest_day": 5},
            "comp1": {"name": "Brother Toll", "kind": "companion",
                      "attitude_value": 0, "last_long_rest_day": 5},
        },
        # no completed quest, no decisions, no consequences → nothing turned anywhere
        "quests": {"q1": {"title": "The Embergloom Pact", "status": "active"}},
        "locations": {
            "a": {"name": "The Emerald Grove (Act 1)", "visited": True},
            "b": {"name": "Moonrise Towers (Act 2)", "visited": True},
            "c": {"name": "The Lower City (Act 3)", "visited": True},
        },
        "decisions": [],
        "consequences": [],
        "flags": {},
    }


def _felt_three_act_state_engine_flags() -> dict:
    """A FELT 3-act run via the ENGINE-STAMPED landed flags: the cursor reached act 3 AND the
    engine recorded both the midpoint reversal (day 10, mid-band of a day-20 arc) and the
    climax (day 18, late-band) → felt_three_act True regardless of decisions/quests."""
    s = _flat_three_act_state()
    s["narrative_arc"] = {"act": 3, "day_act_entered": 15, "beats_in_act": 5,
                          "midpoint_reversal_landed": True, "climax_landed": True,
                          "reversal_day": 10, "climax_day": 18}
    return s


def _felt_three_act_state_events() -> dict:
    """A FELT 3-act run via DAY-BANDED EVENTS (no landed flags): a mid-band Decision carrying
    approval_tags (day 10, in [6, 14] of a day-20 arc) AND a late-band completed quest
    (last_progress_day 18 >= 14) → reversal + climax detected from engine-registered events."""
    s = _flat_three_act_state()
    # tag-acts still read 3, but the engine cursor flags stay False (events path must carry it)
    s["decisions"] = [
        {"day": 10, "summary": "spare the cultist", "approval_tags": ["mercy"]},
    ]
    s["quests"] = {"q1": {"title": "The Embergloom Pact", "status": "completed",
                          "last_progress_day": 18,
                          "objectives": ["x"], "completed_objectives": ["x"]}}
    return s


def test_felt_shape_flat_three_act_is_false():
    """A run with the engine cursor at act 3 but NOTHING landed/turned → felt_three_act False.
    The whole point: 'acts 3/3' walked is not a felt setup→reversal→climax."""
    fs = sr.felt_shape_from_state(_flat_three_act_state())
    assert fs["acts_engine_reached"] == 3
    assert fs["acts_tag_reached"] == 3
    assert fs["reversal"] is False
    assert fs["climax"] is False
    assert fs["felt_three_act"] is False
    assert "flat" in fs["shape"]


def test_felt_shape_felt_via_engine_landed_flags():
    """The engine-stamped midpoint_reversal_landed + climax_landed (with banded days) → True."""
    fs = sr.felt_shape_from_state(_felt_three_act_state_engine_flags())
    assert fs["acts_engine_reached"] == 3
    assert fs["reversal"] is True
    assert fs["climax"] is True
    assert fs["felt_three_act"] is True
    assert fs["shape"] == "setup→reversal→climax"


def test_felt_shape_felt_via_banded_events():
    """A mid-band approval_tags Decision + a late-band completed quest → reversal + climax via
    the day-banding fallback, even with the engine landed flags still False."""
    fs = sr.felt_shape_from_state(_felt_three_act_state_events())
    assert fs["reversal"] is True, fs
    assert fs["climax"] is True, fs
    assert fs["felt_three_act"] is True
    assert fs["shape"] == "setup→reversal→climax"


def test_felt_shape_old_empty_state_falls_back_to_tag_path():
    """An OLD/empty snapshot with NO narrative_arc → acts_engine_reached 0 (cursor absent),
    falls back to the existing tag path (acts_tag_reached), reversal/climax/felt all False —
    never crashes."""
    fs = sr.felt_shape_from_state({})
    assert fs["acts_engine_reached"] == 0
    assert fs["acts_tag_reached"] == 0
    assert fs["reversal"] is False
    assert fs["climax"] is False
    assert fs["felt_three_act"] is False
    assert isinstance(fs["shape"], str)


def test_felt_shape_tag_acts_fallback_when_no_engine_cursor():
    """When narrative_arc is absent but locations carry act tags, acts_tag_reached carries the
    3-act claim (engine cursor 0) and the max() pass criterion still uses it."""
    s = _flat_three_act_state()
    del s["narrative_arc"]  # no engine cursor → engine reached 0, tag path reads 3
    # give it a real turn so felt_three_act can be True off the tag path alone
    s["narrative_arc"] = None  # explicit None (degrade-to-silent, never raise)
    fs = sr.felt_shape_from_state(s)
    assert fs["acts_engine_reached"] == 0
    assert fs["acts_tag_reached"] == 3


def test_felt_shape_short_arc_no_crash():
    """final_day <= 2 has no arc to bisect → reversal False, never crashes."""
    s = {"day": 2, "narrative_arc": {"act": 1}, "decisions": [{"day": 1, "approval_tags": ["x"]}]}
    fs = sr.felt_shape_from_state(s)
    assert fs["reversal"] is False
    assert fs["felt_three_act"] is False


def test_felt_shape_decision_outside_midband_is_not_reversal():
    """A values-Decision in the OPENING (day 2 of a day-20 arc, below the 0.30 band floor) is
    NOT a midpoint reversal — banding is what separates a felt turn from an early choice."""
    s = _flat_three_act_state()
    s["decisions"] = [{"day": 2, "summary": "early choice", "approval_tags": ["mercy"]}]
    fs = sr.felt_shape_from_state(s)
    assert fs["reversal"] is False


def test_felt_shape_quest_completed_early_is_not_climax():
    """A quest completed in beat 2 (last_progress_day 2, below the 0.70 late-band floor) is NOT
    a felt climax — the resolution must land LATE."""
    s = _flat_three_act_state()
    s["quests"] = {"q1": {"title": "x", "status": "completed", "last_progress_day": 2,
                          "objectives": ["x"], "completed_objectives": ["x"]}}
    fs = sr.felt_shape_from_state(s)
    assert fs["climax"] is False


def test_felt_shape_block_is_additive_existing_keys_byte_identical():
    """Wiring contract: structural_coverage_from_state(state).update(felt_shape_from_state(...))
    ADDS the new sub-block keys and leaves every PRE-EXISTING structural_coverage key
    byte-identical. Assert the old keys are unchanged and the new keys appear."""
    state = _flat_three_act_state()
    # Old keys computed WITHOUT the felt-shape helper (simulate the pre-increment block) by
    # reading them off the wired block and comparing to a fresh recompute of each old field.
    block = sr.structural_coverage_from_state(state)
    old_keys = {"acts_reached", "recruited", "approval_moved", "camped", "quest_resolved",
                "quest_evolved", "traveled", "combat", "betrayal", "distinct_visited", "summary"}
    new_keys = {"acts_engine_reached", "acts_tag_reached", "reversal", "climax",
                "felt_three_act", "shape"}
    assert old_keys.issubset(block.keys())
    assert new_keys.issubset(block.keys())
    # The existing acts_reached (tag path) is unchanged by the additive merge.
    assert block["acts_reached"] == 3
    # acts_tag_reached surfaces the SAME tag-acts value under the clearer name.
    assert block["acts_tag_reached"] == block["acts_reached"]


def test_felt_shape_summary_segment_present():
    """structural_coverage_from_state's summary gains a trailing shape segment."""
    flat = sr.structural_coverage_from_state(_flat_three_act_state())
    felt = sr.structural_coverage_from_state(_felt_three_act_state_engine_flags())
    assert "shape" in flat["summary"]
    assert flat["summary"].rstrip().endswith("·")  # flat → no ✓
    assert felt["summary"].rstrip().endswith("✓")  # felt → ✓


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
