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
  * verdict — ACTED   : the cue was present at the START, the DM called a real engagement tool,
                           AND the engine state moved (the cue may legitimately CLEAR on a later
                           beat — that's the success signal, not a failed cue);
              IGNORED : the cue was present at the START but the engine state never moved (or no
                           engagement tool was called) — narrated, never engined;
              CUE_ABSENT : the cue was NOT present at the START (the seeded fixture never held
                           the question) — an inconclusive setup, not a DM verdict;
              INCONCLUSIVE : one or more DM beats FAILED (timeout/401/429/empty reply) — the run
                           produced no reliable ACTED-vs-IGNORED signal either way. Checked
                           BEFORE cue-presence, since a failed beat pre-empts any other verdict.

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
    # #1405 authoring cues. authoring: the DM re-authors the spine-less quest (add_quest with
    # objectives/giver/location, or resolves it away with set_quest_status). capture (quest_no_echo):
    # the DM records the resolved quest's branch outcome via complete_quest(evolves_to=...) /
    # add_consequence / record_decision.
    "quest_authoring_incomplete": ("add_quest", "set_quest_status"),
    "quest_no_echo": ("complete_quest", "add_consequence", "record_decision"),
}


def _bare_tool_name(name: str) -> str:
    """Strip the MCP server prefix so an engine tool called as
    ``mcp__worldos-engine__complete_quest`` matches the bare ``complete_quest`` the cue names.
    Claude's stream-json reports the FULLY-QUALIFIED MCP tool name; the CUE_ENGAGEMENT_TOOLS
    table keys on the engine's own bare tool names, so the match must be prefix-insensitive
    (measured on the first real probe run — the bare-name matcher tallied nothing)."""
    if not isinstance(name, str):
        return "?"
    # mcp__<server>__<tool> → <tool>; anything else passes through unchanged.
    if name.startswith("mcp__") and "__" in name[len("mcp__"):]:
        return name.rsplit("__", 1)[-1]
    return name


def iter_tool_uses(transcript_path: Path) -> Iterable[dict]:
    """Yield each assistant `tool_use` block from a stream-json transcript, tolerant of the
    same event-shape drift qa/distill.py handles (dispatch on "type"; a bad line is skipped).
    Each yielded dict is ``{"name": str, "input": dict}`` — ``name`` is the BARE tool name (the
    ``mcp__<server>__`` prefix stripped) so it matches the cue's engagement-tool table."""
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
                yield {"name": _bare_tool_name(b.get("name", "?")), "input": b.get("input", {}) or {}}


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


def _raw_quests(state: dict):
    """The quests as an iterable of dicts from a RAW engine snapshot (dict-of-Quest or list).
    The #1405 movement checks need the giver/location/objectives/evolves_to fields the projected
    get_state list drops, so they always read the raw snapshot the probe passes."""
    quests = state.get("quests")
    if isinstance(quests, dict):
        return [q for q in quests.values() if isinstance(q, dict)]
    if isinstance(quests, list):
        return [q for q in quests if isinstance(q, dict)]
    return []


def _authoring_signature(state: dict) -> dict:
    """Per-quest authoring richness from a raw snapshot: objective count + whether a giver /
    location is set. The quest_authoring_incomplete movement check compares this before vs after."""
    sig: dict = {}
    for q in _raw_quests(state):
        qid = q.get("id") or q.get("quest_id")
        if qid is None:
            continue
        objectives = q.get("objectives")
        sig[qid] = {
            "n_objectives": len(objectives) if isinstance(objectives, (list, tuple)) else 0,
            "has_giver": bool(q.get("giver_id")),
            "has_location": bool(q.get("location_id")),
        }
    return sig


def quest_authoring_progressed(before: dict, after: dict) -> bool:
    """True iff the DM actually AUTHORED richness (#1405(a)): an existing quest gained objectives /
    a giver / a location, OR a brand-new quest arrived already carrying an objective spine (the
    realistic 'DM re-called add_quest with the fields' path). Ground truth over prose."""
    b = _authoring_signature(before)
    a = _authoring_signature(after)
    for qid, a_sig in a.items():
        b_sig = b.get(qid)
        if b_sig is None:
            if a_sig["n_objectives"] > 0:  # a new quest authored WITH a spine
                return True
            continue
        if a_sig["n_objectives"] > b_sig["n_objectives"]:
            return True
        if a_sig["has_giver"] and not b_sig["has_giver"]:
            return True
        if a_sig["has_location"] and not b_sig["has_location"]:
            return True
    return False


def _echo_signature(state: dict) -> tuple[dict, int]:
    """Per-quest evolves_to-set flag + the campaign's total consequence count, from a raw
    snapshot. The quest_no_echo capture check compares this before vs after."""
    q_echo: dict = {}
    for q in _raw_quests(state):
        qid = q.get("id") or q.get("quest_id")
        if qid is None:
            continue
        q_echo[qid] = bool((q.get("evolves_to") or "").strip())
    consequences = state.get("consequences")
    n = len(consequences) if isinstance(consequences, (list, tuple)) else 0
    return q_echo, n


def quest_echo_captured(before: dict, after: dict) -> bool:
    """True iff the DM captured a resolved quest's branch outcome (#1405(b) / quest_no_echo): a new
    consequence was recorded (add_consequence / a scheduled evolution) OR a quest gained a
    non-empty evolves_to. Ground truth over prose."""
    b_echo, b_n = _echo_signature(before)
    a_echo, a_n = _echo_signature(after)
    if a_n > b_n:
        return True
    for qid, has in a_echo.items():
        if has and not b_echo.get(qid, False):
            return True
    return False


def state_progressed(cue: str, before: dict, after: dict) -> bool:
    """The cue-appropriate 'did engine state MOVE' ground-truth signal. #1405 authoring/capture
    cues clear by AUTHORING (fields populated) or CAPTURE (a consequence recorded), neither of
    which is a quest status/objective flip — so they read their own signals. Every other cue keeps
    the quest_moved status/objective signal it always used (byte-identical for existing cues)."""
    if cue == "quest_authoring_incomplete":
        return quest_authoring_progressed(before, after)
    if cue == "quest_no_echo":
        return quest_echo_captured(before, after)
    return quest_moved(before, after)


def compute_verdict(
    cue: str,
    cue_present_at_start: bool,
    engagement: Counter,
    state_moved: bool,
    any_beat_failed: bool = False,
) -> str:
    """Fold the facts into one verdict string.

      INCONCLUSIVE — one or more DM beats FAILED (timeout/401/429/empty reply, per
                    qa/lib_beat_driver.sh:worldos_resolve_dm_reply's WORLDOS_DM_BEAT_FAILED
                    classification). Checked FIRST: a failed beat means the run never gave the
                    DM a fair chance to act, so neither ACTED nor IGNORED would be an honest
                    signal — surfacing it as IGNORED would be a false negative on the DM.
      CUE_ABSENT  — the cue was NOT present when the probe began (the seeded fixture never held
                    the question) — inconclusive setup, not a DM fail.
      ACTED       — the cue was present at the start, the DM called a cue engagement tool, AND the
                    engine state moved. (The cue CLEARING on a later beat is the SUCCESS signal —
                    the DM resolved the thread — NOT an "absent cue"; that was the false-negative
                    the first real run exposed when the DM acted on beat 1.)
      IGNORED     — the cue was present at the start but the engine never moved (or the DM never
                    called an engagement tool) — the cue was narrated, never engined.

    A tool called but state UNMOVED (a failed/aborted complete_quest) is still IGNORED — the
    ground truth is engine movement, not the attempt.
    """
    if any_beat_failed:
        return "INCONCLUSIVE"
    if not cue_present_at_start:
        return "CUE_ABSENT"
    if state_moved and sum(engagement.values()) > 0:
        return "ACTED"
    return "IGNORED"


def current_next_action_kind(state_dir: str, campaign_id: str) -> str:
    """Load the engine against ``state_dir`` and return the CURRENT beat's next_action.kind for
    ``campaign_id`` (empty string when there is no obligation). Reuses the engine's OWN
    _compute_beat_obligations / _next_action (never a re-implementation) so the probe's per-beat
    cue-present check reads exactly what the DM's persist_beat / scene_context would surface.
    READ-ONLY: only _require + the two pure derivations run — no mutation.

    qa/mechanism_probe.sh invokes this as a fresh `uv run` subprocess per beat, where mutating
    os.environ is harmless (the process exits right after). But the function is importable and
    this module sits on sys.path in qa/test_mechanism_probe.py, so any future IN-PROCESS caller
    must not see WORLDOS_STATE_DIR permanently clobbered by a "read-only" helper — restore the
    prior value (or absence) on the way out."""
    prior = os.environ.get("WORLDOS_STATE_DIR")
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
        import server  # noqa: PLC0415

        c = server._require(campaign_id)
        obligations = server._compute_beat_obligations(c)
        na = server._next_action(obligations)
        return (na or {}).get("kind") or ""
    finally:
        if prior is None:
            os.environ.pop("WORLDOS_STATE_DIR", None)
        else:
            os.environ["WORLDOS_STATE_DIR"] = prior


def build_report(
    cue: str,
    per_beat_cue_present: list[bool],
    transcript_path: Path,
    state_before: dict,
    state_after: dict,
    any_beat_failed: bool = False,
) -> dict:
    """The bounded, deterministic verdict report a probe run prints + logs. All fields are
    derived from the transcript + the two engine snapshots — no LLM, no lens.

    ``any_beat_failed`` — True iff the SHELL driver (qa/mechanism_probe.sh) classified one or
    more DM beats as FAILED via qa/lib_beat_driver.sh:worldos_resolve_dm_reply's
    WORLDOS_DM_BEAT_FAILED (timeout/401/429/empty reply) — that classification lives in the
    shell driver (it owns the live `claude -p` attempt), so it is passed in rather than
    re-derived here from the transcript alone."""
    per_beat = list(per_beat_cue_present)
    cue_present_at_start = bool(per_beat) and bool(per_beat[0])
    cue_present_each_beat = bool(per_beat) and all(per_beat)
    # The cue CLEARING after beat 1 (present at start, absent later) is the success signal that the
    # DM resolved the thread — surfaced for observability alongside the each-beat fact.
    cue_cleared_after_action = cue_present_at_start and not cue_present_each_beat
    engagement = engagement_tally(transcript_path, cue)
    state_moved = state_progressed(cue, state_before, state_after)
    verdict = compute_verdict(cue, cue_present_at_start, engagement, state_moved, any_beat_failed)
    return {
        "cue": cue,
        "cue_present_at_start": cue_present_at_start,
        "cue_present_each_beat": cue_present_each_beat,
        "cue_cleared_after_action": cue_cleared_after_action,
        "per_beat_cue_present": per_beat,
        "dm_engagement_tools_called": dict(engagement),
        "quest_resolved_or_progressed": state_moved,
        "any_beat_failed": any_beat_failed,
        "verdict": verdict,
    }


def _usage() -> int:
    print(
        "usage:\n"
        "  probe_verdict.py cue-check <state_dir> <campaign_id>\n"
        "      → prints the CURRENT next_action.kind (empty line if none)\n"
        "  probe_verdict.py report <cue> <transcript.jsonl> <state_before.json> <state_after.json> "
        "<per_beat_cue_present_csv> [any_beat_failed(0/1)]\n"
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
        # any_beat_failed (optional, default "0"): "1" iff the shell driver classified any DM
        # beat as FAILED (WORLDOS_DM_BEAT_FAILED) — forces the INCONCLUSIVE verdict.
        any_beat_failed = len(argv) > 7 and argv[7].strip() == "1"
        # per_beat_cue_present is a CSV of 1/0 (one per beat the probe drove).
        per_beat = [tok.strip() == "1" for tok in csv.split(",") if tok.strip() != ""]
        before = json.loads(Path(before_path).read_text(encoding="utf-8"))
        after = json.loads(Path(after_path).read_text(encoding="utf-8"))
        report = build_report(cue, per_beat, Path(tpath), before, after, any_beat_failed)
        print(json.dumps(report))
        return 0
    return _usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
