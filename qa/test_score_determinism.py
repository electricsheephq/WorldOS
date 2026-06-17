"""Determinism + drift-detection guards for qa/score.sh (no live LLM).

Covers the two ADDITIVE hardening features layered onto score.sh:

  (a) Scorer-model pinning. score.sh pins a single canonical scorer model unless
      CLAWDND_SCORER_MODEL is set. Setting CLAWDND_SCORER_MODEL WITHOUT the explicit
      opt-in CLAWDND_ALLOW_SCORER_OVERRIDE=1 must ERROR (non-zero + a clear message) so
      a stray env var can't silently swap the scorer and skew the gate baseline. With the
      override flag, the script must proceed PAST the guard.

  (b) prompt_construction_hash. score.sh stamps the sha256 of (rubric contents + schema
      contents + the fixed prompt-template scaffold), NOT the transcript, into the score
      JSON so rubric/template drift is detectable across versions. The hash is produced by
      qa/_score_prompt_hash.py, which both score.sh and this test call.

The shell-behavior tests use WORLDOS_SCORE_GUARD_ONLY=1 — an additive, test-only dry-run
hook that makes score.sh run all guards + emit the hashed artifact, then exit 0 BEFORE the
`claude -p` loop. So nothing here ever touches a live LLM, the gateway, Eva, or global mcp
config. The guard-failure path doesn't even reach that hook (it errors first), so the
not-logged-in / no-claude environment is irrelevant to it.

Default behavior is unchanged: with both env vars unset, score.sh proceeds past the guard
exactly as today (the additive-by-default invariant).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORE_SH = ROOT / "qa" / "score.sh"
HASH_PY = ROOT / "qa" / "_score_prompt_hash.py"


# --- load the helper module directly (it lives in qa/, not on sys.path) ---------------
def _load_hash_module():
    spec = importlib.util.spec_from_file_location("_score_prompt_hash", HASH_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HASH = _load_hash_module()


# --- fixtures the script needs (all dummy; the LLM never runs) -------------------------
def _make_inputs(tmp_path: Path, rubric_text: str = "RUBRIC v1\nbody\n") -> dict:
    rubric = tmp_path / "rubric.md"
    schema = tmp_path / "schema.json"
    md = tmp_path / "run.md"
    state = tmp_path / "run.state.json"
    out = tmp_path / "run.score.json"
    rubric.write_text(rubric_text, encoding="utf-8")
    schema.write_text('{"type":"object"}\n', encoding="utf-8")
    md.write_text("# transcript\nDM: hello\n", encoding="utf-8")
    state.write_text('{"day": 1}\n', encoding="utf-8")
    return {
        "rubric": rubric,
        "schema": schema,
        "md": md,
        "state": state,
        "out": out,
    }


def _run_score(inputs: dict, env_overrides: dict, guard_only: bool = True) -> subprocess.CompletedProcess:
    """Invoke score.sh with a minimal env. PATH keeps bash + python + jq reachable."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    if guard_only:
        env["WORLDOS_SCORE_GUARD_ONLY"] = "1"
    env.update(env_overrides)
    return subprocess.run(
        [
            "bash",
            str(SCORE_SH),
            str(inputs["md"]),
            str(inputs["state"]),
            str(inputs["rubric"]),
            str(inputs["schema"]),
            str(inputs["out"]),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


# ====================================================================================
# (a) Scorer-model pinning guard
# ====================================================================================
def test_scorer_override_without_optin_errors(tmp_path):
    """CLAWDND_SCORER_MODEL set, no opt-in flag → non-zero exit + a clear guard message."""
    inputs = _make_inputs(tmp_path)
    proc = _run_score(inputs, {"CLAWDND_SCORER_MODEL": "opus"})
    assert proc.returncode != 0, (
        "score.sh must REFUSE a CLAWDND_SCORER_MODEL override without the opt-in flag; "
        f"got rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    combined = (proc.stdout + proc.stderr).lower()
    # The message must name the override env + the opt-in flag so the operator knows the fix.
    assert "worldos_scorer_model" in combined
    assert "worldos_allow_scorer_override" in combined
    # The guard must fire BEFORE producing an artifact (no score JSON written).
    assert not inputs["out"].exists(), "guard-failed run must not write a score artifact"


def test_scorer_override_with_optin_proceeds(tmp_path):
    """CLAWDND_SCORER_MODEL + CLAWDND_ALLOW_SCORER_OVERRIDE=1 → proceeds past the guard."""
    inputs = _make_inputs(tmp_path)
    proc = _run_score(
        inputs,
        {"CLAWDND_SCORER_MODEL": "opus", "CLAWDND_ALLOW_SCORER_OVERRIDE": "1"},
    )
    assert proc.returncode == 0, (
        "with the opt-in flag, score.sh must proceed past the guard (guard-only dry run "
        f"exits 0); got rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    # Proceeding past the guard means the hashed artifact got stamped.
    assert inputs["out"].exists(), "proceeding past the guard must reach the artifact-stamp step"
    payload = json.loads(inputs["out"].read_text(encoding="utf-8"))
    assert "prompt_construction_hash" in payload


def test_default_unset_proceeds_past_guard(tmp_path):
    """Additive-by-default: with NO scorer env vars set, score.sh proceeds (today's behavior)."""
    inputs = _make_inputs(tmp_path)
    proc = _run_score(inputs, {})
    assert proc.returncode == 0, (
        "default (no env) must proceed past the guard exactly as today; "
        f"got rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert inputs["out"].exists()


# ====================================================================================
# (b) prompt_construction_hash: stable for identical rubric+template; moves on rubric edit
# ====================================================================================
def test_hash_stable_for_identical_rubric_and_template():
    """Same rubric + same schema + same scaffold ⇒ identical hash (deterministic)."""
    a = HASH.prompt_construction_hash("RUBRIC", "SCHEMA")
    b = HASH.prompt_construction_hash("RUBRIC", "SCHEMA")
    assert a == b
    # sha256 hex digest shape.
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)


def test_hash_changes_when_rubric_content_changes():
    """Editing the rubric content must move the hash (drift is detectable)."""
    base = HASH.prompt_construction_hash("RUBRIC v1", "SCHEMA")
    edited = HASH.prompt_construction_hash("RUBRIC v2 — added a dimension", "SCHEMA")
    assert base != edited


def test_hash_excludes_transcript_and_state():
    """The hash must NOT depend on the transcript or engine state — only rubric/schema/scaffold.

    We prove this end-to-end: two guard-only runs with identical rubric+schema but DIFFERENT
    transcript/state must stamp the SAME prompt_construction_hash.
    """
    inputs_a = _make_inputs(Path(_tmpdir()))
    inputs_b = _make_inputs(Path(_tmpdir()))
    # Give B a wildly different transcript + state; keep rubric/schema content identical.
    inputs_b["md"].write_text("# totally different transcript\n" * 50, encoding="utf-8")
    inputs_b["state"].write_text('{"day": 999, "flags": {"x": true}}\n', encoding="utf-8")

    proc_a = _run_score(inputs_a, {})
    proc_b = _run_score(inputs_b, {})
    assert proc_a.returncode == 0 and proc_b.returncode == 0, (
        f"a.stderr={proc_a.stderr}\nb.stderr={proc_b.stderr}"
    )
    ha = json.loads(inputs_a["out"].read_text(encoding="utf-8"))["prompt_construction_hash"]
    hb = json.loads(inputs_b["out"].read_text(encoding="utf-8"))["prompt_construction_hash"]
    assert ha == hb, "prompt_construction_hash must be invariant to transcript/state content"


def test_shell_hash_matches_python_helper():
    """The hash stamped by score.sh (via the helper CLI) equals the helper's Python result."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        inputs = _make_inputs(Path(d), rubric_text="RUBRIC for parity\n")
        proc = _run_score(inputs, {})
        assert proc.returncode == 0, proc.stderr
        stamped = json.loads(inputs["out"].read_text(encoding="utf-8"))["prompt_construction_hash"]
        expected = HASH.hash_from_files(str(inputs["rubric"]), str(inputs["schema"]))
        assert stamped == expected


# --- helper: a throwaway tmp dir without the pytest fixture (for the two-run hash test) -
def _tmpdir() -> str:
    import tempfile

    return tempfile.mkdtemp(prefix="worldos-score-det-")
