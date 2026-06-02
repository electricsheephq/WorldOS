"""UGC render-profile store (M3 #453, #442) — engine-owned, versioned, additive.

Per-user persistence for AI-built / user-authored games, as the foundation for UGC. The key
design call that keeps this collision-safe and invariant-safe:

  A RENDER-PROFILE IS PRESENTATION, NOT GAME STATE. The engine is the sole writer of game STATE
  (snapshot.json); a render-profile only JOINS to that state by id. So persisting UGC profiles
  does NOT touch the engine's sole-writership and needs NO change to servers/engine/. They are
  SERVER-OWNED artifacts: stored under the state dir, versioned append-only (an edit never
  overwrites — every save is a new version, so ownership + history are intact), and mutated ONLY
  through a constrained, VALIDATED save-intent (mirroring the /move intent pattern — a client
  never writes the store directly; it asks the server to, and the server rejects anything that
  fails the frozen-contract gate).

Layout:  <root>/<owner>/<game_id>/v<N>.json   (+ latest.json convenience copy)
  owner  : per-user namespace ("local" for the single-user v0); enables multi-user ownership.
  game_id: the profile's game_id (slug).
  v<N>   : 1-based, monotonically increasing; append-only.

MIT REDISTRIBUTION (the #453 rights story): the renderers are vendored Phaser (MIT) + our own
MIT glue; a user's generated profile + procedural art are theirs to ship. The first-party BG
catalog is internal-only and is NEVER persisted into a shippable UGC profile — the build-loop's
human-gate `ai-disclosure-and-rights` item enforces that; this store only persists what passed.

Stdlib only; no engine import; no network. Pure functions take an explicit `root: Path` so they
are testable without environment.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_BL = _HERE / "openworlds" / "render" / "build_loop"
_SCHEMA_PATH = _HERE.parent / "docs" / "roadmap" / "contracts" / "render-profile.schema.json"

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _load_gate():
    """Load the build-loop gate module by path (robust regardless of sys.path)."""
    spec = importlib.util.spec_from_file_location("ugc_gate", _BL / "gate.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _safe_slug(text: str, fallback: str) -> str:
    """A filesystem-safe, traversal-proof slug (no '/', no '..')."""
    s = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-.")
    s = s.replace("..", "-")
    return s or fallback


def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


def validate_and_gate(profile: dict, schema: dict | None = None) -> dict:
    """Run the frozen-contract gate on a profile. Returns the gate report (accepted + gates +
    human_gate_queue). The store persists ONLY accepted profiles."""
    gate = _load_gate()
    return gate.run_gate(profile, schema or _schema())


def _game_dir(root: Path, owner: str, game_id: str) -> Path:
    return root / _safe_slug(owner, "local") / _safe_slug(game_id, "untitled")


def _versions(game_dir: Path) -> list[int]:
    if not game_dir.is_dir():
        return []
    out = []
    for p in game_dir.glob("v*.json"):
        m = re.fullmatch(r"v(\d+)", p.stem)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def save_profile(root: Path, profile: dict, *, owner: str = "local",
                 schema: dict | None = None) -> dict:
    """Constrained save-intent: VALIDATE + GATE, then persist a new append-only version. Returns
    {accepted, owner, game_id, version, report}. On reject, nothing is written (version is None)."""
    report = validate_and_gate(profile, schema)
    game_id = str(profile.get("game_id") or "untitled")
    owner_slug = _safe_slug(owner, "local")
    if not report["accepted"]:
        return {"accepted": False, "owner": owner_slug, "game_id": _safe_slug(game_id, "untitled"),
                "version": None, "report": report}
    game_dir = _game_dir(root, owner, game_id)
    game_dir.mkdir(parents=True, exist_ok=True)
    version = (max(_versions(game_dir)) if _versions(game_dir) else 0) + 1
    text = json.dumps(profile, indent=2) + "\n"
    (game_dir / f"v{version}.json").write_text(text)
    (game_dir / "latest.json").write_text(text)  # convenience pointer to the newest version
    return {"accepted": True, "owner": owner_slug, "game_id": _safe_slug(game_id, "untitled"),
            "version": version, "report": report}


def load_profile(root: Path, game_id: str, *, owner: str = "local",
                 version: int | None = None) -> dict | None:
    """Load a stored profile (latest if version is None). None if absent."""
    game_dir = _game_dir(root, owner, game_id)
    if version is None:
        vs = _versions(game_dir)
        if not vs:
            return None
        version = max(vs)
    path = game_dir / f"v{int(version)}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def list_profiles(root: Path) -> list[dict]:
    """List stored UGC games across owners: {owner, game_id, title, scene_kind, latest_version,
    versions}. Read-only; tolerant of a missing/empty store."""
    out: list[dict] = []
    if not root.is_dir():
        return out
    for owner_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for game_dir in sorted(p for p in owner_dir.iterdir() if p.is_dir()):
            vs = _versions(game_dir)
            if not vs:
                continue
            latest = load_profile(root, game_dir.name, owner=owner_dir.name) or {}
            out.append({
                "owner": owner_dir.name,
                "game_id": game_dir.name,
                "title": latest.get("title", ""),
                "scene_kind": (latest.get("core") or {}).get("scene_kind", ""),
                "latest_version": max(vs),
                "versions": vs,
            })
    return out
