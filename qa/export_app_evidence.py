#!/usr/bin/env python3
"""Export a read-only WorldOS app evidence bundle.

The exporter reads the app status endpoint, fetches the projected session surface when
available, and copies small local evidence files named by app-status. It never calls
write endpoints such as /move and never mutates campaign state.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_ROOT = Path("/Volumes/LEXAR/Codex")
ROOT = Path(__file__).resolve().parents[1]
MAX_HTTP_BYTES = 8 * 1024 * 1024
MAX_LOCAL_FILE_BYTES = 64 * 1024 * 1024
RUN_DIR_PATTERNS = (
    "run.json",
    "smoke.json",
    "provider_playtest.json",
    "RRI.json",
    "score.json",
    "summary.md",
    "run.log",
    "backend.log",
    "viewer.log",
    "console.ndjson",
    "network.ndjson",
    "actions.ndjson",
    "bugs.ndjson",
    "moves.ndjson",
    "app-status*.json",
    "session-surface*.json",
    "screenshots/*.png",
    "screenshots/*.jpg",
    "a11y/*",
    "player/console.ndjson",
    "player/network.ndjson",
    "player/actions.ndjson",
    "player/bugs.ndjson",
    "player/summary.md",
    "player/score.json",
    "player/screenshots/*.png",
    "player/screenshots/*.jpg",
    "player/a11y/*",
    "native/*.png",
    "native/*.json",
    "native/*.log",
    "hook-probe.json",
    "provider-trace-summary.json",
    "handoff-command.log",
    "ui_playtest_app.log",
    "scripted-provider/*.json",
    "scripted-provider/*.ndjson",
    "scripted-provider/*.log",
    "codex-provider/*.json",
    "codex-provider/*.jsonl",
    "codex-provider/*.ndjson",
    "codex-provider/*.log",
    "codex-provider/*.txt",
    "play-state/*.jsonl",
    "play-state/scripted-provider/*.json",
    "play-state/scripted-provider/*.ndjson",
    "play-state/codex-provider/*.json",
    "play-state/codex-provider/*.jsonl",
    "play-state/codex-provider/*.ndjson",
    "play-state/codex-provider/*.log",
    "play-state/codex-provider/*.txt",
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def json_dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json_bytes(data: bytes, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source} did not return valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{source} returned JSON {type(payload).__name__}, expected object")
    return payload


def fetch_json(url: str, *, timeout: float = 5.0) -> tuple[dict[str, Any], dict[str, Any]]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "worldos-app-evidence-exporter/1",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(MAX_HTTP_BYTES + 1)
        if len(body) > MAX_HTTP_BYTES:
            raise ValueError(f"{url} response exceeded {MAX_HTTP_BYTES} bytes")
        payload = read_json_bytes(body, url)
        meta = {
            "url": url,
            "status": int(getattr(resp, "status", 0) or 0),
            "content_type": resp.headers.get("Content-Type", ""),
            "bytes": len(body),
        }
        return payload, meta


def bundle_dir_for(out: str) -> Path:
    if out:
        return Path(out).expanduser()
    return DEFAULT_OUTPUT_ROOT / "worldos-app-evidence" / utc_stamp()


def source_display(path: Path, bundle: Path) -> str:
    try:
        return str(path.relative_to(bundle))
    except ValueError:
        return str(path)


def art_status(app_status: dict[str, Any]) -> dict[str, Any]:
    art = app_status.get("art") if isinstance(app_status.get("art"), dict) else {}
    present = art.get("private_root_present")
    if present is True:
        status = "present"
    elif present is False:
        status = "missing"
    else:
        status = "unknown"
    return {
        "repo_root": str(art.get("repo_root") or ""),
        "private_root": str(art.get("private_root") or ""),
        "private_root_present": present if isinstance(present, bool) else None,
        "status": status,
    }


def build_info(app_status: dict[str, Any]) -> dict[str, str]:
    build = app_status.get("build") if isinstance(app_status.get("build"), dict) else {}
    return {
        "sha": str(build.get("sha") or ""),
        "version": str(build.get("version") or ""),
    }


def local_path_from_status(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme and parsed.scheme != "file":
        return None
    if parsed.scheme == "file":
        return Path(urllib.request.url2pathname(parsed.path))
    return Path(raw).expanduser()


def copy_local_file(kind: str, value: Any, bundle: Path, gaps: list[dict[str, str]]) -> dict[str, Any] | None:
    source = local_path_from_status(value)
    if source is None:
        if isinstance(value, str) and value.strip():
            gaps.append({
                "source": "local_file",
                "kind": kind,
                "path": value,
                "reason": "not_a_local_file_path",
            })
        return None

    try:
        resolved = source.resolve(strict=True)
    except FileNotFoundError:
        gaps.append({
            "source": "local_file",
            "kind": kind,
            "path": str(source),
            "reason": "missing",
        })
        return None
    except OSError as exc:
        gaps.append({
            "source": "local_file",
            "kind": kind,
            "path": str(source),
            "reason": f"unreadable_path: {exc}",
        })
        return None

    try:
        stat = resolved.stat()
    except OSError as exc:
        gaps.append({
            "source": "local_file",
            "kind": kind,
            "path": str(resolved),
            "reason": f"stat_failed: {exc}",
        })
        return None
    if not resolved.is_file():
        gaps.append({
            "source": "local_file",
            "kind": kind,
            "path": str(resolved),
            "reason": "not_a_regular_file",
        })
        return None
    if stat.st_size > MAX_LOCAL_FILE_BYTES:
        gaps.append({
            "source": "local_file",
            "kind": kind,
            "path": str(resolved),
            "reason": f"too_large: {stat.st_size} bytes",
        })
        return None
    if not os.access(resolved, os.R_OK):
        gaps.append({
            "source": "local_file",
            "kind": kind,
            "path": str(resolved),
            "reason": "not_readable",
        })
        return None

    suffix = resolved.suffix or ".txt"
    dest_dir = bundle / "local-files"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{kind}{suffix}"
    shutil.copy2(resolved, dest)
    return {
        "kind": kind,
        "source": str(resolved),
        "path": source_display(dest, bundle),
        "bytes": stat.st_size,
    }


def copy_evidence_file(kind: str, source: Path, dest: Path, bundle: Path, gaps: list[dict[str, str]]) -> dict[str, Any] | None:
    try:
        resolved = source.resolve(strict=True)
    except FileNotFoundError:
        gaps.append({"source": "run_dir", "kind": kind, "path": str(source), "reason": "missing"})
        return None
    except OSError as exc:
        gaps.append({"source": "run_dir", "kind": kind, "path": str(source), "reason": f"unreadable_path: {exc}"})
        return None
    try:
        stat = resolved.stat()
    except OSError as exc:
        gaps.append({"source": "run_dir", "kind": kind, "path": str(resolved), "reason": f"stat_failed: {exc}"})
        return None
    if not resolved.is_file():
        return None
    if stat.st_size > MAX_LOCAL_FILE_BYTES:
        gaps.append({"source": "run_dir", "kind": kind, "path": str(resolved), "reason": f"too_large: {stat.st_size} bytes"})
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved, dest)
    return {
        "kind": kind,
        "source": str(resolved),
        "path": source_display(dest, bundle),
        "bytes": stat.st_size,
    }


def copy_run_dir_evidence(run_dir_value: str, bundle: Path, gaps: list[dict[str, str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not run_dir_value:
        return {}, []
    run_dir = Path(run_dir_value).expanduser()
    try:
        resolved = run_dir.resolve(strict=True)
    except FileNotFoundError:
        gaps.append({"source": "run_dir", "kind": "directory", "path": str(run_dir), "reason": "missing"})
        return {"path": str(run_dir), "ok": False}, []
    except OSError as exc:
        gaps.append({"source": "run_dir", "kind": "directory", "path": str(run_dir), "reason": f"unreadable_path: {exc}"})
        return {"path": str(run_dir), "ok": False}, []
    if not resolved.is_dir():
        gaps.append({"source": "run_dir", "kind": "directory", "path": str(resolved), "reason": "not_a_directory"})
        return {"path": str(resolved), "ok": False}, []

    copied: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for pattern in RUN_DIR_PATTERNS:
        for source in resolved.glob(pattern):
            try:
                rel = source.relative_to(resolved)
            except ValueError:
                continue
            if source in seen:
                continue
            seen.add(source)
            kind = rel.as_posix().replace("/", "__")
            dest = bundle / "run-dir" / rel
            entry = copy_evidence_file(kind, source, dest, bundle, gaps)
            if entry is not None:
                copied.append(entry)
    return {
        "path": str(resolved),
        "ok": True,
        "copied_count": len(copied),
        "patterns": list(RUN_DIR_PATTERNS),
    }, copied


def local_file_candidates(app_status: dict[str, Any]) -> list[tuple[str, Any]]:
    viewer = app_status.get("viewer") if isinstance(app_status.get("viewer"), dict) else {}
    live = app_status.get("live") if isinstance(app_status.get("live"), dict) else {}
    return [
        ("chat", viewer.get("chat_path")),
        ("moves", live.get("moves_path")),
        ("transcript", viewer.get("transcript_path")),
    ]


def session_surface_url(app_status_url: str, app_status: dict[str, Any]) -> str:
    endpoints = app_status.get("endpoints") if isinstance(app_status.get("endpoints"), dict) else {}
    endpoint = str(endpoints.get("session_surface") or "/session-surface")
    candidate = urllib.parse.urljoin(app_status_url, endpoint)
    parsed = urllib.parse.urlparse(candidate)
    if parsed.query:
        return candidate
    live = app_status.get("live") if isinstance(app_status.get("live"), dict) else {}
    campaign_id = str(live.get("campaign_id") or "")
    if campaign_id:
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode({"campaign": campaign_id})))
    original = urllib.parse.urlparse(app_status_url)
    if original.query:
        return urllib.parse.urlunparse(parsed._replace(query=original.query))
    return candidate


def find_first_string(payload: Any, keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = find_first_string(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_first_string(value, keys)
            if found:
                return found
    return ""


def read_transition_file(path_value: str, bundle: Path, gaps: list[dict[str, str]]) -> dict[str, Any]:
    if not path_value:
        return {}
    source = local_path_from_status(path_value)
    if source is None:
        gaps.append({
            "source": "transition_file",
            "kind": "transition",
            "path": path_value,
            "reason": "not_a_local_file_path",
        })
        return {}
    copied = copy_local_file("transition", str(source), bundle, gaps)
    if copied is None:
        return {}
    path = bundle / copied["path"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        gaps.append({
            "source": "transition_file",
            "kind": "transition",
            "path": str(source),
            "reason": f"invalid_json: {exc}",
        })
        return {"path": copied["path"]}
    return {
        "path": copied["path"],
        "failure_bucket": find_first_string(payload, ("failure_bucket", "failureBucket", "bucket")),
        "failure_detail": find_first_string(payload, ("failure_detail", "failureDetail", "detail", "error")),
    }


def run_dir_failure(bundle: Path) -> dict[str, str]:
    candidates = (
        bundle / "run-dir" / "smoke.json",
        bundle / "run-dir" / "native" / "transition.json",
        bundle / "run-dir" / "transition.json",
        bundle / "run-dir" / "run.json",
    )
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        bucket = find_first_string(payload, ("failure_bucket", "failureBucket", "bucket"))
        detail = find_first_string(payload, ("failure_detail", "failureDetail", "detail", "error"))
        if bucket or detail:
            return {"failure_bucket": bucket, "failure_detail": detail, "source": source_display(path, bundle)}
    return {"failure_bucket": "", "failure_detail": "", "source": ""}


def copied_kinds(copied_files: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for entry in copied_files:
        kind = str(entry.get("kind") or "")
        path = str(entry.get("path") or "")
        if kind:
            values.add(kind)
        if path:
            values.add(path)
    return sorted(values)


def git_text(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    return (proc.stdout or "").strip()


def repo_snapshot() -> dict[str, Any]:
    status = git_text(["status", "--porcelain"])
    return {
        "path": str(ROOT),
        "branch": git_text(["branch", "--show-current"]),
        "commit_sha": git_text(["rev-parse", "HEAD"]),
        "dirty": bool(status),
    }


def command_from_arg(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def copied_paths(copied_files: list[dict[str, Any]]) -> list[str]:
    paths = []
    for entry in copied_files:
        path = str(entry.get("path") or "")
        if path:
            paths.append(path)
    return sorted(paths)


def evidence_index(copied_files: list[dict[str, Any]], sources: dict[str, Any]) -> dict[str, list[str]]:
    paths = copied_paths(copied_files)

    def matching(*needles: str) -> list[str]:
        lowered = tuple(needle.lower() for needle in needles)
        return sorted(path for path in paths if any(needle in path.lower() for needle in lowered))

    app_status = matching("app-status")
    session_surface = matching("session-surface")
    for key in ("app_status", "app_status_snapshot"):
        if (sources.get(key) or {}).get("path"):
            app_status.insert(0, str((sources.get(key) or {}).get("path")))
    for key in ("session_surface", "session_surface_snapshot"):
        if (sources.get(key) or {}).get("path"):
            session_surface.insert(0, str((sources.get(key) or {}).get("path")))
    return {
        "screenshots": sorted(path for path in paths if "/screenshots/" in f"/{path}" or path.lower().endswith((".png", ".jpg", ".jpeg"))),
        "app_status_snapshots": sorted(dict.fromkeys(app_status)),
        "session_surface_snapshots": sorted(dict.fromkeys(session_surface)),
        "moves": matching("moves", "player_moves"),
        "provider_trace": matching("provider-trace", "scripted-provider", "codex-provider"),
        "console_logs": matching("console.ndjson", "console.log", "console"),
        "network_logs": matching("network.ndjson", "network.log", "network"),
        "action_logs": matching("actions.ndjson", "actions.log", "actions"),
        "all_copied": paths,
    }


def first_bundle_json(bundle: Path, patterns: tuple[str, ...]) -> tuple[dict[str, Any], str]:
    for pattern in patterns:
        for path in sorted((bundle / "run-dir").glob(pattern)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                return payload, source_display(path, bundle)
    return {}, ""


def review_verdict(handoff_gate: dict[str, Any], failure: dict[str, str], gaps: list[dict[str, str]]) -> str:
    if failure.get("failure_bucket"):
        return "failed"
    if handoff_gate.get("ok") is True:
        return "passed"
    if gaps:
        return "failed"
    return "incomplete"


def build_review_entrypoint(
    *,
    args: argparse.Namespace,
    created_at: str,
    build: dict[str, str],
    art: dict[str, Any],
    live: dict[str, Any],
    failure: dict[str, str],
    sources: dict[str, Any],
    copied_files: list[dict[str, Any]],
    gaps: list[dict[str, str]],
    handoff_gate: dict[str, Any],
) -> dict[str, Any]:
    repo = repo_snapshot()
    index = evidence_index(copied_files, sources)
    provider = args.provider or str(live.get("provider") or "")
    verdict = args.verdict or review_verdict(handoff_gate, failure, gaps)
    return {
        "schema": "worldos.app-evidence-review-entrypoint.v1",
        "command": command_from_arg(args.command_json),
        "repo": repo["path"],
        "branch": repo["branch"],
        "commit_sha": args.commit_sha or repo["commit_sha"],
        "dirty": repo["dirty"],
        "app_build_sha": build.get("sha") or "",
        "provider": provider,
        "gate_kind": args.gate_kind or "",
        "run_id": str(live.get("run_id") or ""),
        "started_at": args.started_at or "",
        "ended_at": args.ended_at or created_at,
        "verdict": verdict,
        "failure_bucket": str(failure.get("failure_bucket") or ""),
        "failure_detail": str(failure.get("failure_detail") or ""),
        "art_status": art,
        "handoff_gate_ok": bool(handoff_gate.get("ok")),
        "evidence_gaps": gaps,
        "files": index,
    }


def build_handoff_gate(
    *,
    build: dict[str, str],
    art: dict[str, Any],
    live: dict[str, Any],
    failure: dict[str, str],
    sources: dict[str, Any],
    copied_files: list[dict[str, Any]],
    gaps: list[dict[str, str]],
) -> dict[str, Any]:
    copied = copied_kinds(copied_files)
    app_status_ok = bool((sources.get("app_status") or {}).get("ok") or (sources.get("app_status_snapshot") or {}).get("ok"))
    session_surface_ok = bool((sources.get("session_surface") or {}).get("ok") or (sources.get("session_surface_snapshot") or {}).get("ok"))
    run_dir_source = sources.get("run_dir") if isinstance(sources.get("run_dir"), dict) else None
    run_dir_ok = None if run_dir_source is None else bool(run_dir_source.get("ok"))
    private_art_present = art.get("private_root_present")
    can_act = live.get("can_act")
    enabled_action_count = live.get("enabled_action_count")
    try:
        enabled_count_int = int(enabled_action_count or 0)
    except (TypeError, ValueError):
        enabled_count_int = 0
    move_sink_present = any(
        "moves" in item or item.endswith("moves.ndjson") or item.endswith("player_moves.jsonl")
        for item in copied
    )
    bucket = str(failure.get("failure_bucket") or "")
    detail = str(failure.get("failure_detail") or "")

    blocking: list[str] = []
    if not build.get("sha"):
        blocking.append("missing build SHA")
    if (sources.get("app_status") is not None or sources.get("app_status_snapshot") is not None) and not app_status_ok:
        blocking.append("app-status fetch failed")
    if (sources.get("session_surface") is not None or sources.get("session_surface_snapshot") is not None) and not session_surface_ok:
        blocking.append("session-surface fetch failed")
    if run_dir_source is not None and not run_dir_ok:
        blocking.append("run-dir copy failed")
    if private_art_present is not True:
        blocking.append("private art not proven present")
    if not live.get("campaign_id"):
        blocking.append("campaign id missing")
    if can_act is not True:
        blocking.append("can_act not true")
    if enabled_count_int < 1:
        blocking.append("no enabled actions")
    if not move_sink_present:
        blocking.append("move sink evidence missing")
    if bucket:
        blocking.append(f"failure bucket: {bucket}")
    if gaps:
        blocking.append(f"evidence gaps: {len(gaps)}")

    return {
        "schema": "worldos.app-evidence-handoff.v1",
        "ok": len(blocking) == 0,
        "build_sha": str(build.get("sha") or ""),
        "app_status_ok": app_status_ok,
        "session_surface_ok": session_surface_ok,
        "run_dir_ok": run_dir_ok,
        "private_art_present": private_art_present if isinstance(private_art_present, bool) else None,
        "campaign_id": str(live.get("campaign_id") or ""),
        "run_id": str(live.get("run_id") or ""),
        "can_act": can_act if isinstance(can_act, bool) else None,
        "enabled_action_count": enabled_action_count,
        "move_sink_present": move_sink_present,
        "copied_kinds": copied,
        "failure_bucket": bucket,
        "failure_detail": detail,
        "evidence_gap_count": len(gaps),
        "blocking_reasons": blocking,
    }


def exporter_manifest(args: argparse.Namespace, bundle: Path) -> tuple[dict[str, Any], int]:
    gaps: list[dict[str, str]] = []
    sources: dict[str, Any] = {}
    copied_files: list[dict[str, Any]] = []
    app_status: dict[str, Any] = {}
    exit_code = 0
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def clear_http_gap(source: str) -> None:
        gaps[:] = [
            gap
            for gap in gaps
            if not (gap.get("source") == source and gap.get("kind") == "http_json")
        ]

    if args.app_status_url:
        try:
            app_status, meta = fetch_json(args.app_status_url)
            json_dump(bundle / "app-status.json", app_status)
            sources["app_status"] = {
                **meta,
                "path": "app-status.json",
                "ok": True,
            }
        except (OSError, urllib.error.URLError, ValueError) as exc:
            gaps.append({
                "source": "app_status",
                "kind": "http_json",
                "path": args.app_status_url,
                "reason": str(exc),
            })
            sources["app_status"] = {
                "url": args.app_status_url,
                "ok": False,
            }
            exit_code = 1

    if app_status:
        surface_url = session_surface_url(args.app_status_url, app_status)
        try:
            session_surface, surface_meta = fetch_json(surface_url)
            json_dump(bundle / "session-surface.json", session_surface)
            sources["session_surface"] = {
                **surface_meta,
                "path": "session-surface.json",
                "ok": True,
            }
        except (OSError, urllib.error.URLError, ValueError) as exc:
            gaps.append({
                "source": "session_surface",
                "kind": "http_json",
                "path": surface_url,
                "reason": str(exc),
            })
            sources["session_surface"] = {
                "url": surface_url,
                "ok": False,
            }

        for kind, value in local_file_candidates(app_status):
            copied = copy_local_file(kind, value, bundle, gaps)
            if copied is not None:
                copied_files.append(copied)

    transition = read_transition_file(args.transition_file, bundle, gaps)
    if transition:
        sources["transition"] = transition

    run_source, run_copied = copy_run_dir_evidence(args.run_dir, bundle, gaps)
    if run_source:
        sources["run_dir"] = run_source
        copied_files.extend(run_copied)
    if not app_status:
        app_status, app_status_snapshot = first_bundle_json(bundle, ("app-status.final.json", "app-status*.json"))
        if app_status_snapshot:
            sources["app_status_snapshot"] = {"path": app_status_snapshot, "ok": True}
            if isinstance(sources.get("app_status"), dict):
                sources["app_status"]["recovered_by"] = "app_status_snapshot"
            clear_http_gap("app_status")
            exit_code = 0
    if not (sources.get("session_surface") or {}).get("ok"):
        _surface, session_surface_snapshot = first_bundle_json(bundle, ("session-surface.final.json", "session-surface*.json"))
        if session_surface_snapshot:
            sources["session_surface_snapshot"] = {"path": session_surface_snapshot, "ok": True}
            if isinstance(sources.get("session_surface"), dict):
                sources["session_surface"]["recovered_by"] = "session_surface_snapshot"
            clear_http_gap("session_surface")

    live = app_status.get("live") if isinstance(app_status.get("live"), dict) else {}
    failure = {
        "failure_bucket": "",
        "failure_detail": "",
        "source": "",
    }
    if transition:
        failure = {
            "failure_bucket": str(transition.get("failure_bucket") or ""),
            "failure_detail": str(transition.get("failure_detail") or ""),
            "source": str(transition.get("path") or ""),
        }
    if not failure["failure_bucket"] and run_source:
        failure = run_dir_failure(bundle)
    build = build_info(app_status)
    art = art_status(app_status)
    live_summary = {
        "campaign_id": str(live.get("campaign_id") or ""),
        "attached_campaign_id": str(live.get("attached_campaign_id") or ""),
        "run_id": str(live.get("run_id") or ""),
        "can_act": bool(live.get("can_act")) if "can_act" in live else None,
        "enabled_action_count": live.get("enabled_action_count"),
    }
    handoff_gate = build_handoff_gate(
        build=build,
        art=art,
        live=live_summary,
        failure=failure,
        sources=sources,
        copied_files=copied_files,
        gaps=gaps,
    )
    review_entrypoint = build_review_entrypoint(
        args=args,
        created_at=created_at,
        build=build,
        art=art,
        live=live_summary,
        failure=failure,
        sources=sources,
        copied_files=copied_files,
        gaps=gaps,
        handoff_gate=handoff_gate,
    )
    manifest = {
        "schema": "worldos.app-evidence.v1",
        "created_at": created_at,
        "app_status_url": args.app_status_url or "",
        "bundle_dir": str(bundle),
        "command": review_entrypoint["command"],
        "repo": review_entrypoint["repo"],
        "branch": review_entrypoint["branch"],
        "commit_sha": review_entrypoint["commit_sha"],
        "dirty": review_entrypoint["dirty"],
        "app_build_sha": review_entrypoint["app_build_sha"],
        "provider": review_entrypoint["provider"],
        "gate_kind": review_entrypoint["gate_kind"],
        "run_id": review_entrypoint["run_id"],
        "started_at": review_entrypoint["started_at"],
        "ended_at": review_entrypoint["ended_at"],
        "verdict": review_entrypoint["verdict"],
        "failure_bucket": review_entrypoint["failure_bucket"],
        "build": build,
        "art": art,
        "live": live_summary,
        "failure": failure,
        "sources": sources,
        "copied_files": copied_files,
        "evidence_gaps": gaps,
        "review_entrypoint": review_entrypoint,
        "evidence_files": review_entrypoint["files"],
        "handoff_gate": handoff_gate,
    }
    return manifest, exit_code


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a read-only WorldOS app evidence bundle.")
    parser.add_argument("--app-status-url", default="", help="URL for the app /app-status endpoint")
    parser.add_argument("--out", default="", help="Evidence bundle directory (default: /Volumes/LEXAR/Codex/worldos-app-evidence/<timestamp>)")
    parser.add_argument("--transition-file", default="", help="Optional local JSON file with failure bucket/detail")
    parser.add_argument("--run-dir", default="", help="Optional app playtest run directory to copy into the evidence bundle")
    parser.add_argument("--command-json", default="", help="JSON command argv that produced this evidence")
    parser.add_argument("--gate-kind", default="", help="Gate kind such as web_scripted_smoke or built_app_codex_playtest")
    parser.add_argument("--provider", default="", help="Provider under test")
    parser.add_argument("--started-at", default="", help="Gate start timestamp")
    parser.add_argument("--ended-at", default="", help="Gate end timestamp (defaults to manifest creation time)")
    parser.add_argument("--verdict", default="", help="Optional explicit gate verdict")
    parser.add_argument("--commit-sha", default="", help="Optional repo commit SHA override")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    bundle = bundle_dir_for(args.out).resolve()
    bundle.mkdir(parents=True, exist_ok=True)
    manifest, exit_code = exporter_manifest(args, bundle)
    manifest_path = bundle / "manifest.json"
    json_dump(manifest_path, manifest)
    print(str(manifest_path))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
