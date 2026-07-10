# Journey VQA question set (versioned)

The FACTUAL visual-QA questions `qa/journey_eval.py` asks of every captured journey frame. This is the
instrument that catches the defects the aesthetic panels scored AROUND: a T-posing actor, a wrong-plate
bundle, a character standing inside a painted prop, a failed door-cross plate swap — facts a beauty
score never registers.

**Contract, do not break it:** every question is phrased so **YES = a defect**. `journey_eval.py`
treats ANY `true` flag on ANY frame as a journey FAIL and reports the offending frame path. Keep new
questions in the same polarity. The harness reads ONLY the fenced `json` block below (the prose is for
humans); edit the block to add/adjust questions — `applies_to` is `"all"` (every frame) or
`"transition"` (only the paired frames captured on both sides of a door-cross / combat-entry, where the
backdrop legitimately changes and a wrong/failed swap is the defect).

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
      "flag": "not_singular",
      "applies_to": "all",
      "text": "Is the player character MISSING from the frame, or DUPLICATED (rendered more than once) — i.e. NOT exactly one clearly visible player character?"
    },
    {
      "flag": "broken_backdrop",
      "applies_to": "all",
      "text": "Does the scene backdrop look BROKEN, black/empty, half-loaded, or like the WRONG location for the moment (a mismatched or corrupted plate) rather than a single coherent painted room?"
    },
    {
      "flag": "transition_backdrop_unchanged",
      "applies_to": "transition",
      "text": "This is one side of a door-cross / room transition. Does the backdrop look IDENTICAL to the other side — i.e. the room did NOT actually change when it should have (a failed plate swap)?"
    }
  ]
}
```
