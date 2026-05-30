"""Behavior tests for the player-Chronicle narration guard `sanitizeNarration`.

`sanitizeNarration` (viewer/openworlds/screen-table.jsx) is the read/projection
filter that keeps DM-INTERNAL text out of the player-facing story scroll. It runs
in the browser under the bundled Babel-standalone; these tests exercise the REAL
function by transpiling the actual `.jsx` with that SAME bundled Babel and running
it under Node, so the test tracks the shipped behavior, not a reimplementation.

#335 added the guard for GM-advisory directives + bare engine-tool names.
#347 extends it to story-craft SCAFFOLDING: dice/check tallies ("three failed
social checks"), plot-structure jargon ("spine hook"/"cold open"), and
"beat complete" stage-directions — while NEVER false-positiving on legitimate
fiction that uses "beat"/"scene"/"act"/"hook" as ordinary words.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


_OPENWORLDS = Path(__file__).resolve().parents[1] / "openworlds"
_SCREEN_TABLE = _OPENWORLDS / "screen-table.jsx"
_BABEL = _OPENWORLDS / "vendor" / "babel-standalone-7.29.0.min.js"


@unittest.skipIf(shutil.which("node") is None, "node is required to transpile + run the JSX guard")
class SanitizeNarrationTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    @classmethod
    def setUpClass(cls):
        cls.assertTrue(_SCREEN_TABLE.exists(), f"missing {_SCREEN_TABLE}")
        cls.assertTrue(_BABEL.exists(), f"missing bundled babel at {_BABEL}")

    def _sanitize_many(self, inputs: dict) -> dict:
        """Transpile screen-table.jsx with the bundled Babel and run sanitizeNarration
        over each input under Node. Returns {key: cleaned_string}."""
        program = (
            "const fs = require('fs');\n"
            + "const Babel = require(%s);\n" % json.dumps(str(_BABEL))
            + "const src = fs.readFileSync(%s, 'utf8');\n" % json.dumps(str(_SCREEN_TABLE))
            + "const code = Babel.transform(src, { presets: ['react'], filename: 'screen-table.jsx' }).code;\n"
            # Minimal window/React stub so the module body runs headless; the file
            # exports sanitizeNarration onto window via Object.assign(window, {...}).
            + "const sb = { React: { useState: () => [null, () => {}], useRef: () => ({}),"
            + " useCallback: (f) => f, useEffect: () => {}, createElement: () => null, Fragment: 'F' } };\n"
            + "sb.window = sb;\n"
            + "const vm = require('vm'); vm.createContext(sb); vm.runInContext(code, sb);\n"
            + "const fn = sb.window.sanitizeNarration;\n"
            + "if (typeof fn !== 'function') { throw new Error('sanitizeNarration not exported'); }\n"
            + "const inputs = " + json.dumps(inputs) + ";\n"
            + "const out = Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, fn(v)]));\n"
            + "process.stdout.write(JSON.stringify(out));\n"
        )
        proc = subprocess.run(
            [self.NODE_BIN, "--input-type=commonjs"],
            input=program,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(proc.stdout)

    # The verbatim #347 leak (from the #324 narrative persona): all three scaffolding
    # types in one beat — a dice tally, plot-structure jargon, and a stage-direction.
    _VERBATIM_LEAK = (
        "Zevlor held silence after three failed social checks; the moment "
        "connecting directly to the spine hook. Meeting beat of the cold open complete."
    )

    def test_strips_the_verbatim_347_scaffolding_leak(self):
        out = self._sanitize_many({"leak": self._VERBATIM_LEAK})["leak"]
        low = out.lower()
        # All three scaffolding fingerprints are gone…
        self.assertNotIn("failed social checks", low)
        self.assertNotIn("spine hook", low)
        self.assertNotIn("cold open", low)
        self.assertNotIn("meeting beat", low)
        self.assertNotIn("complete", low)
        # …and the real in-world prose survives (this is the value of the surgical strip).
        self.assertIn("Zevlor held silence", out)

    def test_strips_each_scaffolding_class(self):
        cases = {
            "tally_words": "The lock holds after three failed social checks.",
            "tally_digits": "She fails after 2 missed saves.",
            "jargon_inciting": "Good — the inciting incident has landed.",
            "jargon_midpoint": "That was the midpoint reversal.",
            "stage_beat_complete": "The bell tolls once. Beat complete.",
            "stage_cold_open_is_complete": "The bell rings. Cold open is complete.",
            "stage_act_wraps": "Act 2 wraps here.",
            "stage_connects_spine": "This connects directly to the spine hook.",
            "stage_setup_beat": "This is the setup beat for the betrayal.",
        }
        out = self._sanitize_many(cases)
        self.assertNotIn("social checks", out["tally_words"].lower())
        self.assertNotIn("missed saves", out["tally_digits"].lower())
        self.assertNotIn("inciting incident", out["jargon_inciting"].lower())
        self.assertNotIn("midpoint reversal", out["jargon_midpoint"].lower())
        self.assertNotIn("beat complete", out["stage_beat_complete"].lower())
        self.assertIn("The bell tolls once", out["stage_beat_complete"])  # prose kept
        self.assertNotIn("cold open", out["stage_cold_open_is_complete"].lower())
        self.assertIn("The bell rings", out["stage_cold_open_is_complete"])  # prose kept
        self.assertNotIn("wraps", out["stage_act_wraps"].lower())
        self.assertNotIn("spine hook", out["stage_connects_spine"].lower())
        self.assertNotIn("setup beat", out["stage_setup_beat"].lower())

    def test_preserves_legitimate_prose_using_craft_words(self):
        # The false-positive guard: "beat"/"scene"/"act"/"hook"/"complete" as ORDINARY
        # words, and counts that are NOT check/roll/save tallies, must pass through verbatim.
        legit = {
            "war_drum_beat": "A war-drum's beat rolled across the field as the orcs advanced.",
            "heart_beat": "Her heart skipped a beat when the door creaked open.",
            "tavern_scene": "The tavern scene was warm; a bard tuned his lute in the corner.",
            "act_of_betrayal": "The act of betrayal still stung, weeks later.",
            "second_act_play": "They watched the second act of the play from the balcony.",
            "cold_open_road": "The cold open road stretched north under a bruised sky.",
            "missed_the_mark": "Three of his arrows missed the mark and clattered off stone.",
            "failed_assaults": "After two failed assaults, the gate still held.",
            "ritual_complete": "The ritual is complete; the candles gutter out one by one.",
            "fishing_hook": "The fish took the hook and the line went taut.",
            "scene_of_crime": "The scene of the crime was scrubbed clean before dawn.",
        }
        out = self._sanitize_many(legit)
        for key, original in legit.items():
            with self.subTest(case=key):
                self.assertEqual(out[key], original)

    def test_335_advisory_and_tool_guards_still_apply(self):
        # Regression: the #335 line-oriented guards (GM-advisory header, bare tool line)
        # must keep working after the #347 sentence-level extension.
        cases = {
            "advisory": (
                "GM Advisory: an NPC has been introduced but hasn't spoken — "
                "record their first memory with `remember`."
            ),
            "bare_tool": "The gate groans open.\nremember(hero, 'found the sigil')",
        }
        out = self._sanitize_many(cases)
        self.assertNotIn("GM Advisory", out["advisory"])
        self.assertIn("The gate groans open", out["bare_tool"])
        self.assertNotIn("remember(", out["bare_tool"])

    def test_357_gm_advisory_panel_leak_is_stripped(self):
        # #357 (nb3): the WHOLE GM-Advisory panel string (the rendered debt-kind label +
        # the tool-naming nudge) leaked into the player's live-play view. A narration line
        # led by a scene-debt KIND label (space-rendered "npc introduced silent" or the raw
        # underscore tokens) is GM bookkeeping and must be stripped; legitimate prose using
        # the words "silent"/"consequence"/"npc" as ordinary language must survive.
        cases = {
            "panel_leak": (
                "npc introduced silent NPC 'Vanos' has been introduced but hasn't spoken — "
                "give them a line or record their first memory with remember."
            ),
            "raw_kind_npc": "npc_introduced_silent: Vanos has not spoken yet.",
            "raw_kind_consequence": "due_consequence is overdue — call check_consequences.",
            "raw_kind_quest": "quest_stalled — weave an advancement beat.",
            # false-positive guards: ordinary fiction using these words must pass verbatim
            "legit_silent": "The hall falls silent as the duke rises.",
            "legit_silent_figure": "A silent figure waits in the doorway, hood drawn.",
            "legit_consequence": "The consequence of his oath weighed on him as he climbed.",
        }
        out = self._sanitize_many(cases)
        self.assertEqual(out["panel_leak"], "")
        self.assertEqual(out["raw_kind_npc"], "")
        self.assertEqual(out["raw_kind_consequence"], "")
        self.assertEqual(out["raw_kind_quest"], "")
        self.assertEqual(out["legit_silent"], cases["legit_silent"])
        self.assertEqual(out["legit_silent_figure"], cases["legit_silent_figure"])
        self.assertEqual(out["legit_consequence"], cases["legit_consequence"])

    def test_scaffolding_line_inside_a_multiline_beat_is_dropped(self):
        beat = (
            "Rain hammers the cobbles outside the Elfsong.\n"
            "Meeting beat of the cold open complete.\n"
            "The bard strikes a minor chord."
        )
        out = self._sanitize_many({"beat": beat})["beat"]
        self.assertIn("Rain hammers the cobbles", out)
        self.assertIn("The bard strikes a minor chord", out)
        self.assertNotIn("cold open", out.lower())
        self.assertNotIn("meeting beat", out.lower())


if __name__ == "__main__":
    unittest.main()
