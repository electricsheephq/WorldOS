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

    def test_inline_markdown_emphasis_is_plain_text_for_players(self):
        # Live Codex-provider proof on 43e62e5 produced a roll beat that rendered
        # the raw Markdown marker in the Chronicle: "settles on **11**." The
        # player-facing projection should keep the number while dropping the
        # formatting syntax; React will not render Markdown for us.
        cases = {
            "bold_roll": "The die settles on **11**.",
            "italic_note": "The lute gives one *uneasy* hum.",
            "underscore_italic_note": "The lute gives one _uneasy_ hum.",
            "inline_code": "The clue is marked `violet wax` near your boot.",
        }
        out = self._sanitize_many(cases)
        self.assertEqual(out["bold_roll"], "The die settles on 11.")
        self.assertEqual(out["italic_note"], "The lute gives one uneasy hum.")
        self.assertEqual(out["underscore_italic_note"], "The lute gives one uneasy hum.")
        self.assertEqual(out["inline_code"], "The clue is marked violet wax near your boot.")

    def test_markdown_wrapped_internal_lines_still_do_not_render(self):
        cases = {
            "bold_tool": "**remember**",
            "underscore_tool": "_remember_",
            "italic_subtitle": "*What the campaign owes the story*",
            "bold_advisory": "**GM Advisory:** call remember for the silent NPC.",
            "underscore_advisory": "_GM Advisory:_ call remember for the silent NPC.",
            "underscore_kind": "_npc_introduced_silent_: Vanos has not spoken yet.",
            "dunder_kind": "__npc_introduced_silent__: Vanos has not spoken yet.",
        }
        out = self._sanitize_many(cases)
        self.assertEqual(out["bold_tool"], "")
        self.assertEqual(out["underscore_tool"], "")
        self.assertEqual(out["italic_subtitle"], "")
        self.assertEqual(out["bold_advisory"], "")
        self.assertEqual(out["underscore_advisory"], "")
        self.assertEqual(out["underscore_kind"], "")
        self.assertEqual(out["dunder_kind"], "")

    def test_markdown_wrapped_scaffolding_tallies_are_still_stripped(self):
        cases = {
            "wrapped_count": "The lock holds after **three** failed social checks.",
            "wrapped_result": "The lock holds after three **failed** social checks.",
            "wrapped_noun": "The lock holds after three failed **social checks**.",
        }
        out = self._sanitize_many(cases)
        for value in out.values():
            with self.subTest(value=value):
                self.assertEqual(value, "The lock holds.")

    def test_wrapper_progress_placeholders_do_not_become_story(self):
        cases = {
            "opening": "The first scene gathers around you; voices, risks, and choices come into focus.",
            "move_1": "Your choice takes hold; nearby voices, risks, and consequences begin to answer.",
            "move_2": "The world turns with your action; the scene shifts toward its answer.",
            "move_3": "Your move lands; attention gathers around what changes next.",
            "move_4": "Momentum carries through the scene; consequences are beginning to surface.",
            "mixed": (
                "The guard leans closer.\n"
                "The world turns with your action; the scene shifts toward its answer.\n"
                "The rooftop hand tightens on the dart."
            ),
            "near_miss": (
                "The world turns with your action; the scene shifts toward its answer "
                "as the gate opens."
            ),
        }
        out = self._sanitize_many(cases)
        for key in ("opening", "move_1", "move_2", "move_3", "move_4"):
            with self.subTest(case=key):
                self.assertEqual(out[key], "")
        self.assertEqual(out["mixed"], "The guard leans closer.\nThe rooftop hand tightens on the dart.")
        self.assertEqual(out["near_miss"], cases["near_miss"])

    # DEFENSE-IN-DEPTH viewer arm for the 2026-06-17 craft audit (source fix: PR #972 — the DM
    # SKILL.md FICTION-ONLY rule + the deterministic `narration_no_ooc_leak` gate in
    # qa/assert_behavioral.py::_NARRATION_LEAK). The audit found the first-person OOC AUTHORING-
    # PREAMBLE family shipping to players verbatim. These are the exact 5 leak lines; the viewer
    # must strip them even from an old transcript the source fix never touched.
    def test_strips_ooc_authoring_preamble_leaks(self):
        cases = {
            "seat_as_pc": "Now let me seat Rolan as the player character.",
            "continuity_check": "Continuity check — let me correct that.",
            "set_order": "Let me set the order of it.",
            "round_replay": "Here's how round one actually went:",
            "advancement_through_engine": "Now let me set their advancement through the engine.",
        }
        out = self._sanitize_many(cases)
        for key in cases:
            with self.subTest(case=key):
                self.assertEqual(out[key], "")

    def test_authoring_preamble_stripped_inline_and_in_multiline_beat(self):
        # The audit also saw these land as a trailing CLAUSE inside a real line and as their own
        # line inside an otherwise-real beat. The real prose around them must survive (sentence-
        # surgical strip), mirroring the #347 scaffolding behavior.
        cases = {
            "trailing_clause": (
                "Rolan squares his shoulders beneath the portcullis. "
                "Now let me seat Rolan as the player character."
            ),
            "multiline_beat": (
                "The lantern gutters as Rolan steps into the ring of torchlight.\n"
                "Now let me set their advancement through the engine.\n"
                "He draws his blade, eyes fixed on the cultist."
            ),
        }
        out = self._sanitize_many(cases)
        self.assertEqual(out["trailing_clause"], "Rolan squares his shoulders beneath the portcullis.")
        self.assertNotIn("as the player character", out["multiline_beat"].lower())
        self.assertNotIn("through the engine", out["multiline_beat"].lower())
        self.assertIn("The lantern gutters", out["multiline_beat"])
        self.assertIn("He draws his blade", out["multiline_beat"])

    # SAT→7 (narrative's ONLY major — this "cracked the 9/10"): the ROLL-RESULT-SUMMARY header form
    # ("The intimidation lands at 18; the quiet interpose at 16.") and a leading/standalone Markdown
    # HORIZONTAL RULE ("---") are DM bookkeeping that leaked into the player's story scroll. Both must be
    # stripped — while genuine prose that merely uses "lands at"/"arrive at"/a dash MUST survive.
    def test_strips_dice_result_summary_header(self):
        cases = {
            "verbatim": "The intimidation lands at 18; the quiet interpose at 16.",
            "with_trailing_rule": "The intimidation lands at 18; the quiet interpose at 16. ---",
            "single_roll": "The lockpick check lands at 14.",
            "comes_in_at": "Her stealth comes in at 12.",
            "settles_at": "His persuasion settles at 9.",
            "triple_chain": "The strike lands at 19; the parry at 14; the riposte at 11.",
        }
        out = self._sanitize_many(cases)
        for key, original in cases.items():
            with self.subTest(case=key):
                self.assertEqual(out[key], "", f"roll-summary not stripped for {key!r}: {out[key]!r}")

    def test_strips_dice_header_keeps_surrounding_prose(self):
        beat = (
            "The intimidation lands at 18; the quiet interpose at 16.\n"
            "The guard narrows his eyes and steps back from the door."
        )
        out = self._sanitize_many({"beat": beat})["beat"]
        self.assertNotIn("lands at 18", out.lower())
        self.assertNotIn("interpose at 16", out.lower())
        self.assertNotIn("---", out)
        self.assertIn("The guard narrows his eyes", out)

    def test_strips_leading_and_standalone_horizontal_rule(self):
        cases = {
            "leading_rule": "---\nThe guard narrows his eyes.",
            "rule_dashes": "---",
            "rule_stars": "***",
            "rule_underscores": "___",
            "rule_emdash": "— —",
            "spaced_dashes": "- - - -",
        }
        out = self._sanitize_many(cases)
        self.assertEqual(out["leading_rule"], "The guard narrows his eyes.")
        for key in ("rule_dashes", "rule_stars", "rule_underscores", "rule_emdash", "spaced_dashes"):
            with self.subTest(case=key):
                self.assertEqual(out[key], "", f"horizontal rule not dropped for {key!r}: {out[key]!r}")

    def test_dice_header_and_rule_guard_preserves_legitimate_fiction(self):
        # NEGATIVE test (the critical false-positive guard the file's comments stress): real narration
        # using "lands at"/"arrive at"/"glances at"/a number-after-at-that-is-not-terminal, or an em-dash
        # bound to words, MUST pass through verbatim. Genuine prose is never eaten.
        legit = {
            "arrow_feet": "The arrow lands at his feet, quivering in the mud.",
            "arrive_gate": "They arrive at the gate just past dawn, hooves steaming.",
            "settles_table": "She settles at the table and glances at him across the candle.",
            "lands_stair": "He lands at the bottom of the stair, breathless but whole.",
            "arrive_oclock": "We arrive at 5 in the morning, road-weary and cold.",
            "dash_pause": "Wait — what did you just say to me?",
            "emdash_aside": "The door — old, iron-banded — groans open on its hinges.",
            "single_inline_dash": "A long-forgotten path - half-overgrown - wound up the ridge.",
            "rain_prose": "Rain hammers the cobbles outside the Elfsong as the bard tunes his lute.",
            # NUMBER-after-"at" with NO roll verb — the canonical false-positive class. A bell that
            # "strikes at 12", a rendezvous "at 3", a candle that "gutters out at 9" are all real prose;
            # only a roll-summary VERB ("lands/settles/comes in at <N>") triggers the strip.
            "bell_strikes_12": "The bell tower strikes at 12.",
            "meet_at_3": "We meet at 3 by the broken fountain.",
            "candle_at_9": "The candle gutters out at 9, and the room goes dark.",
            "camp_at_6": "The caravan reached the river and made camp at 6.",
            # a roll-summary VERB but a NOUN target (not a number) is fiction, not a roll total.
            "cat_lands_side": "The cat lands at her side without a sound.",
        }
        out = self._sanitize_many(legit)
        for key, original in legit.items():
            with self.subTest(case=key):
                self.assertEqual(out[key], original)

    def test_roll_verb_with_in_world_quantity_survives_verbatim(self):
        # REGRESSION (adversarial-verify catch): the roll-summary strip is verb-anchored AND
        # number-anchored, but "<roll verb> at <N>" is ONLY a check total when <N> ENDS the clause
        # ("lands at 18." / "lands at 18;"). A roll total never reads "lands at 18 men" — when the
        # integer is followed by a WORD it is an in-world QUANTITY (men, gold, wagons, pounds, votes,
        # bells), genuine fiction that must survive verbatim. The verb-only guard wrongly ate the WHOLE
        # line on these; the clause-terminal requirement (followed by [.;!?] or end-of-string) spares
        # them while STILL stripping the real header (verified in test_strips_dice_result_summary_header).
        legit = {
            "falls_12_men": "The line falls at 12 men, and still the orcs come.",
            "settles_5_gold": "After haggling, the merchant settles at 5 gold and a promise.",
            "lands_7_wagons": "The caravan lands at 7 wagons strong before the gates.",
            "comes_in_40_pounds": "The hauled net comes in at 40 pounds of silver carp.",
            "resolves_9_votes": "The council resolves at 9 votes to 4.",
            "clears_6_bells": "The fog clears at 6 bells, grey and cold.",
        }
        out = self._sanitize_many(legit)
        for key, original in legit.items():
            with self.subTest(case=key):
                self.assertEqual(out[key], original)

    def test_roll_verb_with_comma_after_number_survives_verbatim(self):
        legit = {
            "thousands": "The dragon lands at 12,000 feet from you, wings beating hard.",
            "continued_prose": "The arrow lands at 12, then skips across the flagstones.",
        }
        out = self._sanitize_many(legit)
        for key, original in legit.items():
            with self.subTest(case=key):
                self.assertEqual(out[key], original)

    def test_authoring_preamble_guard_preserves_legitimate_fiction(self):
        # The false-positive guard, mirroring _NARRATION_LEAK's FP-hardening notes: a literal-
        # machinery "through the engine" (the bare form that wrongly matched Gond/artificer/Steel-
        # Watch fiction — now verb/noun-anchored), an in-world "PC" / "the player" that is NOT
        # "the player character" (the full-word hardening), and ordinary "let me …" dialogue must
        # all pass through verbatim. Story quality is the north star — never strip real fiction.
        legit = {
            "engine_block": "Steam screamed through the engine block, and the boiler shrieked.",
            "engine_housing": "The acolyte guided the brass rod through the engine housing.",
            "introduce_dialogue": '"Let me introduce you to the captain," she said.',
            "set_table_dialogue": '"Let me set the table," the innkeeper said with a tired smile.',
            "player_of_lute": "She nodded as the player of the lute began a slow, sad air.",
            "round_went": "Round one went badly for the goblins; two fled into the dark.",
            "order_of_battle": "Let me see the order of battle before we ride, the captain muttered.",
        }
        out = self._sanitize_many(legit)
        for key, original in legit.items():
            with self.subTest(case=key):
                self.assertEqual(out[key], original)


if __name__ == "__main__":
    unittest.main()
