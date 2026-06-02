# qa/ — QA harness

Routing pointers (see also: [`QA_TOOLS.md`](QA_TOOLS.md), [`SCORECARD.md`](SCORECARD.md), [`SCORING.md`](SCORING.md)).

## Finding past QA runs

Don't `ls` / grep `qa/ui_playtest_runs/`, `play-state/`, or `qa/transcripts/` directly — there are 800+ artifacts with mixed naming. Query the index instead:

```bash
qa/scripts/find_run.py --since 2026-05-25 --gate red --failed
qa/scripts/find_run.py --persona newbie --paths-only --limit 20
qa/scripts/find_run.py --sha 1057234
```

Full schema, query recipes, and the canonical naming format for new runs: [`INDEX_SCHEMA.md`](INDEX_SCHEMA.md).

On a fresh clone (or when the index is stale):

```bash
python3 qa/scripts/backfill_index.py
```

Idempotent. Writes `qa/INDEX.jsonl` (gitignored, per-developer). The two playtest runners (`ui_playtest.sh`, `ui_playtest_app.sh`) auto-append to the index on every successful run.

## Layered stores

- **Raw artifact catalog** — `qa/INDEX.jsonl` (this directory). Every playtest dir, play-state, transcript. Auto-built.
- **Curated quality verdicts** — `qa/scores.db` rendered to [`scores_ledger.md`](scores_ledger.md). Hand-validated headline runs across surfaces. Append via `qa/scores_db.py --add`.

INDEX rows that match a curated ledger row get a `scored_in_ledger` field linking the two.

## Other key docs in this dir

| File | Purpose |
|---|---|
| [`QA_TOOLS.md`](QA_TOOLS.md) | Command map for agents — which tool for which surface |
| [`SCORECARD.md`](SCORECARD.md) | Run-level evidence ledger (rendered from `scores.db`) |
| [`SCORING.md`](SCORING.md) | Lens scoring spec (story-craft, mechanical, angry-DM) |
| [`UI_PLAYTEST.md`](UI_PLAYTEST.md) | UI playtest harness (player + DM) |
| [`GUI_WORKBOOK.md`](GUI_WORKBOOK.md) | GUI-built-app surface notes |
| [`INDEX_SCHEMA.md`](INDEX_SCHEMA.md) | Artifact index schema + naming + queries |
