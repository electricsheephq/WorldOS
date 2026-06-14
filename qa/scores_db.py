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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Sibling helper (qa/scoring_config_version.py): the content hashes of the scoring RULERS — the
# FULL ruler (rubrics + schemas + gates incl. RRI; ``sc_…``) and the LENS ruler (the 8 files that
# produce the lens numbers; ``lc_…``, #725). Stamped on every scored run so a number is always
# tagged with which ruler produced it — the fix for "we used to hit 4.5, now 3.6".
try:
    from scoring_config_version import scoring_config_version, scoring_config_label, lens_config_version
except ImportError:  # imported from a different cwd / as a package
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scoring_config_version import scoring_config_version, scoring_config_label, lens_config_version

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
QA_DIR = Path(__file__).resolve().parent
DB_PATH = QA_DIR / "scores.db"
MD_PATH = QA_DIR / "scores_ledger.md"

# Allowed surface values (validated on insert; the crux of the forensic story).
SURFACES = ("engine-duo", "GUI-built-app", "GUI-headless-proxy", "smoke-only")

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
    # --- comparability provenance (the missing stamps that broke cross-time comparison) ---
    "scoring_config_version",  # content hash of the FULL scoring RULER (rubrics+schemas+gates incl.
                               # RRI); see qa/scoring_config_version.py. Fences the RRI trend.
    "lens_config_version",     # content hash of the LENS ruler (#725): the 8 files that produce the
                               # story/mech/angry numbers (full ruler minus release_readiness.py).
                               # Fences the engine-duo quality trend. NULL on rows recorded before
                               # lens stamping (compare falls back to scoring_config_version).
    "rubric_label",       # human label for the ruler, e.g. "ruler@sc_a1b2c3d4e5f6 (9/9 files)"
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
    "pass",               # 1 (pass) / 0 (fail) / NULL (no pass/fail verdict)
    "source_path",        # where the evidence lives (file/dir, LEXAR or repo-relative)
    "notes",              # free-text context: what was under test, caveats, confidence flags
)

# Numeric columns get REAL; pass is an INTEGER bool; the rest TEXT.
_REAL_COLS = {
    "story_overall", "mech_overall", "angrydm_overall", "cross_persona_sat",
    "rri", "image_render_rate",
    # F13-4 latency ledger (all wall-clock seconds / turn counts → REAL)
    "s_per_beat", "coldopen_s", "turns_per_beat",
}
_INT_COLS = {"critical_bugs", "pass"}


def _coltype(col: str) -> str:
    if col in _REAL_COLS:
        return "REAL"
    if col in _INT_COLS:
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
    """Create the runs table if missing; additively ALTER in any new COLUMNS."""
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
    conn.commit()


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
    (so a typo is caught, not silently dropped). ``per_persona_json`` may be passed as a
    dict/list and is JSON-encoded automatically. ``surface`` is validated against
    :data:`SURFACES`. ``ts`` defaults to now (UTC, ISO8601) if omitted.
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
                             (r"\blc_[0-9a-f]{12}\b", "lens_config_version")):
            mismatched = sorted({h for h in re.findall(pattern, notes) if h != fields.get(col)})
            if mismatched:
                raise ValueError(
                    f"notes cite ruler hash(es) {mismatched} but the row's {col} is "
                    f"{fields.get(col)!r} — a notes claim must match the stamp (to reference "
                    f"another run's ruler, cite its run_id, not its hash)"
                )

    # JSON-encode structured per-persona payloads.
    ppj = fields.get("per_persona_json")
    if ppj is not None and not isinstance(ppj, str):
        fields["per_persona_json"] = json.dumps(ppj, ensure_ascii=False, sort_keys=True)

    # Coerce bool pass -> int.
    if isinstance(fields.get("pass"), bool):
        fields["pass"] = int(fields["pass"])

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
            elif key == "pass" and v is not None:
                v = "PASS" if int(v) else "FAIL"
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
    p.add_argument("--run-id")
    for col in COLUMNS:
        p.add_argument(f"--{col.replace('_', '-')}", dest=col, default=None)
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

    if args.render:
        render_markdown(db)
        print(f"rendered {MD_PATH} ({len(fetch_rows(db))} rows)")

    if args.compare:
        print(compare_rc(db, rc=args.rc, include_rc_surface=args.compare_rc_surface))

    if not any([args.init, args.add, args.list, args.render, args.compare]):
        _build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
