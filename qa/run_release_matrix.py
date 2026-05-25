#!/usr/bin/env python3
"""List and dry-run the machine-readable Owlcat release matrix.

Sprint 1 architecture invariant:
  This runner is deterministic and read-only. It never invokes live Claude, QA
  harness scripts, prompts, rubrics, or skills. It only reads release matrix JSON
  and optional sidecar artifacts already present on disk.

Matrix schema (minimal):
  {
    "version": 1,
    "defaults": {
      "mechanical_min": 4.5,
      "story_min": 4.3,
      "budget": 1.2,
      "max_parallel": 1,
      "artifact_root": "qa/transcripts"
    },
    "cells": [
      {
        "id": "postbg3-gortash-tyranny",
        "harness": "run_qa",
        "prompt": "qa/play_prompt_postbg3.txt",
        "tags": ["postbg3", "world_state"],
        "artifact_prefix": "optional-run-id-override"
      }
    ]
  }

Sidecar inspection:
  For each cell, inspect <artifact_root>/<artifact_prefix-or-id>.score.json,
  .tolkien.json, .fiction.json, and .release.json. Missing sidecars are UNKNOWN,
  never PASS. A cell is PASS only when every sidecar is present and passing.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "qa" / "release_matrix.json"
FATAL_DEFECTS = {"high", "critical"}
SIDECARS = {
    "mechanical": ".score.json",
    "story": ".tolkien.json",
    "fiction": ".fiction.json",
    "release": ".release.json",
}


@dataclass
class SidecarStatus:
    kind: str
    path: Path
    status: str
    detail: str

    def to_json(self) -> dict[str, str]:
        return {
            "status": self.status,
            "path": str(self.path),
            "detail": self.detail,
        }


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, str(exc)


def _status_token(value: Any) -> str | None:
    token = str(value or "").strip().lower()
    if token in {"pass", "passed", "green", "ok"}:
        return "PASS"
    if token in {"fail", "failed", "red", "invalid"}:
        return "FAIL"
    if token in {"warn", "warning", "unknown", "missing"}:
        return "UNKNOWN"
    return None


def _severe_defects(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    defects = data.get("defects", [])
    if not isinstance(defects, list):
        return []
    severe: list[str] = []
    for defect in defects:
        if not isinstance(defect, dict):
            continue
        severity = str(defect.get("severity") or "").strip().lower()
        if severity in FATAL_DEFECTS:
            area = defect.get("area") or defect.get("kind") or "defect"
            severe.append(f"{severity} {area}")
    return severe


def _overall_score(data: Any) -> float | None:
    if not isinstance(data, dict):
        return None
    try:
        return float(data["overall"])
    except (TypeError, ValueError):
        return None
    except KeyError:
        return None


def _threshold(value: Any, field: str, cell_id: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"invalid {field} for cell '{cell_id}': {value!r}") from exc


def _relative_artifact_prefix(value: str, cell_id: str, artifacts: Path) -> Path:
    prefix = Path(value)
    if prefix.is_absolute() or ".." in prefix.parts:
        raise SystemExit(f"invalid artifact_prefix for cell '{cell_id}': {value!r}")
    base = artifacts / prefix
    try:
        base.resolve(strict=False).relative_to(artifacts.resolve(strict=False))
    except ValueError as exc:
        raise SystemExit(f"artifact_prefix escapes artifacts root for cell '{cell_id}': {value!r}") from exc
    return prefix


def _sidecar_path(base: Path, kind: str) -> Path:
    return base.with_name(base.name + SIDECARS[kind])


def _score_sidecar(kind: str, path: Path, minimum: float) -> SidecarStatus:
    if not path.exists():
        return SidecarStatus(kind, path, "UNKNOWN", "missing")
    data, err = _read_json(path)
    if err:
        return SidecarStatus(kind, path, "FAIL", f"invalid JSON: {err}")
    if not isinstance(data, dict):
        return SidecarStatus(kind, path, "FAIL", "sidecar root is not an object")

    for key in ("gate_status", "status", "verdict"):
        token = _status_token(data.get(key))
        if token == "FAIL":
            return SidecarStatus(kind, path, "FAIL", f"{key}={data.get(key)}")

    severe = _severe_defects(data)
    if severe:
        return SidecarStatus(kind, path, "FAIL", "; ".join(severe[:3]))

    score = _overall_score(data)
    if score is None:
        return SidecarStatus(kind, path, "FAIL", "missing numeric overall")
    if score >= minimum:
        return SidecarStatus(kind, path, "PASS", f"overall={score:g} >= {minimum:g}")
    return SidecarStatus(kind, path, "FAIL", f"overall={score:g} < {minimum:g}")


def _status_sidecar(kind: str, path: Path) -> SidecarStatus:
    if not path.exists():
        return SidecarStatus(kind, path, "UNKNOWN", "missing")
    data, err = _read_json(path)
    if err:
        return SidecarStatus(kind, path, "FAIL", f"invalid JSON: {err}")
    if not isinstance(data, dict):
        return SidecarStatus(kind, path, "FAIL", "sidecar root is not an object")
    token = None
    for key in ("status", "gate_status", "verdict"):
        token = _status_token(data.get(key))
        if token:
            return SidecarStatus(kind, path, token, f"{key}={data.get(key)}")
    return SidecarStatus(kind, path, "UNKNOWN", "no status field")


def _load_matrix(path: Path) -> dict[str, Any]:
    data, err = _read_json(path)
    if err:
        raise SystemExit(f"failed to read matrix {path}: {err}")
    if not isinstance(data, dict):
        raise SystemExit("release matrix must be a JSON object")
    cells = data.get("cells")
    if not isinstance(cells, list):
        raise SystemExit("release matrix must contain a cells list")
    return data


def _matches(cell: dict[str, Any], needle: str | None) -> bool:
    if not needle:
        return True
    needle = needle.lower()
    cell_id = str(cell.get("id") or "").lower()
    tags = [str(tag).lower() for tag in cell.get("tags", []) if isinstance(tag, str)]
    return needle in cell_id or needle in tags


def _artifact_root(matrix: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override
    defaults = matrix.get("defaults", {}) if isinstance(matrix.get("defaults"), dict) else {}
    root = Path(str(defaults.get("artifact_root") or "qa/transcripts"))
    return root if root.is_absolute() else ROOT / root


def _inspect_cell(cell: dict[str, Any], defaults: dict[str, Any], artifacts: Path) -> dict[str, Any]:
    cell_id = str(cell.get("id") or "")
    prefix = str(cell.get("artifact_prefix") or cell_id)
    base = artifacts / _relative_artifact_prefix(prefix, cell_id, artifacts)
    mechanical_min = _threshold(cell.get("mechanical_min", defaults.get("mechanical_min", 4.5)), "mechanical_min", cell_id)
    story_min = _threshold(cell.get("story_min", defaults.get("story_min", 4.3)), "story_min", cell_id)
    sidecars = {
        "mechanical": _score_sidecar("mechanical", _sidecar_path(base, "mechanical"), mechanical_min),
        "story": _score_sidecar("story", _sidecar_path(base, "story"), story_min),
        "fiction": _status_sidecar("fiction", _sidecar_path(base, "fiction")),
        "release": _status_sidecar("release", _sidecar_path(base, "release")),
    }
    statuses = [sidecar.status for sidecar in sidecars.values()]
    if any(status == "FAIL" for status in statuses):
        status = "FAIL"
    elif all(status == "PASS" for status in statuses):
        status = "PASS"
    else:
        status = "UNKNOWN"
    return {
        "id": cell_id,
        "harness": str(cell.get("harness") or ""),
        "prompt": str(cell.get("prompt") or ""),
        "tags": [str(tag) for tag in cell.get("tags", [])],
        "artifact_prefix": prefix,
        "status": status,
        "sidecars": {name: sidecar.to_json() for name, sidecar in sidecars.items()},
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pass": sum(1 for row in rows if row["status"] == "PASS"),
        "fail": sum(1 for row in rows if row["status"] == "FAIL"),
        "unknown": sum(1 for row in rows if row["status"] == "UNKNOWN"),
    }


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    lines = ["  ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(value.ljust(widths[idx]) for idx, value in enumerate(row)) for row in rows)
    return "\n".join(lines)


def _print_list(cells: list[dict[str, Any]]) -> None:
    rows = [
        [
            str(cell.get("id") or ""),
            str(cell.get("harness") or ""),
            str(cell.get("prompt") or ""),
            ",".join(str(tag) for tag in cell.get("tags", [])),
        ]
        for cell in cells
    ]
    print(_format_table(["ID", "HARNESS", "PROMPT", "TAGS"], rows))


def _print_dry_run(rows: list[dict[str, Any]], artifacts: Path) -> None:
    table = []
    for row in rows:
        sidecars = row["sidecars"]
        table.append(
            [
                row["id"],
                ",".join(row["tags"]),
                row["harness"],
                row["prompt"],
                sidecars["mechanical"]["status"],
                sidecars["story"]["status"],
                sidecars["fiction"]["status"],
                sidecars["release"]["status"],
                row["status"],
            ]
        )
    print(f"mode=dry-run artifacts={artifacts}")
    print(_format_table(["ID", "TAGS", "HARNESS", "PROMPT", "MECH", "STORY", "FICTION", "RELEASE", "STATUS"], table))
    totals = _summary(rows)
    print(f"summary pass={totals['pass']} fail={totals['fail']} unknown={totals['unknown']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List or dry-run the ClawDnD Owlcat release matrix.")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX, help="Release matrix JSON path.")
    parser.add_argument("--artifacts", type=Path, help="Directory containing <run-id> sidecars. Defaults to matrix defaults.artifact_root.")
    parser.add_argument("--filter", help="Filter by cell id substring or exact tag.")
    parser.add_argument("--list", action="store_true", help="List matrix cells without inspecting sidecars.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect existing sidecars without running live QA harnesses.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of tables.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    matrix = _load_matrix(args.matrix)
    defaults = matrix.get("defaults", {}) if isinstance(matrix.get("defaults"), dict) else {}
    cells = [cell for cell in matrix["cells"] if isinstance(cell, dict) and _matches(cell, args.filter)]

    # Default to list mode so an accidental invocation remains deterministic and cheap.
    mode = "dry-run" if args.dry_run else "list"
    if args.list:
        mode = "list"

    if mode == "list":
        if args.json:
            print(json.dumps({"mode": "list", "cells": cells}, indent=2, sort_keys=True))
        else:
            _print_list(cells)
        return 0

    artifacts = _artifact_root(matrix, args.artifacts)
    rows = [_inspect_cell(cell, defaults, artifacts) for cell in cells]
    payload = {"mode": "dry-run", "matrix": str(args.matrix), "artifacts": str(artifacts), "summary": _summary(rows), "cells": rows}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_dry_run(rows, artifacts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
