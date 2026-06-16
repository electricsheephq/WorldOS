# Screenshot baselines — GUI visual-regression reference set

This directory holds the **committed reference screenshots** that
`qa/visual_regression_check.py` diffs a GUI-sweep's candidate screenshots against.
It is the only place visual baselines live; the checker is a pure reader and never
writes here (or anywhere except a caller-supplied report path).

## Directory convention

```
qa/screenshot_baselines/
  v1.0.4/                     # one dir PER RELEASE (the version the shots were blessed under)
    newbie/                   #   <persona>  (matches the play_player_browser_* personas)
      table.png               #     <view>.png  (one PNG per screen/view)
      combat.png
      dialogue.png
      map.png
    optimizer/
      table.png
      ...
  v1.0.5/
    ...
```

* **Per release.** Baselines are blessed against a specific release (`vX.Y.Z`). A new
  release that intentionally re-skins the UI gets a NEW `vX.Y.Z/` dir — you never
  silently overwrite a prior release's reference set. Point `--baseline-dir` at the
  release you are gating against.
* **`<persona>/<view>.png`.** The persona axis mirrors the GUI sweep's personas
  (`newbie`, `optimizer`, `narrative`, `adversarial`, `battlemage`). The view is a
  named screen; the canonical screen-hash list comes from `qa/owshot.sh`
  (`launcher, table, combat, dialogue, map, character, inventory, forge, relations,
  journal, bestiary, acts, merchant, create, seed, settings`).
* **View key = relative path.** The checker keys each comparison on the PNG's path
  *relative to the root you pass* (e.g. `newbie/table.png`). Baseline and candidate
  dirs must use the SAME relative layout so keys line up.

## Capturing a candidate / baseline set

Use the existing headless capture primitive — it loads a localhost URL only and is safe
to run autonomously (it never enumerates the gated external volume):

```bash
# one screen:
qa/owshot.sh table /tmp/sweep-shots/newbie/table.png
# ... repeat per (persona, view), keeping the same relative layout under the root.
```

To bless a candidate set into a release baseline, copy the vetted candidate tree into
`qa/screenshot_baselines/vX.Y.Z/` and commit it (PNGs are committed normally; this is a
reference artifact, NOT a generated data artifact like scores.db/RRI.json).

## Running the check

```bash
# STRICT (the only mode that should ever gate a build):
python qa/visual_regression_check.py \
  --baseline-dir qa/screenshot_baselines/v1.0.4 \
  --candidate-dir /tmp/sweep-shots \
  --mode strict --json

# AUDIT (advisory, never gates) + an HTML report for human review:
python qa/visual_regression_check.py \
  --baseline-dir qa/screenshot_baselines/v1.0.4 \
  --candidate-dir /tmp/sweep-shots \
  --mode audit --report-html /tmp/visual-audit.html --json
```

Exit codes: `0` = PASS / SKIPPED / EMPTY, `2` = FLAG (strict only). An empty or absent
baseline dir is an additive-by-default no-op (PASS/EMPTY, never a flag).

## STRICT vs AUDIT, and the noise-floor caveat — START STRICT

* **STRICT** is stdlib-only (`hashlib` + a tiny IHDR parse). It compares the **sha256 of
  the PNG bytes** and the **pixel dimensions**, and gates ONLY on a *definite* change:
  a byte/dimension diff, or a **baseline view with no candidate** (a whole screen/element
  vanished from the run — the highest-signal regression). A candidate-only view (a brand
  new screen) is reported but NOT flagged. Strict is exact: it cannot tell a meaningful
  re-skin from one-pixel antialiasing jitter, so it is only trustworthy on screens that
  render **byte-for-byte deterministically** (no clock, no cursor blink, no animation, a
  fixed device-scale-factor and window size — exactly what `qa/owshot.sh` pins).

* **AUDIT** is a best-effort **perceptual** diff for human review. It tries `imagehash`
  (perceptual hash, Hamming distance) for a robust metric, falls back to a PIL-only mean
  per-pixel difference, and **skips cleanly with a clear message if Pillow is absent**
  (we deliberately do NOT add a heavy dependency to force it to run — strict mode covers
  the gate without it). AUDIT **never gates the build** (always exits 0) and can emit an
  HTML/JSON report.

* **The noise-floor caveat.** Real screenshots are noisy: font hinting, subpixel
  antialiasing, GPU vs software rasterisation, cursor/caret blink, and live text (clocks,
  timers) all produce tiny perceptual deltas that are NOT regressions. Before you gate on
  any perceptual threshold, **characterise the noise floor first**: capture the same screen
  twice with no code change, run AUDIT, and observe the distribution of `distance` values.
  Only once you know the floor for a given (persona, view) should you consider promoting it
  into a strict gate. **Recommendation: start strict on a small set of deterministic screens;
  use AUDIT purely for review until the floor is understood. Do not wire an AUDIT threshold
  into CI as a gate.**
