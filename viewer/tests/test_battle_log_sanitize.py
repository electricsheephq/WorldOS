"""SAT→7 (adversarial friction): the player-facing Battle Log must get the SAME read/projection
hygiene as the main Chronicle.

`BattleLogLine` (viewer/openworlds/screen-combat.jsx) renders one combat-log row. Before this fix it
emitted `l.title` and `l.text` RAW — so DM engine META-TEXT (a wrapper-progress line like "Momentum
carries through the scene…" / "Your choice takes hold…", a roll-result-summary header, or other
scaffolding) leaked into the combat log because the panel never routed through `sanitizeNarration`
(`grep "Battle" screen-table.jsx` returns 0 hits — the guard lived only in the Chronicle's screen).

The fix routes both rendered fields through the SAME window-exported `sanitizeNarration` the Chronicle
uses. These tests exercise the REAL shipped functions: they transpile the ACTUAL `screen-table.jsx`
(which publishes `window.sanitizeNarration`) AND `screen-combat.jsx` (which renders `BattleLogLine`)
with the bundled Babel into ONE shared sandbox — mirroring how the browser loads them as sibling
<script type='text/babel'> tags — then render each row and assert on its flattened visible TEXT.
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
SCREEN_COMBAT = OPENWORLDS / "screen-combat.jsx"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


def _render_battle_rows(rows: list[dict]) -> list[str]:
    """Transpile screen-table.jsx + screen-combat.jsx into one sandbox (so sanitizeNarration is
    published before BattleLogLine references it, exactly as the sibling browser <script> tags do),
    render each row, and return its flattened visible TEXT."""
    program = (
        "const fs = require('fs'); const vm = require('vm');\n"
        + "const Babel = require(%s);\n" % json.dumps(str(BABEL))
        + "function h(type, props, ...children){ return { type: (typeof type==='function'?(type.name||'C'):type),"
        + " props: props||{}, children: children.flat(Infinity).filter(c=>c!=null) }; }\n"
        + "const React = { useState:()=>[null,()=>{}], useRef:()=>({}), useCallback:f=>f, useEffect:()=>{},"
        + " createElement:h, Fragment:'F' };\n"
        # Cross-file refs screen-combat.jsx reaches for as globals (sibling-script model): stub them
        # inertly so they resolve and contribute no visible text.
        + "const Img = (props) => h('Img', props);\n"
        + "const sb = { React, Img, Array, Number, String, Boolean, Math, Object, JSON };\n"
        + "sb.window = sb; sb.OpenWorldsIcon = undefined;\n"
        + "vm.createContext(sb);\n"
        + "function load(p){ const src = fs.readFileSync(p, 'utf8');"
        + " const code = Babel.transform(src, { presets: ['react'], filename: p }).code;"
        + " vm.runInContext(code, sb); }\n"
        + "load(%s);\n" % json.dumps(str(SCREEN_TABLE))
        + "load(%s);\n" % json.dumps(str(SCREEN_COMBAT))
        + "if (typeof sb.window.sanitizeNarration !== 'function') throw new Error('sanitizeNarration not published');\n"
        + "const BattleLogLine = sb.window.BattleLogLine;\n"
        + "if (typeof BattleLogLine !== 'function') throw new Error('BattleLogLine not exported on window');\n"
        + "function textOf(n){ if(n==null) return ''; if(typeof n==='string'||typeof n==='number') return String(n);"
        + " if(Array.isArray(n)) return n.map(textOf).join(''); if(n.children) return n.children.map(textOf).join('');"
        + " return ''; }\n"
        + "const rows = " + json.dumps(rows) + ";\n"
        + "const out = rows.map((l) => textOf(BattleLogLine({ l })));\n"
        + "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_battle_log_strips_wrapper_progress_meta_text():
    # The verbatim adversarial leak: a wrapper-progress placeholder rode into the combat log as a row's
    # text. The Battle Log must strip it exactly as the Chronicle does — leaving only the real label.
    (momentum, choice) = _render_battle_rows([
        {"title": "Combat", "text": "Momentum carries through the scene; consequences are beginning to surface."},
        {"event": "turn", "text": "Your choice takes hold; nearby voices, risks, and consequences begin to answer."},
    ])
    assert "Momentum carries through" not in momentum
    assert "consequences are beginning to surface" not in momentum
    assert "Combat" in momentum  # the title label survives
    assert "Your choice takes hold" not in choice
    assert "turn" in choice  # the event label survives


def test_battle_log_strips_roll_result_summary_and_rule():
    # A roll-summary header + trailing horizontal rule must not leak into the combat log either.
    (row,) = _render_battle_rows([
        {"title": "Skirmish", "text": "The intimidation lands at 18; the quiet interpose at 16. ---"},
    ])
    assert "lands at 18" not in row.lower()
    assert "interpose at 16" not in row.lower()
    assert "---" not in row
    assert "Skirmish" in row


def test_battle_log_strips_scaffolding_in_title():
    # A scaffolding stage-direction that landed in the TITLE is stripped; the title falls back to the
    # event label / "Combat", and the real text survives.
    (row,) = _render_battle_rows([
        {"title": "Beat complete", "event": "attack", "text": "The orc falls, axe clattering to the stone."},
    ])
    assert "beat complete" not in row.lower()
    assert "The orc falls" in row


def test_battle_log_preserves_real_combat_text_and_meta():
    # The value of the surgical strip: genuine combat narration + the engine title + the dice-meta badge
    # all survive untouched. Story/mechanics quality is the north star — never eat real combat prose.
    (row,) = _render_battle_rows([
        {
            "title": "Tav -> Goblin Sapper",
            "text": "Tav strikes the sapper, steel biting deep.",
            "meta": [{"label": "d20", "value": 13}, {"label": "dmg", "value": 6}],
        },
    ])
    assert "Tav -> Goblin Sapper" in row
    assert "Tav strikes the sapper" in row
    assert "d20" in row and "13" in row
    assert "dmg" in row and "6" in row


def test_battle_log_empty_state_row_is_clean():
    # A row with only an event label and clean text renders the label + the prose, no leakage.
    (row,) = _render_battle_rows([{"event": "move", "text": "Renn advances to the broken cart."}])
    assert "move" in row
    assert "Renn advances to the broken cart" in row
