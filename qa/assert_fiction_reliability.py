#!/usr/bin/env python3
"""Deterministic fiction/state reliability gate for local QA artifacts.

This is the read-only "trust the save file" layer: it checks local transcript/play
text, final state JSON, expectation JSON, and optional scorecards without importing
or mutating engine code.

Exit codes:
  0 = GREEN (warnings allowed)
  1 = RED (one or more release-fatal assertions failed)
  2 = usage / malformed CLI
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FATAL_DEFECTS = {"high", "critical"}


@dataclass
class ArtifactText:
    text: str = ""
    tools: set[str] | None = None
    supports_tools: bool = False


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, str(exc)


def _short_tool_name(name: str) -> str:
    return (name or "").split("__")[-1]


def _tool_names_from_obj(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        if obj.get("type") == "tool_use":
            found.add(_short_tool_name(str(obj.get("name") or "")))
        for value in obj.values():
            found.update(_tool_names_from_obj(value))
    elif isinstance(obj, list):
        for value in obj:
            found.update(_tool_names_from_obj(value))
    return {name for name in found if name}


def _text_from_obj(obj: Any) -> str:
    chunks: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "text" and isinstance(value, str):
                chunks.append(value)
            else:
                text = _text_from_obj(value)
                if text:
                    chunks.append(text)
    elif isinstance(obj, list):
        for value in obj:
            text = _text_from_obj(value)
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def _load_artifact_text(path: Path) -> ArtifactText:
    raw = path.read_text(encoding="utf-8")
    text_chunks: list[str] = []
    tools: set[str] = set()
    parsed_any_json_line = False

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        parsed_any_json_line = True
        tools.update(_tool_names_from_obj(obj))
        text = _text_from_obj(obj)
        if text:
            text_chunks.append(text)

    if parsed_any_json_line:
        return ArtifactText(text="\n".join(text_chunks) or raw, tools=tools, supports_tools=True)

    # Distilled markdown often contains tool markers even though ordinary play text
    # does not. Treat only explicit markers as tool-supported evidence.
    marker_tools = {
        _short_tool_name(match.group(1))
        for match in re.finditer(r"(?:mcp__(?:clawdnd|worldos)-engine__|tool[_ -]?use[:= ]+|→\s*tool\s+)([A-Za-z_][\w-]*)", raw)
    }
    return ArtifactText(text=raw, tools=marker_tools, supports_tools=bool(marker_tools))


def _expectation(path: Path | None) -> tuple[dict[str, Any], str | None]:
    if path is None:
        return {}, None
    data, err = _read_json(path)
    if err:
        return {}, err
    if not isinstance(data, dict):
        return {}, "expectation JSON must be an object"
    expect = data.get("expect", data)
    if not isinstance(expect, dict):
        return {}, "expectation JSON 'expect' must be an object"
    return expect, None


def _path_exists(data: Any, dotted: str) -> bool:
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
            continue
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
            continue
        return False
    return True


def _flatten_facts(data: Any, prefix: str = "") -> set[str]:
    facts: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            facts.update(_flatten_facts(value, path))
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            path = f"{prefix}.{idx}" if prefix else str(idx)
            facts.update(_flatten_facts(value, path))
    else:
        value = str(data).lower()
        key = prefix.rsplit(".", 1)[-1].lower()
        full = prefix.lower()
        facts.add(f"{full}={value}")
        facts.add(f"{key}={value}")
    return facts


def _scorecard_defects(paths: list[Path]) -> tuple[list[str], list[str]]:
    severe: list[str] = []
    errors: list[str] = []
    for path in paths:
        data, err = _read_json(path)
        if err:
            errors.append(f"{path.name}: {err}")
            continue
        defects = data.get("defects", []) if isinstance(data, dict) else []
        if not isinstance(defects, list):
            errors.append(f"{path.name}: defects is not a list")
            continue
        for defect in defects:
            if not isinstance(defect, dict):
                continue
            severity = str(defect.get("severity") or "").strip().lower()
            if severity in FATAL_DEFECTS:
                area = defect.get("area") or defect.get("kind") or "defect"
                evidence = defect.get("evidence") or defect.get("summary") or defect.get("suggested_fix") or ""
                severe.append(f"{path.name}: {severity} {area}" + (f" — {evidence}" if evidence else ""))
    return severe, errors


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _check(
    checks: list[dict[str, Any]],
    check_id: str,
    ok: bool,
    *,
    severity: str,
    evidence: str = "",
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "pass" if ok else ("warn" if severity == "warning" else "fail"),
            "severity": severity,
            "evidence": evidence,
        }
    )


def _default_report_path(transcript: Path | None, state: Path | None) -> Path | None:
    source = transcript or state
    if source is None:
        return None
    name = source.name
    for suffix in (".jsonl", ".play.md", ".chat.jsonl", ".md", ".txt", ".state.json", ".json"):
        if name.endswith(suffix):
            return source.with_name(name[: -len(suffix)] + ".fiction.json")
    return source.with_suffix(source.suffix + ".fiction.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assert deterministic state/fiction reliability over local QA artifacts.",
    )
    parser.add_argument("--transcript", "--play", dest="transcript", type=Path, help="Transcript JSONL, play markdown, or text artifact.")
    parser.add_argument("--state", type=Path, help="Final state JSON artifact.")
    parser.add_argument("--expect", type=Path, help="Optional expectation JSON.")
    parser.add_argument("--scorecard", action="append", type=Path, default=[], help="Optional scorecard JSON. May be repeated.")
    parser.add_argument("--mode", choices=("release", "dev"), default="release", help="release fails missing artifacts; dev emits warnings.")
    parser.add_argument("--out", type=Path, help="Write JSON report to this path.")
    parser.add_argument("--write-sidecar", action="store_true", help="Write <run>.fiction.json next to the transcript/state when --out is omitted.")
    return parser


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    release = args.mode == "release"
    checks: list[dict[str, Any]] = []

    missing_required: list[str] = []
    if args.transcript is None:
        missing_required.append("transcript")
    elif not args.transcript.exists():
        missing_required.append(str(args.transcript))
    if args.state is None:
        missing_required.append("state")
    elif not args.state.exists():
        missing_required.append(str(args.state))
    for scorecard in args.scorecard:
        if not scorecard.exists():
            missing_required.append(str(scorecard))
    if args.expect is not None and not args.expect.exists():
        missing_required.append(str(args.expect))

    _check(
        checks,
        "required_artifacts_present",
        not missing_required,
        severity="critical" if release else "warning",
        evidence=", ".join(missing_required) if missing_required else "transcript/state artifacts present",
    )

    artifact = ArtifactText()
    if args.transcript is not None and args.transcript.exists():
        artifact = _load_artifact_text(args.transcript)

    state: Any = {}
    if args.state is not None and args.state.exists():
        loaded_state, err = _read_json(args.state)
        if err:
            _check(checks, "state_json_valid", False, severity="critical", evidence=err)
        else:
            state = loaded_state
            _check(checks, "state_json_valid", True, severity="critical", evidence=str(args.state))

    expect, expect_err = _expectation(args.expect)
    if expect_err:
        _check(checks, "expectation_json_valid", False, severity="critical" if release else "warning", evidence=expect_err)
    elif args.expect is not None:
        _check(checks, "expectation_json_valid", True, severity="critical", evidence=str(args.expect))

    forbidden = _list(expect.get("forbidden_text") or expect.get("forbidden_texts"))
    if forbidden:
        haystack = artifact.text.lower()
        hits = [needle for needle in forbidden if needle.lower() in haystack]
        _check(
            checks,
            "forbidden_text_absent",
            not hits,
            severity="critical",
            evidence="forbidden text present: " + ", ".join(repr(hit) for hit in hits) if hits else f"{len(forbidden)} forbidden phrase(s) absent",
        )

    required_paths = _list(expect.get("required_state_paths"))
    if required_paths:
        missing = [path for path in required_paths if not _path_exists(state, path)]
        _check(
            checks,
            "required_state_paths_present",
            not missing,
            severity="critical",
            evidence="missing: " + ", ".join(missing) if missing else f"{len(required_paths)} state path(s) present",
        )

    required_facts = _list(expect.get("world_state_contains") or expect.get("state_contains"))
    if required_facts:
        facts = _flatten_facts(state)
        missing = [fact for fact in required_facts if fact.lower() not in facts]
        _check(
            checks,
            "world_state_contains_expected_facts",
            not missing,
            severity="critical",
            evidence="missing: " + ", ".join(missing) if missing else f"{len(required_facts)} expected fact(s) present",
        )

    required_tools = _list(expect.get("required_tools"))
    if required_tools:
        if artifact.supports_tools:
            missing = sorted(set(required_tools) - (artifact.tools or set()))
            _check(
                checks,
                "required_tools_present",
                not missing,
                severity="critical",
                evidence="missing: " + ", ".join(missing) if missing else f"{len(required_tools)} required tool marker(s) present",
            )
        else:
            _check(
                checks,
                "required_tools_present",
                True,
                severity="warning",
                evidence="transcript/play artifact has no parseable tool markers; skipped required tool check",
            )

    severe_defects, scorecard_errors = _scorecard_defects(args.scorecard)
    if scorecard_errors:
        _check(checks, "scorecards_parse", False, severity="critical" if release else "warning", evidence="; ".join(scorecard_errors))
    elif args.scorecard:
        _check(checks, "scorecards_parse", True, severity="critical", evidence=f"{len(args.scorecard)} scorecard(s) parsed")
    _check(
        checks,
        "scorecards_no_high_critical_defects",
        not severe_defects,
        severity="critical",
        evidence="; ".join(severe_defects) if severe_defects else f"{len(args.scorecard)} scorecard(s), no high/critical defects",
    )

    failures = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]
    status = "fail" if failures else "pass"
    report = {"status": status, "mode": args.mode, "checks": checks}
    if args.transcript is not None:
        report["transcript"] = str(args.transcript)
    if args.state is not None:
        report["state"] = str(args.state)

    out = args.out or (_default_report_path(args.transcript, args.state) if args.write_sidecar else None)
    if out is not None:
        report["out"] = str(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("=== fiction reliability assertions ===")
    for check in checks:
        mark = {"pass": "PASS", "fail": "FAIL", "warn": "WARN"}[check["status"]]
        line = f"  [{mark}] {check['id']}"
        if check.get("evidence") and check["status"] != "pass":
            line += f" — {check['evidence']}"
        print(line)
    if failures:
        print(f"RED: {len(failures)} fiction reliability assertion(s) FAILED.", file=sys.stderr)
        return 1, report
    print("GREEN" + (f" ({len(warnings)} warning(s))" if warnings else ""))
    return 0, report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    code, _report = run(args)
    return code


if __name__ == "__main__":
    sys.exit(main())
