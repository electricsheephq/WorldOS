"""Tests for qa/evidence_audit.py — the "is this a real blocker, or do I just need
more runs/budget?" classifier.

PURE READER discipline: every fixture writes a *temporary* RRI.json / run dir under
tmp_path (NEVER the committed qa/RRI.json or any default-path artifact). The tool is
exercised both as an importable API and through its --json CLI against temp inputs.

Coverage required by the build spec:
  1. partial-sweep evidence gap  -> EVIDENCE_GAP (recoverable: run more personas)
  2. genuine failing gate        -> REAL_BLOCKER (fix the product)
  3. complete pass               -> no gaps, no blockers, release_ready
plus: abort (quota) reclassification, artifact-not-supplied gap, and CLI --json shape.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "qa"
SCRIPT = QA / "evidence_audit.py"

sys.path.insert(0, str(QA))
import evidence_audit  # noqa: E402


# ---- RRI.json fixture builders (temp-only) --------------------------------------

def _complete_pass_rri() -> dict:
    """An RRI.json that mirrors release_readiness.py's READY/release_ready shape."""
    gate_names = evidence_audit.RRI_GATE_NAMES
    return {
        "rri": 10.0,
        "status": "READY",
        "aborted": False,
        "abort_reason": "",
        "abort_detail": "",
        "infra_aborted_personas": [],
        "release_ready": True,
        "partial": False,
        "harness_contaminated": False,
        "required_release_personas": ["newbie", "veteran", "adversarial", "narrative", "optimizer"],
        "completed_personas": ["newbie", "veteran", "adversarial", "narrative", "optimizer"],
        "missing_personas": [],
        "missing_release_personas": [],
        "harness_failures": [],
        "evidence_gaps": [],
        "gates_passed": len(gate_names),
        "gates_total": len(gate_names),
        "failed_gates": [],
        "gate_detail": {g: "ok" for g in gate_names},
        "signals": {"cross_persona_satisfaction": 9.0},
    }


def _partial_sweep_rri() -> dict:
    """Only newbie+veteran of the canonical five ran — the other gates fail purely for
    lack of evidence (every failed gate carries a matching evidence_gaps entry)."""
    missing = ["adversarial", "narrative", "optimizer"]
    detail = "missing release persona(s): " + ", ".join(missing)
    failed_gates = ["missing_personas", "missing_release_personas",
                    "cross_persona_sat", "no_give_up", "zero_critical", "image_render"]
    evidence_gaps = [
        {"gate": g, "missing": "canonical five-persona release set", "detail": detail}
        for g in ("cross_persona_sat", "no_give_up", "zero_critical", "image_render")
    ]
    return {
        "rri": 6.4,
        "status": "NOT_READY",
        "aborted": False,
        "release_ready": False,
        "partial": True,
        "harness_contaminated": True,
        "required_release_personas": ["newbie", "veteran", "adversarial", "narrative", "optimizer"],
        "completed_personas": ["newbie", "veteran"],
        "missing_personas": missing,
        "missing_release_personas": missing,
        "harness_failures": [],
        "evidence_gaps": evidence_gaps,
        "gates_passed": 7,
        "gates_total": 11,
        "failed_gates": failed_gates,
        "gate_detail": {
            "cross_persona_sat": "avg=9.0/10 over 2",
            "no_give_up": "any_gave_up=False",
            "zero_critical": "critical=0; console_errors=0",
            "image_render": "source=vm-network; rate=100.00%; denominator=2",
        },
        "signals": {"cross_persona_satisfaction": 9.0},
    }


def _real_fail_rri() -> dict:
    """Five personas, same SHA, full evidence — but story 3.0 (<4.3), a give_up, a
    critical bug, sat 4.0 (<7.0). evidence_gaps is EMPTY: these are real measurements
    that missed thresholds."""
    return {
        "rri": 6.4,
        "status": "NOT_READY",
        "aborted": False,
        "release_ready": False,
        "partial": False,
        "harness_contaminated": False,
        "required_release_personas": ["newbie", "veteran", "adversarial", "narrative", "optimizer"],
        "completed_personas": ["newbie", "veteran", "adversarial", "narrative", "optimizer"],
        "missing_personas": [],
        "missing_release_personas": [],
        "harness_failures": [],
        "evidence_gaps": [],
        "gates_passed": 7,
        "gates_total": 11,
        "failed_gates": ["cross_persona_sat", "no_give_up", "zero_critical", "story_craft"],
        "gate_detail": {
            "cross_persona_sat": "avg=4.0/10 over 5; score_pass_failed=none; missing=none; release_missing=none",
            "no_give_up": "any_gave_up=True",
            "zero_critical": "critical=1; console_errors=0",
            "story_craft": "story=3.0",
        },
        "signals": {"cross_persona_satisfaction": 4.0},
    }


def _abort_rri() -> dict:
    rri = _partial_sweep_rri()
    rri.update({
        "status": "ABORTED",
        "aborted": True,
        "abort_reason": "quota_session_limit",
        "abort_detail": "newbie: resets 3:00pm (UTC)",
        "infra_aborted_personas": [{"persona": "newbie", "run": "gate-newbie", "reset_hint": "resets 3:00pm (UTC)"}],
    })
    return rri


def _artifact_not_supplied_rri() -> dict:
    """Story/behavioral lenses were never supplied to the rollup (recoverable)."""
    rri = _real_fail_rri()
    rri["failed_gates"] = ["story_craft", "behavioral"]
    rri["gate_detail"] = {"story_craft": "story=n/a", "behavioral": "behavioral=n/a"}
    rri["evidence_gaps"] = [
        {"gate": "story_craft", "missing": "--story", "detail": "story lens path not supplied"},
        {"gate": "behavioral", "missing": "--behavioral-path", "detail": "behavioral evidence path not supplied"},
    ]
    return rri


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


class EvidenceAuditApiTests(unittest.TestCase):
    def setUp(self):
        # tmp dir created per-test; nothing touches committed artifacts
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_requirements_json_loads_and_declares_verdicts(self):
        reqs = evidence_audit.load_requirements()
        self.assertEqual(reqs.get("schema"), "worldos.verdict-requirements.v1")
        for verdict in ("rri_release", "duo_quality", "combat_sprint_median"):
            self.assertIn(verdict, reqs["verdicts"], f"verdict {verdict} must be declared")
        self.assertTrue(reqs["gap_recovery_rules"])

    def test_complete_pass_has_no_gaps_no_blockers(self):
        rri = _write(self.tmp, "RRI.json", _complete_pass_rri())
        report = evidence_audit.audit_rri(rri)
        self.assertTrue(report["release_ready"])
        self.assertEqual(report["evidence_gaps"], [])
        self.assertEqual(report["real_blockers"], [])
        self.assertEqual(report["verdict"], "RELEASE_READY")
        # every required item PRESENT
        self.assertTrue(all(item["present"] for item in report["items"]))

    def test_partial_sweep_is_recoverable_evidence_gap_not_blocker(self):
        rri = _write(self.tmp, "RRI.json", _partial_sweep_rri())
        report = evidence_audit.audit_rri(rri)
        self.assertFalse(report["release_ready"])
        # The missing-persona gates are EVIDENCE_GAP, never REAL_BLOCKER.
        self.assertEqual(report["real_blockers"], [],
                         f"a partial sweep must have NO real blockers, got {report['real_blockers']}")
        gap_gates = {g["gate"] for g in report["evidence_gaps"]}
        for gate in ("cross_persona_sat", "no_give_up", "zero_critical", "image_render"):
            self.assertIn(gate, gap_gates)
        # each gap classified recoverable with the "run more personas" action
        for g in report["evidence_gaps"]:
            self.assertTrue(g["recoverable"])
            self.assertEqual(g["classification"], "EVIDENCE_GAP")
            self.assertEqual(g["rule_id"], "missing_personas")
            self.assertIn("persona", g["action"].lower())
        self.assertEqual(report["verdict"], "EVIDENCE_GAP")

    def test_genuine_failing_gate_is_real_blocker(self):
        rri = _write(self.tmp, "RRI.json", _real_fail_rri())
        report = evidence_audit.audit_rri(rri)
        self.assertFalse(report["release_ready"])
        blocker_gates = {b["gate"] for b in report["real_blockers"]}
        # story below 4.3, gave_up, critical bug, sat<7 — all REAL with evidence present
        for gate in ("story_craft", "no_give_up", "zero_critical", "cross_persona_sat"):
            self.assertIn(gate, blocker_gates, f"{gate} must be a REAL_BLOCKER")
        for b in report["real_blockers"]:
            self.assertFalse(b["recoverable"])
            self.assertEqual(b["classification"], "REAL_BLOCKER")
            self.assertTrue(b["action"], "a real blocker must carry a fix action")
            self.assertIn(b["measured"], (b["measured"],))  # measured detail carried through
        # a real-blocker build has no recoverable evidence gaps
        self.assertEqual(report["evidence_gaps"], [])
        self.assertEqual(report["verdict"], "REAL_BLOCKER")

    def test_artifact_not_supplied_is_recoverable_gap_not_blocker(self):
        rri = _write(self.tmp, "RRI.json", _artifact_not_supplied_rri())
        report = evidence_audit.audit_rri(rri)
        self.assertEqual(report["real_blockers"], [])
        gap_gates = {g["gate"] for g in report["evidence_gaps"]}
        self.assertEqual(gap_gates, {"story_craft", "behavioral"})
        for g in report["evidence_gaps"]:
            self.assertTrue(g["recoverable"])
            self.assertEqual(g["rule_id"], "artifact_path_not_supplied")

    def test_abort_reclassifies_everything_as_recoverable(self):
        rri = _write(self.tmp, "RRI.json", _abort_rri())
        report = evidence_audit.audit_rri(rri)
        self.assertTrue(report["aborted"])
        self.assertEqual(report["verdict"], "ABORTED_RECOVERABLE")
        self.assertEqual(report["real_blockers"], [],
                         "a quota abort is never a product blocker")
        self.assertTrue(report["evidence_gaps"])
        for g in report["evidence_gaps"]:
            self.assertTrue(g["recoverable"])
            self.assertEqual(g["classification"], "ABORTED_RECOVERABLE")
            self.assertIn("3:00pm", g["action"] + report.get("abort_detail", ""))

    def test_run_dir_mode_reads_rri_json_inside_dir(self):
        run_dir = self.tmp / "sweep-run"
        run_dir.mkdir()
        _write(run_dir, "RRI.json", _complete_pass_rri())
        report = evidence_audit.audit_run(run_dir)
        self.assertEqual(report["verdict"], "RELEASE_READY")
        self.assertEqual(Path(report["source"]).name, "RRI.json")

    def test_run_dir_without_rri_is_reported_not_crashed(self):
        run_dir = self.tmp / "empty-run"
        run_dir.mkdir()
        report = evidence_audit.audit_run(run_dir)
        self.assertEqual(report["verdict"], "NO_RRI")
        self.assertFalse(report["release_ready"])


class EvidenceAuditCliTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _run(self, *args: str) -> tuple[int, dict, str]:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True, capture_output=True, check=False,
        )
        payload = {}
        if proc.stdout.strip():
            try:
                payload = json.loads(proc.stdout)
            except json.JSONDecodeError:
                payload = {}
        return proc.returncode, payload, proc.stdout + proc.stderr

    def test_cli_json_complete_pass_exit_zero(self):
        rri = _write(self.tmp, "RRI.json", _complete_pass_rri())
        rc, payload, _ = self._run("--rri", str(rri), "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(payload["verdict"], "RELEASE_READY")
        self.assertEqual(payload["real_blockers"], [])

    def test_cli_json_partial_sweep_recoverable_exit_code(self):
        rri = _write(self.tmp, "RRI.json", _partial_sweep_rri())
        rc, payload, _ = self._run("--rri", str(rri), "--json")
        # recoverable gaps -> distinct, non-blocker exit code
        self.assertEqual(rc, evidence_audit.EXIT_EVIDENCE_GAP)
        self.assertEqual(payload["verdict"], "EVIDENCE_GAP")
        self.assertEqual(payload["real_blockers"], [])

    def test_cli_json_real_blocker_exit_code(self):
        rri = _write(self.tmp, "RRI.json", _real_fail_rri())
        rc, payload, _ = self._run("--rri", str(rri), "--json")
        self.assertEqual(rc, evidence_audit.EXIT_REAL_BLOCKER)
        self.assertEqual(payload["verdict"], "REAL_BLOCKER")
        self.assertTrue(payload["real_blockers"])

    def test_cli_run_dir_mode(self):
        run_dir = self.tmp / "sweep"
        run_dir.mkdir()
        _write(run_dir, "RRI.json", _real_fail_rri())
        rc, payload, _ = self._run("--run", str(run_dir), "--json")
        self.assertEqual(payload["verdict"], "REAL_BLOCKER")

    def test_cli_human_output_is_not_json(self):
        rri = _write(self.tmp, "RRI.json", _partial_sweep_rri())
        rc, _payload, text = self._run("--rri", str(rri))
        self.assertIn("EVIDENCE_GAP", text)
        self.assertIn("REAL_BLOCKER", text)  # human report names both buckets


if __name__ == "__main__":
    unittest.main()
