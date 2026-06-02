#!/usr/bin/env python3
"""Hybrid handoff gate for WorldOS GUI implementation velocity.

This orchestrates fast evidence gates for the app handoff lane. It is deliberately
not the release verdict: full five-persona RRI remains owned by
qa/release_readiness.py.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qa import app_smoke_scripted as smoke  # noqa: E402
from qa.app_failure_buckets import APP_FAILURE_BUCKETS  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path("/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability")
DEFAULT_ART_ROOT = Path("/Users/lume/ClawDnD-val")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def append_ndjson(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


def repo_sha(short: bool = True) -> str:
    args = ["git", "-C", str(ROOT), "rev-parse"]
    if short:
        args.extend(["--short", "HEAD"])
    else:
        args.append("HEAD")
    proc = subprocess.run(args, text=True, capture_output=True, check=False, timeout=5)
    return (proc.stdout or "").strip() or "unknown"


def repo_dirty() -> bool:
    proc = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], text=True, capture_output=True, check=False, timeout=5)
    return bool((proc.stdout or "").strip())


def command_text(args: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) for part in args)


def run_logged(cmd: list[str], *, cwd: Path, env: dict[str, str], log_path: Path, timeout: float | None = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {command_text(cmd)}\n")
        log.flush()
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            log.write(f"\n[timeout after {exc.timeout}s]\n")
            log.write("[exit 124]\n")
            log.flush()
            return 124
        log.write(f"\n[exit {proc.returncode}]\n")
        return int(proc.returncode)


def failure(bucket: str, detail: str) -> tuple[str, str]:
    if bucket not in APP_FAILURE_BUCKETS:
        bucket = "no_provider"
    return bucket, detail


def build_matches(reported: str, expected: str) -> bool:
    reported = (reported or "").strip()
    expected = (expected or "").strip()
    if not reported or not expected or reported == "unknown":
        return False
    return reported == expected or expected.startswith(reported) or reported.startswith(expected)


def session_surface_has_narration(surface: dict[str, Any] | None) -> bool:
    if not isinstance(surface, dict):
        return False
    events = surface.get("recentEvents")
    if not isinstance(events, list):
        return False
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "").strip().lower()
        text = str(event.get("text") or "").strip()
        if kind in {"narration", "dialogue"} and text:
            return True
    return False


def validate_app_status(
    status: dict[str, Any],
    *,
    expected_port: int | None,
    expected_sha: str,
    require_ready_for_play: bool = True,
    session_surface: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if not status:
        return failure("no_launcher", "app-status JSON is missing")
    if status.get("schema") != "worldos.app-status.v1":
        return failure("no_launcher", "app-status schema is missing or wrong")
    viewer = status.get("viewer") if isinstance(status.get("viewer"), dict) else {}
    build = status.get("build") if isinstance(status.get("build"), dict) else {}
    art = status.get("art") if isinstance(status.get("art"), dict) else {}
    live = status.get("live") if isinstance(status.get("live"), dict) else {}
    readiness = status.get("readiness") if isinstance(status.get("readiness"), dict) else {}
    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    actor = live.get("actor") if isinstance(live.get("actor"), dict) else {}

    if expected_port is not None and int(viewer.get("port") or 0) != int(expected_port):
        return failure("no_launcher", f"app-status answered for port {viewer.get('port')} instead of expected same port {expected_port}")
    if expected_sha and not build_matches(str(build.get("sha") or ""), expected_sha):
        return failure("no_app", f"app-status build SHA {build.get('sha') or 'missing'} does not match expected {expected_sha}")
    for source in (readiness, health):
        bucket = source.get("failure_bucket")
        if isinstance(bucket, str) and bucket and bucket != "none":
            return failure(bucket, str(source.get("failure_detail") or "app-status readiness failed"))
    if art.get("private_root_present") is not True:
        return failure("no_art", "private art root is missing from app-status")
    if require_ready_for_play and readiness.get("ready_for_play") is not True:
        return failure("no_provider", "app-status did not report ready_for_play:true")
    if readiness.get("ready_for_smoke") is not True:
        return failure("no_provider", "app-status did not report ready_for_smoke:true")
    if live.get("can_act") is not True:
        return failure("no_provider", "app-status did not report can_act:true")
    if not (actor.get("id") or actor.get("name")):
        return failure("no_actor", "app-status did not report an active player actor")
    if int(live.get("enabled_action_count") or 0) <= 0:
        return failure("no_actions", "app-status reported no enabled player actions")
    if int(viewer.get("chat_lines") or 0) <= 0 and not session_surface_has_narration(session_surface):
        return failure("no_narration", "app-status/session-surface reported no chat/narration")
    return "", ""


def provider_trace_summary(play_state: Path, provider: str) -> dict[str, Any]:
    provider_dir = play_state / f"{provider}-provider"
    summary_path = provider_dir / "summary.json"
    if summary_path.exists():
        payload = read_json(summary_path)
        if payload:
            payload.setdefault("provider", provider)
            payload.setdefault("failed_or_error_count", 0)
            payload.setdefault("provider_infra_warning_count", 0)
            payload.setdefault("provider_infra_samples", [])
            return payload

    failed = 0
    infra_warnings = 0
    parsed = 0
    samples: list[str] = []
    infra_samples: list[str] = []
    patterns = ("*stdout*.jsonl", "*stderr*.log", "*.ndjson", "*.jsonl", "*.log", "*.txt")
    seen: set[Path] = set()
    for pattern in patterns:
        for path in provider_dir.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.strip():
                    continue
                parsed += 1
                parsed_payload: dict[str, Any] | None = None
                if path.suffix in {".jsonl", ".ndjson"}:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        payload = None
                    if isinstance(payload, dict):
                        parsed_payload = payload
                if parsed_payload is not None:
                    item = parsed_payload.get("item") if isinstance(parsed_payload.get("item"), dict) else {}
                    status = str(item.get("status") or parsed_payload.get("status") or "").lower()
                    error = item.get("error") if "error" in item else parsed_payload.get("error")
                    event_type = str(parsed_payload.get("type") or item.get("type") or "").lower()
                    is_bad = bool(error) or status in {"failed", "error", "cancelled", "canceled"} or event_type in {"turn.failed", "turn.error"}
                    if is_bad:
                        failed += 1
                        if len(samples) < 5:
                            samples.append(line[:300])
                    continue
                lower = line.lower()
                is_infra_warning = "codex_core::arc_monitor" in lower and "safety monitor returned non-success status" in lower
                if is_infra_warning:
                    infra_warnings += 1
                    if len(infra_samples) < 5:
                        infra_samples.append(line[:300])
                    continue
                is_bad = any(marker in lower for marker in (
                    '"is_error":true',
                    "extra_forbidden",
                    "validation error",
                    "cancelled",
                    "canceled",
                    "safety",
                    '"status":"failed"',
                    "traceback",
                ))
                if is_bad:
                    failed += 1
                    if len(samples) < 5:
                        samples.append(line[:300])
    return {
        "schema": "worldos.provider-trace-summary.v1",
        "provider": provider,
        "trace_dir": str(provider_dir),
        "trace_exists": provider_dir.is_dir(),
        "line_count": parsed,
        "failed_or_error_count": failed,
        "provider_infra_warning_count": infra_warnings,
        "samples": samples,
        "provider_infra_samples": infra_samples,
    }


def summarize_hook_probe(path: Path) -> tuple[bool, str, dict[str, Any]]:
    payload = read_json(path)
    if not payload:
        return False, "hook probe did not write JSON", {}
    missing = payload.get("missing_required") if isinstance(payload.get("missing_required"), list) else []
    errors = int(payload.get("console_errors") or 0)
    if missing:
        return False, "missing hooks: " + ", ".join(str(item) for item in missing), payload
    if errors:
        return False, f"hook probe saw console_errors={errors}", payload
    return bool(payload.get("ok")), "" if payload.get("ok") else "hook probe reported ok:false", payload


def evidence_gap_count(payload: dict[str, Any]) -> int:
    gaps = payload.get("evidence_gaps")
    return len(gaps) if isinstance(gaps, list) else 0


def evidence_manifest_blockers(payload: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    gap_count = evidence_gap_count(payload)
    if gap_count:
        blockers.append(f"evidence gaps: {gap_count}")
    handoff_gate = payload.get("handoff_gate") if isinstance(payload.get("handoff_gate"), dict) else {}
    if handoff_gate.get("ok") is False:
        reasons = handoff_gate.get("blocking_reasons")
        if isinstance(reasons, list) and reasons:
            blockers.extend(str(reason) for reason in reasons)
        else:
            blockers.append("handoff evidence marked ok:false")
    return blockers


@dataclass
class GateResult:
    name: str
    provider: str
    surface: str
    required: bool = True
    status: str = "failed"
    failure_bucket: str = "no_provider"
    failure_detail: str = ""
    evidence_dir: str = ""
    evidence_manifest: str = ""
    run_id: str = ""
    app_status_url: str = ""
    build_sha: str = ""
    port: int | None = None
    command: list[str] = field(default_factory=list)
    evidence_gaps: list[Any] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def pass_(self) -> None:
        self.status = "passed"
        self.failure_bucket = ""
        self.failure_detail = ""

    def fail(self, bucket: str, detail: str) -> None:
        self.status = "failed"
        self.failure_bucket, self.failure_detail = failure(bucket, detail)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "surface": self.surface,
            "required": self.required,
            "status": self.status,
            "failure_bucket": self.failure_bucket,
            "failure_detail": self.failure_detail,
            "evidence_dir": self.evidence_dir,
            "evidence_manifest": self.evidence_manifest,
            "run_id": self.run_id,
            "app_status_url": self.app_status_url,
            "build_sha": self.build_sha,
            "port": self.port,
            "command": self.command,
            "evidence_gaps": self.evidence_gaps,
            "details": self.details,
        }


def finalize_handoff(*, run_id: str, out: Path, gates: list[GateResult], started_at: str, expected_sha: str) -> dict[str, Any]:
    required = [gate for gate in gates if gate.required]
    passed = [gate for gate in required if gate.status == "passed"]
    failed = [gate for gate in required if gate.status != "passed"]
    same_sha = all(build_matches(gate.build_sha, expected_sha) for gate in required if gate.build_sha)
    dirty = repo_dirty()
    status = "passed" if len(passed) == len(required) and same_sha and not dirty else "failed"
    blockers = []
    for gate in failed:
        blockers.append({"gate": gate.name, "bucket": gate.failure_bucket, "detail": gate.failure_detail})
    if not same_sha:
        blockers.append({"gate": "same_sha", "bucket": "no_app", "detail": "mandatory gates did not prove the same build SHA"})
    if dirty:
        blockers.append({"gate": "repo_dirty", "bucket": "no_app", "detail": "handoff score cannot be 100 from a dirty checkout"})
    return {
        "schema": "worldos.app-handoff.v1",
        "run_id": run_id,
        "status": status,
        "handoff_score": 100 if status == "passed" else 0,
        "release_verdict": False,
        "release_verdict_note": "Full non-partial five-persona RRI remains the release verdict.",
        "repo": str(ROOT),
        "branch": subprocess.run(["git", "-C", str(ROOT), "branch", "--show-current"], text=True, capture_output=True, check=False).stdout.strip(),
        "commit_sha": expected_sha,
        "dirty": dirty,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evidence_root": str(out),
        "gates": [gate.as_dict() for gate in gates],
        "blockers": blockers,
        "next_action": "handoff to main GUI implementation agent" if status == "passed" else "fix failing handoff gate before long GUI/RRI runs",
    }


def copy_native_run(ui_run: Path, gate_dir: Path) -> None:
    if not ui_run.exists():
        return
    for rel in ("run.json", "backend.log", "score.json", "summary.md", "session_surface.final.json"):
        src = ui_run / rel
        if src.exists() and src.is_file():
            dst = gate_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    native = ui_run / "native"
    if native.exists():
        dst = gate_dir / "native"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(native, dst)


def cleanup_run(run_id: str, port: int | None) -> None:
    if run_id:
        patterns = [
            f"play-state/{run_id}/",
            f"play_party.sh .* {run_id}",
            f"play.sh .* {run_id}",
            f" {run_id} ",
        ]
        for pattern in patterns:
            subprocess.run(["pkill", "-f", pattern], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if port:
        subprocess.run(["pkill", "-f", f"server.py .* {port}$"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def export_evidence(
    *,
    gate_dir: Path,
    run_dir: Path,
    app_status_url: str,
    transition_file: Path | None,
    command: list[str],
    gate_kind: str,
    provider: str,
    started_at: str = "",
    verdict: str = "",
) -> tuple[str, dict[str, Any]]:
    out = gate_dir / "app-evidence"
    cmd = [
        sys.executable,
        str(ROOT / "qa" / "export_app_evidence.py"),
        "--run-dir",
        str(run_dir),
        "--out",
        str(out),
        "--command-json",
        json.dumps(command),
        "--gate-kind",
        gate_kind,
        "--provider",
        provider,
        "--commit-sha",
        repo_sha(short=False),
    ]
    if started_at:
        cmd.extend(["--started-at", started_at])
    if verdict:
        cmd.extend(["--verdict", verdict])
    if app_status_url:
        cmd.extend(["--app-status-url", app_status_url])
    if transition_file and transition_file.exists():
        cmd.extend(["--transition-file", str(transition_file)])
    manifest_path = out / "manifest.json"

    def persist_failure(reason: str) -> tuple[str, dict[str, Any]]:
        payload = {
            "schema": "worldos.app-evidence.v1",
            "evidence_gaps": [{"source": "export_app_evidence", "kind": "manifest", "path": str(manifest_path), "reason": reason}],
            "failure": {"failure_bucket": "no_provider", "failure_detail": reason},
            "handoff_gate": {"ok": False, "blocking_reasons": [reason]},
        }
        json_dump(manifest_path, payload)
        return str(manifest_path), payload

    try:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False, timeout=60)
    except subprocess.TimeoutExpired as exc:
        return persist_failure(f"export_app_evidence timed out after {exc.timeout}s")
    if proc.returncode != 0:
        return persist_failure(f"export_app_evidence exited {proc.returncode}: {(proc.stderr or proc.stdout)[-1000:]}")
    if not manifest_path.exists():
        return persist_failure("export_app_evidence did not write manifest.json")
    manifest = read_json(manifest_path)
    if not manifest:
        return persist_failure("export_app_evidence wrote invalid or empty manifest.json")
    return str(manifest_path), manifest


def run_hook_probe(base_url: str, gate_dir: Path) -> tuple[bool, str, dict[str, Any]]:
    out = gate_dir / "hook-probe.json"
    cmd = ["node", str(ROOT / "qa" / "app_handoff_hooks.js"), base_url]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False, timeout=45)
    except subprocess.TimeoutExpired as exc:
        payload = {
            "schema": "worldos.app-handoff-hooks.v1",
            "ok": False,
            "exit_code": "timeout",
            "stderr": str(exc),
            "stdout": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
        }
        json_dump(out, payload)
        return False, f"hook probe timed out: {exc}", payload
    if proc.returncode != 0:
        payload = {
            "schema": "worldos.app-handoff-hooks.v1",
            "ok": False,
            "exit_code": proc.returncode,
            "stderr": proc.stderr[-2000:],
            "stdout": proc.stdout[-2000:],
        }
        json_dump(out, payload)
        return False, payload["stderr"] or f"hook probe exited {proc.returncode}", payload
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
    json_dump(out, payload)
    return summarize_hook_probe(out)


def drive_moves(
    *,
    base_url: str,
    gate_dir: Path,
    run_id: str,
    provider: str,
    beats: int,
    timeout: float,
    expected_sha: str,
    expected_port: int,
) -> tuple[bool, str, str, dict[str, Any]]:
    screenshots: list[str] = []
    gaps: list[dict[str, str]] = []
    (gate_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    (gate_dir / "a11y").mkdir(parents=True, exist_ok=True)
    for rel in ("console.ndjson", "network.ndjson", "actions.ndjson", "moves.ndjson"):
        (gate_dir / rel).write_text("", encoding="utf-8")
    status = smoke.wait_for_status(base_url, gate_dir, timeout=timeout)
    json_dump(gate_dir / "app-status.initial.json", status)
    smoke.write_text_snapshot(gate_dir / "a11y" / "initial.html", smoke.html_text(base_url))
    smoke.capture_openworlds_screenshot(base_url=base_url, out=gate_dir, port=expected_port, label="initial", gaps=gaps, screenshots=screenshots)
    try:
        surface, _ = smoke.fetch_json(smoke.surface_url(base_url, status))
        json_dump(gate_dir / "session-surface.initial.json", surface)
    except Exception as exc:  # noqa: BLE001
        return False, "no_provider", f"initial session-surface fetch failed: {exc}", {"screenshots": screenshots, "evidence_gaps": gaps}
    bucket, detail = validate_app_status(status, expected_port=expected_port, expected_sha=expected_sha, session_surface=surface)
    if bucket:
        return False, bucket, detail, {"screenshots": screenshots, "evidence_gaps": gaps}
    hook_ok, hook_detail, hook_payload = run_hook_probe(base_url, gate_dir)
    if not hook_ok:
        return False, "no_actions", hook_detail, {"screenshots": screenshots, "evidence_gaps": gaps, "hook_probe": hook_payload}

    last_chat_lines = int(((status.get("viewer") or {}).get("chat_lines") or 0) if isinstance(status.get("viewer"), dict) else 0)
    move_url = urllib.parse.urljoin(base_url, "/move")
    for beat in range(1, beats + 1):
        move = {
            "kind": "do",
            "text": f"handoff {provider} gate beat {beat}: check the table wiring and continue.",
        }
        append_ndjson(gate_dir / "moves.ndjson", {"at": time.time(), "beat": beat, "request": move})
        append_ndjson(gate_dir / "actions.ndjson", {"at": time.time(), "beat": beat, "action": "post_move", "url": move_url})
        try:
            response, http_status = smoke.post_json(move_url, move, timeout=5)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            append_ndjson(gate_dir / "network.ndjson", {"at": time.time(), "method": "POST", "url": move_url, "error": str(exc)})
            return False, "move_rejected", str(exc), {"screenshots": screenshots, "evidence_gaps": gaps}
        append_ndjson(gate_dir / "network.ndjson", {"at": time.time(), "method": "POST", "url": move_url, "status": http_status})
        if not response.get("ok"):
            return False, "move_rejected", str(response.get("reason") or response), {"screenshots": screenshots, "evidence_gaps": gaps}

        deadline = time.time() + timeout
        advanced = False
        last_status_error = ""
        while time.time() < deadline:
            try:
                status = smoke.wait_for_status(base_url, gate_dir, timeout=3)
            except Exception as exc:  # noqa: BLE001 - keep polling through transient busy/status timeouts.
                last_status_error = str(exc)
                append_ndjson(
                    gate_dir / "network.ndjson",
                    {"at": time.time(), "method": "GET", "url": urllib.parse.urljoin(base_url, "/app-status"), "error": last_status_error},
                )
                time.sleep(1 if provider != "scripted" else 0.5)
                continue
            chat_lines = int(((status.get("viewer") or {}).get("chat_lines") or 0) if isinstance(status.get("viewer"), dict) else 0)
            if chat_lines > last_chat_lines:
                if provider != "scripted":
                    viewer = status.get("viewer") if isinstance(status.get("viewer"), dict) else {}
                    readiness = status.get("readiness") if isinstance(status.get("readiness"), dict) else {}
                    last_role = str(viewer.get("last_chat_role") or "").strip().lower()
                    ready_for_play = readiness.get("ready_for_play") is True
                    # Non-scripted providers echo the player's move before the DM reply.
                    # Count only the post-move DM tail plus a playable app status as narration advance.
                    if chat_lines >= last_chat_lines + 2 and last_role == "dm" and ready_for_play:
                        advanced = True
                        last_chat_lines = chat_lines
                        break
                else:
                    summary = smoke.provider_summary(ROOT / "play-state" / run_id)
                    if int(summary.get("resolved_move_count") or summary.get("move_resolved_count") or 0) >= beat:
                        advanced = True
                        last_chat_lines = chat_lines
                        break
            time.sleep(1 if provider != "scripted" else 0.5)
        json_dump(gate_dir / f"app-status.beat-{beat}.json", status)
        try:
            surface, _ = smoke.fetch_json(smoke.surface_url(base_url, status))
            json_dump(gate_dir / f"session-surface.beat-{beat}.json", surface)
        except Exception as exc:  # noqa: BLE001
            return False, "no_provider", f"session-surface fetch failed after beat {beat}: {exc}", {"screenshots": screenshots, "evidence_gaps": gaps}
        smoke.write_text_snapshot(gate_dir / "a11y" / f"beat-{beat}.html", smoke.html_text(base_url))
        smoke.capture_openworlds_screenshot(base_url=base_url, out=gate_dir, port=expected_port, label=f"beat-{beat:03d}", gaps=gaps, screenshots=screenshots)
        if not advanced:
            suffix = f"; last app-status error: {last_status_error}" if last_status_error else ""
            return False, "no_narration", f"narration did not advance after {provider} beat {beat}{suffix}", {"screenshots": screenshots, "evidence_gaps": gaps}

    final_status = smoke.wait_for_status(base_url, gate_dir, timeout=5)
    json_dump(gate_dir / "app-status.final.json", final_status)
    try:
        final_surface, _ = smoke.fetch_json(smoke.surface_url(base_url, final_status))
        json_dump(gate_dir / "session-surface.final.json", final_surface)
    except Exception as exc:  # noqa: BLE001
        return False, "no_provider", f"final session-surface fetch failed: {exc}", {"screenshots": screenshots, "evidence_gaps": gaps}
    smoke.write_text_snapshot(gate_dir / "a11y" / "final.html", smoke.html_text(base_url))
    smoke.capture_openworlds_screenshot(base_url=base_url, out=gate_dir, port=expected_port, label="final", gaps=gaps, screenshots=screenshots)
    smoke.copy_play_state(run_id, gate_dir)
    trace = provider_trace_summary(ROOT / "play-state" / run_id, provider)
    json_dump(gate_dir / "provider-trace-summary.json", trace)
    if provider == "codex" and (not trace.get("trace_exists") or int(trace.get("failed_or_error_count") or 0) > 0):
        return False, "no_provider", "Codex provider trace missing or reported failed/error/cancellation events", {"screenshots": screenshots, "evidence_gaps": gaps, "provider_trace": trace}
    if gaps:
        return False, "no_provider", "required evidence capture has gaps", {"screenshots": screenshots, "evidence_gaps": gaps, "provider_trace": trace}
    return True, "", "", {"screenshots": screenshots, "evidence_gaps": gaps, "provider_trace": trace}


def run_web_scripted(args: argparse.Namespace, out: Path, expected_sha: str) -> GateResult:
    gate_dir = out / "web-scripted"
    gate = GateResult(name="web_scripted_smoke", provider="scripted", surface="web", evidence_dir=str(gate_dir), run_id=f"{args.run_id}-web-scripted", build_sha=expected_sha)
    cmd = [
        sys.executable,
        str(ROOT / "qa" / "app_smoke_scripted.py"),
        "--beats",
        str(args.web_beats),
        "--port",
        str(args.web_port),
        "--run-id",
        gate.run_id,
        "--out",
        str(gate_dir),
        "--timeout",
        str(args.timeout),
    ]
    if args.art_root:
        cmd.extend(["--art-root", args.art_root])
    gate.command = cmd
    rc = run_logged(cmd, cwd=ROOT, env=os.environ.copy(), log_path=gate_dir / "handoff-command.log")
    smoke_json = read_json(gate_dir / "smoke.json")
    final_status = read_json(gate_dir / "app-status.final.json")
    gate.port = int(args.web_port)
    gate.app_status_url = f"http://127.0.0.1:{args.web_port}/app-status"
    gate.evidence_gaps = smoke_json.get("evidence_gaps") if isinstance(smoke_json.get("evidence_gaps"), list) else []
    gate.details["smoke"] = smoke_json
    if rc != 0 or smoke_json.get("status") != "passed":
        gate.fail(str(smoke_json.get("failure_bucket") or "no_provider"), str(smoke_json.get("failure_detail") or f"web scripted smoke exited {rc}"))
    else:
        bucket, detail = validate_app_status(final_status, expected_port=int(args.web_port), expected_sha=expected_sha)
        if bucket:
            gate.fail(bucket, detail)
        else:
            gate.pass_()
    manifest_path, manifest = export_evidence(
        gate_dir=gate_dir,
        run_dir=gate_dir,
        app_status_url="",
        transition_file=None,
        command=cmd,
        gate_kind=gate.name,
        provider=gate.provider,
        verdict=gate.status,
    )
    gate.evidence_manifest = manifest_path
    blockers = evidence_manifest_blockers(manifest) if gate.status == "passed" else []
    if blockers:
        gate.fail("no_provider", "web scripted evidence manifest is not handoff-ready: " + "; ".join(blockers))
        gate.evidence_gaps = manifest.get("evidence_gaps", [])
    elif gate.status != "passed":
        gate.evidence_gaps = manifest.get("evidence_gaps", []) if isinstance(manifest.get("evidence_gaps"), list) else gate.evidence_gaps
    return gate


def run_native_provider_gate(
    args: argparse.Namespace,
    out: Path,
    *,
    provider: str,
    beats: int,
    budget: str,
    expected_sha: str,
) -> GateResult:
    name = "built_app_scripted_smoke" if provider == "scripted" else "built_app_codex_playtest"
    gate_dir = out / name
    native_run = f"{args.run_id}-{provider}-native"
    gate = GateResult(name=name, provider=provider, surface="dist/WorldOS.app", evidence_dir=str(gate_dir), run_id=native_run, build_sha=expected_sha)
    env = os.environ.copy()
    env.update({
        "WOS_APP_PART": "A",
        "WOS_APP_KEEP_MINTED_BACKEND": "1",
        "WOS_APP_SELECTED_PROVIDER": provider,
    })
    if args.art_root:
        env["WORLDOS_ART_REPO_ROOT"] = args.art_root
        env["CLAWDND_ART_REPO_ROOT"] = args.art_root
    if provider == "scripted":
        env["WORLDOS_ENABLE_SCRIPTED_PROVIDER"] = "1"
    cmd = ["bash", str(ROOT / "qa" / "ui_playtest_app.sh"), native_run, args.world, "newbie", "1", budget]
    gate.command = cmd
    rc = run_logged(cmd, cwd=ROOT, env=env, log_path=gate_dir / "ui_playtest_app.log")
    ui_run = ROOT / "qa" / "ui_playtest_runs" / native_run
    copy_native_run(ui_run, gate_dir)
    run_json = read_json(ui_run / "run.json")
    transition = read_json(ui_run / "native" / "transition.json")
    part_a = run_json.get("part_a") if isinstance(run_json.get("part_a"), dict) else {}
    port = part_a.get("minted_port")
    minted_run = str(part_a.get("minted_run_dir") or "")
    gate.details["ui_run_dir"] = str(ui_run)
    gate.details["transition"] = transition
    gate.details["run_json"] = run_json
    if isinstance(port, int):
        gate.port = port
    elif isinstance(port, str) and port.isdigit():
        gate.port = int(port)
    gate.run_id = minted_run or native_run
    if rc != 0 or part_a.get("result") != "PASS":
        gate.fail(str(part_a.get("failure_bucket") or transition.get("failure_bucket") or "no_launcher"), str(part_a.get("failure_detail") or transition.get("failure_detail") or f"native provider launch exited {rc}"))
        export_path, manifest = export_evidence(
            gate_dir=gate_dir,
            run_dir=gate_dir,
            app_status_url="",
            transition_file=gate_dir / "native" / "transition.json",
            command=cmd,
            gate_kind=gate.name,
            provider=gate.provider,
            verdict=gate.status,
        )
        gate.evidence_manifest = export_path
        gate.evidence_gaps = manifest.get("evidence_gaps", [])
        return gate
    if part_a.get("kept_backend_alive") is not True or part_a.get("first_turn_ready") is not True:
        gate.fail("no_provider", "native Part A did not keep a first-turn-ready backend alive")
        export_path, manifest = export_evidence(
            gate_dir=gate_dir,
            run_dir=gate_dir,
            app_status_url="",
            transition_file=gate_dir / "native" / "transition.json",
            command=cmd,
            gate_kind=gate.name,
            provider=gate.provider,
            verdict=gate.status,
        )
        gate.evidence_manifest = export_path
        gate.evidence_gaps = manifest.get("evidence_gaps", [])
        return gate
    if not gate.port or not minted_run:
        gate.fail("no_launcher", "native Part A did not report minted port and run id")
        export_path, manifest = export_evidence(
            gate_dir=gate_dir,
            run_dir=gate_dir,
            app_status_url="",
            transition_file=gate_dir / "native" / "transition.json",
            command=cmd,
            gate_kind=gate.name,
            provider=gate.provider,
            verdict=gate.status,
        )
        gate.evidence_manifest = export_path
        gate.evidence_gaps = manifest.get("evidence_gaps", [])
        return gate

    base_url = f"http://127.0.0.1:{gate.port}"
    gate.app_status_url = f"{base_url}/app-status"
    try:
        ok, bucket, detail, details = drive_moves(
            base_url=base_url,
            gate_dir=gate_dir,
            run_id=minted_run,
            provider=provider,
            beats=beats,
            timeout=args.timeout if provider == "scripted" else args.codex_timeout,
            expected_sha=expected_sha,
            expected_port=gate.port,
        )
        gate.details.update(details)
        if not ok:
            gate.fail(bucket, detail)
        else:
            gate.pass_()
    except Exception as exc:  # noqa: BLE001 - bucketed in handoff.json.
        gate.fail("no_provider", f"{provider} gate crashed: {exc}")
    finally:
        export_path, manifest = export_evidence(
            gate_dir=gate_dir,
            run_dir=gate_dir,
            app_status_url=gate.app_status_url,
            transition_file=gate_dir / "native" / "transition.json",
            command=cmd,
            gate_kind=gate.name,
            provider=gate.provider,
            verdict=gate.status,
        )
        gate.evidence_manifest = export_path
        gate.evidence_gaps = manifest.get("evidence_gaps", []) if isinstance(manifest.get("evidence_gaps"), list) else []
        cleanup_run(minted_run, gate.port)
    manifest_blockers = evidence_manifest_blockers(read_json(Path(gate.evidence_manifest))) if gate.status == "passed" else []
    if manifest_blockers:
        gate.fail("no_provider", "native evidence manifest is not handoff-ready: " + "; ".join(manifest_blockers))
    return gate


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the WorldOS 100/100 hybrid handoff gate.")
    parser.add_argument("--run-id", default=f"handoff-{utc_stamp()}-{repo_sha(short=True)}")
    parser.add_argument("--out", default="")
    parser.add_argument("--world", default="baldurs-gate")
    parser.add_argument("--web-port", type=int, default=8899)
    parser.add_argument("--web-beats", type=int, default=5)
    parser.add_argument("--built-beats", type=int, default=5)
    parser.add_argument("--codex-moves", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--codex-timeout", type=float, default=180.0)
    parser.add_argument("--codex-budget", default="3.00")
    parser.add_argument("--scripted-budget", default="1.00")
    parser.add_argument("--art-root", default=os.environ.get("WORLDOS_ART_REPO_ROOT") or os.environ.get("CLAWDND_ART_REPO_ROOT") or (str(DEFAULT_ART_ROOT) if DEFAULT_ART_ROOT.exists() else ""))
    parser.add_argument("--skip-web", action="store_true")
    parser.add_argument("--skip-built-scripted", action="store_true")
    parser.add_argument("--skip-codex", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    out = (Path(args.out).expanduser() if args.out else DEFAULT_OUTPUT_ROOT / args.run_id).resolve()
    out.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    expected_sha = repo_sha(short=True)
    gates: list[GateResult] = []
    try:
        if args.skip_web:
            g = GateResult(name="web_scripted_smoke", provider="scripted", surface="web")
            g.fail("no_provider", "web deterministic smoke was skipped")
            gates.append(g)
        else:
            gates.append(run_web_scripted(args, out, expected_sha))
        if args.skip_built_scripted:
            g = GateResult(name="built_app_scripted_smoke", provider="scripted", surface="dist/WorldOS.app")
            g.fail("no_provider", "built-app deterministic smoke was skipped")
            gates.append(g)
        else:
            gates.append(run_native_provider_gate(args, out, provider="scripted", beats=int(args.built_beats), budget=args.scripted_budget, expected_sha=expected_sha))
        if args.skip_codex:
            g = GateResult(name="built_app_codex_playtest", provider="codex", surface="dist/WorldOS.app")
            g.fail("no_provider", "short Codex provider playtest was skipped")
            gates.append(g)
        else:
            gates.append(run_native_provider_gate(args, out, provider="codex", beats=int(args.codex_moves), budget=args.codex_budget, expected_sha=expected_sha))
    finally:
        # Keep a tidy desktop even if one gate crashes.
        subprocess.run(["pkill", "-x", "WorldOSApp"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    handoff = finalize_handoff(run_id=args.run_id, out=out, gates=gates, started_at=started_at, expected_sha=expected_sha)
    json_dump(out / "handoff.json", handoff)
    print(str(out / "handoff.json"))
    return 0 if handoff["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
