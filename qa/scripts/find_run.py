#!/usr/bin/env python3
"""Query qa/INDEX.jsonl for past QA artifacts.

This is the agent-facing surface. Agents and humans use it instead of
grepping the raw filesystem.

Examples:
    qa/scripts/find_run.py --since 2026-05-25 --gate red --failed
    qa/scripts/find_run.py --persona wayfarer --paths-only
    qa/scripts/find_run.py --sha 1057234
    qa/scripts/find_run.py --kind play-state --since 2026-06-01
    qa/scripts/find_run.py --scored --jsonl | jq .
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Iterable, Optional


def _parse_date(s: Optional[str]) -> Optional[_dt.datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = _dt.datetime.strptime(s, fmt)
            return dt.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
    raise SystemExit(f"error: cannot parse date {s!r} (use YYYY-MM-DD or ISO 8601)")


def _entry_ts(entry: dict) -> Optional[_dt.datetime]:
    ts = entry.get("timestamp_iso")
    if not ts:
        return None
    try:
        return _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def iter_entries(index_path: Path) -> Iterable[dict]:
    if not index_path.exists():
        raise SystemExit(
            f"error: {index_path} does not exist. Run `python3 qa/scripts/backfill_index.py` first."
        )
    with index_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def matches(entry: dict, args: argparse.Namespace) -> bool:
    if args.kind and entry.get("kind") != args.kind:
        return False
    if args.sha and (entry.get("commit_sha") or "") != args.sha:
        return False
    if args.world and entry.get("world") != args.world:
        return False
    if args.persona and entry.get("persona") != args.persona:
        return False
    if args.provider and entry.get("provider") != args.provider:
        return False
    if args.scenario and entry.get("scenario") != args.scenario:
        return False
    if args.surface and (entry.get("surface") or "").find(args.surface) < 0:
        return False
    if args.id and args.id not in entry.get("id", ""):
        return False

    if args.since:
        ets = _entry_ts(entry)
        if ets is None or ets < args.since:
            return False
    if args.until:
        ets = _entry_ts(entry)
        if ets is None or ets > args.until:
            return False

    run_only_filter = args.failed or args.completed or args.gave_up
    if run_only_filter and entry.get("kind") != "run":
        return False
    score = entry.get("score") or {}
    if args.failed:
        if score.get("pass") is True:
            return False
    if args.completed:
        if not score.get("completed_intro_flow"):
            return False
    if args.gave_up:
        if not score.get("gave_up"):
            return False
    if args.min_sat is not None:
        sat = score.get("persona_satisfaction")
        if sat is None or sat < args.min_sat:
            return False
    if args.max_sat is not None:
        sat = score.get("persona_satisfaction")
        if sat is None or sat > args.max_sat:
            return False
    if args.scored and not entry.get("scored_in_ledger"):
        return False
    if args.unscored and entry.get("scored_in_ledger"):
        return False
    if args.has_rubric and not entry.get("linked_rubrics"):
        return False
    if args.part_a_result:
        if entry.get("part_a_result") != args.part_a_result:
            return False
    return True


def format_line(entry: dict, args: argparse.Namespace) -> str:
    if args.jsonl:
        return json.dumps(entry, ensure_ascii=False)
    if args.paths_only:
        return entry.get("path", "")
    ts = (entry.get("timestamp_iso") or "?               ")[:19]
    kind = entry.get("kind", "?")[:4]
    sha = (entry.get("commit_sha") or "      ")[:7]
    persona = (entry.get("persona") or "")[:11]
    world = (entry.get("world") or "")[:14]
    extras = []
    if entry.get("kind") == "run":
        result = entry.get("part_a_result")
        if result:
            extras.append(f"A:{result}")
        score = entry.get("score") or {}
        if score.get("gave_up"):
            extras.append("GAVE_UP")
        sat = score.get("persona_satisfaction")
        if sat is not None:
            extras.append(f"sat={sat}")
        ledger = entry.get("scored_in_ledger") or {}
        if ledger:
            story = ledger.get("story_overall")
            mech = ledger.get("mech_overall")
            if story is not None:
                extras.append(f"story={story}")
            if mech is not None:
                extras.append(f"mech={mech}")
    elif entry.get("kind") == "play-state":
        cc = entry.get("campaign_count")
        if cc:
            extras.append(f"camps={cc}")
        cl = entry.get("chat_lines")
        if cl:
            extras.append(f"chat={cl}")
    elif entry.get("kind") == "transcript":
        if entry.get("role"):
            extras.append(entry["role"])
        if entry.get("line_count") is not None:
            extras.append(f"lines={entry['line_count']}")
    extras_str = " ".join(extras)
    line = f"{ts} {kind} {sha} {persona:<11} {world:<14} {entry.get('id', '')[:60]:<60} {extras_str}"
    if not args.no_paths:
        line += f"  → {entry.get('path', '')}"
    return line


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Query qa/INDEX.jsonl for past QA artifacts.",
        epilog=(
            "Examples:\n"
            "  find_run.py --since 2026-05-25 --failed\n"
            "  find_run.py --persona newbie --paths-only\n"
            "  find_run.py --sha 1057234 --jsonl\n"
            "  find_run.py --kind play-state --since 2026-06-01\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--index", help="INDEX.jsonl path (default: <cwd>/qa/INDEX.jsonl)")
    parser.add_argument("--root", help="Repo root (used to find INDEX.jsonl)")
    parser.add_argument("--kind", choices=["run", "play-state", "transcript"])
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--sha", help="Filter by exact commit_sha (7-char)")
    parser.add_argument("--world")
    parser.add_argument("--persona")
    parser.add_argument("--provider")
    parser.add_argument("--scenario")
    parser.add_argument("--surface", help="Substring match on surface (e.g. 'GUI-built-app')")
    parser.add_argument("--id", dest="id", help="Substring match on entry id")
    parser.add_argument("--gate", choices=["green", "red"], help="(reserved) filter by behavioral gate")
    parser.add_argument("--failed", action="store_true", help="score.pass != true")
    parser.add_argument("--completed", action="store_true", help="completed_intro_flow == true")
    parser.add_argument("--gave-up", action="store_true", dest="gave_up")
    parser.add_argument("--part-a-result", choices=["PASS", "FAIL", "skipped"], dest="part_a_result")
    parser.add_argument("--min-sat", type=int, dest="min_sat")
    parser.add_argument("--max-sat", type=int, dest="max_sat")
    parser.add_argument("--scored", action="store_true",
                        help="Has a matching curated row in qa/scores.db")
    parser.add_argument("--unscored", action="store_true",
                        help="No matching curated row in qa/scores.db")
    parser.add_argument("--has-rubric", action="store_true", dest="has_rubric",
                        help="Has linked lens rubric (tolkien/angrydm)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--paths-only", action="store_true", dest="paths_only")
    parser.add_argument("--no-paths", action="store_true", dest="no_paths",
                        help="Don't append → path to default output")
    parser.add_argument("--jsonl", action="store_true", help="Raw JSONL passthrough")
    parser.add_argument("--reverse", action="store_true", help="Oldest first (default newest first)")
    parser.add_argument("--count", action="store_true", help="Print matching count only")
    args = parser.parse_args(argv)

    repo_root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    index_path = Path(args.index).resolve() if args.index else repo_root / "qa" / "INDEX.jsonl"
    args.since = _parse_date(args.since)
    args.until = _parse_date(args.until)

    matched = [e for e in iter_entries(index_path) if matches(e, args)]
    matched.sort(key=lambda e: e.get("timestamp_iso") or "", reverse=not args.reverse)
    if args.limit > 0:
        matched = matched[: args.limit]

    if args.count:
        print(len(matched))
        return 0

    for e in matched:
        print(format_line(e, args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
