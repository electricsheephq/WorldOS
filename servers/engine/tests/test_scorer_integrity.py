"""WS0a — scorer-integrity: a scorer FAILURE must never read as a valid no-score / GREEN.

THE SEAM (diagnosed, not re-derived). ``qa/run_duo.sh`` printed each lens via
``jq -r '.overall//"?"' "$T/$RUN.{tolkien,score,angrydm}.json"``. ``qa/score.sh`` writes a
``{quota_exhausted}`` sentinel + exits rc=2 on a 429, but on a GENERIC retry-exhaustion it
USED TO exit rc=1 WITHOUT writing anything — so the lens file was missing/empty, ``jq`` printed
BLANK (not even ``"?"``), and ``behavioral=GREEN`` still printed. A failed scoring thus
masqueraded as a silent valid no-score (observed live:
``[duo] done. story-craft= mechanical= angry-dm= behavioral=GREEN``).

The fix is additive, in three shell surfaces:
  (1) qa/score.sh — on generic retry-exhaustion, write ``{"error":"scorer_failed",…}`` to its
      OUT file BEFORE ``exit 1`` (mirroring the existing 429 ``{quota_exhausted}`` block) so the
      lens file is ALWAYS valid JSON.
  (2) qa/lib_beat_driver.sh — ``worldos_validate_lens_file`` is the single source of truth for
      "is this lens a TRUSTWORTHY numeric scorecard?": ok | missing | invalid | sentinel |
      nonnumeric. ``worldos_lens_display`` renders the numeric overall for a valid card, else
      ``FAILED:<status>`` (never a blank). qa/run_duo.sh uses both to mark a failed run a
      DISTINCT ``status=unscorable`` (NEITHER green nor a blank no-score).
  (3) qa/SCORING.md — documents that a BLANK lens value is a scorer FAILURE.

These tests are gateway-free / offline: they exercise the shared bash helpers directly (the
same ``/bin/bash -c`` + source-the-lib pattern the play/QA wrappers use, and that
test_dead_beat_classification.py uses), with deliberately-empty and ``{error:scorer_failed}``-
sentinel lens fixtures, asserting the run is marked ``unscorable`` (NOT ``ok``/GREEN).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_DIR.parents[1]
LIB = REPO_ROOT / "qa" / "lib_beat_driver.sh"
SCORE_SH = REPO_ROOT / "qa" / "score.sh"
RUN_DUO = REPO_ROOT / "qa" / "run_duo.sh"
SCORING_MD = REPO_ROOT / "qa" / "SCORING.md"


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )


def _hdr() -> str:
    return f'set -u; . "{LIB}"\n'


def _validate(path: Path) -> tuple[str, int]:
    """Run worldos_validate_lens_file on a fixture; return (status-token, returncode)."""
    r = _bash(_hdr() + f'worldos_validate_lens_file "{path}"\n')
    assert r.stderr == "" or "warning" in r.stderr.lower(), r.stderr
    return r.stdout.strip(), r.returncode


def _display(path: Path) -> str:
    r = _bash(_hdr() + f'worldos_lens_display "{path}"\n')
    return r.stdout.strip()


# ── valid scorecard ─────────────────────────────────────────────────────────────────────────


def test_valid_scorecard_is_ok_and_displays_number(tmp_path):
    f = tmp_path / "tolkien.json"
    f.write_text(json.dumps({"overall": 4.3, "scores": {"a": 4}}), encoding="utf-8")
    status, rc = _validate(f)
    assert status == "ok" and rc == 0, status
    assert _display(f) == "4.3"


def test_integer_overall_is_ok(tmp_path):
    f = tmp_path / "score.json"
    f.write_text(json.dumps({"overall": 5, "scores": {}}), encoding="utf-8")
    status, rc = _validate(f)
    assert status == "ok" and rc == 0
    assert _display(f) == "5"


# ── the BUG cases: a scorer failure must NOT validate as ok ──────────────────────────────────


def test_empty_lens_file_is_missing_not_ok(tmp_path):
    """The exact bug: score.sh exited rc=1 leaving an EMPTY file → jq printed blank → read GREEN."""
    f = tmp_path / "angrydm.json"
    f.write_text("", encoding="utf-8")
    status, rc = _validate(f)
    assert status == "missing", f"empty lens must be 'missing', got {status!r}"
    assert rc != 0, "an empty lens file must NOT return rc=0 (would read as a valid score)"
    assert _display(f) == "FAILED:missing", "an empty lens must NEVER print a blank value"


def test_absent_lens_file_is_missing_not_ok(tmp_path):
    f = tmp_path / "never_written.json"
    assert not f.exists()
    status, rc = _validate(f)
    assert status == "missing" and rc != 0
    assert _display(f) == "FAILED:missing"


def test_scorer_failed_sentinel_is_not_a_score(tmp_path):
    """qa/score.sh's NEW generic-exhaustion sentinel must read as a FAILURE, not a valid card."""
    f = tmp_path / "score.json"
    f.write_text(
        json.dumps({"error": "scorer_failed", "attempts": 3, "last_api_error": "401: bad key"}),
        encoding="utf-8",
    )
    status, rc = _validate(f)
    assert status == "sentinel", f"scorer_failed sentinel must be 'sentinel', got {status!r}"
    assert rc != 0
    assert _display(f) == "FAILED:sentinel"


def test_quota_exhausted_sentinel_is_not_a_score(tmp_path):
    """The pre-existing 429 sentinel must also be caught by the same validator (consistency)."""
    f = tmp_path / "tolkien.json"
    f.write_text(json.dumps({"quota_exhausted": True, "api_error_status": 429}), encoding="utf-8")
    status, rc = _validate(f)
    assert status == "sentinel" and rc != 0
    assert _display(f) == "FAILED:sentinel"


def test_unparseable_json_is_invalid_not_ok(tmp_path):
    f = tmp_path / "score.json"
    f.write_text("not json at all {{{ <<", encoding="utf-8")
    status, rc = _validate(f)
    assert status == "invalid" and rc != 0
    assert _display(f) == "FAILED:invalid"


def test_missing_overall_is_nonnumeric_not_ok(tmp_path):
    """A scorecard whose .overall is absent is malformed, not a score."""
    f = tmp_path / "score.json"
    f.write_text(json.dumps({"scores": {"a": 4}}), encoding="utf-8")
    status, rc = _validate(f)
    assert status == "nonnumeric" and rc != 0
    assert _display(f) == "FAILED:nonnumeric"


def test_bool_overall_is_nonnumeric_not_ok(tmp_path):
    """bool is an int subclass; True must NOT slip through as a numeric overall."""
    f = tmp_path / "score.json"
    f.write_text(json.dumps({"overall": True, "scores": {}}), encoding="utf-8")
    status, rc = _validate(f)
    assert status == "nonnumeric" and rc != 0


def test_string_overall_is_nonnumeric_not_ok(tmp_path):
    f = tmp_path / "score.json"
    f.write_text(json.dumps({"overall": "?", "scores": {}}), encoding="utf-8")
    status, rc = _validate(f)
    assert status == "nonnumeric" and rc != 0


# ── score.sh writes the generic-exhaustion sentinel (the NEW exit-1 path) ─────────────────────


def test_score_sh_writes_scorer_failed_sentinel_block_present():
    """score.sh must, BEFORE its final ``exit 1``, leave the OUT file as valid JSON carrying the
    scorer_failed marker (mirrors the existing 429 quota-sentinel block) — pinned at the source so
    the sentinel block can't be deleted without this test going RED."""
    src = SCORE_SH.read_text(encoding="utf-8")
    assert '"scorer_failed"' in src, "score.sh must write an {error:scorer_failed} sentinel"
    # The sentinel write must come BEFORE the terminal exit 1 (not after — dead code).
    idx_sentinel = src.find('"scorer_failed"')
    idx_exit1 = src.rfind("\nexit 1")
    assert 0 < idx_sentinel < idx_exit1, (
        "the scorer_failed sentinel must be written BEFORE the final `exit 1`"
    )


def test_score_sh_generic_exhaustion_writes_valid_json_sentinel(tmp_path):
    """End-to-end on the sentinel-writing snippet: feed a tricky error string (quotes + newline)
    and assert the resulting OUT is VALID JSON the validator reads as a failure (never blank)."""
    out = tmp_path / "lens.json"
    # Mirror score.sh's exact python heredoc fallback contract.
    script = f'''
attempt=3
LAST_API_ERROR='overloaded: "503" Service
spanning a newline'
OUT="{out}"
python3 - "$OUT" "$attempt" "${{LAST_API_ERROR:-unknown}}" <<'PY' 2>/dev/null || \\
  printf '{{"error":"scorer_failed","attempts":%s,"last_api_error":"json_encode_failed"}}\\n' "$attempt" > "$OUT"
import json, sys
out, attempts, last = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    attempts_n = int(attempts)
except ValueError:
    attempts_n = attempts
json.dump({{"error": "scorer_failed", "attempts": attempts_n, "last_api_error": last}},
          open(out, "w"))
open(out, "a").write("\\n")
PY
. "{LIB}"
worldos_validate_lens_file "$OUT"
'''
    r = _bash(script)
    assert r.returncode != 0, "the validator must return non-zero for a sentinel"
    assert r.stdout.strip() == "sentinel", r.stdout
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["error"] == "scorer_failed" and d["attempts"] == 3
    assert "503" in d["last_api_error"], "the error string must round-trip into the sentinel"


# ── run_duo.sh wires the validator + the distinct 'unscorable' status ─────────────────────────


def test_run_duo_validates_lenses_and_marks_unscorable():
    """run_duo.sh must call the shared validator on the 3 lens files and emit the DISTINCT
    'unscorable' status — pinned at the source so the wiring can't silently regress to the old
    blank-jq print."""
    src = RUN_DUO.read_text(encoding="utf-8")
    assert "worldos_validate_lens_file" in src, "run_duo.sh must validate each lens file"
    assert "unscorable" in src, "run_duo.sh must mark a failed-scoring run 'unscorable'"
    assert "worldos_lens_display" in src, (
        "run_duo.sh must print lenses via worldos_lens_display (never the old blank `.overall//\"?\"`)"
    )
    # The old blank-prone print must be gone from the ACTUAL result-line echo (comments that
    # quote the old pattern when explaining the fix are fine — only the live `[duo] done.` echo
    # matters). Find the result-line echo and assert it no longer uses the blank-prone jq.
    result_lines = [
        ln for ln in src.splitlines()
        if "[duo] done." in ln and not ln.lstrip().startswith("#")
    ]
    assert result_lines, "the `[duo] done.` result-line echo must exist"
    for ln in result_lines:
        assert ".overall//\"?\"" not in ln, (
            "the result-line echo must NOT use `jq -r '.overall//\"?\"'` (it prints BLANK on a "
            f"failed scorer, masquerading as a no-score): {ln!r}"
        )
        assert "worldos_lens_display" in ln, (
            f"the result-line echo must render lenses via worldos_lens_display: {ln!r}"
        )


def test_run_duo_unscorable_branch_distinct_from_green(tmp_path):
    """Drive run_duo.sh's lens-validation + print logic in isolation with one EMPTY lens and one
    scorer_failed-SENTINEL lens (+ one good lens): the result line must NOT read as a clean GREEN —
    it must surface FAILED lenses AND status=unscorable. This is the core anti-regression: a failed
    scorer is NEITHER green nor a blank no-score."""
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"overall": 4.2, "scores": {}}), encoding="utf-8")
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_text(json.dumps({"error": "scorer_failed", "attempts": 3}), encoding="utf-8")

    # Faithful extract of run_duo.sh's WS0a block + result print (the load-bearing logic).
    script = f'''
. "{LIB}"
GATE=0
UNSCORABLE=0
UNSCORABLE_DETAIL=""
for _lens in "story-craft:{good}" "mechanical:{empty}" "angry-dm:{sentinel}"; do
  _lname="${{_lens%%:*}}"; _lpath="${{_lens#*:}}"
  _lstatus="$(worldos_validate_lens_file "$_lpath")"
  if [ "$_lstatus" != "ok" ]; then
    UNSCORABLE=1
    UNSCORABLE_DETAIL="${{UNSCORABLE_DETAIL}}${{UNSCORABLE_DETAIL:+, }}${{_lname}}=${{_lstatus}}"
  fi
done
echo "[duo] done. story-craft=$(worldos_lens_display "{good}") mechanical=$(worldos_lens_display "{empty}") angry-dm=$(worldos_lens_display "{sentinel}") behavioral=$([ "$GATE" = 0 ] && echo GREEN || echo RED)$([ "$UNSCORABLE" = 1 ] && echo ' status=unscorable')"
'''
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip()
    assert "status=unscorable" in line, f"a failed-scorer run must be marked unscorable: {line!r}"
    assert "mechanical=FAILED:missing" in line, f"the empty lens must print FAILED, not blank: {line!r}"
    assert "angry-dm=FAILED:sentinel" in line, f"the sentinel lens must print FAILED: {line!r}"
    assert "story-craft=4.2" in line, "the one good lens still shows its number"
    # CRITICAL: the failed lenses must NOT print blank (the original masquerade).
    assert "mechanical= " not in line and not line.rstrip().endswith("mechanical="), (
        f"a failed lens must never print a BLANK value: {line!r}"
    )


def test_run_duo_all_good_lenses_not_unscorable(tmp_path):
    """The happy path stays clean: 3 valid scorecards → numeric values, NO status=unscorable."""
    paths = []
    for i, name in enumerate(("a", "b", "c")):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps({"overall": 4.0 + i * 0.1, "scores": {}}), encoding="utf-8")
        paths.append(p)
    script = f'''
. "{LIB}"
GATE=0
UNSCORABLE=0
for _p in "{paths[0]}" "{paths[1]}" "{paths[2]}"; do
  [ "$(worldos_validate_lens_file "$_p")" != "ok" ] && UNSCORABLE=1
done
echo "[duo] done. story-craft=$(worldos_lens_display "{paths[0]}") mechanical=$(worldos_lens_display "{paths[1]}") angry-dm=$(worldos_lens_display "{paths[2]}") behavioral=$([ "$GATE" = 0 ] && echo GREEN || echo RED)$([ "$UNSCORABLE" = 1 ] && echo ' status=unscorable')"
'''
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip()
    assert "status=unscorable" not in line, f"all-valid run must NOT be unscorable: {line!r}"
    values = dict(part.split("=", 1) for part in line.split() if "=" in part)
    assert values.get("story-craft") in {"4", "4.0"}
    assert values.get("mechanical") == "4.1"
    assert values.get("angry-dm") == "4.2"
    assert "FAILED:" not in line


# ── doc: SCORING.md documents the blank-lens semantics ───────────────────────────────────────


def test_scoring_md_documents_blank_lens_is_scorer_failure():
    md = SCORING_MD.read_text(encoding="utf-8")
    assert "unscorable" in md, "SCORING.md must document the 'unscorable' status"
    assert "scorer FAILURE" in md or "scorer failure" in md.lower(), (
        "SCORING.md must document that a BLANK lens value is a scorer FAILURE, not a valid no-score"
    )


# ── hygiene: the touched shell surfaces stay /bin/bash -n clean (macOS bash 3.2) ──────────────


@pytest.mark.parametrize("rel", ["qa/lib_beat_driver.sh", "qa/score.sh", "qa/run_duo.sh"])
def test_touched_scripts_parse_under_bin_bash(rel):
    r = subprocess.run(["/bin/bash", "-n", str(REPO_ROOT / rel)], capture_output=True, text=True)
    assert r.returncode == 0, f"{rel} failed bash -n: {r.stderr}"
