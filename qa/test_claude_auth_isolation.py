"""Tests for clawdnd_isolate_claude_auth (qa/lib_beat_driver.sh) — the #892 follow-up that keeps
the GUI .app's cold-open `claude -p` (the DM) off the macOS keychain + off any /Volumes TCC prompt.

The helper is GATED + ADDITIVE:
  (i)   an env credential preset (CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY) → isolate the config
        dir (a scratch .claude.json holding `{}`, no "projects" map) and keep the token in the env.
  (ii)  no env credential but a secret file → export the token from the file AND isolate the config.
  (iii) neither → true NO-OP: CLAUDE_CONFIG_DIR is NOT set and no credential is exported (so the
        Terminal/keychain path is byte-unchanged).

Each case shells out to a bash subprocess that `source`s the lib, calls the helper, then prints the
resulting env so the assertions read the REAL shell behavior (not a python re-impl). HOME is always
redirected at a tmp dir and the default token-file path is forced under tmp, so the real
~/.worldos/claude-token can NEVER be read.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "qa" / "lib_beat_driver.sh"

# A bash harness: source the lib, run the helper, then emit the post-call env as KEY=VALUE lines the
# test parses. We print exactly the three signals the assertions care about — set-but-empty is
# distinguishable from unset because we always print the prefix.
_HARNESS = r"""
set -uo pipefail
. "{lib}"
clawdnd_isolate_claude_auth
printf 'CLAUDE_CONFIG_DIR=%s\n' "${{CLAUDE_CONFIG_DIR:-}}"
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "${{CLAUDE_CODE_OAUTH_TOKEN:-}}"
printf 'ANTHROPIC_API_KEY=%s\n' "${{ANTHROPIC_API_KEY:-}}"
"""


def _run(env_overrides: dict, home: Path) -> dict:
    """Run the harness in a clean-ish env and return the parsed post-call env dict."""
    env = {
        # A minimal base env: PATH for bash + the binaries the helper might touch, HOME redirected.
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
    }
    env.update(env_overrides)
    proc = subprocess.run(
        ["bash", "-c", _HARNESS.format(lib=str(LIB))],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, f"helper must always return 0; got {proc.returncode}\n{proc.stderr}"
    out = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key] = value
    return out


def test_env_token_preset_isolates_config_and_survives(tmp_path):
    """(i) CLAUDE_CODE_OAUTH_TOKEN preset in the env → CLAUDE_CONFIG_DIR is set to an isolated dir
    whose .claude.json has NO "projects" key, and the token env survives unchanged."""
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "scratch-cfg"
    env = _run(
        {
            "CLAUDE_CODE_OAUTH_TOKEN": "fake-oauth-token-from-env",
            "CLAWDND_CLAUDE_CONFIG_DIR": str(cfg),
            # Force the file path under tmp so the real ~/.worldos can never be touched.
            "CLAWDND_CLAUDE_TOKEN_FILE": str(tmp_path / "nonexistent-token"),
        },
        home,
    )

    # CLAUDE_CONFIG_DIR set to the isolated scratch dir.
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg)
    cfg_json = cfg / ".claude.json"
    assert cfg_json.exists(), "isolated config dir must hold a minimal .claude.json"
    data = json.loads(cfg_json.read_text(encoding="utf-8"))
    assert "projects" not in data, "isolated .claude.json must have NO projects map (no /Volumes)"
    assert data == {}, "isolated .claude.json must be exactly {} (no oauth fields, no projects)"

    # The preset token survives untouched (env precedence in -p mode → keychain never consulted).
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "fake-oauth-token-from-env"
    # An sk-ant key wasn't supplied, so ANTHROPIC_API_KEY stays empty.
    assert env["ANTHROPIC_API_KEY"] == ""


def test_file_oauth_token_is_exported_and_config_isolated(tmp_path):
    """(ii) No env credential, but a secret file (pointed at by CLAWDND_CLAUDE_TOKEN_FILE) holding a
    fake oauth token → CLAUDE_CODE_OAUTH_TOKEN gets exported AND CLAUDE_CONFIG_DIR is isolated."""
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "scratch-cfg"
    token_file = tmp_path / "claude-token"
    # A trailing newline (as `claude setup-token` dumps) must be trimmed by the helper.
    token_file.write_text("fake-oauth-token-from-file\n", encoding="utf-8")

    env = _run(
        {
            "CLAWDND_CLAUDE_TOKEN_FILE": str(token_file),
            "CLAWDND_CLAUDE_CONFIG_DIR": str(cfg),
        },
        home,
    )

    # The file token was classified as an OAuth token (no sk-ant- prefix) and exported, trimmed.
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "fake-oauth-token-from-file"
    assert env["ANTHROPIC_API_KEY"] == ""
    # Config dir isolated, exactly {} (no projects map).
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg)
    data = json.loads((cfg / ".claude.json").read_text(encoding="utf-8"))
    assert data == {}
    assert "projects" not in data


def test_file_sk_ant_key_is_classified_as_anthropic_api_key(tmp_path):
    """(ii, variant) A file credential starting with sk-ant- → exported as ANTHROPIC_API_KEY, not
    CLAUDE_CODE_OAUTH_TOKEN (the prefix-classification branch)."""
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "scratch-cfg"
    token_file = tmp_path / "claude-token"
    token_file.write_text("sk-ant-fake-api-key\n", encoding="utf-8")

    env = _run(
        {
            "CLAWDND_CLAUDE_TOKEN_FILE": str(token_file),
            "CLAWDND_CLAUDE_CONFIG_DIR": str(cfg),
        },
        home,
    )

    assert env["ANTHROPIC_API_KEY"] == "sk-ant-fake-api-key"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert env["CLAUDE_CONFIG_DIR"] == str(cfg)


def test_no_credential_is_a_true_noop(tmp_path):
    """(iii) Neither an env credential nor a secret file → CLAUDE_CONFIG_DIR is NOT set (empty) and
    no credential is exported. This preserves today's working Terminal/keychain path."""
    home = tmp_path / "home"
    home.mkdir()
    # Point the token-file at a path that does not exist, under tmp — so the real ~/.worldos token
    # (if the owner has one) can never leak into this test.
    env = _run(
        {
            "CLAWDND_CLAUDE_TOKEN_FILE": str(tmp_path / "nonexistent-token"),
            "CLAWDND_CLAUDE_CONFIG_DIR": str(tmp_path / "should-not-be-created"),
        },
        home,
    )

    assert env["CLAUDE_CONFIG_DIR"] == "", "no-credential path must NOT set CLAUDE_CONFIG_DIR"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "", "no-credential path must export NO oauth token"
    assert env["ANTHROPIC_API_KEY"] == "", "no-credential path must export NO api key"
    # And the scratch config dir must not have been created (true no-op — no filesystem side effect).
    assert not (tmp_path / "should-not-be-created").exists()


def test_idempotent_when_called_twice(tmp_path):
    """The helper must be safe to call more than once (re-pointing at the same dir, never erroring)."""
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "scratch-cfg"
    harness = (
        'set -uo pipefail\n'
        f'. "{LIB}"\n'
        "clawdnd_isolate_claude_auth\n"
        "clawdnd_isolate_claude_auth\n"
        'printf \'CLAUDE_CONFIG_DIR=%s\\n\' "${CLAUDE_CONFIG_DIR:-}"\n'
        'printf \'CLAUDE_CODE_OAUTH_TOKEN=%s\\n\' "${CLAUDE_CODE_OAUTH_TOKEN:-}"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "CLAUDE_CODE_OAUTH_TOKEN": "fake-oauth-token-from-env",
            "CLAWDND_CLAUDE_CONFIG_DIR": str(cfg),
            "CLAWDND_CLAUDE_TOKEN_FILE": str(tmp_path / "nonexistent-token"),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = dict(
        line.partition("=")[::2] for line in proc.stdout.splitlines() if "=" in line
    )
    assert out["CLAUDE_CONFIG_DIR"] == str(cfg)
    assert out["CLAUDE_CODE_OAUTH_TOKEN"] == "fake-oauth-token-from-env"
    assert json.loads((cfg / ".claude.json").read_text(encoding="utf-8")) == {}
