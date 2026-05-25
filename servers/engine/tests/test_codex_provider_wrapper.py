"""Codex provider wrapper contract tests.

The wrapper is allowed to create provider-local logs/config and a move sink. It
must fail closed on missing launch env and its smoke mode must not start Codex or
run narrative QA.
"""

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "play_codex_actor.sh"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": os.environ.get("TMPDIR", ""),
        "CLAWDND_PROVIDER": "codex",
        "CLAWDND_WORLD": "baldurs-gate",
        "CLAWDND_RUN_ID": "codex-smoke",
        "CLAWDND_PLAY_PORT": "8765",
        "CLAWDND_PLAY_BUDGET": "0.05",
        "CLAWDND_PLAY_SESSION_BUDGET": "0.25",
        "CLAWDND_PLAY_MAX_TURNS": "1",
        "CLAWDND_PLAY_COMPANIONS": "",
        "CLAWDND_STATE_ROOT": str(tmp_path),
    }
    env.update(overrides)
    return env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )


def test_codex_wrapper_fails_closed_without_required_env(tmp_path):
    result = _run(["--dry-run"], {"PATH": os.environ.get("PATH", ""), "TMPDIR": str(tmp_path)})

    assert result.returncode != 0
    assert "missing required env" in result.stderr


def test_codex_wrapper_rejects_non_codex_provider(tmp_path):
    result = _run(["--dry-run"], _env(tmp_path, CLAWDND_PROVIDER="openclaw"))

    assert result.returncode != 0
    assert "CLAWDND_PROVIDER must be codex" in result.stderr


def test_codex_wrapper_rejects_unknown_options_before_run_mode(tmp_path):
    result = _run(["--dryrun"], _env(tmp_path))

    assert result.returncode != 0
    assert "unknown option: --dryrun" in result.stderr


def test_codex_wrapper_smoke_generates_player_facade_config_only(tmp_path):
    result = _run(["--smoke"], _env(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{") :])
    assert summary["ok"] is True
    assert summary["mode"] == "smoke"
    assert summary["provider"] == "codex"

    config = Path(summary["config"]).read_text(encoding="utf-8")
    assert "[mcp_servers.clawdnd-player]" in config
    assert "player_server.py" in config
    assert "CLAWDND_PLAYER_MOVES" in config
    assert "servers/engine/server.py" not in config
    assert "qa/" not in config

    moves = Path(summary["moves"])
    assert moves.exists()
    assert moves.read_text(encoding="utf-8") == ""


def test_codex_wrapper_dry_run_uses_play_state_layout(tmp_path):
    result = _run(["--dry-run"], _env(tmp_path, CLAWDND_RUN_ID="layout-check"))

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{") :])
    assert summary["config"].endswith("/layout-check/codex-provider/codex-player.toml")
    assert summary["moves"].endswith("/layout-check/player_moves.jsonl")
