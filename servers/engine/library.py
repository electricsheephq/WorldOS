"""HV4 (Act II §4c, #1326) — the REUSE / assembly surface: a READ-ONLY reader over the promoted
``library/`` pack that HV3's ``tools/library/promote.py`` writes. Closes the harvest flywheel — a
generated campaign can now ASSEMBLE from promoted content instead of always fresh-generating.

CONTRACT (load-bearing, mirrors questgen.py's "pure module" discipline):
  * READ-ONLY. promote.py is the SOLE WRITER of ``library/``; this module only reads. No path here
    ever opens a library file for write.
  * DEFAULT-OFF. Every entrypoint is gated on a world opting in via ``world["library_packs"]`` (a
    list of pack names). An empty / absent list yields an EMPTY pool, so a world without the field
    behaves EXACTLY as today (the seed path stays byte-identical — questgen never sees a candidate).
  * ADDITIVE + degrade-not-abort. A malformed entry / missing dir / bad JSON is SKIPPED, never
    raised — mirroring content._as_list_lenient and questgen's degrade contract.
  * TIER order (canonical > stable > fresh-gen) is a TIE-BREAK only (epic addendum [HIGH]): the
    caller ranks by its own signal first (token overlap in questgen), tier only breaks a near-tie.

LAYOUT (1:1 with promote.py): ``library/pack.json`` + ``library/<class>s/<slug>__<hash>.json`` where
each entry carries ``artifact_id`` / ``class`` / ``tier`` / ``scores`` / ``provenance`` / (optional)
``payload``. This module reads that shape without importing promote.py (they share the on-disk
contract, not code — promote writes, library reads)."""
from __future__ import annotations

import json
import re
from pathlib import Path

# The promoted pack lives at the repo root (tools/library/promote.py's DEFAULT_LIBRARY_DIR). From
# servers/engine/library.py that is three parents up. Overridable for tests via load_pool(root=…).
_ENGINE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _ENGINE_DIR.parent.parent
_DEFAULT_LIBRARY_DIR = _REPO_ROOT / "library"

# Tier weight for the tie-break (higher wins). An unknown tier sorts BELOW every known tier so a
# malformed entry never out-ranks a curated one. Mirrors promote.py's tier vocabulary.
_TIER_WEIGHT = {"canonical": 3, "stable": 2, "fresh-gen": 1}

# class -> subdir, 1:1 with promote.py's _CLASS_TO_SUBDIR (quest -> quests, location -> locations…).
_CLASS_TO_SUBDIR = {c: c + "s" for c in ("quest", "npc", "location", "encounter", "room")}


def tier_weight(tier: str | None) -> int:
    """The tie-break weight for a tier (canonical=3 > stable=2 > fresh-gen=1; unknown/None = 0)."""
    return _TIER_WEIGHT.get(str(tier or ""), 0)


def _library_dir(root: Path | str | None, world: dict | None = None) -> Path:
    """Resolve the on-disk library dir. Precedence: an explicit ``root`` arg > the world's
    ``_library_root`` escape hatch (a content/test override) > the repo-root ``library/``."""
    if root is not None:
        return Path(root)
    if isinstance(world, dict) and world.get("_library_root"):
        return Path(str(world["_library_root"]))
    return _DEFAULT_LIBRARY_DIR


def configured_packs(world: dict) -> list[str]:
    """The world's opted-in pack names (``world["library_packs"]``), as a clean list of non-empty
    strings. DEFAULT-OFF: absent / None / not-a-list / all-blank yields [] — the whole reuse surface
    stays dormant, so the seed path is byte-identical to a world that never heard of the field."""
    if not isinstance(world, dict):
        return []
    raw = world.get("library_packs")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]  # tolerate a bare scalar (a single pack name), mirroring _as_list_lenient
    return [str(p).strip() for p in raw if str(p).strip()]


def _read_pack_name(library_dir: Path) -> str | None:
    """The pack's declared name (library/pack.json ``name``), or None if unreadable/absent."""
    p = library_dir / "pack.json"
    if not p.exists():
        return None
    try:
        return str(json.loads(p.read_text(encoding="utf-8")).get("name") or "") or None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _load_entries(library_dir: Path, cls: str) -> list[dict]:
    """Every well-formed entry of ``cls`` under library/<class>s/, degrade-not-abort. A file that
    isn't valid JSON, isn't a dict, or whose ``class`` disagrees with the subdir is SKIPPED."""
    sub = _CLASS_TO_SUBDIR.get(cls)
    if sub is None:
        return []
    cls_dir = library_dir / sub
    if not cls_dir.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(cls_dir.glob("*.json")):
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue  # a malformed entry is skipped, never aborts the pool
        if isinstance(entry, dict) and entry.get("class") == cls and entry.get("artifact_id"):
            out.append(entry)
    return out


def load_pool(world: dict, cls: str, *, root: Path | str | None = None) -> list[dict]:
    """The read-only candidate POOL of promoted ``cls`` entries this world opted into.

    DEFAULT-OFF: returns [] whenever the world declares no ``library_packs`` — so a caller that
    always calls load_pool still sees no candidates on a default world (the byte-identity guarantee).
    When packs ARE configured, returns every promoted entry of ``cls`` whose pack name is in the
    opted-in set, sorted DETERMINISTICALLY by (tier weight desc, artifact_id asc) so the tie-break
    order is stable. Never writes; never raises (a bad dir/file degrades to fewer candidates)."""
    packs = configured_packs(world)
    if not packs:
        return []  # DEFAULT-OFF gate — no candidate ever reaches the caller
    library_dir = _library_dir(root, world)
    pack_name = _read_pack_name(library_dir)
    # A single pack per library dir today (promote.py writes one pack.json). Only expose entries when
    # the on-disk pack is one the world opted into — an opt-in naming a pack that isn't present yields
    # an empty pool (fall through to pure-gen), never an error.
    if pack_name is None or pack_name not in packs:
        return []
    entries = _load_entries(library_dir, cls)
    entries.sort(key=lambda e: (-tier_weight(e.get("tier")), str(e.get("artifact_id") or "")))
    return entries


# ── query-scored lookup (the lookup_library tool's ranking) ──────────────────────────────────────
_TOKEN = re.compile(r"[a-z][a-z'\-]{3,}")  # words length >= 4, lowercased — matches questgen._TOKEN


def _toks(text: str) -> set[str]:
    return set(_TOKEN.findall(str(text).lower()))


def _entry_text(entry: dict) -> str:
    """The searchable blob for an entry — its artifact_id + payload name/hook/description prose."""
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    parts = [str(entry.get("artifact_id") or "")]
    for k in ("name", "title", "grievance", "hook", "note", "description"):
        v = payload.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts)


def lookup(world: dict, cls: str, query: str, *, limit: int = 5,
           root: Path | str | None = None) -> list[dict]:
    """Score the opted-in ``cls`` pool against ``query`` and return the top ``limit`` entries.

    The lookup_library tool's ranking: token overlap with the query (the same overlap questgen uses
    for apophenia), tier as the TIE-BREAK (epic addendum [HIGH]) — a strictly higher overlap always
    wins; among equal overlaps the higher tier wins; then a stable artifact_id sort. DEFAULT-OFF:
    an un-opted-in world yields [] (load_pool gate). NO-MATCH: an opted-in world whose entries share
    no query token still returns the pool ranked by tier (a caller asked to browse); a caller that
    wants strict matching reads the ``overlap`` field. Never writes; never raises."""
    pool = load_pool(world, cls, root=root)
    if not pool:
        return []
    want = _toks(query)
    scored = []
    for entry in pool:
        overlap = len(want & _toks(_entry_text(entry))) if want else 0
        scored.append((overlap, tier_weight(entry.get("tier")), str(entry.get("artifact_id") or ""), entry))
    # overlap desc, tier desc, artifact_id asc — tier strictly a tie-break under overlap.
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    out: list[dict] = []
    for overlap, _tw, _aid, entry in scored[: max(1, int(limit))]:
        out.append({
            "artifact_id": entry.get("artifact_id"),
            "class": entry.get("class"),
            "tier": entry.get("tier"),
            "overlap": overlap,
            "scores": entry.get("scores"),
            "provenance": entry.get("provenance"),
            "payload": entry.get("payload"),
        })
    return out
