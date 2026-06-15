"""Component-behavior tests for the Parley (Dialogue) screen header + disposition meter.

Two felt v1.0.4 gaps, exercised against the REAL shipped `ParleyMenu` component (no
reimplementation) by transpiling screen-dialogue.jsx with the bundled Babel and capturing
its createElement tree under Node (mirrors test_chronicle_hygiene._render_log_entries):

  • #751 — the Parley header read "Speaking with <PLAYER>": it named `surface.actor` (the
    lead PC) instead of the NPC the party is talking TO. When the surface carries an `npc`
    block, the header must name THAT NPC and the left portrait/label must be the NPC — pinned
    to the bound id for the whole interaction. With no `npc` block the header keeps today's
    behavior (the actor), so the no-target freeform parley is unchanged.

  • #615 — a live disposition read on the Dialogue screen: when the surface carries an `npc`
    block with a `disposition` band (reusing the engine-side _attitude_disposition) the screen
    renders a DispositionDot meter so the player sees the NPC's current standing while talking.

Skipped if Node is not on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
OPENWORLDS = HERE.parent / "openworlds"
SCREEN_DIALOGUE = OPENWORLDS / "screen-dialogue.jsx"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


def _render_parley(surface: dict) -> dict:
    """Transpile screen-dialogue.jsx, render ParleyMenu(surface) with a captured createElement
    tree, and return {"text": <flattened visible text>, "components": [<component names used>]}.

    Bare capitalized JSX identifiers the component reaches for (Img, Panel, DispositionDot,
    Divider, …) are stubbed in the sandbox as passthrough components that simply render their
    children, so their NAME is recorded (lets us assert DispositionDot was used) and any text
    props (label/d) are captured. window.Tooltip/slug/useToast are stubbed likewise."""
    program = (
        "const fs = require('fs'); const vm = require('vm');\n"
        + "const Babel = require(%s);\n" % json.dumps(str(BABEL))
        + "const src = fs.readFileSync(%s, 'utf8');\n" % json.dumps(str(SCREEN_DIALOGUE))
        + "const code = Babel.transform(src, { presets: ['react'], filename: 'screen-dialogue.jsx' }).code;\n"
        + "function h(type, props, ...children){ return { type: (typeof type==='function'?(type.name||'C'):type),"
        + " props: props||{}, children: children.flat(Infinity).filter(c=>c!=null) }; }\n"
        + "const React = { useState:(v)=>[typeof v==='function'?v():v,()=>{}], useRef:()=>({current:false}),"
        + " useCallback:(f)=>f, useEffect:()=>{}, createElement:h, Fragment:'F' };\n"
        + "const sb = { React, document:{ addEventListener(){}, removeEventListener(){}, visibilityState:'visible' },"
        + " fetch:()=>Promise.resolve({ ok:true, json:()=>Promise.resolve({}) }) };\n"
        + "sb.window = sb;\n"
        # Passthrough stubs for the components/helpers ParleyMenu reaches for. Each renders its
        # children so their text survives; their .name is captured by h() above.
        + "function Img(){ return h('Img', arguments[0]); }\n"
        + "function Panel(p){ return h('Panel', p, p&&p.children); }\n"
        + "function Divider(){ return h('Divider', {}); }\n"
        + "function DispositionDot(p){ return h('DispositionDot', p); }\n"
        + "sb.Img = Img; sb.Panel = Panel; sb.Divider = Divider; sb.DispositionDot = DispositionDot;\n"
        + "sb.window.Tooltip = function Tooltip(p){ return h('Tooltip', p, p&&p.children); };\n"
        + "sb.window.slug = (s)=>String(s||'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');\n"
        + "sb.window.useToast = ()=>(()=>{});\n"
        + "sb.window.combatSurfaceFromCampaign = ()=>'';\n"
        + "vm.createContext(sb); vm.runInContext(code, sb);\n"
        + "const ParleyMenu = sb.window.ParleyMenu;\n"
        + "if (typeof ParleyMenu !== 'function') throw new Error('ParleyMenu not exported on window');\n"
        + "function textOf(n){ if(n==null) return ''; if(typeof n==='string'||typeof n==='number') return String(n);"
        + " if(Array.isArray(n)) return n.map(textOf).join(''); let s='';"
        + " if(n.props){ if(typeof n.props.label==='string') s+=' '+n.props.label; if(typeof n.props.d==='string') s+=' '+n.props.d; }"
        + " if(n.children) s+=n.children.map(textOf).join(''); return s; }\n"
        + "const used = [];\n"
        + "function collect(n){ if(n==null||typeof n!=='object') return; if(typeof n.type==='string') used.push(n.type);"
        + " if(Array.isArray(n.children)) n.children.forEach(collect); }\n"
        + "const surface = " + json.dumps(surface) + ";\n"
        + "const tree = ParleyMenu({ surface, slots: surface.skills||[], difficulty:'medium',"
        + " setDifficulty:()=>{}, history:[], setHistory:()=>{}, onNavigate:()=>{}, toast:()=>{} });\n"
        + "collect(tree);\n"
        + "process.stdout.write(JSON.stringify({ text: textOf(tree), components: used }));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


_BASE = {
    "campaign_id": "camp_marches",
    "title": "The Long Road",
    "dayLabel": "Day 12",
    "actor": "Cassian Frostbreaker",
    "actor_id": "cassian",
    "alignment": "Neutral Good",
    "location_id": "lanternrest",
    "imageScope": "location:lanternrest",
    "free_form": True,
    "can_act": False,
    "skills": [
        {"skill": "persuasion", "label": "Persuasion", "modifier": 8, "suggested_dc": 14,
         "proficient": True, "expertise": True, "core": True},
    ],
}


def _with_npc(**npc):
    s = json.loads(json.dumps(_BASE))
    s["npc"] = {"id": "olwen", "name": "Toll-keeper Olwen", "met": True,
                "attitude": "guarded", "attitude_value": -10, "disposition": "cool"}
    s["npc"].update(npc)
    return s


# ── #751: header names the NPC, not the player ──────────────────────────────────

def test_751_header_names_the_npc_not_the_player():
    out = _render_parley(_with_npc())
    txt = out["text"]
    assert "Toll-keeper Olwen" in txt, f"header must name the NPC; got: {txt!r}"
    # The header line specifically must not say "Speaking with <player>".
    assert "Speaking with Cassian Frostbreaker" not in txt, (
        f"header still names the player (the #751 bug); got: {txt!r}"
    )


def test_751_no_npc_block_keeps_actor_header():
    # Regression: with no npc block the header keeps today's behavior (names the lead speaker),
    # so the no-target freeform parley is byte-for-byte unchanged.
    out = _render_parley(_BASE)
    assert "Cassian Frostbreaker" in out["text"]


def test_751_npc_name_is_pinned_independent_of_actor():
    # Even though the actor (lead PC) drives the skill slots, the bound NPC name is what the
    # header shows — proving the header is pinned to the conversation target, not the actor.
    out = _render_parley(_with_npc(id="mira", name="Mira of the Inkstain"))
    assert "Mira of the Inkstain" in out["text"]
    assert "Speaking with Cassian Frostbreaker" not in out["text"]


# ── #615: disposition meter renders on the Dialogue screen ──────────────────────

def test_615_disposition_dot_renders_when_npc_bound():
    out = _render_parley(_with_npc(disposition="cool"))
    assert "DispositionDot" in out["components"], (
        f"a DispositionDot meter must render for the bound NPC; components: {out['components']}"
    )


def test_615_no_disposition_meter_without_an_npc():
    # No npc block -> no disposition meter (nothing to read), today's parley unchanged.
    out = _render_parley(_BASE)
    assert "DispositionDot" not in out["components"]


def test_615_disposition_meter_renders_even_at_zero_attitude():
    out = _render_parley(_with_npc(attitude="", attitude_value=0, disposition="neutral"))
    assert "DispositionDot" in out["components"]
