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

    # 4) dice actually fired somewhere (a whole session with zero rolls is broken). social_check
    # AND skill_check roll a d20 too — count them, so a valid non-combat / social + exploration
    # session (e.g. an S7 cold-open + camp + quest-finding beat) isn't falsely flagged. Mirrors the
    # checks_n treatment above.
    dice = (tools.get("roll", 0) + tools.get("attack", 0) + tools.get("saving_throw", 0)
            + tools.get("social_check", 0) + tools.get("skill_check", 0))
    chk("dice_used", dice > 0,
        f"roll={tools.get('roll', 0)} attack={tools.get('attack', 0)} save={tools.get('saving_throw', 0)} "
        f"social={tools.get('social_check', 0)} skill_check={tools.get('skill_check', 0)}")

    # 5) if combat started, attacks/monsters actually happened
    if tools.get("start_combat", 0) > 0:
        chk("combat_resolved", tools.get("attack", 0) + tools.get("spawn_monster", 0) > 0,
            f"start_combat={tools['start_combat']} attack={tools.get('attack', 0)} spawn={tools.get('spawn_monster', 0)}")
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
    MIN_BEATS = 6
    if chat:
        session_beats = sum(1 for r in chat if r.get("role") == "player")
    elif has_facade:
        session_beats = len(mv)
    else:
        session_beats = dm_text
    if session_beats >= MIN_BEATS:
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
