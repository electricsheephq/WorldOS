# BLIND VISUAL PANEL — WorldOS tavern plate (corrected anchoring, issue #1560)

You are ONE blind scorer on a 5-scorer panel judging painterly isometric game backgrounds against the
**Pillars of Eternity II: Deadfire** pre-rendered-background bar. Score honestly and independently.

## Images (all under `/Users/lume/WorldOS-worktrees/wt-layered-reg/qa/evidence/layered-reg/panel/`)
Read every one of these with the Read tool:
- `A.png`, `B.png`, `C.jpg`, `D.png` — BLIND score-targets (you are NOT told what pipeline produced each).
- `HOUSE_REF_camp.png` — **DISCLOSED house reference**: a DIFFERENT room (a night camp) from the SAME game,
  shown as the in-house painterly QUALITY BAR. Use it to calibrate what "our house best" looks like. Do NOT
  assume the tavern images should copy its content — it is a different room; it only sets the craft bar.
- `INCUMBENT_tavern.png` — **DISCLOSED comparison**: the currently-deployed tavern plate, shown for context
  ONLY. It is NOT the quality anchor and must NOT get an automatic top score for being "the current one."
  Score it on the same merits as everything else.

## What to judge
The tavern plates should read as a **high-angle top-down DIMETRIC (isometric) cutaway room** — the camera
looks DOWN into an open-topped room (like HOUSE_REF_camp and INCUMBENT_tavern do). Score each image 1-10.
Real shipped PoE2/BG2 art scores ~7-9 on this bar; a clean in-house plate ~6-9; a weak one 3-5.

**YOUR LENS (weight this dimension most, but give one overall 1-10 per image): {LENS}**

For EACH of A, B, C, D, and INCUMBENT_tavern, also record defect flags (true/false):
- `camera_break`: the image is NOT a top-down dimetric cutaway (e.g. a front-on / eye-level view). THIS IS A
  SEVERE defect for our pipeline — a plate that abandons the dimetric camera cannot be used.
- `invented_furniture`: solid walk-blocking furniture that looks added/duplicated beyond a plausible tavern set.
- `structure_incoherent`: props relocated/warped, walls inconsistent, layout that doesn't hold together.
- `floating`: props floating in mid-air.

## Output — STRICT JSON ONLY, no prose outside it
```json
{
  "lens": "{LENS}",
  "scores": {"A": <1-10>, "B": <1-10>, "C": <1-10>, "D": <1-10>, "INCUMBENT": <1-10>},
  "defects": {
    "A": {"camera_break": <bool>, "invented_furniture": <bool>, "structure_incoherent": <bool>, "floating": <bool>},
    "B": {"...": "..."}, "C": {"...": "..."}, "D": {"...": "..."}, "INCUMBENT": {"...": "..."}
  },
  "one_line_each": {"A": "<=12 words", "B": "...", "C": "...", "D": "...", "INCUMBENT": "..."}
}
```
Return ONLY that JSON object as your final message.
