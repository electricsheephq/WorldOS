"""Adversarial-hardening regression tests (S1 review C2/H4/H5).

These guard the QA harness itself — the behavioral gate (qa/assert_behavioral.py) and
the dashboard's write path (viewer/server.py sanitize_move) — which live outside the
engine package. We reach them from the repo root: the gate is stdlib-only so we drive
its real entry point via subprocess; the viewer is loaded with importlib (no engine
deps, and main() is guarded by __name__ so importing starts no server).

The holes these close:
  - C2: the gate never checked the DM actually RESOLVED the player's moves (a [cast] /
    [attack] / [check] move must be backed by the matching engine call somewhere).
  - H4: a raw-text (un-[tagged]) player turn means the facade was bypassed — now a hard
    fail (player_turns_structured), not a soft warning.
  - H5: /move accepted arbitrary JSON (incl. DM-side "narration") — sanitize_move now
    whitelists the move palette, forces role=player, caps length, drops unknown fields.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "qa" / "assert_behavioral.py"
PLAYER_IN_PARTY = {"characters": {"pc1": {"kind": "player", "name": "Kield"}}, "party": ["pc1"]}
PARTY_WITH_COMPANION = {
    "characters": {"pc1": {"kind": "player", "name": "Kield"}, "c1": {"kind": "companion", "name": "Petra"}},
    "party": ["pc1", "c1"],
}


def _write(p: Path, rows: list[dict]) -> None:
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _dm_event(tool_names=(), text="") -> dict:
    content = [{"type": "tool_use", "name": f"mcp__clawdnd-engine__{t}", "input": {}} for t in tool_names]
    if text:
        content.append({"type": "text", "text": text})
    return {"type": "assistant", "message": {"content": content}}


def _run_gate(tmp_path: Path, *, dm_tools, chat, moves, state, dm_text="The scene unfolds.") -> subprocess.CompletedProcess:
    run = tmp_path / "run.jsonl"; _write(run, [_dm_event(dm_tools, dm_text)])
    chatp = tmp_path / "chat.jsonl"; _write(chatp, chat)
    movp = tmp_path / "moves.jsonl"; _write(movp, moves)
    stp = tmp_path / "state.json"; stp.write_text(json.dumps(state), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE), str(run), str(stp), str(chatp), str(movp)],
        capture_output=True, text=True,
    )


def test_gate_green_when_dm_resolves_every_player_move(tmp_path):
    r = _run_gate(
        tmp_path,
        dm_tools=["cast_spell", "attack", "roll"],
        chat=[{"role": "player", "text": "[say] hi"}, {"role": "dm", "text": "The barkeep nods."},
              {"role": "player", "text": "[cast] cast fireball"}, {"role": "dm", "text": "Flame blooms."}],
        moves=[{"role": "player", "kind": "say", "text": "hi"},
               {"role": "player", "kind": "cast", "text": "cast fireball", "name": "fireball"},
               {"role": "player", "kind": "attack", "text": "attack goblin", "target": "goblin"},
               {"role": "player", "kind": "check", "text": "stealth", "skill": "stealth"}],
        state=PLAYER_IN_PARTY,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS] dm_resolved_player_moves" in r.stdout
    assert "[PASS] player_turns_structured" in r.stdout


def test_gate_green_cantrip_cast_resolved_via_attack(tmp_path):
    # CRITICAL false-RED regression (review #1): a damage cantrip ([cast] fire bolt) is
    # resolved via attack() with NO cast_spell — cantrips spend no slot, so a healthy DM
    # never calls cast_spell for them. Must stay GREEN.
    r = _run_gate(
        tmp_path,
        dm_tools=["attack", "roll"],  # NO cast_spell — the engine resolves attack cantrips via attack()
        chat=[{"role": "player", "text": "[cast] cast fire bolt at the wraith"},
              {"role": "dm", "text": "Flame leaps from your fingertips and the wraith reels."}],
        moves=[{"role": "player", "kind": "cast", "text": "cast fire bolt", "name": "fire bolt"}],
        state=PLAYER_IN_PARTY,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS] dm_resolved_player_moves" in r.stdout


def test_gate_red_when_dm_ignores_the_players_attack(tmp_path):
    # C2 (tight arm): the player attacked; the DM rolled SOMETHING (dice_used passes) but
    # never called attack() to resolve it — ignored the player. Old gate: GREEN. New: RED.
    # (Uses [attack], not [cast]: a [cast] is now resolvable via any dice path — see the
    # cantrip-via-attack test above — so [attack]/[check] carry the tight correlation.)
    r = _run_gate(
        tmp_path,
        dm_tools=["roll"],  # dice fired (dice_used passes), but no attack() → [attack] unresolved
        chat=[{"role": "player", "text": "[attack] attack the wraith with shortsword"},
              {"role": "dm", "text": "Meanwhile, across town, a bell tolls."}],
        moves=[{"role": "player", "kind": "attack", "text": "attack the wraith", "target": "wraith"}],
        state=PLAYER_IN_PARTY,
    )
    assert r.returncode == 1, r.stdout
    assert "[FAIL] dm_resolved_player_moves" in r.stdout


def test_gate_red_on_unstructured_player_turn(tmp_path):
    # H4: a raw-text player turn (no [tag]) means the facade was bypassed — the player
    # could be over-writing the world ("he never notices"). Hard fail now.
    r = _run_gate(
        tmp_path,
        dm_tools=["cast_spell", "attack", "roll"],
        chat=[{"role": "player", "text": "You slip past the guard unseen; he never notices you."},
              {"role": "dm", "text": "..."}],
        moves=[{"role": "player", "kind": "cast", "text": "cast fireball", "name": "fireball"}],
        state=PLAYER_IN_PARTY,
    )
    assert r.returncode == 1, r.stdout
    assert "[FAIL] player_turns_structured" in r.stdout


def test_gate_red_when_dm_writes_a_log_with_no_dialogue(tmp_path):
    # Structural story-craft FLOOR: a companion is present but the DM produced atmospheric
    # fragments with ZERO quoted dialogue across the run — the exact duo-h1 "log, not a
    # scene" failure. Must flip RED in code (not rely on the LLM rubric).
    r = _run_gate(
        tmp_path,
        dm_tools=["roll"],
        chat=[{"role": "player", "text": "[do] I scan the room"}, {"role": "dm", "text": "Fourteen names. A cold cup. Your move."},
              {"role": "player", "text": "[say] talk to me"}, {"role": "dm", "text": "Steam rising. Two names down."},
              {"role": "player", "text": "[do] I wait"}, {"role": "dm", "text": "The candle moves. Thirty seconds."}],
        moves=[{"role": "player", "kind": "do", "text": "scan"},
               {"role": "player", "kind": "say", "text": "talk"},
               {"role": "player", "kind": "do", "text": "wait"}],
        state=PARTY_WITH_COMPANION,
    )
    assert r.returncode == 1, r.stdout
    assert "[FAIL] dm_voices_characters" in r.stdout


def test_gate_green_when_dm_voices_characters(tmp_path):
    r = _run_gate(
        tmp_path,
        dm_tools=["roll"],
        chat=[{"role": "player", "text": "[do] I scan"}, {"role": "dm", "text": '"You\'re new here," the barkeep says, not looking up.'},
              {"role": "player", "text": "[say] hi"}, {"role": "dm", "text": '"Three coppers." Petra mutters, "I don\'t like this place."'},
              {"role": "player", "text": "[do] I sit"}, {"role": "dm", "text": '"Sit, then," he says, sliding the cup over.'}],
        moves=[{"role": "player", "kind": "do", "text": "scan"},
               {"role": "player", "kind": "say", "text": "hi"},
               {"role": "player", "kind": "do", "text": "sit"}],
        state=PARTY_WITH_COMPANION,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS] dm_voices_characters" in r.stdout


def test_gate_green_social_check_counts_as_dice_used(tmp_path):
    # A valid non-combat session (e.g. an S7 cold-open + quest-finding beat) that resolves a
    # social_check but rolls no attack/save must NOT trip dice_used — social_check rolls a d20.
    r = _run_gate(
        tmp_path,
        dm_tools=["social_check", "look_around", "travel_to"],
        chat=[{"role": "player", "text": "[say] I ask the chronicler what he knows."},
              {"role": "dm", "text": "\"You'll want to sit down for this,\" the warden says."},
              {"role": "player", "text": "[check] persuade the warden"},
              {"role": "dm", "text": "The warden wavers, then nods you through the cordon."}],
        moves=[{"role": "player", "kind": "say", "text": "I ask what he knows"},
               {"role": "player", "kind": "check", "text": "persuade the warden", "skill": "persuasion"}],
        state=PLAYER_IN_PARTY,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS] dice_used" in r.stdout


def test_gate_red_when_dm_ignores_a_companion_attack(tmp_path):
    # Party QA (#54): a COMPANION's [attack] (e.g. a saboteur turning) that the DM never resolves
    # must trip the gate — previously only player-role moves were counted, so an ignored companion
    # attack false-GREENed. The merged move file now carries companion moves too.
    r = _run_gate(
        tmp_path,
        dm_tools=["roll"],  # dice fired (dice_used passes) but NO attack() → companion [attack] unresolved
        chat=[{"role": "player", "text": "[say] Hold the line."},
              {"role": "dm", "text": "The fight breaks out."}],
        moves=[{"role": "player", "kind": "say", "text": "hold the line"},
               {"role": "companion", "kind": "attack", "text": "Grok attacks Kield"}],
        state=PARTY_WITH_COMPANION,
    )
    assert r.returncode != 0  # the ignored companion attack is caught
    assert "attack" in r.stdout.lower()


def test_gate_xp_awarded_satisfied_by_end_combat(tmp_path):
    # fidelity1/easter2 QA: end_combat AUTO-awards the defeated monsters' XP in the default
    # "xp" mode, so a clean fight needs no separate award_xp call — end_combat must satisfy the
    # xp_awarded check (it was falsely WARNing on fights that did award XP).
    r = _run_gate(
        tmp_path,
        dm_tools=["start_combat", "spawn_monster", "attack", "end_combat"],
        chat=[{"role": "player", "text": "[do] I draw and strike."},
              {"role": "dm", "text": "Steel rings; the thug drops."}],
        moves=[{"role": "player", "kind": "do", "text": "attack the thug"}],
        state=PLAYER_IN_PARTY,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS] xp_awarded" in r.stdout  # end_combat counts, not just an explicit award_xp


def test_gate_green_skill_check_counts_as_dice_used(tmp_path):
    # skill_check (the generic ability/skill d20) must satisfy dice_used the same way
    # social_check does — a camp/exploration beat that rolls a Perception or Investigation
    # check but never enters combat is a legitimate GREEN run (regression: camp-clarify2,
    # where skill_check:1 fired but the gate counted roll=attack=save=social=0 → false RED).
    r = _run_gate(
        tmp_path,
        dm_tools=["skill_check", "camp_scene", "long_rest"],
        chat=[{"role": "player", "text": "[do] I search the cold room for what the killers missed."},
              {"role": "dm", "text": "The stone is limestone, recently quarried — clean cuts, unhurried."},
              {"role": "player", "text": "[check] investigate the torn ledger"},
              {"role": "dm", "text": "Two pages gone; the binding still holds a sliver of one."}],
        moves=[{"role": "player", "kind": "do", "text": "search the room"},
               {"role": "player", "kind": "check", "text": "investigate the ledger", "skill": "investigation"}],
        state=PLAYER_IN_PARTY,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASS] dice_used" in r.stdout


# --- viewer /move sanitizer (H5) -------------------------------------------------
def _viewer():
    spec = importlib.util.spec_from_file_location("clawdnd_viewer_under_test", ROOT / "viewer" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # main() is __name__-guarded, so no server starts
    return mod


def test_sanitize_move_accepts_palette_and_forces_player_role():
    v = _viewer()
    move, why = v.sanitize_move({"role": "dm", "kind": "say", "text": "the name, and forty gold"})
    assert move is not None, why
    assert move["role"] == "player" and move["kind"] == "say"  # role can't be spoofed
    # clarify is a valid player move kind (ask the DM a question from the dashboard)
    cm, why2 = v.sanitize_move({"kind": "clarify", "text": "Is the guard armed?"})
    assert cm is not None, why2
    assert cm["kind"] == "clarify" and cm["role"] == "player"


def test_sanitize_move_rejects_dm_narration_and_unknown_kinds():
    v = _viewer()
    # H5: the over-write payload the human could otherwise POST
    assert v.sanitize_move({"kind": "narration", "text": "the dragon dies"})[0] is None
    assert v.sanitize_move({"kind": "system", "text": "you win"})[0] is None
    assert v.sanitize_move("not a dict")[0] is None
    assert v.sanitize_move({"kind": "say"})[0] is None  # needs text or name


def test_sanitize_move_drops_unknown_fields_and_caps_length():
    v = _viewer()
    m, _ = v.sanitize_move({"kind": "do", "text": "x", "evil": "rm -rf /", "role": "dm"})
    assert "evil" not in m and m["role"] == "player"
    long_m, _ = v.sanitize_move({"kind": "say", "text": "z" * 5000})
    assert len(long_m["text"]) <= 2000


# --- viewer combat projection (#65) --------------------------------------------
def test_combat_view_projects_active_combat_read_model():
    v = _viewer()
    snap = {
        "characters": {
            "hero": {
                "id": "hero", "name": "Hero", "kind": "player",
                "current_hp": 8, "max_hp": 12, "armor_class": 15,
                "conditions": ["prone"],
            },
            "gob": {
                "id": "gob", "name": "Goblin", "kind": "monster",
                "current_hp": 3, "max_hp": 7, "armor_class": 13,
                "conditions": [],
            },
        },
        "combat": {
            "active": True, "round": 2, "turn_index": 0,
            "action_used": False, "bonus_action_used": True,
            "order": [
                {"character_id": "hero", "initiative": 17, "reaction_used": False, "zone": "doorway"},
                {"character_id": "gob", "initiative": 11, "reaction_used": True},
            ],
        },
    }

    view = v.build_combat_view(snap)

    assert view["active"] is True
    assert view["round"] == 2
    assert view["current"]["id"] == "hero"
    assert view["current"]["name"] == "Hero"
    assert view["actions"] == {
        "action_available": True,
        "bonus_available": False,
        "reaction_available": True,
    }
    assert view["order"][0]["is_current"] is True
    assert view["order"][0]["hp"] == {"current": 8, "max": 12}
    assert view["order"][0]["ac"] == 15
    assert view["order"][0]["conditions"] == ["prone"]
    assert view["order"][0]["zone"] == "doorway"
    assert view["order"][1]["reaction_available"] is False
    assert view["warnings"] == []


def test_combat_view_warns_for_missing_and_malformed_combatants():
    v = _viewer()
    snap = {
        "characters": {},
        "combat": {
            "active": True, "round": 1, "turn_index": 4,
            "order": [
                {"character_id": "ghost", "initiative": 9},
                {"initiative": 7},
                "bad-row",
            ],
        },
    }

    view = v.build_combat_view(snap)

    assert view["active"] is True
    assert view["current"] is None
    assert view["order"][0]["id"] == "ghost"
    assert view["order"][0]["name"] == "Missing combatant"
    assert len(view["warnings"]) == 3
    assert any("missing character ghost" in w for w in view["warnings"])
    assert any("missing character_id" in w for w in view["warnings"])
    assert any("malformed combatant at index 2" in w for w in view["warnings"])


def test_combat_view_rejects_boolean_turn_index():
    v = _viewer()
    snap = {
        "characters": {"hero": {"id": "hero", "name": "Hero"}},
        "combat": {
            "active": True,
            "round": 1,
            "turn_index": True,
            "order": [{"character_id": "hero", "initiative": 10}],
        },
    }

    view = v.build_combat_view(snap)

    assert view["turn_index"] is None
    assert view["current"] is None
    assert view["order"][0]["is_current"] is False


def test_combat_view_inactive_when_no_active_combat():
    v = _viewer()

    assert v.build_combat_view({}) == {"active": False, "order": [], "warnings": []}
    assert v.build_combat_view({"combat": {"active": False, "order": []}})["active"] is False
