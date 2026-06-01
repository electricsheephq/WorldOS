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
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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
    "story_overall",      # Tolkien/story-craft lens (0-5), NULL if not scored
    "mech_overall",       # Mechanical lens (0-5), NULL if not scored
    "angrydm_overall",    # 5e-fidelity / Angry-DM lens (0-5), NULL if not scored
    "behavioral",         # GREEN / RED / NULL (deterministic behavioral gate)
    "cross_persona_sat",  # avg GUI persona satisfaction (0-10), NULL for non-GUI
    "per_persona_json",   # JSON: per-persona {sat, gaveup, crit, ...}
    "rri",                # Release-Readiness Index (0-10), NULL if not an RRI sweep
    "critical_bugs",      # count of critical bugs, NULL if not measured
    "image_render_rate",  # 0.0-1.0 image render success rate, NULL if not measured
    "pass",               # 1 (pass) / 0 (fail) / NULL (no pass/fail verdict)
    "source_path",        # where the evidence lives (file/dir, LEXAR or repo-relative)
    "notes",              # free-text context: what was under test, caveats, confidence flags
)

# Numeric columns get REAL; pass is an INTEGER bool; the rest TEXT.
_REAL_COLS = {
    "story_overall", "mech_overall", "angrydm_overall", "cross_persona_sat",
    "rri", "image_render_rate",
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
    ("methodology", "Methodology"),
    ("story_overall", "Story"),
    ("mech_overall", "Mech"),
    ("angrydm_overall", "AngryDM"),
    ("behavioral", "Behav"),
    ("cross_persona_sat", "Sat"),
    ("rri", "RRI"),
    ("critical_bugs", "Crit"),
    ("image_render_rate", "Img%"),
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

    if not any([args.init, args.add, args.list, args.render]):
        _build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
