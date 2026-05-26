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
        chk("party_traveled", visited >= 2,
            f"visited {visited}/{len(locs)} location(s) after {session_beats} beats — the party never "
            f"left the opening scene (travel_to / add_location make_current=True)")
        # WARN (the metric is softer): did the world gain/engage faces, or just sit in the seed?
        npcs_met = sum(1 for c in chars.values()
                       if isinstance(c, dict) and c.get("kind") == "npc" and c.get("met"))
        chk("world_peopled", npcs_met >= 2,
            f"only {npcs_met} NPC(s) engaged (met) across {session_beats} beats — a living world "
            f"should introduce new faces, not just sit in the seeded roster", fatal=False)

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
