"""WorldOS asset registry resolver (VIEW layer) — gfx milestone M-C, issue #1195.

The renderer NEVER names a literal asset path. It names a SLOT (an ``asset_id``
plus a ``kind``) and asks this registry, which ALWAYS returns a guaranteed
non-null ref dict — the real asset OR a default template. This is the asset
analogue of the engine=SOLE-WRITER invariant: swapping or regenerating ANY
asset is ZERO renderer edits, because the renderer only ever knows slots.

Resolution rule (in order; the FIRST hit wins):

    exact -> alias -> defaults[kind] -> defaults["__any__"]   (the "floor")

Invariants this module guarantees:
  * ``resolve()`` ALWAYS returns a non-null ``dict`` (never ``None``).
  * ``resolve()`` NEVER raises — a missing/corrupt registry degrades to an
    in-code hardcoded floor template, not an exception.
  * On any fallback the returned dict has ``default_used=True`` and a
    ``resolved_via`` field of "exact" | "alias" | "default:<kind>" | "floor".
    (Exact hits set ``default_used=False`` and ``resolved_via="exact"``.)

VIEW-LAYER PURITY: this module imports NOTHING from ``servers/engine`` (only
the stdlib). It reads, never writes — it is presentation/data only and is not
a second writer of game state.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional

# Slot kinds the registry understands. Anything else still resolves (via the
# "__any__" floor) — this list is documentation, not a gate.
KINDS = ("character", "monster", "room", "effect", "sound")

# Last-resort floor used ONLY if the registry file is missing/corrupt AND the
# requested kind has no usable default. Keeps the never-null / never-throw
# guarantee even with no data on disk. Points at the proven hero template paths.
_HARDCODED_FLOOR: Dict[str, Any] = {
    "kind": "character",
    "model_ref": "Assets/painterly/models/hero.fbx",
    "albedo_ref": "Assets/painterly/models/hero_albedo.png",
    "anim_ref": "Assets/painterly/models/hero@moveset.fbx",
    "gen_recipe": "in-code hardcoded floor (registry.json missing or unreadable)",
    "version": "0.0.0",
    "critic_score": None,
}


def _find_registry_path() -> Optional[str]:
    """Locate ``data/asset-registry/registry.json`` robustly.

    Order: explicit env override -> walk up from this file's dir looking for a
    repo-root marker (``data/asset-registry/registry.json`` itself, or a
    ``.git`` entry) -> ``None`` if nothing is found (caller falls to the floor).
    """
    override = os.environ.get("WORLDOS_ASSET_REGISTRY")
    if override and os.path.isfile(override):
        return override

    here = os.path.dirname(os.path.abspath(__file__))
    # viewer/ is one level under the repo root; walk up a few levels to be safe
    # regardless of where a worktree/checkout puts us.
    cur = here
    for _ in range(8):
        candidate = os.path.join(cur, "data", "asset-registry", "registry.json")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


class AssetRegistry:
    """Resolve an asset SLOT to a guaranteed-non-null ref dict.

    Construct once and reuse; loading is lazy + cached and never raises. Pass an
    explicit ``path`` to point at a specific registry file (tests do this);
    otherwise it is auto-discovered relative to the repo root.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._explicit_path = path
        self._lock = threading.Lock()
        self._loaded = False
        self._assets: Dict[str, Any] = {}
        self._defaults: Dict[str, str] = {}
        self._aliases: Dict[str, str] = {}

    # -- loading -----------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            path = self._explicit_path or _find_registry_path()
            data: Dict[str, Any] = {}
            if path:
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        data = json.load(fh) or {}
                except (OSError, ValueError):
                    # Missing / unreadable / corrupt JSON -> degrade to floor.
                    data = {}
            self._assets = data.get("assets") or {}
            self._defaults = data.get("defaults") or {}
            self._aliases = data.get("aliases") or {}
            self._loaded = True

    # -- resolution --------------------------------------------------------
    def resolve(self, asset_id: Optional[str], kind: Optional[str] = None) -> Dict[str, Any]:
        """Resolve ``asset_id`` (a slot) to a non-null ref dict. Never raises.

        Rule: exact -> alias -> defaults[kind] -> defaults["__any__"] -> floor.
        The returned dict is a COPY (callers may freely mutate it) carrying
        ``default_used`` (bool) and ``resolved_via`` (str). On an exact hit
        ``default_used`` is False; on every fallback it is True.
        """
        try:
            self._ensure_loaded()
            aid = (asset_id or "").strip()

            # 1) exact
            if aid and aid in self._assets:
                return self._row(aid, default_used=False, resolved_via="exact")

            # 2) alias -> asset_id
            if aid and aid in self._aliases:
                target = self._aliases[aid]
                if target in self._assets:
                    return self._row(target, default_used=True, resolved_via="alias")

            # 3) defaults[kind]
            k = (kind or "").strip()
            if k and k in self._defaults:
                target = self._defaults[k]
                if target in self._assets:
                    return self._row(target, default_used=True, resolved_via="default:%s" % k)

            # 4) defaults["__any__"]  (the floor)
            any_default = self._defaults.get("__any__")
            if any_default and any_default in self._assets:
                return self._row(any_default, default_used=True, resolved_via="floor")

            # 5) in-code hardcoded floor (registry empty/corrupt)
            return self._floor_row(kind)
        except Exception:
            # Absolute belt-and-suspenders: the never-throw / never-null contract
            # holds even if something above is unexpectedly broken.
            return self._floor_row(kind)

    # -- helpers -----------------------------------------------------------
    def _row(self, asset_id: str, *, default_used: bool, resolved_via: str) -> Dict[str, Any]:
        row = dict(self._assets.get(asset_id) or {})
        row["asset_id"] = asset_id
        row["default_used"] = default_used
        row["resolved_via"] = resolved_via
        # Guarantee the ref keys exist (non-null contract is about the dict, but
        # downstream code reads these by name).
        for key in ("kind", "model_ref", "albedo_ref", "anim_ref"):
            row.setdefault(key, None)
        return row

    def _floor_row(self, kind: Optional[str]) -> Dict[str, Any]:
        row = dict(_HARDCODED_FLOOR)
        if kind:
            row["kind"] = kind
        row["asset_id"] = "__floor__"
        row["default_used"] = True
        row["resolved_via"] = "floor"
        return row


# Module-level singleton + convenience function for callers that don't want to
# manage an instance (the typical renderer-side call).
_DEFAULT_REGISTRY: Optional[AssetRegistry] = None
_DEFAULT_LOCK = threading.Lock()


def get_registry() -> AssetRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_REGISTRY is None:
                _DEFAULT_REGISTRY = AssetRegistry()
    return _DEFAULT_REGISTRY


def resolve(asset_id: Optional[str], kind: Optional[str] = None) -> Dict[str, Any]:
    """Module-level convenience: resolve via the shared singleton registry."""
    return get_registry().resolve(asset_id, kind)


if __name__ == "__main__":
    # Self-contained smoke (no pytest needed):
    #   python3 viewer/asset_registry.py
    reg = AssetRegistry()
    checks = [
        ("fighter", "character"),     # exact
        ("hero", "character"),        # alias -> fighter
        ("nobody", "character"),      # default:character -> template_human
        ("kraken", "monster"),        # default:monster -> template_demon
        ("mystery", "tarot"),         # unknown kind -> __any__ floor
        (None, None),                 # null slot -> __any__ floor
    ]
    for aid, knd in checks:
        r = reg.resolve(aid, knd)
        assert r is not None, "resolve returned None"
        assert r.get("model_ref") is not None or r.get("anim_ref") is not None, "null ref"
        print(
            "%-10s %-10s -> %-16s via=%-18s default_used=%s model=%s"
            % (aid, knd, r.get("asset_id"), r.get("resolved_via"), r.get("default_used"), r.get("model_ref"))
        )
    print("OK: resolve() never returned None and never threw.")
