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
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )


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


def test_dry_run_requires_numeric_overall_for_score_sidecars(tmp_path):
    matrix = _matrix(tmp_path / "release_matrix.json")
    artifacts = tmp_path / "artifacts"
    run = artifacts / "postbg3-gortash-tyranny"
    _write_json(run.with_name(run.name + ".score.json"), {"status": "pass", "scores": {"rules_correctness": 5}})
    _write_json(run.with_name(run.name + ".tolkien.json"), {"scores": {"scene_craft": 5}, "verdict": "pass"})
    _write_json(run.with_name(run.name + ".fiction.json"), {"status": "pass"})
    _write_json(run.with_name(run.name + ".release.json"), {"status": "PASS"})

    result = _run("--matrix", str(matrix), "--artifacts", str(artifacts), "--dry-run", "--filter", "postbg3", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"pass": 0, "fail": 1, "unknown": 0}
    cell = payload["cells"][0]
    assert cell["sidecars"]["mechanical"]["status"] == "FAIL"
    assert cell["sidecars"]["mechanical"]["detail"] == "missing numeric overall"
    assert cell["sidecars"]["story"]["status"] == "FAIL"
    assert cell["sidecars"]["story"]["detail"] == "missing numeric overall"


def test_dry_run_appends_sidecar_suffix_to_dotted_artifact_prefix(tmp_path):
    matrix = _write_json(
        tmp_path / "release_matrix.json",
        {
            "version": 1,
            "defaults": {"mechanical_min": 4.5, "story_min": 4.3},
            "cells": [
                {
                    "id": "dotted-run",
                    "harness": "run_qa",
                    "prompt": "qa/play_prompt_postbg3.txt",
                    "tags": ["postbg3"],
                    "artifact_prefix": "run.v1",
                }
            ],
        },
    )
    artifacts = tmp_path / "artifacts"
    run = artifacts / "run.v1"
    _write_json(run.with_name(run.name + ".score.json"), {"overall": 4.7, "defects": []})
    _write_json(run.with_name(run.name + ".tolkien.json"), {"overall": 4.4, "defects": []})
    _write_json(run.with_name(run.name + ".fiction.json"), {"status": "pass"})
    _write_json(run.with_name(run.name + ".release.json"), {"status": "PASS"})

    result = _run("--matrix", str(matrix), "--artifacts", str(artifacts), "--dry-run", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"pass": 1, "fail": 0, "unknown": 0}
    assert payload["cells"][0]["sidecars"]["mechanical"]["path"].endswith("run.v1.score.json")


def test_dry_run_rejects_artifact_prefix_that_escapes_artifact_root(tmp_path):
    matrix = _write_json(
        tmp_path / "release_matrix.json",
        {
            "version": 1,
            "defaults": {"mechanical_min": 4.5, "story_min": 4.3},
            "cells": [
                {
                    "id": "escape-run",
                    "harness": "run_qa",
                    "prompt": "qa/play_prompt_postbg3.txt",
                    "tags": ["postbg3"],
                    "artifact_prefix": "../outside",
                }
            ],
        },
    )

    result = _run("--matrix", str(matrix), "--artifacts", str(tmp_path / "artifacts"), "--dry-run")

    assert result.returncode != 0
    assert "invalid artifact_prefix for cell 'escape-run'" in result.stderr


def test_dry_run_reports_invalid_threshold_with_cell_id(tmp_path):
    matrix = _write_json(
        tmp_path / "release_matrix.json",
        {
            "version": 1,
            "defaults": {"mechanical_min": "not-a-number", "story_min": 4.3},
            "cells": [
                {
                    "id": "bad-threshold",
                    "harness": "run_qa",
                    "prompt": "qa/play_prompt_postbg3.txt",
                    "tags": ["postbg3"],
                }
            ],
        },
    )

    result = _run("--matrix", str(matrix), "--artifacts", str(tmp_path / "artifacts"), "--dry-run")

    assert result.returncode != 0
    assert "invalid mechanical_min for cell 'bad-threshold'" in result.stderr
