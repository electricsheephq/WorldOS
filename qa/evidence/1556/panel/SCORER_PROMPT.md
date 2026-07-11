# Blind painterly-plate panel — score 5 crypt background plates

You are ONE independent blind scorer on a 5-scorer panel. Score 5 candidate painterly background
plates for a top-down/isometric D&D video game (an ancient stone burial CRYPT interior). You do NOT
know how any image was made — judge ONLY what you SEE. Do not apply any "AI-made deserves less" prior.

## The reference bar
The quality bar is **Pillars of Eternity II: Deadfire** pre-rendered isometric environment art:
hand-painted oil-brush strokes, muted stone palette (cool grey-blues + warm ambers), warm-core /
cool-periphery chiaroscuro (a warm torchlight pool falling off to cooler shadow at the room edges),
deep-but-not-crushed tenebrism where the room layout stays readable in the dark, and coherent
architecture that reads as a real explorable space.

## The images
Read these 5 files (they are blinded — the order means nothing):
- /Users/lume/WorldOS-worktrees/wt-stylepass-v2/qa/evidence/1556/panel/blind/image_1.png
- /Users/lume/WorldOS-worktrees/wt-stylepass-v2/qa/evidence/1556/panel/blind/image_2.png
- /Users/lume/WorldOS-worktrees/wt-stylepass-v2/qa/evidence/1556/panel/blind/image_3.png
- /Users/lume/WorldOS-worktrees/wt-stylepass-v2/qa/evidence/1556/panel/blind/image_4.png
- /Users/lume/WorldOS-worktrees/wt-stylepass-v2/qa/evidence/1556/panel/blind/image_5.png

## Step 1 — factual defect checklist (plain YES/NO per image, on what you literally SEE)
- `fake_text`: any hallucinated text/letters/numbers/signature/watermark anywhere in the image?
- `invented_architecture`: any doorway/arch/window/opening/staircase/room that looks bolted-on or
  incoherent with the rest of the enclosed crypt (a wall that turned into an opening)?
- `duplicate`: any obviously cloned/tiled/duplicated decorative motif or prop?
- `broken_readability`: are any large regions crushed to flat black or blown out so the layout is
  unreadable there?
- `not_a_crypt`: does it fail to read as an enclosed ancient stone burial crypt interior?

## Step 2 — score each image 0–10 on the PoE2 bar
9–10 indistinguishable from the reference bar · 7–8 clearly the same world, minor tells · 5–6 reads
as a game but visibly below the bar · 3–4 the illusion is breaking · 0–2 broken. Within-panel
comparison only; use the full range and be discerning.

## Output — TEXT-ONLY JSON, nothing else
{"defects":{"image_1":{"fake_text":false,"invented_architecture":false,"duplicate":false,"broken_readability":false,"not_a_crypt":false}, "...image_5":{...}},
 "scores":{"image_1":N,"image_2":N,"image_3":N,"image_4":N,"image_5":N},
 "ranking":["best..worst by image_N"],
 "notes":"one or two sentences on the strongest/weakest and why"}
Score all 5 independently. Return ONLY the JSON object.
