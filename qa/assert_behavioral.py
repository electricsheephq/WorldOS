#!/usr/bin/env python3
"""Behavioral PASS/FAIL gate over a QA playtest — treat the harness like software.

The LLM scorers grade story + mechanics on prose; they can't be trusted to flip RED
on a *structurally broken* run (a dead run, a one-sided duo where the DM never
responded, no dice, combat with no attacks, a player that over-wrote the DM's role,
a missing PC, a duplicate companion). This script asserts those invariants over a
run's artifacts and exits non-zero (RED) if any FATAL check fails. It prints every
check so a red is diagnosable and a false-red is tunable.

Inputs (whatever exists):
  - <run>.jsonl       the DM agent's stream-json (tool calls + assistant text)
  - <run>.state.json  the final engine snapshot (ground truth)
  - <run>.chat.jsonl  the two-sided conversation log (duo runs only)

Usage: assert_behavioral.py <run.jsonl> <state.json> [<chat.jsonl>] [<moves.jsonl>]
Exit 0 = GREEN (warnings allowed), 1 = RED (a fatal gate failed), 2 = usage.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

# Reuse the readout's coverage bucket logic so the structural_completeness assertion and
# the story_readout COVERAGE stamp can't drift (Task D). story_readout.py sits beside this
# file in qa/; make the import robust to being run from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from story_readout import coverage_from_tool_counts
except Exception:  # pragma: no cover - defensive: never let an import break the gate
    coverage_from_tool_counts = None


def _load_jsonl(p: str) -> list[dict]:
    out: list[dict] = []
    if not p or not Path(p).exists():
        return out
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # tolerate a half-written trailing line
    return out


def _tally(events: list[dict]) -> tuple[Counter, int]:
    """Tool-call counts (by short name) + count of DM assistant text turns."""
    tools: Counter = Counter()
    dm_text_turns = 0
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for b in (ev.get("message", {}) or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                tools[(b.get("name") or "").split("__")[-1]] += 1
            elif b.get("type") == "text" and (b.get("text") or "").strip():
                dm_text_turns += 1
    return tools, dm_text_turns


def _dm_narration_texts(events: list[dict]) -> list[str]:
    """The DM's player-facing prose turns — each assistant top-level text block (what the player
    actually reads). The OOC-leak gate scans these. Mirrors _tally's text extraction."""
    out: list[str] = []
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for b in (ev.get("message", {}) or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip():
                out.append(b["text"])
    return out


# OOC craft-scaffolding leaking into PLAYER-FACING prose — the defect class the LLM story scorer
# reliably "blends away" (a 2026-06-17 adversarial audit found 5+ first-person authoring preambles
# in a 4.6-prose run, entirely un-gated, inflating the score to 4.8). The DM SKILL.md FICTION-ONLY
# rule is the source fix; this is the deterministic backstop the scorer can't be trusted for.
# EVERY pattern is OOC-ONLY phrasing that does NOT occur in in-character D&D fiction or dialogue —
# so a clean beat (even one full of combat prose, or a character saying "let me introduce you")
# scores 0 and can never false-RED. (Kept deliberately high-confidence over exhaustive.) An
# adversarial machine-checked sweep (2026-06-17) tightened three over-broad patterns: a bare
# `through the engine` matched literal machinery (Gond/artificer/Steel-Watch fiction) — now
# verb/noun-anchored to the game-engine sense; `as the pc` collided with any in-world "PC"
# initialism — now full-word only; and `your nat...` collided with fictional stammering / `spine
# hook` with a literal flensing tool — both dropped from the gate (the SKILL.md prose rule still
# bans the raw die-vocab + craft jargon at the source).
_NARRATION_LEAK = [
    r"\bas the player character\b",                             # "seat <X> as the player character"
    r"\b(?:advancement|leveling|progression|run it|route it|resolve it|process it"
    r"|the (?:move|action|roll|turn)) through the engine\b",    # the game-ENGINE sense, not machinery
    r"\bcontinuity check\b",                                    # first-person authoring self-correction
    r"\bhere'?s how round \w+ (?:actually )?went\b",            # OOC combat-replay framing (round-anchored)
    r"\blet me set the order of it\b",                          # OOC initiative / turn-ordering preamble
    r"\binciting incident\b",                                   # plot-craft jargon (never in player fiction)
]
_NARRATION_LEAK_RE = [re.compile(p, re.I) for p in _NARRATION_LEAK]


def _tool_events(events: list[dict]) -> list[tuple[str, dict, object, bool, str]]:
    """Ordered (short_name, input, result_obj_or_None, is_error, raw_text).

    Pairs each ``tool_use`` with its following ``tool_result`` by tool_use_id. Both blocks
    live under ``message.content`` — the ``tool_use`` on an ``assistant`` event, the
    ``tool_result`` on a later ``user`` event — so iterating ALL events in order preserves
    pairing. ``result_obj`` is the parsed JSON of the result text when it is a JSON value,
    else ``None``; ``raw_text`` is always the textual payload (for error-message matching).

    This reads the RESULT side of the stream that ``_tally`` (tool_use only) never sees — the
    rich ``attack`` payload, ``is_error`` rejections, and validation walls the gates need.
    """
    pending: dict[str, tuple[str, dict]] = {}  # tool_use_id -> (short, input)
    out: list[tuple[str, dict, object, bool, str]] = []
    for ev in events:
        msg = ev.get("message", {}) or {}
        for b in (msg.get("content") or []):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                short = (b.get("name") or "").split("__")[-1]
                pending[b.get("id")] = (short, b.get("input") or {})
            elif b.get("type") == "tool_result":
                short, inp = pending.pop(b.get("tool_use_id"), ("", {}))
                c = b.get("content")
                if isinstance(c, str):
                    text = c
                elif isinstance(c, list) and c and isinstance(c[0], dict):
                    text = c[0].get("text") or ""
                else:
                    text = json.dumps(c)
                obj: object = None
                if isinstance(text, str):
                    try:
                        obj = json.loads(text)
                    except Exception:
                        obj = None
                out.append((short, inp, obj, bool(b.get("is_error")), text if isinstance(text, str) else ""))
    return out


# A player turn that reads like DM narration or asserts an outcome (the dice's/DM's
# call). Heuristic only -> a WARNING, not a hard fail (It.1's constrained tool surface
# is the real, structural fix; this just surfaces drift until then).
_OVERWRITE = (
    "you see", "you notice", "you feel", "you hear", "the room", "the air",
    "rolls a", "unseen", "unnoticed", "doesn't notice", "does not notice",
    "succeeds", "without being seen",
)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: assert_behavioral.py <run.jsonl> <state.json> [<chat.jsonl>] [<moves.jsonl>]", file=sys.stderr)
        return 2
    events = _load_jsonl(sys.argv[1])
    chat = _load_jsonl(sys.argv[3]) if len(sys.argv) > 3 else []
    moves_path = sys.argv[4] if len(sys.argv) > 4 else ""
    mv = _load_jsonl(moves_path) if moves_path else []
    has_facade = bool(moves_path)  # a facade/duo run: the player acts through tools
    # Count the move-resolution kinds for the PLAYER and every COMPANION (party QA) — a
    # companion's [attack]/[cast]/[check] the DM ignores is a real defect too. Solo/duo runs
    # have only player moves, so this is unchanged there. (#54)
    move_kinds = Counter(
        (m.get("kind") or "").lower() for m in mv if m.get("role") in ("player", "companion")
    )
    # A "substantial" session threshold, shared by the player-agency, combat-integrity, and
    # world-progression floors below (so a short smoke/scene test isn't penalized). Defined
    # here (not at first use) because the has_facade block needs it too. (#agency)
    MIN_BEATS = 6
    try:
        sp = Path(sys.argv[2])
        state = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    except Exception:
        state = {}
    tools, dm_text = _tally(events)

    checks: list[tuple[str, bool, bool, str]] = []  # (name, ok, fatal, detail)

    def chk(name: str, ok: bool, detail: str = "", fatal: bool = True) -> None:
        checks.append((name, bool(ok), fatal, detail))

    # 1) the run produced real DM output (catches the dead/blank run)
    chk("dm_produced_output", dm_text > 0 or sum(tools.values()) > 0,
        f"dm_text_turns={dm_text} tool_calls={sum(tools.values())}")

    # 1b) OOC narration-leak floor (2026-06-17 craft audit): craft scaffolding / first-person
    # authoring preambles ("Now let me seat <X> as the player character", "Continuity check — let me
    # correct that", "Here's how round one actually went") / raw system vocab leaking into the
    # player-facing prose is a real felt-quality defect the LLM story scorer blends away (it scored
    # a 5-leak / 4.6-prose run 4.8). Proportionate: 0 leaks pass; 1-2 incidental leaks WARN; >=3 in
    # a substantial run is a pervasively-broken player surface -> RED (caps the lenses like any
    # structural break). High-confidence OOC-only patterns ⇒ a clean in-fiction beat is always 0.
    leak_hits = [t for t in _dm_narration_texts(events) if any(rx.search(t) for rx in _NARRATION_LEAK_RE)]
    n_leak = len(leak_hits)
    _leak_red = n_leak >= 3 and dm_text >= MIN_BEATS
    chk("narration_no_ooc_leak", n_leak == 0,
        f"{n_leak} player-facing beat(s) leaked OOC craft-scaffolding/bookkeeping/system-vocab"
        + (f" — e.g. {' '.join(leak_hits[0].split())[:90]!r}" if leak_hits else "")
        + (" [pervasive ⇒ RED]" if _leak_red else (" [WARN]" if n_leak else "")),
        fatal=_leak_red)

    # 2) two-sided duo runs: BOTH the player and the DM took turns (catches the
    #    "1 player turn, 0 DM turns" botch). Only when a chat log exists.
    if chat:
        pl = sum(1 for r in chat if r.get("role") == "player")
        dm = sum(1 for r in chat if r.get("role") == "dm")
        chk("both_sides_acted", pl > 0 and dm > 0, f"player_turns={pl} dm_turns={dm}")
        # 3) the player stayed in its lane. In a FACADE run the lane is STRUCTURAL: every
        # relayed player turn is a tagged move ("[say] …", "[do] …"). A RAW-text turn means
        # the player bypassed the facade (the 0-tools fallback) and could be over-writing the
        # world / asserting outcomes — a hard fail (H4), not a soft warning. Without the
        # facade (legacy single-/duo-agent), fall back to the over-write heuristic as a WARN.
        unprefixed = [
            (r.get("text", "") or "")
            for r in chat
            if r.get("role") == "player" and not (r.get("text", "") or "").lstrip().startswith("[")
        ]
        if has_facade:
            chk("player_turns_structured", not unprefixed,
                f"{len(unprefixed)} player turn(s) bypassed the facade (raw text, not a [tagged] move): {[t[:70] for t in unprefixed[:2]]}")
        else:
            bad = [t for t in unprefixed if len(t) > 700 or any(k in t.lower() for k in _OVERWRITE)]
            chk("player_in_lane", not bad,
                f"{len(bad)} turn(s) look like over-writing: {[t[:70] for t in bad[:2]]}", fatal=False)

        # 3.6) STRUCTURAL STORY-CRAFT FLOOR: the duo-h1 "log, not a scene" failure was a DM
        # that narrated atmospheric fragments with ZERO quoted dialogue across a whole run.
        # The rubric grades dialogue QUALITY (an LLM); this is the hard floor that flips RED
        # on its total ABSENCE — enforced in code, like the player facade. A companion is in
        # the party and is meant to speak every beat, so zero dialogue with one present is a
        # broken scene (FATAL). Without a companion a scene may legitimately be wordless (WARN).
        dm_texts = [r.get("text", "") or "" for r in chat if r.get("role") == "dm"]
        dlg = sum(1 for t in dm_texts if re.search(r'"[^"\n]{3,}"|“[^”\n]{3,}”', t))
        has_companion = any((c or {}).get("kind") == "companion"
                            for c in (state.get("characters", {}) or {}).values())
        if len(dm_texts) >= 3:
            chk("dm_voices_characters", dlg > 0,
                f"{dlg}/{len(dm_texts)} DM turns have quoted dialogue — zero ⇒ a log, not a scene"
                + ("" if has_companion else " (no companion in party ⇒ WARN not fatal)"),
                fatal=bool(has_companion))

        # SYN-01 (#757 leg 3): dead-beat honesty counters. The wrappers stamp dm rows with
        # fallback_recovered:true (#357 prose recovered from the engine log, not the DM's own
        # reply) and beat_failed:true (a wrapper-authored VISIBLE failure beat for a dead /
        # error-class DM turn — qa/lib_beat_driver.sh clawdnd_chatlog_dm_failed). COUNT + REPORT
        # both so a masked-dead run can never read as silently clean. The gate does NOT flip on
        # them — the discount/gate policy stays #757's call; this is the consumer that policy
        # was blocked on (the stamp was write-only: zero readers before this check).
        recovered_rows = sum(
            1 for r in chat if r.get("role") == "dm" and r.get("fallback_recovered") is True)
        failed_rows = sum(
            1 for r in chat if r.get("role") == "dm" and r.get("beat_failed") is True)
        chk("dm_beat_honesty", failed_rows == 0 and recovered_rows == 0,
            f"beats_failed={failed_rows} fallback_recovered={recovered_rows} — failed beats "
            f"surfaced as visible failure rows (dead/error-class DM turns); recovered rows used "
            f"the #357 engine-log fallback. Reported only; gate policy stays #757's call.",
            fatal=False)

    # 3.5) constrained-player (It.1 facade): the player must actually ACT through its
    # tools. An empty moves log means the facade was blocked/unused (e.g. a missing
    # --permission-mode), even though it may have produced complaint text.
    if has_facade:
        chk("player_used_facade", len(mv) > 0,
            f"{len(mv)} facade moves recorded (0 ⇒ the player's tools were blocked/unused)")
        # C2) the DM actually RESOLVED the player's mechanical moves. Counting player vs DM
        # turns separately (both_sides_acted) never checks the DM ENGAGED with what the
        # player declared — a [cast]/[attack]/[check]/[save] move must be backed by the
        # matching engine call somewhere, or the player was ignored while the DM narrated
        # its own story. Aggregate (not per-beat) to avoid brittle alignment + false reds:
        # the gate trips only if the DM resolved ZERO of a move-kind the player used ≥1 of.
        roll_n, attack_n, save_n = tools.get("roll", 0), tools.get("attack", 0), tools.get("saving_throw", 0)
        checks_n = roll_n + save_n + tools.get("social_check", 0) + tools.get("skill_check", 0)
        # A [cast] does NOT require cast_spell: the engine resolves attack-roll spells —
        # incl. ALL damage cantrips (Fire Bolt, Eldritch Blast) — via attack(), and save
        # spells via saving_throw; cantrips spend no slot, so a healthy DM never calls
        # cast_spell for them. Count ANY spell-resolution path so a legit cantrip-caster run
        # isn't false-RED'd — the gate still trips if the DM made ZERO mechanical resolution
        # while the player cast. (attack/check stay tightly correlated; the review confirmed
        # those don't false-RED.)
        cast_n = tools.get("cast_spell", 0) + attack_n + save_n + roll_n
        unresolved = []
        if move_kinds.get("cast", 0) and cast_n == 0:
            unresolved.append(f"{move_kinds['cast']} [cast] but DM made no cast_spell/attack/save/roll")
        if move_kinds.get("attack", 0) and attack_n == 0:
            unresolved.append(f"{move_kinds['attack']} [attack] but DM attack=0")
        if move_kinds.get("check", 0) and checks_n == 0:
            unresolved.append(f"{move_kinds['check']} [check] but DM roll/save/social=0")
        if move_kinds.get("save", 0) and (save_n + roll_n) == 0:
            unresolved.append(f"{move_kinds['save']} [save] but DM saving_throw/roll=0")
        chk("dm_resolved_player_moves", not unresolved,
            "; ".join(unresolved) or f"move_kinds={dict(move_kinds)}")
        # M6) a 1-move-and-quit run satisfies the checks above; flag a trivially short
        # session as a WARNING (not every short run is broken — so not fatal).
        chk("player_engaged", len(mv) >= 3,
            f"only {len(mv)} move(s) recorded — a trivially short session?", fatal=False)
        # PLAYER-AGENCY FLOOR (#agency). Two audits found the AI player has NEVER called
        # clarify across 387 moves — it "plays along" (declares an action every turn) and
        # never asks the DM a question or requests a check. Nothing scored it. A real player
        # at a table PROBES: asks the DM ("is he armed?", "do I recognize this sigil?") and
        # requests checks. A substantial session with ZERO of either is a passive plot-passenger,
        # not a played character. WARN (not fatal) — it's an agency smell, not a broken run.
        if len(mv) >= MIN_BEATS:
            probes = move_kinds.get("clarify", 0) + move_kinds.get("check", 0)
            chk("player_probed", probes > 0,
                f"player asked 0 questions + requested 0 checks across {len(mv)} moves — "
                f"played along, never probed (clarify/request_check).", fatal=False)

        # COMBAT-INTEGRITY INVARIANTS (#agency). The engine persists a `combat` block in
        # state but the gate ignored it. Assert its integrity ONLY when combat data is present
        # (state.get("combat") truthy) — a non-combat session has no block and these skip.
        combat = state.get("combat") or {}
        if combat:
            # FATAL: combat left active at end-of-run is a state-integrity failure — a clean run
            # ends_combat. Only for a substantial session (a short smoke test cut off mid-fight
            # is not a real defect).
            if len(mv) >= MIN_BEATS:
                chk("combat_not_left_active", not combat.get("active"),
                    f"combat.active={combat.get('active')!r} at end-of-run — combat left active "
                    f"(state-integrity fail: a finished session should end_combat)")
            # WARN: if a fight started, the action economy should have engaged at some point —
            # an action was consumed / an attack was made. The final snapshot does not reliably
            # expose mid-fight action use (it may have been reset on end_combat), so probe the
            # fields that MIGHT carry it and only WARN when we can affirmatively see none — never
            # false-fire when the data simply isn't in the snapshot.
            if tools.get("start_combat", 0) > 0:
                econ_fields = ("action_used", "action_attacks_made", "attacks_made", "actions_taken")
                present = [f for f in econ_fields if f in combat]
                if present:
                    engaged = any(combat.get(f) for f in present)
                    chk("action_economy_engaged", engaged,
                        f"start_combat fired but combat shows no action consumed "
                        f"({{{', '.join(f'{f}={combat.get(f)!r}' for f in present)}}}) — action economy never engaged?",
                        fatal=False)
                # else: snapshot doesn't carry action-economy data -> skip rather than false-WARN.

                # round1_turn_skipped (SOFT — WARN, not FATAL, #166). Detects the pattern:
                # start_combat → next_turn with NO resolving action (attack / cast_spell /
                # saving_throw / use_action) in between, meaning the first combatant's Round-1
                # turn was advanced without any action being taken — the most common DM drift.
                # Conservative design:
                #   - Only fires when we can affirmatively confirm the pattern from the ordered
                #     tool stream (both start_combat AND next_turn present, AND zero resolving
                #     calls between them). Never fires when the stream is absent or ambiguous.
                #   - False-positive guard: we flag the FIRST next_turn with no prior resolver;
                #     a DM who attacked then called next_turn is clean (resolvers > 0 in between).
                #   - Does NOT try to identify WHICH combatant was skipped (result payloads not
                #     in the stream); it only checks that something was resolved before the first
                #     turn advance. Best-effort; documented as such.
                _RESOLVERS = frozenset({"attack", "cast_spell", "saving_throw", "use_action"})
                ordered_calls: list[str] = []
                for ev in events:
                    if ev.get("type") != "assistant":
                        continue
                    for b in (ev.get("message", {}) or {}).get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            short = (b.get("name") or "").split("__")[-1]
                            ordered_calls.append(short)
                # Scan for the first start_combat → next_turn gap
                round1_skip = False
                in_combat = False
                resolver_since_start = False
                for call in ordered_calls:
                    if call == "start_combat":
                        in_combat = True
                        resolver_since_start = False
                    elif in_combat and call in _RESOLVERS:
                        resolver_since_start = True
                    elif in_combat and call == "next_turn":
                        if not resolver_since_start:
                            round1_skip = True
                        break  # only check the first next_turn after start_combat
                if in_combat:  # only warn when we actually entered combat in this run
                    chk("round1_turn_skipped", not round1_skip,
                        "start_combat fired and next_turn advanced immediately with no "
                        "attack/cast_spell/saving_throw/use_action in between — the first "
                        "combatant's Round-1 turn may have been skipped without resolving "
                        "any action (read turn_brief at each next_turn and resolve the full "
                        "action before advancing; Multiattack = all N attacks).",
                        fatal=False)

        # COMBAT-WITHOUT-INITIATIVE (F01-14, audit 2026-06-11; WARN, NULL-GUARDED). The
        # combat-integrity checks above all nest under `start_combat>0`, so a session that
        # ran a whole FIGHT via raw attack()/cast_spell() calls and NEVER called start_combat
        # was completely invisible to QA — the turn-order and action-economy gates were inert
        # the entire time (the engine now nudges with `combat_not_active`, but the run can
        # still ignore it). Flag it: repeated attacks with no start_combat anywhere is the
        # out-of-initiative loophole. Conservative threshold (>= 3 attacks) so a single
        # narrative strike or a trap doesn't false-fire; only for a substantial session.
        if (
            len(mv) >= MIN_BEATS
            and tools.get("start_combat", 0) == 0
            and tools.get("attack", 0) >= 3
        ):
            chk("combat_ran_outside_initiative",
                False,
                f"{tools.get('attack', 0)} attack call(s) but start_combat was never called — "
                "a fight ran entirely outside initiative, so turn-order/action-economy gates "
                "were inert and combat integrity is unchecked. Call start_combat at the top of "
                "a real encounter (the engine surfaces a `combat_not_active` nudge on each "
                "out-of-combat attack/cast).",
                fatal=False)

        # PARTY-LOCATION COHERENCE (#agency, WARN, NULL-GUARDED). Every party member with a
        # known location should be co-located with the current scene. Skip members whose
        # location_id is absent/empty (serialization sometimes omits it) — never false-fire on
        # a null. Only meaningful when current_location_id is set.
        cur_loc = state.get("current_location_id")
        if cur_loc:
            chars_by_id = state.get("characters", {}) or {}
            stray = []
            for pid in (state.get("party") or []):
                ch = chars_by_id.get(pid) or {}
                loc = ch.get("location_id")
                if loc and loc != cur_loc:  # null/empty -> skipped
                    stray.append(f"{ch.get('name', pid)}@{loc}")
            chk("party_location_coherence", not stray,
                f"party member(s) not at current_location_id={cur_loc!r}: {stray} — party split / "
                f"stale location (a coherent scene keeps the party together unless intentionally split)",
                fatal=False)

        # DURATION-EXPIRY (#agency, WARN, NULL-GUARDED). Combat-grained effects (durations
        # measured in rounds/minutes) must NOT outlive combat — once combat is over, a character
        # still carrying a rounds/minutes effect means an expiry tick was missed. Only checked
        # when combat is NOT active; skips cleanly if active_effects are absent on a character.
        if not combat.get("active"):
            lingering = []
            for cid, ch in (state.get("characters", {}) or {}).items():
                if not isinstance(ch, dict):
                    continue
                for eff in (ch.get("active_effects") or []):  # absent -> [] -> skipped
                    if not isinstance(eff, dict):
                        continue
                    unit = (eff.get("duration_unit") or eff.get("unit") or "").lower()
                    if unit in ("round", "rounds", "minute", "minutes"):
                        lingering.append(f"{ch.get('name', cid)}:{eff.get('name', '?')}({unit})")
            chk("duration_expiry", not lingering,
                f"combat over but combat-grained active_effect(s) linger: {lingering} — a "
                f"rounds/minutes effect outlived combat (missed expiry tick)", fatal=False)

    # 4) dice actually fired somewhere (a whole session with zero rolls is broken). social_check
    # AND skill_check roll a d20 too — count them, so a valid non-combat / social + exploration
    # session (e.g. an S7 cold-open + camp + quest-finding beat) isn't falsely flagged. Mirrors the
    # checks_n treatment above.
    dice = (tools.get("roll", 0) + tools.get("attack", 0) + tools.get("saving_throw", 0)
            + tools.get("social_check", 0) + tools.get("skill_check", 0))
    chk("dice_used", dice > 0,
        f"roll={tools.get('roll', 0)} attack={tools.get('attack', 0)} save={tools.get('saving_throw', 0)} "
        f"social={tools.get('social_check', 0)} skill_check={tools.get('skill_check', 0)}")

    # 5) if combat started, real combat mechanics fired — attack, cast_spell, or saving_throw.
    # Tightened from "attack + spawn_monster > 0": spawn_monster alone means monsters appeared
    # but no dice were rolled to resolve the fight (a caster-only session legitimately has
    # attack=0, resolving via cast_spell/saving_throw). The new gate requires at least ONE of
    # the resolution mechanics; spawn_monster alone no longer satisfies it. FATAL.
    if tools.get("start_combat", 0) > 0:
        combat_dice = (tools.get("attack", 0)
                       + tools.get("cast_spell", 0)
                       + tools.get("saving_throw", 0))
        chk("combat_resolved", combat_dice > 0,
            f"start_combat={tools['start_combat']} attack={tools.get('attack', 0)} "
            f"cast_spell={tools.get('cast_spell', 0)} saving_throw={tools.get('saving_throw', 0)} "
            f"(spawn_monster={tools.get('spawn_monster', 0)} alone does not satisfy this check)")
        # M6/M7) a combat that never ENDS, or never grants XP, is a smell — but a run cut
        # off "out of time" mid-fight legitimately may not end_combat/award_xp, so WARN.
        chk("combat_ended", tools.get("end_combat", 0) > 0,
            f"start_combat={tools['start_combat']} end_combat={tools.get('end_combat', 0)} — combat may be left hanging",
            fatal=False)
        # end_combat AUTO-awards the defeated monsters' XP in the default "xp" leveling mode,
        # so a clean fight needs NO separate award_xp call — count end_combat as the award path
        # (only a fight cut off before end_combat should WARN here).
        chk("xp_awarded", tools.get("award_xp", 0) + tools.get("end_combat", 0) > 0,
            f"combat ran but neither award_xp nor end_combat fired ({tools.get('award_xp', 0)}/{tools.get('end_combat', 0)}) — no XP for the fight?",
            fatal=False)

    # XP STATE-TRUTH CHECK: in "xp" leveling mode, a defeated monster that still
    # carries xp_value > 0 after combat ends means XP was silently lost — the
    # kill-time awarding should have zeroed it. FATAL: this was the exact wave2-b
    # failure (xp=0 on the party despite a real kill). The check is unconditional
    # (not scoped to "start_combat was called") so it fires on post-combat kills too.
    lm = state.get("leveling_mode", "xp")
    if lm == "xp":
        combat_active = (state.get("combat") or {}).get("active", False)
        chars_all = state.get("characters", {}) or {}
        party = state.get("party", []) or []
        # Only FATAL when a LIVING party member existed to receive the XP. After a TPK
        # or total party flee (no living PC), a dead monster keeping its xp_value is a
        # legitimate "awarded to no one" state — not silent progression loss — so the
        # kill-time helper correctly leaves it unzeroed. Don't punish that as a defect.
        party_alive = any(
            (chars_all.get(pid) or {}).get("dead") is not True
            for pid in party if pid in chars_all
        )
        if not combat_active and party_alive:
            orphaned = [
                ch for ch in chars_all.values()
                if ch.get("kind") == "monster" and ch.get("dead") is True
                and (ch.get("xp_value") or 0) > 0
            ]
            for ch in orphaned:
                chk(
                    "xp_not_orphaned",
                    False,
                    f"defeated monster '{ch.get('name', '?')}' kept xp_value={ch.get('xp_value')} — "
                    f"progression silently lost (kill-time award never fired or was bypassed)",
                    fatal=True,
                )

    # 6) a player character exists in the party (state integrity)
    chars = state.get("characters", {}) or {}
    party = state.get("party", []) or []
    players = [chars[i] for i in party if i in chars and chars[i].get("kind") == "player"]
    chk("player_in_party", len(players) > 0, f"party={len(party)} players={len(players)}")

    # caster_has_spellbook (WARN; graduate to FATAL after clean sweeps) — a player/companion
    # whose sheet says it casts (truthy `spellcasting`) but carries NO spells anywhere is the
    # ow-fix-011115 empty-spellbook regression (nothing to cast → mech/combat capped). Check
    # several spell fields so a caster with only cantrips isn't falsely flagged.
    def _has_spells(c: dict) -> bool:
        return bool(c.get("spells_known") or c.get("cantrips_known")
                    or c.get("prepared_spells") or c.get("cantrips"))
    empty_casters = [c.get("name", "?") for c in chars.values()
                     if isinstance(c, dict) and c.get("kind") in ("player", "companion")
                     and c.get("spellcasting") and not _has_spells(c)]
    chk("caster_has_spellbook", not empty_casters,
        f"caster(s) with truthy spellcasting but no spells: {empty_casters}", fatal=False)

    # quest_objectives_progress (WARN) — a quest marked status=completed with a non-empty
    # `objectives` list but an EMPTY `completed_objectives` means the complete_objective
    # write-site was bypassed (the DM narrated the goal done without recording it). Locks the
    # d2f65f1 objective path. Quests may be a dict (id->quest) or a list.
    quests = state.get("quests", {}) or {}
    quest_iter = list(quests.values()) if isinstance(quests, dict) else (quests if isinstance(quests, list) else [])
    stuck_quests = [(q.get("title") or q.get("id") or "?") for q in quest_iter
                    if isinstance(q, dict) and q.get("status") == "completed"
                    and (q.get("objectives") or []) and not (q.get("completed_objectives") or [])]
    chk("quest_objectives_progress", not stuck_quests,
        f"completed quest(s) with objectives but empty completed_objectives: {stuck_quests}", fatal=False)

    # 7) no duplicate-named companion (the engine guards this; assert it held)
    comp = [(c.get("name", "") or "").strip().lower() for c in chars.values() if c.get("kind") == "companion"]
    chk("no_duplicate_companion", len(comp) == len(set(comp)), f"companions={comp}")

    # 8) WORLD-PROGRESSION FLOOR (the keystone fix). The LLM scorers happily gave a frozen
    # one-scene run story=4.1 / mechanical=4.0 / GREEN — because NOTHING checked that the world
    # actually MOVED. Across 56 saved campaigns the day never left 1 and the party never left
    # the opening room, yet every run "passed" and we kept polishing prose inside a dead scene.
    # A living-world session that runs a full arc MUST advance time and travel; one that ends
    # frozen at the start is broken, however pretty the prose. Applied only to a SUBSTANTIAL
    # session (>= MIN_BEATS player beats) so a short smoke/scene test isn't penalized.
    if chat:
        session_beats = sum(1 for r in chat if r.get("role") == "player")
    elif has_facade:
        session_beats = len(mv)
    else:
        session_beats = dm_text
    # The combat-sprint lane (run_combat_sprint.sh) is a single pre-seeded FIGHT in one place — it
    # legitimately never advances days or travels, so it sets CLAWDND_GATE_COMBAT_SPRINT=1 to skip
    # the world-progression floor (which would else false-RED a 40+-beat fight on a 1-location run).
    # Story / duo runs (no env var) keep the floor — it's the honest anti-frozen-scene gate.
    if session_beats >= MIN_BEATS and not os.environ.get("CLAWDND_GATE_COMBAT_SPRINT"):
        day = state.get("day") or 1
        tod = (state.get("time_of_day") or "").strip().lower()
        # Campaigns start at day 1, "morning"; a full session still parked there never aged.
        chk("world_advanced_time", day > 1 or (tod not in ("", "morning")),
            f"day={day} time_of_day={tod or '?'} after {session_beats} beats — the clock never moved "
            f"(advance_time / travel_to(advance_time=True) / long_rest)")
        locs = state.get("locations", {}) or {}
        visited = sum(1 for l in locs.values() if isinstance(l, dict) and l.get("visited"))
        # IN-PLACE PROGRESSION EXCEPTION (#623 false-cap): a multi-beat arc that genuinely
        # RESOLVED in a single location (e.g. a 9-beat tavern negotiation) is a SUCCESS, not a
        # frozen stall — but the bare `visited >= 2` rule false-REDs it (RED-capping every lens
        # ≤ 2.5). Distinguish the two with signals already in scope: a COMPLETE single-scene
        # drama advanced the clock AND resolved its arc; a FROZEN opening did neither. The AND
        # keeps a frozen stall RED (day==1/morning ⇒ clock_advanced False, no completed quest ⇒
        # arc_resolved False — it fails ≥2 conjuncts). Deliberately NOT broadened to clock-only
        # or beats-only.
        clock_advanced = day > 1 or (tod not in ("", "morning"))
        # arc_resolved requires an ACTUAL completed quest in the snapshot — NOT the status-blind
        # quest_resolved tool-count (coverage_from_tool_counts counts set_quest_status(...,"active"
        # /"failed") too, which would let a FROZEN DM game this exception with one cheap call:
        # advance_time + set_quest_status(status="active") on a dead scene. Adversarial-verified.
        arc_resolved = any(
            isinstance(q, dict) and q.get("status") == "completed" for q in quest_iter)
        SINGLE_SCENE_MIN_BEATS = 8  # strictly above MIN_BEATS(6): a real arc, not a smoke test
        in_place_progression = (visited >= 1 and clock_advanced and arc_resolved
                                and session_beats >= SINGLE_SCENE_MIN_BEATS)
        chk("party_traveled", visited >= 2 or in_place_progression,
            f"visited {visited}/{len(locs)} location(s) after {session_beats} beats — the party never "
            f"left the opening scene (travel_to / add_location make_current=True); "
            f"in-place-progression exception NOT met "
            f"(clock_advanced={clock_advanced} arc_resolved={arc_resolved} "
            f"beats>={SINGLE_SCENE_MIN_BEATS}? {session_beats >= SINGLE_SCENE_MIN_BEATS})")
        # WARN (the metric is softer): did the world gain/engage faces, or just sit in the seed?
        npcs_met = sum(1 for c in chars.values()
                       if isinstance(c, dict) and c.get("kind") == "npc" and c.get("met"))
        chk("world_peopled", npcs_met >= 2,
            f"only {npcs_met} NPC(s) engaged (met) across {session_beats} beats — a living world "
            f"should introduce new faces, not just sit in the seeded roster", fatal=False)

    # STRUCTURAL-COMPLETENESS FLOOR (relationship-cues — the owner's "full circle"). The
    # scorers reward prose + dice but are BLIND to structural gaps: the proven failure was an
    # 18-beat run where the DM told the companion + quest story in prose yet NEVER engaged the
    # engine — a companion stayed at attitude 0 all run, a multi-location quest ended still
    # `active`, camp never happened — and every LLM lens called it "doing well". This makes a
    # system-skipping run score like the failure it is.
    #
    # CONTEXTUAL by design (a short combat-sprint or a companion-less session must NOT trip it):
    # gated on a SUBSTANTIAL session (>= STRUCTURAL_MIN_BEATS) AND a kind=companion present in
    # the FINAL state. Reads the engine snapshot (ground truth for end-state) + the readout's
    # coverage buckets (ground truth for whether a system was ever engaged), so it agrees with
    # the story_readout COVERAGE stamp by construction.
    STRUCTURAL_MIN_BEATS = 10
    companions = [c for c in chars.values()
                  if isinstance(c, dict) and c.get("kind") == "companion"]
    if (session_beats >= STRUCTURAL_MIN_BEATS and companions
            and not os.environ.get("CLAWDND_GATE_COMBAT_SPRINT")):
        # Coverage buckets from the SAME tool counts the readout stamp uses (no drift). Fall
        # back to a direct count if the shared helper failed to import (defensive — never skips
        # the gate, just recomputes the camp/quest buckets inline).
        if coverage_from_tool_counts is not None:
            cov = coverage_from_tool_counts(tools)
            camp_engaged = bool(cov.get("camp"))
            quest_resolution_engaged = bool(cov.get("quest_resolved"))
        else:
            camp_engaged = bool(tools.get("camp_scene") or tools.get("record_camp_beat")
                                or tools.get("long_rest"))
            quest_resolution_engaged = bool(tools.get("complete_quest")
                                            or tools.get("set_quest_status"))

        # (a) APPROVAL FROZEN + NO CAMP: not one companion's regard moved off 0 all run AND
        # no camp/long_rest ever happened — the relationship system was narrated, never engaged.
        any_approval_moved = any(
            int((c.get("attitude_value") or 0)) != 0 for c in companions
        )
        approval_frozen_run = (not any_approval_moved) and (not camp_engaged)

        # (b) UNRESOLVED ARC: an active quest reached session end still open in a MULTI-LOCATION
        # arc (>= 2 visited locations ⇒ the party traversed an arc, so a quest left hanging is a
        # dropped thread, not a legitimately-mid-quest single scene). Gated on never having
        # engaged a quest-resolution tool, so a run that DID resolve quests (and simply has
        # another still open) isn't flagged.
        active_quests = [q for q in quest_iter
                         if isinstance(q, dict) and q.get("status") == "active"]
        # Recompute visited locations locally (don't depend on the world-progression block's
        # local, even though session_beats>=10 ⇒ that block ran): >= 2 ⇒ the party traversed
        # an arc.
        _locs = state.get("locations", {}) or {}
        visited = sum(1 for l in _locs.values() if isinstance(l, dict) and l.get("visited"))
        multi_location_arc = visited >= 2
        unresolved_arc = bool(active_quests and multi_location_arc
                              and not quest_resolution_engaged)

        bad_bits = []
        if approval_frozen_run:
            bad_bits.append(
                f"approval frozen all run (no companion left attitude 0; companions="
                f"{[c.get('name','?') for c in companions]}) AND no camp/long_rest happened")
        if unresolved_arc:
            bad_bits.append(
                f"{len(active_quests)} quest(s) still active at session end across a "
                f"{visited}-location arc with no quest-resolution call "
                f"({[q.get('title') or q.get('id') or '?' for q in active_quests]})")
        chk("structural_completeness", not bad_bits,
            f"a {session_beats}-beat session with a companion never engaged a core system: "
            + "; ".join(bad_bits)
            + " — the engine relationship/quest tools (record_decision approval_tags / "
              "adjust_attitude / camp_scene / complete_quest evolves_to) were narrated, not used")

    # ── SECTION A: RESULT-SIDE + per-record state gates (audit-tests.md §A) ───────────────
    # These read artifacts the existing gates ignore: the tool_RESULT payloads (A1/A2/A8) and
    # per-record final state that's present-but-unchecked (A3 living monster, A5 PC XP, A6
    # non-party companions, A7 skill profs). Each is null/scope-guarded so it never false-REDs
    # a run that simply didn't exercise the path — the same discipline as the gates above.
    evs = _tool_events(events)
    chars_all = state.get("characters", {}) or {}
    party_ids = state.get("party", []) or []

    def _quest_reward_already_awarded(q: dict) -> bool:
        return any(bool(q.get(k)) for k in ("milestone_awarded", "awarded", "rewarded", "xp_awarded"))

    # A8 (FATAL) — any tool call REJECTED with a schema/validation error (extra_forbidden ⇒
    # version-skew or a wrong field). The DM's intent silently did not take effect; this is the
    # class of failure that has produced 2 RED-capped runs historically. Benign engine guards
    # the DM is EXPECTED to hit and recover from (travel-graph rejections etc.) are split off to
    # a WARN so healthy recovery never false-REDs.
    errors = [(n, text) for (n, inp, r, err, text) in evs if err]
    if errors:
        fatal_errs = [(n, t) for (n, t) in errors
                      if "extra_forbidden" in t or "validation error" in t.lower()]
        benign = [(n, t) for (n, t) in errors if (n, t) not in fatal_errs]
        chk("no_rejected_tool_calls", not fatal_errs,
            f"{len(fatal_errs)} tool call(s) rejected with a schema/validation error "
            f"(extra_forbidden ⇒ version skew or wrong field): {[n for n, _ in fatal_errs]}; "
            f"first: {fatal_errs[0][1][:160] if fatal_errs else ''}", fatal=True)
        if benign:
            chk("engine_guards_hit", False,
                f"{len(benign)} engine guard rejection(s) (recoverable, DM expected to retry): "
                f"{[n for n, _ in benign]}", fatal=False)

    # F14-13 (#812): SOFT errors. A few tools return a DICT-shaped {"error": ...} with
    # is_error=False (load_canon_character miss, start_character pickup miss, the bestiary
    # miss, the dead-PC path) — invisible to the is_error-only A8 gate above. CONVENTION
    # (documented here, the consumer the convention was blocked on): a tool that cannot do
    # what was asked returns a top-level string ``error`` key; the gate COUNTS those soft
    # errors so a masked-soft-failure run can't read as silently clean. REPORT-ONLY (WARN,
    # never RED) — these are recoverable, the DM is told and re-asks; the gate/discount policy
    # stays the maintainers' call (mirrors dm_beat_honesty). Additive: a clean run is at zero.
    soft_errors = [
        (n, r["error"]) for (n, inp, r, err, text) in evs
        if not err and isinstance(r, dict) and isinstance(r.get("error"), str)
    ]
    if soft_errors:
        chk("tool_soft_errors", False,
            f"{len(soft_errors)} soft-error return(s) (dict {{'error':…}}, is_error=False — "
            f"invisible to A8): {[n for n, _ in soft_errors]}; "
            f"first: {soft_errors[0][1][:140]}. Reported only; recoverable.", fatal=False)

    # A3 (FATAL; WARN under CLAWDND_GATE_COMBAT_SPRINT) — end_combat called but a hostile is
    # still alive (kind=monster, current_hp>0, dead=false) with NO flee/surrender/retreat event
    # logged. The clearest pure-state defect: it corrupts the save for the next session load and
    # was GREEN in the cited run (xp_not_orphaned only fires on dead==true).
    if tools.get("end_combat", 0) > 0 and not (state.get("combat") or {}).get("active"):
        alive_hostiles = [c.get("name", "?") for c in chars_all.values()
                          if isinstance(c, dict) and c.get("kind") == "monster" and not c.get("dead")
                          and (c.get("current_hp") or 0) > 0]
        state_events = state.get("events", []) or []
        # The DM-declared disposition (end_combat(resolution=...)) is the RELIABLE signal: the combat
        # chronicle is NOT in the snapshot, so the legacy events-scan below can't actually see a
        # flee/surrender. A non-empty resolution means the DM explained how the fight ended.
        declared = str(state.get("last_combat_resolution") or "").strip()
        resolved = bool(declared) or any(
            re.search(r"flee|retreat|surrender|captured|driven off|routed|escap",
                      json.dumps(e), re.I) for e in state_events)
        sprint = bool(os.environ.get("CLAWDND_GATE_COMBAT_SPRINT"))
        if alive_hostiles:
            chk("end_combat_no_living_hostiles",
                resolved or sprint,
                f"end_combat called but living hostile(s) remain: {alive_hostiles} "
                f"(no flee/surrender/retreat event logged) — continuity break for the next load",
                fatal=not sprint)

    # A5 (FATAL when reward-worthy) — xp-leveling-mode session that ADVANCED the world and
    # crossed a reward-worthy seam (combat completion, explicit award path, completed quest,
    # or defeated monster) but ended with every living party member at 0 XP. Plain travel in
    # a short setup/provider proof can be valid state discovery, so it is surfaced as a WARN
    # scope classification instead of a false RED. Real combat/quest progression still fails
    # if rewards are silently lost.
    if state.get("leveling_mode", "xp") == "xp" and session_beats >= MIN_BEATS:
        day = state.get("day") or 1
        locs = state.get("locations", {}) or {}
        visited = sum(1 for l in locs.values() if isinstance(l, dict) and l.get("visited"))
        advanced = day > 1 or visited >= 2  # same signal the world-progression floor uses
        living_pcs = [chars_all[i] for i in party_ids
                      if i in chars_all and chars_all[i].get("kind") in ("player", "companion")
                      and not chars_all[i].get("dead")]
        if advanced and living_pcs:
            reward_tools = (
                tools.get("award_xp", 0)
                + tools.get("end_combat", 0)
                + tools.get("complete_objective", 0)
                + tools.get("complete_quest", 0)
            )
            completed_quests = [
                q.get("title") or q.get("id") or "?"
                for q in quest_iter
                if isinstance(q, dict)
                and q.get("status") == "completed"
                and not _quest_reward_already_awarded(q)
            ]
            defeated_reward_monsters = [
                ch.get("name", "?")
                for ch in chars_all.values()
                if isinstance(ch, dict)
                and ch.get("kind") == "monster"
                and ch.get("dead") is True
                and (ch.get("xp_value") or 0) > 0
            ]
            reward_worthy = bool(reward_tools or completed_quests or defeated_reward_monsters)
            any_xp = any((p.get("xp") or 0) > 0 for p in living_pcs)
            if reward_worthy:
                chk("xp_awarded_on_progression", any_xp,
                    f"xp-mode session advanced (day={day}, visited={visited}) and crossed a "
                    f"reward-worthy seam (tools={reward_tools}, completed_quests={completed_quests}, "
                    f"defeated_reward_monsters={defeated_reward_monsters}) but all living party "
                    f"members are at 0 XP — progression/reward parity regression",
                    fatal=True)
            else:
                chk("xp_progression_scope", False,
                    f"xp-mode session advanced (day={day}, visited={visited}) but no combat, "
                    f"quest, explicit reward, or defeated-XP-monster seam appeared; classifying "
                    f"as setup/provider-proof scope rather than missing-XP release evidence",
                    fatal=False)

    # A6 (WARN) — widen the existing party_location_coherence net to companions NOT in
    # state.party[] (the Wyll/Karlach de-facto-companion bug the current loop never sees). Locks
    # the d2f65f1 _move_party_to co-location. Null-guarded; only meaningful with a current loc.
    cur = state.get("current_location_id")
    if cur:
        stray = []
        for cid, ch in chars_all.items():
            if not isinstance(ch, dict) or ch.get("kind") not in ("player", "companion"):
                continue
            loc = ch.get("location_id")
            if loc and loc != cur:
                tag = "" if cid in set(party_ids) else " (de-facto companion, NOT in party[])"
                stray.append(f"{ch.get('name', cid)}@{loc}{tag}")
        chk("companion_location_synced", not stray,
            f"party/companion not at current_location_id={cur!r}: {stray}", fatal=False)
        # #353 explicit travel-scoped alias of the same invariant: the relocate sweep
        # (_move_party_to / travel_to) must leave EVERY de-facto companion at the party's
        # current location once travel has occurred (day>1 OR ≥2 visited locations). Same
        # `stray` set; named so the audit's "companion absent after travel_to" assertion has
        # a first-class key (the duo smoke keeps asserting on companion_location_synced).
        locs_a6 = state.get("locations", {}) or {}
        traveled = (state.get("day") or 1) > 1 or sum(
            1 for l in locs_a6.values() if isinstance(l, dict) and l.get("visited")) >= 2
        if traveled:
            chk("companion_location_synced_on_travel", not stray,
                f"after travel, party/companion not at current_location_id={cur!r}: {stray}",
                fatal=False)

    # #353 (WARN) — companion XP-sync on award. The relocate sweep co-locates every
    # kind='companion' (incl. de-facto companions not in c.party); the XP-award paths must
    # keep that group in step. When a reward seam paid the PC up (the PC's XP > 0) but a
    # LIVING co-located companion is still stuck at 0, the companion was excluded from the
    # split — the asymmetry this fix closes. Scope-guarded so a companion that joined mid-run
    # (still legitimately at 0) or a pre-reward setup beat never false-REDs:
    #   • only fires in xp leveling mode, and
    #   • only flags companions at the party's CURRENT location (co-located = should-have-earned).
    if state.get("leveling_mode", "xp") == "xp":
        cur_xp = state.get("current_location_id")
        pc_xp_max = max(
            (ch.get("xp") or 0)
            for ch in chars_all.values()
            if isinstance(ch, dict) and ch.get("kind") == "player"
        ) if any(isinstance(ch, dict) and ch.get("kind") == "player" for ch in chars_all.values()) else 0
        lagging = []
        if pc_xp_max > 0 and cur_xp:
            for ch in chars_all.values():
                if not isinstance(ch, dict) or ch.get("kind") != "companion" or ch.get("dead"):
                    continue
                if ch.get("location_id") == cur_xp and (ch.get("xp") or 0) == 0:
                    lagging.append(ch.get("name") or "?")
        chk("companion_xp_synced_on_award", not lagging,
            f"PC earned XP (max={pc_xp_max}) but co-located companion(s) still at 0 XP "
            f"(excluded from the party split): {lagging}", fatal=False)

    # A7 (WARN) — a leveled caster/martial with a signature skill but a COMPLETELY EMPTY
    # skill_proficiencies list (the load_canon_character gap: it skips _apply_srd_class_defaults).
    # Empty-list is the real defect; a custom build that swaps the signature skill keeps SOME
    # profs, so the empty-list scope avoids false-positives on variants.
    SIG = {"wizard": "arcana", "cleric": "religion", "rogue": "stealth", "druid": "nature",
           "ranger": "survival", "bard": "performance", "paladin": "religion"}
    sig_missing = []
    for c in chars_all.values():
        if not isinstance(c, dict) or c.get("kind") not in ("player", "companion"):
            continue
        profs = {p.lower() for p in (c.get("skill_proficiencies") or [])}
        if profs:
            continue  # has SOME profs → a variant swap is legitimate; only empty is the bug
        for cl in (c.get("classes") or []):
            want = SIG.get((cl.get("name") or "").lower())
            if want and (cl.get("level") or 1) >= 1:
                sig_missing.append(f"{c.get('name')} ({cl.get('name')}) has NO skill "
                                   f"proficiencies (expected at least {want})")
                break
    chk("caster_has_signature_proficiency", not sig_missing, "; ".join(sig_missing), fatal=False)

    # A4 (WARN) — a Fighter that took >25% HP but ended with Second Wind unused. Tactical-
    # adherence smell (not a broken state). Final-snapshot read of class_resources only.
    sw_unused = []
    for c in chars_all.values():
        if not isinstance(c, dict) or c.get("kind") not in ("player", "companion"):
            continue
        cls = " ".join((cl.get("name") or "") for cl in (c.get("classes") or [])).lower()
        if "fighter" not in cls:
            continue
        sw = (c.get("class_resources") or {}).get("second_wind") or {}
        if not sw:
            continue  # no resource on sheet → skip
        mx = c.get("max_hp")
        took = bool(mx) and (mx - (c.get("current_hp") or 0)) / mx > 0.25
        if took and (sw.get("used", 0) or 0) == 0:
            sw_unused.append(f"{c.get('name')} took >25% HP (now {c.get('current_hp')}/{mx}) "
                             f"and ended with Second Wind unused")
    chk("fighter_second_wind_considered", not sw_unused, "; ".join(sw_unused), fatal=False)

    # SIGNATURE-FEATURE COVERAGE (csmed-1/2/4, WARN, scope-guarded). The combat-sprints left
    # War-Cleric Channel Divinity and Fighter Action Surge / Second Wind at used:0 EVERY run —
    # the Angry-DM lens repeatedly flagged these signature class features as an OMISSION (a due
    # capability never invoked). This is a pure COVERAGE signal: when a seeded party member HAS
    # one of these pools on its sheet, the session should exercise it at least once. "Exercised"
    # is read from BOTH the final snapshot (class_resources[<pool>].used > 0) AND the tool stream
    # (a use_resource(resource=<pool>) call) — so a spend that a later short_rest reset still
    # counts. Scope-guarded so it NEVER false-fires when the party lacks the feature: the check
    # is only emitted for pools that are actually present on a player/companion sheet. A4 above
    # is a TACTICAL-adherence smell (Second Wind unused *after taking HP*); THIS is the broader
    # coverage floor (the feature was never touched at all). WARN, never fatal — a short fight
    # may legitimately not need every cooldown. Additive: a party with none of these pools is
    # byte-identical (no key emitted). Source: mech-climb evidence agent (combat-sprint scorecards).
    _SIGNATURE_POOLS = ("channel_divinity", "action_surge", "second_wind")
    # Pools a use_resource call exercised this run (snapshot may have been reset by a rest).
    used_via_stream = {
        (inp.get("resource") or "").lower()
        for (n, inp, r, err, _) in evs
        if n == "use_resource" and not err
    }
    sig_unused: list[str] = []
    for c in chars_all.values():
        if not isinstance(c, dict) or c.get("kind") not in ("player", "companion"):
            continue
        pools = c.get("class_resources") or {}
        for pool_id in _SIGNATURE_POOLS:
            res = pools.get(pool_id)
            if not isinstance(res, dict):
                continue  # feature absent on this sheet → never flagged (additive)
            exercised = (res.get("used", 0) or 0) > 0 or pool_id in used_via_stream
            if not exercised:
                sig_unused.append(f"{c.get('name', '?')} never used {pool_id}")
    # Only emit the check when at least one seeded party member HAS a signature pool — a party
    # with none of these features produces no key at all (no false PASS, no false WARN).
    has_signature_pool = any(
        isinstance(c, dict) and c.get("kind") in ("player", "companion")
        and isinstance((c.get("class_resources") or {}).get(p), dict)
        for c in chars_all.values() for p in _SIGNATURE_POOLS
    )
    if has_signature_pool:
        chk("signature_feature_exercised", not sig_unused,
            "; ".join(sig_unused) + " — seeded signature feature(s) never invoked this session "
            "(channel_divinity / action_surge / second_wind); the combat seed should exercise them "
            "(csmed-1/2/4 coverage omission)" if sig_unused else "",
            fatal=False)

    # A2 (WARN) — a melee attack HIT a parry-capable monster (state parry>0) but the attack
    # RESULT recorded no parry (parry in {None,0,False}) AND no reaction call fired all fight.
    # The cleanest new result-field read: the attack payload's own `parry` field.
    parry_monsters = {c.get("name"): c for c in chars_all.values()
                      if isinstance(c, dict) and c.get("kind") == "monster" and (c.get("parry") or 0) > 0}
    if parry_monsters:
        reaction_calls = sum(
            1 for (n, inp, r, err, _) in evs
            if (n == "use_action" and (inp.get("kind") == "reaction"))
            or (n == "use_resource" and "parry" in json.dumps(inp).lower())
        )
        hit_on_parrier = [r for (n, inp, r, err, _) in evs
                          if n == "attack" and isinstance(r, dict) and r.get("hit")
                          and r.get("target") in parry_monsters
                          and r.get("parry") in (None, 0, False)]
        if hit_on_parrier and reaction_calls == 0:
            chk("parry_reaction_considered", False,
                f"{len(hit_on_parrier)} melee hit(s) landed on a parry-capable monster "
                f"({list(parry_monsters)}) but 0 reaction calls fired", fatal=False)

    # A1 (WARN) — a ranged shot in melee without disadvantage. Theater-of-mind has no positions,
    # so the structural proxy is a MUTUAL melee exchange: if `victim` also meleed `shooter`
    # (a clearly-melee attack: slashing/bludgeoning damage), they were adjacent, so the shooter's
    # ranged shot needed disadvantage. WARN — the proxy can mis-fire on a genuine 10-ft gap.
    attacks = [(i, inp, r) for i, (n, inp, r, err, _) in enumerate(evs)
               if n == "attack" and isinstance(r, dict)]

    def _melee_damage(inp_dict: dict, res: dict) -> bool:
        """A clearly-MELEE attack: its damage type is slashing/bludgeoning (a sword/club), not
        the piercing that a bow/crossbow/pistol also shares. Best-effort on the available
        fields (no weapon/ranged field exists in the stream)."""
        blob = (json.dumps(inp_dict) + json.dumps(res.get("damage") or {})).lower()
        return ("slashing" in blob or "bludgeoning" in blob) and "piercing" not in blob

    ranged_flags = []
    for ai, (idx_a, inp_a, ra) in enumerate(attacks):
        if ra.get("disadvantage"):
            continue  # already applied → clean
        shooter, victim = ra.get("attacker"), ra.get("target")
        if not shooter or not victim:
            continue
        # victim landed a clearly-melee blow back on shooter → mutual exchange ⇒ adjacency
        mutual_melee = any(
            rb.get("attacker") == victim and rb.get("target") == shooter and _melee_damage(inp_b, rb)
            for (idx_b, inp_b, rb) in attacks if idx_b != idx_a
        )
        # AND this same shooter ALSO meleed this same victim in the run (so the un-disadvantaged
        # shot is the ranged half of a both-meleed-and-ranged pattern — the cited Scimitar+Pistol)
        shooter_also_meleed = any(
            rc.get("attacker") == shooter and rc.get("target") == victim and _melee_damage(inp_c, rc)
            for (idx_c, inp_c, rc) in attacks if idx_c != idx_a
        )
        if mutual_melee and shooter_also_meleed and not _melee_damage(inp_a, ra):
            ranged_flags.append(f"{shooter}->{victim} ranged w/o disadvantage (mutual melee exchange ⇒ adjacent)")
    if attacks:
        chk("ranged_disadvantage_in_melee", not ranged_flags, "; ".join(ranged_flags), fatal=False)

    fails = [c for c in checks if c[2] and not c[1]]
    warns = [c for c in checks if not c[2] and not c[1]]
    print("=== behavioral assertions ===")
    for name, ok, fatal, detail in checks:
        mark = "PASS" if ok else ("FAIL" if fatal else "WARN")
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if fails:
        print(f"RED: {len(fails)} behavioral assertion(s) FAILED.", file=sys.stderr)
        return 1
    print(f"GREEN" + (f" ({len(warns)} warning(s))" if warns else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
