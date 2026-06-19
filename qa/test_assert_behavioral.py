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


# ── F14-13 (#812): SOFT errors (dict {"error": ...}, is_error=False) are visible ──
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F14-13). Some tools return a
# dict-shaped {"error": ...} with is_error=False — invisible to the is_error-only A8
# gate. A report-only `tool_soft_errors` counter SURFACES them (WARN, never RED) so a
# masked soft-failure run can't read as silently clean.

def test_f14_13_soft_error_dict_is_counted_as_warn(tmp_path):
    events = [
        _assistant_tool_use("s1", "mcp__engine__load_canon_character", {"who": "nobody"}),
        _user_tool_result("s1", json.dumps(
            {"error": "no canon character 'nobody' for world 'baldurs-gate'",
             "did_you_mean": ["Minsc"]})),  # is_error=False (soft error)
    ]
    rc, out = _run_gate(tmp_path, events, {"leveling_mode": "milestone"})
    assert rc == 0, out  # report-only: never flips the gate RED
    assert "tool_soft_errors" in out  # the soft error is now VISIBLE to the gate


def test_f14_13_clean_run_reports_zero_soft_errors_silently(tmp_path):
    # A run with NO soft errors must not WARN (the counter is at zero → PASS, no noise).
    events = [
        _assistant_tool_use("ok1", "mcp__engine__look_around", {}),
        _user_tool_result("ok1", json.dumps({"location": {"id": "a", "name": "Hall"}})),
    ]
    rc, out = _run_gate(tmp_path, events, {"leveling_mode": "milestone"})
    assert rc == 0, out
    # the counter line is PASS (no soft errors), not a WARN
    assert "[WARN] tool_soft_errors" not in out


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


def test_a3_clean_when_resolution_declared(tmp_path):
    """A DM-declared disposition (end_combat(resolution=...)) passes A3 even with a foe still
    alive — a legitimate flee/surrender/capture is not a continuity break. The engine persists it
    to last_combat_resolution because the combat chronicle isn't in the snapshot the gate reads."""
    events = [
        _assistant_tool_use("e1", "mcp__engine__end_combat", {"resolution": "the bandits flee"}),
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
        "last_combat_resolution": "the surviving bandits break and flee the alley",
    }
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out


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
    env = dict(os.environ, WORLDOS_GATE_COMBAT_SPRINT="1")
    rc, out = _run_gate(tmp_path, events, state, env=env)
    assert rc == 0, out  # sprint lane → WARN, not RED


# ── A5: xp-mode advanced + reward-worthy but PC at 0 XP (FATAL) ────────────────

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


def test_a5_setup_travel_zero_xp_is_warn_not_red(tmp_path):
    run = tmp_path / "run.jsonl"
    run.write_text(json.dumps(_assistant_tool_use("r1", "mcp__engine__roll", {})) + "\n"
                   + json.dumps(_user_tool_result("r1", json.dumps({"total": 12}))), encoding="utf-8")
    st = tmp_path / "state.json"
    st.write_text(json.dumps(_advanced_xp_state(0)), encoding="utf-8")
    chat = _enough_beats_chat(tmp_path)
    proc = subprocess.run([sys.executable, SCRIPT, str(run), str(st), chat],
                          capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "xp_progression_scope" in out
    assert "xp_awarded_on_progression" not in out


def test_a5_completed_quest_zero_xp_is_red(tmp_path):
    run = tmp_path / "run.jsonl"
    run.write_text(json.dumps(_assistant_tool_use("r1", "mcp__engine__roll", {})) + "\n"
                   + json.dumps(_user_tool_result("r1", json.dumps({"total": 12}))), encoding="utf-8")
    st = tmp_path / "state.json"
    state = _advanced_xp_state(0)
    state["quests"] = {
        "q1": {
            "title": "Rescue the Courier",
            "status": "completed",
            "objectives": ["Find the courier"],
            "completed_objectives": ["Find the courier"],
        }
    }
    st.write_text(json.dumps(state), encoding="utf-8")
    chat = _enough_beats_chat(tmp_path)
    proc = subprocess.run([sys.executable, SCRIPT, str(run), str(st), chat],
                          capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "xp_awarded_on_progression" in (proc.stdout + proc.stderr)


def test_a5_already_awarded_completed_quest_zero_xp_is_warn_not_red(tmp_path):
    run = tmp_path / "run.jsonl"
    run.write_text(json.dumps(_assistant_tool_use("r1", "mcp__engine__roll", {})) + "\n"
                   + json.dumps(_user_tool_result("r1", json.dumps({"total": 12}))), encoding="utf-8")
    st = tmp_path / "state.json"
    state = _advanced_xp_state(0)
    state["quests"] = {
        "q1": {
            "title": "Rescue the Courier",
            "status": "completed",
            "objectives": ["Find the courier"],
            "completed_objectives": ["Find the courier"],
            "milestone_awarded": True,
        }
    }
    st.write_text(json.dumps(state), encoding="utf-8")
    chat = _enough_beats_chat(tmp_path)
    proc = subprocess.run([sys.executable, SCRIPT, str(run), str(st), chat],
                          capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "xp_progression_scope" in out
    assert "xp_awarded_on_progression" not in out


def test_a5_clean_when_party_has_xp(tmp_path):
    run = tmp_path / "run.jsonl"
    run.write_text(json.dumps(_assistant_tool_use("r1", "mcp__engine__roll", {})) + "\n"
                   + json.dumps(_user_tool_result("r1", json.dumps({"total": 12}))), encoding="utf-8")
    st = tmp_path / "state.json"
    state = _advanced_xp_state(300)
    state["quests"] = {
        "q1": {
            "title": "Rescue the Courier",
            "status": "completed",
            "objectives": ["Find the courier"],
            "completed_objectives": ["Find the courier"],
        }
    }
    st.write_text(json.dumps(state), encoding="utf-8")
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


# ── signature_feature_exercised (WARN, csmed-1/2/4) — coverage signal ──────────────
# The combat-sprints left War-Cleric Channel Divinity + Fighter Action Surge / Second Wind at
# used:0 EVERY time (medium/low omission — untested resource plumbing). These checks WARN when
# a seeded party member HAS the feature on its sheet but the run never exercised it, and must
# NOT false-fire when the feature is absent. Source: mech-climb evidence agent (combat-sprints).

def _char_with_pool(name, kind, cls, pool_id, max_, used):
    return {"name": name, "kind": kind,
            "classes": [{"name": cls, "level": 4}],
            "class_resources": {pool_id: {"max": max_, "used": used, "recharge": "short"}}}


def test_signature_feature_warns_when_seeded_but_unused(tmp_path):
    # Aldric (fighter) has second_wind + action_surge; Maren (cleric) has channel_divinity —
    # all seeded, all used:0 (the exact csmed-1/2/4 omission). WARN each, run stays GREEN.
    state = {"leveling_mode": "milestone", "party": ["pc1", "c1"], "characters": {
        "pc1": {"name": "Aldric", "kind": "player", "classes": [{"name": "fighter", "level": 4}],
                "class_resources": {"second_wind": {"max": 3, "used": 0, "recharge": "short"},
                                    "action_surge": {"max": 1, "used": 0, "recharge": "short"}}},
        "c1": _char_with_pool("Maren", "companion", "cleric", "channel_divinity", 2, 0)}}
    rc, out = _run_gate(tmp_path, [], state)
    assert rc == 0, out  # WARN, not RED
    assert "[WARN] signature_feature_exercised" in out
    for token in ("second_wind", "action_surge", "channel_divinity", "Aldric", "Maren"):
        assert token in out, token


def test_signature_feature_passes_when_used_in_final_state(tmp_path):
    # used>0 on the final sheet ⇒ exercised ⇒ PASS (no need to scan the tool stream).
    state = {"leveling_mode": "milestone", "party": ["pc1", "c1"], "characters": {
        "pc1": _char_with_pool("Aldric", "player", "fighter", "second_wind", 3, 1),
        "c1": _char_with_pool("Maren", "companion", "cleric", "channel_divinity", 2, 1)}}
    # action_surge still 0 on Aldric → keep only the exercised pools so this asserts the clean PASS.
    state["characters"]["pc1"]["class_resources"]["action_surge"] = {"max": 1, "used": 1, "recharge": "short"}
    rc, out = _run_gate(tmp_path, [], state)
    assert rc == 0, out
    assert "[PASS] signature_feature_exercised" in out


def test_signature_feature_passes_when_use_resource_called(tmp_path):
    # Even if the final-snapshot `used` were reset (e.g. a short rest after the spend), a
    # use_resource(resource=…) call in the stream proves the feature was exercised → PASS.
    events = [
        _assistant_tool_use("u1", "mcp__engine__use_resource",
                            {"character_id": "pc1", "resource": "action_surge"}),
        _user_tool_result("u1", json.dumps({"ok": True, "resource": "action_surge",
                                            "spent": 1, "remaining": 0, "used": 1})),
    ]
    state = {"leveling_mode": "milestone", "party": ["pc1"], "characters": {
        "pc1": _char_with_pool("Aldric", "player", "fighter", "action_surge", 1, 0)}}
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    assert "[PASS] signature_feature_exercised" in out


def test_signature_feature_silent_when_party_lacks_feature(tmp_path):
    # A party with NO channel_divinity/action_surge/second_wind pool must NOT be flagged
    # (additive: a wizard-only party never trips this). The check is absent ⇒ neither WARN nor PASS line.
    state = {"leveling_mode": "milestone", "party": ["pc1"], "characters": {
        "pc1": {"name": "Gale", "kind": "player", "classes": [{"name": "wizard", "level": 4}],
                "class_resources": {}}}}
    rc, out = _run_gate(tmp_path, [], state)
    assert rc == 0, out
    assert "signature_feature_exercised" not in out  # no party member has the feature → check skipped


# ── structural_completeness (FATAL, relationship-cues) — the owner's "full circle" ──
# The scorers reward prose+dice but were blind to a system-skipping run (an 18-beat run that
# narrated the companion+quest story but never engaged the engine: companion frozen at 0, a
# multi-location quest left `active`, no camp). This FATAL gate makes such a run score RED.
# CONTEXTUAL: only a SUBSTANTIAL session (>= 10 beats) with a companion present trips it; a
# short combat-sprint or a companion-less session must NOT.

def _dm_text_turns(n: int):
    """n DM assistant TEXT turns — session_beats(no chat/facade) == dm_text, so this drives
    the structural floor's >= 10-beat gate."""
    return [{"type": "assistant",
             "message": {"content": [{"type": "text", "text": f"The scene unfolds, beat {i}."}]}}
            for i in range(n)]


def _toolcall(name: str):
    return [_assistant_tool_use(f"t_{name}", f"mcp__engine__{name}", {}),
            _user_tool_result(f"t_{name}", json.dumps({"ok": True}))]


def _frozen_run_state(*, approval_moved=False, active_quest=True, visited2=True):
    """A substantial-session final state: a player + a companion, an active (unresolved)
    quest, two visited locations (a real arc). approval_moved toggles whether the companion's
    regard left 0."""
    locs = {"loc_a": {"visited": True}, "loc_b": {"visited": True if visited2 else False}}
    state = {
        "leveling_mode": "milestone",
        "day": 5,
        "locations": locs,
        "party": ["pc1", "comp1"],
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "location_id": "loc_b"},
            "comp1": {"name": "Brother Toll", "kind": "companion",
                      "attitude_value": 20 if approval_moved else 0,
                      "location_id": "loc_b"},
        },
    }
    if active_quest:
        state["quests"] = {"q1": {"title": "The Embergloom Pact", "status": "active",
                                  "objectives": ["free the prisoners"], "completed_objectives": []}}
    return state


def test_structural_completeness_trips_on_frozen_companion_and_unresolved_quest(tmp_path):
    # 12 DM beats, a companion stuck at attitude 0, no camp/long_rest tool, an active quest
    # across a 2-location arc with no resolution → RED.
    events = _dm_text_turns(12)
    state = _frozen_run_state(approval_moved=False, active_quest=True)
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 1, out
    assert "structural_completeness" in out
    assert "[FAIL] structural_completeness" in out


def test_structural_completeness_passes_when_approval_moved_and_quest_resolved(tmp_path):
    # The companion's regard moved (attitude 20) AND a camp happened AND the quest resolved.
    events = (_dm_text_turns(12) + _toolcall("long_rest") + _toolcall("complete_quest"))
    state = _frozen_run_state(approval_moved=True, active_quest=False)
    # quest resolved (no active quest)
    state["quests"] = {"q1": {"title": "The Embergloom Pact", "status": "completed",
                              "objectives": ["free the prisoners"],
                              "completed_objectives": ["free the prisoners"],
                              "evolves_to": "the cult regroups"}}
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    assert "[PASS] structural_completeness" in out


def test_structural_completeness_passes_when_camp_engaged_even_if_attitude_zero(tmp_path):
    # The (a) frozen+no-camp clause requires BOTH approval-frozen AND no camp. A run that DID
    # camp (engaged the relationship system) must not trip clause (a). Keep the quest resolved
    # so clause (b) is also satisfied.
    events = _dm_text_turns(12) + _toolcall("camp_scene") + _toolcall("complete_quest")
    state = _frozen_run_state(approval_moved=False, active_quest=False)
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    assert "[PASS] structural_completeness" in out


def test_structural_completeness_passes_when_quest_resolution_engaged(tmp_path):
    # Clause (b) is gated on never having engaged a quest-resolution tool. A run that called
    # complete_quest (and simply has another quest still open) is NOT a dropped arc. Approval
    # moved + camp happened so clause (a) is clean too.
    events = (_dm_text_turns(12) + _toolcall("long_rest") + _toolcall("complete_quest"))
    state = _frozen_run_state(approval_moved=True, active_quest=True)
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    assert "[PASS] structural_completeness" in out


def test_structural_completeness_silent_on_companionless_session(tmp_path):
    # No companion in the final state → the gate is contextual and skips entirely (no line).
    events = _dm_text_turns(12)
    state = {"leveling_mode": "milestone", "day": 5,
             "locations": {"loc_a": {"visited": True}, "loc_b": {"visited": True}},
             "party": ["pc1"],
             "characters": {"pc1": {"name": "Dal", "kind": "player", "location_id": "loc_b"}},
             "quests": {"q1": {"title": "Solo errand", "status": "active",
                               "objectives": ["x"], "completed_objectives": []}}}
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    assert "structural_completeness" not in out  # contextual: no companion → check skipped


def test_structural_completeness_silent_on_short_session(tmp_path):
    # A 5-beat run (< 10) with a frozen companion must NOT trip — too short to be substantial.
    events = _dm_text_turns(5)
    state = _frozen_run_state(approval_moved=False, active_quest=True)
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    assert "structural_completeness" not in out  # contextual: < 10 beats → check skipped


def test_structural_completeness_silent_in_combat_sprint(tmp_path):
    import os
    # The combat-sprint lane sets WORLDOS_GATE_COMBAT_SPRINT — a 1-location pre-seeded fight
    # legitimately never moves approval/quests, so the structural floor must skip.
    events = _dm_text_turns(12)
    state = _frozen_run_state(approval_moved=False, active_quest=True)
    env = dict(os.environ, WORLDOS_GATE_COMBAT_SPRINT="1")
    rc, out = _run_gate(tmp_path, events, state, env=env)
    assert rc == 0, out
    assert "structural_completeness" not in out


# ── flat_arc (WARN-first) — a >=24-beat 3-act run whose arc never turned ───────────
# A run that CLAIMS 3 acts (engine cursor or contiguous tags) but has felt_three_act False (no
# real reversal+climax) is a flat fetch-quest shape, not a felt setup→reversal→climax. Shipped
# WARN-first (fatal=False) behind FELT_SHAPE_MIN_BEATS=24, so it NEVER REDs yet and is silent
# below 24 beats / on a non-3-act run. Graduates to fatal after a clean CI sweep.

def _act3_completable_state(*, felt: bool) -> dict:
    """A >=24-beat-eligible 3-act final state that satisfies the two FATAL structural clauses
    (approval moved + quest resolved across the arc) so ONLY flat_arc can speak. `felt` toggles
    whether the engine stamped a real midpoint reversal + climax (felt arc) or left them unlanded
    (flat arc)."""
    arc = {"act": 3, "day_act_entered": 15, "beats_in_act": 5}
    if felt:
        arc.update(midpoint_reversal_landed=True, reversal_day=10,
                   climax_landed=True, climax_day=18)
    else:
        arc.update(midpoint_reversal_landed=False, reversal_day=0,
                   climax_landed=False, climax_day=0)
    return {
        "leveling_mode": "milestone",
        "day": 20,
        "narrative_arc": arc,
        "party": ["pc1", "comp1"],
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "location_id": "loc_c"},
            # approval moved (attitude 20) → approval_frozen_run clause is clean
            "comp1": {"name": "Brother Toll", "kind": "companion",
                      "attitude_value": 20, "location_id": "loc_c",
                      "last_long_rest_day": 5},
        },
        # quest resolved late → unresolved_arc clause is clean AND (when felt) the late climax lands
        "quests": {"q1": {"title": "The Embergloom Pact", "status": "completed",
                          "last_progress_day": 18,
                          "objectives": ["x"], "completed_objectives": ["x"],
                          "evolves_to": "the cult regroups"}},
        "locations": {
            "loc_a": {"name": "The Grove (Act 1)", "visited": True},
            "loc_b": {"name": "Moonrise (Act 2)", "visited": True},
            "loc_c": {"name": "The Lower City (Act 3)", "visited": True},
        },
        "decisions": [],
        "consequences": [{"due": 21, "text": "the cult regroups"}],
        "flags": {},
    }


def test_flat_arc_warns_not_red_on_24beat_flat_three_act(tmp_path):
    # 24 DM beats, engine cursor at act 3, BOTH landed flags False, no mid-band reversal →
    # felt_three_act False → flat_arc must WARN (not RED). The two fatal clauses are satisfied
    # (approval moved + quest resolved) so the run stays GREEN overall.
    events = _dm_text_turns(24) + _toolcall("long_rest") + _toolcall("complete_quest")
    state = _act3_completable_state(felt=False)
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out  # WARN-first: never RED
    assert "[WARN] flat_arc" in out, out
    assert "[FAIL] flat_arc" not in out


def test_flat_arc_passes_clean_on_felt_three_act(tmp_path):
    # A real 3-act run: the engine stamped a banded midpoint reversal + climax → felt_three_act
    # True → flat_arc passes clean (PASS, no WARN).
    events = _dm_text_turns(24) + _toolcall("long_rest") + _toolcall("complete_quest")
    state = _act3_completable_state(felt=True)
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    assert "[PASS] flat_arc" in out, out
    assert "[WARN] flat_arc" not in out


def test_flat_arc_silent_below_24_beats(tmp_path):
    # The SAME flat 3-act state at only 12 beats (>= STRUCTURAL_MIN_BEATS 10 but < 24) must NOT
    # emit flat_arc at all — the higher floor keeps every currently-passing <24-beat run unaffected.
    events = _dm_text_turns(12) + _toolcall("long_rest") + _toolcall("complete_quest")
    state = _act3_completable_state(felt=False)
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    assert "flat_arc" not in out  # below the 24-beat floor → check skipped entirely


def test_flat_arc_silent_on_non_three_act_run(tmp_path):
    # A >=24-beat run that only reached act 2 (never CLAIMED 3 acts) must NOT be penalized for
    # lacking a climax — flat_arc only fires when the run claims >=3 acts.
    events = _dm_text_turns(24) + _toolcall("long_rest") + _toolcall("complete_quest")
    state = _act3_completable_state(felt=False)
    state["narrative_arc"]["act"] = 2
    # drop the act-3 tag so the tag path also reads <3 (contiguous {1,2} → 2)
    state["locations"]["loc_c"]["name"] = "The Lower City"  # untagged
    rc, out = _run_gate(tmp_path, events, state)
    assert rc == 0, out
    # The clause runs (>=24 beats) but a <3-act run is never PENALIZED: it passes, never WARNs.
    assert "[WARN] flat_arc" not in out, out
    assert "[FAIL] flat_arc" not in out


# ── FIX 4: party_traveled in-place-progression exception (#623 false-cap) ─────────
# A multi-beat arc that RESOLVED in a single location (clock advanced + quest completed,
# beats >= 8) is a SUCCESS — it must PASS party_traveled. A frozen opening (day 1/morning,
# no resolved quest) must STILL fail (RED). The AND keeps the frozen stall red.

def _single_scene_state(*, day, tod=None, quest_completed, visited_count=1):
    """A single-location final state (visited_count locations visited). `day`/`tod` drive
    clock_advanced; `quest_completed` drives arc_resolved. No companion → the structural
    floor (>=10 beats + companion) stays silent so we isolate party_traveled."""
    locs = {"loc_a": {"visited": True}}
    if visited_count >= 2:
        locs["loc_b"] = {"visited": True}
    state = {
        "leveling_mode": "milestone",
        "day": day,
        "party": ["pc1"],
        "locations": locs,
        "characters": {"pc1": {"name": "Dal", "kind": "player", "location_id": "loc_a"}},
    }
    if tod is not None:
        state["time_of_day"] = tod
    if quest_completed:
        state["quests"] = {"q1": {"title": "Tavern Negotiation", "status": "completed",
                                  "objectives": ["strike the bargain"],
                                  "completed_objectives": ["strike the bargain"]}}
    else:
        state["quests"] = {"q1": {"title": "Tavern Negotiation", "status": "active",
                                  "objectives": ["strike the bargain"],
                                  "completed_objectives": []}}
    return state


def test_party_traveled_passes_single_scene_arc_that_progressed_in_place(tmp_path):
    # visited=1, but day advanced (day 2) AND the quest completed AND beats>=8 → the in-place
    # progression exception fires → party_traveled PASSES (a resolved one-location drama).
    events = _dm_text_turns(9)  # session_beats=9 (>= SINGLE_SCENE_MIN_BEATS 8)
    state = _single_scene_state(day=2, quest_completed=True, visited_count=1)
    rc, out = _run_gate(tmp_path, events, state)
    assert "[PASS] party_traveled" in out, out
    assert "[FAIL] party_traveled" not in out, out
    assert rc == 0, out  # clock advanced too, so world_advanced_time also passes


def test_party_traveled_still_red_on_frozen_run(tmp_path):
    # visited=1, day==1/morning (clock never moved), no completed quest, beats>=8 → the
    # exception's AND fails (clock_advanced False, arc_resolved False) → party_traveled RED.
    events = _dm_text_turns(9)
    state = _single_scene_state(day=1, tod="morning", quest_completed=False, visited_count=1)
    rc, out = _run_gate(tmp_path, events, state)
    assert "[FAIL] party_traveled" in out, out
    assert rc == 1, out


# ── WS-E: dm_advanced_time — unmask a frozen DM whose clock the harness soft-tick moved ────────

def test_dm_advanced_time_warns_when_clock_moved_but_no_dm_time_tool(tmp_path):
    """The state day advanced (day=3) — but the DM issued NO time-advance tool, so only the harness
    worldos_soft_tick moved the clock. world_advanced_time PASSES (it sees day>1), yet the DM never
    rested/advanced time itself, starving companion regard / camp / day-gated systems. WS-E surfaces
    this as a WARN (the run stays GREEN — advisory, not fatal)."""
    events = _dm_text_turns(9)                       # session_beats=9 (>= MIN_BEATS), no time tool
    state = _single_scene_state(day=3, quest_completed=True, visited_count=2)
    rc, out = _run_gate(tmp_path, events, state)
    assert "[WARN] dm_advanced_time" in out, out
    assert "[FAIL] dm_advanced_time" not in out, out  # WARN-only, never fatal
    assert rc == 0, out                               # the run is still GREEN


def test_dm_advanced_time_passes_when_dm_issues_long_rest(tmp_path):
    """A DM that actually issues a time-advance tool (long_rest) is not flagged."""
    events = _dm_text_turns(9) + [
        _assistant_tool_use("lr1", "mcp__worldos-engine__long_rest", {"campaign_id": "c"}),
        _user_tool_result("lr1", json.dumps({"ok": True})),
    ]
    state = _single_scene_state(day=3, quest_completed=True, visited_count=2)
    rc, out = _run_gate(tmp_path, events, state)
    assert "[PASS] dm_advanced_time" in out, out
    assert "[WARN] dm_advanced_time" not in out, out
    assert rc == 0, out


def test_party_traveled_still_red_when_clock_advanced_but_arc_unresolved(tmp_path):
    # Guard against broadening to clock-only: day advanced but NO completed quest → the AND
    # still fails (arc_resolved False) → party_traveled stays RED.
    events = _dm_text_turns(9)
    state = _single_scene_state(day=3, quest_completed=False, visited_count=1)
    rc, out = _run_gate(tmp_path, events, state)
    assert "[FAIL] party_traveled" in out, out
    assert rc == 1, out


def test_party_traveled_red_despite_status_blind_quest_tool_count(tmp_path):
    # GATE-WEAKENING REGRESSION (adversarial-verified): a FROZEN single scene where the DM
    # advanced the clock AND called set_quest_status — the OLD status-blind quest_resolved
    # tool-count would have flipped arc_resolved True and let this DEAD scene PASS via the
    # in-place exception. arc_resolved now requires a snapshot quest at status=="completed";
    # the quest stays "active" (quest_completed=False) → arc_resolved False → party_traveled
    # stays RED even though set_quest_status was called.
    events = _dm_text_turns(9) + _toolcall("set_quest_status")
    state = _single_scene_state(day=3, quest_completed=False, visited_count=1)
    rc, out = _run_gate(tmp_path, events, state)
    assert "[FAIL] party_traveled" in out, out
    assert rc == 1, out


def test_party_traveled_warns_not_red_on_short_single_scene_vignette(tmp_path):
    # SEVERITY IS BEAT-SCOPED (2026-06-19 false-cap fix): a SHORT single-scene run
    # (< SINGLE_SCENE_MIN_BEATS 8) that stayed in one location is a legitimate vignette — the
    # standard 6-8 beat emergent social/combat duo — NOT a frozen stall. Below 8 beats
    # party_traveled is a WARN, never a lens-capping RED (it was over-capping legitimate short
    # play on BOTH Claude and GLM). Here: visited=1, day advanced + quest completed but only 7
    # beats (< 8) → the in-place exception's beat-floor isn't met (so it doesn't PASS via the
    # exception) AND the FATAL beat-floor isn't met (so it's a WARN, not RED). The run stays GREEN.
    events = _dm_text_turns(7)
    state = _single_scene_state(day=2, quest_completed=True, visited_count=1)
    rc, out = _run_gate(tmp_path, events, state)
    assert "[WARN] party_traveled" in out, out
    assert "[FAIL] party_traveled" not in out, out
    assert rc == 0, out  # a short single-scene vignette is not a fatal frozen stall


def test_party_traveled_still_red_on_substantial_frozen_run_too_few_visited(tmp_path):
    # The PRESERVED FATAL path: at/above SINGLE_SCENE_MIN_BEATS(8) a run that stayed in ONE
    # location AND did not progress in place (no completed quest → arc_resolved False) is a real
    # stuck-DM frozen stall → RED. (Guards against the beat-scoping weakening the substantial-run
    # FATAL: 8 beats, visited=1, clock advanced but arc unresolved → the in-place AND fails → RED.)
    events = _dm_text_turns(8)
    state = _single_scene_state(day=2, quest_completed=False, visited_count=1)
    rc, out = _run_gate(tmp_path, events, state)
    assert "[FAIL] party_traveled" in out, out
    assert rc == 1, out


# ── narration_no_ooc_leak (2026-06-17 craft audit) — OOC scaffolding in player-facing prose ──────
# The 5 VERBATIM leak lines the audit found in the 4.8-scored gs-ember-deep run.
_LEAK_SEAT = "Now let me seat Kield Vant as the player character — an ex-Flaming Fist soldier fits a Fighter build in this world."
_LEAK_CONTINUITY = "Continuity check — authored Garrick Donn is the *miller* (the dazed man up at the mill), so the cart in town is run by his hand. Let me correct that and bring a new face on-screen."
_LEAK_ORDER = "The blade comes out and the room tips into violence. Let me set the order of it."
_LEAK_REPLAY = "The grey miller never gets his answer, and neither do you — not yet. Here's how round one actually went: the dead mill-hand came at you first."
_LEAK_ADVANCE = "The party hardens on the long way down — let me set their advancement through the engine before the climax."
_ALL_LEAKS = [_LEAK_SEAT, _LEAK_CONTINUITY, _LEAK_ORDER, _LEAK_REPLAY, _LEAK_ADVANCE]

# Clean in-fiction prose engineered to brush AGAINST the patterns without being a leak — the
# false-positive guard that keeps the gate from ever RED-ing a clean beat.
_CLEAN_NEARMISS = [
    '"Let me introduce you to the captain," she says; "he\'ll want to hear this."',  # dialogue "let me introduce"
    "The siege engine groaned as the crew hauled it through the gate.",              # engine, but not "through the engine"
    "A natural spring fed the pool where the deer drank at dusk.",                   # "natural", not "natural 1"
    '"Let me set the table," the innkeep mutters, clearing the mugs.',               # "let me set", not "the order of it"
    "The bridge cracked at its midpoint and pitched you toward the black water.",    # "midpoint" in fiction
    "He recounted how the night went, every ugly detail of it.",                    # "how the night went", not "round"
    "You read the room: three farmers, a nervous barkeep, the door at your back.",   # clean tension prose
    # the 2026-06-17 adversarial sweep's machine-checked killers — now clean after hardening:
    "Steam screamed through the engine block as the Steel Watcher lurched upright.",  # literal machinery, not the game-engine sense
    "She would serve as the PC — the Principal Courier — until the writ cleared.",    # in-world "PC" initialism, not "player character"
    '"Your nat... your natural gift for trouble," he sighed, trailing off.',          # fictional stammer on a "nat-" word
    "The torturer's spine hook glinted on the rack, wet and patient.",               # literal flensing implement, not craft jargon
]


def _dm_leak(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def test_narration_leak_patterns_match_all_five_verbatim_leaks():
    for line in _ALL_LEAKS:
        assert any(rx.search(line) for rx in ab._NARRATION_LEAK_RE), f"missed leak: {line!r}"


def test_narration_leak_patterns_do_not_match_clean_fiction():
    for line in _CLEAN_NEARMISS:
        hit = [rx.pattern for rx in ab._NARRATION_LEAK_RE if rx.search(line)]
        assert not hit, f"false positive on clean line {line!r}: {hit}"


def test_dm_narration_texts_extracts_assistant_prose():
    events = [_dm_leak("First beat of fiction."), _assistant_tool_use("t1", "roll", {}),
              _dm_leak("Second beat of fiction.")]
    assert ab._dm_narration_texts(events) == ["First beat of fiction.", "Second beat of fiction."]


def test_narration_leak_three_plus_in_substantial_run_is_red(tmp_path):
    # 3 distinct OOC leaks across a substantial run (>= MIN_BEATS dm_text) -> the leak check is FATAL.
    events = [_dm_leak(_LEAK_SEAT), _dm_leak(_LEAK_ORDER), _dm_leak(_LEAK_ADVANCE)] + _dm_text_turns(6)
    rc, out = _run_gate(tmp_path, events, _with_party({"leveling_mode": "milestone"}))
    assert "[FAIL] narration_no_ooc_leak" in out, out


def test_narration_leak_one_or_two_is_warn_not_red(tmp_path):
    # A single incidental leak in a substantial run is a WARN (surfaced), never a RED.
    events = [_dm_leak(_LEAK_CONTINUITY)] + _dm_text_turns(6)
    rc, out = _run_gate(tmp_path, events, _with_party({"leveling_mode": "milestone"}))
    assert "[WARN] narration_no_ooc_leak" in out, out
    assert "[FAIL] narration_no_ooc_leak" not in out, out


def test_narration_leak_three_in_short_run_is_only_warn(tmp_path):
    # 3 leaks but only 3 dm_text turns (< MIN_BEATS) -> WARN, not RED: a tiny smoke isn't RED-capped.
    events = [_dm_leak(_LEAK_SEAT), _dm_leak(_LEAK_ORDER), _dm_leak(_LEAK_REPLAY)]
    rc, out = _run_gate(tmp_path, events, _with_party({"leveling_mode": "milestone"}))
    assert "[WARN] narration_no_ooc_leak" in out, out
    assert "[FAIL] narration_no_ooc_leak" not in out, out


def test_narration_leak_clean_run_passes(tmp_path):
    # A clean session (even with near-miss fiction) PASSES the leak gate.
    events = _dm_text_turns(4) + [_dm_leak(t) for t in _CLEAN_NEARMISS]
    rc, out = _run_gate(tmp_path, events, _with_party({"leveling_mode": "milestone"}))
    assert "[PASS] narration_no_ooc_leak" in out, out
