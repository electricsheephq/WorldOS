"""Unit tests for qa/root_cause_analyzer.py — the RED-to-actionable-locations tool.

Stdlib + pytest only. Self-contained: every fixture is a synthetic gate.txt / check list
written into tmp_path, so the test runs in a fresh checkout where qa/transcripts/ (gitignored
runtime data) is empty. The analyzer is a PURE READER — these tests never write a committed
artifact, never touch qa/scores.db, never run a game.

Run with the engine venv (which has pytest):
    uv run --directory /Users/lume/clawdnd-qa-p2b/servers/engine \
        python -m pytest /Users/lume/clawdnd-qa-p2b/qa/test_root_cause_analyzer.py -q -p no:xdist
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# root_cause_analyzer lives next to this test (qa/); make it importable regardless of pytest rootdir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import root_cause_analyzer as rca  # noqa: E402

_QA_DIR = Path(__file__).resolve().parent
_TAXONOMY = json.loads((_QA_DIR / "BEHAVIORAL_GATE_TAXONOMY.json").read_text(encoding="utf-8"))


# ── gate.txt fixtures (the exact format assert_behavioral.py writes) ───────────────────────
def _write_gate(p: Path, *lines: str) -> Path:
    p.write_text("=== behavioral assertions ===\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return p


_RED_GATE = (
    "  [PASS] dm_produced_output",
    "  [PASS] both_sides_acted — player_turns=8 dm_turns=8",
    "  [FAIL] xp_not_orphaned — defeated monster 'Goblin' kept xp_value=50 — progression silently lost",
    "  [WARN] world_peopled — only 1 NPC(s) engaged",
    "  [FAIL] no_rejected_tool_calls — 1 tool call(s) rejected with a schema/validation error: ['attack']",
    "RED: 2 behavioral assertion(s) FAILED.",
)

_GREEN_GATE = (
    "  [PASS] dm_produced_output",
    "  [PASS] dice_used — roll=4 attack=3",
    "  [WARN] world_peopled — only 1 NPC(s) engaged",
    "GREEN (1 warning(s))",
)


# ── taxonomy integrity (the JSON the analyzer reads) ──────────────────────────────────────
def test_taxonomy_covers_every_check_with_valid_shape():
    """Every taxonomy entry has a valid category, non-empty locations, a retest cmd, a hint."""
    valid_cats = {"ENGINE_INVARIANT", "DM_ADHERENCE", "HARNESS_WIRING"}
    checks = _TAXONOMY["checks"]
    assert checks, "taxonomy has no checks"
    for name, entry in checks.items():
        assert entry["category"] in valid_cats, f"{name} has bad category {entry['category']!r}"
        assert isinstance(entry["likely_code_locations"], list) and entry["likely_code_locations"], \
            f"{name} has no code locations"
        assert isinstance(entry["retest"], str) and entry["retest"].strip(), f"{name} has no retest"
        assert isinstance(entry["hint"], str) and entry["hint"].strip(), f"{name} has no hint"


def test_taxonomy_matches_real_gate_check_names():
    """The taxonomy keys must be the real chk() names from assert_behavioral.py (no drift).

    Parses the chk("name", ...) calls straight out of the source so this fails loudly if a
    check is added/renamed in the gate without updating the taxonomy."""
    import re
    src = (_QA_DIR / "assert_behavioral.py").read_text(encoding="utf-8")
    real = set(re.findall(r'chk\(\s*"([a-z0-9_]+)"', src))
    real |= set(re.findall(r'chk\(\s*\n\s*"([a-z0-9_]+)"', src))
    taxo = set(_TAXONOMY["checks"])
    missing = real - taxo
    assert not missing, f"taxonomy is missing real gate checks: {sorted(missing)}"


# ── analyze_checks: the core mapping ──────────────────────────────────────────────────────
def test_analyze_known_check_maps_category_locations_retest():
    report = rca.analyze_checks(["xp_not_orphaned"])
    assert len(report) == 1
    r = report[0]
    assert r["check"] == "xp_not_orphaned"
    assert r["category"] == "ENGINE_INVARIANT"
    assert "servers/engine/combat.py" in r["likely_code_locations"]
    assert r["retest"].startswith("bash qa/run_combat_sprint.sh")
    assert r["hint"]  # non-empty


def test_analyze_dm_adherence_check_category():
    report = rca.analyze_checks(["dm_resolved_player_moves"])
    assert report[0]["category"] == "DM_ADHERENCE"
    assert any(loc.startswith("qa/play_dm_duo.txt") for loc in report[0]["likely_code_locations"])


def test_analyze_harness_wiring_check_category():
    report = rca.analyze_checks(["no_rejected_tool_calls"])
    assert report[0]["category"] == "HARNESS_WIRING"


def test_unknown_check_degrades_gracefully():
    """An UNKNOWN check never crashes — it maps to category UNKNOWN with a generic hint and a
    safe (non-empty) retest, and is clearly flagged so the agent knows the taxonomy is stale."""
    report = rca.analyze_checks(["totally_made_up_check_xyz"])
    assert len(report) == 1
    r = report[0]
    assert r["check"] == "totally_made_up_check_xyz"
    assert r["category"] == "UNKNOWN"
    assert r["hint"]  # a generic hint, not empty
    assert r["retest"]  # a safe generic retest, not empty
    assert isinstance(r["likely_code_locations"], list)  # never crashes; may be a generic pointer


def test_mixed_known_and_unknown_preserves_order_and_count():
    names = ["dice_used", "totally_made_up", "xp_not_orphaned"]
    report = rca.analyze_checks(names)
    assert [r["check"] for r in report] == names
    assert report[0]["category"] == "DM_ADHERENCE"
    assert report[1]["category"] == "UNKNOWN"
    assert report[2]["category"] == "ENGINE_INVARIANT"


def test_empty_check_list_returns_empty_report():
    assert rca.analyze_checks([]) == []


# ── gate.txt parsing: only the FAILED checks become actionable ─────────────────────────────
def test_failed_checks_from_red_gate(tmp_path):
    gate = _write_gate(tmp_path / "run.gate.txt", *_RED_GATE)
    failed = rca.failed_checks_from_gate(gate)
    # Only the two [FAIL] rows — WARN and PASS are not actionable RED causes.
    assert failed == ["xp_not_orphaned", "no_rejected_tool_calls"]


def test_failed_checks_from_green_gate_is_empty(tmp_path):
    gate = _write_gate(tmp_path / "run.gate.txt", *_GREEN_GATE)
    assert rca.failed_checks_from_gate(gate) == []


def test_failed_checks_missing_file_is_empty(tmp_path):
    assert rca.failed_checks_from_gate(tmp_path / "nope.gate.txt") == []


def test_include_warnings_opt_in(tmp_path):
    """With include_warnings=True the WARN rows are surfaced too (still distinct from FAIL)."""
    gate = _write_gate(tmp_path / "run.gate.txt", *_RED_GATE)
    failed = rca.failed_checks_from_gate(gate, include_warnings=True)
    assert "world_peopled" in failed
    assert "xp_not_orphaned" in failed


# ── analyze_gate end-to-end (the --gate path) ──────────────────────────────────────────────
def test_analyze_gate_produces_report_per_failed_check(tmp_path):
    gate = _write_gate(tmp_path / "run.gate.txt", *_RED_GATE)
    result = rca.analyze_gate(gate)
    assert result["verdict"] == "RED"
    assert result["failed_count"] == 2
    checks = [r["check"] for r in result["reports"]]
    assert checks == ["xp_not_orphaned", "no_rejected_tool_calls"]
    # The summary is a one-liner naming the categories at play.
    assert "ENGINE_INVARIANT" in result["summary"] or "HARNESS_WIRING" in result["summary"]


def test_analyze_gate_green_is_clean(tmp_path):
    gate = _write_gate(tmp_path / "run.gate.txt", *_GREEN_GATE)
    result = rca.analyze_gate(gate)
    assert result["verdict"] == "GREEN"
    assert result["failed_count"] == 0
    assert result["reports"] == []


# ── --run path: resolve <run_id> to its on-disk gate.txt ───────────────────────────────────
def test_resolve_run_gate_path(tmp_path):
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    _write_gate(transcripts / "duo-1200.gate.txt", *_RED_GATE)
    resolved = rca.resolve_run_gate("duo-1200", transcripts=transcripts)
    assert resolved == transcripts / "duo-1200.gate.txt"


def test_resolve_run_gate_missing_returns_none(tmp_path):
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    assert rca.resolve_run_gate("ghost-run", transcripts=transcripts) is None


# ── CLI: --checks and --json never crash, exit codes are sane ──────────────────────────────
def test_cli_checks_json(tmp_path, capsys):
    rc = rca.main(["--checks", "xp_not_orphaned,totally_made_up", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 0  # a pure reporter exits 0; the verdict lives in the payload, not the code
    names = [r["check"] for r in payload["reports"]]
    assert names == ["xp_not_orphaned", "totally_made_up"]
    assert payload["reports"][1]["category"] == "UNKNOWN"


def test_cli_gate_human_readable(tmp_path, capsys):
    gate = _write_gate(tmp_path / "run.gate.txt", *_RED_GATE)
    rc = rca.main(["--gate", str(gate)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "xp_not_orphaned" in out
    assert "ENGINE_INVARIANT" in out
    assert "no_rejected_tool_calls" in out


def test_cli_no_args_errors_cleanly(capsys):
    rc = rca.main([])
    assert rc == 2  # usage error, not a crash
