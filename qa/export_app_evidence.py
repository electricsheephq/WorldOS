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
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_ROOT = Path("/Volumes/LEXAR/Codex")
MAX_HTTP_BYTES = 8 * 1024 * 1024
MAX_LOCAL_FILE_BYTES = 64 * 1024 * 1024
RUN_ARTIFACT_PATTERNS: dict[str, tuple[str, ...]] = {
    "screenshots": ("native/*.png", "player/screenshots/*.png"),
    "a11y": ("player/a11y/*.txt",),
    "app_status_snapshots": ("native/app-status*.json",),
    "session_surfaces": ("session_surface*.json", "native/session-surface*.json"),
    "logs": (
        "console.ndjson",
        "network.ndjson",
        "actions.ndjson",
        "bugs.ndjson",
        "backend.log",
        "native/transition.log",
        "player/player.err",
    ),
    "scores": ("run.json", "score.json", "summary.md", "meta.json"),
}


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


def copy_run_artifact(
    category: str,
    run_dir: Path,
    source: Path,
    bundle: Path,
    gaps: list[dict[str, str]],
) -> dict[str, Any] | None:
    try:
        resolved = source.resolve(strict=True)
        relative = resolved.relative_to(run_dir.resolve(strict=True))
    except (FileNotFoundError, ValueError, OSError) as exc:
        gaps.append({
            "source": "run_artifact",
            "kind": category,
            "path": str(source),
            "reason": f"unreadable_path: {exc}",
        })
        return None

    try:
        stat = resolved.stat()
    except OSError as exc:
        gaps.append({
            "source": "run_artifact",
            "kind": category,
            "path": str(resolved),
            "reason": f"stat_failed: {exc}",
        })
        return None
    if not resolved.is_file() or stat.st_size > MAX_LOCAL_FILE_BYTES:
        reason = "not_a_regular_file" if not resolved.is_file() else f"too_large: {stat.st_size} bytes"
        gaps.append({
            "source": "run_artifact",
            "kind": category,
            "path": str(resolved),
            "reason": reason,
        })
        return None

    dest = bundle / "run-artifacts" / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved, dest)
    return {
        "kind": category,
        "source": str(resolved),
        "path": source_display(dest, bundle),
        "bytes": stat.st_size,
    }


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


def run_dir_from_args(args: argparse.Namespace) -> Path | None:
    if args.run_dir:
        return Path(args.run_dir).expanduser()
    source = local_path_from_status(args.transition_file)
    if source is None:
        return None
    if source.name != "transition.json":
        return source.parent
    if source.parent.name == "native":
        return source.parent.parent
    return source.parent


def collect_run_artifacts(run_dir: Path | None, bundle: Path, gaps: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    artifacts: dict[str, list[dict[str, Any]]] = {category: [] for category in RUN_ARTIFACT_PATTERNS}
    if run_dir is None:
        return artifacts
    try:
        resolved_run_dir = run_dir.resolve(strict=True)
    except FileNotFoundError:
        gaps.append({
            "source": "run_dir",
            "kind": "directory",
            "path": str(run_dir),
            "reason": "missing",
        })
        return artifacts
    except OSError as exc:
        gaps.append({
            "source": "run_dir",
            "kind": "directory",
            "path": str(run_dir),
            "reason": f"unreadable_path: {exc}",
        })
        return artifacts

    seen: set[Path] = set()
    for category, patterns in RUN_ARTIFACT_PATTERNS.items():
        for pattern in patterns:
            for source in sorted(resolved_run_dir.glob(pattern)):
                try:
                    resolved = source.resolve(strict=True)
                except OSError:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                copied = copy_run_artifact(category, resolved_run_dir, resolved, bundle, gaps)
                if copied is not None:
                    artifacts[category].append(copied)
    return artifacts


def provider_trace_summary(app_status: dict[str, Any], copied_files: list[dict[str, Any]], gaps: list[dict[str, str]]) -> dict[str, Any]:
    viewer = app_status.get("viewer") if isinstance(app_status.get("viewer"), dict) else {}
    source = local_path_from_status(viewer.get("transcript_path"))
    copied_path = ""
    for copied in copied_files:
        if copied.get("kind") == "transcript":
            copied_path = str(copied.get("path") or "")
            break
    summary: dict[str, Any] = {
        "source": str(source or ""),
        "path": copied_path,
        "line_count": 0,
        "json_line_count": 0,
        "result_count": 0,
        "total_cost_usd": 0.0,
        "available": False,
    }
    if source is None:
        return summary
    try:
        resolved = source.resolve(strict=True)
        stat = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        gaps.append({
            "source": "provider_trace",
            "kind": "transcript_summary",
            "path": str(source),
            "reason": str(exc),
        })
        return summary
    if stat.st_size > MAX_LOCAL_FILE_BYTES:
        gaps.append({
            "source": "provider_trace",
            "kind": "transcript_summary",
            "path": str(resolved),
            "reason": f"too_large: {stat.st_size} bytes",
        })
        return summary

    try:
        with resolved.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                summary["line_count"] += 1
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    summary["json_line_count"] += 1
                    if payload.get("type") == "result":
                        summary["result_count"] += 1
                        try:
                            summary["total_cost_usd"] += float(payload.get("total_cost_usd") or 0)
                        except (TypeError, ValueError):
                            pass
    except OSError as exc:
        gaps.append({
            "source": "provider_trace",
            "kind": "transcript_summary",
            "path": str(resolved),
            "reason": f"read_failed: {exc}",
        })
        return summary
    summary["total_cost_usd"] = round(float(summary["total_cost_usd"]), 4)
    summary["available"] = summary["line_count"] > 0
    return summary


def exporter_manifest(args: argparse.Namespace, bundle: Path) -> tuple[dict[str, Any], int]:
    gaps: list[dict[str, str]] = []
    sources: dict[str, Any] = {}
    copied_files: list[dict[str, Any]] = []
    app_status: dict[str, Any] = {}
    exit_code = 0

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

    run_dir = run_dir_from_args(args)
    transition_file = args.transition_file
    if not transition_file and run_dir is not None:
        candidate = run_dir / "native" / "transition.json"
        if candidate.exists():
            transition_file = str(candidate)
    transition = read_transition_file(transition_file, bundle, gaps)
    if transition:
        sources["transition"] = transition
    run_artifacts = collect_run_artifacts(run_dir, bundle, gaps)
    provider_summary = provider_trace_summary(app_status, copied_files, gaps)

    live = app_status.get("live") if isinstance(app_status.get("live"), dict) else {}
    failure_bucket = str(transition.get("failure_bucket") or "")
    failure_detail = str(transition.get("failure_detail") or "")
    manifest = {
        "schema": "worldos.app-evidence.v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "app_status_url": args.app_status_url,
        "bundle_dir": str(bundle),
        "run_dir": str(run_dir.resolve()) if run_dir is not None and run_dir.exists() else str(run_dir or ""),
        "build": build_info(app_status),
        "art": art_status(app_status),
        "live": {
            "campaign_id": str(live.get("campaign_id") or ""),
            "attached_campaign_id": str(live.get("attached_campaign_id") or ""),
            "run_id": str(live.get("run_id") or ""),
            "can_act": bool(live.get("can_act")) if "can_act" in live else None,
            "enabled_action_count": live.get("enabled_action_count"),
        },
        "failure_bucket": failure_bucket,
        "failure": {
            "bucket": failure_bucket,
            "detail": failure_detail,
        },
        "sources": sources,
        "copied_files": copied_files,
        "run_artifacts": run_artifacts,
        "provider_trace_summary": provider_summary,
        "evidence_gaps": gaps,
    }
    return manifest, exit_code


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a read-only WorldOS app evidence bundle.")
    parser.add_argument("--app-status-url", required=True, help="URL for the app /app-status endpoint")
    parser.add_argument("--out", default="", help="Evidence bundle directory (default: /Volumes/LEXAR/Codex/worldos-app-evidence/<timestamp>)")
    parser.add_argument("--run-dir", default="", help="Optional qa/ui_playtest_runs/<run> directory to copy screenshots/logs/snapshots from")
    parser.add_argument("--transition-file", default="", help="Optional local JSON file with failure bucket/detail")
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
