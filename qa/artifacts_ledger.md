# WorldOS Per-Artifact Scores Ledger

> **Auto-generated from `qa/scores.db` (`artifacts` table) — do not hand-edit.** Regenerate with `python3 qa/scores_db.py --render-artifacts`. Rows are appended via `qa/scores_db.add_artifact(...)`, called by both `qa/artifact_score.py` and `qa/artifact_calibration_panel.py`. One row per scored content artifact (quest / npc / location / encounter). Overall is a 1.0–5.0 lens score.
> **Artifact ruler** = `ac_…` (its OWN hash family; the quest/npc/location/encounter rubrics + schemas). Rows under DIFFERENT ac_ rulers are NOT directly comparable. **Control** rows are disguised hand-authored canon (the panel-validity anchor); **Anchor** is the expected band midpoint for a control (the ±1.2 noise law bounds drift).
> Rows: **0** · rendered 2026-07-03T08:00:26+00:00

| Artifact | Class | World | When | Overall | Panel | Scorer | Artifact ruler | Control | Anchor | Run | SHA | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
