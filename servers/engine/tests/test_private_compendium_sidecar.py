"""Guards the private compendium sidecar manifest validator.

The sidecar is stdlib-only ingest tooling that deliberately plans local/private
outputs instead of importing owned content into tracked ClawDnD content.
"""

import json
import sys
from pathlib import Path

_INGEST = Path(__file__).resolve().parents[3] / "tools" / "ingest"
sys.path.insert(0, str(_INGEST))

import private_compendium_sidecar as sidecar  # noqa: E402


def _manifest(root: Path, **overrides):
    data = {
        "schema_version": 1,
        "world_id": "my-private-world",
        "owner_acknowledgement": True,
        "sources": [
            {
                "id": "owned-book",
                "title": "Owned Book",
                "format": "markdown",
                "path": "vault/owned-book.md",
                "content_type": "lore",
            }
        ],
    }
    data.update(overrides)
    manifest = root / "private-compendium-manifest.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    (root / "vault").mkdir()
    (root / "vault" / "owned-book.md").write_text("# owned", encoding="utf-8")
    return manifest


def test_manifest_validation_plans_gitignored_private_outputs(tmp_path):
    manifest = _manifest(tmp_path)

    plan = sidecar.load_plan(manifest, repo_root=Path("/repo"))

    assert plan.world_id == "my-private-world"
    assert plan.private_root == tmp_path
    assert len(plan.sources) == 1
    assert plan.sources[0].planned_output == Path(
        "content/worlds/_private/my-private-world/lore/compendium/owned-book.md"
    )
    assert plan.sources[0].source_path == tmp_path / "vault" / "owned-book.md"


def test_manifest_requires_owner_acknowledgement(tmp_path):
    manifest = _manifest(tmp_path, owner_acknowledgement=False)

    try:
        sidecar.load_plan(manifest, repo_root=Path("/repo"))
    except sidecar.ManifestError as exc:
        assert "owner_acknowledgement" in str(exc)
    else:
        raise AssertionError("expected owner acknowledgement failure")


def test_manifest_rejects_path_escape(tmp_path):
    manifest = _manifest(
        tmp_path,
        sources=[{
            "id": "escape",
            "title": "Escape",
            "format": "markdown",
            "path": "../outside.md",
            "content_type": "lore",
        }],
    )

    try:
        sidecar.load_plan(manifest, repo_root=Path("/repo"))
    except sidecar.ManifestError as exc:
        assert "outside sidecar root" in str(exc)
    else:
        raise AssertionError("expected path escape failure")


def test_manifest_rejects_path_like_source_id(tmp_path):
    manifest = _manifest(
        tmp_path,
        sources=[{
            "id": "../owned-book",
            "title": "Owned Book",
            "format": "markdown",
            "path": "vault/owned-book.md",
            "content_type": "lore",
        }],
    )

    try:
        sidecar.load_plan(manifest, repo_root=Path("/repo"))
    except sidecar.ManifestError as exc:
        assert "id must be a simple slug" in str(exc)
    else:
        raise AssertionError("expected source id validation failure")


def test_manifest_rejects_repo_local_sidecar_root(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sidecar_root = repo / "sidecar"
    sidecar_root.mkdir()
    manifest = _manifest(sidecar_root)

    try:
        sidecar.load_plan(manifest, repo_root=repo)
    except sidecar.ManifestError as exc:
        assert "outside the git repository" in str(exc)
    else:
        raise AssertionError("expected repo-local sidecar failure")
