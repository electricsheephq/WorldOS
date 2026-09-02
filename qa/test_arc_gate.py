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
              manifest: dict | None = SEED_MANIFEST,
              state_obj: dict | None = None,
              truth_trace: dict | None = None) -> tuple[int, str]:
    run = tmp_path / "run.jsonl"
    run.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    state = tmp_path / "state.json"
    # A minimal snapshot: the pre-existing gates are all null-guarded / scope-guarded on it, so an
    # empty-but-valid state keeps this suite focused on the ARC rows.
    state.write_text(json.dumps(state_obj or {"characters": {}, "locations": {}, "quests": []}),
                     encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    if arc:
        env["WORLDOS_GATE_ARC"] = "1"
        if manifest is not None:
            mp = tmp_path / "seed_species.json"
            mp.write_text(json.dumps(manifest), encoding="utf-8")
            env["WORLDOS_ARC_SEED_SPECIES"] = str(mp)
        if truth_trace is not None:
            tp = tmp_path / "quest_trace.json"
            tp.write_text(json.dumps(truth_trace), encoding="utf-8")
            env["WORLDOS_ARC_QUEST_TRACE"] = str(tp)
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


def test_objective_ticked_against_invented_content_is_a_hard_fail(tmp_path):
    reason = "objective 3 'Slay the goblin boss': seeded boss boss-1 alive at 21/21"
    rc, out = _run_gate(tmp_path, _clean_events(), truth_trace={
        "quest_status": "completed", "completion_claimed": True,
        "completion_verified": False, "completion_truth": [reason],
    })
    assert rc == 1, out
    rows = [row for row in _arc_fail_rows(out) if "arc_objective_completion_truth" in row]
    assert rows, out
    assert f"objective ticked against invented content: {reason}" in rows[0]


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


# ── 5. the ESSENTIAL cast (clause F) ───────────────────────────────────────────────────────────
# The seeded arc, as the snapshot records it: Keeper Maera is the quest giver AND is named by the
# last objective; Merchant Oswin is an NPC no objective names (so NOT essential).
MAERA = "char_maera"
OSWIN = "char_oswin"


def _state_with_quest(status: str = "active", maera_dead: bool = False) -> dict:
    return {
        "locations": {},
        "characters": {
            MAERA: {"kind": "npc", "name": "Keeper Maera",
                    "dead": maera_dead, "current_hp": 0 if maera_dead else 1},
            OSWIN: {"kind": "npc", "name": "Merchant Oswin", "dead": False, "current_hp": 1},
        },
        "quests": {"q1": {"id": "q1", "title": "The Crypt Below", "status": status,
                          "giver_id": MAERA,
                          "objectives": ["Speak with Keeper Maera", "Clear the crypt of goblins",
                                         "Slay the goblin boss", "Return to Maera for the reward"],
                          "completed_objectives": []}},
    }


def test_killing_the_quest_giver_fails_with_beat(tmp_path):
    ev = _clean_events() + [
        _beat(15), _call("k1", "update_character",
                         {"character_id": MAERA, "patch": {"dead": True, "current_hp": 0}}),
        _result("k1", {"id": MAERA, "name": "Keeper Maera", "dead": True, "current_hp": 0}),
    ]
    rc, out = _run_gate(tmp_path, ev, state_obj=_state_with_quest("active", maera_dead=True))
    assert rc == 1, out
    row = [r for r in _arc_fail_rows(out) if "arc_essential_npc_killed" in r]
    assert row, out
    assert "beat 15" in row[0] and "Keeper Maera" in row[0], row[0]


def test_killing_the_giver_via_an_attack_result_is_caught(tmp_path):
    """The DM need not patch her dead — an attack whose RESULT reports her at 0 hp is the same kill."""
    ev = _clean_events() + [
        _beat(15), _call("a1", "attack", {"attacker_id": "char_boss", "defender_id": MAERA}),
        _result("a1", {"hit": True, "target": {"id": MAERA, "name": "Keeper Maera",
                                               "current_hp": 0, "dead": True}}),
    ]
    _rc, out = _run_gate(tmp_path, ev, state_obj=_state_with_quest("active", maera_dead=True))
    row = [r for r in _arc_fail_rows(out) if "arc_essential_npc_killed" in r]
    assert row and "beat 15" in row[0], out


def test_softlock_row_fires_when_the_quest_is_open_and_the_giver_is_dead(tmp_path):
    """State-only: however she died, an ACTIVE quest plus a dead giver is an arc that can no longer
    complete. This is the row that catches a kill the tool-call scan missed."""
    _rc, out = _run_gate(tmp_path, _clean_events(),
                         state_obj=_state_with_quest("active", maera_dead=True))
    row = [r for r in _arc_fail_rows(out) if "arc_quest_softlocked_on_dead_npc" in r]
    assert row and "The Crypt Below" in row[0], out


def test_no_softlock_row_when_the_quest_completed(tmp_path):
    """A giver who dies AFTER the quest resolves is a legitimate ending, not a softlock."""
    _rc, out = _run_gate(tmp_path, _clean_events(),
                         state_obj=_state_with_quest("completed", maera_dead=True))
    assert not [r for r in _arc_fail_rows(out) if "arc_quest_softlocked_on_dead_npc" in r], out


def test_a_non_essential_npc_may_die(tmp_path):
    """Merchant Oswin is named by no objective and gives no quest — killing him is allowed, and
    must not fire either row (the rule protects the arc, it does not make NPCs immortal)."""
    ev = _clean_events() + [
        _beat(9), _call("k1", "update_character",
                        {"character_id": OSWIN, "patch": {"dead": True, "current_hp": 0}}),
        _result("k1", {"id": OSWIN, "name": "Merchant Oswin", "dead": True, "current_hp": 0}),
    ]
    _rc, out = _run_gate(tmp_path, ev, state_obj=_state_with_quest("active"))
    assert not [r for r in _arc_fail_rows(out) if "arc_essential" in r or "softlock" in r], out


def test_reading_or_healing_the_giver_is_not_a_kill(tmp_path):
    ev = _clean_events() + [
        _beat(4), _call("g1", "get_character", {"character_id": MAERA}),
        _result("g1", {"id": MAERA, "name": "Keeper Maera", "current_hp": 1, "dead": False}),
        _call("h1", "update_character", {"character_id": MAERA, "patch": {"current_hp": 6}}),
        _result("h1", {"id": MAERA, "name": "Keeper Maera", "current_hp": 6, "dead": False}),
    ]
    _rc, out = _run_gate(tmp_path, ev, state_obj=_state_with_quest("active"))
    assert not [r for r in _arc_fail_rows(out) if "arc_essential_npc_killed" in r], out


def test_essential_cast_stands_down_without_a_quest(tmp_path):
    """No quest in the snapshot ⇒ no derivable essential cast ⇒ NO row (never a guessed FAIL)."""
    ev = _clean_events() + [
        _beat(15), _call("k1", "update_character",
                         {"character_id": MAERA, "patch": {"dead": True}}),
        _result("k1", {"id": MAERA, "dead": True}),
    ]
    _rc, out = _run_gate(tmp_path, ev)
    assert "arc_essential_npc_killed" not in out, out


# ── 6. bot-round hardening (PR #1766) ──────────────────────────────────────────────────────────
def test_killing_the_giver_after_the_quest_resolved_is_not_a_kill_row(tmp_path):
    """Clause (F) binds only UNTIL the quest resolves. A final beat that completes the last
    objective and then kills the giver as part of the resolved ending broke no rule — an arc that
    FINISHED must never be failed for what happened after it finished."""
    ev = _clean_events() + [
        _beat(20), _call("k1", "update_character",
                         {"character_id": MAERA, "patch": {"dead": True, "current_hp": 0}}),
        _result("k1", {"id": MAERA, "name": "Keeper Maera", "dead": True, "current_hp": 0}),
    ]
    _rc, out = _run_gate(tmp_path, ev, state_obj=_state_with_quest("completed", maera_dead=True))
    assert not [r for r in _arc_fail_rows(out) if "arc_essential" in r or "softlock" in r], out


def test_a_stabilised_giver_is_downed_not_dead(tmp_path):
    """The engine explicitly permits an NPC at 0 HP to be stable and healed (server.py: `ch.dead or
    (ch.current_hp <= 0 and not ch.stable)`), and clause (C) asks the DM to stabilise INSIDE the
    beat. A recoverable run must not be score-capped as a softlock."""
    state = _state_with_quest("active")
    state["characters"][MAERA] = {"kind": "npc", "name": "Keeper Maera",
                                  "dead": False, "current_hp": 0, "stable": True}
    ev = _clean_events() + [
        _beat(9), _call("a1", "attack", {"attacker_id": "char_boss", "defender_id": MAERA}),
        _result("a1", {"hit": True, "target": {"id": MAERA, "name": "Keeper Maera",
                                               "current_hp": 0, "dead": False, "stable": True}}),
    ]
    _rc, out = _run_gate(tmp_path, ev, state_obj=state)
    assert not [r for r in _arc_fail_rows(out) if "arc_essential" in r or "softlock" in r], out


def test_an_unstabilised_giver_at_zero_hp_still_softlocks(tmp_path):
    """The other side of the same coin: down and NOT stable, with the quest still open, is the
    softlock the row exists for — the downed/dead split must not become a hole."""
    state = _state_with_quest("active")
    state["characters"][MAERA] = {"kind": "npc", "name": "Keeper Maera",
                                  "dead": False, "current_hp": 0, "stable": False}
    _rc, out = _run_gate(tmp_path, _clean_events(), state_obj=state)
    assert [r for r in _arc_fail_rows(out) if "arc_quest_softlocked_on_dead_npc" in r], out


def test_create_character_fails_with_beat(tmp_path):
    """The arc runbook names create_character a forbidden call; give the claim teeth. This is the
    opening grief-NPC that all three failed Opus-5 arc runs minted."""
    ev = _clean_events() + [
        _beat(2), _call("c1", "create_character", {"name": "TomTom the grieving brother"}),
        _result("c1", {"id": "char_tom", "name": "TomTom the grieving brother"}),
    ]
    rc, out = _run_gate(tmp_path, ev)
    assert rc == 1, out
    row = [r for r in _arc_fail_rows(out) if "arc_no_create_character" in r]
    assert row and 'beat 2 "TomTom the grieving brother"' in row[0], out


def test_create_character_is_green_without_arc_mode(tmp_path):
    """The lens is opt-in: run_duo/play mint NPCs by design and must stay unaffected."""
    ev = _clean_events() + [
        _beat(2), _call("c1", "create_character", {"name": "TomTom"}),
        _result("c1", {"id": "char_tom", "name": "TomTom"}),
    ]
    _rc, out = _run_gate(tmp_path, ev, arc=False)
    assert not _arc_fail_rows(out), out
    assert "arc_no_create_character" not in out


def test_a_failed_create_character_is_not_a_fail(tmp_path):
    ev = _clean_events() + [
        _beat(2), _call("c1", "create_character", {"name": "TomTom"}),
        _result("c1", {"error": "guard: seeded arc"}, is_error=True),
    ]
    _rc, out = _run_gate(tmp_path, ev)
    assert not _arc_fail_rows(out), out
    assert "arc_no_create_character" not in out


def test_a_wrong_shaped_manifest_stands_the_spawn_rule_down(tmp_path):
    """A truncated/recovered run can leave valid JSON of the WRONG shape. That must stand the spawn
    rule down exactly as an unreadable manifest does — never abort the gate before its verdict."""
    ev = _clean_events() + [
        _beat(5), _call("s1", "spawn_monster", {"name": "Wight"}),
        _result("s1", {"spawned": [{"id": "c9"}], "name": "Wight"}),
    ]
    for shape in ([], "goblin-warrior", 7):
        _rc, out = _run_gate(tmp_path, ev, manifest=shape)
        assert "behavioral assertions" in out, (shape, out)   # a verdict was printed at all
        assert not _arc_fail_rows(out), (shape, out)
        assert "arc_only_seeded_species" not in out, (shape, out)
