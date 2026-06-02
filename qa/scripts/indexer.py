#!/usr/bin/env python3
"""QA artifact indexer.

Reads a single artifact dir or transcript file and returns one dict suitable
for `qa/INDEX.jsonl`. Designed to be called by:
  - the playtest runners (auto-append at end of run, best-effort)
  - the backfill script (rebuild INDEX.jsonl from scratch)
  - find_run.py (reads only, no writes)

Stdlib-only. Treats every extracted field as optional; missing → null, no crash.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

CANONICAL_NAME_RE = re.compile(
    r"^(?P<ts>\d{8}T\d{6}Z)-(?P<sha>[0-9a-f]{7,12})-(?P<world>[a-z0-9-]+)-(?P<persona>[a-z0-9]+)-(?P<provider>[a-z0-9]+)-(?P<scenario>[a-z0-9-]+)$"
)
ISO_TS_RE = re.compile(r"(\d{8}T\d{6}Z)")
SHA7_RE = re.compile(r"\b([0-9a-f]{7})\b")
KNOWN_PERSONAS = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
KNOWN_WORLDS = ("baldurs-gate", "tidal-commonwealth", "sundered-reach")


def _read_json(path: Path) -> Optional[dict]:
    try:
        with path.open() as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _ndjson_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open() as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_canonical_name(name: str) -> dict:
    """Extract structured fields from a dir/file name.

    Returns whatever it can parse. Canonical form populates all fields; other
    forms get partial credit (ts/sha extracted by regex; persona by suffix match).
    """
    out: dict[str, Any] = {}
    m = CANONICAL_NAME_RE.match(name)
    if m:
        out.update(m.groupdict())
        out["canonical"] = True
        return out
    out["canonical"] = False
    ts_match = ISO_TS_RE.search(name)
    if ts_match:
        out["ts"] = ts_match.group(1)
    sha_match = SHA7_RE.search(name)
    if sha_match:
        out["sha"] = sha_match.group(1)
    for persona in KNOWN_PERSONAS:
        if persona in name.lower():
            out["persona"] = persona
            break
    for world in KNOWN_WORLDS:
        if world in name.lower():
            out["world"] = world
            break
    return out


def _ts_to_iso(ts: str) -> Optional[str]:
    """Convert YYYYMMDDTHHMMSSZ → 2026-06-02T05:30:15Z."""
    try:
        dt = _dt.datetime.strptime(ts, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _scores_db_lookup(run_id: str, repo_root: Path) -> Optional[dict]:
    """Look up curated scores from qa/scores.db if a row matches run_id."""
    db_path = repo_root / "qa" / "scores.db"
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT story_overall, mech_overall, angrydm_overall, "
                "behavioral, rri, critical_bugs, image_render_rate, pass, "
                "surface, dm_model, scorer_model "
                "FROM runs WHERE run_id = ? LIMIT 1",
                (run_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {k: row[k] for k in row.keys()}
    except sqlite3.Error:
        return None


def _linked_transcripts(run_id: str, repo_root: Path) -> list[str]:
    """Find qa/transcripts/<run_id>*.jsonl files."""
    transc_dir = repo_root / "qa" / "transcripts"
    if not transc_dir.exists() or not run_id:
        return []
    matches = sorted(transc_dir.glob(f"{run_id}*.jsonl"))
    return [str(p.relative_to(repo_root)) for p in matches]


def _linked_lens_rubrics(run_id: str, commit_sha: Optional[str], repo_root: Path) -> dict:
    """Find lens scoring artifacts in qa/transcripts/.

    Tries exact run_id match first (e.g. <run_id>.tolkien.json), then falls back
    to sha-proximity (e.g. gate-<sha>-duo.tolkien.json shares the sha).
    Returns paths only — agents read the file for full scores.
    """
    transc_dir = repo_root / "qa" / "transcripts"
    if not transc_dir.exists():
        return {}
    out: dict[str, str] = {}
    for lens in ("tolkien", "angrydm", "score", "state"):
        exact = transc_dir / f"{run_id}.{lens}.json"
        if exact.exists():
            out[lens] = str(exact.relative_to(repo_root))
            continue
        if commit_sha:
            duo = transc_dir / f"gate-{commit_sha}-duo.{lens}.json"
            if duo.exists():
                out[lens] = str(duo.relative_to(repo_root))
    return out


def _linked_play_state(run_json: Optional[dict], run_id: str, repo_root: Path) -> Optional[str]:
    """Find a play-state dir linked to this run.

    Prefer run.json.part_a.minted_run_dir; fall back to play-state/<run_id>.
    """
    ps_dir = repo_root / "play-state"
    if not ps_dir.exists():
        return None
    if run_json:
        minted = (run_json.get("part_a") or {}).get("minted_run_dir")
        if minted:
            cand = ps_dir / minted
            if cand.exists():
                return str(cand.relative_to(repo_root))
    direct = ps_dir / run_id
    if direct.exists():
        return str(direct.relative_to(repo_root))
    return None


def extract_run(run_dir: Path, repo_root: Path) -> Optional[dict]:
    """Build an INDEX entry for one qa/ui_playtest_runs/<dir>."""
    if not run_dir.is_dir():
        return None
    run_id = run_dir.name
    run_json = _read_json(run_dir / "run.json")
    meta_json = _read_json(run_dir / "meta.json")
    score_json = _read_json(run_dir / "score.json")
    parsed_name = parse_canonical_name(run_id)

    src = run_json or meta_json or {}
    build_sha = src.get("build_sha") or parsed_name.get("sha")
    persona = src.get("persona") or parsed_name.get("persona")
    world = src.get("world") or parsed_name.get("world")
    timestamp_iso = (
        src.get("at")
        or src.get("finished_at")
        or _ts_to_iso(parsed_name.get("ts", ""))
        or _mtime_iso(run_dir)
    )

    entry: dict[str, Any] = {
        "kind": "run",
        "id": run_id,
        "path": str(run_dir.relative_to(repo_root)),
        "timestamp_iso": timestamp_iso,
        "commit_sha": build_sha[:7] if build_sha else None,
        "version": src.get("version"),
        "world": world,
        "persona": persona,
        "provider": parsed_name.get("provider")
            or ((run_json.get("part_b") or {}).get("provider") if run_json else None),
        "scenario": parsed_name.get("scenario"),
        "surface": src.get("surface"),
        "beats_cap": src.get("beats_cap"),
        "budget_usd": src.get("budget_usd"),
        "canonical_name": parsed_name.get("canonical", False),
    }

    if run_json:
        part_a = run_json.get("part_a") or {}
        part_b = run_json.get("part_b") or {}
        entry["part"] = run_json.get("part")
        entry["part_a_result"] = part_a.get("result")
        entry["part_b_persona_loop"] = part_b.get("persona_loop")
        entry["part_b_score_pass"] = part_b.get("score_pass")
        entry["spend_usd"] = (run_json.get("spend_usd") or {}).get("total")
        entry["minted_run_dir"] = part_a.get("minted_run_dir")
        entry["provider"] = entry["provider"] or part_b.get("provider")
        entry["player_agent"] = part_b.get("player_agent")

    if meta_json:
        entry.setdefault("player_cost_usd", meta_json.get("player_cost_usd"))
        entry.setdefault("player_rc", meta_json.get("player_rc"))
        entry.setdefault("port", meta_json.get("port"))

    if score_json:
        entry["score"] = {
            "completed_intro_flow": score_json.get("completed_intro_flow"),
            "reached_play_screen": score_json.get("reached_play_screen"),
            "actions_total": score_json.get("actions_total"),
            "in_story_turns": score_json.get("in_story_turns"),
            "console_errors": score_json.get("console_errors"),
            "network_failures": score_json.get("network_failures"),
            "image_404s": score_json.get("image_404s"),
            "gave_up": score_json.get("gave_up"),
            "persona_satisfaction": score_json.get("persona_satisfaction"),
            "satisfaction_source": score_json.get("satisfaction_source"),
            "pass": score_json.get("pass"),
        }
        entry["bug_counts"] = {
            "critical": score_json.get("bug_reports_critical"),
            "major": score_json.get("bug_reports_major"),
            "minor": score_json.get("bug_reports_minor"),
            "trivial": score_json.get("bug_reports_trivial"),
            "total": score_json.get("bug_reports_total"),
        }

    bugs_path = run_dir / "bugs.ndjson"
    if bugs_path.exists():
        entry.setdefault("bug_counts", {})
        entry["bug_counts"].setdefault("ndjson_lines", _ndjson_count(bugs_path))

    summary = run_dir / "summary.md"
    if summary.exists():
        entry["summary_md"] = str(summary.relative_to(repo_root))

    entry["linked_transcripts"] = _linked_transcripts(run_id, repo_root)
    entry["linked_play_state"] = _linked_play_state(run_json, run_id, repo_root)
    rubrics = _linked_lens_rubrics(run_id, entry.get("commit_sha"), repo_root)
    if rubrics:
        entry["linked_rubrics"] = rubrics

    scored = _scores_db_lookup(run_id, repo_root)
    if scored:
        entry["scored_in_ledger"] = scored

    entry["indexed_at"] = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return entry


def extract_play_state(ps_dir: Path, repo_root: Path) -> Optional[dict]:
    """Build an INDEX entry for one play-state/<dir>."""
    if not ps_dir.is_dir():
        return None
    name = ps_dir.name
    parsed = parse_canonical_name(name)
    timestamp_iso = _ts_to_iso(parsed.get("ts", "")) or _mtime_iso(ps_dir)

    entry: dict[str, Any] = {
        "kind": "play-state",
        "id": name,
        "path": str(ps_dir.relative_to(repo_root)),
        "timestamp_iso": timestamp_iso,
        "commit_sha": parsed.get("sha"),
        "world": parsed.get("world"),
        "persona": parsed.get("persona"),
        "canonical_name": parsed.get("canonical", False),
    }

    campaigns_dir = ps_dir / "campaigns"
    if campaigns_dir.exists():
        entry["campaign_count"] = sum(1 for _ in campaigns_dir.iterdir() if _.is_dir())

    chat_jsonl = ps_dir / "chat.jsonl"
    if chat_jsonl.exists():
        entry["chat_lines"] = _ndjson_count(chat_jsonl)

    moves_jsonl = ps_dir / "player_moves.jsonl"
    if moves_jsonl.exists():
        entry["player_moves"] = _ndjson_count(moves_jsonl)

    linked_run = (repo_root / "qa" / "ui_playtest_runs" / name).exists()
    if linked_run:
        entry["linked_run"] = f"qa/ui_playtest_runs/{name}"
    else:
        for run_dir in (repo_root / "qa" / "ui_playtest_runs").glob("*"):
            run_json = _read_json(run_dir / "run.json")
            if run_json and (run_json.get("part_a") or {}).get("minted_run_dir") == name:
                entry["linked_run"] = str(run_dir.relative_to(repo_root))
                break

    entry["indexed_at"] = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return entry


def extract_transcript(transc_path: Path, repo_root: Path) -> Optional[dict]:
    """Build an INDEX entry for one qa/transcripts/<file>."""
    if not transc_path.is_file():
        return None
    name = transc_path.name
    parts = name.split(".", 1)
    run_id = parts[0]
    suffix = parts[1] if len(parts) > 1 else ""

    parsed = parse_canonical_name(run_id)
    timestamp_iso = _ts_to_iso(parsed.get("ts", "")) or _mtime_iso(transc_path)

    role = None
    if ".chat." in name:
        role = "chat"
    elif ".dm." in name:
        role = "dm"
    elif ".player." in name:
        role = "player"

    entry: dict[str, Any] = {
        "kind": "transcript",
        "id": name,
        "path": str(transc_path.relative_to(repo_root)),
        "timestamp_iso": timestamp_iso,
        "commit_sha": parsed.get("sha"),
        "run_id": run_id,
        "role": role,
        "suffix": suffix,
        "line_count": _ndjson_count(transc_path),
    }
    linked = (repo_root / "qa" / "ui_playtest_runs" / run_id)
    if linked.exists():
        entry["linked_run"] = f"qa/ui_playtest_runs/{run_id}"
    entry["indexed_at"] = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return entry


def _iter_existing(index_path: Path) -> Iterable[dict]:
    if not index_path.exists():
        return
    with index_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def append_or_update(entry: dict, index_path: Path) -> str:
    """Append entry to INDEX.jsonl, or replace an existing same-id entry.

    Returns "appended" or "updated". File-locked for concurrent-runner safety.
    """
    if entry is None:
        return "skipped"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = index_path.with_suffix(index_path.suffix + ".lock")
    with lock_path.open("w") as lockfile:
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
        try:
            existing = list(_iter_existing(index_path))
            key = (entry.get("kind"), entry.get("id"))
            replaced = False
            for i, row in enumerate(existing):
                if (row.get("kind"), row.get("id")) == key:
                    existing[i] = entry
                    replaced = True
                    break
            if not replaced:
                existing.append(entry)
            tmp_path = index_path.with_suffix(index_path.suffix + ".new")
            with tmp_path.open("w") as out:
                for row in existing:
                    out.write(json.dumps(row, ensure_ascii=False))
                    out.write("\n")
            os.replace(tmp_path, index_path)
            return "updated" if replaced else "appended"
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)


def _repo_root_from(path: Path) -> Path:
    """Walk up to find repo root (contains qa/)."""
    p = path.resolve()
    for ancestor in [p] + list(p.parents):
        if (ancestor / "qa").is_dir() and (ancestor / ".git").exists():
            return ancestor
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Index a QA artifact dir or file.")
    parser.add_argument("path", help="Artifact dir or transcript file to index")
    parser.add_argument("--append", action="store_true",
                        help="Append/update INDEX.jsonl (default: print to stdout)")
    parser.add_argument("--index", help="Path to INDEX.jsonl (default: <root>/qa/INDEX.jsonl)")
    parser.add_argument("--root", help="Repo root (default: walk up from path)")
    args = parser.parse_args(argv)

    target = Path(args.path).resolve()
    repo_root = Path(args.root).resolve() if args.root else _repo_root_from(target)
    index_path = Path(args.index).resolve() if args.index else repo_root / "qa" / "INDEX.jsonl"

    if target.is_dir():
        try:
            rel = target.relative_to(repo_root)
        except ValueError:
            print(f"error: {target} is not under repo root {repo_root}", file=sys.stderr)
            return 2
        parts = rel.parts
        if parts and parts[0] == "qa" and len(parts) >= 2 and parts[1] == "ui_playtest_runs":
            entry = extract_run(target, repo_root)
        elif parts and parts[0] == "play-state":
            entry = extract_play_state(target, repo_root)
        else:
            print(f"error: unrecognized artifact dir kind under {target}", file=sys.stderr)
            return 2
    elif target.is_file() and target.suffix == ".jsonl":
        entry = extract_transcript(target, repo_root)
    else:
        print(f"error: {target} is not a directory or .jsonl file", file=sys.stderr)
        return 2

    if entry is None:
        print(f"error: could not extract entry from {target}", file=sys.stderr)
        return 1

    if args.append:
        result = append_or_update(entry, index_path)
        print(f"{result}: {entry['kind']}:{entry['id']} → {index_path}")
    else:
        print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
