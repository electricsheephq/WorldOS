# QA Index — Schema & Usage

`qa/INDEX.jsonl` is an append-only index of QA artifacts. One JSON object per
line, three artifact kinds (`run`, `play-state`, `transcript`). Built by:

- **`qa/scripts/indexer.py`** — extract metadata from a single dir/file
- **`qa/scripts/backfill_index.py`** — walk all artifact dirs, rebuild from scratch
- **`qa/scripts/find_run.py`** — query helper (the agent-facing surface)

The index is **gitignored** (matches the `qa/ui_playtest_runs/` ignore pattern)
— each developer keeps a local index of their own artifacts. The scripts and
schema are committed; the data is not.

## Canonical run-name format

New playtest runs (going-forward) use:

```
<YYYYMMDDTHHMMSSZ>-<sha7>-<world>-<persona>-<provider>-<scenario>
```

Example:

```
20260602T053015Z-9545383-baldurs-gate-newbie-claude-codex-native
```

Components:

| Field      | Source                                | Notes                                          |
|------------|---------------------------------------|------------------------------------------------|
| `TS`       | `date -u +%Y%m%dT%H%M%SZ`            | UTC, sortable                                  |
| `sha7`     | `git rev-parse --short HEAD`         | 7-char commit                                  |
| `world`    | runner arg `$2`                       | e.g. `baldurs-gate`                            |
| `persona`  | runner arg `$3`                       | `newbie` / `veteran` / `adversarial` / ...     |
| `provider` | env `WOS_APP_SELECTED_PROVIDER`       | `claude` / `codex`                             |
| `scenario` | runner-fixed suffix                   | `play` (ui_playtest.sh), `app` (ui_playtest_app.sh) |

Legacy names (`nb1`, `gate-96c0401-newbie`, `handoff-<TS>-<sha>-<scenario>`,
etc.) are parsed best-effort by `indexer.parse_canonical_name`: timestamp
extracted by regex, sha by 7-hex match, persona/world by suffix match against
known values. Whatever isn't parseable falls through to the dir mtime + `null`
fields — never crashes.

## INDEX.jsonl row schema

Every row has these fields:

```json
{
  "kind": "run | play-state | transcript",
  "id": "<dir name or transcript filename>",
  "path": "<repo-relative path>",
  "timestamp_iso": "2026-06-02T05:30:15Z",
  "commit_sha": "9545383",
  "indexed_at": "2026-06-02T14:00:00Z",
  "source": "runner | backfill"
}
```

### `kind: "run"` adds

```json
{
  "version": "v1.0.3-126-g1057234",
  "world": "baldurs-gate",
  "persona": "newbie",
  "provider": "claude",
  "scenario": "codex-native",
  "surface": "BUILT dist/WorldOS.app (part A) + ...",
  "beats_cap": 6,
  "budget_usd": 4.0,
  "canonical_name": false,

  "part": "A | B | AB",
  "part_a_result": "PASS | FAIL | skipped",
  "part_b_persona_loop": "ran | skipped | backend_not_ready | ...",
  "part_b_score_pass": true,
  "spend_usd": 0.42,
  "minted_run_dir": "play-20260602043338",
  "player_agent": "claude",
  "player_cost_usd": 0.25,
  "player_rc": 0,
  "port": 8765,

  "score": {
    "completed_intro_flow": true,
    "reached_play_screen": true,
    "actions_total": 11,
    "in_story_turns": 2,
    "console_errors": 0,
    "network_failures": 0,
    "image_404s": 0,
    "gave_up": true,
    "persona_satisfaction": 4,
    "satisfaction_source": "derived | self-reported",
    "pass": false
  },
  "bug_counts": {
    "critical": 0, "major": 2, "minor": 0, "trivial": 0,
    "total": 2, "ndjson_lines": 2
  },
  "summary_md": "qa/ui_playtest_runs/<id>/summary.md",

  "linked_transcripts": ["qa/transcripts/<id>.chat.jsonl", ...],
  "linked_play_state": "play-state/<id-or-minted-dir>",
  "linked_rubrics": {
    "tolkien": "qa/transcripts/<...>.tolkien.json",
    "angrydm": "qa/transcripts/<...>.angrydm.json",
    "score":   "qa/transcripts/<...>.score.json",
    "state":   "qa/transcripts/<...>.state.json"
  },

  "scored_in_ledger": {
    "story_overall": 4.1, "mech_overall": 4.1, "angrydm_overall": 3.2,
    "behavioral": "GREEN", "rri": null, "critical_bugs": 0,
    "image_render_rate": null, "pass": 1,
    "surface": "engine-duo", "dm_model": "sonnet", "scorer_model": "claude"
  }
}
```

`scored_in_ledger` is populated only when `qa/scores.db` has a row with the
matching `run_id`. It's the curated quality verdict (69 rows total, across all
surfaces and worktrees); the raw `INDEX.jsonl` row is the raw artifact.

### `kind: "play-state"` adds

```json
{
  "world": null, "persona": null, "canonical_name": false,
  "campaign_count": 1,
  "chat_lines": 7,
  "player_moves": 2,
  "linked_run": "qa/ui_playtest_runs/<id-if-matching>"
}
```

### `kind: "transcript"` adds

```json
{
  "run_id": "gate-96c0401-duo",
  "role": "chat | dm | player | null",
  "suffix": "chat.jsonl | dm.<nanos>.jsonl | ...",
  "line_count": 5,
  "linked_run": "qa/ui_playtest_runs/<run_id-if-matching>"
}
```

## Common queries (`find_run.py`)

```bash
# All runs since a date
qa/scripts/find_run.py --since 2026-05-25

# Failed runs only (score.pass != true)
qa/scripts/find_run.py --kind run --failed

# Runs where the persona gave up
qa/scripts/find_run.py --gave-up

# Runs by exact commit
qa/scripts/find_run.py --sha 1057234

# Just paths, for scripting
qa/scripts/find_run.py --persona newbie --paths-only --limit 20

# Runs that have a curated scores.db row
qa/scripts/find_run.py --scored

# Runs that DON'T have a curated row (mostly exploratory)
qa/scripts/find_run.py --unscored --kind run

# Runs with story-craft / angry-DM rubrics available
qa/scripts/find_run.py --has-rubric

# Raw JSONL, pipe to jq
qa/scripts/find_run.py --since 2026-06-01 --jsonl | jq '.id, .score.persona_satisfaction'

# Just count matches
qa/scripts/find_run.py --failed --count
```

Full flag reference: `qa/scripts/find_run.py --help`.

## Grep recipes (no Python required)

```bash
# Recent runs in last 24h (by indexed_at)
grep -F "\"indexed_at\": \"$(date -u +%Y-%m-%d)" qa/INDEX.jsonl

# Find an id substring
grep -F '"id": "handoff-20260602' qa/INDEX.jsonl | python3 -c 'import sys,json; [print(json.loads(l)["path"]) for l in sys.stdin]'

# All gave-up runs
python3 -c '
import json
for line in open("qa/INDEX.jsonl"):
    e = json.loads(line)
    if (e.get("score") or {}).get("gave_up"):
        print(e["id"], "→", e["path"])
'
```

## Rebuilding the index

If the index gets out of sync (file deleted, runner skipped the append, fresh
clone), rebuild from scratch:

```bash
python3 qa/scripts/backfill_index.py
```

Idempotent; safe to re-run. Backfill writes to `qa/INDEX.jsonl.new` then atomic
renames, so a partial run doesn't leave a corrupt index.

## Auto-append on every run

The two playtest runners write to `INDEX.jsonl` automatically:

- `qa/ui_playtest.sh` — appends after `score.json` write
- `qa/ui_playtest_app.sh` — appends after `run.json` write
- `qa/release_gate.sh` — inherits via its per-persona `ui_playtest_app.sh` calls

The append is wrapped in `|| true` so a broken indexer never fails a real
playtest. Indexer is also idempotent — re-running on an already-indexed dir
updates the existing row (matched by `(kind, id)`) rather than appending a
duplicate.

## Why JSONL, not SQLite

- ~800 rows total, growing slowly. SQLite is overkill.
- Append-only is failsafe: a half-written line at EOF is recoverable; a
  half-written sqlite write can corrupt the db.
- Grep/jq/awk-friendly. No client library needed.
- `qa/scores.db` already exists for the *curated* scores layer — JSONL covers
  the *raw* artifact layer. Two different concerns, two different stores.

## Relationship to `qa/scores.db`

| Layer            | Store              | Rows  | Source        | Purpose                                                  |
|------------------|--------------------|-------|---------------|----------------------------------------------------------|
| Raw artifacts    | `qa/INDEX.jsonl`   | ~800  | runners + backfill | Every playtest dir + play-state + transcript            |
| Curated quality  | `qa/scores.db`    | ~69   | hand-validated (`scores_db.py --add`) | Headline scored runs across surfaces      |

INDEX rows with a matching `run_id` in `scores.db` get a `scored_in_ledger`
field pointing at the curated verdict. Use `--scored` / `--unscored` on
`find_run.py` to filter either way.

## Sister surfaces (not yet indexed)

- Engine-side play-state writes (engine server is Python, separate concern) —
  backfill catches existing dirs; runtime auto-append would need an engine
  hook. Filed as a follow-up issue.
- Cross-worktree artifacts (other devs' / CI's runs landing under
  `/private/tmp/wos-*/qa/...`) — each worktree has its own index. If a shared
  catalog becomes useful later, promote milestone rows into a committed
  `qa/MILESTONES.jsonl`.
