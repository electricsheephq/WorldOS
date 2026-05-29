"""Self-tests for qa/assert_behavioral.py — the new Section-A gates (audit-tests.md).

Covers the `_tool_events` result-side parser plus the three new FATAL gates (A3 end_combat
with a living hostile, A5 xp-mode advanced but 0 XP, A8 rejected/extra_forbidden tool call)
and a couple of the WARN seams (A2 parry, A6 companion location desync). Each gate is exercised
in BOTH its tripping case and a clean/scope-guarded case so a future edit that breaks the
detection (or makes it false-RED) is caught.

Stdlib + pytest only; self-contained. The end-to-end cases invoke the script as a subprocess
(it is a CLI: argv = run.jsonl, state.json, [chat.jsonl], [moves.jsonl]; exit 1 = RED).

Run with the engine venv (which has pytest):
    uv run --directory servers/engine python -m pytest qa/test_assert_behavioral.py -p no:cacheprovider
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assert_behavioral as ab  # noqa: E402

SCRIPT = str(Path(__file__).resolve().parent / "assert_behavioral.py")


# ── helpers ───────────────────────────────────────────────────────────────────

def _assistant_tool_use(tool_use_id: str, name: str, inp: dict) -> dict:
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": tool_use_id,
                                     "name": name, "input": inp}]}}


def _user_tool_result(tool_use_id: str, text: str, is_error: bool = False) -> dict:
    return {"type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_use_id,
                                     "content": [{"type": "text", "text": text}],
                                     "is_error": is_error}]}}


_PC = {"name": "Dal", "kind": "player"}  # a minimal valid PC for the existing player_in_party gate


def _with_party(state: dict) -> dict:
    """Ensure the state has a player in party[] so the EXISTING player_in_party FATAL gate
    (#6) is satisfied — otherwise every 'clean' fixture would RED on that pre-existing gate,
    masking what we're actually testing. Only injects when the fixture didn't set its own."""
    state.setdefault("party", ["pc1"])
    chars = state.setdefault("characters", {})
    if not any(isinstance(c, dict) and c.get("kind") == "player" for c in chars.values()):
        chars["pc1"] = dict(_PC)
    return state


_DICE = ("roll", "attack", "saving_throw", "social_check", "skill_check")


def _has_dice(events) -> bool:
    for e in events:
        for b in (e.get("message", {}) or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" \
                    and (b.get("name") or "").split("__")[-1] in _DICE:
                return True
    return False


def _run_gate(tmp_path, events, state, env=None):
    """Write run.jsonl + state.json and invoke the gate as a subprocess. Returns
    (returncode, stdout). exit 1 = RED (a FATAL gate failed); 0 = GREEN.

    Injects a valid party (player_in_party gate) and a baseline `roll` (dm_produced_output +
    dice_used gates) when the fixture didn't supply them, so each test exercises ONLY its own
    new gate rather than tripping a pre-existing baseline FATAL."""
    state = _with_party(state)
    events = list(events)
    if not _has_dice(events):
        events = [_assistant_tool_use("__dice", "mcp__engine__roll", {}),
                  _user_tool_result("__dice", json.dumps({"total": 12}))] + events
    run = tmp_path / "run.jsonl"
    run.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    st = tmp_path / "state.json"
    st.write_text(json.dumps(state), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, SCRIPT, str(run), str(st)],
        capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


# ── _tool_events parser ─────────────────────────────────────────────────────────

def test_tool_events_pairs_use_with_result_and_parses_json():
    events = [
        _assistant_tool_use("t1", "mcp__engine__attack", {"attacker_id": "a", "target_id": "b"}),
        _user_tool_result("t1", json.dumps({"attacker": "Aldric", "target": "Goblin", "hit": True})),
    ]
    evs = ab._tool_events(events)
    assert len(evs) == 1
    short, inp, obj, is_err, text = evs[0]
    assert short == "attack"  # the mcp__engine__ prefix is stripped to the short name
    assert inp["attacker_id"] == "a"
    assert isinstance(obj, dict) and obj["attacker"] == "Aldric"
    assert is_err is False


def test_tool_events_flags_is_error_and_keeps_raw_text_for_non_json():
    events = [
        _assistant_tool_use("t9", "mcp__engine__update_character", {"level": 5}),
        _user_tool_result("t9", "Error executing tool update_character: 1 validation error "
                                "[type=extra_forbidden, ...]", is_error=True),
    ]
    evs = ab._tool_events(events)
    assert len(evs) == 1
    short, inp, obj, is_err, text = evs[0]
    assert short == "update_character"
    assert is_err is True
    assert obj is None  # not JSON → result_obj None
    assert "extra_forbidden" in text  # raw text preserved for matching


# ── A8: rejected / extra_forbidden tool call (FATAL) ────────────────────────────

def test_a8_extra_forbidden_tool_call_is_red(tmp_path):
    events = [
        _assistant_tool_use("u1", "mcp__engine__update_character",
                            {"level": 5, "class_name": "Wizard"}),
        _user_tool_result("u1", "Error executing tool update_character: validation error "
                                "[type=extra_forbidden, loc=('level',)]", is_error=True),
    ]
    rc, out = _run_gate(tmp_path, events, {"leveling_mode": "milestone"})
    assert rc == 1, out
    assert "no_rejected_tool_calls" in out


def test_a8_benign_engine_guard_is_not_red_only_warn(tmp_path):
    # A travel-graph rejection the DM is EXPECTED to hit and recover from → WARN, not RED.
    events = [
        _assistant_tool_use("g1", "mcp__engine__travel_to", {"to": "moonrise"}),
        _user_tool_result("g1", "Error: not connected to the current location", is_error=True),
    ]
    rc, out = _run_gate(tmp_path, events, {"leveling_mode": "milestone"})
    assert rc == 0, out
    assert "engine_guards_hit" in out  # surfaced as a WARN


# ── A3: end_combat with a living, un-fled hostile (FATAL) ──────────────────────

def test_a3_end_combat_with_living_hostile_is_red(tmp_path):
    events = [
        _assistant_tool_use("e1", "mcp__engine__end_combat", {}),
        _user_tool_result("e1", json.dumps({"ok": True})),
    ]
    state = {
        "leveling_mode": "milestone",
        "combat": {"active": False},
        "characters": {
            "m1": {"name": "Bandit Captain", "kind": "monster", "current_hp": 34,
                   "max_hp": 52, "dead": False},
        },
        "events": [],
    }
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 1, out
    assert "end_combat_no_living_hostiles" in out


def test_a3_clean_when_hostile_dead(tmp_path):
    events = [
        _assistant_tool_use("e1", "mcp__engine__end_combat", {}),
        _user_tool_result("e1", json.dumps({"ok": True})),
    ]
    state = {
        "leveling_mode": "milestone",
        "combat": {"active": False},
        "characters": {"m1": {"name": "Ghoul", "kind": "monster", "current_hp": 0, "dead": True}},
        "events": [],
    }
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out


def test_a3_clean_when_flee_event_logged(tmp_path):
    events = [
        _assistant_tool_use("e1", "mcp__engine__end_combat", {}),
        _user_tool_result("e1", json.dumps({"ok": True})),
    ]
    state = {
        "leveling_mode": "milestone",
        "combat": {"active": False},
        "characters": {"m1": {"name": "Bandit", "kind": "monster", "current_hp": 5, "dead": False}},
        "events": [{"text": "The surviving bandit turns and flees into the alley."}],
    }
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out


def test_a3_downgraded_to_warn_in_combat_sprint(tmp_path):
    import os
    events = [
        _assistant_tool_use("e1", "mcp__engine__end_combat", {}),
        _user_tool_result("e1", json.dumps({"ok": True})),
    ]
    state = {
        "leveling_mode": "milestone",
        "combat": {"active": False},
        "characters": {"m1": {"name": "Captain", "kind": "monster", "current_hp": 34, "dead": False}},
        "events": [],
    }
    env = dict(os.environ, CLAWDND_GATE_COMBAT_SPRINT="1")
    rc, out = _run_gate(tmp_path, events, state, env=env)
    assert rc == 0, out  # sprint lane → WARN, not RED


# ── A5: xp-mode advanced but PC at 0 XP (FATAL) ────────────────────────────────

def _advanced_xp_state(party_xp: int) -> dict:
    return {
        "leveling_mode": "xp",
        "day": 3,  # clock moved → advanced
        "current_location_id": "loc_b",
        "locations": {"loc_a": {"visited": True}, "loc_b": {"visited": True}},
        "party": ["pc1"],
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "dead": False, "xp": party_xp,
                    "location_id": "loc_b"},
        },
    }


def _enough_beats_chat(tmp_path) -> str:
    # session_beats >= MIN_BEATS(6) is required for A5/world-floor; supply a chat log.
    chat = tmp_path / "chat.jsonl"
    rows = []
    for _ in range(6):
        rows.append({"role": "player", "text": "[do] I press on."})
        rows.append({"role": "dm", "text": 'The road bends. "Keep close," she says.'})
    chat.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(chat)


def test_a5_advanced_zero_xp_is_red(tmp_path):
    run = tmp_path / "run.jsonl"
    run.write_text(json.dumps(_assistant_tool_use("r1", "mcp__engine__roll", {})) + "\n"
                   + json.dumps(_user_tool_result("r1", json.dumps({"total": 12}))), encoding="utf-8")
    st = tmp_path / "state.json"
    st.write_text(json.dumps(_advanced_xp_state(0)), encoding="utf-8")
    chat = _enough_beats_chat(tmp_path)
    proc = subprocess.run([sys.executable, SCRIPT, str(run), str(st), chat],
                          capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "xp_awarded_on_progression" in (proc.stdout + proc.stderr)


def test_a5_clean_when_party_has_xp(tmp_path):
    run = tmp_path / "run.jsonl"
    run.write_text(json.dumps(_assistant_tool_use("r1", "mcp__engine__roll", {})) + "\n"
                   + json.dumps(_user_tool_result("r1", json.dumps({"total": 12}))), encoding="utf-8")
    st = tmp_path / "state.json"
    st.write_text(json.dumps(_advanced_xp_state(300)), encoding="utf-8")
    chat = _enough_beats_chat(tmp_path)
    proc = subprocess.run([sys.executable, SCRIPT, str(run), str(st), chat],
                          capture_output=True, text=True)
    # may still WARN on other seams, but A5 must not flip RED
    assert "xp_awarded_on_progression" not in (proc.stdout + proc.stderr) or proc.returncode == 0


# ── A6: companion location desync incl. non-party companions (WARN) ────────────

def test_a6_flags_de_facto_companion_not_in_party(tmp_path):
    state = {
        "leveling_mode": "milestone",
        "current_location_id": "loc_here",
        "party": ["pc1"],
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "location_id": "loc_here"},
            # Karlach: a de-facto companion NOT in party[], stuck at a stale location
            "comp_k": {"name": "Karlach", "kind": "companion", "location_id": "loc_elsewhere"},
        },
    }
    rc, out = _run_gate(tmp_path, [], state)
    assert rc == 0, out  # WARN, not RED
    assert "companion_location_synced" in out
    assert "NOT in party[]" in out


# ── A2: parry-capable monster hit, no reaction (WARN) ──────────────────────────

def test_a2_parry_monster_hit_no_reaction_warns(tmp_path):
    events = [
        _assistant_tool_use("a1", "mcp__engine__attack", {"attacker_id": "pc1", "target_id": "m1"}),
        _user_tool_result("a1", json.dumps({"attacker": "Aldric", "target": "Bandit Captain",
                                            "hit": True, "parry": None})),
    ]
    state = {
        "leveling_mode": "milestone",
        "characters": {
            "m1": {"name": "Bandit Captain", "kind": "monster", "parry": 2, "current_hp": 30},
        },
    }
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out  # WARN, not RED
    assert "parry_reaction_considered" in out


# ── regression: the existing 17 gates still load / a clean run stays GREEN ──────

def test_clean_minimal_run_is_green(tmp_path):
    events = [
        _assistant_tool_use("r1", "mcp__engine__roll", {}),
        _user_tool_result("r1", json.dumps({"total": 14})),
    ]
    state = {"leveling_mode": "milestone",
             "party": ["pc1"],
             "characters": {"pc1": {"name": "Dal", "kind": "player"}}}
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    assert "GREEN" in out


# ── caster_has_spellbook (WARN) + quest_objectives_progress (WARN) — release-signal gates ──

def test_caster_has_spellbook_warns_on_empty_spellbook(tmp_path):
    # A caster (truthy spellcasting) with NO spells anywhere → flagged (ow-fix-011115 regression).
    state = {"characters": {"caster1": {"name": "Gale", "kind": "companion",
             "spellcasting": {"ability": "int"}, "spells_known": []}}}
    rc, out = _run_gate(tmp_path, [], state)
    assert "caster_has_spellbook" in out and "Gale" in out  # WARN, so rc stays 0
    assert rc == 0, out


def test_caster_has_spellbook_passes_with_spells(tmp_path):
    state = {"characters": {"caster1": {"name": "Gale", "kind": "companion",
             "spellcasting": {"ability": "int"}, "spells_known": ["Magic Missile"]}}}
    rc, out = _run_gate(tmp_path, [], state)
    assert "[PASS] caster_has_spellbook" in out


def test_caster_has_spellbook_ignores_non_caster(tmp_path):
    # A martial (no spellcasting) with empty spells must NOT be flagged.
    state = {"characters": {"f1": {"name": "Aldric", "kind": "player", "spells_known": []}}}
    rc, out = _run_gate(tmp_path, [], state)
    assert "[PASS] caster_has_spellbook" in out


def test_quest_objectives_progress_warns_on_stuck_quest(tmp_path):
    # A completed quest with objectives but empty completed_objectives → write-site bypassed.
    state = {"quests": {"q1": {"title": "Find the Relic", "status": "completed",
             "objectives": ["reach the crypt"], "completed_objectives": []}}}
    rc, out = _run_gate(tmp_path, [], state)
    assert "quest_objectives_progress" in out and "Find the Relic" in out
    assert rc == 0, out


def test_quest_objectives_progress_passes_when_recorded(tmp_path):
    state = {"quests": {"q1": {"title": "Find the Relic", "status": "completed",
             "objectives": ["reach the crypt"], "completed_objectives": ["reach the crypt"]}}}
    rc, out = _run_gate(tmp_path, [], state)
    assert "[PASS] quest_objectives_progress" in out
