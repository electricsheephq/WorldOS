#!/usr/bin/env python3
"""probe_verdict.py — the DETERMINISTIC (no-LLM) verdict for a Tier-1.5 mechanism probe.

Given a seeded cue ("does the DM act on obligation X?"), a short live-beat run produces:
  * a DM stream-json transcript (the concatenated `claude -p --output-format stream-json`
    events — the SAME $COMBINED file qa/run_duo.sh writes, parsed the SAME way qa/distill.py
    parses tool_use blocks); and
  * the engine snapshot BEFORE and AFTER the beats.

This module reads those and answers three yes/no facts + one verdict, with NO LLM lens:
  * cue_present_each_beat  — did the seeded cue still fire as next_action at the START of every
                             beat the probe drove? (a cue that vanished mid-run isn't a clean test)
  * dm_engagement_tools_called — a tally of the engagement tools the cue asked for (for the
                             quest_endgame cue: complete_quest / complete_objective / add_consequence)
  * quest_resolved_or_progressed — did the engine's quest state actually MOVE (a quest went
                             completed, or an objective got marked done) between before/after?
  * verdict — ACTED   : the DM called a real engagement tool AND the engine state moved;
              IGNORED : the cue fired every beat but the engine state never moved;
              CUE_ABSENT : the cue was not present at every beat (the probe didn't hold the
                           question steady — an inconclusive setup, not a DM verdict).

⚠ ITERATION SIGNAL ONLY — a seeded mid-arc probe skips the cold-open / seat-path / free-play
surfaces where our real bugs live (docs/qa/FAST_GATE.md). NEVER cite it as release evidence.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


# The engagement tools each seeded cue asks the DM to reach for. Keyed by obligation `kind`
# (the cue the fixture seeds). A tool the cue names, called by the DM, is "acting on the cue".
# quest_endgame_unresolved's detail text names exactly these three (complete_quest closes the
# thread; complete_objective clears a step; add_consequence carries a hand-off forward).
CUE_ENGAGEMENT_TOOLS: dict[str, tuple[str, ...]] = {
    "quest_endgame_unresolved": ("complete_quest", "complete_objective", "add_consequence"),
    "quest_resolvable": ("complete_quest", "complete_objective"),
    "companion_gauge_unauthored": ("author_companion_gauges",),
    "companion_approval_frozen": ("record_decision", "camp_scene"),
    "camp_overdue": ("long_rest", "camp_scene"),
    "act_climax_owed": ("mark_climax", "complete_quest"),
    "act_midpoint_owed": ("mark_reversal", "record_decision"),
}

# The engagement tools whose SUCCESSFUL call actually MOVES quest state (used to phrase the
# tally; the ground-truth "did state move" check reads the engine snapshot, not this).
_QUEST_MOVING_TOOLS = ("complete_quest", "complete_objective")


def iter_tool_uses(transcript_path: Path) -> Iterable[dict]:
    """Yield each assistant `tool_use` block from a stream-json transcript, tolerant of the
    same event-shape drift qa/distill.py handles (dispatch on "type"; a bad line is skipped).
    Each yielded dict is ``{"name": str, "input": dict}``."""
    text = transcript_path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                yield {"name": b.get("name", "?"), "input": b.get("input", {}) or {}}


def tool_tally(transcript_path: Path) -> Counter:
    """A Counter of every tool the DM called across the transcript (name → count)."""
    tally: Counter = Counter()
    for tu in iter_tool_uses(transcript_path):
        tally[tu["name"]] += 1
    return tally


def engagement_tally(transcript_path: Path, cue: str) -> Counter:
    """The subset of the tool tally that are ENGAGEMENT tools for ``cue`` (the tools the cue
    asked the DM to reach for). Empty when the DM called none of them."""
    wanted = set(CUE_ENGAGEMENT_TOOLS.get(cue, ()))
    full = tool_tally(transcript_path)
    return Counter({name: n for name, n in full.items() if name in wanted})


def _quest_progress_signature(state: dict) -> dict:
    """A compact, comparable view of quest resolution state from an engine snapshot: for each
    quest, its status + the count of completed objectives. The verdict's ground-truth movement
    check compares this BEFORE vs AFTER (engine state, never DM prose). Tolerant of both the
    raw snapshot shape (``quests`` as a dict of Quest dicts) and a projected list."""
    sig: dict = {}
    quests = state.get("quests")
    if isinstance(quests, dict):
        items = quests.values()
    elif isinstance(quests, list):
        items = quests
    else:
        # get_state() projects active quests under `active_quests` ({id,title}); when only that
        # is present we can still detect a status flip (a quest that LEFT active) but not an
        # objective delta — the caller passes the raw snapshot to get objective granularity.
        items = state.get("active_quests") or []
    for q in items:
        if not isinstance(q, dict):
            continue
        qid = q.get("id") or q.get("quest_id")
        if qid is None:
            continue
        completed = q.get("completed_objectives")
        sig[qid] = {
            "status": q.get("status", "active"),
            "completed_objectives": len(completed) if isinstance(completed, (list, tuple)) else 0,
        }
    return sig


def quest_moved(before: dict, after: dict) -> bool:
    """True iff engine quest state actually MOVED between the two snapshots: a quest changed
    status (e.g. active → completed) OR gained a completed objective. Pure state read — the
    ground-truth the verdict trusts over any tool-call or prose signal."""
    b = _quest_progress_signature(before)
    a = _quest_progress_signature(after)
    # A quest present after that resolved / progressed vs its before-state.
    for qid, a_sig in a.items():
        b_sig = b.get(qid)
        if b_sig is None:
            # A brand-new quest with progress already on it counts as movement too.
            if a_sig["status"] != "active" or a_sig["completed_objectives"] > 0:
                return True
            continue
        if a_sig["status"] != b_sig["status"]:
            return True
        if a_sig["completed_objectives"] > b_sig["completed_objectives"]:
            return True
    # A quest that DISAPPEARED from `active` (only visible when before had it active) is movement.
    for qid, b_sig in b.items():
        if qid not in a and b_sig["status"] == "active":
            return True
    return False


def compute_verdict(
    cue: str,
    cue_present_each_beat: bool,
    engagement: Counter,
    state_moved: bool,
) -> str:
    """Fold the three facts into one verdict string.

      CUE_ABSENT  — the cue didn't hold for every beat: the probe never asked its question
                    cleanly, so a DM verdict would be unfair (inconclusive, not a fail).
      ACTED       — the DM called a cue engagement tool AND the engine state moved.
      IGNORED     — the cue fired every beat but the engine never moved (narrated, not engined).

    A tool called but state UNMOVED (a failed/aborted complete_quest) is still IGNORED — the
    ground truth is engine movement, not the attempt.
    """
    if not cue_present_each_beat:
        return "CUE_ABSENT"
    if state_moved and sum(engagement.values()) > 0:
        return "ACTED"
    return "IGNORED"


def current_next_action_kind(state_dir: str, campaign_id: str) -> str:
    """Load the engine against ``state_dir`` and return the CURRENT beat's next_action.kind for
    ``campaign_id`` (empty string when there is no obligation). Reuses the engine's OWN
    _compute_beat_obligations / _next_action (never a re-implementation) so the probe's per-beat
    cue-present check reads exactly what the DM's persist_beat / scene_context would surface.
    READ-ONLY: only _require + the two pure derivations run — no mutation."""
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415

    c = server._require(campaign_id)
    obligations = server._compute_beat_obligations(c)
    na = server._next_action(obligations)
    return (na or {}).get("kind") or ""


def build_report(
    cue: str,
    per_beat_cue_present: list[bool],
    transcript_path: Path,
    state_before: dict,
    state_after: dict,
) -> dict:
    """The bounded, deterministic verdict report a probe run prints + logs. All fields are
    derived from the transcript + the two engine snapshots — no LLM, no lens."""
    cue_present_each_beat = bool(per_beat_cue_present) and all(per_beat_cue_present)
    engagement = engagement_tally(transcript_path, cue)
    state_moved = quest_moved(state_before, state_after)
    verdict = compute_verdict(cue, cue_present_each_beat, engagement, state_moved)
    return {
        "cue": cue,
        "cue_present_each_beat": cue_present_each_beat,
        "per_beat_cue_present": list(per_beat_cue_present),
        "dm_engagement_tools_called": dict(engagement),
        "quest_resolved_or_progressed": state_moved,
        "verdict": verdict,
    }


def _usage() -> int:
    print(
        "usage:\n"
        "  probe_verdict.py cue-check <state_dir> <campaign_id>\n"
        "      → prints the CURRENT next_action.kind (empty line if none)\n"
        "  probe_verdict.py report <cue> <transcript.jsonl> <state_before.json> <state_after.json> "
        "<per_beat_cue_present_csv>\n"
        "      → prints the deterministic verdict report as one JSON line",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return _usage()
    mode = argv[1]
    if mode == "cue-check":
        if len(argv) < 4:
            return _usage()
        print(current_next_action_kind(argv[2], argv[3]))
        return 0
    if mode == "report":
        if len(argv) < 7:
            return _usage()
        cue, tpath, before_path, after_path, csv = argv[2], argv[3], argv[4], argv[5], argv[6]
        # per_beat_cue_present is a CSV of 1/0 (one per beat the probe drove).
        per_beat = [tok.strip() == "1" for tok in csv.split(",") if tok.strip() != ""]
        before = json.loads(Path(before_path).read_text(encoding="utf-8"))
        after = json.loads(Path(after_path).read_text(encoding="utf-8"))
        report = build_report(cue, per_beat, Path(tpath), before, after)
        print(json.dumps(report))
        return 0
    return _usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
