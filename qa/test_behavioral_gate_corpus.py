"""Regression GUARD for the behavioral gate itself (qa/assert_behavioral.py).

The behavioral gate is the structural PASS/FAIL net under the LLM scorers. Like any
load-bearing software it can be silently WEAKENED — a future edit relaxes a threshold,
drops a `chk(... fatal=True)`, or narrows a scope guard so a known-broken run quietly
reads GREEN. The LLM scorers won't catch that (they grade prose). This corpus does.

`qa/gate_corpus/` holds a set of KNOWN-RED cases — each a minimal on-disk bundle of the
exact run-artifacts the gate reads (run.jsonl / state.json / [chat.jsonl] / [moves.jsonl])
crafted to trip ONE specific FATAL `CHECK`, plus a `manifest.json` that pins each
{case_dir -> expected_red_check}. For every case this test invokes assert_behavioral.py
as a subprocess (single-process; the gate is a CLI) and asserts:
  1. the gate returns RED (exit code 1), and
  2. the expected CHECK appears as a `[FAIL]` line in the gate's output.

If a gate edit stops a known-RED case from flipping RED — or stops naming the right
check — a corpus case goes GREEN and this test fails: the goalpost moved.

The fixtures are GENERATED (deterministically) by qa/gate_corpus/builder.py and committed
to disk so the corpus is inspectable and diffable. To regenerate after an intentional gate
change:  python qa/gate_corpus/builder.py  (writes into qa/gate_corpus/cases/).

READ-ONLY: this test reads only the committed corpus fixtures and invokes the gate against
THOSE inputs. It NEVER touches qa/scores.db, qa/scores_ledger.md, qa/RRI.json, the real
transcripts, or any default-path artifact. The gate is itself read-only on its inputs.

Run with the engine venv (single-process — NEVER -n/xdist):
    uv run --directory servers/engine python -m pytest qa/test_behavioral_gate_corpus.py -q -p no:xdist
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_QA_DIR = Path(__file__).resolve().parent
_GATE = _QA_DIR / "assert_behavioral.py"
_CORPUS_DIR = _QA_DIR / "gate_corpus"
_CASES_DIR = _CORPUS_DIR / "cases"
_MANIFEST = _CORPUS_DIR / "manifest.json"

# The artifact filenames a corpus case may carry, in the POSITIONAL order the gate's argv
# expects them:  run.jsonl  state.json  [chat.jsonl]  [moves.jsonl].
_RUN = "run.jsonl"
_STATE = "state.json"
_CHAT = "chat.jsonl"
_MOVES = "moves.jsonl"


def _load_manifest() -> list[dict]:
    if not _MANIFEST.exists():
        pytest.fail(
            f"gate corpus manifest missing: {_MANIFEST}. Generate it with "
            f"`python {_CORPUS_DIR / 'builder.py'}`."
        )
    data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    assert isinstance(cases, list) and cases, "manifest must list at least one case"
    return cases


def _argv_for(case_path: Path) -> list[str]:
    """Build the gate's positional argv from whichever artifacts the case directory carries.

    run.jsonl + state.json are required; chat.jsonl / moves.jsonl are appended only when
    present (the gate keys facade/duo behavior off whether those trailing args exist)."""
    run = case_path / _RUN
    state = case_path / _STATE
    assert run.exists(), f"case {case_path.name}: missing required {_RUN}"
    assert state.exists(), f"case {case_path.name}: missing required {_STATE}"
    argv = [sys.executable, str(_GATE), str(run), str(state)]
    chat = case_path / _CHAT
    moves = case_path / _MOVES
    # moves is argv[4]; if a case has moves but no chat it still needs a chat placeholder
    # at argv[3]. Real facade runs always emit a chat log, and our builder always writes one
    # alongside a moves file, so this stays faithful — but guard it anyway.
    if moves.exists() and not chat.exists():
        pytest.fail(
            f"case {case_path.name}: has {_MOVES} but no {_CHAT}; the gate reads moves at "
            f"argv[4], which requires a chat arg at argv[3]."
        )
    if chat.exists():
        argv.append(str(chat))
    if moves.exists():
        argv.append(str(moves))
    return argv


def _run_gate(case_path: Path) -> tuple[int, str]:
    """Invoke the gate as a subprocess over the case's artifacts. Returns (rc, combined_output).

    The corpus may set environment toggles per case (e.g. CLAWDND_GATE_COMBAT_SPRINT) via an
    optional `env` mapping in the manifest entry — applied by the caller, not here."""
    argv = _argv_for(case_path)
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _failed_checks(output: str) -> set[str]:
    """The set of CHECK names the gate printed as a [FAIL] (a FATAL miss). WARN lines are
    intentionally excluded — only a FATAL [FAIL] flips the gate RED."""
    out: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("[FAIL]"):
            rest = line[len("[FAIL]"):].strip()
            # name is the first token up to a space or an em-dash separator
            name = rest.split(" — ", 1)[0].split()[0] if rest else ""
            if name:
                out.add(name)
    return out


_MANIFEST_CASES = _load_manifest()
_CASE_IDS = [c["case_dir"] for c in _MANIFEST_CASES]


@pytest.mark.parametrize("case", _MANIFEST_CASES, ids=_CASE_IDS)
def test_corpus_case_trips_expected_red_check(case: dict):
    """Each known-RED corpus case must (1) return RED and (2) name its expected FATAL check."""
    case_dir = case["case_dir"]
    expected = case["expected_red_check"]
    if case.get("todo"):
        pytest.skip(f"{case_dir}: marked TODO in manifest — no faithful fixture yet "
                    f"(expected_red_check={expected!r}); reason: {case.get('reason', '')}")

    case_path = _CASES_DIR / case_dir
    assert case_path.is_dir(), f"corpus case dir missing: {case_path}"

    rc, output = _run_gate(case_path)

    assert rc == 1, (
        f"case {case_dir!r} expected RED (exit 1) for check {expected!r} but the gate "
        f"returned exit {rc} — a gate edit may have WEAKENED/removed {expected!r}.\n"
        f"--- gate output ---\n{output}"
    )
    failed = _failed_checks(output)
    assert expected in failed, (
        f"case {case_dir!r} returned RED but its expected FATAL check {expected!r} is NOT "
        f"among the failed checks {sorted(failed)} — the case may now trip a DIFFERENT gate, "
        f"or {expected!r} was weakened to a non-fatal WARN.\n--- gate output ---\n{output}"
    )
    # Isolation guard. Unless a case is explicitly flagged multi-fatal in the manifest (e.g. a
    # deliberately dead/blank run that trips several baseline gates at once), the expected check
    # must be the SOLE fatal fail. This stops a future fixture from quietly relying on a SECOND
    # fatal gate to hold it RED — which would mask a regression that weakened the target check.
    if not case.get("multi_fatal"):
        assert failed == {expected}, (
            f"case {case_dir!r} is meant to isolate {expected!r} but tripped other fatal "
            f"check(s) too: {sorted(failed)}. A second gate masking the target would hide a "
            f"weakening of {expected!r}. Fix the fixture (isolate it) or set multi_fatal:true "
            f"in the manifest with a reason.\n--- gate output ---\n{output}"
        )


def test_manifest_covers_every_fatal_check():
    """Coverage audit: every FATAL check declared by the gate is either exercised by a corpus
    case or explicitly marked TODO with a reason. A NEW fatal check added to the gate with no
    corpus entry fails here — forcing the corpus to grow with the gate (the anti-goalpost guard
    must not silently fall behind)."""
    import assert_behavioral  # noqa: F401  (import to ensure the gate module is importable)

    src = _GATE.read_text(encoding="utf-8")
    # Parse every chk(...) call and classify FATAL vs WARN by paren-balanced scan (mirrors the
    # builder's classifier so the two never drift).
    import re

    fatal: set[str] = set()
    for chunk in src.split("chk(")[1:]:
        m = re.match(r'\s*"([a-z0-9_]+)"', chunk)
        if not m:
            continue
        name = m.group(1)
        depth = 1
        buf = []
        for ch in chunk:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            buf.append(ch)
        call = "".join(buf)
        if "fatal=False" not in call:
            fatal.add(name)

    covered = {
        c["expected_red_check"]
        for c in _MANIFEST_CASES
    }
    todo = {
        c["expected_red_check"]
        for c in _MANIFEST_CASES
        if c.get("todo")
    }
    missing = fatal - covered
    assert not missing, (
        f"FATAL gate check(s) with NO corpus case and NO TODO entry: {sorted(missing)}. "
        f"Add a fixture (or a TODO manifest entry with a reason) so the regression guard keeps "
        f"pace with the gate. Covered={sorted(covered)} TODO={sorted(todo)}"
    )
