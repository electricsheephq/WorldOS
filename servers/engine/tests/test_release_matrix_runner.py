"""Synthetic regressions for the release matrix dry-run runner (#63).

These tests exercise only local JSON artifacts and subprocess CLI output. They
must never invoke the live Claude QA harnesses.
"""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "qa" / "run_release_matrix.py"


def _write_json(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _matrix(path: Path) -> Path:
    return _write_json(
        path,
        {
            "version": 1,
            "defaults": {"mechanical_min": 4.5, "story_min": 4.3, "budget": 1.2, "max_parallel": 1},
            "cells": [
                {
                    "id": "postbg3-gortash-tyranny",
                    "harness": "run_qa",
                    "prompt": "qa/play_prompt_postbg3.txt",
                    "tags": ["postbg3", "world_state"],
                },
                {
                    "id": "s7-coldopen-hooks",
                    "harness": "run_qa",
                    "prompt": "qa/play_prompt_s7_coldopen.txt",
                    "tags": ["s7", "hooks"],
                },
            ],
        },
    )


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RUNNER), *args], cwd=ROOT, capture_output=True, text=True)


def test_list_outputs_cells_without_requiring_sidecars(tmp_path):
    matrix = _matrix(tmp_path / "release_matrix.json")

    result = _run("--matrix", str(matrix), "--list")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "postbg3-gortash-tyranny" in result.stdout
    assert "s7-coldopen-hooks" in result.stdout
    assert "UNKNOWN" not in result.stdout


def test_dry_run_filter_matches_tag_and_marks_missing_sidecars_unknown(tmp_path):
    matrix = _matrix(tmp_path / "release_matrix.json")
    artifacts = tmp_path / "artifacts"

    result = _run("--matrix", str(matrix), "--artifacts", str(artifacts), "--dry-run", "--filter", "postbg3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "postbg3-gortash-tyranny" in result.stdout
    assert "s7-coldopen-hooks" not in result.stdout
    assert "UNKNOWN" in result.stdout
    assert "PASS" not in result.stdout


def test_dry_run_json_summarizes_sidecar_statuses(tmp_path):
    matrix = _matrix(tmp_path / "release_matrix.json")
    artifacts = tmp_path / "artifacts"
    run = artifacts / "postbg3-gortash-tyranny"
    _write_json(run.with_suffix(".score.json"), {"overall": 4.7, "defects": []})
    _write_json(run.with_suffix(".tolkien.json"), {"overall": 4.4, "defects": []})
    _write_json(run.with_suffix(".fiction.json"), {"status": "pass"})
    _write_json(run.with_suffix(".release.json"), {"status": "PASS"})

    result = _run("--matrix", str(matrix), "--artifacts", str(artifacts), "--dry-run", "--filter", "postbg3", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "dry-run"
    assert payload["summary"] == {"pass": 1, "fail": 0, "unknown": 0}
    cell = payload["cells"][0]
    assert cell["id"] == "postbg3-gortash-tyranny"
    assert cell["status"] == "PASS"
    assert cell["sidecars"]["mechanical"]["status"] == "PASS"
    assert cell["sidecars"]["story"]["status"] == "PASS"
    assert cell["sidecars"]["fiction"]["status"] == "PASS"
    assert cell["sidecars"]["release"]["status"] == "PASS"
