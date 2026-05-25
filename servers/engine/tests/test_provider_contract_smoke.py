"""Provider contract smoke adapter tests.

The smoke adapter is intentionally artifact-only: it validates app/provider env
and appends one constrained player move to a temp JSONL sink. It must not start
Claude/Codex/OpenClaw, mutate campaign state, or run narrative QA.
"""

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "provider_contract_smoke.sh"


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
    merged = {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": os.environ.get("TMPDIR", ""),
    }
    merged.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
    )


def test_provider_contract_smoke_requires_env(tmp_path):
    result = _run({})

    assert result.returncode != 0
    assert "missing required env" in result.stderr


def test_provider_contract_smoke_rejects_unknown_provider(tmp_path):
    result = _run(
        {
            "CLAWDND_PROVIDER": "mystery",
            "CLAWDND_WORLD": "baldurs-gate",
            "CLAWDND_RUN_ID": "smoke",
            "CLAWDND_PLAY_PORT": "8765",
            "CLAWDND_PLAYER_MOVES": str(tmp_path / "moves.jsonl"),
        }
    )

    assert result.returncode != 0
    assert "unknown provider" in result.stderr


def test_provider_contract_smoke_appends_one_legal_move_and_summary(tmp_path):
    moves = tmp_path / "moves.jsonl"
    result = _run(
        {
            "CLAWDND_PROVIDER": "codex",
            "CLAWDND_WORLD": "baldurs-gate",
            "CLAWDND_RUN_ID": "smoke",
            "CLAWDND_PLAY_PORT": "8765",
            "CLAWDND_PLAY_COMPANIONS": "Astarion:rogue,Minsc:ranger",
            "CLAWDND_PLAYER_MOVES": str(moves),
            "OPENAI_API_KEY": "must-not-print",
        }
    )

    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads(moves.read_text(encoding="utf-8"))
    assert row == {
        "kind": "clarify",
        "text": "provider contract smoke: confirm launch environment and move sink wiring",
    }
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["provider"] == "codex"
    assert summary["world"] == "baldurs-gate"
    assert summary["run_id"] == "smoke"
    assert summary["port"] == 8765
    assert summary["companions"] == ["Astarion:rogue", "Minsc:ranger"]
    assert "OPENAI_API_KEY" in summary["redacted_env_keys"]
    assert "must-not-print" not in result.stdout


def test_provider_contract_smoke_rejects_non_temp_move_path_without_override(tmp_path):
    moves = ROOT / "play-state" / "provider-contract-smoke-test.jsonl"
    result = _run(
        {
            "CLAWDND_PROVIDER": "openclaw",
            "CLAWDND_WORLD": "baldurs-gate",
            "CLAWDND_RUN_ID": "smoke",
            "CLAWDND_PLAY_PORT": "8765",
            "CLAWDND_PLAYER_MOVES": str(moves),
        }
    )

    assert result.returncode != 0
    assert "must be under a temp directory" in result.stderr
    assert not moves.exists()
