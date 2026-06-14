"""#752 (dedup + meta-text legs) — the chronicle must show each beat ONCE and must not leak the
engine's meta-text transition phrases between player beats.

From the 2026-06-15 confirm sweep (adversarial persona):
  • "Chronicle shows DUPLICATE DM narration for the opening beat (same scene narration)"
  • "Engine META-TEXT transition phrases LEAK into the chronicle between player beats"
  • (confirmed bug ndjson) "All Enter-submitted player actions echoed twice in chronicle — second
    entry is raw You\"…\" template text" / "Continue button leaks internal template text:
    Rolan—Continue You\"continue\""

These exercise the REAL shipped pure functions transpiled from the actual `.jsx` under Node:
  • buildChronicleLog (screen-table.jsx) — the merge/dedup/order of the chronicle's three sources.
    A player move's OPTIMISTIC echo (the `log` band) and its `/chat` REPLAY (the `chatBeats` band)
    are the SAME turn; they must collapse to ONE row even when the replay returns as a `dialog`
    "You" row (engine logged the line with no routing tag) rather than an `action` row.
  • sanitizeNarration (screen-table.jsx) — the player-facing prose filter. The engine/wrapper
    meta-text "transition" phrases (the wrapper progress heartbeats #749 + the inter-beat
    scene-transition scaffolding) must NEVER render as story prose.

Skipped where Node is not on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OPENWORLDS = HERE.parent / "openworlds"
SCREEN_TABLE = OPENWORLDS / "screen-table.jsx"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


def _run_screen_table(expr_body: str):
    """Transpile screen-table.jsx under Node (window scope set up exactly as the browser loads it)
    and return whatever `expr_body` writes to stdout as JSON. The window-guarded helpers
    (buildChronicleLog, sanitizeNarration, isWrapperProgressLine, WRAPPER_PROGRESS_LINES) are all
    reachable off `sb.window` because the module installs them on `window` at load."""
    program = (
        "const fs = require('fs'); const vm = require('vm');\n"
        + "const Babel = require(%s);\n" % json.dumps(str(BABEL))
        + "const src = fs.readFileSync(%s, 'utf8');\n" % json.dumps(str(SCREEN_TABLE))
        + "const code = Babel.transform(src, { presets: ['react'], filename: 'screen-table.jsx' }).code;\n"
        + "function h(type, props, ...children){ return { type:(typeof type==='function'?(type.name||'C'):type),"
        + " props: props||{}, children: children.flat(Infinity).filter(c=>c!=null) }; }\n"
        + "const React = { useState:()=>[null,()=>{}], useRef:()=>({}), useCallback:f=>f, useEffect:()=>{},"
        + " createElement:h, Fragment:'F' };\n"
        + "const sb = { React, console }; sb.window = sb; vm.createContext(sb); vm.runInContext(code, sb);\n"
        + expr_body
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def _chronicle_texts(recent_events, chat_beats, log):
    """Run the REAL buildChronicleLog and return the flattened visible text of each merged row,
    tagged with its kind, so a test can assert a beat appears exactly once."""
    return _run_screen_table(
        "const buildChronicleLog = sb.window.buildChronicleLog;\n"
        "if (typeof buildChronicleLog !== 'function') throw new Error('buildChronicleLog not exported');\n"
        "const rows = buildChronicleLog(" + json.dumps(recent_events) + ", "
        + json.dumps(chat_beats) + ", " + json.dumps(log) + ");\n"
        "const out = rows.map((r) => ({ kind: r.kind || 'narration', who: (r.who||''), text: (r.text||'') }));\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )


# --------------------------------------------------------------------------------------------- #
# DEDUP — the opening beat / player echo appears exactly once.
# --------------------------------------------------------------------------------------------- #
def test_opening_dm_narration_appears_once_across_recent_and_live_bands():
    # The opening DM beat lands in BOTH the leading history band (recentEvents, seq=0) AND the live
    # /events tail (orderSeq "sid:0") — the same session-log line. It must render ONCE.
    opening = "The lantern gutters as you step into the Lower City."
    rows = _chronicle_texts(
        recent_events=[{"kind": "narration", "text": opening, "seq": 0, "sid": "s1", "eventAt": 1.0}],
        chat_beats=[{"kind": "narration", "text": opening, "orderSeq": "s1:0", "at": 1, "eventAt": 1.0}],
        log=[],
    )
    narration_rows = [r for r in rows if opening in r["text"]]
    assert len(narration_rows) == 1, f"opening DM narration must render ONCE, got {len(narration_rows)}: {rows}"


def test_continue_action_does_not_double_echo_as_dialog_you_row():
    # The confirmed bug: a Continue move shows as BOTH the optimistic action echo ("Continue") AND a
    # raw `dialog` "You" replay (rendered You"continue"). The /chat replay comes back as a dialog row
    # (engine logged the line with no routing tag). It must dedup against the optimistic echo → ONE row.
    optimistic = {"kind": "action", "who": "You", "text": "Continue", "route": "continue", "at": 1, "eventAt": 1.0}
    chat_replay_dialog = {"kind": "dialog", "who": "You", "text": "continue", "route": "", "at": 2, "eventAt": 1.1}
    rows = _chronicle_texts(recent_events=[], chat_beats=[chat_replay_dialog], log=[optimistic])
    you_rows = [r for r in rows if r["text"].strip().lower() in ("continue",)]
    assert len(you_rows) == 1, f"the Continue move must render ONCE, got {len(you_rows)}: {rows}"
    # and it must NOT be the raw dialog "You\"continue\"" template — it should be the clean action.
    dialog_you = [r for r in rows if r["kind"] == "dialog" and r["who"].lower() == "you"]
    assert not dialog_you, f"the raw You\"continue\" dialog template must not survive: {dialog_you}"


def test_freetext_action_does_not_double_echo_across_optimistic_and_chat_replay():
    # A free-text "do" action: optimistic echo (action) + /chat replay (the engine logged "[do] …").
    # The replay parses the tag back to an action; it must dedup against the optimistic echo.
    text = "I draw my staff and step toward the alley mouth"
    optimistic = {"kind": "action", "who": "You", "text": text, "route": "do", "at": 1, "eventAt": 1.0}
    chat_replay = {"kind": "action", "who": "You", "text": text, "route": "do", "at": 2, "eventAt": 1.1}
    rows = _chronicle_texts(recent_events=[], chat_beats=[chat_replay], log=[optimistic])
    matches = [r for r in rows if text in r["text"]]
    assert len(matches) == 1, f"a free-text action must render ONCE, got {len(matches)}: {rows}"


# --------------------------------------------------------------------------------------------- #
# META-TEXT LEAK — the engine/wrapper transition phrases never render as story prose.
# --------------------------------------------------------------------------------------------- #
def _sanitize(text: str) -> str:
    return _run_screen_table(
        "const s = sb.window.sanitizeNarration; if (typeof s !== 'function') throw new Error('sanitizeNarration not exported');\n"
        "process.stdout.write(JSON.stringify(s(" + json.dumps(text) + ")));\n"
    )


def test_wrapper_progress_transition_phrases_are_suppressed():
    # The wrapper progress heartbeats (#749) are LIVENESS meta-text, never story. Each must be
    # stripped from the player-facing chronicle entirely.
    lines = _run_screen_table(
        "process.stdout.write(JSON.stringify(sb.window.WRAPPER_PROGRESS_LINES || []));\n"
    )
    assert lines, "WRAPPER_PROGRESS_LINES must be exported (the meta-text transition phrases)"
    for phrase in lines:
        assert _sanitize(phrase) == "", f"wrapper transition phrase leaked into the chronicle: {phrase!r}"


@pytest.mark.parametrize("phrase", [
    "Time passes between the beats.",
    "Transitioning to the next scene.",
    "Moving on to the next beat.",
    "We now move to the next part of the story.",
    "Scene transition.",
    "End of beat.",
    "Beginning the next beat.",
])
def test_inter_beat_transition_scaffolding_is_suppressed(phrase):
    # The adversarial leak: engine/DM meta-text "transition" stage-directions between player beats
    # ("Transitioning to the next scene", "Moving on to the next beat", …). These are scaffolding —
    # the player must see story prose, never the seams between beats. A WHOLE-LINE transition note
    # is dropped; real prose around it survives (asserted below).
    assert _sanitize(phrase).strip() == "", f"inter-beat transition meta-text leaked: {phrase!r}"


def test_transition_scaffolding_drop_preserves_surrounding_real_prose():
    # The transition strip must be surgical: a real narration line that happens to sit beside a
    # transition note keeps its prose; only the meta-text seam is removed.
    beat = (
        "Zevlor meets your eyes and gives a slow nod.\n"
        "Moving on to the next beat.\n"
        "The gate groans open onto the rain."
    )
    out = _sanitize(beat)
    assert "Zevlor meets your eyes" in out, f"real prose was over-stripped: {out!r}"
    assert "The gate groans open" in out, f"real prose was over-stripped: {out!r}"
    assert "next beat" not in out.lower(), f"transition meta-text survived: {out!r}"


def test_real_prose_mentioning_a_beat_or_transition_survives():
    # The guard must be HIGH-CONFIDENCE: legitimate fiction that merely uses the words "beat",
    # "scene", or "transition" in-world must NOT be stripped (a war-drum's beat, a tavern scene).
    for prose in (
        "Your heart skips a beat as the blade whistles past.",
        "The tavern scene is loud with laughter and spilled ale.",
        "She makes a smooth transition from the parapet to the rope.",
        # The over-strip class the first cut missed (adversarial review): "act"/"chapter"/"part"
        # are real-fiction words, and the "end/close of the <struct>" form is descriptive prose
        # when the struct sits mid-sentence (trailing prose follows) — must NOT be stripped.
        "The end of the act left the audience breathless.",
        "By the close of the scene, three lay dead on the cathedral steps.",
        "Start of the chapter that defined her, though she did not yet know it.",
        "Beginning the act of contrition, the old priest knelt in the ash.",
        "He took up his part of the story and carried it to the gate.",
    ):
        out = _sanitize(prose)
        assert out.strip() == prose.strip(), f"real in-world prose was wrongly stripped: {prose!r} -> {out!r}"
