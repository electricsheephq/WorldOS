"""#397 — the build-choice PICKER (LevelUpModal) regression guard.

The optimizer persona bails when it cannot make build choices (level-up / subclass). The picker
closes that: a character-screen affordance that surfaces a DUE choice, shows the engine-owned legal
preview from /build-options, and on confirm relays a `do` move-intent to the DM (sole writer) — the
SAME write pattern camp-sidebar.jsx uses for "make camp".

This guard is a static-content test (no server needed). It pins the load-bearing invariants so the
picker can never silently regress into a display-only stub (the trap RestPrepareModal sits in) or
start fabricating data the engine does not assert:

  1. the affordance exists AND is gated on a real read-model signal (pendingSubclass / XP), never
     an always-on button;
  2. the modal reads the engine planner (/build-options) — real HP/features, nothing faked;
  3. the confirm WRITES for real (POST /move kind:"do") and is never permanently disabled;
  4. the subclass picker presents the engine-exposed SRD options (with feature previews) AND keeps a
     named free-text input for any world-canon tradition the engine's SRD table doesn't enumerate
     (#624: the options come FROM the engine planner — never a JSX-hardcoded list).
"""

import re
import unittest
from pathlib import Path

_SCREEN = Path(__file__).resolve().parents[1] / "openworlds" / "screen-character.jsx"


def _modal_block(src: str) -> str:
    """Return just the LevelUpModal function body, so write-path assertions are scoped to the
    picker and can't be satisfied by some unrelated POST elsewhere in the screen."""
    start = src.index("function LevelUpModal")
    # the next top-level `function ` after it bounds the block
    nxt = src.index("\nfunction ", start + 1)
    return src[start:nxt]


class LevelUpPickerGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _SCREEN.read_text(encoding="utf-8")
        cls.modal = _modal_block(cls.src)

    def test_affordance_exists_and_is_read_model_gated(self):
        """The Level Up / Choose Subclass button exists and only renders when a choice is actually
        due — gated on pendingSubclass or enough XP — never an always-visible button."""
        self.assertIn('testId="level-up-open"', self.src)
        self.assertIn("Choose Subclass", self.src)
        # the gate must reference the read-model signals (inc1 pendingSubclass + xp threshold)
        self.assertRegex(
            self.src,
            r"hero\.pendingSubclass[^\n]*Number\(hero\.xp\)\s*>=\s*Number\(hero\.xpMax\)",
            "the affordance must be gated on pendingSubclass / XP, not always shown",
        )

    def test_modal_reads_engine_planner_not_faked_data(self):
        """The modal fetches the engine-owned /build-options planner (real HP/features/slots)."""
        self.assertIn("function LevelUpModal", self.src)
        self.assertIn('data-worldos-testid="level-up-modal"', self.modal)
        self.assertIn("/build-options?campaign=", self.modal)
        # it renders the planner's real features, not an invented list
        self.assertIn("features_gained", self.modal)

    def test_confirm_writes_a_real_do_intent_and_is_not_display_only(self):
        """Unlike RestPrepareModal (display-only, permanently disabled), the picker confirm POSTs a
        real `do` move-intent to /move — the DM resolves it through the engine level_up."""
        self.assertIn('fetch("/move"', self.modal)
        self.assertIn('kind: "do"', self.modal)
        self.assertIn('testId="levelup-confirm"', self.modal)
        # the confirm's disabled state is DYNAMIC (submitting / unnamed-subclass), never a hardcoded
        # `disabled` with a display-only title like the rest modal carries.
        self.assertIn("disabled={confirmDisabled}", self.modal)
        self.assertNotIn("not saved to the engine", self.modal)

    def test_confirm_is_guarded_against_double_submit(self):
        """A rapid double-click must not relay two level-up intents (which would double-level the
        character). The state-based `submitting` only updates on re-render, so the guard MUST be a
        synchronous ref lock (the established screen-table.jsx pattern), checked + set before await."""
        self.assertIn("submittingRef", self.modal)
        self.assertIn("if (submittingRef.current) return;", self.modal)
        self.assertIn("submittingRef.current = true;", self.modal)

    def test_subclass_presents_engine_options_and_keeps_named_input(self):
        """#624: the picker presents the engine-exposed SRD subclass options (with previews) when the
        planner provides them, AND keeps a named free-text input for world-canon traditions the engine
        doesn't enumerate. The options come FROM the planner (option.subclass) — never JSX-hardcoded."""
        self.assertIn('data-worldos-testid="levelup-subclass-input"', self.modal)
        self.assertIn("subclassDue", self.modal)
        # confirm is blocked until a due subclass is named (honest required-field, not silent default)
        self.assertIn("subclassDue && !subclassName.trim()", self.modal)
        # the options list is sourced from the engine planner option, not a hardcoded JSX array
        self.assertIn("option.subclass", self.modal)
        self.assertIn('data-worldos-testid="levelup-subclass-options"', self.modal)
        # selecting an exposed option fills the named subclass (so confirm relays the chosen name)
        self.assertIn("setSubclassName(opt.name)", self.modal)


if __name__ == "__main__":
    unittest.main()
