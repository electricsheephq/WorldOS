# WorldOS Library Metrics Ledger — the flywheel's own eval

> **Auto-generated from `qa/scores.db` (`library_metrics` table) — do not hand-edit.** Regenerate with `python3 qa/scores_db.py --render-library-metrics`. Rows are appended via `qa/scores_db.add_library_metrics(...)`, called by `qa/library_metrics.py`'s `snapshot_library()` (its sole writer). One row per SNAPSHOT of the harvest loop's own health — library size, Σreuse_count, promotion pass-rate, and (once HV4 wires it) %library-sourced beats per run.
> **Promo pass%** = promoted / (promoted + rejected) over `library/.promoted.jsonl` (promote.py's processed-log); blank when no batch has run yet. **Lib-sourced%** stays blank until HV4 wires per-run library-vs-fresh-gen attribution — an unset column here is today's expected state, not a bug.
> Rows: **1** · rendered 2026-07-07T22:26:21+00:00

| When | SHA | Size | By class | By tier | Σreuse | Promo pass% | Promoted | Rejected | Lib-sourced% | Source | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-07-07T22:26:21+00:00 | bf0a5d03 | 3 | {"location": 2, "npc": 1} | {"canonical": 0, "experimental": 0, "stable": 3} | 0 | 10% | 3 | 27 |  | /Users/lume/WorldOS/library | 2nd promotion batch (extractor v2 yield): 2/21 fresh nominations promoted (npc-minsc rri-a1-gate 4.1, loc-elfsong-tavern rri-a1-gate2 4.3); quest panel not control-valid after 3 attempts (the-shadow-cursed-lands control landed 2.4-2.9, below [2.8,5.2] band each time) |
