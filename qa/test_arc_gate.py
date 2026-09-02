"""Offline unit tests for the SEEDED-ARC lens in qa/assert_behavioral.py (WORLDOS_GATE_ARC).

Each test drives the real gate over a TINY synthetic stream-json transcript carrying exactly one
deviation, and asserts the arc lens turns it into a hard [FAIL] row naming its BEAT. Every case is
RED-FIRST by construction: the same transcript with the deviation removed must stay GREEN, and the
same transcript WITHOUT WORLDOS_GATE_ARC must stay GREEN too (the lens is opt-in, so run_duo/play
gates are unchanged).

The four deviations are the ones measured in the three failed Opus-5 arc runs and absent from the
passing opus-4-8 control (session-notes 2026-09-02 DM-DEVIATIONS):
  reroll_character · add_location · a spawn_monster off the seed's species · end_combat while
  the engine reported warning_live_hostiles.

Single-process (matches the sibling A-T suites):
    uv run --directory servers/engine python -m pytest qa/test_arc_gate.py -p no:xdist
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

QA = Path(__file__).resolve().parent
REPO = QA.parent
GATE = QA / "assert_behavioral.py"

# The seeded bestiary of qa/seed_adventure_demo.py, in the manifest shape run_adventure.sh writes.
SEED_MANIFEST = {"species": ["goblin-boss", "goblin-warrior"],
                 "names": ["Goblin Boss", "Goblin Warrior 1"]}


# ── synthetic-transcript builders ──────────────────────────────────────────────────────────────
def _beat(n: int) -> dict:
    return {"type": "worldos_arc_beat", "beat": n}


def _text(s: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": s}]}}


def _call(uid: str, name: str, inp: dict) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": uid, "name": f"mcp__worldos-engine__{name}", "input": inp}]}}


def _result(uid: str, payload, is_error: bool = False) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": uid, "is_error": is_error,
         "content": [{"type": "text", "text": json.dumps(payload)}]}]}}


def _clean_events() -> list[dict]:
    """A minimal ON-SEED arc: the DM narrates, re-spawns the seeded goblins, fights, closes cleanly."""
    return [
        _beat(1), _text("The camp smells of wet ash. Maera is waiting in the snug."),
        _beat(2), _call("t1", "spawn_monster", {"name": "Goblin", "count": 3}),
        _result("t1", {"spawned": [{"id": "c1", "name": "Goblin Warrior 1", "reused": True}],
                       "name": "Goblin Warrior", "cr": "1/4"}),
        _beat(3), _call("t2", "end_combat", {"resolution": "the goblins are down"}),
        _result("t2", {"ok": True, "xp_awarded": 150}),
    ]


def _run_gate(tmp_path: Path, events: list[dict], *, arc: bool = True,
              manifest: dict | None = SEED_MANIFEST) -> tuple[int, str]:
    run = tmp_path / "run.jsonl"
    run.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    state = tmp_path / "state.json"
    # A minimal snapshot: the pre-existing gates are all null-guarded / scope-guarded on it, so an
    # empty-but-valid state keeps this suite focused on the ARC rows.
    state.write_text(json.dumps({"characters": {}, "locations": {}, "quests": []}), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    if arc:
        env["WORLDOS_GATE_ARC"] = "1"
        if manifest is not None:
            mp = tmp_path / "seed_species.json"
            mp.write_text(json.dumps(manifest), encoding="utf-8")
            env["WORLDOS_ARC_SEED_SPECIES"] = str(mp)
    proc = subprocess.run([sys.executable, str(GATE), str(run), str(state)],
                          cwd=str(REPO), env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def _fail_rows(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines() if ln.strip().startswith("[FAIL]")]


def _arc_fail_rows(out: str) -> list[str]:
    """Only the ARC lens's rows. The synthetic transcripts are deliberately minimal, so the
    pre-existing full-session gates (dice_used, player_in_party, …) legitimately fire on them —
    this suite owns the arc_* rows and asserts nothing about the rest of the gate."""
    return [r for r in _fail_rows(out) if r.startswith("[FAIL] arc_")]


# ── the GREEN baseline (so every RED below is caused by the deviation, not the harness) ─────────
def test_clean_arc_transcript_is_green(tmp_path):
    _rc, out = _run_gate(tmp_path, _clean_events())
    assert not _arc_fail_rows(out), out


# ── 1. reroll_character ────────────────────────────────────────────────────────────────────────
def test_reroll_character_fails_with_beat(tmp_path):
    ev = _clean_events() + [
        _beat(13), _call("r1", "reroll_character", {"dead_id": "char_365871336422", "name": "Jory Vance"}),
        _result("r1", {"id": "char_new", "name": "Jory Vance"}),
    ]
    rc, out = _run_gate(tmp_path, ev)
    assert rc == 1, out
    row = [r for r in _arc_fail_rows(out) if "arc_no_reroll_character" in r]
    assert row, out
    assert "beat 13" in row[0], row[0]


def test_reroll_character_is_green_without_arc_mode(tmp_path):
    ev = _clean_events() + [
        _beat(13), _call("r1", "reroll_character", {"dead_id": "x", "name": "Jory Vance"}),
        _result("r1", {"id": "char_new"}),
    ]
    _rc, out = _run_gate(tmp_path, ev, arc=False)
    assert not _arc_fail_rows(out), out
    assert "arc_no_reroll_character" not in out


# ── 2. add_location ────────────────────────────────────────────────────────────────────────────
def test_add_location_fails_with_beat(tmp_path):
    ev = _clean_events() + [
        _beat(8), _call("a1", "add_location", {"name": "The Black Passage", "make_current": True}),
        _result("a1", {"id": "loc_black_passage"}),
    ]
    rc, out = _run_gate(tmp_path, ev)
    assert rc == 1, out
    row = [r for r in _arc_fail_rows(out) if "arc_no_add_location" in r]
    assert row, out
    assert "beat 8" in row[0] and "The Black Passage" in row[0], row[0]


def test_add_location_is_green_without_arc_mode(tmp_path):
    ev = _clean_events() + [
        _beat(8), _call("a1", "add_location", {"name": "The Black Passage"}),
        _result("a1", {"id": "loc_x"}),
    ]
    _rc, out = _run_gate(tmp_path, ev, arc=False)
    assert not _arc_fail_rows(out), out
    assert "arc_no_add_location" not in out


# ── 3. a spawn_monster off the seed's species ──────────────────────────────────────────────────
def test_non_seeded_spawn_fails_with_beat(tmp_path):
    ev = _clean_events() + [
        _beat(5), _call("s1", "spawn_monster", {"name": "Zombie", "count": 1}),
        _result("s1", {"spawned": [{"id": "c9", "name": "Zombie"}], "name": "Zombie", "cr": "1/4"}),
    ]
    rc, out = _run_gate(tmp_path, ev)
    assert rc == 1, out
    row = [r for r in _arc_fail_rows(out) if "arc_only_seeded_species" in r]
    assert row, out
    assert "beat 5" in row[0] and "Zombie" in row[0], row[0]


def test_seeded_spawn_by_alias_stays_green(tmp_path):
    """spawn_monster("Goblin") resolves to the seeded "Goblin Warrior" — the gate compares the
    engine's RESOLVED canonical name, not the DM's requested string, so the alias must not FAIL."""
    ev = _clean_events() + [
        _beat(4), _call("s1", "spawn_monster", {"name": "Goblin", "count": 2}),
        _result("s1", {"spawned": [{"id": "c2", "name": "Goblin Warrior 1"}], "name": "Goblin Warrior"}),
    ]
    _rc, out = _run_gate(tmp_path, ev)
    assert not _arc_fail_rows(out), out


def test_near_miss_species_is_not_read_as_on_seed(tmp_path):
    """"Hobgoblin Warrior" CONTAINS "goblin warrior" — a substring comparison would pass it. The
    slug comparison must FAIL it (reboot4 beat 7 spawned exactly this)."""
    ev = _clean_events() + [
        _beat(7), _call("s1", "spawn_monster", {"name": "Hobgoblin Warrior", "count": 1}),
        _result("s1", {"spawned": [{"id": "c7", "name": "Hobgoblin Warrior"}],
                       "name": "Hobgoblin Warrior"}),
    ]
    rc, out = _run_gate(tmp_path, ev)
    assert rc == 1, out
    assert any("arc_only_seeded_species" in r and "Hobgoblin Warrior" in r for r in _arc_fail_rows(out)), out


def test_failed_spawn_result_is_not_failed(tmp_path):
    """An unresolvable name returns {"error": …} and mints nothing — never a FAIL (no false RED)."""
    ev = _clean_events() + [
        _beat(6), _call("s1", "spawn_monster", {"name": "Gribbly", "count": 1}),
        _result("s1", {"error": "no creature named 'Gribbly' in the bestiary", "suggestions": []}),
    ]
    _rc, out = _run_gate(tmp_path, ev)
    assert not _arc_fail_rows(out), out


def test_spawn_rule_stands_down_without_a_manifest(tmp_path):
    """No seed manifest ⇒ the species rule cannot be derived, so it emits NO row at all rather than
    guessing (the other three arc rules still apply)."""
    ev = _clean_events() + [
        _beat(5), _call("s1", "spawn_monster", {"name": "Zombie"}),
        _result("s1", {"spawned": [{"id": "c9", "name": "Zombie"}], "name": "Zombie"}),
    ]
    _rc, out = _run_gate(tmp_path, ev, manifest=None)
    assert not _arc_fail_rows(out), out
    assert "arc_only_seeded_species" not in out


# ── 4. end_combat with living hostiles ─────────────────────────────────────────────────────────
def test_false_end_combat_fails_with_beat(tmp_path):
    ev = _clean_events() + [
        _beat(6), _call("e1", "end_combat", {"resolution": ""}),
        _result("e1", {"ok": True, "warning_live_hostiles": {
            "count": 3, "resolved": False,
            "hostiles": [{"id": "c1", "name": "Goblin Warrior 1", "hp": "7/7"}]}}),
    ]
    rc, out = _run_gate(tmp_path, ev)
    assert rc == 1, out
    row = [r for r in _arc_fail_rows(out) if "arc_end_combat_live_hostiles" in r]
    assert row, out
    assert "beat 6" in row[0] and "3 alive" in row[0], row[0]


def test_clean_end_combat_stays_green(tmp_path):
    _rc, out = _run_gate(tmp_path, _clean_events())
    assert "arc_end_combat_live_hostiles" not in out, out


# ── beat attribution ───────────────────────────────────────────────────────────────────────────
def test_calls_before_the_first_marker_report_an_unknown_beat(tmp_path):
    """The cold-open grounding turn precedes beat 1's marker — it must still FAIL, labelled 'beat ?'
    rather than silently attributed to beat 1."""
    ev = [
        _call("a1", "add_location", {"name": "A Second Camp"}),
        _result("a1", {"id": "loc_x"}),
    ] + _clean_events()
    rc, out = _run_gate(tmp_path, ev)
    assert rc == 1, out
    row = [r for r in _arc_fail_rows(out) if "arc_no_add_location" in r]
    assert row and "beat ?" in row[0], out


def test_all_four_rules_fail_together(tmp_path):
    ev = _clean_events() + [
        _beat(9), _call("a1", "add_location", {"name": "The Pillared Throne Hall"}),
        _result("a1", {"id": "loc_p"}),
        _call("s1", "spawn_monster", {"name": "Wight"}),
        _result("s1", {"spawned": [{"id": "c8", "name": "Wight"}], "name": "Wight"}),
        _beat(10), _call("e1", "end_combat", {}),
        _result("e1", {"warning_live_hostiles": {"count": 2, "resolved": False, "hostiles": []}}),
        _beat(11), _call("r1", "reroll_character", {"dead_id": "x", "name": "Jory"}),
        _result("r1", {"id": "c_new"}),
    ]
    rc, out = _run_gate(tmp_path, ev)
    assert rc == 1, out
    names = {"arc_no_add_location", "arc_only_seeded_species",
             "arc_end_combat_live_hostiles", "arc_no_reroll_character"}
    assert names <= {n for n in names if any(n in r for r in _arc_fail_rows(out))}, out
