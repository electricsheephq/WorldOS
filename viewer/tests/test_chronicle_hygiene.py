"""Chronicle-hygiene behavior tests for the player-facing story scroll (#731 + #732).

These exercise the REAL shipped browser functions (no reimplementation):

  • #731 (XSS partial-sanitization): `neutralizeMarkup` (viewer/openworlds/app.jsx) is the
    guard that cleans player free-text BEFORE it is echoed into the chronicle / sent to the DM.
    The v1.0.4-rc1 adversarial RRI found `<script>alert(1)</script>` had its TAGS stripped but
    the INNER TEXT ("alert(1)") leaked through and rendered as a player action — an
    injection/spoofing surface. The guard must strip dangerous tag *bodies* (script/style/etc.)
    so nothing of the payload survives, and HTML-escape is moot because the result must be inert
    TEXT either way.

  • #732 (chronicle metadata leak): `LogEntry` (viewer/openworlds/screen-table.jsx) renders one
    chronicle row. A `recentEvents` history-band row carries the engine's INTERNAL session-log
    `kind` ("narration" | "dialogue" | "roll" | "system" | "combat"). Only narration/action/roll/
    dialog had branches, so a `dialogue`/`combat` row fell to the default branch and rendered the
    raw kind STRING ("dialogue"/"combat") as an uppercase label, with its text NOT passed through
    sanitizeNarration (so scaffolding leaked too). And a player action rendered "You—…", a
    formatting artifact that reads like DM narration. The chronicle must render clean prose only:
    never an internal kind label, never the "You—" artifact, and the dialogue path must sanitize.

Both functions live in browser JS; mirroring the sibling JS-behavior tests we transpile/eval the
ACTUAL `.jsx` under Node so the test tracks shipped behavior. Skipped if Node is not on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OPENWORLDS = HERE.parent / "openworlds"
APP_JSX = OPENWORLDS / "app.jsx"
SCREEN_TABLE = OPENWORLDS / "screen-table.jsx"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


# --------------------------------------------------------------------------------------------- #
# #731 — neutralizeMarkup: strip dangerous tag BODIES, never leak inner text into the chronicle.
# --------------------------------------------------------------------------------------------- #
def _extract_fn(src: str, name: str) -> str:
    marker = f"function {name}("
    start = src.index(marker)
    depth = 0
    for i in range(start, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"could not brace-match {name}()")


def _neutralize(text) -> str:
    src = APP_JSX.read_text(encoding="utf-8")
    fn = _extract_fn(src, "neutralizeMarkup")
    snippet = (
        fn
        + "\nconst __t = "
        + json.dumps(text)
        + ";\nprocess.stdout.write(String(neutralizeMarkup(__t)));\n"
    )
    proc = subprocess.run([_node(), "-e", snippet], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return proc.stdout


def test_731_script_payload_inner_text_does_not_leak():
    # The exact RRI move: a <script> tag is no longer enough — the BODY must be gone, not just
    # the tags. Previously this returned "alert(1)" (the inner text rode through as a player action).
    out = _neutralize("<script>alert(1)</script>")
    assert "alert" not in out
    assert out.strip() == ""


def test_731_script_body_does_not_ride_along_with_real_prose():
    # A payload glued to a legit action must drop the payload while keeping the action prose.
    out = _neutralize("<script>document.cookie</script>I draw my sword")
    assert "document.cookie" not in out
    assert "cookie" not in out
    assert "I draw my sword" in out


@pytest.mark.parametrize("tag", ["script", "style", "iframe", "object", "embed", "svg", "math", "template"])
def test_731_dangerous_tag_bodies_are_excised(tag):
    out = _neutralize(f"<{tag}>PAYLOAD_{tag}</{tag}>safe text")
    assert f"PAYLOAD_{tag}" not in out
    assert "PAYLOAD" not in out
    assert "safe text" in out


def test_731_event_handler_attribute_vector_is_inert():
    # An <img onerror=…> never had a body, but the attribute payload must not survive as text.
    out = _neutralize("<img src=x onerror=alert(1)>")
    assert "alert" not in out
    assert "onerror" not in out


def test_731_ordinary_emphasis_prose_is_preserved_as_text():
    # The fix must NOT over-strip: a benign inline tag around real words keeps the words (the tag
    # itself is removed, the prose reads clean). This is a player typing markup by habit, not an attack.
    out = _neutralize("I say <b>hello</b> to the guard")
    assert "<b>" not in out and "</b>" not in out
    assert "hello" in out
    assert "I say" in out and "to the guard" in out


def test_731_case_and_whitespace_variants_of_script_are_stripped():
    for raw in (
        "<SCRIPT>alert(1)</SCRIPT>",
        "<script >alert(2)</script >",
        "<script\ntype='text/javascript'>alert(3)</script>",
    ):
        out = _neutralize(raw)
        assert "alert" not in out, f"leaked from: {raw!r} -> {out!r}"


# --------------------------------------------------------------------------------------------- #
# #732 — LogEntry: no internal kind label, no "You—" artifact, dialogue text is sanitized.
# --------------------------------------------------------------------------------------------- #
def _render_log_entries(entries: list[dict]) -> list[str]:
    """Transpile screen-table.jsx with the bundled Babel, capture each LogEntry's createElement
    tree, and return the flattened visible TEXT of each rendered row."""
    program = (
        "const fs = require('fs'); const vm = require('vm');\n"
        + "const Babel = require(%s);\n" % json.dumps(str(BABEL))
        + "const src = fs.readFileSync(%s, 'utf8');\n" % json.dumps(str(SCREEN_TABLE))
        + "const code = Babel.transform(src, { presets: ['react'], filename: 'screen-table.jsx' }).code;\n"
        + "function h(type, props, ...children){ return { type: (typeof type==='function'?(type.name||'C'):type),"
        + " props: props||{}, children: children.flat(Infinity).filter(c=>c!=null) }; }\n"
        + "const React = { useState:()=>[null,()=>{}], useRef:()=>({}), useCallback:f=>f, useEffect:()=>{},"
        + " createElement:h, Fragment:'F' };\n"
        + "const sb = { React }; sb.window = sb; vm.createContext(sb); vm.runInContext(code, sb);\n"
        + "const LogEntry = sb.window.LogEntry;\n"
        + "if (typeof LogEntry !== 'function') throw new Error('LogEntry not exported on window');\n"
        + "function textOf(n){ if(n==null) return ''; if(typeof n==='string'||typeof n==='number') return String(n);"
        + " if(Array.isArray(n)) return n.map(textOf).join(''); if(n.children) return n.children.map(textOf).join('');"
        + " return ''; }\n"
        + "const entries = " + json.dumps(entries) + ";\n"
        + "const out = entries.map((e) => textOf(LogEntry({ entry: e })));\n"
        + "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_732_dialogue_row_does_not_surface_its_kind_label():
    # A recentEvents dialogue row with NO speaker label previously rendered the raw kind string
    # "dialogue" as an uppercase label. The player must never see the internal kind name.
    (rendered,) = _render_log_entries([{"kind": "dialogue", "text": '"Hold there."'}])
    assert "dialogue" not in rendered.lower()
    assert "Hold there" in rendered  # the in-world line survives


def test_732_combat_row_does_not_surface_its_kind_label():
    (rendered,) = _render_log_entries([{"kind": "combat", "text": "A blade flashes."}])
    assert "combat" not in rendered.lower()
    assert "A blade flashes" in rendered


def test_732_unknown_internal_kind_never_renders_its_raw_name():
    # Any internal/scaffolding kind name (e.g. a prelude "meeting"/"arrival" note or a future kind)
    # must not leak as a visible label.
    for kind in ("narration", "dialogue", "combat", "began", "meeting", "arrival", "faction_move"):
        (rendered,) = _render_log_entries([{"kind": kind, "text": "The world turns."}])
        assert kind.lower() not in rendered.lower(), f"kind label leaked for {kind!r}: {rendered!r}"
        assert "The world turns" in rendered


def test_732_player_action_has_no_you_dash_artifact():
    # A player action row rendered "You—I draw my sword." — the "You—" prefix reads like DM
    # narration scaffolding. The player's own action must render as clean prose with no "You—" artifact.
    (rendered,) = _render_log_entries([{"kind": "action", "who": "You", "text": "I draw my sword."}])
    assert "You—" not in rendered
    assert "I draw my sword" in rendered


def test_732_dialogue_text_is_sanitized_like_narration():
    # The dialogue path must run sanitizeNarration so story-craft scaffolding can't ride a
    # dialogue-kind row into the chronicle (the narration path already strips this — #347).
    (rendered,) = _render_log_entries(
        [{"kind": "dialogue", "text": "The lock holds after three failed social checks.", "label": "DM"}]
    )
    assert "social checks" not in rendered.lower()
    assert "The lock holds" in rendered


def test_732_narration_row_is_unchanged_clean_prose():
    # Regression guard: an ordinary narration row still renders its prose, no label, no artifact.
    (rendered,) = _render_log_entries([{"kind": "narration", "text": "Rain gathers on the cobbles."}])
    assert rendered == "Rain gathers on the cobbles."
