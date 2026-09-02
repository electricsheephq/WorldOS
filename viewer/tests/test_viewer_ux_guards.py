"""SAT→7 viewer UX guards — the die-roll-context builder + the drop-confirm-on-equipped predicate.

Two adversarial-minor fixes, each backed by a PURE window-exported helper so the guard is unit-testable
without mounting the React screen (mirroring the sibling JS-behavior tests, which transpile the ACTUAL
`.jsx` with the bundled Babel and run the exported function under Node):

  • composeRollMove (viewer/openworlds/screen-table.jsx): the d20/d12/d8/d6 buttons used to fire a
    CONTEXTLESS "requests a d20 roll" — bypassing the "Type a move before declaring" guard. This builder
    gives the roll a reason: the player's TYPED intent if present, else the latest narrative line attached
    as context, so the DM never resolves a bare die. Parity with the Declare text-guard.

  • isItemEquipped / dropEquippedConfirmMessage (viewer/openworlds/screen-inventory.jsx): a one-click
    Drop on EQUIPPED body armor was irrevocable with no confirmation. The predicate flags ONLY currently-
    equipped gear (loose stash items are never nagged); the message is the shared confirm copy.
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
SCREEN_INVENTORY = OPENWORLDS / "screen-inventory.jsx"
BABEL = OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH; skipping JS-behavior test")
    return node


def _eval(files: list[Path], expr_setup: str) -> object:
    """Transpile the given .jsx files into one sandbox and run `expr_setup` (which must
    process.stdout.write a JSON result)."""
    loads = "".join(
        "load(%s);\n" % json.dumps(str(f)) for f in files
    )
    program = (
        "const fs = require('fs'); const vm = require('vm');\n"
        + "const Babel = require(%s);\n" % json.dumps(str(BABEL))
        + "const React = { useState:()=>[null,()=>{}], useRef:()=>({}), useCallback:f=>f, useEffect:()=>{},"
        + " createElement:()=>null, Fragment:'F' };\n"
        + "const sb = { React, Array, Number, String, Boolean, Math, Object, JSON };\n"
        + "sb.window = sb; vm.createContext(sb);\n"
        + "function load(p){ const src = fs.readFileSync(p, 'utf8');"
        + " const code = Babel.transform(src, { presets: ['react'], filename: p }).code;"
        + " vm.runInContext(code, sb); }\n"
        + loads
        + expr_setup
    )
    proc = subprocess.run(
        [_node(), "--input-type=commonjs"], input=program, text=True, capture_output=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------------------------- #
# Fix #3 — composeRollMove: a die-roll is never contextless (parity with the Declare text-guard).
# --------------------------------------------------------------------------------------------- #
def _compose_roll(sides, intent, context) -> dict:
    setup = (
        "const fn = sb.window.composeRollMove;\n"
        + "if (typeof fn !== 'function') throw new Error('composeRollMove not exported');\n"
        + "const r = fn(%s, %s, %s);\n" % (json.dumps(sides), json.dumps(intent), json.dumps(context))
        + "process.stdout.write(JSON.stringify(r));\n"
    )
    return _eval([SCREEN_TABLE], setup)


def test_roll_uses_typed_intent_as_the_reason():
    r = _compose_roll(20, "pick the lock", "The door is iron-barred.")
    assert r["move"]["kind"] == "check"
    assert r["move"]["name"] == "d20"
    # The TYPED intent wins and becomes the move's reason (never a bare "d20 roll").
    assert r["move"]["text"] == "I roll a d20 to pick the lock"
    assert r["echo"] == "rolls a d20 to pick the lock"


def test_roll_attaches_latest_narrative_context_when_no_intent():
    r = _compose_roll(20, "", "The guard eyes you warily, hand on his hilt.")
    # No typed intent -> bind the roll to the latest scene line so it is NOT contextless.
    assert "(in response to: The guard eyes you warily, hand on his hilt.)" in r["move"]["text"]
    assert r["move"]["text"].startswith("I roll a d20")
    assert r["echo"] == "rolls a d20 (in response to: The guard eyes you warily, hand on his hilt.)"


def test_roll_whitespace_intent_falls_through_to_context():
    r = _compose_roll(8, "   ", "Smoke fills the hall.")
    assert "(in response to: Smoke fills the hall.)" in r["move"]["text"]
    assert "I roll a d8" in r["move"]["text"]


def test_roll_with_no_intent_and_no_context_is_explicit_not_blank():
    # Graceful floor: even with nothing to bind to, the move names the die explicitly — it is never the
    # old bare "requests a dN roll" string with no actor verb.
    r = _compose_roll(12, "", "")
    assert r["move"]["text"] == "I roll a d12"
    assert r["echo"] == "rolls a d12"
    assert "(in response to:" not in r["move"]["text"]


def test_roll_defaults_to_d20_for_bad_sides():
    r = _compose_roll(None, "leap the chasm", "")
    assert r["move"]["name"] == "d20"
    assert r["move"]["text"] == "I roll a d20 to leap the chasm"


# --------------------------------------------------------------------------------------------- #
# Fix #4 — isItemEquipped + dropEquippedConfirmMessage: confirm only on EQUIPPED gear.
# --------------------------------------------------------------------------------------------- #
def _is_equipped(item, equipped) -> bool:
    setup = (
        "const fn = sb.window.isItemEquipped;\n"
        + "if (typeof fn !== 'function') throw new Error('isItemEquipped not exported');\n"
        + "process.stdout.write(JSON.stringify(fn(%s, %s)));\n" % (json.dumps(item), json.dumps(equipped))
    )
    return _eval([SCREEN_INVENTORY], setup)


def test_equipped_armor_is_flagged():
    equipped = [{"name": "Studded Leather"}, {"name": "Longsword"}]
    assert _is_equipped({"name": "Studded Leather"}, equipped) is True
    assert _is_equipped({"name": "Longsword"}, equipped) is True


def test_equipped_match_is_slug_tolerant_but_strict():
    # Slug-normalized match folds trivial case/space differences…
    assert _is_equipped({"name": "studded leather"}, [{"name": "Studded Leather"}]) is True
    # …but a genuinely different item (a loose peer) is NOT wrongly flagged.
    assert _is_equipped({"name": "Studded Leather +1"}, [{"name": "Studded Leather"}]) is False


def test_duplicate_item_names_use_stable_ids_before_name_fallback():
    equipped = [{"id": "equipped-sword", "name": "Longsword"}]
    assert _is_equipped({"id": "loose-sword", "name": "Longsword"}, equipped) is False
    assert _is_equipped({"id": "equipped-sword", "name": "Longsword"}, equipped) is True


def test_loose_inventory_item_is_not_flagged():
    equipped = [{"name": "Studded Leather"}, {"name": "Longsword"}]
    # A potion / a stash blade the hero is NOT wearing must never trigger the confirm nag.
    assert _is_equipped({"name": "Healing Potion"}, equipped) is False
    assert _is_equipped({"name": "Rusty Shortsword"}, equipped) is False


def test_equipped_predicate_is_defensive():
    assert _is_equipped(None, [{"name": "Longsword"}]) is False
    assert _is_equipped({"name": "Longsword"}, None) is False
    assert _is_equipped({}, [{"name": "Longsword"}]) is False
    assert _is_equipped({"name": "Longsword"}, []) is False


def test_drop_confirm_message_names_the_item_and_warns_irreversible():
    setup = (
        "const fn = sb.window.dropEquippedConfirmMessage;\n"
        + "if (typeof fn !== 'function') throw new Error('dropEquippedConfirmMessage not exported');\n"
        + "process.stdout.write(JSON.stringify(fn('Studded Leather')));\n"
    )
    msg = _eval([SCREEN_INVENTORY], setup)
    assert "Studded Leather" in msg
    assert "EQUIPPED" in msg
    assert "cannot be undone" in msg


def test_both_drop_affordances_use_the_shared_confirm_wrapper():
    source = SCREEN_INVENTORY.read_text()
    assert source.count("confirmDrop(") == 2
    assert source.count("window.confirm(dropEquippedConfirmMessage") == 1
