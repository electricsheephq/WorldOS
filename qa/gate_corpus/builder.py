#!/usr/bin/env python3
"""Generate the behavioral-gate regression corpus under qa/gate_corpus/cases/.

Each case is a MINIMAL bundle of the exact run-artifacts qa/assert_behavioral.py reads
(run.jsonl / state.json / [chat.jsonl] / [moves.jsonl]) crafted to trip ONE specific FATAL
`chk(...)` check — and ONLY that check (the baseline is built clean so every OTHER fatal gate
passes, so the assertion is unambiguous). A manifest.json pins {case_dir -> expected_red_check}.

Why generated-then-committed (not authored by hand): the gate is sensitive to subtle artifact
shapes; a generator keeps every fixture minimal, isolated, and regenerable after an INTENTIONAL
gate change, while the committed output stays diffable/inspectable in review. Run:

    python qa/gate_corpus/builder.py

and re-run the corpus test (qa/test_behavioral_gate_corpus.py) to confirm each case still RED-s
on its expected check. This script writes ONLY into qa/gate_corpus/cases/ + the manifest — it
NEVER touches scores.db, the ledger, RRI.json, or the real transcripts.

Provenance: where a real recorded RED exists for a check (see REAL_RED_PROVENANCE), the synthetic
fixture is modeled on that real failure shape (e.g. the extra_forbidden update_character rejection
that RED-capped ow-swB-123842); the fixtures are kept minimal rather than copying the multi-MB
real artifacts, but the failure MODE is faithful to recorded reality.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CASES = _HERE / "cases"
_MANIFEST = _HERE / "manifest.json"
_GATE = _HERE.parent / "assert_behavioral.py"

# Real recorded REDs whose failure MODE each synthetic fixture is modeled on. Sourced from
# /Users/lume/WorldOS/qa/transcripts/*.gate.txt (historical path — /Users/lume/WorldOS/qa/transcripts/*.gate.txt; that machine is gone, see NOW.md) at corpus-build time. A blank value means
# the check has no recorded real RED and the fixture is purely synthetic (still faithful to the
# gate's documented trip condition).
REAL_RED_PROVENANCE = {
    "no_rejected_tool_calls": "ow-swB-123842 (extra_forbidden update_character), baseline-rc1 (persist_beat)",
    "party_traveled": "cue-thaw, ow-dal-003502 (visited 1/N after >=6 beats)",
    "dice_used": "openworlds-c2-234542 (roll=attack=save=social=skill_check=0)",
    "player_in_party": "ow-duoF-112226, ow-rv1-134258, ow-swA-123842 (party=1 players=0)",
    "dm_voices_characters": "ow-rv1-134258 (0/9 DM turns with quoted dialogue, companion present)",
    "narration_no_ooc_leak": "2026-06-17 craft audit (#972): 5+ first-person OOC authoring preambles in a 4.6-prose run, un-gated, inflated the LLM story score to 4.8",
}

# Explicit TODO reasons for FATAL checks the coverage audit below finds uncovered, where the
# generic auto-flagged reason isn't the truthful story. A check listed here is INTENTIONALLY
# left out of _CASES_SPEC (not just "not gotten to yet") — the reason explains why a corpus
# fixture doesn't fit and points at the alternate coverage that closes the gap.
TODO_REASONS = {
    # #1285: run_infra_valid trips on a SIDECAR <run>.infra_invalid.json sentinel file next to
    # run.jsonl (qa/lib_beat_driver.sh worldos_mark_run_infra_invalid), not on the content of
    # run.jsonl/state.json/chat.jsonl/moves.jsonl the corpus's _write_case + _argv_for model —
    # those four are the gate's only POSITIONAL argv artifacts; a 5th sidecar file has no slot in
    # that model. Not a fiction-content bundle the builder can synthesize; covered instead by
    # qa/test_assert_behavioral.py::test_run_infra_invalid_sentinel_flips_red +
    # test_no_infra_invalid_sentinel_passes + test_infra_invalid_sentinel_caps_no_other_gate_weakened,
    # and by qa/test_run_invalidation_guard.sh (exercises the real bash stamping helper end-to-end).
    "run_infra_valid": (
        "infra-sentinel guard: RED requires a .infra_invalid.json sidecar next to run.jsonl "
        "(stamped by qa/lib_beat_driver.sh worldos_mark_run_infra_invalid), not a fiction bundle "
        "the builder synthesizes via _write_case's 4 positional artifacts — covered by "
        "qa/test_assert_behavioral.py::test_run_infra_invalid_sentinel_flips_red (+ 2 sibling "
        "tests) and qa/test_run_invalidation_guard.sh"
    ),
}
# The SEEDED-ARC lens (WORLDOS_GATE_ARC) rows share one reason: they are opt-in — only
# qa/run_adventure.sh sets the env toggle, and the species rule additionally needs the run's
# seed-species manifest on a 5th path (WORLDOS_ARC_SEED_SPECIES). Neither is expressible in
# _write_case's four POSITIONAL argv artifacts, and every synthetic arc bundle also trips the
# baseline session gates (dice_used / player_in_party), so no case here can ISOLATE one arc row.
# Covered instead by qa/test_arc_gate.py, which drives this same gate CLI over tiny synthetic
# transcripts with the toggles set and asserts each row is a [FAIL] naming its beat.
for _arc_check in ("arc_objective_completion_truth", "arc_no_reroll_character", "arc_no_add_location", "arc_no_create_character",
                   "arc_only_seeded_species", "arc_end_combat_live_hostiles",
                   "arc_essential_npc_killed", "arc_quest_softlocked_on_dead_npc"):
    TODO_REASONS[_arc_check] = (
        "seeded-arc lens: opt-in via WORLDOS_GATE_ARC (+ WORLDOS_ARC_SEED_SPECIES for the species "
        "rule) — env toggles and a 5th artifact path that _write_case's 4 positional argv "
        "artifacts cannot express, and no synthetic arc bundle can isolate one arc row from the "
        "baseline session gates — covered by qa/test_arc_gate.py (red-first, per-row, per-beat)"
    )


# ── event / artifact builders ─────────────────────────────────────────────────

def _assistant_tool_use(tool_use_id: str, name: str, inp: dict | None = None) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": tool_use_id,
                                 "name": name, "input": inp or {}}]},
    }


def _assistant_text(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _user_tool_result(tool_use_id: str, text: str, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_use_id,
                                 "content": [{"type": "text", "text": text}],
                                 "is_error": is_error}]},
    }


def _roll(tid: str = "t_roll", total: int = 14) -> list[dict]:
    """A clean d20 roll pair — satisfies dm_produced_output + dice_used without being a combat
    resolver (so it never accidentally satisfies combat_resolved)."""
    return [_assistant_tool_use(tid, "mcp__engine__roll", {"sides": 20}),
            _user_tool_result(tid, json.dumps({"total": total}))]


def _clean_player_state() -> dict:
    """Baseline state: one living player in party[], xp>0 (so xp-progression gates pass),
    no combat, no monsters, no duplicate companions, day/visited advanced enough that the
    world-progression FATAL floors pass when a case crosses MIN_BEATS."""
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


def _dm_chat_row(text: str) -> dict:
    return {"role": "dm", "text": text}


def _player_chat_row(text: str) -> dict:
    return {"role": "player", "text": text}


def _move(kind: str, role: str = "player", text: str = "") -> dict:
    return {"role": role, "kind": kind, "text": text or f"[{kind}] does a thing"}


def _write_case(name: str, run_events: list[dict], state: dict,
                chat: list[dict] | None = None, moves: list[dict] | None = None) -> None:
    d = _CASES / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.jsonl").write_text(
        "\n".join(json.dumps(e) for e in run_events) + "\n", encoding="utf-8")
    (d / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    if chat is not None:
        (d / "chat.jsonl").write_text(
            "\n".join(json.dumps(r) for r in chat) + "\n", encoding="utf-8")
    if moves is not None:
        (d / "moves.jsonl").write_text(
            "\n".join(json.dumps(m) for m in moves) + "\n", encoding="utf-8")


# ── per-check fixture definitions ───────────────────────────────────────────────
# Each builder returns (run_events, state, chat_or_None, moves_or_None) crafted so that ONLY the
# named FATAL check fails. Comments cite the exact gate condition each fixture exploits.

def case_dm_produced_output():
    # chk #1: dm_text==0 AND total tool calls==0 -> a truly dead/blank run. (Such a dead run
    # also necessarily trips dice_used + player_in_party; the corpus asserts the EXPECTED check
    # APPEARS among the fails, which is the faithful semantic for a dead run.)
    return [], {"party": [], "characters": {}}, None, None


def case_narration_no_ooc_leak():
    # chk 1b: player-facing DM prose leaks OUT-OF-CHARACTER craft-scaffolding. FATAL when
    # n_leak >= 3 AND dm_text >= MIN_BEATS(6) — a pervasively-broken player surface. Models the
    # 2026-06-17 craft audit (#972): first-person authoring preambles in a 4.6-prose run, entirely
    # un-gated, inflated the LLM story score to 4.8. A clean `roll` keeps dice_used passing; NO
    # companion so dm_voices_characters stays inert; the baseline state (day=2, 2 visited, xp>0)
    # keeps the world/xp floors passing at session_beats==dm_text; no chat/moves -> the facade lane
    # is inert; 6 (< STRUCTURAL_MIN_BEATS=10) beats + no companion -> structural_completeness inert.
    # 6 DM text beats (== MIN_BEATS), 4 of them leaking the exact OOC patterns _NARRATION_LEAK_RE
    # bans, so narration_no_ooc_leak is the SOLE fatal fail.
    texts = [
        # leak: "as the player character"
        "Now let me seat Dal Lightspark as the player character and open on the tavern.",
        # clean in-fiction prose (scores 0 leaks)
        "Rain hammers the shutters of the Elfsong Tavern as you shoulder inside, the common room "
        "thick with pipe-smoke and low talk.",
        # leak: "continuity check"
        "Continuity check — let me correct that: the barkeep already named the missing caravan a moment ago.",
        # clean in-fiction prose (quoted dialogue; no companion present, so dm_voices stays inert)
        "The barkeep leans close, her voice dropping. \"You're asking after the caravan? Bad "
        "business, that — three nights gone now.\"",
        # leak: "inciting incident"
        "This is the inciting incident of the arc, so I'll raise the stakes before the scene turns.",
        # leak: "here's how round <n> actually went"
        "Here's how round one actually went: the cutpurse moved first and the lantern guttered out.",
    ]
    events = _roll() + [_assistant_text(t) for t in texts]
    return events, _clean_player_state(), None, None


def case_both_sides_acted():
    # chk #2: chat present, player turns > 0 but dm turns == 0. Keep player rows < MIN_BEATS(6)
    # so the world-progression floors stay inert, and < 3 dm rows so dm_voices is inert.
    chat = [_player_chat_row("[say] hello")]
    return _roll(), _clean_player_state(), chat, None


def case_player_turns_structured():
    # chk #3 (facade lane): a relayed player turn that is RAW text (no [tag] prefix) means the
    # player bypassed the facade -> FATAL. Needs has_facade (a moves arg present). A DM reply is
    # included so both_sides_acted passes and player_turns_structured is the SOLE fail.
    chat = [_player_chat_row("I sneak past the guard unseen"),  # raw, no leading [
            _dm_chat_row('"Roll stealth," she says.')]
    moves = [_move("say")]
    return _roll(), _clean_player_state(), chat, moves


def case_dm_voices_characters():
    # chk #3.6: >=3 dm turns, ZERO quoted dialogue, AND a companion present -> FATAL.
    state = _clean_player_state()
    state["characters"]["cmp1"] = {"name": "Astarion", "kind": "companion",
                                   "location_id": "loc_camp", "xp": 300}
    state["party"].append("cmp1")
    chat = [_player_chat_row("[say] hi"),
            _dm_chat_row("The wind moves through the camp."),
            _dm_chat_row("Smoke rises. The fire gutters low."),
            _dm_chat_row("Shadows lengthen across the stones.")]
    return _roll(), state, chat, None


def case_player_used_facade():
    # chk #3.5: a facade run (moves arg present) with ZERO recorded moves -> the player's tools
    # were blocked/unused. Empty moves file. A chat exists (faithful: facade runs emit one). A DM
    # reply keeps both_sides_acted satisfied so player_used_facade is the SOLE fail.
    chat = [_player_chat_row("[say] I try to act but my tools are blocked"),
            _dm_chat_row('"Something is wrong," she frowns.')]
    return _roll(), _clean_player_state(), chat, []


def case_dm_resolved_player_moves():
    # chk C2: a facade [attack] move with the DM never calling attack() -> ignored move. Keep a
    # `roll` so dice_used passes; attack stays 0 so the [attack] is unresolved. mv < MIN_BEATS so
    # the agency floors stay WARN-only and inert. A DM reply keeps both_sides_acted satisfied so
    # dm_resolved_player_moves is the SOLE fail.
    chat = [_player_chat_row("[attack] I swing at the goblin"),
            _dm_chat_row('"It dodges," he warns.')]
    moves = [_move("attack")]
    return _roll(), _clean_player_state(), chat, moves


def case_combat_resolved():
    # chk #5: start_combat fired but attack + cast_spell + saving_throw all 0. Provide a clean
    # `roll` (satisfies dice_used WITHOUT being a combat resolver) so only combat_resolved fails.
    events = _roll() + [_assistant_tool_use("t_sc", "mcp__engine__start_combat", {})]
    return events, _clean_player_state(), None, None


def case_combat_not_left_active():
    # chk: a GENUINE ABANDON (the preserved FATAL path after the truncation-vs-abandon split).
    # combat_not_left_active is FATAL only when ALL of:
    #   • facade mv >= COMBAT_ABANDON_MIN_BEATS(10) — a SUBSTANTIAL run (room to resolve), AND
    #   • start_combat fired EARLY in the tool stream (NOT in the final ~20%) — the fight had time
    #     to be wrapped, so it wasn't truncated, AND
    #   • end_combat was never called, AND
    #   • state.combat.active is still True at the snapshot.
    # A SHORT run (< 10 beats) OR a LATE start_combat is treated as a harness-length truncation ⇒
    # WARN (the qa/transcripts/claude-1v1-2 opus duo: start_combat@36/42 calls + only 7 beats), so
    # this fixture must model the real-defect shape, not the truncation shape.
    state = _clean_player_state()
    state["combat"] = {"active": True, "round": 3}
    moves = [_move("say") for _ in range(12)]  # >= COMBAT_ABANDON_MIN_BEATS(10): substantial run
    # 12 player beats (>= COMBAT_ABANDON_MIN_BEATS) interleaved with DM replies carrying quoted
    # dialogue so both_sides_acted + dm_voices_characters both pass. No companion in state ->
    # structural_completeness stays inert even at >=10 beats, so combat_not_left_active is the SOLE
    # fatal fail.
    chat = []
    for i in range(12):
        chat.append(_player_chat_row(f"[say] beat {i}"))
        chat.append(_dm_chat_row('"The fight rages on," she calls.'))
    # start_combat fires EARLY (first of the tool calls) and an attack resolves it (so combat_resolved
    # passes), but end_combat is NEVER called and combat stays active -> a fight ABANDONED with room
    # to resolve. The clean trailing `_roll()` keeps start_combat well inside the first 80% of the
    # stream (last_sc index 0 of >=5 calls), so `started_late` is False -> the abandon path, FATAL.
    events = [
        _assistant_tool_use("t_sc", "mcp__engine__start_combat", {}),
        _user_tool_result("t_sc", json.dumps({"ok": True})),
        _assistant_tool_use("t_atk", "mcp__engine__attack", {"target": "g1"}),
        _user_tool_result("t_atk", json.dumps({"hit": True, "damage": 6})),
    ] + _roll()
    return events, state, chat, moves


def case_party_traveled():
    # chk: session_beats >= SINGLE_SCENE_MIN_BEATS(8), day>1 (world_advanced_time passes) but
    # visited < 2 (only the opening scene). Isolates party_traveled. Uses chat beats (player rows)
    # for session_beats.
    # SEVERITY IS BEAT-SCOPED (the 2026-06-19 false-cap fix): party_traveled is FATAL only at
    # >= SINGLE_SCENE_MIN_BEATS(8) — below that a single-scene vignette is a legitimate WARN, not a
    # frozen stall. So the fixture must carry 8 player beats to land on the PRESERVED FATAL path
    # (at 6 it would now correctly WARN and this case would no longer flip RED). Keep the builder in
    # lock-step with the committed 8-beat fixture so a regenerate doesn't silently revert the fix.
    state = _clean_player_state()
    state["day"] = 2  # world_advanced_time passes
    state["locations"] = {"loc_start": {"name": "Tavern", "visited": True}}  # visited == 1
    state["current_location_id"] = "loc_start"
    state["characters"]["pc1"]["location_id"] = "loc_start"
    # dm rows must carry dialogue so dm_voices passes (>=3 dm rows present).
    chat = ([_player_chat_row(f"[say] beat {i}") for i in range(8)] +
            [_dm_chat_row('"We press on," she says.') for _ in range(3)])
    return _roll(), state, chat, None


def case_world_advanced_time():
    # chk: session_beats>=MIN_BEATS, visited>=2 (party_traveled passes) but day==1 AND
    # time_of_day=="morning" (the clock never moved). Isolates world_advanced_time.
    state = _clean_player_state()
    state["day"] = 1
    state["time_of_day"] = "morning"
    # keep 2 visited so party_traveled passes
    chat = ([_player_chat_row(f"[say] beat {i}") for i in range(6)] +
            [_dm_chat_row('"Onward," he murmurs.') for _ in range(3)])
    return _roll(), state, chat, None


def case_player_in_party():
    # chk #6: party non-empty but contains no kind=="player" character (state-integrity).
    state = _clean_player_state()
    state["characters"]["pc1"]["kind"] = "npc"  # the only party member is no longer a player
    return _roll(), state, None, None


def case_dice_used():
    # chk #4: a whole session with ZERO dice (roll + attack + saving_throw + social_check +
    # skill_check all 0). To isolate it, the run still produces DM output (an assistant text turn
    # + a non-dice tool call like log_event) so dm_produced_output passes, and the baseline state
    # keeps a valid party so player_in_party passes. Modeled on openworlds-c2-234542 (all dice 0).
    events = [
        _assistant_text("The tavern door swings open."),
        _assistant_tool_use("t_log", "mcp__engine__log_event",
                            {"kind": "narration", "text": "A hush falls."}),
        _user_tool_result("t_log", json.dumps({"ok": True})),
    ]
    return events, _clean_player_state(), None, None


def case_no_duplicate_companion():
    # chk #7: two companions with the same (normalized) name -> the engine's dedup guard breached.
    state = _clean_player_state()
    state["characters"]["c1"] = {"name": "Shadowheart", "kind": "companion",
                                 "location_id": "loc_camp", "xp": 300}
    state["characters"]["c2"] = {"name": "shadowheart ", "kind": "companion",
                                 "location_id": "loc_camp", "xp": 300}
    state["party"] += ["c1", "c2"]
    return _roll(), state, None, None


def case_no_rejected_tool_calls():
    # chk A8: a tool call REJECTED with an extra_forbidden schema/validation error (version skew /
    # wrong field). Modeled on ow-swB-123842's update_character rejection that RED-capped the run.
    # NOTE (#897 de-flake): this fixture is deliberately UNRECOVERED — update_character is rejected
    # and NEVER successfully retried, so it is the genuine version-skew defect that must STAY FATAL.
    # (A recovered-transient variant lives in the GREEN corpus as
    # case_no_rejected_tool_calls_recovered_warn.) Do NOT add a clean update_character call here or
    # this case would correctly demote to a WARN and stop RED-ing.
    err = ("Error executing tool update_character: 1 validation error for Character\n"
           "skills\n  Extra inputs are not permitted "
           "[type=extra_forbidden, input_value=['Arcana'], input_type=list]")
    events = _roll() + [
        _assistant_tool_use("t_uc", "mcp__engine__update_character", {"skills": ["Arcana"]}),
        _user_tool_result("t_uc", err, is_error=True),
    ]
    return events, _clean_player_state(), None, None


def case_no_rejected_tool_calls_recovered_warn():
    # GREEN fixture (#897) — the A8 de-flake scope guard. A SINGLE malformed update_character
    # (extra_forbidden) that the DM immediately RETRIES correctly (the same tool, is_error=False)
    # is a recovered transient — invisible to the player. Under the #897 de-flake A8 is demoted to
    # a WARN (recovered transient), so the gate must exit GREEN with no_rejected_tool_calls as a
    # [WARN] line — NOT a RED cap. A future edit that re-promotes recovered transients to FATAL
    # (re-introducing the high-variance false-RED) flips this case RED and the green test fails.
    err = ("Error executing tool update_character: 1 validation error for Character\n"
           "skills\n  Extra inputs are not permitted "
           "[type=extra_forbidden, input_value=['Arcana'], input_type=list]")
    events = _roll() + [
        _assistant_tool_use("t_uc_bad", "mcp__engine__update_character", {"skills": ["Arcana"]}),
        _user_tool_result("t_uc_bad", err, is_error=True),
        # the DM retries the SAME tool correctly -> recovered.
        _assistant_tool_use("t_uc_ok", "mcp__engine__update_character",
                            {"skill_proficiencies": ["Arcana"]}),
        _user_tool_result("t_uc_ok", json.dumps({"ok": True})),
    ]
    return events, _clean_player_state(), None, None


def case_end_combat_no_living_hostiles():
    # chk A3: end_combat called, combat NOT active, a LIVING hostile (monster, hp>0, not dead)
    # remains, and NO flee/surrender/retreat resolution declared. Keep moves < MIN_BEATS so the
    # A5/world floors stay inert; not under the combat-sprint env so it stays FATAL.
    state = _clean_player_state()
    state["combat"] = {"active": False}
    state["last_combat_resolution"] = ""  # nothing declared
    state["characters"]["g1"] = {"name": "Goblin", "kind": "monster",
                                 "current_hp": 7, "dead": False}
    events = _roll() + [_assistant_tool_use("t_ec", "mcp__engine__end_combat", {})]
    return events, state, None, None


def case_xp_not_orphaned():
    # chk: xp mode, combat NOT active, a LIVING party member exists, and a DEFEATED monster
    # (dead=True) still carries xp_value>0 -> the kill-time award was bypassed (XP silently lost).
    state = _clean_player_state()
    state["combat"] = {"active": False}
    state["characters"]["g1"] = {"name": "Goblin", "kind": "monster",
                                 "dead": True, "xp_value": 50, "current_hp": 0}
    return _roll(), state, None, None


def case_structural_completeness():
    # chk (relationship-cues): a SUBSTANTIAL session (>= 10 DM beats) with a companion present in
    # the final state that NEVER engaged a core system — approval frozen at 0 AND no camp/long_rest,
    # AND an active quest left unresolved across a multi-location arc with no quest-resolution call.
    # No chat/moves -> session_beats == dm_text, so we drive >= 10 with assistant TEXT turns. The
    # baseline state (day=2, 2 visited locations, xp=300) keeps the world/xp floors passing, so this
    # is the SOLE fatal fail. Models the proven 18-beat playtest where the DM narrated the companion+
    # quest story but never moved attitude / resolved the quest / made camp.
    state = _clean_player_state()
    state["characters"]["cmp1"] = {"name": "Brother Toll", "kind": "companion",
                                   "attitude_value": 0, "location_id": "loc_camp", "xp": 300}
    state["party"].append("cmp1")
    state["quests"] = {
        "q1": {"id": "q1", "title": "The Embergloom Pact", "status": "active",
               "objectives": ["free the prisoners"], "completed_objectives": []},
    }
    # 12 DM text beats (>= the STRUCTURAL_MIN_BEATS of 10) + a clean roll for dice_used. No camp_scene/
    # long_rest, no complete_quest/adjust_attitude anywhere -> every core system stays unengaged.
    events = _roll() + [_assistant_text(f"The scene unfolds, beat {i}.") for i in range(12)]
    return events, state, None, None


def case_structural_completeness_authored_warn():
    # GREEN fixture (#1036) — the Option A scope guard. SAME frozen-companion / active-quest /
    # 2-location / no-resolution profile as case_structural_completeness ABOVE, EXCEPT the run is
    # an AUTHORED campaign: it emits a `start_adventure` cold-open call (and a non-empty `scenes`
    # in state). Under the #1036 guard the unresolved-arc sub-check (b) is demoted FATAL->WARN for
    # an authored run whose only open quest is the hook-seeded arc (no add_quest), so the gate must
    # exit GREEN with structural_completeness appearing as a [WARN] line — NOT a RED cap. This is
    # the anti-score-gaming proof that an authored golden-spine run is no longer false-capped to 2.5.
    # NOTE: clause (a) approval-frozen is NOT tripped here (a camp/long_rest engaged the relationship
    # system) so (b) is the SOLE softened bit — isolating the #1036 change. Lives OUTSIDE the RED
    # manifest (corpus is RED-only); a dedicated GREEN test in test_behavioral_gate_corpus.py asserts
    # exit 0 + [WARN] structural_completeness.
    state = _clean_player_state()
    state["characters"]["cmp1"] = {"name": "Brother Toll", "kind": "companion",
                                   "attitude_value": 0, "location_id": "loc_camp", "xp": 300}
    state["party"].append("cmp1")
    # Authored snapshot marker: a non-empty scenes list (only authored adventures carry this).
    state["scenes"] = [{"id": "s1", "name": "The Embergloom Gate", "location_id": "loc_start"}]
    state["quests"] = {
        # The hook-seeded campaign arc — left active by design (multi-session). NO add_quest call,
        # so it is the only-hook-seeded case the #1036 guard softens to WARN.
        "q1": {"id": "q1", "title": "The Embergloom Pact", "status": "active",
               "objectives": ["free the prisoners"], "completed_objectives": []},
    }
    # start_adventure cold-open (authored signal in the tool stream) + a camp beat (engages the
    # relationship system so clause (a) approval-frozen stays clean) + 12 DM text beats.
    events = (
        _roll()
        + [_assistant_tool_use("t_adv", "mcp__engine__start_adventure",
                               {"adventure_id": "embergloom-pact"})]
        + [_assistant_tool_use("t_camp", "mcp__engine__camp_scene", {})]
        + [_user_tool_result("t_camp", json.dumps({"ok": True}))]
        + [_assistant_text(f"The scene unfolds, beat {i}.") for i in range(12)]
    )
    return events, state, None, None


def case_dm_beat_honesty_no_consecutive_collapse():
    # chk (#1285/#1287 chat-log twin of run_infra_valid): 3+ CONSECUTIVE dm chat rows stamped
    # beat_failed=True (qa/lib_beat_driver.sh worldos_chatlog_dm_failed) is a run-invalidating
    # infra collapse (quota window / host-session death mid-run), not scattered product defects —
    # the rri-a1-duo/duo2 defect class. Threshold matches _CONSECUTIVE_INVALID_THRESHOLD=3. A
    # leading clean dm/player exchange keeps both_sides_acted + dm_voices_characters satisfied so
    # this stays the SOLE fatal fail; the 3 failed rows carry no quoted dialogue.
    def _failed_beat_row() -> dict:
        row = _dm_chat_row("(beat failed — no reply)")
        row["beat_failed"] = True
        return row

    chat = [
        _player_chat_row("[say] hello"),
        _dm_chat_row('"Welcome," she says.'),
        _failed_beat_row(),
        _failed_beat_row(),
        _failed_beat_row(),
    ]
    return _roll(), _clean_player_state(), chat, None


def case_xp_awarded_on_progression():
    # chk A5: xp mode, session advanced (day>1 AND visited>=2 so the world floors pass), a
    # reward-worthy seam (a COMPLETED quest — NOT a dead monster, so xp_not_orphaned stays inert),
    # living party member(s) all at 0 XP -> progression/reward parity regression. Facade mv>=6.
    state = _clean_player_state()
    state["characters"]["pc1"]["xp"] = 0  # the only living PC earned nothing
    state["quests"] = {
        "q1": {"id": "q1", "title": "The Lost Relic", "status": "completed",
               "objectives": ["find it"], "completed_objectives": ["find it"]},
    }
    moves = [_move("say") for _ in range(6)]
    # 6 player beats + DM replies with quoted dialogue so both_sides_acted + dm_voices_characters
    # pass and xp_awarded_on_progression is the SOLE fail.
    chat = []
    for i in range(6):
        chat.append(_player_chat_row(f"[say] beat {i}"))
        chat.append(_dm_chat_row('"Well earned," she nods.'))
    return _roll(), state, chat, moves


# Map case_dir -> (builder_fn, expected_red_check[, multi_fatal_reason]). Order reads top-down
# by gate section. A 4th element flags a case that intentionally trips MORE than one fatal gate
# (only the dead/blank run) with the reason; everything else must isolate a single fatal check.
_CASES_SPEC: list[tuple] = [
    ("dm_produced_output", case_dm_produced_output, "dm_produced_output",
     "a truly dead/blank run necessarily also trips dice_used + player_in_party"),
    ("narration_no_ooc_leak", case_narration_no_ooc_leak, "narration_no_ooc_leak"),
    ("both_sides_acted", case_both_sides_acted, "both_sides_acted"),
    ("player_turns_structured", case_player_turns_structured, "player_turns_structured"),
    ("dm_voices_characters", case_dm_voices_characters, "dm_voices_characters"),
    ("player_used_facade", case_player_used_facade, "player_used_facade"),
    ("dm_resolved_player_moves", case_dm_resolved_player_moves, "dm_resolved_player_moves"),
    ("combat_resolved", case_combat_resolved, "combat_resolved"),
    ("dice_used", case_dice_used, "dice_used"),
    ("combat_not_left_active", case_combat_not_left_active, "combat_not_left_active"),
    ("party_traveled", case_party_traveled, "party_traveled"),
    ("world_advanced_time", case_world_advanced_time, "world_advanced_time"),
    ("player_in_party", case_player_in_party, "player_in_party"),
    ("no_duplicate_companion", case_no_duplicate_companion, "no_duplicate_companion"),
    ("no_rejected_tool_calls", case_no_rejected_tool_calls, "no_rejected_tool_calls"),
    ("end_combat_no_living_hostiles", case_end_combat_no_living_hostiles,
     "end_combat_no_living_hostiles"),
    ("xp_not_orphaned", case_xp_not_orphaned, "xp_not_orphaned"),
    ("xp_awarded_on_progression", case_xp_awarded_on_progression, "xp_awarded_on_progression"),
    ("structural_completeness", case_structural_completeness, "structural_completeness"),
    ("dm_beat_honesty_no_consecutive_collapse", case_dm_beat_honesty_no_consecutive_collapse,
     "dm_beat_honesty_no_consecutive_collapse"),
]

# GREEN cases — the inverse guard. These fixtures must NOT trip their named check (the gate must
# exit 0 and surface the check as a [WARN], not a [FAIL]). They lock a SCOPE-GUARD that intentionally
# demotes a former FATAL to WARN, so a future edit that RE-promotes it to FATAL (re-introducing the
# false-cap) is caught. Asserted by test_behavioral_gate_corpus.py::test_green_case_*. Tuple shape:
# (case_dir, builder_fn, warn_check). Kept SEPARATE from _CASES_SPEC (which is RED-only by contract).
_GREEN_CASES_SPEC: list[tuple] = [
    ("structural_completeness_authored_warn", case_structural_completeness_authored_warn,
     "structural_completeness"),
    ("no_rejected_tool_calls_recovered_warn", case_no_rejected_tool_calls_recovered_warn,
     "no_rejected_tool_calls"),
]


def _fatal_checks_in_gate() -> set[str]:
    """Parse the gate source for every FATAL chk(...) name (paren-balanced; mirrors the test)."""
    src = _GATE.read_text(encoding="utf-8")
    fatal: set[str] = set()
    for chunk in src.split("chk(")[1:]:
        m = re.match(r'\s*"([a-z0-9_]+)"', chunk)
        if not m:
            continue
        name = m.group(1)
        depth, buf = 1, []
        for ch in chunk:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            buf.append(ch)
        if "fatal=False" not in "".join(buf):
            fatal.add(name)
    return fatal


def build() -> dict:
    if _CASES.exists():
        shutil.rmtree(_CASES)
    _CASES.mkdir(parents=True, exist_ok=True)

    manifest_cases: list[dict] = []
    for spec in _CASES_SPEC:
        case_dir, fn, expected = spec[0], spec[1], spec[2]
        multi_fatal_reason = spec[3] if len(spec) > 3 else None
        run_events, state, chat, moves = fn()
        _write_case(case_dir, run_events, state, chat, moves)
        artifacts = ["run.jsonl", "state.json"]
        if chat is not None:
            artifacts.append("chat.jsonl")
        if moves is not None:
            artifacts.append("moves.jsonl")
        entry = {
            "case_dir": case_dir,
            "expected_red_check": expected,
            "artifacts": artifacts,
            "real_red_provenance": REAL_RED_PROVENANCE.get(expected, ""),
        }
        if multi_fatal_reason:
            entry["multi_fatal"] = True
            entry["multi_fatal_reason"] = multi_fatal_reason
        manifest_cases.append(entry)

    # Coverage audit: any FATAL gate check NOT covered by a case is added as a TODO entry with a
    # reason so the corpus test's coverage assertion surfaces it (rather than silently passing).
    covered = {c["expected_red_check"] for c in manifest_cases}
    for missing in sorted(_fatal_checks_in_gate() - covered):
        manifest_cases.append({
            "case_dir": f"TODO__{missing}",
            "expected_red_check": missing,
            "todo": True,
            "reason": TODO_REASONS.get(
                missing, "no faithful minimal fixture constructed yet (auto-flagged by builder)"),
        })

    # GREEN cases — known-GREEN bundles that lock a deliberate FATAL->WARN scope guard (the inverse
    # of the RED corpus). Written to disk like the RED cases but recorded under a SEPARATE manifest
    # key so the RED-only corpus test never mistakes them for known-REDs.
    green_cases: list[dict] = []
    for case_dir, fn, warn_check in _GREEN_CASES_SPEC:
        run_events, state, chat, moves = fn()
        _write_case(case_dir, run_events, state, chat, moves)
        artifacts = ["run.jsonl", "state.json"]
        if chat is not None:
            artifacts.append("chat.jsonl")
        if moves is not None:
            artifacts.append("moves.jsonl")
        green_cases.append({
            "case_dir": case_dir,
            "warn_check": warn_check,
            "artifacts": artifacts,
            "note": ("must exit GREEN (rc 0) with warn_check as a [WARN] — locks a deliberate "
                     "FATAL->WARN scope guard so a re-promotion to FATAL is caught (#1036)."),
        })

    manifest = {
        "_doc": ("Behavioral-gate regression corpus. `cases` are minimal known-RED bundles that "
                 "must trip their expected_red_check; `green_cases` are known-GREEN bundles that "
                 "must NOT (they lock a FATAL->WARN scope guard). Regenerate with "
                 "qa/gate_corpus/builder.py."),
        "cases": manifest_cases,
        "green_cases": green_cases,
    }
    _MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    m = build()
    n_real = sum(1 for c in m["cases"] if not c.get("todo"))
    n_todo = sum(1 for c in m["cases"] if c.get("todo"))
    n_green = len(m.get("green_cases", []))
    print(f"wrote {n_real} RED case(s) + {n_todo} TODO + {n_green} GREEN case(s) -> {_MANIFEST}")
    for c in m["cases"]:
        flag = " [TODO]" if c.get("todo") else ""
        print(f"  RED   {c['case_dir']:36s} -> {c['expected_red_check']}{flag}")
    for c in m.get("green_cases", []):
        print(f"  GREEN {c['case_dir']:36s} -> {c['warn_check']} [WARN]")
