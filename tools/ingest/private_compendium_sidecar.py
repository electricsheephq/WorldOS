#!/usr/bin/env python3
"""Plan private compendium sidecar imports without importing private content.

This is local-only ingest scaffolding for user-owned books, adventures, exports, or
homebrew. It validates a sidecar manifest kept outside the git checkout and prints
the private, gitignored WorldOS outputs a later importer would write. It does not
copy records, mutate campaign state, or write tracked content.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SIDECAR_ROOT = Path("/Volumes/LEXAR/Codex/clawdnd-private-compendium")
ENV_SIDECAR_ROOT = "CLAWDND_PRIVATE_COMPENDIUM_ROOT"
MANIFEST_NAME = "private-compendium-manifest.json"
SUPPORTED_FORMATS = {"markdown", "json", "text"}
SUPPORTED_CONTENT_TYPES = {"lore"}

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]


class ManifestError(ValueError):
    """Raised when a private sidecar manifest is unsafe or malformed."""


@dataclass(frozen=True)
class PlannedSource:
    source_id: str
    title: str
    format: str
    content_type: str
    source_path: Path
    planned_output: Path


@dataclass(frozen=True)
class SidecarPlan:
    manifest_path: Path
    private_root: Path
    world_id: str
    sources: tuple[PlannedSource, ...]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _source_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value.strip()):
        raise ManifestError(f"{field} must be a simple slug, not a path")
    return value.strip().lower()


def _safe_world_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ManifestError("world_id must be a simple slug, not a path")
    return value


def _safe_source_path(raw_path: object, private_root: Path) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ManifestError("source.path must be a non-empty string")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = private_root / candidate
    resolved = candidate.resolve(strict=False)
    root = private_root.resolve(strict=False)
    if not _is_relative_to(resolved, root):
        raise ManifestError(f"source.path is outside sidecar root: {raw_path}")
    return resolved


def _validate_private_root(private_root: Path, repo_root: Path) -> Path:
    root = private_root.resolve(strict=False)
    repo = repo_root.resolve(strict=False)
    if root == repo or _is_relative_to(root, repo):
        raise ManifestError("private sidecar root must be outside the git repository")
    return root


def _planned_output(world_id: str, source_id: str, content_type: str, source_format: str) -> Path:
    if content_type != "lore":
        raise ManifestError(f"unsupported content_type: {content_type}")
    ext = ".json" if source_format == "json" else ".md"
    return Path("content") / "worlds" / "_private" / world_id / "lore" / "compendium" / f"{source_id}{ext}"


def load_plan(manifest_path: Path, repo_root: Path = _REPO) -> SidecarPlan:
    """Read and validate a local-only sidecar manifest, returning planned outputs."""
    manifest_path = manifest_path.resolve(strict=False)
    private_root = _validate_private_root(manifest_path.parent, repo_root)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("manifest must be a JSON object")
    if data.get("schema_version") != 1:
        raise ManifestError("schema_version must be 1")
    if data.get("owner_acknowledgement") is not True:
        raise ManifestError("owner_acknowledgement must be true for private/user-owned content")

    world_id = _safe_world_id(data.get("world_id"))
    sources_raw = data.get("sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        raise ManifestError("sources must be a non-empty list")

    seen: set[str] = set()
    sources: list[PlannedSource] = []
    for index, raw in enumerate(sources_raw, 1):
        if not isinstance(raw, dict):
            raise ManifestError(f"sources[{index}] must be an object")
        source_id = _source_id(raw.get("id"), f"sources[{index}].id")
        if source_id in seen:
            raise ManifestError(f"duplicate source id: {source_id}")
        seen.add(source_id)

        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ManifestError(f"sources[{index}].title must be a non-empty string")
        source_format = str(raw.get("format", "")).strip().lower()
        if source_format not in SUPPORTED_FORMATS:
            raise ManifestError(f"sources[{index}].format must be one of {sorted(SUPPORTED_FORMATS)}")
        content_type = str(raw.get("content_type", "")).strip().lower()
        if content_type not in SUPPORTED_CONTENT_TYPES:
            raise ManifestError(
                f"sources[{index}].content_type must be one of {sorted(SUPPORTED_CONTENT_TYPES)}"
            )

        source_path = _safe_source_path(raw.get("path"), private_root)
        sources.append(
            PlannedSource(
                source_id=source_id,
                title=title.strip(),
                format=source_format,
                content_type=content_type,
                source_path=source_path,
                planned_output=_planned_output(world_id, source_id, content_type, source_format),
            )
        )

    return SidecarPlan(
        manifest_path=manifest_path,
        private_root=private_root,
        world_id=world_id,
        sources=tuple(sources),
    )


def _default_manifest_path() -> Path:
    root = Path(os.environ.get(ENV_SIDECAR_ROOT, str(DEFAULT_SIDECAR_ROOT))).expanduser()
    return root / MANIFEST_NAME


def _write_template(manifest_path: Path) -> None:
    if manifest_path.exists():
        raise ManifestError(f"manifest already exists: {manifest_path}")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    template = {
        "schema_version": 1,
        "world_id": "my-private-world",
        "owner_acknowledgement": True,
        "sources": [
            {
                "id": "owned-source",
                "title": "Owned Source",
                "format": "markdown",
                "path": "vault/owned-source.md",
                "content_type": "lore",
            }
        ],
    }
    manifest_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")


def _print_plan(plan: SidecarPlan) -> None:
    print(f"[private-sidecar] manifest: {plan.manifest_path}")
    print(f"[private-sidecar] sidecar root: {plan.private_root}")
    print(f"[private-sidecar] world: {plan.world_id}")
    print("[private-sidecar] planned outputs only; no content was imported")
    for source in plan.sources:
        exists = "exists" if source.source_path.exists() else "missing"
        print(
            f"  - {source.source_id}: {source.content_type}/{source.format} "
            f"{source.source_path} ({exists}) -> {source.planned_output}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=_default_manifest_path())
    ap.add_argument("--repo-root", type=Path, default=_REPO)
    ap.add_argument("--init", action="store_true", help="write a safe template manifest outside the repo")
    args = ap.parse_args()

    try:
        if args.init:
            _write_template(args.manifest)
            print(f"[private-sidecar] wrote template manifest: {args.manifest}")
            return 0
        plan = load_plan(args.manifest, repo_root=args.repo_root)
    except ManifestError as exc:
        print(f"PRIVATE COMPENDIUM SIDECAR ERROR: {exc}", file=sys.stderr)
        return 1

    _print_plan(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
