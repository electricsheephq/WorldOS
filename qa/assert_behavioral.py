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
    from story_readout import coverage_from_tool_counts, felt_shape_from_state
except Exception:  # pragma: no cover - defensive: never let an import break the gate
    coverage_from_tool_counts = None
    felt_shape_from_state = None
# WS0 — the feature-engagement coverage scorer (manifest of authored story systems). Defensive
# import on the SAME pattern: a missing module degrades the engagement block to a no-op, never
# breaks the gate. All systems ship WARN, so this adds ZERO fatals (strictly additive).
try:
    from feature_engagement import engagement_coverage
except Exception:  # pragma: no cover - defensive
    engagement_coverage = None


def _run_infra_invalid_sentinel(run_jsonl_path: str) -> dict | None:
    """#1285 — the run-invalidation guard's scorer-side reader. qa/lib_beat_driver.sh's
    worldos_mark_run_infra_invalid stamps a sibling ``<run>.infra_invalid.json`` file (derived
    from the run.jsonl path exactly like ``<run>.state.json`` / ``<run>.chat.jsonl`` already are)
    when N consecutive DM beats fail — a quota window or host/session death mid-run (rri-a1-duo/
    duo2), not a product defect. Returns the parsed sentinel dict when present + valid, else None
    (no new CLI arg needed; this stays back-compat with every existing caller). Mirrors
    worldos_validate_lens_file's sentinel discipline for the bash side."""
    p = Path(run_jsonl_path)
    marker = p.with_name(p.name[: -len(p.suffix)] + ".infra_invalid.json") if p.suffix else p.with_suffix(".infra_invalid.json")
    if not marker.exists() or not marker.stat().st_size:
        return None
    try:
        d = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return {"infra_invalid": True, "reason": "sentinel file present but unparseable"}
    return d if isinstance(d, dict) else {"infra_invalid": True, "reason": "sentinel file malformed"}


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

# AMBUSH SIGNALS (#1271) — DM narration that plainly stages a surprise attack. When these fire
# but start_combat ran with NO surprise evaluation (no surpriser_ids passed, no `surprise` key
# in any return), the fight skipped the passive-Perception-vs-Stealth gate entirely — the exact
# "narrated an ambush but ran straight to initiative" omission #1271 was filed on. High-confidence,
# ambush-only phrasings (a clean pitched-battle beat scores 0 and can never false-fire).
_AMBUSH_SIGNAL = [
    r"\b(?:lunge|lunges|leap|leaps|spring|springs|burst|bursts|strike|strikes) (?:out )?from the shadows\b",
    r"\bfrom the shadows\b.{0,40}\b(?:attack|strike|lunge|pounce|ambush)\w*",
    r"\bcatch(?:es)? (?:them|you|him|her|the \w+) (?:by surprise|off[- ]guard|unaware|flat[- ]footed)\b",
    r"\bnever (?:saw|see|sees) (?:it|you|them|the \w+) coming\b",
    r"\b(?:spring|springs|sprung|lay|laid|set|sets) (?:the |an |a )?(?:ambush|trap)\b",
    r"\bambush(?:es|ed)?\b",
]
_AMBUSH_SIGNAL_RE = [re.compile(p, re.I) for p in _AMBUSH_SIGNAL]

# DETECTION SIGNALS (#1287, WARN — same family as the ambush/surprise WARN #1271). DM narration
# that plainly stages an NPC "noticing" or "spotting" the party — a passive-Perception/Insight
# beat the rules gate behind a roll, not DM fiat. High-confidence, detection-only phrasings (a
# clean beat with no detection language scores 0 and can never false-fire).
_DETECTION_SIGNAL = [
    r"\b(?:notices?|spots?|catches? sight of|glimpses?) (?:you|them|the party|movement|a shape)\b",
    r"\b(?:hears?|catches?) (?:you|them|the party|a sound|footsteps|a noise)\b",
    r"\b(?:eyes?|gaze) (?:snaps?|flicks?|turns?) (?:to|toward|on) (?:you|them|the party)\b",
    r"\b(?:doesn'?t|does not|fails? to) notice (?:you|them|the party)\b",
    r"\bpasses? (?:you|them|the party) by, unaware\b",
    r"\bstiffens?,? sensing\b",
]
_DETECTION_SIGNAL_RE = [re.compile(p, re.I) for p in _DETECTION_SIGNAL]
# Skills that gate a detection beat mechanically (skill_check(skill=…)) plus a bare `roll` whose
# `reason` names the same family — so a DM that rolled raw dice for "perception" still counts.
_DETECTION_SKILLS = ("perception", "insight", "investigation")
_DETECTION_ROLL_REASON_RE = re.compile(r"\b(?:perception|insight|investigation)\b", re.I)


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


def _detection_beats_without_check(events: list[dict]) -> list[str]:
    """DM narration text blocks that read like a detection beat (#1287 — same family as the
    ambush/surprise WARN #1271) with NO qualifying Perception/Insight/Investigation tool call
    in the SAME beat. A "beat" here is ONE assistant turn (one `assistant`-type event) plus every
    tool_use/tool_result it issued while producing its reply — a skill_check(skill=perception|
    insight|investigation) or a bare roll(reason=~that family) ANYWHERE earlier in that turn (or
    a preceding turn still in the same beat, before the NEXT assistant turn starts) satisfies the
    gate. The span resets at the START of each new assistant event (not after every text block),
    so multiple text blocks within one reply — or a tool call followed by narration later in the
    SAME turn — never false-split a beat. Returns the offending text blocks."""
    offenders: list[str] = []
    span_has_check = False
    prev_type = None
    for ev in events:
        ev_type = ev.get("type")
        if ev_type not in ("assistant", "user"):
            continue
        # A new beat starts at the first assistant event AFTER a text-terminated prior turn — i.e.
        # the transition into a fresh assistant event resets the check-seen flag. Consecutive
        # assistant events (a turn spanning several tool-call round-trips) and the interleaved
        # user/tool_result events in between all stay part of the SAME beat.
        if ev_type == "assistant" and prev_type == "assistant-with-text":
            span_has_check = False
        for b in (ev.get("message", {}) or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                short = (b.get("name") or "").split("__")[-1]
                inp = b.get("input") or {}
                if short == "skill_check":
                    skill = str(inp.get("skill") or inp.get("ability") or inp.get("skill_name")
                                or inp.get("check") or "").strip().lower()
                    if skill in _DETECTION_SKILLS:
                        span_has_check = True
                elif short == "roll" and _DETECTION_ROLL_REASON_RE.search(str(inp.get("reason") or "")):
                    span_has_check = True
            elif b.get("type") == "text" and (b.get("text") or "").strip():
                text = b["text"]
                if any(rx.search(text) for rx in _DETECTION_SIGNAL_RE) and not span_has_check:
                    offenders.append(text)
        if ev_type == "assistant":
            has_text = any(isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip()
                            for b in (ev.get("message", {}) or {}).get("content") or [])
            prev_type = "assistant-with-text" if has_text else "assistant"
        else:
            prev_type = ev_type
    return offenders


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

    # 0) RUN-INVALIDATION GUARD (#1285, FATAL). A stamped .infra_invalid.json sentinel means the
    # beat driver detected N consecutive DM beat failures mid-run (a quota window / host-session
    # death — rri-a1-duo/duo2: 5 straight is_error beats, narrated 'could not resolve this beat'
    # at peak dramatic tension, then scored to a 3.8 that was actually the infra failure). FATAL,
    # not WARN: this run's transcript is contaminated and must never be cited as a clean product
    # measurement — run_duo.sh's existing gate-RED path (worldos_cap_score_red) already caps every
    # scorecard to ≤2.5/INVALID on any FATAL failure, so this reuses that machinery for free.
    _infra = _run_infra_invalid_sentinel(sys.argv[1])
    chk("run_infra_valid", _infra is None,
        f"run stamped INFRA-INVALID: {(_infra or {}).get('reason', '(no reason recorded)')} "
        f"(consecutive_failed_beats={(_infra or {}).get('consecutive_failed_beats', '?')}) — "
        f"this measures infra collapse, not the product; the run must be re-run, not cited.",
        fatal=True)

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
        # error-class DM turn — qa/lib_beat_driver.sh worldos_chatlog_dm_failed). COUNT + REPORT
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

        # #1285: promote an 'N consecutive error beats' VARIANT of dm_beat_honesty to a
        # run-invalidating (FATAL) marker — the chat-log-derived twin of the run_infra_valid
        # sentinel check above. This is the SAME rri-a1-duo/duo2 defect class (a quota window or
        # host/session death that fails several beats in a row, not scattered across the run) but
        # derived independently from $CHAT's beat_failed rows in order, so a run whose runner
        # predates the #1285 sentinel (or a non-run_duo caller: run_party.sh / ui_playtest.sh,
        # which share worldos_chatlog_dm_failed but not yet the abort-and-stamp wiring) still gets
        # caught here rather than reading as a merely-WARNed, cite-able run. Threshold matches the
        # beat driver's default (qa/lib_beat_driver.sh WORLDOS_INFRA_INVALID_STREAK=3).
        _CONSECUTIVE_INVALID_THRESHOLD = 3
        _max_consecutive_failed = 0
        _run_len = 0
        for r in chat:
            if r.get("role") != "dm":
                continue
            if r.get("beat_failed") is True:
                _run_len += 1
                _max_consecutive_failed = max(_max_consecutive_failed, _run_len)
            else:
                _run_len = 0
        chk("dm_beat_honesty_no_consecutive_collapse", _max_consecutive_failed < _CONSECUTIVE_INVALID_THRESHOLD,
            f"{_max_consecutive_failed} consecutive DM beat failure(s) in $CHAT (threshold="
            f"{_CONSECUTIVE_INVALID_THRESHOLD}) — a run of back-to-back failed beats is an infra "
            f"collapse (quota window / host-session death mid-run), not scattered product defects; "
            f"this transcript is contaminated and must not be cited as a clean product measurement.",
            fatal=True)

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
            # combat left active at end-of-run. Naively this is a state-integrity failure (a clean
            # run end_combats), BUT the dominant cause in QA is a HARNESS-LENGTH ARTIFACT, not a DM
            # bug: a short emergent duo that ENTERS combat near its beat budget and TRUNCATES
            # mid-fight legitimately never reaches end_combat — the fight was cut off, not abandoned.
            # (Proven: qa/transcripts/claude-1v1-2 — an opus duo where start_combat fired in the last
            # handful of tool calls and the final DM line is literally cut off mid-sentence; the old
            # bare FATAL RED-capped all three lenses on a run that did nothing wrong.) So make the
            # SEVERITY beat-scoped, exactly like party_traveled:
            #
            #   • A SHORT facade run (< COMBAT_ABANDON_MIN_BEATS) with combat still active is treated
            #     as a TRUNCATED mid-combat scene → WARN. The standard 6-8 beat emergent combat duo
            #     lives here: it can't both run a full fight AND wrap it inside its budget.
            #   • A LONG run (>= COMBAT_ABANDON_MIN_BEATS) that ALSO truncated mid-fight (start_combat
            #     fired in the last beat or two of the tool stream) is STILL a truncation, not an
            #     abandon → WARN. The length alone doesn't make a cut-off fight a defect.
            #   • Only a LONG run where combat started EARLY (room to resolve), end_combat never fired,
            #     and the fight is STILL active at the snapshot is a genuine ABANDON — a real
            #     state-integrity bug that corrupts the next load → FATAL.
            #
            # This is the same conservative, beat-scoped pattern as the party_traveled fix: it keeps
            # the FATAL path for the real defect (a long run that left a fight hanging with room to
            # resolve) and stops the model-agnostic false-cap on short/truncated emergent runs.
            if len(mv) >= MIN_BEATS and combat.get("active"):
                # Strictly above MIN_BEATS(6): a real multi-encounter arc, not a short single-fight
                # vignette. A run shorter than this that's still mid-fight is presumed truncated.
                COMBAT_ABANDON_MIN_BEATS = 10
                # WHERE did the (last) start_combat fire in the ordered tool stream? If it's in the
                # final stretch of calls the fight only just began before the run ended ⇒ truncation,
                # never an abandon. Build the ordered short-name list once (mirrors the round1 scan's
                # extraction below); cheap and self-contained.
                _ordered_short: list[str] = []
                for _ev in events:
                    if _ev.get("type") != "assistant":
                        continue
                    for _b in (_ev.get("message", {}) or {}).get("content") or []:
                        if isinstance(_b, dict) and _b.get("type") == "tool_use":
                            _ordered_short.append((_b.get("name") or "").split("__")[-1])
                _total_calls = len(_ordered_short)
                _last_sc = max((i for i, c in enumerate(_ordered_short) if c == "start_combat"),
                               default=-1)
                # "Started late" = the last start_combat landed in the final ~20% of the tool stream
                # (truncation: the fight only just began before the run ended), OR there is NO
                # start_combat in the stream at all — a resume-into-combat session whose fight carried
                # over from a prior session, which we CANNOT prove started early this run, so it is a
                # truncation/resume, never an abandon (this matches the rationale comment above; the
                # earlier form FATAL'd a resumed-into-combat run, contradicting it).
                started_late = (
                    _last_sc < 0
                    or (_last_sc >= 0 and _total_calls > 0
                        and _last_sc >= int(_total_calls * 0.8))
                )
                # A genuine ABANDON: a SUBSTANTIAL run, combat started EARLY (room to resolve), the DM
                # never end_combat'd, yet the fight is still active. Everything else (short run, OR a
                # late/truncated start, OR no start_combat in the stream) is a truncation ⇒ WARN.
                _abandoned = (
                    len(mv) >= COMBAT_ABANDON_MIN_BEATS
                    and not started_late
                    and tools.get("end_combat", 0) == 0
                )
                chk("combat_not_left_active", False,
                    f"combat.active=True at end-of-run — combat left active "
                    f"(beats={len(mv)}, start_combat@{_last_sc}/{_total_calls} calls, "
                    f"end_combat={tools.get('end_combat', 0)}) — "
                    + ("FATAL: substantial run, fight started early with room to resolve and was "
                       "never end_combat'd (state-integrity fail: a finished session should end_combat)"
                       if _abandoned else
                       "WARN: run plausibly TRUNCATED mid-combat (short run or combat started near "
                       "the beat budget) — a harness-length artifact, not an abandoned fight"),
                    fatal=_abandoned)
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
    # legitimately never advances days or travels, so it sets WORLDOS_GATE_COMBAT_SPRINT=1 to skip
    # the world-progression floor (which would else false-RED a 40+-beat fight on a 1-location run).
    # Story / duo runs (no env var) keep the floor — it's the honest anti-frozen-scene gate.
    if session_beats >= MIN_BEATS and not os.environ.get("WORLDOS_GATE_COMBAT_SPRINT"):
        day = state.get("day") or 1
        tod = (state.get("time_of_day") or "").strip().lower()
        # Campaigns start at day 1, "morning"; a full session still parked there never aged.
        chk("world_advanced_time", day > 1 or (tod not in ("", "morning")),
            f"day={day} time_of_day={tod or '?'} after {session_beats} beats — the clock never moved "
            f"(advance_time / travel_to(advance_time=True) / long_rest)")
        # dm_advanced_time (WS-E) — WARN-only. world_advanced_time above passes on ANY clock movement,
        # INCLUDING the qa worldos_soft_tick backstop that auto-advances the clock every idle beat —
        # which MASKS a DM that never rests or advances time itself (0 long_rest/downtime across the
        # corpus, yet the soft-tick made day>1 so the floor stayed GREEN). This surfaces that mask
        # without failing the run: a DM-ISSUED advance_time / long_rest / short_rest / downtime in the
        # tool stream. The DM moving time itself is what feeds companion regard + camp + every
        # day-gated system. (travel_to(advance_time=True) also advances time but isn't separately
        # tallied here, so a rare travel-only run may WARN — it's advisory, never fatal.)
        dm_time_tools = (tools["advance_time"] + tools["long_rest"]
                         + tools["short_rest"] + tools["downtime"])
        chk("dm_advanced_time", dm_time_tools > 0,
            f"the DM never issued a time-advance tool in {session_beats} beats — only the harness "
            f"soft-tick moved the clock, so companion regard / camp / day-gated systems stay starved; "
            f"call long_rest / advance_time / downtime", fatal=False)
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
        # SEVERITY IS BEAT-SCOPED (false-cap fix): "the DM never left the opening scene" is only a
        # STUCK-DM failure on a SUBSTANTIAL run. A SHORT run (< SINGLE_SCENE_MIN_BEATS) in one
        # location is a legitimate single-scene vignette — the standard 6-beat social duo lives here
        # — NOT a frozen stall, so below that length this is a WARN, never a lens-capping RED. It was
        # FATAL-capping legitimate short single-scene play on BOTH models (Claude opus AND GLM — a
        # model-agnostic false-cap that deflated the duo scores). At/above SINGLE_SCENE_MIN_BEATS the
        # strict exception is UNCHANGED (travel >=2, OR a clock-advancing arc-resolving in-place
        # drama) — a substantial run that never moves AND never progresses is still a FATAL stuck DM,
        # and the anti-gaming AND-logic (clock-only/beats-only deliberately excluded) is preserved.
        _pt_fatal = session_beats >= SINGLE_SCENE_MIN_BEATS
        chk("party_traveled", visited >= 2 or in_place_progression,
            f"visited {visited}/{len(locs)} location(s) after {session_beats} beats — the party never "
            f"left the opening scene (travel_to / add_location make_current=True); "
            f"in-place-progression exception NOT met "
            f"(clock_advanced={clock_advanced} arc_resolved={arc_resolved} "
            f"beats>={SINGLE_SCENE_MIN_BEATS}? {session_beats >= SINGLE_SCENE_MIN_BEATS}) — "
            f"{'FATAL (substantial run, stuck)' if _pt_fatal else 'WARN (short single-scene vignette)'}",
            fatal=_pt_fatal)
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
    # FELT-SHAPE floor — a run only OWES a felt 3-act shape once it's long enough to have one
    # (a memory note: structural runs need >=24 beats; strictly above STRUCTURAL_MIN_BEATS so
    # every currently-passing <24-beat run is unaffected). See the flat_arc clause below.
    FELT_SHAPE_MIN_BEATS = 24
    companions = [c for c in chars.values()
                  if isinstance(c, dict) and c.get("kind") == "companion"]
    if (session_beats >= STRUCTURAL_MIN_BEATS and companions
            and not os.environ.get("WORLDOS_GATE_COMBAT_SPRINT")):
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

        # AUTHORED-CAMPAIGN SCOPE GUARD for sub-check (b) — #1036 (Option A; mirrors #1030's
        # WARN-vs-FATAL discipline for party_traveled / combat_not_left_active). The campaign-arc
        # quest is SEEDED from the authored adventure `hook` and is multi-session by design; the
        # authored adventures (e.g. embergloom-pact) author NO closable sub-quests, so the DM never
        # calls complete_quest / set_quest_status and (b) `unresolved_arc` FATAL-capped a clean
        # 25-beat authored run to 2.5 — a self-inflicted false-cap (the main quest can't legitimately
        # resolve inside one session). An AUTHORED run is identifiable at the gate from signals
        # already in scope: `start_adventure` is the cold-open call for authored runs (in the tool
        # stream `_tally` always sees), and `state["scenes"]` is non-empty only for authored
        # adventures (the seeded-campaign snapshot). When authored, (b) is demoted FATAL->WARN —
        # the gate still APPENDS the WARN message (visibility preserved), but the run is not
        # RED-capped on (b) alone.
        #
        # PRESERVE FATAL for the original failure class:
        #   - any NON-authored run (the proven 18-beat narrated-not-engaged failure), AND
        #   - an authored run where the DM ADDED a sub-quest (add_quest) and left it unresolved —
        #     a genuine dropped thread the DM itself opened, distinct from the hook-seeded campaign
        #     arc (add_quest is the engine quest-creation tool, server.py:add_quest; it is
        #     distinguishable from the hook-seeded quest at gate time, so we keep that case FATAL).
        is_authored_campaign = bool(tools.get("start_adventure") or state.get("scenes"))
        dm_added_quest = bool(tools.get("add_quest"))

        bad_bits = []
        if approval_frozen_run:
            bad_bits.append(
                f"approval frozen all run (no companion left attitude 0; companions="
                f"{[c.get('name','?') for c in companions]}) AND no camp/long_rest happened")
        if unresolved_arc:
            bad_bits.append(
                f"{len(active_quests)} quest(s) still active at session end across a "
                f"{visited}-location arc with no quest-resolution call "
                f"({[q.get('title') or q.get('id') or '?' for q in active_quests]})"
                + (" [authored campaign + only the hook-seeded arc ⇒ WARN, not RED (#1036)]"
                   if (is_authored_campaign and not dm_added_quest) else ""))

        # Severity: clause (a) approval-frozen stays FATAL ALWAYS. Clause (b) unresolved_arc is
        # FATAL unless it's an authored campaign whose ONLY open quest is the hook-seeded arc.
        _unresolved_fatal = unresolved_arc and (not is_authored_campaign or dm_added_quest)
        _structural_fatal = approval_frozen_run or _unresolved_fatal
        chk("structural_completeness", not bad_bits,
            f"a {session_beats}-beat session with a companion never engaged a core system: "
            + "; ".join(bad_bits)
            + " — the engine relationship/quest tools (record_decision approval_tags / "
              "adjust_attitude / camp_scene / complete_quest evolves_to) were narrated, not used",
            fatal=_structural_fatal)

        # (c) FLAT ARC (WARN-FIRST). A LONG run (>= FELT_SHAPE_MIN_BEATS) that CLAIMS three acts
        # (the engine narrative_arc cursor OR contiguous act-tags) but whose arc never TURNED — no
        # real midpoint reversal AND no climax — is a flat fetch-quest shape, not a felt
        # setup→reversal→climax. It ONLY fires when the run claims >=3 acts (a legit short/2-act
        # session is never penalized for not climaxing) and reads ENGINE-REGISTERED events
        # (narrative_arc landed flags / banded decisions / completed quests), so a DM that genuinely
        # ran a 3-act arc through the tools passes by construction. Shipped WARN-first (fatal=False)
        # for one CI sweep, then graduates to fatal — the same discipline as caster_has_spellbook.
        if session_beats >= FELT_SHAPE_MIN_BEATS and felt_shape_from_state is not None:
            fs = felt_shape_from_state(state, tools)
            acts_claimed = max(int(fs.get("acts_engine_reached") or 0),
                               int(fs.get("acts_tag_reached") or 0))
            flat_arc = acts_claimed >= 3 and not (fs.get("reversal") and fs.get("climax"))
            chk("flat_arc", not flat_arc,
                f"a {session_beats}-beat run covered {acts_claimed} acts but the arc never turned "
                f"(reversal={bool(fs.get('reversal'))} climax={bool(fs.get('climax'))}) — a flat "
                f"fetch-quest shape, not a felt setup→reversal→climax (land a real midpoint "
                f"reversal + a late climax: record_decision the turn, complete_quest the spine late)",
                fatal=False)

    # ── WS0: FEATURE-ENGAGEMENT COVERAGE (the dead-system tracker; ALL-WARN this PR) ──────
    # Today an entire authored subsystem (companion approval, camp downtime, faction questlines,
    # …) can be 100% INERT across a whole run and every gate/lens still scores it 10/10 — no gate
    # is engagement-coverage. This block reads the engine snapshot + the DM tool counts (the SAME
    # ground-truth surfaces the structural_completeness floor uses — never fiction) and, for each
    # system the run was OWED but never engaged, emits a chk. Severity rides the manifest: every
    # system ships 'warn' this PR, so this adds ZERO fatals (every currently-green run stays
    # green). FATAL graduation is a FUTURE, post-sweep PR. The combat-sprint env skip is honored
    # inside engagement_coverage (mirrors the world-progression / structural floors above).
    if engagement_coverage is not None:
        try:
            eng = engagement_coverage(state, dict(tools), session_beats)
        except Exception:  # pragma: no cover - the engagement scorer must never break the gate
            eng = {"inert": []}
        for item in eng.get("inert", []):
            chk(f"engagement_{item.get('id', '?')}", False, item.get("why", ""),
                fatal=(item.get("severity") == "fatal"))

    # ── SECTION A: RESULT-SIDE + per-record state gates (audit-tests.md §A) ───────────────
    # These read artifacts the existing gates ignore: the tool_RESULT payloads (A1/A2/A8) and
    # per-record final state that's present-but-unchecked (A3 living monster, A5 PC XP, A6
    # non-party companions, A7 skill profs). Each is null/scope-guarded so it never false-REDs
    # a run that simply didn't exercise the path — the same discipline as the gates above.
    evs = _tool_events(events)
    chars_all = state.get("characters", {}) or {}
    party_ids = state.get("party", []) or []

    # AMBUSH-WITHOUT-SURPRISE-GATE (#1271, WARN). The DM narrated an ambush ("lunge from the
    # shadows", a sprung trap, "never saw it coming") but start_combat ran with NO surprise
    # evaluation — no surpriser_ids on any start_combat call AND no `surprise` key in any return —
    # so the passive-Perception-vs-Stealth gate never ran and the ambush lost its mechanical teeth.
    # Only meaningful when a fight actually STARTED (start_combat>0); a purely-narrative ambush the
    # player talks/sneaks past never reaches combat and correctly does not fire. WARN, never fatal —
    # an ambush-flavored word in prose is a soft signal, and the engine may legitimately find nobody
    # was surprised (but then surpriser_ids WAS passed → `surprise` key present → this doesn't fire).
    _sc_calls = [(inp, r) for (n, inp, r, err, _t) in evs if n == "start_combat" and not err]
    if _sc_calls:
        _ambush_narrated = any(
            any(rx.search(t) for rx in _AMBUSH_SIGNAL_RE) for t in _dm_narration_texts(events)
        )
        _surprise_evaluated = any(
            (inp.get("surpriser_ids") or (isinstance(r, dict) and r.get("surprise")))
            for inp, r in _sc_calls
        )
        chk("ambush_ran_surprise_gate", not (_ambush_narrated and not _surprise_evaluated),
            "DM narration staged an ambush but start_combat ran with no surprise evaluation "
            "(no surpriser_ids, no `surprise` in the return) — the passive-Perception-vs-Stealth "
            "gate was skipped, so the ambush had no mechanical effect. Pass "
            "surpriser_ids=[the attacker id(s)] on start_combat for any narrated ambush; the engine "
            "rolls Stealth vs passive Perception and applies SRD-5.2 initiative disadvantage to the "
            "surprised set.",
            fatal=False)

    # DETECTION-BEAT-REQUIRES-CHECK GATE (#1287, WARN — same family as #1271 above). DM narration
    # that plainly stages an NPC noticing/spotting/hearing the party ("the guard's eyes snap
    # toward you", "she catches a sound") without a preceding Perception/Insight/Investigation
    # skill_check (or a reason-tagged roll) in the SAME beat is DM fiat, not a gated roll — the
    # rri-a1-duo defect this promotes from the scorer's suggested_fix. WARN, never fatal: prose
    # alone is a soft signal and a false-fire here should never cap an otherwise-clean run.
    _detection_offenders = _detection_beats_without_check(events)
    chk("detection_beat_requires_check", not _detection_offenders,
        f"{len(_detection_offenders)} DM narration beat(s) staged an NPC detecting/noticing the "
        f"party with no preceding Perception/Insight/Investigation skill_check (or reason-tagged "
        f"roll) in the same beat — DM fiat, not a gated roll — e.g. "
        f"{_detection_offenders[0][:90]!r}" if _detection_offenders else "",
        fatal=False)

    def _quest_reward_already_awarded(q: dict) -> bool:
        return any(bool(q.get(k)) for k in ("milestone_awarded", "awarded", "rewarded", "xp_awarded"))

    # A8 — a tool call REJECTED with a schema/validation error (extra_forbidden ⇒ version-skew
    # or a wrong field). The DM's intent for that call silently did not take effect.
    #
    # DE-FLAKE (#897, mirrors #1030's discriminator-aware severity). Behavioral is computed from
    # ONE stochastic duo; the bare "ANY schema rejection ⇒ FATAL" rule made a SINGLE recovered
    # transient (the DM emits one malformed call, immediately retries the SAME tool correctly, the
    # session completes cleanly — invisible to the player) RED-cap EVERY lens to 2.5 and swing the
    # headline RRI by ~1.0 (observed twice). That is a precision bug, not a real integrity signal.
    #
    # A rejection now counts toward the FATAL set only when it is a PATTERN, not a recovered blip:
    #   • UNRECOVERED — the offending tool was NEVER successfully called (is_error=False) anywhere
    #     in the run, so the DM's intent for that tool silently never took effect (the genuine
    #     version-skew defect: a stale signature the DM could not get right). [corpus fixture]
    #   • REPEATED — the SAME tool was rejected with a schema/validation error >=2x across the run
    #     = a systematic skew (the DM keeps re-using a stale/wrong signature). Repetition is the
    #     real-skew signal, so this stays FATAL EVEN IF a later call eventually succeeds.
    # A SINGLE rejection of a tool the DM then successfully retried (recovered transient) ⇒ WARN,
    # never RED. This is a PRECISION improvement (distinguish player-felt skew from invisible
    # recovered transients), NOT a leniency hack — the unrecovered + repeated classes the gate was
    # built for still flip RED; the corpus fixture (an unrecovered update_character) still REDs.
    errors = [(n, text) for (n, inp, r, err, text) in evs if err]
    if errors:
        schema_errs = [(n, t) for (n, t) in errors
                       if "extra_forbidden" in t or "validation error" in t.lower()]
        benign = [(n, t) for (n, t) in errors if (n, t) not in schema_errs]
        # Which tools were EVER called successfully (is_error=False) anywhere in the run? A schema
        # rejection of tool X is "recovered" iff X also appears with a clean result somewhere —
        # the DM got the call right (order-independent: a clean call before or after a flub both
        # prove the DM CAN issue that tool; the rejected intent itself is what we score).
        succeeded_tools = {n for (n, inp, r, err, _) in evs if not err}
        # How many times was each tool rejected with a schema/validation error?
        schema_reject_counts: Counter = Counter(n for (n, _t) in schema_errs)
        # FATAL set: a rejection is fatal if its tool was NEVER successfully called (unrecovered)
        # OR its tool was rejected >=2x (repeated = systematic skew). De-dup by tool for the
        # message (the per-tool classification is what matters, not the raw rejection count).
        fatal_tools = sorted({
            n for (n, _t) in schema_errs
            if n not in succeeded_tools or schema_reject_counts[n] >= 2
        })
        # Recovered transients: a tool rejected exactly once that was later (or earlier) called
        # cleanly — surfaced as a WARN so the flub is never silently dropped.
        recovered_tools = sorted({
            n for (n, _t) in schema_errs
            if n in succeeded_tools and schema_reject_counts[n] < 2
        })
        if fatal_tools:
            # Detail names which fatal class each tool fell into, so a RED is diagnosable.
            why = ", ".join(
                f"{n}(" + ("repeated x%d" % schema_reject_counts[n]
                           if schema_reject_counts[n] >= 2 else "unrecovered") + ")"
                for n in fatal_tools)
            first = next((t for (n, t) in schema_errs if n in set(fatal_tools)), "")
            chk("no_rejected_tool_calls", False,
                f"{len(fatal_tools)} tool(s) with a SYSTEMATIC schema/validation rejection "
                f"(extra_forbidden ⇒ version skew / wrong field): {why}; first: {first[:160]}",
                fatal=True)
        elif recovered_tools:
            # All schema rejections were single + recovered ⇒ GREEN, but WARN so it's visible.
            chk("no_rejected_tool_calls", False,
                f"{len(recovered_tools)} RECOVERED transient schema rejection(s) "
                f"(flubbed once, retried the same tool successfully ⇒ invisible to the player): "
                f"{recovered_tools} — surfaced, not RED-capped (#897)", fatal=False)
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

    # A3 (FATAL; WARN under WORLDOS_GATE_COMBAT_SPRINT) — end_combat called but a hostile is
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
        sprint = bool(os.environ.get("WORLDOS_GATE_COMBAT_SPRINT"))
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
    # superiority_dice (Battle Master) joins the seeded-but-unused coverage pool (#1040 scorer-opt):
    # the Angry-DM lens used to flag a seeded-but-never-spent Superiority-Dice pool as a 5e-fidelity
    # omission; it's a pure COVERAGE signal exactly like the other three, scope-guarded the same way
    # (only emitted when a BM sheet actually carries the `superiority_dice` pool — a party without it
    # is byte-identical, no key emitted). The pool key is `superiority_dice` (server.py use_resource +
    # the L631 re-derive note both key it that way).
    _SIGNATURE_POOLS = ("channel_divinity", "action_surge", "second_wind", "superiority_dice")
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
            "(channel_divinity / action_surge / second_wind / superiority_dice); the combat seed "
            "should exercise them (csmed-1/2/4 + #1040 coverage omission)" if sig_unused else "",
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
    #
    # #461 grid (PR-5): on a GRID the engine HAS positions and AUTO-APPLIES the rule, surfacing
    # attack_roll.ranged_in_melee_disadvantage=True (and folding disadvantage into the roll). For
    # those engine-ruled attacks the brittle theater proxy is moot — the engine already decided
    # authoritatively. We READ the new field: an attack that carries it is definitively clean
    # (the rule fired), and it does not need the mutual-melee heuristic. (An on-grid ranged shot
    # with NO field is the engine ruling "no adjacent hostile" — also authoritative, not a miss.)
    attacks = [(i, inp, r) for i, (n, inp, r, err, _) in enumerate(evs)
               if n == "attack" and isinstance(r, dict)]

    def _grid_ranged_ruled(res: dict) -> bool:
        """True if the engine AUTO-APPLIED the on-grid ranged-in-melee disadvantage to this
        attack (the surfaced result field). On-grid the engine is authoritative on adjacency,
        so such an attack is clean by construction — never a proxy false-flag."""
        ar = res.get("attack_roll") or {}
        return bool(isinstance(ar, dict) and ar.get("ranged_in_melee_disadvantage"))

    grid_auto_applied = sum(1 for (_i, _inp, r) in attacks if _grid_ranged_ruled(r))

    def _melee_damage(inp_dict: dict, res: dict) -> bool:
        """A clearly-MELEE attack: its damage type is slashing/bludgeoning (a sword/club), not
        the piercing that a bow/crossbow/pistol also shares. Best-effort on the available
        fields (no weapon/ranged field exists in the stream)."""
        blob = (json.dumps(inp_dict) + json.dumps(res.get("damage") or {})).lower()
        return ("slashing" in blob or "bludgeoning" in blob) and "piercing" not in blob

    ranged_flags = []
    for ai, (idx_a, inp_a, ra) in enumerate(attacks):
        if ra.get("disadvantage") or _grid_ranged_ruled(ra):
            continue  # disadvantage already applied (incl. on-grid auto-apply) → clean
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
        # On a grid run the engine auto-applies the rule (grid_auto_applied attacks above);
        # the theater proxy only ever fires off-grid. Either way the check PASSES when no
        # un-disadvantaged adjacent shot remains; the detail notes the grid auto-applies so a
        # green grid run is legible (not a silent pass).
        detail = "; ".join(ranged_flags)
        if not ranged_flags and grid_auto_applied:
            detail = f"grid: engine auto-applied ranged-in-melee disadvantage on {grid_auto_applied} shot(s)"
        chk("ranged_disadvantage_in_melee", not ranged_flags, detail, fatal=False)

    # cs-1040val (#1/#2): a class-feature rider spent via use_resource MUST be consumed by a
    # following attack — a Battle Master superiority die appears in that attack's DAMAGE, and a
    # War Domain Guided Strike's +10 in its attack ROLL. The engine now auto-folds the die and
    # surfaces the +10, so this catches the residual "burned the resource but never attacked"
    # sequencing miss (the exact cs-1040val omission: die/+10 spent, narrated as landing, but no
    # attack carried it). The pending rider is consumed by the spender's FIRST subsequent attack,
    # so we check that attack. WARN, not FATAL — a sequencing miss is a DM-adherence defect that
    # should surface to the scorer, not RED-cap the whole run (graduate to FATAL after clean
    # sweeps, per the gate-graduation discipline).
    def _spender(inp_dict: dict) -> str:
        return inp_dict.get("character_id") or inp_dict.get("actor_id") or ""

    def _attacker_id(inp_dict: dict) -> str:
        return (inp_dict.get("attacker_id") or inp_dict.get("character_id")
                or inp_dict.get("npc_id") or "")

    dangling_riders: list[str] = []
    for i, (short, inp, obj, is_err, _t) in enumerate(evs):
        if short != "use_resource" or is_err or not isinstance(obj, dict):
            continue
        sets_dmg = bool(obj.get("maneuver_damage") or obj.get("auto_folded"))
        sets_hit = bool(obj.get("attack_bonus"))
        if not (sets_dmg or sets_hit):
            continue  # an ordinary resource spend (no pending attack rider) — nothing to track
        spender = _spender(inp)
        consumed = False
        for short2, inp2, obj2, is_err2, _t2 in evs[i + 1:]:
            # a later rider-setting spend by the same spender supersedes this one — stop here
            if (short2 == "use_resource" and _spender(inp2) == spender
                    and isinstance(obj2, dict)
                    and (obj2.get("maneuver_damage") or obj2.get("auto_folded") or obj2.get("attack_bonus"))):
                break
            if short2 == "attack" and isinstance(obj2, dict) and _attacker_id(inp2) == spender:
                ar = obj2.get("attack_roll") or {}
                consumed = (sets_dmg and bool(obj2.get("maneuver_damage"))) or \
                           (sets_hit and bool(ar.get("to_hit_bonus")))
                break  # the FIRST attack consumes the pending rider, carried or not
        if not consumed:
            kind = "Guided Strike +10" if sets_hit else "superiority die"
            src = obj.get("resource") or "?"
            dangling_riders.append(f"{spender or '?'} spent a {kind} ({src}) but no following attack carried it")
    chk("maneuver_rider_consumed", not dangling_riders, "; ".join(dangling_riders), fatal=False)

    # cs-wave2-val: a Guiding Bolt advantage rider, once it LANDS on a target, must auto-grant
    # advantage to the NEXT attack against that target ("on a hit ... the next attack roll made
    # against it ... has Advantage", SRD 5.2). The engine materializes the rider on a hit
    # (attack -> on_hit_effect_applied: ["Guiding Bolt"]) and combat.attack_modifiers auto-grants
    # + attack() consumes it (advantage_source == "Guiding Bolt"), with NO advantage= flag needed
    # from the DM (#194/#1033). This catches the residual omission the Angry-DM scorer flagged:
    # the rider was registered + narrated as landing, but the next attack on the marked target did
    # not carry the advantage (the engine path was bypassed, or the marker silently dropped). We
    # find each attack that MATERIALIZED the marker (on_hit_effect_applied), then check the FIRST
    # subsequent attack against that SAME target (before any GB re-materialization on it) — it must
    # report advantage_source == "Guiding Bolt". WARN, not FATAL — a single missed rider is a
    # DM/path-adherence smell that should surface to the scorer, not RED-cap the whole run
    # (graduate to FATAL after clean sweeps, per the gate-graduation discipline). Scope-guarded: a
    # run that never lands a Guiding Bolt rider emits NOTHING (additive — byte-identical to today).
    GB = "Guiding Bolt"
    gb_unconsumed: list[str] = []
    for i, (short, inp, obj, is_err, _t) in enumerate(evs):
        if short != "attack" or is_err or not isinstance(obj, dict):
            continue
        if GB not in (obj.get("on_hit_effect_applied") or []):
            continue  # this attack did not LAND a Guiding Bolt advantage marker — skip
        marked = obj.get("target")  # the foe now carrying the "next attack has advantage" marker
        if not marked:
            continue
        # The FIRST subsequent attack against this SAME target must carry the GB advantage. The
        # attack that materialized the marker (this one, a GB spell attack) is NOT the consumer —
        # the marker benefits the NEXT attack roll against the foe. A later GB re-cast on the same
        # foe re-arms a fresh marker, so we stop the search there (the new marker owns its own
        # window) and never charge this incident with a follow-up that belongs to the re-arm.
        for short2, inp2, obj2, is_err2, _t2 in evs[i + 1:]:
            if short2 != "attack" or is_err2 or not isinstance(obj2, dict):
                continue
            if obj2.get("target") != marked:
                continue  # an attack on a different target neither consumes nor proves the marker
            if GB in (obj2.get("on_hit_effect_applied") or []):
                break  # a re-materialized GB marker on this foe — new window owns the next attack
            ok = obj2.get("advantage_source") == GB or bool(obj2.get("advantage_consumed"))
            if not ok:
                attacker2 = obj2.get("attacker") or "?"
                gb_unconsumed.append(
                    f"{attacker2}->{marked}: a Guiding Bolt advantage marker was live on "
                    f"{marked} but the next attack against it showed "
                    f"advantage_source={obj2.get('advantage_source')!r} (expected 'Guiding Bolt')"
                )
            break  # only the FIRST subsequent attack on the marked foe is the marker's beneficiary
    chk("guiding_bolt_advantage_consumed", not gb_unconsumed, "; ".join(gb_unconsumed), fatal=False)

    # ── #1040 scorer-opt: deterministic 5e-fidelity checks MIGRATED from the slow Angry-DM LLM
    # lens into the fast gate. Each is WARN-first (graduate to FATAL after clean sweeps) and
    # scope-guarded so a run lacking the feature/state emits NOTHING (additive: byte-identical).
    # They read ONLY persisted snapshot fields + the tool counter — never fiction.

    # M1) MULTIATTACK BUDGET HONORED (the single biggest omission class). The 5e Extra-Attack
    # feature lets a char with extra_attacks>0 make extra_attacks+1 attacks per Attack action; the
    # Angry-DM lens repeatedly flagged a Multiattack truncated to ONE swing. Deterministic proxy:
    # if a combat ran (start_combat>0) and a player/companion has extra_attacks>0, the run should
    # show at least extra_attacks+1 attack() calls. Conservative + aggregate (run-total, not
    # per-turn — the snapshot doesn't expose a reliable per-turn attack ledger): we only WARN when
    # the WHOLE run's attack count is below a SINGLE multiattacker's own budget, which is an
    # unambiguous truncation (even one Attack action by the highest-budget combatant should clear
    # it). Scope-guarded: never emitted unless a combat ran AND a party member carries extra_attacks
    # (a party with none — every <L5 build — produces no key). Reads chars_all["extra_attacks"],
    # tools["attack"], state["combat"]. WARN: theater-of-mind can't prove a SPECIFIC turn truncated;
    # this surfaces the smell to the scorer without RED-capping the run.
    if tools.get("start_combat", 0) > 0:
        multiattackers = [
            c for c in chars_all.values()
            if isinstance(c, dict) and c.get("kind") in ("player", "companion")
            and int(c.get("extra_attacks") or 0) > 0
        ]
        if multiattackers:
            attack_calls = tools.get("attack", 0)
            # The single largest per-Attack-action budget in the party (extra_attacks+1). If the
            # whole run made fewer attack() calls than even ONE such combatant's single-action
            # budget, Multiattack was almost certainly truncated to one swing.
            top_budget = max(int(c.get("extra_attacks") or 0) + 1 for c in multiattackers)
            truncated = attack_calls < top_budget
            names = [f"{c.get('name', '?')} (extra_attacks={int(c.get('extra_attacks') or 0)})"
                     for c in multiattackers]
            chk("multiattack_budget_honored", not truncated,
                f"{attack_calls} attack call(s) across the run but a multiattacker's budget is "
                f"{top_budget} (extra_attacks+1) — Multiattack likely truncated to one attack "
                f"[{', '.join(names)}]" if truncated else "",
                fatal=False)

    # M3) CASTER EXERCISED SPELLCASTING. A spellcaster present for a multi-beat run that NEVER cast
    # a spell is a 5e-fidelity smell the Angry-DM lens flagged (a wizard who only swung a dagger).
    # Scope-guard: "is a caster" is read from REAL serialized fields — non-empty spell_slots /
    # spells_known / spells_prepared / cantrips_known (the engine's _recompute_spellcasting sizes
    # spell_slots, so a real seeded caster always carries them) OR a truthy `spellcasting` blob
    # (test/legacy fixtures). A non-caster (martial) carries none of these → no key emitted
    # (additive). "Cast" is read broadly: cast_spell OR an attack-roll spell resolves via attack(),
    # so we require ZERO of (cast_spell) AND the run to be substantial — a caster who only ever
    # cantripped via attack() is NOT flagged (attack>0 means they engaged). Only a caster with
    # cast_spell==0 across a MIN_BEATS+ run WARNs. WARN, never fatal.
    def _is_caster(c: dict) -> bool:
        return bool(
            c.get("spell_slots") or c.get("spells_known") or c.get("spells_prepared")
            or c.get("cantrips_known") or c.get("cantrips") or c.get("prepared_spells")
            or c.get("spellcasting")
        )
    if session_beats >= MIN_BEATS:
        idle_casters = []
        for c in chars_all.values():
            if not isinstance(c, dict) or c.get("kind") not in ("player", "companion"):
                continue
            if _is_caster(c) and tools.get("cast_spell", 0) == 0:
                idle_casters.append(c.get("name", "?"))
        # Scope-guard: only emit when at least one caster is present (a martial-only party → no key).
        if idle_casters:
            chk("caster_exercised_spellcasting", False,
                f"caster(s) present but cast_spell=0 across {session_beats} beats: {idle_casters} "
                f"— a spellcaster that never cast a leveled/prepared spell (note: attack-roll "
                f"cantrips resolve via attack(); this only flags a wholly-uncast caster)",
                fatal=False)

    # M4) DEATH SAVES ROLLED WHEN DOWNED. A char at the snapshot with current_hp==0, not dead, not
    # stable is actively dying — the engine rolls death saves (auto-clocked on next_turn) or the DM
    # calls roll_death_save. If the run shows NO roll_death_save call AND no death_saves recorded on
    # the dying char, the dying state was narrated, not resolved (the Angry-DM "downed but no death
    # saves" flag). Scope-guard: only emitted when a char is actually in the downed-but-dying state
    # (current_hp==0 and not dead and not stable). A clean run with nobody downed produces no key.
    # WARN, never fatal — a player downed on the VERY LAST beat legitimately may not have rolled yet.
    downed_dying = [
        c for c in chars_all.values()
        if isinstance(c, dict) and c.get("kind") in ("player", "companion")
        and (c.get("current_hp") or 0) == 0 and not c.get("dead") and not c.get("stable")
    ]
    if downed_dying:
        # A death save may have been recorded on the char's own death_saves ledger even without a
        # manual roll_death_save call (the engine auto-clocks them) — count that as resolved.
        def _has_death_saves(c: dict) -> bool:
            ds = c.get("death_saves") or {}
            if not isinstance(ds, dict):
                return False
            return bool((ds.get("successes") or 0) or (ds.get("failures") or 0))
        unrolled = [c.get("name", "?") for c in downed_dying
                    if tools.get("roll_death_save", 0) == 0 and not _has_death_saves(c)]
        if unrolled:
            chk("death_saves_rolled_when_downed", False,
                f"char(s) downed (current_hp=0, not dead, not stable) at end-of-run with NO "
                f"roll_death_save call and no death_saves recorded: {unrolled} — the dying state was "
                f"narrated, not rolled (roll_death_save / the auto-clocked save on next_turn)",
                fatal=False)

    # M5) CONCENTRATION DROPPED CLEANLY. A char can concentrate on only ONE spell; casting a second
    # concentration spell must drop the first (the engine enforces this on cast_spell, but a DM that
    # narrates a second concentration spell while leaving the first on the sheet is the Angry-DM
    # "double-concentration" flag). Detecting the SEQUENCE of concentration casts is unreliable from
    # the available data (cast_spell payloads don't reliably tag concentration), so — per the task's
    # fallback — this is the SIMPLER, conservative end-state check: a char ends with `concentration`
    # set to a spell AND its active_effects carry >1 concentration-tagged effect from ITS OWN casting
    # (an own-source concentration effect is one with concentration==True that is NOT a
    # linked_to_concentration child — those are ally-side twins of someone else's spell). Two
    # own-source concentration effects on one caster is a clean double-concentration the drop never
    # cleared. Scope-guard: only emitted when a char actually ends concentrating with >1 own
    # concentration effect — every other run produces no key. WARN, never fatal (conservative proxy).
    double_conc = []
    for c in chars_all.values():
        if not isinstance(c, dict) or c.get("kind") not in ("player", "companion"):
            continue
        if not c.get("concentration"):
            continue  # not concentrating at end → nothing to check
        own_conc = [
            eff for eff in (c.get("active_effects") or [])
            if isinstance(eff, dict) and eff.get("concentration")
            and not eff.get("linked_to_concentration")
        ]
        if len(own_conc) > 1:
            eff_names = [eff.get("name", "?") for eff in own_conc]
            double_conc.append(
                f"{c.get('name', '?')} ends concentrating on {c.get('concentration')!r} but "
                f"carries {len(own_conc)} own concentration effects {eff_names} — a second "
                f"concentration spell should have dropped the first (drop_concentration)")
    if double_conc:
        chk("concentration_dropped_cleanly", False, "; ".join(double_conc), fatal=False)

    # UNRESOLVED_SPELL_ATTACK (#1270; WARN — graduate to FATAL after clean sweeps, mirroring
    # caster_has_spellbook). A DM-resolved attack-roll spell (Guiding Bolt, Fire Bolt cast via
    # cast_spell -> the result carries automated:false + attack_roll:true) leaves its to-hit for
    # the DM to make via attack(). If the SAME caster never made a same-turn attack() before the
    # next next_turn, the spell ate its slot and dealt ZERO — the exact sprint defect. Read the
    # ordered result stream so we can see the automated flag and pair the caster to the follow-up.
    # Conservative + NULL-GUARDED: only fires on an affirmatively-observed cast_spell result with
    # automated:false + attack_roll:true and NO matching attack() before the caster's turn ended.
    try:
        pairs = _tool_events(events)
    except Exception:  # defensive: never let the pairing break the gate
        pairs = []
    unresolved_spell_atk: list[str] = []
    # Track, per pending cast, the caster whose attack() would resolve it. A next_turn closes the
    # window (the leg is same-turn only, #1270); an attack() by that caster resolves it.
    pending_spell_casters: list[tuple[str, str]] = []  # (caster_id, spell_name)
    for short, inp, obj, is_err, _text in pairs:
        if short == "cast_spell" and not is_err and isinstance(obj, dict):
            if obj.get("automated") is False and obj.get("attack_roll") is True:
                caster = str(inp.get("character_id") or inp.get("caster_id") or "")
                pending_spell_casters.append((caster, str(obj.get("spell") or inp.get("spell_name") or "spell")))
        elif short == "attack" and not is_err:
            atk_by = str(inp.get("attacker_id") or inp.get("character_id") or "")
            # Resolve the OLDEST pending leg for this caster (a same-turn attack pairs to it).
            for i, (caster, _sp) in enumerate(pending_spell_casters):
                if caster and caster == atk_by:
                    pending_spell_casters.pop(i)
                    break
        elif short == "next_turn":
            # The turn ended: any still-pending spell-attack leg went unresolved this turn.
            for caster, sp in pending_spell_casters:
                unresolved_spell_atk.append(
                    f"{sp} cast by {caster or '?'} returned automated:false (DM must resolve the "
                    f"attack roll via attack()) but no same-turn attack() resolved it before "
                    f"next_turn — the spell ate its slot and dealt no damage")
            pending_spell_casters = []
    # Any legs still pending at end-of-run (no trailing next_turn) are also unresolved.
    for caster, sp in pending_spell_casters:
        unresolved_spell_atk.append(
            f"{sp} cast by {caster or '?'} returned automated:false but no attack() resolved it")
    if unresolved_spell_atk:
        chk("unresolved_spell_attack", False, "; ".join(unresolved_spell_atk), fatal=False)

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
