# Journey VQA question set (versioned)

The FACTUAL visual-QA questions `qa/journey_eval.py` asks of every captured journey frame. This is the
instrument that catches the defects the aesthetic panels scored AROUND: a T-posing actor, a wrong-plate
bundle, a character standing inside a painted prop, a failed door-cross plate swap — facts a beauty
score never registers.

**Contract, do not break it:** every question is phrased so **YES = a defect**. `journey_eval.py`
treats ANY `true` flag on ANY frame as a journey FAIL and reports the offending frame path. Keep new
questions in the same polarity. The harness reads ONLY the fenced `json` block below (the prose is for
humans); edit the block to add/adjust questions. `applies_to` is one of:
- `"all"` — asked of every frame by the single-frame LLM scorer.
- `"transition"` — asked of each side of a transition by the single-frame scorer.
- `"transition_pair"` — computed DETERMINISTICALLY by the harness from BOTH sides of a transition (a
  single-frame scorer can't compare to the other side). Today `transition_backdrop_unchanged` is a
  pre/post luma-difference check: a door-cross/combat-entry whose two frames barely differ = a failed
  plate swap. These are never sent to the LLM.

The scorer answers strictly from what is literally visible (no lore, no intent), one YES/NO per flag.

```json
{
  "version": 2,
  "questions": [
    {
      "flag": "on_prop",
      "applies_to": "all",
      "text": "Is the player character standing ON TOP OF or INSIDE a painted prop/object (a sarcophagus, crate, log, rock, table) rather than on open, walkable floor?"
    },
    {
      "flag": "t_pose",
      "applies_to": "all",
      "text": "Is any character in a T-pose, a stiff bind-pose, or an obviously broken/frozen rig pose (limbs splayed, no natural stance)?"
    },
    {
      "flag": "floating",
      "applies_to": "all",
      "text": "Is any character floating — feet clearly off the ground, or no ground-contact shadow anchoring them to the floor?"
    },
    {
      "flag": "missing_or_cloned",
      "applies_to": "all",
      "text": "Are ALL player characters MISSING (no adventurer figures visible at all), OR is a single character CLONED (the exact same character — same outfit/pose — rendered two or more times)? A party of DIFFERENT adventurers is normal and is NOT a defect; only 'nobody there' or 'the same person duplicated' counts."
    },
    {
      "flag": "broken_backdrop",
      "applies_to": "all",
      "text": "Does the scene backdrop look BROKEN, black/empty, half-loaded, or like the WRONG location for the moment (a mismatched or corrupted plate) rather than a single coherent painted room?"
    },
    {
      "flag": "transition_backdrop_unchanged",
      "applies_to": "transition_pair",
      "text": "HARNESS-COMPUTED (not asked of the LLM): the pre/post frames of a door-cross / combat-entry barely differ — the room did NOT change when it should have (a failed plate swap)."
    }
  ]
}
```
