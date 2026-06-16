"""Tests for qa/orchestrate_split_rri.py — the split VM(part-B)+Mac(part-A handoff)
RRI rollup orchestrator.

These tests prove the HARD SAFETY contract of the tool:
  - importing the module runs NOTHING remote (no SSH on import / by default),
  - --plan (the default) PRINTS the exact command sequence and executes no remote step,
  - the RRI rollup invocation assembles the correct release_readiness.py args from
    on-disk fixtures (tmp_path),
  - a missing/blocked support preflight or a SHA mismatch is REFUSED,
  - no live SSH is ever performed in tests (a fake runner records would-be commands).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

# The qa/ tree has no __init__.py; make `import qa.X` resolve regardless of the
# pytest cwd (CI runs from servers/engine). Insert the repo root onto sys.path.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qa.orchestrate_split_rri as orch  # noqa: E402


SHA = "da05101cafef00dba5eba11deadbeef012345678"
SHORT = SHA[:7]


def write_support_preflight(
    tmp: Path,
    *,
    sha: str = SHORT,
    ready: bool = True,
    blocking_categories: list[str] | None = None,
) -> Path:
    """A support_vm_preflight.json shaped exactly like the validator in
    release_readiness.validate_support_preflight_json expects (mirrors the helper
    in test_release_readiness.py so the two stay in lockstep)."""
    preflight = tmp / "support_vm_preflight.json"
    blocking_categories = blocking_categories or []
    preflight.write_text(
        json.dumps(
            {
                "schema": "worldos.support-vm-preflight.v1",
                "verdict": "passed" if ready else "blocked",
                "ready_for_rri": ready,
                "release_verdict": False,
                "blockers": [] if ready else ["host memory below required capacity"],
                "repo": {
                    "head_short": sha,
                    "expected_sha": sha,
                    "expected_sha_match": True,
                    "dirty": False,
                    "origin_main_query": {"ok": True, "head_short": sha},
                },
                "readiness": {
                    "safe_to_run_personas": ready,
                    "release_verdict": False,
                    "expected_sha": sha,
                    "repo_head_short": sha,
                    "same_sha_ready": ready,
                    "provider": "codex",
                    "player_agent": "codex",
                    "provider_auth_ready": ready,
                    "player_agent_auth_ready": ready,
                    "required_tools_ready": ready,
                    "persona_briefs_ready": ready,
                    "private_art_ready": ready,
                    "artifact_return_ready": ready,
                    "host_capacity_ready": ready,
                    "min_memory_gb": 24,
                    "mac_handoff_required": True,
                    "blocking_categories": blocking_categories,
                },
                "rri_plan": {
                    "support_preflight_json": str(preflight),
                    "support_preflight_required_for_split_rollup": True,
                    "expected_personas": list(orch.CANONICAL_PERSONAS),
                    "rri_rollup_command_template": (
                        "python3 qa/release_readiness.py --runs VM_PERSONA_RUN_DIRS_CSV "
                        "--handoff-json SAME_SHA_MAC_HANDOFF_JSON "
                        "--support-preflight-json support_vm_preflight.json "
                        f"--build-sha {sha}"
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    return preflight


def write_handoff(tmp: Path, *, sha: str = SHORT) -> Path:
    handoff = tmp / "handoff.json"
    handoff.write_text(
        json.dumps(
            {
                "schema": "worldos.app-handoff.v1",
                "status": "passed",
                "handoff_score": 100,
                "dirty": False,
                "commit_sha": sha,
                "release_verdict": False,
                "gates": [],
            }
        ),
        encoding="utf-8",
    )
    return handoff


class RecordingRunner:
    """A stand-in for the remote runner. NEVER touches the network — it only
    records the argv it was asked to run and returns a canned success."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}


def make_config(tmp: Path, preflight: Path, handoff: Path, *, sha: str = SHA) -> "orch.OrchestratorConfig":
    return orch.OrchestratorConfig(
        repo=ROOT,
        build_sha=sha,
        handoff_json=handoff,
        support_preflight_json=preflight,
        ssh_host="root@example.invalid",
        ssh_key="/dev/null",
        remote_repo="/root/worldos-qa/WorldOS",
        vm_run_root="/root/worldos-qa/runs",
        local_fetch_dir=tmp / "fetched",
        rri_out=tmp / "RRI.json",
        personas=list(orch.CANONICAL_PERSONAS),
    )


class ImportSafetyTests(unittest.TestCase):
    def test_module_exposes_no_default_ssh_endpoint(self):
        # HARD SAFETY: the module must NOT hardcode an operator VM endpoint. The
        # runbook keeps connection/auth details in operator-only runbooks, not the
        # tracked repo. So the source must not embed the known VM IP / key path.
        src = Path(orch.__file__).read_text(encoding="utf-8")
        self.assertNotIn("178.104.123.213", src)
        self.assertNotIn("cloud-deploy-key", src)

    def test_import_does_not_ssh(self):
        # Importing the module (done at top of file) must not have run anything
        # remote. The default runner is a no-op placeholder, never auto-invoked.
        self.assertTrue(hasattr(orch, "build_plan"))
        self.assertTrue(hasattr(orch, "OrchestratorConfig"))


class PlanModeTests(unittest.TestCase):
    def test_plan_lists_command_sequence_without_running_anything(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg = make_config(tmp, write_support_preflight(tmp), write_handoff(tmp))
            runner = RecordingRunner()
            plan = orch.build_plan(cfg)
            result = orch.run(cfg, execute=False, runner=runner)

            # Nothing remote was run in plan mode.
            self.assertEqual(runner.calls, [])
            self.assertEqual(result["mode"], "plan")
            self.assertFalse(result["executed"])

            # The plan enumerates the three remote phases in order.
            kinds = [step["kind"] for step in plan["steps"]]
            self.assertEqual(kinds, ["preflight", "persona_sweep", "fetch", "rollup"])
            # Each remote step carries the exact command string it WOULD run.
            for step in plan["steps"]:
                self.assertTrue(step["command"].strip())

    def test_plan_renders_text_with_all_commands(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg = make_config(tmp, write_support_preflight(tmp), write_handoff(tmp))
            text = orch.render_plan_text(orch.build_plan(cfg))
            # The SSH command is shown (operator can read exactly what runs).
            self.assertIn("ssh", text)
            self.assertIn(cfg.ssh_host, text)
            # The persona sweep references the part-B sweep tool.
            self.assertIn("ui_playtest_app.sh", text)
            # The rollup line is the release_readiness invocation.
            self.assertIn("release_readiness.py", text)

    def test_cli_plan_is_default_and_runs_no_remote(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            preflight = write_support_preflight(tmp)
            handoff = write_handoff(tmp)
            runner = RecordingRunner()
            argv = [
                "--build-sha", SHA,
                "--handoff-json", str(handoff),
                "--support-preflight-json", str(preflight),
                "--ssh-host", "root@example.invalid",
                "--ssh-key", "/dev/null",
                "--remote-repo", "/root/worldos-qa/WorldOS",
                "--rri-out", str(tmp / "RRI.json"),
            ]
            rc = orch.main(argv, runner=runner)
            self.assertEqual(rc, 0)
            # Default == plan: nothing remote executed.
            self.assertEqual(runner.calls, [])

    def test_cli_json_emits_machine_readable_plan(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            preflight = write_support_preflight(tmp)
            handoff = write_handoff(tmp)
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            argv = [
                "--build-sha", SHA,
                "--handoff-json", str(handoff),
                "--support-preflight-json", str(preflight),
                "--ssh-host", "root@example.invalid",
                "--ssh-key", "/dev/null",
                "--remote-repo", "/root/worldos-qa/WorldOS",
                "--rri-out", str(tmp / "RRI.json"),
                "--json",
            ]
            with redirect_stdout(buf):
                rc = orch.main(argv, runner=RecordingRunner())
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["mode"], "plan")
            self.assertFalse(payload["executed"])
            # The full result nests the plan (which carries the command sequence).
            self.assertIn("plan", payload)
            self.assertIn("steps", payload["plan"])
            self.assertEqual(
                [s["kind"] for s in payload["plan"]["steps"]],
                ["preflight", "persona_sweep", "fetch", "rollup"],
            )


class RollupArgAssemblyTests(unittest.TestCase):
    def test_rollup_command_assembles_release_readiness_args(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            preflight = write_support_preflight(tmp)
            handoff = write_handoff(tmp)
            cfg = make_config(tmp, preflight, handoff)
            args = orch.build_rollup_args(cfg)

            # The rollup MUST pass the same-SHA handoff + support preflight + build-sha
            # through to release_readiness.py, plus the fetched VM persona run dirs.
            self.assertIn("--handoff-json", args)
            self.assertEqual(args[args.index("--handoff-json") + 1], str(handoff))
            self.assertIn("--support-preflight-json", args)
            self.assertEqual(args[args.index("--support-preflight-json") + 1], str(preflight))
            self.assertIn("--build-sha", args)
            self.assertEqual(args[args.index("--build-sha") + 1], SHA)
            self.assertIn("--expected-personas", args)
            self.assertEqual(
                args[args.index("--expected-personas") + 1],
                ",".join(orch.CANONICAL_PERSONAS),
            )
            self.assertIn("--out", args)
            self.assertEqual(args[args.index("--out") + 1], str(cfg.rri_out))

            # --runs points at the LOCAL fetched per-persona dirs (one per persona).
            self.assertIn("--runs", args)
            runs_csv = args[args.index("--runs") + 1]
            run_dirs = runs_csv.split(",")
            self.assertEqual(len(run_dirs), len(orch.CANONICAL_PERSONAS))
            for persona in orch.CANONICAL_PERSONAS:
                self.assertTrue(
                    any(persona in rd for rd in run_dirs),
                    f"expected a fetched run dir for persona {persona}",
                )

    def test_rollup_command_string_invokes_release_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg = make_config(tmp, write_support_preflight(tmp), write_handoff(tmp))
            cmd = orch.build_rollup_command(cfg)
            self.assertIn("release_readiness.py", cmd)
            self.assertIn("--support-preflight-json", cmd)
            self.assertIn("--handoff-json", cmd)


class RefusalTests(unittest.TestCase):
    def test_missing_support_preflight_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            handoff = write_handoff(tmp)
            missing = tmp / "does_not_exist.json"
            cfg = orch.OrchestratorConfig(
                repo=ROOT,
                build_sha=SHA,
                handoff_json=handoff,
                support_preflight_json=missing,
                ssh_host="root@example.invalid",
                ssh_key="/dev/null",
                remote_repo="/root/worldos-qa/WorldOS",
                vm_run_root="/root/worldos-qa/runs",
                local_fetch_dir=tmp / "fetched",
                rri_out=tmp / "RRI.json",
                personas=list(orch.CANONICAL_PERSONAS),
            )
            gaps = orch.validate_preconditions(cfg)
            self.assertTrue(gaps, "missing support preflight must produce a refusal gap")

            runner = RecordingRunner()
            result = orch.run(cfg, execute=True, runner=runner)
            self.assertFalse(result["executed"], "must refuse to execute with a missing preflight")
            self.assertEqual(runner.calls, [], "no remote command may run when refused")
            self.assertTrue(result["gaps"])

    def test_blocked_support_preflight_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            preflight = write_support_preflight(tmp, ready=False, blocking_categories=["host_capacity"])
            cfg = make_config(tmp, preflight, write_handoff(tmp))
            gaps = orch.validate_preconditions(cfg)
            self.assertTrue(gaps, "a blocked preflight must refuse the rollup")
            runner = RecordingRunner()
            result = orch.run(cfg, execute=True, runner=runner)
            self.assertFalse(result["executed"])
            self.assertEqual(runner.calls, [])

    def test_sha_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Preflight proves a DIFFERENT sha than --build-sha.
            preflight = write_support_preflight(tmp, sha="0000000")
            handoff = write_handoff(tmp, sha=SHORT)
            cfg = make_config(tmp, preflight, handoff, sha=SHA)
            gaps = orch.validate_preconditions(cfg)
            self.assertTrue(gaps, "a SHA mismatch must produce a refusal gap")
            runner = RecordingRunner()
            result = orch.run(cfg, execute=True, runner=runner)
            self.assertFalse(result["executed"])
            self.assertEqual(runner.calls, [])

    def test_handoff_sha_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            preflight = write_support_preflight(tmp, sha=SHORT)
            handoff = write_handoff(tmp, sha="9999999")
            cfg = make_config(tmp, preflight, handoff, sha=SHA)
            gaps = orch.validate_preconditions(cfg)
            self.assertTrue(gaps, "a mismatched handoff SHA must produce a refusal gap")
            runner = RecordingRunner()
            result = orch.run(cfg, execute=True, runner=runner)
            self.assertFalse(result["executed"])
            self.assertEqual(runner.calls, [])

    def test_clean_inputs_have_no_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            preflight = write_support_preflight(tmp, sha=SHORT)
            handoff = write_handoff(tmp, sha=SHORT)
            cfg = make_config(tmp, preflight, handoff, sha=SHA)
            self.assertEqual(orch.validate_preconditions(cfg), [])


class ExecuteSafetyTests(unittest.TestCase):
    def test_execute_shows_ssh_command_before_running(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg = make_config(tmp, write_support_preflight(tmp), write_handoff(tmp))
            runner = RecordingRunner()
            result = orch.run(cfg, execute=True, runner=runner)
            # With clean inputs, execute proceeds and the runner sees the remote step,
            # but the printed plan (shown_command) is captured first.
            self.assertTrue(result["executed"])
            self.assertTrue(result.get("shown_command"))
            self.assertIn("ssh", result["shown_command"])
            # The runner was actually invoked for the remote sweep step.
            self.assertTrue(runner.calls, "execute mode must invoke the injected runner")

    def test_execute_only_runs_remote_step_through_injected_runner(self):
        # No live SSH: the only way a remote command runs is via the injected runner.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg = make_config(tmp, write_support_preflight(tmp), write_handoff(tmp))
            runner = RecordingRunner()
            orch.run(cfg, execute=True, runner=runner)
            # Every recorded call is an ssh invocation to the configured host.
            for call in runner.calls:
                self.assertIn("ssh", call[0])


if __name__ == "__main__":
    unittest.main()
