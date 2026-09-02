#!/usr/bin/env python3
"""Canonical WorldOS scores ledger — ONE consistent place for every scored QA run.

WHY THIS EXISTS
---------------
Scores have historically lived in three different shapes — ``qa/SCORECARD.md`` (story/mech
duo rows), ``qa/RRI.json`` + ``qa/ui_playtest_runs/*/score.json`` (GUI sweeps), and prose
citations buried in LEXAR session notes. With no single schema tying every run to *{when,
build SHA, surface, model, methodology, what it scored}*, the same project looked like a
"4.5 engine killer" and a "GUI ~2/10" at the same time, with no way to see whether that gap
was a regression, a surface change, a model swap, or a methodology change.

This module is the fix: a single SQLite ledger (``qa/scores.db``) with one ``runs`` row per
scored run, plus a deterministic Markdown render (``qa/scores_ledger.md``). It is **additive
tooling** — it does NOT modify or replace any existing scorer. Every future run should append
exactly one row here via :func:`add_run` (or the ``--add`` CLI), then ``--render`` to refresh
the human-readable table. That is the whole discipline: one row in, one place to read.

SURFACE TAXONOMY (the crux of the forensics — classify EVERY run precisely)
---------------------------------------------------------------------------
* ``engine-duo``         — ``qa/run_duo.sh`` / ``run_party.sh`` / ``run_combat_sprint.sh``:
                            two gateway-free ``claude -p`` sessions (DM + AI player), engine
                            MCP + snapshot writer, **NO GUI**. The "4.x" story/mech numbers.
* ``GUI-built-app``      — the shipped ``dist/WorldOS.app`` native surface (WKWebView), or a
                            built-app handoff/smoke proof against it.
* ``GUI-headless-proxy`` — the AI-playtester harness (Playwright/CDP palette) driving the real
                            ``/openworlds/`` viewer served by ``play_party.sh`` / ``server.py``
                            (byte-identical backend to the .app). Persona satisfaction runs.
* ``smoke-only``         — deterministic / scripted wiring proof, no LLM quality read.
* ``visual``             — a single rendered FRAME scored by the visual-critic loop (the painterly
                           render→critique→fix→re-render cycle). dm_model/actor_model are unused;
                           ``scorer_model`` = the panel model; ``methodology`` = the lens set + round,
                           e.g. "vc-panel-6lens round=2". The visual quality numbers live in the
                           visual_* columns, NOT story/mech/angry (which stay NULL).
* ``adventure``          — the A-series adventure-loop eval (``qa/adventure_eval.py``): an AGGREGATE
                           over N arc-directed ``qa/run_adventure.sh`` runs against the one-call
                           adventure fixture. ``methodology`` = "arc-duo N=<n>"; the row's
                           story/mech/angry/behavioral/engagement columns are the per-dimension
                           aggregate (median lenses, green-rate, etc.) and ``notes`` carries the
                           WEAKEST-LINK verdict line (the routing instrument for the next sprint).
* ``agent_g4``            — the two-pass agent-as-playtester loop from DESIGN-MEMO §1. Its
                            methodology is ``agent-g4 pass=<1|2|both> build=<sha> lenses=<n>
                            reproductions=<n>``; P1/P2/P3 counts and route/pass verdicts are kept
                            in the additive G4 columns below.

USAGE
-----
    python3 qa/scores_db.py --init                  # create empty db (idempotent)
    python3 qa/scores_db.py --render                # regenerate qa/scores_ledger.md from db
    python3 qa/scores_db.py --add --run-id X ...     # append one run (see --help)
    python3 qa/scores_db.py --list                  # print rows (newest first) to stdout

    # From Python (the supported programmatic path for future runs):
    from qa.scores_db import add_run, render_markdown
    add_run(run_id="duo-foo", ts="2026-06-02T...", surface="engine-duo",
            dm_model="sonnet", story_overall=4.2, ...)
    render_markdown()
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Sibling helper (qa/scoring_config_version.py): the content hashes of the scoring RULERS — the
# FULL ruler (rubrics + schemas + gates incl. RRI; ``sc_…``) and the LENS ruler (the 8 files that
# produce the lens numbers; ``lc_…``, #725). Stamped on every scored run so a number is always
# tagged with which ruler produced it — the fix for "we used to hit 4.5, now 3.6".
try:
    from scoring_config_version import (
        scoring_config_version, scoring_config_label, lens_config_version,
        artifact_config_version,
    )
except ImportError:  # imported from a different cwd / as a package
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scoring_config_version import (
        scoring_config_version, scoring_config_label, lens_config_version,
        artifact_config_version,
    )

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
QA_DIR = Path(__file__).resolve().parent
DB_PATH = QA_DIR / "scores.db"
MD_PATH = QA_DIR / "scores_ledger.md"

# Allowed surface values (validated on insert; the crux of the forensic story).
SURFACES = ("engine-duo", "GUI-built-app", "GUI-headless-proxy", "smoke-only", "visual", "adventure", "agent_g4")

# Column order is the canonical schema. Adding a column is additive: bump this list and
# _ensure_schema() will ALTER TABLE ADD COLUMN on an existing db (old rows read NULL).
# (run_id is the PRIMARY KEY and is created separately in _ensure_schema.)
COLUMNS: tuple[str, ...] = (
    "ts",                 # ISO8601 timestamp of the run (UTC where known)
    "build_sha",          # short git SHA of the build/code under test
    "build_date",         # ISO date the build_sha was committed (date<->SHA cross-check)
    "surface",            # one of SURFACES — THE load-bearing classification
    "dm_model",           # model driving the DM (sonnet / opus / gpt-5.4 / scripted / codex)
    "actor_model",        # model driving the AI player/persona (often == dm_model)
    "scorer_model",       # model that produced the lens scores (claude / gpt-5.4 / derived)
    "methodology",        # free text, e.g. "3-lens duo 8-beat", "5-persona part-B", "handoff smoke"
    "persona",            # persona file/name the run used (e.g. qa/play_player_duo.txt). NULL =
                          # unstamped (additive; old rows / non-persona runs read back NULL).
    # --- comparability provenance (the missing stamps that broke cross-time comparison) ---
    "scoring_config_version",  # content hash of the FULL scoring RULER (rubrics+schemas+gates incl.
                               # RRI); see qa/scoring_config_version.py. Fences the RRI trend.
    "lens_config_version",     # content hash of the LENS ruler (#725): the 8 files that produce the
                               # story/mech/angry numbers (full ruler minus release_readiness.py).
                               # Fences the engine-duo quality trend. NULL on rows recorded before
                               # lens stamping (compare falls back to scoring_config_version).
    "rubric_label",       # human label for the ruler, e.g. "ruler@sc_a1b2c3d4e5f6 (9/9 files)"
    "adventure_config_version",  # content hash of the ADVENTURE ruler (av_…) — the N-run adventure
                               # aggregator's OWN hash family (see scoring_config_version.py
                               # ADVENTURE_CONFIG_FILES). Stamped ONLY on surface="adventure" rows;
                               # NULL on every other row (additive, migration-free — mirrors ac_ruler).
    "completion_claimed",      # raw DM/engine completion stamp rate; never used as completion score
    "completion_verified",     # 1 only when every run's seeded-world objective checks passed
    "completion_truth",        # JSON [{run,reasons}] explaining claimed/verified disagreement
    "measured",                # 1 only for the ruler-pinned resolved DM model
    "rc_label",           # release candidate this run scored, e.g. "v1.0.4-rc1" (NULL = ad-hoc)
    "story_overall",      # Tolkien/story-craft lens (0-5), NULL if not scored
    "mech_overall",       # Mechanical lens (0-5), NULL if not scored
    "angrydm_overall",    # 5e-fidelity / Angry-DM lens (0-5), NULL if not scored
    "behavioral",         # GREEN / RED / NULL (deterministic behavioral gate)
    "cross_persona_sat",  # avg GUI persona satisfaction (0-10), NULL for non-GUI
    "per_persona_json",   # JSON: per-persona {sat, gaveup, crit, ...}
    "rri",                # Release-Readiness Index (0-10), NULL if not an RRI sweep
    "critical_bugs",      # count of critical bugs, NULL if not measured
    "image_render_rate",  # 0.0-1.0 image render success rate, NULL if not measured
    # --- latency ledger (F13-4 / #753): the per-beat GENERATION cost the #753 budget is
    # judged against. Derived from each beat's `duration_api_ms` (the worldos-latency-
    # forensics method) by qa/latency_rollup.py. NULL on rows recorded before latency
    # stamping (pre-F13-4 runs read back NULL — additive, migration-free). ---
    "s_per_beat",         # mean GENERATION seconds per CONTINUING (routine) beat (cold open excluded)
    "coldopen_s",         # cold-open (first/world-build beat) GENERATION seconds
    "turns_per_beat",     # mean claude -p `num_turns` per CONTINUING beat (ToolSearch / round-trip proxy)
    # --- Wave-1 1B: per-kind + per-tool timing attribution (qa/latency_rollup.py). Per-kind
    # means split the routine s_per_beat by what the beat DID (a combat beat costs more than a
    # social one); the tool-exec split (from the optional 1A {ts,tool,wall_ms,ok,campaign_id}
    # sidecar) attributes how much of a beat is engine tool-exec vs model generation. All NULL
    # on rows recorded before this stamping (additive, migration-free). ---
    "combat_s_per_beat",  # mean GENERATION seconds over COMBAT beats (None when no combat beat)
    "social_s_per_beat",  # mean GENERATION seconds over SOCIAL beats (None when no social beat)
    "mean_tool_call_ms",  # mean per-tool-call wall_ms across the 1A sidecar (None w/o sidecar)
    "slowest_tool",       # tool with the largest TOTAL summed wall_ms (None w/o sidecar)
    "tool_exec_pct",      # sum(tool wall_s) / sum(beat duration_ms) — engine-exec fraction of a beat
    "duration_wall_s",    # total WHOLE-BEAT wall seconds for the run (sum of duration_ms), if measured
    # --- structural coverage (the owner's "full circle"; pairs with the #961 structural_completeness
    # gate). Tracks whether a run actually EXERCISED whole systems (not just scored prose+dice): how
    # far the arc got + a one-line human roll-up. Derived from the engine snapshot + DM tool counts by
    # qa/story_readout.structural_coverage_from_state. NULL on rows recorded before this stamping
    # (additive, migration-free). ---
    "acts_reached",       # max distinct authored act reached (1/2/3), NULL if not measured
    "structural_coverage",# one-line human roll-up, e.g. "acts 1/3 · recruit ✓ · camp · · quest-resolved ·"
    # --- WS0 feature-engagement coverage (the dead-system tracker; manifest of authored story
    # systems). engagement_pct = engaged/(engaged+inert) over the manifest (N/A systems excluded);
    # engagement_inert = the comma-joined ids of systems that were OWED but never engaged. Derived
    # from the engine snapshot + DM tool counts by qa/feature_engagement.engagement_coverage. NULL
    # on rows recorded before this stamping (additive, migration-free). ---
    "engagement_pct",     # engaged/(engaged+inert) fraction (0.0-1.0), NULL if not measured
    "engagement_inert",   # comma-joined inert system ids, e.g. "companion_approval,camp_downtime"
    "pass",               # 1 (pass) / 0 (fail) / NULL (no pass/fail verdict)
    "source_path",        # where the evidence lives (file/dir, LEXAR or repo-relative)
    "notes",              # free-text context: what was under test, caveats, confidence flags
    # --- canonical GREEN baseline marker (P1) ---
    "is_canonical_baseline",  # 1 = this run is THE canonical GREEN baseline for its comparability
                              # key (surface + dm_model + methodology + lens_config_version); 0/NULL
                              # otherwise. At most ONE per key (set_canonical_baseline clears prior).
                              # add_run defaults this to 0 — today's behavior is "not canonical".
    # --- visual-critic loop (the painterly render scorer; surface="visual"). overall + per-dim
    # gap-to-reference scores, the render path, and the round index so a scene's convergence is a
    # trend. NULL on every non-visual row (additive, migration-free). per-dim is a JSON map
    # {dim: score} matching the 6-lens panel (see qa/visual_pregate.py / SKILL.md lens ids). ---
    "visual_overall",     # 0-10 holistic "reads as ONE painting at the reference bar", NULL if not visual
    "visual_dims_json",   # JSON {registration, occlusion_grounding, scene_light_coherence,
                          #       character_integration, tactical_readability, painterly_vs_reference}
    "visual_round",       # 1-based round index within a scene's convergence loop (NULL if not visual)
    "visual_scene",       # scene_id / fixture the frame is of (e.g. "fixture:...tavern")
    "visual_backend",     # render backend: "unity-cl" | "godot" | "still" (the SKILL's render source)
    "visual_pregate",     # the deterministic pre-gate verdict for this frame: PASS|FLAG|SKIPPED
    "visual_blocking",    # comma-joined CRITICAL/HIGH defect ids still open at this round (NULL = none)
    # --- L7 MOTION lens (the "PoE2 + motion" recalibration; scored from a render REEL, not a still).
    # The still-frame quality stays in visual_* above; the MOTION quality lives here so a frozen-but-
    # pretty render and a moving-but-ugly render are distinguishable. NULL on every still-only row and
    # on every non-visual row (additive, migration-free; empty == today). ---
    "motion_overall",     # 0-10 holistic L7 motion score (idle life + locomotion weight + attack arc
                          # + hit-react + death + timing-sync + turn-to-face), NULL if no reel scored
    "motion_dims_json",   # JSON {idle_life, locomotion_weight, attack_arc, hit_react, death,
                          #       timing_sync, turn_to_face} — the L7 sub-scores (auto-JSON-encoded)
    "motion_reel_ref",    # path/id of the scored motion reel (the qa/motion_reel.py contact-sheet)
    "milestone",          # coarse art-milestone tag, e.g. "M1.0" | "M1.2" (groups rounds by milestone)
    # --- agent G4 playtester loop (DESIGN-MEMO-2026-09-02 §1; surface="agent_g4") ---
    "p1_count",           # count of triaged P1 user-truth defects
    "p2_count",           # count of triaged P2 defects
    "p3_count",           # count of triaged P3 defects
    "route_completion",   # 1 when the complete supported route was walked, else 0
    "pass1_verdict",      # PASS | FAIL for the walkaround/navigation pass
    "pass2_verdict",      # PASS | FAIL for the live-DM story pass
    "legibility_median",  # optional per-room legibility median
    "actor_luminance_floor",  # optional measured actor-luminance floor
    "frames_per_room",    # optional JSON map of room -> frame count
)

# Numeric columns get REAL; pass is an INTEGER bool; the rest TEXT.
_REAL_COLS = {
    "story_overall", "mech_overall", "angrydm_overall", "cross_persona_sat",
    "rri", "image_render_rate",
    # F13-4 latency ledger (all wall-clock seconds / turn counts → REAL)
    "s_per_beat", "coldopen_s", "turns_per_beat",
    # Wave-1 1B per-kind + per-tool timing (seconds / ms / ratio → REAL; slowest_tool is TEXT)
    "combat_s_per_beat", "social_s_per_beat", "mean_tool_call_ms", "tool_exec_pct",
    "duration_wall_s",
    # WS0 engagement coverage fraction (0.0-1.0 → REAL; engagement_inert is TEXT)
    "engagement_pct",
    "completion_claimed",
    # visual-critic loop (0-10 gap-to-reference score → REAL)
    "visual_overall",
    # L7 motion lens (0-10 holistic motion score → REAL)
    "motion_overall",
    # agent G4 optional visual measurements
    "legibility_median", "actor_luminance_floor",
}
_INT_COLS = {
    "critical_bugs", "pass", "is_canonical_baseline", "acts_reached", "visual_round",
    "completion_verified", "measured", "p1_count", "p2_count", "p3_count", "route_completion",
}


def _coltype(col: str) -> str:
    if col in _REAL_COLS:
        return "REAL"
    if col in _INT_COLS:
        return "INTEGER"
    return "TEXT"


# ---------------------------------------------------------------------------
# The `artifacts` table (HV1, #1323): per-ARTIFACT eval scores (quest/npc/location/encounter)
# ---------------------------------------------------------------------------
# ADDITIVE and SEPARATE from `runs`: the harvest loop scores individual CONTENT artifacts (a single
# quest / NPC / location / encounter), not whole playtests. Those scores live in their own table so
# they never touch the runs-table schema or its comparability stamps. Their sole writer is
# qa/artifact_score.py (mirrors "add_run is the sole writer of runs"). run_id is a NULLABLE FK to
# runs.run_id (an artifact extracted from a live campaign links back to the run that produced the
# snapshot; a canon-authored control has no run). The per-dim scores are a JSON blob (each class has a
# different dim set) rather than fixed columns, so adding a class/dim never migrates the table.
# The ruler stamp is the ARTIFACT ruler (``ac_…``) — its OWN hash family (see scoring_config_version.py
# ARTIFACT_CONFIG_FILES), NEVER the sc_/lc_ engine-duo rulers.
#
# TEXT vs VISUAL split (promotion-gate decision, 2026-07-08, docs/roadmap/VISUAL-PROMOTION-GATE-DECISION.md):
# these four are the TEXT artifact classes — they score on the 1.0-5.0 rubric in THIS table and promote
# through promote.py's text threshold gate (overall>=4.0, dims>=3.0, control-valid). The VISUAL class
# "room" is deliberately NOT here: painterly backdrop plates score 0-10 in the `runs` table
# (surface="visual", visual_overall/visual_dims_json) + panel JSONs, and promote through promote.py's
# separate delta-anchored visual gate (GATE_STRATEGIES["room"]="visual"; registry
# qa/visual_controls_identity.json). Adding "room" here would wrongly subject it to the 1-5 text gate.
ARTIFACT_CLASSES: tuple[str, ...] = ("quest", "npc", "location", "encounter")

# GATE-LEDGER classes: rows that carry an automated GATE VERDICT (walk_gate/walk_report_path), not a
# rubric panel score. Deliberately NOT in ARTIFACT_CLASSES — that tuple drives the TEXT-eval
# machinery's invariants (per-class rubric+schema files, >=2 committed disguised controls;
# qa/test_artifact_evals.py), none of which apply to a verdict row. The beauty-gate strategy for
# visual classes (0-10 panels vs the 1-5 text rubrics) is a separate, still-open decision; a room
# row's `overall` stays NULL until that lands.
GATE_LEDGER_CLASSES: tuple[str, ...] = ("room",)

ARTIFACT_COLUMNS: tuple[str, ...] = (
    "class",          # one of ARTIFACT_CLASSES — selects the rubric that scored it
    "run_id",         # NULLABLE FK to runs.run_id (the run that produced the source snapshot); NULL for canon
    "world",          # world id the artifact belongs to (e.g. "baldurs-gate"; "canon" for a control)
    "sha",            # short git SHA of the build the artifact was extracted under (NULL for canon)
    "ts",             # ISO8601 timestamp the score was recorded (UTC where known)
    "dims_json",      # JSON {dim: score, ...} — the per-class dimension scores (class-dependent keys)
    "overall",        # holistic 1.0-5.0 artifact score
    "panel_id",       # id grouping the N scorers of ONE calibration panel (e.g. "cal-quest-20260703")
    "scorer_model",   # model that produced this score (e.g. "sonnet")
    "ac_ruler",       # ARTIFACT ruler content hash (ac_…) — fences artifact-score comparability
    "is_control",     # 1 = a disguised hand-authored canon CONTROL row; 0/NULL = a scored artifact
    "control_anchor", # for a control: the expected anchor band midpoint (REAL), NULL otherwise
    "source_path",    # where the artifact JSON / evidence lives (repo-relative or LEXAR)
    "notes",          # free-text context / caveats
    "walk_gate",        # "GREEN" | "RED" — the automated walkability verdict (rooms; NULL for text classes)
    "walk_report_path", # pointer to the walk_report.json evidence that produced walk_gate
)

_ARTIFACT_REAL_COLS = {"overall", "control_anchor"}
_ARTIFACT_INT_COLS = {"is_control"}


def _artifact_coltype(col: str) -> str:
    if col in _ARTIFACT_REAL_COLS:
        return "REAL"
    if col in _ARTIFACT_INT_COLS:
        return "INTEGER"
    return "TEXT"


# ---------------------------------------------------------------------------
# The `library_metrics` table (HV5 slice 2, #1327): the flywheel's OWN eval.
# ---------------------------------------------------------------------------
# ADDITIVE and SEPARATE from both `runs` and `artifacts`: this table tracks the HEALTH of the
# harvest loop itself (library size, reuse, promotion pass-rate), not any single scored run or
# artifact. Sole writer is qa/library_metrics.py's snapshot_library() (mirrors "add_run is the sole
# writer of runs" / "add_artifact is the sole writer of artifacts"). One row per SNAPSHOT — taken
# whenever the owner/cadence wants a reading (nightly, weekly curation, or ad hoc); trend the size/
# reuse/pass-rate/library-sourced numbers across snapshots exactly like trends_json does for runs.
# No ruler stamp (ac_/sc_/lc_): this table doesn't SCORE anything — it measures the library's own
# state, which needs no rubric-version fence.
LIBRARY_METRICS_COLUMNS: tuple[str, ...] = (
    "ts",                      # ISO8601 timestamp the snapshot was taken (UTC where known)
    "library_sha",             # short git SHA of the repo state the snapshot was read at (NULL if unknown)
    "size_total",              # total entry count across every class/tier in library/
    "size_by_class_json",      # JSON {quest: N, npc: N, location: N, encounter: N, room: N}
    "size_by_tier_json",       # JSON {experimental: N, stable: N, canonical: N}
    "reuse_count_sum",         # Σ reuse_count over every entry (HV4's "less AI dependence" numerator)
    "promotion_pass_rate",     # promoted / (promoted + rejected) over promote.py's processed-log,
                               # 0.0-1.0, NULL if the log is absent/empty (no batch run yet)
    "promoted_total",          # count of "promoted" lines in library/.promoted.jsonl
    "rejected_total",          # count of "rejected" lines in library/.promoted.jsonl
    "pct_library_sourced",     # % of a run's beats sourced from library/ vs freshly AI-generated,
                               # 0.0-1.0, NULL if not measured this snapshot (HV4 wiring; additive)
    "source_path",             # where the snapshot's evidence lives (library dir path read)
    "notes",                   # free-text context / caveats
)

_LIBRARY_METRICS_REAL_COLS = {"promotion_pass_rate", "pct_library_sourced"}
_LIBRARY_METRICS_INT_COLS = {"size_total", "reuse_count_sum", "promoted_total", "rejected_total"}


def _library_metrics_coltype(col: str) -> str:
    if col in _LIBRARY_METRICS_REAL_COLS:
        return "REAL"
    if col in _LIBRARY_METRICS_INT_COLS:
        return "INTEGER"
    return "TEXT"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the runs table if missing; additively ALTER in any new COLUMNS. Also ensures the
    additive `artifacts` table (HV1) exists — a SEPARATE table that never touches runs."""
    cols_ddl = ",\n  ".join(f'"{c}" {_coltype(c)}' for c in COLUMNS)
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS runs (\n'
        f'  "run_id" TEXT PRIMARY KEY,\n  {cols_ddl}\n)'
    )
    # Additive migration: add any COLUMNS missing from an older db.
    existing = {r["name"] for r in conn.execute('PRAGMA table_info(runs)')}
    for col in COLUMNS:
        if col not in existing:
            conn.execute(f'ALTER TABLE runs ADD COLUMN "{col}" {_coltype(col)}')
    _ensure_artifacts_schema(conn)
    _ensure_library_metrics_schema(conn)
    conn.commit()


def _ensure_artifacts_schema(conn: sqlite3.Connection) -> None:
    """Create the `artifacts` table if missing; additively ALTER in any new ARTIFACT_COLUMNS.

    Purely additive: on an existing db this ONLY creates a new table (and back-fills any new artifact
    column) — it never alters `runs`. `artifact_id` is the PRIMARY KEY (created separately)."""
    cols_ddl = ",\n  ".join(f'"{c}" {_artifact_coltype(c)}' for c in ARTIFACT_COLUMNS)
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS artifacts (\n'
        f'  "artifact_id" TEXT PRIMARY KEY,\n  {cols_ddl}\n)'
    )
    existing = {r["name"] for r in conn.execute('PRAGMA table_info(artifacts)')}
    if existing:  # table already existed → back-fill any newly-added columns
        for col in ARTIFACT_COLUMNS:
            if col not in existing:
                conn.execute(f'ALTER TABLE artifacts ADD COLUMN "{col}" {_artifact_coltype(col)}')


def _ensure_library_metrics_schema(conn: sqlite3.Connection) -> None:
    """Create the `library_metrics` table if missing; additively ALTER in any new
    LIBRARY_METRICS_COLUMNS. Purely additive: on an existing db this ONLY creates a new table (and
    back-fills any new column) — it never alters `runs` or `artifacts`. Unlike those two tables,
    snapshots have no natural single-column key (a library can be snapshotted many times), so the
    row id is a plain autoincrementing integer, not a caller-supplied PK."""
    cols_ddl = ",\n  ".join(f'"{c}" {_library_metrics_coltype(c)}' for c in LIBRARY_METRICS_COLUMNS)
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS library_metrics (\n'
        f'  "id" INTEGER PRIMARY KEY AUTOINCREMENT,\n  {cols_ddl}\n)'
    )
    existing = {r["name"] for r in conn.execute('PRAGMA table_info(library_metrics)')}
    if existing:  # table already existed → back-fill any newly-added columns
        for col in LIBRARY_METRICS_COLUMNS:
            if col not in existing:
                conn.execute(
                    f'ALTER TABLE library_metrics ADD COLUMN "{col}" {_library_metrics_coltype(col)}'
                )


# ---------------------------------------------------------------------------
# The one append helper every future run should call
# ---------------------------------------------------------------------------
def add_run(
    run_id: str,
    *,
    db_path: Path | str = DB_PATH,
    replace: bool = True,
    **fields: Any,
) -> None:
    """Append (or replace) ONE run row in the canonical ledger.

    Pass ``run_id`` plus any subset of :data:`COLUMNS` as keyword args. Unknown keys raise
    (so a typo is caught, not silently dropped). ``per_persona_json``, ``visual_dims_json``, and
    ``motion_dims_json`` may be passed as dicts and are JSON-encoded automatically. ``surface`` is
    validated against :data:`SURFACES`. ``ts`` defaults to now (UTC, ISO8601) if omitted.
    """
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(
            f"unknown field(s) {sorted(unknown)}; valid: {sorted(COLUMNS)}"
        )

    surface = fields.get("surface")
    if surface is not None and surface not in SURFACES:
        raise ValueError(f"surface {surface!r} not in {SURFACES}")

    if fields.get("ts") is None:
        fields["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Canonical-baseline marker defaults to 0 for NEW rows (today's behavior: "not canonical").
    # The single-baseline-per-key invariant is enforced via set_canonical_baseline(), so stamping
    # a 1 here is allowed (e.g. a backfill) but does NOT auto-clear siblings — use the helper for
    # that. persona has no special default: omitting it simply leaves the column NULL (additive).
    if fields.get("is_canonical_baseline") is None:
        fields["is_canonical_baseline"] = 0

    # Auto-stamp the scoring RULERS unless the caller pinned them, so no scored run is ever recorded
    # without the ruler that produced it (the comparability fix). When BACKFILLING an old re-scored
    # transcript, pass scoring_config_version/rubric_label explicitly to record the ruler used THEN —
    # in that pinned case the LENS stamp is left NULL unless also pinned (stamping TODAY's lens hash
    # onto an old run would itself be a false claim; compare_rc falls back to the full ruler).
    if fields.get("scoring_config_version") is None:
        fields["scoring_config_version"] = scoring_config_version()
        if fields.get("lens_config_version") is None:
            fields["lens_config_version"] = lens_config_version()
    if fields.get("rubric_label") is None:
        fields["rubric_label"] = scoring_config_label()

    # Notes/stamp consistency guard (the v1.0.4-rc2 'apples-to-apples' false-claim class): a ruler
    # hash CITED in the notes must equal the hash STAMPED on the row. To reference another run's
    # ruler, name the run_id ("rc1's ruler differs") instead of pasting its hash.
    notes = fields.get("notes")
    if notes:
        for pattern, col in ((r"\bsc_[0-9a-f]{12}\b", "scoring_config_version"),
                             (r"\blc_[0-9a-f]{12}\b", "lens_config_version"),
                             (r"\bav_[0-9a-f]{12}\b", "adventure_config_version")):
            mismatched = sorted({h for h in re.findall(pattern, notes) if h != fields.get(col)})
            if mismatched:
                raise ValueError(
                    f"notes cite ruler hash(es) {mismatched} but the row's {col} is "
                    f"{fields.get(col)!r} — a notes claim must match the stamp (to reference "
                    f"another run's ruler, cite its run_id, not its hash)"
                )

    # JSON-encode structured payloads (per_persona_json, visual_dims_json, and motion_dims_json may
    # be passed as dicts and are auto-encoded to avoid sqlite3.ProgrammingError on non-string values).
    for _jcol in ("per_persona_json", "visual_dims_json", "motion_dims_json", "frames_per_room"):
        _jv = fields.get(_jcol)
        if _jv is not None and not isinstance(_jv, str):
            fields[_jcol] = json.dumps(_jv, ensure_ascii=False, sort_keys=True)

    # Coerce bool pass / is_canonical_baseline -> int.
    if isinstance(fields.get("pass"), bool):
        fields["pass"] = int(fields["pass"])
    if isinstance(fields.get("is_canonical_baseline"), bool):
        fields["is_canonical_baseline"] = int(fields["is_canonical_baseline"])

    cols = ["run_id"] + [c for c in COLUMNS if c in fields]
    vals = [run_id] + [fields[c] for c in COLUMNS if c in fields]
    placeholders = ", ".join("?" for _ in cols)
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    quoted = ", ".join(f'"{c}"' for c in cols)

    own = isinstance(db_path, (str, os.PathLike))
    conn = connect(db_path) if own else db_path  # type: ignore[arg-type]
    try:
        conn.execute(f"{verb} INTO runs ({quoted}) VALUES ({placeholders})", vals)
        conn.commit()
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# The one append helper every future ARTIFACT score should call (HV1, #1323)
# ---------------------------------------------------------------------------
def add_artifact(
    artifact_id: str,
    *,
    db_path: Path | str = DB_PATH,
    replace: bool = True,
    **fields: Any,
) -> None:
    """Append (or replace) ONE per-artifact score row in the additive `artifacts` table.

    Mirrors :func:`add_run`'s validation discipline, but writes the SEPARATE `artifacts` table and
    NEVER touches `runs`. Pass ``artifact_id`` plus any subset of :data:`ARTIFACT_COLUMNS` as keyword
    args. Unknown keys raise (a typo is caught, not silently dropped). ``dims_json`` may be passed as a
    dict and is JSON-encoded automatically. ``class`` is validated against :data:`ARTIFACT_CLASSES`.
    ``ts`` defaults to now (UTC, ISO8601) if omitted, and ``ac_ruler`` auto-stamps the current ARTIFACT
    ruler hash unless the caller pinned it (so no artifact score is ever recorded without the ruler that
    produced it — the same comparability guarantee the runs table gets for sc_/lc_). ``is_control``
    coerces a bool to int.
    """
    unknown = set(fields) - set(ARTIFACT_COLUMNS)
    if unknown:
        raise ValueError(
            f"unknown field(s) {sorted(unknown)}; valid: {sorted(ARTIFACT_COLUMNS)}"
        )

    cls = fields.get("class")
    if cls is not None and cls not in ARTIFACT_CLASSES + GATE_LEDGER_CLASSES:
        raise ValueError(f"class {cls!r} not in {ARTIFACT_CLASSES + GATE_LEDGER_CLASSES}")

    wg = fields.get("walk_gate")
    if wg is not None and wg not in ("GREEN", "RED"):
        raise ValueError(f"walk_gate {wg!r} must be 'GREEN' or 'RED'")

    if fields.get("ts") is None:
        fields["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Auto-stamp the ARTIFACT ruler unless pinned (mirrors add_run's sc_/lc_ auto-stamp). Pinning is
    # for a backfill of an artifact scored under an older artifact ruler.
    if fields.get("ac_ruler") is None:
        fields["ac_ruler"] = artifact_config_version()

    # Notes/stamp consistency guard (mirrors add_run): an ac_ ruler hash CITED in notes must equal the
    # ac_ruler STAMPED on the row (reference another artifact's ruler by its id, not by pasting a hash).
    notes = fields.get("notes")
    if notes:
        mismatched = sorted({h for h in re.findall(r"\bac_[0-9a-f]{12}\b", notes)
                             if h != fields.get("ac_ruler")})
        if mismatched:
            raise ValueError(
                f"notes cite artifact-ruler hash(es) {mismatched} but the row's ac_ruler is "
                f"{fields.get('ac_ruler')!r} — a notes claim must match the stamp"
            )

    _jv = fields.get("dims_json")
    if _jv is not None and not isinstance(_jv, str):
        fields["dims_json"] = json.dumps(_jv, ensure_ascii=False, sort_keys=True)

    if isinstance(fields.get("is_control"), bool):
        fields["is_control"] = int(fields["is_control"])

    cols = ["artifact_id"] + [c for c in ARTIFACT_COLUMNS if c in fields]
    vals = [artifact_id] + [fields[c] for c in ARTIFACT_COLUMNS if c in fields]
    placeholders = ", ".join("?" for _ in cols)
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    quoted = ", ".join(f'"{c}"' for c in cols)

    own = isinstance(db_path, (str, os.PathLike))
    conn = connect(db_path) if own else db_path  # type: ignore[arg-type]
    try:
        conn.execute(f"{verb} INTO artifacts ({quoted}) VALUES ({placeholders})", vals)
        conn.commit()
    finally:
        if own:
            conn.close()


def record_room_walk(
    room: str,
    verdict: str,
    *,
    db_path: Path | str = DB_PATH,
    sha: str | None = None,
    walk_report_path: str | None = None,
    source_path: str | None = None,
    notes: str | None = None,
) -> None:
    """Record a room's CURRENT walkability verdict in the artifact ledger (class="room").

    The artifact_id is the stable ``room:<room>`` — INSERT OR REPLACE semantics make this the
    latest-verdict surface ("which rooms are walk-certified right now?"); history lives in git +
    qa/certifications/. ``source_path`` should point at the certification json when one exists."""
    add_artifact(
        f"room:{room}",
        db_path=db_path,
        **{"class": "room"},
        sha=sha,
        walk_gate=verdict,
        walk_report_path=walk_report_path,
        source_path=source_path,
        notes=notes,
    )


def fetch_artifacts(db_path: Path | str = DB_PATH) -> list[dict]:
    """Return all `artifacts` rows as dicts, newest-first (ts desc, then artifact_id)."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM artifacts ORDER BY ts DESC, artifact_id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The one append helper every future LIBRARY_METRICS snapshot should call (HV5 slice 2, #1327)
# ---------------------------------------------------------------------------
def add_library_metrics(
    *,
    db_path: Path | str = DB_PATH,
    **fields: Any,
) -> int:
    """Append ONE library-health snapshot row to the additive `library_metrics` table.

    Mirrors :func:`add_run` / :func:`add_artifact`'s validation discipline, but writes the SEPARATE
    `library_metrics` table and NEVER touches `runs` or `artifacts`. Pass any subset of
    :data:`LIBRARY_METRICS_COLUMNS` as keyword args. Unknown keys raise (a typo is caught, not
    silently dropped). ``size_by_class_json`` / ``size_by_tier_json`` may be passed as dicts and are
    JSON-encoded automatically. ``ts`` defaults to now (UTC, ISO8601) if omitted. Unlike ``runs``/
    ``artifacts``, there is no caller-supplied id — every call INSERTS a new row (a snapshot never
    replaces a prior one; the row id is autoincrement). Returns the new row's integer id.
    """
    unknown = set(fields) - set(LIBRARY_METRICS_COLUMNS)
    if unknown:
        raise ValueError(
            f"unknown field(s) {sorted(unknown)}; valid: {sorted(LIBRARY_METRICS_COLUMNS)}"
        )

    if fields.get("ts") is None:
        fields["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for _jcol in ("size_by_class_json", "size_by_tier_json"):
        _jv = fields.get(_jcol)
        if _jv is not None and not isinstance(_jv, str):
            fields[_jcol] = json.dumps(_jv, ensure_ascii=False, sort_keys=True)

    for _pcol in ("promotion_pass_rate", "pct_library_sourced"):
        _pv = fields.get(_pcol)
        if _pv is not None and not (0.0 <= float(_pv) <= 1.0):
            raise ValueError(f"{_pcol} must be in [0.0, 1.0], got {_pv!r}")

    cols = [c for c in LIBRARY_METRICS_COLUMNS if c in fields]
    vals = [fields[c] for c in cols]
    placeholders = ", ".join("?" for _ in cols)
    quoted = ", ".join(f'"{c}"' for c in cols)

    own = isinstance(db_path, (str, os.PathLike))
    conn = connect(db_path) if own else db_path  # type: ignore[arg-type]
    try:
        cur = conn.execute(f"INSERT INTO library_metrics ({quoted}) VALUES ({placeholders})", vals)
        conn.commit()
        return int(cur.lastrowid)
    finally:
        if own:
            conn.close()


def fetch_library_metrics(db_path: Path | str = DB_PATH) -> list[dict]:
    """Return all `library_metrics` snapshot rows as dicts, newest-first (ts desc, then id desc)."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM library_metrics ORDER BY ts DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fetch_rows(db_path: Path | str = DB_PATH) -> list[dict]:
    """Return all rows as dicts, newest-first (ts desc, then build_date desc)."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY ts DESC, build_date DESC, run_id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fetch_rows_readonly(db_path: Path | str = DB_PATH) -> list[dict]:
    """Like :func:`fetch_rows`, but opens the db in SQLite read-only (``mode=ro``) mode and does
    NOT run ``_ensure_schema`` — so reading the COMMITTED ``qa/scores.db`` never rewrites it
    (the schema-ensure / journal touch that ``connect`` performs would otherwise mark the binary
    as modified in git). A missing file or missing ``runs`` table yields ``[]`` (no creation).
    Use this for pure read paths (e.g. release_readiness_verdict) over a committed db."""
    p = Path(db_path)
    if not p.exists():
        return []
    uri = f"file:{p}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return []
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY ts DESC, build_date DESC, run_id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []  # no runs table yet
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Canonical GREEN baseline (P1): mark / query the ONE reference run per comparability key
# ---------------------------------------------------------------------------
# The comparability key is (surface, dm_model, methodology, lens_config_version) — the same axes
# that fence a quality trend. At most ONE run per key may be the canonical baseline; setting a new
# one clears the prior (a re-baseline, not a duplicate). This is read-only over fiction and purely
# additive: a db with zero canonical rows behaves exactly as today.
_BASELINE_KEY: tuple[str, ...] = ("surface", "dm_model", "methodology", "lens_config_version")


def set_canonical_baseline(run_id: str, *, db_path: Path | str = DB_PATH) -> None:
    """Mark ``run_id`` as THE canonical GREEN baseline for its comparability key.

    Reads the row's (surface, dm_model, methodology, lens_config_version), clears
    ``is_canonical_baseline`` on every OTHER row sharing that exact key (so the invariant
    "at most one canonical per key" holds), then sets it to 1 on ``run_id``. Raises if
    ``run_id`` does not exist. Idempotent: re-marking the same run is a no-op.
    """
    own = isinstance(db_path, (str, os.PathLike))
    conn = connect(db_path) if own else db_path  # type: ignore[arg-type]
    try:
        row = conn.execute(
            "SELECT " + ", ".join(f'"{c}"' for c in _BASELINE_KEY) + " FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"run_id {run_id!r} not found; cannot set canonical baseline")
        key_vals = [row[c] for c in _BASELINE_KEY]
        # Build a NULL-safe equality predicate (IS handles NULL == NULL, which `=` does not).
        where = " AND ".join(f'"{c}" IS ?' for c in _BASELINE_KEY)
        # Clear the prior canonical baseline(s) sharing this exact key, except this run.
        conn.execute(
            f'UPDATE runs SET "is_canonical_baseline" = 0 '
            f'WHERE {where} AND run_id != ? AND "is_canonical_baseline" = 1',
            (*key_vals, run_id),
        )
        conn.execute(
            'UPDATE runs SET "is_canonical_baseline" = 1 WHERE run_id = ?', (run_id,)
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def get_canonical_baseline(
    *,
    surface: Optional[str] = None,
    dm_model: Optional[str] = None,
    methodology: Optional[str] = None,
    lens_config_version: Optional[str] = None,
    db_path: Path | str = DB_PATH,
) -> Optional[dict]:
    """Return the single canonical-baseline row for a comparability key, or ``None``.

    The key is (surface, dm_model, methodology, lens_config_version); each is matched NULL-safely
    (``IS``) so a key component left None matches rows whose column is NULL. Returns the row dict
    with ``is_canonical_baseline == 1``, or ``None`` if no canonical baseline is set for that key.
    """
    key = {
        "surface": surface,
        "dm_model": dm_model,
        "methodology": methodology,
        "lens_config_version": lens_config_version,
    }
    where = " AND ".join(f'"{c}" IS ?' for c in _BASELINE_KEY)
    conn = connect(db_path)
    try:
        row = conn.execute(
            f'SELECT * FROM runs WHERE {where} AND "is_canonical_baseline" = 1 LIMIT 1',
            tuple(key[c] for c in _BASELINE_KEY),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Markdown render (deterministic; the human-readable mirror of the db)
# ---------------------------------------------------------------------------
def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        # trim trailing zeros but keep one decimal for scores
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s if s else "0"
    return str(v).replace("|", "\\|").replace("\n", " ")


_MD_COLS = [
    ("run_id", "Run"),
    ("ts", "When"),
    ("build_sha", "SHA"),
    ("build_date", "Build date"),
    ("surface", "Surface"),
    ("dm_model", "DM model"),
    ("actor_model", "Actor model"),
    ("scorer_model", "Scorer"),
    ("scoring_config_version", "Ruler"),
    ("lens_config_version", "Lens ruler"),
    ("rc_label", "RC"),
    ("methodology", "Methodology"),
    ("story_overall", "Story"),
    ("mech_overall", "Mech"),
    ("angrydm_overall", "AngryDM"),
    ("behavioral", "Behav"),
    ("cross_persona_sat", "Sat"),
    ("rri", "RRI"),
    ("critical_bugs", "Crit"),
    ("image_render_rate", "Img%"),
    ("s_per_beat", "s/beat"),
    ("coldopen_s", "cold-open s"),
    ("turns_per_beat", "turns/beat"),
    ("combat_s_per_beat", "combat s/beat"),
    ("social_s_per_beat", "social s/beat"),
    ("tool_exec_pct", "tool%"),
    ("mean_tool_call_ms", "tool ms"),
    ("slowest_tool", "slowest tool"),
    ("acts_reached", "Acts"),
    ("structural_coverage", "Structural coverage"),
    ("engagement_pct", "Engagement"),
    ("engagement_inert", "Inert systems"),
    ("pass", "Pass"),
    ("source_path", "Source"),
    ("notes", "Notes"),
]


def render_markdown(db_path: Path | str = DB_PATH, md_path: Path | str = MD_PATH) -> str:
    rows = fetch_rows(db_path)
    out: list[str] = []
    out.append("# WorldOS Canonical Scores Ledger")
    out.append("")
    out.append(
        "> **Auto-generated from `qa/scores.db` — do not hand-edit.** "
        "Regenerate with `python3 qa/scores_db.py --render`. "
        "Append new runs via `qa/scores_db.add_run(...)` (or `--add`); this is the ONE place "
        "every scored run is recorded so scores stay consistent across surfaces/models."
    )
    out.append("")
    out.append(
        "> **Surface** is load-bearing: `engine-duo` (gateway-free DM+player `claude -p`, NO "
        "GUI — the \"4.x\" story/mech numbers) · `GUI-built-app` (shipped `dist/WorldOS.app`) · "
        "`GUI-headless-proxy` (Playwright palette on the real `/openworlds/` viewer, "
        "byte-identical backend) · `smoke-only` (deterministic wiring proof, no quality read). "
        "Story/Mech/AngryDM are 0–5 lens scores; Sat is 0–10 GUI persona satisfaction; RRI is "
        "0–10. `*` in notes = RED-capped / partial / suspect — not a clean quality reading."
    )
    out.append("")
    out.append(
        "> **Ruler** = `scoring_config_version` (a content hash of the rubric + schema + gate files, "
        "RRI gate included). **Lens ruler** = `lens_config_version` (the 8 files that produce the "
        "story/mech/angry LENS numbers — full ruler minus `release_readiness.py`; blank = recorded "
        "before lens stamping, #725). Rows under DIFFERENT Ruler values are **NOT directly comparable "
        "as a quality trend** — the ruler changed (a rubric recalibration or a new gate moves the "
        "number with no change in play quality). Use `python3 qa/scores_db.py --compare` for a "
        "lens-fenced engine-duo trend (add `--compare-rc-surface` for the GUI-built-app RC blocks); "
        "comparing across rulers requires re-scoring an archived transcript under the current ruler. "
        "**RC** = the release candidate a run scored (e.g. `v1.0.4-rc1`)."
    )
    out.append("")
    out.append(f"> Rows: **{len(rows)}** · rendered {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    out.append("")

    header = "| " + " | ".join(label for _, label in _MD_COLS) + " |"
    sep = "|" + "|".join("---" for _ in _MD_COLS) + "|"
    out.append(header)
    out.append(sep)
    for r in rows:
        cells = []
        for key, _ in _MD_COLS:
            v = r.get(key)
            if key == "image_render_rate" and v is not None:
                v = f"{float(v) * 100:.0f}%"
            elif key == "tool_exec_pct" and v is not None:
                v = f"{float(v) * 100:.0f}%"
            elif key == "pass" and v is not None:
                v = "PASS" if int(v) else "FAIL"
            cells.append(_fmt(v))
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    out.extend(_render_artifact_panels_section(db_path))
    out.extend(_render_library_metrics_trend_section(db_path))
    text = "\n".join(out)
    Path(md_path).write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------------------
# Ledger unification (#1415): fold the `artifacts` and `library_metrics` stores into the SAME
# qa/scores_ledger.md the `runs` table renders into, so "one place every scored run is recorded"
# (the file's own header promise) is actually true — RUNBOOK-INDEX gap 2. Purely ADDITIVE to
# render_markdown: the runs table renders exactly as before; these two sections are appended
# after it, and each is OMITTED ENTIRELY (no header, no table) when its store has zero groupable
# rows — a db with no artifacts/library_metrics yet renders byte-identically to before this PR.
# The full per-row detail still lives in artifacts_ledger.md / library_metrics_ledger.md
# (render_artifacts_markdown / render_library_metrics_markdown, unchanged) — these sections are a
# roll-up, not a replacement.
# ---------------------------------------------------------------------------
def _median(vals: list[Optional[float]]) -> Optional[float]:
    xs = [float(v) for v in vals if v is not None]
    return statistics.median(xs) if xs else None


# The ±1.2 per-panel noise-band already documented as bounding a control's drift (see the
# `control_anchor` column doc above + qa/felt_rest_panel.md's CALIBRATION-CONTROL LAW) — reused
# here as the artifact-panel control-band verdict so this section states an honest IN/OUT-OF-BAND
# read rather than inventing a new threshold.
_ARTIFACT_CONTROL_BAND = 1.2


def _artifact_panel_rows(db_path: Path | str = DB_PATH) -> list[dict]:
    """One roll-up row per (panel_id, class) pair present in the `artifacts` table, most-recent
    panel first. Rows with no `panel_id` recorded are excluded from this per-panel view (they
    still render in full in `qa/artifacts_ledger.md`) — a panel id is what makes "per-class
    median" and "control-band verdict" a coherent unit; an unpanelled artifact score has no group
    to summarize into."""
    rows = fetch_artifacts(db_path)
    groups: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for r in rows:
        panel_id, cls = r.get("panel_id"), r.get("class")
        if not panel_id or not cls:
            continue
        key = (str(panel_id), str(cls))
        if key not in groups:
            groups[key] = {"scored": [], "control_anchors": [], "ts": r.get("ts") or ""}
            order.append(key)
        g = groups[key]
        if r.get("ts") and r["ts"] > g["ts"]:
            g["ts"] = r["ts"]
        if r.get("is_control"):
            if r.get("control_anchor") is not None:
                g["control_anchors"].append(r["control_anchor"])
        elif r.get("overall") is not None:
            g["scored"].append(r["overall"])

    out = []
    for panel_id, cls in order:
        g = groups[(panel_id, cls)]
        median = _median(g["scored"])
        anchor = _median(g["control_anchors"])
        if anchor is None:
            verdict = "NO-CONTROL"
        elif median is None:
            verdict = "UNSCORED"
        elif abs(median - anchor) <= _ARTIFACT_CONTROL_BAND:
            verdict = "IN-BAND"
        else:
            verdict = "OUT-OF-BAND"
        out.append({
            "panel_id": panel_id, "class": cls, "n": len(g["scored"]),
            "median": median, "control_anchor": anchor, "verdict": verdict, "ts": g["ts"],
        })
    out.sort(key=lambda d: (d["ts"], d["panel_id"], d["class"]), reverse=True)
    return out


_ARTIFACT_PANEL_MD_COLS = [
    ("panel_id", "Panel"), ("class", "Class"), ("n", "N"),
    ("median", "Median"), ("control_anchor", "Control anchor"), ("verdict", "Verdict"),
]


def _render_artifact_panels_section(db_path: Path | str = DB_PATH) -> list[str]:
    """Return the '## Artifact panels' section lines, or `[]` when the `artifacts` table has no
    panelled rows (cleanly omitted — no header/table for an empty/unpanelled store)."""
    panel_rows = _artifact_panel_rows(db_path)
    if not panel_rows:
        return []
    out = ["## Artifact panels", ""]
    out.append(
        "> Per-`panel_id` roll-up of the `artifacts` table (HV1, #1323) — grouped by panel, then "
        "class. **Median** is the scored (non-control) rows' median `overall`; **Control anchor** "
        "is the disguised control's expected band midpoint (median across control rows, if >1); "
        "**Verdict** applies the ±1.2 noise-band law: `IN-BAND` / `OUT-OF-BAND`, or `NO-CONTROL` "
        "when the panel recorded no control row (`UNSCORED` when it recorded a control but no "
        "scored artifact). Full per-artifact detail lives in `qa/artifacts_ledger.md` "
        "(`--render-artifacts`)."
    )
    out.append("")
    header = "| " + " | ".join(label for _, label in _ARTIFACT_PANEL_MD_COLS) + " |"
    sep = "|" + "|".join("---" for _ in _ARTIFACT_PANEL_MD_COLS) + "|"
    out.append(header)
    out.append(sep)
    for r in panel_rows:
        cells = [_fmt(r.get(k)) for k, _ in _ARTIFACT_PANEL_MD_COLS]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return out


def _render_library_metrics_trend_section(db_path: Path | str = DB_PATH) -> list[str]:
    """Return the '## Library metrics' section lines — a CHRONOLOGICAL (oldest-first, the natural
    trend-reading order) render of every `library_metrics` snapshot — or `[]` when that table is
    empty (cleanly omitted)."""
    rows = list(reversed(fetch_library_metrics(db_path)))  # fetch is newest-first -> chronological
    if not rows:
        return []
    out = ["## Library metrics", ""]
    out.append(
        "> Chronological trend (oldest first) of the `library_metrics` table (HV5 slice 2, #1327) "
        "— the flywheel's own health, one row per snapshot. **Back-link** is the snapshot's "
        "`notes` (falls back to `source_path` when notes is unset) so a size/reuse jump can be "
        "traced to the run/curation pass that produced it. Full render lives in "
        "`qa/library_metrics_ledger.md` (`--render-library-metrics`)."
    )
    out.append("")
    cols = [
        ("ts", "When"), ("library_sha", "SHA"), ("size_total", "Size"),
        ("size_by_class_json", "By class"), ("size_by_tier_json", "By tier"),
        ("reuse_count_sum", "Σreuse"), ("_backlink", "Back-link"),
    ]
    header = "| " + " | ".join(label for _, label in cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    out.append(header)
    out.append(sep)
    for r in rows:
        cells = []
        for key, _ in cols:
            v = (r.get("notes") or r.get("source_path") or "") if key == "_backlink" else r.get(key)
            cells.append(_fmt(v))
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return out


ARTIFACTS_MD_PATH = QA_DIR / "artifacts_ledger.md"

_ARTIFACT_MD_COLS = [
    ("artifact_id", "Artifact"),
    ("class", "Class"),
    ("world", "World"),
    ("ts", "When"),
    ("overall", "Overall"),
    ("panel_id", "Panel"),
    ("scorer_model", "Scorer"),
    ("ac_ruler", "Artifact ruler"),
    ("is_control", "Control"),
    ("control_anchor", "Anchor"),
    ("run_id", "Run"),
    ("sha", "SHA"),
    ("notes", "Notes"),
]


def render_artifacts_markdown(db_path: Path | str = DB_PATH,
                              md_path: Path | str = ARTIFACTS_MD_PATH) -> str:
    """Deterministic Markdown mirror of the `artifacts` table (HV1). Separate from the runs ledger."""
    rows = fetch_artifacts(db_path)
    out: list[str] = []
    out.append("# WorldOS Per-Artifact Scores Ledger")
    out.append("")
    # A SINGLE blockquote block (each line prefixed "> ", no blank line between them) — a blank line
    # between "> " lines is markdownlint MD028 ("blank line inside blockquote"). Writers: artifact rows
    # come from qa/artifact_score.py AND qa/artifact_calibration_panel.py (both call
    # scores_db.add_artifact directly) — this file (scores_db.py) is the sole table/ledger writer.
    out.append(
        "> **Auto-generated from `qa/scores.db` (`artifacts` table) — do not hand-edit.** "
        "Regenerate with `python3 qa/scores_db.py --render-artifacts`. Rows are appended via "
        "`qa/scores_db.add_artifact(...)`, called by both `qa/artifact_score.py` and "
        "`qa/artifact_calibration_panel.py`. One row per scored content artifact "
        "(quest / npc / location / encounter). Overall is a 1.0–5.0 lens score."
    )
    out.append(
        "> **Artifact ruler** = `ac_…` (its OWN hash family; the quest/npc/location/encounter rubrics "
        "+ schemas). Rows under DIFFERENT ac_ rulers are NOT directly comparable. **Control** rows are "
        "disguised hand-authored canon (the panel-validity anchor); **Anchor** is the expected band "
        "midpoint for a control (the ±1.2 noise law bounds drift)."
    )
    out.append(f"> Rows: **{len(rows)}** · rendered {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    out.append("")
    header = "| " + " | ".join(label for _, label in _ARTIFACT_MD_COLS) + " |"
    sep = "|" + "|".join("---" for _ in _ARTIFACT_MD_COLS) + "|"
    out.append(header)
    out.append(sep)
    for r in rows:
        cells = []
        for key, _ in _ARTIFACT_MD_COLS:
            v = r.get(key)
            if key == "is_control" and v is not None:
                v = "control" if int(v) else ""
            cells.append(_fmt(v))
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    text = "\n".join(out)
    Path(md_path).write_text(text, encoding="utf-8")
    return text


LIBRARY_METRICS_MD_PATH = QA_DIR / "library_metrics_ledger.md"

_LIBRARY_METRICS_MD_COLS = [
    ("ts", "When"),
    ("library_sha", "SHA"),
    ("size_total", "Size"),
    ("size_by_class_json", "By class"),
    ("size_by_tier_json", "By tier"),
    ("reuse_count_sum", "Σreuse"),
    ("promotion_pass_rate", "Promo pass%"),
    ("promoted_total", "Promoted"),
    ("rejected_total", "Rejected"),
    ("pct_library_sourced", "Lib-sourced%"),
    ("source_path", "Source"),
    ("notes", "Notes"),
]


def render_library_metrics_markdown(db_path: Path | str = DB_PATH,
                                    md_path: Path | str = LIBRARY_METRICS_MD_PATH) -> str:
    """Deterministic Markdown mirror of the `library_metrics` table (HV5 slice 2, #1327). Separate
    from both the runs ledger and the artifacts ledger — THE FLYWHEEL'S OWN EVAL: how big the library
    is, how much it's reused, how often promotion passes, and (once HV4 wires it) what fraction of a
    run's beats came from the library instead of fresh AI generation — the "less AI dependence" trend
    the epic names, read as a number over time instead of eyeballed."""
    rows = fetch_library_metrics(db_path)
    out: list[str] = []
    out.append("# WorldOS Library Metrics Ledger — the flywheel's own eval")
    out.append("")
    out.append(
        "> **Auto-generated from `qa/scores.db` (`library_metrics` table) — do not hand-edit.** "
        "Regenerate with `python3 qa/scores_db.py --render-library-metrics`. Rows are appended via "
        "`qa/scores_db.add_library_metrics(...)`, called by `qa/library_metrics.py`'s "
        "`snapshot_library()` (its sole writer). One row per SNAPSHOT of the harvest loop's own "
        "health — library size, Σreuse_count, promotion pass-rate, and (once HV4 wires it) "
        "%library-sourced beats per run."
    )
    out.append(
        "> **Promo pass%** = promoted / (promoted + rejected) over "
        "`library/.promoted.jsonl` (promote.py's processed-log); blank when no batch has run yet. "
        "**Lib-sourced%** stays blank until HV4 wires per-run library-vs-fresh-gen attribution — "
        "an unset column here is today's expected state, not a bug."
    )
    out.append(f"> Rows: **{len(rows)}** · rendered {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    out.append("")
    header = "| " + " | ".join(label for _, label in _LIBRARY_METRICS_MD_COLS) + " |"
    sep = "|" + "|".join("---" for _ in _LIBRARY_METRICS_MD_COLS) + "|"
    out.append(header)
    out.append(sep)
    for r in rows:
        cells = []
        for key, _ in _LIBRARY_METRICS_MD_COLS:
            v = r.get(key)
            if key in ("promotion_pass_rate", "pct_library_sourced") and v is not None:
                v = f"{float(v) * 100:.0f}%"
            cells.append(_fmt(v))
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    text = "\n".join(out)
    Path(md_path).write_text(text, encoding="utf-8")
    return text


def compare_rc(db_path: Path | str = DB_PATH, rc: Optional[str] = None,
               include_rc_surface: bool = False) -> str:
    """The release-candidate quality view: engine-duo lens scores, FENCED by the LENS ruler (#725)
    so numbers are only lined up when they're actually comparable. This resolves the "we used to hit
    4.5, now 3.6" confusion — rows scored under different rulers (a rubric recalibration or an added
    gate) appear in separate blocks (a re-baseline, not a trend). The lens trend fences on
    ``lens_config_version`` (the 8 files that PRODUCE the lens numbers — an RRI-gate-only edit no
    longer false-fences it), falling back to the full ``scoring_config_version`` for rows recorded
    before lens stamping (conservative: may split more than needed, never falsely merges). With
    ``rc`` set, restrict to runs that scored that release candidate (e.g. ``v1.0.4-rc1``). With
    ``include_rc_surface``, append the GUI-built-app RC rows (the RRI sweeps) as their OWN blocks,
    fenced on the FULL ruler — the RRI is produced by release_readiness.py, which IS in that ruler."""
    all_rows = fetch_rows(db_path)
    rows = [r for r in all_rows if r.get("surface") == "engine-duo"]
    if rc:
        rows = [r for r in rows if (r.get("rc_label") or "") == rc]
    groups: dict[str, list] = {}
    order: list[str] = []
    for r in rows:
        key = (r.get("lens_config_version") or r.get("scoring_config_version")
               or "(unstamped — pre-versioning)")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    cur_lens = lens_config_version()
    cur_full = scoring_config_version()
    out = ["WorldOS quality trend — surface=engine-duo, FENCED by LENS ruler (lc_…; sc_/unstamped = pre-lens fallback).",
           "Only compare numbers WITHIN a block; across blocks the ruler changed."]
    if rc:
        out.append(f"release candidate filter: {rc}")
    out.append("")

    def _g(r: dict, k: str) -> str:
        v = r.get(k)
        return "" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))

    for key in order:
        grp = groups[key]
        tag = "   <<< CURRENT ruler" if key in (cur_lens, cur_full) else ""
        out.append(f"=== ruler {key}{tag}  ({len(grp)} run(s)) ===")
        out.append(f"  {'rc / build':<24}{'dm_model':<16}{'story':>6}{'mech':>6}{'angry':>6}{'beh':>5}{'rri':>5}")
        for r in reversed(grp):  # fetch_rows is newest-first → chronological
            lbl = r.get("rc_label") or str(r.get("build_sha") or "?")
            out.append(
                f"  {str(lbl)[:23]:<24}{str(r.get('dm_model') or '')[:15]:<16}"
                f"{_g(r,'story_overall'):>6}{_g(r,'mech_overall'):>6}{_g(r,'angrydm_overall'):>6}"
                f"{str(r.get('behavioral') or '')[:4]:>5}{_g(r,'rri'):>5}"
            )
        out.append("")
    if len(order) > 1:
        out += ["NOTE: the blocks above used DIFFERENT rulers — a number in one block is NOT directly",
                "comparable to one in another. To anchor across rulers, re-score an archived transcript",
                "under the current ruler (qa/score.sh is generic over <transcript> <state> <rubric> <schema>).",
                ""]

    if include_rc_surface:
        rc_rows = [r for r in all_rows if r.get("surface") == "GUI-built-app"]
        if rc:
            rc_rows = [r for r in rc_rows if (r.get("rc_label") or "") == rc]
        rc_groups: dict[str, list] = {}
        rc_order: list[str] = []
        for r in rc_rows:
            key = r.get("scoring_config_version") or "(unstamped — pre-versioning)"
            if key not in rc_groups:
                rc_groups[key] = []
                rc_order.append(key)
            rc_groups[key].append(r)
        out += ["RC surface (GUI-built-app) — RRI/sat trend, FENCED by the FULL scoring ruler",
                "(release_readiness.py included; an RRI-gate edit IS a new ruler here).", ""]
        for key in rc_order:
            grp = rc_groups[key]
            tag = "   <<< CURRENT ruler" if key == cur_full else ""
            out.append(f"--- rc-surface ruler {key}{tag}  ({len(grp)} run(s)) ---")
            out.append(f"  {'rc / build':<24}{'dm_model':<16}{'rri':>6}{'sat':>6}{'crit':>6}{'beh':>6}{'pass':>6}")
            for r in reversed(grp):
                lbl = r.get("rc_label") or str(r.get("build_sha") or "?")
                pv = r.get("pass")
                out.append(
                    f"  {str(lbl)[:23]:<24}{str(r.get('dm_model') or '')[:15]:<16}"
                    f"{_g(r,'rri'):>6}{_g(r,'cross_persona_sat'):>6}{_g(r,'critical_bugs'):>6}"
                    f"{str(r.get('behavioral') or '')[:5]:>6}"
                    f"{('' if pv is None else ('PASS' if int(pv) else 'FAIL')):>6}"
                )
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Phase-3 observability reader (1): trends_json — machine-readable per-field time-series
# ---------------------------------------------------------------------------
# Lets the agent ask "story trend over the last N runs?" in ONE call instead of hand-reading the
# ledger. PURE READER over the same rows fetch_rows returns; additive (no schema change). Points are
# emitted OLDEST-FIRST (chronological, the natural reading order for a trend) — the opposite of
# fetch_rows' newest-first, which is for the human table. Optional fences (surface / lens ruler) let
# the caller line up an apples-to-apples series; an unset fence == every row (today's behavior).
TREND_FIELDS_DEFAULT: tuple[str, ...] = (
    "story_overall", "mech_overall", "angrydm_overall", "rri", "s_per_beat", "coldopen_s",
)


def trends_json(
    db_path: Path | str = DB_PATH,
    *,
    fields: Optional[list[str]] = None,
    surface: Optional[str] = None,
    lens_config_version: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    """Return a machine-readable per-field time-series so a trend can be read in one call.

    Shape::

        {
          "fields": ["story_overall", ...],            # the metrics being tracked
          "fence":  {"surface": ..., "lens_config_version": ..., "limit": ...},
          "points": [                                  # OLDEST-FIRST (chronological)
            {"run_id": ..., "ts": ..., "build_sha": ..., "rc_label": ...,
             "<field>": value, ...},                   # one entry per requested field (None if unscored)
            ...
          ],
        }

    ``fields`` defaults to :data:`TREND_FIELDS_DEFAULT` (story/mech/angrydm/rri/s_per_beat/coldopen_s);
    an unknown field raises (a typo is caught, not silently dropped). ``surface`` and
    ``lens_config_version`` are optional fences — when set, only rows matching that exact value are
    included (the comparability axes that fence a quality trend). ``limit`` keeps the N MOST-RECENT
    matching runs (then re-orders them oldest-first). With no fence and no limit the series is every
    row (additive: empty/unset == today). READ-ONLY — never writes the db.
    """
    flds = list(fields) if fields is not None else list(TREND_FIELDS_DEFAULT)
    unknown = [f for f in flds if f not in COLUMNS]
    if unknown:
        raise ValueError(f"unknown trend field(s) {unknown}; valid: {sorted(COLUMNS)}")

    rows = fetch_rows(db_path)  # newest-first
    if surface is not None:
        rows = [r for r in rows if r.get("surface") == surface]
    if lens_config_version is not None:
        rows = [r for r in rows if r.get("lens_config_version") == lens_config_version]
    if limit is not None:
        rows = rows[:limit]  # fetch_rows is newest-first → the N most recent
    rows = list(reversed(rows))  # emit oldest-first (chronological trend order)

    points: list[dict] = []
    for r in rows:
        pt: dict[str, Any] = {
            "run_id": r.get("run_id"),
            "ts": r.get("ts"),
            "build_sha": r.get("build_sha"),
            "rc_label": r.get("rc_label"),
        }
        for f in flds:
            pt[f] = r.get(f)
        points.append(pt)

    return {
        "fields": flds,
        "fence": {
            "surface": surface,
            "lens_config_version": lens_config_version,
            "limit": limit,
        },
        "points": points,
    }


# ---------------------------------------------------------------------------
# Phase-3 observability reader (2): reconcile — READ-ONLY ledger <-> INDEX.jsonl consistency
# ---------------------------------------------------------------------------
# Reports runs in the scores ledger missing from qa/INDEX.jsonl and vice-versa, so a drift between
# the two catalogs surfaces in one call. TOLERANT READER of INDEX.jsonl: it is JSONL with ARBITRARY
# keys (an open sibling PR #573 is reshaping run-naming), so this assumes ONLY "a per-line JSON object
# with some run-id field" — it tries several known id-key names, SKIPS (and reports) any line it can't
# parse or that carries no recognizable id, and NEVER rewrites INDEX.jsonl. Strictly read-only on both
# sides (the engine/harness owns INDEX.jsonl; the ledger is appended via add_run, never here).

# Candidate run-id key names, in priority order. The real INDEX.jsonl uses "id" (a row also carries
# "kind":"run"); "run_id"/"run"/"run_name" are accepted defensively so a PR-#573 rename doesn't make
# every row look orphaned. The FIRST present non-empty string key wins.
_INDEX_ID_KEYS: tuple[str, ...] = ("run_id", "id", "run", "run_name")


def _index_run_id(obj: dict) -> Optional[str]:
    """Best-effort extract a run id from one INDEX.jsonl object; None if no recognizable id."""
    for k in _INDEX_ID_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def reconcile(db_path: Path | str = DB_PATH, index_path: Path | str = QA_DIR / "INDEX.jsonl") -> dict:
    """READ-ONLY consistency check between the scores ledger and ``qa/INDEX.jsonl``.

    Returns::

        {
          "in_ledger_not_index": [run_id, ...],   # scored but not catalogued in the index (sorted)
          "in_index_not_ledger": [run_id, ...],   # catalogued but never scored into the ledger (sorted)
          "matched_count":  int,                  # run ids present in BOTH
          "ledger_count":   int,                  # distinct run ids in scores.db
          "index_count":    int,                  # distinct run ids parsed from INDEX.jsonl
          "skipped_lines":  [ {"line": int, "reason": str, "raw": str}, ... ],  # tolerant warnings
        }

    The INDEX reader is deliberately tolerant: each line must be a JSON OBJECT carrying one of
    :data:`_INDEX_ID_KEYS` (``run_id`` / ``id`` / ``run`` / ``run_name``). Blank lines, malformed
    JSON, non-object JSON, and objects with no recognizable id are SKIPPED and reported in
    ``skipped_lines`` (never raise). A missing index file is treated as an empty index (every ledger
    row becomes a ledger-only orphan). This function NEVER writes either file.
    """
    ledger_ids = {r["run_id"] for r in fetch_rows(db_path)}

    index_ids: set[str] = set()
    skipped: list[dict] = []
    idx = Path(index_path)
    if idx.exists():
        with idx.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue  # blank line — not an error, not a row
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    skipped.append({"line": lineno, "reason": "unparseable JSON",
                                    "raw": line[:200]})
                    continue
                if not isinstance(obj, dict):
                    skipped.append({"line": lineno, "reason": "not a JSON object",
                                    "raw": line[:200]})
                    continue
                rid = _index_run_id(obj)
                if rid is None:
                    skipped.append({"line": lineno, "reason": "no recognizable run-id key",
                                    "raw": line[:200]})
                    continue
                index_ids.add(rid)

    return {
        "in_ledger_not_index": sorted(ledger_ids - index_ids),
        "in_index_not_ledger": sorted(index_ids - ledger_ids),
        "matched_count": len(ledger_ids & index_ids),
        "ledger_count": len(ledger_ids),
        "index_count": len(index_ids),
        "skipped_lines": skipped,
    }


# ---------------------------------------------------------------------------
# Versioning Phase-1: machine-readable release_readiness_verdict.json
# ---------------------------------------------------------------------------
# The 11 canonical RRI gates (qa/release_readiness.py: LLM_PERSONA_GATES + the 4 base
# DETERMINISTIC_GATES, EXCLUDING the additive opt-in gates story_engagement + the two latency
# gates, which are evidence-gap SKIPs when absent and would otherwise make a clean RRI.json
# read as "missing" gates). A gate that is not PASSED — failed, skipped, or simply absent from
# the RRI.json — is the signal a tag was cut WITHOUT a complete formal RRI (DEVELOPMENT, not
# RELEASE). This is the same 11-gate spine qa/generate_release_notes.py reasons over.
RRI_CANONICAL_GATES: tuple[str, ...] = (
    "native_gate",
    "arc_completed",
    "cross_persona_sat",
    "no_give_up",
    "zero_critical",
    "story_craft",
    "mechanical",
    "behavioral",
    "ui_audit",
    "image_render",
    "palette_live",
)


def _classify_gate(name: str, rri: dict) -> str:
    """Classify ONE canonical gate from a release_readiness.py RRI.json payload.

    Returns "PASSED" / "FAILED" / "SKIPPED" / "MISSING". The RRI.json carries the booleans
    indirectly: a gate is FAILED if it is listed in ``failed_gates``, SKIPPED if in
    ``skipped_gates``, MISSING if the run produced no gate evidence at all (no gates_total),
    else PASSED. ``failed_gates`` may also carry the synthetic ``missing_personas`` /
    ``missing_release_personas`` entries, which are not real gates and are ignored here."""
    failed = set(rri.get("failed_gates") or [])
    skipped = set(rri.get("skipped_gates") or [])
    if name in skipped:
        return "SKIPPED"
    if name in failed:
        return "FAILED"
    # No evaluated gates at all (e.g. an empty/aborted rollup) -> every gate is unproven.
    if not rri.get("gates_total"):
        return "MISSING"
    return "PASSED"


def release_readiness_verdict(
    rri_json: Path | str,
    *,
    db_path: Path | str = DB_PATH,
    out_path: Optional[Path | str] = None,
    build_sha: Optional[str] = None,
) -> dict:
    """Emit a machine-readable release-readiness verdict (the 11 gate results + ruler versions
    + build SHA + timestamp) so every tag can link to it. ADDITIVE + READ-ONLY: this reads an
    existing ``release_readiness.py`` RRI.json (the authoritative per-gate source) and the scores
    ledger (for ruler provenance), and NEVER mutates the committed db (no rows, no schema change).

    The DEVELOPMENT-vs-RELEASE flag is the crux: if ANY of the 11 canonical gates is not PASSED
    (FAILED / SKIPPED / MISSING), the verdict is **DEVELOPMENT** (a tag cut without a complete
    formal RRI); only all-11-PASSED is **RELEASE**.

    Ruler versions (``scoring_config_version`` / ``lens_config_version``) are taken, in order, from
    the RRI.json itself (if it stamped them), then from the matching ledger row (matched by build
    SHA), then from the CURRENT rulers as a last resort (clearly labelled ``ruler_source``).

    Returns the verdict dict; if ``out_path`` is given, also writes it there as indented JSON.
    """
    rri_path = Path(rri_json)
    try:
        rri = json.loads(rri_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read RRI JSON {rri_path}: {exc}") from exc
    if not isinstance(rri, dict):
        raise ValueError(f"RRI JSON {rri_path} root is not an object")

    sha = (build_sha or rri.get("build_sha") or "").strip()

    gate_detail = rri.get("gate_detail") if isinstance(rri.get("gate_detail"), dict) else {}
    gates = {
        name: {
            "status": _classify_gate(name, rri),
            "detail": gate_detail.get(name, ""),
        }
        for name in RRI_CANONICAL_GATES
    }
    not_passed = [n for n, g in gates.items() if g["status"] != "PASSED"]
    skipped = sorted(n for n, g in gates.items() if g["status"] == "SKIPPED")
    failed = sorted(n for n, g in gates.items() if g["status"] == "FAILED")
    missing = sorted(n for n, g in gates.items() if g["status"] == "MISSING")
    status = "RELEASE" if not not_passed else "DEVELOPMENT"

    # Ruler provenance: RRI.json first, then the ledger row at this SHA, then current rulers.
    scv = rri.get("scoring_config_version")
    lcv = rri.get("lens_config_version")
    ruler_source = "rri_json"
    if not scv:
        match = None
        if sha:
            for r in fetch_rows_readonly(db_path):
                rb = str(r.get("build_sha") or "")
                if rb and (rb == sha or rb.startswith(sha) or sha.startswith(rb)):
                    match = r
                    break
        if match is not None:
            scv = match.get("scoring_config_version")
            lcv = lcv or match.get("lens_config_version")
            ruler_source = "scores_ledger"
        else:
            scv = scoring_config_version()
            lcv = lcv or lens_config_version()
            ruler_source = "current_rulers"

    verdict = {
        "schema": "worldos.release-readiness-verdict.v1",
        "status": status,  # RELEASE | DEVELOPMENT
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build_sha": sha,
        "rri": rri.get("rri"),
        "rri_status": rri.get("status"),
        "release_ready": bool(rri.get("release_ready")),
        "gates_passed": sum(1 for g in gates.values() if g["status"] == "PASSED"),
        "gates_total": len(RRI_CANONICAL_GATES),
        "gates": gates,
        "gates_not_passed": not_passed,
        "gates_failed": failed,
        "gates_skipped": skipped,
        "gates_missing": missing,
        "scoring_config_version": scv,
        "lens_config_version": lcv,
        "ruler_source": ruler_source,
        "source_rri_json": str(rri_path),
    }

    if out_path is not None:
        Path(out_path).write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DB_PATH), help="path to scores.db")
    p.add_argument("--init", action="store_true", help="create the db/schema (idempotent) and exit")
    p.add_argument("--render", action="store_true", help="regenerate qa/scores_ledger.md from the db")
    p.add_argument("--list", action="store_true", help="print rows (newest first) as JSON")
    p.add_argument("--add", action="store_true", help="append one run from the --field flags below")
    p.add_argument("--compare", action="store_true",
                   help="print the engine-duo quality trend, FENCED by LENS ruler (the honest rc1-vs-rc2-vs-4.5-era view)")
    p.add_argument("--rc", default=None,
                   help="with --compare: restrict to runs that scored this release candidate (e.g. v1.0.4-rc1)")
    p.add_argument("--compare-rc-surface", action="store_true",
                   help="with --compare: also show GUI-built-app RC rows (RRI sweeps) in their own "
                        "blocks, fenced on the FULL scoring ruler")
    # --- Phase-3 observability readers (pure readers; print JSON to stdout) ---
    p.add_argument("--trends-json", action="store_true",
                   help="print a machine-readable per-field time-series (oldest-first) as JSON; "
                        "fence with --surface / --lens-config-version, cap with --trends-limit, "
                        "pick metrics with --trends-fields")
    p.add_argument("--trends-fields", default=None,
                   help="with --trends-json: comma-separated metric columns (default: "
                        "story_overall,mech_overall,angrydm_overall,rri,s_per_beat,coldopen_s)")
    p.add_argument("--trends-limit", type=int, default=None,
                   help="with --trends-json: keep only the N most-recent matching runs")
    p.add_argument("--reconcile", action="store_true",
                   help="READ-ONLY consistency check between qa/INDEX.jsonl and scores.db; print "
                        "the orphan lists (ledger-only / index-only) as JSON")
    p.add_argument("--index", default=str(QA_DIR / "INDEX.jsonl"),
                   help="with --reconcile: path to INDEX.jsonl (default qa/INDEX.jsonl)")
    # --- Versioning Phase-1: emit the machine-readable release_readiness_verdict.json ---
    p.add_argument("--release-verdict", dest="release_verdict_rri", default=None,
                   metavar="RRI_JSON",
                   help="emit a machine-readable release_readiness_verdict.json (11 gate results "
                        "+ ruler versions + build SHA + timestamp) from a release_readiness.py "
                        "RRI.json; pair with --release-verdict-out to write it (default stdout)")
    p.add_argument("--release-verdict-out", default=None,
                   help="with --release-verdict: write the verdict JSON to this path")
    p.add_argument("--run-id")
    for col in COLUMNS:
        p.add_argument(f"--{col.replace('_', '-')}", dest=col, default=None)
    # --- HV1 artifacts table (separate; --artifact-<col> flags to avoid colliding with runs cols) ---
    p.add_argument("--add-artifact", action="store_true",
                   help="append one artifact score from the --artifact-* flags below")
    p.add_argument("--list-artifacts", action="store_true", help="print artifact rows (newest first) as JSON")
    p.add_argument("--render-artifacts", action="store_true",
                   help="regenerate qa/artifacts_ledger.md from the artifacts table")
    p.add_argument("--artifact-id", dest="artifact_id", default=None)
    for col in ARTIFACT_COLUMNS:
        p.add_argument(f"--artifact-{col.replace('_', '-')}", dest=f"artifact__{col}", default=None)
    # --- HV5 library_metrics table (separate; --libmetric-<col> flags) ---
    p.add_argument("--add-library-metrics", action="store_true",
                   help="append one library-health snapshot from the --libmetric-* flags below")
    p.add_argument("--list-library-metrics", action="store_true",
                   help="print library_metrics rows (newest first) as JSON")
    p.add_argument("--render-library-metrics", action="store_true",
                   help="regenerate qa/library_metrics_ledger.md from the library_metrics table")
    for col in LIBRARY_METRICS_COLUMNS:
        p.add_argument(f"--libmetric-{col.replace('_', '-')}", dest=f"libmetric__{col}", default=None)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    db = args.db

    if args.init:
        connect(db).close()
        print(f"initialized {db}")

    if args.add:
        if not args.run_id:
            raise SystemExit("--add requires --run-id")
        fields = {c: getattr(args, c) for c in COLUMNS if getattr(args, c) is not None}
        # cast numerics from CLI strings
        for c in _REAL_COLS:
            if c in fields:
                fields[c] = float(fields[c])
        for c in _INT_COLS:
            if c in fields:
                fields[c] = int(fields[c])
        add_run(args.run_id, db_path=db, **fields)
        print(f"added run {args.run_id}")

    if args.list:
        print(json.dumps(fetch_rows(db), indent=2, ensure_ascii=False))

    if args.add_artifact:
        if not args.artifact_id:
            raise SystemExit("--add-artifact requires --artifact-id")
        afields = {c: getattr(args, f"artifact__{c}") for c in ARTIFACT_COLUMNS
                   if getattr(args, f"artifact__{c}") is not None}
        for c in _ARTIFACT_REAL_COLS:
            if c in afields:
                afields[c] = float(afields[c])
        for c in _ARTIFACT_INT_COLS:
            if c in afields:
                afields[c] = int(afields[c])
        add_artifact(args.artifact_id, db_path=db, **afields)
        print(f"added artifact {args.artifact_id}")

    if args.list_artifacts:
        print(json.dumps(fetch_artifacts(db), indent=2, ensure_ascii=False))

    if args.add_library_metrics:
        lfields = {c: getattr(args, f"libmetric__{c}") for c in LIBRARY_METRICS_COLUMNS
                  if getattr(args, f"libmetric__{c}") is not None}
        for c in _LIBRARY_METRICS_REAL_COLS:
            if c in lfields:
                lfields[c] = float(lfields[c])
        for c in _LIBRARY_METRICS_INT_COLS:
            if c in lfields:
                lfields[c] = int(lfields[c])
        new_id = add_library_metrics(db_path=db, **lfields)
        print(f"added library_metrics snapshot id={new_id}")

    if args.list_library_metrics:
        print(json.dumps(fetch_library_metrics(db), indent=2, ensure_ascii=False))

    if args.render:
        render_markdown(db)
        print(f"rendered {MD_PATH} ({len(fetch_rows(db))} rows)")

    if args.render_artifacts:
        render_artifacts_markdown(db)
        print(f"rendered {ARTIFACTS_MD_PATH} ({len(fetch_artifacts(db))} rows)")

    if args.render_library_metrics:
        render_library_metrics_markdown(db)
        print(f"rendered {LIBRARY_METRICS_MD_PATH} ({len(fetch_library_metrics(db))} rows)")

    if args.compare:
        print(compare_rc(db, rc=args.rc, include_rc_surface=args.compare_rc_surface))

    if args.trends_json:
        flds = ([f.strip() for f in args.trends_fields.split(",") if f.strip()]
                if args.trends_fields else None)
        print(json.dumps(
            trends_json(db, fields=flds, surface=args.surface,
                        lens_config_version=args.lens_config_version, limit=args.trends_limit),
            indent=2, ensure_ascii=False,
        ))

    if args.reconcile:
        print(json.dumps(reconcile(db, args.index), indent=2, ensure_ascii=False))

    if args.release_verdict_rri:
        verdict = release_readiness_verdict(
            args.release_verdict_rri, db_path=db,
            out_path=args.release_verdict_out, build_sha=args.build_sha,
        )
        print(json.dumps(verdict, indent=2, ensure_ascii=False))
        if args.release_verdict_out:
            print(f"wrote {args.release_verdict_out}", file=sys.stderr)

    if not any([args.init, args.add, args.list, args.render, args.compare,
                args.trends_json, args.reconcile, args.release_verdict_rri,
                args.add_artifact, args.list_artifacts, args.render_artifacts,
                args.add_library_metrics, args.list_library_metrics,
                args.render_library_metrics]):
        _build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
