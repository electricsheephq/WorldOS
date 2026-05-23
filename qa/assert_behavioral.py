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
        # 3) the player stayed in its lane (no DM-style narration / outcome assertion)
        bad = [
            (r.get("text", "") or "")[:70]
            for r in chat
            if r.get("role") == "player"
            # structured facade moves ("[say] …", "[do] …") are in-lane by construction
            and not (r.get("text", "") or "").lstrip().startswith("[")
            and (len(r.get("text", "")) > 700
                 or any(k in (r.get("text", "") or "").lower() for k in _OVERWRITE))
        ]
        chk("player_in_lane", not bad, f"{len(bad)} turn(s) look like over-writing: {bad[:2]}", fatal=False)

    # 3.5) constrained-player (It.1 facade): the player must actually ACT through its
    # tools. An empty moves log means the facade was blocked/unused (e.g. a missing
    # --permission-mode), even though it may have produced complaint text.
    if len(sys.argv) > 4 and sys.argv[4]:
        mv = _load_jsonl(sys.argv[4])
        chk("player_used_facade", len(mv) > 0,
            f"{len(mv)} facade moves recorded (0 ⇒ the player's tools were blocked/unused)")

    # 4) dice actually fired somewhere (a whole session with zero rolls is broken)
    dice = tools.get("roll", 0) + tools.get("attack", 0) + tools.get("saving_throw", 0)
    chk("dice_used", dice > 0, f"roll={tools.get('roll', 0)} attack={tools.get('attack', 0)} save={tools.get('saving_throw', 0)}")

    # 5) if combat started, attacks/monsters actually happened
    if tools.get("start_combat", 0) > 0:
        chk("combat_resolved", tools.get("attack", 0) + tools.get("spawn_monster", 0) > 0,
            f"start_combat={tools['start_combat']} attack={tools.get('attack', 0)} spawn={tools.get('spawn_monster', 0)}")

    # 6) a player character exists in the party (state integrity)
    chars = state.get("characters", {}) or {}
    party = state.get("party", []) or []
    players = [chars[i] for i in party if i in chars and chars[i].get("kind") == "player"]
    chk("player_in_party", len(players) > 0, f"party={len(party)} players={len(players)}")

    # 7) no duplicate-named companion (the engine guards this; assert it held)
    comp = [(c.get("name", "") or "").strip().lower() for c in chars.values() if c.get("kind") == "companion"]
    chk("no_duplicate_companion", len(comp) == len(set(comp)), f"companions={comp}")

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
