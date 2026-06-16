#!/usr/bin/env python3
"""Render a WorldOS playtest transcript as a READABLE adventure + a structural-coverage stamp.

The QA harness emits score.json (numbers), never the played story — so a regression like
"camp stopped firing" or "the arc never leaves Act 1" is invisible until someone reads a
transcript by hand. This turns any transcript into (1) the adventure as a human reads it —
DM prose beats + the rolls/outcomes + the companion/combat/decision moments, in order — and
(2) a one-line STRUCTURAL-COVERAGE stamp: did the play actually recruit a companion, reach
camp, move approval, resolve+evolve a quest, fight, foreshadow a betrayal, traverse acts?

Complements (does not overlap) the regression/RRI infra: the coverage stamp is a new signal
that infra can consume, and the readable render is for a human (the owner) to judge craft.

Usage:
  qa/story_readout.py <transcript.jsonl | run-dir>   # render + stamp
  qa/story_readout.py <path> --coverage-only          # just the one-line stamp + JSON
  qa/story_readout.py <path> --out readout.md         # write the render to a file

Input: a claude -p stream-json transcript (qa/transcripts/*.jsonl, *.dm.*.jsonl) or a run dir
(picks the largest *.jsonl). Robust to the system/hook noise the harness prepends.
"""
from __future__ import annotations
import json, re, sys, glob, os

# Tool calls that are STORY (kept in the render); everything else (Read, ToolSearch, speak,
# scene_context, persist_beat logging, get_state, list_canon, ...) is harness noise, dropped.
STORY_TOOLS = {
    "start_adventure", "start_world", "recruit_companion", "load_canon_character",
    "social_check", "skill_check", "saving_throw", "ability_check",
    "start_combat", "attack", "make_attack", "cast_spell", "use_resource", "end_combat",
    "long_rest", "camp_scene", "record_camp_beat", "check_companion_arc",
    "adjust_attitude", "advance_companion_quest_arc",
    "record_decision", "add_quest", "start_quest", "complete_quest", "set_quest_status",
    "resolve_event", "add_consequence",
}
# Structural signals → which coverage bucket each LIVE tool call proves.
COVERAGE = {
    "authored_start": ["start_adventure"],
    "recruit":        ["recruit_companion"],
    "camp":           ["camp_scene", "record_camp_beat", "long_rest"],
    "approval_moved": ["adjust_attitude", "check_companion_arc", "advance_companion_quest_arc"],
    "quest_resolved": ["complete_quest", "set_quest_status"],
    "combat":         ["start_combat"],
    "decision":       ["record_decision"],
}
_OUT_KEYS = re.compile(
    r'"(roll|total|dc|success|failed|degree|crit|hit|damage|hp|remaining|defeated|dead|'
    r'attitude|approval|standing|status|evolves_to|xp|day)"\s*:\s*([^,}\]\n]+)')


def _events(path: str):
    """Yield (role, kind, name, text_or_input, raw) for the meaningful stream-json events."""
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        t = d.get("type")
        if t in ("system", "result"):
            continue
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        role = msg.get("role", "")
        content = msg.get("content")
        if isinstance(content, str):
            if content.strip():
                yield (role, "text", "", content, d)
            continue
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            ct = c.get("type")
            if ct == "text" and c.get("text", "").strip():
                yield (role, "text", "", c["text"], d)
            elif ct == "tool_use":
                yield (role, "tool_use", c.get("name", "").split("__")[-1], c.get("input", {}), d)
            elif ct == "tool_result":
                body = c.get("content")
                txt = body if isinstance(body, str) else " ".join(
                    x.get("text", "") for x in body if isinstance(x, dict) and x.get("type") == "text"
                ) if isinstance(body, list) else ""
                yield (role, "tool_result", "", txt, d)


def _tool_summary(name: str, inp) -> str:
    if not isinstance(inp, dict):
        return ""
    bits = []
    for k in ("adventure_id", "skill", "ability", "dc", "spell", "weapon", "target", "cause",
              "tags", "approval_tags", "decision", "evolves_to", "status", "amount", "name",
              "kind", "reason", "maneuver", "resource"):
        v = inp.get(k)
        if v not in (None, "", [], {}):
            v = json.dumps(v)[:48] if isinstance(v, (list, dict)) else str(v)[:56]
            bits.append(f"{k}={v}")
    return " ".join(bits[:4])


def _outcome(txt: str) -> str:
    m = _OUT_KEYS.findall(txt or "")
    return " ".join(f"{k}={v.strip()[:18]}" for k, v in m[:7])


def analyze(path: str):
    """Return (render_lines, coverage_dict)."""
    render, beat = [], 0
    calls: dict[str, int] = {}
    evolves, approval_deltas, betrayal_flag = [], 0, False
    locations, days = set(), set()
    for role, kind, name, payload, raw in _events(path):
        if kind == "text":
            if role == "assistant":
                beat += 1
                render.append(f"\n━━ DM beat {beat} ━━\n{payload.strip()[:900]}")
            elif role == "user":
                # the player's injected move ([say]/[do]/...) — short, tagged
                s = payload.strip()
                if s and len(s) < 600 and not s.startswith("{"):
                    render.append(f"  ▶ PLAYER: {s[:280]}")
        elif kind == "tool_use":
            calls[name] = calls.get(name, 0) + 1
            if name in ("start_adventure", "start_world", "add_location", "travel_to"):
                loc = payload.get("name") or payload.get("location") or payload.get("adventure_id")
                if loc:
                    locations.add(str(loc))
            if name == "complete_quest" and isinstance(payload, dict) and payload.get("evolves_to"):
                evolves.append(str(payload["evolves_to"])[:50])
            if name == "adjust_attitude" and isinstance(payload, dict):
                try:
                    approval_deltas += int(payload.get("delta") or 0)
                except Exception:
                    pass
            if name in STORY_TOOLS:
                s = _tool_summary(name, payload)
                render.append(f"    ⚙ {name}({s})")
        elif kind == "tool_result":
            low = (payload or "").lower()
            # Flag a betrayal/loyalty fork only on an engine SIGNAL (a JSON field / gauge), never a
            # prose mention: start_adventure's premise text names "betrayal" as a THEME, not a fired
            # gate, so a bare-word match false-positives. Require a field-shaped signal.
            if (re.search(r'"\w*betray\w*"\s*:\s*(true|\[|"[^"])', low)
                    or '"attitude_below"' in low
                    or re.search(r'"agenda[_a-z]*"\s*:\s*("?fir|true|\{)', low)):
                betrayal_flag = True
            o = _outcome(payload)
            if o and any(s in o for s in ("roll=", "success=", "hit=", "damage=", "defeated=",
                                          "attitude=", "approval=", "evolves_to=")):
                render.append(f"      → {o}")
    cov = {k: sum(calls.get(t, 0) for t in tools) for k, tools in COVERAGE.items()}
    cov["beats"] = beat
    cov["quest_evolved"] = len(evolves)
    cov["approval_delta"] = approval_deltas
    cov["betrayal_foreshadowed"] = betrayal_flag
    cov["distinct_locations"] = len(locations)
    cov["calls"] = calls
    return render, cov


def stamp(cov: dict) -> str:
    def mark(b):
        return "✓" if b else "·"
    return (
        f"COVERAGE | beats={cov['beats']} locs={cov['distinct_locations']} "
        f"| recruit {mark(cov['recruit'])} | camp {mark(cov['camp'])} "
        f"| approval-moved {mark(cov['approval_moved'] or cov['decision'])} "
        f"| combat {mark(cov['combat'])} "
        f"| quest-resolved {mark(cov['quest_resolved'])} evolved {mark(cov['quest_evolved'])} "
        f"| betrayal {mark(cov['betrayal_foreshadowed'])}"
    )


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    path = argv[0]
    coverage_only = "--coverage-only" in argv
    out = None
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    if os.path.isdir(path):
        cands = sorted(glob.glob(os.path.join(path, "**", "*.jsonl"), recursive=True),
                       key=lambda p: os.path.getsize(p), reverse=True)
        cands = [c for c in cands if os.path.getsize(c) > 2000] or cands
        if not cands:
            print(f"no .jsonl transcript under {path}", file=sys.stderr)
            return 2
        path = cands[0]
    render, cov = analyze(path)
    line = stamp(cov)
    if coverage_only:
        print(line)
        print(json.dumps({k: v for k, v in cov.items() if k != "calls"}))
        return 0
    body = f"# Story readout — {os.path.basename(path)}\n\n{line}\n" + "\n".join(render) + f"\n\n{line}\n"
    if out:
        open(out, "w", encoding="utf-8").write(body)
        print(f"wrote {out}\n{line}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
