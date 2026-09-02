#!/usr/bin/env python3
"""Check that a packaged player's plate data matches the repository manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable


_SCRIPT_REPO = Path(__file__).resolve().parents[1]
_UNITY_RELATIVE = Path("extensions") / "renderers" / "unity"
_STREAMING_RELATIVE = Path("Contents") / "Resources" / "Data" / "StreamingAssets"
_MISSING = object()


def _repo_sha(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else None


def _repo_dirty(repo: Path) -> bool | None:
    """True when renderer data under the repo differs from HEAD (best effort; None if unknown).

    A file that `git status` lists but whose RAW bytes equal the committed blob is NOT dirty: on this Mac
    the LFS clean filter marks byte-identical plates as modified (a filter artifact, not a data change)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "-z", "--", str(_UNITY_RELATIVE)],
            capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    entries = [e for e in result.stdout.split("\0") if e]
    i = 0
    while i < len(entries):
        entry = entries[i]
        status, path = entry[:2], entry[3:]
        i += 1
        if status[0] == "R" or status[1] == "R":
            i += 1  # rename carries the old path as the next entry
            return True
        if status.strip() in ("M",):
            try:
                raw = subprocess.run(["git", "-C", str(repo), "hash-object", "--no-filters", "--", path],
                                     capture_output=True, text=True, check=False, timeout=5).stdout.strip()
                head = subprocess.run(["git", "-C", str(repo), "rev-parse", f"HEAD:{path}"],
                                      capture_output=True, text=True, check=False, timeout=5).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return None
            if raw and head and raw == head:
                continue  # byte-identical to HEAD: a clean-filter artifact, not a change
        return True
    return False


_MAX_HASH_BYTES = 1 << 30  # 1 GiB — a plate/sidecar is MBs; anything larger is not ours to hash


def _base_report(app: Path, repo: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ts": datetime.now(timezone.utc).isoformat(),
        "app": str(app.resolve()),
        "repo": str(repo.resolve()),
        "repo_sha": (lambda sha, dirty: (sha + "-dirty") if (sha and dirty) else sha)(_repo_sha(repo), _repo_dirty(repo)),
        "repo_dirty": _repo_dirty(repo),
        "rooms_requested": None,
        "rooms": [],
        "verdict": "ERROR",
    }


def _error(app: Path, repo: Path, message: str) -> dict[str, Any]:
    report = _base_report(app, repo)
    report["error"] = message
    return report


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("plates"), dict):
        raise ValueError("manifest must contain a plates object")
    if not all(isinstance(room, str) for room in payload["plates"]):
        raise ValueError("manifest room keys must be strings")
    return payload


def _normalise_rooms(rooms: Iterable[str] | str | None) -> set[str] | None:
    if rooms is None:
        return None
    if isinstance(rooms, str):
        rooms = rooms.split(",")
    return {str(room).strip() for room in rooms if str(room).strip()}


def _entry_path(root: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = root / value
    norm = os.path.normpath(str(candidate))
    root_norm = os.path.normpath(str(root))
    if norm != root_norm and not norm.startswith(root_norm + os.sep):
        return None  # escapes the packaged/repo root
    return candidate


def _sha256(path: Path) -> str:
    # Refuse anything that is not a plain, bounded file: a symlink to a device (e.g. /dev/zero) or a socket
    # would read forever; an oversized target is not renderer data. Raised as OSError -> main() reports ERROR/2.
    st = path.stat()
    if not path.is_file():
        raise OSError(f"refusing to hash a non-regular file: {path}")
    if st.st_size > _MAX_HASH_BYTES:
        raise OSError(f"refusing to hash an oversized file ({st.st_size} bytes): {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compare_file(
    kind: str,
    source_entry: dict[str, Any],
    packaged_entry: dict[str, Any],
    source_root: Path,
    packaged_root: Path,
    reasons: list[str],
) -> None:
    source_value = source_entry.get(kind, _MISSING)
    packaged_value = packaged_entry.get(kind, _MISSING)
    if source_value is _MISSING and packaged_value is _MISSING:
        return
    if source_value is _MISSING:
        reasons.append(f"MISSING {kind} manifest entry on source")
        return
    if packaged_value is _MISSING:
        reasons.append(f"MISSING {kind} manifest entry on packaged side")
        return
    if not isinstance(source_value, str) or not source_value:
        reasons.append(f"MISSING {kind} manifest path on source")
        return
    if not isinstance(packaged_value, str) or not packaged_value:
        reasons.append(f"MISSING {kind} manifest path on packaged side")
        return

    source_path = _entry_path(source_root, source_value)
    packaged_path = _entry_path(packaged_root, packaged_value)
    if source_value != packaged_value:
        reasons.append(f"{kind} path differs: source={source_value} packaged={packaged_value}")
    if source_path is None or not source_path.is_file():
        reasons.append(f"MISSING {kind} on source: {source_value}")
    if packaged_path is None or not packaged_path.is_file():
        reasons.append(f"MISSING {kind} on packaged side: {packaged_value}")
    if source_value == packaged_value and source_path is not None and packaged_path is not None:
        if source_path.is_file() and packaged_path.is_file():
            if _sha256(source_path) != _sha256(packaged_path):
                reasons.append(f"{kind} sha256 differs")


def _compare_camera_pin(
    source_entry: dict[str, Any], packaged_entry: dict[str, Any], reasons: list[str]
) -> None:
    source_pin = source_entry.get("cameraPin", _MISSING)
    packaged_pin = packaged_entry.get("cameraPin", _MISSING)
    if source_pin is _MISSING and packaged_pin is _MISSING:
        return
    if source_pin is _MISSING:
        reasons.append("MISSING cameraPin on source")
        return
    if packaged_pin is _MISSING:
        reasons.append("MISSING cameraPin on packaged side")
        return
    if not isinstance(source_pin, dict) or not isinstance(packaged_pin, dict):
        reasons.append("cameraPin is not an object on both sides")
        return
    for key in sorted(set(source_pin) | set(packaged_pin)):
        source_value = source_pin.get(key, _MISSING)
        packaged_value = packaged_pin.get(key, _MISSING)
        if source_value is _MISSING:
            reasons.append(f"MISSING cameraPin.{key} on source")
        elif packaged_value is _MISSING:
            reasons.append(f"MISSING cameraPin.{key} on packaged side")
        elif source_value != packaged_value:
            reasons.append(
                f"cameraPin.{key} differs: source={source_value} packaged={packaged_value}"
            )


def _compare_room(
    room: str,
    source_entry: Any,
    packaged_entry: Any,
    source_root: Path,
    packaged_root: Path,
) -> dict[str, Any]:
    reasons: list[str] = []
    if source_entry is None:
        reasons.append("MISSING room on source side")
    elif packaged_entry is None:
        reasons.append("MISSING room on packaged side")
    elif not isinstance(source_entry, dict) or not isinstance(packaged_entry, dict):
        reasons.append("invalid room manifest entry")
    else:
        _compare_file("plate", source_entry, packaged_entry, source_root, packaged_root, reasons)
        _compare_file("boxes", source_entry, packaged_entry, source_root, packaged_root, reasons)
        _compare_camera_pin(source_entry, packaged_entry, reasons)
        # Every OTHER runtime field the client reads (planeSize, effects, door_hotspots, ...) must match too;
        # keys starting with "_" are comments/provenance and are not runtime data.
        for key in sorted(set(source_entry) | set(packaged_entry)):
            if key in ("plate", "boxes", "cameraPin") or key.startswith("_"):
                continue
            if source_entry.get(key, _MISSING) != packaged_entry.get(key, _MISSING):
                reasons.append(f"manifest field differs: {key}")
    return {"room": room, "status": "GREEN" if not reasons else "RED", "reasons": reasons}


def check(app: Path, repo: Path, rooms: Iterable[str] | str | None = None) -> dict[str, Any]:
    """Compare packaged plate data with the repository's Unity project data."""
    app = Path(app)
    repo = Path(repo)
    if app.suffix != ".app":
        return _error(app, repo, "app path is not a .app")

    packaged_root = app / _STREAMING_RELATIVE
    if not packaged_root.is_dir():
        return _error(app, repo, "StreamingAssets directory is missing")

    source_root = repo / _UNITY_RELATIVE
    source_manifest_path = source_root / "plates_manifest.json"
    packaged_manifest_path = packaged_root / "plates_manifest.json"
    try:
        source_manifest = _load_manifest(source_manifest_path)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _error(app, repo, f"repo manifest unreadable: {exc}")
    try:
        packaged_manifest = _load_manifest(packaged_manifest_path)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _error(app, repo, f"packaged manifest unreadable: {exc}")

    source_rooms = source_manifest["plates"]
    packaged_rooms = packaged_manifest["plates"]
    selected = _normalise_rooms(rooms)
    room_names = sorted(set(source_rooms) | set(packaged_rooms))
    if selected is not None:
        unknown = sorted(selected - set(room_names))
        if unknown:
            return _error(app, repo, f"unknown room(s) requested: {', '.join(unknown)}")
        room_names = [room for room in room_names if room in selected]
    if not room_names:
        return _error(app, repo, "no rooms to check (empty selection, or neither manifest lists a plate)")

    report = _base_report(app, repo)
    report["rooms_requested"] = sorted(selected) if selected is not None else None
    report["rooms"] = [
        _compare_room(
            room,
            source_rooms.get(room),
            packaged_rooms.get(room),
            source_root,
            packaged_root,
        )
        for room in room_names
    ]
    report["verdict"] = "RED" if any(room["status"] == "RED" for room in report["rooms"]) else "GREEN"
    return report


def _exit_code(report: dict[str, Any]) -> int:
    verdict = report.get("verdict")
    return 0 if verdict == "GREEN" else 1 if verdict == "RED" else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path, help="WorldOSPlayer.app bundle")
    parser.add_argument("--repo", type=Path, default=_SCRIPT_REPO, help="WorldOS repository root")
    parser.add_argument("--json", dest="json_path", type=Path, help="write the JSON report here")
    parser.add_argument("--rooms", help="comma-separated room keys to check")
    args = parser.parse_args(argv)

    try:
        report = check(args.app, args.repo, args.rooms)
    except Exception as exc:  # a harness defect is never a verdict
        report = _error(args.app, args.repo, f"unexpected: {type(exc).__name__}: {exc}")
    if report["verdict"] == "ERROR":
        print(f"PINS ERROR ({report.get('error', 'unknown error')})")
    else:
        for room in report["rooms"]:
            suffix = f" — {'; '.join(room['reasons'])}" if room["reasons"] else ""
            print(f"{room['room']}: {room['status']}{suffix}")
        drift = sum(room["status"] == "RED" for room in report["rooms"])
        if report.get("repo_dirty"):
            print("WARN: renderer data under the repo has uncommitted changes — repo_sha is stamped -dirty; "
                  "parity is measured against the WORKING TREE, not that commit", file=sys.stderr)
        print(f"PINS {report['verdict']} ({drift} drift)")

    if args.json_path is not None:
        try:
            args.json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"PINS ERROR (JSON report write failed: {exc})", file=sys.stderr)
            return 2
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
