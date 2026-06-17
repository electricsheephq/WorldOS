"""Synthetic regressions for the deterministic fiction reliability gate (#62).

The checker is intentionally artifact-only: these tests create local transcript,
state, expectation, and scorecard files, then drive the real CLI via subprocess.
No live QA/story sessions run here.
"""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "qa" / "assert_fiction_reliability.py"


def _write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _assistant_event(*, text: str = "", tools: list[str] | None = None) -> dict:
    content = []
    for tool in tools or []:
        content.append({"type": "tool_use", "name": f"mcp__worldos-engine__{tool}", "input": {}})
    if text:
        content.append({"type": "text", "text": text})
    return {"type": "assistant", "message": {"content": content}}


def _base_state() -> dict:
    return {
        "world_state": {"gortash": "archduke", "baldurs_gate": "occupied"},
        "quest_outcomes": {"who-rules-the-gate": "tyrant-holds"},
        "party": ["pc1"],
        "characters": {"pc1": {"kind": "player", "name": "Kield"}},
    }


def _base_expectation() -> dict:
    return {
        "expect": {
            "forbidden_text": ["Gortash is dead", "the city is rebuilding"],
            "required_state_paths": ["world_state.gortash", "quest_outcomes", "party.0"],
            "world_state_contains": ["gortash=archduke", "baldurs_gate=occupied"],
            "required_tools": ["lookup_lore", "recall", "social_check"],
        }
    }


def _run_gate(
    tmp_path: Path,
    *,
    transcript: Path | None = None,
    state: Path | None = None,
    expect: Path | None = None,
    scorecards: list[Path] | None = None,
    out: Path | None = None,
    mode: str = "release",
) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE), "--mode", mode]
    if transcript is not None:
        cmd += ["--transcript", str(transcript)]
    if state is not None:
        cmd += ["--state", str(state)]
    if expect is not None:
        cmd += ["--expect", str(expect)]
    for scorecard in scorecards or []:
        cmd += ["--scorecard", str(scorecard)]
    if out is not None:
        cmd += ["--out", str(out)]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def test_fiction_gate_green_emits_sidecar(tmp_path):
    transcript = _write_jsonl(
        tmp_path / "run.jsonl",
        [
            _assistant_event(
                text="Gortash rules as archduke while Baldur's Gate remains occupied.",
                tools=["lookup_lore", "recall", "social_check"],
            )
        ],
    )
    state = _write_json(tmp_path / "run.state.json", _base_state())
    expect = _write_json(tmp_path / "expect.json", _base_expectation())
    scorecard = _write_json(
        tmp_path / "run.score.json",
        {"defects": [{"severity": "medium", "area": "pacing", "evidence": "slow beat"}]},
    )
    out = tmp_path / "run.fiction.json"

    result = _run_gate(tmp_path, transcript=transcript, state=state, expect=expect, scorecards=[scorecard], out=out)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[PASS] forbidden_text_absent" in result.stdout
    assert "[PASS] required_state_paths_present" in result.stdout
    assert "[PASS] required_tools_present" in result.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert {check["id"] for check in report["checks"]} >= {
        "forbidden_text_absent",
        "required_state_paths_present",
        "required_tools_present",
        "scorecards_no_high_critical_defects",
    }


def test_fiction_gate_red_on_forbidden_text_and_missing_state_path(tmp_path):
    transcript = _write_jsonl(
        tmp_path / "run.jsonl",
        [_assistant_event(text="Gortash is dead and the city is rebuilding.", tools=["lookup_lore"])],
    )
    state = _write_json(tmp_path / "run.state.json", {"world_state": {"gortash": "archduke"}})
    expect = _write_json(tmp_path / "expect.json", _base_expectation())

    result = _run_gate(tmp_path, transcript=transcript, state=state, expect=expect)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "[FAIL] forbidden_text_absent" in result.stdout
    assert "[FAIL] required_state_paths_present" in result.stdout
    assert "quest_outcomes" in result.stdout


def test_fiction_gate_red_on_missing_required_tool_markers_in_jsonl(tmp_path):
    transcript = _write_jsonl(
        tmp_path / "run.jsonl",
        [_assistant_event(text="The state-grounded scene continues.", tools=["lookup_lore"])],
    )
    state = _write_json(tmp_path / "run.state.json", _base_state())
    expect = _write_json(tmp_path / "expect.json", _base_expectation())

    result = _run_gate(tmp_path, transcript=transcript, state=state, expect=expect)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "[FAIL] required_tools_present" in result.stdout
    assert "recall" in result.stdout and "social_check" in result.stdout


def test_fiction_gate_red_on_high_or_critical_scorecard_defects(tmp_path):
    transcript = _write_jsonl(
        tmp_path / "run.jsonl",
        [_assistant_event(text="Gortash remains archduke.", tools=["lookup_lore", "recall", "social_check"])],
    )
    state = _write_json(tmp_path / "run.state.json", _base_state())
    expect = _write_json(tmp_path / "expect.json", _base_expectation())
    scorecard = _write_json(
        tmp_path / "run.tolkien.json",
        {
            "defects": [
                {"severity": "high", "area": "canon", "evidence": "contradicts ending"},
                {"severity": "critical", "area": "state", "evidence": "ignores campaign snapshot"},
            ]
        },
    )

    result = _run_gate(tmp_path, transcript=transcript, state=state, expect=expect, scorecards=[scorecard])

    assert result.returncode == 1, result.stdout + result.stderr
    assert "[FAIL] scorecards_no_high_critical_defects" in result.stdout
    assert "run.tolkien.json" in result.stdout
    assert "critical" in result.stdout


def test_fiction_gate_release_missing_artifacts_fail_but_dev_warns(tmp_path):
    missing_transcript = tmp_path / "missing.jsonl"
    missing_state = tmp_path / "missing.state.json"

    release = _run_gate(tmp_path, transcript=missing_transcript, state=missing_state)
    dev = _run_gate(tmp_path, transcript=missing_transcript, state=missing_state, mode="dev")

    assert release.returncode == 1, release.stdout + release.stderr
    assert "[FAIL] required_artifacts_present" in release.stdout
    assert dev.returncode == 0, dev.stdout + dev.stderr
    assert "[WARN] required_artifacts_present" in dev.stdout
