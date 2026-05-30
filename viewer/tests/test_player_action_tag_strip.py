"""Tests for stripRoutingTag() (#410) — the viewer-side guard that removes the
internal write-lane routing tag ("[do] ", "[say] ", "[check] ", …) from the
player's line BEFORE it is shown in the Chronicle.

The newbie playtest saw the player's own action rendered as
``"[do] Without making it obvious…"``: the optimistic echo already stripped the
tag, but the /chat replay of the player's *logged* line (which the engine keeps
tagged for move classification) rendered ``it.text`` verbatim, so the tag leaked
once the line round-tripped. Both player-line render paths now share this helper.

The function lives in app.jsx (browser JS), so — mirroring test_sanitize_narration —
we brace-match its source out of app.jsx and run it under Node. Skipped if Node is
not on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
APP_JSX = HERE.parent / "openworlds" / "app.jsx"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


def _run_js(snippet: str) -> str:
    proc = subprocess.run([_node(), "-e", snippet], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return proc.stdout


def _strip_fn_source() -> str:
    """Pull `stripRoutingTag`'s source out of app.jsx by brace-matching from
    `function stripRoutingTag(` to its closing brace (same approach as
    test_sanitize_narration._strip_fn_source)."""
    src = APP_JSX.read_text(encoding="utf-8")
    marker = "function stripRoutingTag("
    assert marker in src, "stripRoutingTag must be defined in app.jsx (#410)"
    start = src.index(marker)
    depth = 0
    i = start
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1
    raise AssertionError("could not brace-match stripRoutingTag()")


def _strip(text) -> str:
    snippet = (
        _strip_fn_source()
        + "\nconst __t = "
        + json.dumps(text)
        + ";\nprocess.stdout.write(String(stripRoutingTag(__t)));\n"
    )
    return _run_js(snippet)


def test_defined_in_app_jsx_and_registered_on_window():
    # The definition must exist (this is also the latent-bug guard) and be exposed
    # on window so the table screen + devtools can reach it.
    src = APP_JSX.read_text(encoding="utf-8")
    assert "function stripRoutingTag(" in src
    # Registered as a window-guarded global (mirrors neutralizeMarkup) so the table
    # screen + these tests can reach it.
    assert "window.stripRoutingTag" in src


def test_strips_the_reported_do_tag():
    # The exact class of string the #324 newbie saw in the chronicle.
    assert _strip("[do] Without making it obvious, I scan the room.") == (
        "Without making it obvious, I scan the room."
    )


@pytest.mark.parametrize(
    "verb", ["say", "do", "check", "save", "continue", "attack", "cast", "use_item", "clarify"]
)
def test_strips_every_write_lane_verb(verb):
    # Mirrors viewer/server.py _MOVE_KINDS — every tag the write lane can prepend.
    assert _strip(f"[{verb}] hello there") == "hello there"


def test_case_insensitive_and_no_space_after_tag():
    assert _strip("[DO]opens the door") == "opens the door"
    assert _strip("[Say]   wait for the guard") == "wait for the guard"


def test_leaves_untagged_text_and_mid_line_brackets_untouched():
    assert _strip("opens the door quietly") == "opens the door quietly"
    assert _strip("I shout [for help] down the hall") == "I shout [for help] down the hall"


def test_does_not_strip_a_non_routing_bracket_token():
    # Only the known routing verbs are stripped; a stray "[note]" stays as authored.
    assert _strip("[note] keep this verbatim") == "[note] keep this verbatim"


def test_is_null_and_undefined_safe():
    assert _strip(None) == ""
    # JSON has no `undefined`; exercise the JS path directly.
    out = _run_js(
        _strip_fn_source()
        + "\nprocess.stdout.write(String(stripRoutingTag(undefined)));\n"
    )
    assert out == ""
