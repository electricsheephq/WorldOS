#!/usr/bin/env python3
"""Read-only support-VM preflight for WorldOS five-persona RRI sweeps.

This script does not launch the app, mutate campaign state, run persona sweeps,
or print secrets. It writes a redacted artifact that answers the question:
"Is this host ready to produce same-SHA VM evidence for #466?"
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


SCHEMA = "worldos.support-vm-preflight.v1"
CANONICAL_PERSONAS = ["newbie", "veteran", "adversarial", "narrative", "optimizer"]
MIN_SHA_MATCH_CHARS = 7
MIN_CODEX_MCP_OVERRIDE_VERSION = (0, 120, 0)
BASE_REQUIRED_TOOLS = [
    "git",
    "python3",
    "uv",
    "node",
    "npm",
    "npx",
    "jq",
    "curl",
    "lsof",
    "timeout",
    "pkill",
    "pgrep",
    "ps",
]
PERSONA_PROVIDERS = ("codex", "claude")
PLAYER_AGENTS = ("codex", "claude")
INTERESTING_ENV_PREFIXES = ("WORLDOS_", "CLAWDND_", "CODEX_", "OPENAI_", "ANTHROPIC_")
SAFE_PATH_ENV_NAMES = {
    "WORLDOS_ART_REPO_ROOT",
    "CLAWDND_ART_REPO_ROOT",
    "WORLDOS_REPO_ROOT",
    "CLAWDND_REPO_ROOT",
    "CODEX_HOME",
}


CommandRunner = Callable[[Sequence[str], Path | None, int], dict]
WhichFn = Callable[[str], str | None]


@dataclass
class PreflightConfig:
    repo: Path
    expected_sha: str
    artifact_dir: Path
    artifact_return_target: str
    art_root: Path
    private_art_mode: str
    personas: list[str]
    budget: str
    concurrency: int
    port: int
    provider: str = "codex"
    player_agent: str = "codex"


def required_tools_for(config: PreflightConfig) -> list[str]:
    tools = list(BASE_REQUIRED_TOOLS)
    if config.provider == "codex" or config.player_agent == "codex":
        tools.append("codex")
    if config.provider == "claude" or config.player_agent == "claude":
        tools.append("claude")
    return tools


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def redact(value: str) -> str:
    if not value:
        return value
    redacted = str(value)
    redacted = re.sub(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b", "sk-[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*\s*[=:]\s*)([\"']?)[^\"'\s,;]+",
        r"\1\2[REDACTED]",
        redacted,
    )
    return redacted


def redacted_remote_url(value: str) -> str:
    value = redact(value or "")
    return re.sub(r"(https?://)[^/@\s]+@", r"\1[REDACTED]@", value)


def is_secret_env_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))


def looks_like_path(value: str) -> bool:
    return value.startswith(("/", "~", "./", "../")) or "/" in value


def env_snapshot(env: dict[str, str]) -> dict:
    out: dict[str, dict] = {}
    for key in sorted(env):
        if not key.startswith(INTERESTING_ENV_PREFIXES):
            continue
        value = env.get(key, "")
        entry = {"present": bool(value)}
        if not value:
            entry["value"] = ""
            entry["value_policy"] = "empty"
        elif is_secret_env_key(key):
            entry["value"] = "[REDACTED]"
            entry["value_policy"] = "secret-redacted"
        elif key in SAFE_PATH_ENV_NAMES or (key.endswith(("_ROOT", "_DIR", "_HOME")) and looks_like_path(value)):
            entry["value"] = redact(value)
            entry["value_policy"] = "safe-path"
        else:
            entry["value"] = "[REDACTED]"
            entry["value_policy"] = "presence-only"
        out[key] = entry
    return out


def build_sha_matches(reported: str, expected: str) -> bool:
    reported = (reported or "").strip()
    expected = (expected or "").strip()
    if len(reported) < MIN_SHA_MATCH_CHARS or len(expected) < MIN_SHA_MATCH_CHARS:
        return False
    return reported == expected or reported.startswith(expected) or expected.startswith(reported)


def has_auth_marker(text: str, markers: Sequence[str]) -> bool:
    return any(re.search(rf"\b{re.escape(marker)}\b", text) for marker in markers)


def parse_semver(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def supports_codex_mcp_overrides(version_text: str) -> bool:
    version = parse_semver(version_text)
    return bool(version and version >= MIN_CODEX_MCP_OVERRIDE_VERSION)


def run_command(cmd: Sequence[str], cwd: Path | None = None, timeout: int = 8) -> dict:
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": redact(proc.stdout.strip()),
            "stderr": redact(proc.stderr.strip()),
            "timed_out": False,
        }
    except FileNotFoundError:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "command not found", "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": redact((exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""),
            "stderr": "command timed out",
            "timed_out": True,
        }


def git_text(repo: Path, args: Sequence[str], runner: CommandRunner) -> str:
    result = runner(["git", *args], repo, 8)
    return (result.get("stdout") or "").strip() if result.get("ok") else ""


def inspect_origin_main_query(repo: Path, runner: CommandRunner) -> dict:
    result = runner(["git", "ls-remote", "origin", "refs/heads/main"], repo, 15)
    stdout = (result.get("stdout") or "").strip()
    remote_head = ""
    if result.get("ok") and stdout:
        first_field = stdout.split()[0]
        if re.fullmatch(r"[0-9a-fA-F]{40}", first_field):
            remote_head = first_field.lower()
    info = {
        "ok": bool(result.get("ok")) and bool(remote_head),
        "exit_code": result.get("exit_code"),
        "timed_out": bool(result.get("timed_out")),
        "head": remote_head,
        "head_short": remote_head[:7],
    }
    if not info["ok"]:
        combined = "\n".join(
            part for part in (result.get("stdout") or "", result.get("stderr") or "") if part
        ).strip()
        info["error_redacted"] = redacted_remote_url(combined)[:500]
    return info


def inspect_repo(repo: Path, expected_sha: str, runner: CommandRunner) -> tuple[dict, list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    info = {
        "path": str(repo),
        "exists": repo.exists(),
        "is_dir": repo.is_dir(),
        "branch": "",
        "head": "",
        "head_short": "",
        "origin_main": "",
        "dirty": None,
        "expected_sha": expected_sha or "",
        "expected_sha_match": None,
        "remote_origin": "",
        "origin_main_query": {
            "ok": False,
            "exit_code": None,
            "timed_out": False,
            "head": "",
            "head_short": "",
        },
    }

    if not repo.exists() or not repo.is_dir():
        blockers.append(f"repo path is not a directory: {repo}")
        return info, blockers, warnings

    git_root = git_text(repo, ["rev-parse", "--show-toplevel"], runner)
    if not git_root:
        blockers.append(f"repo path is not a git checkout: {repo}")
        return info, blockers, warnings

    info["git_root"] = git_root
    info["branch"] = git_text(repo, ["rev-parse", "--abbrev-ref", "HEAD"], runner)
    info["head"] = git_text(repo, ["rev-parse", "HEAD"], runner)
    info["head_short"] = git_text(repo, ["rev-parse", "--short", "HEAD"], runner)
    info["origin_main"] = git_text(repo, ["rev-parse", "origin/main"], runner)
    info["remote_origin"] = redacted_remote_url(git_text(repo, ["remote", "get-url", "origin"], runner))
    info["origin_main_query"] = inspect_origin_main_query(repo, runner)
    status = git_text(repo, ["status", "--short"], runner)
    info["dirty"] = bool(status)
    if status:
        info["status_short_redacted"] = redact(status)
        blockers.append("repo checkout is dirty; do not run release evidence from uncommitted state")

    if expected_sha:
        info["expected_sha_match"] = build_sha_matches(info["head"], expected_sha)
        if not info["expected_sha_match"]:
            blockers.append(
                f"repo HEAD {info['head_short'] or info['head'] or 'unknown'} does not match expected SHA {expected_sha}"
            )
    else:
        blockers.append("expected SHA is required for support-VM RRI readiness")

    if info["origin_main"] and info["head"] and info["origin_main"] != info["head"]:
        warnings.append(
            f"HEAD {info['head_short'] or info['head']} differs from origin/main {info['origin_main'][:7]}"
        )
    if not info["origin_main_query"].get("ok"):
        blockers.append("repo origin/main is not queryable from this VM; configure approved GitHub credentials before RRI")
    else:
        remote_head = info["origin_main_query"].get("head")
        if remote_head and info["origin_main"] and remote_head != info["origin_main"]:
            warnings.append(
                f"local origin/main {info['origin_main'][:7]} differs from queried origin/main {remote_head[:7]}"
            )

    return info, blockers, warnings


def sysctl_int(name: str) -> int | None:
    try:
        proc = subprocess.run(["sysctl", "-n", name], text=True, capture_output=True, timeout=3, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def memory_total_bytes() -> int | None:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) * 1024
    return sysctl_int("hw.memsize")


def disk_summary(path: Path) -> dict:
    target = path if path.exists() else path.parent
    try:
        usage = shutil.disk_usage(target)
        return {
            "path": str(target),
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "free_gb": round(usage.free / (1024**3), 2),
        }
    except OSError as exc:
        return {"path": str(target), "error": str(exc)}


def inspect_host(repo: Path, artifact_dir: Path) -> dict:
    mem_bytes = memory_total_bytes()
    return {
        "hostname": socket.gethostname(),
        "user": getpass.getuser(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "cpu_count": os.cpu_count(),
        "memory_total_bytes": mem_bytes,
        "memory_total_gb": round(mem_bytes / (1024**3), 2) if mem_bytes else None,
        "repo_disk": disk_summary(repo),
        "artifact_disk": disk_summary(artifact_dir),
    }


def inspect_tool(
    name: str,
    executable: str,
    version_args: Sequence[str],
    repo: Path,
    runner: CommandRunner,
    which: WhichFn,
) -> dict:
    path = which(executable)
    info = {"available": bool(path), "path": path or "", "version": "", "exit_code": None, "timed_out": False}
    if not path:
        return info
    result = runner([path, *version_args], repo, 8)
    info.update(
        {
            "version": " ".join(p for p in (result.get("stdout"), result.get("stderr")) if p).strip()[:300],
            "exit_code": result.get("exit_code"),
            "timed_out": bool(result.get("timed_out")),
        }
    )
    return info


def inspect_tools(
    repo: Path,
    runner: CommandRunner,
    which: WhichFn,
    required_tools: Sequence[str],
) -> tuple[dict, list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    tool_specs = {
        "git": ("git", ["--version"]),
        "python3": ("python3", ["--version"]),
        "uv": ("uv", ["--version"]),
        "node": ("node", ["--version"]),
        "npm": ("npm", ["--version"]),
        "npx": ("npx", ["--version"]),
        "codex": ("codex", ["--version"]),
        "claude": ("claude", ["--version"]),
        "jq": ("jq", ["--version"]),
        "curl": ("curl", ["--version"]),
        "lsof": ("lsof", ["-v"]),
        "timeout": ("timeout", ["--version"]),
        "pkill": ("pkill", ["-V"]),
        "pgrep": ("pgrep", ["-V"]),
        "ps": ("ps", ["--version"]),
    }
    tools = {name: inspect_tool(name, exe, args, repo, runner, which) for name, (exe, args) in tool_specs.items()}
    for name in required_tools:
        if not tools.get(name, {}).get("available"):
            blockers.append(f"required VM tool missing: {name}")

    node_path = tools.get("node", {}).get("path") or which("node")
    playwright_dir = repo / "qa" / "playwright" / "node_modules" / "playwright"
    playwright = {"available": False, "path": "", "exit_code": None, "timed_out": False}
    if playwright_dir.is_dir():
        playwright.update({"available": True, "path": str(playwright_dir), "source": "qa/playwright/node_modules"})
    elif node_path:
        playwright_cwd = repo / "qa" / "playwright" if (repo / "qa" / "playwright").is_dir() else repo
        result = runner([node_path, "-e", "process.stdout.write(require.resolve('playwright'))"], playwright_cwd, 8)
        playwright.update(
            {
                "available": bool(result.get("ok")),
                "path": (result.get("stdout") or "")[:300],
                "exit_code": result.get("exit_code"),
                "timed_out": bool(result.get("timed_out")),
                "source": "node_require_resolve",
            }
        )
    if not playwright["available"]:
        blockers.append("Playwright node module is not resolvable from the repo checkout")
    tools["playwright_node_module"] = playwright

    chromium = {"available": False, "path": "", "exit_code": None, "timed_out": False}
    if playwright["available"] and node_path:
        playwright_cwd = repo / "qa" / "playwright" if (repo / "qa" / "playwright").is_dir() else repo
        result = runner(
            [
                node_path,
                "-e",
                "const { chromium } = require('playwright'); process.stdout.write(chromium.executablePath())",
            ],
            playwright_cwd,
            8,
        )
        chromium_path = (result.get("stdout") or "").strip()
        chromium.update(
            {
                "available": bool(result.get("ok")) and bool(chromium_path) and Path(chromium_path).exists(),
                "path": chromium_path[:300],
                "exit_code": result.get("exit_code"),
                "timed_out": bool(result.get("timed_out")),
            }
        )
    if not chromium["available"]:
        blockers.append("Playwright Chromium executable is not installed; run (cd qa/playwright && npx playwright install chromium)")
    tools["playwright_chromium"] = chromium

    codex_required = "codex" in required_tools
    codex = {"available": bool(tools.get("codex", {}).get("available")), "auth_status": "not_required"}
    codex_path = tools.get("codex", {}).get("path")
    codex_version = tools.get("codex", {}).get("version") or ""
    codex["mcp_override_min_version"] = ".".join(str(part) for part in MIN_CODEX_MCP_OVERRIDE_VERSION)
    codex["mcp_override_supported"] = supports_codex_mcp_overrides(codex_version) if codex_path else False
    if codex_required and codex_path and not codex["mcp_override_supported"]:
        blockers.append(
            "Codex CLI version does not prove support for codex exec -c mcp_servers.* overrides; require >= 0.120.0"
        )
    if codex_path:
        codex["auth_status"] = "not_proven"
        codex["auth_probe_command"] = "codex login status"
        result = runner([codex_path, "login", "status"], repo, 10)
        combined = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}".strip()
        lower = combined.lower()
        codex.update(
            {
                "auth_probe_exit_code": result.get("exit_code"),
                "auth_probe_timed_out": bool(result.get("timed_out")),
            }
        )
        negative_auth = (
            "not authenticated",
            "unauthenticated",
            "not logged in",
            "not signed in",
            "not signed",
            "signed out",
            "inactive",
        )
        positive_auth = ("authenticated", "logged in", "signed in")
        if has_auth_marker(lower, negative_auth):
            codex["auth_status"] = "not_proven"
            if codex_required:
                blockers.append("Codex CLI auth/profile status is not proven")
        elif result.get("ok") and has_auth_marker(lower, positive_auth):
            codex["auth_status"] = "proven"
        elif result.get("ok"):
            codex["auth_status"] = "command_ok_unclassified"
            if codex_required:
                blockers.append("Codex CLI auth/profile status is not proven")
        elif codex_required:
            blockers.append("Codex CLI auth/profile status is not proven")
    tools["codex_auth"] = codex
    return tools, blockers, warnings


def inspect_private_art(art_root: Path, mode: str) -> tuple[dict, list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    images_root = art_root / "content" / "worlds" / "_private" / "baldurs-gate" / "images"
    scope_count = 0
    image_png_count = 0
    if images_root.is_dir():
        try:
            for entry in images_root.iterdir():
                if entry.is_dir():
                    scope_count += 1
                    if (entry / "image.png").is_file():
                        image_png_count += 1
        except OSError as exc:
            warnings.append(f"private art directory could not be fully scanned: {exc}")
    present = images_root.is_dir() and image_png_count > 0
    info = {
        "mode": mode,
        "art_root": str(art_root),
        "private_images_root": str(images_root),
        "private_root_present": present,
        "scope_dir_count": scope_count,
        "image_png_count": image_png_count,
        "contents_listed": False,
    }
    if not present and mode == "required":
        blockers.append(f"private art required but not proven at {images_root}")
    elif not present and mode == "optional":
        warnings.append(f"private art not proven at {images_root}; classify resulting VM evidence as no-art/backend-only")
    return info, blockers, warnings


def inspect_required_repo_files(
    repo: Path,
    personas: list[str],
    provider: str,
    _player_agent: str,
) -> tuple[dict, list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    required = [
        "qa/ui_playtest_app.sh",
        "qa/run_duo.sh",
        "qa/assert_behavioral.py",
        "qa/ui_audit_health.sh",
        "qa/release_readiness.py",
        "qa/play_player_duo.txt",
        "qa/playwright/palette_server.js",
    ]
    if provider == "codex":
        required.append("scripts/play_codex_dm.sh")
    elif provider == "claude":
        required.extend(["scripts/play.sh", "scripts/play_party.sh"])
    required.extend(f"qa/play_player_browser_{persona}.txt" for persona in personas)
    files = {}
    for rel in required:
        path = repo / rel
        present = path.is_file()
        files[rel] = {"present": present}
        if not present:
            blockers.append(f"required release artifact tool/brief missing: {rel}")
    return {"required_files": files}, blockers, warnings


def teardown_commands(repo: Path, port: int, expected_sha: str) -> list[str]:
    run_prefix = f"gate-{(expected_sha or 'SHA')[:7]}"
    repo_path = q(repo)
    return [
        f"pkill -f {q('play_party.sh .* ' + run_prefix)} || true",
        f"pkill -f {q('play.sh .* ' + run_prefix)} || true",
        f"pkill -f {q('play-state/' + run_prefix)} || true",
        f"pkill -f {q('qa/playwright/palette_server.js.*' + run_prefix)} || true",
        (
            f"repo={repo_path}; "
            f"for pid in $(lsof -nP -tiTCP:{port} -sTCP:LISTEN 2>/dev/null); do "
            "cwd=$(lsof -a -p \"$pid\" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1); "
            '[ "$cwd" = "$repo" ] && kill "$pid" || true; '
            "done"
        ),
    ]


def q(value: object) -> str:
    return shlex.quote(str(value))


def build_vm_persona_commands(config: PreflightConfig) -> list[str]:
    run_prefix = f"gate-{(config.expected_sha or 'SHA')[:7]}"
    commands: list[str] = []
    for persona in config.personas:
        commands.append(
            " ".join(
                [
                    f"WORLDOS_ART_REPO_ROOT={q(config.art_root)}",
                    "WOS_APP_PART=B",
                    "WOS_APP_SKIP_BUILD=1",
                    "WOS_APP_NO_GLOBAL_KILL=1",
                    f"WOS_APP_SELECTED_PROVIDER={q(config.provider)}",
                    f"WOS_APP_PLAYER_AGENT={q(config.player_agent)}",
                    f"WOS_APP_PREFERRED_PORT={q(config.port)}",
                    "qa/ui_playtest_app.sh",
                    q(f"{run_prefix}-{persona}"),
                    "baldurs-gate",
                    q(persona),
                    "40",
                    q(config.budget),
                ]
            )
        )
    return commands


def lane_auth_ready(agent: str, tools: dict) -> bool:
    if agent == "codex":
        codex = tools.get("codex_auth", {})
        return codex.get("auth_status") == "proven" and bool(codex.get("mcp_override_supported"))
    if agent == "claude":
        return bool(tools.get("claude", {}).get("available"))
    return False


def readiness_summary(
    config: PreflightConfig,
    *,
    ready: bool,
    repo: dict,
    tools: dict,
    private_art: dict,
    repo_files: dict,
    required_tools: Sequence[str],
) -> dict:
    """Compact path-free readiness object for agent routing."""
    same_sha_ready = (
        bool(config.expected_sha)
        and repo.get("expected_sha_match") is True
        and repo.get("dirty") is False
        and bool(repo.get("origin_main_query", {}).get("ok"))
    )
    required_tools_ready = all(bool(tools.get(tool, {}).get("available")) for tool in required_tools) and bool(
        tools.get("playwright_node_module", {}).get("available")
    ) and bool(tools.get("playwright_chromium", {}).get("available"))
    persona_briefs_ready = all(
        bool(item.get("present")) for item in repo_files.get("required_files", {}).values()
    )
    private_art_ready = config.private_art_mode == "required" and bool(private_art.get("private_root_present"))
    artifact_return_ready = bool(config.artifact_return_target.strip())
    provider_auth_ready = lane_auth_ready(config.provider, tools)
    player_agent_auth_ready = lane_auth_ready(config.player_agent, tools)

    checks = {
        "repo_state": same_sha_ready,
        "required_tools": required_tools_ready,
        "provider_auth": provider_auth_ready,
        "player_agent_auth": player_agent_auth_ready,
        "persona_briefs": persona_briefs_ready,
        "private_art": private_art_ready,
        "artifact_return": artifact_return_ready,
    }
    return {
        "safe_to_run_personas": bool(ready),
        "release_verdict": False,
        "expected_sha": config.expected_sha,
        "repo_head_short": repo.get("head_short") or "",
        "same_sha_ready": same_sha_ready,
        "provider": config.provider,
        "player_agent": config.player_agent,
        "provider_auth_ready": provider_auth_ready,
        "player_agent_auth_ready": player_agent_auth_ready,
        "required_tools_ready": required_tools_ready,
        "persona_briefs_ready": persona_briefs_ready,
        "private_art_ready": private_art_ready,
        "artifact_return_ready": artifact_return_ready,
        "mac_handoff_required": True,
        "blocking_categories": [name for name, passed in checks.items() if not passed],
    }


def build_report(
    config: PreflightConfig,
    *,
    runner: CommandRunner = run_command,
    which: WhichFn = shutil.which,
    env: dict[str, str] | None = None,
) -> dict:
    blockers: list[str] = []
    warnings: list[str] = []
    config.artifact_dir.mkdir(parents=True, exist_ok=True)
    if config.provider not in PERSONA_PROVIDERS:
        blockers.append(f"unsupported support-VM persona provider: {config.provider}")
    if config.player_agent not in PLAYER_AGENTS:
        blockers.append(f"unsupported support-VM player agent: {config.player_agent}")

    required_tools = required_tools_for(config)
    repo, repo_blockers, repo_warnings = inspect_repo(config.repo, config.expected_sha, runner)
    tools, tool_blockers, tool_warnings = inspect_tools(config.repo, runner, which, required_tools)
    art, art_blockers, art_warnings = inspect_private_art(config.art_root, config.private_art_mode)
    repo_files, file_blockers, file_warnings = inspect_required_repo_files(
        config.repo,
        config.personas,
        config.provider,
        config.player_agent,
    )
    blockers.extend(repo_blockers + tool_blockers + art_blockers + file_blockers)
    warnings.extend(repo_warnings + tool_warnings + art_warnings + file_warnings)

    # #466 release-RRI readiness needs private art evidence; optional/no-art modes
    # are allowed for diagnostics but must not produce a green readiness artifact.
    if config.private_art_mode != "required":
        blockers.append("private art mode must be 'required' for #466 release-RRI readiness")

    if config.concurrency > 1:
        warnings.append("concurrency > 1 requested; verify support VM headroom before increasing persona parallelism")

    ready = not blockers
    report = {
        "schema": SCHEMA,
        "generated_at": utc_timestamp(),
        "verdict": "passed" if ready else "blocked",
        "ready_for_rri": ready,
        "release_verdict": False,
        "blockers": blockers,
        "warnings": warnings,
        "host": inspect_host(config.repo, config.artifact_dir),
        "repo": repo,
        "tools": tools,
        "repo_files": repo_files,
        "private_art": art,
        "readiness": readiness_summary(
            config,
            ready=ready,
            repo=repo,
            tools=tools,
            private_art=art,
            repo_files=repo_files,
            required_tools=required_tools,
        ),
        "environment": env_snapshot(env or dict(os.environ)),
        "rri_plan": {
            "expected_personas": config.personas,
            "canonical_personas": CANONICAL_PERSONAS,
            "budget": config.budget,
            "concurrency_cap": config.concurrency,
            "port": config.port,
            "provider": config.provider,
            "player_agent": config.player_agent,
            "required_tools": required_tools,
            "same_sha_required": True,
            "expected_sha": config.expected_sha,
            "support_vm_scope": "backend/persona artifacts only; Mac built-app/native handoff evidence is supplied separately",
            "do_not_run_on_support_vm": "qa/release_gate.sh includes Mac built-app/native proof and is not the support-VM sweep command",
            "vm_persona_sweep_commands": build_vm_persona_commands(config),
            "rollup_requires": [
                "VM persona run dirs with run.json, score.json, network/image evidence, session_surface.final.json, and matching build_sha",
                "VM story/mechanical scorer JSON, behavioral output, UI audit log, palette-live source, and image denominator evidence",
                "Mac qa/app_handoff_gate.py handoff.json from the same SHA",
                "qa/release_readiness.py rollup with --handoff-json and --build-sha",
            ],
            "mac_handoff_required": True,
            "notes": [
                "This preflight is not release evidence.",
                "Pair VM persona artifacts with a Mac built-app handoff JSON from the same SHA.",
                "If any persona score.json or build SHA is missing, RRI must remain partial/harness-contaminated.",
            ],
        },
        "artifact_return": {
            "local_artifact_dir": str(config.artifact_dir),
            "return_target": config.artifact_return_target,
            "lexar_note": "/Volumes/LEXAR/Codex may not exist on the VM; stage remotely and copy back to local Lexar.",
        },
        "teardown": {"commands": teardown_commands(config.repo, config.port, config.expected_sha), "executed": False},
    }
    return report


def markdown_report(report: dict) -> str:
    lines = [
        "# WorldOS Support VM Preflight",
        "",
        f"- Schema: `{report['schema']}`",
        f"- Generated: `{report['generated_at']}`",
        f"- Verdict: `{report['verdict']}`",
        f"- Ready for RRI: `{str(report['ready_for_rri']).lower()}`",
        f"- Release verdict: `{str(report['release_verdict']).lower()}`",
        "",
        "## Repository",
        "",
        f"- Path: `{report['repo'].get('path', '')}`",
        f"- HEAD: `{report['repo'].get('head_short') or report['repo'].get('head') or 'unknown'}`",
        f"- Expected SHA: `{report['repo'].get('expected_sha') or 'not supplied'}`",
        f"- Expected SHA match: `{report['repo'].get('expected_sha_match')}`",
        f"- Dirty: `{report['repo'].get('dirty')}`",
        f"- Origin/main query: `{str(report['repo'].get('origin_main_query', {}).get('ok')).lower()}`",
        f"- Queried origin/main: `{report['repo'].get('origin_main_query', {}).get('head_short') or 'unknown'}`",
        "",
        "## Readiness",
        "",
        f"- Safe to run personas: `{str(report.get('readiness', {}).get('safe_to_run_personas')).lower()}`",
        f"- Blocking categories: `{','.join(report.get('readiness', {}).get('blocking_categories') or []) or 'none'}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers") or []
    lines.extend([f"- {item}" for item in blockers] if blockers else ["- none"])
    lines.extend(["", "## Warnings", ""])
    warnings = report.get("warnings") or []
    lines.extend([f"- {item}" for item in warnings] if warnings else ["- none"])
    lines.extend(
        [
            "",
            "## RRI Plan",
            "",
            f"- Personas: `{','.join(report['rri_plan']['expected_personas'])}`",
            f"- Budget: `{report['rri_plan']['budget']}`",
            f"- Port: `{report['rri_plan']['port']}`",
            f"- Provider: `{report['rri_plan']['provider']}`",
            f"- Player agent: `{report['rri_plan']['player_agent']}`",
            f"- Required tools: `{','.join(report['rri_plan'].get('required_tools', []))}`",
            f"- Support VM scope: `{report['rri_plan']['support_vm_scope']}`",
            f"- Do not run on support VM: `{report['rri_plan']['do_not_run_on_support_vm']}`",
            f"- First persona command: `{(report['rri_plan'].get('vm_persona_sweep_commands') or [''])[0]}`",
            "",
            "## Teardown",
            "",
        ]
    )
    lines.extend([f"- `{cmd}`" for cmd in report["teardown"]["commands"]])
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    default_artifact = Path(tempfile.gettempdir()) / f"worldos-support-vm-preflight-{compact_timestamp()}"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.getcwd(), help="WorldOS checkout to inspect")
    parser.add_argument("--expected-sha", default="", help="SHA that VM evidence must match")
    parser.add_argument("--artifact-dir", default=str(default_artifact), help="Directory for preflight artifacts")
    parser.add_argument(
        "--artifact-return-target",
        default=f"/Volumes/LEXAR/Codex/worldos-support-vm-rri/preflight-{compact_timestamp()}",
        help="Local Lexar target or operator return path to record in the report",
    )
    parser.add_argument("--art-root", default=os.environ.get("WORLDOS_ART_REPO_ROOT") or os.environ.get("CLAWDND_ART_REPO_ROOT") or os.getcwd())
    parser.add_argument("--private-art-mode", choices=("required", "optional", "none"), default="required")
    parser.add_argument("--personas", default=",".join(CANONICAL_PERSONAS))
    parser.add_argument("--budget", default="12.00")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--port", type=int, default=8785)
    parser.add_argument("--provider", choices=PERSONA_PROVIDERS, default="codex")
    parser.add_argument("--player-agent", choices=PLAYER_AGENTS, default="codex")
    parser.add_argument("--no-fail", action="store_true", help="Write the report and exit 0 even if blockers exist")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    personas = [p.strip() for p in args.personas.split(",") if p.strip()]
    config = PreflightConfig(
        repo=Path(args.repo).expanduser().resolve(),
        expected_sha=args.expected_sha.strip(),
        artifact_dir=Path(args.artifact_dir).expanduser().resolve(),
        artifact_return_target=args.artifact_return_target,
        art_root=Path(args.art_root).expanduser().resolve(),
        private_art_mode=args.private_art_mode,
        personas=personas,
        budget=args.budget,
        concurrency=args.concurrency,
        port=args.port,
        provider=args.provider,
        player_agent=args.player_agent,
    )
    report = build_report(config)
    json_path = config.artifact_dir / "support_vm_preflight.json"
    md_path = config.artifact_dir / "support_vm_preflight.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")

    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    if report["blockers"]:
        print("blockers:")
        for blocker in report["blockers"]:
            print(f"- {blocker}")
    return 0 if args.no_fail or report["ready_for_rri"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
