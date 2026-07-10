# Blind art-judge task — score 3 isometric painterly game environments

You are an independent, blind art judge. You will look at THREE painted isometric/dimetric
fantasy game-environment frames and score each one on a single 0-10 quality scale.

## The bar (anchor your scale here)
The target is the **Pillars of Eternity II: Deadfire** painted-environment look: rich hand-painted
oil brushwork, dramatic chiaroscuro lighting, dense believable stone/material detail, cohesive
composition, strong tactical readability. On this scale, a **real shipped PoE2 / Baldur's Gate II /
Divinity frame = ~9.0**. A flat, thin, or generic asset-flip = ~4-5. Be calibrated and honest —
most AI game art sits 5-7; only genuinely reference-caliber work earns 8+.

## What to weigh
- Painterly brushwork quality & surface richness (not flat/plasticky)
- Lighting: chiaroscuro drama, believable light pooling, value contrast
- Material & ornamentation density: carved stone, reliefs, weathering, props, clutter
- Cohesion & atmosphere (does it read as one lived-in place)
- Readability of the space

## Rules
- Score BLIND. Do NOT try to guess which image is AI-generated vs a real game screenshot, and do
  NOT let resolution/crop influence you. Judge only the painted quality against the PoE2 bar.
- Score each image on its own merits, 0.0-10.0, one decimal allowed.
- The three images are the three files given to you by absolute path. Read all three, then score.

## Output — STRICT, nothing else
Return ONLY this JSON object (no prose before/after):
```json
{"image_1": <score>, "image_2": <score>, "image_3": <score>, "notes": {"image_1":"<=12 words","image_2":"<=12 words","image_3":"<=12 words"}}
```
