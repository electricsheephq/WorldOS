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


def coverage_from_tool_counts(counts) -> dict:
    """Roll a {short_tool_name: count} mapping up into the COVERAGE buckets — the SAME
    bucket logic `analyze()` applies to the live transcript, factored out so a second
    consumer (the behavioral gate's structural_completeness assertion) can't drift from
    the readout's coverage stamp. `counts` is any mapping (a Counter, a dict) of short
    tool names to call counts. Returns {bucket: total_calls_in_bucket}."""
    return {k: sum(int(counts.get(t, 0) or 0) for t in tools) for k, tools in COVERAGE.items()}


# Match an "(Act 1)" / "(Act II)" / "Act 3 -" tag in a location NAME. Authored adventures
# tag their location names with the act they belong to ("The Emerald Grove (Act 1)"); a
# distinct-act count over the VISITED locations is the ground-truth "how far did the arc
# get" signal. Roman numerals I-III + arabic 1-3 are both accepted.
_ACT_TAG = re.compile(r'\bact\s*(?:(1|2|3)|(iii|ii|i))\b', re.I)
_ROMAN_ACT = {"i": 1, "ii": 2, "iii": 3}


def _act_of(name) -> int | None:
    """The act number (1/2/3) tagged in a location NAME, or None when untagged."""
    if not name:
        return None
    m = _ACT_TAG.search(str(name))
    if not m:
        return None
    if m.group(1):
        return int(m.group(1))
    return _ROMAN_ACT.get((m.group(2) or "").lower())


# Short tool NAMES that, when carrying a fired/agenda signal in their RESULT, prove a betrayal
# actually turned. The engine's only companion-arc evaluator is `check_companion_arc` (server.py)
# — it returns the companion_arc.evaluate() shape: a `results` list whose entries carry
# `agenda_fired: true` / a fired `agenda` dump / a `betrayal_warning`. The OLD code keyed betrayal
# on `trigger_companion_agenda` / `companion_betrayal` / `resolve_companion_agenda` — tool names
# that DO NOT EXIST in the engine — so a real betrayal could never stamp `betrayal ✓` and (worse)
# the count for a non-existent tool was always 0, so the bucket was dead. We key on the real tool.
_ARC_EVAL_TOOLS = ("check_companion_arc",)


def _tag_acts_reached(visited_locs: list) -> int:
    """The highest CONTIGUOUS act-tag visited from act 1 over a list of visited location dicts
    (the existing name-tag `acts_reached` logic, factored out so structural_coverage_from_state
    AND felt_shape_from_state derive it identically — no drift). Visiting {3} reads 0; {1,3}
    reads 1; {1,2,3} reads 3. Untagged: 1 if any location was visited, else 0."""
    tagged_acts = {a for l in visited_locs
                   if isinstance(l, dict) and (a := _act_of(l.get("name"))) is not None}
    if tagged_acts:
        reached = 0
        for n in (1, 2, 3):
            if n in tagged_acts:
                reached = n
            else:
                break  # a gap breaks the chain — never credit an act past the first missing one
        return reached
    return 1 if visited_locs else 0


def _agenda_fired_in_state(state: dict) -> bool:
    """GROUND-TRUTH betrayal signal from the snapshot: any companion whose sealed agenda has
    actually FIRED (``character.arc.agenda.fired == True``). A fired agenda is the durable record
    that the companion turned — companion_arc.evaluate() flips ``fired`` and the engine persists
    it (the snapshot is the sole writer). Defensive: tolerates a missing/None arc or agenda, and a
    list- OR dict-shaped ``characters`` collection. This is the snapshot analog of the transcript's
    `check_companion_arc` ``agenda_fired`` result signal — either proves the betrayal landed."""
    chars = (state or {}).get("characters", {}) or {}
    char_list = list(chars.values()) if isinstance(chars, dict) else (chars if isinstance(chars, list) else [])
    for c in char_list:
        if not isinstance(c, dict):
            continue
        arc = c.get("arc")
        agenda = arc.get("agenda") if isinstance(arc, dict) else None
        if isinstance(agenda, dict) and bool(agenda.get("fired")):
            return True
    return False


def _as_list(coll) -> list:
    """A dict- OR list-shaped engine collection (characters/quests/locations/...) → a list of
    its entries. Tolerant of None/other so a malformed snapshot never raises."""
    if isinstance(coll, dict):
        return list(coll.values())
    if isinstance(coll, list):
        return coll
    return []


def felt_shape_from_state(state: dict, tool_counts=None) -> dict:
    """The SETUP→REVERSAL→CLIMAX detector — "did a real 3-act arc actually TURN", not just
    "N act-tagged rooms walked". PURE-READ over ENGINE-MUTATED state ONLY (the engine
    ``narrative_arc`` cursor + landed flags, Decisions/Quests/Consequences with engine-stamped
    days) — never DM fiction prose. Additive: an old/empty snapshot with no ``narrative_arc``
    yields ``acts_engine_reached=0`` and falls back to the existing name-tag acts, with
    reversal/climax/felt_three_act all False.

    Returns an additive sub-block merged into structural_coverage_from_state:
      * ``acts_engine_reached``: the engine cursor act (0..3) — ``narrative_arc.act`` (0 absent/old).
      * ``acts_tag_reached``:    the EXISTING name-tag acts (kept, surfaced under a clearer name).
      * ``reversal``:  a real arc-turning event landed in the MIDDLE day-band.
      * ``climax``:    a real arc-resolving event landed in the LATE day-band.
      * ``felt_three_act``: ``max(engine, tag) >= 3 AND reversal AND climax`` — the pass criterion.
      * ``shape``: human-readable — ``"setup→reversal→climax"`` when felt, else a flat stamp.

    REVERSAL prefers the engine-stamped ``narrative_arc.midpoint_reversal_landed`` (banded on
    ``reversal_day``); else a turning event in ``[0.30*final_day, 0.70*final_day]``: a Decision
    with non-empty ``approval_tags`` (or, when any decision is in-band, a set campaign flag); a
    Quest whose ``last_progress_day`` is in-band with progress made; a fired Consequence in-band.
    CLIMAX prefers ``narrative_arc.climax_landed`` (banded on ``climax_day``); else a resolving
    event with day ``>= 0.70*final_day``: a completed Quest whose ``last_progress_day`` is late;
    a fired companion agenda; a late fired Consequence. ``final_day <= 2`` ⇒ no arc to bisect ⇒
    reversal/climax False (never crashes)."""
    state = state or {}
    arc = state.get("narrative_arc")
    if not isinstance(arc, dict):
        arc = {}  # None / absent / variant → degrade to the all-default cursor (act unreadable)

    acts_engine = 0
    try:
        acts_engine = int(arc.get("act") or 0)
    except (TypeError, ValueError):
        acts_engine = 0

    # tag-acts: the EXISTING name-tag path (fallback when the engine cursor is absent/0).
    locs = _as_list(state.get("locations", {}))
    visited_locs = [l for l in locs if isinstance(l, dict) and l.get("visited")]
    acts_tag = _tag_acts_reached(visited_locs)

    try:
        final_day = int(state.get("day") or 0)
    except (TypeError, ValueError):
        final_day = 0

    # Day-banding (only meaningful once there's an arc to bisect — final_day > 2).
    has_arc = final_day > 2
    mid_lo = 0.30 * final_day
    mid_hi = 0.70 * final_day
    late_lo = 0.70 * final_day

    def _day_of(obj, key="day", default=None):
        try:
            v = obj.get(key)
            return int(v) if v is not None else default
        except (AttributeError, TypeError, ValueError):
            return default

    decisions = _as_list(state.get("decisions", []))
    quests = _as_list(state.get("quests", []))
    consequences = _as_list(state.get("consequences", []))
    flags = state.get("flags") if isinstance(state.get("flags"), dict) else {}
    any_flag_set = any(bool(v) for v in flags.values())

    # ── REVERSAL (the midpoint turn) ──────────────────────────────────────────────
    reversal = False
    # (1) engine-stamped, preferred — banded on reversal_day.
    if bool(arc.get("midpoint_reversal_landed")):
        rday = _day_of(arc, "reversal_day", default=0) or 0
        # If the engine stamped a day, honor banding; if it stamped 0 (legacy) but the flag is
        # set, trust the engine flag (it only flips when the engine recorded the reversal).
        reversal = (not has_arc) is False and (rday <= 0 or (mid_lo <= rday <= mid_hi)) \
            if has_arc else True
        # A stamped reversal on a sub-3-day arc still counts (the engine is authoritative).
        if not has_arc:
            reversal = True
    # (2) fallback — a turning event in the MIDDLE band (only if we have an arc to bisect).
    if not reversal and has_arc:
        for d in decisions:
            if not isinstance(d, dict):
                continue
            dday = _day_of(d, "day")
            if dday is None or not (mid_lo <= dday <= mid_hi):
                continue
            # a values-choice the engine REGISTERED moved something
            if d.get("approval_tags") or any_flag_set:
                reversal = True
                break
        if not reversal:
            for q in quests:
                if not isinstance(q, dict):
                    continue
                lpd = _day_of(q, "last_progress_day")
                if lpd is None or not (mid_lo <= lpd <= mid_hi):
                    continue
                if q.get("objectives") and q.get("completed_objectives"):
                    reversal = True
                    break
        if not reversal:
            for cq in consequences:
                if not isinstance(cq, dict) or not cq.get("fired"):
                    continue
                tday = _day_of(cq, "trigger_day")
                if tday is not None and mid_lo <= tday <= mid_hi:
                    reversal = True
                    break

    # ── CLIMAX (the late resolve) ─────────────────────────────────────────────────
    climax = False
    if bool(arc.get("climax_landed")):
        cday = _day_of(arc, "climax_day", default=0) or 0
        if not has_arc:
            climax = True
        else:
            climax = cday <= 0 or (cday >= late_lo)
    if not climax and has_arc:
        for q in quests:
            if not isinstance(q, dict) or q.get("status") != "completed":
                continue
            lpd = _day_of(q, "last_progress_day")
            if lpd is not None and lpd >= late_lo:
                climax = True
                break
        if not climax and _agenda_fired_in_state(state):
            climax = True  # the betrayal landed — the strongest climax signal
        if not climax:
            for cq in consequences:
                if not isinstance(cq, dict) or not cq.get("fired"):
                    continue
                tday = _day_of(cq, "trigger_day")
                if tday is not None and tday >= late_lo:
                    climax = True
                    break

    acts = max(acts_engine, acts_tag)
    felt_three_act = (acts >= 3) and reversal and climax

    if felt_three_act:
        shape = "setup→reversal→climax"
    else:
        shape = (f"flat (acts {acts}/3, reversal {'✓' if reversal else '·'}, "
                 f"climax {'✓' if climax else '·'})")

    return {
        "acts_engine_reached": int(acts_engine),
        "acts_tag_reached": int(acts_tag),
        "reversal": bool(reversal),
        "climax": bool(climax),
        "felt_three_act": bool(felt_three_act),
        "shape": shape,
    }


def structural_coverage_from_state(state: dict, tool_counts=None) -> dict:
    """A per-run STRUCTURAL-COVERAGE block derived from GROUND TRUTH (the final engine
    snapshot), not the player's actions — the owner's "full circle" (pairs with the #961
    structural_completeness gate). The persona scorer reads only actions.ndjson (no DM tool
    calls); the structural outcomes live in the campaign snapshot + the DM transcript's tool
    counts, so this reads BOTH:

      * From the snapshot (``state``): recruited (a kind=companion in ``party``),
        approval_moved (any companion ``attitude_value`` != 0), camped (any character with a
        ``last_long_rest_day`` >= 0), quest_resolved (any quest ``status`` == completed),
        quest_evolved (a completed quest carrying ``evolves_to`` OR a scheduled
        ``consequences`` entry), traveled (>= 2 distinct ``visited`` locations),
        acts_reached (the highest CONTIGUOUS "(Act N)" tag visited from act 1 — coverage,
        not max: visiting only the act-3 site reads 0, not 3 — with a visited-count proxy
        when the world is untagged), and betrayal (a companion's sealed agenda actually
        FIRED — ``character.arc.agenda.fired`` is True in the snapshot).
      * From ``tool_counts`` (optional — a {short_tool_name: count} mapping, reusing
        ``coverage_from_tool_counts``): combat (a start_combat fired). When ``tool_counts``
        is None, combat rides the snapshot only (any kind=monster engaged). betrayal is
        snapshot-ground-truth and does not depend on ``tool_counts``.

    Additive + defensive: every field defaults to a safe falsy value, so a system-skipping
    run yields a LOW/false block and a complete run yields acts 3/3 + all ✓. Returns a dict
    with the booleans/ints above plus a one-line human ``summary``."""
    state = state or {}
    chars = state.get("characters", {}) or {}
    char_list = list(chars.values()) if isinstance(chars, dict) else (chars if isinstance(chars, list) else [])
    companions = [c for c in char_list if isinstance(c, dict) and c.get("kind") == "companion"]
    party = state.get("party", []) or []

    # recruited: a kind=companion is IN the party (engaged, not just present in the roster).
    recruited = any(
        isinstance(cid, str) and isinstance(chars.get(cid), dict)
        and chars[cid].get("kind") == "companion"
        for cid in party
    ) if isinstance(chars, dict) else False
    # Fall back to "a companion exists but there's no party list to consult" (a list-shaped
    # roster, or a snapshot that doesn't track membership via `party`) so a recruited companion
    # the party array omits still counts. A populated party that simply has no companion → False.
    if not recruited and companions and not party:
        recruited = True

    approval_moved = any(int((c.get("attitude_value") or 0)) != 0 for c in companions)

    # camped: any character finished a long rest (last_long_rest_day stamped >= 0). -1/absent
    # == never rested. This is the snapshot ground truth for camp/long_rest (the tool-count
    # camp bucket is the transcript proxy; either proves it).
    camped = any(
        isinstance(c, dict) and int((c.get("last_long_rest_day", -1)) or -1) >= 0
        for c in char_list
    )

    quests = state.get("quests", {}) or {}
    quest_list = list(quests.values()) if isinstance(quests, dict) else (quests if isinstance(quests, list) else [])
    completed = [q for q in quest_list if isinstance(q, dict) and q.get("status") == "completed"]
    quest_resolved = bool(completed)
    consequences = state.get("consequences", []) or []
    # quest_evolved: a completed quest carries an `evolves_to` seed (rule-of-three echo) OR the
    # engine scheduled a follow-on consequence — either proves a resolved thread evolved.
    quest_evolved = any(bool(q.get("evolves_to")) for q in completed) or bool(consequences)

    locs = state.get("locations", {}) or {}
    loc_list = list(locs.values()) if isinstance(locs, dict) else (locs if isinstance(locs, list) else [])
    visited_locs = [l for l in loc_list if isinstance(l, dict) and l.get("visited")]
    distinct_visited = len(visited_locs)
    traveled = distinct_visited >= 2

    # acts_reached: distinct act-tags over VISITED location names (authored adventures tag
    # "(Act 1)/(Act 2)/(Act 3)"). When NO visited location is tagged, fall back to a proxy:
    # >= ~6 visited locations OR a multi-day arc suggests the party pushed past Act 1, but
    # without authored tags we can only PROVE act 1 — so the proxy caps at 1 (honest: never
    # claim an act the world didn't tag). The proxy still distinguishes "moved at all".
    #
    # COVERAGE, not max-tag: an arc that visits ONLY the Act-3 site is not "in act 3/3" — the
    # party skipped acts 1-2 (a fast-travel/QA shortcut, or a malformed run), so the structural
    # claim "reached act 3" is a LIE. acts_reached is the highest CONTIGUOUS act actually
    # visited from 1: act 1 if 1 is tagged, act 2 only if 1 AND 2 are, act 3 only if 1, 2 AND 3
    # are. Visiting {3} reads 0 (act 1 itself never proven); {1,3} reads 1; {1,2,3} reads 3.
    acts_reached = _tag_acts_reached(visited_locs)

    # combat from the transcript tool counts (ground truth for "a system fired"), reusing the
    # SAME bucket logic the readout stamp + the #961 gate share. When no tool counts are given,
    # derive combat from the snapshot (a kind=monster was engaged).
    combat = False
    if tool_counts is not None:
        cov = coverage_from_tool_counts(tool_counts)
        combat = bool(cov.get("combat"))
    if not combat:
        combat = any(isinstance(c, dict) and c.get("kind") == "monster" for c in char_list)

    # betrayal: a companion's sealed agenda ACTUALLY FIRED. The ground truth is the snapshot
    # (`character.arc.agenda.fired == True`) — the durable record the engine writes when
    # companion_arc.evaluate() flips the agenda. The OLD code keyed this on tool NAMES that don't
    # exist in the engine (trigger_companion_agenda / companion_betrayal / resolve_companion_agenda),
    # so a real betrayal stamped `betrayal ·` AND a system-skipping run could never disprove it —
    # the bucket was dead. A bare `check_companion_arc` CALL is necessary-but-not-sufficient (the DM
    # calls it every beat to evaluate; it usually returns nothing fired), so a call count alone must
    # NOT stamp betrayal — only the fired agenda does. The snapshot is the sole, honest source.
    betrayal = _agenda_fired_in_state(state)

    block = {
        "acts_reached": int(acts_reached),
        "recruited": bool(recruited),
        "approval_moved": bool(approval_moved),
        "camped": bool(camped),
        "quest_resolved": bool(quest_resolved),
        "quest_evolved": bool(quest_evolved),
        "traveled": bool(traveled),
        "combat": bool(combat),
        "betrayal": bool(betrayal),
        "distinct_visited": int(distinct_visited),
    }
    # FELT-SHAPE sub-block (additive — new keys only; the existing keys above are untouched).
    # Did a real SETUP→REVERSAL→CLIMAX arc actually TURN, vs "N act-tagged rooms walked"?
    block.update(felt_shape_from_state(state, tool_counts))
    block["summary"] = structural_summary(block)
    return block


def structural_summary(block: dict) -> str:
    """A one-line human summary of a structural_coverage block, e.g.
    'acts 3/3 · recruit ✓ · camp ✓ · approval · · quest-resolved ✓ · evolved · · travel ✓ · combat ✓'."""
    def mark(b):
        return "✓" if b else "·"
    acts = int(block.get("acts_reached") or 0)
    base = (
        f"acts {acts}/3 · recruit {mark(block.get('recruited'))} · "
        f"camp {mark(block.get('camped'))} · approval {mark(block.get('approval_moved'))} · "
        f"quest-resolved {mark(block.get('quest_resolved'))} · evolved {mark(block.get('quest_evolved'))} · "
        f"travel {mark(block.get('traveled'))} · combat {mark(block.get('combat'))} · "
        f"betrayal {mark(block.get('betrayal'))}"
    )
    # Trailing FELT-SHAPE segment — distinguishes a felt arc (setup→reversal→climax) from a
    # walked one. Only appended when the felt-shape sub-block is present (additive).
    if "felt_three_act" in block:
        base += f" · shape {mark(block.get('felt_three_act'))}"
    return base
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


# Keys whose engine value is strictly boolean. On a beat with several tool results joined into
# one blob, the permissive value capture can grab adjacent narration/lore prose as a "value"; a
# boolean key whose captured value isn't true/false is such a mis-capture — drop it so the outcome
# line never renders garble like `success="The Book of Grace …`.
_BOOL_OUT_KEYS = {"success", "failed", "crit", "hit", "defeated", "dead"}


def _outcome(txt: str) -> str:
    m = _OUT_KEYS.findall(txt or "")
    bits = []
    for k, v in m[:12]:
        v = v.strip().strip('"').strip()
        if k in _BOOL_OUT_KEYS and v.lower() not in ("true", "false"):
            continue  # narration captured as a boolean value — skip the garble
        if not v:
            continue
        bits.append(f"{k}={v[:18]}")
    return " ".join(bits[:7])


# Tool calls whose INPUT can carry a companion approval move via `approval_tags`. In real play
# the DM most often moves regard by persisting the beat's DECISION (persist_beat / record_decision
# carrying approval_tags) — the engine returns `approval_results` and stamps attitude_value — NOT
# by a bare adjust_attitude. coverage_from_tool_counts only counts adjust_attitude /
# check_companion_arc / advance_companion_quest_arc, so the stamp read `approval ·` while the
# engine state showed attitude moved. These names let analyze() detect the persist_beat path too.
_APPROVAL_TAG_TOOLS = {"persist_beat", "record_decision"}
# A tool_RESULT that proves the engine MOVED regard: it returned approval_results, or an attitude/
# approval delta field. Field-shaped (a JSON key), never a bare prose mention of "approval".
_APPROVAL_RESULT = re.compile(
    r'"(approval_results|attitude_results)"\s*:\s*[\[{]'           # the engine's approval payload
    r'|"(attitude|approval)(_value|_delta|_change)?"\s*:\s*-?\d'  # a numeric attitude/approval field
    r'|"(attitude|approval)_delta"\s*:',
    re.I,
)
# A companion is ENGAGED when one of these fires. This covers the AUTHORED-adventure case: the
# companion is PRE-SEEDED by start_adventure, so recruit_companion is NEVER called, yet the session
# plays them (camp beats, arc checks, regard movement). The transcript-only stamp can't read the
# snapshot party (the snapshot-coverage path already marks a pre-seeded companion `recruited`), so it
# DERIVES engagement from these signals — otherwise a 54-beat run that camps with the companion and
# moves their regard +27 reads `recruit ·`, contradicting its own `camp ✓ approval ✓`. Bare
# long_rest is intentionally EXCLUDED (a solo rest must not imply a companion).
_COMPANION_ENGAGE_TOOLS = {
    "recruit_companion", "camp_scene", "record_camp_beat",
    "check_companion_arc", "advance_companion_quest_arc",
}
# The engine stamps `current_location_id` into look_around / scene_context / travel_to results, so
# the set of these over the whole transcript IS the set of locations the party actually OCCUPIED —
# including the START location (which travel_to inputs alone miss when the party never travels back
# to it) and excluding world-build noise. Field-shaped so a prose mention can't false-positive.
_CURRENT_LOC = re.compile(r'"current_location_id"\s*:\s*"([^"]+)"')


def analyze(path: str):
    """Return (render_lines, coverage_dict)."""
    render, beat = [], 0
    calls: dict[str, int] = {}
    evolves, approval_deltas, betrayal_flag = [], 0, False
    # Did approval MOVE via the persist_beat/record_decision path (approval_tags in an input, or
    # an approval_results/attitude-delta in a result)? coverage_from_tool_counts can't see this —
    # it only rolls up the adjust_attitude/check_companion_arc tool NAMES — so the stamp showed
    # `approval ·` on a run where the engine state had attitude_value moved. Detect it here.
    approval_signal = False
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
            if name == "travel_to":
                # Count where the party actually GOES. travel_to's destination is `destination_id`
                # (canonical) with aliases destination / to / location_id (see server.py travel_to).
                # The old code looked at name/location/adventure_id for start_adventure/add_location
                # — none of which is an OCCUPIED location (adventure_id is an id, add_location is
                # world-building, a world name isn't a place) — and never read travel_to's real
                # field, so a 4-location arc (gs-ember-deep: cinderhollow→hollowmere-mill→
                # ashen-barrow→crypt) stamped locs=1. Occupied locations come from travel
                # destinations here + current_location_id in results (below).
                loc = (payload.get("destination_id") or payload.get("destination")
                       or payload.get("to") or payload.get("location_id"))
                if loc:
                    locations.add(str(loc))
            if name == "complete_quest" and isinstance(payload, dict) and payload.get("evolves_to"):
                evolves.append(str(payload["evolves_to"])[:50])
            if name == "adjust_attitude" and isinstance(payload, dict):
                try:
                    approval_deltas += int(payload.get("delta") or 0)
                except Exception:
                    pass
            # A persist_beat / record_decision carrying non-empty approval_tags MOVES regard via
            # the decision path (the engine returns approval_results). This is the common real-play
            # path the tool-NAME counts miss.
            if name in _APPROVAL_TAG_TOOLS and isinstance(payload, dict):
                tags = payload.get("approval_tags")
                if isinstance(payload.get("decision"), dict):
                    tags = tags or payload["decision"].get("approval_tags")
                if tags not in (None, "", [], {}):
                    approval_signal = True
            if name in STORY_TOOLS:
                s = _tool_summary(name, payload)
                render.append(f"    ⚙ {name}({s})")
        elif kind == "tool_result":
            low = (payload or "").lower()
            # Every location the party OCCUPIED — the engine reports current_location_id in
            # look_around / scene_context / travel_to results. Captures the start location too.
            for _loc in _CURRENT_LOC.findall(payload or ""):
                locations.add(_loc)
            # Flag a betrayal/loyalty fork only on an engine SIGNAL (a JSON field / gauge), never a
            # prose mention: start_adventure's premise text names "betrayal" as a THEME, not a fired
            # gate, so a bare-word match false-positives. Require a field-shaped signal.
            if (re.search(r'"\w*betray\w*"\s*:\s*(true|\[|"[^"])', low)
                    or '"attitude_below"' in low
                    or re.search(r'"agenda[_a-z]*"\s*:\s*("?fir|true|\{)', low)):
                betrayal_flag = True
            # An engine RESULT proving regard moved (approval_results payload, or an attitude/
            # approval delta) — the persist_beat decision path's return shape.
            if _APPROVAL_RESULT.search(payload or ""):
                approval_signal = True
            o = _outcome(payload)
            if o and any(s in o for s in ("roll=", "success=", "hit=", "damage=", "defeated=",
                                          "attitude=", "approval=", "evolves_to=")):
                render.append(f"      → {o}")
    cov = coverage_from_tool_counts(calls)
    # Fold the persist_beat/record_decision approval-move signal into the approval_moved bucket so
    # the stamp reflects the SAME movement the engine state records. coverage_from_tool_counts is
    # left intact (the #961 behavioral assertion reuses it for the camp/quest buckets only).
    cov["approval_moved"] = bool(cov.get("approval_moved")) or approval_signal
    cov["beats"] = beat
    cov["quest_evolved"] = len(evolves)
    cov["approval_delta"] = approval_deltas
    cov["betrayal_foreshadowed"] = betrayal_flag
    cov["distinct_locations"] = len(locations)
    # companion_engaged: a companion is in play and the session played them — including the AUTHORED
    # case where they were pre-seeded (recruit_companion never called). approval_moved is conclusive
    # (you only move a companion's regard); the companion-specific tools are too. The stamp marks
    # `recruit` from this so a pre-seeded engaged companion isn't read as a structural gap — matching
    # the snapshot-coverage path, which already marks a party companion `recruited`.
    cov["companion_engaged"] = bool(cov.get("approval_moved")) or any(
        calls.get(t, 0) for t in _COMPANION_ENGAGE_TOOLS)
    cov["calls"] = calls
    return render, cov


def stamp(cov: dict) -> str:
    def mark(b):
        return "✓" if b else "·"
    return (
        f"COVERAGE | beats={cov['beats']} locs={cov['distinct_locations']} "
        f"| recruit {mark(cov.get('companion_engaged') or cov['recruit'])} | camp {mark(cov['camp'])} "
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
