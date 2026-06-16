#!/usr/bin/env python3
"""WorldOS split-lane RRI orchestrator — run the VM(part-B personas)+Mac(part-A
handoff) RRI rollup as ONE coordinated, auditable step instead of hand-run SSH/scp.

WHY THIS EXISTS
  The support-VM lane (heavy 5-persona real-browser sweep) and the Mac lane
  (built-app native #356 + handoff.json) must roll up into ONE same-SHA RRI. Doing
  it by hand is a long fragile chain of `ssh`/`scp`/preflight/`release_readiness.py`
  steps, each a place a stale build / mixed SHA / silent auth failure can produce a
  meaningless green. This tool composes the EXISTING primitives:
    qa/support_vm_preflight.py   — the same-SHA / auth / tool / private-art readiness gate
    qa/ui_playtest_app.sh        — the part-B persona sweep (run ON the VM)
    qa/release_readiness.py      — the RRI rollup (--handoff-json + --support-preflight-json)
  It does NOT reimplement any of them; it sequences them and refuses loudly on the traps.

HARD SAFETY (the whole point of the default mode)
  The actual remote SSH / persona-run step is OPERATOR-APPROVED ONLY:
    * The DEFAULT mode is --plan (a DRY RUN). It PRINTS the exact commands it WOULD
      run (preflight, persona sweep, fetch, rollup) and executes NOTHING remote.
    * Remote execution happens ONLY behind an explicit --execute flag, and even then
      the SSH command is shown FIRST.
    * Importing this module SSHes nowhere; the default runner is a no-op placeholder.
    * Connection/auth details (VM host, key path, remote checkout) are NEVER hardcoded
      — the operator supplies them as flags (the runbook keeps them in operator-only
      runbooks, not the tracked repo).
  Before ANY execution (or even when assembling the rollup) the tool REFUSES when the
  support preflight is missing/blocked or the build SHA does not match the preflight /
  handoff — a mismatched-SHA rollup is exactly the corruption RRI exists to prevent.

This module is a pure reader/reporter + an opt-in, operator-gated remote step. It
does not write any committed data artifact (qa/RRI.json etc.) on its own — the RRI
output path is operator-chosen and the rollup is the operator's to run.

Usage:
  orchestrate_split_rri.py \
      --build-sha SHA \
      --handoff-json /path/to/handoff.json \
      --support-preflight-json /path/to/support_vm_preflight.json \
      --ssh-host root@<vm> --ssh-key /path/to/key \
      --remote-repo /root/worldos-qa/WorldOS \
      [--vm-run-root /root/worldos-qa/runs] [--local-fetch-dir DIR] \
      [--rri-out qa/RRI.json] [--personas newbie,veteran,...] \
      [--plan | --execute] [--json]
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

# Reuse the release-authority validators rather than duplicating the same-SHA /
# readiness contract. qa/ has no __init__.py, so make the sibling import resolve
# regardless of the caller's cwd (CI runs pytest from servers/engine).
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from qa.release_readiness import (  # noqa: E402
    build_sha_matches,
    read_json_with_error,
    validate_support_preflight_json,
)

CANONICAL_PERSONAS = ["newbie", "veteran", "adversarial", "narrative", "optimizer"]
DEFAULT_BUDGET = "12.00"
DEFAULT_PORT = 8785


RunResult = dict
# A runner takes an argv sequence and returns {ok, exit_code, stdout, stderr}.
Runner = Callable[..., RunResult]


def _noop_runner(argv: Sequence[str], **kwargs) -> RunResult:
    """The default runner. It NEVER runs anything — calling it is a programming
    error (run() in plan mode must not invoke a runner, and execute mode must be
    given a real runner explicitly). This guarantees `import` and accidental
    default use SSH nowhere."""
    raise RuntimeError(
        "orchestrate_split_rri: no runner supplied for a remote step. "
        "Remote execution is operator-gated; pass an explicit runner (or use --plan)."
    )


@dataclass
class OrchestratorConfig:
    repo: Path
    build_sha: str
    handoff_json: Path
    support_preflight_json: Path
    ssh_host: str
    ssh_key: str
    remote_repo: str
    vm_run_root: str = "/root/worldos-qa/runs"
    local_fetch_dir: Path = field(default_factory=lambda: Path("qa/ui_playtest_runs/split-vm-fetch"))
    rri_out: Path = field(default_factory=lambda: Path("qa/RRI.json"))
    personas: list[str] = field(default_factory=lambda: list(CANONICAL_PERSONAS))
    budget: str = DEFAULT_BUDGET
    port: int = DEFAULT_PORT
    provider: str = "codex"
    player_agent: str = "codex"

    def run_prefix(self) -> str:
        short = (self.build_sha or "SHA").strip()[:7] or "SHA"
        return f"gate-{short}"


def q(value: object) -> str:
    return shlex.quote(str(value))


# ── precondition validation ──────────────────────────────────────────────────────
def validate_preconditions(config: OrchestratorConfig) -> list[dict]:
    """Return refusal gaps. EMPTY == safe to assemble/execute the split rollup.

    Reuses release_readiness' same-SHA validators so the orchestrator and the RRI
    rollup agree on what 'same-SHA, ready, not-blocked' means — no second source of
    truth. A non-empty result means the orchestrator must REFUSE to execute.
    """
    gaps: list[dict] = []

    if not config.build_sha.strip():
        gaps.append({"gate": "orchestrate", "missing": "--build-sha", "detail": "a build SHA is required for a same-SHA split rollup"})

    # Support preflight is MANDATORY for the split lane (VM persona evidence rides
    # the Mac --handoff-json for native proof, which release_readiness only honors
    # when a same-SHA support preflight is present and green).
    if not config.support_preflight_json or str(config.support_preflight_json) in ("", ".") or not Path(config.support_preflight_json).is_file():
        where = str(config.support_preflight_json) if str(config.support_preflight_json) not in ("", ".") else "(not supplied)"
        gaps.append({
            "gate": "support_preflight",
            "missing": "--support-preflight-json",
            "detail": f"support preflight artifact not found: {where}",
        })
    else:
        _proof, preflight_gaps = validate_support_preflight_json(str(config.support_preflight_json), config.build_sha)
        gaps.extend(preflight_gaps)

    # The Mac handoff is what supplies the native #356 / built-app proof for the VM
    # persona lane. The orchestrator's job is SAME-SHA COORDINATION, not re-deriving
    # the release verdict — the DEEP per-gate handoff evidence check is performed by
    # release_readiness.py during the rollup itself (validate_handoff_json), so we do
    # not duplicate it here. We refuse upfront only on the cheap, meaningful coordination
    # signals: presence, status=passed, not-dirty, and same-SHA (a mismatched-SHA or
    # failed/dirty handoff is exactly the corruption the split rollup must not paper over).
    gaps.extend(_handoff_coordination_gaps(config))

    return gaps


def _handoff_coordination_gaps(config: OrchestratorConfig) -> list[dict]:
    gaps: list[dict] = []
    if not config.handoff_json or str(config.handoff_json) in ("", ".") or not Path(config.handoff_json).is_file():
        where = str(config.handoff_json) if str(config.handoff_json) not in ("", ".") else "(not supplied)"
        gaps.append({
            "gate": "native_gate",
            "missing": "--handoff-json",
            "detail": f"Mac handoff artifact not found: {where}",
        })
        return gaps
    payload, error = read_json_with_error(Path(config.handoff_json))
    if error:
        gaps.append({"gate": "native_gate", "missing": str(config.handoff_json), "detail": f"handoff JSON {error}"})
        return gaps
    if payload.get("schema") != "worldos.app-handoff.v1":
        gaps.append({"gate": "native_gate", "missing": str(config.handoff_json), "detail": "handoff schema is missing or wrong"})
    if payload.get("status") != "passed":
        gaps.append({"gate": "native_gate", "missing": str(config.handoff_json), "detail": f"handoff status is {payload.get('status') or 'missing'}"})
    if payload.get("dirty") is not False:
        gaps.append({"gate": "native_gate", "missing": str(config.handoff_json), "detail": "handoff evidence was recorded from a dirty worktree"})
    commit_sha = str(payload.get("commit_sha") or "")
    if config.build_sha and not build_sha_matches(commit_sha, config.build_sha):
        gaps.append({
            "gate": "native_gate",
            "missing": str(config.handoff_json),
            "detail": f"handoff commit_sha {commit_sha or 'missing'} does not match --build-sha {config.build_sha}",
        })
    return gaps


# ── command assembly (the "what it WOULD run" surface) ─────────────────────────────
def remote_persona_sweep_commands(config: OrchestratorConfig) -> list[str]:
    """The part-B persona sweep commands as they would run ON the VM (cwd = remote
    repo). Mirrors the env contract qa/support_vm_preflight.build_vm_persona_commands
    emits, so the two stay in lockstep."""
    commands: list[str] = []
    prefix = config.run_prefix()
    for persona in config.personas:
        commands.append(
            " ".join(
                [
                    f"WORLDOS_ART_REPO_ROOT={q(config.remote_repo)}",
                    "WOS_APP_PART=B",
                    "WOS_APP_SKIP_BUILD=1",
                    "WOS_APP_NO_GLOBAL_KILL=1",
                    f"WOS_APP_SELECTED_PROVIDER={q(config.provider)}",
                    f"WOS_APP_PLAYER_AGENT={q(config.player_agent)}",
                    f"WOS_APP_PREFERRED_PORT={q(config.port)}",
                    "qa/ui_playtest_app.sh",
                    q(f"{prefix}-{persona}"),
                    "baldurs-gate",
                    q(persona),
                    "40",
                    q(config.budget),
                ]
            )
        )
    return commands


def ssh_base(config: OrchestratorConfig) -> list[str]:
    base = ["ssh"]
    if config.ssh_key:
        base += ["-i", config.ssh_key]
    base += [config.ssh_host]
    return base


def preflight_command(config: OrchestratorConfig) -> str:
    """The same-SHA readiness gate run on the VM before any persona sweep."""
    remote = " ".join(
        [
            f"cd {q(config.remote_repo)} &&",
            "python3 qa/support_vm_preflight.py",
            "--repo", q(config.remote_repo),
            "--expected-sha", q(config.build_sha),
            "--provider", q(config.provider),
            "--player-agent", q(config.player_agent),
            "--art-root", q(config.remote_repo),
            "--private-art-mode", "required",
        ]
    )
    return " ".join(ssh_base(config) + [q(remote)])


def persona_sweep_command(config: OrchestratorConfig) -> str:
    """One SSH invocation that runs the part-B persona sweep on the VM."""
    inner = f"cd {q(config.remote_repo)} && " + " && ".join(remote_persona_sweep_commands(config))
    return " ".join(ssh_base(config) + [q(inner)])


def local_run_dirs(config: OrchestratorConfig) -> list[Path]:
    """The LOCAL per-persona run dirs the VM artifacts are fetched into."""
    prefix = config.run_prefix()
    return [Path(config.local_fetch_dir) / f"{prefix}-{persona}" for persona in config.personas]


def fetch_command(config: OrchestratorConfig) -> str:
    """scp the per-persona VM run dirs back to the local fetch dir."""
    prefix = config.run_prefix()
    remote_glob = f"{config.vm_run_root}/{prefix}-*"
    parts = ["scp", "-r"]
    if config.ssh_key:
        parts += ["-i", config.ssh_key]
    parts += [f"{config.ssh_host}:{remote_glob}", str(config.local_fetch_dir)]
    return " ".join(q(p) if (" " in str(p)) else str(p) for p in parts)


def build_rollup_args(config: OrchestratorConfig) -> list[str]:
    """Assemble the release_readiness.py arg list for the same-SHA RRI rollup.

    This is the heart of the tool: it threads the Mac --handoff-json, the
    --support-preflight-json, the --build-sha and the fetched VM persona run dirs
    into the ONE rollup invocation. The args are intentionally a list so callers can
    assert on exact pairs (no string-parsing fragility)."""
    run_dirs = local_run_dirs(config)
    args = [
        "--runs", ",".join(str(p) for p in run_dirs),
        "--expected-personas", ",".join(config.personas),
        "--handoff-json", str(config.handoff_json),
        "--support-preflight-json", str(config.support_preflight_json),
        "--build-sha", config.build_sha,
        "--out", str(config.rri_out),
        "--scorecard-row",
    ]
    return args


def build_rollup_command(config: OrchestratorConfig) -> str:
    """The rollup as a single LOCAL command string (runs on the Mac, not the VM)."""
    parts = ["python3", "qa/release_readiness.py", *build_rollup_args(config)]
    return " ".join(q(p) for p in parts)


def build_plan(config: OrchestratorConfig) -> dict:
    """A machine-readable plan: the ordered command sequence the tool would run,
    plus the precondition gaps that would refuse it. PURE — runs nothing."""
    steps = [
        {
            "kind": "preflight",
            "where": "vm",
            "description": "Same-SHA support-VM readiness gate (refuses on stale build / unproven auth).",
            "command": preflight_command(config),
        },
        {
            "kind": "persona_sweep",
            "where": "vm",
            "description": "Part-B five-persona real-browser sweep on the VM (operator-approved remote step).",
            "command": persona_sweep_command(config),
            "per_persona": remote_persona_sweep_commands(config),
        },
        {
            "kind": "fetch",
            "where": "local",
            "description": "Copy the per-persona VM run dirs back to the local fetch dir.",
            "command": fetch_command(config),
        },
        {
            "kind": "rollup",
            "where": "local",
            "description": "Same-SHA RRI rollup (--handoff-json + --support-preflight-json + --build-sha).",
            "command": build_rollup_command(config),
        },
    ]
    gaps = validate_preconditions(config)
    return {
        "schema": "worldos.orchestrate-split-rri.plan.v1",
        "build_sha": config.build_sha,
        "ssh_host": config.ssh_host,
        "remote_repo": config.remote_repo,
        "personas": config.personas,
        "local_fetch_dir": str(config.local_fetch_dir),
        "rri_out": str(config.rri_out),
        "rollup_args": build_rollup_args(config),
        "steps": steps,
        "gaps": gaps,
        "ready": not gaps,
        "safety": (
            "Default mode is a DRY RUN: nothing remote runs without --execute. The remote "
            "persona sweep is operator-approved and the SSH command is shown before it runs."
        ),
    }


def render_plan_text(plan: dict) -> str:
    lines = [
        "# WorldOS split-lane RRI orchestration plan (DRY RUN)",
        "",
        f"- Build SHA: {plan['build_sha'] or '(missing)'}",
        f"- SSH host: {plan['ssh_host'] or '(missing)'}",
        f"- Remote repo: {plan['remote_repo'] or '(missing)'}",
        f"- Personas: {','.join(plan['personas'])}",
        f"- Local fetch dir: {plan['local_fetch_dir']}",
        f"- RRI out: {plan['rri_out']}",
        f"- Ready: {str(plan['ready']).lower()}",
        "",
        plan["safety"],
        "",
        "## Command sequence (this is what would run)",
        "",
    ]
    for idx, step in enumerate(plan["steps"], start=1):
        lines.append(f"### {idx}. {step['kind']} ({step['where']})")
        lines.append(f"# {step['description']}")
        lines.append(step["command"])
        lines.append("")
    if plan["gaps"]:
        lines.append("## REFUSAL — preconditions not met (would NOT execute)")
        lines.append("")
        for gap in plan["gaps"]:
            lines.append(f"- [{gap.get('gate')}] {gap.get('missing')}: {gap.get('detail')}")
        lines.append("")
    return "\n".join(lines)


def run(config: OrchestratorConfig, *, execute: bool = False, runner: Runner | None = None) -> dict:
    """Build the plan and, only when execute=True AND preconditions hold, run the
    operator-approved REMOTE step via the injected runner.

    In plan mode (default) NOTHING is run and `executed` is False. Even in execute
    mode, the SSH command string is captured in `shown_command` (the caller prints it
    first) and a missing/blocked preflight or SHA mismatch REFUSES execution. There is
    no path to a live SSH without an explicitly injected runner."""
    plan = build_plan(config)
    result = {
        "mode": "execute" if execute else "plan",
        "executed": False,
        "ready": plan["ready"],
        "gaps": plan["gaps"],
        "plan": plan,
        "shown_command": "",
    }

    if not execute:
        return result

    # --execute: refuse on any precondition gap (the whole point of the gate).
    if plan["gaps"]:
        result["refused"] = True
        return result

    runner = runner or _noop_runner
    # Show the SSH command FIRST (operator can read exactly what runs), then run only
    # the remote persona sweep through the injected runner. Fetch + rollup remain the
    # operator's local steps (the plan prints them) so this tool never silently writes
    # a committed RRI artifact.
    sweep_step = next(step for step in plan["steps"] if step["kind"] == "persona_sweep")
    result["shown_command"] = sweep_step["command"]
    argv = ssh_base(config) + [
        "cd " + q(config.remote_repo) + " && " + " && ".join(remote_persona_sweep_commands(config))
    ]
    run_result = runner(argv)
    result["executed"] = True
    result["remote_result"] = run_result
    return result


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="Local WorldOS checkout (for the rollup; default cwd)")
    parser.add_argument("--build-sha", default="", help="SHA both lanes must match (REQUIRED for a real run)")
    parser.add_argument("--handoff-json", default="", help="Mac app handoff JSON (qa/app_handoff_gate.py) for native proof")
    parser.add_argument("--support-preflight-json", default="", help="Support-VM preflight JSON (qa/support_vm_preflight.py)")
    parser.add_argument("--ssh-host", default="", help="Operator-supplied VM ssh target, e.g. root@host (NOT hardcoded)")
    parser.add_argument("--ssh-key", default="", help="Operator-supplied SSH key path")
    parser.add_argument("--remote-repo", default="", help="Remote WorldOS checkout path on the VM")
    parser.add_argument("--vm-run-root", default="/root/worldos-qa/runs", help="Remote dir holding per-persona run dirs")
    parser.add_argument("--local-fetch-dir", default="qa/ui_playtest_runs/split-vm-fetch", help="Local dir to fetch VM run dirs into")
    parser.add_argument("--rri-out", default="qa/RRI.json", help="RRI rollup output path (operator-chosen)")
    parser.add_argument("--personas", default=",".join(CANONICAL_PERSONAS), help="Comma-separated persona list")
    parser.add_argument("--budget", default=DEFAULT_BUDGET)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--player-agent", choices=("codex", "claude"), default="codex")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="DRY RUN (default): print the command sequence, run nothing remote")
    mode.add_argument("--execute", action="store_true", help="Operator-approved: run the remote persona sweep (SSH shown first)")
    parser.add_argument("--json", action="store_true", help="Emit the machine-readable plan/result as JSON")
    return parser.parse_args(list(argv))


def config_from_args(args: argparse.Namespace) -> OrchestratorConfig:
    personas = [p.strip() for p in args.personas.split(",") if p.strip()] or list(CANONICAL_PERSONAS)
    return OrchestratorConfig(
        repo=Path(args.repo).expanduser(),
        build_sha=args.build_sha.strip(),
        handoff_json=Path(args.handoff_json).expanduser() if args.handoff_json else Path(""),
        support_preflight_json=Path(args.support_preflight_json).expanduser() if args.support_preflight_json else Path(""),
        ssh_host=args.ssh_host.strip(),
        ssh_key=args.ssh_key.strip(),
        remote_repo=args.remote_repo.strip(),
        vm_run_root=args.vm_run_root.strip(),
        local_fetch_dir=Path(args.local_fetch_dir).expanduser(),
        rri_out=Path(args.rri_out).expanduser(),
        personas=personas,
        budget=args.budget,
        port=args.port,
        provider=args.provider,
        player_agent=args.player_agent,
    )


def main(argv: Sequence[str] | None = None, *, runner: Runner | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    config = config_from_args(args)
    execute = bool(args.execute)

    result = run(config, execute=execute, runner=runner)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_plan_text(result["plan"]))
        if execute:
            if result["executed"]:
                print("\n# EXECUTED the remote persona sweep (SSH shown above). "
                      "Now run the fetch + rollup steps locally when the sweep completes.")
            else:
                print("\n# REFUSED to execute: preconditions not met (see REFUSAL above).")

    # Exit non-zero when a real run was requested but refused, OR when a plan is not
    # ready (so CI/operators see the red). A clean plan exits 0 (it ran nothing).
    if execute and not result["executed"]:
        return 1
    if not result["ready"] and execute:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
