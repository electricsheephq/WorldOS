#!/usr/bin/env python3
"""WS0 — the feature-engagement feedback loop: a MANIFEST of the authored story systems + a
coverage scorer that distinguishes a system that was NEVER ENGAGED (inert) from one that was
LEGITIMATELY OUT OF SCOPE (n/a) for the run.

The keystone gap this closes: today an entire authored subsystem (companion approval, camp
downtime, faction questlines, …) can be 100% inert across a whole sweep and every gate /
lens still scores it 10/10 — because no gate is engagement-coverage. A frozen-relationship
run that narrated the companion but never moved a gauge passed; a run that never camped
passed; a run that seeded factions and never joined one passed. This module makes those
"dead system" shapes a visible, queryable signal.

DESIGN (load-bearing):
  * PURE-READ over ENGINE-MUTATED snapshot state ONLY (attitude_value, last_long_rest_day,
    faction.joined/standing, narrative_arc.act, consequence.fired/trigger_day, the arc/agenda
    fired flags, campaign.decisions/quests/factions/*_arcs) or DM TOOL-COUNTS — NEVER fiction /
    prose (engine invariant #3). Reuses story_readout.structural_coverage_from_state /
    felt_shape_from_state so the shared buckets (recruit/camp/quest/acts) never drift.
  * A system is ENGAGED if its detector is true; N/A if its precondition is false (the run
    legitimately had no occasion to engage it — solo party, no factions seeded, too short);
    INERT only when the precondition is TRUE and the detector is FALSE (the system was OWED
    and never fired). INERT is the signal.
  * session_beats lives in the TRANSCRIPT, not the snapshot — the signature accepts it
    EXPLICITLY and every beats-keyed precondition DEFAULTS TO N/A when it is None (safe
    under-detect: an inject callsite with no beats count can never false-RED a system).
  * WORLDOS_GATE_COMBAT_SPRINT: under it, all FATAL systems are SKIPPED (mirrors
    qa/assert_behavioral.py) — a single pre-seeded fight legitimately exercises no story
    system. (All systems ship WARN this PR, so the skip is wired but currently a no-op for
    coverage; it is the safe graduation hook.)
  * ALL-WARN this PR (severity='warn' on every system). FATAL graduation is a FUTURE,
    post-sweep PR — so this is strictly additive and cannot flip any currently-green run RED.

Old/empty snapshots round-trip: every predicate null-guards a missing collection / a None
narrative_arc, so a legacy snapshot yields all-N/A (or inert where the precondition is
snapshot-only and true) and never raises.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional

# Reuse the shared structural buckets so this scorer and the story_readout stamp can never
# drift. Defensive import (run as a script / imported from varied cwds, like the sibling tests).
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from story_readout import (  # noqa: E402
        structural_coverage_from_state,
        felt_shape_from_state,
    )
except Exception:  # pragma: no cover - defensive; absence only degrades the shared buckets
    structural_coverage_from_state = None  # type: ignore[assignment]
    felt_shape_from_state = None  # type: ignore[assignment]


# ── shape helpers (tolerant of dict- OR list-shaped engine collections, None, garbage) ───────

def _as_list(coll) -> list:
    if isinstance(coll, dict):
        return list(coll.values())
    if isinstance(coll, list):
        return coll
    return []


def _characters(state: dict) -> list:
    return [c for c in _as_list((state or {}).get("characters")) if isinstance(c, dict)]


def _companions(state: dict) -> list:
    return [c for c in _characters(state) if c.get("kind") == "companion"]


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _narrative_arc(state: dict) -> dict:
    """The engine narrative_arc cursor, null-guarded for absent-AND-None (reuses the same
    degrade-to-empty-cursor guard felt_shape_from_state applies). Never raises."""
    arc = (state or {}).get("narrative_arc")
    return arc if isinstance(arc, dict) else {}


def _struct(ctx_state: dict, tool_counts) -> dict:
    """Shared structural-coverage buckets (recruit/camp/quest/acts/…). Reuses
    story_readout.structural_coverage_from_state when importable; falls back to an empty dict
    (every consumer below uses .get with a falsy default, so the absence only under-detects)."""
    if structural_coverage_from_state is None:
        return {}
    try:
        return structural_coverage_from_state(ctx_state or {}, tool_counts)
    except Exception:  # pragma: no cover - defensive
        return {}


# ── the parsed context every predicate reads (engine-state predicates + tool counts) ─────────

class Ctx(NamedTuple):
    state: dict
    tools: dict          # {short_tool_name: count}
    session_beats: Optional[int]
    struct: dict         # the shared structural_coverage block (may be {})
    felt: dict           # the shared felt_shape block (may be {})


def build_ctx(state: Optional[dict], tool_counts: Optional[dict],
              session_beats: Optional[int]) -> Ctx:
    """Parse the engine snapshot + DM tool counts into the Ctx every SystemSpec predicate
    reads. session_beats is passed THROUGH unchanged (None ⇒ beats-keyed systems N/A)."""
    state = state or {}
    tools = {str(k): _int(v) for k, v in (tool_counts or {}).items()}
    struct = _struct(state, tools or None)
    felt = {}
    if felt_shape_from_state is not None:
        try:
            felt = felt_shape_from_state(state, tools or None)
        except Exception:  # pragma: no cover - defensive
            felt = {}
    sb = session_beats if isinstance(session_beats, int) else None
    return Ctx(state=state, tools=tools, session_beats=sb, struct=struct, felt=felt)


# ── small ctx predicates (all engine-state / tool-count, never fiction) ───────────────────────

def _beats_at_least(ctx: Ctx, n: int) -> Optional[bool]:
    """True/False when beats are known; None (⇒ N/A) when session_beats is None. Every
    beats-keyed precondition routes through this so the None ⇒ N/A rule is uniform."""
    if ctx.session_beats is None:
        return None
    return ctx.session_beats >= n


def _companion_in_party(ctx: Ctx) -> bool:
    return bool(ctx.struct.get("recruited")) or bool(_companions(ctx.state))


def _final_day(ctx: Ctx) -> int:
    return _int(ctx.state.get("day"), 0)


def _tool(ctx: Ctx, name: str) -> int:
    return _int(ctx.tools.get(name), 0)


# ── the SystemSpec manifest ───────────────────────────────────────────────────────────────────

class SystemSpec(NamedTuple):
    id: str
    # precondition: did the run have any OCCASION to engage this system? True ⇒ owed; False ⇒
    # N/A; None ⇒ N/A (a beats-keyed precondition with no beats count — safe under-detect).
    precondition: Callable[[Ctx], Optional[bool]]
    # detector: did the engine state / tool counts prove the system actually fired?
    detector: Callable[[Ctx], bool]
    severity: str  # 'warn' | 'fatal' — ALL 'warn' this PR (graduation is a future PR)


# ── the 10 systems ────────────────────────────────────────────────────────────────────────────
# Each precondition reads ONLY engine-mutated snapshot fields / tool counts; each detector the
# same. Where a precondition is beats-keyed it routes through _beats_at_least (None ⇒ N/A).

# 1 companion_approval — a recruited companion + a substantial run ⇒ regard should have moved.
def _pc_companion_approval(ctx: Ctx) -> Optional[bool]:
    b = _beats_at_least(ctx, 10)
    if b is None:
        return None
    return b and _companion_in_party(ctx)


def _dt_companion_approval(ctx: Ctx) -> bool:
    if any(_int(c.get("attitude_value")) != 0 for c in _companions(ctx.state)):
        return True
    if any(_as_list(c.get("approval_log")) for c in _companions(ctx.state)):
        return True
    if bool(ctx.struct.get("approval_moved")):
        return True
    return _tool(ctx, "adjust_attitude") > 0


# 2 camp_downtime — a companion present + a multi-day arc ⇒ the party should have camped.
def _pc_camp_downtime(ctx: Ctx) -> Optional[bool]:
    b = _beats_at_least(ctx, 10)
    if b is None:
        return None
    return b and _companion_in_party(ctx) and _final_day(ctx) > 1


def _dt_camp_downtime(ctx: Ctx) -> bool:
    if any(_int(c.get("last_long_rest_day"), -1) >= 0 for c in _characters(ctx.state)):
        return True
    if bool(ctx.struct.get("camped")):
        return True
    return (_tool(ctx, "camp_scene") + _tool(ctx, "record_camp_beat")
            + _tool(ctx, "long_rest")) > 0


# 3 quests_objectives — a quest exists + a moderate run ⇒ at least one objective/quest resolved.
def _pc_quests_objectives(ctx: Ctx) -> Optional[bool]:
    b = _beats_at_least(ctx, 6)
    if b is None:
        return None
    return b and bool(_as_list(ctx.state.get("quests")))


def _dt_quests_objectives(ctx: Ctx) -> bool:
    quests = [q for q in _as_list(ctx.state.get("quests")) if isinstance(q, dict)]
    if any(q.get("status") == "completed" for q in quests):
        return True
    if any(_as_list(q.get("completed_objectives")) for q in quests):
        return True
    if bool(ctx.struct.get("quest_resolved")):
        return True
    return (_tool(ctx, "complete_quest") + _tool(ctx, "complete_objective")) > 0


# 4 acts_advance — a long run with an arc/act-tagged world ⇒ the arc should leave act 1.
def _pc_acts_advance(ctx: Ctx) -> Optional[bool]:
    b = _beats_at_least(ctx, 24)
    if b is None:
        return None
    if not b:
        return False
    has_arc = bool(_narrative_arc(ctx.state))
    tag_acts = _int(ctx.felt.get("acts_tag_reached"), 0)
    return has_arc or tag_acts >= 2


def _dt_acts_advance(ctx: Ctx) -> bool:
    if _int(_narrative_arc(ctx.state).get("act"), 0) > 1:
        return True
    return _int(ctx.felt.get("acts_tag_reached"), 0) >= 2


# 5 consequences_fired — a consequence owed strictly BEFORE the final day ⇒ it should have fired.
def _consequences(ctx: Ctx) -> list:
    return [c for c in _as_list(ctx.state.get("consequences")) if isinstance(c, dict)]


def _owed_consequences(ctx: Ctx) -> list:
    """Consequences whose trigger_day is STRICTLY < the final day. trigger_day == final_day is
    WARN-not-owed (it may legitimately fire on the very last beat we didn't see); future-dated
    is N/A (not yet due). Only a strictly-past-due consequence is OWED a fired flag."""
    final = _final_day(ctx)
    out = []
    for c in _consequences(ctx):
        td = c.get("trigger_day")
        if td is None:
            continue
        if _int(td, default=final) < final:
            out.append(c)
    return out


def _pc_consequences_fired(ctx: Ctx) -> Optional[bool]:
    # Snapshot-only precondition (no beats key): owed iff a consequence is strictly past due.
    return bool(_owed_consequences(ctx))


def _dt_consequences_fired(ctx: Ctx) -> bool:
    return all(bool(c.get("fired")) for c in _owed_consequences(ctx)) and bool(
        _owed_consequences(ctx))


# 6 factions_membership — a joinable faction seeded + a substantial run ⇒ should have joined.
def _factions(ctx: Ctx) -> list:
    return [f for f in _as_list(ctx.state.get("factions")) if isinstance(f, dict)]


def _pc_factions_membership(ctx: Ctx) -> Optional[bool]:
    b = _beats_at_least(ctx, 10)
    if b is None:
        return None
    joinable = any(str(f.get("questline_arc_id") or "") for f in _factions(ctx))
    return b and bool(_factions(ctx)) and joinable


def _dt_factions_membership(ctx: Ctx) -> bool:
    if any(bool(f.get("joined")) or _int(f.get("standing")) > 0 for f in _factions(ctx)):
        return True
    return _tool(ctx, "join_faction") > 0


# 7 faction_arc — a faction arc seeded AND a faction joined ⇒ the arc should have unlocked/moved.
# BLOCKED (stays WARN regardless): a snapshot-only precondition can't tell "seeded-but-locked"
# from "never-seeded" beyond the joined latch — a known open spike. Detector is generous.
def _faction_arcs(ctx: Ctx) -> list:
    return [a for a in _as_list(ctx.state.get("faction_arcs")) if isinstance(a, dict)]


def _pc_faction_arc(ctx: Ctx) -> Optional[bool]:
    arcs = _faction_arcs(ctx)
    joined = any(bool(f.get("joined")) for f in _factions(ctx))
    return bool(arcs) and joined


def _dt_faction_arc(ctx: Ctx) -> bool:
    for a in _faction_arcs(ctx):
        if str(a.get("status") or "locked") != "locked":
            return True
        for st in _as_list(a.get("stages")):
            if isinstance(st, dict) and st.get("status") in ("available", "active", "resolved"):
                return True
    return _tool(ctx, "advance_faction_arc") > 0


# 8 companion_quest_arc — a companion quest arc seeded + a substantial run ⇒ should have moved.
# BLOCKED (stays WARN regardless): same seeded-but-locked vs never-seeded ambiguity as #7.
def _companion_quest_arcs(ctx: Ctx) -> list:
    return [a for a in _as_list(ctx.state.get("companion_quest_arcs")) if isinstance(a, dict)]


def _pc_companion_quest_arc(ctx: Ctx) -> Optional[bool]:
    b = _beats_at_least(ctx, 10)
    if b is None:
        return None
    return b and bool(_companion_quest_arcs(ctx))


def _dt_companion_quest_arc(ctx: Ctx) -> bool:
    for a in _companion_quest_arcs(ctx):
        if str(a.get("status") or "locked") != "locked":
            return True
        for st in _as_list(a.get("stages")):
            if isinstance(st, dict) and st.get("status") in ("available", "active", "resolved"):
                return True
    return _tool(ctx, "advance_companion_quest_arc") > 0


# 9 companion_agenda — a companion with an authored agenda + a substantial run ⇒ it should fire
#   (or at least be evaluated). The agenda lives at character.arc.agenda.
def _companions_with_agenda(ctx: Ctx) -> list:
    out = []
    for c in _companions(ctx.state):
        arc = c.get("arc")
        agenda = arc.get("agenda") if isinstance(arc, dict) else None
        if isinstance(agenda, dict):
            out.append(c)
    return out


def _pc_companion_agenda(ctx: Ctx) -> Optional[bool]:
    b = _beats_at_least(ctx, 10)
    if b is None:
        return None
    return b and bool(_companions_with_agenda(ctx))


def _dt_companion_agenda(ctx: Ctx) -> bool:
    for c in _companions_with_agenda(ctx):
        agenda = c["arc"].get("agenda")
        if isinstance(agenda, dict) and bool(agenda.get("fired")):
            return True
    return _tool(ctx, "check_companion_arc") > 0


# 10 decisions_recorded — any substantial run ⇒ the party made a callback-worthy choice.
def _pc_decisions_recorded(ctx: Ctx) -> Optional[bool]:
    return _beats_at_least(ctx, 10)


def _dt_decisions_recorded(ctx: Ctx) -> bool:
    if bool(_as_list(ctx.state.get("decisions"))):
        return True
    return _tool(ctx, "record_decision") > 0


SYSTEMS: tuple[SystemSpec, ...] = (
    SystemSpec("companion_approval", _pc_companion_approval, _dt_companion_approval, "warn"),
    SystemSpec("camp_downtime", _pc_camp_downtime, _dt_camp_downtime, "warn"),
    SystemSpec("quests_objectives", _pc_quests_objectives, _dt_quests_objectives, "warn"),
    SystemSpec("acts_advance", _pc_acts_advance, _dt_acts_advance, "warn"),
    SystemSpec("consequences_fired", _pc_consequences_fired, _dt_consequences_fired, "warn"),
    SystemSpec("factions_membership", _pc_factions_membership, _dt_factions_membership, "warn"),
    SystemSpec("faction_arc", _pc_faction_arc, _dt_faction_arc, "warn"),
    SystemSpec("companion_quest_arc", _pc_companion_quest_arc, _dt_companion_quest_arc, "warn"),
    SystemSpec("companion_agenda", _pc_companion_agenda, _dt_companion_agenda, "warn"),
    SystemSpec("decisions_recorded", _pc_decisions_recorded, _dt_decisions_recorded, "warn"),
)

# The REVIEWED manifest — the forcing meta-test asserts {s.id for s in SYSTEMS} == this set, so
# adding/removing a system is a deliberate, visible diff (mirrors test_tool_schema_budget.py).
REVIEWED_SYSTEM_IDS = frozenset(s.id for s in SYSTEMS)

# Systems that BLOCK on a known snapshot-only ambiguity (seeded-but-locked vs never-seeded) and
# must NEVER graduate past WARN until the spike is resolved. Documented so graduation can't miss
# them. (Today every system is WARN; these two are the durable carve-out.)
BLOCKED_SYSTEM_IDS = frozenset({"faction_arc", "companion_quest_arc"})


def _combat_sprint_active() -> bool:
    """Mirror qa/assert_behavioral.py: under WORLDOS_GATE_COMBAT_SPRINT a single pre-seeded
    fight legitimately exercises no story system, so FATAL systems are skipped."""
    return bool(os.environ.get("WORLDOS_GATE_COMBAT_SPRINT"))


def engagement_coverage(state: Optional[dict], tool_counts: Optional[dict] = None,
                        session_beats: Optional[int] = None) -> dict:
    """Classify every system in the manifest for one run.

    Returns::

        {
          "coverage": "engaged/expected",   # e.g. "2/4" — engaged over (engaged+inert)
          "engaged":  [id, ...],            # detector true (precondition irrelevant once engaged)
          "na":       [id, ...],            # precondition false / unknown ⇒ no occasion to engage
          "inert":    [{"id","why","severity"}, ...],  # OWED (precondition true) but never fired
        }

    ENGAGED if detector true; N/A if precondition false-or-None; INERT iff precondition is TRUE
    AND detector is FALSE. Under WORLDOS_GATE_COMBAT_SPRINT, FATAL systems are skipped entirely
    (treated as N/A) — wired now for safe graduation; a no-op while every system is WARN."""
    ctx = build_ctx(state, tool_counts, session_beats)
    sprint = _combat_sprint_active()

    engaged: list[str] = []
    na: list[str] = []
    inert: list[dict] = []

    for spec in SYSTEMS:
        if sprint and spec.severity == "fatal":
            # Combat-sprint: a FATAL system is skipped (counted as N/A, never inert). WARN
            # systems still report (they can't false-RED), matching assert_behavioral's split.
            na.append(spec.id)
            continue
        try:
            engaged_now = bool(spec.detector(ctx))
        except Exception:  # pragma: no cover - a detector must never crash the scorer
            engaged_now = False
        if engaged_now:
            engaged.append(spec.id)
            continue
        try:
            pre = spec.precondition(ctx)
        except Exception:  # pragma: no cover - a precondition must never crash the scorer
            pre = None
        if pre:  # precondition TRUE and detector FALSE ⇒ INERT (the signal)
            inert.append({
                "id": spec.id,
                "why": _inert_why(spec, ctx),
                "severity": spec.severity,
            })
        else:  # precondition False or None ⇒ N/A (no occasion / unknown beats)
            na.append(spec.id)

    expected = len(engaged) + len(inert)
    return {
        "coverage": f"{len(engaged)}/{expected}",
        "engaged": engaged,
        "na": na,
        "inert": inert,
    }


_WHY = {
    "companion_approval": "a recruited companion's regard never moved off 0 (no attitude_value, "
                          "approval_log, or adjust_attitude) across a substantial run",
    "camp_downtime": "no character ever long-rested and no camp beat fired in a multi-day run "
                     "with a companion present",
    "quests_objectives": "a quest was seeded but no objective/quest was ever completed",
    "acts_advance": "the arc never left act 1 in a long run with an act-tagged world / engine arc",
    "consequences_fired": "a consequence came due (trigger_day before the final day) but never fired",
    "factions_membership": "a joinable faction was seeded but the party never joined "
                           "(no joined latch, no standing, no join_faction)",
    "faction_arc": "a faction arc exists and a faction was joined but no stage ever unlocked/moved "
                   "[BLOCKED: snapshot can't tell seeded-but-locked from never-seeded — stays WARN]",
    "companion_quest_arc": "a companion quest arc was seeded but no stage ever unlocked/moved "
                           "[BLOCKED: snapshot can't tell seeded-but-locked from never-seeded — stays WARN]",
    "companion_agenda": "a companion carries an authored agenda but it never fired and the arc was "
                        "never evaluated (check_companion_arc)",
    "decisions_recorded": "a substantial run recorded no callback-worthy decision",
}


def _inert_why(spec: SystemSpec, ctx: Ctx) -> str:
    base = _WHY.get(spec.id, f"{spec.id} was owed but never engaged")
    beats = "" if ctx.session_beats is None else f" (session_beats={ctx.session_beats})"
    return base + beats


# Snake_case id guard reused by the forcing meta-test (kept here so the rule lives with the data).
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")


def _is_snake(s: str) -> bool:
    return bool(_SNAKE.match(s))


def main(argv: list[str]) -> int:
    """CLI: print the engagement-coverage block for a snapshot.json (no beats ⇒ beats-keyed
    systems N/A). For ad-hoc inspection; the sweep wires this via inject_structural_coverage."""
    import json
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    state = {}
    try:
        state = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"could not read snapshot {argv[0]!r}: {e}", file=sys.stderr)
        return 2
    sb = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else None
    block = engagement_coverage(state if isinstance(state, dict) else {}, None, sb)
    print(json.dumps(block, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
