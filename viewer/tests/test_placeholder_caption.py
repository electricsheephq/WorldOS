"""#1764 (A): the art-missing placeholder must never print the generator's brief at the player.

`Placeholder`'s `label` prop is authored as an ART-DIRECTION BRIEF for the image generator
("cover illustration · 16:9 · painted hero scene") and doubles as the <img> alt text — and the
shipped component rendered it VERBATIM inside the empty frame. The agent playtest caught five
visible .ph-label spans on the launcher/openworlds screens and the player read them as UI copy:
"There's a note where the picture should be. It says cover illustration, 16 by 9, painted hero
scene."

These tests render the REAL shipped `Placeholder` out of chrome.jsx (transpiled with the bundled
Babel, executed under node's vm — the test_dialogue_npc_header harness) and assert:

  • the visible caption is a short NEUTRAL slot name, never the brief;
  • no art-direction token (aspect ratio, "painted", "illustration", "vignette") survives to
    the screen;
  • the brief is still carried on the element as `data-art-brief`, so generators and QA keep
    the art direction they need.

Skipped if node is not on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OPENWORLDS = HERE.parent / "openworlds"
CHROME = OPENWORLDS / "chrome.jsx"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"

# The exact briefs the playtest saw rendered as visible captions (issue #1764), plus the
# aspect-ratio/direction-laden cover brief that produced the player quote.
PLAYTEST_BRIEFS = [
    "cover illustration · 16:9 · painted hero scene",
    "vignette · crypt",
    "scene · crypt",
    "sketch · last scene",
    "seal",
    "scene · battlefield",
    "chronicle · a bloodied slate carried into the dark",
]

# Words that only ever come from art direction — none may reach a player-visible caption.
ART_DIRECTION_TOKENS = ["16:9", "painted", "illustration", "vignette", "4:3", "hero scene"]


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


def _render_placeholders(briefs: list[str]) -> list[dict]:
    """Render the real <Placeholder label=…> for each brief; return {caption, brief_attr, props}."""
    program = (
        "const fs = require('fs'); const vm = require('vm');\n"
        + "const Babel = require(%s);\n" % json.dumps(str(BABEL))
        + "const src = fs.readFileSync(%s, 'utf8');\n" % json.dumps(str(CHROME))
        + "const code = Babel.transform(src, { presets: ['react'], filename: 'chrome.jsx' }).code;\n"
        + "function h(type, props, ...children){ return { type: (typeof type==='function'?(type.name||'C'):type),"
        + " props: props||{}, children: children.flat(Infinity).filter(c=>c!=null) }; }\n"
        + "const React = { useState:(v)=>[typeof v==='function'?v():v,()=>{}], useRef:()=>({current:null}),"
        + " useCallback:(f)=>f, useEffect:()=>{}, createElement:h, Fragment:'F' };\n"
        + "const sb = { React, document:{ addEventListener(){}, removeEventListener(){} } };\n"
        + "sb.window = sb;\n"
        + "vm.createContext(sb); vm.runInContext(code, sb);\n"
        + "const Placeholder = sb.window.Placeholder;\n"
        + "if (typeof Placeholder !== 'function') throw new Error('Placeholder not exported on window');\n"
        + "function textOf(n){ if(n==null) return ''; if(typeof n==='string'||typeof n==='number') return String(n);"
        + " if(Array.isArray(n)) return n.map(textOf).join(''); let s='';"
        + " if(n.children) s+=n.children.map(textOf).join(''); return s; }\n"
        + "const briefs = " + json.dumps(briefs) + ";\n"
        + "const out = briefs.map((label) => { const tree = Placeholder({ label, w: 100, h: 100, framed: true });\n"
        + "  return { caption: textOf(tree), props: JSON.stringify(tree.props || {}) }; });\n"
        + "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def test_1764_caption_is_never_the_brief():
    """No rendered caption may repeat the art-direction brief it was fed."""
    rendered = _render_placeholders(PLAYTEST_BRIEFS)
    assert len(rendered) == len(PLAYTEST_BRIEFS)
    for brief, out in zip(PLAYTEST_BRIEFS, rendered):
        caption = out["caption"]
        if "\u00b7" not in brief:
            # A one-word brief ("seal") is already the slot's display name — nothing to leak.
            continue
        # A multi-part brief is art direction. Replaying it — whole or in part, and regardless of
        # casing (.ph-label is lowercased by CSS, so case is no defence) — is the shipped defect.
        assert brief.lower() not in caption.lower(), (
            f"the generator brief {brief!r} is printed at the player as {caption!r} (#1764)"
        )
        assert caption.strip().lower() != brief.strip().lower(), (
            f"the caption is still the brief, only re-cased: {caption!r}"
        )


def test_1764_caption_carries_no_art_direction_tokens():
    """Aspect ratios and direction words ('painted hero scene') never reach the screen."""
    rendered = _render_placeholders(PLAYTEST_BRIEFS)
    for brief, out in zip(PLAYTEST_BRIEFS, rendered):
        low = out["caption"].lower()
        for token in ART_DIRECTION_TOKENS:
            assert token not in low, (
                f"art-direction token {token!r} leaked into the caption {out['caption']!r} "
                f"(from brief {brief!r})"
            )


def test_1764_caption_is_a_short_neutral_slot_name():
    """The caption reads as a slot name ('Scene art · Crypt'), not prose."""
    rendered = _render_placeholders(PLAYTEST_BRIEFS)
    by_brief = dict(zip(PLAYTEST_BRIEFS, (r["caption"] for r in rendered)))
    assert by_brief["cover illustration · 16:9 · painted hero scene"] == "Cover art"
    assert by_brief["vignette · crypt"] == "Scene art · Crypt"
    assert by_brief["scene · crypt"] == "Scene art · Crypt"
    assert by_brief["sketch \u00b7 last scene"] == "Sketch"
    assert by_brief["seal"] == "Seal"
    for caption in by_brief.values():
        assert caption, "an art slot must still be captioned (not blank)"
        assert len(caption) <= 32, f"caption too long to be a slot name: {caption!r}"


def test_1764_brief_survives_as_a_data_attribute_for_generators():
    """The brief is not lost — it stays on the element for the image generators."""
    rendered = _render_placeholders(PLAYTEST_BRIEFS)
    for brief, out in zip(PLAYTEST_BRIEFS, rendered):
        assert brief in out["props"], (
            f"brief {brief!r} must remain available to generators (data-art-brief); props={out['props']}"
        )
