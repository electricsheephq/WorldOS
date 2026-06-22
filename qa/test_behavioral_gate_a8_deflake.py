"""De-flake guard for the A8 `no_rejected_tool_calls` behavioral gate (#897).

The A8 gate used to flip RED on ANY single tool rejection carrying an
`extra_forbidden` / `validation error` payload. Because behavioral is computed from
ONE stochastic duo, a single RECOVERED transient flub (the DM emits one malformed
call, immediately retries the same tool correctly, the session completes cleanly)
RED-capped EVERY lens to 2.5 and swung the headline RRI by ~1.0 — a high-variance
false-RED (#897, observed twice).

This change makes A8 reflect a PATTERN, not a single recovered transient, WITHOUT
weakening the version-skew signal it was built for. A rejection counts toward FATAL
only when it is:
  * UNRECOVERED — the same tool was never successfully called (is_error=False) after
    the rejection (the DM's intent silently did not take effect), OR
  * REPEATED — the same tool was rejected >=2x across the run (a systematic skew: the
    DM keeps using a stale/wrong signature — the real thing this gate must catch).
A SINGLE rejection of a tool that the DM then successfully retried -> WARN, never RED.

This mirrors the #1030 discriminator-aware severity pattern (WARN below threshold,
FATAL only for the genuine defect). It is a PRECISION improvement, not a leniency hack:
the corpus fixture (an unrecovered update_character rejection) STILL REDs, and a new
repeated-rejection fixture STILL REDs.

Run with the engine venv (single-process — NEVER -n/xdist):
    uv run --directory servers/engine python -m pytest \
        ../../qa/test_behavioral_gate_a8_deflake.py -q -p no:xdist
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_QA_DIR = Path(__file__).resolve().parent
_GATE = _QA_DIR / "assert_behavioral.py"


# ── minimal artifact builders (same event shapes the gate reads) ────────────────

def _assistant_tool_use(tid: str, name: str, inp: dict | None = None) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": tid,
                                 "name": name, "input": inp or {}}]},
    }


def _user_tool_result(tid: str, text: str, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                 "content": [{"type": "text", "text": text}],
                                 "is_error": is_error}]},
    }


def _roll(tid: str = "t_roll", total: int = 14) -> list[dict]:
    """A clean d20 roll pair — satisfies dm_produced_output + dice_used without being a
    combat resolver."""
    return [_assistant_tool_use(tid, "mcp__engine__roll", {"sides": 20}),
            _user_tool_result(tid, json.dumps({"total": total}))]


def _validation_err(tool: str = "update_character", field: str = "skills") -> str:
    """An extra_forbidden schema/validation rejection payload — the exact A8 trip shape
    (modeled on ow-swB-123842's update_character rejection)."""
    return (f"Error executing tool {tool}: 1 validation error for Character\n{field}\n"
            f"  Extra inputs are not permitted "
            f"[type=extra_forbidden, input_value=['Arcana'], input_type=list]")


def _clean_player_state() -> dict:
    """One living player in party[], xp>0, no combat/monsters — every OTHER fatal gate
    passes, so A8 is the only check under test."""
    return {
        "party": ["pc1"],
        "leveling_mode": "xp",
        "day": 2,
        "time_of_day": "evening",
        "current_location_id": "loc_camp",
        "characters": {
            "pc1": {"name": "Dal", "kind": "player", "xp": 300, "location_id": "loc_camp"},
        },
        "locations": {
            "loc_start": {"name": "Tavern", "visited": True},
            "loc_camp": {"name": "Camp", "visited": True},
        },
    }


def _run_gate(tmp_path: Path, run_events: list[dict], state: dict) -> tuple[int, str, dict]:
    """Write a minimal run.jsonl + state.json and invoke the gate. Returns
    (rc, combined_output, {check_name: mark})."""
    run = tmp_path / "run.jsonl"
    st = tmp_path / "state.json"
    run.write_text("\n".join(json.dumps(e) for e in run_events) + "\n", encoding="utf-8")
    st.write_text(json.dumps(state, indent=2), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(_GATE), str(run), str(st)],
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    marks: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        for tag in ("[PASS]", "[WARN]", "[FAIL]"):
            if line.startswith(tag):
                rest = line[len(tag):].strip()
                name = rest.split(" — ", 1)[0].split()[0] if rest else ""
                if name:
                    marks[name] = tag.strip("[]")
                break
    return proc.returncode, out, marks


# ── the de-flake behavior (#897) ────────────────────────────────────────────────

def test_single_recovered_transient_rejection_is_warn_not_red(tmp_path):
    """A SINGLE malformed call the DM immediately retries correctly (same tool, later
    is_error=False) is an invisible-to-the-player recovered transient -> the run stays
    GREEN and A8 is a [WARN], never a lens-capping RED. THIS is the #897 false-RED."""
    events = _roll() + [
        # one malformed update_character (extra_forbidden) ...
        _assistant_tool_use("t_uc_bad", "mcp__engine__update_character", {"skills": ["Arcana"]}),
        _user_tool_result("t_uc_bad", _validation_err(), is_error=True),
        # ... then the DM retries the SAME tool correctly (recovered).
        _assistant_tool_use("t_uc_ok", "mcp__engine__update_character",
                            {"skill_proficiencies": ["Arcana"]}),
        _user_tool_result("t_uc_ok", json.dumps({"ok": True})),
    ]
    rc, out, marks = _run_gate(tmp_path, events, _clean_player_state())
    assert rc == 0, (
        "a single RECOVERED transient rejection must NOT RED-cap the run (#897 false-RED).\n"
        f"--- gate output ---\n{out}"
    )
    assert marks.get("no_rejected_tool_calls") == "WARN", (
        "the recovered transient must still be SURFACED as a WARN (visibility preserved), "
        f"not silently dropped. marks={marks}\n--- gate output ---\n{out}"
    )


def test_unrecovered_single_rejection_stays_red(tmp_path):
    """A single rejection that is NEVER successfully retried (the DM's intent silently did
    not take effect) is the genuine version-skew defect — it STILL flips RED. This is the
    corpus fixture's shape; it must not be weakened."""
    events = _roll() + [
        _assistant_tool_use("t_uc", "mcp__engine__update_character", {"skills": ["Arcana"]}),
        _user_tool_result("t_uc", _validation_err(), is_error=True),
        # NO successful update_character afterward -> unrecovered.
    ]
    rc, out, marks = _run_gate(tmp_path, events, _clean_player_state())
    assert rc == 1, (
        "an UNRECOVERED rejection (intent never took effect) must STILL RED — the gate's "
        f"core version-skew signal must not be weakened.\n--- gate output ---\n{out}"
    )
    assert marks.get("no_rejected_tool_calls") == "FAIL", (
        f"unrecovered rejection must be a FATAL [FAIL]. marks={marks}\n--- gate output ---\n{out}"
    )


def test_repeated_same_tool_rejection_stays_red_even_if_eventually_recovered(tmp_path):
    """The SAME tool rejected >=2x is a systematic skew (the DM keeps using a stale/wrong
    signature) — a REAL defect this gate exists to catch. It STILL flips RED even if a
    later call eventually succeeds: repetition is the skew signal, not non-recovery."""
    events = _roll() + [
        _assistant_tool_use("t_uc1", "mcp__engine__update_character", {"skills": ["Arcana"]}),
        _user_tool_result("t_uc1", _validation_err(), is_error=True),
        _assistant_tool_use("t_uc2", "mcp__engine__update_character", {"skills": ["History"]}),
        _user_tool_result("t_uc2", _validation_err(), is_error=True),
        # even an eventual success does not redeem a REPEATED (systematic) rejection.
        _assistant_tool_use("t_uc3", "mcp__engine__update_character",
                            {"skill_proficiencies": ["Arcana"]}),
        _user_tool_result("t_uc3", json.dumps({"ok": True})),
    ]
    rc, out, marks = _run_gate(tmp_path, events, _clean_player_state())
    assert rc == 1, (
        "a tool rejected >=2x (systematic skew) must STILL RED even if a later call "
        f"succeeds — repetition is the real-skew signal.\n--- gate output ---\n{out}"
    )
    assert marks.get("no_rejected_tool_calls") == "FAIL", (
        f"repeated rejection must be a FATAL [FAIL]. marks={marks}\n--- gate output ---\n{out}"
    )


def test_two_distinct_tools_each_recovered_is_warn_not_red(tmp_path):
    """Two DIFFERENT tools each flubbed ONCE and each recovered are two independent
    transients, not a systematic skew — the run stays GREEN with A8 as WARN. (Guards
    against a naive 'total rejections >= 2' rule that would re-introduce the false-RED.)"""
    events = _roll() + [
        _assistant_tool_use("t_uc_bad", "mcp__engine__update_character", {"skills": ["Arcana"]}),
        _user_tool_result("t_uc_bad", _validation_err("update_character", "skills"), is_error=True),
        _assistant_tool_use("t_uc_ok", "mcp__engine__update_character",
                            {"skill_proficiencies": ["Arcana"]}),
        _user_tool_result("t_uc_ok", json.dumps({"ok": True})),
        _assistant_tool_use("t_pb_bad", "mcp__engine__persist_beat", {"bad_field": 1}),
        _user_tool_result("t_pb_bad", _validation_err("persist_beat", "bad_field"), is_error=True),
        _assistant_tool_use("t_pb_ok", "mcp__engine__persist_beat", {"campaign_id": "c1"}),
        _user_tool_result("t_pb_ok", json.dumps({"ok": True})),
    ]
    rc, out, marks = _run_gate(tmp_path, events, _clean_player_state())
    assert rc == 0, (
        "two distinct tools each flubbed once and recovered are independent transients, "
        f"not a systematic skew — must NOT RED-cap.\n--- gate output ---\n{out}"
    )
    assert marks.get("no_rejected_tool_calls") == "WARN", (
        f"two recovered transients must surface as a WARN. marks={marks}\n--- gate output ---\n{out}"
    )


def test_mixed_one_unrecovered_one_recovered_stays_red(tmp_path):
    """A run with ONE unrecovered rejection (tool X never succeeds) AND one recovered
    transient (tool Y flubbed once, retried OK) is RED on X's account — a real defect is
    present, the recovered Y does not redeem it. (The fatal path must not be masked by a
    co-occurring recovered transient.)"""
    events = _roll() + [
        # X = update_character, rejected, NEVER recovered (unrecovered -> fatal).
        _assistant_tool_use("t_x", "mcp__engine__update_character", {"skills": ["Arcana"]}),
        _user_tool_result("t_x", _validation_err("update_character", "skills"), is_error=True),
        # Y = persist_beat, rejected once then retried OK (recovered transient -> warn-class).
        _assistant_tool_use("t_y_bad", "mcp__engine__persist_beat", {"bad_field": 1}),
        _user_tool_result("t_y_bad", _validation_err("persist_beat", "bad_field"), is_error=True),
        _assistant_tool_use("t_y_ok", "mcp__engine__persist_beat", {"campaign_id": "c1"}),
        _user_tool_result("t_y_ok", json.dumps({"ok": True})),
    ]
    rc, out, marks = _run_gate(tmp_path, events, _clean_player_state())
    assert rc == 1, (
        "an unrecovered rejection must RED even when a separate recovered transient "
        f"co-occurs.\n--- gate output ---\n{out}"
    )
    assert marks.get("no_rejected_tool_calls") == "FAIL", (
        f"the fatal class must win when both classes are present. marks={marks}\n"
        f"--- gate output ---\n{out}"
    )
    # And the fatal detail must name the UNRECOVERED tool, not the recovered one.
    assert "update_character" in out and "unrecovered" in out, (
        f"the RED detail must name update_character(unrecovered).\n--- gate output ---\n{out}"
    )


def test_benign_engine_guard_rejection_is_warn_not_a8(tmp_path):
    """A NON-schema engine-guard rejection (e.g. a travel-graph guard the DM is expected to
    hit + retry) is split off to the engine_guards_hit WARN and never touches A8 — that
    behavior is preserved by the de-flake."""
    events = _roll() + [
        _assistant_tool_use("t_tr", "mcp__engine__travel_to", {"location_id": "nowhere"}),
        _user_tool_result("t_tr", "no route from here to 'nowhere'", is_error=True),
    ]
    rc, out, marks = _run_gate(tmp_path, events, _clean_player_state())
    assert rc == 0, f"a benign engine-guard rejection must not RED.\n--- gate output ---\n{out}"
    assert "no_rejected_tool_calls" not in marks, (
        f"a non-schema guard rejection must not surface as A8 at all. marks={marks}\n"
        f"--- gate output ---\n{out}"
    )
    assert marks.get("engine_guards_hit") == "WARN", (
        f"the benign guard must surface as engine_guards_hit WARN. marks={marks}\n"
        f"--- gate output ---\n{out}"
    )


def test_clean_run_has_no_a8_check(tmp_path):
    """A run with ZERO rejections never emits the A8 check at all (additive: a clean run
    is byte-identical) — neither PASS, WARN, nor FAIL."""
    rc, out, marks = _run_gate(tmp_path, _roll(), _clean_player_state())
    assert rc == 0, f"clean run must be GREEN.\n--- gate output ---\n{out}"
    assert "no_rejected_tool_calls" not in marks, (
        f"a rejection-free run must not emit the A8 check. marks={marks}\n--- gate output ---\n{out}"
    )
